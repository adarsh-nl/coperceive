# SPDX-License-Identifier: Apache-2.0
"""The connected-union ground truth of a fused output (``docs/eval-spec.md`` A.4, A.9).

One scene, one time, three agents on the x axis: the ego ``a`` at the origin, ``b``
at 30 m (connected), ``c`` at 100 m (out of the 70 m range). Annotations:

* ``a``: A1, a car at (10, 0).
* ``b``: B1 at (10.3, 0), the same car as A1 (0.3 m apart, under the 2 m gate);
  B2 at (40, 5), hit by a LiDAR return; B3 at (50, -5), hit by none.
* ``c``: C1 at (90, 0), hit by a return, but ``c`` is not connected.

Every count below is worked by hand from the spec, not read from the product.
"""

from __future__ import annotations

import struct
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest
from coperceive.evaluation.config import DEFAULT_CONFIG, EvalConfig
from coperceive.evaluation.evaluate import GT_AGENT_COLUMN, evaluate_run, read_matches, read_metrics
from coperceive.evaluation.predictions import PredictionBox, RunMeta, write_run
from coperceive.model import (
    SCHEMA_VERSION,
    Agent,
    AgentType,
    Annotation,
    BoxFrame,
    Clock,
    Modality,
    PointCloud,
    Scene,
    Sensor,
    StampedPose,
)
from coperceive.se3 import SE3
from coperceive.store import write_scene

CREATED = "2026-11-16T00:00:00Z"
SIZE = (4.0, 2.0, 1.5)
UNION = replace(DEFAULT_CONFIG, ranking_scope="pooled", gt_scope="connected_union")
EGO_ONLY = replace(DEFAULT_CONFIG, ranking_scope="pooled", gt_scope="ego_only")


def _agent(agent_id: str, x: float, z: float = 0.0) -> Agent:
    return Agent(
        agent_id=agent_id,
        agent_type=AgentType.VEHICLE,
        poses=(StampedPose(0, SE3.identity().translation(x, 0.0, z)),),
    )


def _car(agent_id: str, track: str, x: float, y: float) -> Annotation:
    return Annotation(
        agent_id, 0, track, "car", (x, y, 0.0), SIZE, SE3.identity(), 1.0, BoxFrame.WORLD
    )


def _scene(*, lidar: bool, b_x: float = 30.0, b_z: float = 0.0) -> Scene:
    agents = (_agent("a", 0.0), _agent("b", b_x, b_z), _agent("c", 100.0))
    annotations = (
        _car("a", "a1", 10.0, 0.0),
        _car("b", "b1", 10.3, 0.0),
        _car("b", "b2", 40.0, 5.0),
        _car("b", "b3", 50.0, -5.0),
        _car("c", "c1", 90.0, 0.0),
    )
    sensors: tuple[Sensor, ...] = ()
    clouds: tuple[PointCloud, ...] = ()
    if lidar:
        # The ego's sweep, in its body frame (the ego sits at the origin): one return
        # inside A1, one inside B2, one inside C1, none inside B3.
        points = ((10.0, 0.0, 0.0), (40.5, 5.2, 0.3), (90.0, 0.4, 0.0))
        sensors = (Sensor("lidar", "a", Modality.LIDAR, SE3.identity()),)
        clouds = (
            PointCloud(
                "a",
                "lidar",
                0,
                len(points),
                b"".join(struct.pack("<4f", x, y, z, 0.0) for x, y, z in points),
            ),
        )
    return Scene(
        schema_version=SCHEMA_VERSION,
        scene_id="s",
        world_frame="ENU",
        clock=Clock(unit="us", scale_note="protocol test"),
        agents=agents,
        sensors=sensors,
        annotations=annotations,
        objects=(),
        point_clouds=clouds,
    )


def _box(source: str, score: float, x: float, y: float, agent: str = "a") -> PredictionBox:
    return PredictionBox(
        scene_id="s",
        agent_id=agent,
        source=source,
        time_us=0,
        category="car",
        score=score,
        track_id=None,
        center=(x, y, 0.0),
        size_lwh=SIZE,
        box_from_rpy=SE3.identity(),
        frame=BoxFrame.WORLD,
    )


# The fused output finds A1 (0.9), then B3 (0.85), B2 (0.8) and C1 (0.6). The single
# output finds A1 (0.9) and B2 (0.8).
FUSED = (
    _box("fused", 0.9, 10.0, 0.0),
    _box("fused", 0.85, 50.0, -5.0),
    _box("fused", 0.8, 40.0, 5.0),
    _box("fused", 0.6, 90.0, 0.0),
)
SINGLE = (_box("single", 0.9, 10.0, 0.0), _box("single", 0.8, 40.0, 5.0))


def _evaluate(
    tmp_path: Path,
    scene: Scene,
    config: EvalConfig,
    rows: tuple[PredictionBox, ...] = FUSED + SINGLE,
) -> Path:
    root = tmp_path / "scenes"
    write_scene(scene, root / scene.scene_id)
    predictions = tmp_path / "predictions"
    meta = RunMeta(
        run_id="r",
        model="test",
        score_note="constant",
        categories=("car",),
        sources=(("single", "own sensors"), ("fused", "cooperative")),
        created_utc=CREATED,
    )
    write_run(predictions, "r", meta, rows)
    report = evaluate_run(root, predictions, "r", config, created_utc=CREATED)
    assert report.errors == ()
    return predictions / "r" / "s"


