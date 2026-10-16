# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json
from pathlib import Path

import pytest
from coperceive.adapters.build import BuildError, ManifestAdapter
from coperceive.adapters.spec import builtin_manifest_path, load_manifest
from coperceive.griffin.raw import load_scene
from coperceive.model import AgentType, Modality
from tests.griffin_release import write_release


def _griffin() -> ManifestAdapter:
    return ManifestAdapter(load_manifest(builtin_manifest_path("griffin")))


def test_scenes_come_from_the_index_with_platform_values(tmp_path: Path) -> None:
    write_release(tmp_path, {"Town03-000": [11185, 11186], "Town03-001": [5]})
    refs = _griffin().scenes(tmp_path, {"subset": "25m"})
    assert [ref.scene_id for ref in refs] == ["Town03-000", "Town03-001"]
    assert refs[0].frames == (11185, 11186)
    assert dict(refs[0].platform) == {
        "cruise_altitude_m": "25.5",
        "map_name": "Carla/Maps/Town03",
        "subset": "25m",
    }


def test_params_are_checked_against_allowed(tmp_path: Path) -> None:
    write_release(tmp_path, {"Town03-000": [1]})
    with pytest.raises(BuildError, match="subset"):
        _griffin().scenes(tmp_path, {"subset": "30m"})
    with pytest.raises(BuildError, match="subset"):
        _griffin().scenes(tmp_path, {})


def test_index_files_must_be_equal(tmp_path: Path) -> None:
    write_release(tmp_path, {"Town03-000": [1]})
    (tmp_path / "drone-side" / "scene_infos.json").write_text("[]", encoding="utf-8")
    with pytest.raises(BuildError, match="must_equal"):
        _griffin().scenes(tmp_path, {"subset": "25m"})


def test_load_matches_the_hand_written_reader(tmp_path: Path) -> None:
    write_release(tmp_path, {"Town03-000": [11185, 11186]})
    adapter = _griffin()
    ref = adapter.scenes(tmp_path, {"subset": "25m"})[0]
    built = adapter.load(tmp_path, ref)
    expected = load_scene(tmp_path, "Town03-000")
    # The hand-written reader attaches platform values in convert.py, not raw.py.
    assert built.scene_id == expected.scene_id
    assert built.clock == expected.clock
    assert built.world_frame == expected.world_frame
    assert [a.agent_id for a in built.agents] == [a.agent_id for a in expected.agents]
    for mine, theirs in zip(built.agents, expected.agents):
        assert mine.agent_type is theirs.agent_type
        assert mine.poses == theirs.poses
        assert dict(mine.platform)["map_name"] == "Carla/Maps/Town03"
    assert built.sensors == expected.sensors
    assert built.annotations == expected.annotations
    assert built.objects == expected.objects
    assert built.images == expected.images
    assert built.point_clouds == expected.point_clouds


def test_label_variants_and_missing_angles(tmp_path: Path) -> None:
    write_release(tmp_path, {"Town03-000": [1]})
    label = tmp_path / "vehicle-side" / "label" / "000001.txt"
    label.write_text(
        "car 1 2 0.5 4 1.8 1.5 0 0 90 7 0.5\n"
        "car 1 2 0.5 4 1.8 1.5 0 0 90 8\n"
        "truck 1 2 0.5 4 1.8 1.5 90 9.0\n",
        encoding="utf-8",
    )
    adapter = _griffin()
    ref = adapter.scenes(tmp_path, {"subset": "25m"})[0]
    scene = adapter.load(tmp_path, ref)
    rows = [a for a in scene.annotations if a.agent_id == "vehicle"]
    assert [a.track_id for a in rows] == ["7", "8", "9"]
    assert [a.visibility for a in rows] == [0.5, None, None]
    assert rows[2].box_from_rpy.qx == 0.0 and rows[2].box_from_rpy.qy == 0.0
    assert rows[2].box_from_rpy.almost_equal(rows[0].box_from_rpy)
    assert scene.annotations == load_scene(tmp_path, "Town03-000").annotations


def test_unknown_label_width_is_an_error(tmp_path: Path) -> None:
    write_release(tmp_path, {"Town03-000": [1]})
    (tmp_path / "vehicle-side" / "label" / "000001.txt").write_text("car 1 2\n", encoding="utf-8")
    adapter = _griffin()
    ref = adapter.scenes(tmp_path, {"subset": "25m"})[0]
    with pytest.raises(BuildError, match="3 fields"):
        adapter.load(tmp_path, ref)


def test_sensor_modality_rules_and_derived_image_size(tmp_path: Path) -> None:
    write_release(tmp_path, {"Town03-000": [1]})
    (tmp_path / "drone-side" / "calib" / "lidar_virtual.json").write_text(
        json.dumps({"extrinsic": [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 2], [0, 0, 0, 1]]}),
        encoding="utf-8",
    )
    (tmp_path / "drone-side" / "calib" / "odd.json").write_text(
        json.dumps({"extrinsic": [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 2], [0, 0, 0, 1]]}),
        encoding="utf-8",
    )
    adapter = _griffin()
    scene = adapter.load(tmp_path, adapter.scenes(tmp_path, {"subset": "25m"})[0])
    assert scene.sensor("drone", "lidar_virtual").modality is Modality.LIDAR
    assert scene.sensor("drone", "odd").modality is Modality.CAMERA
    front = scene.sensor("vehicle", "front")
    assert (front.image_width_px, front.image_height_px) == (4, 2)
    assert scene.sensor("vehicle", "lidar_top").fx is None


def test_pose_timestamp_check_and_missing_pose(tmp_path: Path) -> None:
    write_release(tmp_path, {"Town03-000": [1, 2]})
    pose = tmp_path / "drone-side" / "pose" / "000002.json"
    payload = json.loads(pose.read_text(encoding="utf-8"))
    payload["timestamp"] = 3
    pose.write_text(json.dumps(payload), encoding="utf-8")
    adapter = _griffin()
    ref = adapter.scenes(tmp_path, {"subset": "25m"})[0]
    with pytest.raises(BuildError, match="timestamp"):
        adapter.load(tmp_path, ref)
    pose.unlink()
    with pytest.raises(BuildError, match="000002.json"):
        adapter.load(tmp_path, ref)


def test_lidar_directory_absent_skips_and_missing_file_fails(tmp_path: Path) -> None:
    write_release(tmp_path, {"Town03-000": [1, 2]}, lidar=False)
    adapter = _griffin()
    scene = adapter.load(tmp_path, adapter.scenes(tmp_path, {"subset": "25m"})[0])
    assert scene.point_clouds == ()
    assert scene.agent("drone").agent_type is AgentType.UAV
    other = tmp_path / "partial"
    write_release(other, {"Town03-000": [1, 2]}, lidar_frames={1})
    ref = adapter.scenes(other, {"subset": "25m"})[0]
    with pytest.raises(BuildError, match="000002.ply"):
        adapter.load(other, ref)


def test_check_reads_first_and_last_sample_and_writes_nothing(tmp_path: Path) -> None:
    write_release(tmp_path, {"Town03-000": [1, 2, 3]})
    before = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*"))
    report = _griffin().check(tmp_path, {"subset": "25m"})
    assert report.scenes == 1
    assert report.errors == ()
    counts = dict(report.counts["Town03-000"])
    assert counts["poses"] == 4  # two agents, first and last sample
    assert counts["boxes"] == 4
    assert counts["lidar"] == 2
    assert sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*")) == before
