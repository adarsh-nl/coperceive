# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import struct
from pathlib import Path

import pytest
from coperceive.app.jobs import confine
from coperceive.app.server import create_app
from coperceive.cli import main
from coperceive.hdmap.opendrive import parse_opendrive
from coperceive.hdmap.store import write_map
from coperceive.model import (
    SCHEMA_VERSION,
    Agent,
    AgentType,
    Clock,
    Modality,
    PointCloud,
    Scene,
    Sensor,
    StampedPose,
)
from coperceive.se3 import SE3
from coperceive.store import write_scene
from fastapi.testclient import TestClient

from tests.test_hdmap import _scene_on_lane, _straight


def test_help_and_schema(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    assert "coperceive" in capsys.readouterr().out
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
    app = create_app(root=tmp_path, token="secret", root_path="/user/coperceive")
    client = TestClient(app)
    denied = client.get("/api/health")
    assert denied.status_code == 401
    headers = {"Authorization": "Bearer secret"}
    assert client.get("/api/health", headers=headers).json()["root_path"] == "/user/coperceive"
    missing = client.post("/api/jobs", headers=headers, json={"name": "echo", "args": ["hi"]})
    assert missing.status_code == 403
    headers["X-Coperceive-Request"] = "1"
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


def _viewer_scene(root: Path) -> None:
    pose = StampedPose(0, SE3.identity().translation(10.0, 0.0, 0.0))
    agent = Agent("vehicle", AgentType.VEHICLE, (pose,))
    sensor = Sensor("lidar_top", "vehicle", Modality.LIDAR, SE3.identity())
    cloud = PointCloud("vehicle", "lidar_top", 0, 1, struct.pack("<ffff", 1.0, 0.0, 0.0, 1.0))
    scene = Scene(
        schema_version=SCHEMA_VERSION,
        scene_id="desk",
        world_frame="ENU",
        clock=Clock("us", "app viewer fixture"),
        agents=(agent,),
        sensors=(sensor,),
        annotations=(),
        objects=(),
        point_clouds=(cloud,),
    )
    write_scene(scene, root / "desk")


def test_viewer_api_lists_a_frame_and_rejects_a_bad_id(tmp_path: Path) -> None:
    _viewer_scene(tmp_path)
    app = create_app(root=tmp_path, token=None)
    client = TestClient(app)
    listed = client.get("/api/scenes")
    assert listed.status_code == 200
    assert listed.json() == [{"scene_id": "desk"}]
    summary = client.get("/api/scenes/desk")
    assert summary.status_code == 200
    assert summary.json()["times_us"] == [0]
    frame = client.get("/api/scenes/desk/frame", params={"time_us": 0})
    assert frame.status_code == 200
    assert frame.json()["point_sensors"][0]["count"] == 1
    points = client.get(
        "/api/scenes/desk/points",
        params={"time_us": 0, "agent_id": "vehicle", "sensor_id": "lidar_top"},
    )
    assert points.status_code == 200
    assert struct.unpack("<fff", points.content) == pytest.approx((11.0, 0.0, 0.0))
    denied = client.get("/api/scenes/foo..bar")
    assert denied.status_code == 400
    missing = client.get("/api/scenes/nope")
    assert missing.status_code == 404


def test_rerun_export_route_writes_under_the_root(tmp_path: Path) -> None:
    pytest.importorskip("rerun")
    _viewer_scene(tmp_path)
    app = create_app(root=tmp_path, token="secret")
    client = TestClient(app)
    headers = {"Authorization": "Bearer secret", "X-Coperceive-Request": "1"}
    missing = client.post("/api/scenes/desk/export/rerun")
    assert missing.status_code == 401
    no_header = client.post(
        "/api/scenes/desk/export/rerun",
        headers={"Authorization": "Bearer secret"},
    )
    assert no_header.status_code == 403
    created = client.post("/api/scenes/desk/export/rerun", headers=headers)
    assert created.status_code == 200
    body = created.json()
    assert body["samples"] == 1
    assert (tmp_path / body["path"]).is_file()


def test_map_http_and_favicon(tmp_path: Path) -> None:
    xodr = tmp_path / "Town03.xodr"
    _straight(xodr)
    maps = tmp_path / "maps"
    write_map(
        parse_opendrive(xodr, sample_step_m=25),
        maps / "Town03",
        map_name="Carla/Maps/Town03",
        source={"licence": "MIT"},
    )
    scenes = tmp_path / "scenes"
    _scene_on_lane(scenes)
    app = create_app(root=scenes, token=None, maps=maps)
    client = TestClient(app)
    summary = client.get("/api/scenes/Town03-000/map")
    assert summary.status_code == 200
    assert summary.json()["available"] is True
    lines = client.get("/api/scenes/Town03-000/map/lines")
    assert lines.status_code == 200
    count = struct.unpack_from("<I", lines.content)[0]
    assert count > 0
    icon = client.get("/favicon.ico")
    assert icon.status_code == 200
    assert icon.headers["content-type"].startswith("image/svg")
    missing = client.get("/api/scenes/Town03-000/map/lines")
    assert missing.status_code == 200
