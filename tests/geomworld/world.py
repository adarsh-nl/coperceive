# SPDX-License-Identifier: Apache-2.0
# pyright: reportUnknownMemberType=false
"""A synthetic world with known answers (``docs/adr/0017-geometry-conformance.md`` §2).

Everything is in one right-handed, z-up world frame, metres and radians:

* **objects**: boxes with a class, a size ``(length, width, height)`` and a pose
  ``world_from_box`` (length along box x, width along box y, height along box z);
* **agents**: a body pose ``world_from_body`` per frame, one LiDAR and pinhole
  cameras, each with a fixed pose in the body;
* **LiDAR returns** by ray casting against the boxes, every return tagged with
  the object it struck and given in the LiDAR frame;
* **camera truth**: the eight corners of every box in pixels.

Frames of a sensor: a LiDAR is x forward, y left, z up. A camera is the optical
frame, x right, y down, z forward, with ``u = fx * x / z + cx`` and
``v = fy * y / z + cy``; no distortion.

The world is plain data. It reads nothing from disk, imports nothing from
``coperceive`` and uses no random number that is not seeded here, so the same
arguments give the same world on every machine.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import numpy.typing as npt

Array = npt.NDArray[np.float64]

# Signs of the eight corners of a box, in the order every exporter and check uses:
# corner i = centre + R @ (sx * l/2, sy * w/2, sz * h/2).
CORNER_SIGNS: Array = np.array(
    [
        [1.0, 1.0, -1.0],
        [1.0, -1.0, -1.0],
        [-1.0, -1.0, -1.0],
        [-1.0, 1.0, -1.0],
        [1.0, 1.0, 1.0],
        [1.0, -1.0, 1.0],
        [-1.0, -1.0, 1.0],
        [-1.0, 1.0, 1.0],
    ]
)


def rot_x(angle: float) -> Array:
    c, s = math.cos(angle), math.sin(angle)
    return np.array([[1.0, 0.0, 0.0], [0.0, c, -s], [0.0, s, c]])


def rot_y(angle: float) -> Array:
    c, s = math.cos(angle), math.sin(angle)
    return np.array([[c, 0.0, s], [0.0, 1.0, 0.0], [-s, 0.0, c]])


def rot_z(angle: float) -> Array:
    c, s = math.cos(angle), math.sin(angle)
    return np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])


def rpy_matrix(roll: float, pitch: float, yaw: float) -> Array:
    """``Rz(yaw) @ Ry(pitch) @ Rx(roll)``: right-handed, angles in radians."""
    return rot_z(yaw) @ rot_y(pitch) @ rot_x(roll)


def matrix_rpy(rotation: Array) -> tuple[float, float, float]:
    """Roll, pitch and yaw of ``Rz(yaw) @ Ry(pitch) @ Rx(roll)``, pitch in ``[-π/2, π/2]``."""
    pitch = math.asin(max(-1.0, min(1.0, -float(rotation[2, 0]))))
    roll = math.atan2(float(rotation[2, 1]), float(rotation[2, 2]))
    yaw = math.atan2(float(rotation[1, 0]), float(rotation[0, 0]))
    return roll, pitch, yaw


@dataclass(frozen=True)
class Pose:
    """A rigid transform ``parent_from_child``: ``p_parent = rotation @ p_child + translation``."""

    rotation: Array
    translation: Array

    @staticmethod
    def of(
        xyz: tuple[float, float, float], rpy: tuple[float, float, float] = (0.0, 0.0, 0.0)
    ) -> Pose:
        return Pose(rpy_matrix(*rpy), np.array(xyz, dtype=np.float64))

    def apply(self, points: Array) -> Array:
        """Points of shape ``(..., 3)`` taken from the child frame into the parent frame."""
        return points @ self.rotation.T + self.translation

    def inverse(self) -> Pose:
        return Pose(self.rotation.T, -(self.rotation.T @ self.translation))

    def compose(self, other: Pose) -> Pose:
        """``self ∘ other``: apply ``other`` first."""
        return Pose(
            self.rotation @ other.rotation, self.rotation @ other.translation + self.translation
        )

    def matrix(self) -> Array:
        """The 4 × 4 homogeneous matrix."""
        out = np.eye(4)
        out[:3, :3] = self.rotation
        out[:3, 3] = self.translation
        return out


@dataclass(frozen=True)
class WorldObject:
    """A box in the world. ``object_id`` is unique in the world and never 0."""

    object_id: int
    category: str
    size_lwh: tuple[float, float, float]
    world_from_box: Pose

    def corners_world(self) -> Array:
        """The eight corners in the world frame, in :data:`CORNER_SIGNS` order."""
        half = np.array(self.size_lwh) / 2.0
        return self.world_from_box.apply(CORNER_SIGNS * half)


@dataclass(frozen=True)
class Lidar:
    """A spinning LiDAR: one ray per azimuth step and elevation angle, from its origin."""

    sensor_id: str
    body_from_lidar: Pose
    azimuth_steps: int = 720
    elevations_deg: tuple[float, ...] = tuple(float(v) for v in np.linspace(-24.0, 6.0, 31))
    max_range_m: float = 120.0

    def directions(self) -> Array:
        """Unit ray directions in the LiDAR frame, shape ``(steps * elevations, 3)``."""
        azimuth = np.linspace(-math.pi, math.pi, self.azimuth_steps, endpoint=False)
        elevation = np.radians(np.array(self.elevations_deg))
        az, el = np.meshgrid(azimuth, elevation, indexing="ij")
        return np.stack(
            [np.cos(el) * np.cos(az), np.cos(el) * np.sin(az), np.sin(el)], axis=-1
        ).reshape(-1, 3)


@dataclass(frozen=True)
class Camera:
    """A pinhole camera without distortion, optical frame x right, y down, z forward."""

    sensor_id: str
    body_from_camera: Pose
    width: int
    height: int
    fx: float
    fy: float
    cx: float
    cy: float

    def intrinsic(self) -> Array:
        return np.array([[self.fx, 0.0, self.cx], [0.0, self.fy, self.cy], [0.0, 0.0, 1.0]])

    def project(self, points_camera: Array) -> tuple[Array, npt.NDArray[np.bool_]]:
        """Pixels ``(N, 2)`` of points in the camera frame, and which lie in front of it."""
        z = points_camera[:, 2]
        in_front = z > 1e-6
        safe = np.where(in_front, z, 1.0)
        u = self.fx * points_camera[:, 0] / safe + self.cx
        v = self.fy * points_camera[:, 1] / safe + self.cy
        return np.stack([u, v], axis=-1), in_front


# body_from_camera for a camera that looks along body +x: optical z = body x,
# optical x = body -y (right), optical y = body -z (down).
FORWARD_CAMERA: Array = np.array([[0.0, 0.0, 1.0], [-1.0, 0.0, 0.0], [0.0, -1.0, 0.0]])


def camera_pose(
    xyz: tuple[float, float, float], yaw: float = 0.0, pitch: float = 0.0, roll: float = 0.0
) -> Pose:
    """``body_from_camera`` of a camera at ``xyz`` whose optical axis is turned by the angles."""
    return Pose(rpy_matrix(roll, pitch, yaw) @ FORWARD_CAMERA, np.array(xyz, dtype=np.float64))


@dataclass(frozen=True)
class Agent:
    """An agent: ``kind`` is ``vehicle``, ``infrastructure`` or ``uav``; one body pose per frame."""

    agent_id: str
    kind: str
    world_from_body: tuple[Pose, ...]
    lidar: Lidar
    cameras: tuple[Camera, ...] = ()


@dataclass(frozen=True)
class LidarReturns:
    """The returns of one LiDAR at one frame.

    ``points`` are in the LiDAR frame, shape ``(N, 3)``. ``object_ids`` has the id
    of the object each point lies on, shape ``(N,)``.
    """

    points: Array
    object_ids: npt.NDArray[np.int64]


@dataclass(frozen=True)
class CameraTruth:
    """Where the corners of one object fall in one image.

    ``pixels`` is ``(8, 2)`` in :data:`CORNER_SIGNS` order; ``in_front`` says which
    corners lie in front of the camera. ``box_2d`` is ``(u_min, v_min, u_max, v_max)``
    over the corners and is only meaningful when all eight are in front.
    """

    pixels: Array
    in_front: npt.NDArray[np.bool_]
    box_2d: tuple[float, float, float, float]


@dataclass(frozen=True)
class World:
    """Objects, agents and ``n_frames`` frames, with the answers every check needs.

    ``objects[frame]`` are the objects of that frame (they may move between frames;
    ids persist). ``period_s`` is the time between frames.
    """

    objects: tuple[tuple[WorldObject, ...], ...]
    agents: tuple[Agent, ...]
    period_s: float = 0.1
    _returns: dict[tuple[str, int], LidarReturns] = field(
        default_factory=dict[tuple[str, int], LidarReturns], compare=False, repr=False
    )

    @property
    def n_frames(self) -> int:
        return len(self.objects)

    def agent(self, agent_id: str) -> Agent:
        for agent in self.agents:
            if agent.agent_id == agent_id:
                return agent
        raise KeyError(agent_id)

    def world_from_lidar(self, agent_id: str, frame: int) -> Pose:
        agent = self.agent(agent_id)
        return agent.world_from_body[frame].compose(agent.lidar.body_from_lidar)

    def world_from_camera(self, agent_id: str, sensor_id: str, frame: int) -> Pose:
        agent = self.agent(agent_id)
        camera = next(c for c in agent.cameras if c.sensor_id == sensor_id)
        return agent.world_from_body[frame].compose(camera.body_from_camera)

    def lidar_returns(self, agent_id: str, frame: int) -> LidarReturns:
        """Ray-cast returns of the agent's LiDAR at ``frame`` (cached)."""
        key = (agent_id, frame)
        if key not in self._returns:
            self._returns[key] = cast_rays(
                self.agent(agent_id).lidar,
                self.world_from_lidar(agent_id, frame),
                self.objects[frame],
            )
        return self._returns[key]

    def seen_ids(self, agent_id: str, frame: int, *, min_points: int = 1) -> tuple[int, ...]:
        """Ids of the objects the agent's LiDAR hits at least ``min_points`` times, ascending."""
        ids, counts = np.unique(self.lidar_returns(agent_id, frame).object_ids, return_counts=True)
        return tuple(int(i) for i, n in zip(ids, counts, strict=True) if n >= min_points)

    def camera_truth(self, agent_id: str, sensor_id: str, frame: int) -> dict[int, CameraTruth]:
        """Per object id, the pixels of its corners in that camera at ``frame``."""
        agent = self.agent(agent_id)
        camera = next(c for c in agent.cameras if c.sensor_id == sensor_id)
        camera_from_world = self.world_from_camera(agent_id, sensor_id, frame).inverse()
        truth: dict[int, CameraTruth] = {}
        for obj in self.objects[frame]:
            pixels, in_front = camera.project(camera_from_world.apply(obj.corners_world()))
            box = (
                float(pixels[:, 0].min()),
                float(pixels[:, 1].min()),
                float(pixels[:, 0].max()),
                float(pixels[:, 1].max()),
            )
            truth[obj.object_id] = CameraTruth(pixels, in_front, box)
        return truth


