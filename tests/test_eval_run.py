# SPDX-License-Identifier: Apache-2.0
"""Scene evaluation, the match table, the metrics file, the run driver and the reports
(``docs/spec/predictions.md`` §Matching, §Metrics, §Cooperative views,
§``matches.parquet``, §``metrics.json``) on the cooperative fixture with the synthetic
predictions of ``tests/eval_fixture.py``.

Expected numbers are counted from the fixture here rather than quoted. The assertions
stay on the 100000 µs sample: the fixture's 0.3 m cone at 200000 µs, shifted 0.1 m,
has a BEV IoU of exactly 0.5 against the default display threshold, which rounding may
put on either side.
"""

from __future__ import annotations

import json
import math
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest
from coperceive.evaluation.config import DEFAULT_CONFIG, config_json, parse_config
from coperceive.evaluation.evaluate import (
    MATCH_SCHEMA,
    METRICS_FORMAT,
    METRICS_VERSION,
    RunReport,
    SceneEvaluation,
    evaluate_run,
    evaluate_scene,
    read_matches,
    read_metrics,
    world_box,
    write_scene_evaluation,
)
from coperceive.evaluation.predictions import PredictionBox, RunMeta, write_run
from coperceive.evaluation.report import Row, collect, render_compare, render_report
from coperceive.model import BoxFrame, Scene
from coperceive.se3 import SE3
from coperceive.store import write_scene

from tests.cooperative import cooperative_scene
from tests.eval_fixture import synthetic_predictions

CREATED = "2026-10-17T01:02:03Z"

MATCH_COLUMNS = [
    "kind",
    "agent_id",
    "source",
    "time_us",
    "category",
    "row",
    "score",
    "track_id",
    "visibility",
    "status",
    "pair_id",
    "iou_bev",
    "centre_error_m",
    "yaw_error_deg",
    "cx",
    "cy",
    "cz",
    "length",
    "width",
    "height",
    "qw",
    "qx",
    "qy",
    "qz",
    "status_single",
    "status_fused",
    "other_kind",
    "seen_by_other_gt",
    "other_agent_tp",
]


@pytest.fixture(scope="module")
def evaluated() -> tuple[Scene, RunMeta, tuple[PredictionBox, ...], SceneEvaluation]:
    scene = cooperative_scene()
    meta, boxes = synthetic_predictions(scene)
    result = evaluate_scene(scene, meta, boxes, DEFAULT_CONFIG, created_utc=CREATED)
    return scene, meta, boxes, result


def _output(metrics: dict[str, object], agent_id: str, source: str) -> dict[str, object]:
    for item in cast(list[dict[str, object]], metrics["outputs"]):
        if item["agent_id"] == agent_id and item["source"] == source:
            return item
    raise AssertionError(f"no output {agent_id}/{source}")


def _sample(output: dict[str, object], time_us: int) -> dict[str, object]:
    for item in cast(list[dict[str, object]], output["samples"]):
        if item["time_us"] == time_us:
            return item
    raise AssertionError(f"no sample at {time_us}")


def _gt_rows(
    result: SceneEvaluation, agent_id: str, source: str, time_us: int
) -> list[dict[str, object]]:
    return [
        row
        for row in result.matches
        if row["kind"] == "gt"
        and row["agent_id"] == agent_id
        and row["source"] == source
        and row["time_us"] == time_us
    ]


def _n_gt(scene: Scene, agent_id: str, time_us: int) -> int:
    return sum(1 for a in scene.annotations if a.agent_id == agent_id and a.time_us == time_us)


# --- world placement ---


def test_world_box_places_a_body_box_with_the_pose_at_that_time() -> None:
    scene = cooperative_scene()
    body = world_box(
        scene, "vehicle", 100_000, (1.0, 2.0, 3.0), (4.0, 2.0, 1.5), SE3.identity(), BoxFrame.BODY
    )
    assert body.center == pytest.approx((11.0, 2.0, 3.0))
    assert body.size_lwh == (4.0, 2.0, 1.5)
    world = world_box(
        scene, "vehicle", 100_000, (1.0, 2.0, 3.0), (4.0, 2.0, 1.5), SE3.identity(), BoxFrame.WORLD
    )
    assert world.center == (1.0, 2.0, 3.0)


# --- counts at the operating point (§Matching, §Metrics) ---


