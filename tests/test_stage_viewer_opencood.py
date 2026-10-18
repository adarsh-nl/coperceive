# SPDX-License-Identifier: Apache-2.0
"""``scripts/opencood/stage_viewer.py`` end to end on the synthetic release (no dataset bytes)."""

from __future__ import annotations

from pathlib import Path

import pytest
from coperceive.adapters.spec import builtin_manifest_path, load_manifest
from coperceive.store import read_scene
from scripts.griffin.stage_viewer import StagingError
from scripts.opencood import stage_viewer as stage

from tests.opencood_release import AgentTrack, Box, SceneSpec, two_cav_scene, write_release
from tests.test_hdmap import straight_road


def _on_road_scene(name: str = "2021_08_20_21_10_24") -> SceneSpec:
    """A CAV driving along the synthetic Town03 road (x axis, right lane at source y = +1.75)."""
    # Source y = +1.75 reflects to schema y = -1.75: the fixture road's right-hand lane.
    cav = AgentTrack(
        "1996",
        poses=[((20.0 + 10.0 * i, 1.75, 1.9), (0.0, 0.0, 0.0)) for i in range(3)],
        boxes=[Box(2005, location=(40.0, 1.75, 0.0))],
    )
    return SceneSpec(name, [cav])


def test_poses_only_adapter_drops_lidar_and_images() -> None:
    adapter = stage.adapter_for("v2xset")
    light = stage.poses_only(adapter)
    assert light.spec.streams.lidar is None and light.spec.streams.images is None
    assert adapter.spec.streams.lidar is not None
    assert light.spec.agents.discover is not None
    assert light.spec.agents.discover.types


def test_scratch_under_home_is_refused(tmp_path: Path) -> None:
    options = stage.StageOptions(
        "opv2v", tmp_path, "train", (), Path("/home/someone/x"), tmp_path, allow_unpinned_xodr=True
    )
    with pytest.raises(StagingError, match="/home"):
        stage.stage(options)


def test_unknown_dataset_and_unpinned_xodr_are_refused(tmp_path: Path) -> None:
    with pytest.raises(StagingError, match="opv2v, v2xset"):
        stage.adapter_for("nuscenes")
    xodr = tmp_path / "xodr"
    xodr.mkdir()
    straight_road(xodr / "Town03.xodr")
    with pytest.raises(StagingError, match="not the ADR 0008 pin"):
        stage.town_files(xodr, allow_unpinned=False)
    files = stage.town_files(xodr, allow_unpinned=True)
    assert [(f.town, f.pinned) for f in files] == [("Town03", False)]


def test_stage_identifies_the_town_links_media_and_counts_points(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    write_release(raw, "test", [_on_road_scene(), two_cav_scene("2021_08_18_19_48_05")])
    xodr = tmp_path / "xodr"
    xodr.mkdir()
    straight_road(xodr / "Town03.xodr")
    shifted = xodr / "Town05.xodr"
    shifted.write_text(
        (xodr / "Town03.xodr").read_text(encoding="utf-8").replace('x="0" y="0"', 'x="0" y="500"'),
        encoding="utf-8",
    )
    scratch = tmp_path / "scratch"
    options = stage.StageOptions(
        dataset="opv2v",
        raw=raw,
        split="test",
        scenes=("2021_08_20_21_10_24", "2021_08_18_19_48_05"),
        scratch=scratch,
        xodr_dir=xodr,
        static=None,
        workers=1,
        allow_unpinned_xodr=True,
    )
    report = stage.stage(options)
    towns = {g.scene_id: (g.status, g.map_name) for g in report.towns}
    assert towns["2021_08_20_21_10_24"] == ("identified", "Carla/Maps/Town03")
    assert towns["2021_08_18_19_48_05"][0] == "unidentified"  # far from both synthetic roads
    on_road = read_scene(scratch / "scenes" / "2021_08_20_21_10_24")
    assert dict(on_road.agents[0].platform) == {"map_name": "Carla/Maps/Town03", "split": "test"}
    off_road = read_scene(scratch / "scenes" / "2021_08_18_19_48_05")
    assert dict(off_road.agents[0].platform) == {"split": "test"}
    assert dict(report.points_per_scene)["2021_08_20_21_10_24"] == 3 * 10  # 9 box points + ground
    assert (scratch / "maps" / "Town03" / "manifest.json").is_file()
    assert [m for m, _, _ in report.maps_aligned] == ["Carla/Maps/Town03"]
    link = scratch / "scenes" / "2021_08_20_21_10_24" / "test"
    assert link.is_symlink() and link.resolve() == (raw / "test").resolve()
    assert (link / "2021_08_20_21_10_24" / "1996" / "000069_camera0.png").is_file()
    assert report.serve_command.startswith("coperceive serve --host 127.0.0.1")
    assert f"--media-root {raw.resolve()}" in report.serve_command
    assert (scratch / "scene_towns-opv2v.yaml").is_file()
    # A second run (the other dataset into the same scratch) keeps the ingested maps.
    assert stage.ingest_maps(stage.town_files(xodr, allow_unpinned=True), scratch / "maps") == (
        "Carla/Maps/Town03",
        "Carla/Maps/Town05",
    )
    assert load_manifest(builtin_manifest_path("opv2v")).adapter == "opv2v"
