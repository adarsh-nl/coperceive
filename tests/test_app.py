# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pkg.app.jobs import confine
from pkg.app.server import create_app
from pkg.cli import main


def test_help_and_schema(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    assert "placeholder" in capsys.readouterr().out
    main(["schema"])
    assert capsys.readouterr().out.strip() == "1"


def test_refuses_public_bind_without_token() -> None:
    with pytest.raises(SystemExit) as exc:
        main(["serve", "--host", "0.0.0.0", "--root", "/tmp"])
    assert exc.value.code == 2


def test_path_confinement(tmp_path: Path) -> None:
    inside = tmp_path / "note.txt"
    inside.write_text("ok", encoding="utf-8")
    assert confine(tmp_path, "note.txt") == inside.resolve()
    with pytest.raises(ValueError):
        confine(tmp_path, "../note.txt")


def test_jobs_sse_and_files(tmp_path: Path) -> None:
    (tmp_path / "note.txt").write_text("hello", encoding="utf-8")
    app = create_app(root=tmp_path, token="secret", root_path="/user/pkg")
    client = TestClient(app)
    denied = client.get("/api/health")
    assert denied.status_code == 401
    headers = {"Authorization": "Bearer secret"}
    assert client.get("/api/health", headers=headers).json()["root_path"] == "/user/pkg"
    missing = client.post("/api/jobs", headers=headers, json={"name": "echo", "args": ["hi"]})
    assert missing.status_code == 403
    headers["X-Pkg-Request"] = "1"
    blocked = client.post(
        "/api/jobs", headers=headers, json={"name": "bash", "args": ["-c", "true"]}
    )
    assert blocked.status_code == 400
    created = client.post("/api/jobs", headers=headers, json={"name": "echo", "args": ["hi"]})
    assert created.status_code == 200
    jobs = client.get("/api/jobs", headers=headers).json()
    assert jobs[0]["status"] == "ok"
    assert "hi" in jobs[0]["output"]
    with client.stream("GET", "/api/events", headers=headers) as response:
        line = next(response.iter_lines())
    assert line.startswith("data:")
    listed = client.get("/api/files", headers=headers, params={"path": "note.txt"})
    assert listed.text == "hello"
    escaped = client.get("/api/files", headers=headers, params={"path": "../note.txt"})
    assert escaped.status_code == 400