def test_vehicle_single_counts_at_100000(
    evaluated: tuple[Scene, RunMeta, tuple[PredictionBox, ...], SceneEvaluation],
) -> None:
    scene, _meta, _boxes, result = evaluated
    n_vehicle_gt = _n_gt(scene, "vehicle", 100_000)
    sample = _sample(_output(result.metrics, "vehicle", "single"), 100_000)
    # Track 99 is not predicted and track 5 is predicted as a truck: two misses; the
    # mislabelled truck and the far car are the two false positives.
    assert sample["tp"] == n_vehicle_gt - 2
    assert sample["fp"] == 2
    assert sample["miss"] == 2
    assert sample["ignored_gt"] == 0
    assert sample["precision"] == pytest.approx((n_vehicle_gt - 2) / n_vehicle_gt)
    assert sample["recall"] == pytest.approx((n_vehicle_gt - 2) / n_vehicle_gt)


def test_vehicle_fused_recovers_track_99_at_100000(
    evaluated: tuple[Scene, RunMeta, tuple[PredictionBox, ...], SceneEvaluation],
) -> None:
    scene, _meta, _boxes, result = evaluated
    n_vehicle_gt = _n_gt(scene, "vehicle", 100_000)
    sample = _sample(_output(result.metrics, "vehicle", "fused"), 100_000)
    assert sample["tp"] == n_vehicle_gt - 1
    assert sample["fp"] == 1
    assert sample["miss"] == 1


def test_samples_cover_every_pose_time_in_order(
    evaluated: tuple[Scene, RunMeta, tuple[PredictionBox, ...], SceneEvaluation],
) -> None:
    scene, _meta, boxes, result = evaluated
    output = _output(result.metrics, "vehicle", "single")
    samples = cast(list[dict[str, object]], output["samples"])
    assert [s["time_us"] for s in samples] == list(scene.pose_times("vehicle"))
    # A pose time without ground truth and with one far car: one fp, nothing else.
    assert _sample(output, 0) == {
        "time_us": 0,
        "tp": 0,
        "fp": 1,
        "miss": 0,
        "ignored_gt": 0,
        "precision": 0.0,
        "recall": None,
    }
    totals = cast(dict[str, object], output["totals"])
    n_rows = sum(1 for b in boxes if b.agent_id == "vehicle" and b.source == "single")
    assert totals["predictions"] == n_rows
    assert totals["tp"] == sum(cast(int, s["tp"]) for s in samples)
    assert totals["gt"] == cast(int, totals["tp"]) + cast(int, totals["miss"])


# --- cooperative views (§Cooperative views) ---


def test_cooperative_counts_for_the_vehicle(
    evaluated: tuple[Scene, RunMeta, tuple[PredictionBox, ...], SceneEvaluation],
) -> None:
    _scene, _meta, _boxes, result = evaluated
    single = cast(dict[str, object], _output(result.metrics, "vehicle", "single")["cooperative"])
    fused = cast(dict[str, object], _output(result.metrics, "vehicle", "fused")["cooperative"])
    assert single["recovered"] == 1
    assert single["lost_by_fusion"] == 0
    assert single == fused
    # The uav has no fused output: nothing enters its counts.
    uav = cast(dict[str, object], _output(result.metrics, "uav", "single")["cooperative"])
    assert uav == {
        "recovered": 0,
        "lost_by_fusion": 0,
        "only_other_agent": 0,
        "nobody": 0,
        "recovery_rate": None,
    }


def test_track_42_vehicle_row_is_seen_by_the_uav_as_a_shared_track(
    evaluated: tuple[Scene, RunMeta, tuple[PredictionBox, ...], SceneEvaluation],
) -> None:
    _scene, _meta, _boxes, result = evaluated
    rows = {row["track_id"]: row for row in _gt_rows(result, "vehicle", "single", 100_000)}
    shared = rows["42"]
    assert shared["seen_by_other_gt"] is True
    assert shared["other_kind"] == "shared_track"
    assert shared["other_agent_tp"] is True
    assert shared["status_single"] == "tp"
    assert shared["status_fused"] == "tp"
    recovered = rows["99"]
    assert recovered["status"] == "miss"
    assert recovered["status_single"] == "miss"
    assert recovered["status_fused"] == "tp"
    assert recovered["seen_by_other_gt"] is False
    assert recovered["other_kind"] is None
    assert recovered["other_agent_tp"] is None
    # The same cooperative columns on the fused output's copy of the gt rows.
    fused_rows = {row["track_id"]: row for row in _gt_rows(result, "vehicle", "fused", 100_000)}
    cooperative_keys = (
        "status_single",
        "status_fused",
        "seen_by_other_gt",
        "other_kind",
        "other_agent_tp",
    )
    for key in cooperative_keys:
        assert fused_rows["42"][key] == shared[key]
    # A prediction row carries none of them.
    prediction = next(row for row in result.matches if row["kind"] == "prediction")
    assert prediction["status_single"] is None
    assert prediction["seen_by_other_gt"] is None
    assert prediction["visibility"] is None


