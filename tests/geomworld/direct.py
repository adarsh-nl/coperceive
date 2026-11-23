# SPDX-License-Identifier: Apache-2.0
# pyright: reportUnknownMemberType=false
"""The world written straight into a schema-1 scene, with no dataset in between.

This is the known-good path the checks are themselves checked on
(``tests/test_geometry_checks.py``): a scene built here passes every check, and
a scene built from a world that was bent in one of the ways adapters get wrong
fails the check that is there to catch it. No adapter is involved.
"""

from __future__ import annotations

import numpy as np
from coperceive.diagnostics import shared_objects
from coperceive.model import (
    SCHEMA_VERSION,
    Agent,
    AgentType,
    Annotation,
    BoxFrame,
    Clock,
    Modality,
    PointCloud,
    Scene,
    Sensor,
    StampedPose,
)
from coperceive.se3 import SE3

from tests.geomworld.expectation import Expectation
from tests.geomworld.world import Pose, World

_AGENT_TYPES = {
    "vehicle": AgentType.VEHICLE,
    "infrastructure": AgentType.INFRASTRUCTURE,
    "uav": AgentType.UAV,
}


def se3_of(pose: Pose) -> SE3:
    """A world pose as the scene's ``SE3``."""
    r = pose.rotation
    rows = (
        (float(r[0, 0]), float(r[0, 1]), float(r[0, 2])),
        (float(r[1, 0]), float(r[1, 1]), float(r[1, 2])),
        (float(r[2, 0]), float(r[2, 1]), float(r[2, 2])),
    )
    t = pose.translation
    return SE3.from_matrix(rows, (float(t[0]), float(t[1]), float(t[2])))


def _rotation_only(pose: Pose) -> SE3:
    return se3_of(Pose(pose.rotation, np.zeros(3)))


def frame_time_us(world: World, frame: int) -> int:
    return round(frame * world.period_s * 1_000_000)


def expectation_of(world: World, *, min_points: int = 1) -> Expectation:
    """Ids kept as they are; an agent lists the objects its LiDAR hits at least once."""
    return Expectation(
        agents={agent.agent_id: agent.agent_id for agent in world.agents},
        lidars={agent.agent_id: agent.lidar.sensor_id for agent in world.agents},
        cameras={
            (agent.agent_id, camera.sensor_id): camera.sensor_id
            for agent in world.agents
            for camera in agent.cameras
        },
        annotated={
            (agent.agent_id, frame): world.seen_ids(agent.agent_id, frame, min_points=min_points)
            for agent in world.agents
            for frame in range(world.n_frames)
        },
        track_ids={obj.object_id: str(obj.object_id) for obj in world.objects[0]},
    )


def scene_of(
    world: World, *, box_frame: BoxFrame = BoxFrame.WORLD, recorded: World | None = None
) -> Scene:
    """A schema-1 scene holding ``world``.

    ``recorded`` is the world the sensors saw: it supplies the LiDAR returns (in the
    sensor frame, as a file would hold them) and which objects each agent lists. It
    defaults to ``world``. It is given when ``world`` is a bent copy, so that the
    bent scene holds the true recordings under wrongly read poses and boxes, which
    is what an adapter with a convention bug produces.
    """
    source = recorded if recorded is not None else world
    agents: list[Agent] = []
    sensors: list[Sensor] = []
    annotations: list[Annotation] = []
    clouds: list[PointCloud] = []
    for agent in world.agents:
        agents.append(
            Agent(
                agent_id=agent.agent_id,
                agent_type=_AGENT_TYPES[agent.kind],
                poses=tuple(
                    StampedPose(frame_time_us(world, frame), se3_of(pose))
                    for frame, pose in enumerate(agent.world_from_body)
                ),
            )
        )
        sensors.append(
            Sensor(
                sensor_id=agent.lidar.sensor_id,
                agent_id=agent.agent_id,
                modality=Modality.LIDAR,
                body_from_sensor=se3_of(agent.lidar.body_from_lidar),
            )
        )
        for camera in agent.cameras:
            sensors.append(
                Sensor(
                    sensor_id=camera.sensor_id,
                    agent_id=agent.agent_id,
                    modality=Modality.CAMERA,
                    body_from_sensor=se3_of(camera.body_from_camera),
                    image_width_px=camera.width,
                    image_height_px=camera.height,
                    fx=camera.fx,
                    fy=camera.fy,
                    cx=camera.cx,
                    cy=camera.cy,
                )
            )
        for frame in range(world.n_frames):
            time_us = frame_time_us(world, frame)
            returns = source.lidar_returns(agent.agent_id, frame)
            packed = np.zeros((len(returns.points), 4), dtype=np.float32)
            packed[:, :3] = returns.points
            clouds.append(
                PointCloud(
                    agent_id=agent.agent_id,
                    sensor_id=agent.lidar.sensor_id,
                    time_us=time_us,
                    count=len(returns.points),
                    xyz_i_f32=packed.tobytes(),
                )
            )
            objects = {obj.object_id: obj for obj in world.objects[frame]}
            body_from_world = agent.world_from_body[frame].inverse()
            for object_id in source.seen_ids(agent.agent_id, frame):
                obj = objects[object_id]
                pose = obj.world_from_box
                if box_frame is BoxFrame.BODY:
                    pose = body_from_world.compose(pose)
                centre = pose.translation
                annotations.append(
                    Annotation(
                        agent_id=agent.agent_id,
                        time_us=time_us,
                        track_id=str(object_id),
                        category=obj.category,
                        center=(float(centre[0]), float(centre[1]), float(centre[2])),
                        size_lwh=obj.size_lwh,
                        box_from_rpy=_rotation_only(pose),
                        visibility=None,
                        frame=box_frame,
                    )
                )
    return Scene(
        schema_version=SCHEMA_VERSION,
        scene_id="geomworld",
        world_frame="geomworld",
        clock=Clock(unit="us", scale_note="frame index times the world period"),
        agents=tuple(agents),
        sensors=tuple(sensors),
        annotations=tuple(annotations),
        objects=shared_objects(annotations),
        point_clouds=tuple(clouds),
    )
