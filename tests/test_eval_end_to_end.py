# SPDX-License-Identifier: Apache-2.0
"""The whole step A chain on the cooperative fixture (plan, Task 11).

A scene is written to ``<tmp>/scenes/coop``, the synthetic predictions of
``tests/eval_fixture.py`` are dumped to JSON lines with a ``scene_id`` column,
then ``coperceive predictions import`` and ``coperceive eval run`` go through
``coperceive.cli.main``, the three prediction routes and the bird's-eye export
go through the FastAPI test client, and ``coperceive eval compare`` closes the
chain. The frame rows at 100000 µs are checked against the ``metrics.json``
sample row of the vehicle ``single`` output, so the file the page draws and the
file the report reads agree on the same numbers.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Protocol, cast

import pytest
from coperceive.app.server import create_app
from coperceive.cli import main
from coperceive.evaluation.predictions import PredictionBox
from coperceive.store import write_scene
from fastapi.testclient import TestClient

from tests.cooperative import cooperative_scene
from tests.eval_fixture import synthetic_predictions

SCENE_ID = "coop"
RUN_ID = "synthetic"
TIME_US = 100_000
CSRF = {"X-Coperceive-Request": "1"}

Row = dict[str, object]


class _Response(Protocol):
    """What this test reads from an HTTP response.

    starlette 1.8 returns ``httpx2.Response`` (not ``httpx.Response``), and the CI
    runner has reported TestClient's base class as unknown under strict pyright,
    so the calls are typed by this protocol and kept in two helpers.
    """

    status_code: int

    def json(self) -> object: ...


def _get(client: TestClient, path: str, params: dict[str, int] | None = None) -> _Response:
    return cast(_Response, client.get(path, params=params))  # pyright: ignore[reportUnknownMemberType]


def _post(client: TestClient, path: str, body: dict[str, object]) -> _Response:
    return cast(_Response, client.post(path, headers=CSRF, json=body))  # pyright: ignore[reportUnknownMemberType]


def _jsonl_row(box: PredictionBox) -> Row:
    rotation = box.box_from_rpy
    return {
        "scene_id": SCENE_ID,
        "agent_id": box.agent_id,
        "source": box.source,
        "time_us": box.time_us,
        "category": box.category,
        "score": box.score,
        "track_id": box.track_id,
        "cx": box.center[0],
        "cy": box.center[1],
        "cz": box.center[2],
        "length": box.size_lwh[0],
        "width": box.size_lwh[1],
        "height": box.size_lwh[2],
        "qw": rotation.qw,
        "qx": rotation.qx,
        "qy": rotation.qy,
        "qz": rotation.qz,
        "frame": box.frame.value,
    }


def _vehicle_single(rows: list[Row]) -> list[Row]:
    return [row for row in rows if row["agent_id"] == "vehicle" and row["source"] == "single"]


def _count(rows: list[Row], *, kind: str, status: str) -> int:
    return sum(1 for row in rows if row["kind"] == kind and row["status"] == status)


def test_import_run_serve_export_and_compare(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Scene and the JSON lines a training script would write.
    scene = replace(cooperative_scene(), scene_id=SCENE_ID)
    scenes = tmp_path / "scenes"
    write_scene(scene, scenes / SCENE_ID)
    (scenes / SCENE_ID / "COMPLETE.json").write_text("{}\n", encoding="utf-8")
    _, boxes = synthetic_predictions(scene)
    jsonl = tmp_path / "boxes.jsonl"
    jsonl.write_text("".join(json.dumps(_jsonl_row(box)) + "\n" for box in boxes), encoding="utf-8")

    # predictions import -> run.json and boxes.parquet under <scenes>/predictions.
    main(
        [
            "predictions",
            "import",
            str(jsonl),
            "--run",
            RUN_ID,
            "--root",
            str(scenes),
            "--score-note",
            "constant",
        ]
    )
    scene_out = scenes / "predictions" / RUN_ID / SCENE_ID
    assert (scenes / "predictions" / RUN_ID / "run.json").is_file()
    assert (scene_out / "boxes.parquet").is_file()

    # eval run -> matches.parquet and metrics.json next to it.
    main(["eval", "run", RUN_ID, "--root", str(scenes)])
    assert capsys.readouterr().out.splitlines()[-1] == "evaluated 1 skipped 0 errors 0"
    assert (scene_out / "matches.parquet").is_file()
    assert (scene_out / "metrics.json").is_file()
    metrics = cast(Row, json.loads((scene_out / "metrics.json").read_text(encoding="utf-8")))
    assert metrics["format"] == "coperceive-eval"

    # The three routes the page uses, with <scenes>/predictions found by default.
    client: TestClient = TestClient(create_app(root=scenes, token=None))
    listed = _get(client, f"/api/scenes/{SCENE_ID}/predictions")
    assert listed.status_code == 200
    runs = cast(list[Row], cast(Row, listed.json())["runs"])
    assert [run["run_id"] for run in runs] == [RUN_ID]
    assert runs[0]["evaluated"] is True

    frame = _get(client, f"/api/scenes/{SCENE_ID}/predictions/{RUN_ID}/frame", {"time_us": TIME_US})
    assert frame.status_code == 200
    frame_body = cast(Row, frame.json())
    assert frame_body["time_us"] == TIME_US
    frame_rows = cast(list[Row], frame_body["rows"])
    assert frame_rows
    assert all(len(cast(list[object], row["lines"])) == 72 for row in frame_rows)

    series = _get(client, f"/api/scenes/{SCENE_ID}/predictions/{RUN_ID}/series")
    assert series.status_code == 200
    series_outputs = cast(list[Row], cast(Row, series.json())["outputs"])
    assert {(o["agent_id"], o["source"]) for o in series_outputs} >= {("vehicle", "single")}

    # The frame rows and the metrics.json sample row of vehicle/single agree at 100000 µs.
    vehicle_rows = _vehicle_single(frame_rows)
    outputs = cast(list[Row], metrics["outputs"])
    output = next(o for o in outputs if o["agent_id"] == "vehicle" and o["source"] == "single")
    sample = next(s for s in cast(list[Row], output["samples"]) if s["time_us"] == TIME_US)
    tp, fp, miss = (cast(int, sample[key]) for key in ("tp", "fp", "miss"))
    assert _count(vehicle_rows, kind="prediction", status="tp") == tp
    assert _count(vehicle_rows, kind="gt", status="tp") == tp
    assert _count(vehicle_rows, kind="prediction", status="fp") == fp
    assert _count(vehicle_rows, kind="gt", status="miss") == miss
    assert tp > 0 and fp > 0 and miss > 0

    # The bird's-eye figure with the run's rows on it.
    bev = _post(
        client,
        f"/api/scenes/{SCENE_ID}/export/bev",
        {"format": "svg", "time_us": TIME_US, "run_id": RUN_ID, "points": False},
    )
    assert bev.status_code == 200
    saved = cast(Row, bev.json())
    figure = scenes / cast(str, saved["path"])
    assert figure.is_file()
    assert cast(int, saved["predictions"]) > 0
    assert "#1fa3a3" in figure.read_text(encoding="utf-8").lower()

    # eval compare of a run against itself: every delta is zero.
    main(["eval", "compare", RUN_ID, RUN_ID, "--root", str(scenes)])
    table = capsys.readouterr().out
    assert f"| {SCENE_ID} | vehicle | single |" in table
    assert "| all |" in table
    assert "+0.000" in table