# --- matches.parquet (§matches.parquet) ---


def test_matches_round_trip_with_the_spec_columns(
    evaluated: tuple[Scene, RunMeta, tuple[PredictionBox, ...], SceneEvaluation],
    tmp_path: Path,
) -> None:
    _scene, _meta, boxes, result = evaluated
    assert MATCH_SCHEMA.names == MATCH_COLUMNS
    write_scene_evaluation(tmp_path, result)
    table = read_matches(tmp_path)
    assert table.schema.names == MATCH_COLUMNS
    rows = table.to_pylist()
    assert len(rows) == len(result.matches)
    assert rows == [dict(row) for row in result.matches]
    # Outputs in first-appearance order, then time, then gt before prediction.
    outputs: list[tuple[object, object]] = []
    for row in rows:
        key = (row["agent_id"], row["source"])
        if key not in outputs:
            outputs.append(key)
    first_seen: list[tuple[str, str]] = []
    for box in boxes:
        if (box.agent_id, box.source) not in first_seen:
            first_seen.append((box.agent_id, box.source))
    assert outputs == first_seen
    for key in outputs:
        times = [row["time_us"] for row in rows if (row["agent_id"], row["source"]) == key]
        assert times == sorted(times)
        for time_us in sorted(set(times)):
            kinds = [
                row["kind"]
                for row in rows
                if (row["agent_id"], row["source"]) == key and row["time_us"] == time_us
            ]
            assert kinds == sorted(kinds)  # "gt" < "prediction"
    # Every prediction row is there once, with its boxes.parquet row index.
    prediction_rows = [row["row"] for row in rows if row["kind"] == "prediction"]
    assert sorted(prediction_rows) == list(range(len(boxes)))


def test_pair_ids_join_a_tp_prediction_to_its_gt(
    evaluated: tuple[Scene, RunMeta, tuple[PredictionBox, ...], SceneEvaluation],
) -> None:
    _scene, _meta, _boxes, result = evaluated
    tp_predictions = [
        row for row in result.matches if row["kind"] == "prediction" and row["status"] == "tp"
    ]
    assert tp_predictions
    ids = [row["pair_id"] for row in tp_predictions]
    assert len(set(ids)) == len(ids)
    assert min(cast(list[int], ids)) == 0
    for row in tp_predictions:
        partners = [
            other
            for other in result.matches
            if other["kind"] == "gt" and other["pair_id"] == row["pair_id"]
        ]
        assert len(partners) == 1
        gt = partners[0]
        assert (gt["agent_id"], gt["source"], gt["time_us"]) == (
            row["agent_id"],
            row["source"],
            row["time_us"],
        )
        assert gt["status"] == "tp"
        assert gt["category"] == row["category"]
        assert gt["iou_bev"] == row["iou_bev"]
        assert cast(float, row["centre_error_m"]) == pytest.approx(0.1)
        # IoU >= the display threshold; the cone pair sits exactly on 0.5.
        assert cast(float, row["iou_bev"]) >= 0.5
    for row in result.matches:
        if row["status"] != "tp":
            assert row["pair_id"] is None
            assert row["iou_bev"] is None
            assert row["centre_error_m"] is None
            assert row["yaw_error_deg"] is None


def test_boxes_are_written_in_the_world_frame(
    evaluated: tuple[Scene, RunMeta, tuple[PredictionBox, ...], SceneEvaluation],
) -> None:
    scene, _meta, _boxes, result = evaluated
    # Vehicle gt row 0 is body (8, 1, 0.5) at 100000 µs, where the vehicle is at (10, 0, 0).
    row = next(
        r
        for r in result.matches
        if r["kind"] == "gt" and r["agent_id"] == "vehicle" and r["row"] == 0
    )
    assert scene.annotations[0].center == (8.0, 1.0, 0.5)
    assert (row["cx"], row["cy"], row["cz"]) == pytest.approx((18.0, 1.0, 0.5))
    assert (row["length"], row["width"], row["height"]) == (4.0, 2.0, 1.5)
    assert (row["qw"], row["qx"], row["qy"], row["qz"]) == pytest.approx((1.0, 0.0, 0.0, 0.0))
    assert row["visibility"] == 1.0
    assert row["score"] is None


