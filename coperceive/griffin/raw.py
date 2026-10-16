# SPDX-License-Identifier: Apache-2.0
"""Read one Griffin scene from an extracted ``griffin-release`` directory.

The integer ``timestamp`` in each pose file is the frame id. Microseconds are
``frame_id * 100_000`` because the paper states a 10 Hz capture. That scale is
written into the scene clock note. It is not a clock recovered from the files.
"""

from __future__ import annotations

import json
from pathlib import Path

from coperceive.diagnostics import shared_objects
from coperceive.model import (
    SCHEMA_VERSION,
    Agent,
    AgentType,
    Annotation,
    Clock,
    ImageRef,
    Modality,
    PointCloud,
    Scene,
    Sensor,
    StampedPose,
)
from coperceive.se3 import SE3

_CLOCK_NOTE = (
    "Griffin pose 'timestamp' is the integer frame id, equal to the file stem. "
    "time_us = frame_id * 100000 assumes the 10 Hz rate stated for the lidar in "
    "the Griffin paper. The files do not contain a measured wall clock."
)
_SIDES = (("vehicle-side", "vehicle"), ("drone-side", "drone"))


def load_scene(release_root: Path, scene_name: str) -> Scene:
    """Load both agents of one scene.

    ``release_root`` contains ``vehicle-side`` and ``drone-side``.
    """
    vehicle_infos = release_root / "vehicle-side" / "scene_infos.json"
    drone_infos_path = release_root / "drone-side" / "scene_infos.json"
    infos = json.loads(vehicle_infos.read_text(encoding="utf-8"))
    drone_infos = json.loads(drone_infos_path.read_text(encoding="utf-8"))
    if infos != drone_infos:
        raise ValueError("vehicle-side and drone-side scene_infos.json differ")
    match = next((row for row in infos if row["name"] == scene_name), None)
    if match is None:
        raise KeyError(scene_name)
    frames = [int(frame) for frame in match["info"]["frames"]]
    agents: list[Agent] = []
    sensors: list[Sensor] = []
    annotations: list[Annotation] = []
    images: list[ImageRef] = []
    clouds: list[PointCloud] = []
    for side, agent_id in _SIDES:
        agents.append(_poses(release_root / side, agent_id, frames))
        side_sensors = _sensors(release_root / side, agent_id)
        sensors.extend(side_sensors)
        annotations.extend(_labels(release_root / side, agent_id, frames))
        images.extend(_image_refs(agent_id, frames, side_sensors))
        if agent_id == "vehicle":
            clouds.extend(_lidar(release_root / side, frames))
    return Scene(
        schema_version=SCHEMA_VERSION,
        scene_id=scene_name,
        world_frame="ENU",
        clock=Clock(unit="us", scale_note=_CLOCK_NOTE),
        agents=tuple(agents),
        sensors=tuple(sensors),
        annotations=tuple(annotations),
        objects=shared_objects(annotations),
        point_clouds=tuple(clouds),
        images=tuple(images),
    )


def parse_label_line(line: str, *, agent_id: str, time_us: int) -> Annotation:
    """Parse one Griffin label line into a body-frame box.

    Twelve fields are ``type x y z l w h roll pitch yaw id visibility``.
    Eleven fields omit visibility; it is stored as null, not as a filled-in
    1.0. Nine fields omit roll, pitch, and visibility; roll and pitch are 0
    and visibility is null. Lengths, widths, heights, and translations are
    metres. Angles are degrees. See ``docs/datasets/griffin.md``.
    """
    parts = line.split()
    if len(parts) == 12:
        roll, pitch, yaw = float(parts[7]), float(parts[8]), float(parts[9])
        track = str(int(float(parts[10])))
        visibility: float | None = float(parts[11])
    elif len(parts) == 11:
        roll, pitch, yaw = float(parts[7]), float(parts[8]), float(parts[9])
        track = str(int(float(parts[10])))
        visibility = None
    elif len(parts) == 9:
        roll, pitch = 0.0, 0.0
        yaw = float(parts[7])
        track = str(int(float(parts[8])))
        visibility = None
    else:
        raise ValueError(f"expected 12, 11, or 9 label fields, got {len(parts)}")
    return Annotation(
        agent_id=agent_id,
        time_us=time_us,
        track_id=track,
        category=parts[0],
        center=(float(parts[1]), float(parts[2]), float(parts[3])),
        size_lwh=(float(parts[4]), float(parts[5]), float(parts[6])),
        box_from_rpy=SE3.from_rpy_deg(roll, pitch, yaw),
        visibility=visibility,
    )


def parse_pose(payload: dict[str, object]) -> tuple[int, SE3]:
    frame = int(payload["timestamp"])  # type: ignore[arg-type]
    rotation = SE3.from_rpy_deg(
        float(payload["roll"]),  # type: ignore[arg-type]
        float(payload["pitch"]),  # type: ignore[arg-type]
        float(payload["yaw"]),  # type: ignore[arg-type]
    )
    pose = rotation.translation(
        float(payload["x"]),  # type: ignore[arg-type]
        float(payload["y"]),  # type: ignore[arg-type]
        float(payload["z"]),  # type: ignore[arg-type]
    )
    return frame, pose


