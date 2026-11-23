# SPDX-License-Identifier: Apache-2.0
"""The viewer job's lease is renewed while the served viewer is in use, not when idle.

`scripts/desktop/remote_serve.sbatch`'s watchdog ends the job once the lease
file's mtime is COPERCEIVE_HEARTBEAT_TIMEOUT seconds old. Every test here runs on a
fake clock: the lease's mtime is set to the fake time, and the watchdog's rule
(`AGE = now - mtime >= timeout`) is applied to it.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from coperceive.app.lease import RENEW_EVERY_S, ViewerLease, renews
from coperceive.app.server import create_app
from fastapi.testclient import TestClient

TOKEN = "fake-token-0000"
BEARER = {"Authorization": f"Bearer {TOKEN}"}
TIMEOUT_S = 600  # remote_serve.sbatch's default COPERCEIVE_HEARTBEAT_TIMEOUT
START = 1_790_000_000.0


class FakeClock:
    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> float:
        return self.now


def _lease_file(tmp_path: Path) -> Path:
    # As the job creates it: touched at start, 0600.
    path = tmp_path / "sessions" / "55bef61ccf2f60b8.heartbeat"
    path.parent.mkdir(parents=True)
    path.touch(mode=0o600)
    os.utime(path, (START, START))
    return path


def _expired(path: Path, now: float) -> bool:
    """The watchdog's check (remote_serve.sbatch): the age of the file's mtime."""
    return int(now) - int(path.stat().st_mtime) >= TIMEOUT_S


def test_a_renewal_touches_the_lease_at_most_every_ten_seconds(tmp_path: Path) -> None:
    path = _lease_file(tmp_path)
    clock = FakeClock()
    lease = ViewerLease(path, clock=clock)
    clock.now += 1
    assert lease.renew()
    assert path.stat().st_mtime == START + 1
    clock.now += RENEW_EVERY_S - 1
    assert not lease.renew(), "renewed again within RENEW_EVERY_S"
    assert path.stat().st_mtime == START + 1
    clock.now += 1
    assert lease.renew()
    assert path.stat().st_mtime == START + 1 + RENEW_EVERY_S
    assert lease.last_renewed == clock.now


def test_a_removed_lease_is_never_recreated(tmp_path: Path) -> None:
    # remote stop and the watchdog remove the file to stop the job.
    path = _lease_file(tmp_path)
    path.unlink()
    lease = ViewerLease(path, clock=FakeClock())
    assert not lease.renew()
    assert not path.exists()


@pytest.mark.parametrize(
    ("path", "counts"),
    [
        ("/api/scenes", True),
        ("/api/scenes/s/points", True),
        ("/api", True),
        ("/api/health", False),
        ("/", False),
        ("/assets/index.js", False),
        ("/apix", False),
    ],
)
def test_which_requests_count_as_use(path: str, counts: bool) -> None:
    assert renews(path) is counts


def _client(tmp_path: Path, lease: ViewerLease) -> TestClient:
    root = tmp_path / "root"
    root.mkdir()
    return TestClient(create_app(root=root, token=TOKEN, lease=lease))


def test_the_served_viewer_renews_on_authenticated_api_requests_only(tmp_path: Path) -> None:
    path = _lease_file(tmp_path)
    clock = FakeClock()
    client = _client(tmp_path, ViewerLease(path, clock=clock))

    clock.now += 100
    assert client.get("/api/health", headers=BEARER).status_code == 200
    assert path.stat().st_mtime == START, "a remote status probe kept the job alive"
    assert client.get("/api/scenes").status_code == 401
    assert path.stat().st_mtime == START, "an unauthenticated request renewed the lease"
    client.get("/")
    client.options("/api/scenes")
    assert path.stat().st_mtime == START

    assert client.get("/api/scenes", headers=BEARER).status_code == 200
    assert path.stat().st_mtime == START + 100


def test_a_viewer_in_use_outlives_the_lease_and_an_idle_one_ends_after_it(tmp_path: Path) -> None:
    path = _lease_file(tmp_path)
    clock = FakeClock()
    client = _client(tmp_path, ViewerLease(path, clock=clock))

    # In use for an hour, a request a minute: never expired, whatever the start time.
    for _ in range(60):
        clock.now += 60
        assert client.get("/api/scenes", headers=BEARER).status_code == 200
        assert not _expired(path, clock.now)
    last_request = clock.now

    # Then the tab is closed (or only status checks arrive): the lease runs out.
    clock.now = last_request + TIMEOUT_S - 1
    assert client.get("/api/health", headers=BEARER).status_code == 200
    assert not _expired(path, clock.now)
    clock.now = last_request + TIMEOUT_S
    assert _expired(path, clock.now), "an idle viewer job kept its lease"
