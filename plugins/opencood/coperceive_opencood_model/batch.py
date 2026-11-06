# SPDX-License-Identifier: Apache-2.0
"""Build OpenCOOD's per-CAV ``base_data_dict`` from one Coperceive protocol frame.

Pure conversion in ``convert`` plus this module's mapping: no coperceive import, no OpenCOOD
import, no torch. The worker reads the point files and passes rows in; the engine turns the
result into numpy arrays and model inputs. The dict shape follows OpenCOOD's dataset
``retrieve_base_data`` output, which is the input of its own inference pipeline:

``OrderedDict[cav_id -> {ego, time_delay, lidar_np, params}]`` where ``params`` carries
``lidar_pose`` (``[x, y, z, roll, yaw, pitch]`` degrees, CARLA convention), ``ego_speed``,
``transformation_matrix`` (this CAV to the ego frame), ``spatial_correction_matrix``,
``gt_transformation_matrix``, and ``vehicles`` (ground truth, never sent: always ``{}``).
The intermediate-fusion dataset reads ``proj_first``/``cur_ego_pose_flag`` from the
checkpoint config itself.

Ego choice: the first requested agent of type ``vehicle`` (schema agent order, which is the
parent's ``configure.agents`` order) that has both a pose and a lidar sweep in the frame.
Other CAVs within the checkpoint's communication range are ordered by distance with
infrastructure last, and at most the checkpoint config's ``max_cav`` CAVs are sent. All of
that is documented in ``docs/models/opencood.md``; the worker's manifest asks the parent for
vehicle and infrastructure agents so a roadside unit's sweep is part of the fusion.
"""

from __future__ import annotations

import struct

from convert import (
    ConvertError,
    carla_pose_from_schema,
    carla_x_to_world,
    points_to_carla,
    quaternion_rotate,
    x1_to_x2,
)

VEHICLE = "vehicle"
INFRASTRUCTURE = "infrastructure"
LIDAR_LAYOUT = "xyzi_f32le"

_POSE_KEYS = ("qw", "qx", "qy", "qz", "tx", "ty", "tz")


def read_points(path: str, count: int, layout: str) -> list[list[float]]:
    """One ``xyzi_f32le`` sweep as ``[x, y, z, intensity]`` rows (the protocol's layout).

    ``count`` is the row count the parent extracted; a file that does not hold exactly that
    many bytes is a hard error rather than a silently short cloud.
    """
    if layout != LIDAR_LAYOUT:
        raise ConvertError(f"point layout {layout!r} is not {LIDAR_LAYOUT!r}")
    expected = int(count) * 16
    with open(path, "rb") as handle:
        raw = handle.read()
    if len(raw) != expected:
        raise ConvertError(f"{path}: {len(raw)} bytes, expected {expected} for {count} points")
    values = struct.unpack("<" + "f" * (count * 4), raw)
    return [list(values[index : index + 4]) for index in range(0, count * 4, 4)]


def _sensor_extrinsic(sensors: object, agent_id: str, sensor_id: str) -> list[float]:
    """``(qw, qx, qy, qz, tx, ty, tz)`` of the swept sensor's ``body_from_sensor``."""
    if not isinstance(sensors, (list, tuple)):
        raise ConvertError("configure.sensors must be a list")
    for sensor in sensors:
        if not isinstance(sensor, dict):
            raise ConvertError("configure.sensors: every sensor must be an object")
        if sensor.get("agent_id") == agent_id and sensor.get("sensor_id") == sensor_id:
            extrinsic = sensor.get("body_from_sensor")
            if not isinstance(extrinsic, dict):
                raise ConvertError(f"{agent_id}/{sensor_id}: body_from_sensor is missing")
            values = []
            for key in _POSE_KEYS:
                value = extrinsic.get(key)
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise ConvertError(f"{agent_id}/{sensor_id}: body_from_sensor.{key}")
                values.append(float(value))
            return values
    # The parent lists every sensor of a requested agent; a missing one is the parent's bug.
    raise ConvertError(f"configure.sensors does not list {agent_id}/{sensor_id}")


def points_in_body(
    points: list[list[float]], extrinsic: list[float], where: str
) -> list[list[float]]:
    """A sweep in the agent's body frame; the lidar sensor is a fixed extrinsic on the body."""
    qw, qx, qy, qz, tx, ty, tz = extrinsic
    identity = abs(qw - 1.0) < 1e-12 and abs(qx) < 1e-12 and abs(qy) < 1e-12
    identity = identity and abs(qz) < 1e-12
    if identity and tx == 0.0 and ty == 0.0 and tz == 0.0:
        return points
    quat = [qw, qx, qy, qz]
    out: list[list[float]] = []
    for x, y, z, intensity in points:
        rx, ry, rz = quaternion_rotate(quat, (x, y, z))
        out.append([rx + tx, ry + ty, rz + tz, intensity])
    return out


