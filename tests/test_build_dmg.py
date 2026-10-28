# SPDX-License-Identifier: Apache-2.0
"""The DMG build script's bounded retry, proven without a macOS runner.

``scripts/desktop/build_dmg.sh`` retries the Tauri build only when that attempt's
own output contains ``hdiutil: create failed - Resource busy``, and it must never
run more than three builds. The real failure is an intermittent macOS runner
lock (actions/runner-images#7522), so these tests copy the script into a
temporary tree with a fake ``npm`` first on ``PATH``: the retry path, the cap and
the no-retry path then run on the Linux pytest runners as well.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "desktop" / "build_dmg.sh"

# One fake npm for every case: FAKE_NPM_MODE says what the build does and
# FAKE_NPM_COUNTER records how many times it was invoked.
FAKE_NPM = """#!/bin/bash
counter="${FAKE_NPM_COUNTER:?}"
count=0
if [ -f "${counter}" ]; then
  count="$(cat "${counter}")"
fi
count=$((count + 1))
printf '%s' "${count}" > "${counter}"
case "${FAKE_NPM_MODE:?}" in
  once)
    if [ "${count}" -eq 1 ]; then
      echo "Creating disk image..."
      echo "hdiutil: create failed - Resource busy" >&2
      exit 1
    fi
    echo "fake tauri build ok"
    ;;
  always)
    echo "Creating disk image..."
    echo "hdiutil: create failed - Resource busy" >&2
    exit 1
    ;;
  other)
    echo "fake tauri build failed for another reason"
    exit 7
    ;;
  *)
    echo "unknown FAKE_NPM_MODE ${FAKE_NPM_MODE}" >&2
    exit 99
    ;;
esac
"""


def _build_tree(tmp_path: Path) -> Path:
    """A throwaway repository root holding the real script, web/ and the fake npm."""
    script = tmp_path / "scripts" / "desktop" / "build_dmg.sh"
    script.parent.mkdir(parents=True)
    script.write_text(SCRIPT.read_text(encoding="utf-8"), encoding="utf-8")
    script.chmod(0o755)
    (tmp_path / "web").mkdir()
    binaries = tmp_path / "bin"
    binaries.mkdir()
    npm = binaries / "npm"
    npm.write_text(FAKE_NPM, encoding="utf-8")
    npm.chmod(0o755)
    return tmp_path


def _run(
    tmp_path: Path, mode: str, attempts: str | None = None
) -> tuple[subprocess.CompletedProcess[str], str, int]:
    """Run the script against the fake npm; return the result, its log and the npm call count."""
    root = _build_tree(tmp_path)
    counter = tmp_path / "fake_npm_calls"
    build_log = tmp_path / "build.log"
    environment = dict(os.environ)
    environment["PATH"] = f"{tmp_path / 'bin'}{os.pathsep}{environment.get('PATH', '')}"
    environment["FAKE_NPM_MODE"] = mode
    environment["FAKE_NPM_COUNTER"] = str(counter)
    environment["COPERCEIVE_TAURI_BUILD_LOG"] = str(build_log)
    environment["GITHUB_ACTIONS"] = "false"  # the CI-only volume detach stays off
    environment.pop("COPERCEIVE_DMG_ATTEMPTS", None)
    if attempts is not None:
        environment["COPERCEIVE_DMG_ATTEMPTS"] = attempts
    result = subprocess.run(
        ["bash", str(root / "scripts" / "desktop" / "build_dmg.sh")],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    calls = int(counter.read_text(encoding="utf-8")) if counter.exists() else 0
    return result, build_log.read_text(encoding="utf-8"), calls


def test_the_resource_busy_signature_is_retried_once(tmp_path: Path) -> None:
    """The runner lock is retried: two builds, the second one succeeds, exit 0."""
    result, log, calls = _run(tmp_path, "once")
    assert result.returncode == 0, (result.stdout, result.stderr, log)
    assert calls == 2, calls
    assert log.count("=== Tauri build attempt ") == 2, log
    assert "=== Tauri build attempt 1/2 ===" in log, log
    assert "Retrying: hdiutil create hit the runner's transient Resource busy lock." in log, log
    assert "=== Tauri build finished with status 0 ===" in log, log


def test_the_attempt_count_is_capped_at_three(tmp_path: Path) -> None:
    """A signature that keeps hitting the lock stops at three builds, even when asked for nine."""
    result, log, calls = _run(tmp_path, "always", attempts="9")
    assert result.returncode != 0, (result.stdout, result.stderr, log)
    assert calls == 3, calls
    assert log.count("=== Tauri build attempt ") == 3, log
    assert "=== Tauri build attempt 3/3 ===" in log, log
    assert "COPERCEIVE_DMG_ATTEMPTS=9 clamped to 3 (allowed 1..3)" in log, log


def test_a_different_failure_is_not_retried(tmp_path: Path) -> None:
    """Only the exact runner signature is retried; another failure keeps its exit code at once."""
    result, log, calls = _run(tmp_path, "other")
    assert result.returncode == 7, (result.stdout, result.stderr, log)
    assert calls == 1, calls
    assert log.count("=== Tauri build attempt ") == 1, log
    assert "Not retrying: the failure is not the runner's transient hdiutil lock." in log, log


def test_a_non_integer_attempt_count_is_rejected(tmp_path: Path) -> None:
    """A non-integer COPERCEIVE_DMG_ATTEMPTS fails before any build is attempted."""
    result, log, calls = _run(tmp_path, "once", attempts="two")
    assert result.returncode != 0, (result.stdout, result.stderr, log)
    assert calls == 0, calls
    assert "COPERCEIVE_DMG_ATTEMPTS must be a whole number, got 'two'." in result.stderr, (
        result.stderr,
        log,
    )
