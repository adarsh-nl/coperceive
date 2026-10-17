# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from coperceive.evaluation.predictions import (
    BOX_SCHEMA,
    FORMAT,
    FORMAT_VERSION,
    PredictionError,
    RunMeta,
    check_run_id,
    list_runs,
    load_rows,
    predictions_root,
    read_boxes,
    read_run_meta,
    validate_boxes,
    write_run,
)
from coperceive.model import BoxFrame
from coperceive.se3 import SE3

from tests.cooperative import cooperative_scene
from tests.eval_fixture import synthetic_predictions


def _row(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "agent_id": "vehicle",
        "source": "single",
        "time_us": 100_000,
        "category": "car",
        "score": 0.9,
        "track_id": None,
        "cx": 1.0,
        "cy": 2.0,
        "cz": 0.5,
        "length": 4.0,
        "width": 2.0,
        "height": 1.5,
        "qw": 1.0,
        "qx": 0.0,
        "qy": 0.0,
        "qz": 0.0,
        "frame": "body",
    }
    base.update(overrides)
    return base


def test_fixture_shape() -> None:
    scene = cooperative_scene()
    meta, rows = synthetic_predictions(scene)
    assert meta.run_id == "synthetic"
    single_vehicle = [r for r in rows if r.source == "single" and r.agent_id == "vehicle"]
    single_uav = [r for r in rows if r.source == "single" and r.agent_id == "uav"]
    fused = [r for r in rows if r.source == "fused"]
    assert all(r.agent_id == "vehicle" for r in fused)
    # Vehicle GT: 42@100k, 42@200k, 99 (left out), 5, cone@200k; plus 4 FP cars.
    assert len(single_vehicle) == 4 + 4
    assert len(single_uav) == 3
    assert len(fused) == 5
    assert all(r.track_id != "99" for r in single_vehicle)
    assert any(r.track_id == "99" and r.category == "pedestrian" for r in fused)
    assert [r.category for r in rows if r.track_id == "5" and r.agent_id == "vehicle"] == [
        "truck",
        "truck",
    ]
    assert all(r.frame is BoxFrame.BODY for r in rows)
    assert all(r.scene_id == "coop-fixture" for r in rows)
    first = single_vehicle[0]
    assert first.track_id == "42"
    assert first.center == pytest.approx((8.1, 1.0, 0.5))
    fps = [r for r in single_vehicle if r.score == 0.8]
    assert [r.time_us for r in fps] == [0, 100_000, 200_000, 300_000]
    assert fps[0].center == (50.0, 50.0, 0.0)


def test_write_read_round_trip(tmp_path: Path) -> None:
    scene = cooperative_scene()
    meta, rows = synthetic_predictions(scene)
    root = tmp_path / "predictions"
    written = write_run(root, "synthetic", meta, rows)
    assert written == ("coop-fixture",)
    assert (root / "synthetic" / "run.json").is_file()
    assert (root / "synthetic" / "coop-fixture" / "boxes.parquet").is_file()
    payload = json.loads((root / "synthetic" / "run.json").read_text(encoding="utf-8"))
    assert payload["format"] == FORMAT
    assert payload["format_version"] == FORMAT_VERSION
    assert payload["sources"] == {"single": "own sensors", "fused": "cooperative"}
    assert "notes" not in payload
    assert read_run_meta(root, "synthetic") == meta
    assert read_boxes(root, "synthetic", "coop-fixture") == rows
    assert list_runs(root) == ("synthetic",)
    table = pq.read_table(root / "synthetic" / "coop-fixture" / "boxes.parquet")
    assert table.schema.names == BOX_SCHEMA.names


def test_write_run_refuses_a_second_write_without_force(tmp_path: Path) -> None:
    scene = cooperative_scene()
    meta, rows = synthetic_predictions(scene)
    root = tmp_path / "predictions"
    write_run(root, "synthetic", meta, rows)
    with pytest.raises(PredictionError, match="boxes.parquet"):
        write_run(root, "synthetic", meta, rows[:1])
    assert len(read_boxes(root, "synthetic", "coop-fixture")) == len(rows)
    assert write_run(root, "synthetic", meta, rows[:1], force=True) == ("coop-fixture",)
    assert len(read_boxes(root, "synthetic", "coop-fixture")) == 1


def test_write_run_needs_a_scene_id_and_a_known_source(tmp_path: Path) -> None:
    scene = cooperative_scene()
    meta, rows = synthetic_predictions(scene)
    root = tmp_path / "predictions"
    with pytest.raises(PredictionError, match="row 1: scene_id"):
        write_run(root, "synthetic", meta, (rows[0], replace(rows[1], scene_id=None)))
    with pytest.raises(PredictionError, match="row 0: source 'lidar'"):
        write_run(root, "synthetic", meta, (replace(rows[0], source="lidar"),))
    with pytest.raises(PredictionError, match="run_id"):
        write_run(root, "other", meta, rows)
    assert not root.exists()


