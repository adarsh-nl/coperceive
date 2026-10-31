# SPDX-License-Identifier: Apache-2.0
"""Metadata-only folder scan behind ``coperceive remote scan``.

The command runs on a cluster login node, so it must stay light: it stats
directory entries, reads a scene's ``manifest.json`` only (never points, images,
parquet or any other data file), bounds the depth, the entries per directory,
the total folders and the wall-clock time, and returns partial results with a
``truncated`` marker when a bound stops it.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypedDict, cast

DEFAULT_DEPTH = 2
MAX_DEPTH = 4
DEFAULT_TIMEOUT_S = 10.0
MAX_TIMEOUT_S = 30.0
DEFAULT_MAX_ENTRIES = 200
MAX_MAX_ENTRIES = 1000
DEFAULT_MAX_TOTAL = 2000
MAX_MAX_TOTAL = 10_000
MANIFEST_MAX_BYTES = 1_048_576
MANIFEST = "manifest.json"
COMPLETE = "COMPLETE.json"


class ScanError(ValueError):
    """The request is invalid, or the path is not a directory inside the root."""


class FolderInfo(TypedDict):
    """One folder described by the scan; paths are relative to ``--root``."""

    path: str
    depth: int
    is_scene: bool
    scene_id: str | None
    dataset: bool
    scene_count: int
    has_manifest: bool
    has_complete: bool
    subfolder_count: int
    file_count: int


class SceneInfo(TypedDict):
    """One detected coperceive scene (a folder with manifest.json and COMPLETE.json)."""

    path: str
    scene_id: str
    folder_name: str


class DatasetInfo(TypedDict):
    """A non-scene folder with at least one scene below it."""

    path: str
    scene_count: int


class SkippedInfo(TypedDict):
    """A directory entry the scan did not follow, and why."""

    path: str
    reason: str


class ScanResult(TypedDict):
    """The JSON document printed by ``coperceive remote scan``."""

    status: str
    root: str
    path: str
    relative_path: str
    depth: int
    timeout_s: float
    max_entries: int
    max_total: int
    elapsed_ms: float
    truncated: bool
    truncated_reason: str | None
    folder_count: int
    scene_count: int
    dataset_count: int
    folders: list[FolderInfo]
    scenes: list[SceneInfo]
    datasets: list[DatasetInfo]
    skipped: list[SkippedInfo]


@dataclass
class _Limits:
    depth: int
    timeout_s: float
    max_entries: int
    max_total: int


@dataclass
class _State:
    limits: _Limits
    root: Path
    deadline: float
    clock: Callable[[], float]
    folders: list[FolderInfo] = field(default_factory=list[FolderInfo])
    scenes: list[SceneInfo] = field(default_factory=list[SceneInfo])
    skipped: list[SkippedInfo] = field(default_factory=list[SkippedInfo])
    visited: set[str] = field(default_factory=set[str])
    processed: int = 0
    truncated: bool = False
    truncated_reason: str | None = None

    def check(self) -> bool:
        """False when the wall-clock budget is spent; marks ``truncated``."""
        if self.truncated:
            return False
        if self.clock() >= self.deadline:
            self.mark("timeout")
            return False
        return True

    def mark(self, reason: str) -> None:
        if not self.truncated:
            self.truncated = True
            self.truncated_reason = reason


def _real(path: Path) -> Path:
    return Path(os.path.realpath(path))


def _inside(root: Path, candidate: Path) -> bool:
    return candidate == root or root in candidate.parents


def _requested_relative(raw: Path) -> str:
    """Keep the name the user navigated through, even for a symlinked directory."""
    parts = [part for part in raw.parts if part not in ("", ".", "/")]
    return "/".join(parts) if parts else "."


def _relative(root: Path, path: Path) -> str:
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        return str(path)
    return "/".join(parts) if parts else "."


def _child_path(parent: str, name: str) -> str:
    return name if parent == "." else f"{parent}/{name}"


def _validate_bounds(*, depth: int, timeout_s: float, max_entries: int, max_total: int) -> _Limits:
    if not 0 <= depth <= MAX_DEPTH:
        raise ScanError(f"depth must be between 0 and {MAX_DEPTH}: {depth}")
    if not 0 < timeout_s <= MAX_TIMEOUT_S:
        raise ScanError(
            f"timeout must be more than 0 and at most {MAX_TIMEOUT_S:g} seconds: {timeout_s:g}"
        )
    if not 1 <= max_entries <= MAX_MAX_ENTRIES:
        raise ScanError(f"max-entries must be between 1 and {MAX_MAX_ENTRIES}: {max_entries}")
    if not 1 <= max_total <= MAX_MAX_TOTAL:
        raise ScanError(f"max-total must be between 1 and {MAX_MAX_TOTAL}: {max_total}")
    return _Limits(depth=depth, timeout_s=timeout_s, max_entries=max_entries, max_total=max_total)


def resolve_request(root: str | Path, path: str | Path) -> tuple[Path, Path, str]:
    """Return (root realpath, target realpath, path relative to root).

    ``path`` must stay inside ``root``: absolute paths outside it, ``..``
    escapes and symlinks whose realpath leaves the root are refused.
    """
    raw = Path(str(path)) if str(path) else Path(".")
    if ".." in raw.parts:
        raise ScanError(f"the path must not contain '..': {path}")
    root_real = _real(Path(str(root)))
    if not root_real.is_dir():
        raise ScanError(f"the data root is not a directory: {root}")
    candidate = raw if raw.is_absolute() else root_real / raw
    target_real = _real(candidate)
    if raw.is_absolute() and not _inside(root_real, target_real):
        raise ScanError(f"the path is outside the data root: {path}")
    # A symlinked directory is followed when its target is a readable directory,
    # including a shared dataset outside the selected root. The listing follows
    # the same rule, so a folder that appears can always be opened.
    if not target_real.is_dir():
        raise ScanError(f"the path is not a directory: {path}")
    if not os.access(target_real, os.R_OK | os.X_OK):
        raise ScanError(f"the path is not a readable directory: {path}")
    if raw.is_absolute():
        return root_real, target_real, _relative(root_real, target_real)
    return root_real, target_real, _requested_relative(raw)


def _manifest_scene_id(manifest: Path) -> str | None:
    """The scene_id recorded in a small ``manifest.json``; nothing else is read."""
    try:
        if manifest.stat().st_size > MANIFEST_MAX_BYTES:
            return None
        raw: object = json.loads(manifest.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    payload = cast(dict[object, object], raw)
    scene_id = payload.get("scene_id")
    return scene_id if isinstance(scene_id, str) and scene_id else None


def _scan_dir(state: _State, real_dir: Path, rel_dir: str, depth: int) -> None:
    """Scan one folder; records it and recurses while the bounds allow."""
    if state.processed >= state.limits.max_total:
        state.mark("total limit")
        return
    state.processed += 1

    files: list[str] = []
    dirs: list[tuple[str, Path]] = []
    entries_seen = 0
    try:
        with os.scandir(real_dir) as iterator:
            for entry in iterator:
                if not state.check():
                    break
                entries_seen += 1
                if entries_seen > state.limits.max_entries:
                    state.mark("entry limit")
                    break
                try:
                    is_link = entry.is_symlink()
                except OSError:
                    continue
                if is_link:
                    link_path = Path(entry.path)
                    target = _real(link_path)
                    # Follow a readable directory even when the target is outside
                    # the selected root (shared dataset trees often do this); the
                    # resolver above applies the same rule on navigation.
                    if target.is_dir():
                        if os.access(target, os.R_OK | os.X_OK):
                            dirs.append((entry.name, target))
                        else:
                            state.skipped.append(
                                {
                                    "path": _relative(state.root, link_path),
                                    "reason": "symlink target is not a readable directory",
                                }
                            )
                    elif os.access(target, os.R_OK):
                        files.append(entry.name)
                    else:
                        state.skipped.append(
                            {
                                "path": _relative(state.root, link_path),
                                "reason": "symlink target is not readable",
                            }
                        )
                elif entry.is_dir(follow_symlinks=False):
                    dirs.append((entry.name, Path(entry.path)))
                else:
                    files.append(entry.name)
    except OSError as exc:
        state.skipped.append(
            {"path": rel_dir, "reason": f"could not read the folder: {exc.strerror or exc}"}
        )

    files.sort()
    dirs.sort(key=lambda item: item[0])
    has_manifest = MANIFEST in files
    has_complete = COMPLETE in files
    is_scene = has_manifest and has_complete
    scene_id = _manifest_scene_id(real_dir / MANIFEST) if is_scene else None
    record: FolderInfo = {
        "path": rel_dir,
        "depth": depth,
        "is_scene": is_scene,
        "scene_id": scene_id if is_scene else None,
        "dataset": False,
        "scene_count": 0,
        "has_manifest": has_manifest,
        "has_complete": has_complete,
        "subfolder_count": len(dirs),
        "file_count": len(files),
    }
    state.folders.append(record)
    if is_scene:
        state.scenes.append(
            {
                "path": rel_dir,
                "scene_id": scene_id or real_dir.name,
                "folder_name": real_dir.name,
            }
        )

    if depth >= state.limits.depth:
        return
    for name, child_real in dirs:
        child_rel = _child_path(rel_dir, name)
        child_key = str(child_real)
        if child_key in state.visited:
            state.skipped.append({"path": child_rel, "reason": "already visited (symlink loop)"})
            continue
        state.visited.add(child_key)
        _scan_dir(state, child_real, child_rel, depth + 1)


def _mark_datasets(state: _State) -> list[DatasetInfo]:
    """A dataset is a non-scene folder with at least one scene anywhere below it."""
    datasets: list[DatasetInfo] = []
    for record in state.folders:
        if record["is_scene"]:
            continue
        folder = record["path"]
        prefix = "" if folder == "." else folder + "/"
        count = sum(
            1
            for scene in state.scenes
            if scene["path"] != folder and scene["path"].startswith(prefix)
        )
        if count:
            record["dataset"] = True
            record["scene_count"] = count
            datasets.append({"path": folder, "scene_count": count})
    return datasets


def scan_tree(
    root: str | Path,
    path: str | Path,
    *,
    depth: int = DEFAULT_DEPTH,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    max_entries: int = DEFAULT_MAX_ENTRIES,
    max_total: int = DEFAULT_MAX_TOTAL,
    clock: Callable[[], float] = time.monotonic,
) -> ScanResult:
    """Describe the folders and coperceive scenes under ``path`` (metadata only)."""
    limits = _validate_bounds(
        depth=depth, timeout_s=timeout_s, max_entries=max_entries, max_total=max_total
    )
    root_real, target_real, rel_path = resolve_request(root, path)
    started = clock()
    state = _State(
        limits=limits,
        root=root_real,
        deadline=started + limits.timeout_s,
        clock=clock,
        visited={str(target_real), str(root_real)},
    )
    _scan_dir(state, target_real, rel_path, 0)
    datasets = _mark_datasets(state)
    elapsed_ms = max(0.0, (clock() - started) * 1000.0)
    state.folders.sort(key=lambda record: record["path"])
    state.skipped.sort(key=lambda item: item["path"])
    return {
        "status": "ok",
        "root": str(root_real),
        "path": str(target_real),
        "relative_path": rel_path,
        "depth": limits.depth,
        "timeout_s": limits.timeout_s,
        "max_entries": limits.max_entries,
        "max_total": limits.max_total,
        "elapsed_ms": round(elapsed_ms, 3),
        "truncated": state.truncated,
        "truncated_reason": state.truncated_reason,
        "folder_count": len(state.folders),
        "scene_count": len(state.scenes),
        "dataset_count": len(datasets),
        "folders": state.folders,
        "scenes": state.scenes,
        "datasets": datasets,
        "skipped": state.skipped,
    }


__all__ = [
    "COMPLETE",
    "DEFAULT_DEPTH",
    "DEFAULT_MAX_ENTRIES",
    "DEFAULT_MAX_TOTAL",
    "DEFAULT_TIMEOUT_S",
    "MANIFEST",
    "MAX_DEPTH",
    "MAX_MAX_ENTRIES",
    "MAX_MAX_TOTAL",
    "MAX_TIMEOUT_S",
    "DatasetInfo",
    "FolderInfo",
    "ScanError",
    "ScanResult",
    "SceneInfo",
    "SkippedInfo",
    "resolve_request",
    "scan_tree",
]
