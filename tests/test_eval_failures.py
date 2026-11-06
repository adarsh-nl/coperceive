# SPDX-License-Identifier: Apache-2.0
"""Failure tags on hand-built outcome records, plus the breakdown scene through the CLI."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest
from coperceive.cli import main
from coperceive.evaluation.failures import FailureWeights, mine_failures
from coperceive.evaluation.outcomes import OutcomeRecord
from coperceive.evaluation.schemautil import validate_schema

from tests.test_eval_compare import SCENE, evaluate_run, prepare_breakdown_scene

ROOT = Path(__file__).parents[1]
SCHEMA = json.loads((ROOT / "docs/spec/failures.schema.json").read_text(encoding="utf-8"))


def _record(**overrides: object) -> OutcomeRecord:
    values: dict[str, object] = {
        "kind": "gt",
        "key": "gt:s:vehicle:0:0",
        "scene_id": "s",
        "run_id": "run",
        "agent_id": "vehicle",
        "source": "fused",
        "time_us": 0,
        "row": 0,
        "track_id": "t",
        "category": "car",
        "score": None,
        "distance_m": 10.0,
        "visibility": 1.0,
        "agent_count": 1,
        "agent_bin": "1",
        "status": "miss",
        "status_at": (),
        "pair_id": None,
        "iou_bev": None,
        "centre_error_m": None,
        "yaw_error_deg": None,
        "status_single": None,
        "status_fused": None,
        "center": (10.0, 0.0, 0.0),
        "size_lwh": (4.0, 2.0, 1.5),
        "rotation": (1.0, 0.0, 0.0, 0.0),
    }
    values.update(overrides)
    return OutcomeRecord(
        kind=str(values["kind"]),
        key=str(values["key"]),
        scene_id=str(values["scene_id"]),
        run_id=str(values["run_id"]),
        agent_id=str(values["agent_id"]),
        source=str(values["source"]),
        time_us=int(cast(int, values["time_us"])),
        row=int(cast(int, values["row"])),
        track_id=cast(str | None, values["track_id"]),
        category=str(values["category"]),
        score=cast(float | None, values["score"]),
        distance_m=cast(float | None, values["distance_m"]),
        visibility=cast(float | None, values["visibility"]),
        agent_count=cast(int | None, values["agent_count"]),
        agent_bin=str(values["agent_bin"]),
        status=str(values["status"]),
        status_at=cast(tuple[str, ...], values["status_at"]),
        pair_id=cast(int | None, values["pair_id"]),
        iou_bev=cast(float | None, values["iou_bev"]),
        centre_error_m=cast(float | None, values["centre_error_m"]),
        yaw_error_deg=cast(float | None, values["yaw_error_deg"]),
        status_single=cast(str | None, values["status_single"]),
        status_fused=cast(str | None, values["status_fused"]),
        center=cast(tuple[float, float, float], values["center"]),
        size_lwh=cast(tuple[float, float, float], values["size_lwh"]),
        rotation=cast(tuple[float, float, float, float], values["rotation"]),
    )


def _as_dict(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    return cast(dict[str, object], value)


def _as_list(value: object) -> list[object]:
    assert isinstance(value, list)
    return cast(list[object], value)


def _tags(
    records: list[OutcomeRecord], latency_records: list[OutcomeRecord] | None = None
) -> list[str]:
    document = mine_failures(records, run_id="run", latency_records=latency_records)
    frames = _as_list(document["frames"])
    if not frames:
        return []
    tags = _as_dict(frames[0])["tags"]
    return [str(tag) for tag in _as_list(tags)]


def test_each_tag_fires_on_one_synthetic_case_and_not_the_other() -> None:
    near_vru = _record(category="pedestrian", status="miss", distance_m=12.0, visibility=1.0)
    far_vru = _record(
        category="pedestrian", status="miss", distance_m=40.0, key="gt:s:vehicle:0:1", row=1
    )
    assert "missed_vru" in _tags([near_vru])
    assert "missed_vru" not in _tags([far_vru])
    assert "missed_vru" not in _tags([_record(category="car", status="miss", distance_m=10.0)])

    ghost = _record(
        kind="prediction",
        key="fp:run:s:vehicle:fused:0:2",
        status="fp",
        score=0.4,
        center=(0.0, 80.0, 0.0),
        distance_m=80.0,
        row=2,
    )
    close_fp = _record(
        kind="prediction",
        key="fp:run:s:vehicle:fused:0:3",
        status="fp",
        score=0.4,
        center=(10.5, 0.0, 0.0),
        distance_m=10.5,
        row=3,
    )
    anchor = _record(status="tp", center=(10.0, 0.0, 0.0))
    assert "ghost_box" in _tags([anchor, ghost])
    assert "ghost_box" not in _tags([anchor, close_fp])

    duplicate = _record(
        kind="prediction",
        key="fp:run:s:vehicle:fused:0:4",
        status="fp",
        score=0.4,
        center=(10.2, 0.0, 0.0),
        distance_m=10.2,
        row=4,
    )
    assert "duplicate_boxes" in _tags([anchor, duplicate])
    assert "duplicate_boxes" not in _tags([anchor, ghost])

    loose = _record(status="tp", centre_error_m=2.0, yaw_error_deg=1.0)
    tight = _record(status="tp", centre_error_m=0.1, yaw_error_deg=1.0)
    assert "localisation_error" in _tags([loose])
    assert "localisation_error" not in _tags([tight])
    yaw = _record(status="tp", centre_error_m=0.1, yaw_error_deg=20.0)
    assert "localisation_error" in _tags([yaw])

    assert "far_range_miss" in _tags([_record(status="miss", distance_m=60.0)])
    assert "far_range_miss" not in _tags([_record(status="miss", distance_m=40.0)])

    assert "occluded_miss" in _tags([_record(status="miss", visibility=0.1)])
    assert "occluded_miss" not in _tags([_record(status="miss", visibility=None)])
    assert "occluded_miss" not in _tags([_record(status="miss", visibility=0.9)])

    lost = _record(status="miss", status_single="tp", status_fused="miss")
    kept = _record(status="tp", status_single="tp", status_fused="tp")
    assert "fusion_disagreement" in _tags([lost])
    assert "fusion_disagreement" not in _tags([kept])
    single = _record(status="miss", source="single", status_single="tp", status_fused="miss")
    assert "fusion_disagreement" not in _tags([single])

    clean = _record(status="tp", key="gt:s:vehicle:0:0")
    delayed = _record(status="miss", run_id="late", key="gt:s:vehicle:0:0")
    without = _tags([clean])
    assert "latency_sensitive" not in without
    with_latency = _tags([clean], [delayed])
    assert "latency_sensitive" in with_latency
    still_hit = _record(status="tp", run_id="late", key="gt:s:vehicle:0:0")
    assert "latency_sensitive" not in _tags([clean], [still_hit])


def test_ranking_is_stable_and_badness_uses_the_documented_weights() -> None:
    near = _record(
        scene_id="b",
        key="gt:b:vehicle:0:0",
        category="pedestrian",
        status="miss",
        distance_m=10.0,
    )
    later = _record(
        scene_id="a",
        key="gt:a:vehicle:0:0",
        category="pedestrian",
        status="miss",
        distance_m=10.0,
    )
    document = mine_failures([later, near], run_id="run", top=1)
    again = mine_failures([near, later], run_id="run", top=1)
    assert document == again
    frames = _as_list(document["frames"])
    assert len(frames) == 1
    first = _as_dict(frames[0])
    # class 2 times distance weight 2 (under 20 m) = 4, and top keeps the scene-sorted winner.
    assert first["badness"] == 4.0
    assert first["scene_id"] == "a"
    assert first["rank"] == 1
    validate_schema(document, SCHEMA)


def test_failures_cli_on_the_breakdown_scene(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = prepare_breakdown_scene(tmp_path)
    evaluate_run(root, "det-a")
    evaluate_run(root, "det-b")
    capsys.readouterr()
    destination = tmp_path / "failures.json"
    main(
        [
            "eval",
            "failures",
            "det-a",
            "--root",
            str(root),
            "--top",
            "10",
            "--json",
            str(destination),
        ]
    )
    captured = capsys.readouterr()
    assert "badness 3.000" in captured.out
    assert "ghost_box,far_range_miss,occluded_miss" in captured.out
    assert "missed_vru" not in captured.out
    document = json.loads(destination.read_text(encoding="utf-8"))
    validate_schema(document, SCHEMA)
    assert document["latency_compared"] is False
    frames = _as_list(document["frames"])
    assert len(frames) == 1
    frame = _as_dict(frames[0])
    assert frame["badness"] == 3.0
    assert frame["tags"] == ["ghost_box", "far_range_miss", "occluded_miss"]
    assert frame["scene_id"] == SCENE
    golden = ROOT / "tests/fixtures/eval/failures_golden.json"
    assert destination.read_text(encoding="utf-8") == golden.read_text(encoding="utf-8")

    with pytest.raises(SystemExit) as caught:
        main(["eval", "failures", "det-b", "--root", str(root), "--latency-run", "det-a"])
    assert caught.value.code == 1
    assert "latency" in capsys.readouterr().err

    (root / "predictions" / "det-a" / "perturbation.json").write_text(
        json.dumps({"latency_frames": 1, "seed": 1}) + "\n", encoding="utf-8"
    )
    main(
        [
            "eval",
            "failures",
            "det-b",
            "--root",
            str(root),
            "--latency-run",
            "det-a",
            "--json",
            "-",
        ]
    )
    body = json.loads(capsys.readouterr().out)
    assert body["latency_compared"] is True
    late_frames = _as_list(body["frames"])
    tags: list[object] = []
    for item in late_frames:
        tags.extend(_as_list(_as_dict(item)["tags"]))
    assert "latency_sensitive" in tags


def test_custom_weights_change_the_pedestrian_term(tmp_path: Path) -> None:
    path = tmp_path / "weights.json"
    path.write_text(json.dumps({"vru_weight": 4}) + "\n", encoding="utf-8")
    from coperceive.evaluation.failures import weights_from_json

    weights = weights_from_json(json.loads(path.read_text(encoding="utf-8")))
    assert isinstance(weights, FailureWeights)
    document = mine_failures(
        [_record(category="pedestrian", status="miss", distance_m=10.0)],
        run_id="run",
        weights=weights,
    )
    frames = _as_list(document["frames"])
    first = _as_dict(frames[0])
    assert first["badness"] == 8.0
