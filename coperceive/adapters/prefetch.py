# SPDX-License-Identifier: Apache-2.0
"""Read a scene's raw files ahead of the parser, several at a time.

On a cluster's shared filesystem a cold file costs ~150 ms before its first
byte (measured: 6.4 files/s for one sequential reader, ~800 MiB/s once warm),
so a scene of ~1,700 records and sweeps spends minutes waiting while the CPU
idles. A :class:`Prefetcher` reads the files the build will consume, in that
order, with a bounded thread pool and a bounded number of bytes held ahead,
and hands each file's bytes to the reader that asks for it
(:func:`read_bytes`). A file the plan does not name, or one that resolves
outside the raw root, is read the ordinary way by the caller, so confinement
and error messages are unchanged.

``COPERCEIVE_IO_THREADS`` sets the concurrent reads per process (default 16;
0 reads sequentially, as before).
"""

from __future__ import annotations

import contextvars
import os
import threading
import time
from collections.abc import Generator, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_THREADS = 16
DEFAULT_AHEAD_BYTES = 256 * 1024 * 1024

_current: contextvars.ContextVar[Prefetcher | None] = contextvars.ContextVar(
    "coperceive_prefetch", default=None
)


def io_threads() -> int:
    """Concurrent raw reads per process, from ``COPERCEIVE_IO_THREADS``."""
    text = os.environ.get("COPERCEIVE_IO_THREADS", "").strip()
    if not text:
        return DEFAULT_THREADS
    try:
        return max(0, min(int(text), 128))
    except ValueError:
        return DEFAULT_THREADS


@dataclass
class ReadStats:
    """What reading the raw files cost, for the prepare timing line."""

    files: int = 0
    bytes: int = 0
    wait_s: float = 0.0  # time the parser waited for a file not yet read
    threads: int = 0
    build_s: float = 0.0  # reading and parsing the scene (ingest's load)
    write_s: float = 0.0  # writing the schema 1 scene
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def add(self, size: int) -> None:
        with self._lock:
            self.files += 1
            self.bytes += size


_stats: contextvars.ContextVar[ReadStats | None] = contextvars.ContextVar(
    "coperceive_read_stats", default=None
)


def current_stats() -> ReadStats | None:
    return _stats.get()


@contextmanager
def collecting() -> Generator[ReadStats, None, None]:
    """Count raw reads (prefetched or not) made in this context."""
    stats = ReadStats(threads=io_threads())
    token = _stats.set(stats)
    try:
        yield stats
    finally:
        _stats.reset(token)


class Prefetcher:
    """Reads ``paths`` in order, at most ``threads`` at once and ``ahead_bytes`` ahead."""

    def __init__(
        self,
        root: Path,
        paths: Sequence[Path],
        *,
        threads: int,
        ahead_bytes: int = DEFAULT_AHEAD_BYTES,
    ) -> None:
        self._root = root.resolve()
        self._order = list(dict.fromkeys(paths))
        self._index = {path: i for i, path in enumerate(self._order)}
        self._pool = ThreadPoolExecutor(max_workers=max(1, threads), thread_name_prefix="prefetch")
        self._futures: dict[int, Future[bytes | None]] = {}
        self._next = 0
        self._ahead = 0
        self._ahead_cap = ahead_bytes
        self._threads = max(1, threads)
        self._lock = threading.Lock()
        self._stats = _stats.get()
        self._fill()

    def _read(self, path: Path) -> bytes | None:
        try:
            real = path.resolve()
            if real != self._root and not real.is_relative_to(self._root):
                return None  # the caller's own read refuses it with the usual message
            data = real.read_bytes()
        except OSError:
            return None  # missing or unreadable: the caller reports it
        with self._lock:
            self._ahead += len(data)
        return data

    def _fill(self) -> None:
        # Keep `threads` reads in flight, plus a queue while under the byte cap.
        while self._next < len(self._order):
            with self._lock:
                in_flight = sum(1 for f in self._futures.values() if not f.done())
                if in_flight >= self._threads * 2 or self._ahead >= self._ahead_cap:
                    return
            self._futures[self._next] = self._pool.submit(self._read, self._order[self._next])
            self._next += 1

    def take(self, path: Path) -> bytes | None:
        """The prefetched bytes of ``path``, or None to read it the ordinary way."""
        index = self._index.get(path)
        if index is None:
            return None
        future = self._futures.pop(index, None)
        if future is None:
            return None
        ready = future.done()
        started = time.perf_counter()
        data = future.result()
        if self._stats is not None and not ready:
            self._stats.wait_s += time.perf_counter() - started
        if data is not None:
            with self._lock:
                self._ahead -= len(data)
        self._fill()
        return data

    def close(self) -> None:
        for future in self._futures.values():
            future.cancel()
        self._pool.shutdown(wait=True, cancel_futures=True)
        self._futures.clear()


@contextmanager
def prefetching(root: Path, paths: Sequence[Path]) -> Generator[None, None, None]:
    """Prefetch ``paths`` (under ``root``) for :func:`read_bytes` in this context."""
    threads = io_threads()
    if threads <= 0 or not paths:
        yield
        return
    prefetcher = Prefetcher(root, paths, threads=threads)
    token = _current.set(prefetcher)
    try:
        yield
    finally:
        _current.reset(token)
        prefetcher.close()


def read_bytes(path: Path) -> bytes:
    """A raw file's bytes: prefetched when planned, else read now."""
    prefetcher = _current.get()
    data = prefetcher.take(path) if prefetcher is not None else None
    if data is None:
        data = path.read_bytes()
    stats = _stats.get()
    if stats is not None:
        stats.add(len(data))
    return data
