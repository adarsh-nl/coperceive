# SPDX-License-Identifier: Apache-2.0
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
"""FOLD-03: a bundle export never follows a link or reads a non-regular file.

The export walks every scene and run folder it reads with
``os.walk(followlinks=False)`` and ``lstat``s each entry before anything is read.
A symbolic link (to a file, a directory, another link, or a file inside the same
scene), a FIFO or any other non-regular file refuses the whole export with an
error that names the entry; nothing is skipped silently and no ``.cpbundle`` is
written. Files staged for the archive pass the same check before they are
scrubbed, hashed and zipped. Synthetic demo data only.
"""

from __future__ import annotations

import os
import shutil
import zipfile
from pathlib import Path

import pytest
from coperceive.bundle import BundleExportError, BundleExportRequest, export_bundle, preview_bundle
from coperceive.demo import create

SCENE = "demo-junction"
SECRET = "NOT-A-REAL-KEY-0000"


@pytest.fixture(scope="module")
def pristine(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return create(tmp_path_factory.mktemp("links-demo"), seed=0).root


@pytest.fixture
def demo(pristine: Path, tmp_path: Path) -> Path:
    root = tmp_path / "demo"
    shutil.copytree(pristine, root, symlinks=True)
    return root


def _outside(tmp_path: Path) -> Path:
    folder = tmp_path / "outside"
    folder.mkdir(exist_ok=True)
    return folder


def _request(demo: Path, out: Path, *, sensor: bool = False) -> BundleExportRequest:
    return BundleExportRequest(
        run_id="demo",
        scenes_root=demo / "scenes",
        predictions_root=demo / "predictions",
        out=out,
        dataset_id="demo",
        include_sensor_data=sensor,
        created_utc="2026-01-01T00:00:00Z",
    )


def _refused(demo: Path, tmp_path: Path, match: str, *, sensor: bool = False) -> None:
    out = tmp_path / "out" / "shared.cpbundle"
    with pytest.raises(BundleExportError, match=match):
        export_bundle(_request(demo, out, sensor=sensor))
    assert not out.exists()
    assert not (out.parent.exists() and any(out.parent.iterdir()))


def _camera_frames(demo: Path) -> list[Path]:
    frames = sorted((demo / "scenes" / SCENE / "agents" / "cav-a" / "camera").iterdir())
    assert len(frames) >= 2
    return frames


def test_baseline_export_of_the_copied_demo_succeeds(demo: Path, tmp_path: Path) -> None:
    """The fixture itself is exportable, so each refusal below is the planted entry."""
    out = export_bundle(_request(demo, tmp_path / "plain.cpbundle", sensor=True))
    with zipfile.ZipFile(out) as archive:
        assert "bundle.json" in archive.namelist()


def test_symlinked_file_is_refused(demo: Path, tmp_path: Path) -> None:
    complete = demo / "scenes" / SCENE / "COMPLETE.json"
    target = _outside(tmp_path) / "credentials.json"
    target.write_text(
        complete.read_text(encoding="utf-8").replace("{", f'{{"aws_secret": "{SECRET}",', 1),
        encoding="utf-8",
    )
    complete.unlink()
    complete.symlink_to(target)
    _refused(demo, tmp_path, r"COMPLETE\.json is a symbolic link")


def test_symlinked_directory_is_refused(demo: Path, tmp_path: Path) -> None:
    camera = demo / "scenes" / SCENE / "agents" / "cav-a" / "camera"
    moved = _outside(tmp_path) / "camera"
    shutil.move(str(camera), moved)
    camera.symlink_to(moved, target_is_directory=True)
    _refused(demo, tmp_path, r"agents/cav-a/camera is a symbolic link", sensor=True)


def test_symlink_chain_is_refused(demo: Path, tmp_path: Path) -> None:
    complete = demo / "scenes" / SCENE / "COMPLETE.json"
    real = _outside(tmp_path) / "id_ed25519"
    real.write_text(complete.read_text(encoding="utf-8"), encoding="utf-8")
    second = _outside(tmp_path) / "hop-2"
    second.symlink_to(real)
    first = demo / "scenes" / "hop-1"
    first.symlink_to(second)
    complete.unlink()
    complete.symlink_to(Path("..") / "hop-1")
    _refused(demo, tmp_path, r"COMPLETE\.json is a symbolic link")


def test_fifo_is_refused_not_skipped(demo: Path, tmp_path: Path) -> None:
    frame = _camera_frames(demo)[0]
    frame.unlink()
    os.mkfifo(frame)
    _refused(demo, tmp_path, rf"{frame.name} is not a regular file", sensor=True)


def test_symlink_to_a_file_in_the_same_scene_is_refused(demo: Path, tmp_path: Path) -> None:
    """Refused, not copied: one rule for every link, and the manifest stays 1:1 with disk."""
    first, second = _camera_frames(demo)[:2]
    second.unlink()
    second.symlink_to(first.name)
    _refused(demo, tmp_path, rf"{second.name} is a symbolic link", sensor=True)


def test_symlinked_run_file_is_refused(demo: Path, tmp_path: Path) -> None:
    metrics = demo / "predictions" / "demo" / SCENE / "metrics.json"
    target = _outside(tmp_path) / "metrics.json"
    shutil.copy2(metrics, target)
    metrics.unlink()
    metrics.symlink_to(target)
    _refused(demo, tmp_path, r"metrics\.json is a symbolic link")


def test_scene_folder_that_is_a_link_inside_the_root_is_refused(demo: Path, tmp_path: Path) -> None:
    scenes = demo / "scenes"
    (scenes / SCENE).rename(scenes / "real-junction")
    (scenes / SCENE).symlink_to("real-junction", target_is_directory=True)
    _refused(demo, tmp_path, rf"{SCENE} is a symbolic link")


def test_preview_refuses_a_link_too(demo: Path, tmp_path: Path) -> None:
    complete = demo / "scenes" / SCENE / "COMPLETE.json"
    target = _outside(tmp_path) / "COMPLETE.json"
    shutil.copy2(complete, target)
    complete.unlink()
    complete.symlink_to(target)
    with pytest.raises(BundleExportError, match="symbolic link"):
        preview_bundle(_request(demo, tmp_path / "review" / "shared.cpbundle"))


def test_a_staged_file_that_is_a_link_is_refused(
    demo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every manifest-listed file is checked too, not only the source folders."""
    import coperceive.bundle.export as export_module

    secret = _outside(tmp_path) / "planted.png"
    secret.write_text(SECRET, encoding="utf-8")

    def linked_preview(_scene: object, path: Path, **_kwargs: object) -> None:
        path.symlink_to(secret)

    monkeypatch.setattr(export_module, "render_bev", linked_preview)
    _refused(demo, tmp_path, r"frames/frame-01\.png is a symbolic link")
    assert secret.read_text(encoding="utf-8") == SECRET