@pytest.mark.parametrize("bad", ["../x", "a/b", "a\\b", ".", "..", ".hidden", ""])
def test_run_id_rule(tmp_path: Path, bad: str) -> None:
    scene = cooperative_scene()
    meta, rows = synthetic_predictions(scene)
    with pytest.raises(PredictionError):
        check_run_id(bad)
    with pytest.raises(PredictionError):
        RunMeta(
            run_id=bad,
            model="m",
            score_note="s",
            categories=(),
            sources=(),
            created_utc="2026-10-17T00:00:00Z",
        )
    with pytest.raises(PredictionError):
        write_run(tmp_path, bad, meta, rows)
    with pytest.raises(PredictionError):
        read_run_meta(tmp_path, bad)
    with pytest.raises(PredictionError):
        read_boxes(tmp_path, bad, "coop-fixture")
    with pytest.raises(PredictionError):
        read_boxes(tmp_path, "synthetic", bad)
    assert list(tmp_path.iterdir()) == []
    assert check_run_id("det-v1") == "det-v1"


def test_validate_boxes_passes_on_the_fixture() -> None:
    scene = cooperative_scene()
    meta, rows = synthetic_predictions(scene)
    validate_boxes(scene, meta, rows)


def test_validate_rejects_an_unknown_agent() -> None:
    scene = cooperative_scene()
    meta, rows = synthetic_predictions(scene)
    bad = (replace(rows[0], agent_id="ghost"), *rows[1:])
    with pytest.raises(PredictionError, match=r"^row 0: .*ghost"):
        validate_boxes(scene, meta, bad)


def test_validate_rejects_an_unknown_source() -> None:
    scene = cooperative_scene()
    meta, rows = synthetic_predictions(scene)
    bad = (*rows[:2], replace(rows[2], source="radar"), *rows[3:])
    with pytest.raises(PredictionError, match=r"^row 2: .*radar"):
        validate_boxes(scene, meta, bad)


def test_validate_rejects_a_time_that_is_not_a_pose_of_that_agent() -> None:
    scene = cooperative_scene()
    meta, rows = synthetic_predictions(scene)
    assert 160_000 in scene.pose_times("uav")
    assert 160_000 not in scene.pose_times("vehicle")
    bad = (*rows[:3], replace(rows[3], agent_id="vehicle", time_us=160_000), *rows[4:])
    with pytest.raises(PredictionError, match=r"^row 3: .*160000"):
        validate_boxes(scene, meta, bad)


def test_validate_rejects_an_empty_category_and_a_foreign_scene_id() -> None:
    scene = cooperative_scene()
    meta, rows = synthetic_predictions(scene)
    with pytest.raises(PredictionError, match=r"^row 1: category"):
        validate_boxes(scene, meta, (rows[0], replace(rows[1], category="")))
    with pytest.raises(PredictionError, match=r"^row 0: scene_id"):
        validate_boxes(scene, meta, (replace(rows[0], scene_id="elsewhere"),))


def test_prediction_box_rejects_bad_numbers() -> None:
    scene = cooperative_scene()
    _, rows = synthetic_predictions(scene)
    good = rows[0]
    with pytest.raises(PredictionError, match="unit"):
        replace(good, box_from_rpy=SE3(1.0, 0.1, 0.0, 0.0, 0.0, 0.0, 0.0))
    with pytest.raises(PredictionError, match="pure rotation"):
        replace(good, box_from_rpy=SE3(1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0))
    with pytest.raises(PredictionError, match="score"):
        replace(good, score=float("nan"))
    with pytest.raises(PredictionError, match="size"):
        replace(good, size_lwh=(4.0, 0.0, 1.5))
    with pytest.raises(PredictionError, match="time_us"):
        replace(good, time_us=1.5)  # type: ignore[arg-type]
    # Within 1e-6 of unit norm is accepted.
    replace(good, box_from_rpy=SE3(1.0 + 5e-7, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0))


def test_load_rows_jsonl(tmp_path: Path) -> None:
    path = tmp_path / "boxes.jsonl"
    lines = [
        _row(),
        _row(agent_id="uav", track_id="42", score=0.5, time_us=160_000),
        _row(scene_id="coop-fixture", category="pedestrian"),
    ]
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n\n", encoding="utf-8")
    rows = load_rows(path)
    assert len(rows) == 3
    assert [r.scene_id for r in rows] == [None, None, "coop-fixture"]
    assert rows[1].agent_id == "uav"
    assert rows[1].track_id == "42"
    assert rows[1].time_us == 160_000
    assert rows[2].category == "pedestrian"
    assert rows[0].center == (1.0, 2.0, 0.5)
    assert rows[0].size_lwh == (4.0, 2.0, 1.5)
    assert rows[0].box_from_rpy == SE3.identity()
    assert rows[0].frame is BoxFrame.BODY


