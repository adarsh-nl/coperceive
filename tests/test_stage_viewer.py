# SPDX-License-Identifier: Apache-2.0
"""``scripts/griffin/stage_viewer.py`` on a synthetic release: val frames only are
copied, cameras are linked, maps are built, nothing lands under ``/home``."""

from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path

import pyarrow.parquet as pq
import pytest
from scripts.griffin import stage_viewer
from scripts.griffin.stage_viewer import StageOptions, StagingError, stage

from tests.griffin_release import write_release

# Dataset order. Blocks are contiguous and separated, as scene_infos_val.py requires.
SCENES: dict[str, list[int]] = {
    "Town03-000": [11185, 11186, 11187],
    "Town03-001": [11190, 11191],
    "Town03-002": [11195, 11196, 11197, 11198],
}
VAL = ("Town03-000", "Town03-002")
VAL_FRAMES = [frame for name in VAL for frame in SCENES[name]]
CAMERAS = (("vehicle-side", "front"), ("drone-side", "front"))

# The same straight two-lane road as tests/test_hdmap.py::_straight.
_XODR = """<?xml version="1.0" encoding="UTF-8"?>
<OpenDRIVE>
  <header revMajor="1" revMinor="4" name="fixture" north="10" south="-10" east="20" west="-20">
    <geoReference><![CDATA[+proj=tmerc +units=m +no_defs]]></geoReference>
  </header>
  <road name="main" length="100" id="1" junction="-1">
    <planView>
      <geometry s="0" x="0" y="0" hdg="0" length="100"><line/></geometry>
    </planView>
    <lanes>
      <laneSection s="0">
        <left>
          <lane id="1" type="driving" level="false">
            <width sOffset="0" a="3.5" b="0" c="0" d="0"/>
          </lane>
        </left>
        <center><lane id="0" type="none" level="false"/></center>
        <right>
          <lane id="-1" type="driving" level="false">
            <width sOffset="0" a="3.5" b="0" c="0" d="0"/>
          </lane>
        </right>
      </laneSection>
    </lanes>
  </road>
</OpenDRIVE>
"""


def _release(root: Path) -> Path:
    release = root / "griffin-release"
    write_release(release, SCENES)
    for side, camera in CAMERAS:
        camera_dir = release / side / "camera" / camera
        camera_dir.mkdir(parents=True)
        for frames in SCENES.values():
            for frame in frames:
                (camera_dir / f"{frame:06d}.png").write_bytes(b"\x89PNG stub")
    return release


def _split(root: Path) -> Path:
    path = root / "griffin_50scenes_25m.json"
    split = {
        "batch_split": {
            "train": ["scene-0001-Town03-001"],
            "val": ["scene-0002-Town03-002", "scene-0000-Town03-000"],
        }
    }
    path.write_text(json.dumps(split), encoding="utf-8")
    return path


def _xodr_dir(root: Path) -> Path:
    directory = root / "xodr"
    directory.mkdir()
    (directory / "Town03.xodr").write_text(_XODR, encoding="utf-8")
    return directory


def _options(root: Path, **overrides: object) -> StageOptions:
    base = StageOptions(
        raw=_release(root),
        split=_split(root),
        scratch=root / "scratch",
        xodr_dir=_xodr_dir(root),
        workers=1,
        allow_unpinned_xodr=True,
    )
    return replace(base, **overrides)


def _stems(directory: Path) -> list[int]:
    return sorted(int(path.stem) for path in directory.iterdir())


