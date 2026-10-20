# SPDX-License-Identifier: Apache-2.0
"""``scripts/griffin/filter_run.py`` and ``compare_runs.py`` on synthetic runs."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from coperceive.evaluation.predictions import read_boxes, read_run_meta, write_run
from scripts.griffin.compare_runs import compare_runs
from scripts.griffin.filter_run import filter_run

from tests.cooperative import cooperative_scene
from tests.eval_fixture import synthetic_predictions


def _merged(tmp_path: Path) -> tuple[Path, str]:
    scene = replace(cooperative_scene(), scene_id="coop")
    meta, rows = synthetic_predictions(scene)
    rows = tuple(replace(row, scene_id="coop") for row in rows)
    meta = replace(meta, run_id="merged")
    write_run(tmp_path / "old", "merged", meta, rows)
    return tmp_path / "old", "merged"


def test_filter_cuts_one_output_in_file_order(tmp_path: Path) -> None:
    root, run = _merged(tmp_path)
    scenes, rows = filter_run(root, run, "vehicle", "fused", tmp_path / "ref", "ref")
    assert scenes == 1 and rows > 0
    kept = read_boxes(tmp_path / "ref", "ref", "coop")
    original = [
        b for b in read_boxes(root, run, "coop") if (b.agent_id, b.source) == ("vehicle", "fused")
    ]
    assert list(kept) == original and len(kept) == rows
    assert {b.source for b in kept} == {"fused"}
    with pytest.raises(SystemExit, match="no source"):
        filter_run(root, run, "vehicle", "nope", tmp_path / "ref2", "ref2")


def test_compare_reports_identical_and_different_scenes(tmp_path: Path) -> None:
    root, run = _merged(tmp_path)
    filter_run(root, run, "vehicle", "fused", tmp_path / "ref", "ref")
    filter_run(root, run, "vehicle", "fused", tmp_path / "same", "same")
    lines, identical, total = compare_runs(tmp_path / "ref", "ref", tmp_path / "same", "same")
    assert (identical, total) == (1, 1) and lines[0].startswith("coop: byte-identical")
    # A different output of the same run: bytes differ, and the report says by how much.
    filter_run(root, run, "vehicle", "single", tmp_path / "other", "other")
    lines, identical, total = compare_runs(tmp_path / "ref", "ref", tmp_path / "other", "other")
    assert identical == 0 and "bytes differ" in lines[0] and "max |centre|" in lines[0]
    # Same rows in a different order: identical as a set, not as bytes.
    rows = list(read_boxes(tmp_path / "ref", "ref", "coop"))
    meta = replace(read_run_meta(tmp_path / "ref", "ref"), run_id="shuffled")
    write_run(tmp_path / "shuf", "shuffled", meta, list(reversed(rows)))
    lines, identical, _ = compare_runs(tmp_path / "ref", "ref", tmp_path / "shuf", "shuffled")
    assert identical == 0 and "same rows in another order True" in lines[0]
