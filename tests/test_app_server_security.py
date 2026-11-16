# SPDX-License-Identifier: Apache-2.0
"""Security regressions for ``coperceive serve`` (ADR 0019, group app-server).

FOLD-02: the bearer token and CSRF header apply to every ``/api`` route whatever
``--root-path`` is. FOLD-06: token-less serve answers only a loopback Host on its
own port. FOLD-08: ``jobs.sqlite`` is never opened through a symbolic link.
FOLD-09: the Rerun export name passes the same check as every other export.
And ``--token-file`` must be a regular file the current user owns.
"""

from __future__ import annotations

import contextlib
import os
import socket
import sqlite3
import threading
import time
from collections.abc import Generator
from pathlib import Path
from typing import Any

import httpx
import pytest
import uvicorn
from coperceive.app import jobs, serve
from coperceive.app.server import create_app
from coperceive.cli import build_parser, main
from coperceive.model import SCHEMA_VERSION, Agent, AgentType, Clock, Scene, StampedPose
from coperceive.se3 import SE3
from coperceive.store import write_scene
from fastapi import FastAPI
from fastapi.testclient import TestClient

PREFIX = "/user/alice/proxy/8731"
TOKEN = "fake-token-0000"
BEARER = {"Authorization": f"Bearer {TOKEN}"}
CSRF = {"X-Coperceive-Request": "1"}
NOTES = "private research notes\n"


def _scene(root: Path, directory: str, scene_id: str) -> None:
    pose = StampedPose(0, SE3.identity())
    scene = Scene(
        schema_version=SCHEMA_VERSION,
        scene_id=scene_id,
        world_frame="ENU",
        clock=Clock("us", "app-server security fixture"),
        agents=(Agent("vehicle", AgentType.VEHICLE, (pose,)),),
        sensors=(),
        annotations=(),
        objects=(),
    )
    write_scene(scene, root / directory)
    (root / directory / "COMPLETE.json").write_text("{}\n", encoding="utf-8")


def _root_with_notes(tmp_path: Path) -> Path:
    root = tmp_path / "root"
    _scene(root, "desk", "desk")
    (root / "notes.txt").write_text(NOTES, encoding="utf-8")
    return root


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _capture_serve(
    monkeypatch: pytest.MonkeyPatch, argv: list[str] | None = None, **run: Any
) -> tuple[FastAPI, dict[str, Any]]:
    """The app and uvicorn options ``coperceive serve`` would run, without serving."""
    captured: dict[str, Any] = {}

    def fake_run(app: FastAPI, **kwargs: Any) -> None:
        captured["app"] = app
        captured["kwargs"] = kwargs

    monkeypatch.setattr(serve.uvicorn, "run", fake_run)
    if argv is not None:
        main(argv)
    else:
        serve.run(**run)
    return captured["app"], captured["kwargs"]


@contextlib.contextmanager
def _uvicorn(app: FastAPI, options: dict[str, Any]) -> Generator[str]:
    """Run the real uvicorn with ``options`` (what ``serve.run`` passes) on a thread."""
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning", **options))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.02)
    assert server.started, "uvicorn did not start"
    try:
        yield f"http://127.0.0.1:{options['port']}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)


# FOLD-02 ------------------------------------------------------------------------


def test_root_path_keeps_the_token_and_csrf_on_real_uvicorn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """uvicorn puts --root-path in front of scope["path"]; the guard must still run."""
    root = _root_with_notes(tmp_path)
    port = _free_port()
    app, options = _capture_serve(
        monkeypatch,
        host="127.0.0.1",
        port=port,
        root=str(root),
        token=TOKEN,
        root_path=PREFIX,
    )
    assert options["root_path"] == PREFIX
    with _uvicorn(app, options) as base, httpx.Client(base_url=base, timeout=10) as http:
        # What a hub proxy forwards after stripping its prefix.
        read = http.get("/api/files", params={"path": "notes.txt"})
        assert read.status_code == 401, read.text
        assert http.get("/api/health").status_code == 401
        job = http.post("/api/jobs", json={"name": "echo", "args": ["x"]})
        assert job.status_code == 401
        no_csrf = http.post("/api/jobs", headers=BEARER, json={"name": "echo", "args": ["x"]})
        assert no_csrf.status_code == 403
        allowed = http.get("/api/files", params={"path": "notes.txt"}, headers=BEARER)
        assert allowed.status_code == 200
        assert allowed.text == NOTES
        posted = http.post(
            "/api/jobs", headers={**BEARER, **CSRF}, json={"name": "echo", "args": ["x"]}
        )
        assert posted.status_code == 200
        # The page itself still loads without a token (the browser cannot send one).
        assert http.get("/").status_code == 200


