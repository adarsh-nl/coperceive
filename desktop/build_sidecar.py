# SPDX-License-Identifier: Apache-2.0
"""Freeze ``coperceive serve`` into ``desktop/src-tauri/binaries``."""

from __future__ import annotations

import stat
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BIN = ROOT / "src-tauri" / "binaries"


def main() -> None:
    BIN.mkdir(parents=True, exist_ok=True)
    subprocess.check_call(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--distpath",
            str(BIN),
            "--workpath",
            str(ROOT / "build" / "work"),
            str(ROOT / "coperceive-server.spec"),
        ],
        cwd=ROOT,
    )
    name = "coperceive-server.exe" if sys.platform == "win32" else "coperceive-server"
    produced = BIN / name
    if not produced.is_file():
        raise SystemExit(f"sidecar was not written: {produced}")
    produced.chmod(produced.stat().st_mode | stat.S_IEXEC)


if __name__ == "__main__":
    main()
