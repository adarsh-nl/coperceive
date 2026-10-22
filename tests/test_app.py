# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import base64
import struct
from dataclasses import replace
from pathlib import Path

import pytest
from coperceive.app.jobs import confine
from coperceive.app.server import create_app
from coperceive.cli import build_parser, main
from coperceive.evaluation.config import DEFAULT_CONFIG
from coperceive.evaluation.evaluate import evaluate_run
from coperceive.evaluation.predictions import write_run
from coperceive.hdmap.opendrive import parse_opendrive
from coperceive.hdmap.store import write_map
from coperceive.model import (
    SCHEMA_VERSION,
    Agent,
    AgentType,
    Clock,
    ImageRef,
    Modality,
    PointCloud,
    Scene,
    Sensor,
    StampedPose,
)
from coperceive.se3 import SE3
from coperceive.store import write_scene
from fastapi.testclient import TestClient

from tests.cooperative import cooperative_scene
from tests.eval_fixture import synthetic_predictions
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


def test_serve_token_from_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("COPERCEIVE_TOKEN", "env-secret-token")
    parser = build_parser()
    args = parser.parse_args(["serve", "--root", str(tmp_path)])
    from coperceive.cli import _resolve_token

    assert _resolve_token(args) == "env-secret-token"


def test_serve_token_from_file_strict_permissions(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    token_file = tmp_path / "token.txt"
    token_file.write_text("file-secret-token\n", encoding="utf-8")
    token_file.chmod(0o600)

    parser = build_parser()
    args = parser.parse_args(["serve", "--root", str(tmp_path), "--token-file", str(token_file)])
    from coperceive.cli import _resolve_token

    assert _resolve_token(args) == "file-secret-token"

    # Loose permissions (0o644 or 0o660) must be rejected
    token_file.chmod(0o644)
    with pytest.raises(SystemExit) as exc:
        _resolve_token(args)
    assert exc.value.code == 2
    assert "loose permissions" in capsys.readouterr().err

    # Missing file must be rejected
    missing = tmp_path / "missing.txt"
    args_missing = parser.parse_args(
        ["serve", "--root", str(tmp_path), "--token-file", str(missing)]
    )
    with pytest.raises(SystemExit) as exc_missing:
        _resolve_token(args_missing)
    assert exc_missing.value.code == 2
    assert "not a file" in capsys.readouterr().err


def test_serve_token_argv_warns_deprecation(tmp_path: Path) -> None:
    parser = build_parser()
    args = parser.parse_args(["serve", "--root", str(tmp_path), "--token", "argv-token"])
    from coperceive.cli import _resolve_token

    with pytest.deprecated_call(match="Passing --token on argv is deprecated"):
        token = _resolve_token(args)
    assert token == "argv-token"


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


def stamp_scene(scene: Scene, directory: Path) -> None:
    """Write a scene the desktop server will list (manifest and COMPLETE.json)."""
    write_scene(scene, directory)
    (directory / "COMPLETE.json").write_text("{}\n", encoding="utf-8")


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
    stamp_scene(scene, root / "desk")


def test_serve_rejects_a_root_without_a_complete_scene(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["serve", "--root", str(tmp_path)])
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "COMPLETE.json" in err
    assert "manifest.json" in err


def test_scene_list_omits_a_directory_without_complete(tmp_path: Path) -> None:
    _viewer_scene(tmp_path)
    partial = tmp_path / "partial"
    pose = StampedPose(0, SE3.identity())
    scene = Scene(
        schema_version=SCHEMA_VERSION,
        scene_id="partial",
        world_frame="ENU",
        clock=Clock("us", "incomplete"),
        agents=(Agent("vehicle", AgentType.VEHICLE, (pose,)),),
        sensors=(),
        annotations=(),
        objects=(),
        point_clouds=(),
    )
    write_scene(scene, partial)
    client = TestClient(create_app(root=tmp_path, token=None))
    listed = client.get("/api/scenes")
    assert listed.status_code == 200
    assert listed.json() == [{"scene_id": "desk"}]
    assert client.get("/api/scenes/partial").status_code == 404
    assert client.get("/api/scenes/desk").status_code == 200


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


_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def test_image_route_serves_the_recorded_file(tmp_path: Path) -> None:
    pose = StampedPose(0, SE3.identity())
    agent = Agent("vehicle", AgentType.VEHICLE, (pose,))
    camera = Sensor(
        "front",
        "vehicle",
        Modality.CAMERA,
        SE3.identity(),
        1,
        1,
        1.0,
        1.0,
        0.5,
        0.5,
    )
    scene = Scene(
        schema_version=SCHEMA_VERSION,
        scene_id="desk",
        world_frame="ENU",
        clock=Clock("us", "image fixture"),
        agents=(agent,),
        sensors=(camera,),
        annotations=(),
        objects=(),
        images=(ImageRef("vehicle", "front", 0, "camera/front/000000.png"),),
    )
    stamp_scene(scene, tmp_path / "desk")
    image = tmp_path / "desk" / "camera" / "front" / "000000.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(_PNG)
    outside = tmp_path / "secret.png"
    outside.write_bytes(_PNG)
    escaped = Scene(
        schema_version=SCHEMA_VERSION,
        scene_id="leak",
        world_frame="ENU",
        clock=Clock("us", "image fixture"),
        agents=(agent,),
        sensors=(camera,),
        annotations=(),
        objects=(),
        images=(ImageRef("vehicle", "front", 0, "../secret.png"),),
    )
    stamp_scene(escaped, tmp_path / "leak")
    client = TestClient(create_app(root=tmp_path, token=None))
    found = client.get(
        "/api/scenes/desk/image",
        params={"time_us": 0, "agent_id": "vehicle", "sensor_id": "front"},
    )
    assert found.status_code == 200
    assert found.headers["content-type"].startswith("image/png")
    assert found.content == _PNG
    absent = client.get(
        "/api/scenes/desk/image",
        params={"time_us": 1, "agent_id": "vehicle", "sensor_id": "front"},
    )
    assert absent.status_code == 404
    leaked = client.get(
        "/api/scenes/leak/image",
        params={"time_us": 0, "agent_id": "vehicle", "sensor_id": "front"},
    )
    assert leaked.status_code == 400


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


def test_sequence_export_route_writes_gif_and_view(tmp_path: Path) -> None:
    pose = StampedPose(0, SE3.identity())
    agent = Agent("vehicle", AgentType.VEHICLE, (pose,))
    camera = Sensor(
        "front",
        "vehicle",
        Modality.CAMERA,
        SE3.identity(),
        16,
        16,
        8.0,
        8.0,
        8.0,
        8.0,
    )
    scene = Scene(
        schema_version=SCHEMA_VERSION,
        scene_id="desk",
        world_frame="ENU",
        clock=Clock("us", "sequence route"),
        agents=(agent,),
        sensors=(camera,),
        annotations=(),
        objects=(),
        images=(ImageRef("vehicle", "front", 0, "camera/front/0.png"),),
    )
    stamp_scene(scene, tmp_path / "desk")
    image = tmp_path / "desk" / "camera" / "front" / "0.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(_PNG)
    app = create_app(root=tmp_path, token="secret")
    client = TestClient(app)
    headers = {"Authorization": "Bearer secret", "X-Coperceive-Request": "1"}
    denied = client.post(
        "/api/scenes/desk/export/sequence",
        headers={"Authorization": "Bearer secret"},
        json={"agent_id": "vehicle", "sensor_id": "front", "format": "gif"},
    )
    assert denied.status_code == 403
    created = client.post(
        "/api/scenes/desk/export/sequence",
        headers=headers,
        json={
            "agent_id": "vehicle",
            "sensor_id": "front",
            "format": "gif",
            "fps": 10,
            "width": 16,
            "height": 16,
        },
    )
    assert created.status_code == 200
    body = created.json()
    assert body["frames"] == 1
    assert body["format"] == "gif"
    assert (tmp_path / body["path"]).is_file()
    escaped = client.post(
        "/api/scenes/desk/export/sequence",
        headers=headers,
        json={"agent_id": "../vehicle", "sensor_id": "front", "format": "gif"},
    )
    assert escaped.status_code == 400
    view = client.post(
        "/api/scenes/desk/export/view",
        headers=headers,
        json={
            "format": "png",
            "fps": 10,
            "start_us": 0,
            "end_us": 0,
            "frames_b64": [base64.b64encode(_PNG).decode("ascii")],
        },
    )
    assert view.status_code == 200
    saved = view.json()
    assert saved["frames"] == 1
    assert saved["path"].endswith(".png")
    assert (tmp_path / saved["path"]).is_file()


def test_plot_routes_require_the_csrf_header_and_write_svg(tmp_path: Path) -> None:
    pose = StampedPose(0, SE3.identity())
    agent = Agent("vehicle", AgentType.VEHICLE, (pose,))
    camera = Sensor(
        "front",
        "vehicle",
        Modality.CAMERA,
        SE3.identity(),
        16,
        16,
        8.0,
        8.0,
        8.0,
        8.0,
    )
    scene = Scene(
        schema_version=SCHEMA_VERSION,
        scene_id="desk",
        world_frame="ENU",
        clock=Clock("us", "plot route"),
        agents=(agent,),
        sensors=(camera,),
        annotations=(),
        objects=(),
        images=(ImageRef("vehicle", "front", 0, "camera/front/0.png"),),
    )
    stamp_scene(scene, tmp_path / "desk")
    image = tmp_path / "desk" / "camera" / "front" / "0.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(_PNG)
    app = create_app(root=tmp_path, token="secret")
    client = TestClient(app)
    headers = {"Authorization": "Bearer secret", "X-Coperceive-Request": "1"}
    denied = client.post(
        "/api/scenes/desk/export/bev",
        headers={"Authorization": "Bearer secret"},
        json={"format": "svg"},
    )
    assert denied.status_code == 403
    created = client.post(
        "/api/scenes/desk/export/bev",
        headers=headers,
        json={"format": "svg", "time_us": 0},
    )
    assert created.status_code == 200
    body = created.json()
    assert body["format"] == "svg"
    assert body["boxes"] == 0
    assert (tmp_path / body["path"]).is_file()
    assert (tmp_path / body["path"]).read_text(encoding="utf-8").startswith("<?xml")
    missing = client.post(
        "/api/scenes/desk/export/overlay",
        headers=headers,
        json={"agent_id": "vehicle", "sensor_id": "missing", "format": "png"},
    )
    assert missing.status_code == 404
    overlay = client.post(
        "/api/scenes/desk/export/overlay",
        headers=headers,
        json={"agent_id": "vehicle", "sensor_id": "front", "format": "png", "time_us": 0},
    )
    assert overlay.status_code == 200
    saved = overlay.json()
    assert saved["path"].endswith(".png")
    assert (tmp_path / saved["path"]).is_file()


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


def _evaluated_root(root: Path, *, predictions: Path | None = None) -> Path:
    """``root/scenes/coop`` plus the ``synthetic`` run, written and evaluated."""
    scene = cooperative_scene()
    scenes = root / "scenes"
    stamp_scene(replace(scene, scene_id="coop"), scenes / "coop")
    store = scenes / "predictions" if predictions is None else predictions
    meta, boxes = synthetic_predictions(scene)
    rows = tuple(replace(box, scene_id="coop") for box in boxes)
    write_run(store, "synthetic", meta, rows)
    report = evaluate_run(
        scenes, store, "synthetic", DEFAULT_CONFIG, created_utc="2026-10-17T00:00:00Z"
    )
    assert report.evaluated == ("coop",), report
    return scenes


def test_prediction_routes_list_frame_and_series(tmp_path: Path) -> None:
    scenes = _evaluated_root(tmp_path)
    client = TestClient(create_app(root=scenes, token=None))

    listed = client.get("/api/scenes/coop/predictions")
    assert listed.status_code == 200
    runs = listed.json()["runs"]
    assert [run["run_id"] for run in runs] == ["synthetic"]
    assert runs[0]["evaluated"] is True
    assert runs[0]["run"]["format"] == "coperceive-predictions"
    assert runs[0]["config"]["display_threshold"] == 0.5
    outputs = runs[0]["outputs"]
    assert {(o["agent_id"], o["source"]) for o in outputs} >= {("vehicle", "single")}
    assert set(outputs[0]) == {"agent_id", "source", "totals", "ap", "cooperative"}

    frame = client.get(
        "/api/scenes/coop/predictions/synthetic/frame",
        params={"time_us": 100000, "compare": "synthetic"},
    )
    assert frame.status_code == 200
    body = frame.json()
    assert body["run_id"] == "synthetic"
    assert body["time_us"] == 100000
    assert body["display_threshold"] == 0.5
    assert body["score_threshold"] == 0.3
    rows = body["rows"]
    assert rows
    assert {row["status"] for row in rows} >= {"tp", "fp", "miss"}
    assert all(len(row["lines"]) == 72 for row in rows)
    assert body["compare"]["run_id"] == "synthetic"
    assert len(body["compare"]["rows"]) == len(rows)
    alone = client.get("/api/scenes/coop/predictions/synthetic/frame", params={"time_us": 100000})
    assert alone.json()["compare"] is None

    series = client.get(
        "/api/scenes/coop/predictions/synthetic/series", params={"compare": "synthetic"}
    )
    assert series.status_code == 200
    body = series.json()
    assert body["run_id"] == "synthetic"
    for output in body["outputs"]:
        assert set(output) >= {
            "agent_id",
            "source",
            "times_us",
            "tp",
            "fp",
            "miss",
            "precision",
            "recall",
        }
        assert len(output["times_us"]) == len(output["tp"]) == len(output["recall"])
    assert body["compare"]["run_id"] == "synthetic"

    missing = client.get("/api/scenes/coop/predictions/nope/frame", params={"time_us": 100000})
    assert missing.status_code == 404
    other = client.get("/api/scenes/other/predictions")
    assert other.status_code == 404

    headers = {"X-Coperceive-Request": "1"}
    bev = client.post(
        "/api/scenes/coop/export/bev",
        headers=headers,
        json={"format": "svg", "time_us": 100000, "run_id": "synthetic", "points": False},
    )
    assert bev.status_code == 200
    saved = bev.json()
    assert saved["predictions"] > 0
    assert (scenes / saved["path"]).is_file()
    assert "#1fa3a3" in (scenes / saved["path"]).read_text(encoding="utf-8").lower()
    unknown = client.post(
        "/api/scenes/coop/export/bev",
        headers=headers,
        json={"format": "svg", "time_us": 100000, "run_id": "nope"},
    )
    assert unknown.status_code == 404


def test_prediction_routes_refuse_traversal(tmp_path: Path) -> None:
    scenes = _evaluated_root(tmp_path)
    client = TestClient(create_app(root=scenes, token=None))
    for path in (
        "/api/scenes/coop/predictions/..%2Fx/frame",
        "/api/scenes/coop/predictions/a%2Fb/frame",
        "/api/scenes/coop/predictions/..%2Fx/series",
        "/api/scenes/coop/predictions/.hidden/frame",
        "/api/scenes/..%2Fcoop/predictions",
    ):
        response = client.get(path, params={"time_us": 100000})
        assert response.status_code in {400, 404}, path
    bev = client.post(
        "/api/scenes/coop/export/bev",
        headers={"X-Coperceive-Request": "1"},
        json={"format": "svg", "time_us": 100000, "run_id": "../x"},
    )
    assert bev.status_code in {400, 404}


def test_prediction_routes_404_without_root(tmp_path: Path) -> None:
    _viewer_scene(tmp_path)
    client = TestClient(create_app(root=tmp_path, token=None))
    for path in (
        "/api/scenes/desk/predictions",
        "/api/scenes/desk/predictions/synthetic/frame",
        "/api/scenes/desk/predictions/synthetic/series",
    ):
        response = client.get(path, params={"time_us": 0})
        assert response.status_code == 404, path
        assert response.json()["detail"] == "no predictions root"
    bev = client.post(
        "/api/scenes/desk/export/bev",
        headers={"X-Coperceive-Request": "1"},
        json={"format": "svg", "time_us": 0, "run_id": "synthetic"},
    )
    assert bev.status_code == 404
    assert bev.json()["detail"] == "no predictions root"
    # An explicit --predictions root wins over the <root>/predictions default.
    elsewhere = tmp_path / "elsewhere"
    scenes = _evaluated_root(tmp_path / "second", predictions=elsewhere)
    assert not (scenes / "predictions").exists()
    client = TestClient(create_app(root=scenes, token=None, predictions=elsewhere))
    assert client.get("/api/scenes/coop/predictions").json()["runs"][0]["run_id"] == "synthetic"


def test_prediction_routes_answer_400_for_nul_and_overlong_ids(tmp_path: Path) -> None:
    """A run or scene id that passes the name rule but not the OS gives 400, never 500."""
    scenes = _evaluated_root(tmp_path)
    client = TestClient(create_app(root=scenes, token=None), raise_server_exceptions=False)
    long_id = "a" * 300
    for run_id in ("a%00b", long_id):
        for path in (
            f"/api/scenes/coop/predictions/{run_id}/frame",
            f"/api/scenes/coop/predictions/{run_id}/series",
        ):
            response = client.get(path, params={"time_us": 100000})
            assert response.status_code == 400, (path, response.status_code)
        compare = client.get(
            "/api/scenes/coop/predictions/synthetic/frame",
            params={"time_us": 100000, "compare": run_id.replace("%00", "\x00")},
        )
        assert compare.status_code == 400, run_id
        bev = client.post(
            "/api/scenes/coop/export/bev",
            headers={"X-Coperceive-Request": "1"},
            json={"format": "svg", "time_us": 100000, "run_id": run_id.replace("%00", "\x00")},
        )
        assert bev.status_code == 400, run_id
    for scene_id in ("coop%00", long_id):
        for path in (
            f"/api/scenes/{scene_id}/predictions",
            f"/api/scenes/{scene_id}/predictions/synthetic/frame",
            f"/api/scenes/{scene_id}",
        ):
            response = client.get(path, params={"time_us": 100000})
            assert response.status_code in {400, 404}, (path, response.status_code)
            assert response.status_code != 500


def _camera_scene(scene_id: str, image_path: str) -> Scene:
    pose = StampedPose(0, SE3.identity())
    agent = Agent("vehicle", AgentType.VEHICLE, (pose,))
    camera = Sensor("front", "vehicle", Modality.CAMERA, SE3.identity(), 1, 1, 1.0, 1.0, 0.5, 0.5)
    return Scene(
        schema_version=SCHEMA_VERSION,
        scene_id=scene_id,
        world_frame="ENU",
        clock=Clock("us", "media fixture"),
        agents=(agent,),
        sensors=(camera,),
        annotations=(),
        objects=(),
        images=(ImageRef("vehicle", "front", 0, image_path),),
    )


def test_static_option_serves_another_bundle(tmp_path: Path) -> None:
    """``coperceive serve --static DIR`` serves that build instead of the packaged one, so a
    bundle built out of tree (never committed from the HPC) can be viewed."""
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<title>fresh bundle</title>", encoding="utf-8")
    (dist / "favicon.svg").write_text("<svg>fresh</svg>", encoding="utf-8")
    root = tmp_path / "scenes"
    root.mkdir()
    client = TestClient(create_app(root=root, token=None, static=dist))
    page = client.get("/")
    assert page.status_code == 200
    assert "fresh bundle" in page.text
    assert client.get("/favicon.ico").text == "<svg>fresh</svg>"  # the same build's icon
    with pytest.raises(SystemExit) as exc:
        main(["serve", "--root", str(root), "--static", str(tmp_path / "missing")])
    assert exc.value.code == 2


def test_image_route_follows_a_symlink_only_into_a_media_root(tmp_path: Path) -> None:
    """Staged Griffin scenes link ``<side>/camera`` to the dataset copy. The image route
    serves such a file only when it resolves under a ``--media-root``; a link elsewhere
    and a ``..`` path stay 400 even then."""
    media = tmp_path / "release"
    real = media / "vehicle-side" / "camera" / "front" / "000000.png"
    real.parent.mkdir(parents=True)
    real.write_bytes(_PNG)
    elsewhere = tmp_path / "elsewhere" / "secret.png"
    elsewhere.parent.mkdir()
    elsewhere.write_bytes(_PNG)
    root = tmp_path / "scenes"
    stamp_scene(_camera_scene("linked", "vehicle-side/camera/front/000000.png"), root / "linked")
    (root / "linked" / "vehicle-side").mkdir()
    (root / "linked" / "vehicle-side" / "camera").symlink_to(media / "vehicle-side" / "camera")
    stamp_scene(_camera_scene("stray", "leak/secret.png"), root / "stray")
    (root / "stray" / "leak").symlink_to(elsewhere.parent)
    dotted = "../../release/vehicle-side/camera/front/000000.png"
    stamp_scene(_camera_scene("dotted", dotted), root / "dotted")
    params = {"time_us": 0, "agent_id": "vehicle", "sensor_id": "front"}

    plain = TestClient(create_app(root=root, token=None))
    assert plain.get("/api/scenes/linked/image", params=params).status_code == 400

    allowed = TestClient(create_app(root=root, token=None, media_roots=(media,)))
    found = allowed.get("/api/scenes/linked/image", params=params)
    assert found.status_code == 200
    assert found.content == _PNG
    assert allowed.get("/api/scenes/stray/image", params=params).status_code == 400
    assert allowed.get("/api/scenes/dotted/image", params=params).status_code == 400


def test_summary_reports_what_media_is_staged(tmp_path: Path) -> None:
    """The scene summary says how many lidar sweeps and image rows the scene has and whether
    the image files are actually present, so the page can show an empty state per pane."""
    root = tmp_path / "scenes"
    stamp_scene(_camera_scene("bare", "vehicle-side/camera/front/000000.png"), root / "bare")
    stamp_scene(_camera_scene("staged", "vehicle-side/camera/front/000000.png"), root / "staged")
    staged_file = root / "staged" / "vehicle-side" / "camera" / "front" / "000000.png"
    staged_file.parent.mkdir(parents=True)
    staged_file.write_bytes(_PNG)
    _viewer_scene(root)  # "desk": one lidar sweep, no image rows
    client = TestClient(create_app(root=root, token=None))
    bare = client.get("/api/scenes/bare").json()
    assert (bare["point_sweeps"], bare["image_rows"], bare["images_staged"]) == (0, 1, False)
    staged = client.get("/api/scenes/staged").json()
    assert (staged["point_sweeps"], staged["image_rows"], staged["images_staged"]) == (0, 1, True)
    desk = client.get("/api/scenes/desk").json()
    assert (desk["point_sweeps"], desk["image_rows"], desk["images_staged"]) == (1, 0, None)
    # A path the OS cannot stat (over-long component) is absent, not a 500.
    overlong = "a" * 300 + "/img.png"
    stamp_scene(_camera_scene("odd", overlong), root / "odd")
    odd = client.get("/api/scenes/odd")
    assert odd.status_code == 200
    assert odd.json()["images_staged"] is False
    image = client.get(
        "/api/scenes/odd/image", params={"time_us": 0, "agent_id": "vehicle", "sensor_id": "front"}
    )
    assert image.status_code == 404


def test_non_loopback_token_auth_protects_all_api_routes(tmp_path: Path) -> None:
    """When a token is configured (e.g. binding non-loopback on a cluster node), every /api
    route (GET included: scenes, frames, points, images, map lines, jobs, predictions)
    strictly requires the Bearer token and returns 401 if missing or invalid."""
    root = tmp_path / "scenes"
    _viewer_scene(root)  # creates scene "desk"
    token = "secret-session-token-xyz"
    app = create_app(root=root, token=token)
    client = TestClient(app)

    routes = [
        "/api/health",
        "/api/scenes",
        "/api/scenes/desk",
        "/api/scenes/desk/frame",
        "/api/scenes/desk/points",
        "/api/scenes/desk/image",
        "/api/scenes/desk/map/lines",
        "/api/jobs",
        "/api/scenes/desk/predictions",
    ]

    for route in routes:
        # Request with no Authorization header
        resp_no_auth = client.get(route)
        assert resp_no_auth.status_code == 401, (
            f"{route} without auth returned {resp_no_auth.status_code}"
        )
        assert resp_no_auth.json() == {"detail": "bearer token required"}

        # Request with invalid token
        resp_bad_auth = client.get(route, headers={"Authorization": "Bearer wrong-token"})
        assert resp_bad_auth.status_code == 401, (
            f"{route} with bad auth returned {resp_bad_auth.status_code}"
        )
        assert resp_bad_auth.json() == {"detail": "bearer token required"}

        # Request with valid token should not be rejected with 401
        resp_good = client.get(route, headers={"Authorization": f"Bearer {token}"})
        assert resp_good.status_code != 401, (
            f"{route} with valid auth returned {resp_good.status_code}"
        )


def test_token_auth_uses_constant_time_comparison(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Token authentication uses constant-time comparison (hmac.compare_digest)
    to prevent timing attacks."""
    import hmac

    called = []
    original_compare = hmac.compare_digest

    def spy_compare(a: str, b: str) -> bool:
        called.append((a, b))
        return original_compare(a, b)

    monkeypatch.setattr(hmac, "compare_digest", spy_compare)

    token = "super-secret-token-123"
    app = create_app(root=tmp_path, token=token)
    client = TestClient(app)

    resp = client.get("/api/health", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert len(called) >= 1
    assert called[-1] == (token, token)

    called.clear()
    resp_bad = client.get("/api/health", headers={"Authorization": "Bearer bad-token"})
    assert resp_bad.status_code == 401
    assert len(called) >= 1
    assert called[-1] == ("bad-token", token)


def test_cors_options_preflight_on_authenticated_server(tmp_path: Path) -> None:
    """CORS OPTIONS preflight requests must succeed without Authorization headers
    when allow_origin matches, so that the browser can complete cross-origin
    requests when switching sidecar instances."""
    app = create_app(
        root=tmp_path,
        token="preflight-secret-token",
        allow_origin="http://127.0.0.1:49152",
    )
    client = TestClient(app)

    resp = client.options(
        "/api/scenes",
        headers={
            "Origin": "http://127.0.0.1:49152",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        },
    )
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") == "http://127.0.0.1:49152"
    assert "authorization" in resp.headers.get("access-control-allow-headers", "").lower()
    # allow_credentials must be False
    allow_cred = resp.headers.get("access-control-allow-credentials")
    assert allow_cred is None or allow_cred == "false"


def test_cors_foreign_origin_rejected(tmp_path: Path) -> None:
    """A foreign localhost or remote origin gets no CORS allow headers."""
    app = create_app(
        root=tmp_path,
        token="preflight-secret-token",
        allow_origin="http://127.0.0.1:49152",
    )
    client = TestClient(app)

    # Foreign localhost origin preflight
    resp_preflight = client.options(
        "/api/scenes",
        headers={
            "Origin": "http://127.0.0.1:55555",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        },
    )
    assert resp_preflight.headers.get("access-control-allow-origin") is None

    # Foreign origin GET request
    resp_get = client.get(
        "/api/scenes",
        headers={
            "Origin": "http://127.0.0.1:55555",
            "Authorization": "Bearer preflight-secret-token",
        },
    )
    assert resp_get.headers.get("access-control-allow-origin") is None
