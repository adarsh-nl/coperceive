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
import json
import os
import shutil
from pathlib import Path
from typing import Any, cast

import pytest
from coperceive.app import serve
from coperceive.app.server import create_app
from coperceive.bundle import BundleExportRequest, OpenedBundle, export_bundle, open_bundle
from coperceive.cli import main
from coperceive.demo import create
from coperceive.store import read_scene
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
    """The local copy has 100 frames; only the bundle's own frames are served."""
    opened = views["local-copy"]
    client = TestClient(_app(opened))
    frames = _frames(opened)
    inside = client.get(f"/api/scenes/{SCENE}/frame", params={"time_us": frames[0]})
    sensors = inside.json()["point_sensors"]
    assert sensors
    outside = max(frames) + 100_000  # a frame the local copy has and the bundle lacks
    rows = cast(list[dict[str, object]], opened.report["scenes"])
    local = read_scene(Path(str(rows[0]["local_copy"])), points=False)
    assert outside in {pose.time_us for agent in local.agents for pose in agent.poses}
    for row in sensors:
        points = client.get(
            f"/api/scenes/{SCENE}/points",
            params={"time_us": outside, "agent_id": row["agent_id"], "sensor_id": row["sensor_id"]},
        )
        assert points.status_code != 200 or not points.content, row
    camera = _camera(client.get(f"/api/scenes/{SCENE}").json())
    image = client.get(
        f"/api/scenes/{SCENE}/image",
        params={
            "time_us": outside,
            "agent_id": camera["agent_id"],
            "sensor_id": camera["sensor_id"],
        },
    )
    assert image.status_code == 404


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


def _real_copy(demo_root: Path, folder: Path, *, scene_id: str = SCENE) -> Path:
    """A full copy of the demo scene (sweeps and images) at ``folder``."""
    shutil.copytree(demo_root / "scenes" / SCENE, folder)
    if scene_id != SCENE:
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        manifest["scene_id"] = scene_id
        (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return folder


def _sweeps_served(
    served: Path, report: dict[str, object], local_copy: str, roots: list[Path]
) -> int:
    """``point_sweeps`` of the summary when open.json names ``local_copy``."""
    report = json.loads(json.dumps(report))
    report["scenes"][0]["local_copy"] = local_copy  # type: ignore[index]
    report["library_roots"] = [str(root) for root in roots]
    path = served.parent / f"open-{len(list(served.parent.glob('open-*.json')))}.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    app = create_app(
        root=served / "scenes", predictions=served / "predictions", bundle_info=path, token=None
    )
    summary = TestClient(app).get(f"/api/scenes/{SCENE}")
    assert summary.status_code == 200, summary.text
    return int(summary.json()["point_sweeps"])


@pytest.mark.parametrize(
    "case", ["relative", "other-scene-id", "inside-the-bundle", "outside-the-library"]
)
def test_a_local_copy_that_fails_one_check_is_not_served(
    views: dict[str, OpenedBundle],
    demo_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    """Each bad local_copy holds real sweeps, so only the guard under test refuses it."""
    opened = views["local-copy"]
    report = dict(opened.report)
    served = tmp_path / "served"
    shutil.copytree(opened.root, served)
    library = tmp_path / "library"
    good = _real_copy(demo_root, library / SCENE)
    # The control: a real copy inside the recorded library root is served.
    assert _sweeps_served(served, report, str(good), [library]) > 0
    if case == "relative":
        monkeypatch.chdir(tmp_path)
        bad = "library/" + SCENE
        assert (tmp_path / bad / "points.bin").is_file()
    elif case == "other-scene-id":
        bad = str(_real_copy(demo_root, library / "renamed" / SCENE, scene_id="another-scene"))
    elif case == "inside-the-bundle":
        bad = str(_real_copy(demo_root, served / "scenes" / ".inside" / SCENE))
        library = served
    else:
        bad = str(_real_copy(demo_root, tmp_path / "elsewhere" / SCENE))
    assert _sweeps_served(served, report, bad, [library]) == 0


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
