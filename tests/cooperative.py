# SPDX-License-Identifier: Apache-2.0
"""Synthetic cooperative scene with known poses.

Three agents, one world frame, one clock. The numbers below are the fixture
contract. Tests quote them as literals rather than recomputing them from the
implementation.

* ``vehicle`` — type vehicle, 10 Hz. Times 0, 100_000, 200_000, 300_000 us.
  Translations (0, 0, 0), (10, 0, 0), (20, 0, 0), (30, 0, 0).
* ``uav`` — type uav, nominal 20 Hz, partial overlap [50_000, 200_000].
  The third stamp is 10_000 us late (160_000 instead of 150_000) and 0.5 m
  off in y: (5, 5, 25), (10, 5, 25), (16, 5.5, 25), (20, 5, 25).
* ``mast`` — type infrastructure, static (100, 0, 3), stamps 0 and 400_000.
  That span starts with the vehicle and continues after both moving agents.
"""

from __future__ import annotations

import struct

from coperceive.diagnostics import shared_objects
from coperceive.model import (
    SCHEMA_VERSION,
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


def _pose(time_us: int, x: float, y: float, z: float) -> StampedPose:
    return StampedPose(time_us, SE3.identity().translation(x, y, z))


def cooperative_scene() -> Scene:
    vehicle = Agent(
        agent_id="vehicle",
        agent_type=AgentType.VEHICLE,
        platform=(("name", "ground-car"),),
        poses=(
            _pose(0, 0.0, 0.0, 0.0),
            _pose(100_000, 10.0, 0.0, 0.0),
            _pose(200_000, 20.0, 0.0, 0.0),
            _pose(300_000, 30.0, 0.0, 0.0),
        ),
    )
    uav = Agent(
        agent_id="uav",
        agent_type=AgentType.UAV,
        platform=(("name", "quad"),),
        poses=(
            _pose(50_000, 5.0, 5.0, 25.0),
            _pose(100_000, 10.0, 5.0, 25.0),
            _pose(160_000, 16.0, 5.5, 25.0),
            _pose(200_000, 20.0, 5.0, 25.0),
        ),
    )
    mast = Agent(
        agent_id="mast",
        agent_type=AgentType.INFRASTRUCTURE,
        platform=(("site", "pole-a"),),
        poses=(
            _pose(0, 100.0, 0.0, 3.0),
            _pose(400_000, 100.0, 0.0, 3.0),
        ),
    )
    box = SE3.identity()
    annotations = (
        Annotation(
            "vehicle",
            100_000,
            "42",
            "car",
            (8.0, 1.0, 0.5),
            (4.0, 2.0, 1.5),
            box,
            1.0,
            BoxFrame.BODY,
        ),
        Annotation(
            "uav",
            100_000,
            "42",
            "car",
            (8.0, -4.0, -24.5),
            (4.0, 2.0, 1.5),
            box,
            0.4,
            BoxFrame.BODY,
        ),
        Annotation(
            "vehicle",
            200_000,
            "42",
            "car",
            (0.0, 0.0, 0.0),
            (4.0, 2.0, 1.5),
            box,
            None,
            BoxFrame.BODY,
        ),
        Annotation(
            "vehicle",
            100_000,
            "99",
            "pedestrian",
            (1.0, 1.0, 1.0),
            (0.6, 0.6, 1.7),
            box,
            None,
        ),
        Annotation("uav", 100_000, None, "sign", (0.0, 0.0, 0.0), (0.2, 0.8, 1.2), box, None),
        Annotation("vehicle", 100_000, "5", "car", (0.0, 0.0, 0.0), (4.0, 2.0, 1.5), box, 1.0),
        Annotation("uav", 100_000, "5", "truck", (0.0, 0.0, 0.0), (6.0, 2.5, 2.0), box, 1.0),
        Annotation("mast", 0, "7", "pole", (0.0, 0.0, 1.0), (0.3, 0.3, 4.0), box, None),
        Annotation(
            "vehicle",
            200_000,
            None,
            "cone",
            (21.0, 2.0, 0.0),
            (0.3, 0.3, 0.7),
            box,
            None,
            BoxFrame.WORLD,
        ),
    )
    sensors = (
        Sensor(
            "lidar_top",
            "vehicle",
            Modality.LIDAR,
            SE3.identity().translation(0.0, 0.0, 1.5),
        ),
        Sensor(
            "cam_front",
            "vehicle",
            Modality.CAMERA,
            SE3.identity().translation(1.2, 0.0, 1.4),
            image_width_px=1280,
            image_height_px=720,
            fx=1000.0,
            fy=1000.0,
            cx=640.0,
            cy=360.0,
        ),
        Sensor(
            "radar_down",
            "uav",
            Modality.RADAR,
            SE3.identity().translation(0.0, 0.0, -0.2),
        ),
    )
    cloud = PointCloud(
        agent_id="vehicle",
        sensor_id="lidar_top",
        time_us=100_000,
        count=1,
        xyz_i_f32=struct.pack("<4f", 1.25, -0.5, 0.25, 0.75),
    )
    return Scene(
        schema_version=SCHEMA_VERSION,
        scene_id="coop-fixture",
        world_frame="ENU",
        clock=Clock(
            unit="us", scale_note="synthetic cooperative fixture; time base is microseconds"
        ),
        agents=(vehicle, uav, mast),
        sensors=sensors,
        annotations=annotations,
        objects=shared_objects(annotations),
        point_clouds=(cloud,),
        images=(ImageRef("vehicle", "cam_front", 100_000, "vehicle/cam_front/0100000.png"),),
    )
