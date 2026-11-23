# SPDX-License-Identifier: Apache-2.0
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
"""Prepared cluster scenes for the local viewer server, read over one SSH stream.

Each scene's small files (manifest, tables) are mirrored into a local directory
the server reads like any scene; point sweeps and camera images are read on
demand as byte ranges and kept in a size-capped disk cache. Nothing is written
on the cluster.
"""

from __future__ import annotations

import hashlib
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import pyarrow.parquet as pq

from coperceive.remote_view.client import CancelToken, StreamClient, StreamRefused

# Mirrored per scene; points.bin and images are read on demand instead.
SMALL_FILES = (
    "manifest.json",
    "poses.parquet",
    "annotations.parquet",
    "objects.parquet",
    "images.parquet",
    "point_index.parquet",
    "COMPLETE.json",  # last: a mirror is complete only once this is in place
)
DEFAULT_CACHE_BYTES = 2 * 1024**3
_LIST_TTL_S = 3.0
_CHUNK = 8 * 1024 * 1024


class DiskCache:
    """Bytes by key on local disk, least recently used first out, capped in size."""

    def __init__(self, directory: Path, cap_bytes: int = DEFAULT_CACHE_BYTES) -> None:
        self.directory = directory
        self.cap = max(0, cap_bytes)
        self.directory.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._size = sum(entry.stat().st_size for entry in self.directory.glob("*.bin"))

    def _path(self, key: str) -> Path:
        return self.directory / f"{hashlib.sha1(key.encode('utf-8')).hexdigest()}.bin"

    def get(self, key: str) -> bytes | None:
        path = self._path(key)
        try:
            data = path.read_bytes()
        except OSError:
            return None
        try:
            os.utime(path)  # most recently used
        except OSError:
            pass
        return data

    def put(self, key: str, data: bytes) -> None:
        if len(data) > self.cap:
            return
        path = self._path(key)
        temporary = path.with_suffix(".tmp")
        with self._lock:
            try:
                previous = path.stat().st_size
            except OSError:
                previous = 0
            temporary.write_bytes(data)
            os.replace(temporary, path)
            self._size += len(data) - previous
            if self._size > self.cap:
                self._evict()

    def _evict(self) -> None:
        # The app's "Clear cache" empties the directory under a running server.
        self._size = sum(entry.stat().st_size for entry in self.directory.glob("*.bin"))
        entries = sorted(self.directory.glob("*.bin"), key=lambda entry: entry.stat().st_mtime)
        for entry in entries:
            if self._size <= self.cap * 0.9:
                break
            try:
                size = entry.stat().st_size
                entry.unlink()
                self._size -= size
            except OSError:
                continue

    @property
    def size(self) -> int:
        return self._size

    def clear(self) -> None:
        with self._lock:
            for entry in self.directory.glob("*.bin"):
                try:
                    entry.unlink()
                except OSError:
                    pass
            self._size = 0


@dataclass(frozen=True)
class Sweep:
    agent_id: str
    sensor_id: str
    time_us: int
    offset: int
    nbytes: int
    count: int


def _valid_scene_id(scene_id: str) -> bool:
    return not (
        scene_id in {"", ".", ".."}
        or "/" in scene_id
        or "\\" in scene_id
        or "\x00" in scene_id
        or scene_id.startswith(".")
        or scene_id.endswith(".partial")
    )


