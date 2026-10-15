# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import pytest
from pkg.errors import ExtrapolationError, GapError, ToleranceError
from pkg.query import TimeMode, query_time
from pkg.se3 import SE3
from pkg.transform import T_dst_from_src, lookup_pose

from tests.conftest import sample_scene


def test_modes_and_dt_sign() -> None:
    times = [0, 100_000, 200_000]
    exact = query_time(times, 100_000, TimeMode.EXACT, tolerance_us=0)
    assert exact.dt_us == 0
    backward = query_time(times, 150_000, TimeMode.BACKWARD, tolerance_us=100_000)
    assert backward.time_us == 100_000
    assert backward.dt_us == -50_000
    forward = query_time(times, 150_000, TimeMode.FORWARD, tolerance_us=100_000)
    assert forward.time_us == 200_000
    assert forward.dt_us == 50_000
    nearest = query_time(times, 140_000, TimeMode.NEAREST, tolerance_us=100_000)
    assert nearest.time_us == 100_000
    with pytest.raises(ToleranceError):
        query_time(times, 150_000, TimeMode.NEAREST, tolerance_us=10)


def test_nearest_tie_prefers_earlier_sample() -> None:
    hit = query_time([0, 100], 50, TimeMode.NEAREST, tolerance_us=50)
    assert hit.time_us == 0


def test_lookup_interpolates_and_refuses_gap_and_extrapolation() -> None:
    scene = sample_scene()
    vehicle = scene.agent("vehicle")
    mid = lookup_pose(vehicle, 50_000, max_gap_us=100_000)
    assert mid.tx == pytest.approx(0.5)
    with pytest.raises(GapError):
        lookup_pose(vehicle, 50_000, max_gap_us=10)
    with pytest.raises(ExtrapolationError):
        lookup_pose(vehicle, 300_000, max_gap_us=100_000)


def test_transform_between_agents() -> None:
    scene = sample_scene()
    eye = T_dst_from_src(scene, "vehicle", "vehicle", 100_000, max_gap_us=0)
    assert eye.almost_equal(SE3.identity())
    # At t=100000 the vehicle is at x=1 and the drone is at x=1, z=25.
    into_drone = T_dst_from_src(scene, "drone", "vehicle", 100_000, max_gap_us=0)
    # A point at the vehicle origin maps to (0, 0, -25) in the drone body.
    x, y, z = into_drone.apply((0.0, 0.0, 0.0))
    assert x == pytest.approx(0.0)
    assert y == pytest.approx(0.0)
    assert z == pytest.approx(-25.0)