def cast_rays(
    lidar: Lidar, world_from_lidar: Pose, objects: tuple[WorldObject, ...]
) -> LidarReturns:
    """Nearest intersection of every ray with every box (slab test in the box frame).

    A ray that starts inside a box is not a return of that box: an agent does not
    see its own body. Returns beyond ``max_range_m`` are dropped.
    """
    directions = lidar.directions()
    n_rays = directions.shape[0]
    best_t = np.full(n_rays, np.inf)
    best_id = np.zeros(n_rays, dtype=np.int64)
    origin_world = world_from_lidar.translation
    directions_world = directions @ world_from_lidar.rotation.T
    for obj in objects:
        box_from_world = obj.world_from_box.inverse()
        origin = box_from_world.apply(origin_world)
        direction = directions_world @ box_from_world.rotation.T
        half = np.array(obj.size_lwh) / 2.0
        if bool(np.all(np.abs(origin) <= half)):
            continue
        with np.errstate(divide="ignore", invalid="ignore"):
            t_low = (-half - origin) / direction
            t_high = (half - origin) / direction
        t_near = np.minimum(t_low, t_high)
        t_far = np.maximum(t_low, t_high)
        # A ray parallel to a slab and outside it never enters the box.
        parallel = direction == 0.0
        outside = parallel & (np.abs(origin) > half)
        t_near = np.where(parallel, -np.inf, t_near)
        t_far = np.where(parallel, np.inf, t_far)
        enter = t_near.max(axis=1)
        leave = t_far.min(axis=1)
        hit = (enter <= leave) & (enter > 0.0) & ~outside.any(axis=1) & (enter < best_t)
        best_t = np.where(hit, enter, best_t)
        best_id = np.where(hit, obj.object_id, best_id)
    keep = np.isfinite(best_t) & (best_t <= lidar.max_range_m)
    points = directions[keep] * best_t[keep, None]
    return LidarReturns(points=points, object_ids=best_id[keep])


def inside_box(
    points_world: Array,
    world_from_box: Pose,
    size_lwh: tuple[float, float, float],
    *,
    margin_m: float,
) -> npt.NDArray[np.bool_]:
    """Which world points lie inside the box grown by ``margin_m`` on every side."""
    local = world_from_box.inverse().apply(points_world)
    half = np.array(size_lwh) / 2.0 + margin_m
    return np.all(np.abs(local) <= half, axis=1)
