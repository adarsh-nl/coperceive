# SPDX-License-Identifier: Apache-2.0
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportPrivateUsage=false, reportAttributeAccessIssue=false
"""Protocol v2 of the remote-view stream: concurrency, cancellation, timing.

The streamer runs in-process over pipes where a test needs to observe its
ordering (and where a monkeypatched handler can stand in for a slow disk read);
raw JSON lines exercise the compatibility promise directly, and the client is
also run against a minimal legacy server fixture.
"""

from __future__ import annotations

import asyncio
import json
import os
import select
import struct
import sys
import threading
import time
from pathlib import Path
from typing import Any, cast

import pytest
from coperceive.app.server import _StreamHttpStats, create_app
from coperceive.model import PointCloud
from coperceive.remote_view import stream as stream_module
from coperceive.remote_view.client import (
    CancelToken,
    StreamClient,
    StreamFuture,
)
from coperceive.remote_view.scenes import Sweep
from coperceive.remote_view.stream import (
    MAX_READ,
    PROTOCOL,
    Roots,
    StreamError,
    StreamServer,
    handle,
)
from coperceive.store import write_scene
from fastapi.testclient import TestClient

from tests.conftest import sample_scene

SCENE = "synthetic"
STREAM_ARGS = ["-m", "coperceive", "dataset", "stream"]


# -- an in-process streamer over pipes ------------------------------------------------


def _read_exact(fd: int, size: int, timeout: float) -> bytes:
    data = b""
    deadline = time.monotonic() + timeout
    while len(data) < size:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AssertionError(f"timed out after {len(data)} of {size} payload bytes")
        ready, _, _ = select.select([fd], [], [], remaining)
        if not ready:
            raise AssertionError(f"timed out after {len(data)} of {size} payload bytes")
        chunk = os.read(fd, size - len(data))
        if not chunk:
            raise AssertionError("the streamer closed mid-reply")
        data += chunk
    return data


def _read_line(fd: int, timeout: float) -> bytes:
    line = b""
    deadline = time.monotonic() + timeout
    while not line.endswith(b"\n"):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AssertionError(f"timed out waiting for a reply (got {line!r})")
        ready, _, _ = select.select([fd], [], [], remaining)
        if not ready:
            raise AssertionError(f"timed out waiting for a reply (got {line!r})")
        chunk = os.read(fd, 1)
        if not chunk:
            raise AssertionError("the streamer closed while a reply was expected")
        line += chunk
    return line


def _parse_replies(data: bytes) -> list[tuple[dict[str, object], bytes]]:
    """Split a captured stdout byte stream into (header, payload) replies."""
    replies: list[tuple[dict[str, object], bytes]] = []
    offset = 0
    while offset < len(data):
        end = data.index(b"\n", offset)
        header = cast(dict[str, object], json.loads(data[offset:end]))
        offset = end + 1
        size = int(cast(int, header.get("size") or 0))
        replies.append((header, data[offset : offset + size]))
        offset += size
    return replies


def _complete_replies(data: bytes) -> list[tuple[dict[str, object], bytes]]:
    """Parse the complete replies at the start of a still-growing byte buffer."""
    replies: list[tuple[dict[str, object], bytes]] = []
    offset = 0
    while offset < len(data):
        end = data.find(b"\n", offset)
        if end < 0:
            break
        header = cast(dict[str, object], json.loads(data[offset:end]))
        size = int(cast(int, header.get("size") or 0))
        start = end + 1
        if start + size > len(data):
            break
        replies.append((header, data[start : start + size]))
        offset = start + size
    return replies


def _written_replies(
    stdout: _BlockingStdout, count: int, timeout: float = 10.0
) -> list[tuple[dict[str, object], bytes]]:
    data = b""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with stdout.lock:
            data = bytes(stdout.written)
        replies = _complete_replies(data)
        if len(replies) >= count:
            return replies
        time.sleep(0.01)
    raise AssertionError(f"only {len(_complete_replies(data))} of {count} replies were written")


