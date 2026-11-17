# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownParameterType=false, reportMissingParameterType=false, reportArgumentType=false, reportPrivateUsage=false, reportIndexIssue=false, reportAttributeAccessIssue=false, reportOptionalMemberAccess=false, reportGeneralTypeIssues=false, reportUnusedFunction=false
"""Open a ``.cpbundle`` read-only and safely extract it under ``bundles/<sha>/``.

Opening never adds anything to the recipient's library and writes nothing
outside ``<data_root>/bundles/<bundle-sha>/``. When the bundle omits sensor data
because of the dataset licence, the recipient's own copy of the same scene is
used if one is found (same scene id, adapter and frame count); otherwise the
viewer shows the boxes on an empty grid with the reason.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
import zipfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from coperceive.bundle.format import (
    MANIFEST,
    MAX_FILE_BYTES,
    MAX_FILES,
    MAX_TOTAL_BYTES,
    BundleError,
    _is_sensor_path,
    check_zip_entry,
    read_manifest,
    safe_member,
    validate_manifest,
)
from coperceive.bundle.sensor import SENSOR_MAX_BYTES, dataset_key
from coperceive.errors import CoperceiveError
from coperceive.model import Scene
from coperceive.repro.export import check_card
from coperceive.repro.schema import validate_card as validate_repro_schema
from coperceive.store import read_scene

OPEN_REPORT = "open.json"
OPEN_SCHEMA = "coperceive-bundle-open"
OPEN_SCHEMA_VERSION = 1
SCENES = "scenes"
PREDICTIONS = "predictions"
OPEN_REPORT_KEYS = frozenset(
    {
        "schema",
        "schema_version",
        "bundle_sha256",
        "run_id",
        "read_only",
        "sensor_data",
        "dataset",
        "licence",
        "rerun_command",
        "scenes",
        "paths",
        "library_roots",
    }
)


@dataclass(frozen=True)
class OpenLimits:
    max_files: int = MAX_FILES
    max_file_bytes: int = MAX_FILE_BYTES
    max_total_bytes: int = MAX_TOTAL_BYTES
    #: LiDAR + camera bytes the bundle may carry for the included frames.
    max_sensor_bytes: int = SENSOR_MAX_BYTES


@dataclass(frozen=True)
class OpenedBundle:
    root: Path
    manifest: dict[str, object]
    digest: str
    #: The per-open decision (sensor data included, from a local copy, or omitted).
    report: dict[str, object]

    @property
    def scenes_root(self) -> Path:
        return self.root / SCENES

    @property
    def predictions_root(self) -> Path:
        return self.root / PREDICTIONS

    @property
    def report_path(self) -> Path:
        return self.root / OPEN_REPORT


@dataclass(frozen=True)
class _LocalScene:
    path: Path
    adapter: str


def _bundle_sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_report(target: Path) -> dict[str, object] | None:
    try:
        payload: object = json.loads((target / OPEN_REPORT).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("schema") != OPEN_SCHEMA:
        return None
    return dict(payload)


def open_bundle(
    path: Path,
    *,
    data_root: Path,
    limits: OpenLimits | None = None,
    library_roots: Sequence[Path] = (),
) -> OpenedBundle:
    """Validate, verify and extract a bundle. Never executes anything from it."""
    limits = limits or OpenLimits()
    from coperceive.app.bundle_jobs import sweep_stale

    # A served bundle's private jobs folder outlives a crashed server; tidy up here.
    sweep_stale()
    try:
        digest = _bundle_sha(path)
        target = data_root / "bundles" / digest
        if target.is_dir():
            # Reopening re-checks everything: the archive passes every check again
            # (with today's limits), and the extracted copy must still be exactly it.
            with tempfile.TemporaryDirectory(prefix="coperceive-reopen-", dir=target.parent) as raw:
                fresh = _extract(path, Path(raw) / "fresh", digest, limits)
                _require_same_tree(fresh.root, target)
            manifest = fresh.manifest
            report = _resolve_sensor_data(target, manifest, digest, library_roots, limits)
            _write_report(target, report)
            return OpenedBundle(target, manifest, digest, report)
        target.parent.mkdir(parents=True, exist_ok=True)
        opened = _extract(path, target, digest, limits)
        report = _resolve_sensor_data(opened.root, opened.manifest, digest, library_roots, limits)
        _write_report(opened.root, report)
        return OpenedBundle(opened.root, opened.manifest, digest, report)
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        if isinstance(exc, BundleError):
            raise
        raise BundleError(f"cannot open bundle: {exc}") from None


def _extract(path: Path, target: Path, digest: str, limits: OpenLimits) -> OpenedBundle:
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="coperceive-open-", dir=target.parent) as raw:
        work = Path(raw)
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            if len(infos) > limits.max_files:
                raise BundleError("bundle has too many files")
            seen: set[str] = set()
            total = 0
            for info in infos:
                check_zip_entry(info)
                if info.filename in seen:
                    raise BundleError(f"duplicate zip entry {info.filename!r}")
                seen.add(info.filename)
                if info.file_size > limits.max_file_bytes:
                    raise BundleError(f"zip entry {info.filename!r} is too large")
                total += info.file_size
                if total > limits.max_total_bytes:
                    raise BundleError("bundle is larger than the uncompressed size cap")
            if MANIFEST not in seen:
                raise BundleError("bundle.json is missing")
            archive.extract(MANIFEST, work)
        manifest = read_manifest(work / MANIFEST)
        contents = validate_manifest(manifest)
        sensor_files = [item for item in contents if _is_sensor_path(item.path)]
        sensor_data = manifest["sensor_data"]
        status = sensor_data.get("status") if isinstance(sensor_data, dict) else None
        if status != "included" and sensor_files:
            raise BundleError(
                f"sensor data is omitted, but the bundle holds sensor file {sensor_files[0].path!r}"
            )
        sensor_bytes = sum(item.size for item in sensor_files)
        if sensor_bytes > limits.max_sensor_bytes:
            raise BundleError(
                f"this bundle's sensor data is {sensor_bytes} bytes, over the "
                f"{limits.max_sensor_bytes}-byte sensor-data cap"
            )
        if status == "included" and sensor_bytes != sensor_data.get("bytes"):  # type: ignore[union-attr]
            raise BundleError("sensor_data.bytes does not match the sensor files the bundle holds")
        expected = {item.path: item for item in contents}
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            for info in infos:
                if info.filename == MANIFEST:
                    continue
                item = expected.get(info.filename)
                if item is None:
                    raise BundleError(f"zip contains an unlisted file {info.filename!r}")
                target_path = work / safe_member(info.filename)
                target_path.parent.mkdir(parents=True, exist_ok=True)
                digest_hash = hashlib.sha256()
                written = 0
                try:
                    with archive.open(info) as source, target_path.open("wb") as sink:
                        for chunk in iter(lambda: source.read(1024 * 1024), b""):
                            written += len(chunk)
                            if written > item.size or written > limits.max_file_bytes:
                                raise BundleError(
                                    f"zip entry {info.filename!r} is larger than declared"
                                )
                            digest_hash.update(chunk)
                            sink.write(chunk)
                except (zipfile.BadZipFile, RuntimeError, OSError) as exc:
                    raise BundleError(f"cannot read zip entry {info.filename!r}: {exc}") from None
                if written != item.size or digest_hash.hexdigest() != item.sha256:
                    raise BundleError(f"hash or size mismatch for {info.filename!r}")
            if set(seen) != {MANIFEST} | set(expected):
                raise BundleError("bundle.json does not list every zip entry exactly once")
        _validate_json(work)
        work.rename(target)
    return OpenedBundle(target, manifest, digest, {})


def _tree_files(root: Path, *, skip: frozenset[str] = frozenset()) -> dict[str, str]:
    """Every file under ``root`` with its SHA-256; a link or special file is a change."""
    files: dict[str, str] = {}
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        for name in dirnames + filenames:
            entry = Path(dirpath) / name
            relative = entry.relative_to(root).as_posix()
            mode = os.lstat(entry).st_mode
            if stat.S_ISDIR(mode):
                continue
            if not stat.S_ISREG(mode):
                raise BundleError(f"{relative} in the opened copy is not a regular file")
            if relative not in skip:
                files[relative] = hashlib.sha256(entry.read_bytes()).hexdigest()
    return files


def _require_same_tree(fresh: Path, opened: Path) -> None:
    """The earlier extraction at ``opened`` still matches a fresh, checked one."""
    try:
        same = _tree_files(opened, skip=frozenset({OPEN_REPORT})) == _tree_files(fresh)
    except BundleError:
        same = False
    if not same:
        raise BundleError(
            f"the opened copy at {opened} has changed since it was opened; "
            "remove that folder and open the bundle again"
        )


def _validate_json(root: Path) -> None:
    for path in root.rglob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise BundleError(f"{path.relative_to(root)} is not valid JSON: {exc}") from None
        if path.name == "card.json":
            schema_problems = validate_repro_schema(payload)
            if schema_problems:
                raise BundleError("repro card schema: " + "; ".join(schema_problems))
            problems = check_card(payload)
            if problems:
                raise BundleError("repro card failed its check: " + "; ".join(problems))


# --- local copy lookup -------------------------------------------------------
#
# The recipient's library root is searched at the paths a coperceive library
# uses: ``<root>/<scene>``, ``<root>/scenes/<scene>``, ``<root>/<dataset>/<scene>``
# and ``<root>/<dataset>/scenes/<scene>``. A candidate matches only when
# ``manifest.json`` records the same scene id, ``COMPLETE.json`` records the same
# adapter and the number of pose frames equals the bundle's recorded frame count.


def _scene_adapter(directory: Path) -> str | None:
    try:
        payload: object = json.loads((directory / "COMPLETE.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    adapter = payload.get("adapter")
    return adapter if isinstance(adapter, str) and adapter else None


def _candidate_directories(
    library_roots: Sequence[Path], dataset_id: str | None, scene_id: str
) -> list[Path]:
    candidates: list[Path] = []
    key = dataset_key(dataset_id) if dataset_id else ""
    for raw_root in library_roots:
        try:
            root = Path(raw_root).expanduser()
            if not root.is_dir():
                continue
        except OSError:
            continue
        candidates.extend([root / scene_id, root / SCENES / scene_id])
        names: list[str] = []
        if dataset_id:
            names.append(dataset_id)
        if key:
            try:
                names.extend(
                    child.name
                    for child in root.iterdir()
                    if child.is_dir() and dataset_key(child.name) == key
                )
            except OSError:
                pass
        for name in names:
            candidates.extend(
                [
                    root / name / scene_id,
                    root / name / SCENES / scene_id,
                ]
            )
    return candidates


def _frame_count(scene: Scene) -> int:
    return len({pose.time_us for agent in scene.agents for pose in agent.poses})


def find_local_scene(
    library_roots: Sequence[Path],
    *,
    dataset_id: str | None,
    scene_id: str,
    frame_count: int,
    adapter: str | None,
    bundle_root: Path,
) -> _LocalScene | None:
    """A matching scene in the recipient's own library, or ``None``."""
    if not library_roots or not adapter:
        return None
    for candidate in _candidate_directories(library_roots, dataset_id, scene_id):
        try:
            resolved = candidate.resolve()
            roots = []
            for library in library_roots:
                try:
                    roots.append(Path(library).resolve())
                except OSError:
                    continue
            if not any(resolved == root or root in resolved.parents for root in roots):
                continue
            if resolved == bundle_root.resolve() or bundle_root.resolve() in resolved.parents:
                continue
            if not (resolved / "manifest.json").is_file():
                continue
        except OSError:
            continue
        if _scene_adapter(resolved) != adapter:
            continue
        try:
            manifest = json.loads((resolved / "manifest.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(manifest, dict) or manifest.get("scene_id") != scene_id:
            continue
        try:
            scene = read_scene(resolved, points=False)
        except (OSError, CoperceiveError, ValueError):
            continue
        if _frame_count(scene) != frame_count:
            continue
        return _LocalScene(resolved, adapter)
    return None


def _image_bytes(scene_dir: Path, images: Sequence[object]) -> int:
    total = 0
    for image in images:
        path_value = getattr(image, "path", None)
        if not isinstance(path_value, str):
            continue
        try:
            relative = safe_member(path_value)
        except ValueError:
            continue
        try:
            source = scene_dir / relative
            if source.is_file():
                total += source.stat().st_size
        except OSError:
            continue
    return total


def _sensor_content_bytes(manifest: dict[str, object]) -> int:
    contents = manifest.get("contents")
    if not isinstance(contents, list):
        return 0
    total = 0
    for item in contents:
        if isinstance(item, dict) and item.get("kind") in {"sensor-points", "sensor-image"}:
            size = item.get("size")
            if isinstance(size, int):
                total += size
    return total


def _resolve_sensor_data(
    root: Path,
    manifest: dict[str, object],
    digest: str,
    library_roots: Sequence[Path],
    limits: OpenLimits,
) -> dict[str, object]:
    sensor = manifest.get("sensor_data")
    if not isinstance(sensor, dict):
        raise BundleError("bundle.json is missing sensor_data")
    status = str(sensor.get("status"))
    scenes = sensor.get("scenes")
    if not isinstance(scenes, list):
        raise BundleError("bundle.json sensor_data.scenes must be a list")
    dataset_id = sensor.get("dataset_id")
    dataset_id = dataset_id if isinstance(dataset_id, str) and dataset_id else None
    dataset_label = sensor.get("dataset_label")
    dataset_label = (
        dataset_label if isinstance(dataset_label, str) and dataset_label else dataset_id
    )
    display = dataset_label or "the dataset"
    adapter = sensor.get("adapter")
    adapter = adapter if isinstance(adapter, str) and adapter else None
    licence = sensor.get("licence")
    licence = licence if isinstance(licence, str) else None
    bundle_bytes = _sensor_content_bytes(manifest)
    report_scenes: list[dict[str, object]] = []
    any_local = False
    any_included = status == "included"
    if status == "included":
        declared = sensor.get("bytes")
        if bundle_bytes != declared:
            raise BundleError("bundle.json sensor_data.bytes does not match the listed files")
        if bundle_bytes > limits.max_sensor_bytes:
            raise BundleError(
                f"this bundle's sensor data is {bundle_bytes} bytes, over the "
                f"{limits.max_sensor_bytes}-byte sensor-data cap"
            )
    for row in scenes:
        if not isinstance(row, dict):
            raise BundleError("bundle.json sensor_data.scenes rows must be objects")
        scene_id = str(row.get("scene_id"))
        frame_count = row.get("frame_count")
        frame_count = frame_count if isinstance(frame_count, int) else 0
        frames = row.get("frames_included")
        frames = (
            [value for value in frames if isinstance(value, int)]
            if isinstance(frames, list)
            else []
        )
        entry: dict[str, object] = {
            "scene_id": scene_id,
            "frame_count": frame_count,
            "frames": frames,
            "sensor_data": "included" if status == "included" else "omitted",
            "label": None,
            "note": None,
            "local_copy": None,
            "bytes": 0,
        }
        if status == "omitted":
            local = find_local_scene(
                library_roots,
                dataset_id=dataset_id,
                scene_id=scene_id,
                frame_count=frame_count,
                adapter=adapter,
                bundle_root=root,
            )
            if local is not None:
                any_local = True
                entry["sensor_data"] = "local-copy"
                entry["bytes"] = 0
                entry["local_copy"] = str(local.path)
                entry["label"] = f"Frames from your local copy of {display}"
            if entry["local_copy"] is None:
                reason = str(
                    (manifest.get("sensor_data") or {}).get("reason")
                    or "no sensor data in this bundle"
                )
                entry["note"] = (
                    f"Frames not included — {reason}. Open your own copy of {display} to see them."
                )
        report_scenes.append(entry)
    overall = "included" if any_included else ("local-copy" if any_local else "omitted")
    return {
        "schema": OPEN_SCHEMA,
        "schema_version": OPEN_SCHEMA_VERSION,
        "bundle_sha256": digest,
        "run_id": manifest.get("run_id"),
        "title": manifest.get("title"),
        "read_only": True,
        "sensor_data": overall,
        "dataset": {
            "id": dataset_id,
            "label": dataset_label,
            "licence": licence,
        },
        "licence": licence,
        "rerun_command": manifest.get("rerun_command"),
        "scenes": report_scenes,
        "paths": {"scenes": SCENES, "predictions": PREDICTIONS},
        # The server serves a local copy only while it still resolves inside one of these.
        "library_roots": _resolved_roots(library_roots),
    }


def _resolved_roots(library_roots: Sequence[Path]) -> list[str]:
    roots: list[str] = []
    for raw in library_roots:
        try:
            resolved = Path(raw).expanduser().resolve(strict=True)
        except OSError:
            continue
        if resolved.is_dir() and str(resolved) not in roots:
            roots.append(str(resolved))
    return roots


def _write_report(root: Path, report: dict[str, object]) -> None:
    (root / OPEN_REPORT).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
