# SPDX-License-Identifier: Apache-2.0
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportArgumentType=false, reportAttributeAccessIssue=false, reportOptionalMemberAccess=false, reportIndexIssue=false, reportUnknownLambdaType=false
"""Read-only detection and indexing of raw datasets on a cluster.

This is the login-node half of cluster datasets: it recognises a built-in raw
layout, reads the adapter's scene index, and computes a bounded metadata
fingerprint. It never converts, writes, or reads scene data such as images or
point clouds. The schemas are frozen in ``docs/datasets/cluster-datasets.md``.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import time
from collections.abc import Callable, Mapping
from concurrent.futures import ProcessPoolExecutor, as_completed
from contextlib import contextmanager
from pathlib import Path
from typing import Any, cast

from coperceive.adapters.build import BuildError, ManifestAdapter
from coperceive.adapters.ingest import ingest
from coperceive.adapters.registry import RegistryError, resolve_adapter
from coperceive.adapters.spec import SpecError

DETECT_SCHEMA = "coperceive-cluster-detect-1"
INDEX_SCHEMA = "coperceive-cluster-index-1"
CACHE_SCHEMA = "coperceive-cluster-dataset-cache-1"
SCENE_STATUS_SCHEMA = "coperceive-cluster-scene-status-1"
PREPARE_PROGRESS_SCHEMA = "coperceive-cluster-prepare-progress-1"
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


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _directory_bytes(root: Path, *, max_entries: int) -> int:
    total = 0
    seen = 0
    pending = [root]
    while pending and seen < max_entries:
        folder = pending.pop()
        try:
            with os.scandir(folder) as iterator:
                for entry in iterator:
                    if seen >= max_entries:
                        break
                    seen += 1
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            pending.append(Path(entry.path))
                        elif entry.is_file(follow_symlinks=False):
                            total += entry.stat(follow_symlinks=False).st_size
                    except OSError:
                        continue
        except OSError:
            continue
    return total


def cache_split_dir(
    results_root: str | Path, adapter: str, raw_root: str | Path, split: str | None
) -> Path:
    return Path(results_root) / cache_relative_path(adapter, raw_root, split)


def _dataset_json_path(cache_dir: Path) -> Path:
    return cache_dir / "dataset.json"


def _read_dataset(cache_dir: Path) -> dict[str, Any]:
    path = _dataset_json_path(cache_dir)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DatasetError(f"cannot read {path}: {exc}") from None
    if payload.get("schema") != CACHE_SCHEMA:
        raise DatasetError(f"{path} is not a {CACHE_SCHEMA} document")
    return cast(dict[str, Any], payload)


def _write_dataset(cache_dir: Path, payload: dict[str, Any]) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = _dataset_json_path(cache_dir)
    temporary = cache_dir / f".dataset.json.{os.getpid()}.tmp"
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _scene_ready(cache_dir: Path, scene_id: str) -> bool:
    folder = cache_dir / scene_id
    return (folder / "manifest.json").is_file() and (folder / "COMPLETE.json").is_file()


def _status_record(cache_dir: Path, record: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "scene_id": record.get("scene_id"),
        "state": record.get("state"),
        "reason": record.get("reason"),
        "job_id": record.get("job_id"),
        "prepared_unix": record.get("prepared_unix"),
        "scene_dir": record.get("scene_dir"),
    }


def prepare_progress(
    stage: str,
    *,
    scene_id: str | None,
    done: int,
    total: int,
    message: str,
    state: str | None = None,
) -> dict[str, Any]:
    return {
        "schema": PREPARE_PROGRESS_SCHEMA,
        "stage": stage,
        "scene_id": scene_id,
        "state": state,
        "done": done,
        "total": total,
        "message": message,
    }


@contextmanager
def _cache_lock(cache: Path):
    cache.mkdir(parents=True, exist_ok=True)
    lock_path = cache / ".dataset.json.lock"
    with open(lock_path, "a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _job_alive(job_id: str) -> bool | None:
    """True/False from squeue, or None when squeue is unavailable."""
    try:
        result = subprocess.run(
            ["squeue", "-h", "-j", job_id, "-o", "%T"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.returncode == 0 and bool(result.stdout.strip())


def _claim_still_live(record: Mapping[str, Any], current_job: str | None, now: float) -> bool:
    if record.get("state") != "preparing":
        return False
    owner = record.get("job_id")
    if current_job and owner == current_job:
        return True
    claimed = record.get("claimed_unix")
    if not isinstance(claimed, (int, float)):
        return True
    if now - float(claimed) >= 4 * 3600:
        return False
    if isinstance(owner, str) and owner:
        alive = _job_alive(owner)
        if alive is False:
            return False
    return True


def _claim_and_prepare_scene(
    adapter_name: str,
    raw_root: str,
    cache_root: str,
    scene_id: str,
    params: Mapping[str, str],
    job_id: str | None,
) -> tuple[str, str, str | None, int | None]:
    raw = Path(raw_root)
    cache = Path(cache_root)
    adapter = resolve_adapter(adapter_name)
    now = time.time()
    with _cache_lock(cache):
        dataset = _read_dataset(cache)
        record = dataset.setdefault("scenes", {}).setdefault(
            scene_id,
            {
                "state": "not_prepared",
                "reason": None,
                "job_id": None,
                "prepared_unix": None,
                "scene_dir": scene_id,
            },
        )
        if record.get("state") == "ready" and _scene_ready(cache, scene_id):
            return scene_id, "ready", None, record.get("prepared_unix")
        if _claim_still_live(record, job_id, now):
            return scene_id, "preparing", "another prepare job claimed this scene", None
        record.update(
            {
                "state": "preparing",
                "reason": None,
                "job_id": job_id,
                "claimed_unix": int(now),
                "prepared_unix": None,
            }
        )
        dataset["updated_unix"] = int(now)
        _write_dataset(cache, dataset)
    partial_root = cache / ".partial"
    partial_root.mkdir(parents=True, exist_ok=True)
    temporary = partial_root / scene_id
    shutil.rmtree(temporary, ignore_errors=True)
    state = "failed"
    reason: str | None = None
    prepared: int | None = None
    try:
        ingest(
            adapter,
            raw,
            partial_root,
            params=params,
            workers=1,
            scene_ids=[scene_id],
        )
        produced = partial_root / scene_id
        if not (produced / "manifest.json").is_file() or not (produced / "COMPLETE.json").is_file():
            raise DatasetError("ingest finished without a complete scene")
        destination = cache / scene_id
        old = cache / f".old-{scene_id}"
        if old.exists():
            shutil.rmtree(old)
        if destination.exists():
            destination.rename(old)
        os.replace(produced, destination)
        if old.exists():
            shutil.rmtree(old)
        state = "ready"
        prepared = int(time.time())
    except Exception as exc:  # noqa: BLE001 - one bad scene must not abort the dataset
        state = "failed"
        reason = f"{type(exc).__name__}: {exc}"
    finally:
        shutil.rmtree(temporary, ignore_errors=True)
        try:
            partial_root.rmdir()
        except OSError:
            pass
    with _cache_lock(cache):
        dataset = _read_dataset(cache)
        record = dataset.setdefault("scenes", {}).setdefault(scene_id, {})
        if record.get("job_id") != job_id:
            # A newer owner took this scene over while we were finishing; never
            # overwrite its state with our (superseded) result.
            return (
                scene_id,
                str(record.get("state") or "preparing"),
                "superseded by another prepare job",
                None,
            )
        record.update(
            {
                "state": state,
                "reason": reason,
                "job_id": job_id,
                "prepared_unix": prepared,
                "scene_dir": scene_id,
            }
        )
        dataset["updated_unix"] = int(time.time())
        _write_dataset(cache, dataset)
    return scene_id, state, reason, prepared


def prepare_cluster_dataset(
    adapter_name: str,
    raw_root: str | Path,
    cache_dir: str | Path,
    *,
    scenes: list[str] | None = None,
    workers: int = 1,
    progress: Callable[[dict[str, Any]], None] | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> dict[str, Any]:
    """Prepare selected scenes in parallel, with per-scene atomic staging."""
    if adapter_name not in _BUILTIN:
        raise DatasetError(f"{adapter_name!r} is not a built-in cluster adapter")
    raw = _real(raw_root)
    cache = _real(cache_dir)
    if not raw.is_dir():
        raise DatasetError(f"raw root is not a directory: {raw}")
    raw, split = _raw_and_split(adapter_name, raw)
    if _inside(cache, raw):
        raise DatasetError("the cache directory must not be inside the raw dataset root")
    params = _params_for(adapter_name, raw, split)
    adapter = resolve_adapter(adapter_name)
    refs = list(adapter.scenes(raw, params))
    wanted = set(scenes) if scenes else None
    selected = [ref for ref in refs if wanted is None or ref.scene_id in wanted]
    if wanted is not None:
        missing = wanted - {ref.scene_id for ref in refs}
        if missing:
            raise DatasetError(f"unknown scene(s): {', '.join(sorted(missing))}")
    if not selected:
        raise DatasetError("no scenes selected")
    fingerprint = _fingerprint(
        raw / split if split else raw,
        max_entries=DEFAULT_MAX_ENTRIES,
        deadline=time.monotonic() + max(2.0, timeout_s),
    )
    notify = progress or (lambda _event: None)
    with _cache_lock(cache):
        try:
            dataset = _read_dataset(cache)
        except DatasetError:
            dataset = {
                "schema": CACHE_SCHEMA,
                "adapter": adapter_name,
                "raw_root": str(raw),
                "split": split,
                "params": params,
                "cache_root": str(cache),
                "raw_fingerprint": fingerprint,
                "created_unix": int(time.time()),
                "updated_unix": int(time.time()),
                "scenes": {},
            }
        if dataset.get("raw_fingerprint", {}).get("digest") != fingerprint["digest"]:
            for record in dataset.get("scenes", {}).values():
                if record.get("state") == "ready":
                    record["state"] = "stale"
                    record["reason"] = "raw dataset fingerprint changed"
        for ref in refs:
            dataset.setdefault("scenes", {}).setdefault(
                ref.scene_id,
                {
                    "state": "not_prepared",
                    "reason": None,
                    "job_id": None,
                    "prepared_unix": None,
                    "scene_dir": ref.scene_id,
                },
            )
        dataset["raw_fingerprint"] = fingerprint
        dataset["updated_unix"] = int(time.time())
        _write_dataset(cache, dataset)
    job_id = os.environ.get("SLURM_JOB_ID", "").strip() or None
    total = len(selected)
    done = 0
    pool_size = max(1, min(workers, total))
    results: dict[str, tuple[str, str | None]] = {}
    with ProcessPoolExecutor(max_workers=pool_size) as pool:
        futures = {
            pool.submit(
                _claim_and_prepare_scene,
                adapter_name,
                str(raw),
                str(cache),
                ref.scene_id,
                params,
                job_id,
            ): ref.scene_id
            for ref in selected
        }
        for future in as_completed(futures):
            scene_id, state, reason, _prepared = future.result()
            results[scene_id] = (state, reason)
            done += 1
            notify(
                prepare_progress(
                    "scene",
                    scene_id=scene_id,
                    done=done,
                    total=total,
                    message=state,
                    state=state,
                )
            )
    notify(
        prepare_progress(
            "done", scene_id=None, done=done, total=total, message="finished", state=None
        )
    )
    return dataset_status_document(cache)


def dataset_status_document(cache_dir: str | Path, *, refresh_stale: bool = True) -> dict[str, Any]:
    cache = _real(cache_dir)
    dataset = _read_dataset(cache)
    fingerprint = dataset.get("raw_fingerprint", {})
    if refresh_stale and isinstance(fingerprint, dict):
        raw_root = dataset.get("raw_root")
        split = dataset.get("split")
        if isinstance(raw_root, str):
            root = Path(raw_root) / split if split else Path(raw_root)
            current = _fingerprint(
                root,
                max_entries=DEFAULT_MAX_ENTRIES,
                deadline=time.monotonic() + max(2.0, DEFAULT_TIMEOUT_S),
            )
            if current["digest"] != fingerprint.get("digest"):
                for record in dataset.get("scenes", {}).values():
                    if record.get("state") == "ready":
                        record["state"] = "stale"
                        record["reason"] = "raw dataset fingerprint changed"
    records = []
    for scene_id, record in sorted(dataset.get("scenes", {}).items()):
        item = _status_record(cache, record)
        item["scene_id"] = scene_id
        records.append(item)
    return {
        "schema": SCENE_STATUS_SCHEMA,
        "cache_root": str(cache),
        "adapter": dataset.get("adapter"),
        "raw_root": dataset.get("raw_root"),
        "split": dataset.get("split"),
        "updated_unix": dataset.get("updated_unix"),
        "scenes": records,
    }


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
    estimated_cache_bytes = 0
    for ref in refs:
        scene_dir = raw / split / ref.scene_id if split else raw / ref.scene_id
        estimated_cache_bytes += _directory_bytes(scene_dir, max_entries=50_000)
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
        "estimated_cache_bytes": estimated_cache_bytes,
        "estimated_cache_bytes_method": (
            "sum of file sizes under each scene directory, metadata only"
        ),
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
    "PREPARE_PROGRESS_SCHEMA",
    "SCENE_STATUS_SCHEMA",
    "cache_relative_path",
    "cache_split_dir",
    "dataset_status_document",
    "detect_cluster_dataset",
    "index_cluster_dataset",
    "prepare_cluster_dataset",
]
