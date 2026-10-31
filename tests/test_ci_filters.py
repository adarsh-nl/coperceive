# SPDX-License-Identifier: Apache-2.0
"""Tests for CI workflow path filters and triggers."""

from fnmatch import fnmatch
from pathlib import Path
from typing import Any, cast

import yaml


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _load_ci_workflow() -> dict[str, Any]:
    ci_path = _repo_root() / ".github" / "workflows" / "ci.yml"
    assert ci_path.is_file(), f"Workflow file not found: {ci_path}"
    with open(ci_path, encoding="utf-8") as f:
        loaded = cast(dict[str, Any] | None, yaml.safe_load(f))
        return loaded or {}


def test_ci_workflow_triggers_and_concurrency() -> None:
    """Verify that CI runs on PRs, on push only for integration branches,
    includes a nightly schedule, and cancels obsolete runs via concurrency."""
    data = _load_ci_workflow()

    # Triggers (in PyYAML, unquoted 'on:' parses as True or 'on')
    on_raw = cast(dict[str, Any] | None, data.get("on") or data.get(True))  # type: ignore[reportCallIssue]
    on_block: dict[str, Any] = on_raw or {}
    assert "pull_request" in on_block, "Workflow must trigger on pull_request"

    push_block = cast(dict[str, Any], on_block.get("push", {}))
    push_branches = cast(list[str], push_block.get("branches", []))
    assert "main" in push_branches, "Push trigger must include main"
    assert "feat/m2-import" in push_branches, "Push trigger must include feat/m2-import"
    assert len(push_branches) == 2, (
        f"Push trigger should only target integration branches, got {push_branches}"
    )

    # Nightly schedule
    assert "schedule" in on_block, "Workflow must include nightly schedule trigger"
    schedule = cast(list[dict[str, Any]], on_block.get("schedule", []))
    assert any("cron" in s for s in schedule), "Schedule must define cron"

    # Concurrency
    concurrency = cast(dict[str, Any], data.get("concurrency", {}))
    cip = concurrency.get("cancel-in-progress")
    assert cip is True or "pull_request" in str(cip), (
        f"concurrency must enable cancel-in-progress on pull_request, got {cip}"
    )
    assert "group" in concurrency, "concurrency must define group"


def test_desktop_path_filters_cover_required_directories() -> None:
    """Verify that the changes job filters desktop-macos changes on all required paths."""
    data = _load_ci_workflow()
    jobs = cast(dict[str, Any], data.get("jobs", {}))
    assert "changes" in jobs, "Workflow must include a 'changes' job for path filtering"

    changes_job = cast(dict[str, Any], jobs["changes"])
    changes_steps = cast(list[dict[str, Any]], changes_job.get("steps", []))
    paths_filter_step = next(
        (s for s in changes_steps if "dorny/paths-filter" in str(s.get("uses", ""))),
        None,
    )
    assert paths_filter_step is not None, "changes job must use dorny/paths-filter"

    with_block = cast(dict[str, Any], paths_filter_step.get("with", {}))
    filters_str = str(with_block.get("filters", ""))
    filters_yaml = cast(dict[str, Any], yaml.safe_load(filters_str))
    desktop_patterns = cast(list[str], filters_yaml.get("desktop", []))
    assert desktop_patterns, f"Expected 'desktop' patterns in filters, got {filters_yaml}"

    def matches_any(path: str) -> bool:
        return any(fnmatch(path, pattern) for pattern in desktop_patterns)

    # 1. Critical desktop paths MUST match (must not skip)
    assert matches_any("src-tauri/src/main.rs"), "Changes to src-tauri/ must not skip desktop-macos"
    assert matches_any("src-tauri/Cargo.toml"), "Changes to Cargo.toml must not skip desktop-macos"
    assert matches_any("src-tauri/Cargo.lock"), "Changes to Cargo.lock must not skip desktop-macos"
    assert matches_any("web/src/App.tsx"), "Changes to web/ must not skip desktop-macos"
    assert matches_any("web/package-lock.json"), (
        "Changes to web lockfile must not skip desktop-macos"
    )
    assert matches_any("coperceive/app/server.py"), (
        "Changes to coperceive/app/ must not skip desktop-macos"
    )
    assert matches_any("scripts/desktop/e2e_mac.sh"), (
        "Changes to scripts/desktop/ must not skip desktop-macos"
    )
    assert matches_any("scripts/desktop/requirements-darwin-arm64.lock"), (
        "Changes to requirements lock must not skip"
    )
    assert matches_any("pyproject.toml"), "Changes to pyproject.toml must not skip desktop-macos"
    assert matches_any(".github/workflows/ci.yml"), (
        "Changes to CI workflow must not skip desktop-macos"
    )

    # 2. Python-only/docs changes MUST NOT match (must skip desktop-macos)
    assert not matches_any("coperceive/griffin/commands.py"), (
        "Python library changes should skip desktop-macos"
    )
    assert not matches_any("tests/test_cli_griffin.py"), "Test changes should skip desktop-macos"
    assert not matches_any("docs/ci.md"), "Documentation changes should skip desktop-macos"
    assert not matches_any("README.md"), "Root doc changes should skip desktop-macos"
