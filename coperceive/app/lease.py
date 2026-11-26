# SPDX-License-Identifier: Apache-2.0
"""The viewer job's lease, renewed while the server is in use.

``coperceive remote start`` runs ``scripts/desktop/remote_serve.sbatch``. Its
watchdog ends the job once the lease file's mtime is ``COPERCEIVE_HEARTBEAT_TIMEOUT``
seconds old (600 by default; 0 disables the watchdog) or the file is gone. Before
v0.1.2 nothing renewed the lease except a manual ``coperceive remote heartbeat``,
so a viewer job stopped about 600 s after it started, whatever ``--time`` said.

The served viewer now renews it itself (``coperceive serve --heartbeat-file``):
every authenticated ``/api`` request touches the file, at most once every
:data:`RENEW_EVERY_S` seconds. ``/api/health`` does not count: ``remote status``
probes it from the login node, and a status check must not keep a job alive.
An idle server renews nothing, so an abandoned job still ends one lease after its
last request. A missing file is never recreated: ``remote stop`` and the watchdog
remove it to stop the job.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from pathlib import Path

RENEW_EVERY_S = 10.0
"""The most often the lease is touched. The watchdog checks every 5 s, and the
default lease is 600 s; a lease below about 30 s can expire between renewals."""

_NOT_RENEWED = ("/api/health",)


class ViewerLease:
    """Touches ``path`` (its mtime, which is all the watchdog reads), at most every
    ``every_s`` seconds of ``clock``."""

    def __init__(
        self,
        path: str | Path,
        *,
        every_s: float = RENEW_EVERY_S,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.path = Path(path)
        self.every_s = every_s
        self._clock = clock
        self._last: float | None = None

    @property
    def last_renewed(self) -> float | None:
        return self._last

    def renew(self) -> bool:
        """Renew now unless renewed less than ``every_s`` ago; True when touched."""
        now = self._clock()
        if self._last is not None and now - self._last < self.every_s:
            return False
        try:
            # utime, never open/create: a removed lease means "stop", not "renew".
            os.utime(self.path, (now, now))
        except OSError:
            return False
        self._last = now
        return True


def renews(path: str) -> bool:
    """Whether a request on route ``path`` counts as use of the viewer."""
    return (path == "/api" or path.startswith("/api/")) and path not in _NOT_RENEWED
