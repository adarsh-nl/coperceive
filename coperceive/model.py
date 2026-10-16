# SPDX-License-Identifier: Apache-2.0
"""In-memory scene: one world frame, N agents, per-agent sensors and boxes.

The on-disk contract is ``docs/spec/format.md``. Schema version 1.
"""

from __future__ import annotations

import enum
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from coperceive.query import TimeMode
    from coperceive.sample import CooperativeSample

from coperceive.se3 import SE3

SCHEMA_VERSION = 1

_INT64_MIN = -9223372036854775808
_INT64_MAX = 9223372036854775807


class AgentType(enum.Enum):
    """Closed set of agent kinds. Dataset-specific names stay in ``agent_id``."""

    VEHICLE = "vehicle"
    UAV = "uav"
    INFRASTRUCTURE = "infrastructure"
    OTHER = "other"

    @classmethod
    def parse(cls, value: object) -> AgentType:
        if isinstance(value, cls):
            return value
        if isinstance(value, str):
            try:
                return cls(value)
            except ValueError:
                pass
        raise ValueError("agent_type must be vehicle, uav, infrastructure, or other")


class Modality(enum.Enum):
    CAMERA = "camera"
    LIDAR = "lidar"
    RADAR = "radar"

    @classmethod
    def parse(cls, value: object) -> Modality:
        if isinstance(value, cls):
            return value
        if isinstance(value, str):
            try:
                return cls(value)
            except ValueError:
                pass
        raise ValueError("modality must be camera, lidar, or radar")


class BoxFrame(enum.Enum):
    """Frame the box center and orientation are expressed in."""

    BODY = "body"
    WORLD = "world"

    @classmethod
    def parse(cls, value: object) -> BoxFrame:
        if isinstance(value, cls):
            return value
        if isinstance(value, str):
            try:
                return cls(value)
            except ValueError:
                pass
        raise ValueError("box frame must be body or world")


def require_time_us(value: object, what: str) -> int:
    """Return ``value`` when it is a Python int that fits in int64."""
    if type(value) is not int:
        raise ValueError(f"{what} must be a Python int holding int64 microseconds")
    if value < _INT64_MIN or value > _INT64_MAX:
        raise ValueError(f"{what} does not fit in int64")
    return value


def _finite(value: object, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{what} must be a finite float")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{what} must be a finite float")
    return number


def _vec3(value: object, what: str) -> tuple[float, float, float]:
    if not isinstance(value, tuple):
        raise ValueError(f"{what} must be a tuple of three floats")
    items = cast(tuple[object, ...], value)
    if len(items) != 3:
        raise ValueError(f"{what} must be a tuple of three floats")
    return (_finite(items[0], what), _finite(items[1], what), _finite(items[2], what))


def _nonempty(value: object, what: str) -> str:
    if not isinstance(value, str) or value == "":
        raise ValueError(f"{what} must be a non-empty string")
    return value


@dataclass(frozen=True)
class Clock:
    """Integer microsecond clock.

    ``scale_note`` records how a source dataset was mapped onto microseconds.
    The mapping is never implied. ``unit`` is ``us`` in schema 1.
    """

    unit: str
    scale_note: str

    def __post_init__(self) -> None:
        if self.unit != "us":
            raise ValueError("clock unit must be us")
        _nonempty(self.scale_note, "clock.scale_note")


@dataclass(frozen=True)
class StampedPose:
    time_us: int
    world_from_body: SE3

    def __post_init__(self) -> None:
        require_time_us(self.time_us, "pose time_us")


@dataclass(frozen=True)
class Agent:
    agent_id: str
    agent_type: AgentType
    poses: tuple[StampedPose, ...]
    platform: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        _nonempty(self.agent_id, "agent_id")
        times = [pose.time_us for pose in self.poses]
        if any(times[i] >= times[i + 1] for i in range(len(times) - 1)):
            raise ValueError(f"agent {self.agent_id} poses must be strictly increasing in time")
        keys = [key for key, _ in self.platform]
        if any(key == "" for key, _ in self.platform):
            raise ValueError(f"agent {self.agent_id} platform keys must be non-empty strings")
        if len(keys) != len(set(keys)):
            raise ValueError(f"agent {self.agent_id} has a duplicate platform key")
        ordered = tuple(sorted(self.platform))
        if ordered != self.platform:
            object.__setattr__(self, "platform", ordered)


@dataclass(frozen=True)
class Sensor:
    """A sensor rigidly mounted on an agent.

    ``body_from_sensor`` takes a point in the sensor frame into the agent body.
    Camera intrinsics are an optional pinhole. Lidar and radar have no
    measurement model in schema 1; they still carry the rigid extrinsic.
    """

    sensor_id: str
    agent_id: str
    modality: Modality
    body_from_sensor: SE3
    image_width_px: int | None = None
    image_height_px: int | None = None
    fx: float | None = None
    fy: float | None = None
    cx: float | None = None
    cy: float | None = None

    def __post_init__(self) -> None:
        _nonempty(self.sensor_id, "sensor_id")
        _nonempty(self.agent_id, "agent_id")
        for name in ("image_width_px", "image_height_px"):
            value = getattr(self, name)
            if value is None:
                continue
            if type(value) is not int or value <= 0:
                raise ValueError(f"{name} must be a positive int or null")
        for name in ("fx", "fy", "cx", "cy"):
            value = getattr(self, name)
            if value is not None:
                _finite(value, name)


