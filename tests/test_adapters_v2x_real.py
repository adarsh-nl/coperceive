# SPDX-License-Identifier: Apache-2.0
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
"""Synthetic V2X-Real OpenCOOD-style fixture: check, load, ingest, transforms."""

from __future__ import annotations

import math
import shutil
import struct
from pathlib import Path

import pytest
from coperceive.adapters.ingest import ingest
from coperceive.adapters.registry import list_adapters, resolve_adapter
from coperceive.model import AgentType, Scene
from coperceive.store import read_scene
from coperceive.validate import validate_root

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "v2x_real"
SCENE = "2023-03-17-15-53-02_1_0"


def _points(scene: Scene, agent_id: str, time_us: int) -> list[tuple[float, float, float, float]]:
    cloud = next(
        item for item in scene.point_clouds if item.agent_id == agent_id and item.time_us == time_us
    )
    return [struct.unpack_from("<4f", cloud.xyz_i_f32, index * 16) for index in range(cloud.count)]


def test_v2x_real_is_listed_as_a_builtin_adapter() -> None:
    names = {row.name: row.source for row in list_adapters()}
    assert names["v2x-real"] == "builtin"


def test_adapter_check_accepts_the_synthetic_v2x_real_fixture() -> None:
    adapter = resolve_adapter("v2x-real")
    report = adapter.check(FIXTURE_ROOT, {"split": "train"})
    assert report.scenes == 1
    assert report.errors == ()
    counts = dict(report.counts[SCENE])
    assert counts["agents"] == 2
    assert counts["poses"] == 4
    assert counts["boxes"] == 8
    assert counts["lidar"] == 4
    assert counts["sensors"] == 2


def test_split_is_required_and_limited_to_train_validate_test() -> None:
    adapter = resolve_adapter("v2x-real")
    with pytest.raises(Exception, match="split"):
        adapter.scenes(FIXTURE_ROOT, {})
    with pytest.raises(Exception, match="train, validate, test"):
        adapter.scenes(FIXTURE_ROOT, {"split": "test_culver_city"})


def test_agents_clock_classes_and_lidar_intensity_fill() -> None:
    adapter = resolve_adapter("v2x-real")
    ref = adapter.scenes(FIXTURE_ROOT, {"split": "train"})[0]
    scene = adapter.load(FIXTURE_ROOT, ref)

    assert scene.world_frame == "v2x-real-world"
    assert scene.agent("1").agent_type == AgentType.VEHICLE
    assert scene.agent("-1").agent_type == AgentType.INFRASTRUCTURE
    assert [pose.time_us for pose in scene.agent("1").poses] == [0, 100_000]
    assert [pose.time_us for pose in scene.agent("-1").poses] == [0, 100_000]

    assert {sensor.modality.value for sensor in scene.sensors} == {"lidar"}
    assert scene.images == ()

    categories = {ann.category for ann in scene.annotations}
    assert categories == {"car", "pedestrian", "truck"}

    # The synthetic .bin stores non-zero 4th floats, which the manifest must zero-fill.
    for _, _, _, intensity in _points(scene, "1", 0):
        assert intensity == 0.0
    for _, _, _, intensity in _points(scene, "-1", 0):
        assert intensity == 0.0


def test_hand_computed_v2x_real_pose_box_and_point_transforms() -> None:
    adapter = resolve_adapter("v2x-real")
    ref = adapter.scenes(FIXTURE_ROOT, {"split": "train"})[0]
    scene = adapter.load(FIXTURE_ROOT, ref)

    pose_v1 = scene.agent("1").poses[0]
    # Provisional ADR 0012 y-reflection: source (10, 20, 1.9) -> schema (10, -20, 1.9).
    assert (
        pose_v1.world_from_body.tx,
        pose_v1.world_from_body.ty,
        pose_v1.world_from_body.tz,
    ) == pytest.approx((10.0, -20.0, 1.9))

    car = next(
        a for a in scene.annotations if a.agent_id == "1" and a.time_us == 0 and a.track_id == "101"
    )
    assert car.category == "car"
    assert car.center == pytest.approx((16.0, -30.0, 0.75))

    # Vehicle 1's lidar point cloud transformed to world must match the reflected box centre.
    world_points = [
        pose_v1.world_from_body.apply((x, y, z)) for x, y, z, _ in _points(scene, "1", 0)
    ]
    assert min(math.dist(pt, (16.0, -30.0, 0.75)) for pt in world_points) < 1e-4
    assert min(math.dist(pt, (16.0, 30.0, 0.75)) for pt in world_points) > 1.0

    # Infrastructure -1's Truck 203 is at source (20, -5, 1.5) with yaw 1.5 degrees.
    truck = next(
        a
        for a in scene.annotations
        if a.agent_id == "-1" and a.time_us == 0 and a.track_id == "203"
    )
    assert truck.category == "truck"
    assert truck.center == pytest.approx((20.0, 5.0, 1.5))
    rotated = truck.box_from_rpy.apply((1.0, 0.0, 0.0))
    assert rotated[1] == pytest.approx(-math.sin(math.radians(1.5)), abs=1e-6)


def test_v2x_real_ingest_produces_a_valid_schema1_scene(tmp_path: Path) -> None:
    adapter = resolve_adapter("v2x-real")
    out_dir = tmp_path / "scenes"
    report = ingest(adapter, FIXTURE_ROOT, out_dir, params={"split": "train"}, workers=1)
    assert report.written == (SCENE,)
    assert report.errors == ()

    scene_dir = out_dir / SCENE
    assert (scene_dir / "COMPLETE.json").is_file()
    roundtrip = read_scene(scene_dir)
    assert roundtrip.scene_id == SCENE
    assert roundtrip.world_frame == "v2x-real-world"
    assert len(roundtrip.annotations) == 8

    validation = validate_root(out_dir)
    aggregate = validation["aggregate"]
    assert isinstance(aggregate, dict)
    assert aggregate["scenes_read"] == 1
    assert aggregate["scenes_failed"] == 0
    assert aggregate["hard_failure_count"] == 0


def test_missing_agents_directory_fails_check(tmp_path: Path) -> None:
    copy_root = tmp_path / "v2x_real"
    shutil.copytree(FIXTURE_ROOT, copy_root)
    for agent in ("1", "-1"):
        shutil.rmtree(copy_root / "train" / SCENE / agent)
    with pytest.raises(Exception, match="no agents"):
        resolve_adapter("v2x-real").check(copy_root, {"split": "train"})
