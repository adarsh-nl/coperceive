# SPDX-License-Identifier: Apache-2.0
# pyright: reportUnknownMemberType=false
"""The conformance checks catch what they are for (ADR 0017 §4).

A scene written straight from the world passes every check. The same recordings
under a pose or a box that was read the way adapters have got wrong before
(a radian taken for a degree, a yaw with the wrong sign, ``[roll, yaw, pitch]``
read as ``[roll, pitch, yaw]``, a frame mirrored in y, a camera left in body
axes, a LiDAR mount forgotten, boxes of the neighbouring frame) fail the check
that names the damage. No adapter is involved: this is the test of the tests.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import replace

import numpy as np
import pytest
from coperceive.model import BoxFrame

from tests.geomworld.checks import run_checks
from tests.geomworld.direct import expectation_of, scene_of
from tests.geomworld.scenes import standard_world
from tests.geomworld.world import (
    FORWARD_CAMERA,
    Agent,
    Pose,
    World,
    WorldObject,
    matrix_rpy,
    rpy_matrix,
)


@pytest.fixture(scope="module")
def world() -> World:
    return standard_world()


def _objects(world: World, bend: Callable[[WorldObject], WorldObject]) -> World:
    return replace(
        world, objects=tuple(tuple(bend(obj) for obj in frame) for frame in world.objects)
    )


def _agents(world: World, bend: Callable[[Agent], Agent]) -> World:
    return replace(world, agents=tuple(bend(agent) for agent in world.agents))


def _box_angles(
    change: Callable[[float, float, float], tuple[float, float, float]],
) -> Callable[[WorldObject], WorldObject]:
    def bend(obj: WorldObject) -> WorldObject:
        pose = obj.world_from_box
        rotation = rpy_matrix(*change(*matrix_rpy(pose.rotation)))
        return replace(obj, world_from_box=Pose(rotation, pose.translation))

    return bend


def _body_angles(
    change: Callable[[float, float, float], tuple[float, float, float]],
) -> Callable[[Agent], Agent]:
    def bend(agent: Agent) -> Agent:
        poses = tuple(
            Pose(rpy_matrix(*change(*matrix_rpy(pose.rotation))), pose.translation)
            for pose in agent.world_from_body
        )
        return replace(agent, world_from_body=poses)

    return bend


def _mirror_box(obj: WorldObject) -> WorldObject:
    pose = obj.world_from_box
    roll, pitch, yaw = matrix_rpy(pose.rotation)
    flipped = pose.translation * np.array([1.0, -1.0, 1.0])
    return replace(obj, world_from_box=Pose(rpy_matrix(-roll, pitch, -yaw), flipped))


def _camera_in_body_axes(agent: Agent) -> Agent:
    cameras = tuple(
        replace(
            camera,
            body_from_camera=Pose(
                camera.body_from_camera.rotation @ FORWARD_CAMERA.T,
                camera.body_from_camera.translation,
            ),
        )
        for camera in agent.cameras
    )
    return replace(agent, cameras=cameras)


def _lidar_on_the_body_origin(agent: Agent) -> Agent:
    return replace(agent, lidar=replace(agent.lidar, body_from_lidar=Pose.of((0.0, 0.0, 0.0))))


def _one_frame_late(world: World) -> World:
    last = world.n_frames - 1
    return replace(
        world, objects=tuple(world.objects[min(frame + 1, last)] for frame in range(world.n_frames))
    )


# name -> (how the world is bent, the checks that must then report something)
BENDS: dict[str, tuple[Callable[[World], World], tuple[str, ...]]] = {
    "box yaw in radians read as degrees": (
        lambda w: _objects(w, _box_angles(lambda r, p, y: (r, p, math.radians(y)))),
        ("boxes", "points", "cameras"),
    ),
    "box yaw with the wrong sign": (
        lambda w: _objects(w, _box_angles(lambda r, p, y: (r, p, -y))),
        ("boxes", "points", "cameras"),
    ),
    "box roll and pitch swapped": (
        lambda w: _objects(w, _box_angles(lambda r, p, y: (p, r, y))),
        ("boxes",),
    ),
    "boxes mirrored in y": (lambda w: _objects(w, _mirror_box), ("boxes", "points", "cameras")),
    "boxes of the next frame": (_one_frame_late, ("boxes",)),
    "agent yaw with the wrong sign": (
        lambda w: _agents(w, _body_angles(lambda r, p, y: (r, p, -y))),
        ("sensor-pose", "registration"),
    ),
    "agent angles [roll, yaw, pitch] read as [roll, pitch, yaw]": (
        lambda w: _agents(w, _body_angles(lambda r, p, y: (r, y, p))),
        ("sensor-pose", "registration"),
    ),
    "agent yaw in radians read as degrees": (
        lambda w: _agents(w, _body_angles(lambda r, p, y: (r, p, math.radians(y)))),
        ("sensor-pose", "registration"),
    ),
    "camera left in body axes": (
        lambda w: _agents(w, _camera_in_body_axes),
        ("sensor-pose", "cameras"),
    ),
    "lidar mount forgotten": (
        lambda w: _agents(w, _lidar_on_the_body_origin),
        ("sensor-pose", "registration"),
    ),
}


@pytest.mark.parametrize("box_frame", [BoxFrame.WORLD, BoxFrame.BODY], ids=lambda f: f.value)
def test_a_scene_written_straight_from_the_world_passes_every_check(
    world: World, box_frame: BoxFrame
) -> None:
    results = run_checks(world, scene_of(world, box_frame=box_frame), expectation_of(world))
    assert sorted(results) == [
        "boxes",
        "cameras",
        "frames",
        "points",
        "registration",
        "sensor-pose",
    ]
    for name, findings in results.items():
        assert not findings, f"{name}: {[str(f) for f in findings[:3]]}"


@pytest.mark.parametrize("name", sorted(BENDS))
def test_a_convention_read_wrongly_fails_the_check_that_names_it(world: World, name: str) -> None:
    bend, must_fail = BENDS[name]
    scene = scene_of(bend(world), recorded=world)
    results = run_checks(world, scene, expectation_of(world))
    for check in must_fail:
        assert results[check], f"{name}: the {check} check reported nothing"


def test_a_wrong_yaw_fails_across_the_sweep_and_not_on_one_object(world: World) -> None:
    """A degree/radian mix-up or a sign flip is not a corner case: most headings fail."""
    for name in ("box yaw in radians read as degrees", "box yaw with the wrong sign"):
        bend, _ = BENDS[name]
        findings = run_checks(world, scene_of(bend(world), recorded=world), expectation_of(world))[
            "boxes"
        ]
        failing = {finding.subject.split()[1] for finding in findings if finding.frame == 0}
        listed = {
            str(object_id)
            for agent in world.agents
            for object_id in world.seen_ids(agent.agent_id, 0)
        }
        assert len(failing) >= 0.8 * len(listed), f"{name}: only {len(failing)} of {len(listed)}"


def test_a_missing_sweep_and_a_missing_box_are_findings(world: World) -> None:
    scene = scene_of(world)
    expectation = expectation_of(world)
    no_cloud = replace(scene, point_clouds=scene.point_clouds[1:])
    assert run_checks(world, no_cloud, expectation)["points"]
    no_box = replace(scene, annotations=scene.annotations[1:])
    assert run_checks(world, no_box, expectation)["boxes"]
    no_agent = replace(scene, agents=scene.agents[1:], sensors=(), annotations=(), point_clouds=())
    assert list(run_checks(world, no_agent, expectation)) == ["frames"]
