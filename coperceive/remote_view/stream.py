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
    {"id": 4, "op": "ping", "protocol": 2}
    {"op": "cancel", "id": 1}
    {"id": 5, "op": "stats"}

A request that carries ``"protocol": 2`` is handled on a small bounded pool,
so a large sweep read does not hold up the small manifest/stat reads behind it;
its replies may therefore arrive out of order (every header carries its
request's ``id`` and the client routes by it). Requests without the field keep
the old behaviour exactly: they are answered one at a time, in request order,
so a raw legacy consumer that reads one reply per request is unaffected (the
cluster-dataset e2e does this). The ``cancel`` op is the one request with no
reply: its ``id`` names the request to cancel. A request that carries
``"protocol": 2`` (or later) gets the additive timing fields ``protocol``,
``queue_ms`` (from this loop to the handler), ``read_ms`` and ``bytes``;
requests without the field get exactly the legacy reply, field for field.
``{"op": "stats"}`` returns the streamer's aggregate counters and p50/p95.
Cancelled queued requests are skipped; a cancelled in-flight read finishes but
its reply (header and payload) is dropped, so the client never waits for bytes
that will not come. A second concurrent protocol-2 request reusing an id that
is still in flight is refused with a clear error (a cancel could otherwise hit
both requests). Slots for accepted requests are released only once the writer
has written the reply, so replies waiting on a slow link are bounded by
``max_queued`` and the reader is backpressured instead of buffering
unboundedly on the login node.

``MAX_READ`` and the root confinement are unchanged from the single-request
version.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from queue import Empty, Queue
from typing import BinaryIO, cast

from coperceive.remote_view.stats import DEFAULT_CAPACITY, SampleStats

MAX_READ = 16 * 1024 * 1024
PROTOCOL = 2
DEFAULT_WORKERS = 4
MAX_QUEUED = 32


class StreamError(ValueError):
    """A request the streamer refuses (outside the roots, malformed)."""


_NO_ID = object()


class _WriterStopped(Exception):
    """The reply writer failed; the read loop must stop instead of waiting for slots."""


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
    """One request -> (header, payload). Pure per request: safe to call concurrently."""
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


def wants_protocol_2(request: dict[str, object]) -> bool:
    """Whether the request opted into the additive v2 reply fields."""
    version = request.get("protocol")
    return isinstance(version, int) and not isinstance(version, bool) and version >= PROTOCOL


def _cancel_key(request_id: object) -> int | None:
    """The value a cancel op names, or None for an id the server cannot cancel."""
    if isinstance(request_id, bool) or not isinstance(request_id, int):
        return None
    return request_id


class StreamCounters:
    """Aggregate streamer counters with bounded timing samples."""

    def __init__(self, capacity: int = DEFAULT_CAPACITY) -> None:
        self._lock = threading.Lock()
        self.requests = 0
        self.replies = 0
        self.errors = 0
        self.cancelled = 0
        self.bytes = 0
        self.queue_ms = SampleStats(capacity)
        self.read_ms = SampleStats(capacity)

    def requested(self) -> None:
        with self._lock:
            self.requests += 1

    def completed(
        self, header: dict[str, object], payload: bytes, queue_ms: float, read_ms: float
    ) -> None:
        with self._lock:
            self.replies += 1
            if not header.get("ok"):
                self.errors += 1
            self.bytes += len(payload)
        self.queue_ms.add(queue_ms)
        self.read_ms.add(read_ms)

    def cancelled_one(self) -> None:
        with self._lock:
            self.cancelled += 1

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            return {
                "requests": self.requests,
                "replies": self.replies,
                "errors": self.errors,
                "cancelled": self.cancelled,
                "bytes": self.bytes,
                "queue_ms": self.queue_ms.snapshot(),
                "read_ms": self.read_ms.snapshot(),
            }


def _error_header(exc: Exception) -> dict[str, object]:
    if isinstance(exc, OSError):
        return {"ok": False, "error": f"{type(exc).__name__}: {exc.strerror or exc}"}
    return {"ok": False, "error": str(exc)}


class StreamServer:
    """Answers requests from one stdin until it closes, on one stdout.

    Reads run on a bounded pool (``workers``), replies are written by a single
    writer thread in completion order so a header and its payload never
    interleave. ``max_queued`` caps the accepted-but-not-yet-written requests:
    a slot is held until the writer has written that reply, so a slow link
    cannot let finished replies pile up in memory, and when the client outruns
    the writer the read loop blocks instead of growing a queue. If the writer
    fails (stdout broken, a header that cannot be serialised), the server
    releases the queue and stops instead of waiting on slots nobody will free.
    """

    def __init__(
        self,
        roots: Roots,
        stdout: BinaryIO,
        *,
        workers: int = DEFAULT_WORKERS,
        max_queued: int = MAX_QUEUED,
        capacity: int = DEFAULT_CAPACITY,
    ) -> None:
        self.roots = roots
        self._stdout = stdout
        self._replies: Queue[tuple[dict[str, object], bytes, Callable[[], None] | None] | None] = (
            Queue()
        )
        self._pool = ThreadPoolExecutor(max_workers=max(1, workers), thread_name_prefix="stream")
        self._slots = threading.Semaphore(max(1, max_queued))
        # Set when the writer cannot write any more (broken stdout, bad header):
        # the reader then stops instead of blocking forever on a slot that will
        # never be released.
        self._stopping = threading.Event()
        self._lock = threading.Lock()
        # Ids accepted but not yet answered; a cancel for one of them is honoured.
        self._outstanding: set[int] = set()
        self._cancelled: set[int] = set()
        self._writer: threading.Thread | None = None
        self.counters = StreamCounters(capacity)

    # -- lifecycle -------------------------------------------------------------

    def run(self, stdin: BinaryIO) -> None:
        """Read requests until stdin closes, then drain and flush."""
        self._writer = threading.Thread(
            target=self._write_replies, name="stream-writer", daemon=True
        )
        self._writer.start()
        try:
            for line in stdin:
                self._accept(line)
        except _WriterStopped:
            pass
        finally:
            self._shutdown()

    def _shutdown(self) -> None:
        self._pool.shutdown(wait=True)
        self._replies.put(None)
        if self._writer is not None:
            self._writer.join(timeout=10)

    # -- request intake ----------------------------------------------------------

    def _accept(self, line: bytes) -> None:
        request_id: object = None
        try:
            parsed: object = json.loads(line)
            if not isinstance(parsed, dict):
                raise StreamError("a request is a JSON object")
            request = cast(dict[str, object], parsed)
            request_id = request.get("id")
            op = request.get("op")
            if op == "cancel":
                self._cancel(request_id)
                return
            queued_at = time.perf_counter()
            self.counters.requested()
            if op in {"ping", "stats"}:
                header: dict[str, object] = {"ok": True}
                if op == "stats":
                    header["stats"] = self.counters.snapshot()
                # Cheap ops answer on the read loop so a cancel/stats op stays
                # responsive while workers are busy with large reads.
                self._publish(header, b"", request_id, request, queued_at, queued_at)
                return
            if not wants_protocol_2(request):
                # Legacy clients get exactly the serialized, in-order server they
                # always had: their replies must arrive in request order (the old
                # client read one reply per request; raw consumers such as the
                # cluster-dataset e2e rely on that). Concurrency and out-of-order
                # replies are the additive protocol-2 behaviour only.
                self._acquire_slot()
                release = self._slots.release
                read_started = time.perf_counter()
                try:
                    header, payload = handle(self.roots, request)
                except (StreamError, OSError) as exc:
                    header, payload = _error_header(exc), b""
                self._publish(
                    header, payload, request_id, request, queued_at, read_started, release
                )
                return
            key = _cancel_key(request_id)
            if key is not None:
                with self._lock:
                    duplicate = key in self._outstanding
                if duplicate:
                    # The refusal must not look like the original request's reply:
                    # a client routes by id, so this one carries no id and names the
                    # request it duplicates instead.
                    refusal: dict[str, object] = {
                        "ok": False,
                        "error": f"duplicate request id {key}: it is still in flight",
                        "duplicate_of": key,
                    }
                    self._publish(
                        refusal,
                        b"",
                        request_id,
                        request,
                        queued_at,
                        queued_at,
                        header_id=None,
                    )
                    return
            self._acquire_slot()
            with self._lock:
                if key is not None:
                    self._outstanding.add(key)
            self._pool.submit(self._work, request, request_id, queued_at, key)
        except (StreamError, json.JSONDecodeError) as exc:
            self._publish(
                _error_header(exc), b"", request_id, {}, time.perf_counter(), time.perf_counter()
            )
        except OSError as exc:
            self._publish(
                _error_header(exc), b"", request_id, {}, time.perf_counter(), time.perf_counter()
            )

    def _acquire_slot(self) -> None:
        """Take an accepted-request slot, unless the writer has stopped."""
        while not self._stopping.is_set():
            if self._slots.acquire(timeout=0.05):
                return
        raise _WriterStopped

    def _retire(self, key: int | None) -> None:
        """A reply has been written: free its slot and its id, in that order."""
        self._slots.release()
        if key is not None:
            with self._lock:
                self._outstanding.discard(key)

    def _writer_failed(self) -> None:
        """The writer cannot continue: stop accepting and release queued slots."""
        self._stopping.set()
        while True:
            try:
                item = self._replies.get_nowait()
            except Empty:
                return
            if item is not None and item[2] is not None:
                item[2]()

    def _cancel(self, request_id: object) -> None:
        """Cancel a queued/in-flight request: no reply of its own, unknown ids harmless."""
        key = _cancel_key(request_id)
        if key is None:
            return
        with self._lock:
            if key not in self._outstanding or key in self._cancelled:
                return
            self._cancelled.add(key)
        self.counters.cancelled_one()

    def _take_cancelled(self, key: int | None) -> bool:
        """True when this request was cancelled and must not answer."""
        if key is None:
            return False
        with self._lock:
            if key not in self._cancelled:
                return False
            self._cancelled.discard(key)
            self._outstanding.discard(key)
            return True

    def _take_cancelled_after_read(self, key: int | None) -> bool:
        """True when the request was cancelled while it read; retire it, drop the reply."""
        if key is None:
            return False
        with self._lock:
            if key not in self._cancelled:
                return False
            self._cancelled.discard(key)
            self._outstanding.discard(key)
            return True

    # -- workers -----------------------------------------------------------------

    def _work(
        self, request: dict[str, object], request_id: object, queued_at: float, key: int | None
    ) -> None:
        release = self._slots.release
        handed_to_writer = False
        try:
            if self._take_cancelled(key):
                return
            read_started = time.perf_counter()
            try:
                header, payload = handle(self.roots, request)
            except (StreamError, OSError) as exc:  # one bad request must not stop the rest
                header, payload = _error_header(exc), b""
            if self._take_cancelled_after_read(key):
                return  # cancelled while reading: finish the read, drop the reply
            # The slot and the id stay held until the writer has written the bytes:
            # a slow link must not let finished replies accumulate in memory, and
            # an id may not be reused while its reply is still queued.
            self._publish(
                header,
                payload,
                request_id,
                request,
                queued_at,
                read_started,
                lambda: self._retire(key),
            )
            handed_to_writer = True
        finally:
            if not handed_to_writer:
                release()
                if key is not None:
                    with self._lock:
                        self._outstanding.discard(key)

    # -- replies -----------------------------------------------------------------

    def _publish(
        self,
        header: dict[str, object],
        payload: bytes,
        request_id: object,
        request: dict[str, object],
        queued_at: float,
        read_started: float,
        release: Callable[[], None] | None = None,
        header_id: object = _NO_ID,
    ) -> None:
        now = time.perf_counter()
        queue_ms = (read_started - queued_at) * 1000.0
        read_ms = (now - read_started) * 1000.0
        header["id"] = request_id if header_id is _NO_ID else header_id
        header.setdefault("size", len(payload))
        if wants_protocol_2(request):
            header["protocol"] = PROTOCOL
            header["queue_ms"] = round(queue_ms, 3)
            header["read_ms"] = round(read_ms, 3)
            header["bytes"] = len(payload)
        self.counters.completed(header, payload, queue_ms, read_ms)
        self._replies.put((header, payload, release))

    def _write_replies(self) -> None:
        while True:
            item = self._replies.get()
            if item is None:
                return
            header, payload, release = item
            try:
                self._stdout.write(
                    json.dumps(header, separators=(",", ":")).encode("utf-8") + b"\n"
                )
                if payload:
                    self._stdout.write(payload)
                self._stdout.flush()
            except Exception:  # broken stdout, unserialisable header: stop, don't hang
                if release is not None:
                    release()
                self._writer_failed()
                return
            if release is not None:
                release()


def serve(roots: Roots, stdin: BinaryIO, stdout: BinaryIO) -> None:
    """Answer requests until stdin closes; legacy and v2 clients alike."""
    StreamServer(roots, stdout).run(stdin)


def main(roots: Sequence[str]) -> None:
    serve(Roots(roots), sys.stdin.buffer, sys.stdout.buffer)
