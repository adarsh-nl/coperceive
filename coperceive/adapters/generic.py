# SPDX-License-Identifier: Apache-2.0
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownParameterType=false, reportMissingParameterType=false, reportArgumentType=false, reportPrivateUsage=false, reportUnknownLambdaType=false, reportIndexIssue=false, reportAttributeAccessIssue=false, reportOptionalMemberAccess=false, reportPossiblyUnboundVariable=false, reportUnusedFunction=false
"""Generic multi-view dataset format ("bring your own dataset").

One ``scene.yaml`` describes any collection of viewpoints — moving vehicles,
roadside units, drones, or fixed inspection cameras/LiDARs. Per-agent folders
hold images/point clouds and optional boxes. See docs/spec/generic-multiview.md.
"""

from __future__ import annotations

import csv
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn

import yaml

from coperceive.adapters.build import CheckReport, SceneRef
from coperceive.adapters.readers.lidar import read_bin_f32, read_pcd
from coperceive.adapters.rotation import Reflection
from coperceive.model import (
    Agent,
    AgentType,
    Annotation,
    BoxFrame,
    Clock,
    ImageRef,
    Modality,
    PointCloud,
    Scene,
    Sensor,
    StampedPose,
)
from coperceive.se3 import SE3

NAME = "generic"
SCHEMA = "coperceive-generic-1"
MARKER = "scene.yaml"
_ROLES = {
    "vehicle": AgentType.VEHICLE,
    "uav": AgentType.UAV,
    "drone": AgentType.UAV,
    "infrastructure": AgentType.INFRASTRUCTURE,
    "roadside": AgentType.INFRASTRUCTURE,
    "fixed": AgentType.INFRASTRUCTURE,
    "other": AgentType.OTHER,
}


class GenericError(ValueError):
    """A generic dataset is malformed. The message names the file and field."""


def _fail(where: str, message: str) -> NoReturn:
    raise GenericError(f"{where}: {message}")


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise GenericError(f"{path.name}: cannot read YAML: {exc}") from None
    if not isinstance(payload, dict):
        raise GenericError(f"{path.name}: expected a mapping")
    return payload


def _vec3(value: object, where: str, name: str) -> tuple[float, float, float]:
    if not isinstance(value, list) or len(value) != 3:
        _fail(where, f"{name} must be [x, y, z]")
    try:
        return (float(value[0]), float(value[1]), float(value[2]))
    except (TypeError, ValueError):
        _fail(where, f"{name} must be numbers")
    raise AssertionError


def _pose(value: object, where: str) -> SE3:
    if not isinstance(value, dict):
        _fail(where, "pose must be a mapping")
    translation = _vec3(value.get("translation_m", [0, 0, 0]), where, "translation_m")
    rpy = _vec3(value.get("rotation_rpy_deg", [0, 0, 0]), where, "rotation_rpy_deg")
    return SE3.from_rpy_deg(rpy[0], rpy[1], rpy[2]).translation(*translation)


def _matrix4(value: object, where: str) -> SE3:
    if (
        not isinstance(value, list)
        or len(value) != 4
        or not all(isinstance(row, list) and len(row) == 4 for row in value)
    ):
        _fail(where, "extrinsic must be 4×4")
    try:
        rows = [[float(v) for v in row] for row in value]
    except (TypeError, ValueError):
        _fail(where, "extrinsic must be numeric")
    return SE3.from_matrix(
        (
            (rows[0][0], rows[0][1], rows[0][2]),
            (rows[1][0], rows[1][1], rows[1][2]),
            (rows[2][0], rows[2][1], rows[2][2]),
        ),
        (rows[0][3], rows[1][3], rows[2][3]),
    )


