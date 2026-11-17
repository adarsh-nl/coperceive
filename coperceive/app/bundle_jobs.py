# SPDX-License-Identifier: Apache-2.0
"""The private folder that holds a served bundle's ``jobs.sqlite``.

A bundle is read-only, so its viewer jobs database lives in
``<tmp>/coperceive-bundle-jobs-<random>/``. The serving process creates
``owner.lock`` in it and holds an exclusive ``flock`` on it for as long as it
runs, and removes the folder when the server shuts down.

A process that died without shutting down leaves its folder behind. The next
bundle open or serve removes such a folder only when it can prove it is one of
ours and stale:

- its name is exactly the prefix plus ``mkdtemp``'s random suffix, directly in
  the temp directory;
- ``lstat`` shows a real folder (never a link) owned by this user, last changed
  before this process started;
- its ``owner.lock`` is a regular file (opened without following a link) that
  no process holds: our own ``flock`` on it succeeds.

Anything that fails a check is kept. Removal uses ``shutil.rmtree``, which
follows no link inside the folder, and is skipped where it cannot promise that.
Without ``fcntl`` (Windows) nothing is swept: a folder in use cannot be told
from a stale one there.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore[assignment]

PREFIX = "coperceive-bundle-jobs-"
LOCK = "owner.lock"
_NAME = re.compile(re.escape(PREFIX) + r"[a-z0-9_]+")
_STARTED = time.time()


@dataclass
class JobsFolder:
    """A created folder and the descriptor that holds its lock."""

    path: Path
    _lock: int | None

    @property
    def database(self) -> Path:
        return self.path / "jobs.sqlite"

    def remove(self) -> None:
        """Remove the folder (at shutdown). Safe to call more than once."""
        if self._lock is not None:
            os.close(self._lock)
            self._lock = None
        _remove_folder(self.path)


def create() -> JobsFolder:
    """Sweep stale folders, then make and lock a new one."""
    sweep_stale()
    path = Path(tempfile.mkdtemp(prefix=PREFIX))
    flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    lock = os.open(path / LOCK, flags, 0o600)
    if fcntl is not None:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    os.write(lock, f"{os.getpid()}\n".encode())
    return JobsFolder(path, lock)


def sweep_stale() -> list[Path]:
    """Remove the stale jobs folders this user left behind; return what was removed."""
    if fcntl is None or not shutil.rmtree.avoids_symlink_attacks:
        return []
    base = Path(tempfile.gettempdir())
    removed: list[Path] = []
    try:
        entries = list(os.scandir(base))
    except OSError:
        return removed
    for entry in entries:
        if _NAME.fullmatch(entry.name) is None:
            continue
        path = base / entry.name
        if _stale(path):
            removed.append(path)
    return removed


def _stale(path: Path) -> bool:
    """True when ``path`` was proven ours and stale, and has been removed."""
    try:
        info = os.lstat(path)
    except OSError:
        return False
    if not stat.S_ISDIR(info.st_mode):  # a link or a file is never ours to remove
        return False
    if hasattr(os, "getuid") and info.st_uid != os.getuid():
        return False
    if info.st_mtime >= _STARTED:
        return False
    flags = os.O_RDWR | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        lock = os.open(path / LOCK, flags)
    except OSError:  # missing, a link, or unreadable: not proven ours
        return False
    try:
        if not stat.S_ISREG(os.fstat(lock).st_mode):
            return False
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)  # type: ignore[union-attr]
        except OSError:  # its owner still holds it
            return False
        _remove_folder(path)
        return True
    finally:
        os.close(lock)


def _remove_folder(path: Path) -> None:
    try:
        if stat.S_ISDIR(os.lstat(path).st_mode):
            shutil.rmtree(path)
    except OSError:
        pass
