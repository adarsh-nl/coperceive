# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import pytest
from coperceive.errors import TimeQueryError
from coperceive.query import TimeMode

from tests.cooperative import cooperative_scene


def test_at_exact_time_reports_every_agent_and_a_miss() -> None:
    sample = cooperative_scene().at(100_000, tolerance=0)
    assert sample.query_us == 100_000
    assert sample.tolerance_us == 0
    assert sample.mode is TimeMode.NEAREST
    assert [row.agent_id for row in sample.agents] == ["vehicle", "uav", "mast"]

    vehicle = sample.agent("vehicle")
    assert vehicle.matched is True
    assert vehicle.time_us == 100_000
    assert vehicle.dt_us == 0
    assert vehicle.pose is not None
    assert vehicle.pose.tx == 10.0
    assert vehicle.pose.ty == 0.0
    assert vehicle.pose.tz == 0.0
    assert [ann.track_id for ann in vehicle.annotations] == ["42", "99", "5"]
    assert [image.path for image in vehicle.images] == ["vehicle/cam_front/0100000.png"]
    assert len(vehicle.point_clouds) == 1
    assert vehicle.point_clouds[0].count == 1
    assert [sensor.sensor_id for sensor in vehicle.sensors] == ["lidar_top", "cam_front"]

    uav = sample.agent("uav")
    assert uav.matched is True
    assert uav.dt_us == 0
    assert uav.pose is not None
    assert (uav.pose.tx, uav.pose.ty, uav.pose.tz) == (10.0, 5.0, 25.0)
    assert [ann.track_id for ann in uav.annotations] == ["42", None, "5"]
    assert [sensor.sensor_id for sensor in uav.sensors] == ["radar_down"]
    assert uav.images == ()
    assert uav.point_clouds == ()

    mast = sample.agent("mast")
    assert mast.matched is False
    assert mast.time_us is None
    assert mast.dt_us is None
    assert mast.pose is None
    assert mast.annotations == ()
    assert mast.images == ()
    assert mast.point_clouds == ()
    assert mast.sensors == ()


def test_tie_at_zero_tolerance_is_a_reported_miss() -> None:
    sample = cooperative_scene().at(50_000, tolerance=0)
    vehicle = sample.agent("vehicle")
    assert vehicle.matched is False
    assert vehicle.pose is None
    assert vehicle.dt_us is None
    assert [sensor.sensor_id for sensor in vehicle.sensors] == ["lidar_top", "cam_front"]
    uav = sample.agent("uav")
    assert uav.matched is True
    assert uav.time_us == 50_000
    assert uav.dt_us == 0
    assert sample.agent("mast").matched is False


def test_tolerance_keeps_the_earlier_tie_and_does_not_interpolate() -> None:
    sample = cooperative_scene().at(50_000, tolerance=50_000)
    vehicle = sample.agent("vehicle")
    assert vehicle.matched is True
    assert vehicle.time_us == 0
    assert vehicle.dt_us == -50_000
    assert vehicle.pose is not None
    assert (vehicle.pose.tx, vehicle.pose.ty, vehicle.pose.tz) == (0.0, 0.0, 0.0)
    assert vehicle.annotations == ()
    assert vehicle.images == ()
    assert vehicle.point_clouds == ()
    uav = sample.agent("uav")
    assert uav.time_us == 50_000
    assert uav.dt_us == 0
    assert uav.pose is not None
    assert (uav.pose.tx, uav.pose.ty, uav.pose.tz) == (5.0, 5.0, 25.0)
    mast = sample.agent("mast")
    assert mast.matched is True
    assert mast.time_us == 0
    assert mast.dt_us == -50_000
    assert mast.pose is not None
    assert (mast.pose.tx, mast.pose.ty, mast.pose.tz) == (100.0, 0.0, 3.0)
    assert [ann.track_id for ann in mast.annotations] == ["7"]


def test_invalid_tolerance_raises_and_exact_mode_still_reports_a_miss() -> None:
    scene = cooperative_scene()
    with pytest.raises(TimeQueryError):
        scene.at(100_000, tolerance=-1)
    with pytest.raises(TimeQueryError):
        scene.at(100_000, tolerance=True)  # type: ignore[arg-type]
    exact = scene.at(100_000, tolerance=0, mode=TimeMode.EXACT)
    assert exact.agent("vehicle").dt_us == 0
    assert exact.agent("uav").dt_us == 0
    assert exact.agent("mast").matched is False
    with pytest.raises(KeyError):
        exact.agent("missing")