def cav_ids(agent_ids: list[str], agent_types: dict[str, str], ego_id: str) -> dict[str, str]:
    """OpenCOOD CAV ids: the ego is ``"0"``, vehicles positive, infrastructure negative.

    OpenCOOD's dataset tests ``int(cav_id) < 0`` for the infrastructure prior, so the ids
    must parse as integers and a roadside unit must be negative.
    """
    ids: dict[str, str] = {ego_id: "0"}
    next_vehicle = 1
    next_infrastructure = -1
    for agent_id in agent_ids:
        if agent_id in ids:
            continue
        if agent_types.get(agent_id) == INFRASTRUCTURE:
            ids[agent_id] = str(next_infrastructure)
            next_infrastructure -= 1
        else:
            ids[agent_id] = str(next_vehicle)
            next_vehicle += 1
    return ids


def ego_agent_id(agents: list[dict[str, str]]) -> str | None:
    """The scene's ego agent id from the configure agent list (first vehicle)."""
    for agent in agents:
        agent_id = agent.get("agent_id", "")
        if agent.get("agent_type") == VEHICLE and agent_id:
            return agent_id
    return None


def select_ego(
    poses: dict[str, object],
    points_by_agent: dict[str, tuple[str, str, int, str]],
    preferred: str | None,
) -> str | None:
    """The ego for this frame, or ``None`` when its data is missing.

    ``preferred`` is the scene's first vehicle from ``configure.agents``; a frame where that
    agent has no pose or sweep gets an empty result rather than boxes attributed to another
    agent, so rows always belong to one agent per scene.
    """
    if preferred is None:
        return None
    if preferred in poses and preferred in points_by_agent:
        return preferred
    return None


def build_base_data(
    *,
    agents: list[dict[str, str]],
    sensors: object,
    poses: dict[str, object],
    points_by_agent: dict[str, tuple[str, str, int, str]],
    com_range: float,
    max_cav: int,
    preferred_ego: str | None = None,
    point_reader=read_points,
) -> dict[str, object] | None:
    """OpenCOOD base data for one frame, or ``None`` when the ego has nothing to see.

    ``points_by_agent`` maps an agent id to ``(path, sensor_id, count, layout)`` from the
    protocol's ``frame.points``. Returns an insertion-ordered dict (ego first) whose CAV
    dicts hold plain Python lists; the engine converts them to numpy.
    """
    if max_cav < 1:
        raise ConvertError(f"max_cav must be at least 1, got {max_cav}")
    ego_id = select_ego(poses, points_by_agent, preferred_ego)
    if ego_id is None:
        return None
    agent_types = {agent.get("agent_id", ""): agent.get("agent_type", "") for agent in agents}
    ego_pose = carla_pose_from_schema(poses[ego_id], f"pose of {ego_id}")
    ego_world = carla_x_to_world(ego_pose)
    ego_x, ego_y = ego_world[0][3], ego_world[1][3]
    candidates: list[tuple[float, int, str]] = []
    for agent in agents:
        agent_id = agent.get("agent_id", "")
        if agent_id == ego_id or agent_id not in poses or agent_id not in points_by_agent:
            continue
        pose = carla_pose_from_schema(poses[agent_id], f"pose of {agent_id}")
        distance = ((pose[0] - ego_x) ** 2 + (pose[1] - ego_y) ** 2) ** 0.5
        if distance > com_range:
            continue
        infrastructure = 1 if agent.get("agent_type") == INFRASTRUCTURE else 0
        candidates.append((distance, infrastructure, agent_id))
    candidates.sort()
    member_ids = [ego_id] + [agent_id for _, _, agent_id in candidates][: max_cav - 1]
    member_ids = member_ids[:max_cav]
    ids = cav_ids(member_ids, agent_types, ego_id)
    data: dict[str, object] = {}
    for agent_id in member_ids:
        path, sensor_id, count, layout = points_by_agent[agent_id]
        rows = point_reader(path, count, layout)
        extrinsic = _sensor_extrinsic(sensors, agent_id, sensor_id)
        body_rows = points_in_body(rows, extrinsic, f"agent {agent_id}")
        pose = carla_pose_from_schema(poses[agent_id], f"pose of {agent_id}")
        data[ids[agent_id]] = {
            "ego": agent_id == ego_id,
            "time_delay": 0.0,
            "lidar_np": points_to_carla(body_rows, f"sweep of {agent_id}"),
            "params": {
                "lidar_pose": pose,
                "ego_speed": 0.0,  # schema 1 stores no speed; docs/models/opencood.md
                "transformation_matrix": x1_to_x2(pose, ego_pose),
                "gt_transformation_matrix": x1_to_x2(pose, ego_pose),
                "spatial_correction_matrix": [
                    [1.0, 0.0, 0.0, 0.0],
                    [0.0, 1.0, 0.0, 0.0],
                    [0.0, 0.0, 1.0, 0.0],
                    [0.0, 0.0, 0.0, 1.0],
                ],
                "vehicles": {},  # ground truth is never sent
            },
        }
    return data