def test_root_path_scope_as_uvicorn_builds_it_is_guarded(tmp_path: Path) -> None:
    """The same scope through TestClient: path = root_path + route, root_path set."""
    root = _root_with_notes(tmp_path)
    with_token = TestClient(create_app(root=root, token=TOKEN, root_path=PREFIX), root_path=PREFIX)
    assert with_token.get(f"{PREFIX}/api/files", params={"path": "notes.txt"}).status_code == 401
    assert with_token.get(f"{PREFIX}/api/scenes").status_code == 401
    assert with_token.get(f"{PREFIX}/api/scenes", headers=BEARER).status_code == 200
    anonymous = TestClient(create_app(root=root, token=None, root_path=PREFIX), root_path=PREFIX)
    no_csrf = anonymous.post(f"{PREFIX}/api/jobs", json={"name": "echo", "args": ["x"]})
    assert no_csrf.status_code == 403
    assert not (root / "jobs.sqlite").exists()


# FOLD-06 ------------------------------------------------------------------------


def _tokenless_serve(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, port: int = 8731
) -> tuple[Path, FastAPI]:
    monkeypatch.delenv("COPERCEIVE_TOKEN", raising=False)
    root = _root_with_notes(tmp_path)
    app, _ = _capture_serve(monkeypatch, ["serve", "--root", str(root), "--port", str(port)])
    return root, app


def test_tokenless_serve_refuses_a_foreign_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A DNS-rebinding page is same-origin with the server but keeps its own Host."""
    root, app = _tokenless_serve(tmp_path, monkeypatch)
    rebound = TestClient(app, base_url="http://rebind.attacker.example:8731")
    read = rebound.get("/api/files", params={"path": "notes.txt"})
    assert read.status_code == 400, read.text
    assert NOTES not in read.text
    job = rebound.post("/api/jobs", headers=CSRF, json={"name": "echo", "args": ["x"]})
    assert job.status_code == 400
    assert not (root / "jobs.sqlite").exists()
    for base_url in (
        "http://127.0.0.1:8732",  # another port
        "http://127.0.0.1",  # no port: 80
        "http://localhost.attacker.example:8731",
        "http://127.0.0.1.nip.io:8731",
    ):
        response = TestClient(app, base_url=base_url).get("/api/health")
        assert response.status_code == 400, base_url
    spoofed = TestClient(app, base_url="http://127.0.0.1:8731").get(
        "/api/health", headers={"Host": "rebind.attacker.example:8731"}
    )
    assert spoofed.status_code == 400


def test_tokenless_serve_answers_loopback_hosts_on_its_port(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, app = _tokenless_serve(tmp_path, monkeypatch)
    for base_url in (
        "http://127.0.0.1:8731",
        "http://localhost:8731",
        "http://[::1]:8731",
        "http://LOCALHOST:8731",
    ):
        client = TestClient(app, base_url=base_url)
        read = client.get("/api/files", params={"path": "notes.txt"})
        assert read.status_code == 200, base_url
        assert read.text == NOTES
        assert client.get("/").status_code == 200
        job = client.post("/api/jobs", headers=CSRF, json={"name": "echo", "args": ["x"]})
        assert job.status_code == 200, base_url


def test_tokenless_serve_on_port_zero_checks_the_listening_port(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("COPERCEIVE_TOKEN", raising=False)
    root = _root_with_notes(tmp_path)
    app, _ = _capture_serve(
        monkeypatch, host="127.0.0.1", port=0, root=str(root), token=None, root_path=""
    )
    # TestClient reports the base URL's host and port as the listening socket.
    assert TestClient(app, base_url="http://127.0.0.1:43210").get("/api/health").status_code == 200
    foreign = TestClient(app, base_url="http://rebind.attacker.example:43210")
    assert foreign.get("/api/health").status_code == 400
    other_port = TestClient(app, base_url="http://127.0.0.1:43210").get(
        "/api/health", headers={"Host": "127.0.0.1:1"}
    )
    assert other_port.status_code == 400


def test_token_serve_keeps_working_behind_a_proxy_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With a token the Host is not checked: a hub proxy forwards its own name."""
    monkeypatch.setenv("COPERCEIVE_TOKEN", TOKEN)
    root = _root_with_notes(tmp_path)
    app, _ = _capture_serve(monkeypatch, ["serve", "--root", str(root), "--port", "8731"])
    hub = TestClient(app, base_url="https://hub.example.org")
    assert hub.get("/api/files", params={"path": "notes.txt"}).status_code == 401
    read = hub.get("/api/files", params={"path": "notes.txt"}, headers=BEARER)
    assert read.status_code == 200
    assert read.text == NOTES


