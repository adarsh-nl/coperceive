# SPDX-License-Identifier: Apache-2.0
"""Subprocess jobs. The argv allow-list is the only way to start a process.

``shell`` is never true. Arguments are passed as a list.
"""

from __future__ import annotations

import os
import sqlite3
import stat
import subprocess
import time
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType

# Phase 0 allow-list. Add names here on purpose; do not accept a free-form command.
ALLOW: dict[str, tuple[str, ...]] = {"echo": ("echo",)}

CSRF_HEADER = "x-coperceive-request"

# Files SQLite opens next to the database (rollback journal, WAL and its index).
_DB_COMPANIONS = ("-journal", "-wal", "-shm")

# ``dir_fd`` pins the folder between the checks; Windows has no ``dir_fd``.
_DIR_FD = os.open in os.supports_dir_fd and os.stat in os.supports_dir_fd


class JobsDatabaseError(ValueError):
    """The jobs database cannot be used safely (a link, not a regular file)."""


class _Folder:
    """The folder that holds the jobs database, opened once.

    Every name is looked up relative to the same directory descriptor, so the
    checks below all see one folder even if a path component is renamed.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.fd: int | None = None
        if _DIR_FD:
            flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
            try:
                self.fd = os.open(path, flags)
            except OSError as exc:
                raise JobsDatabaseError(
                    f"cannot open the folder of the jobs database: {exc.strerror}"
                ) from exc

    def close(self) -> None:
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def lstat(self, name: str) -> os.stat_result:
        if self.fd is not None:
            return os.stat(name, dir_fd=self.fd, follow_symlinks=False)
        return (self.path / name).lstat()

    def create(self, name: str) -> os.stat_result:
        """Create ``name`` as a new, empty file: never through a link, never an existing name."""
        flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        flags |= getattr(os, "O_CLOEXEC", 0)
        if self.fd is not None:
            fd = os.open(name, flags, 0o644, dir_fd=self.fd)
        else:
            fd = os.open(self.path / name, flags, 0o644)
        try:
            return os.fstat(fd)
        finally:
            os.close(fd)

    def same_folder(self) -> bool:
        if self.fd is None:
            return True
        here, opened = os.stat(self.path), os.fstat(self.fd)
        return (here.st_dev, here.st_ino) == (opened.st_dev, opened.st_ino)


def _require_plain_file(name: str, info: os.stat_result) -> None:
    if stat.S_ISLNK(info.st_mode):
        raise JobsDatabaseError(f"refusing the jobs database: {name} is a symbolic link")
    if not stat.S_ISREG(info.st_mode):
        raise JobsDatabaseError(f"refusing the jobs database: {name} is not a regular file")
    if info.st_nlink > 1:
        raise JobsDatabaseError(
            f"refusing the jobs database: {name} is a hard link (it has other names)"
        )


def _check_companions(folder: _Folder, name: str) -> None:
    for suffix in _DB_COMPANIONS:
        companion = name + suffix
        try:
            info = folder.lstat(companion)
        except FileNotFoundError:
            continue
        _require_plain_file(companion, info)


def _existing_or_new(folder: _Folder, name: str) -> os.stat_result:
    """The ``lstat`` of the database file, creating it (``O_EXCL | O_NOFOLLOW``) if missing.

    An existing database is checked with ``lstat`` only, not opened: closing
    any descriptor of the file would drop the POSIX locks SQLite holds on it
    for other connections of this process.
    """
    try:
        info = folder.lstat(name)
    except FileNotFoundError:
        try:
            info = folder.create(name)
        except FileExistsError:
            info = folder.lstat(name)
        except OSError as exc:
            raise JobsDatabaseError(f"cannot create the jobs database: {exc.strerror}") from exc
    _require_plain_file(name, info)
    return info


def same_path(a: str, b: str, path_module: ModuleType = os.path) -> bool:
    """True when ``a`` and ``b`` spell one path. Windows can report one file with another
    case or separator (SQLite's ``database_list`` against ``os.path.realpath``), so both
    are normalised the platform's way; no filesystem lookup is made."""
    norm = path_module.normcase
    return norm(path_module.normpath(a)) == norm(path_module.normpath(b))


def _main_file(conn: sqlite3.Connection) -> str:
    for _seq, schema, filename in conn.execute("PRAGMA database_list").fetchall():
        if schema == "main":
            return str(filename)
    return ""


def open_db(path: Path) -> sqlite3.Connection:
    """Open (creating it if needed) the jobs database at ``path``.

    ``path`` sits in the folder the server was opened on, which may come from
    someone else, possibly while the server runs. SQLite follows symbolic links
    and hard links write through to their other names, so a planted
    ``jobs.sqlite -> elsewhere`` (or a link for its journal) would make the
    server create or write a file outside that folder.

    The checks: no companion file (journal, WAL, shared memory) may be a link
    or anything but a regular file (a FIFO would hang the open); the database
    is a regular file with one name, created here with ``O_EXCL | O_NOFOLLOW``
    when missing. SQLite then opens it with ``mode=rw``, so it never creates a
    file itself (a link swapped in after the check cannot make it create one
    elsewhere), and before anything is written the file SQLite opened must be
    the one checked: its resolved name is ``path`` and ``path`` still has the
    checked device and inode, with no other names.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    name = path.name
    folder = _Folder(path.parent)
    try:
        try:
            _check_companions(folder, name)
            checked = _existing_or_new(folder, name)
        except JobsDatabaseError:
            raise
        except OSError as exc:  # e.g. the file vanished between two checks
            raise JobsDatabaseError(f"cannot open the jobs database: {exc.strerror}") from exc
        expected = Path(os.path.realpath(path.parent)) / name
        try:
            conn = sqlite3.connect(f"{expected.as_uri()}?mode=rw", uri=True)
        except sqlite3.Error as exc:
            raise JobsDatabaseError(f"cannot open the jobs database: {exc}") from exc
        try:
            try:
                opened = _main_file(conn)
                now = folder.lstat(name)
                _require_plain_file(name, now)
                if (
                    not same_path(opened, str(expected))
                    or (now.st_dev, now.st_ino) != (checked.st_dev, checked.st_ino)
                    or not folder.same_folder()
                ):
                    raise JobsDatabaseError(
                        f"refusing the jobs database: {name} changed while it was being opened"
                    )
                _check_companions(folder, name)
            except (sqlite3.Error, OSError) as exc:
                raise JobsDatabaseError(f"cannot open the jobs database: {exc}") from exc
            _create_table(conn)
        except BaseException:
            conn.close()
            raise
        return conn
    finally:
        folder.close()


def _create_table(conn: sqlite3.Connection) -> None:
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                argv TEXT NOT NULL,
                status TEXT NOT NULL,
                output TEXT NOT NULL,
                created_us INTEGER NOT NULL
            )
            """
        )
    except sqlite3.Error as exc:
        raise JobsDatabaseError(f"cannot use the jobs database: {exc}") from exc


