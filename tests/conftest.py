# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import struct

from pkg.model import (
    SCHEMA_VERSION,
    Agent,
    Annotation,
    Clock,
    ImageRef,
    PointCloud,
    Scene,
    Sensor,
    StampedPose,
)
from pkg.se3 import SE3


def sample_scene() -> Scene:
    vehicle = Agent(
        agent_id="vehicle",
        agent_type="vehicle",
        poses=(
            StampedPose(0, SE3.identity().translation(0.0, 0.0, 0.0)),
            StampedPose(100_000, SE3.identity().translation(1.0, 0.0, 0.0)),
            StampedPose(200_000, SE3.identity().translation(2.0, 0.0, 0.0)),
        ),
    )
    drone = Agent(
        agent_id="drone",
        agent_type="drone",
        poses=(
            StampedPose(0, SE3.identity().translation(0.0, 0.0, 25.0)),
            StampedPose(100_000, SE3.identity().translation(1.0, 0.0, 25.0)),
            StampedPose(200_000, SE3.identity().translation(2.0, 0.0, 25.0)),
        ),
    )
    box = SE3.identity()
    annotations = (
        Annotation("vehicle", 100_000, "7", "car", (1.0, 0.0, 0.5), (4.0, 2.0, 1.5), box, 1.0),
        Annotation("drone", 100_000, "7", "car", (1.0, 0.0, -24.5), (4.0, 2.0, 1.5), box, 0.8),
        Annotation(
            "drone", 100_000, "9", "pedestrian", (3.0, 1.0, -24.0), (0.5, 0.5, 1.7), box, 1.0
        ),
    )
    cloud = PointCloud(
        agent_id="vehicle",
        sensor_id="lidar_top",
        time_us=100_000,
        count=1,
        xyz_i_f32=struct.pack("<4f", 1.0, 0.0, 0.0, 0.0),
    )
    return Scene(
        schema_version=SCHEMA_VERSION,
        scene_id="synthetic",
        world_frame="ENU",
        clock=Clock(unit="us", scale_note="synthetic 10 Hz"),
        agents=(vehicle, drone),
        sensors=(
            Sensor(
                sensor_id="lidar_top",
                agent_id="vehicle",
                modality="lidar",
                body_from_sensor=SE3.identity().translation(0.0, 0.0, 1.1),
            ),
        ),
        annotations=annotations,
        objects=(),
        point_clouds=(cloud,),
        images=(ImageRef("vehicle", "front", 100_000, "vehicle-side/camera/front/000001.png"),),
    )