# FOLD-08 ------------------------------------------------------------------------


def test_jobs_database_symlink_is_refused(tmp_path: Path) -> None:
    root = _root_with_notes(tmp_path)
    outside = tmp_path / "outside" / "planted-name.db"
    outside.parent.mkdir()
    (root / "jobs.sqlite").symlink_to(outside)
    client = TestClient(create_app(root=root, token=TOKEN), raise_server_exceptions=False)
    listed = client.get("/api/jobs", headers=BEARER)
    assert listed.status_code == 400, listed.text
    assert "symbolic link" in listed.json()["detail"]
    created = client.post(
        "/api/jobs", headers={**BEARER, **CSRF}, json={"name": "echo", "args": ["x"]}
    )
    assert created.status_code == 400
    events = client.get("/api/events", headers=BEARER)
    assert events.status_code == 400
    assert not outside.exists()
    assert (root / "jobs.sqlite").is_symlink()


def test_jobs_database_journal_symlink_is_refused(tmp_path: Path) -> None:
    root = _root_with_notes(tmp_path)
    outside = tmp_path / "outside" / "victim"
    outside.parent.mkdir()
    (root / "jobs.sqlite-journal").symlink_to(outside)
    client = TestClient(create_app(root=root, token=TOKEN), raise_server_exceptions=False)
    created = client.post(
        "/api/jobs", headers={**BEARER, **CSRF}, json={"name": "echo", "args": ["x"]}
    )
    assert created.status_code == 400, created.text
    assert "symbolic link" in created.json()["detail"]
    assert not outside.exists()


def test_jobs_database_is_a_regular_file_that_persists(tmp_path: Path) -> None:
    root = _root_with_notes(tmp_path)
    first = TestClient(create_app(root=root, token=TOKEN))
    made = first.post("/api/jobs", headers={**BEARER, **CSRF}, json={"name": "echo", "args": ["a"]})
    assert made.status_code == 200
    database = root / "jobs.sqlite"
    assert database.is_file()
    assert not database.is_symlink()
    again = TestClient(create_app(root=root, token=TOKEN))
    jobs = again.get("/api/jobs", headers=BEARER).json()
    assert [job["status"] for job in jobs] == ["ok"]
    assert "a" in jobs[0]["output"]


