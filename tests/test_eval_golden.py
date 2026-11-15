# SPDX-License-Identifier: Apache-2.0
"""Golden cases of ``docs/eval-spec.md`` Part D, worked by hand.

The table in the document is the source of the expected values: this module
reads it, builds each case, and asks both the product and the reference
evaluator for the number. A value in the table that either evaluator does not
reproduce fails, and so does a case that is in the table and not here, or here
and not in the table. The arithmetic column of the table is for the reader.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

import pytest
from coperceive.evaluation.geometry import WorldBox, bev_iou, centre_distance_m, yaw_error_deg
from coperceive.se3 import SE3

from tests import reference_eval
from tests.evalcheck.product import evaluate_in_memory, evaluate_on_disk
from tests.evalcheck.scenario import (
    FRAME_BODY,
    FRAME_WORLD,
    SOURCE_FUSED,
    SOURCE_SINGLE,
    AgentSpec,
    BoxSpec,
    ConfigSpec,
    GtSpec,
    OutputResult,
    PooledResult,
    PoseSample,
    PredSpec,
    Quat,
    RunSpec,
    SceneResult,
    SceneSpec,
)

SPEC = Path(__file__).resolve().parents[1] / "docs" / "eval-spec.md"
_ROW = re.compile(r"^\|\s*(D\d+)\s*\|(.*)\|\s*`([^`|]+)`\s*\|\s*$")


def _table() -> dict[str, float]:
    """``{case id: value}`` from the Part D table: the last cell, a number or ``a/b``."""
    values: dict[str, float] = {}
    for line in SPEC.read_text(encoding="utf-8").splitlines():
        match = _ROW.match(line)
        if match is None:
            continue
        case_id, text = match.group(1), match.group(3).strip()
        assert case_id not in values, f"{case_id} is in the table twice"
        values[case_id] = float(Fraction(text)) if "/" in text else float(text)
    return values


# --- building cases -----------------------------------------------------------------


def _yaw(deg: float) -> Quat:
    half = math.radians(deg) / 2.0
    return (math.cos(half), 0.0, 0.0, math.sin(half))


def _box(
    x: float,
    y: float,
    length: float = 4.0,
    width: float = 2.0,
    *,
    yaw: float = 0.0,
    z: float = 0.0,
    frame: str = FRAME_WORLD,
) -> BoxSpec:
    return BoxSpec((x, y, z), (length, width, 1.5), _yaw(yaw), frame)


def _agent(
    agent_id: str = "ego", *, at: tuple[float, float, float] = (0.0, 0.0, 0.0), yaw: float = 0.0
) -> AgentSpec:
    return AgentSpec(agent_id, (PoseSample(0, _yaw(yaw), at),))


def _gt(
    box: BoxSpec,
    *,
    agent: str = "ego",
    category: str = "car",
    track: str | None = None,
    visibility: float | None = None,
) -> GtSpec:
    return GtSpec(agent, 0, category, track, visibility, box)


def _pred(
    box: BoxSpec,
    score: float,
    *,
    agent: str = "ego",
    source: str = SOURCE_SINGLE,
    category: str = "car",
) -> PredSpec:
    return PredSpec(agent, source, 0, category, score, box)


def _scene(
    ground_truth: tuple[GtSpec, ...],
    predictions: tuple[PredSpec, ...],
    *,
    scene_id: str = "s",
    agents: tuple[AgentSpec, ...] | None = None,
    shared: tuple[tuple[str, str], ...] = (),
) -> SceneSpec:
    return SceneSpec(
        scene_id, agents if agents is not None else (_agent(),), ground_truth, predictions, shared
    )


def _run(*scenes: SceneSpec, config: ConfigSpec | None = None) -> RunSpec:
    return RunSpec(("car",), config if config is not None else ConfigSpec(), scenes)


FAR = _box(60.0, 60.0)  # a prediction that overlaps nothing


@dataclass(frozen=True)
class Engine:
    """One evaluator behind the few questions a golden case asks."""

    name: str
    iou: Callable[[BoxSpec, BoxSpec], float]
    centre: Callable[[BoxSpec, BoxSpec], float]
    yaw_error: Callable[[BoxSpec, BoxSpec], float]
    scenes: Callable[[RunSpec], tuple[SceneResult, ...]]
    pooled: Callable[[RunSpec], tuple[PooledResult, ...]]

    def output(self, run: RunSpec, source: str = SOURCE_SINGLE, agent: str = "ego") -> OutputResult:
        for scene in self.scenes(run):
            for output in scene.outputs:
                if (output.agent_id, output.source) == (agent, source):
                    return output
        raise KeyError((agent, source))


def _world(box: BoxSpec) -> WorldBox:
    q = box.rotation
    return WorldBox(box.center, box.size_lwh, SE3(q[0], q[1], q[2], q[3], 0.0, 0.0, 0.0))


def _placed(box: BoxSpec) -> reference_eval.PlacedBox:
    return reference_eval.place_box(box, None)


def _number(value: float | None) -> float:
    assert value is not None
    return value


# --- the cases ---------------------------------------------------------------------


def _one_gt(*predictions: PredSpec, config: ConfigSpec | None = None) -> RunSpec:
    return _run(_scene((_gt(_box(0.0, 0.0)),), predictions), config=config)


def _three_gt_five_predictions() -> RunSpec:
    boxes = (_box(0.0, 0.0), _box(10.0, 0.0), _box(20.0, 0.0))
    return _run(
        _scene(
            tuple(_gt(box) for box in boxes),
            (
                _pred(boxes[0], 0.9),
                _pred(FAR, 0.8),
                _pred(boxes[1], 0.7),
                _pred(_box(-60.0, 60.0), 0.6),
                _pred(boxes[2], 0.5),
            ),
        )
    )


def _duplicate_next_to_a_neighbour() -> RunSpec:
    # Two overlapping ground-truth cars (IoU 0.6) and the same detection twice on the first.
    return _run(
        _scene(
            (_gt(_box(0.0, 0.0)), _gt(_box(1.0, 0.0))),
            (_pred(_box(0.0, 0.0), 0.9), _pred(_box(0.0, 0.0), 0.8)),
        )
    )


def _ignored_box_absorbs() -> RunSpec:
    # The detection sits exactly on an occluded car that is ignored; a kept car overlaps it by 0.6.
    return _run(
        _scene(
            (_gt(_box(0.0, 0.0), visibility=0.1), _gt(_box(1.0, 0.0), visibility=0.9)),
            (_pred(_box(0.0, 0.0), 0.9),),
        ),
        config=ConfigSpec(ignore_gt_visibility_below=0.5),
    )


def _two_scenes() -> RunSpec:
    first = _scene((_gt(_box(0.0, 0.0)),), (_pred(_box(0.0, 0.0), 0.9),), scene_id="a")
    second = _scene(
        (_gt(_box(0.0, 0.0)),), (_pred(FAR, 0.8), _pred(_box(0.0, 0.0), 0.7)), scene_id="b"
    )
    return _run(first, second)


def _range_limit() -> RunSpec:
    on_limit, beyond = _box(50.0, 0.0), _box(0.0, 50.5)
    return _run(
        _scene((_gt(on_limit), _gt(beyond)), (_pred(on_limit, 0.9), _pred(beyond, 0.8))),
        config=ConfigSpec(max_range_m=50.0),
    )


def _body_frame() -> RunSpec:
    # The agent stands at (10, 5) facing +y; 2 m ahead of it is the world point (10, 7).
    return _run(
        _scene(
            (_gt(_box(10.0, 7.0, yaw=90.0)),),
            (_pred(_box(2.0, 0.0, frame=FRAME_BODY), 0.9),),
            agents=(_agent(at=(10.0, 5.0, 0.0), yaw=90.0),),
        )
    )


def _fusion(*, single_hits: bool, fused_hits: bool, scene_id: str = "s") -> SceneSpec:
    target = _box(0.0, 0.0)
    return _scene(
        (_gt(target),),
        (
            _pred(target if single_hits else FAR, 0.9, source=SOURCE_SINGLE),
            _pred(target if fused_hits else FAR, 0.9, source=SOURCE_FUSED),
        ),
        scene_id=scene_id,
    )


def _seen_by_other(offset: tuple[float, float, float], length: float) -> RunSpec:
    mine = BoxSpec((0.0, 0.0, 0.0), (length, 2.0, 1.5), _yaw(0.0), FRAME_WORLD)
    theirs = BoxSpec(offset, (length, 2.0, 1.5), _yaw(0.0), FRAME_WORLD)
    return _run(
        _scene(
            (_gt(mine), _gt(theirs, agent="rsu")),
            (_pred(mine, 0.9),),
            agents=(_agent(), _agent("rsu", at=(30.0, 0.0, 5.0))),
        )
    )


def _every_output_row(engine: Engine, run: RunSpec) -> PooledResult:
    row = engine.pooled(run)[-1]
    assert row.agent_id is None and row.source is None
    return row


def _coop_seen(engine: Engine, run: RunSpec) -> float:
    return float(engine.output(run).gt_cooperative[0].seen_by_other_gt)


CASES: dict[str, Callable[[Engine], float]] = {
    # Geometry (A.3)
    "D01": lambda e: e.iou(_box(0.0, 0.0), _box(0.0, 0.0)),
    "D02": lambda e: e.iou(_box(0.0, 0.0), _box(2.0, 0.0)),
    "D03": lambda e: e.iou(_box(0.0, 0.0), _box(0.0, 0.0, yaw=90.0)),
    "D04": lambda e: e.iou(_box(0.0, 0.0), _box(0.5, 0.25, 1.0, 1.0)),
    "D05": lambda e: e.iou(_box(0.0, 0.0), _box(4.0, 0.0)),
    "D06": lambda e: e.iou(_box(0.0, 0.0, 2.0, 2.0), _box(0.0, 0.0, 2.0, 2.0, yaw=45.0)),
    "D07": lambda e: e.iou(_box(3.0, 1.0, yaw=180.0), _box(3.0, 1.0, yaw=-180.0)),
    "D08": lambda e: e.yaw_error(_box(0.0, 0.0, yaw=179.0), _box(0.0, 0.0, yaw=-179.0)),
    "D09": lambda e: e.centre(_box(0.0, 0.0), _box(3.0, 4.0, z=10.0)),
    "D10": lambda e: e.output(_body_frame()).pairs[0].iou_bev,
    "D11": lambda e: e.output(_body_frame()).pairs[0].centre_error_m,
    # The AP walk (A.5)
    "D12": lambda e: _number(
        e.output(_one_gt(_pred(FAR, 0.9), _pred(_box(0.0, 0.0), 0.8))).ap[0].per_category["car"]
    ),
    "D13": lambda e: _number(e.output(_three_gt_five_predictions()).ap[0].per_category["car"]),
    "D14": lambda e: _number(
        e.output(
            _run(_scene((_gt(_box(0.0, 0.0)), _gt(_box(10.0, 0.0))), (_pred(_box(0.0, 0.0), 0.9),)))
        )
        .ap[0]
        .per_category["car"]
    ),
    # Duplicates (A.4 rule 3)
    "D15": lambda e: _number(
        e.output(_one_gt(_pred(_box(0.0, 0.0), 0.9), _pred(_box(0.0, 0.0), 0.8)))
        .ap[0]
        .per_category["car"]
    ),
    "D16": lambda e: _number(
        e.output(_one_gt(_pred(_box(0.0, 0.0), 0.9), _pred(_box(0.0, 0.0), 0.8))).totals.precision
    ),
    "D17": lambda e: _number(e.output(_duplicate_next_to_a_neighbour()).ap[0].per_category["car"]),
    "D18": lambda e: _number(e.output(_duplicate_next_to_a_neighbour()).ap[1].per_category["car"]),
    # An ignored box takes the detection (A.4 rule 3 step 6)
    "D19": lambda e: _number(e.output(_ignored_box_absorbs()).totals.recall),
    "D20": lambda e: _number(e.output(_ignored_box_absorbs()).ap[0].per_category["car"]),
    # Equal scores follow row order (A.4 rule 2, A.5 step 3)
    "D21": lambda e: _number(
        e.output(_one_gt(_pred(FAR, 0.5), _pred(_box(0.0, 0.0), 0.5))).ap[0].per_category["car"]
    ),
    "D22": lambda e: _number(
        e.output(_one_gt(_pred(_box(0.0, 0.0), 0.5), _pred(FAR, 0.5))).ap[0].per_category["car"]
    ),
    # Pooling (A.8)
    "D23": lambda e: _number(e.pooled(_two_scenes())[0].ap[0].per_category["car"]),
    "D24": lambda e: _number(e.scenes(_two_scenes())[1].outputs[0].ap[0].per_category["car"]),
    # The operating point (A.4 rule 3 step 2, A.6)
    "D25": lambda e: _number(e.output(_one_gt(_pred(_box(0.0, 0.0), 0.3))).totals.recall),
    "D26": lambda e: _number(e.output(_one_gt(_pred(_box(0.0, 0.0), 0.29))).totals.recall),
    "D27": lambda e: _number(
        e.output(_one_gt(_pred(_box(0.0, 0.0), 0.29))).ap[0].per_category["car"]
    ),
    # The range limit (A.4 rule 1)
    "D28": lambda e: float(e.output(_range_limit()).totals.tp),
    "D29": lambda e: float(e.output(_range_limit()).totals.ignored_gt),
    "D30": lambda e: _number(e.output(_range_limit()).totals.recall),
    # Class map (A.2)
    "D31": lambda e: _number(
        e.output(
            _run(
                _scene((_gt(_box(0.0, 0.0), category="truck"),), (_pred(_box(0.0, 0.0), 0.9),)),
                config=ConfigSpec(gt_class_map={"truck": "car"}),
            )
        )
        .ap[0]
        .per_category["car"]
    ),
    # Cooperative counts (A.9, A.8)
    "D32": lambda e: float(
        e.output(_run(_fusion(single_hits=False, fused_hits=True))).cooperative.recovered
    ),
    "D33": lambda e: float(
        e.output(_run(_fusion(single_hits=True, fused_hits=False))).cooperative.lost_by_fusion
    ),
    "D34": lambda e: float(
        _every_output_row(
            e,
            _run(
                _fusion(single_hits=False, fused_hits=True, scene_id="a"),
                _fusion(single_hits=False, fused_hits=True, scene_id="b"),
            ),
        ).recovered
    ),
    "D35": lambda e: _coop_seen(e, _seen_by_other((2.0, 0.0, 0.0), 4.0)),
    "D36": lambda e: _coop_seen(e, _seen_by_other((0.0, 0.0, 2.5), 4.0)),
    "D37": lambda e: _coop_seen(e, _seen_by_other((0.0, 0.0, 2.5), 6.0)),
}


@pytest.fixture(params=("product", "reference"))
def engine(request: pytest.FixtureRequest, tmp_path: Path) -> Engine:
    if request.param == "reference":
        return Engine(
            name="reference",
            iou=lambda a, b: reference_eval.bev_iou(_placed(a), _placed(b)),
            centre=lambda a, b: reference_eval.centre_distance_m(_placed(a), _placed(b)),
            yaw_error=lambda a, b: reference_eval.yaw_error_deg(_placed(a), _placed(b)),
            scenes=lambda run: reference_eval.evaluate(run).scenes,
            pooled=lambda run: reference_eval.evaluate(run).pooled,
        )
    counter = iter(range(1_000_000))

    def pooled(run: RunSpec) -> tuple[PooledResult, ...]:
        return evaluate_on_disk(run, tmp_path / f"run-{next(counter)}").pooled

    return Engine(
        name="product",
        iou=lambda a, b: bev_iou(_world(a), _world(b)),
        centre=lambda a, b: centre_distance_m(_world(a), _world(b)),
        yaw_error=lambda a, b: yaw_error_deg(_world(a), _world(b)),
        scenes=evaluate_in_memory,
        pooled=pooled,
    )


def test_the_table_and_the_cases_are_the_same_set() -> None:
    table = _table()
    assert len(table) >= 30, "Part D of docs/eval-spec.md lost its table"
    assert sorted(table) == sorted(CASES)


def test_every_golden_value_is_reproduced(engine: Engine) -> None:
    wrong: list[str] = []
    for case_id, expected in sorted(_table().items()):
        got = CASES[case_id](engine)
        if not math.isclose(got, expected, rel_tol=0.0, abs_tol=1e-9):
            wrong.append(f"{case_id}: {engine.name} gives {got!r}, the table says {expected!r}")
    assert not wrong, "\n".join(wrong)
