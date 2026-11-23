# SPDX-License-Identifier: Apache-2.0
"""Bounded timing samples and percentiles for the remote-view stream.

Shared by the streamer (server-side queue wait and read time), the client
(transfer time and the server's timings) and the app's viewer server (per HTTP
request). A ring of the last ``capacity`` samples keeps memory bounded when
viewing runs for hours; counters are exact. All operations are thread-safe.
"""

from __future__ import annotations

import math
import threading
from collections import deque
from collections.abc import Sequence

DEFAULT_CAPACITY = 512


def percentile(ordered: Sequence[float], fraction: float) -> float:
    """A linearly interpolated percentile of an already sorted sequence."""
    if not ordered:
        return 0.0
    clamped = min(1.0, max(0.0, fraction))
    position = (len(ordered) - 1) * clamped
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _rounded(value: float) -> float:
    return round(value, 1)


class SampleStats:
    """The last ``capacity`` samples, thread-safe, plus an exact count and total."""

    def __init__(self, capacity: int = DEFAULT_CAPACITY) -> None:
        if capacity < 1:
            raise ValueError("capacity must be at least one sample")
        self.capacity = capacity
        self._samples: deque[float] = deque(maxlen=capacity)
        self._lock = threading.Lock()
        self.count = 0
        self.total = 0.0

    def add(self, value: float) -> None:
        with self._lock:
            self._samples.append(float(value))
            self.count += 1
            self.total += float(value)

    def __len__(self) -> int:
        with self._lock:
            return len(self._samples)

    def snapshot(self) -> dict[str, float | int]:
        """``{"count", "p50", "p95", "max"}`` from the samples currently held."""
        with self._lock:
            values = sorted(self._samples)
        if not values:
            return {"count": 0, "p50": 0.0, "p95": 0.0, "max": 0.0}
        return {
            "count": len(values),
            "p50": _rounded(percentile(values, 0.5)),
            "p95": _rounded(percentile(values, 0.95)),
            "max": _rounded(values[-1]),
        }

    def mean(self) -> float:
        with self._lock:
            if self.count == 0:
                return 0.0
            return self.total / self.count