class PipeServer:
    """A real ``StreamServer`` in a thread, requests and replies as OS pipes."""

    def __init__(self, root: Path, *, stdout: Any = None, **options: int) -> None:
        requests_r, self._requests_w = os.pipe()
        self._stdin = os.fdopen(requests_r, "rb")
        self._replies_r: int | None = None
        if stdout is None:
            self._replies_r, replies_w = os.pipe()
            self._stdout = os.fdopen(replies_w, "wb")
        else:
            self._stdout = stdout
        self.server = StreamServer(Roots([root]), self._stdout, **options)
        self._thread = threading.Thread(target=self.server.run, args=(self._stdin,), daemon=True)
        self._thread.start()

    def wait_cancelled(self, count: int = 1, timeout: float = 20.0) -> None:
        """Block until the streamer has accepted ``count`` cancel ops."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if int(cast(int, self.server.counters.snapshot()["cancelled"])) >= count:
                return
            time.sleep(0.002)
        raise AssertionError(f"the streamer did not accept {count} cancel(s) in time")

    def send(self, request: dict[str, object]) -> None:
        self.send_raw(json.dumps(request, separators=(",", ":")).encode("utf-8"))

    def send_raw(self, line: bytes) -> None:
        os.write(self._requests_w, line + b"\n")

    def read_reply(self, timeout: float = 20.0) -> tuple[dict[str, object], bytes]:
        assert self._replies_r is not None, "this server was built with an external stdout"
        header = cast(dict[str, object], json.loads(_read_line(self._replies_r, timeout)))
        size = int(cast(int, header.get("size") or 0))
        payload = _read_exact(self._replies_r, size, timeout)
        return header, payload

    def close(self, timeout: float = 20.0) -> None:
        try:
            os.close(self._requests_w)
        except OSError:
            pass
        self._thread.join(timeout)
        assert not self._thread.is_alive(), "the streamer did not stop when stdin closed"
        if self._replies_r is not None:
            os.close(self._replies_r)


@pytest.fixture
def small_root(tmp_path: Path) -> Path:
    (tmp_path / "a.bin").write_bytes(b"abcdef")
    (tmp_path / "empty.bin").write_bytes(b"")
    return tmp_path


# -- protocol v2 replies and legacy compatibility -------------------------------------


def test_v2_replies_carry_timings_and_legacy_replies_stay_byte_identical(
    small_root: Path,
) -> None:
    server = PipeServer(small_root, workers=2)
    try:
        # A protocol-2 read: additive fields, all cheap and numeric.
        server.send({"id": 1, "op": "read", "path": "a.bin", "protocol": 2})
        header, payload = server.read_reply()
        assert payload == b"abcdef"
        assert (header["ok"], header["id"], header["size"]) == (True, 1, 6)
        assert header["protocol"] == PROTOCOL
        assert isinstance(header["queue_ms"], (int, float))
        assert isinstance(header["read_ms"], (int, float))
        assert header["bytes"] == 6
        # A legacy request: exactly the old reply, with no v2 field added.
        server.send({"id": 2, "op": "read", "path": "a.bin"})
        legacy, payload = server.read_reply()
        assert payload == b"abcdef"
        assert set(legacy) == {"ok", "size", "file_size", "id"}
        assert legacy["id"] == 2
        # Errors keep their legacy shape too.
        server.send({"id": 3, "op": "read", "path": "../escape"})
        legacy_error, payload = server.read_reply()
        assert payload == b""
        assert set(legacy_error) == {"ok", "error", "id", "size"}
        assert (legacy_error["ok"], legacy_error["id"]) == (False, 3)
        assert "escapes" in str(legacy_error["error"])
    finally:
        server.close()


def test_a_large_read_does_not_hold_up_a_later_small_one(
    small_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    release = threading.Event()
    entered = threading.Event()
    original = stream_module.handle

    def slow_large(roots: Roots, request: dict[str, object]) -> tuple[dict[str, object], bytes]:
        if request.get("path") == "slow.bin":
            entered.set()
            assert release.wait(timeout=20), "the test did not release the slow read"
            return {"ok": True, "size": 4}, b"slow"
        return original(roots, request)

    monkeypatch.setattr(stream_module, "handle", slow_large)
    server = PipeServer(small_root, workers=2)
    try:
        # Only protocol-2 requests opt into the concurrent pool; legacy requests
        # keep the old serialized order (covered by the test below).
        server.send({"id": 1, "op": "read", "path": "slow.bin", "protocol": 2})
        assert entered.wait(timeout=20)
        server.send({"id": 2, "op": "read", "path": "a.bin", "protocol": 2})
        # The small read answers first: one slow handler cannot block the pool.
        header, payload = server.read_reply()
        assert (header["id"], payload) == (2, b"abcdef")
        release.set()
        header, payload = server.read_reply()
        assert (header["id"], payload) == (1, b"slow")
    finally:
        release.set()
        server.close()


class _BlockingStdout:
    """A stdout that blocks in ``write`` until the test releases it."""

    def __init__(self) -> None:
        self.gate = threading.Event()
        self.blocked = threading.Event()
        self.lock = threading.Lock()
        self.written = bytearray()

    def write(self, data: bytes) -> int:
        self.blocked.set()
        assert self.gate.wait(timeout=20), "the test did not release the writer"
        with self.lock:
            self.written.extend(data)
        return len(data)

    def flush(self) -> None:
        return


def test_slow_writer_applies_backpressure_instead_of_buffering_replies(
    small_root: Path,
) -> None:
    """A writer stuck on the link must cap queued replies and block the reader.

    ``max_queued`` slots are held until the writer has written the reply, so a
    client cannot make the login node hold more than that many finished sweep
    replies; the read loop blocks instead of accepting more work.
    """
    (small_root / "big.bin").write_bytes(b"x" * 4096)
    stdout = _BlockingStdout()
    max_queued = 3
    server = PipeServer(small_root, stdout=stdout, workers=2, max_queued=max_queued)
    try:
        for request_id in range(1, max_queued + 4):
            server.send({"id": request_id, "op": "read", "path": "big.bin", "protocol": 2})
        # The writer blocks on the first reply; the pool fills the remaining
        # slots, and the read loop stops accepting once one request more than the
        # slot count has been parsed (it counts, then blocks on a slot).
        assert stdout.blocked.wait(timeout=20)
        requested = 0
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            requested = int(cast(int, server.server.counters.snapshot()["requests"]))
            if requested >= max_queued + 1:
                break
            time.sleep(0.01)
        assert requested == max_queued + 1, requested
        # Finished replies are bounded by the slot count, not by how many
        # requests the client sent (the writer holds one while blocked in write).
        deadline = time.monotonic() + 5
        while server.server._replies.qsize() == 0 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert 0 < server.server._replies.qsize() <= max_queued
        time.sleep(0.2)
        assert server.server._replies.qsize() <= max_queued
        assert int(cast(int, server.server.counters.snapshot()["requests"])) == requested

        # Let the writer drain: every accepted request gets its reply, in order.
        stdout.gate.set()
        server.close()
        replies = _parse_replies(bytes(stdout.written))
        assert len(replies) == max_queued + 3, [reply[0].get("id") for reply in replies]
        assert all(header["ok"] for header, _ in replies), replies
        # Protocol 2 replies may be out of order; all six arrived exactly once.
        assert sorted(int(cast(int, header["id"])) for header, _ in replies) == list(
            range(1, max_queued + 4)
        )
        assert all(len(payload) == 4096 for _, payload in replies)
    finally:
        stdout.gate.set()
        server.close()


class _FailingStdout:
    """A stdout whose writes always fail, for the writer-failure path."""

    def __init__(self, error: Exception) -> None:
        self.error = error

    def write(self, data: bytes) -> int:
        raise self.error

    def flush(self) -> None:
        return


@pytest.mark.parametrize(
    "error",
    [BrokenPipeError("broken pipe"), ValueError("header cannot be serialized")],
    ids=["oserror", "not-an-oserror"],
)
def test_broken_stdout_stops_the_streamer_without_hanging(
    small_root: Path, error: Exception
) -> None:
    """A dead writer must not leave the reader blocked on a slot nobody frees.

    This was the reviewer's orphaned-streamer case: a fake stdout that raises,
    nine protocol-2 reads, then stdin closed. The server used to keep every queued
    reply's slot, so the read loop blocked in ``_slots.acquire()`` forever and
    ``run()`` never returned.
    """
    (small_root / "big.bin").write_bytes(b"x" * 4096)
    server = PipeServer(small_root, stdout=_FailingStdout(error), workers=2, max_queued=3)
    for request_id in range(1, 10):
        server.send({"id": request_id, "op": "read", "path": "big.bin", "protocol": 2})
    started = time.monotonic()
    server.close(timeout=5.0)  # closes stdin; must not block on a held slot
    assert time.monotonic() - started < 5.0


def test_legacy_replies_are_backpressured_too(small_root: Path) -> None:
    """Protocol-1 replies hold a slot as well, so they cannot queue unbounded."""
    (small_root / "big.bin").write_bytes(b"x" * 4096)
    stdout = _BlockingStdout()
    max_queued = 3
    server = PipeServer(small_root, stdout=stdout, workers=2, max_queued=max_queued)
    try:
        for request_id in range(1, max_queued + 4):
            server.send({"id": request_id, "op": "read", "path": "big.bin"})
        assert stdout.blocked.wait(timeout=20)
        requested = 0
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            requested = int(cast(int, server.server.counters.snapshot()["requests"]))
            if requested >= max_queued + 1:
                break
            time.sleep(0.01)
        assert requested == max_queued + 1, requested
        deadline = time.monotonic() + 5
        while server.server._replies.qsize() == 0 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert 0 < server.server._replies.qsize() <= max_queued
        time.sleep(0.2)
        assert server.server._replies.qsize() <= max_queued
        assert int(cast(int, server.server.counters.snapshot()["requests"])) == requested
        stdout.gate.set()
        server.close()
        replies = _parse_replies(bytes(stdout.written))
        assert len(replies) == max_queued + 3, [reply[0].get("id") for reply in replies]
        assert [header["id"] for header, _ in replies] == list(range(1, max_queued + 4))
        assert all(header["ok"] for header, _ in replies), replies
        assert all(len(payload) == 4096 for _, payload in replies)
    finally:
        stdout.gate.set()
        server.close()


def test_duplicate_id_stays_refused_until_the_reply_is_written(small_root: Path) -> None:
    """An id may not be reused in the gap between the read and the write."""
    stdout = _BlockingStdout()
    server = PipeServer(small_root, stdout=stdout, workers=2, max_queued=3)
    try:
        server.send({"id": 1, "op": "read", "path": "a.bin", "protocol": 2})
        assert stdout.blocked.wait(timeout=20)  # the reply is queued, not written
        server.send({"id": 1, "op": "read", "path": "a.bin", "protocol": 2})
        time.sleep(0.2)  # let the read loop process the duplicate while the writer is stuck
        stdout.gate.set()
        replies = _written_replies(stdout, 2)
        (first, first_payload), (refusal, refusal_payload) = replies[:2]
        assert (first["ok"], first["id"], first_payload) == (True, 1, b"abcdef")
        assert refusal["ok"] is False and refusal["id"] is None
        assert refusal["duplicate_of"] == 1 and refusal_payload == b""
        # Once the reply was written the id is retired and may be reused.
        server.send({"id": 1, "op": "read", "path": "a.bin", "protocol": 2})
        replies = _written_replies(stdout, 3)
        again, again_payload = replies[2]
        assert (again["ok"], again["id"], again_payload) == (True, 1, b"abcdef")
    finally:
        stdout.gate.set()
        server.close()


def test_duplicate_protocol_2_request_id_is_refused_while_in_flight(
    small_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A second request reusing an in-flight id is refused, so cancel hits one."""
    release = threading.Event()
    entered = threading.Event()
    original = stream_module.handle

    def slow_large(roots: Roots, request: dict[str, object]) -> tuple[dict[str, object], bytes]:
        if request.get("path") == "slow.bin":
            entered.set()
            assert release.wait(timeout=20), "the test did not release the slow read"
            return {"ok": True, "size": 4}, b"slow"
        return original(roots, request)

    monkeypatch.setattr(stream_module, "handle", slow_large)
    server = PipeServer(small_root, workers=2)
    try:
        server.send({"id": 1, "op": "read", "path": "slow.bin", "protocol": 2})
        assert entered.wait(timeout=20)
        server.send({"id": 1, "op": "read", "path": "a.bin", "protocol": 2})
        header, payload = server.read_reply()
        assert header["ok"] is False
        # The refusal must not be routable as the original request's reply.
        assert header["id"] is None
        assert header["duplicate_of"] == 1
        assert "duplicate request id 1" in str(header["error"]), header
        assert payload == b""
        # Only the original request answers for id 1.
        release.set()
        header, payload = server.read_reply()
        assert (header["ok"], payload) == (True, b"slow")
        # The refused duplicate never replies, so no second header for id 1 follows.
        with pytest.raises(AssertionError):
            server.read_reply(timeout=0.3)
    finally:
        release.set()
        server.close()