def test_load_rows_jsonl_reports_the_row(tmp_path: Path) -> None:
    path = tmp_path / "boxes.jsonl"
    lines = [_row(), _row(qw=1.0, qx=0.1), _row()]
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")
    with pytest.raises(PredictionError, match=r"^row 1: "):
        load_rows(path)
    missing = _row()
    del missing["score"]
    path.write_text(json.dumps(missing) + "\n", encoding="utf-8")
    with pytest.raises(PredictionError, match=r"^row 0: .*score"):
        load_rows(path)
    path.write_text('{"agent_id": "vehicle"\n', encoding="utf-8")
    with pytest.raises(PredictionError, match=r"^row 0: "):
        load_rows(path)
    with pytest.raises(PredictionError, match="jsonl"):
        load_rows(tmp_path / "boxes.csv")


def test_load_rows_parquet(tmp_path: Path) -> None:
    path = tmp_path / "boxes.parquet"
    table = pa.Table.from_pylist([_row(), _row(track_id="7", frame="world")], schema=BOX_SCHEMA)
    pq.write_table(table, path)
    rows = load_rows(path)
    assert len(rows) == 2
    assert rows[0].scene_id is None
    assert rows[1].track_id == "7"
    assert rows[1].frame is BoxFrame.WORLD
    # An extra column is ignored; a missing one is an error.
    evolved = table.append_column("extra", pa.nulls(table.num_rows, pa.float32()))
    pq.write_table(evolved, path)
    assert len(load_rows(path)) == 2
    partial = pa.table({"agent_id": pa.array(["vehicle"], pa.string())})
    pq.write_table(partial, path)
    with pytest.raises(PredictionError, match="score"):
        load_rows(path)


def test_predictions_root_default_rule_and_override(tmp_path: Path) -> None:
    assert predictions_root(tmp_path, None) is None
    (tmp_path / "predictions").mkdir()
    assert predictions_root(tmp_path, None) is None
    (tmp_path / "predictions" / "det-v1").mkdir()
    assert predictions_root(tmp_path, None) is None
    (tmp_path / "predictions" / "det-v1" / "run.json").write_text("{}", encoding="utf-8")
    assert predictions_root(tmp_path, None) == tmp_path / "predictions"
    override = tmp_path / "elsewhere"
    assert predictions_root(tmp_path, override) == override


def test_list_runs_skips_what_is_not_a_run(tmp_path: Path) -> None:
    assert list_runs(tmp_path / "missing") == ()
    for name in ("b-run", "a-run", ".hidden", "no-json"):
        (tmp_path / name).mkdir()
    for name in ("b-run", "a-run", ".hidden"):
        (tmp_path / name / "run.json").write_text("{}", encoding="utf-8")
    (tmp_path / "file.txt").write_text("x", encoding="utf-8")
    assert list_runs(tmp_path) == ("a-run", "b-run")


def test_run_meta_json_rules(tmp_path: Path) -> None:
    scene = cooperative_scene()
    meta, _ = synthetic_predictions(scene)
    noted = replace(meta, notes="first try")
    payload = noted.to_json()
    assert payload["notes"] == "first try"
    assert RunMeta.from_json(payload, directory_name="synthetic") == noted
    assert RunMeta.from_json({**payload, "unknown": 1}, directory_name="synthetic") == noted
    with pytest.raises(PredictionError, match="run_id"):
        RunMeta.from_json(payload, directory_name="other")
    with pytest.raises(PredictionError, match="format"):
        RunMeta.from_json({**payload, "format": "other"}, directory_name="synthetic")
    with pytest.raises(PredictionError, match="format_version"):
        RunMeta.from_json({**payload, "format_version": 2}, directory_name="synthetic")
    with pytest.raises(PredictionError, match="score_note"):
        RunMeta.from_json({**payload, "score_note": ""}, directory_name="synthetic")
    without = dict(payload)
    del without["categories"]
    with pytest.raises(PredictionError, match="categories"):
        RunMeta.from_json(without, directory_name="synthetic")
    with pytest.raises(PredictionError, match="sources"):
        RunMeta.from_json({**payload, "sources": ["single"]}, directory_name="synthetic")
    with pytest.raises(PredictionError):
        RunMeta.from_json([], directory_name="synthetic")
    (tmp_path / "det-v1").mkdir()
    (tmp_path / "det-v1" / "run.json").write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(PredictionError, match="run_id"):
        read_run_meta(tmp_path, "det-v1")
    with pytest.raises(PredictionError, match="run.json"):
        read_run_meta(tmp_path, "absent")
    with pytest.raises(PredictionError, match="boxes.parquet"):
        read_boxes(tmp_path, "det-v1", "coop-fixture")


def test_prediction_box_frame_and_scene_id_are_kept(tmp_path: Path) -> None:
    scene = cooperative_scene()
    meta, rows = synthetic_predictions(scene)
    world = replace(rows[0], frame=BoxFrame.WORLD, track_id=None)
    root = tmp_path / "predictions"
    write_run(root, "synthetic", meta, (world, rows[1]))
    back = read_boxes(root, "synthetic", "coop-fixture")
    assert back[0].frame is BoxFrame.WORLD
    assert back[0].track_id is None
    assert back[0].scene_id == "coop-fixture"
    assert back[1] == rows[1]
