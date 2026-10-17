# SPDX-License-Identifier: Apache-2.0
"""Subprocess jobs. The argv allow-list is the only way to start a process.

``shell`` is never true. Arguments are passed as a list.
"""

from __future__ import annotations

import sqlite3
import subprocess
import time
from collections.abc import Sequence
from pathlib import Path

# Phase 0 allow-list. Add names here on purpose; do not accept a free-form command.
ALLOW: dict[str, tuple[str, ...]] = {"echo": ("echo",)}

CSRF_HEADER = "x-coperceive-request"


def open_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
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
    return conn


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