def test_legacy_requests_are_answered_one_at_a_time_in_order(
    small_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A raw legacy client gets the old serialized, in-order streamer."""
    release = threading.Event()
    entered = threading.Event()
    original = stream_module.handle

    def slow_large(roots: Roots, request: dict[str, object]) -> tuple[dict[str, object], bytes]:
        if request.get("path") == "slow.bin":
            entered.set()
            assert release.wait(timeout=20), "the test did not release the slow read"
            return {"ok": True, "size": 4}, b"slow"
        return original(roots, request)

    monkeypatch.setattr(stream_module, "handle", slow_large)
    server = PipeServer(small_root, workers=2)
    try:
        server.send({"id": 1, "op": "read", "path": "slow.bin"})
        assert entered.wait(timeout=20)
        server.send({"id": 2, "op": "read", "path": "a.bin"})
        release.set()
        # Request 1 answers first even though request 2 is cheaper: legacy order.
        header, payload = server.read_reply()
        assert (header["id"], payload) == (1, b"slow")
        header, payload = server.read_reply()
        assert (header["id"], payload) == (2, b"abcdef")
    finally:
        release.set()
        server.close()


def test_old_style_client_requests_against_the_new_server(tmp_path: Path) -> None:
    """A legacy client (raw JSON, no protocol field) gets the old reply shapes."""
    (tmp_path / "a.bin").write_bytes(b"abcdef")
    argv = [sys.executable, *STREAM_ARGS, "--root", str(tmp_path)]
    import subprocess

    server = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    assert server.stdin is not None and server.stdout is not None
    try:
        requests = [
            {"id": 1, "op": "read", "path": "a.bin", "offset": 0, "length": 3},
            {"id": 2, "op": "stat", "path": "a.bin"},
            {"id": 3, "op": "ready_scenes"},
            {"id": 4, "op": "ping"},
        ]
        for request in requests:
            server.stdin.write(json.dumps(request).encode("utf-8") + b"\n")
        server.stdin.flush()
        replies: list[dict[str, object]] = []
        for _ in requests:
            line = server.stdout.readline()
            header = cast(dict[str, object], json.loads(line))
            payload = server.stdout.read(int(cast(int, header.get("size") or 0)))
            replies.append({**header, "payload": payload})
        by_id = {int(cast(int, reply["id"])): reply for reply in replies}
        assert by_id[1]["payload"] == b"abc"
        assert set(by_id[1]) == {"ok", "size", "file_size", "id", "payload"}
        assert by_id[2]["exists"] is True
        assert set(by_id[2]) == {
            "ok",
            "exists",
            "size",
            "file_size",
            "mtime_ns",
            "is_file",
            "id",
            "payload",
        }
        assert by_id[3]["scenes"] == []
        assert by_id[4]["ok"] is True
        for reply in replies:
            assert "protocol" not in reply and "queue_ms" not in reply and "read_ms" not in reply
    finally:
        server.stdin.close()
        server.wait(timeout=20)


# -- cancellation -------------------------------------------------------------------


def test_cancel_skips_a_queued_read_and_answers_nothing(
    small_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered = threading.Event()
    release = threading.Event()
    original = stream_module.handle

    def slow(roots: Roots, request: dict[str, object]) -> tuple[dict[str, object], bytes]:
        if str(request.get("path")).startswith("slow-"):
            entered.set()
            assert release.wait(timeout=20)
            return {"ok": True, "size": 1}, b"x"
        return original(roots, request)

    monkeypatch.setattr(stream_module, "handle", slow)
    server = PipeServer(small_root, workers=1)
    try:
        server.send({"id": 1, "op": "read", "path": "slow-1.bin", "protocol": 2})
        assert entered.wait(timeout=20)
        server.send({"id": 2, "op": "read", "path": "slow-2.bin", "protocol": 2})
        server.send({"op": "cancel", "id": 2})
        server.wait_cancelled()
        release.set()
        header, _ = server.read_reply()
        assert header["id"] == 1
        # The queued request was skipped: its reply never comes; the next reply
        # in flight is the ping sent after the cancel.
        server.send({"id": 3, "op": "ping", "protocol": 2})
        header, _ = server.read_reply()
        assert header["id"] == 3
        # And a cancel is not itself a reply: only ids 1 and 3 were seen.
    finally:
        release.set()
        server.close()


def test_cancel_drops_an_in_flight_read_reply(
    small_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered = threading.Event()
    release = threading.Event()
    original = stream_module.handle

    def slow(roots: Roots, request: dict[str, object]) -> tuple[dict[str, object], bytes]:
        if request.get("path") == "slow.bin":
            entered.set()
            assert release.wait(timeout=20)
            return {"ok": True, "size": 4}, b"slow"
        return original(roots, request)

    monkeypatch.setattr(stream_module, "handle", slow)
    server = PipeServer(small_root, workers=2)
    try:
        server.send({"id": 1, "op": "read", "path": "slow.bin", "protocol": 2})
        assert entered.wait(timeout=20)
        server.send({"op": "cancel", "id": 1})
        server.wait_cancelled()
        release.set()
        server.send({"id": 2, "op": "read", "path": "a.bin", "protocol": 2})
        header, payload = server.read_reply()
        # The cancelled in-flight reply (id 1, its payload read to the end) is
        # dropped; the client never sees bytes it no longer waits for.
        assert (header["id"], payload) == (2, b"abcdef")
        # Unknown ids are harmless: they produce no reply and break nothing.
        server.send({"op": "cancel", "id": 999})
        server.send({"id": 3, "op": "ping"})
        header, _ = server.read_reply()
        assert header["id"] == 3
    finally:
        release.set()
        server.close()


def test_stats_op_aggregates_counts_and_bounded_percentiles(small_root: Path) -> None:
    server = PipeServer(small_root, workers=2, capacity=8)
    try:
        server.send({"id": 1, "op": "read", "path": "a.bin", "protocol": 2})
        server.read_reply()
        server.send({"id": 2, "op": "stats"})
        header, _ = server.read_reply()
        assert header["ok"] is True
        stats = cast(dict[str, object], header["stats"])
        assert int(cast(int, stats["requests"])) >= 1
        assert int(cast(int, stats["replies"])) >= 1
        assert int(cast(int, stats["bytes"])) == 6
        for key in ("queue_ms", "read_ms"):
            timing = cast(dict[str, object], stats[key])
            assert set(timing) == {"count", "p50", "p95", "max"}
            assert int(cast(int, timing["count"])) <= 8
    finally:
        server.close()


# -- the client over a slow legacy server and a real streamer --------------------------


_LEGACY_SERVER = r"""
import json
import sys
import time

log = sys.argv[1]

def reply(header, payload=b""):
    sys.stdout.write(json.dumps(header, separators=(",", ":")) + "\n")
    sys.stdout.flush()
    if payload:
        sys.stdout.buffer.write(payload)
        sys.stdout.buffer.flush()

for line in sys.stdin:
    request = json.loads(line)
    if request.get("op") == "cancel":
        with open(log, "a", encoding="utf-8") as handle:
            handle.write(str(request.get("id")) + "\n")
        continue
    op = request.get("op")
    if op == "read":
        time.sleep(0.2)  # a legacy server answers one request at a time, slowly
        reply({"ok": True, "size": 3, "file_size": 6, "id": request["id"]}, b"abc")
    elif op == "ping":
        reply({"ok": True, "id": request["id"]})
    else:
        reply({"ok": False, "error": "unknown op", "size": 0, "id": request["id"]})
"""


def test_new_client_works_against_a_minimal_legacy_server(tmp_path: Path) -> None:
    script = tmp_path / "legacy_server.py"
    script.write_text(_LEGACY_SERVER, encoding="utf-8")
    cancel_log = tmp_path / "cancelled.txt"
    client = StreamClient([sys.executable, str(script), str(cancel_log)], timeout_s=30)
    try:
        first = client.submit({"op": "read", "path": "a.bin"})
        assert client.cancel(first.request_id) is True
        assert first.cancelled()
        assert client.read("a.bin") == b"abc"
        assert client.cancelled == 1
        stats = client.stats()
        assert int(cast(int, stats["requests"])) == 2
        assert stats["queue_ms"] == {"count": 0, "p50": 0.0, "p95": 0.0, "max": 0.0}
        assert int(cast(int, stats["transfer_ms"]["count"])) == 1  # type: ignore[index]
    finally:
        client.close()
    # The cancel op really crossed the wire; the legacy server logged it.
    assert cancel_log.read_text(encoding="utf-8").strip() == str(first.request_id)


def test_client_and_streamer_together_after_a_cancelled_read(tmp_path: Path) -> None:
    (tmp_path / "a.bin").write_bytes(b"abcdef")
    argv = [sys.executable, *STREAM_ARGS, "--root", str(tmp_path)]
    client = StreamClient(argv, timeout_s=30)
    try:
        future = client.submit({"op": "read", "path": "a.bin"})
        cancelled = client.cancel(future.request_id)
        assert future.cancelled() or cancelled is False  # answered-in-time is allowed
        # The connection keeps working: a cancelled request never poisons it.
        assert client.read("a.bin") == b"abcdef"
        stats = client.stats()
        assert int(cast(int, stats["samples"])) >= 1
        assert int(cast(int, stats["bytes"])) >= 6
    finally:
        client.close()


def test_max_read_and_confinement_are_unchanged(tmp_path: Path) -> None:
    big = tmp_path / "big.bin"
    big.write_bytes(b"x" * (MAX_READ + 1024))
    roots = Roots([tmp_path])
    header, payload = handle(roots, {"op": "read", "path": "big.bin"})
    assert len(payload) == MAX_READ
    assert header["size"] == MAX_READ
    with pytest.raises(StreamError, match="escapes"):
        handle(roots, {"op": "read", "path": "/etc/passwd"})


# -- the viewer server: stats endpoint and disconnect cancellation --------------------


def test_stream_stats_endpoint_has_a_versioned_shape(tmp_path: Path) -> None:
    http = TestClient(create_app(root=tmp_path, token=None))
    response = http.get("/api/stream-stats")
    assert response.status_code == 200
    body = response.json()
    assert body["schema"] == "coperceive-stream-stats-1"
    for key in ("requests", "bytes", "queue_ms", "read_ms", "transfer_ms", "active", "queued"):
        assert key in body
    for key in ("queue_ms", "read_ms", "transfer_ms"):
        assert set(body[key]) == {"count", "p50", "p95", "max"}
        assert body[key]["count"] == 0
    assert body["remote"] == {}
    assert body["by_endpoint"]["points"]["requests"] == 0


def test_stream_stats_memory_is_bounded_and_thread_safe() -> None:
    stats = _StreamHttpStats(capacity=8)
    started = time.perf_counter()

    def record(index: int) -> None:
        for _ in range(50):
            stats.begin()
            stats.finish("points", time.perf_counter() - 0.001, index)

    threads = [threading.Thread(target=record, args=(index,)) for index in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    snapshot = stats.snapshot(None)
    assert snapshot["requests"] == 200
    assert snapshot["bytes"] == sum(index for index in range(4)) * 50
    assert snapshot["active"] == 0
    assert int(cast(int, snapshot["samples"])) <= 8
    assert snapshot["by_endpoint"]["points"]["requests"] == 200  # type: ignore[index]
    assert stats._http_ms.count == 200
    del started


class _FakeStreamClient:
    """Just enough StreamClient for the disconnect-cancel path."""

    def __init__(self) -> None:
        self.cancel_event = threading.Event()
        self.cancelled_ids: list[int] = []
        self.token_obj: CancelToken | None = None

    def token(self) -> CancelToken:
        self.token_obj = CancelToken(cast(Any, self))
        return self.token_obj

    def cancel(self, request_id: int) -> bool:
        self.cancelled_ids.append(request_id)
        self.cancel_event.set()
        return True


class _FakeRemote:
    """A RemoteScenes stand-in whose sweep read blocks until it is cancelled."""

    def __init__(self, scene_dir: Path) -> None:
        self.client = _FakeStreamClient()
        self.scene_dir = scene_dir
        self.sweep = Sweep("vehicle", "lidar_top", 100_000, 0, 16, 1)
        self.data = struct.pack("<4f", 1.0, 0.0, 0.0, 0.0)
        self.read_started = threading.Event()

    def ensure(self, scene_id: str) -> Path:
        assert scene_id == SCENE
        return self.scene_dir

    def sweeps(self, scene_id: str) -> dict[tuple[str, str, int], Sweep]:
        assert scene_id == SCENE
        return {(self.sweep.agent_id, self.sweep.sensor_id, self.sweep.time_us): self.sweep}

    def sweep_bytes(
        self, scene_id: str, sweep: Sweep, *, token: CancelToken | None = None
    ) -> bytes:
        assert sweep is self.sweep
        assert token is not None
        waiter = StreamFuture()
        waiter.request_id = 7
        token.attach(waiter)
        self.read_started.set()
        assert self.client.cancel_event.wait(timeout=20), "the endpoint never cancelled the read"
        return self.data


def _run_asgi(app: Any, scope: dict[str, Any], receive: Any) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []

    async def send(message: dict[str, Any]) -> None:
        messages.append(message)

    asyncio.run(app(scope, receive, send))
    return messages


def _points_scope() -> dict[str, Any]:
    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": f"/api/scenes/{SCENE}/points",
        "raw_path": f"/api/scenes/{SCENE}/points".encode(),
        "query_string": b"time_us=100000&agent_id=vehicle&sensor_id=lidar_top",
        "root_path": "",
        "headers": [],
        "client": ("test", 1),
        "server": ("test", 80),
    }


def test_points_endpoint_cancels_the_remote_read_when_the_client_disconnects(
    tmp_path: Path,
) -> None:
    mirror = tmp_path / "mirror"
    write_scene(sample_scene(), mirror / SCENE)
    remote = _FakeRemote(mirror / SCENE)
    app = create_app(root=mirror, token=None, remote=cast(Any, remote))
    calls = 0

    async def receive() -> dict[str, Any]:
        # An ASGI server queues the request message, then a disconnect once the
        # browser is gone. Both are available immediately: Starlette's
        # is_disconnected() polls under a cancelled scope, so a receive that
        # awaited made the poll a no-op instead of reading the disconnect.
        nonlocal calls
        calls += 1
        if calls == 1:
            return {"type": "http.request", "body": b"", "more_body": False}
        return {"type": "http.disconnect"}

    messages = _run_asgi(app, _points_scope(), receive)
    statuses = [
        message["status"] for message in messages if message["type"] == "http.response.start"
    ]
    assert statuses == [499], messages
    assert remote.client.cancel_event.is_set()
    assert 7 in remote.client.cancelled_ids
    # The viewer's own stats recorded the cancelled request, not a normal one.
    stats = TestClient(app).get("/api/stream-stats").json()
    assert stats["cancelled"] == 1
    assert stats["active"] == 0


def test_points_on_a_local_dataset_still_answers_normally(tmp_path: Path) -> None:
    write_scene(sample_scene(), tmp_path / SCENE)
    (tmp_path / SCENE / "COMPLETE.json").write_text("{}\n")
    http = TestClient(create_app(root=tmp_path, token=None))
    scene = sample_scene()
    cloud: PointCloud = scene.point_clouds[0]
    response = http.get(
        f"/api/scenes/{SCENE}/points",
        params={
            "time_us": cloud.time_us,
            "agent_id": cloud.agent_id,
            "sensor_id": cloud.sensor_id,
        },
    )
    assert response.status_code == 200
    assert len(response.content) == 12  # one point, world xyz
    stats = http.get("/api/stream-stats").json()
    assert stats["by_endpoint"]["points"]["requests"] == 1
