# SPDX-License-Identifier: Apache-2.0
"""``coperceive dataset stream``: a read-only file streamer for remote viewing.

It reads one JSON request per line on stdin and answers each with one JSON
header line on stdout, followed by exactly ``size`` payload bytes when the
header says so. Requests carry paths relative to the first root (the dataset's
cache split directory); a path is served only if its ``realpath`` is inside one
of the roots given on the command line, so a scene's ``raw`` link may lead into
the dataset's raw root and nothing may lead anywhere else. It never writes.

Requests::

    {"id": 1, "op": "read", "path": "scene/points.bin", "offset": 0, "length": 1048576}
    {"id": 2, "op": "stat", "path": "scene/COMPLETE.json"}
    {"id": 3, "op": "ready_scenes"}
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import BinaryIO, cast

MAX_READ = 16 * 1024 * 1024


class StreamError(ValueError):
    """A request the streamer refuses (outside the roots, malformed)."""


class Roots:
    """The allowed roots, real paths; the first is where relative paths start."""

    def __init__(self, roots: Sequence[str | Path]) -> None:
        if not roots:
            raise StreamError("at least one root is required")
        self.base = Path(os.path.realpath(roots[0]))
        self.allowed = tuple(Path(os.path.realpath(root)) for root in roots)
        if not self.base.is_dir():
            raise StreamError(f"not a directory: {roots[0]}")

    def resolve(self, relative: object) -> Path:
        if not isinstance(relative, str) or relative == "" or "\x00" in relative:
            raise StreamError("a relative path is required")
        parts = Path(relative).parts
        if Path(relative).is_absolute() or ".." in parts:
            raise StreamError(f"path escapes the dataset: {relative}")
        candidate = Path(os.path.realpath(self.base / relative))
        if not any(candidate == root or candidate.is_relative_to(root) for root in self.allowed):
            raise StreamError(f"path escapes the dataset: {relative}")
        return candidate


# A scene's version is the newest change to these or to its directory: a
# camera-image mode switch rewrites images.parquet (and adds or removes
# previews) without touching COMPLETE.json, and the viewer must see it.
_VERSIONED = (
    "COMPLETE.json",
    "manifest.json",
    "images.parquet",
    "point_index.parquet",
    "poses.parquet",
    "annotations.parquet",
    "objects.parquet",
)


def _scene_version(scene: Path) -> int:
    newest = scene.stat().st_mtime_ns
    for name in _VERSIONED:
        try:
            newest = max(newest, (scene / name).stat().st_mtime_ns)
        except OSError:
            continue
    return newest


def _ready_scenes(base: Path) -> list[dict[str, object]]:
    scenes: list[dict[str, object]] = []
    with os.scandir(base) as entries:
        for entry in entries:
            if entry.name.startswith(".") or not entry.is_dir(follow_symlinks=False):
                continue
            scene = Path(entry.path)
            if (scene / "manifest.json").is_file() and (scene / "COMPLETE.json").is_file():
                scenes.append({"scene_id": entry.name, "mtime_ns": _scene_version(scene)})
    return sorted(scenes, key=lambda scene: str(scene["scene_id"]))


def handle(roots: Roots, request: dict[str, object]) -> tuple[dict[str, object], bytes]:
    """One request -> (header, payload)."""
    op = request.get("op")
    if op == "ready_scenes":
        return {"ok": True, "scenes": _ready_scenes(roots.base)}, b""
    if op == "ping":
        return {"ok": True}, b""
    path = roots.resolve(request.get("path"))
    if op == "stat":
        try:
            stat = path.stat()
        except OSError:
            return {"ok": True, "exists": False}, b""
        return {
            "ok": True,
            "exists": True,
            "size": 0,
            "file_size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
            "is_file": path.is_file(),
        }, b""
    if op == "read":
        offset = request.get("offset", 0)
        length = request.get("length")
        if not isinstance(offset, int) or offset < 0:
            raise StreamError("offset must be a non-negative integer")
        if length is not None and (not isinstance(length, int) or length < 0):
            raise StreamError("length must be a non-negative integer")
        if not path.is_file():
            return {"ok": False, "error": "not found", "missing": True}, b""
        size = path.stat().st_size
        want = size - offset if length is None else length
        want = max(0, min(want, size - offset, MAX_READ))
        with path.open("rb") as handle_:
            handle_.seek(offset)
            data = handle_.read(want)
        return {"ok": True, "size": len(data), "file_size": size}, data
    raise StreamError(f"unknown op: {op!r}")


def serve(roots: Roots, stdin: BinaryIO, stdout: BinaryIO) -> None:
    """Answer requests until stdin closes."""
    for line in stdin:
        request_id: object = None
        header: dict[str, object]
        try:
            request: object = json.loads(line)
            if not isinstance(request, dict):
                raise StreamError("a request is a JSON object")
            typed = cast(dict[str, object], request)
            request_id = typed.get("id")
            header, payload = handle(roots, typed)
        except (StreamError, json.JSONDecodeError) as exc:
            header, payload = dict[str, object](ok=False, error=str(exc)), b""
        except OSError as exc:
            error = f"{type(exc).__name__}: {exc.strerror or exc}"
            header, payload = dict[str, object](ok=False, error=error), b""
        header["id"] = request_id
        header.setdefault("size", 0)
        stdout.write(json.dumps(header, separators=(",", ":")).encode("utf-8") + b"\n")
        if payload:
            stdout.write(payload)
        stdout.flush()


def main(roots: Sequence[str]) -> None:
    serve(Roots(roots), sys.stdin.buffer, sys.stdout.buffer)
