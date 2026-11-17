# SPDX-License-Identifier: Apache-2.0
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
"""The server side of the three bundle views, through the ``/api`` routes the viewer uses.

Included sensor data, the recipient's local copy, and no sensor data. Each view
is served like ``coperceive serve --root <bundle>/scenes --predictions
<bundle>/predictions --bundle-info <bundle>/open.json`` and must show the
bundle's frames, boxes and predictions. The new behaviour stays behind #170's
token, CSRF and loopback-Host checks, and the private jobs database still
passes ``open_db``. Synthetic demo data only.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

import pytest
from coperceive.app import serve
from coperceive.app.server import create_app
from coperceive.bundle import BundleExportRequest, OpenedBundle, export_bundle, open_bundle
from coperceive.cli import main
from coperceive.demo import create
from fastapi import FastAPI
from fastapi.testclient import TestClient

SCENE = "demo-junction"
RUN = "demo"
TOKEN = "fake-token-0000"
BEARER = {"Authorization": f"Bearer {TOKEN}"}
CSRF = {"X-Coperceive-Request": "1"}
PREFIX = "/user/alice/proxy/8731"


@pytest.fixture(scope="module")
def demo_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return create(tmp_path_factory.mktemp("views-demo"), seed=0).root


def _export(demo_root: Path, out: Path, *, sensor: bool) -> Path:
    return export_bundle(
        BundleExportRequest(
            run_id=RUN,
            scenes_root=demo_root / "scenes",
            predictions_root=demo_root / "predictions",
            out=out,
            dataset_id="demo",
            include_sensor_data=sensor,
            created_utc="2026-01-01T00:00:00Z",
        )
    )


@pytest.fixture(scope="module")
def views(demo_root: Path, tmp_path_factory: pytest.TempPathFactory) -> dict[str, OpenedBundle]:
    out = tmp_path_factory.mktemp("views-bundles")
    included = _export(demo_root, out / "included.cpbundle", sensor=True)
    omitted = _export(demo_root, out / "omitted.cpbundle", sensor=False)
    data = tmp_path_factory.mktemp("views-data")
    return {
        "included": open_bundle(included, data_root=data / "included"),
        "local-copy": open_bundle(
            omitted, data_root=data / "local", library_roots=[demo_root / "scenes"]
        ),
        "omitted": open_bundle(omitted, data_root=data / "omitted"),
    }


def _app(opened: OpenedBundle, token: str | None = None) -> FastAPI:
    return create_app(
        root=opened.scenes_root,
        predictions=opened.predictions_root,
        bundle_info=opened.report_path,
        token=token,
    )


def _frames(opened: OpenedBundle) -> list[int]:
    return list(opened.report["scenes"][0]["frames"])  # type: ignore[index]


def _tree(root: Path) -> dict[str, tuple[int, str]]:
    return {
        path.relative_to(root).as_posix(): (
            path.stat().st_mtime_ns,
            hashlib.sha256(path.read_bytes()).hexdigest(),
        )
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _camera(summary: dict[str, Any]) -> dict[str, str]:
    return next(sensor for sensor in summary["sensors"] if sensor["modality"] == "camera")


def _walk_view(client: TestClient, frames: list[int]) -> dict[str, Any]:
    """The requests the viewer makes for the first frame of the bundle's one scene."""
    summary = client.get(f"/api/scenes/{SCENE}")
    assert summary.status_code == 200, summary.text
    body = summary.json()
    time_us = frames[0]
    frame = client.get(f"/api/scenes/{SCENE}/frame", params={"time_us": time_us})
    assert frame.status_code == 200, frame.text
    predictions = client.get(
        f"/api/scenes/{SCENE}/predictions/{RUN}/frame", params={"time_us": time_us}
    )
    camera = _camera(body)
    image = client.get(
        f"/api/scenes/{SCENE}/image",
        params={
            "time_us": time_us,
            "agent_id": camera["agent_id"],
            "sensor_id": camera["sensor_id"],
        },
    )
    points = [
        client.get(
            f"/api/scenes/{SCENE}/points",
            params={"time_us": time_us, "agent_id": row["agent_id"], "sensor_id": row["sensor_id"]},
        )
        for row in frame.json()["point_sensors"]
    ]
    return {
        "summary": body,
        "frame": frame.json(),
        "predictions": predictions,
        "image": image,
        "points": points,
        "series": client.get(f"/api/scenes/{SCENE}/predictions/{RUN}/series"),
    }