def _output(directory: Path, source: str) -> dict[str, object]:
    for output in read_metrics(directory)["outputs"]:  # type: ignore[union-attr]
        if output["agent_id"] == "a" and output["source"] == source:  # type: ignore[index]
            return output  # type: ignore[return-value]
    raise KeyError(source)


def _ap(output: dict[str, object]) -> object:
    """The car AP at IoU 0.5 of one output of ``metrics.json``."""
    return cast(dict[str, dict[str, object]], output["ap"])["0.5"]["car"]


def _recovered(output: dict[str, object]) -> object:
    return cast(dict[str, object], output["cooperative"])["recovered"]


def _totals(output: dict[str, object]) -> tuple[object, ...]:
    totals = output["totals"]
    return tuple(totals[key] for key in ("gt", "tp", "fp", "miss"))  # type: ignore[index]


def test_a_fused_output_is_scored_against_what_a_connected_agent_saw(tmp_path: Path) -> None:
    """Set: A1 (own), B2 (b's, hit by a return). B1 is A1 again; B3 holds no return; c
    is 100 m away. Fused walk by score: tp (A1), fp (B3), tp (B2), fp (C1), n_gt 2:
    points (0.5, 1), (0.5, 0.5), (1, 2/3), (1, 0.5); envelope 1 then 2/3;
    AP = 0.5 * 1 + 0.5 * 2/3 = 5/6.
    """
    directory = _evaluate(tmp_path, _scene(lidar=True), UNION)
    fused = _output(directory, "fused")
    assert _totals(fused) == (2, 2, 2, 0)
    assert _ap(fused) == pytest.approx(5 / 6)
    # The single output keeps a's own boxes: A1 tp, B2 fp.
    assert _totals(_output(directory, "single")) == (1, 1, 1, 0)
    # B2 is an object only b annotated, found by the fused output: recovered (A.9).
    assert _recovered(fused) == 1
    # matches.parquet says who annotated each ground-truth row of the fused output.
    columns = ["kind", "source", "row", GT_AGENT_COLUMN]
    rows = read_matches(directory, columns=columns).to_pylist()
    annotated = sorted(
        (row["row"], row[GT_AGENT_COLUMN])
        for row in rows
        if row["kind"] == "gt" and row["source"] == "fused"
    )
    assert [agent for _row, agent in annotated] == ["a", "b"]


def test_ego_only_scores_the_fused_output_against_its_own_boxes(tmp_path: Path) -> None:
    """Set: A1 only. Fused walk: tp first, so AP 1.0; B3, B2 and C1 are false positives."""
    directory = _evaluate(tmp_path, _scene(lidar=True), EGO_ONLY)
    fused = _output(directory, "fused")
    assert _totals(fused) == (1, 1, 3, 0)
    assert _ap(fused) == pytest.approx(1.0)
    assert _recovered(fused) == 0
    assert _totals(_output(directory, "single")) == (1, 1, 1, 0)


def test_with_no_sweep_each_connected_agents_list_counts_as_observed(tmp_path: Path) -> None:
    """No LiDAR at the time: b's list stands for what b saw (A.4 item 2). Set: A1, B2, B3
    (B1 is still A1). Fused walk: tp, tp, tp, fp; n_gt 3; AP 1.0.
    """
    directory = _evaluate(tmp_path, _scene(lidar=False), UNION)
    fused = _output(directory, "fused")
    assert _totals(fused) == (3, 3, 1, 0)
    assert _ap(fused) == pytest.approx(1.0)
    assert _recovered(fused) == 2


def test_the_range_is_horizontal_and_inclusive(tmp_path: Path) -> None:
    """b at exactly 70 m along x and 5 m up: horizontally 70 m, so connected (A.4 item 1).

    Its boxes are in the world frame, so the set is the one of the first test.
    """
    directory = _evaluate(tmp_path, _scene(lidar=True, b_x=70.0, b_z=5.0), UNION)
    assert _totals(_output(directory, "fused")) == (2, 2, 2, 0)
    # One centimetre further and b is out of range: the set is A1 alone.
    beyond = tmp_path / "beyond"
    directory = _evaluate(beyond, _scene(lidar=True, b_x=70.01, b_z=5.0), UNION)
    assert _totals(_output(directory, "fused")) == (1, 1, 3, 0)


def test_an_added_box_the_fused_pass_ignores_counts_in_no_cooperative_count(
    tmp_path: Path,
) -> None:
    """``max_range_m: 30``: B2, 40.3 m from a, is in the set but ignored by rule 1.

    b's own single output finds B2 (11.2 m from b), so ``other_agent_tp`` is true, but a
    box whose status is ignored under either output falls in none of the counts (A.9).
    Found by the differential suite: ``only_other_agent`` counted it.
    """
    config = replace(UNION, max_range_m=30.0)
    rows = FUSED + SINGLE + (_box("single", 0.9, 40.0, 5.0, agent="b"),)
    directory = _evaluate(tmp_path, _scene(lidar=True), config, rows)
    fused = _output(directory, "fused")
    # A1 is the only kept box; every other fused prediction is beyond 30 m.
    assert _totals(fused) == (1, 1, 0, 0)
    cooperative = cast(dict[str, object], fused["cooperative"])
    assert cooperative["recovered"] == 0
    assert cooperative["only_other_agent"] == 0
