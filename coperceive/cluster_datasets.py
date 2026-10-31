# SPDX-License-Identifier: Apache-2.0
"""Read-only detection and indexing of raw datasets on a cluster.

This is the login-node half of cluster datasets: it recognises a built-in raw
layout, reads the adapter's scene index, and computes a bounded metadata
fingerprint. It never converts, writes, or reads scene data such as images or
point clouds. The schemas are frozen in ``docs/datasets/cluster-datasets.md``.
"""

from __future__ import annotations

import hashlib
import os
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

from coperceive.adapters.build import BuildError, ManifestAdapter
from coperceive.adapters.registry import RegistryError, resolve_adapter
from coperceive.adapters.spec import SpecError

DETECT_SCHEMA = "coperceive-cluster-detect-1"
INDEX_SCHEMA = "coperceive-cluster-index-1"
CACHE_SCHEMA = "coperceive-cluster-dataset-cache-1"
SCENE_STATUS_SCHEMA = "coperceive-cluster-scene-status-1"
CACHE_DIR = "coperceive-cache"
DEFAULT_MAX_ENTRIES = 200_000
DEFAULT_MAX_SCENES = 5_000
DEFAULT_TIMEOUT_S = 30.0
_BUILTIN = ("griffin", "opv2v", "v2xset")
_SPLITS = {
    "opv2v": ("train", "validate", "test", "test_culver_city"),
    "v2xset": ("train", "validate", "test"),
}
_GRIFFIN_SUBSETS = ("25m", "40m", "55m", "random")


class DatasetError(RuntimeError):
    """The path is not a dataset, or the request cannot be bounded safely."""


def _real(path: str | Path) -> Path:
    return Path(os.path.realpath(path))


def _licence(adapter: object) -> dict[str, object] | None:
    spec = getattr(adapter, "spec", None)
    licence = getattr(spec, "licence", None)
    if licence is None:
        return None
    return {
        "status": licence.status,
        "spdx": licence.spdx,
        "notice": licence.notice,
        "links": list(licence.links),
    }


def _params_for(adapter: str, path: Path, split: str | None = None) -> dict[str, str]:
    if adapter in _SPLITS:
        chosen = split or _split_for(adapter, path)
        if chosen is None:
            raise DatasetError("cannot tell which split this directory belongs to")
        return {"split": chosen}
    if adapter == "griffin":
        for part in path.parts:
            if part in _GRIFFIN_SUBSETS:
                return {"subset": part}
        return {"subset": _GRIFFIN_SUBSETS[0]}
    return {}


def _split_for(adapter: str, path: Path) -> str | None:
    allowed = _SPLITS[adapter]
    if path.name in allowed and path.is_dir():
        return path.name
    present = [name for name in allowed if (path / name).is_dir()]
    return present[0] if present else None


def _raw_and_split(adapter: str, raw_root: Path) -> tuple[Path, str | None]:
    if adapter not in _SPLITS:
        return raw_root, None
    allowed = _SPLITS[adapter]
    if raw_root.name in allowed and raw_root.is_dir():
        return raw_root.parent, raw_root.name
    present = [name for name in allowed if (raw_root / name).is_dir()]
    if not present:
        raise DatasetError(f"no split directory ({', '.join(allowed)}) under {raw_root}")
    return raw_root, present[0]


def _is_v2xset(raw_root: Path, split: str, max_scenes: int) -> bool:
    try:
        with os.scandir(raw_root / split) as scenes:
            for index, scene in enumerate(scenes):
                if index >= max_scenes:
                    break
                if not scene.is_dir(follow_symlinks=False):
                    continue
                try:
                    with os.scandir(scene.path) as agents:
                        if any(agent.name == "-1" and agent.is_dir() for agent in agents):
                            return True
                except OSError:
                    continue
    except OSError:
        return False
    return False


def _fingerprint(
    root: Path,
    *,
    max_entries: int = DEFAULT_MAX_ENTRIES,
    deadline: float,
) -> dict[str, object]:
    """Bounded sha1 over file and directory metadata below ``root``."""
    lines: list[str] = []
    entries = 0
    truncated = False
    pending = [root]
    while pending:
        if time.monotonic() >= deadline:
            truncated = True
            break
        folder = pending.pop()
        try:
            with os.scandir(folder) as iterator:
                for entry in iterator:
                    if entries >= max_entries or time.monotonic() >= deadline:
                        truncated = True
                        break
                    entries += 1
                    try:
                        stat = entry.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    relative = str(Path(entry.path).relative_to(root))
                    kind = "d" if entry.is_dir(follow_symlinks=False) else "f"
                    lines.append(f"{kind}\t{relative}\t{stat.st_size}\t{stat.st_mtime_ns}")
                    if kind == "d":
                        pending.append(Path(entry.path))
                else:
                    continue
                break
        except OSError:
            continue
    lines.sort()
    digest = hashlib.sha1("\n".join(lines).encode("utf-8")).hexdigest()
    return {
        "algorithm": "sha1-mtime-size-v1",
        "digest": digest,
        "entries": entries,
        "truncated": truncated,
    }


def cache_relative_path(adapter: str, raw_root: str | Path, split: str | None) -> str:
    real = _real(raw_root)
    root_hash = hashlib.sha1(str(real).encode("utf-8")).hexdigest()[:12]
    pieces = [CACHE_DIR, adapter, root_hash, split or "all"]
    return "/".join(pieces)