class RemoteScenes:
    """The ready scenes of one cluster dataset, mirrored on demand."""

    def __init__(self, client: StreamClient, mirror_root: Path, cache: DiskCache) -> None:
        self.client = client
        self.mirror_root = mirror_root
        self.cache = cache
        self.mirror_root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._listed: tuple[float, dict[str, int]] = (0.0, {})
        self._sweeps: dict[str, tuple[int, dict[tuple[str, str, int], Sweep]]] = {}

    # -- listing and mirroring -------------------------------------------------

    def ready(self) -> dict[str, int]:
        """scene id -> version (newest change) of every ready scene, refreshed every few seconds."""
        with self._lock:
            listed_at, scenes = self._listed
            if time.monotonic() - listed_at < _LIST_TTL_S:
                return scenes
        fresh = {
            str(row["scene_id"]): int(row.get("mtime_ns") or 0)
            for row in self.client.ready_scenes()
            if _valid_scene_id(str(row.get("scene_id", "")))
        }
        with self._lock:
            self._listed = (time.monotonic(), fresh)
        return fresh

    def ensure(self, scene_id: str) -> Path | None:
        """The local mirror of a ready scene (its small files), or None if not ready."""
        if not _valid_scene_id(scene_id):
            return None
        version = self.ready().get(scene_id)
        if version is None:
            return None
        local = self.mirror_root / scene_id
        stamp = local / ".remote-version"
        try:
            if stamp.read_text(encoding="utf-8") == str(version):
                return local
        except OSError:
            pass
        local.mkdir(parents=True, exist_ok=True)
        for name in SMALL_FILES:
            try:
                data = self._read_whole(f"{scene_id}/{name}")
            except StreamRefused as exc:
                if exc.missing and name == "point_index.parquet":
                    (local / name).unlink(missing_ok=True)
                    continue
                raise
            temporary = local / f".{name}.tmp"
            temporary.write_bytes(data)
            os.replace(temporary, local / name)
        stamp.write_text(str(version), encoding="utf-8")
        with self._lock:
            self._sweeps.pop(scene_id, None)
        return local

    def _read_whole(self, path: str) -> bytes:
        first = self.client.read(path, 0, _CHUNK)
        if len(first) < _CHUNK:
            return first
        parts = [first]
        offset = len(first)
        while True:
            part = self.client.read(path, offset, _CHUNK)
            parts.append(part)
            offset += len(part)
            if len(part) < _CHUNK:
                return b"".join(parts)

    # -- point sweeps ------------------------------------------------------------

    def sweeps(self, scene_id: str) -> dict[tuple[str, str, int], Sweep]:
        """The scene's point index, (agent, sensor, time) -> where the sweep's bytes are."""
        version = self.ready().get(scene_id, 0)
        with self._lock:
            cached = self._sweeps.get(scene_id)
            if cached and cached[0] == version:
                return cached[1]
        local = self.ensure(scene_id)
        index: dict[tuple[str, str, int], Sweep] = {}
        if local is not None and (local / "point_index.parquet").is_file():
            for row in pq.read_table(local / "point_index.parquet").to_pylist():
                sweep = Sweep(
                    str(row["agent_id"]),
                    str(row["sensor_id"]),
                    int(row["time_us"]),
                    int(row["offset"]),
                    int(row["nbytes"]),
                    int(row["count"]),
                )
                index[(sweep.agent_id, sweep.sensor_id, sweep.time_us)] = sweep
        with self._lock:
            self._sweeps[scene_id] = (version, index)
        return index

    def sweep_bytes(
        self, scene_id: str, sweep: Sweep, *, token: CancelToken | None = None
    ) -> bytes:
        version = self.ready().get(scene_id, 0)
        key = f"{scene_id}|{version}|points|{sweep.offset}|{sweep.nbytes}"
        cached = self.cache.get(key)
        if cached is not None and len(cached) == sweep.nbytes:
            return cached
        data = self.client.read(f"{scene_id}/points.bin", sweep.offset, sweep.nbytes, token=token)
        if len(data) != sweep.nbytes:
            raise OSError(
                f"{scene_id}: short read of points.bin ({len(data)} of {sweep.nbytes} bytes)"
            )
        self.cache.put(key, data)
        return data

    # -- camera images -------------------------------------------------------------

    def file_bytes(self, scene_id: str, relative: str) -> bytes | None:
        """A file below the scene (a preview, or an original through its raw link)."""
        version = self.ready().get(scene_id, 0)
        key = f"{scene_id}|{version}|file|{relative}"
        cached = self.cache.get(key)
        if cached is not None:
            return cached
        try:
            data = self._read_whole(f"{scene_id}/{relative}")
        except StreamRefused as exc:
            if exc.missing:
                return None
            raise
        self.cache.put(key, data)
        return data
