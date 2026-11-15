# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownParameterType=false, reportMissingParameterType=false, reportArgumentType=false, reportPrivateUsage=false, reportIndexIssue=false, reportAttributeAccessIssue=false, reportOptionalMemberAccess=false, reportGeneralTypeIssues=false, reportUnusedFunction=false
"""Result bundle format (``.cpbundle``): a zip with a versioned manifest."""

from __future__ import annotations

import hashlib
import json
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

SCHEMA = "coperceive-result-bundle"
VERSION = 1
MANIFEST = "bundle.json"
MAX_FILES = 20_000
MAX_FILE_BYTES = 512 * 1024 * 1024
MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024


class BundleError(ValueError):
    """A bundle is malformed, unsafe, or was made by a newer Coperceive."""


@dataclass(frozen=True)
class Content:
    path: str
    sha256: str
    size: int
    kind: str


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


_WINDOWS_RESERVED = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }
)


def safe_member(name: str) -> str:
    """A zip member path that cannot escape the extraction root on any OS."""
    if not name or name.startswith("/") or "\\" in name or "\x00" in name or ":" in name:
        raise BundleError(f"unsafe bundle path {name!r}")
    parts = name.split("/")
    for part in parts:
        if part in {"", ".", ".."}:
            raise BundleError(f"unsafe bundle path {name!r}")
        if part.endswith((".", " ")):
            raise BundleError(f"unsafe bundle path {name!r}")
        stem = part.split(".", 1)[0].upper()
        if stem in _WINDOWS_RESERVED:
            raise BundleError(f"unsafe bundle path {name!r}")
    return name


def manifest_document(
    *,
    title: str,
    app_version: str,
    created_utc: str,
    run_id: str,
    contents: list[Content],
    licences: list[dict[str, str]],
    privacy: Mapping[str, object],
    sensor_data: Mapping[str, object],
    rerun_command: str,
) -> dict[str, object]:
    return {
        "schema": SCHEMA,
        "schema_version": VERSION,
        "created_utc": created_utc,
        "app_version": app_version,
        "title": title,
        "run_id": run_id,
        "contents": [
            {"path": item.path, "sha256": item.sha256, "size": item.size, "kind": item.kind}
            for item in contents
        ],
        "licences": licences,
        "privacy": dict(privacy),
        "rerun_command": rerun_command,
        "sensor_data": dict(sensor_data),
    }


#: Keys of the ``sensor_data`` object. ``status`` is ``included`` or ``omitted``;
#: an omitted bundle still records the dataset id, its recorded licence (when the
#: dataset is on the allow list) and why the bytes are not there.
SENSOR_DATA_KEYS = frozenset(
    {
        "status",
        "reason",
        "licence",
        "licence_url",
        "dataset_id",
        "dataset_label",
        "adapter",
        "bytes",
        "max_bytes",
        "scenes",
    }
)


def _single_part(value: object, what: str) -> str:
    """A scene id that is one safe path part (no separators, not a dot name)."""
    import re

    if (
        not isinstance(value, str)
        or not value
        or value in {".", ".."}
        or re.fullmatch(r"[A-Za-z0-9._-]+", value) is None
    ):
        raise BundleError(f"sensor_data {what} is not a safe single path part")
    return value


def validate_sensor_data(payload: object) -> dict[str, object]:
    """The recorded sensor-data decision, refused unless it is exactly shaped."""
    if not isinstance(payload, dict):
        raise BundleError("bundle.json is missing sensor_data")
    unknown = set(payload) - SENSOR_DATA_KEYS
    if unknown:
        raise BundleError(f"sensor_data has unknown key {sorted(unknown)[0]!r}")
    status = payload.get("status")
    if status not in {"included", "omitted"}:
        raise BundleError("sensor_data.status must be 'included' or 'omitted'")
    for key in ("reason", "licence", "licence_url", "dataset_id", "dataset_label", "adapter"):
        value = payload.get(key)
        if value is not None and not isinstance(value, str):
            raise BundleError(f"sensor_data.{key} must be a string or null")
    for key in ("bytes", "max_bytes"):
        value = payload.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise BundleError(f"sensor_data.{key} must be a non-negative integer")
    scenes = payload.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        raise BundleError("sensor_data.scenes must be a non-empty list")
    for index, row in enumerate(scenes):
        if not isinstance(row, dict):
            raise BundleError(f"sensor_data.scenes[{index}] must be an object")
        unknown_scene = set(row) - {"scene_id", "frame_count", "frames_included"}
        if unknown_scene:
            raise BundleError(
                f"sensor_data.scenes[{index}] has unknown key {sorted(unknown_scene)[0]!r}"
            )
        _single_part(row.get("scene_id"), f"scenes[{index}].scene_id")
        frame_count = row.get("frame_count")
        if not isinstance(frame_count, int) or isinstance(frame_count, bool) or frame_count < 0:
            raise BundleError(f"sensor_data.scenes[{index}].frame_count is not an integer")
        frames = row.get("frames_included")
        if not isinstance(frames, list) or not frames:
            raise BundleError(f"sensor_data.scenes[{index}].frames_included is empty")
        for time_us in frames:
            if not isinstance(time_us, int) or isinstance(time_us, bool):
                raise BundleError(f"sensor_data.scenes[{index}].frames_included must hold integers")
    if status == "omitted":
        if payload["bytes"] != 0:
            raise BundleError("sensor_data.bytes must be 0 when sensor data is omitted")
    else:
        licence = payload.get("licence")
        if not isinstance(licence, str) or not licence.strip():
            raise BundleError("sensor_data.licence is required when sensor data is included")
        if payload["bytes"] <= 0:
            raise BundleError("sensor_data.bytes must be positive when sensor data is included")
    return dict(payload)