# --- metrics.json (§metrics.json, Review Focus 3) ---


def _walk(value: object, seen: list[object]) -> None:
    seen.append(value)
    if isinstance(value, dict):
        for key, item in cast(dict[object, object], value).items():
            assert isinstance(key, str)
            assert key not in {"None", "null", "nan"}
            _walk(item, seen)
    elif isinstance(value, list):
        for item in cast(list[object], value):
            _walk(item, seen)
    elif isinstance(value, float):
        assert math.isfinite(value)


def test_metrics_json_has_no_nan_and_a_zero_ap_for_an_unpredicted_class(
    evaluated: tuple[Scene, RunMeta, tuple[PredictionBox, ...], SceneEvaluation],
    tmp_path: Path,
) -> None:
    scene, meta, _boxes, result = evaluated
    write_scene_evaluation(tmp_path, result)
    text = (tmp_path / "metrics.json").read_text(encoding="utf-8")
    assert "NaN" not in text and "Infinity" not in text
    loaded = cast(dict[str, object], json.loads(text))
    _walk(loaded, [])
    assert loaded == result.metrics
    assert read_metrics(tmp_path) == result.metrics
    assert loaded["format"] == METRICS_FORMAT == "coperceive-eval"
    assert loaded["format_version"] == METRICS_VERSION == 1
    assert loaded["run_id"] == meta.run_id
    assert loaded["scene_id"] == scene.scene_id
    assert loaded["created_utc"] == CREATED
    assert isinstance(loaded["coperceive_version"], str) and loaded["coperceive_version"]
    assert loaded["config"] == config_json(DEFAULT_CONFIG)
    single = _output(loaded, "vehicle", "single")
    ap = cast(dict[str, dict[str, object]], single["ap"])
    assert set(ap) == {"0.5", "0.7"}
    # Pedestrian (track 99) has ground truth on the vehicle and no single prediction.
    assert ap["0.5"]["pedestrian"] == 0.0
    assert ap["0.7"]["pedestrian"] == 0.0
    # Every run.json category gets a key; one without ground truth is null.
    for category in meta.categories:
        assert category in ap["0.5"]
    assert ap["0.5"]["truck"] is None
    assert ap["0.5"]["sign"] is None
    assert ap["0.5"]["pole"] is None
    # Three car boxes, two matched at 0.9 above four far cars at 0.8: AP 2/3 at both.
    assert ap["0.5"]["car"] == pytest.approx(2 / 3)
    assert ap["0.7"]["car"] == pytest.approx(2 / 3)
    assert isinstance(ap["0.5"]["mean"], float)
    errors = cast(dict[str, dict[str, object]], single["tp_errors"])
    assert cast(float, errors["centre_m"]["mean"]) == pytest.approx(0.1)
    assert cast(float, errors["centre_m"]["median"]) == pytest.approx(0.1)
    assert errors["yaw_deg"]["mean_abs"] == pytest.approx(0.0)
    assert errors["size_m"] == {"length": 0.0, "width": 0.0, "height": 0.0}
    # Every output has the full shape, including one with no true positive anywhere.
    for output in cast(list[dict[str, object]], loaded["outputs"]):
        assert set(output) == {
            "agent_id",
            "source",
            "samples",
            "totals",
            "ap",
            "tp_errors",
            "cooperative",
        }
        assert set(cast(dict[str, object], output["totals"])) == {
            "tp",
            "fp",
            "miss",
            "ignored_gt",
            "gt",
            "predictions",
            "precision",
            "recall",
        }


def test_ap_pass_ignores_the_score_threshold_and_statuses_do_not() -> None:
    scene = cooperative_scene()
    meta, boxes = synthetic_predictions(scene)
    config = parse_config({"score_threshold": 0.99})
    result = evaluate_scene(scene, meta, boxes, config, created_utc=CREATED)
    single = _output(result.metrics, "vehicle", "single")
    totals = cast(dict[str, object], single["totals"])
    assert totals["tp"] == 0 and totals["fp"] == 0
    assert totals["precision"] is None
    assert all(
        row["status"] == "below_threshold"
        for row in result.matches
        if row["kind"] == "prediction" and row["source"] == "single"
    )
    ap = cast(dict[str, dict[str, object]], single["ap"])
    assert ap["0.5"]["car"] == pytest.approx(2 / 3)
    assert cast(dict[str, object], single["tp_errors"])["centre_m"] == {
        "mean": None,
        "median": None,
    }


