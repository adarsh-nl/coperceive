# SPDX-License-Identifier: Apache-2.0
"""Golden time alignment and known-pose transforms for the cooperative fixture.

Expected microseconds and metres are written out here. They are not derived
by calling the function under test a second way.
"""

from __future__ import annotations

import struct
from pathlib import Path

import pyarrow.parquet as pq
import pytest
from coperceive.diagnostics import alignment, shared_objects, track_center_spread_m
from coperceive.errors import ExtrapolationError, GapError, TimeQueryError, ToleranceError
from coperceive.model import AgentType, BoxFrame, Modality
from coperceive.query import TimeMode, query_agents, query_time
from coperceive.se3 import SE3
from coperceive.store import ANNOTATION_SCHEMA, read_points_at, read_scene, write_scene
from coperceive.transform import (
    body_frame,
    lookup_pose,
    sensor_frame,
    transform,
    world_frame,
)

from tests.cooperative import cooperative_scene

_Hit = tuple[bool, int | None, int | None]


def _hit(time_us: int, dt_us: int) -> _Hit:
    return (True, time_us, dt_us)


def _miss() -> _Hit:
    return (False, None, None)


# query_us, mode, tolerance_us, per-agent (matched, sample_us, dt_us)
_GOLDEN: tuple[tuple[int, TimeMode, int, dict[str, _Hit]], ...] = (
    (
        150_000,
        TimeMode.NEAREST,
        50_000,
        {"vehicle": _hit(100_000, -50_000), "uav": _hit(160_000, 10_000), "mast": _miss()},
    ),
    (
        150_000,
        TimeMode.NEAREST,
        49_999,
        {"vehicle": _miss(), "uav": _hit(160_000, 10_000), "mast": _miss()},
    ),
    (
        150_000,
        TimeMode.NEAREST,
        10_000,
        {"vehicle": _miss(), "uav": _hit(160_000, 10_000), "mast": _miss()},
    ),
    (
        150_000,
        TimeMode.NEAREST,
        9_999,
        {"vehicle": _miss(), "uav": _miss(), "mast": _miss()},
    ),
    (
        150_000,
        TimeMode.EXACT,
        0,
        {"vehicle": _miss(), "uav": _miss(), "mast": _miss()},
    ),
    (
        150_000,
        TimeMode.EXACT,
        10_000,
        {"vehicle": _miss(), "uav": _hit(160_000, 10_000), "mast": _miss()},
    ),
    (
        150_000,
        TimeMode.EXACT,
        50_000,
        {
            "vehicle": _hit(100_000, -50_000),
            "uav": _hit(160_000, 10_000),
            "mast": _miss(),
        },
    ),
    (
        150_000,
        TimeMode.BACKWARD,
        50_000,
        {
            "vehicle": _hit(100_000, -50_000),
            "uav": _hit(100_000, -50_000),
            "mast": _miss(),
        },
    ),
    (
        150_000,
        TimeMode.BACKWARD,
        49_999,
        {"vehicle": _miss(), "uav": _miss(), "mast": _miss()},
    ),
    (
        150_000,
        TimeMode.FORWARD,
        10_000,
        {"vehicle": _miss(), "uav": _hit(160_000, 10_000), "mast": _miss()},
    ),
    (
        150_000,
        TimeMode.FORWARD,
        9_999,
        {"vehicle": _miss(), "uav": _miss(), "mast": _miss()},
    ),
    (
        150_000,
        TimeMode.FORWARD,
        50_000,
        {
            "vehicle": _hit(200_000, 50_000),
            "uav": _hit(160_000, 10_000),
            "mast": _miss(),
        },
    ),
    (
        0,
        TimeMode.BACKWARD,
        0,
        {"vehicle": _hit(0, 0), "uav": _miss(), "mast": _hit(0, 0)},
    ),
    (
        0,
        TimeMode.EXACT,
        0,
        {"vehicle": _hit(0, 0), "uav": _miss(), "mast": _hit(0, 0)},
    ),
    (
        0,
        TimeMode.FORWARD,
        0,
        {"vehicle": _hit(0, 0), "uav": _miss(), "mast": _hit(0, 0)},
    ),
    (
        0,
        TimeMode.NEAREST,
        49_999,
        {"vehicle": _hit(0, 0), "uav": _miss(), "mast": _hit(0, 0)},
    ),
    (
        350_000,
        TimeMode.NEAREST,
        0,
        {"vehicle": _miss(), "uav": _miss(), "mast": _miss()},
    ),
    (
        350_000,
        TimeMode.FORWARD,
        0,
        {"vehicle": _miss(), "uav": _miss(), "mast": _miss()},
    ),
    (
        350_000,
        TimeMode.FORWARD,
        50_000,
        {"vehicle": _miss(), "uav": _miss(), "mast": _hit(400_000, 50_000)},
    ),
    (
        350_000,
        TimeMode.FORWARD,
        49_999,
        {"vehicle": _miss(), "uav": _miss(), "mast": _miss()},
    ),
    (
        350_000,
        TimeMode.BACKWARD,
        50_000,
        {"vehicle": _hit(300_000, -50_000), "uav": _miss(), "mast": _miss()},
    ),
    (
        350_000,
        TimeMode.NEAREST,
        50_000,
        {
            "vehicle": _hit(300_000, -50_000),
            "uav": _miss(),
            "mast": _hit(400_000, 50_000),
        },
    ),
    (
        200_000,
        TimeMode.NEAREST,
        200_000,
        {
            "vehicle": _hit(200_000, 0),
            "uav": _hit(200_000, 0),
            "mast": _hit(0, -200_000),
        },
    ),
    (
        200_000,
        TimeMode.NEAREST,
        199_999,
        {"vehicle": _hit(200_000, 0), "uav": _hit(200_000, 0), "mast": _miss()},
    ),
)


