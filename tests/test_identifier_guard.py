# SPDX-License-Identifier: Apache-2.0
"""Tests for scripts/ci/check_identifiers.sh."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CHECK_SCRIPT = REPO_ROOT / "scripts" / "ci" / "check_identifiers.sh"
PATTERN_FILE = REPO_ROOT / "scripts" / "ci" / "identifier_patterns.txt"


def test_repo_has_no_personal_or_cluster_identifiers() -> None:
    proc = subprocess.run(
        ["bash", str(CHECK_SCRIPT), str(REPO_ROOT), str(PATTERN_FILE)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, f"Identifier guard failed:\n{proc.stdout}\n{proc.stderr}"


def test_identifier_guard_fails_when_hit_is_planted(tmp_path: Path) -> None:
    (tmp_path / "web" / "src").mkdir(parents=True)
    (tmp_path / "scripts" / "ci").mkdir(parents=True)
    shutil.copy2(PATTERN_FILE, tmp_path / "scripts" / "ci" / "identifier_patterns.txt")
    (tmp_path / "web" / "src" / "leak.ts").write_text(
        'export const HOST = "hpc-head1.ewi.utwente.nl";\n',
        encoding="utf-8",
    )

    planted_patterns = tmp_path / "scripts" / "ci" / "identifier_patterns.txt"
    proc = subprocess.run(
        ["bash", str(CHECK_SCRIPT), str(tmp_path), str(planted_patterns)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode != 0, "Identifier guard must fail when a hit is planted"
    assert "leak.ts" in proc.stderr
