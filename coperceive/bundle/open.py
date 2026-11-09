# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownParameterType=false, reportMissingParameterType=false, reportArgumentType=false, reportPrivateUsage=false, reportIndexIssue=false, reportAttributeAccessIssue=false, reportOptionalMemberAccess=false, reportGeneralTypeIssues=false, reportUnusedFunction=false
"""Open a ``.cpbundle`` read-only and safely extract it under ``bundles/<sha>/``."""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

from coperceive.bundle.format import (
    MANIFEST,
    MAX_FILE_BYTES,
    MAX_FILES,
    MAX_TOTAL_BYTES,
    BundleError,
    check_zip_entry,
    read_manifest,
    safe_member,
    validate_manifest,
)
from coperceive.repro.export import check_card
from coperceive.repro.schema import validate_card as validate_repro_schema


@dataclass(frozen=True)
class OpenLimits:
    max_files: int = MAX_FILES
    max_file_bytes: int = MAX_FILE_BYTES
    max_total_bytes: int = MAX_TOTAL_BYTES


@dataclass(frozen=True)
class OpenedBundle:
    root: Path
    manifest: dict[str, object]
    digest: str


def _bundle_sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def open_bundle(
    path: Path,
    *,
    data_root: Path,
    limits: OpenLimits | None = None,
) -> OpenedBundle:
    """Validate, verify and extract a bundle. Never executes anything from it."""
    limits = limits or OpenLimits()
    try:
        digest = _bundle_sha(path)
        target = data_root / "bundles" / digest
        if target.is_dir():
            manifest = read_manifest(target / MANIFEST)
            return OpenedBundle(target, manifest, digest)
        target.parent.mkdir(parents=True, exist_ok=True)
        return _extract(path, target, digest, limits)
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
        # The viewer reads scores from <predictions>/<run>/<scene>/metrics.json;
        # mirror the bundled eval reports there without changing the archive.
        for metrics in (work / "eval").glob("*/metrics.json"):
            run_id = str(manifest.get("run_id", ""))
            if not run_id:
                break
            target_metrics = work / "predictions" / run_id / metrics.parent.name / "metrics.json"
            target_metrics.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(metrics, target_metrics)
        work.rename(target)
    return OpenedBundle(target, manifest, digest)


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