def _assert_bundle_frames_and_predictions(view: dict[str, Any], opened: OpenedBundle) -> None:
    frames = _frames(opened)
    assert view["summary"]["times_us"] == frames
    assert view["frame"]["boxes"]
    assert view["predictions"].status_code == 200, view["predictions"].text
    rows = view["predictions"].json()["rows"]
    assert rows and {row["time_us"] for row in rows} == {frames[0]}
    assert view["series"].status_code == 200, view["series"].text


def test_included_view_serves_the_bundled_sensors(views: dict[str, OpenedBundle]) -> None:
    opened = views["included"]
    view = _walk_view(TestClient(_app(opened)), _frames(opened))
    _assert_bundle_frames_and_predictions(view, opened)
    assert view["summary"]["bundle"]["sensor_data"] == "included"
    assert view["summary"]["bundle"]["licence"] == "Apache-2.0"
    assert view["image"].status_code == 200
    assert view["points"] and all(r.status_code == 200 and r.content for r in view["points"])


def test_local_copy_view_shows_the_bundle_with_the_local_sensors(
    views: dict[str, OpenedBundle], demo_root: Path
) -> None:
    opened = views["local-copy"]
    library = demo_root / "scenes" / SCENE
    library_before = _tree(library)
    bundle_before = _tree(opened.root)
    view = _walk_view(TestClient(_app(opened)), _frames(opened))
    # The bundle's six frames, not the hundred of the local copy.
    _assert_bundle_frames_and_predictions(view, opened)
    bundle = view["summary"]["bundle"]
    assert bundle["sensor_data"] == "local-copy"
    assert "your local copy" in bundle["label"]
    assert Path(bundle["local_copy"]) == library.resolve()
    assert view["image"].status_code == 200
    assert view["points"] and all(r.status_code == 200 and r.content for r in view["points"])
    # The same sweeps the sender's own bundle carries for that frame.
    shared = _walk_view(TestClient(_app(views["included"])), _frames(opened))
    assert [r.content for r in view["points"]] == [r.content for r in shared["points"]]
    # Read in place: neither the library nor the extracted bundle changes.
    assert _tree(library) == library_before
    assert _tree(opened.root) == bundle_before
    assert not list((opened.scenes_root / SCENE).rglob("*.png"))


def test_local_copy_view_does_not_serve_frames_outside_the_bundle(
    views: dict[str, OpenedBundle],
) -> None:
    opened = views["local-copy"]
    client = TestClient(_app(opened))
    outside = max(_frames(opened)) + 100_000
    frame = client.get(f"/api/scenes/{SCENE}/frame", params={"time_us": outside})
    sensors = frame.json()["point_sensors"] if frame.status_code == 200 else []
    for row in sensors:
        points = client.get(
            f"/api/scenes/{SCENE}/points",
            params={"time_us": outside, "agent_id": row["agent_id"], "sensor_id": row["sensor_id"]},
        )
        assert points.status_code != 200 or not points.content


def test_omitted_view_shows_boxes_and_the_reason(views: dict[str, OpenedBundle]) -> None:
    opened = views["omitted"]
    view = _walk_view(TestClient(_app(opened)), _frames(opened))
    _assert_bundle_frames_and_predictions(view, opened)
    bundle = view["summary"]["bundle"]
    assert bundle["sensor_data"] == "omitted"
    assert "Frames not included" in bundle["note"]
    assert view["summary"]["point_sweeps"] == 0
    assert view["image"].status_code == 404
    assert not view["frame"]["point_sensors"]


def test_a_local_copy_path_outside_its_report_shape_is_not_served(
    views: dict[str, OpenedBundle], tmp_path: Path
) -> None:
    """open.json's local_copy must be an absolute folder of the same scene."""
    import json

    opened = views["local-copy"]
    report = json.loads(opened.report_path.read_text(encoding="utf-8"))
    for bad in (
        "relative/demo-junction",
        str(tmp_path / "missing"),
        str(opened.scenes_root / SCENE),
    ):
        report["scenes"][0]["local_copy"] = bad
        path = tmp_path / "open.json"
        path.write_text(json.dumps(report), encoding="utf-8")
        app = create_app(
            root=opened.scenes_root,
            predictions=opened.predictions_root,
            bundle_info=path,
            token=None,
        )
        summary = TestClient(app).get(f"/api/scenes/{SCENE}").json()
        assert summary["point_sweeps"] == 0, bad


# --- #170 stays intact ------------------------------------------------------------


