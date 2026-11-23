# SPDX-License-Identifier: Apache-2.0
"""The machine a bench ran on, for the report (ADR 0020, phase 0).

CPU model and RAM from ``/proc`` on Linux and ``sysctl`` on macOS; elsewhere
only what :mod:`platform` knows. No host or user names: reports are pasted into
pull requests.
"""

from __future__ import annotations

import os
import platform
import subprocess
import sys
from pathlib import Path


def _linux() -> tuple[str | None, int | None]:
    cpu: str | None = None
    memory: int | None = None
    try:
        for line in Path("/proc/cpuinfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("model name"):
                cpu = line.split(":", 1)[1].strip()
                break
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("MemTotal:"):
                memory = int(line.split()[1]) * 1024
                break
    except (OSError, ValueError):
        pass
    return cpu, memory


def _darwin() -> tuple[str | None, int | None]:
    def sysctl(key: str) -> str:
        try:
            return subprocess.run(
                ["sysctl", "-n", key], capture_output=True, text=True, check=False, timeout=5
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return ""

    memory = sysctl("hw.memsize")
    return sysctl("machdep.cpu.brand_string") or None, int(memory) if memory.isdigit() else None


def machine_info() -> dict[str, object]:
    if sys.platform.startswith("linux"):
        cpu, memory = _linux()
    elif sys.platform == "darwin":
        cpu, memory = _darwin()
    else:
        cpu, memory = platform.processor() or None, None
    return {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "cpu": cpu,
        "cpu_count": os.cpu_count(),
        "memory_bytes": memory,
        "python": platform.python_version(),
        "ci": os.environ.get("GITHUB_ACTIONS") == "true",
        "runner": os.environ.get("RUNNER_OS") or None,
    }


def machine_line(info: dict[str, object]) -> str:
    memory = info.get("memory_bytes")
    ram = f"{memory / 1024**3:.0f} GB RAM" if isinstance(memory, int) else "RAM unknown"
    where = " (GitHub Actions runner)" if info.get("ci") else ""
    return (
        f"{info.get('cpu') or info.get('machine')}, {info.get('cpu_count')} logical CPUs, "
        f"{ram}, {info.get('platform')}{where}"
    )
