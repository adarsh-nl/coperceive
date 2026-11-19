# SPDX-License-Identifier: Apache-2.0
"""Write ``environment.json`` for a run, and the shell lines a job script uses.

The program below is fixed text. Job scripts pass the output path as argv and
feed the program on stdin with a quoted heredoc, so nothing from the profile
or the environment is interpolated into the program. A capture failure must
not fail the job (``|| true``).
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
from importlib import metadata
from pathlib import Path

ENVIRONMENT_SCHEMA = "coperceive-environment"
ENVIRONMENT_VERSION = 1
KEY_PACKAGES = ("numpy", "pyarrow", "torch", "pillow", "pyyaml")

# Kept in lockstep with the copy in src-tauri/src/slurm_adapter.rs.
# Do not mention credentials in this text: rendered job scripts are scanned
# for those words.
CAPTURE_PROGRAM = """\
import hashlib
import json
import os
import platform
import subprocess
import sys
from importlib import metadata

out = sys.argv[1]
wanted = ("numpy", "pyarrow", "torch", "pillow", "pyyaml")
packages = {}
for name in wanted:
    try:
        packages[name] = metadata.version(name)
    except metadata.PackageNotFoundError:
        continue
freeze = None
try:
    done = subprocess.run(
        [sys.executable, "-m", "pip", "freeze"],
        capture_output=True,
        timeout=20,
        check=False,
    )
    if done.returncode == 0:
        freeze = hashlib.sha256(done.stdout).hexdigest()
except (OSError, subprocess.TimeoutExpired):
    freeze = None
loaded = os.environ.get("LOADEDMODULES", "")
modules = [part for part in loaded.split(":") if part]
cuda = None
driver = None
try:
    smi = subprocess.run(
        ["nvidia-smi"],
        capture_output=True,
        text=True,
        timeout=8,
        check=False,
    )
    if smi.returncode == 0:
        marker = "CUDA Version:"
        for line in smi.stdout.splitlines():
            if marker in line:
                tail = line.split(marker, 1)[1].strip()
                cuda = tail.split()[0] if tail else None
                break
    query = subprocess.run(
        ["nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader"],
        capture_output=True,
        text=True,
        timeout=8,
        check=False,
    )
    if query.returncode == 0:
        rows = [row.strip() for row in query.stdout.splitlines() if row.strip()]
        if rows:
            driver = rows[0].split(",")[0].strip() or None
except (OSError, subprocess.TimeoutExpired):
    pass
digest = None
for key in ("APPTAINER_IMAGE", "SINGULARITY_IMAGE"):
    value = os.environ.get(key, "")
    if value.startswith("sha256:"):
        digest = value
        break
payload = {
    "schema": "coperceive-environment",
    "schema_version": 1,
    "python": platform.python_version(),
    "key_packages": packages,
    "pip_freeze_sha256": freeze,
    "modules": modules,
    "container_image_digest": digest,
    "cuda": cuda,
    "driver": driver,
}
with open(out, "w", encoding="utf-8") as handle:
    json.dump(payload, handle, sort_keys=True)
    handle.write("\\n")
"""


def environment_capture_lines(destination_shell: str, python: str = "python3") -> list[str]:
    """Bash lines that write ``environment.json``.

    ``destination_shell`` and ``python`` are shell words the caller has already
    quoted when they are not fixed text such as ``python3`` or ``"$OUT_DIR/..."``.
    """
    if "\n" in destination_shell or "\n" in python:
        raise ValueError("environment capture cannot contain a line break")
    return [
        f"{python} - {destination_shell} <<'PY' || true",
        *CAPTURE_PROGRAM.splitlines(),
        "PY",
    ]


def capture_environment(dest: Path) -> dict[str, object]:
    """Write ``environment.json`` for this process and return the object.

    Used by ``coperceive repro capture-env``. The job-script program is the
    same facts, kept as text so a compute node does not need this module.
    """
    packages: dict[str, str] = {}
    for name in KEY_PACKAGES:
        try:
            packages[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            continue
    freeze = _pip_freeze_sha256()
    cuda, driver = _nvidia()
    digest = _container_digest()
    modules = [part for part in os.environ.get("LOADEDMODULES", "").split(":") if part]
    payload: dict[str, object] = {
        "schema": ENVIRONMENT_SCHEMA,
        "schema_version": ENVIRONMENT_VERSION,
        "python": platform.python_version(),
        "key_packages": packages,
        "pip_freeze_sha256": freeze,
        "modules": modules,
        "container_image_digest": digest,
        "cuda": cuda,
        "driver": driver,
    }
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def _pip_freeze_sha256() -> str | None:
    try:
        done = subprocess.run(
            [sys.executable, "-m", "pip", "freeze"],
            capture_output=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if done.returncode != 0:
        return None
    return hashlib.sha256(done.stdout).hexdigest()


def _nvidia() -> tuple[str | None, str | None]:
    cuda: str | None = None
    driver: str | None = None
    try:
        smi = subprocess.run(
            ["nvidia-smi"],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
        query = subprocess.run(
            ["nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None, None
    if smi.returncode == 0:
        marker = "CUDA Version:"
        for line in smi.stdout.splitlines():
            if marker in line:
                tail = line.split(marker, 1)[1].strip()
                cuda = tail.split()[0] if tail else None
                break
    if query.returncode == 0:
        rows = [row.strip() for row in query.stdout.splitlines() if row.strip()]
        if rows:
            driver = rows[0].split(",")[0].strip() or None
    return cuda, driver


def _container_digest() -> str | None:
    for key in ("APPTAINER_IMAGE", "SINGULARITY_IMAGE"):
        value = os.environ.get(key, "")
        if value.startswith("sha256:"):
            return value
    return None