def _victim_db(path: Path) -> bytes:
    """An SQLite database outside the root that the server must never write."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE victim (x TEXT)")
    conn.execute("INSERT INTO victim VALUES ('untouched')")
    conn.commit()
    conn.close()
    return path.read_bytes()


def _tables(path: Path) -> list[str]:
    conn = sqlite3.connect(path)
    try:
        return sorted(row[0] for row in conn.execute("SELECT name FROM sqlite_master"))
    finally:
        conn.close()


def test_jobs_database_hard_link_is_refused(tmp_path: Path) -> None:
    """A hard link passes lstat and S_ISREG, yet writes land in the outside file."""
    root = _root_with_notes(tmp_path)
    victim = tmp_path / "outside" / "victim.db"
    before = _victim_db(victim)
    os.link(victim, root / "jobs.sqlite")
    client = TestClient(create_app(root=root, token=TOKEN), raise_server_exceptions=False)
    created = client.post(
        "/api/jobs", headers={**BEARER, **CSRF}, json={"name": "echo", "args": ["x"]}
    )
    assert created.status_code == 400, created.text
    assert "link" in created.json()["detail"]
    assert client.get("/api/jobs", headers=BEARER).status_code == 400
    assert victim.read_bytes() == before
    assert _tables(victim) == ["victim"]


@pytest.mark.parametrize("suffix", ["-wal", "-shm", "-journal"])
def test_jobs_database_companion_symlink_is_refused(tmp_path: Path, suffix: str) -> None:
    root = _root_with_notes(tmp_path)
    first = TestClient(create_app(root=root, token=TOKEN))
    made = first.post("/api/jobs", headers={**BEARER, **CSRF}, json={"name": "echo", "args": ["a"]})
    assert made.status_code == 200
    victim = tmp_path / "outside" / "victim.db"
    before = _victim_db(victim)
    (root / f"jobs.sqlite{suffix}").symlink_to(victim)
    client = TestClient(create_app(root=root, token=TOKEN), raise_server_exceptions=False)
    created = client.post(
        "/api/jobs", headers={**BEARER, **CSRF}, json={"name": "echo", "args": ["x"]}
    )
    assert created.status_code == 400, created.text
    assert "symbolic link" in created.json()["detail"]
    assert victim.read_bytes() == before


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs FIFOs")
@pytest.mark.parametrize("name", ["jobs.sqlite", "jobs.sqlite-journal", "jobs.sqlite-wal"])
@pytest.mark.parametrize("kind", ["fifo", "directory"])
def test_jobs_database_that_is_not_a_regular_file_is_refused(
    tmp_path: Path, name: str, kind: str
) -> None:
    """A FIFO (or a directory) planted by tar, rsync or a share: refused, never waited on."""
    root = _root_with_notes(tmp_path)
    if name != "jobs.sqlite":
        made = TestClient(create_app(root=root, token=TOKEN)).post(
            "/api/jobs", headers={**BEARER, **CSRF}, json={"name": "echo", "args": ["a"]}
        )
        assert made.status_code == 200
    planted = root / name
    if kind == "fifo":
        os.mkfifo(planted)
    else:
        planted.mkdir()
    result: dict[str, Any] = {}

    def attempt() -> None:
        try:
            jobs.open_db(root / "jobs.sqlite").close()
            result["outcome"] = "opened"
        except jobs.JobsDatabaseError as exc:
            result["outcome"] = f"refused: {exc}"
        except Exception as exc:  # noqa: BLE001 (any other failure is reported)
            result["outcome"] = f"{type(exc).__name__}: {exc}"

    worker = threading.Thread(target=attempt, daemon=True)
    worker.start()
    worker.join(5)
    if worker.is_alive() and kind == "fifo":
        # Unblock the reader the old code left waiting on the FIFO.
        with contextlib.suppress(OSError):
            os.close(os.open(planted, os.O_WRONLY | os.O_NONBLOCK))
        worker.join(5)
        result.setdefault("outcome", "hung on the FIFO")
    assert result.get("outcome", "hung").startswith("refused"), result
    assert "not a regular file" in result["outcome"] or "not a file" in result["outcome"]


def _swap_before_connect(
    monkeypatch: pytest.MonkeyPatch, database: Path, link_target: Path, *, restore: bool
) -> None:
    """Simulate the check-then-open race deterministically.

    Right before SQLite opens the database, ``jobs.sqlite`` becomes a symbolic
    link to ``link_target``. With ``restore``, the original file (same inode)
    is renamed back right after the open, as an attacker's swap loop would.
    """
    real_connect = sqlite3.connect
    parked = database.with_name(".parked")

    def racing_connect(*args: Any, **kwargs: Any) -> sqlite3.Connection:
        os.replace(database, parked)
        database.symlink_to(link_target)
        try:
            return real_connect(*args, **kwargs)
        finally:
            if restore:
                os.replace(parked, database)

    monkeypatch.setattr(jobs.sqlite3, "connect", racing_connect)


def test_jobs_database_swapped_for_a_dangling_link_after_the_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _root_with_notes(tmp_path)
    database = root / "jobs.sqlite"
    jobs.open_db(database).close()
    outside = tmp_path / "outside" / "planted-by-race.db"
    outside.parent.mkdir()
    _swap_before_connect(monkeypatch, database, outside, restore=False)
    with pytest.raises(jobs.JobsDatabaseError):
        jobs.open_db(database).close()
    assert not outside.exists(), "SQLite created the database at the link's target"


def test_jobs_database_swapped_for_a_link_and_back_is_detected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _root_with_notes(tmp_path)
    database = root / "jobs.sqlite"
    jobs.open_db(database).close()
    victim = tmp_path / "outside" / "victim.db"
    before = _victim_db(victim)
    _swap_before_connect(monkeypatch, database, victim, restore=True)
    with pytest.raises(jobs.JobsDatabaseError):
        jobs.open_db(database).close()
    assert victim.read_bytes() == before
    assert _tables(victim) == ["victim"]


def test_read_only_jobs_database_is_still_listed(tmp_path: Path) -> None:
    root = _root_with_notes(tmp_path)
    first = TestClient(create_app(root=root, token=TOKEN))
    made = first.post("/api/jobs", headers={**BEARER, **CSRF}, json={"name": "echo", "args": ["a"]})
    assert made.status_code == 200
    (root / "jobs.sqlite").chmod(0o444)
    try:
        listed = TestClient(create_app(root=root, token=TOKEN)).get("/api/jobs", headers=BEARER)
        assert listed.status_code == 200, listed.text
        assert [job["status"] for job in listed.json()] == ["ok"]
    finally:
        (root / "jobs.sqlite").chmod(0o644)


# FOLD-09 ------------------------------------------------------------------------


def _fake_rerun(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    written: list[Path] = []

    def fake_export(scene: Scene, dest: Path) -> int:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"rrd")
        written.append(dest)
        return 1

    monkeypatch.setattr("coperceive.app.server.export_rerun", fake_export)
    return written


def test_rerun_export_name_ignores_a_manifest_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    written = _fake_rerun(monkeypatch)
    root = tmp_path / "library"
    _scene(root, "shared-scene", "../other-scene/planted")
    (root / "other-scene").mkdir()
    client = TestClient(create_app(root=root, token=TOKEN))
    response = client.post("/api/scenes/shared-scene/export/rerun", headers={**BEARER, **CSRF})
    assert response.status_code == 200, response.text
    assert response.json()["path"] == "exports/shared-scene.rrd"
    assert written == [(root / "exports" / "shared-scene.rrd").resolve()]
    assert not (root / "other-scene" / "planted.rrd").exists()


def test_rerun_export_keeps_a_plain_manifest_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    written = _fake_rerun(monkeypatch)
    root = tmp_path / "library"
    _scene(root, "folder-name", "Town03-000")
    _scene(root, "odd.name", "odd.name")
    client = TestClient(create_app(root=root, token=TOKEN))
    plain = client.post("/api/scenes/folder-name/export/rerun", headers={**BEARER, **CSRF})
    assert plain.json()["path"] == "exports/Town03-000.rrd"
    odd = client.post("/api/scenes/odd.name/export/rerun", headers={**BEARER, **CSRF})
    assert odd.status_code == 200
    assert odd.json()["path"] == "exports/scene.rrd"
    assert all(path.parent == (root / "exports").resolve() for path in written)


# --token-file -------------------------------------------------------------------


def _token_args(token_file: Path) -> Any:
    return build_parser().parse_args(
        ["serve", "--root", str(token_file.parent), "--token-file", str(token_file)]
    )


def _resolve(token_file: Path) -> str | None:
    from coperceive import cli

    resolve: Any = cli._resolve_token  # pyright: ignore[reportPrivateUsage]
    return resolve(_token_args(token_file))


posix_only = pytest.mark.skipif(os.name == "nt", reason="POSIX owner and mode bits")


@posix_only
def test_token_file_owned_by_another_user_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    token_file = tmp_path / "token.txt"
    token_file.write_text("swapped-in-token\n", encoding="utf-8")
    token_file.chmod(0o600)
    assert _resolve(token_file) == "swapped-in-token"
    owner = token_file.stat().st_uid
    monkeypatch.setattr(os, "geteuid", lambda: owner + 1)
    monkeypatch.setattr(os, "getuid", lambda: owner + 1)
    with pytest.raises(SystemExit) as exc:
        _resolve(token_file)
    assert exc.value.code == 2
    assert "owned by" in capsys.readouterr().err


@posix_only
def test_token_file_symlink_is_refused(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = tmp_path / "real-token.txt"
    target.write_text("linked-token\n", encoding="utf-8")
    target.chmod(0o600)
    link = tmp_path / "token.txt"
    link.symlink_to(target)
    with pytest.raises(SystemExit) as exc:
        _resolve(link)
    assert exc.value.code == 2
    assert "symbolic link" in capsys.readouterr().err


@posix_only
def test_empty_token_file_does_not_fall_back_to_no_token(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    token_file = tmp_path / "token.txt"
    token_file.write_text("\n", encoding="utf-8")
    token_file.chmod(0o600)
    with pytest.raises(SystemExit) as exc:
        _resolve(token_file)
    assert exc.value.code == 2
    assert "empty" in capsys.readouterr().err


@posix_only
def test_token_file_the_desktop_writes_is_accepted(tmp_path: Path) -> None:
    """What sidecar.rs and remote/manager.py write: a 0600 regular file, token + newline."""
    token_file = tmp_path / "coperceive-local-1" / "token-8731.txt"
    token_file.parent.mkdir()
    descriptor = os.open(token_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write("0123456789abcdef0123456789abcdef\n")
    assert _resolve(token_file) == "0123456789abcdef0123456789abcdef"
    token_file.chmod(0o400)
    assert _resolve(token_file) == "0123456789abcdef0123456789abcdef"