def test_ignored_gt_is_counted_and_not_missed() -> None:
    scene = cooperative_scene()
    meta, boxes = synthetic_predictions(scene)
    # The uav's track 42 has visibility 0.4; its prediction then is ignored, not a tp.
    # Under centre distance so that the uav's 0.2 m sign, shifted 0.1 m (IoU 1/3), is
    # a tp and only the visibility rule moves the counts.
    config = parse_config(
        {
            "criterion": "centre",
            "thresholds": [2.0, 1.0],
            "display_threshold": 2.0,
            "ignore_gt_visibility_below": 0.5,
        }
    )
    result = evaluate_scene(scene, meta, boxes, config, created_utc=CREATED)
    sample = _sample(_output(result.metrics, "uav", "single"), 100_000)
    assert sample["ignored_gt"] == 1
    n_uav_gt = _n_gt(scene, "uav", 100_000)
    assert sample["tp"] == n_uav_gt - 1
    assert sample["fp"] == 0
    assert sample["miss"] == 0
    rows = {row["track_id"]: row for row in _gt_rows(result, "uav", "single", 100_000)}
    assert rows["42"]["status"] == "ignored"
    ignored_prediction = next(
        row
        for row in result.matches
        if row["kind"] == "prediction" and row["agent_id"] == "uav" and row["track_id"] == "42"
    )
    assert ignored_prediction["status"] == "ignored"
    ap = cast(dict[str, dict[str, object]], _output(result.metrics, "uav", "single")["ap"])
    assert set(ap) == {"2.0", "1.0"}
    assert ap["2.0"]["car"] is None  # no kept car ground truth on the uav
    assert ap["2.0"]["sign"] == 1.0 and ap["2.0"]["truck"] == 1.0


# --- run driver ---


def _scenes_root(tmp_path: Path, *scene_ids: str) -> tuple[Path, Scene]:
    scene = cooperative_scene()
    root = tmp_path / "scenes"
    for scene_id in scene_ids:
        write_scene(replace(scene, scene_id=scene_id), root / scene_id)
    return root, scene


def _write_fixture_run(
    predictions: Path, scene: Scene, scene_ids: tuple[str, ...]
) -> tuple[RunMeta, tuple[PredictionBox, ...]]:
    meta, boxes = synthetic_predictions(scene)
    rows = tuple(replace(box, scene_id=scene_id) for scene_id in scene_ids for box in boxes)
    write_run(predictions, meta.run_id, meta, rows)
    return meta, boxes


def test_evaluate_run_evaluates_every_scene_with_boxes_and_skips_the_rest(
    tmp_path: Path,
) -> None:
    root, scene = _scenes_root(tmp_path, "coop-a", "coop-b", "coop-c")
    predictions = tmp_path / "predictions"
    meta, boxes = _write_fixture_run(predictions, scene, ("coop-a", "coop-b"))
    report = evaluate_run(root, predictions, meta.run_id, DEFAULT_CONFIG, created_utc=CREATED)
    assert report == RunReport(evaluated=("coop-a", "coop-b"), skipped=("coop-c",), errors=())
    for scene_id in ("coop-a", "coop-b"):
        directory = predictions / meta.run_id / scene_id
        metrics = read_metrics(directory)
        assert metrics["scene_id"] == scene_id
        assert metrics["run_id"] == meta.run_id
        table = read_matches(directory)
        assert table.num_rows > len(boxes)
    assert not (predictions / meta.run_id / "coop-c").exists()
    expected = evaluate_scene(scene, meta, boxes, DEFAULT_CONFIG, created_utc=CREATED)
    actual = read_metrics(predictions / meta.run_id / "coop-a")
    assert actual["outputs"] == expected.metrics["outputs"]