def confine(root: Path, relative: str) -> Path:
    """Resolve ``relative`` and require it to stay under ``root``."""
    root_resolved = root.resolve()
    candidate = (root_resolved / relative).resolve()
    if not candidate.is_relative_to(root_resolved):
        raise ValueError(f"path escapes the server root: {relative}")
    return candidate


def confine_media(root: Path, relative: str, media_roots: Sequence[Path]) -> Path:
    """``confine``, but a symlink may also land inside one of ``media_roots``.

    A staged scene links its camera directories to the dataset copy instead of
    duplicating the images. ``relative`` still has to be a plain relative path
    (no ``..``, not absolute): the media roots widen where a link may point,
    not what a recorded path may say.
    """
    parts = Path(relative).parts
    if relative == "" or Path(relative).is_absolute() or ".." in parts:
        raise ValueError(f"path escapes the server root: {relative}")
    try:
        return confine(root, relative)
    except ValueError:
        candidate = (root.resolve() / relative).resolve()
        for media in media_roots:
            if candidate.is_relative_to(media.resolve()):
                return candidate
        raise


def argv_for(name: str, args: list[str]) -> list[str]:
    if name not in ALLOW:
        raise ValueError(f"command {name!r} is not allow-listed")
    if any("\x00" in arg for arg in args):
        raise ValueError("arguments must not contain NUL")
    return [*ALLOW[name], *args]


def run_job(conn: sqlite3.Connection, root: Path, name: str, args: list[str]) -> int:
    argv = argv_for(name, args)
    created = time.time_ns() // 1000
    cursor = conn.execute(
        "INSERT INTO jobs (name, argv, status, output, created_us) VALUES (?, ?, ?, ?, ?)",
        (name, " ".join(argv), "running", "", created),
    )
    job_id = int(cursor.lastrowid)
    conn.commit()
    completed = subprocess.run(
        argv,
        shell=False,
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    output = completed.stdout + completed.stderr
    status = "ok" if completed.returncode == 0 else "error"
    conn.execute("UPDATE jobs SET status = ?, output = ? WHERE id = ?", (status, output, job_id))
    conn.commit()
    return job_id


def list_jobs(conn: sqlite3.Connection) -> list[dict[str, object]]:
    rows = conn.execute(
        "SELECT id, name, argv, status, output, created_us FROM jobs ORDER BY id"
    ).fetchall()
    return [
        {
            "id": row[0],
            "name": row[1],
            "argv": row[2],
            "status": row[3],
            "output": row[4],
            "created_us": row[5],
        }
        for row in rows
    ]
