# SPDX-License-Identifier: Apache-2.0
"""One persistent connection to ``coperceive dataset stream``, with pipelining.

The connection is a single long-lived process, normally
``ssh <host> <quoted coperceive argv> dataset stream --root …``: no process per
file. Requests are written as they come, each with an id; a reader thread
routes every reply to its waiting caller, so several reads are in flight at
once and the round trips overlap (what keeps playback fed over 20–40 ms RTT).
Paths travel only as JSON on stdin, never through a shell.

Each request carries ``protocol: 2``, which makes this client ask the streamer
for the additive timing fields (``queue_ms`` from the server, ``read_ms``,
``bytes``). A server too old to know the field ignores it and answers the
legacy reply; the timing simply stays ``None``. Requests are recorded locally
(write to reply transfer time), and :meth:`StreamClient.cancel` cancels a read
that is no longer wanted (see :class:`CancelToken` for the app's use: a browser
that disconnects while a sweep is in flight).
"""

from __future__ import annotations

import json
import subprocess
import threading
import time
from collections.abc import Sequence
from concurrent.futures import CancelledError, Future
from dataclasses import dataclass
from typing import Any, cast

from coperceive.remote_view.stats import DEFAULT_CAPACITY, SampleStats
from coperceive.remote_view.stream import PROTOCOL


class StreamUnavailable(OSError):
    """The streamer process is gone or did not answer."""


class StreamRefused(ValueError):
    """The streamer refused the request (outside the dataset, not found)."""

    def __init__(self, message: str, *, missing: bool = False) -> None:
        super().__init__(message)
        self.missing = missing


class StreamCancelled(StreamUnavailable):
    """The request was cancelled before its reply arrived."""


@dataclass(frozen=True)
class RequestTiming:
    """One request's timing: server queue/read, and the local write→reply time."""

    request_id: int
    queue_ms: float | None
    read_ms: float | None
    transfer_ms: float
    bytes: int


class StreamFuture(Future[tuple[dict[str, Any], bytes]]):
    """A pipelined request; carries its id and, once answered, its timing."""

    def __init__(self) -> None:
        super().__init__()
        self.request_id = 0
        self.written_at = 0.0
        self.timing: RequestTiming | None = None


class CancelToken:
    """Lets another thread cancel the request the caller has just submitted.

    ``client.call(request, token=token)`` attaches its request id to the token;
    a thread polling a browser connection calls :meth:`cancel`, which sends the
    ``cancel`` op and marks the future cancelled.
    """

    def __init__(self, client: StreamClient) -> None:
        self._client = client
        self._lock = threading.Lock()
        self._request_id: int | None = None
        self._cancelled = False

    def attach(self, waiter: StreamFuture) -> None:
        with self._lock:
            self._request_id = waiter.request_id
            cancelled = self._cancelled
        if cancelled:
            self._client.cancel(waiter.request_id)

    def cancel(self) -> bool:
        """Best effort: False when no request has been attached or it already answered."""
        with self._lock:
            self._cancelled = True
            request_id = self._request_id
        if request_id is None:
            return False
        return self._client.cancel(request_id)


