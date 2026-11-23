# SPDX-License-Identifier: Apache-2.0
"""The row over every output does not depend on the order of the rows (spec A.8).

One scene, one time, two agents ``a`` and ``b``, one car each. Agent ``a``'s single
output finds its car at score 0.5; agent ``b``'s single output puts a box on nothing
at score 0.9 and misses its car. The row over every output pools both: n_gt 2.

Merged by score within the frame, the walk is fp (0.9), tp (0.5): precision 0 then
1/2 at recall 1/2, so AP = 0.5 * 0.5 = 0.25, under ``per_frame`` and ``pooled`` alike.
Before the fix, ``per_frame`` joined the outputs in the order they first appear in
``boxes.parquet``: with ``a``'s row first the walk was tp, fp and AP 0.5.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from coperceive.evaluation.config import DEFAULT_CONFIG
from coperceive.evaluation.evaluate import evaluate_run
from coperceive.evaluation.predictions import PredictionBox, RunMeta, write_run
from coperceive.evaluation.report import aggregate, collect
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

CREATED = "2026-11-16T00:00:00Z"
SIZE = (4.0, 2.0, 1.5)


def _car(agent: str, track: str, x: float) -> Annotation:
    return Annotation(
        agent, 0, track, "car", (x, 0.0, 0.0), SIZE, SE3.identity(), 1.0, BoxFrame.WORLD
    )


def _scene() -> Scene:
    agents = tuple(
        Agent(agent_id, AgentType.VEHICLE, (StampedPose(0, SE3.identity().translation(x, 0, 0)),))
        for agent_id, x in (("a", 0.0), ("b", 200.0))
    )
    annotations = (_car("a", "1", 10.0), _car("b", "2", 210.0))
    return Scene(
        schema_version=SCHEMA_VERSION,
        scene_id="s",
        world_frame="ENU",
        clock=Clock(unit="us", scale_note="every-output test"),
        agents=agents,
        sensors=(),
        annotations=annotations,
        objects=(),
    )


def _box(agent: str, score: float, x: float) -> PredictionBox:
    return PredictionBox(
        scene_id="s",
        agent_id=agent,
        source="single",
        time_us=0,
        category="car",
        score=score,
        track_id=None,
        center=(x, 0.0, 0.0),
        size_lwh=SIZE,
        box_from_rpy=SE3.identity(),
        frame=BoxFrame.WORLD,
    )


ROWS = (_box("a", 0.5, 10.0), _box("b", 0.9, 260.0))


@pytest.mark.parametrize("ranking_scope", ["per_frame", "pooled"])
@pytest.mark.parametrize("order", [ROWS, ROWS[::-1]], ids=["a-first", "b-first"])
def test_the_row_over_every_output_ignores_row_order(
    tmp_path: Path, ranking_scope: str, order: tuple[PredictionBox, ...]
) -> None:
    root = tmp_path / "scenes"
    write_scene(_scene(), root / "s")
    predictions = tmp_path / "predictions"
    meta = RunMeta(
        run_id="r",
        model="test",
        score_note="constant",
        categories=("car",),
        sources=(("single", "own sensors"),),
        created_utc=CREATED,
    )
    write_run(predictions, "r", meta, order)
    config = replace(DEFAULT_CONFIG, ranking_scope=ranking_scope, gt_scope="ego_only")
    assert evaluate_run(root, predictions, "r", config, created_utc=CREATED).errors == ()
    total = aggregate(collect(root, predictions, "r"), config.thresholds)
    assert total["mAP@0.5 all"] == pytest.approx(0.25)
