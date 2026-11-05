# SPDX-License-Identifier: Apache-2.0
"""One persistent connection to ``coperceive dataset stream``, with pipelining.

The connection is a single long-lived process, normally
``ssh <host> <quoted coperceive argv> dataset stream --root …``: no process per
file. Requests are written as they come, each with an id; a reader thread
routes every reply to its waiting caller, so several reads are in flight at
once and the round trips overlap (what keeps playback fed over 20–40 ms RTT).
Paths travel only as JSON on stdin, never through a shell.
"""

from __future__ import annotations

import json
import subprocess
import threading
from collections.abc import Sequence
from concurrent.futures import Future
from typing import Any, cast


class StreamUnavailable(OSError):
    """The streamer process is gone or did not answer."""


class StreamRefused(ValueError):
    """The streamer refused the request (outside the dataset, not found)."""

    def __init__(self, message: str, *, missing: bool = False) -> None:
        super().__init__(message)
        self.missing = missing


class StreamClient:
    def __init__(self, argv: Sequence[str], *, timeout_s: float = 60.0) -> None:
        if not argv:
            raise ValueError("the streamer command is empty")
        self._argv = list(argv)
        self._timeout = timeout_s
        self._lock = threading.Lock()
        self._pending: dict[int, Future[tuple[dict[str, Any], bytes]]] = {}
        self._next_id = 1
        self._process: subprocess.Popen[bytes] | None = None
        self.requests = 0
        self.bytes_read = 0

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
                with self._lock:
                    waiter = self._pending.pop(int(header.get("id") or 0), None)
                if waiter is not None:
                    waiter.set_result((header, payload))
        except (OSError, ValueError) as exc:
            self._fail_all(StreamUnavailable(f"the streamer connection broke: {exc}"))
            return
        self._fail_all(StreamUnavailable("the streamer connection closed"))

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

    # -- requests ---------------------------------------------------------------

    def submit(self, request: dict[str, Any]) -> Future[tuple[dict[str, Any], bytes]]:
        """Send one request now; the reply arrives on the returned future."""
        waiter: Future[tuple[dict[str, Any], bytes]] = Future()
        with self._lock:
            if self._process is None or self._process.poll() is not None:
                self._process = self._start()
            request_id = self._next_id
            self._next_id += 1
            self._pending[request_id] = waiter
            process = self._process
            self.requests += 1
        line = json.dumps({**request, "id": request_id}, separators=(",", ":")).encode("utf-8")
        try:
            assert process.stdin is not None
            process.stdin.write(line + b"\n")
            process.stdin.flush()
        except OSError as exc:
            with self._lock:
                self._pending.pop(request_id, None)
            raise StreamUnavailable(f"could not reach the streamer: {exc}") from exc
        return waiter

    def call(self, request: dict[str, Any]) -> tuple[dict[str, Any], bytes]:
        try:
            header, payload = self.submit(request).result(timeout=self._timeout)
        except TimeoutError as exc:
            raise StreamUnavailable("the streamer did not answer in time") from exc
        if not header.get("ok"):
            raise StreamRefused(
                str(header.get("error") or "refused"), missing=bool(header.get("missing"))
            )
        self.bytes_read += len(payload)
        return header, payload

    def read(self, path: str, offset: int = 0, length: int | None = None) -> bytes:
        return self.call({"op": "read", "path": path, "offset": offset, "length": length})[1]

    def stat(self, path: str) -> dict[str, Any]:
        return self.call({"op": "stat", "path": path})[0]

    def ready_scenes(self) -> list[dict[str, Any]]:
        scenes: object = self.call({"op": "ready_scenes"})[0].get("scenes")
        return cast(list[dict[str, Any]], scenes) if isinstance(scenes, list) else []
