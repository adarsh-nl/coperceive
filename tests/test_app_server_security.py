# SPDX-License-Identifier: Apache-2.0
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
"""Security regressions for ``coperceive serve`` (ADR 0019, group app-server).

FOLD-02: the bearer token and CSRF header apply to every ``/api`` route whatever
``--root-path`` is. FOLD-06: token-less serve answers only a loopback Host on its
own port.
"""

from __future__ import annotations

import contextlib
import socket
import threading
import time
from collections.abc import Generator
from pathlib import Path
from typing import Any

import httpx
import pytest
import uvicorn
from coperceive.app import serve
from coperceive.app.server import create_app
from coperceive.cli import main
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
