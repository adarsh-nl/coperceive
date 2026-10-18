# SPDX-License-Identifier: Apache-2.0
"""The OPV2V and V2XSet manifests on a synthetic OpenCOOD-style release (ADR 0012)."""

from __future__ import annotations

from pathlib import Path

import pytest
from coperceive.adapters.build import ManifestAdapter
from coperceive.adapters.registry import list_adapters, resolve_adapter
from coperceive.adapters.spec import builtin_manifest_path, load_manifest

from tests.opencood_release import AgentTrack, Box, SceneSpec, two_cav_scene, write_release


def _adapter(name: str = "opv2v") -> ManifestAdapter:
    return ManifestAdapter(load_manifest(builtin_manifest_path(name)))


def test_both_manifests_load_and_list() -> None:
    names = {row.name: row.source for row in list_adapters()}
    assert names["opv2v"] == "builtin" and names["v2xset"] == "builtin"
    for name in ("opv2v", "v2xset"):
        spec = load_manifest(builtin_manifest_path(name))
        assert spec.licence is not None
        assert spec.licence.status == "unknown"
        assert any("github.com" in link for link in spec.licence.links)
        assert spec.clock.scale_us == 50000
        assert "assumption" in spec.clock.scale_note
        assert "fixed_delta_seconds" in spec.clock.scale_note
        assert spec.conventions.source_handedness == "left"
    assert load_manifest(builtin_manifest_path("opv2v")).params["split"].allowed == (
        "train",
        "validate",
        "test",
        "test_culver_city",
    )
    assert load_manifest(builtin_manifest_path("v2xset")).params["split"].allowed == (
        "train",
        "validate",
        "test",
    )


def test_scenes_and_agents_are_discovered_per_split(tmp_path: Path) -> None:
    write_release(
        tmp_path, "train", [two_cav_scene("2021_08_16_22_26_54"), two_cav_scene("2021_08_18_09_02_56")]
    )
    write_release(tmp_path, "validate", [two_cav_scene("2021_08_20_21_48_35")])
    adapter = _adapter()
    refs = adapter.scenes(tmp_path, {"split": "train"})
    assert [ref.scene_id for ref in refs] == ["2021_08_16_22_26_54", "2021_08_18_09_02_56"]
    assert refs[0].samples == (69, 71, 73)
    assert dict(refs[0].platform) == {"split": "train"}
    scene = adapter.load(tmp_path, refs[0])
    assert [agent.agent_id for agent in scene.agents] == ["641", "650"]
    assert [ref.scene_id for ref in adapter.scenes(tmp_path, {"split": "validate"})] == [
        "2021_08_20_21_48_35"
    ]


def test_clock_is_fifty_ms_per_tick(tmp_path: Path) -> None:
    write_release(tmp_path, "train", [two_cav_scene()])
    adapter = _adapter()
    scene = adapter.load(tmp_path, adapter.scenes(tmp_path, {"split": "train"})[0])
    assert scene.pose_times("641") == (3_450_000, 3_550_000, 3_650_000)
    assert "fixed_delta_seconds" in scene.clock.scale_note


def test_agents_with_different_stems_keep_their_own_samples(tmp_path: Path) -> None:
    short = AgentTrack("650", poses=[((10.0, 40.0, 1.9), (0.0, 90.0, 0.0))])  # stem 69 only
    full = two_cav_scene().agents[0]
    write_release(tmp_path, "train", [SceneSpec("2021_08_16_22_26_54", [full, short])])
    adapter = _adapter()
    ref = adapter.scenes(tmp_path, {"split": "train"})[0]
    assert ref.samples == (69, 71, 73)
    scene = adapter.load(tmp_path, ref)
    assert scene.pose_times("641") == (3_450_000, 3_550_000, 3_650_000)
    assert scene.pose_times("650") == (3_450_000,)


def test_split_param_is_required_and_checked(tmp_path: Path) -> None:
    write_release(tmp_path, "train", [two_cav_scene()])
    with pytest.raises(Exception, match="split"):
        _adapter().scenes(tmp_path, {})
    with pytest.raises(Exception, match="train, validate, test"):
        _adapter().scenes(tmp_path, {"split": "nope"})
    assert resolve_adapter("v2xset").name == "v2xset"


def test_fixture_boxes_are_written_as_a_mapping_keyed_by_actor_id(tmp_path: Path) -> None:
    write_release(tmp_path, "train", [two_cav_scene()])
    import yaml

    record = yaml.safe_load(
        (tmp_path / "train/2021_08_16_22_26_54/641/000069.yaml").read_text(encoding="utf-8")
    )
    assert set(record["vehicles"]) == {650, 677}
    assert record["vehicles"][677]["center"] == [1.0, 0.0, 0.75]
    assert record["lidar_pose"] == [10.0, 20.0, 1.9, 0.0, 90.0, 0.0]
    assert Box(1, (0.0, 0.0, 0.0)).extent == (2.4, 1.0, 0.75)
