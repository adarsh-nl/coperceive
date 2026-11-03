# SPDX-License-Identifier: Apache-2.0
"""A network link stand-in for ``ssh``: ``link.py RTT_MS MBPS -- argv...``.

Runs ``argv`` (the streamer) and relays stdin and stdout with half the round trip
in each direction, scheduled per message so requests already in flight are not
serialised, and the reply stream throttled to MBPS megabytes per second.
"""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import time
from typing import IO


def main() -> None:
    rtt = float(sys.argv[1]) / 1000.0
    rate = float(sys.argv[2]) * 1024 * 1024
    argv = sys.argv[sys.argv.index("--") + 1 :]
    child = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, bufsize=0)
    assert child.stdin and child.stdout
    stdin: IO[bytes] = child.stdin
    stdout: IO[bytes] = child.stdout

    def upstream() -> None:
        for line in sys.stdin.buffer:
            due = time.monotonic() + rtt / 2
            threading.Timer(max(0.0, due - time.monotonic()), _send, (stdin, line)).start()
        time.sleep(rtt / 2 + 0.05)
        stdin.close()

    threading.Thread(target=upstream, daemon=True).start()
    out = sys.stdout.buffer
    arrivals: queue.Queue[tuple[float, bytes] | None] = queue.Queue()

    def downstream_reader() -> None:
        # Stamp each chunk when the streamer wrote it, independent of delivery.
        while True:
            chunk = os.read(stdout.fileno(), 65536)
            arrivals.put((time.monotonic(), chunk) if chunk else None)
            if not chunk:
                return

    threading.Thread(target=downstream_reader, daemon=True).start()
    link_free = time.monotonic()
    while True:
        item = arrivals.get()
        if item is None:
            break
        arrived, chunk = item
        # Bandwidth: the chunk occupies the link after the ones before it;
        # latency: it lands half a round trip after it left.
        link_free = max(link_free, arrived) + len(chunk) / rate
        wait = link_free + rtt / 2 - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        out.write(chunk)
        out.flush()
    child.wait()


_lock = threading.Lock()


def _send(stream: IO[bytes], line: bytes) -> None:
    with _lock:
        stream.write(line)
        stream.flush()


if __name__ == "__main__":
    main()
