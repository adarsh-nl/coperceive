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


def safe_member(name: str) -> str:
    """A zip member path that cannot escape the extraction root."""
    if not name or name.startswith("/") or "\\" in name or "\x00" in name:
        raise BundleError(f"unsafe bundle path {name!r}")
    parts = name.split("/")
    if any(part in {"", ".", ".."} for part in parts):
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
    }


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
    return payload


def validate_manifest(payload: Mapping[str, object]) -> tuple[Content, ...]:
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
        if not isinstance(path, str) or not isinstance(digest, str) or not isinstance(size, int):
            raise BundleError(f"contents[{index}] is missing path/sha256/size")
        safe_member(path)
        if path == MANIFEST or path in seen:
            raise BundleError(f"duplicate content path {path!r}")
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise BundleError(f"contents[{index}].sha256 is not a lowercase SHA-256")
        if size < 0 or size > MAX_FILE_BYTES:
            raise BundleError(f"contents[{index}] is too large")
        seen.add(path)
        out.append(Content(path, digest, size, str(kind)))
    if len(out) > MAX_FILES:
        raise BundleError("bundle has too many files")
    if sum(item.size for item in out) > MAX_TOTAL_BYTES:
        raise BundleError("bundle is larger than the uncompressed size cap")
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