def test_golden_time_alignment_for_every_mode() -> None:
    scene = cooperative_scene()
    assert [agent.agent_type for agent in scene.agents] == [
        AgentType.VEHICLE,
        AgentType.UAV,
        AgentType.INFRASTRUCTURE,
    ]
    for query_us, mode, tolerance_us, expected in _GOLDEN:
        hits = {
            hit.agent_id: hit
            for hit in query_agents(scene, query_us, mode, tolerance_us=tolerance_us)
        }
        for agent_id, (matched, sample_us, dt_us) in expected.items():
            hit = hits[agent_id]
            assert hit.matched is matched
            assert hit.time_us == sample_us
            assert hit.dt_us == dt_us
            if matched:
                assert sample_us is not None and dt_us is not None
                assert dt_us == sample_us - query_us


def test_empty_overlap_drops_pairs_and_partial_overlap_keeps_them() -> None:
    scene = cooperative_scene()
    assert alignment(scene, 350_000, tolerance_us=0, mode=TimeMode.NEAREST) == ()
    rows = alignment(scene, 0, tolerance_us=0, mode=TimeMode.BACKWARD)
    pairs = {(row.dst_agent, row.src_agent) for row in rows}
    assert pairs == {("vehicle", "mast"), ("mast", "vehicle")}
    assert {row.dst_dt_us for row in rows} == {0}
    assert {row.src_dt_us for row in rows} == {0}


def test_query_time_tolerance_edges_raise() -> None:
    with pytest.raises(TimeQueryError):
        query_agents(cooperative_scene(), 0, TimeMode.EXACT, tolerance_us=-1)
    with pytest.raises(ToleranceError):
        query_time([0, 100_000], 50_000, TimeMode.NEAREST, tolerance_us=49_999)
    assert query_time([0, 100_000], 50_000, TimeMode.NEAREST, tolerance_us=50_000).time_us == 0


