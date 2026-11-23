# SPDX-License-Identifier: Apache-2.0
# pyright: reportUnknownMemberType=false
"""The synthetic world is right before anything is checked against it (ADR 0017 §2).

Hand-worked cases for the ray caster and the camera, and the properties the
conformance suite leans on: a return lies on the surface of the object it is
tagged with, nothing is seen through another object, the headings are the sweep
the page says, and the generator is deterministic.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from tests.geomworld.scenes import (
    CAV,
    EGO,
    MOVING_IDS,
    N_FRAMES,
    RSU,
    UAV,
    headings_rad,
    standard_world,
    subset,
)
from tests.geomworld.world import (
    CORNER_SIGNS,
    Camera,
    Lidar,
    Pose,
    World,
    WorldObject,
    camera_pose,
    cast_rays,
    inside_box,
    matrix_rpy,
    rpy_matrix,
)


@pytest.fixture(scope="module")
def world() -> World:
    return standard_world()


def test_a_ray_along_x_hits_the_near_face_of_a_box_ahead() -> None:
    # A 2 m cube centred 5 m ahead: its near face is the plane x = 4.
    lidar = Lidar("l", Pose.of((0.0, 0.0, 0.0)), azimuth_steps=4, elevations_deg=(0.0,))
    cube = WorldObject(7, "car", (2.0, 2.0, 2.0), Pose.of((5.0, 0.0, 0.0)))
    returns = cast_rays(lidar, Pose.of((0.0, 0.0, 0.0)), (cube,))
    assert returns.object_ids.tolist() == [7]
    assert returns.points[0] == pytest.approx((4.0, 0.0, 0.0), abs=1e-12)


def test_a_turned_lidar_reports_the_hit_in_its_own_frame() -> None:
    # The LiDAR faces world +y; the cube is 5 m along world +y, so it is 4 m ahead of the LiDAR.
    lidar = Lidar("l", Pose.of((0.0, 0.0, 0.0)), azimuth_steps=4, elevations_deg=(0.0,))
    cube = WorldObject(3, "car", (2.0, 2.0, 2.0), Pose.of((0.0, 5.0, 0.0)))
    world_from_lidar = Pose.of((0.0, 0.0, 0.0), (0.0, 0.0, math.pi / 2.0))
    returns = cast_rays(lidar, world_from_lidar, (cube,))
    assert returns.object_ids.tolist() == [3]
    assert returns.points[0] == pytest.approx((4.0, 0.0, 0.0), abs=1e-12)
    assert world_from_lidar.apply(returns.points)[0] == pytest.approx((0.0, 4.0, 0.0), abs=1e-12)


def test_the_nearer_box_hides_the_farther_one_and_a_box_around_the_sensor_is_not_seen() -> None:
    lidar = Lidar("l", Pose.of((0.0, 0.0, 0.0)), azimuth_steps=4, elevations_deg=(0.0,))
    near = WorldObject(1, "car", (2.0, 2.0, 2.0), Pose.of((5.0, 0.0, 0.0)))
    far = WorldObject(2, "car", (2.0, 2.0, 2.0), Pose.of((9.0, 0.0, 0.0)))
    own_body = WorldObject(3, "car", (4.0, 2.0, 2.0), Pose.of((0.0, 0.0, 0.0)))
    returns = cast_rays(lidar, Pose.of((0.0, 0.0, 0.0)), (far, near, own_body))
    assert returns.object_ids.tolist() == [1]


def test_a_forward_camera_puts_the_point_ahead_on_the_principal_point() -> None:
    camera = Camera("c", camera_pose((0.0, 0.0, 0.0)), 800, 600, 400.0, 400.0, 400.0, 300.0)
    camera_from_body = camera.body_from_camera.inverse()
    # Body frame: x forward, y left, z up. Ahead; ahead and 1 m right; ahead and 1 m up.
    points = camera_from_body.apply(
        np.array([[10.0, 0.0, 0.0], [10.0, -1.0, 0.0], [10.0, 0.0, 1.0]])
    )
    pixels, in_front = camera.project(points)
    assert in_front.all()
    assert pixels[0] == pytest.approx((400.0, 300.0), abs=1e-9)
    assert pixels[1] == pytest.approx((440.0, 300.0), abs=1e-9)  # right is +u: 400 * 1 / 10
    assert pixels[2] == pytest.approx((400.0, 260.0), abs=1e-9)  # up is -v


def test_rpy_round_trips_and_composes_as_yaw_pitch_roll() -> None:
    for roll, pitch, yaw in ((0.1, -0.2, 3.0), (-0.3, 0.25, -3.1), (0.0, 0.0, math.pi)):
        got = matrix_rpy(rpy_matrix(roll, pitch, yaw))
        assert got[0] == pytest.approx(roll, abs=1e-12)
        assert got[1] == pytest.approx(pitch, abs=1e-12)
        assert math.remainder(got[2] - yaw, 2.0 * math.pi) == pytest.approx(0.0, abs=1e-12)
    # Yaw 90° takes body x onto world y; pitch 90° takes body x onto world -z.
    assert rpy_matrix(0.0, 0.0, math.pi / 2.0) @ np.array([1.0, 0.0, 0.0]) == pytest.approx(
        (0.0, 1.0, 0.0), abs=1e-12
    )
    assert rpy_matrix(0.0, math.pi / 2.0, 0.0) @ np.array([1.0, 0.0, 0.0]) == pytest.approx(
        (0.0, 0.0, -1.0), abs=1e-12
    )


def test_every_return_lies_on_the_surface_of_its_object(world: World) -> None:
    for agent in world.agents:
        for frame in range(world.n_frames):
            returns = world.lidar_returns(agent.agent_id, frame)
            assert len(returns.points) > 1000
            points = world.world_from_lidar(agent.agent_id, frame).apply(returns.points)
            for obj in world.objects[frame]:
                mine = points[returns.object_ids == obj.object_id]
                if len(mine) == 0:
                    continue
                # On the box: inside it grown by a nanometre, outside it shrunk by one.
                assert inside_box(mine, obj.world_from_box, obj.size_lwh, margin_m=1e-9).all()
                assert not inside_box(mine, obj.world_from_box, obj.size_lwh, margin_m=-1e-9).any()
                # And not inside any other object: nothing is seen through a box.
                for other in world.objects[frame]:
                    if other.object_id != obj.object_id:
                        assert not inside_box(
                            mine, other.world_from_box, other.size_lwh, margin_m=-1e-6
                        ).any()


def test_the_headings_are_the_sweep_and_its_edges(world: World) -> None:
    headings = headings_rad()
    assert len(headings) == 42 == len(world.objects[0])
    assert headings[0] == pytest.approx(-math.pi) and headings[35] == pytest.approx(
        math.radians(170.0)
    )
    assert headings[36:] == (
        math.pi,
        -math.pi,
        math.pi - 1e-6,
        -math.pi + 1e-6,
        math.pi / 2.0,
        -math.pi / 2.0,
    )
    for obj, heading in zip(world.objects[0], headings, strict=True):
        _roll, _pitch, yaw = matrix_rpy(obj.world_from_box.rotation)
        assert math.remainder(yaw - heading, 2.0 * math.pi) == pytest.approx(0.0, abs=1e-9)
    tilted = [
        obj for obj in world.objects[0] if abs(matrix_rpy(obj.world_from_box.rotation)[0]) > 0.05
    ]
    assert len(tilted) == 14


def test_every_agent_is_turned_and_tilted_and_sees_most_of_the_world(world: World) -> None:
    assert [agent.agent_id for agent in world.agents] == [EGO, CAV, RSU, UAV]
    assert [agent.kind for agent in world.agents] == ["vehicle", "vehicle", "infrastructure", "uav"]
    everyone: set[int] = set()
    for agent in world.agents:
        roll, pitch, yaw = matrix_rpy(agent.world_from_body[0].rotation)
        assert abs(yaw) > 0.05 and (abs(roll) > 0.005 or abs(pitch) > 0.005)
        seen = world.seen_ids(agent.agent_id, 0, min_points=10)
        assert len(seen) >= 25
        everyone.update(seen)
    assert len(everyone) >= 40
    # The driving vehicle changes position and heading between frames.
    first, last = world.agent(EGO).world_from_body[0], world.agent(EGO).world_from_body[-1]
    assert np.linalg.norm(last.translation - first.translation) > 1.0
    assert matrix_rpy(last.rotation)[2] - matrix_rpy(first.rotation)[2] == pytest.approx(0.1)


def test_three_objects_move_and_the_rest_stand_still(world: World) -> None:
    assert world.n_frames == N_FRAMES == 3
    for first, last in zip(world.objects[0], world.objects[-1], strict=True):
        moved = float(
            np.linalg.norm(last.world_from_box.translation - first.world_from_box.translation)
        )
        assert (moved > 0.5) == (first.object_id in MOVING_IDS)


def test_camera_truth_matches_a_direct_projection(world: World) -> None:
    truth = world.camera_truth(EGO, "front", 1)
    obj = next(
        o
        for o in world.objects[1]
        if truth[o.object_id].in_front.all()
        and 0.0 <= truth[o.object_id].box_2d[0]
        and truth[o.object_id].box_2d[2] < 800.0
    )
    camera = world.agent(EGO).cameras[0]
    camera_from_world = world.world_from_camera(EGO, "front", 1).inverse()
    half = np.array(obj.size_lwh) / 2.0
    for corner, pixel in zip(CORNER_SIGNS * half, truth[obj.object_id].pixels, strict=True):
        in_camera = camera_from_world.apply(obj.world_from_box.apply(corner[None, :]))[0]
        assert in_camera[2] > 0.0
        assert camera.fx * in_camera[0] / in_camera[2] + camera.cx == pytest.approx(
            pixel[0], abs=1e-9
        )
        assert camera.fy * in_camera[1] / in_camera[2] + camera.cy == pytest.approx(
            pixel[1], abs=1e-9
        )


def test_the_world_is_deterministic_and_a_subset_keeps_the_objects(world: World) -> None:
    again = standard_world()
    for agent in world.agents:
        a = world.lidar_returns(agent.agent_id, 2)
        b = again.lidar_returns(agent.agent_id, 2)
        assert np.array_equal(a.points, b.points) and np.array_equal(a.object_ids, b.object_ids)
    pair = subset(world, (EGO, CAV))
    assert [agent.agent_id for agent in pair.agents] == [EGO, CAV]
    assert pair.objects == world.objects