def test_evaluate_run_honours_scene_ids(tmp_path: Path) -> None:
    root, scene = _scenes_root(tmp_path, "coop-a", "coop-b")
    predictions = tmp_path / "predictions"
    meta, _boxes = _write_fixture_run(predictions, scene, ("coop-a", "coop-b"))
    report = evaluate_run(
        root,
        predictions,
        meta.run_id,
        DEFAULT_CONFIG,
        scene_ids=["coop-b", "coop-zz"],
        created_utc=CREATED,
    )
    assert report.evaluated == ("coop-b",)
    assert report.skipped == ()
    assert len(report.errors) == 1 and report.errors[0].startswith("coop-zz: ")
    assert not (predictions / meta.run_id / "coop-a" / "metrics.json").exists()


def test_evaluate_run_reports_a_validation_error_and_continues(tmp_path: Path) -> None:
    root, scene = _scenes_root(tmp_path, "coop-a", "coop-b")
    predictions = tmp_path / "predictions"
    meta, boxes = synthetic_predictions(scene)
    bad = replace(boxes[0], scene_id="coop-a", agent_id="ghost")
    rows = (bad,) + tuple(replace(box, scene_id="coop-b") for box in boxes)
    write_run(predictions, meta.run_id, meta, rows)
    report = evaluate_run(root, predictions, meta.run_id, DEFAULT_CONFIG, created_utc=CREATED)
    assert report.evaluated == ("coop-b",)
    assert report.skipped == ()
    assert len(report.errors) == 1
    assert report.errors[0].startswith("coop-a: ")
    assert "row 0" in report.errors[0] and "ghost" in report.errors[0]
    assert not (predictions / meta.run_id / "coop-a" / "metrics.json").exists()
    assert not (predictions / meta.run_id / "coop-a" / "matches.parquet").exists()
    assert (predictions / meta.run_id / "coop-b" / "metrics.json").is_file()


# --- report and compare ---


def _table(text: str) -> list[list[str]]:
    lines = [line for line in text.splitlines() if line.startswith("|")]
    return [[cell.strip() for cell in line.strip("|").split("|")] for line in lines]


def test_render_report_has_the_aggregate_row_and_an_ap_column(tmp_path: Path) -> None:
    root, scene = _scenes_root(tmp_path, "coop-a", "coop-b")
    predictions = tmp_path / "predictions"
    meta, boxes = _write_fixture_run(predictions, scene, ("coop-a", "coop-b"))
    evaluate_run(root, predictions, meta.run_id, DEFAULT_CONFIG, created_utc=CREATED)
    rows = collect(root, predictions, meta.run_id)
    n_outputs = len({(box.agent_id, box.source) for box in boxes})
    assert len(rows) == 2 * n_outputs
    assert all(isinstance(row, Row) for row in rows)
    assert [row.scene_id for row in rows[:n_outputs]] == ["coop-a"] * n_outputs
    text = render_report(rows, DEFAULT_CONFIG.thresholds)
    table = _table(text)
    header = table[0]
    assert header == [
        "scene",
        "agent",
        "source",
        "gt",
        "predictions",
        "tp",
        "fp",
        "miss",
        "precision",
        "recall",
        "AP@0.5",
        "AP@0.7",
        "recovered",
        "lost_by_fusion",
    ]
    body = table[2:]
    assert len(body) == len(rows) + 1
    aggregate = body[-1]
    assert aggregate[0] == "all"
    tp_column = header.index("tp")
    assert int(aggregate[tp_column]) == sum(int(line[tp_column]) for line in body[:-1])
    gt_column = header.index("gt")
    assert int(aggregate[gt_column]) == sum(cast(int, row.values["gt"]) for row in rows)
    recall_column = header.index("recall")
    expected_recall = int(aggregate[tp_column]) / int(aggregate[gt_column])
    assert float(aggregate[recall_column]) == pytest.approx(expected_recall, abs=5e-4)


def test_render_compare_shows_zero_deltas_for_the_same_files(tmp_path: Path) -> None:
    root, scene = _scenes_root(tmp_path, "coop-a")
    predictions = tmp_path / "predictions"
    meta, _boxes = _write_fixture_run(predictions, scene, ("coop-a",))
    evaluate_run(root, predictions, meta.run_id, DEFAULT_CONFIG, created_utc=CREATED)
    rows = collect(root, predictions, meta.run_id)
    text = render_compare(rows, rows, DEFAULT_CONFIG.thresholds)
    assert "+0.000" in text
    table = _table(text)
    assert table[0][:3] == ["scene", "agent", "source"]
    for line in table[2:]:
        for cell in line[3:]:
            assert cell in {"+0", "+0.000", "n/a"}, cell
    assert table[-1][0] == "all"
    assert len(table) == len(rows) + 3