@dataclass(frozen=True)
class Annotation:
    """A 3D box at one time.

    ``center`` and ``box_from_rpy`` are expressed in ``frame``. ``box_from_rpy``
    is a pure rotation. ``track_id`` and ``visibility`` are absent when the
    source did not record them.
    """

    agent_id: str
    time_us: int
    track_id: str | None
    category: str
    center: tuple[float, float, float]
    size_lwh: tuple[float, float, float]
    box_from_rpy: SE3
    visibility: float | None = None
    frame: BoxFrame = BoxFrame.BODY

    def __post_init__(self) -> None:
        _nonempty(self.agent_id, "agent_id")
        require_time_us(self.time_us, "annotation time_us")
        if self.track_id is not None:
            _nonempty(self.track_id, "track_id")
        _nonempty(self.category, "category")
        object.__setattr__(self, "center", _vec3(self.center, "center"))
        object.__setattr__(self, "size_lwh", _vec3(self.size_lwh, "size_lwh"))
        rotation = self.box_from_rpy
        if rotation.tx != 0.0 or rotation.ty != 0.0 or rotation.tz != 0.0:
            raise ValueError("box orientation must be a pure rotation")
        if self.visibility is not None:
            object.__setattr__(self, "visibility", _finite(self.visibility, "visibility"))


@dataclass(frozen=True)
class SharedObject:
    """A track id that is the same object for more than one agent.

    Geometry stays on the per-agent annotations. A row exists only when at
    least two agents use the id and they agree on exactly one category.
    """

    track_id: str
    category: str

    def __post_init__(self) -> None:
        _nonempty(self.track_id, "track_id")
        _nonempty(self.category, "category")


@dataclass(frozen=True)
class PointCloud:
    agent_id: str
    sensor_id: str
    time_us: int
    count: int
    xyz_i_f32: bytes

    def __post_init__(self) -> None:
        _nonempty(self.agent_id, "agent_id")
        _nonempty(self.sensor_id, "sensor_id")
        require_time_us(self.time_us, "point cloud time_us")
        if type(self.count) is not int or self.count < 0:
            raise ValueError("point count must be a non-negative int")
        expected = self.count * 4 * 4
        if len(self.xyz_i_f32) != expected:
            raise ValueError(f"point cloud has {len(self.xyz_i_f32)} bytes, expected {expected}")


@dataclass(frozen=True)
class ImageRef:
    """Path of an image that is not copied into the store."""

    agent_id: str
    sensor_id: str
    time_us: int
    path: str

    def __post_init__(self) -> None:
        _nonempty(self.agent_id, "agent_id")
        _nonempty(self.sensor_id, "sensor_id")
        require_time_us(self.time_us, "image time_us")
        _nonempty(self.path, "image path")


@dataclass(frozen=True)
class Scene:
    schema_version: int
    scene_id: str
    world_frame: str
    clock: Clock
    agents: tuple[Agent, ...]
    sensors: tuple[Sensor, ...]
    annotations: tuple[Annotation, ...]
    objects: tuple[SharedObject, ...]
    point_clouds: tuple[PointCloud, ...] = ()
    images: tuple[ImageRef, ...] = ()

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int:
            raise ValueError("schema_version must be an int")
        _nonempty(self.scene_id, "scene_id")
        _nonempty(self.world_frame, "world_frame")
        agent_ids = [agent.agent_id for agent in self.agents]
        if len(agent_ids) != len(set(agent_ids)):
            raise ValueError("duplicate agent_id")
        known = set(agent_ids)
        sensor_keys = [(sensor.agent_id, sensor.sensor_id) for sensor in self.sensors]
        if len(sensor_keys) != len(set(sensor_keys)):
            raise ValueError("duplicate sensor_id on one agent")
        for sensor in self.sensors:
            if sensor.agent_id not in known:
                raise ValueError(f"sensor {sensor.sensor_id} names unknown agent {sensor.agent_id}")
        for ann in self.annotations:
            if ann.agent_id not in known:
                raise ValueError(f"annotation names unknown agent {ann.agent_id}")
        for cloud in self.point_clouds:
            if (cloud.agent_id, cloud.sensor_id) not in set(sensor_keys):
                raise ValueError(
                    f"point cloud names unknown sensor {cloud.agent_id}/{cloud.sensor_id}"
                )
        for image in self.images:
            if (image.agent_id, image.sensor_id) not in set(sensor_keys):
                raise ValueError(f"image names unknown sensor {image.agent_id}/{image.sensor_id}")

    def agent(self, agent_id: str) -> Agent:
        for agent in self.agents:
            if agent.agent_id == agent_id:
                return agent
        raise KeyError(agent_id)

    def sensor(self, agent_id: str, sensor_id: str) -> Sensor:
        for sensor in self.sensors:
            if sensor.agent_id == agent_id and sensor.sensor_id == sensor_id:
                return sensor
        raise KeyError((agent_id, sensor_id))

    def pose_times(self, agent_id: str) -> tuple[int, ...]:
        return tuple(pose.time_us for pose in self.agent(agent_id).poses)

    def at(
        self,
        t: int,
        *,
        tolerance: int,
        mode: TimeMode | None = None,
    ) -> CooperativeSample:
        """Every agent's pose, sensors, and boxes at ``t``, plus each ``dt_us``.

        ``tolerance`` is ``tolerance_us`` in microseconds and is required.
        ``mode`` defaults to nearest. A miss stays in the result.
        """
        from coperceive.query import TimeMode as RuntimeMode
        from coperceive.sample import scene_at

        chosen = RuntimeMode.NEAREST if mode is None else mode
        return scene_at(self, t, tolerance=tolerance, mode=chosen)