def detect_cluster_dataset(
    path: str | Path, *, max_scenes: int = DEFAULT_MAX_SCENES
) -> dict[str, Any]:
    """Identify the built-in adapter and split for a raw cluster path."""
    candidate = _real(path)
    result: dict[str, Any] = {
        "schema": DETECT_SCHEMA,
        "path": str(candidate),
        "adapter": None,
        "raw_root": None,
        "split": None,
        "params": {},
        "licence": None,
        "scene_count": 0,
        "status": "none",
        "reason": None,
    }
    if not candidate.is_dir():
        result["reason"] = f"not a directory: {candidate}"
        return result

    griffin = candidate
    if not (griffin / "vehicle-side" / "scene_infos.json").is_file():
        griffin = candidate / "vehicle-side"
        griffin = griffin.parent if griffin.name == "vehicle-side" else candidate
    if (griffin / "vehicle-side" / "scene_infos.json").is_file() and (
        griffin / "drone-side" / "scene_infos.json"
    ).is_file():
        adapter_name = "griffin"
        raw_root = griffin
        split = None
    else:
        accepted = (*_SPLITS["opv2v"], *_SPLITS["v2xset"])
        if candidate.name in accepted and candidate.is_dir():
            raw_root = candidate.parent
            split = candidate.name
        else:
            present = [name for name in accepted if (candidate / name).is_dir()]
            if not present:
                return result
            raw_root = candidate
            split = present[0]
        adapter_name = "v2xset" if _is_v2xset(raw_root, split, max_scenes) else "opv2v"
        if split not in _SPLITS[adapter_name]:
            adapter_name = "opv2v"
    params = _params_for(adapter_name, raw_root, split)
    try:
        adapter = resolve_adapter(adapter_name)
        refs = adapter.scenes(raw_root, params)
    except (RegistryError, SpecError, BuildError, OSError, ValueError) as exc:
        result["reason"] = str(exc)
        return result
    result.update(
        {
            "adapter": adapter_name,
            "raw_root": str(raw_root),
            "split": split,
            "params": params,
            "licence": _licence(adapter),
            "scene_count": min(len(refs), max_scenes),
            "status": "ok",
            "reason": None,
        }
    )
    return result


def index_cluster_dataset(
    adapter_name: str,
    raw_root: str | Path,
    *,
    max_scenes: int = DEFAULT_MAX_SCENES,
    max_entries: int = DEFAULT_MAX_ENTRIES,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> dict[str, Any]:
    """Every scene of a raw tree, metadata only. Writes nothing."""
    started = time.monotonic()
    deadline = started + max(1.0, timeout_s)
    if adapter_name not in _BUILTIN:
        raise DatasetError(f"{adapter_name!r} is not a built-in cluster adapter")
    raw = _real(raw_root)
    if not raw.is_dir():
        raise DatasetError(f"raw root is not a directory: {raw}")
    raw, split = _raw_and_split(adapter_name, raw)
    params = _params_for(adapter_name, raw, split)
    adapter = resolve_adapter(adapter_name)
    refs = list(adapter.scenes(raw, params))
    if len(refs) > max_scenes:
        refs = refs[:max_scenes]
    report = adapter.check(raw, params, scene_ids=[ref.scene_id for ref in refs])
    counts_by_scene: Mapping[str, tuple[tuple[str, int], ...]] = report.counts
    manifest_adapter = cast(ManifestAdapter, adapter)
    scale_us = manifest_adapter.spec.clock.scale_us or 0
    fingerprint_root = raw / split if split else raw
    fingerprint = _fingerprint(
        fingerprint_root,
        max_entries=max_entries,
        deadline=min(deadline, time.monotonic() + max(1.0, timeout_s)),
    )
    scenes: list[dict[str, Any]] = []
    for ref in refs:
        counts = dict(counts_by_scene.get(ref.scene_id, ()))
        duration_s: float | None = None
        if scale_us:
            duration_s = round(len(ref.samples) * scale_us / 1_000_000, 6)
        scenes.append(
            {
                "scene_id": ref.scene_id,
                "sample_count": len(ref.samples),
                "duration_s": duration_s,
                "agent_count": counts.get("agents", 0),
                "sensors": {
                    "sensors": counts.get("sensors", 0),
                    "camera_samples": counts.get("images", 0),
                    "lidar_samples": counts.get("lidar", 0),
                },
                "raw": {"scene_id": ref.scene_id},
            }
        )
    errors = list(report.errors)
    return {
        "schema": INDEX_SCHEMA,
        "adapter": adapter_name,
        "raw_root": str(raw),
        "split": split,
        "params": params,
        "licence": _licence(adapter),
        "fingerprint": fingerprint,
        "cache": {
            "relative_path": cache_relative_path(adapter_name, raw, split),
        },
        "scene_count": len(scenes),
        "scenes": scenes,
        "errors": errors,
        "truncated": len(refs) >= max_scenes,
        "elapsed_ms": round((time.monotonic() - started) * 1000.0, 3),
    }


__all__ = [
    "CACHE_DIR",
    "CACHE_SCHEMA",
    "DEFAULT_MAX_ENTRIES",
    "DEFAULT_MAX_SCENES",
    "DEFAULT_TIMEOUT_S",
    "DETECT_SCHEMA",
    "DatasetError",
    "INDEX_SCHEMA",
    "SCENE_STATUS_SCHEMA",
    "cache_relative_path",
    "detect_cluster_dataset",
    "index_cluster_dataset",
]
