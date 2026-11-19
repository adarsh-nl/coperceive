# SPDX-License-Identifier: Apache-2.0
"""Which files the pre-push hook checks (scripts/hooks/pre-push).

The hook runs the slow checks (tsc, cargo check) only for the areas a push
changes. These tests build a small repository with an integration branch and
a feature branch, and ask the hook (COPERCEIVE_PREPUSH_LIST_FILES=1) which
files it would check, without running any checker.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[1] / "scripts" / "hooks" / "pre-push"
ZERO = "0" * 40

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")


def _git(repo: Path, *args: str) -> str:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.invalid",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.invalid",
    }
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, env=env
    ).stdout.strip()


def _commit(repo: Path, path: str, text: str) -> str:
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    _git(repo, "add", path)
    _git(repo, "commit", "-q", "-m", f"change {path}")
    return _git(repo, "rev-parse", "HEAD")


def _listed(repo: Path, stdin: str) -> set[str]:
    hook = repo / "scripts" / "hooks" / "pre-push"
    env = {**os.environ, "COPERCEIVE_PREPUSH_LIST_FILES": "1"}
    out = subprocess.run(
        ["bash", str(hook)],
        cwd=repo,
        input=stdin,
        capture_output=True,
        text=True,
        env=env,
        check=True,
    ).stdout
    return {line for line in out.splitlines() if line.strip()}


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "release")
    (repo / "scripts" / "hooks").mkdir(parents=True)
    shutil.copy(HOOK, repo / "scripts" / "hooks" / "pre-push")
    _git(repo, "add", "scripts")
    _git(repo, "commit", "-q", "-m", "hook")
    _commit(repo, "docs/a.md", "a\n")
    return repo


def _publish(repo: Path, ref: str, oid: str) -> None:
    _git(repo, "update-ref", f"refs/remotes/origin/{ref}", oid)


def test_rebased_branch_is_checked_only_for_its_own_files(repo: Path) -> None:
    base = _git(repo, "rev-parse", "HEAD")
    _publish(repo, "release/v9", base)
    _git(repo, "switch", "-q", "-c", "feature")
    old_head = _commit(repo, "docs/feature.md", "feature\n")
    _publish(repo, "agent/feature", old_head)

    # The release branch moves on and touches src-tauri/ and web/.
    _git(repo, "switch", "-q", "release")
    _commit(repo, "src-tauri/src/main.rs", "fn main() {}\n")
    new_base = _commit(repo, "web/src/app.ts", "export {};\n")
    _publish(repo, "release/v9", new_base)

    # The feature branch is rebased onto it and pushed over its old remote head.
    _git(repo, "switch", "-q", "feature")
    _git(repo, "rebase", "-q", "release")
    new_head = _git(repo, "rev-parse", "HEAD")

    listed = _listed(repo, f"refs/heads/feature {new_head} refs/heads/agent/feature {old_head}\n")
    assert listed == {"docs/feature.md"}


def test_merging_the_release_branch_in_does_not_count_its_files(repo: Path) -> None:
    base = _git(repo, "rev-parse", "HEAD")
    _publish(repo, "release/v9", base)
    _git(repo, "switch", "-q", "-c", "feature")
    old_head = _commit(repo, "docs/feature.md", "feature\n")
    _git(repo, "switch", "-q", "release")
    new_base = _commit(repo, "src-tauri/src/main.rs", "fn main() {}\n")
    _publish(repo, "release/v9", new_base)
    _git(repo, "switch", "-q", "feature")
    _git(repo, "merge", "-q", "--no-edit", "release")
    new_head = _git(repo, "rev-parse", "HEAD")

    listed = _listed(repo, f"refs/heads/feature {new_head} refs/heads/agent/feature {old_head}\n")
    assert listed == {"docs/feature.md"}


def test_fast_forward_push_still_checks_new_files(repo: Path) -> None:
    base = _git(repo, "rev-parse", "HEAD")
    _publish(repo, "release/v9", base)
    _git(repo, "switch", "-q", "-c", "feature")
    old_head = _commit(repo, "docs/feature.md", "feature\n")
    new_head = _commit(repo, "src-tauri/src/lib.rs", "pub fn f() {}\n")

    listed = _listed(repo, f"refs/heads/feature {new_head} refs/heads/agent/feature {old_head}\n")
    assert "src-tauri/src/lib.rs" in listed


def test_push_to_the_integration_branch_checks_only_the_new_commits(repo: Path) -> None:
    old_head = _git(repo, "rev-parse", "HEAD")
    _publish(repo, "release/v9", old_head)
    new_head = _commit(repo, "web/src/app.ts", "export {};\n")

    listed = _listed(repo, f"refs/heads/release {new_head} refs/heads/release/v9 {old_head}\n")
    assert listed == {"web/src/app.ts"}


def test_new_branch_push_is_unchanged(repo: Path) -> None:
    base = _git(repo, "rev-parse", "HEAD")
    _publish(repo, "release/v9", base)
    _git(repo, "switch", "-q", "-c", "feature")
    head = _commit(repo, "docs/feature.md", "feature\n")

    listed = _listed(repo, f"refs/heads/feature {head} refs/heads/agent/feature {ZERO}\n")
    assert listed == {"docs/feature.md"}
