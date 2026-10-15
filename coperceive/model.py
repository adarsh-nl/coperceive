# SPDX-License-Identifier: Apache-2.0
"""In-memory scene: one world frame, N agents, per-agent sensors and boxes."""

from __future__ import annotations

from dataclasses import dataclass

from coperceive.se3 import SE3

SCHEMA_VERSION = 1


@dataclass(frozen=True)
class Clock:
    """Integer microsecond clock.

    ``scale_note`` records how a source dataset was mapped onto microseconds.
    The mapping is never implied.
    """

    unit: str
    scale_note: str


@dataclass(frozen=True)
class StampedPose:
    time_us: int
    world_from_body: SE3


@dataclass(frozen=True)
class Agent:
    agent_id: str
    agent_type: str
    poses: tuple[StampedPose, ...]

    def __post_init__(self) -> None:
        times = [p.time_us for p in self.poses]
        if any(times[i] >= times[i + 1] for i in range(len(times) - 1)):
            raise ValueError(f"agent {self.agent_id} poses must be strictly increasing in time")


@dataclass(frozen=True)
class Sensor:
    """A sensor rigidly mounted on an agent.

    ``body_from_sensor`` takes a point in the sensor frame into the agent body.
    """

    sensor_id: str
    agent_id: str
    modality: str
    body_from_sensor: SE3
    image_width_px: int | None = None
    image_height_px: int | None = None
    fx: float | None = None
    fy: float | None = None
    cx: float | None = None
    cy: float | None = None


@dataclass(frozen=True)
class Annotation:
    """A box in the annotating agent's body frame at one time."""

    agent_id: str
    time_us: int
    track_id: str
    category: str
    center_body: tuple[float, float, float]
    size_lwh: tuple[float, float, float]
    box_from_rpy: SE3
    visibility: float | None = None


@dataclass(frozen=True)
class SharedObject:
    """A track id that really is the same object for more than one agent.

    Geometry stays on the per-agent annotations. This record only exists when
    the caller has decided the id is shared.
    """

    track_id: str
    category: str


@dataclass(frozen=True)
class PointCloud:
    agent_id: str
    sensor_id: str
    time_us: int
    count: int
    xyz_i_f32: bytes

    def __post_init__(self) -> None:
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

    def agent(self, agent_id: str) -> Agent:
        for agent in self.agents:
            if agent.agent_id == agent_id:
                return agent
        raise KeyError(agent_id)

    def pose_times(self, agent_id: str) -> tuple[int, ...]:
        return tuple(p.time_us for p in self.agent(agent_id).poses)