def _timestamps(scene: dict[str, Any], where: str) -> tuple[int, ...]:
    if "timestamps_us" in scene:
        values = scene["timestamps_us"]
        if not isinstance(values, list) or not values:
            _fail(where, "timestamps_us must be a non-empty list")
    elif "timestamp_file" in scene:
        name = scene["timestamp_file"]
        if not isinstance(name, str):
            _fail(where, "timestamp_file must be a path")
        path = Path(where).parent / name
        try:
            values = [int(line.strip()) for line in path.read_text().splitlines() if line.strip()]
        except (OSError, ValueError) as exc:
            raise GenericError(f"{name}: cannot read timestamps: {exc}") from None
    elif "frame_rate_hz" in scene:
        rate = scene["frame_rate_hz"]
        count = scene.get("frame_count")
        if not isinstance(rate, (int, float)) or rate <= 0:
            _fail(where, "frame_rate_hz must be positive")
        if not isinstance(count, int) or count <= 0:
            _fail(where, "frame_count must be positive with frame_rate_hz")
        values = [round(index * 1_000_000 / float(rate)) for index in range(count)]
    else:
        _fail(where, "needs timestamps_us, timestamp_file or frame_rate_hz + frame_count")
    out = []
    for value in values:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            _fail(where, "timestamps_us must be non-negative integers")
        out.append(value)
    if any(a >= b for a, b in zip(out, out[1:], strict=False)):
        _fail(where, "timestamps_us must be strictly increasing")
    return tuple(out)


@dataclass(frozen=True)
class _AgentSpec:
    id: str
    role: str
    agent_type: AgentType
    fixed: SE3 | None
    poses: tuple[StampedPose, ...]
    sensors: tuple[Sensor, ...]
    image_files: tuple[tuple[str, str], ...]  # (sensor_id, template)
    lidar_files: tuple[tuple[str, int], ...]  # (sensor_id, modality)


def _sensor_extrinsic(sensor: Mapping[str, object], where: str) -> SE3:
    if "extrinsic" in sensor:
        return _matrix4(sensor["extrinsic"], where)
    if "body_from_sensor" in sensor:
        return _pose(sensor["body_from_sensor"], where)
    return SE3.identity()


