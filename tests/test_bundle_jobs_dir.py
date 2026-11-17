# SPDX-License-Identifier: Apache-2.0
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportPrivateUsage=false
"""A bundle's private jobs-database folder is removed at shutdown, and stale ones later.

The folder is ``<tmp>/coperceive-bundle-jobs-<random>``, holding ``owner.lock``
that the serving process keeps locked. Shutdown removes it. The next bundle
open or serve removes a folder only when it can prove it is ours and stale:
the exact prefix in the temp directory, a real folder (never a link) owned by
this user, older than this process, with an ``owner.lock`` that is a regular
file nobody holds. Anything else is kept.
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

import pytest
from coperceive.app import bundle_jobs
from coperceive.app.server import create_app
from fastapi.testclient import TestClient

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="the stale sweep needs flock")

CSRF = {"X-Coperceive-Request": "1"}


@pytest.fixture
def temp_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    folder = tmp_path / "tmp"
    folder.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(folder))
    return folder


def _bundle_app(tmp_path: Path) -> object:
    return create_app(root=tmp_path / "scenes", token=None, bundle_info=tmp_path / "open.json")


def test_shutdown_removes_the_jobs_folder(temp_dir: Path, tmp_path: Path) -> None:
    app = create_app(root=tmp_path / "scenes", token=None, bundle_info=tmp_path / "open.json")
    database = Path(app.state.db_path)
    folder = database.parent
    assert folder.parent == temp_dir and folder.name.startswith(bundle_jobs.PREFIX)
    with TestClient(app) as client:
        made = client.post("/api/jobs", headers=CSRF, json={"name": "echo", "args": ["x"]})
        assert made.status_code == 200, made.text
        assert database.is_file()
    assert not folder.exists()


def _old(path: Path) -> Path:
    past = time.time() - 3600
    os.utime(path, (past, past), follow_symlinks=False)
    return path


def _stale(temp_dir: Path, name: str) -> Path:
    folder = temp_dir / f"{bundle_jobs.PREFIX}{name}"
    folder.mkdir()
    (folder / "jobs.sqlite").write_bytes(b"")
    (folder / bundle_jobs.LOCK).write_text("999999\n", encoding="utf-8")
    return _old(folder)


def test_stale_folders_are_removed_and_anything_unproven_is_kept(
    temp_dir: Path, tmp_path: Path
) -> None:
    import fcntl

    stale = _stale(temp_dir, "stale001")
    held = _stale(temp_dir, "held0001")
    holder = os.open(held / bundle_jobs.LOCK, os.O_RDWR)
    fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
    recent = temp_dir / f"{bundle_jobs.PREFIX}recent01"
    recent.mkdir()
    (recent / bundle_jobs.LOCK).write_text("1\n", encoding="utf-8")
    unproven = temp_dir / f"{bundle_jobs.PREFIX}nolock01"
    unproven.mkdir()
    _old(unproven)
    linked_lock = _stale(temp_dir, "lnklock1")
    (linked_lock / bundle_jobs.LOCK).unlink()
    (linked_lock / bundle_jobs.LOCK).symlink_to(tmp_path / "elsewhere.lock")
    _old(linked_lock)
    victim = tmp_path / "victim"
    victim.mkdir()
    (victim / bundle_jobs.LOCK).write_text("1\n", encoding="utf-8")
    (victim / "keep.txt").write_text("keep\n", encoding="utf-8")
    _old(victim)
    link = temp_dir / f"{bundle_jobs.PREFIX}symlink1"
    link.symlink_to(victim, target_is_directory=True)
    _old(link)
    plain_file = temp_dir / f"{bundle_jobs.PREFIX}file0001"
    plain_file.write_text("x", encoding="utf-8")
    _old(plain_file)
    other_prefix = temp_dir / "coperceive-bundle-jobsX-old"
    other_prefix.mkdir()
    (other_prefix / bundle_jobs.LOCK).write_text("1\n", encoding="utf-8")
    _old(other_prefix)
    try:
        app = _bundle_app(tmp_path)  # serving a bundle sweeps first
    finally:
        fcntl.flock(holder, fcntl.LOCK_UN)
        os.close(holder)
    mine = Path(app.state.db_path).parent  # type: ignore[attr-defined]
    assert not stale.exists()
    for kept in (held, recent, unproven, linked_lock, link, plain_file, other_prefix, mine):
        assert os.path.lexists(kept), kept
    assert (victim / "keep.txt").read_text(encoding="utf-8") == "keep\n"
    assert (tmp_path / "elsewhere.lock").exists() is False


def test_opening_a_bundle_sweeps_stale_folders(temp_dir: Path, tmp_path: Path) -> None:
    from coperceive.bundle import BundleError, open_bundle

    stale = _stale(temp_dir, "stale002")
    with pytest.raises(BundleError):
        open_bundle(tmp_path / "missing.cpbundle", data_root=tmp_path / "data")
    assert not stale.exists()