def read_manifest(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BundleError(f"bundle.json cannot be read: {exc}") from None
    if not isinstance(payload, dict):
        raise BundleError("bundle.json must be an object")
    if payload.get("schema") != SCHEMA:
        raise BundleError("this is not a Coperceive result bundle")
    version = payload.get("schema_version")
    if not isinstance(version, int) or version < 1:
        raise BundleError("bundle.json has no valid schema_version")
    if version > VERSION:
        raise BundleError(
            f"this bundle was made with a newer Coperceive (schema {version}); "
            "update Coperceive to open it"
        )
    import re

    run_id = payload.get("run_id")
    if (
        not isinstance(run_id, str)
        or re.fullmatch(r"[A-Za-z0-9._-]+", run_id) is None
        or run_id in {".", ".."}
    ):
        raise BundleError("bundle.json run_id is not a safe single path part")
    return payload


def validate_manifest(payload: Mapping[str, object]) -> tuple[Content, ...]:
    import re

    run_id = payload.get("run_id")
    if (
        not isinstance(run_id, str)
        or re.fullmatch(r"[A-Za-z0-9._-]+", run_id) is None
        or run_id in {".", ".."}
    ):
        raise BundleError("bundle.json run_id is not a safe single path part")
    allowed = {
        "schema",
        "schema_version",
        "created_utc",
        "app_version",
        "title",
        "run_id",
        "contents",
        "licences",
        "privacy",
        "rerun_command",
        "sensor_data",
    }
    unknown = set(payload) - allowed
    if unknown:
        raise BundleError(f"bundle.json has unknown key {sorted(unknown)[0]!r}")
    for key in (
        "schema",
        "schema_version",
        "created_utc",
        "app_version",
        "title",
        "run_id",
        "contents",
        "rerun_command",
        "sensor_data",
    ):
        if key not in payload:
            raise BundleError(f"bundle.json is missing {key}")
    if not isinstance(payload.get("title"), str) or not isinstance(payload.get("created_utc"), str):
        raise BundleError("bundle.json title/created_utc must be strings")
    if not isinstance(payload.get("app_version"), str) or not isinstance(
        payload.get("schema_version"), int
    ):
        raise BundleError("bundle.json app_version/schema_version have wrong types")
    rerun = payload.get("rerun_command")
    if not isinstance(rerun, str) or not rerun.strip() or "\x00" in rerun:
        raise BundleError("bundle.json rerun_command must be non-empty text")
    sensor_data = validate_sensor_data(payload.get("sensor_data"))
    if not isinstance(payload.get("licences", []), list) or not isinstance(
        payload.get("privacy", {}), dict
    ):
        raise BundleError("bundle.json licences/privacy have wrong types")
    contents = payload.get("contents")
    if not isinstance(contents, list):
        raise BundleError("bundle.json contents must be a list")
    out: list[Content] = []
    seen: set[str] = set()
    for index, item in enumerate(contents):
        if not isinstance(item, dict):
            raise BundleError(f"contents[{index}] must be an object")
        path = item.get("path")
        digest = item.get("sha256")
        size = item.get("size")
        kind = item.get("kind", "file")
        if set(item) - {"path", "sha256", "size", "kind"}:
            raise BundleError(f"contents[{index}] has an unknown key")
        if not isinstance(path, str) or not isinstance(digest, str) or not isinstance(size, int):
            raise BundleError(f"contents[{index}] is missing path/sha256/size")
        safe_member(path)
        if path == MANIFEST or path in seen:
            raise BundleError(f"duplicate content path {path!r}")
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise BundleError(f"contents[{index}].sha256 is not a lowercase SHA-256")
        if size < 0 or size > MAX_FILE_BYTES:
            raise BundleError(f"contents[{index}] is too large")
        lowered = path.lower()
        if any(existing.lower() == lowered for existing in seen):
            raise BundleError(f"duplicate content path (case-insensitive) {path!r}")
        for existing in seen:
            if lowered.startswith(existing.lower() + "/") or existing.lower().startswith(
                lowered + "/"
            ):
                raise BundleError(f"content path clashes with {existing!r}")
        seen.add(path)
        out.append(Content(path, digest, size, str(kind)))
    if len(out) > MAX_FILES:
        raise BundleError("bundle has too many files")
    if sum(item.size for item in out) > MAX_TOTAL_BYTES:
        raise BundleError("bundle is larger than the uncompressed size cap")
    sensor_bytes = sum(item.size for item in out if item.kind in {"sensor-points", "sensor-image"})
    if sensor_data["status"] == "included":
        if sensor_bytes != sensor_data["bytes"]:
            raise BundleError(
                "sensor_data.bytes does not match the listed sensor files "
                f"({sensor_data['bytes']} != {sensor_bytes})"
            )
    elif sensor_bytes:
        raise BundleError("sensor_data is omitted but the bundle lists sensor files")
    listed = {item.path for item in out}
    for row in sensor_data["scenes"]:  # type: ignore[index]
        scene_id = row["scene_id"]  # type: ignore[index]
        if f"scenes/{scene_id}/manifest.json" not in listed:
            raise BundleError(f"sensor_data names a scene that is not in the bundle: {scene_id!r}")
    return tuple(out)


def check_zip_entry(info: zipfile.ZipInfo) -> None:
    safe_member(info.filename)
    if info.flag_bits & 0x1:
        raise BundleError("encrypted zip entries are not supported")
    mode = (info.external_attr >> 16) & 0o170000
    if mode == 0o120000:
        raise BundleError(f"symlink entry {info.filename!r} is not allowed")
    if info.file_size < 0 or info.file_size > MAX_FILE_BYTES:
        raise BundleError(f"zip entry {info.filename!r} is too large")
