# SPDX-License-Identifier: Apache-2.0
"""What a raw dataset folder is, from directory listings only.

Shared by ``coperceive dataset detect`` and the folder scan behind the desktop's
Servers browser ("Recognised as"). It must stay light on a login node: no
dataset modules are imported, no data file is read, and every listing is
bounded. A folder it cannot place is left unrecognised (``None``), never guessed.
"""

from __future__ import annotations

import os
from pathlib import Path

SPLITS = {
    "opv2v": ("train", "validate", "test", "test_culver_city"),
    "v2xset": ("train", "validate", "test"),
}
GRIFFIN_SUBSETS = ("25m", "40m", "55m", "random")
DETECT_PROBE_SCENES = 8
# The scan labels many folders at once: fewer scenes probed per folder.
SCAN_PROBE_SCENES = 3
CHECKPOINT_SUFFIXES = (".pth", ".pt", ".ckpt")
_LISTING_LIMIT = 200


def scene_dirs(split_dir: Path, limit: int) -> list[str]:
    """Scene directory names in a split, from one listing (no per-entry stat)."""
    names: list[str] = []
    with os.scandir(split_dir) as entries:
        for entry in entries:
            if entry.name.startswith(".") or not entry.is_dir():
                continue
            names.append(entry.name)
            if len(names) >= limit:
                break
    return sorted(names)


def is_v2xset(
    raw_root: Path, scenes: list[str], split: str, probe: int = DETECT_PROBE_SCENES
) -> bool:
    """V2XSet scenes carry a roadside unit as agent ``-1``; OPV2V's never do."""
    for scene in scenes[:probe]:
        try:
            with os.scandir(raw_root / split / scene) as agents:
                if any(agent.name == "-1" and agent.is_dir() for agent in agents):
                    return True
        except OSError:
            continue
    return False


def is_griffin(folder: Path) -> bool:
    return (folder / "vehicle-side" / "scene_infos.json").is_file() and (
        folder / "drone-side" / "scene_infos.json"
    ).is_file()


def _opencood(folder: Path) -> tuple[str, list[str]] | None:
    """("OPV2V" or "V2XSet", the splits present) for a raw OpenCOOD release root."""
    splits = [name for name in SPLITS["opv2v"] if (folder / name).is_dir()]
    if not splits:
        return None
    try:
        scenes = scene_dirs(folder / splits[0], SCAN_PROBE_SCENES)
    except OSError:
        return None
    if not scenes:
        return None
    name = "V2XSet" if is_v2xset(folder, scenes, splits[0], SCAN_PROBE_SCENES) else "OPV2V"
    return name, splits


def recognise(folder: Path) -> str | None:
    """A short label for a folder, or None when nothing here says what it is."""
    try:
        if is_griffin(folder):
            return "Griffin"
        subsets = [name for name in GRIFFIN_SUBSETS if is_griffin(folder / name)]
        if subsets:
            return f"Griffin · {', '.join(subsets)}"
        found = _opencood(folder)
        if found:
            return f"{found[0]} · {', '.join(found[1])}"
        if folder.name in SPLITS["opv2v"]:
            parent = _opencood(folder.parent)
            if parent:
                return f"{parent[0]} · {folder.name} split"
        datasets = 0
        checkpoints = 0
        with os.scandir(folder) as entries:
            for index, entry in enumerate(entries):
                if index >= _LISTING_LIMIT:
                    break
                if entry.name.startswith("."):
                    continue
                if entry.is_dir() and datasets < 10 and _opencood(Path(entry.path)):
                    datasets += 1
                elif entry.name.endswith(CHECKPOINT_SUFFIXES):
                    checkpoints += 1
        if datasets >= 2:
            return f"OpenCOOD root · {datasets} datasets"
        if checkpoints:
            return f"Checkpoints · {checkpoints}"
    except OSError:
        return None
    return None
