# SPDX-License-Identifier: Apache-2.0
"""The release workflow, the bundle version and the updater endpoint agree.

docs/release/v0.1.0-checklist.md, check R2. A release tag (``v0.1.1``) must
produce a bundle whose version is that tag, a ``latest.json`` that names it, at
the URL the app polls. Nothing here builds, signs or tags.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RELEASE_YML = ROOT / ".github" / "workflows" / "release.yml"
TAURI_CONF = ROOT / "src-tauri" / "tauri.conf.json"
SYNC_VERSION = ROOT / "scripts" / "desktop" / "sync_version.sh"
REPO = "adarsh-nl/coperceive"

_spec = importlib.util.spec_from_file_location(
    "write_latest_json", ROOT / "scripts" / "desktop" / "write_latest_json.py"
)
assert _spec is not None and _spec.loader is not None
wlj = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wlj)


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


def _checkout(tmp_path: Path, *tags: str) -> Path:
    """A tiny checkout shaped like the repo, optionally at a tag."""
    work = tmp_path / "checkout"
    (work / "src-tauri").mkdir(parents=True)
    (work / "scripts" / "desktop").mkdir(parents=True)
    shutil.copy(SYNC_VERSION, work / "scripts" / "desktop" / "sync_version.sh")
    shutil.copy(ROOT / "pyproject.toml", work / "pyproject.toml")
    (work / "src-tauri" / "tauri.conf.json").write_text(json.dumps({"version": "0.0.0"}))
    _git(work, "init", "-q")
    _git(work, "add", ".")
    _git(work, "commit", "-q", "-m", "fixture")
    for tag in tags:
        _git(work, "tag", tag)
    return work


def _sync(work: Path, **github: str) -> subprocess.CompletedProcess[str]:
    # Only the GitHub variables given here: these tests also run inside the
    # tag-triggered release workflow, whose own GITHUB_REF_* must not leak in.
    env = {k: v for k, v in os.environ.items() if not k.startswith("GITHUB_REF")}
    return subprocess.run(
        ["bash", "scripts/desktop/sync_version.sh"],
        cwd=work,
        env={**env, **github},
        capture_output=True,
        text=True,
    )


def _synced_version(work: Path, **github: str) -> str:
    result = _sync(work, **github)
    assert result.returncode == 0, result.stderr
    return str(json.loads((work / "src-tauri" / "tauri.conf.json").read_text())["version"])


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_a_release_tag_becomes_the_bundle_version_and_names_latest_json(tmp_path: Path) -> None:
    work = _checkout(tmp_path, "v0.1.1")
    assert _synced_version(work, GITHUB_REF_TYPE="tag", GITHUB_REF_NAME="v0.1.1") == "0.1.1"
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "Coperceive.app.tar.gz").write_bytes(b"archive")
    (assets / "Coperceive.app.tar.gz.sig").write_text("c2ln\n")
    out = tmp_path / "latest.json"
    argv = ["--conf", str(work / "src-tauri" / "tauri.conf.json"), "--tag", "v0.1.1",
            "--assets", str(assets), "--repo", REPO, "--out", str(out)]  # fmt: skip
    assert wlj.main(argv) == 0
    manifest = json.loads(out.read_text())
    assert manifest["version"] == "0.1.1"
    assert manifest["platforms"]["darwin-aarch64"]["url"] == (
        f"https://github.com/{REPO}/releases/download/v0.1.1/Coperceive.app.tar.gz"
    )


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_two_tags_on_one_commit_use_the_tag_that_triggered_the_build(tmp_path: Path) -> None:
    """Gap G2: never guess between tags on the same commit."""
    work = _checkout(tmp_path, "v0.1.1", "v0.1.2")
    for tag in ("v0.1.1", "v0.1.2"):
        assert _synced_version(work, GITHUB_REF_TYPE="tag", GITHUB_REF_NAME=tag) == tag[1:]
    refused = _sync(work)
    assert refused.returncode != 0
    assert "several release tags on HEAD" in refused.stderr, refused.stderr
    # A branch build ignores its branch name and does not guess either.
    assert _sync(work, GITHUB_REF_TYPE="branch", GITHUB_REF_NAME="main").returncode != 0


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_a_tag_that_is_not_a_version_is_refused(tmp_path: Path) -> None:
    work = _checkout(tmp_path)
    for tag in ("v0.1", "vnext", "v1.2.3'; import os; '"):
        result = _sync(work, GITHUB_REF_TYPE="tag", GITHUB_REF_NAME=tag)
        assert result.returncode != 0 and "not a release version" in result.stderr, tag
    assert json.loads((work / "src-tauri" / "tauri.conf.json").read_text())["version"] == "0.0.0"


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_an_untagged_build_reports_the_pyproject_version(tmp_path: Path) -> None:
    """A build that is not at a tag says pyproject's version; one tag on HEAD is that tag."""
    work = _checkout(tmp_path)
    assert _synced_version(work) == "0.1.0"
    one_tag = _checkout(tmp_path / "tagged", "v0.1.1")
    assert _synced_version(one_tag) == "0.1.1"


def test_the_app_polls_the_file_the_release_uploads() -> None:
    conf = json.loads(TAURI_CONF.read_text())
    updater = conf["plugins"]["updater"]
    assert updater["endpoints"] == [
        f"https://github.com/{REPO}/releases/latest/download/latest.json"
    ]
    assert updater["pubkey"].strip()
    # Updater artifacts are switched on only in the release workflow (it has the key).
    assert not conf["bundle"].get("createUpdaterArtifacts")
    workflow = RELEASE_YML.read_text()
    assert 'tags:\n      - "v*"' in workflow
    assert "scripts/desktop/sync_version.sh" in workflow
    assert "--out release-assets/latest.json" in workflow
    assert "files: release-assets/*" in workflow
    # /releases/latest/ skips prereleases: a release the app should see must not be one.
    assert "prerelease: false" in workflow
    assert workflow.index("sync_version.sh") < workflow.index("build_dmg.sh")


def test_the_release_runs_the_tests_before_it_builds() -> None:
    """Gap G4: a tag on a commit whose tests fail must not become a release."""
    workflow = RELEASE_YML.read_text()
    checks = workflow[workflow.index("  checks:") : workflow.index("  build-release:")]
    steps = ("ruff check", "ruff format --check", "pyright", "pytest", "npx tsc -b", "npm test")
    for step in steps:
        assert step in checks, step
    build = workflow[workflow.index("  build-release:") :]
    assert "needs: checks" in build.split("steps:", 1)[0]
