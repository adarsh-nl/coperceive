# SPDX-License-Identifier: Apache-2.0
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportPrivateUsage=false
"""FOLD-03: a bundle export never follows a link or reads a non-regular file.

The export walks every scene and run folder it reads with
``os.walk(followlinks=False)`` and ``lstat``s each entry before anything is read.
A symbolic link (to a file, a directory, another link, or a file inside the same
scene), a hard link, a FIFO or any other non-regular file refuses the whole
export with an error that names the entry; nothing is skipped silently and no
``.cpbundle`` is written. A file or folder swapped after the walk is refused when
it is read. Files staged for the archive pass the same check before they are
scrubbed, hashed and zipped. Synthetic demo data only.

Every export here runs on a thread joined with a deadline, so a regression that
blocks on a FIFO fails the test instead of hanging the run.
"""

from __future__ import annotations

import os
import shutil
import stat
import threading
import zipfile
from collections.abc import Callable
from pathlib import Path

import pytest
from coperceive.bundle import BundleExportError, BundleExportRequest, export_bundle, preview_bundle
from coperceive.demo import create

SCENE = "demo-junction"
SECRET = "NOT-A-REAL-KEY-0000"
#: A synthetic export takes seconds; this only turns a hang into a failure.
DEADLINE_S = 120.0


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


def _release_fifos(root: Path) -> None:
    """Open each FIFO for writing so a reader blocked on it returns."""
    for path in root.rglob("*"):
        try:
            if stat.S_ISFIFO(os.lstat(path).st_mode):
                os.close(os.open(path, os.O_WRONLY | os.O_NONBLOCK))
        except OSError:
            continue


def _bounded(run: Callable[[], object], demo: Path) -> None:
    """Run ``run`` on a thread; a call still blocked after the deadline fails the test."""
    raised: list[BaseException] = []

    def target() -> None:
        try:
            run()
        except BaseException as exc:  # handed back to the test thread
            raised.append(exc)

    worker = threading.Thread(target=target, daemon=True)
    worker.start()
    worker.join(DEADLINE_S)
    if worker.is_alive():
        _release_fifos(demo)
        pytest.fail(f"the export was still blocked after {DEADLINE_S:.0f} s")
    if raised:
        raise raised[0]


def _refused(demo: Path, tmp_path: Path, match: str, *, sensor: bool = False) -> None:
    out = tmp_path / "out" / "shared.cpbundle"
    with pytest.raises(BundleExportError, match=match):
        _bounded(lambda: export_bundle(_request(demo, out, sensor=sensor)), demo)
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
        _bounded(lambda: preview_bundle(_request(demo, tmp_path / "review" / "x.cpbundle")), demo)


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


def test_hard_link_to_an_outside_file_is_refused(demo: Path, tmp_path: Path) -> None:
    complete = demo / "scenes" / SCENE / "COMPLETE.json"
    target = _outside(tmp_path) / "credentials.json"
    target.write_text(
        complete.read_text(encoding="utf-8").replace("{", f'{{"aws_secret": "{SECRET}",', 1),
        encoding="utf-8",
    )
    complete.unlink()
    os.link(target, complete)
    _refused(demo, tmp_path, r"COMPLETE\.json is a hard link")


def _swap_after_walk(monkeypatch: pytest.MonkeyPatch, folder: Path, replacement: Path) -> list[str]:
    """Once every folder is walked, put a link to ``replacement`` where ``folder`` was.

    The export walks all the folders it reads before it plans the sensor data,
    and reads scene files from then on; wrapping ``_plan_sensor`` makes the race
    deterministic: the swap lands after every check and before any scene read.
    """
    import coperceive.bundle.export as export_module

    real = export_module._plan_sensor
    swapped: list[str] = []

    def swap_then_plan(*args: object, **kwargs: object) -> object:
        folder.rename(folder.with_name(folder.name + "-walked"))
        folder.symlink_to(replacement, target_is_directory=True)
        swapped.append(str(folder))
        return real(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(export_module, "_plan_sensor", swap_then_plan)
    return swapped


def _no_secret_written(tmp_path: Path) -> None:
    out = tmp_path / "out"
    for path in out.rglob("*") if out.exists() else ():
        assert SECRET.encode() not in path.read_bytes()


def test_scene_folder_swapped_for_a_link_after_the_walk_is_refused(
    demo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scene = demo / "scenes" / SCENE
    # Inside the scenes root, so a resolved-path containment check alone passes.
    planted = demo / "scenes" / ".planted-scene"
    shutil.copytree(scene, planted)
    complete = planted / "COMPLETE.json"
    complete.write_text(
        complete.read_text(encoding="utf-8").replace("{", f'{{"aws_secret": "{SECRET}",', 1),
        encoding="utf-8",
    )
    swapped = _swap_after_walk(monkeypatch, scene, planted)
    _refused(demo, tmp_path, "changed after it was checked")
    assert swapped
    _no_secret_written(tmp_path)


def test_camera_folder_swapped_for_a_link_after_the_walk_is_refused(
    demo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    camera = demo / "scenes" / SCENE / "agents" / "cav-a" / "camera"
    planted = _outside(tmp_path) / "planted-camera"
    planted.mkdir()
    for frame in camera.iterdir():
        (planted / frame.name).write_text(SECRET, encoding="utf-8")
    swapped = _swap_after_walk(monkeypatch, camera, planted)
    _refused(demo, tmp_path, "changed after it was checked", sensor=True)
    assert swapped
    _no_secret_written(tmp_path)
