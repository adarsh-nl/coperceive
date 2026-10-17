# SPDX-License-Identifier: Apache-2.0
"""Scene evaluation, the match table, the metrics file, the run driver and the reports
(``docs/spec/predictions.md`` §Matching, §Metrics, §Cooperative views,
§``matches.parquet``, §``metrics.json``) on the cooperative fixture with the synthetic
predictions of ``tests/eval_fixture.py``.

Expected numbers are counted from the fixture here rather than quoted. The count
assertions stay on the 100000 µs sample, where every box is a 0.1 m shift along x. The
fixture's 0.3 m cone at 200000 µs is shifted 0.1 m along the diagonal (BEV IoU 0.41, an
``fp`` and a ``miss`` at both thresholds) so that no prediction sits exactly on the
display threshold.
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
    EvaluationError,
    RunReport,
    SceneEvaluation,
    evaluate_run,
    evaluate_scene,
    match_schema,
    read_matches,
    read_metrics,
    status_at_columns,
    world_box,
    write_scene_evaluation,
)
from coperceive.evaluation.geometry import bev_iou, centre_distance_m
from coperceive.evaluation.metrics import average_precision
from coperceive.evaluation.predictions import PredictionBox, RunMeta, write_run
from coperceive.evaluation.report import (
    Row,
    aggregate,
    collect,
    pooled_ap,
    render_compare,
    render_report,
)
from coperceive.model import (
    SCHEMA_VERSION,
    Agent,
    AgentType,
    Annotation,
    BoxFrame,
    Clock,
    Scene,
    StampedPose,
)
from coperceive.se3 import SE3
from coperceive.store import write_scene

from tests.cooperative import cooperative_scene
from tests.eval_fixture import synthetic_predictions, synthetic_run_meta

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
    # The default read is the spec's columns; the per-threshold AP statuses follow them
    # in the file (§matches.parquet, one ``status_at_<i>`` per entry of ``thresholds``).
    table = read_matches(tmp_path)
    assert table.schema.names == MATCH_COLUMNS
    assert status_at_columns(2) == ["status_at_0", "status_at_1"]
    assert match_schema(2).names == [*MATCH_COLUMNS, "status_at_0", "status_at_1"]
    full = read_matches(tmp_path, columns=match_schema(2).names)
    assert full.schema.names == [*MATCH_COLUMNS, "status_at_0", "status_at_1"]
    rows = full.to_pylist()
    assert len(rows) == len(result.matches)
    assert rows == [dict(row) for row in result.matches]
    assert table.to_pylist() == [{key: row[key] for key in MATCH_COLUMNS} for row in result.matches]
    with pytest.raises(EvaluationError, match="status_at_2"):
        read_matches(tmp_path, columns=match_schema(3).names)
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


def test_status_at_columns_hold_the_ap_pass_status_per_threshold(
    evaluated: tuple[Scene, RunMeta, tuple[PredictionBox, ...], SceneEvaluation],
) -> None:
    """``status_at_<i>`` is the status of the AP pass at ``thresholds[i]`` with the score
    threshold ignored: ``tp``, ``fp`` or ``ignored``; null on ground-truth rows."""
    scene, meta, boxes, result = evaluated
    for row in result.matches:
        if row["kind"] == "gt":
            assert row["status_at_0"] is None and row["status_at_1"] is None
            continue
        assert row["status_at_0"] in {"tp", "fp", "ignored"}
        assert row["status_at_1"] in {"tp", "fp", "ignored"}
        # Display index 0 and every fixture score above 0.3: status_at_0 is the status.
        assert row["status_at_0"] == row["status"]
    # The 4 x 2 cars shifted 0.1 m have IoU 0.95: tp at 0.7 too; the diagonal cone is fp at both.
    cars = [r for r in result.matches if r["kind"] == "prediction" and r["status"] == "tp"]
    assert cars and all(r["status_at_1"] == "tp" for r in cars)
    cone = next(r for r in result.matches if r["kind"] == "prediction" and r["category"] == "cone")
    assert (cone["status"], cone["status_at_0"], cone["status_at_1"]) == ("fp", "fp", "fp")
    # Under a score threshold nothing passes, the statuses say so and status_at does not.
    strict = evaluate_scene(
        scene, meta, boxes, parse_config({"score_threshold": 0.99}), created_utc=CREATED
    )
    predictions = [r for r in strict.matches if r["kind"] == "prediction"]
    assert all(r["status"] == "below_threshold" for r in predictions)
    assert [r["status_at_0"] for r in predictions] == [
        r["status_at_0"] for r in result.matches if r["kind"] == "prediction"
    ]
    # The AP in metrics.json is the walk over these very statuses.
    single = cast(dict[str, dict[str, object]], _output(result.metrics, "vehicle", "single")["ap"])
    records = [
        (cast(float, r["score"]), cast(str, r["status_at_1"]))
        for r in sorted(
            (
                r
                for r in result.matches
                if r["kind"] == "prediction"
                and (r["agent_id"], r["source"], r["category"]) == ("vehicle", "single", "car")
            ),
            key=lambda r: cast(int, r["row"]),
        )
    ]
    n_car_gt = sum(1 for a in scene.annotations if a.agent_id == "vehicle" and a.category == "car")
    assert average_precision(records, n_car_gt) == single["0.7"]["car"]


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
        # IoU >= the display threshold, and no fixture pair sits on it.
        assert cast(float, row["iou_bev"]) > 0.5
    for row in result.matches:
        if row["status"] != "tp":
            assert row["pair_id"] is None
            assert row["iou_bev"] is None
            assert row["centre_error_m"] is None
            assert row["yaw_error_deg"] is None


def test_no_fixture_prediction_sits_on_the_display_threshold(
    evaluated: tuple[Scene, RunMeta, tuple[PredictionBox, ...], SceneEvaluation],
) -> None:
    """The fixture is nudged off the boundary: the 0.3 m cone, shifted 0.1 m along x,
    would have IoU exactly 0.5; shifted along the diagonal it is a clear fp."""
    scene, _meta, boxes, result = evaluated
    cone_gt = next(a for a in scene.annotations if a.category == "cone")
    cone_pred = next(b for b in boxes if b.category == "cone" and b.source == "single")
    gt = world_box(
        scene,
        "vehicle",
        cone_gt.time_us,
        cone_gt.center,
        cone_gt.size_lwh,
        cone_gt.box_from_rpy,
        cone_gt.frame,
    )
    pred = world_box(
        scene,
        "vehicle",
        cone_pred.time_us,
        cone_pred.center,
        cone_pred.size_lwh,
        cone_pred.box_from_rpy,
        cone_pred.frame,
    )
    iou = bev_iou(gt, pred)
    assert abs(iou - 0.5) > 0.05
    assert centre_distance_m(gt, pred) == pytest.approx(0.1)
    cone_rows = [
        row
        for row in result.matches
        if row["category"] == "cone" and row["source"] == "single" and row["time_us"] == 200_000
    ]
    assert {row["kind"]: row["status"] for row in cone_rows} == {"gt": "miss", "prediction": "fp"}
    # Every other synthetic pair is a 0.1 m shift of a box at least 0.2 m wide: IoU > 0.5.
    for row in result.matches:
        if row["iou_bev"] is not None:
            assert cast(float, row["iou_bev"]) != 0.5


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


def test_readers_refuse_a_symlinked_file_that_leaves_the_scene_directory(
    evaluated: tuple[Scene, RunMeta, tuple[PredictionBox, ...], SceneEvaluation],
    tmp_path: Path,
) -> None:
    """§Layout: every path a reader opens is resolved and must stay under the root."""
    _scene, _meta, _boxes, result = evaluated
    outside = tmp_path / "outside"
    write_scene_evaluation(outside, result)
    directory = tmp_path / "root" / "run" / "scene"
    directory.mkdir(parents=True)
    (directory / "metrics.json").symlink_to(outside / "metrics.json")
    (directory / "matches.parquet").symlink_to(outside / "matches.parquet")
    with pytest.raises(EvaluationError, match="leaves"):
        read_metrics(directory)
    with pytest.raises(EvaluationError, match="leaves"):
        read_matches(directory)


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


def test_categories_outside_run_json_are_reported_and_a_class_without_gt_is_null() -> None:
    """Decision: the AP categories stay a union; a class with no ground truth is ``null``,
    not 0; a class absent from ``run.json`` is a warning, not an error."""
    scene = cooperative_scene()
    meta, boxes = synthetic_predictions(scene)
    bicycle = replace(_cone(100_000, (50.0, 50.0, 0.0)), category="bicycle")
    result = evaluate_scene(scene, meta, (*boxes, bicycle), DEFAULT_CONFIG, created_utc=CREATED)
    assert result.warnings == (
        "category 'bicycle' is not in run.json categories",
        "category 'cone' is not in run.json categories",
    )
    ap = cast(dict[str, dict[str, object]], _output(result.metrics, "vehicle", "single")["ap"])
    for threshold in ("0.5", "0.7"):
        # Predicted only, no ground truth anywhere: null.
        assert "bicycle" in ap[threshold] and ap[threshold]["bicycle"] is None
        # Listed in run.json, no vehicle ground truth (the truck prediction is a wrong class).
        assert ap[threshold]["truck"] is None
        # Ground truth only (track 99), no single prediction: 0.0, not null.
        assert ap[threshold]["pedestrian"] == 0.0
        # Ground truth outside run.json, predicted as a clear fp: 0.0.
        assert ap[threshold]["cone"] == 0.0
    # A scene whose classes are all listed gives no warning.
    listed = replace(meta, categories=(*meta.categories, "cone"))
    assert evaluate_scene(scene, listed, boxes, DEFAULT_CONFIG, created_utc=CREATED).warnings == ()


def _cone(time_us: int, center: tuple[float, float, float]) -> PredictionBox:
    """A world-frame cone prediction on the vehicle ``single`` output, score 0.9."""
    return PredictionBox(
        scene_id="coop-fixture",
        agent_id="vehicle",
        source="single",
        time_us=time_us,
        category="cone",
        score=0.9,
        track_id=None,
        center=center,
        size_lwh=(0.3, 0.3, 0.7),
        box_from_rpy=SE3.identity(),
        frame=BoxFrame.WORLD,
    )


def test_ap_ties_follow_file_order_across_sample_times() -> None:
    # §Metrics AP step 1: ties in file order. The fixture's only cone is the vehicle's
    # world-frame box at (21, 2, 0) at 200000 µs. One prediction sits on it (a tp) and an
    # equal-score cone at the earlier time 100000 µs is a fp. With the tp first in the file
    # the walk is tp, fp: AP 1.0. With the fp first: 0.5. Pose-time order would always put
    # the 100000 µs fp first and give 0.5 for both files.
    scene = cooperative_scene()
    meta = synthetic_run_meta()
    tp = _cone(200_000, (21.0, 2.0, 0.0))
    fp = _cone(100_000, (50.0, 50.0, 0.0))
    tp_first = evaluate_scene(scene, meta, (tp, fp), DEFAULT_CONFIG, created_utc=CREATED)
    fp_first = evaluate_scene(scene, meta, (fp, tp), DEFAULT_CONFIG, created_utc=CREATED)
    ap_tp_first = cast(
        dict[str, dict[str, object]], _output(tp_first.metrics, "vehicle", "single")["ap"]
    )
    ap_fp_first = cast(
        dict[str, dict[str, object]], _output(fp_first.metrics, "vehicle", "single")["ap"]
    )
    for threshold in ("0.5", "0.7"):
        assert ap_tp_first[threshold]["cone"] == 1.0
        assert ap_fp_first[threshold]["cone"] == 0.5


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
    # The fixture's cone is in the ground truth but not in run.json categories.
    assert report == RunReport(
        evaluated=("coop-a", "coop-b"),
        skipped=("coop-c",),
        errors=(),
        warnings=(
            "coop-a: category 'cone' is not in run.json categories",
            "coop-b: category 'cone' is not in run.json categories",
        ),
    )
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


def _tables(text: str) -> list[list[list[str]]]:
    """The Markdown tables of a report, each a list of rows of stripped cells."""
    tables: list[list[list[str]]] = []
    current: list[list[str]] = []
    for line in text.splitlines():
        if line.startswith("|"):
            current.append([cell.strip() for cell in line.strip("|").split("|")])
        elif current:
            tables.append(current)
            current = []
    if current:
        tables.append(current)
    return tables


def _table(text: str) -> list[list[str]]:
    """The first (main) table of a report."""
    return _tables(text)[0]


def _one_car_scene(scene_id: str) -> Scene:
    """One agent, one pose at 0, one world-frame 4 x 2 car at the origin."""
    vehicle = Agent(
        agent_id="vehicle",
        agent_type=AgentType.VEHICLE,
        poses=(StampedPose(0, SE3.identity()),),
    )
    car = Annotation(
        "vehicle",
        0,
        "1",
        "car",
        (0.0, 0.0, 0.0),
        (4.0, 2.0, 1.5),
        SE3.identity(),
        None,
        BoxFrame.WORLD,
    )
    return Scene(
        schema_version=SCHEMA_VERSION,
        scene_id=scene_id,
        world_frame="ENU",
        clock=Clock(unit="us", scale_note="pooled AP test"),
        agents=(vehicle,),
        sensors=(),
        annotations=(car,),
        objects=(),
    )


def _car(scene_id: str, score: float, cx: float, category: str = "car") -> PredictionBox:
    return PredictionBox(
        scene_id=scene_id,
        agent_id="vehicle",
        source="single",
        time_us=0,
        category=category,
        score=score,
        track_id=None,
        center=(cx, 0.0, 0.0),
        size_lwh=(4.0, 2.0, 1.5),
        box_from_rpy=SE3.identity(),
        frame=BoxFrame.WORLD,
    )


def test_headline_ap_pools_every_detection_across_scenes(tmp_path: Path) -> None:
    """§Metrics, Pooled AP (the normative case). Scene a: one GT car, one tp at 0.9.
    Scene b: one GT car, one fp at 0.8 and one tp at 0.7. Pooled over n_gt = 2 the walk
    is tp, fp, tp: precisions 1, 1/2, 2/3 at recalls 1/2, 1/2, 1; envelope 1 at r = 1/2
    and 2/3 at r = 1; AP = 1/2 * 1 + 1/2 * 2/3 = 5/6. The mean of the per-scene APs
    (1.0 and 0.5) would be 0.75."""
    root = tmp_path / "scenes"
    for scene_id in ("a", "b"):
        write_scene(_one_car_scene(scene_id), root / scene_id)
    predictions = tmp_path / "predictions"
    meta = RunMeta(
        run_id="pooled",
        model="test",
        score_note="constant",
        categories=("car", "pedestrian"),
        sources=(("single", "own sensors"),),
        created_utc=CREATED,
    )
    rows = (
        _car("a", 0.9, 0.0),
        _car("b", 0.8, 50.0),
        _car("b", 0.7, 0.0),
        _car("b", 0.6, 60.0, category="bicycle"),  # predicted only: no ground truth anywhere
    )
    write_run(predictions, meta.run_id, meta, rows)
    report = evaluate_run(root, predictions, meta.run_id, DEFAULT_CONFIG, created_utc=CREATED)
    assert report.errors == ()
    assert report.warnings == ("b: category 'bicycle' is not in run.json categories",)
    collected = collect(root, predictions, meta.run_id)
    assert [(row.scene_id, row.run_id) for row in collected] == [("a", "pooled"), ("b", "pooled")]
    assert [row.values["AP@0.5"] for row in collected] == [1.0, 0.5]

    per_class = pooled_ap(collected, DEFAULT_CONFIG.thresholds)
    assert per_class == {
        "0.5": {"bicycle": None, "car": pytest.approx(5 / 6), "pedestrian": None},
        "0.7": {"bicycle": None, "car": pytest.approx(5 / 6), "pedestrian": None},
    }
    total = aggregate(collected, DEFAULT_CONFIG.thresholds)
    assert total["AP@0.5"] == pytest.approx(5 / 6)
    assert total["gt"] == 2 and total["tp"] == 2 and total["fp"] == 2

    text = render_report(collected, DEFAULT_CONFIG.thresholds)
    main, per_class_table = _tables(text)
    header = main[0]
    ap_column = header.index("AP@0.5")
    body = {tuple(line[:3]): line for line in main[2:]}
    assert body[("a", "vehicle", "single")][ap_column] == "1.000"
    assert body[("b", "vehicle", "single")][ap_column] == "0.500"
    assert body[("all", "vehicle", "single")][ap_column] == "0.833"
    assert body[("all", "", "")][ap_column] == "0.833"
    assert "0.750" not in body[("all", "", "")]
    assert main[-1][:3] == ["all", "", ""]
    assert per_class_table[0] == ["agent", "source", "class", "AP@0.5", "AP@0.7"]
    assert per_class_table[2:] == [
        ["vehicle", "single", "bicycle", "n/a", "n/a"],
        ["vehicle", "single", "car", "0.833", "0.833"],
        ["vehicle", "single", "pedestrian", "n/a", "n/a"],
        ["all", "", "bicycle", "n/a", "n/a"],
        ["all", "", "car", "0.833", "0.833"],
        ["all", "", "pedestrian", "n/a", "n/a"],
    ]


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
    # Scene rows, one pooled ``all`` row per output, then the pooled ``all`` row.
    assert len(body) == len(rows) + n_outputs + 1
    scene_lines = body[: len(rows)]
    output_lines = body[len(rows) : -1]
    assert [tuple(line[:3]) for line in output_lines] == [
        ("all", agent, source)
        for agent, source in dict.fromkeys((row.agent_id, row.source) for row in rows)
    ]
    total = body[-1]
    assert total[:3] == ["all", "", ""]
    tp_column = header.index("tp")
    assert int(total[tp_column]) == sum(int(line[tp_column]) for line in scene_lines)
    gt_column = header.index("gt")
    assert int(total[gt_column]) == sum(cast(int, row.values["gt"]) for row in rows)
    recall_column = header.index("recall")
    expected_recall = int(total[tp_column]) / int(total[gt_column])
    assert float(total[recall_column]) == pytest.approx(expected_recall, abs=5e-4)
    # Two copies of one scene pool to the per-scene values; the per-output rows and the
    # mean over outputs differ from a mean of means only in what they pool.
    ap_column = header.index("AP@0.5")
    for line in output_lines:
        scene_line = next(s for s in scene_lines if s[1:3] == line[1:3])
        assert line[ap_column] == scene_line[ap_column]
        assert int(line[tp_column]) == 2 * int(scene_line[tp_column])


def test_render_report_shows_a_missing_count_as_n_a() -> None:
    # A truncated metrics.json leaves a count out; the table says so rather than printing 0.
    row = Row(scene_id="s", agent_id="a", source="single", values={"tp": 3, "precision": 0.5})
    table = _table(render_report([row], (0.5,)))
    header, body, total = table[0], table[2], table[-1]
    assert body[header.index("tp")] == "3"
    assert body[header.index("gt")] == "n/a"
    assert body[header.index("precision")] == "0.500"
    assert body[header.index("AP@0.5")] == "n/a"
    assert total[:3] == ["all", "", ""]
    assert total[header.index("tp")] == "3"
    assert total[header.index("AP@0.5")] == "n/a"
    compare = _table(render_compare([row], [row], (0.5,)))[2]
    assert compare[header.index("tp")] == "+0"
    assert compare[header.index("gt")] == "n/a"


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
    assert table[-1][:3] == ["all", "", ""]
    n_outputs = len({(row.agent_id, row.source) for row in rows})
    assert len(table) == len(rows) + n_outputs + 3


def test_render_compare_lists_rows_of_either_run_and_counts_them() -> None:
    """Decision: a row missing on one side shows ``n/a`` in every delta cell, and a footer
    counts the shared rows and the rows only one run has."""

    def row(scene_id: str, run_id: str, tp: int, ap: float | None) -> Row:
        values: dict[str, object] = {"gt": 4, "tp": tp, "fp": 1, "AP@0.5": ap}
        return Row(scene_id, "vehicle", "single", values, run_id=run_id)

    rows_a = [row("s1", "det-a", 2, 0.5), row("s2", "det-a", 3, 0.75)]
    rows_b = [row("s2", "det-b", 4, 1.0), row("s3", "det-b", 1, None)]
    text = render_compare(rows_a, rows_b, (0.5,))
    main = _tables(text)[0]
    header = main[0]
    tp_column, ap_col, gt_column = header.index("tp"), header.index("AP@0.5"), header.index("gt")
    body = {line[0]: line for line in main[2:]}
    # a's rows in a's order, then b's extra rows, then the pooled rows.
    assert [line[0] for line in main[2:]] == ["s1", "s2", "s3", "all", "all"]
    assert body["s2"][tp_column] == "+1" and body["s2"][ap_col] == "+0.250"
    for scene_id in ("s1", "s3"):
        assert all(cell == "n/a" for cell in body[scene_id][3:]), body[scene_id]
    # The all rows compare the shared rows only: gt 4 vs 4, tp 3 vs 4.
    assert main[-2][:3] == ["all", "vehicle", "single"]
    assert main[-1][:3] == ["all", "", ""]
    assert main[-1][gt_column] == "+0" and main[-1][tp_column] == "+1"
    footer = [line for line in text.splitlines() if line.startswith("rows:")]
    assert footer == ["rows: 1 shared, 1 only in det-a, 1 only in det-b"]
    # The footer sits right under the main table, before the per-class table.
    lines = text.splitlines()
    assert lines[lines.index(footer[0]) - 1].startswith("| all |")
    assert lines[lines.index(footer[0]) + 1] == ""
    # Rows without a run id fall back to a and b.
    plain = render_compare([row("s1", "", 2, 0.5)], [row("s2", "", 2, 0.5)], (0.5,))
    assert "rows: 0 shared, 1 only in a, 1 only in b" in plain
