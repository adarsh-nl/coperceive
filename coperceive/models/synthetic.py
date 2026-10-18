# SPDX-License-Identifier: Apache-2.0
"""The synthetic scene of ``coperceive model check`` (spec §Conformance).

Two agents, a vehicle and a uav, four pose times, a car and a pedestrian, a camera on each
agent and a lidar on the vehicle with a small sweep per sample. Written with
``coperceive.store.write_scene``; one PNG per camera sample when the model lists ``camera``,
``points.bin`` when it lists ``lidar``. No dataset bytes anywhere.
"""

from __future__ import annotations

import struct
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
from coperceive.models.spec import ModelSpec
from coperceive.se3 import SE3
from coperceive.store import write_scene

SCENE_ID = "synthetic-check"
TIMES_US = (0, 100_000, 200_000, 300_000)
AGENTS = (("vehicle", AgentType.VEHICLE), ("uav", AgentType.UAV))
CAMERAS = {"vehicle": "cam_front", "uav": "cam_down"}
LIDAR = "lidar_top"


def _points(time_us: int) -> PointCloud:
    """Twelve points on a ring 8 m ahead of the vehicle, with the sample's index as intensity."""
    values: list[float] = []
    index = TIMES_US.index(time_us)
    for k in range(12):
        angle = k / 12.0
        values.extend([8.0 + 0.5 * k, -2.0 + 4.0 * angle, 0.3, float(index)])
    return PointCloud("vehicle", LIDAR, time_us, 12, struct.pack(f"<{len(values)}f", *values))


def synthetic_scene() -> Scene:
    identity = SE3.identity()
    agents = (
        Agent(
            "vehicle",
            AgentType.VEHICLE,
            tuple(
                StampedPose(t, identity.translation(0.0 + t / 100_000, 0.0, 0.0)) for t in TIMES_US
            ),
            display_name="Vehicle 1",
        ),
        Agent(
            "uav",
            AgentType.UAV,
            tuple(StampedPose(t, identity.translation(5.0, 2.0, 30.0)) for t in TIMES_US),
            display_name="Drone 1",
        ),
    )
    sensors = (
        Sensor(LIDAR, "vehicle", Modality.LIDAR, identity.translation(0.0, 0.0, 1.5)),
        Sensor(
            CAMERAS["vehicle"],
            "vehicle",
            Modality.CAMERA,
            identity.translation(1.2, 0.0, 1.4),
            64,
            48,
            40.0,
            40.0,
            32.0,
            24.0,
        ),
        Sensor(
            CAMERAS["uav"],
            "uav",
            Modality.CAMERA,
            identity,
            64,
            48,
            40.0,
            40.0,
            32.0,
            24.0,
        ),
    )
    annotations: list[Annotation] = []
    for t in TIMES_US:
        annotations.append(
            Annotation(
                "vehicle",
                t,
                "1",
                "car",
                (10.0 + t / 100_000, 0.0, 0.8),
                (4.0, 1.9, 1.6),
                identity,
                1.0,
            )
        )
        annotations.append(
            Annotation(
                "uav", t, "1", "car", (10.0 + t / 100_000, 0.0, 0.8), (4.0, 1.9, 1.6), identity, 1.0
            )
        )
        annotations.append(
            Annotation(
                "vehicle", t, "2", "pedestrian", (6.0, 3.0, 0.9), (0.6, 0.6, 1.8), identity, 1.0
            )
        )
    images = tuple(
        ImageRef(agent_id, CAMERAS[agent_id], t, f"{agent_id}/{CAMERAS[agent_id]}/{t:07d}.png")
        for agent_id, _ in AGENTS
        for t in TIMES_US
    )
    return Scene(
        schema_version=SCHEMA_VERSION,
        scene_id=SCENE_ID,
        world_frame="ENU",
        clock=Clock("us", "synthetic conformance scene, 10 Hz"),
        agents=agents,
        sensors=sensors,
        annotations=tuple(annotations),
        objects=shared_objects(tuple(annotations)),
        point_clouds=tuple(_points(t) for t in TIMES_US),
        images=images,
    )


def write_synthetic_scene(root: Path, spec: ModelSpec) -> Path:
    """Write the scene under ``root/<scene id>`` with the media the model's modalities need."""
    scene = synthetic_scene()
    if "lidar" not in spec.inputs.modalities:
        scene = Scene(
            schema_version=scene.schema_version,
            scene_id=scene.scene_id,
            world_frame=scene.world_frame,
            clock=scene.clock,
            agents=scene.agents,
            sensors=scene.sensors,
            annotations=scene.annotations,
            objects=scene.objects,
            point_clouds=(),
            images=scene.images,
        )
    directory = root / scene.scene_id
    write_scene(scene, directory)
    if "camera" in spec.inputs.modalities:
        from PIL import Image

        for image in scene.images:
            target = directory / image.path
            target.parent.mkdir(parents=True, exist_ok=True)
            picture = Image.new("RGB", (64, 48), (40, 60, 80))
            picture.save(target)
    return directory


__all__ = ["SCENE_ID", "TIMES_US", "synthetic_scene", "write_synthetic_scene"]
