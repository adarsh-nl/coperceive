# SPDX-License-Identifier: Apache-2.0
"""The vectorised sweep transform gives the same bytes as the point-by-point one."""

from __future__ import annotations

import array
import random
import struct

from coperceive.model import (
    SCHEMA_VERSION,
    Agent,
    AgentType,
    Clock,
    Modality,
    PointCloud,
    Scene,
    Sensor,
    StampedPose,
)
from coperceive.se3 import SE3
from coperceive.transform import lookup_pose
from coperceive.viewdata import world_points


def _loop(scene: Scene, cloud: PointCloud) -> bytes:
    """The transform as it was: SE3.apply per point."""
    pose = lookup_pose(scene.agent(cloud.agent_id), cloud.time_us, max_gap_us=0)
    world = pose.compose(scene.sensor(cloud.agent_id, cloud.sensor_id).body_from_sensor)
    values = array.array("f")
    values.frombytes(cloud.xyz_i_f32)
    out = array.array("f")
    for index in range(cloud.count):
        out.extend(world.apply((values[4 * index], values[4 * index + 1], values[4 * index + 2])))
    return out.tobytes()


def test_vectorised_world_points_match_the_loop_bit_for_bit() -> None:
    rng = random.Random(7)
    pose = SE3(0.8775826, 0.0, 0.0, 0.4794255, 12.5, -3.25, 0.75)  # 1 rad about z
    mount = SE3(0.9238795, 0.0, 0.0, 0.3826834, 1.2, 0.1, 1.9)
    count = 2_000
    blob = b"".join(
        struct.pack(
            "<4f", rng.uniform(-80, 80), rng.uniform(-80, 80), rng.uniform(-3, 5), rng.random()
        )
        for _ in range(count)
    )
    scene = Scene(
        schema_version=SCHEMA_VERSION,
        scene_id="w",
        world_frame="ENU",
        clock=Clock("us", "t"),
        agents=(Agent("a", AgentType.VEHICLE, (StampedPose(0, pose),)),),
        sensors=(Sensor("lidar", "a", Modality.LIDAR, mount),),
        annotations=(),
        objects=(),
    )
    cloud = PointCloud("a", "lidar", 0, count, blob)
    assert world_points(scene, cloud) == _loop(scene, cloud)