class StreamClient:
    def __init__(self, argv: Sequence[str], *, timeout_s: float = 60.0) -> None:
        if not argv:
            raise ValueError("the streamer command is empty")
        self._argv = list(argv)
        self._timeout = timeout_s
        self._lock = threading.Lock()
        self._pending: dict[int, StreamFuture] = {}
        self._next_id = 1
        self._process: subprocess.Popen[bytes] | None = None
        self._requests = 0
        self._bytes_read = 0
        self._cancelled = 0
        self._errors = 0
        self._queue_ms = SampleStats(DEFAULT_CAPACITY)
        self._read_ms = SampleStats(DEFAULT_CAPACITY)
        self._transfer_ms = SampleStats(DEFAULT_CAPACITY)

    # -- connection -------------------------------------------------------------

    def _start(self) -> subprocess.Popen[bytes]:
        process = subprocess.Popen(
            self._argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0,
        )
        reader = threading.Thread(target=self._read_replies, args=(process,), daemon=True)
        reader.start()
        return process

    def _read_replies(self, process: subprocess.Popen[bytes]) -> None:
        stream = process.stdout
        assert stream is not None
        try:
            while True:
                line = stream.readline()
                if not line:
                    break
                header = json.loads(line)
                size = int(header.get("size") or 0)
                payload = b""
                while len(payload) < size:
                    chunk = stream.read(size - len(payload))
                    if not chunk:
                        raise StreamUnavailable("the streamer closed mid-reply")
                    payload += chunk
                arrived = time.perf_counter()
                request_id = int(header.get("id") or 0)
                with self._lock:
                    waiter = self._pending.pop(request_id, None)
                self._record(header, payload, arrived, waiter)
                if waiter is not None and not waiter.cancelled():
                    waiter.set_result((header, payload))
        except (OSError, ValueError) as exc:
            self._fail_all(StreamUnavailable(f"the streamer connection broke: {exc}"))
            return
        self._fail_all(StreamUnavailable("the streamer connection closed"))

    def _record(
        self,
        header: dict[str, Any],
        payload: bytes,
        arrived: float,
        waiter: StreamFuture | None,
    ) -> None:
        """Count one reply and keep its timing samples, bounded in memory."""
        queue_ms = _optional_float(header.get("queue_ms"))
        read_ms = _optional_float(header.get("read_ms"))
        if waiter is not None and waiter.written_at:
            transfer_ms = (arrived - waiter.written_at) * 1000.0
            waiter.timing = RequestTiming(
                request_id=waiter.request_id,
                queue_ms=queue_ms,
                read_ms=read_ms,
                transfer_ms=transfer_ms,
                bytes=len(payload),
            )
        else:
            transfer_ms = None
        with self._lock:
            # Bytes that crossed the connection, including a cancelled reply
            # the caller no longer waits for: they still took link time.
            self._bytes_read += len(payload)
            if not header.get("ok") and not (waiter is not None and waiter.cancelled()):
                self._errors += 1
        if queue_ms is not None:
            self._queue_ms.add(queue_ms)
        if read_ms is not None:
            self._read_ms.add(read_ms)
        if transfer_ms is not None:
            self._transfer_ms.add(transfer_ms)

    def _fail_all(self, error: Exception) -> None:
        with self._lock:
            pending, self._pending = self._pending, {}
            self._process = None
        for waiter in pending.values():
            if not waiter.done():
                waiter.set_exception(error)

    def close(self) -> None:
        with self._lock:
            process, self._process = self._process, None
        if process is not None:
            try:
                if process.stdin:
                    process.stdin.close()
                process.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                process.kill()

    # -- counters ----------------------------------------------------------------

    @property
    def requests(self) -> int:
        with self._lock:
            return self._requests

    @property
    def bytes_read(self) -> int:
        with self._lock:
            return self._bytes_read

    @property
    def cancelled(self) -> int:
        with self._lock:
            return self._cancelled

    @property
    def outstanding(self) -> int:
        """Requests written but not answered yet."""
        with self._lock:
            return len(self._pending)

    def stats(self) -> dict[str, object]:
        """Aggregate counters and bounded p50/p95 of queue/read/transfer times."""
        with self._lock:
            counters = {
                "requests": self._requests,
                "bytes": self._bytes_read,
                "cancelled": self._cancelled,
                "errors": self._errors,
                "outstanding": len(self._pending),
            }
        return {
            **counters,
            "queue_ms": self._queue_ms.snapshot(),
            "read_ms": self._read_ms.snapshot(),
            "transfer_ms": self._transfer_ms.snapshot(),
            "samples": len(self._transfer_ms),
        }

    # -- requests ---------------------------------------------------------------

    def submit(self, request: dict[str, Any]) -> StreamFuture:
        """Send one request now; the reply arrives on the returned future."""
        waiter = StreamFuture()
        with self._lock:
            if self._process is None or self._process.poll() is not None:
                self._process = self._start()
            request_id = self._next_id
            self._next_id += 1
            waiter.request_id = request_id
            self._pending[request_id] = waiter
            process = self._process
            self._requests += 1
        body: dict[str, Any] = {**request, "id": request_id}
        body.setdefault("protocol", PROTOCOL)
        line = json.dumps(body, separators=(",", ":")).encode("utf-8")
        waiter.written_at = time.perf_counter()
        try:
            assert process.stdin is not None
            process.stdin.write(line + b"\n")
            process.stdin.flush()
        except OSError as exc:
            with self._lock:
                self._pending.pop(request_id, None)
            raise StreamUnavailable(f"could not reach the streamer: {exc}") from exc
        return waiter

    def cancel(self, request_id: int) -> bool:
        """Best effort cancel: the server skips a queued read or drops its reply.

        The future is marked cancelled, so the caller stops waiting. Unknown or
        already answered ids are harmless (False).
        """
        with self._lock:
            waiter = self._pending.pop(request_id, None)
            process = self._process
        if waiter is None:
            return False
        waiter.cancel()
        with self._lock:
            self._cancelled += 1
        if process is not None and process.stdin is not None:
            line = json.dumps({"op": "cancel", "id": request_id}, separators=(",", ":")).encode()
            try:
                process.stdin.write(line + b"\n")
                process.stdin.flush()
            except OSError:
                pass
        return True

    def token(self) -> CancelToken:
        """A token for cancelling a read from another thread (a disconnected browser)."""
        return CancelToken(self)

    def call(
        self, request: dict[str, Any], *, token: CancelToken | None = None
    ) -> tuple[dict[str, Any], bytes]:
        try:
            waiter = self.submit(request)
            if token is not None:
                token.attach(waiter)
            header, payload = waiter.result(timeout=self._timeout)
        except CancelledError as exc:
            raise StreamCancelled("the stream read was cancelled") from exc
        except TimeoutError as exc:
            raise StreamUnavailable("the streamer did not answer in time") from exc
        if not header.get("ok"):
            raise StreamRefused(
                str(header.get("error") or "refused"), missing=bool(header.get("missing"))
            )
        return header, payload

    def read(
        self,
        path: str,
        offset: int = 0,
        length: int | None = None,
        *,
        token: CancelToken | None = None,
    ) -> bytes:
        request = {"op": "read", "path": path, "offset": offset, "length": length}
        return self.call(request, token=token)[1]

    def stat(self, path: str) -> dict[str, Any]:
        return self.call({"op": "stat", "path": path})[0]

    def ready_scenes(self) -> list[dict[str, Any]]:
        scenes: object = self.call({"op": "ready_scenes"})[0].get("scenes")
        return cast(list[dict[str, Any]], scenes) if isinstance(scenes, list) else []


def _optional_float(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)
