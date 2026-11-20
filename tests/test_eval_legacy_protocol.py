# SPDX-License-Identifier: Apache-2.0
"""``protocol: legacy`` gives v0.1.1's numbers (docs/eval.md, "Reproduce v0.1.1 numbers").

The expected values are ``tests/fixtures/eval/v011_golden.json``, written by the code at
tag v0.1.1, not by this branch. Every count, precision, recall and AP must be equal.
One family of fields may differ, and the test says by how much: the BEV IoU of a
true positive (``tp_errors.iou_bev``) is now computed about the midpoint of the pair
(CHANGELOG, PR #165), which moves it in the last bits only.
"""

from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest
from coperceive.cli import main
from coperceive.demo.generate import create
from coperceive.evaluation.config import ConfigError, load_config, parse_config
from coperceive.store import write_scene

from tests.cooperative import cooperative_scene
from tests.eval_fixture import synthetic_predictions

GOLDEN = Path(__file__).parent / "fixtures" / "eval" / "v011_golden.json"
# The IoU of one pair moved by at most a few units in the last place (measured: 2.2e-16 on
# the demo, 5.6e-16 on the cooperative fixture). Only tp_errors.iou_bev may differ.
IOU_LAST_BITS = 1e-15
# Keys v0.1.2 adds to metrics.json: the protocol it was scored under, and the overlap.
ADDED = {
    "/config/ranking_scope": "pooled",
    "/config/gt_scope": "ego_only",
    "/config/comm_range_m": 70.0,
    "/iou": "bev",
}
SKIPPED = {"/created_utc", "/coperceive_version"}


def _golden() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(GOLDEN.read_text(encoding="utf-8")))


def _assert_same(old: object, new: object, path: str = "") -> None:
    if isinstance(old, dict) and isinstance(new, dict):
        old_d = cast(dict[str, object], old)
        new_d = cast(dict[str, object], new)
        for key in sorted(set(old_d) | set(new_d)):
            child = f"{path}/{key}"
            if child in SKIPPED:
                continue
            if key not in old_d:
                assert child in ADDED, f"{child}: not in v0.1.1"
                assert new_d[key] == ADDED[child], child
            elif key not in new_d:
                assert child in SKIPPED, f"{child}: missing"
            else:
                _assert_same(old_d[key], new_d[key], child)
        return
    if isinstance(old, list) and isinstance(new, list):
        old_l = cast(list[object], old)
        new_l = cast(list[object], new)
        assert len(old_l) == len(new_l), path
        for index, (a, b) in enumerate(zip(old_l, new_l, strict=True)):
            _assert_same(a, b, f"{path}[{index}]")
        return
    if "/tp_errors/iou_bev/" in path and isinstance(old, float) and isinstance(new, float):
        assert abs(old - new) <= IOU_LAST_BITS, f"{path}: {old!r} -> {new!r}"
        return
    assert old == new, f"{path}: {old!r} -> {new!r}"


def _report(root: Path, predictions: Path, run_id: str) -> str:
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        main(["eval", "report", run_id, "--root", str(root), "--predictions", str(predictions)])
    return buffer.getvalue()


def test_legacy_protocol_on_the_demo_gives_v011_numbers(tmp_path: Path) -> None:
    golden = _golden()["demo"]
    demo = create(tmp_path / "demo", seed=0)
    main(
        [
            "eval",
            "run",
            "demo",
            "--root",
            str(demo.scenes),
            "--predictions",
            str(demo.predictions),
            "--protocol",
            "legacy",
        ]
    )
    metrics = json.loads(
        (demo.predictions / "demo" / "demo-junction" / "metrics.json").read_text(encoding="utf-8")
    )
    _assert_same(golden["metrics"], metrics)
    # The report prints the same numbers. Two labels are new in v0.1.2: the protocol line
    # that heads it, and "(BEV)" after every AP column name (spec A.12).
    expected = str(golden["report"]).replace("AP@0.5", "AP@0.5 (BEV)")
    expected = expected.replace("AP@0.7", "AP@0.7 (BEV)")
    assert _report(demo.scenes, demo.predictions, "demo") == (
        "protocol: legacy (pooled, ego-only)\n\n" + expected
    )


def test_legacy_protocol_on_a_fused_output_gives_v011_numbers(tmp_path: Path) -> None:
    # The demo has no fused output, so it cannot show gt_scope: ego_only. This scene has one.
    golden = _golden()["cooperative"]
    scene = replace(cooperative_scene(), scene_id="coop")
    root = tmp_path / "scenes"
    write_scene(scene, root / "coop")
    _, boxes = synthetic_predictions(scene)
    rows: list[str] = []
    for box in boxes:
        rotation = box.box_from_rpy
        row = {
            "scene_id": "coop",
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
        rows.append(json.dumps(row) + "\n")
    jsonl = tmp_path / "boxes.jsonl"
    jsonl.write_text("".join(rows), encoding="utf-8")
    main(
        [
            "predictions",
            "import",
            str(jsonl),
            "--run",
            "synthetic",
            "--root",
            str(root),
            "--score-note",
            "constant",
        ]
    )
    config = tmp_path / "legacy.yaml"
    config.write_text("protocol: legacy\n", encoding="utf-8")
    main(["eval", "run", "synthetic", "--root", str(root), "--config", str(config)])
    metrics = json.loads(
        (root / "predictions" / "synthetic" / "coop" / "metrics.json").read_text(encoding="utf-8")
    )
    _assert_same(golden["metrics"], metrics)


def test_protocol_is_one_switch_in_the_config_and_on_the_command_line(tmp_path: Path) -> None:
    legacy = parse_config({"protocol": "legacy"})
    assert (legacy.ranking_scope, legacy.gt_scope) == ("pooled", "ego_only")
    assert load_config(None, "legacy") == legacy
    default = parse_config({})
    assert (default.ranking_scope, default.gt_scope, default.comm_range_m) == (
        "per_frame",
        "connected_union",
        70.0,
    )
    assert parse_config({"protocol": "default"}) == default
    # Saying the same thing twice is fine; saying something else is an error.
    assert parse_config({"protocol": "legacy", "ranking_scope": "pooled"}) == legacy
    with pytest.raises(ConfigError, match="ranking_scope: protocol legacy sets"):
        parse_config({"protocol": "legacy", "ranking_scope": "per_frame"})
    with pytest.raises(ConfigError, match="gt_scope: protocol legacy sets"):
        parse_config({"protocol": "legacy", "gt_scope": "connected_union"})
    with pytest.raises(ConfigError, match="protocol: expected one of default, legacy"):
        parse_config({"protocol": "v0.1.1"})
    path = tmp_path / "eval.yaml"
    path.write_text("protocol: default\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="--protocol says 'legacy'"):
        load_config(path, "legacy")
