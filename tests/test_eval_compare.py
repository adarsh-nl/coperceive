# SPDX-License-Identifier: Apache-2.0
"""Breakdowns for ``coperceive eval compare`` on a hand-built scene.

The scene has three vehicle boxes and known predictions, so the pooled AP in
each distance, occlusion and agent bin is fixed before the code runs. No
dataset frame is used.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest
from coperceive.cli import main
from coperceive.diagnostics import shared_objects
from coperceive.evaluation.predictions import PredictionBox, RunMeta, write_run
from coperceive.evaluation.report import collect, render_compare
from coperceive.evaluation.schemautil import SchemaError, validate_schema
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
from tests.eval_fixture import synthetic_predictions

SCENE = "bins"
ROOT = Path(__file__).parents[1]
SCHEMA = json.loads((ROOT / "docs/spec/compare.schema.json").read_text(encoding="utf-8"))


def _scene() -> Scene:
    identity = SE3.identity()
    agents = (
        Agent(
            "vehicle",
            AgentType.VEHICLE,
            (StampedPose(0, identity),),
            (("name", "car"),),
        ),
        Agent(
            "uav",
            AgentType.UAV,
            (StampedPose(0, identity.translation(5.0, 5.0, 25.0)),),
            (("name", "quad"),),
        ),
    )
    annotations = (
        Annotation(
            "vehicle",
            0,
            "near",
            "car",
            (10.0, 0.0, 0.0),
            (4.0, 2.0, 1.5),
            identity,
            1.0,
            BoxFrame.WORLD,
        ),
        Annotation(
            "vehicle",
            0,
            "ped",
            "pedestrian",
            (40.0, 0.0, 0.0),
            (0.6, 0.6, 1.7),
            identity,
            0.1,
            BoxFrame.WORLD,
        ),
        Annotation(
            "vehicle",
            0,
            "far",
            "car",
            (60.0, 0.0, 0.0),
            (4.0, 2.0, 1.5),
            identity,
            None,
            BoxFrame.WORLD,
        ),
        Annotation(
            "uav",
            0,
            "near",
            "car",
            (10.0, 0.0, 0.0),
            (4.0, 2.0, 1.5),
            identity,
            1.0,
            BoxFrame.WORLD,
        ),
    )
    return Scene(
        SCHEMA_VERSION,
        SCENE,
        "world",
        Clock("us", "synthetic breakdown scene"),
        agents,
        (),
        annotations,
        shared_objects(annotations),
    )


def _box(category: str, score: float, center: tuple[float, float, float]) -> PredictionBox:
    return PredictionBox(
        SCENE,
        "vehicle",
        "fused",
        0,
        category,
        score,
        None,
        center,
        (4.0, 2.0, 1.5) if category == "car" else (0.6, 0.6, 1.7),
        SE3.identity(),
        BoxFrame.WORLD,
    )


def _meta(run_id: str) -> RunMeta:
    return RunMeta(
        run_id,
        "fixture",
        "constant",
        ("car", "pedestrian"),
        (("fused", "cooperative"),),
        "2026-11-02T00:00:00Z",
    )


def _prepare(tmp_path: Path) -> Path:
    root = tmp_path / "scenes"
    write_scene(_scene(), root / SCENE)
    predictions = root / "predictions"
    write_run(
        predictions,
        "det-a",
        _meta("det-a"),
        (_box("car", 0.9, (10.0, 0.0, 0.0)), _box("car", 0.4, (0.0, 80.0, 0.0))),
    )
    write_run(
        predictions,
        "det-b",
        _meta("det-b"),
        (_box("car", 0.9, (10.0, 0.0, 0.0)), _box("pedestrian", 0.8, (40.0, 0.0, 0.0))),
    )
    write_run(predictions, "det-c", _meta("det-c"), (_box("car", 0.9, (10.0, 0.0, 0.0)),))
    return root


def _eval(root: Path, run_id: str) -> None:
    main(["eval", "run", run_id, "--root", str(root)])


def _as_dict(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    return cast(dict[str, object], value)


def _as_list(value: object) -> list[object]:
    assert isinstance(value, list)
    return cast(list[object], value)


def _cell(
    document: dict[str, object], dimension: str, label: str, run_id: str
) -> dict[str, object]:
    outputs = _as_list(document["outputs"])
    assert len(outputs) == 1
    output = _as_dict(outputs[0])
    breakdowns = _as_dict(output["breakdowns"])
    for row in _as_list(breakdowns[dimension]):
        item = _as_dict(row)
        if item["bin"] == label:
            return _as_dict(_as_dict(item["runs"])[run_id])
    raise AssertionError(f"no bin {label}")


def _mean(cell: dict[str, object]) -> tuple[object, object, object]:
    point = _as_dict(_as_dict(cell["ap"])["0.5"])
    return point["mean"], point["note"], point["n_objects"]


def test_distance_occlusion_and_agent_bins_match_the_hand_count(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _prepare(tmp_path)
    _eval(root, "det-a")
    _eval(root, "det-b")
    capsys.readouterr()
    assert (root / "predictions" / "det-a" / SCENE / "outcomes.json").is_file()

    rows_a = collect(root, root / "predictions", "det-a")
    rows_b = collect(root, root / "predictions", "det-b")
    main(["eval", "compare", "det-a", "det-b", "--root", str(root)])
    legacy = capsys.readouterr().out
    assert legacy == render_compare(rows_a, rows_b, (0.5, 0.7))

    destination = tmp_path / "compare.json"
    main(
        [
            "eval",
            "compare",
            "det-a",
            "det-b",
            "--root",
            str(root),
            "--by",
            "distance,occlusion,agents,class,scene",
            "--json",
            str(destination),
        ]
    )
    captured = capsys.readouterr()
    assert captured.out.startswith(legacy)
    assert "no objects" in captured.out
    assert f"wrote {destination}" in captured.err
    document = json.loads(destination.read_text(encoding="utf-8"))
    validate_schema(document, SCHEMA)
    golden = ROOT / "tests/fixtures/eval/compare_golden.json"
    assert destination.read_text(encoding="utf-8") == golden.read_text(encoding="utf-8")
    again = destination.read_text(encoding="utf-8")
    destination.write_text("")
    main(
        [
            "eval",
            "compare",
            "det-a",
            "det-b",
            "--root",
            str(root),
            "--by",
            "distance,occlusion,agents,class,scene",
            "--json",
            str(destination),
        ]
    )
    assert destination.read_text(encoding="utf-8") == again

    near_a = _cell(document, "distance", "0-30m", "det-a")
    near_b = _cell(document, "distance", "0-30m", "det-b")
    assert _mean(near_a) == (1.0, None, 1)
    assert _mean(near_b) == (1.0, None, 1)
    assert _mean(_cell(document, "distance", "30-50m", "det-a")) == (0.0, None, 1)
    assert _mean(_cell(document, "distance", "30-50m", "det-b")) == (1.0, None, 1)
    assert _mean(_cell(document, "distance", "50m+", "det-a")) == (0.0, None, 1)
    assert _mean(_cell(document, "distance", "50m+", "det-b")) == (0.0, None, 1)

    partial = _cell(document, "occlusion", "partial", "det-a")
    assert _mean(partial) == (None, "no objects", 0)
    assert partial["n_objects"] == 0
    assert _mean(_cell(document, "occlusion", "heavy", "det-a")) == (0.0, None, 1)
    assert _mean(_cell(document, "occlusion", "heavy", "det-b")) == (1.0, None, 1)
    assert _mean(_cell(document, "occlusion", "clear", "det-a")) == (1.0, None, 1)
    assert _mean(_cell(document, "occlusion", "unknown", "det-a")) == (0.0, None, 1)

    assert _mean(_cell(document, "agents", "2", "det-a")) == (1.0, None, 1)
    assert _mean(_cell(document, "agents", "1", "det-b")) == (0.5, None, 2)
    assert _mean(_cell(document, "class", "car", "det-a")) == (0.5, None, 2)
    assert _mean(_cell(document, "class", "pedestrian", "det-b")) == (1.0, None, 1)
    assert _mean(_cell(document, "scene", SCENE, "det-a")) == (0.25, None, 3)
    assert _mean(_cell(document, "scene", SCENE, "det-b")) == (0.75, None, 3)

    objects = _as_list(document["objects"])
    keys = [_as_dict(item)["key"] for item in objects]
    assert keys[:3] == [
        f"gt:{SCENE}:vehicle:0:0",
        f"gt:{SCENE}:vehicle:0:1",
        f"gt:{SCENE}:vehicle:0:2",
    ]
    gt_near = _as_dict(objects[0])
    assert gt_near["agent_count"] == 2
    assert gt_near["distance_m"] == 10.0
    runs = _as_dict(gt_near["runs"])
    assert _as_dict(_as_dict(runs["det-a"])["fused"])["outcome"] == "tp"
    assert _as_dict(_as_dict(runs["det-a"])["fused"])["centre_error_m"] == 0.0
    ped = _as_dict(objects[1])
    ped_runs = _as_dict(ped["runs"])
    assert _as_dict(_as_dict(ped_runs["det-a"])["fused"])["outcome"] == "fn"
    assert ped["occlusion"] == "heavy"
    fps = [_as_dict(item) for item in objects if _as_dict(item)["kind"] == "fp"]
    assert len(fps) == 1
    assert fps[0]["key"] == f"fp:det-a:{SCENE}:vehicle:fused:0:1"
    assert fps[0]["outcome"] == "fp"


def test_empty_distance_bin_says_no_objects(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _prepare(tmp_path)
    _eval(root, "det-a")
    capsys.readouterr()
    destination = tmp_path / "compare.json"
    main(
        [
            "eval",
            "compare",
            "det-a",
            "det-a",
            "--root",
            str(root),
            "--by",
            "distance",
            "--distance-bins",
            "0,5,30,50",
            "--json",
            str(destination),
        ]
    )
    capsys.readouterr()
    document = json.loads(destination.read_text(encoding="utf-8"))
    cell = _cell(document, "distance", "0-5m", "det-a")
    assert _mean(cell) == (None, "no objects", 0)
    point = _as_dict(_as_dict(cell["ap"])["0.5"])
    assert point["mean"] != 0


def test_from_scores_uses_outcomes_and_falls_back(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _prepare(tmp_path)
    _eval(root, "det-a")
    _eval(root, "det-b")
    capsys.readouterr()
    scored = tmp_path / "scored.json"
    opened = tmp_path / "opened.json"
    main(
        [
            "eval",
            "compare",
            "det-a",
            "det-b",
            "--root",
            str(root),
            "--from-scores",
            "--json",
            str(scored),
        ]
    )
    main(["eval", "compare", "det-a", "det-b", "--root", str(root), "--json", str(opened)])
    capsys.readouterr()
    left = json.loads(scored.read_text(encoding="utf-8"))
    right = json.loads(opened.read_text(encoding="utf-8"))
    assert left["distance_source"] == "outcomes"
    assert right["distance_source"] == "scene"
    assert _mean(_cell(left, "distance", "0-30m", "det-a")) == _mean(
        _cell(right, "distance", "0-30m", "det-a")
    )
    for run_id in ("det-a", "det-b"):
        (root / "predictions" / run_id / SCENE / "outcomes.json").unlink()
    fallback = tmp_path / "fallback.json"
    main(
        [
            "eval",
            "compare",
            "det-a",
            "det-b",
            "--root",
            str(root),
            "--from-scores",
            "--json",
            str(fallback),
        ]
    )
    document = json.loads(fallback.read_text(encoding="utf-8"))
    assert document["distance_source"] == "unavailable"
    assert _mean(_cell(document, "distance", "0-30m", "det-a")) == (None, "no objects", 0)
    assert _cell(document, "distance", "unknown", "det-a")["n_objects"] == 3


def test_three_runs_render_and_a_fifth_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _prepare(tmp_path)
    for run_id in ("det-a", "det-b", "det-c"):
        _eval(root, run_id)
    capsys.readouterr()
    main(["eval", "compare", "det-a", "det-b", "det-c", "--root", str(root)])
    out = capsys.readouterr().out
    assert "| run | gt |" in out
    assert "## det-c" in out
    assert "| det-a |" in out
    with pytest.raises(SystemExit) as caught:
        main(["eval", "compare", "det-a", "--root", str(root)])
    assert caught.value.code == 1
    with pytest.raises(SystemExit) as caught:
        main(["eval", "compare", "det-a", "det-b", "det-c", "det-a", "det-b", "--root", str(root)])
    assert caught.value.code == 1


def test_two_run_markdown_on_the_cooperative_fixture_is_unchanged(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    scene = cooperative_scene()
    root = tmp_path / "scenes"
    write_scene(scene, root / scene.scene_id)
    meta, boxes = synthetic_predictions(scene)
    write_run(root / "predictions", meta.run_id, meta, boxes)
    main(["eval", "run", meta.run_id, "--root", str(root)])
    capsys.readouterr()
    rows = collect(root, root / "predictions", meta.run_id)
    main(["eval", "compare", meta.run_id, meta.run_id, "--root", str(root)])
    assert capsys.readouterr().out == render_compare(rows, rows, (0.5, 0.7))


def test_schema_rejects_a_document_without_the_format() -> None:
    with pytest.raises(SchemaError):
        validate_schema({"format_version": 1}, SCHEMA)


def test_object_keys_do_not_depend_on_the_run(tmp_path: Path) -> None:
    _prepare(tmp_path)
    scene = _scene()
    from coperceive.evaluation.outcomes import gt_key, records_from_rows

    rows = (
        {
            "kind": "gt",
            "agent_id": "vehicle",
            "source": "fused",
            "time_us": 0,
            "row": 0,
            "category": "car",
            "track_id": "near",
            "status": "tp",
            "cx": 10.0,
            "cy": 0.0,
            "cz": 0.0,
            "length": 4.0,
            "width": 2.0,
            "height": 1.5,
            "qw": 1.0,
            "qx": 0.0,
            "qy": 0.0,
            "qz": 0.0,
            "visibility": 1.0,
            "seen_by_other_gt": True,
            "pair_id": 0,
            "iou_bev": 1.0,
            "centre_error_m": 0.0,
            "yaw_error_deg": 0.0,
            "status_at_0": None,
        },
    )
    first = records_from_rows(scene, rows, run_id="det-a", scene_id=SCENE)
    second = records_from_rows(scene, rows, run_id="det-b", scene_id=SCENE)
    assert first[0].key == second[0].key == gt_key(SCENE, "vehicle", 0, 0)
    assert "det-a" not in first[0].key