def _bundle_routes(opened: OpenedBundle) -> list[str]:
    time_us = _frames(opened)[0]
    scene = f"/api/scenes/{SCENE}"
    return [
        "/api/scenes",
        scene,
        f"{scene}/frame?time_us={time_us}",
        f"{scene}/points?time_us={time_us}&agent_id=cav-a&sensor_id=lidar",
        f"{scene}/image?time_us={time_us}&agent_id=cav-a&sensor_id=front",
        f"{scene}/predictions",
        f"{scene}/predictions/{RUN}/frame?time_us={time_us}",
        f"{scene}/predictions/{RUN}/series",
    ]


def _served_by_cli(
    monkeypatch: pytest.MonkeyPatch, opened: OpenedBundle, *extra: str
) -> tuple[FastAPI, dict[str, Any]]:
    captured: dict[str, Any] = {}

    def fake_run(app: FastAPI, **kwargs: Any) -> None:
        captured["app"] = app
        captured["kwargs"] = kwargs

    monkeypatch.setattr(serve.uvicorn, "run", fake_run)
    main(
        [
            "serve",
            "--root",
            str(opened.scenes_root),
            "--predictions",
            str(opened.predictions_root),
            "--bundle-info",
            str(opened.report_path),
            "--port",
            "8731",
            *extra,
        ]
    )
    return captured["app"], captured["kwargs"]


@pytest.mark.parametrize("view", ["included", "local-copy", "omitted"])
def test_every_bundle_route_needs_the_token(
    views: dict[str, OpenedBundle], view: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened = views[view]
    monkeypatch.setenv("COPERCEIVE_TOKEN", TOKEN)
    app, options = _served_by_cli(monkeypatch, opened)
    anonymous = TestClient(app)
    allowed = TestClient(app, headers=BEARER)
    for route in _bundle_routes(opened):
        refused = anonymous.get(route)
        assert refused.status_code == 401, (route, refused.status_code)
        assert allowed.get(route).status_code in {200, 404}, route
    assert allowed.get(f"/api/scenes/{SCENE}").json()["bundle"]["sensor_data"] == view
    # The same with --root-path, as uvicorn builds the scope behind a proxy.
    app, options = _served_by_cli(monkeypatch, opened, "--root-path", PREFIX)
    assert options["root_path"] == PREFIX
    proxied = TestClient(app, root_path=PREFIX)
    for route in _bundle_routes(opened):
        assert proxied.get(f"{PREFIX}{route}").status_code == 401, route
        assert proxied.get(f"{PREFIX}{route}", headers=BEARER).status_code in {200, 404}, route


def test_tokenless_bundle_serve_refuses_a_foreign_host_and_a_post_without_csrf(
    views: dict[str, OpenedBundle], monkeypatch: pytest.MonkeyPatch
) -> None:
    opened = views["local-copy"]
    monkeypatch.delenv("COPERCEIVE_TOKEN", raising=False)
    app, _ = _served_by_cli(monkeypatch, opened)
    rebound = TestClient(app, base_url="http://rebind.attacker.example:8731")
    for route in _bundle_routes(opened):
        assert rebound.get(route).status_code == 400, route
    local = TestClient(app, base_url="http://127.0.0.1:8731")
    assert local.get(f"/api/scenes/{SCENE}").status_code == 200
    assert local.post("/api/jobs", json={"name": "echo", "args": ["x"]}).status_code == 403


def test_bundle_jobs_database_is_private_and_passes_open_db(
    views: dict[str, OpenedBundle],
) -> None:
    from coperceive.app import jobs

    opened = views["included"]
    before = _tree(opened.root)
    app = _app(opened, token=TOKEN)
    database = Path(app.state.db_path)
    assert not database.resolve().is_relative_to(opened.root.resolve())
    assert oct(database.parent.stat().st_mode & 0o777) == oct(0o700)
    client = TestClient(app, headers=BEARER)
    made = client.post("/api/jobs", headers=CSRF, json={"name": "echo", "args": ["bundle"]})
    assert made.status_code == 200, made.text
    listed = client.get("/api/jobs")
    assert listed.status_code == 200
    assert [job["status"] for job in listed.json()] == ["ok"]
    assert database.is_file() and not database.is_symlink()
    assert os.stat(database).st_nlink == 1
    jobs.open_db(database).close()
    assert _tree(opened.root) == before
    assert not (opened.scenes_root / "jobs.sqlite").exists()
