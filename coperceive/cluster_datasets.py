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
import socket
import subprocess
import time
from collections.abc import Callable, Mapping
from concurrent.futures import ProcessPoolExecutor, as_completed
from contextlib import contextmanager
from pathlib import Path
from typing import Any, cast

from coperceive.adapters.build import BuildError, ManifestAdapter
from coperceive.adapters.ingest import ingest
from coperceive.adapters.prefetch import collecting
from coperceive.adapters.readers.numpy_yaml import yaml_loader_kind
from coperceive.adapters.registry import RegistryError, resolve_adapter
from coperceive.adapters.spec import SpecError
from coperceive.camera_images import IMAGE_MODES, stage_images
from coperceive.errors import CoperceiveError
from coperceive.remote.recognise import (
    DETECT_PROBE_SCENES,
    GRIFFIN_SUBSETS,
    SPLITS,
    is_v2v4real,
    is_v2xset,
    scene_dirs,
)

DETECT_SCHEMA = "coperceive-cluster-detect-1"
INDEX_SCHEMA = "coperceive-cluster-index-1"
CACHE_SCHEMA = "coperceive-cluster-dataset-cache-1"
SCENE_STATUS_SCHEMA = "coperceive-cluster-scene-status-1"
PREPARE_PROGRESS_SCHEMA = "coperceive-cluster-prepare-progress-1"
CACHE_DIR = "coperceive-cache"
DEFAULT_MAX_ENTRIES = 200_000
DEFAULT_MAX_SCENES = 5_000
DEFAULT_TIMEOUT_S = 30.0
_BUILTIN = ("griffin", "opv2v", "v2xset", "v2v4real")
# Shared with the folder scan's "Recognised as" (coperceive/remote/recognise.py).
_SPLITS = SPLITS
_GRIFFIN_SUBSETS = GRIFFIN_SUBSETS


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


def _raw_and_split(
    adapter: str, raw_root: Path, split: str | None = None
) -> tuple[Path, str | None]:
    """The dataset root and the one split to work on. Never guesses between splits."""
    if adapter not in _SPLITS:
        if split:
            raise DatasetError(f"{adapter} has no splits; leave the split out")
        return raw_root, None
    allowed = _SPLITS[adapter]
    if split is not None and split not in allowed:
        raise DatasetError(
            f"{split!r} is not a {adapter} split; choose one of: {', '.join(allowed)}"
        )
    if raw_root.name in allowed and raw_root.is_dir():
        if split is not None and split != raw_root.name:
            raise DatasetError(f"{raw_root} is the {raw_root.name} split, not {split}")
        return raw_root.parent, raw_root.name
    present = [name for name in allowed if (raw_root / name).is_dir()]
    if not present:
        raise DatasetError(f"no split directory ({', '.join(allowed)}) under {raw_root}")
    if split is not None:
        if split not in present:
            raise DatasetError(f"no {split} split under {raw_root}; it has: {', '.join(present)}")
        return raw_root, split
    if len(present) > 1:
        raise DatasetError(f"{raw_root} holds several splits ({', '.join(present)}); choose one")
    return raw_root, present[0]


# Detection looks at this many scenes' agent folders at most; it never lists frames.
_DETECT_PROBE_SCENES = DETECT_PROBE_SCENES


_scene_dirs = scene_dirs
_is_v2xset = is_v2xset


def _fingerprint(
    root: Path,
    *,
    max_entries: int = DEFAULT_MAX_ENTRIES,
    deadline: float,
    sizes: dict[str, int] | None = None,
) -> dict[str, object]:
    """Bounded sha1 over file and directory metadata below ``root``.

    With ``sizes``, file bytes are also summed per top-level entry (a scene
    directory below a split), so the index needs no second walk.
    """
    lines: list[str] = []
    prefix = len(str(root)) + 1
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
                    relative = entry.path[prefix:]
                    kind = "d" if entry.is_dir(follow_symlinks=False) else "f"
                    lines.append(f"{kind}\t{relative}\t{stat.st_size}\t{stat.st_mtime_ns}")
                    if sizes is not None and kind == "f":
                        top = relative.split(os.sep, 1)[0]
                        sizes[top] = sizes.get(top, 0) + stat.st_size
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