def read_ascii_ply(path: Path) -> tuple[int, bytes]:
    """ASCII PLY with float properties x, y, z, I.

    Returns ``(count, little-endian x, y, z, intensity)``. The caller attaches
    the agent, sensor, and time.
    """
    text = path.read_text(encoding="utf-8")
    header, _, body = text.partition("end_header\n")
    count = 0
    for line in header.splitlines():
        if line.startswith("element vertex"):
            count = int(line.split()[-1])
    values: list[float] = []
    for line in body.splitlines():
        if not line.strip():
            continue
        values.extend(float(part) for part in line.split())
        if len(values) >= count * 4:
            break
    return count, _f32_bytes(values[: count * 4])


def _f32_bytes(values: list[float]) -> bytes:
    import struct

    return struct.pack("<" + "f" * len(values), *values)


def _poses(side: Path, agent_id: str, frames: list[int]) -> Agent:
    stamped: list[StampedPose] = []
    for frame in frames:
        payload = json.loads((side / "pose" / f"{frame:06d}.json").read_text(encoding="utf-8"))
        file_frame, pose = parse_pose(payload)
        if file_frame != frame:
            raise ValueError(f"{side} pose {frame} has timestamp {file_frame}")
        stamped.append(StampedPose(time_us=frame * 100_000, world_from_body=pose))
    agent_type = AgentType.UAV if agent_id == "drone" else AgentType.VEHICLE
    return Agent(agent_id=agent_id, agent_type=agent_type, poses=tuple(stamped))


def _sensors(side: Path, agent_id: str) -> list[Sensor]:
    sensors: list[Sensor] = []
    for path in sorted((side / "calib").glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        extrinsic = payload["extrinsic"]
        rotation = _rotation_from_rows(extrinsic)
        translation = SE3.identity().translation(
            float(extrinsic[0][3]), float(extrinsic[1][3]), float(extrinsic[2][3])
        )
        body_from_sensor = SE3(
            rotation.qw,
            rotation.qx,
            rotation.qy,
            rotation.qz,
            translation.tx,
            translation.ty,
            translation.tz,
        )
        intrinsic = payload.get("intrinsic")
        width = height = fx = fy = cx = cy = None
        modality = Modality.LIDAR if path.stem.startswith("lidar") else Modality.CAMERA
        if intrinsic is not None:
            fx = float(intrinsic[0][0])
            fy = float(intrinsic[1][1])
            cx = float(intrinsic[0][2])
            cy = float(intrinsic[1][2])
            width = int(round(cx * 2))
            height = int(round(cy * 2))
            modality = Modality.CAMERA
        sensors.append(
            Sensor(
                sensor_id=path.stem,
                agent_id=agent_id,
                modality=modality,
                body_from_sensor=body_from_sensor,
                image_width_px=width,
                image_height_px=height,
                fx=fx,
                fy=fy,
                cx=cx,
                cy=cy,
            )
        )
    return sensors


def _rotation_from_rows(matrix: list[list[float]]) -> SE3:
    rows = (
        (float(matrix[0][0]), float(matrix[0][1]), float(matrix[0][2])),
        (float(matrix[1][0]), float(matrix[1][1]), float(matrix[1][2])),
        (float(matrix[2][0]), float(matrix[2][1]), float(matrix[2][2])),
    )
    return SE3.from_matrix(rows)


def _labels(side: Path, agent_id: str, frames: list[int]) -> list[Annotation]:
    rows: list[Annotation] = []
    for frame in frames:
        text = (side / "label" / f"{frame:06d}.txt").read_text(encoding="utf-8")
        for line in text.splitlines():
            if line.strip():
                rows.append(parse_label_line(line, agent_id=agent_id, time_us=frame * 100_000))
    return rows


def _image_refs(agent_id: str, frames: list[int], sensors: list[Sensor]) -> list[ImageRef]:
    """Path refs for camera calibs that were actually read. Bytes stay outside the store."""
    side = "drone-side" if agent_id == "drone" else "vehicle-side"
    refs: list[ImageRef] = []
    for sensor in sensors:
        if sensor.modality is not Modality.CAMERA:
            continue
        for frame in frames:
            refs.append(
                ImageRef(
                    agent_id=agent_id,
                    sensor_id=sensor.sensor_id,
                    time_us=frame * 100_000,
                    path=f"{side}/camera/{sensor.sensor_id}/{frame:06d}.png",
                )
            )
    return refs


def _lidar(side: Path, frames: list[int]) -> list[PointCloud]:
    clouds: list[PointCloud] = []
    lidar = side / "lidar" / "lidar_top"
    if not lidar.is_dir():
        return clouds
    for frame in frames:
        path = lidar / f"{frame:06d}.ply"
        if not path.exists():
            continue
        count, blob = read_ascii_ply(path)
        clouds.append(
            PointCloud(
                agent_id="vehicle",
                sensor_id="lidar_top",
                time_us=frame * 100_000,
                count=count,
                xyz_i_f32=blob,
            )
        )
    return clouds
