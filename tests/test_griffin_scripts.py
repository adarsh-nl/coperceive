# SPDX-License-Identifier: Apache-2.0
"""``scripts/griffin``: the val trimmer's file list and the det-JSON converter's append mode."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from coperceive.evaluation.config import DEFAULT_CONFIG
from coperceive.evaluation.evaluate import evaluate_run
from coperceive.evaluation.predictions import read_boxes, read_run_meta
from coperceive.store import write_scene
from scripts.griffin import griffin_to_predictions as conv
from scripts.griffin import scene_infos_val as trim

from tests.cooperative import cooperative_scene
from tests.griffin_release import write_release

SCENES: dict[str, list[int]] = {"Town03-000": [11185, 11186], "Town03-001": [11190, 11191]}


def test_trimmer_lists_drone_cameras_only_when_asked(tmp_path: Path) -> None:
    raw = tmp_path / "release"
    write_release(raw, SCENES)
    split = tmp_path / "split.json"
    split.write_text(
        json.dumps({"batch_split": {"train": [], "val": ["scene-0000-Town03-000"]}}),
        encoding="utf-8",
    )
    common = [
        "--raw",
        str(raw),
        "--split",
        str(split),
        "--out",
        str(tmp_path / "stage"),
        "--split-out",
        str(tmp_path / "split-out.json"),
        "--no-check",
    ]
    listing = tmp_path / "files.txt"
    trim.main([*common, "--files-from", str(listing)])
    vehicle_only = listing.read_text(encoding="utf-8").split()
    assert "vehicle-side/camera/front/011185.png" in vehicle_only
    assert not [f for f in vehicle_only if f.startswith("drone-side/camera/")]
    trim.main([*common, "--files-from", str(listing), "--drone-cams", "front,bottom"])
    with_drone = listing.read_text(encoding="utf-8").split()
    assert "drone-side/camera/front/011185.png" in with_drone
    assert "drone-side/camera/bottom/011186.png" in with_drone
    assert "drone-side/camera/left/011185.png" not in with_drone
    assert "drone-side/camera/front/011190.png" not in with_drone  # not a val frame


def _tables(root: Path, frames: list[int]) -> Path:
    """``sample.json`` and ``scene.json`` for one scene named like the toolkit does."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "scene.json").write_text(
        json.dumps([{"token": "sc0", "name": "scene-0000-Town03-000"}]), encoding="utf-8"
    )
    samples = [
        {"token": f"sample_{f}", "scene_token": "sc0", "timestamp": conv.BASE_US + f * conv.STEP_US}
        for f in frames
    ]
    (root / "sample.json").write_text(json.dumps(samples), encoding="utf-8")
    return root


def _det_json(path: Path, frames: list[int], *, x: float, score: float = 0.9) -> Path:
    results = {
        f"sample_{f}": [
            {
                "translation": [x + f, 0.0, 0.8],
                "size": [2.0, 4.0, 1.6],  # w, l, h
                "rotation": [1.0, 0.0, 0.0, 0.0],
                "detection_name": "car",
                "detection_score": score,
            }
        ]
        for f in frames
    }
    path.write_text(json.dumps({"results": results}), encoding="utf-8")
    return path


def test_append_folds_drone_and_fused_outputs_into_one_run(tmp_path: Path) -> None:
    """Vehicle single, then drone single, then vehicle fused in one run: three outputs,
    merged categories and sources, and Step A evaluates all three."""
    scenes = tmp_path / "scenes"
    scene = replace(cooperative_scene(), scene_id="Town03-000")
    write_scene(scene, scenes / "Town03-000")
    frames = [0, 1, 2]  # times 0, 100000, 200000: the vehicle's and the uav's pose times
    tables = _tables(tmp_path / "tables", frames)
    predictions = tmp_path / "predictions"
    base = [
        "--nusc-root",
        str(tables),
        "--scenes",
        str(scenes),
        "--predictions",
        str(predictions),
        "--run",
        "coop",
        "--categories",
        "car,bicycle,pedestrian",
        "--score-note",
        "constant",
    ]
    vehicle = _det_json(tmp_path / "vehicle.json", frames, x=10.0)
    assert (
        conv.main([*base, "--det-json", str(vehicle), "--model", "veh", "--agent", "vehicle"]) == 0
    )
    drone = _det_json(tmp_path / "drone.json", frames[1:], x=20.0)  # the uav has no pose at time 0
    assert (
        conv.main([*base, "--det-json", str(drone), "--model", "uav", "--agent", "uav", "--append"])
        == 0
    )
    fused = _det_json(tmp_path / "fused.json", frames, x=30.0)
    fused_args = [
        *base,
        "--det-json",
        str(fused),
        "--model",
        "fusion",
        "--agent",
        "vehicle",
        "--source",
        "fused",
        "--source-note",
        "instance fusion",
        "--append",
    ]
    assert conv.main(fused_args) == 0

    meta = read_run_meta(predictions, "coop")
    assert meta.model == "veh | uav | fusion"
    assert [name for name, _ in meta.sources] == ["single", "fused"]
    assert meta.categories == ("car", "bicycle", "pedestrian")
    rows = read_boxes(predictions, "coop", "Town03-000")
    outputs = {(row.agent_id, row.source) for row in rows}
    assert outputs == {("vehicle", "single"), ("uav", "single"), ("vehicle", "fused")}
    assert len(rows) == 8  # 3 vehicle single + 2 uav single + 3 vehicle fused
    # The same output again is refused without --force, replaced with it.
    assert conv.main(fused_args) == 1  # ConvertError: output already present
    assert conv.main([*fused_args, "--force"]) == 0
    assert len(read_boxes(predictions, "coop", "Town03-000")) == 8

    report = evaluate_run(
        scenes, predictions, "coop", DEFAULT_CONFIG, created_utc="2026-10-17T00:00:00Z"
    )
    assert report.evaluated == ("Town03-000",)
    metrics = json.loads((predictions / "coop" / "Town03-000" / "metrics.json").read_text("utf-8"))
    evaluated = {(o["agent_id"], o["source"]) for o in metrics["outputs"]}
    assert evaluated == outputs