def test_stage_copies_val_metadata_links_cameras_and_builds_maps(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    options = _options(tmp_path)
    report = stage(options)
    scratch = options.scratch
    release = options.raw

    # 1. raw tree: val frames only, both sides, lidar for the val frames, no cameras.
    for side in ("vehicle-side", "drone-side"):
        assert _stems(scratch / "raw" / side / "pose") == VAL_FRAMES
        assert _stems(scratch / "raw" / side / "label") == VAL_FRAMES
        infos = json.loads((scratch / "raw" / side / "scene_infos.json").read_text("utf-8"))
        assert [entry["name"] for entry in infos] == list(VAL)
    assert _stems(scratch / "raw" / "vehicle-side" / "lidar" / "lidar_top") == VAL_FRAMES
    assert list(scratch.rglob("*.png")) == []

    # 2. scenes: exactly the val scenes, with points.
    assert report.scenes == VAL
    assert sorted(
        p.name for p in (scratch / "scenes").iterdir() if not p.name.startswith(".")
    ) == list(VAL)
    assert (scratch / "scenes" / "Town03-000" / "manifest.json").is_file()
    assert (scratch / "scenes" / "Town03-002" / "manifest.json").is_file()
    assert not (scratch / "scenes" / "Town03-001").exists()
    assert dict(report.clouds_per_scene) == {"Town03-000": 3, "Town03-002": 4}
    assert dict(report.points_per_scene) == {"Town03-000": 3, "Town03-002": 4}

    # 3. cameras: one symlink per side into the release; recorded paths resolve through it.
    scene_dir = scratch / "scenes" / "Town03-000"
    for side in ("vehicle-side", "drone-side"):
        link = scene_dir / side / "camera"
        assert link.is_symlink()
        assert Path(os.readlink(link)) == (release / side / "camera").resolve()
    rows = pq.read_table(scene_dir / "images.parquet", columns=["agent_id", "path"]).to_pylist()
    paths = {str(row["agent_id"]): str(row["path"]) for row in rows}
    assert paths["vehicle"].startswith("vehicle-side/camera/front/")
    resolved = (scene_dir / paths["vehicle"]).resolve()
    assert resolved.is_file()
    assert resolved.is_relative_to(release.resolve())
    assert report.camera_links == 4
    assert report.images_recorded == 7 * 2
    assert report.images_resolved == report.images_recorded

    # 4. maps: Town03 ingested and aligned against the staged scenes.
    manifest = json.loads((scratch / "maps" / "Town03" / "manifest.json").read_text("utf-8"))
    assert manifest["map_name"] == "Carla/Maps/Town03"
    assert [item.town for item in report.maps] == ["Town03"]
    assert report.maps[0].measured is True
    assert manifest["alignment"]["measured"] is True

    # 5. the serve line, printed and returned.
    out = capsys.readouterr().out
    assert "coperceive serve" in report.serve_command
    assert f"--media-root {release}" in report.serve_command
    assert f"--root {scratch / 'scenes'}" in report.serve_command
    assert f"--maps {scratch / 'maps'}" in report.serve_command
    assert "--predictions" not in report.serve_command
    assert report.serve_command in out
    assert "UNPINNED" in out


def test_no_lidar_leaves_empty_point_tables(tmp_path: Path) -> None:
    options = _options(tmp_path, lidar=False, predictions=tmp_path / "preds", port=9000)
    report = stage(options)
    scratch = options.scratch
    assert not (scratch / "raw" / "vehicle-side" / "lidar").exists()
    for name in VAL:
        table = pq.read_table(scratch / "scenes" / name / "point_index.parquet")
        assert table.num_rows == 0
    assert dict(report.points_per_scene) == {"Town03-000": 0, "Town03-002": 0}
    assert f"--predictions {tmp_path / 'preds'}" in report.serve_command
    assert "--port 9000" in report.serve_command


def test_unpinned_xodr_is_refused_before_anything_is_staged(tmp_path: Path) -> None:
    options = _options(tmp_path, allow_unpinned_xodr=False)
    with pytest.raises(StagingError, match="SHA-256"):
        stage(options)
    assert not options.scratch.exists()
    missing = _options(tmp_path / "other", allow_unpinned_xodr=True)
    (missing.xodr_dir / "Town03.xodr").unlink()
    with pytest.raises(StagingError, match="no OpenDRIVE file"):
        stage(missing)


def test_scratch_under_home_is_refused(tmp_path: Path) -> None:
    options = _options(tmp_path, scratch=Path("/home/someone/viewer"))
    with pytest.raises(StagingError, match="/home"):
        stage(options)


def test_main_needs_a_scratch_outside_slurm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SLURM_JOB_ID", raising=False)
    assert stage_viewer.default_scratch() is None
    monkeypatch.setenv("SLURM_JOB_ID", "123456")
    assert stage_viewer.default_scratch() == Path("/local/123456/coperceive-viewer")
    monkeypatch.delenv("SLURM_JOB_ID")
    with pytest.raises(SystemExit) as excinfo:
        stage_viewer.main(
            ["--raw", str(tmp_path), "--split", str(tmp_path), "--xodr-dir", str(tmp_path)]
        )
    assert excinfo.value.code == 2


def test_rerun_with_lidar_reconverts_scenes_staged_without_it(tmp_path: Path) -> None:
    """``--no-lidar`` then lidar in the same scratch: the empty scenes are converted again
    instead of being skipped on their COMPLETE.json."""
    first = _options(tmp_path, lidar=False)
    assert dict(stage(first).clouds_per_scene) == {"Town03-000": 0, "Town03-002": 0}
    second = replace(first, lidar=True)
    assert dict(stage(second).clouds_per_scene) == {"Town03-000": 3, "Town03-002": 4}


def test_scenes_from_an_earlier_split_are_removed(tmp_path: Path) -> None:
    options = _options(tmp_path)
    stale = options.scratch / "scenes" / "Town03-001"
    stale.mkdir(parents=True)
    (stale / "manifest.json").write_text("{}", encoding="utf-8")
    report = stage(options)
    assert not stale.exists()
    kept = sorted(p.name for p in (options.scratch / "scenes").iterdir() if p.name[0] != ".")
    assert kept == list(VAL)  # the ingest's .partial/ aside
    assert report.scenes == VAL


def test_serve_line_names_the_resolved_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A relative --raw is resolved: the links point at the resolved release and the
    printed --media-root has to be the same path from any working directory."""
    options = _options(tmp_path)
    monkeypatch.chdir(options.raw.parent)
    report = stage(replace(options, raw=Path(options.raw.name)))
    assert f"--media-root {options.raw.resolve()}" in report.serve_command