class GenericAdapter:
    name = NAME
    spec = None

    def _scene_file(self, raw_root: Path) -> Path:
        path = raw_root / MARKER
        if not path.is_file():
            raise GenericError(f"{raw_root}: missing {MARKER}")
        return path

    def validate(self, raw_root: Path) -> dict[str, Any]:
        path = self._scene_file(raw_root)
        payload = _read_yaml(path)
        if payload.get("schema") != SCHEMA or not isinstance(payload.get("schema"), str):
            _fail(path.name, f"schema must be {SCHEMA}")
        scene_id = payload.get("scene_id")
        if not isinstance(scene_id, str) or not scene_id:
            _fail(path.name, "scene_id must be a non-empty string")
        handedness = payload.get("handedness", "right")
        if handedness not in ("right", "left"):
            _fail(path.name, "handedness must be right or left")
        timestamps = _timestamps(payload, str(path))
        agents = payload.get("agents")
        if not isinstance(agents, list) or not agents:
            _fail(path.name, "agents must be a non-empty list")
        seen: set[str] = set()
        count = 0
        for index, agent in enumerate(agents):
            where = f"agent[{index}]"
            if not isinstance(agent, dict):
                _fail(path.name, f"{where} must be a mapping")
            agent_id = agent.get("id")
            if not isinstance(agent_id, str) or not agent_id:
                _fail(path.name, f"{where}.id must be a non-empty string")
            if agent_id in seen:
                _fail(path.name, f"{where}.id {agent_id!r} is duplicated")
            seen.add(agent_id)
            role = agent.get("role", "other")
            if not isinstance(role, str) or not role:
                _fail(path.name, f"agent {agent_id}: role must be a string")
            sensors = agent.get("sensors")
            if not isinstance(sensors, list) or not sensors:
                _fail(path.name, f"agent {agent_id}: sensors must be a non-empty list")
            sensor_ids: set[str] = set()
            for sindex, sensor in enumerate(sensors):
                swhere = f"agent {agent_id}: sensors[{sindex}]"
                if not isinstance(sensor, dict):
                    _fail(path.name, f"{swhere} must be a mapping")
                sensor_id = sensor.get("id")
                if not isinstance(sensor_id, str) or not sensor_id:
                    _fail(path.name, f"{swhere}.id must be a non-empty string")
                if sensor_id in sensor_ids:
                    _fail(path.name, f"agent {agent_id}: sensor {sensor_id!r} is duplicated")
                sensor_ids.add(sensor_id)
                modality = sensor.get("modality")
                if modality not in ("camera", "lidar"):
                    _fail(path.name, f"{swhere}.modality must be camera or lidar")
                files = sensor.get("files")
                if not isinstance(files, str) or "{frame:06d}" not in files:
                    _fail(
                        path.name,
                        f"agent {agent_id}: sensor {sensor_id}: files must contain {{frame:06d}}",
                    )
                if modality == "camera":
                    intrinsic = sensor.get("intrinsic")
                    if not isinstance(intrinsic, list) or len(intrinsic) != 4:
                        _fail(
                            path.name,
                            f"agent {agent_id}: sensor {sensor_id}: "
                            "intrinsic must be [fx, fy, cx, cy]",
                        )
                    first = raw_root / files.format(frame=0)
                    if not first.is_file():
                        _fail(
                            path.name,
                            f"agent {agent_id}: sensor {sensor_id}: "
                            f"missing {files.format(frame=0)}",
                        )
                else:
                    first = raw_root / files.format(frame=0)
                    if not first.is_file():
                        _fail(
                            path.name,
                            f"agent {agent_id}: sensor {sensor_id}: "
                            f"missing {files.format(frame=0)}",
                        )
                _sensor_extrinsic(sensor, swhere)
            has_fixed = "fixed_pose" in agent
            has_poses = "poses" in agent
            if has_fixed == has_poses:
                _fail(path.name, f"agent {agent_id}: exactly one of fixed_pose, poses")
            if has_fixed:
                _pose(agent["fixed_pose"], f"agent {agent_id}: fixed_pose")
            else:
                poses_name = agent["poses"]
                if not isinstance(poses_name, str):
                    _fail(path.name, f"agent {agent_id}: poses must be a CSV path")
                if not (raw_root / poses_name).is_file():
                    _fail(path.name, f"agent {agent_id}: missing poses file {poses_name}")
            boxes = agent.get("boxes")
            if boxes is not None:
                if not isinstance(boxes, str) or not (raw_root / boxes).is_file():
                    _fail(path.name, f"agent {agent_id}: boxes file is missing")
            count += 1
        return {
            "scene_id": scene_id,
            "handedness": handedness,
            "timestamps": timestamps,
            "agents": agents,
        }

    def scenes(self, raw_root: Path, params: Mapping[str, str]) -> tuple[SceneRef, ...]:
        if params:
            _fail(MARKER, f"unsupported params: {', '.join(sorted(params))}")
        payload = self.validate(raw_root)
        return (SceneRef(str(payload["scene_id"]), tuple(payload["timestamps"]), (), ()),)

    def _poses(
        self, raw_root: Path, agent: Mapping[str, object], timestamps: Sequence[int]
    ) -> tuple[tuple[StampedPose, ...], SE3 | None]:
        if "fixed_pose" in agent:
            pose = _pose(agent["fixed_pose"], "fixed_pose")
            return tuple(StampedPose(int(t), pose) for t in timestamps), pose
        name = str(agent["poses"])
        path = raw_root / name
        out: list[StampedPose] = []
        try:
            with path.open(newline="", encoding="utf-8") as handle:
                for row in csv.DictReader(handle):
                    time_us = int(row["timestamp_us"])
                    rpy = (float(row["roll_deg"]), float(row["yaw_deg"]), float(row["pitch_deg"]))
                    pose = SE3.from_rpy_deg(*rpy).translation(
                        float(row["x"]), float(row["y"]), float(row["z"])
                    )
                    out.append(StampedPose(time_us, pose))
        except (OSError, KeyError, ValueError) as exc:
            raise GenericError(f"{name}: cannot read poses: {exc}") from None
        out.sort(key=lambda p: p.time_us)
        if not out:
            raise GenericError(f"{name}: no poses")
        return tuple(out), None

    def load(self, raw_root: Path, ref: SceneRef) -> Scene:
        payload = self.validate(raw_root)
        timestamps = payload["timestamps"]
        reflect = Reflection("y") if payload["handedness"] == "left" else None
        agents: list[Agent] = []
        sensors: list[Sensor] = []
        images: list[ImageRef] = []
        clouds: list[PointCloud] = []
        annotations: list[Annotation] = []
        scene_id = str(payload["scene_id"])
        for agent in payload["agents"]:
            assert isinstance(agent, dict)
            agent_id = str(agent["id"])
            role = str(agent.get("role", "other"))
            agent_type = _ROLES.get(role.lower(), AgentType.OTHER)
            poses, _fixed = self._poses(raw_root, agent, timestamps)
            if reflect is not None:
                poses = tuple(StampedPose(p.time_us, reflect.se3(p.world_from_body)) for p in poses)
            agent_sensors: list[Sensor] = []
            for sensor in agent["sensors"]:
                assert isinstance(sensor, dict)
                sensor_id = str(sensor["id"])
                modality = Modality.CAMERA if sensor["modality"] == "camera" else Modality.LIDAR
                extrinsic = _sensor_extrinsic(sensor, sensor_id)
                if reflect is not None:
                    extrinsic = reflect.se3(extrinsic)
                kwargs: dict[str, Any] = {}
                if modality is Modality.CAMERA:
                    intrinsic = [float(v) for v in sensor["intrinsic"]]
                    kwargs = {
                        "fx": intrinsic[0],
                        "fy": intrinsic[1],
                        "cx": intrinsic[2],
                        "cy": intrinsic[3],
                    }
                s = Sensor(
                    sensor_id=sensor_id,
                    agent_id=agent_id,
                    modality=modality,
                    body_from_sensor=extrinsic,
                    **kwargs,
                )
                agents_sensor = s
                agent_sensors.append(agents_sensor)
                sensors.append(agents_sensor)
                template = str(sensor["files"])
                for frame_index, time_us in enumerate(timestamps):
                    path = raw_root / template.format(frame=frame_index)
                    if modality is Modality.CAMERA:
                        images.append(
                            ImageRef(
                                agent_id, sensor_id, int(time_us), str(path.relative_to(raw_root))
                            )
                        )
                    else:
                        if path.suffix.lower() == ".bin":
                            count, packed = read_bin_f32(
                                path,
                                stride=4,
                                offsets={"x": 0, "y": 1, "z": 2},
                                intensity_fill=0.0,
                            )
                        else:
                            count, packed = read_pcd(
                                path, fields={"x": "x", "y": "y", "z": "z"}, intensity_fill=0.0
                            )
                        clouds.append(PointCloud(agent_id, sensor_id, int(time_us), count, packed))
            agents.append(Agent(agent_id, agent_type, poses, platform=(("role", role),)))
            boxes = agent.get("boxes")
            if isinstance(boxes, str):
                annotations.extend(self._boxes(raw_root / boxes, agent_id, reflect))
        return Scene(
            schema_version=1,
            scene_id=scene_id,
            world_frame="right-handed ENU",
            clock=Clock("us", "generic timestamps in microseconds"),
            agents=tuple(agents),
            sensors=tuple(sensors),
            annotations=tuple(annotations),
            objects=(),
            point_clouds=tuple(clouds),
            images=tuple(images),
        )

    def _boxes(self, path: Path, agent_id: str, reflect: Reflection | None) -> list[Annotation]:
        out: list[Annotation] = []
        try:
            with path.open(newline="", encoding="utf-8") as handle:
                for row in csv.DictReader(handle):
                    center = (float(row["x"]), float(row["y"]), float(row["z"]))
                    size = (float(row["length"]), float(row["width"]), float(row["height"]))
                    yaw = float(row.get("yaw_deg", 0.0))
                    rotation = SE3.from_rpy_deg(0.0, yaw, 0.0)
                    if reflect is not None:
                        center = reflect.point(center)
                        rotation = reflect.se3(rotation)
                    out.append(
                        Annotation(
                            agent_id=agent_id,
                            time_us=int(row["timestamp_us"]),
                            track_id=row.get("track_id") or None,
                            category=row.get("category", "unknown"),
                            center=center,
                            size_lwh=size,
                            box_from_rpy=rotation,
                            frame=BoxFrame.WORLD,
                        )
                    )
        except (OSError, KeyError, ValueError) as exc:
            raise GenericError(f"{path.name}: cannot read boxes: {exc}") from None
        return out

    def check(
        self, raw_root: Path, params: Mapping[str, str], *, scene_ids: Sequence[str] | None = None
    ) -> CheckReport:
        try:
            ref = self.scenes(raw_root, params)[0]
            if scene_ids is not None and ref.scene_id not in set(scene_ids):
                return CheckReport(0, (), {}, ())
            scene = self.load(raw_root, ref)
            counts = (
                ("agents", len(scene.agents)),
                ("boxes", len(scene.annotations)),
                ("lidar", len(scene.point_clouds)),
            )
            return CheckReport(1, (), {ref.scene_id: counts}, ())
        except (GenericError, OSError, ValueError) as exc:
            return CheckReport(1, (str(exc),), {}, ())


def validate_generic(raw_root: Path) -> dict[str, Any]:
    return GenericAdapter().validate(raw_root)
