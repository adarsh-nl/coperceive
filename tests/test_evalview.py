# SPDX-License-Identifier: Apache-2.0
"""Evaluation view data (``docs/spec/predictions.md`` §HTTP) without a web framework.

A predictions root with three runs over the cooperative fixture: ``synthetic`` and
``again`` are evaluated, ``imported`` only has ``boxes.parquet``. Expected row counts
are read back from ``matches.parquet`` rather than quoted.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import cast

import pytest
from coperceive.evaluation.config import DEFAULT_CONFIG
from coperceive.evaluation.evaluate import (
    MATCH_SCHEMA,
    EvaluationError,
    evaluate_run,
    read_matches,
)
from coperceive.evaluation.predictions import write_run
from coperceive.evalview import EvalViewError, frame_rows, runs_for_scene, series
from coperceive.store import write_scene

from tests.cooperative import cooperative_scene
from tests.eval_fixture import synthetic_predictions

CREATED = "2026-10-17T01:02:03Z"
SCENE = "coop"
TIME = 100_000


@dataclass(frozen=True)
class Root:
    scenes: Path
    predictions: Path


@pytest.fixture(scope="module")
def root(tmp_path_factory: pytest.TempPathFactory) -> Root:
    tmp = tmp_path_factory.mktemp("evalview")
    scene = cooperative_scene()
    scenes = tmp / "scenes"
    write_scene(replace(scene, scene_id=SCENE), scenes / SCENE)
    predictions = tmp / "predictions"
    meta, boxes = synthetic_predictions(scene)
    rows = tuple(replace(box, scene_id=SCENE) for box in boxes)
    for run_id in ("synthetic", "again", "imported"):
        write_run(predictions, run_id, replace(meta, run_id=run_id), rows)
    for run_id in ("synthetic", "again"):
        report = evaluate_run(scenes, predictions, run_id, DEFAULT_CONFIG, created_utc=CREATED)
        assert report.evaluated == (SCENE,), report
    return Root(scenes=scenes, predictions=predictions)


def _runs(body: dict[str, object]) -> dict[str, dict[str, object]]:
    return {cast(str, item["run_id"]): item for item in cast(list[dict[str, object]], body["runs"])}


def _rows(body: dict[str, object]) -> list[dict[str, object]]:
    return cast(list[dict[str, object]], body["rows"])


# --- /predictions ---


def test_runs_for_scene_lists_evaluated_runs_with_their_outputs(root: Root) -> None:
    runs = _runs(runs_for_scene(root.predictions, SCENE))
    assert set(runs) == {"synthetic", "again", "imported"}
    synthetic = runs["synthetic"]
    assert synthetic["evaluated"] is True
    run = cast(dict[str, object], synthetic["run"])
    assert run["run_id"] == "synthetic"
    assert run["format"] == "coperceive-predictions"
    config = cast(dict[str, object], synthetic["config"])
    assert config["display_threshold"] == 0.5
    outputs = cast(list[dict[str, object]], synthetic["outputs"])
    keys = {(o["agent_id"], o["source"]) for o in outputs}
    assert {("vehicle", "single"), ("vehicle", "fused"), ("uav", "single")} <= keys
    for output in outputs:
        assert set(output) == {"agent_id", "source", "totals", "ap", "cooperative"}


def test_unevaluated_run_is_listed_but_has_no_frame(root: Root) -> None:
    runs = _runs(runs_for_scene(root.predictions, SCENE))
    imported = runs["imported"]
    assert imported["evaluated"] is False
    assert imported["config"] is None
    assert imported["outputs"] == []
    with pytest.raises(EvalViewError) as info:
        frame_rows(root.predictions, "imported", SCENE, TIME, compare=None)
    assert info.value.status == 404
    assert "eval run" in str(info.value)
    with pytest.raises(EvalViewError) as info:
        series(root.predictions, "imported", SCENE, compare=None)
    assert info.value.status == 404


def test_runs_for_scene_is_empty_for_a_scene_without_predictions(root: Root) -> None:
    assert runs_for_scene(root.predictions, "other") == {"runs": []}


# --- /frame ---


def test_frame_rows_has_one_row_per_match_row_at_that_time(root: Root) -> None:
    table = read_matches(root.predictions / "synthetic" / SCENE)
    expected = [row for row in table.to_pylist() if row["time_us"] == TIME]
    assert expected, "the fixture has rows at 100000 µs"

    body = frame_rows(root.predictions, "synthetic", SCENE, TIME, compare=None)
    assert body["run_id"] == "synthetic"
    assert body["time_us"] == TIME
    assert body["display_threshold"] == 0.5
    assert body["score_threshold"] == 0.3
    assert body["compare"] is None
    rows = _rows(body)
    assert len(rows) == len(expected)
    extra = {"center", "size_lwh", "pose", "lines"}
    for row, source in zip(rows, expected, strict=True):
        assert set(row) == set(MATCH_SCHEMA.names) | extra
        assert row["kind"] == source["kind"]
        assert row["status"] == source["status"]
        assert row["center"] == [source["cx"], source["cy"], source["cz"]]
        assert row["size_lwh"] == [source["length"], source["width"], source["height"]]
        pose = cast(dict[str, object], row["pose"])
        assert set(pose) == {"qw", "qx", "qy", "qz", "tx", "ty", "tz"}
        assert pose["tx"] == source["cx"]
        lines = cast(list[object], row["lines"])
        assert len(lines) == 72
        assert all(isinstance(v, float) for v in lines)
    assert {row["status"] for row in rows} >= {"tp", "fp", "miss"}


def test_frame_rows_compare_carries_the_other_run(root: Root) -> None:
    body = frame_rows(root.predictions, "synthetic", SCENE, TIME, compare="again")
    compare = cast(dict[str, object], body["compare"])
    assert compare["run_id"] == "again"
    assert compare["time_us"] == TIME
    assert "compare" not in compare
    assert len(_rows(compare)) == len(_rows(body))


def test_frame_rows_at_a_time_without_rows_is_empty(root: Root) -> None:
    body = frame_rows(root.predictions, "synthetic", SCENE, 12_345, compare=None)
    assert _rows(body) == []


# --- /series ---


def test_series_is_aligned_per_output(root: Root) -> None:
    body = series(root.predictions, "synthetic", SCENE, compare=None)
    assert body["run_id"] == "synthetic"
    assert body["compare"] is None
    outputs = cast(list[dict[str, object]], body["outputs"])
    assert {(o["agent_id"], o["source"]) for o in outputs} >= {("vehicle", "single")}
    for output in outputs:
        assert set(output) == {
            "agent_id",
            "source",
            "times_us",
            "tp",
            "fp",
            "miss",
            "precision",
            "recall",
        }
        times = cast(list[int], output["times_us"])
        assert len(times) > 0
        for key in ("tp", "fp", "miss", "precision", "recall"):
            assert len(cast(list[object], output[key])) == len(times)
    vehicle = next(o for o in outputs if (o["agent_id"], o["source"]) == ("vehicle", "single"))
    assert cast(list[int], vehicle["times_us"]) == [0, 100_000, 200_000, 300_000]

    with_compare = series(root.predictions, "synthetic", SCENE, compare="again")
    compare = cast(dict[str, object], with_compare["compare"])
    assert compare["run_id"] == "again"


# --- errors ---


@pytest.mark.parametrize("bad", ["../x", "a/b", "a\\b", ".hidden", "", "."])
def test_bad_run_id_is_a_400(root: Root, bad: str) -> None:
    with pytest.raises(EvalViewError) as info:
        frame_rows(root.predictions, bad, SCENE, TIME, compare=None)
    assert info.value.status == 400
    with pytest.raises(EvalViewError) as info:
        series(root.predictions, bad, SCENE, compare=None)
    assert info.value.status == 400
    with pytest.raises(EvalViewError) as info:
        frame_rows(root.predictions, "synthetic", SCENE, TIME, compare=bad)
    assert info.value.status == 400


def test_bad_scene_id_is_a_400(root: Root) -> None:
    with pytest.raises(EvalViewError) as info:
        runs_for_scene(root.predictions, "../x")
    assert info.value.status == 400
    with pytest.raises(EvalViewError) as info:
        frame_rows(root.predictions, "synthetic", "../x", TIME, compare=None)
    assert info.value.status == 400


def test_unknown_run_or_scene_is_a_404(root: Root) -> None:
    with pytest.raises(EvalViewError) as info:
        frame_rows(root.predictions, "nope", SCENE, TIME, compare=None)
    assert info.value.status == 404
    with pytest.raises(EvalViewError) as info:
        frame_rows(root.predictions, "synthetic", "other", TIME, compare=None)
    assert info.value.status == 404
    with pytest.raises(EvalViewError) as info:
        frame_rows(root.predictions, "synthetic", SCENE, TIME, compare="nope")
    assert info.value.status == 404


# --- cache ---


def test_matches_cache_is_keyed_by_mtime(tmp_path: Path) -> None:
    from coperceive import evalview

    scene = cooperative_scene()
    scenes = tmp_path / "scenes"
    write_scene(replace(scene, scene_id=SCENE), scenes / SCENE)
    predictions = tmp_path / "predictions"
    meta, boxes = synthetic_predictions(scene)
    write_run(predictions, meta.run_id, meta, tuple(replace(b, scene_id=SCENE) for b in boxes))
    evaluate_run(scenes, predictions, meta.run_id, DEFAULT_CONFIG, created_utc=CREATED)
    matches = predictions / meta.run_id / SCENE / "matches.parquet"

    evalview.clear_cache()
    first = frame_rows(predictions, meta.run_id, SCENE, TIME, compare=None)
    second = frame_rows(predictions, meta.run_id, SCENE, TIME, compare=None)
    assert first == second
    assert evalview.cache_keys() == ((meta.run_id, SCENE, matches.stat().st_mtime_ns),)

    stamp = matches.stat().st_mtime_ns + 1_000_000_000
    os.utime(matches, ns=(stamp, stamp))
    frame_rows(predictions, meta.run_id, SCENE, TIME, compare=None)
    assert evalview.cache_keys() == ((meta.run_id, SCENE, stamp),)


# --- confinement (§Layout) ---


def test_a_symlinked_metrics_or_matches_file_outside_the_root_is_refused(tmp_path: Path) -> None:
    """Every path a reader opens is resolved and must stay under the predictions root."""
    from coperceive import evalview

    scene = cooperative_scene()
    scenes = tmp_path / "scenes"
    write_scene(replace(scene, scene_id=SCENE), scenes / SCENE)
    predictions = tmp_path / "predictions"
    meta, boxes = synthetic_predictions(scene)
    write_run(predictions, meta.run_id, meta, tuple(replace(b, scene_id=SCENE) for b in boxes))
    evaluate_run(scenes, predictions, meta.run_id, DEFAULT_CONFIG, created_utc=CREATED)
    directory = predictions / meta.run_id / SCENE
    outside = tmp_path / "outside"
    outside.mkdir()
    for name in ("metrics.json", "matches.parquet"):
        (directory / name).rename(outside / name)
        (directory / name).symlink_to(outside / name)
    evalview.clear_cache()

    with pytest.raises(EvaluationError, match="leaves"):
        series(predictions, meta.run_id, SCENE, compare=None)
    with pytest.raises(EvaluationError, match="leaves"):
        frame_rows(predictions, meta.run_id, SCENE, TIME, compare=None)
    with pytest.raises(EvaluationError, match="leaves"):
        runs_for_scene(predictions, SCENE)
