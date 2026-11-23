# SPDX-License-Identifier: Apache-2.0
"""The standard conformance world (``docs/adr/0017-geometry-conformance.md`` §2).

Forty-two objects stand on three rings around four agents. Their headings sweep
from −180° to 170° in steps of 10°, followed by the headings a convention bug
hides behind: exactly ±π, a millionth of a radian inside ±π, and ±π/2. Every
sixth object is tilted. Three objects move, so a frame read at the wrong time is
visible. The agents are two vehicles, a roadside unit and an aerial agent; all of
them are turned and tilted, and one vehicle drives and yaws between frames.

A dataset that has no roadside unit or no aerial agent exports a subset
(:func:`subset`); the objects and the frames are the same for everyone.
"""

from __future__ import annotations

import math
from dataclasses import replace

import numpy as np

from tests.geomworld.world import Agent, Camera, Lidar, Pose, World, WorldObject, camera_pose

N_FRAMES = 3
PERIOD_S = 0.1

EGO = "ego"
CAV = "cav"
RSU = "rsu"
UAV = "uav"

_SIZES: dict[str, tuple[float, float, float]] = {
    "car": (4.5, 1.9, 1.6),
    "truck": (7.0, 2.5, 3.0),
    "pedestrian": (0.6, 0.6, 1.75),
}
_RINGS_M = (16.0, 26.0, 36.0)
_SWEEP_DEG = tuple(float(v) for v in range(-180, 180, 10))
_EDGE_RAD = (
    math.pi,
    -math.pi,
    math.pi - 1e-6,
    -math.pi + 1e-6,
    math.pi / 2.0,
    -math.pi / 2.0,
)
# (roll, pitch) in degrees of the tilted objects.
_TILTS_DEG = ((6.0, -4.0), (-8.0, 10.0))
MOVING_IDS = (1, 2, 3)
_MOVE_M_PER_FRAME = (0.5, 0.2, 0.0)


def headings_rad() -> tuple[float, ...]:
    """The heading of every object, in object-id order (id 1 first)."""
    return tuple(math.radians(v) for v in _SWEEP_DEG) + _EDGE_RAD


def _category(index: int) -> str:
    if index % 7 == 6:
        return "pedestrian"
    if index % 5 == 4:
        return "truck"
    return "car"


def _objects(frame: int) -> tuple[WorldObject, ...]:
    headings = headings_rad()
    count = len(headings)
    objects: list[WorldObject] = []
    for index, heading in enumerate(headings):
        category = _category(index)
        size = _SIZES[category]
        bearing = 2.0 * math.pi * index / count
        radius = _RINGS_M[index % len(_RINGS_M)]
        roll = pitch = 0.0
        lift = 0.0
        if index % 6 == 3 or index % 6 == 5:
            roll_deg, pitch_deg = _TILTS_DEG[0] if index % 6 == 3 else _TILTS_DEG[1]
            roll, pitch = math.radians(roll_deg), math.radians(pitch_deg)
            lift = 0.4
        x = radius * math.cos(bearing)
        y = radius * math.sin(bearing)
        object_id = index + 1
        if object_id in MOVING_IDS:
            x += frame * _MOVE_M_PER_FRAME[0]
            y += frame * _MOVE_M_PER_FRAME[1]
        objects.append(
            WorldObject(
                object_id=object_id,
                category=category,
                size_lwh=size,
                world_from_box=Pose.of((x, y, size[2] / 2.0 + lift), (roll, pitch, heading)),
            )
        )
    return tuple(objects)


def _vehicle_cameras() -> tuple[Camera, ...]:
    """Front, left, right and rear cameras, 800 × 600, 90° horizontal field of view."""
    focal = 400.0
    return (
        Camera("front", camera_pose((1.2, 0.0, 1.6)), 800, 600, focal, focal, 400.0, 300.0),
        Camera(
            "left",
            camera_pose((0.2, 0.6, 1.6), yaw=math.radians(100.0)),
            800,
            600,
            focal,
            focal,
            400.0,
            300.0,
        ),
        Camera(
            "right",
            camera_pose((0.2, -0.6, 1.6), yaw=math.radians(-100.0)),
            800,
            600,
            focal,
            focal,
            400.0,
            300.0,
        ),
        Camera(
            "rear", camera_pose((-1.4, 0.0, 1.6), yaw=math.pi), 800, 600, focal, focal, 400.0, 300.0
        ),
    )


def _agents() -> tuple[Agent, ...]:
    ground_lidar = Lidar("lidar", Pose.of((0.0, 0.0, 1.9)))
    ego = Agent(
        agent_id=EGO,
        kind="vehicle",
        world_from_body=tuple(
            Pose.of((1.0 * frame, 0.3 * frame, 0.0), (0.010, -0.020, 0.10 + 0.05 * frame))
            for frame in range(N_FRAMES)
        ),
        lidar=ground_lidar,
        cameras=_vehicle_cameras(),
    )
    cav = Agent(
        agent_id=CAV,
        kind="vehicle",
        world_from_body=tuple(
            Pose.of((8.0 - 0.4 * frame, -6.0, 0.0), (0.030, 0.020, 2.2))
            for frame in range(N_FRAMES)
        ),
        lidar=ground_lidar,
        cameras=_vehicle_cameras(),
    )
    rsu = Agent(
        agent_id=RSU,
        kind="infrastructure",
        world_from_body=tuple(
            Pose.of((-5.0, 9.0, 5.0), (0.0, 0.15, -1.0)) for _ in range(N_FRAMES)
        ),
        lidar=Lidar(
            "lidar",
            Pose.of((0.0, 0.0, 0.3)),
            elevations_deg=tuple(float(v) for v in np.linspace(-40.0, 0.0, 41)),
        ),
        cameras=(
            Camera(
                "cam",
                camera_pose((0.2, 0.0, 0.0), pitch=math.radians(10.0)),
                1920,
                1080,
                1100.0,
                1100.0,
                960.0,
                540.0,
            ),
        ),
    )
    uav = Agent(
        agent_id=UAV,
        kind="uav",
        world_from_body=tuple(
            Pose.of((3.0 + 0.5 * frame, 3.0, 25.0), (0.05, -0.08, 0.7)) for frame in range(N_FRAMES)
        ),
        lidar=Lidar(
            "lidar",
            Pose.of((0.0, 0.0, -0.2)),
            elevations_deg=tuple(float(v) for v in np.linspace(-88.0, -20.0, 35)),
        ),
        cameras=(
            Camera(
                "down",
                # The optical axis points 60° below the horizon of the body.
                camera_pose((0.3, 0.0, -0.2), pitch=math.radians(60.0)),
                1280,
                720,
                640.0,
                640.0,
                640.0,
                360.0,
            ),
        ),
    )
    return (ego, cav, rsu, uav)


def standard_world() -> World:
    """The world every exporter writes and every adapter is checked against."""
    return World(
        objects=tuple(_objects(frame) for frame in range(N_FRAMES)),
        agents=_agents(),
        period_s=PERIOD_S,
    )


def subset(world: World, agent_ids: tuple[str, ...]) -> World:
    """The same objects and frames with only ``agent_ids``, in that order."""
    return replace(world, agents=tuple(world.agent(agent_id) for agent_id in agent_ids))