def test_known_poses_interpolation_and_frames() -> None:
    scene = cooperative_scene()
    vehicle = scene.agent("vehicle")
    uav = scene.agent("uav")
    mid = lookup_pose(vehicle, 50_000, max_gap_us=100_000)
    assert mid.tx == pytest.approx(5.0)
    assert mid.ty == pytest.approx(0.0)
    assert mid.tz == pytest.approx(0.0)
    with pytest.raises(GapError):
        lookup_pose(vehicle, 50_000, max_gap_us=99_999)
    jittered = lookup_pose(uav, 130_000, max_gap_us=60_000)
    assert jittered.tx == pytest.approx(13.0)
    assert jittered.ty == pytest.approx(5.25)
    assert jittered.tz == pytest.approx(25.0)
    assert lookup_pose(uav, 160_000, max_gap_us=0).ty == pytest.approx(5.5)
    with pytest.raises(GapError):
        lookup_pose(uav, 130_000, max_gap_us=59_999)
    with pytest.raises(ExtrapolationError):
        lookup_pose(uav, 49_999, max_gap_us=100_000)
    with pytest.raises(ExtrapolationError):
        lookup_pose(uav, 200_001, max_gap_us=100_000)
    with pytest.raises(ExtrapolationError):
        lookup_pose(vehicle, 300_001, max_gap_us=100_000)
    with pytest.raises(ExtrapolationError):
        lookup_pose(scene.agent("mast"), -1, max_gap_us=400_000)
    with pytest.raises(GapError):
        lookup_pose(scene.agent("mast"), 100_000, max_gap_us=0)
    static = lookup_pose(scene.agent("mast"), 100_000, max_gap_us=400_000)
    assert (static.tx, static.ty, static.tz) == pytest.approx((100.0, 0.0, 3.0))

    into_uav = transform(scene, body_frame("uav"), body_frame("vehicle"), 100_000, max_gap_us=0)
    assert into_uav.apply((0.0, 0.0, 0.0)) == pytest.approx((0.0, -5.0, -25.0))
    assert transform(scene, world_frame(), body_frame("vehicle"), 100_000, max_gap_us=0).apply(
        (0.0, 0.0, 0.0)
    ) == pytest.approx((10.0, 0.0, 0.0))
    assert transform(
        scene, world_frame(), sensor_frame("vehicle", "lidar_top"), 100_000, max_gap_us=0
    ).apply((0.0, 0.0, 0.0)) == pytest.approx((10.0, 0.0, 1.5))
    assert transform(
        scene,
        sensor_frame("vehicle", "cam_front"),
        sensor_frame("vehicle", "lidar_top"),
        100_000,
        max_gap_us=0,
    ).apply((0.0, 0.0, 0.0)) == pytest.approx((-1.2, 0.0, 0.1))
    assert transform(
        scene, world_frame(), sensor_frame("uav", "radar_down"), 100_000, max_gap_us=0
    ).apply((0.0, 0.0, 0.0)) == pytest.approx((10.0, 5.0, 24.8))
    assert transform(scene, world_frame(), world_frame(), 10**12, max_gap_us=0).almost_equal(
        SE3.identity()
    )
    with pytest.raises(ExtrapolationError):
        transform(scene, world_frame(), sensor_frame("uav", "radar_down"), 0, max_gap_us=0)


def test_only_shared_ids_become_objects_and_centers_agree() -> None:
    scene = cooperative_scene()
    promoted = shared_objects(scene.annotations)
    assert [(obj.track_id, obj.category) for obj in promoted] == [("42", "car")]
    assert [(obj.track_id, obj.category) for obj in scene.objects] == [("42", "car")]
    spread = track_center_spread_m(scene, "42", max_gap_us=0)
    assert spread == pytest.approx(0.0, abs=1e-12)
    assert track_center_spread_m(scene, "99", max_gap_us=0) is None
    with pytest.raises(KeyError):
        track_center_spread_m(scene, "missing", max_gap_us=0)


def test_cooperative_scene_round_trip(tmp_path: Path) -> None:
    scene = cooperative_scene()
    root = tmp_path / "scene"
    write_scene(scene, root)
    schema = pq.read_table(root / "annotations.parquet").schema
    assert schema.names == list(ANNOTATION_SCHEMA.names)
    assert schema.field("track_id").nullable
    assert schema.field("visibility").nullable
    assert schema.field("frame").nullable is False
    loaded = read_scene(root)
    assert loaded.scene_id == "coop-fixture"
    assert loaded.world_frame == "ENU"
    assert loaded.clock.unit == "us"
    assert [agent.agent_id for agent in loaded.agents] == ["vehicle", "uav", "mast"]
    assert loaded.agent("uav").agent_type is AgentType.UAV
    assert dict(loaded.agent("vehicle").platform) == {"name": "ground-car"}
    assert loaded.sensor("uav", "radar_down").modality is Modality.RADAR
    camera = loaded.sensor("vehicle", "cam_front")
    assert camera.modality is Modality.CAMERA
    assert camera.fx == pytest.approx(1000.0)
    assert camera.image_width_px == 1280
    assert loaded.sensor("vehicle", "lidar_top").body_from_sensor.tz == pytest.approx(1.5)
    unlabeled = [ann for ann in loaded.annotations if ann.track_id is None]
    assert {ann.category for ann in unlabeled} == {"sign", "cone"}
    cone = next(ann for ann in loaded.annotations if ann.category == "cone")
    assert cone.frame is BoxFrame.WORLD
    assert cone.center == pytest.approx((21.0, 2.0, 0.0))
    sign = next(ann for ann in loaded.annotations if ann.category == "sign")
    assert sign.visibility is None
    assert loaded.objects[0].track_id == "42"
    cloud = read_points_at(root, agent_id="vehicle", sensor_id="lidar_top", time_us=100_000)
    assert struct.unpack("<4f", cloud.xyz_i_f32) == pytest.approx((1.25, -0.5, 0.25, 0.75))
    again = transform(loaded, body_frame("uav"), body_frame("vehicle"), 100_000, max_gap_us=0)
    assert again.apply((0.0, 0.0, 0.0)) == pytest.approx((0.0, -5.0, -25.0))
