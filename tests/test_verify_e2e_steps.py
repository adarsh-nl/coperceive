# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest

# Load scripts/desktop/verify_e2e_steps.py directly
SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "desktop" / "verify_e2e_steps.py"
spec = importlib.util.spec_from_file_location("verify_e2e_steps", SCRIPT_PATH)
assert spec and spec.loader
verify_e2e_steps: Any = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verify_e2e_steps)

SAMPLE_PASSING_LOG = (
    "Step 1 UI interactions verified successfully.\n"
    'Step 1 verified state.json content: {"viewpoint": "chase"}\n'
    "Playback requirement (c) verified: max frame gap = 120ms, loop wraps observed = 1\n"
    "Step 2 verified: All restored values from DOM and scene-graph counts match perfectly.\n"
    "Step 2b verified: loop stayed off across quit and relaunch.\n"
    "Step 2c verified: viewer window frame remained completely unchanged across 3 relaunches.\n"
    "Step 2d verified: one viewer window after relaunch.\n"
    "Step 3 verified: switched cleanly to second fixture, new scene rendered.\n"
    "Step 3b verified: Switched from single-scene folder to multi-scene root cleanly.\n"
    "Step 4 verified: Persistent validation error UI displayed with "
    "'Choose another folder' button.\n"
    "Step 5b verified: Connect window contains Library and Get Data catalog navigation.\n"
    "Step 5c verified: import, cancel, resume, open.\n"
    "Step 5d verified: banner, Later, and Restart.\n"
    "Step 5d verified: mock server received latest.json.\n"
    "Step 5e verified: Pause, Resume and Cancel of a dataset download job in Get data.\n"
    "Step 5f verified: Move library relocated synth_001 to the new root, left the old root empty, "
    "and updated state.json.\n"
    "Step 5g verified: Library badge ran in the background and its answer is cached.\n"
    "Real-App macOS E2E Test PASSED Successfully!\n"
)


def test_verify_e2e_log_all_present() -> None:
    missing: list[str] = verify_e2e_steps.verify_e2e_log(SAMPLE_PASSING_LOG)
    assert missing == []


def test_verify_e2e_log_detects_missing_step() -> None:
    # Remove Step 3b
    truncated = "\n".join(
        line for line in SAMPLE_PASSING_LOG.splitlines() if "Step 3b verified" not in line
    )
    missing: list[str] = verify_e2e_steps.verify_e2e_log(truncated)
    assert len(missing) == 1
    assert "Step 3b verified" in missing[0]


def test_verify_e2e_log_detects_missing_passed() -> None:
    # Remove final PASSED line
    marker = "Real-App macOS E2E Test PASSED"
    truncated = "\n".join(line for line in SAMPLE_PASSING_LOG.splitlines() if marker not in line)
    missing: list[str] = verify_e2e_steps.verify_e2e_log(truncated)
    assert len(missing) == 1
    assert marker in missing[0]


def test_main_cli_exit_codes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    passing_file = tmp_path / "passing.log"
    passing_file.write_text(SAMPLE_PASSING_LOG, encoding="utf-8")

    failing_file = tmp_path / "failing.log"
    failing_lines = [line for line in SAMPLE_PASSING_LOG.splitlines() if "Step 5f" not in line]
    failing_file.write_text("\n".join(failing_lines), encoding="utf-8")

    monkeypatch.setattr("sys.argv", ["verify_e2e_steps.py", str(passing_file)])
    assert verify_e2e_steps.main() == 0

    monkeypatch.setattr("sys.argv", ["verify_e2e_steps.py", str(failing_file)])
    assert verify_e2e_steps.main() == 1
