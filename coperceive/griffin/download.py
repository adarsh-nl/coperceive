# SPDX-License-Identifier: Apache-2.0
"""Download the small Griffin-25m archives and check them against ``md5.txt``.

Camera archives are not in the allow-list. Dataset bytes are never written
into this repository. A Hugging Face token, if needed, is read from the
environment by ``huggingface_hub`` and is not logged here.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

REPO_ID = "wjh-svm/Griffin"
SUBSET = "griffin_50scenes_25m"
ALLOW = (
    "md5.txt",
    "drone_metadata.zip",
    "vehicle_metadata.zip",
    "vehicle_lidar.zip",
)


def download(dest: Path, *, subset: str = SUBSET) -> Path:
    """Download the allow-listed files into ``dest / subset`` and verify MD5s."""
    if subset != SUBSET:
        raise ValueError(f"phase 0 downloader only fetches {SUBSET}")
    try:
        from huggingface_hub import hf_hub_download
    except ImportError as exc:
        raise RuntimeError("install the griffin extra: pip install 'coperceive[griffin]'") from exc
    target = dest / subset
    target.mkdir(parents=True, exist_ok=True)
    for name in ALLOW:
        hf_hub_download(
            repo_id=REPO_ID,
            filename=f"datasets/{subset}/{name}",
            repo_type="dataset",
            local_dir=dest / "_hf",
        )
        source = dest / "_hf" / "datasets" / subset / name
        destination = target / name
        destination.write_bytes(source.read_bytes())
    verify_md5(target, [name for name in ALLOW if name != "md5.txt"])
    return target


def verify_md5(directory: Path, names: list[str]) -> None:
    """Check ``names`` against ``md5.txt``.

    The published file includes a hash of itself that does not match the file
    on disk (measured 2026-10-15). That line is ignored. Payload lines are not.
    """
    listed = _parse_md5(directory / "md5.txt")
    for name in names:
        if name == "md5.txt":
            continue
        expected = listed[name]
        digest = hashlib.md5((directory / name).read_bytes()).hexdigest()
        if digest != expected:
            raise ValueError(f"{name} md5 {digest} != {expected}")


def _parse_md5(path: Path) -> dict[str, str]:
    rows: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        digest, name = line.split()
        rows[name.removeprefix("./")] = digest
    return rows