def _scene_error(scene_id: str, exc: BaseException) -> str:
    """``scene: reason`` on one line, as the index ``errors`` list holds them."""
    text = " ".join(str(exc).split()) or type(exc).__name__
    return text if text.startswith(f"{scene_id}:") else f"{scene_id}: {text}"


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


def _directory_bytes(root: Path, *, max_entries: int, deadline: float | None = None) -> int:
    total = 0
    seen = 0
    pending = [root]
    while pending and seen < max_entries:
        if deadline is not None and time.monotonic() >= deadline:
            break
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
    # Array tasks on several nodes write here: a pid alone is not unique.
    temporary = cache_dir / f".dataset.json.{socket.gethostname()}.{os.getpid()}.tmp"
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
        "images": record.get("images"),
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


_LOCK_STALE_S = 120.0
_LOCK_WAIT_S = 600.0


@contextmanager
def _cache_lock(cache: Path):
    """Exclusive access to ``dataset.json``, across processes and nodes.

    ``flock`` serialises processes on one node; prepare array tasks run on
    several nodes, where ``flock`` may be node-local (Lustre's ``localflock``),
    so an atomic ``mkdir`` guards the read-modify-write as well. The section is
    a JSON read and write; a lock directory older than two minutes was left by
    a task that died and is taken over.
    """
    cache.mkdir(parents=True, exist_ok=True)
    lock_path = cache / ".dataset.json.lock"
    with open(lock_path, "a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            with _directory_lock(cache / ".dataset.json.lockdir"):
                yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def _directory_lock(path: Path):
    deadline = time.monotonic() + _LOCK_WAIT_S
    while True:
        try:
            path.mkdir()
            break
        except FileExistsError:
            try:
                if time.time() - path.stat().st_mtime > _LOCK_STALE_S:
                    path.rmdir()
                    continue
            except OSError:
                pass
            if time.monotonic() > deadline:
                raise DatasetError(f"timed out waiting for {path.name}") from None
            time.sleep(0.05)
    try:
        yield
    finally:
        try:
            path.rmdir()
        except OSError:
            pass


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


def _slurm_job_id() -> str | None:
    """This task's job id; ``<array>_<task>`` in a prepare array (squeue takes both)."""
    array = os.environ.get("SLURM_ARRAY_JOB_ID", "").strip()
    task = os.environ.get("SLURM_ARRAY_TASK_ID", "").strip()
    if array.isdigit() and task.isdigit():
        return f"{array}_{task}"
    return os.environ.get("SLURM_JOB_ID", "").strip() or None


def mark_scene_images(cache: Path, scene_id: str, mode: str) -> None:
    """Record which camera-image mode a prepared scene uses."""
    with _cache_lock(cache):
        try:
            dataset = _read_dataset(cache)
        except DatasetError:
            return
        record = dataset.get("scenes", {}).get(scene_id)
        if isinstance(record, dict):
            record["images"] = mode
            dataset["updated_unix"] = int(time.time())
            _write_dataset(cache, dataset)


def _claim_and_prepare_scene(
    adapter_name: str,
    raw_root: str,
    cache_root: str,
    scene_id: str,
    params: Mapping[str, str],
    job_id: str | None,
    images: str = "originals",
) -> tuple[str, str, str | None, int | None]:
    raw = Path(raw_root)
    cache = Path(cache_root)
    adapter = resolve_adapter(adapter_name)
    now = time.time()
    restage = False
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
            if record.get("images") == images:
                return scene_id, "ready", None, record.get("prepared_unix")
            # Ready in the other camera-image mode (or from before modes): only its
            # image paths and previews change, the scene is not ingested again.
            restage = True
        if not restage and _claim_still_live(record, job_id, now):
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
    if restage:
        started = time.perf_counter()
        try:
            stage_images(cache / scene_id, raw, images)
            state, reason, prepared = "ready", None, int(time.time())
        except Exception as exc:  # noqa: BLE001 - reported per scene
            state, reason, prepared = "failed", f"{type(exc).__name__}: {exc}", None
        _log_timing(
            cache,
            {"scene_id": scene_id, "job_id": job_id, "images": images, "restage": True,
             "previews_s": round(time.perf_counter() - started, 2), "state": state},
        )  # fmt: skip
        return _finish_scene(cache, scene_id, job_id, state, reason, prepared, images)
    partial_root = cache / ".partial"
    partial_root.mkdir(parents=True, exist_ok=True)
    temporary = partial_root / scene_id
    shutil.rmtree(temporary, ignore_errors=True)
    state = "failed"
    reason: str | None = None
    prepared: int | None = None
    started, cpu_started = time.perf_counter(), time.process_time()
    previews_s = 0.0
    with collecting() as stats:
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
            if (
                not (produced / "manifest.json").is_file()
                or not (produced / "COMPLETE.json").is_file()
            ):
                raise DatasetError("ingest finished without a complete scene")
            staged = time.perf_counter()
            stage_images(produced, raw, images)
            previews_s = time.perf_counter() - staged
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
    _log_timing(
        cache,
        {
            "scene_id": scene_id,
            "job_id": job_id,
            "images": images,
            "state": state,
            "io_threads": stats.threads,
            "files_read": stats.files,
            "mb_read": round(stats.bytes / 1024**2, 1),
            "read_wait_s": round(stats.wait_s, 2),
            "build_s": round(stats.build_s, 2),
            "write_s": round(stats.write_s, 2),
            "previews_s": round(previews_s, 2),
            "total_s": round(time.perf_counter() - started, 2),
            "cpu_s": round(time.process_time() - cpu_started, 2),
            "yaml_loader": yaml_loader_kind(),
        },
    )
    return _finish_scene(cache, scene_id, job_id, state, reason, prepared, images)


TIMING_LOG = ".timing.jsonl"


def _log_timing(cache: Path, record: dict[str, Any]) -> None:
    """One line per prepared scene: in the job log, and in the cache for the next preview.

    ``read_wait_s`` is how long parsing waited for raw files still being read;
    ``build_s`` includes it. The app estimates the next prepare from these lines.
    """
    line = json.dumps({"unix": int(time.time()), "host": socket.gethostname(), **record})
    print(f"coperceive-timing {line}", flush=True)
    try:
        with open(cache / TIMING_LOG, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError:
        pass


def _finish_scene(
    cache: Path,
    scene_id: str,
    job_id: str | None,
    state: str,
    reason: str | None,
    prepared: int | None,
    images: str,
) -> tuple[str, str, str | None, int | None]:
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
        if state == "ready":
            record["images"] = images
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
    split: str | None = None,
    images: str = "originals",
) -> dict[str, Any]:
    """Prepare selected scenes in parallel, with per-scene atomic staging.

    ``images`` is the camera-image mode, ``originals`` or ``previews``
    (docs/datasets/cluster-datasets.md, "Camera images").
    """
    if adapter_name not in _BUILTIN:
        raise DatasetError(f"{adapter_name!r} is not a built-in cluster adapter")
    if images not in IMAGE_MODES:
        raise DatasetError(f"camera images must be one of {', '.join(IMAGE_MODES)}, not {images!r}")
    raw = _real(raw_root)
    cache = _real(cache_dir)
    if not raw.is_dir():
        raise DatasetError(f"raw root is not a directory: {raw}")
    raw, split = _raw_and_split(adapter_name, raw, split)
    if _inside(cache, raw):
        raise DatasetError("the cache directory must not be inside the raw dataset root")
    params = _params_for(adapter_name, raw, split)
    adapter = cast(ManifestAdapter, resolve_adapter(adapter_name))
    unlisted: list[str] = []
    refs = list(adapter.scenes(raw, params, errors=unlisted))
    wanted = set(scenes) if scenes else None
    selected = [ref for ref in refs if wanted is None or ref.scene_id in wanted]
    if wanted is not None:
        missing = wanted - {ref.scene_id for ref in refs}
        reasons = [line for line in unlisted if line.split(":", 1)[0] in missing]
        if reasons:
            raise DatasetError(f"cannot read scene {reasons[0]}")
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
    job_id = _slurm_job_id()
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
                images,
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
        scene_names: list[str] = []
    else:
        accepted = (*_SPLITS["opv2v"], *_SPLITS["v2xset"], *_SPLITS["v2v4real"])
        if candidate.name in accepted and candidate.is_dir():
            raw_root = candidate.parent
            split = candidate.name
        else:
            present = [name for name in accepted if (candidate / name).is_dir()]
            if not present:
                return result
            raw_root = candidate
            split = present[0]
        try:
            scene_names = _scene_dirs(raw_root / split, max(1, max_scenes))
        except OSError as exc:
            result["reason"] = f"cannot list {raw_root / split}: {exc.strerror or exc}"
            return result
        if _is_v2xset(raw_root, scene_names, split):
            adapter_name = "v2xset"
        elif is_v2v4real(raw_root, scene_names, split):
            adapter_name = "v2v4real"
        else:
            adapter_name = "opv2v"
        if split not in _SPLITS[adapter_name]:
            adapter_name = "opv2v"
    params = _params_for(adapter_name, raw_root, split)
    try:
        adapter = resolve_adapter(adapter_name)
        if split is None:
            scene_count = len(adapter.scenes(raw_root, params))
        else:
            # OPV2V/V2XSet: the split listing is the scene list. Listing every
            # agent's frames here took ~30 s on a cluster filesystem; index does it.
            scene_count = len(scene_names)
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
            "scene_count": min(scene_count, max_scenes),
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
    split: str | None = None,
) -> dict[str, Any]:
    """Every scene of one split of a raw tree, metadata only. Writes nothing."""
    started = time.monotonic()
    deadline = started + max(1.0, timeout_s)
    if adapter_name not in _BUILTIN:
        raise DatasetError(f"{adapter_name!r} is not a built-in cluster adapter")
    raw = _real(raw_root)
    if not raw.is_dir():
        raise DatasetError(f"raw root is not a directory: {raw}")
    raw, split = _raw_and_split(adapter_name, raw, split)
    params = _params_for(adapter_name, raw, split)
    adapter = cast(ManifestAdapter, resolve_adapter(adapter_name))
    errors: list[str] = []
    refs = list(adapter.scenes(raw, params, errors=errors))
    if len(refs) > max_scenes:
        refs = refs[:max_scenes]
    scale_us = adapter.spec.clock.scale_us or 0
    fingerprint_root = raw / split if split else raw
    sizes: dict[str, int] = {}
    fingerprint = _fingerprint(
        fingerprint_root,
        max_entries=max_entries,
        deadline=min(deadline, time.monotonic() + max(1.0, timeout_s)),
        sizes=sizes,
    )
    scenes: list[dict[str, Any]] = []
    estimated_cache_bytes = 0
    for ref in refs:
        try:
            counts = adapter.summarise(raw, ref)
        except (CoperceiveError, ValueError, OSError) as exc:
            # One unreadable scene is an entry in errors, not a failed index.
            errors.append(_scene_error(ref.scene_id, exc))
            continue
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
        if split and not fingerprint["truncated"]:
            estimated_cache_bytes += sizes.get(ref.scene_id, 0)
        else:
            scene_dir = raw / split / ref.scene_id if split else raw / ref.scene_id
            # Bounded by the same deadline: a truncated walk must not turn into
            # an unbounded one per scene (index then ran far past --timeout).
            estimated_cache_bytes += _directory_bytes(
                scene_dir, max_entries=50_000, deadline=deadline
            )
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
