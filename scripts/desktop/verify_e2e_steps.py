#!/usr/bin/env python3
"""verify_e2e_steps.py

Validates that all required 'Step N verified' lines (plus 'Real-App macOS E2E Test PASSED')
are present in the desktop e2e log. Emits GitHub Actions notices for matched lines and fails
with exit code 1 if any required step is missing, naming each missing step.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# Complete list of required step verification markers emitted by scripts/desktop/e2e_mac.sh.
REQUIRED_STEP_MARKERS: list[str] = [
    "Step 1 UI interactions verified successfully.",
    "Step 1 verified state.json content:",
    "Playback requirement (c) verified",
    "Step 2 verified: All restored values from DOM and scene-graph counts match perfectly.",
    "Step 2b verified: loop stayed off across quit and relaunch.",
    "Step 2c verified: viewer window frame remained completely unchanged across 3 relaunches.",
    "Step 2d verified: one viewer window after relaunch.",
    "Step 3 verified: switched cleanly to second fixture, new scene rendered.",
    "Step 3b verified: Switched from single-scene folder to multi-scene root cleanly.",
    (
        "Step 4 verified: Persistent validation error UI displayed with 'Choose another"
        " folder' button."
    ),
    "Step 5b verified: Connect window contains Library and Get Data catalog navigation.",
    "Step 5c verified: import, cancel, resume, open.",
    "Step 5d verified: banner, Later, and Restart.",
    "Step 5d verified: mock server received latest.json.",
    "Step 5e verified: Pause, Resume and Cancel of a dataset download job in Get data.",
    (
        "Step 5f verified: Move library relocated synth_001 to the new root, left the"
        " old root empty, and updated state.json."
    ),
    "Step 5g verified: Library badge ran in the background and its answer is cached.",
    "Real-App macOS E2E Test PASSED",
]

# Patterns for lines to surface as GitHub Actions notices
NOTICE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"^Step \d[a-z]? verified.*", re.IGNORECASE),
    re.compile(r"^Step \d[a-z]? UI interactions verified.*", re.IGNORECASE),
    re.compile(r"^Playback requirement.*verified.*", re.IGNORECASE),
    re.compile(r"^Real-App macOS E2E Test PASSED.*", re.IGNORECASE),
]

ANSI_ESCAPE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


def strip_ansi(text: str) -> str:
    return ANSI_ESCAPE.sub("", text)


def verify_e2e_log(log_text: str) -> list[str]:
    """Check that all required markers exist in log_text.

    Emits ::notice lines for matched steps.
    Returns a list of missing step descriptions (empty if all passed).
    """
    clean_lines = [strip_ansi(line).strip() for line in log_text.splitlines()]
    joined_clean = "\n".join(clean_lines)

    # Emit notices for relevant progress / verification lines
    seen_notices: set[str] = set()
    for raw_line in clean_lines:
        if "grep " in raw_line or "##[notice]" in raw_line:
            continue
        # Strip runner timestamp / job prefix if present
        line = re.sub(r"^.*?Z\s+", "", raw_line)
        for pat in NOTICE_PATTERNS:
            if pat.search(line):
                if line not in seen_notices:
                    seen_notices.add(line)
                    print(f"::notice title=e2e::{line}")
                break

    # Check for all required markers
    missing: list[str] = []
    for marker in REQUIRED_STEP_MARKERS:
        if marker not in joined_clean:
            missing.append(marker)

    return missing


def main() -> int:
    if len(sys.argv) > 1:
        log_path = Path(sys.argv[1])
        if not log_path.exists():
            print(f"Error: Log file not found: {log_path}", file=sys.stderr)
            return 2
        log_text = log_path.read_text(encoding="utf-8", errors="replace")
    else:
        log_text = sys.stdin.read()

    missing = verify_e2e_log(log_text)
    if missing:
        msg = f"\n[ERROR] E2E Step Gate Failed: {len(missing)} required step(s) missing from log:"
        print(msg, file=sys.stderr)
        for m in missing:
            print(f"::error title=MissingE2EStep::Missing required e2e step: {m}", file=sys.stderr)
            print(f"  - MISSING: {m}", file=sys.stderr)
        return 1

    print(f"\n[SUCCESS] All {len(REQUIRED_STEP_MARKERS)} required E2E steps verified successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
