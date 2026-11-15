# SPDX-License-Identifier: Apache-2.0
"""Scenes, outputs and headline rows of the reference evaluator.

``docs/eval-spec.md`` Part A, sections A.0 and A.4 to A.9: the outputs and samples
of a scene, the passes over every sample, the per-scene tables and the rows pooled
over scenes. Written from Part A alone; imports nothing from ``coperceive``.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from tests.evalcheck.scenario import (
    SOURCE_FUSED,
    SOURCE_SINGLE,
    STATUS_FP,
    STATUS_IGNORED,
    STATUS_MISS,
    STATUS_TP,
    ApResult,
    ConfigSpec,
    CooperativeResult,
    GtCooperativeResult,
    OutputResult,
    PairResult,
    PooledResult,
    PoseSample,
    RunResult,
    RunSpec,
    SampleResult,
    SceneResult,
    SceneSpec,
    TotalsResult,
    TpErrorsResult,
)
from tests.reference_eval.ap import average_precision, mean_of_non_null
from tests.reference_eval.cooperative import (
    CoopBox,
    cooperative_counts,
    other_kind_of,
    pair_boxes,
)
from tests.reference_eval.geometry import (
    PlacedBox,
    check_prediction_box,
    horizontal_range_m,
    place_box,
    rectangle_of,
)
from tests.reference_eval.matching import (
    GtBox,
    PairMeasures,
    PredBox,
    match_sample,
    validate_config,
)

OutputKey = tuple[str, str]  # (agent_id, source), A.0
ApRecords = dict[str, list[tuple[float, str]]]  # category -> (score, status) in row order


@dataclass(frozen=True)
class _ApInput:
    """What A.5 and A.8 need from one ``(scene, output)`` pair.

    ``n_gt[c]`` is ``n_gt(c)`` of A.5 step 1. ``records[i][c]`` is the list of A.5
    step 3 at threshold index ``i`` before sorting: the output's predictions of
    category ``c`` in row order, each with its status in the AP pass at ``i``.
    ``categories`` is the category set of the output.
    """

    n_gt: dict[str, int]
    records: tuple[ApRecords, ...]
    categories: frozenset[str]


@dataclass(frozen=True)
class _Matched:
    """The passes of A.4 over every sample of one output, gathered by row."""

    key: OutputKey
    pred_status: dict[int, str]  # operating-point pass
    gt_status: dict[int, str]  # operating-point pass
    pred_status_at: tuple[dict[int, str], ...]  # AP pass per threshold index
    pairs: tuple[PairResult, ...]  # operating-point pass, samples in time order
    samples: tuple[SampleResult, ...]
    ap_input: _ApInput


@dataclass(frozen=True)
class _OutputEval:
    """One ``(scene, output)`` pair: its result, and its AP lists for pooling (A.8)."""

    scene_id: str
    key: OutputKey
    result: OutputResult
    ap_input: _ApInput


def _ratio(numerator: int, denominator: int) -> float | None:
    """A quotient of counts, null when the denominator is 0 (A.6, A.8)."""
    return numerator / denominator if denominator else None


def _mean(values: Sequence[float]) -> float | None:
    return math.fsum(values) / len(values) if values else None


def _median(values: Sequence[float]) -> float | None:
    """A.7: the median; of an even count, the mean of the two middle values."""
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


class _SceneIndex:
    """One scene, indexed the way Part A talks about it, with its placed boxes kept."""

    def __init__(self, scene: SceneSpec, config: ConfigSpec) -> None:
        self.scene = scene
        self.config = config

        # Pose samples by agent and time. A sample of an output is one pose time (A.0).
        self.poses: dict[str, dict[int, PoseSample]] = {}
        for agent in scene.agents:
            if agent.agent_id in self.poses:
                raise ValueError(
                    f"scene {scene.scene_id!r}: agent {agent.agent_id!r} is listed twice"
                )
            times = [pose.time_us for pose in agent.poses]
            if any(later <= earlier for earlier, later in zip(times, times[1:], strict=False)):
                raise ValueError(
                    f"scene {scene.scene_id!r}: pose times of agent {agent.agent_id!r} "
                    "are not strictly increasing"
                )
            self.poses[agent.agent_id] = {pose.time_us: pose for pose in agent.poses}

        # A.0: before anything is matched, every prediction row is checked: its agent is
        # an agent of the scene and its time is one of that agent's pose times, whatever
        # its frame. Its box is checked too (A.1 item 6, A.3 "Footprint"). A scene with a
        # row that fails is not evaluated at all.
        for row, pred in enumerate(scene.predictions):
            where = f"scene {scene.scene_id!r}: prediction row {row}"
            if pred.agent_id not in self.poses:
                raise ValueError(f"{where}: {pred.agent_id!r} is not an agent of the scene (A.0)")
            if pred.time_us not in self.poses[pred.agent_id]:
                raise ValueError(
                    f"{where}: time {pred.time_us} is not a pose time of agent "
                    f"{pred.agent_id!r} (A.0)"
                )
            try:
                check_prediction_box(pred.box)
            except ValueError as error:
                raise ValueError(f"{where}: {error}") from error

        # A.0: the outputs, ordered by the row where they first occur, with their rows.
        self.pred_rows: dict[OutputKey, list[int]] = {}
        for row, pred in enumerate(scene.predictions):
            self.pred_rows.setdefault((pred.agent_id, pred.source), []).append(row)
        evaluated_agents = {agent_id for agent_id, _source in self.pred_rows}

        # The annotations of every agent by time, in row order (A.4 "G", A.9).
        self.gt_rows: dict[str, dict[int, list[int]]] = {}
        for row, gt in enumerate(scene.ground_truth):
            # A.4: an annotation of an evaluated agent must sit on one of its pose times.
            if gt.agent_id in evaluated_agents and self.pose(gt.agent_id, gt.time_us) is None:
                raise ValueError(
                    f"scene {scene.scene_id!r}: annotation row {row} of evaluated agent "
                    f"{gt.agent_id!r} is at time {gt.time_us}, which is not one of its pose times"
                )
            self.gt_rows.setdefault(gt.agent_id, {}).setdefault(gt.time_us, []).append(row)

        self._placed_gt: dict[int, PlacedBox] = {}
        self._gt_boxes: dict[tuple[str, int], tuple[GtBox, ...]] = {}

    def pose(self, agent_id: str, time_us: int) -> PoseSample | None:
        """The pose sample of an agent whose time is exactly ``time_us``, if it has one."""
        return self.poses.get(agent_id, {}).get(time_us)

    def pose_times(self, agent_id: str) -> list[int]:
        """The samples of an agent's outputs: its pose times, in time order (A.0)."""
        return list(self.poses.get(agent_id, {}))

    def placed_gt(self, row: int) -> PlacedBox:
        """The annotation of a row placed in the world (A.3), placed once."""
        placed = self._placed_gt.get(row)
        if placed is None:
            gt = self.scene.ground_truth[row]
            try:
                placed = place_box(gt.box, self.pose(gt.agent_id, gt.time_us))
            except ValueError as error:
                raise ValueError(
                    f"scene {self.scene.scene_id!r}: annotation row {row}: {error}"
                ) from error
            self._placed_gt[row] = placed
        return placed

    def gt_boxes(self, agent_id: str, time_us: int) -> tuple[GtBox, ...]:
        """A.4 "G" of a sample: the agent's annotations at a pose time, in row order."""
        key = (agent_id, time_us)
        boxes = self._gt_boxes.get(key)
        if boxes is None:
            pose = self.poses[agent_id][time_us]
            built: list[GtBox] = []
            for row in self.gt_rows.get(agent_id, {}).get(time_us, []):
                gt = self.scene.ground_truth[row]
                placed = self.placed_gt(row)
                built.append(
                    GtBox(
                        row=row,
                        # A.2 gt_class_map, applied to ground truth before anything else.
                        category=self.config.gt_class_map.get(gt.category, gt.category),
                        placed=placed,
                        rectangle=rectangle_of(placed),
                        ignored=self._gt_ignored(gt.visibility, placed, pose),
                    )
                )
            boxes = tuple(built)
            self._gt_boxes[key] = boxes
        return boxes

    def pred_boxes(self, key: OutputKey) -> dict[int, list[PredBox]]:
        """A.4 "P" of every sample of an output: its predictions by time, in row order.

        Every row was checked when the scene was indexed (A.0), so each one has the
        pose sample of its agent at its time and belongs to exactly one sample.
        """
        agent_id, _source = key
        by_time: dict[int, list[PredBox]] = {}
        for row in self.pred_rows[key]:
            pred = self.scene.predictions[row]
            pose = self.poses[agent_id][pred.time_us]
            try:
                placed = place_box(pred.box, pose)
            except ValueError as error:
                raise ValueError(
                    f"scene {self.scene.scene_id!r}: prediction row {row}: {error}"
                ) from error
            by_time.setdefault(pred.time_us, []).append(
                PredBox(
                    row=row,
                    category=pred.category,
                    score=pred.score,
                    placed=placed,
                    rectangle=rectangle_of(placed),
                    out_of_range=self._beyond_max_range(placed, pose),
                )
            )
        return by_time

    def _beyond_max_range(self, placed: PlacedBox, pose: PoseSample) -> bool:
        """A.4 rule 1: range ``> max_range_m``; a box exactly at ``max_range_m`` is kept."""
        max_range_m = self.config.max_range_m
        if max_range_m is None:
            return False
        return horizontal_range_m(placed, pose.translation) > max_range_m

    def _gt_ignored(self, visibility: float | None, placed: PlacedBox, pose: PoseSample) -> bool:
        """A.4 rule 1: a ground-truth box is ignored for low visibility or for range."""
        floor = self.config.ignore_gt_visibility_below
        if floor is not None and visibility is not None and visibility < floor:
            return True
        return self._beyond_max_range(placed, pose)


# --- A.4 to A.7: one output of one scene ----------------------------------------------


def _match_output(
    index: _SceneIndex, key: OutputKey, declared: Sequence[str], display_index: int
) -> _Matched:
    """Run the operating-point pass and every AP pass over every sample of an output (A.4)."""
    agent_id, _source = key
    config = index.config
    scene = index.scene
    n_thresholds = len(config.thresholds)
    preds_by_time = index.pred_boxes(key)

    pred_status: dict[int, str] = {}
    gt_status: dict[int, str] = {}
    pred_status_at: list[dict[int, str]] = [{} for _ in range(n_thresholds)]
    pairs: list[PairResult] = []
    samples: list[SampleResult] = []
    n_gt: dict[str, int] = {}

    # A.0: every pose time of the agent is a sample, with or without boxes.
    for time_us in index.pose_times(agent_id):
        gts = index.gt_boxes(agent_id, time_us)
        preds = preds_by_time.get(time_us, [])
        measures = PairMeasures(gts, preds, config)

        # The operating-point pass: i = d, operating = true.
        operating = match_sample(
            gts,
            preds,
            config=config,
            threshold_index=display_index,
            operating=True,
            measures=measures,
        )
        pred_status.update(operating.pred_status)
        gt_status.update(operating.gt_status)
        pairs.extend(operating.pairs)

        # One AP pass per threshold index: operating = false.
        for i in range(n_thresholds):
            ap_pass = match_sample(
                gts, preds, config=config, threshold_index=i, operating=False, measures=measures
            )
            pred_status_at[i].update(ap_pass.pred_status)

        # A.6: the counts of the sample, from the operating-point pass.
        tp = sum(1 for status in operating.pred_status.values() if status == STATUS_TP)
        fp = sum(1 for status in operating.pred_status.values() if status == STATUS_FP)
        miss = sum(1 for status in operating.gt_status.values() if status == STATUS_MISS)
        ignored_gt = sum(1 for status in operating.gt_status.values() if status == STATUS_IGNORED)
        samples.append(
            SampleResult(
                time_us=time_us,
                tp=tp,
                fp=fp,
                miss=miss,
                ignored_gt=ignored_gt,
                precision=_ratio(tp, tp + fp),
                recall=_ratio(tp, tp + miss),
            )
        )

        # A.5 step 1: n_gt(c) counts the annotations that rule 1 does not ignore.
        for gt in gts:
            if not gt.ignored:
                n_gt[gt.category] = n_gt.get(gt.category, 0) + 1

    # A.5 step 3: per threshold index and category, the output's rows in row order with
    # their AP-pass status. Every row is in exactly one sample (A.0), so it has one.
    records: list[ApRecords] = [{} for _ in range(n_thresholds)]
    for row in index.pred_rows[key]:
        pred = scene.predictions[row]
        for i in range(n_thresholds):
            records[i].setdefault(pred.category, []).append((pred.score, pred_status_at[i][row]))

    # A.5: the category set of the output.
    categories = set(declared)
    for rows in index.gt_rows.get(agent_id, {}).values():
        for row in rows:
            category = scene.ground_truth[row].category
            categories.add(config.gt_class_map.get(category, category))
    categories.update(scene.predictions[row].category for row in index.pred_rows[key])

    return _Matched(
        key=key,
        pred_status=dict(sorted(pred_status.items())),
        gt_status=dict(sorted(gt_status.items())),
        pred_status_at=tuple(dict(sorted(statuses.items())) for statuses in pred_status_at),
        pairs=tuple(pairs),
        samples=tuple(samples),
        ap_input=_ApInput(n_gt=n_gt, records=tuple(records), categories=frozenset(categories)),
    )


def _totals(samples: Sequence[SampleResult], n_predictions: int) -> TotalsResult:
    """A.6 "Totals" of an output in a scene."""
    tp = sum(sample.tp for sample in samples)
    fp = sum(sample.fp for sample in samples)
    miss = sum(sample.miss for sample in samples)
    return TotalsResult(
        tp=tp,
        fp=fp,
        miss=miss,
        ignored_gt=sum(sample.ignored_gt for sample in samples),
        gt=tp + miss,
        predictions=n_predictions,
        precision=_ratio(tp, tp + fp),
        recall=_ratio(tp, tp + miss),
    )


def _tp_errors(scene: SceneSpec, pairs: Sequence[PairResult]) -> TpErrorsResult:
    """A.7: the error summary over the pairs of the operating-point pass."""
    size_errors: tuple[list[float], list[float], list[float]] = ([], [], [])
    for pair in pairs:
        size_pred = scene.predictions[pair.pred_row].box.size_lwh
        size_gt = scene.ground_truth[pair.gt_row].box.size_lwh
        for component in range(3):
            size_errors[component].append(abs(size_pred[component] - size_gt[component]))
    centre_errors = [pair.centre_error_m for pair in pairs]
    return TpErrorsResult(
        centre_mean_m=_mean(centre_errors),
        centre_median_m=_median(centre_errors),
        yaw_mean_abs_deg=_mean([pair.yaw_error_deg for pair in pairs]),
        size_length_m=_mean(size_errors[0]),
        size_width_m=_mean(size_errors[1]),
        size_height_m=_mean(size_errors[2]),
        iou_bev_mean=_mean([pair.iou_bev for pair in pairs]),
    )


def _ordered_categories(categories: Iterable[str], declared: Sequence[str]) -> list[str]:
    """The declared categories in declared order, then the others in string order."""
    wanted = set(categories)
    ordered = [category for category in dict.fromkeys(declared) if category in wanted]
    ordered.extend(sorted(wanted.difference(ordered)))
    return ordered


def _ap_table(
    inputs: Sequence[_ApInput], declared: Sequence[str], n_thresholds: int
) -> tuple[ApResult, ...]:
    """AP per threshold index over ``(scene, output)`` pairs given in pooling order.

    With one pair this is the per-scene table of A.5. With several it is the pooled
    table of A.8: ``n_gt(c)`` summed, the lists of step 3 concatenated in the order
    given, steps 2 to 6 run once, and the category sets united.
    """
    categories: set[str] = set()
    for ap_input in inputs:
        categories.update(ap_input.categories)
    ordered = _ordered_categories(categories, declared)
    declared_set = dict.fromkeys(declared)

    table: list[ApResult] = []
    for i in range(n_thresholds):
        per_category: dict[str, float | None] = {}
        for category in ordered:
            n_gt = sum(ap_input.n_gt.get(category, 0) for ap_input in inputs)
            records = [
                record for ap_input in inputs for record in ap_input.records[i].get(category, [])
            ]
            per_category[category] = average_precision(records, n_gt)
        table.append(
            ApResult(
                per_category=per_category,
                # mean: over the category set. mean_model: over the declared categories.
                mean=mean_of_non_null(per_category.values()),
                mean_model=mean_of_non_null(per_category.get(c) for c in declared_set),
            )
        )
    return tuple(table)


# --- A.9: cooperative columns ------------------------------------------------------------


def _cooperative_columns(
    index: _SceneIndex, agent_id: str, gt_status_of: dict[OutputKey, dict[int, str]]
) -> dict[int, GtCooperativeResult]:
    """A.9: the cooperative columns of every ground-truth box of one evaluated agent.

    ``gt_status_of`` holds, for every output of the scene, the ground-truth statuses
    of its operating-point pass by row.
    """
    scene = index.scene
    shared_track_ids = frozenset(track_id for track_id, _category in scene.shared_tracks)
    single = gt_status_of.get((agent_id, SOURCE_SINGLE))
    fused = gt_status_of.get((agent_id, SOURCE_FUSED))

    def coop_boxes(rows: Sequence[int]) -> list[CoopBox]:
        return [
            CoopBox(
                row=row,
                track_id=scene.ground_truth[row].track_id,
                placed=index.placed_gt(row),
            )
            for row in rows
        ]

    columns: dict[int, GtCooperativeResult] = {}
    for time_us, rows in index.gt_rows.get(agent_id, {}).items():
        xs = coop_boxes(rows)
        # For each box of X at t: the boxes of other agents paired with it, as
        # (other agent, row of its box, kind of the pair).
        partners: dict[int, list[tuple[str, int, str]]] = {row: [] for row in rows}
        for other in scene.agents:  # each other agent Y of the scene, in scene order
            other_id = other.agent_id
            if other_id == agent_id:
                continue
            other_rows = index.gt_rows.get(other_id, {}).get(time_us, [])
            if not other_rows:
                continue  # Y has no annotation at t
            ys = coop_boxes(other_rows)
            for i, j, kind in pair_boxes(xs, ys, shared_track_ids):
                partners[xs[i].row].append((other_id, ys[j].row, kind))

        for row in rows:
            paired = partners[row]
            # other_agent_tp: over the paired boxes h of agents Y that have an output
            # (Y, single), is any of them a tp in the operating-point pass of that output?
            judged = [
                gt_status_of[(other_id, SOURCE_SINGLE)][other_row]
                for other_id, other_row, _kind in paired
                if (other_id, SOURCE_SINGLE) in gt_status_of
            ]
            columns[row] = GtCooperativeResult(
                status_single=None if single is None else single[row],
                status_fused=None if fused is None else fused[row],
                seen_by_other_gt=bool(paired),
                other_kind=other_kind_of({kind for _id, _row, kind in paired}),
                other_agent_tp=any(status == STATUS_TP for status in judged) if judged else None,
            )
    return dict(sorted(columns.items()))


# --- one scene ---------------------------------------------------------------------------


def _evaluate_scene(scene: SceneSpec, run: RunSpec, display_index: int) -> list[_OutputEval]:
    """Everything of A.4 to A.9 for one evaluated scene, one entry per output in output order."""
    index = _SceneIndex(scene, run.config)
    n_thresholds = len(run.config.thresholds)

    # A.4 to A.6: the passes of every output.
    matched = [_match_output(index, key, run.categories, display_index) for key in index.pred_rows]

    # A.9: the columns depend on the operating-point statuses of other outputs, so they
    # come after every output is matched. They are the same for every output of an agent.
    gt_status_of = {output.key: output.gt_status for output in matched}
    columns_of: dict[str, dict[int, GtCooperativeResult]] = {}
    for agent_id, _source in index.pred_rows:
        if agent_id not in columns_of:
            columns_of[agent_id] = _cooperative_columns(index, agent_id, gt_status_of)

    evaluated: list[_OutputEval] = []
    for output in matched:
        agent_id, source = output.key
        gt_cooperative = columns_of[agent_id]
        cooperative: CooperativeResult = cooperative_counts(gt_cooperative.values())
        result = OutputResult(
            agent_id=agent_id,
            source=source,
            pred_status=output.pred_status,
            gt_status=output.gt_status,
            pred_status_at=output.pred_status_at,
            pairs=output.pairs,
            samples=output.samples,
            totals=_totals(output.samples, len(index.pred_rows[output.key])),
            ap=_ap_table([output.ap_input], run.categories, n_thresholds),
            tp_errors=_tp_errors(scene, output.pairs),
            cooperative=cooperative,
            gt_cooperative=dict(gt_cooperative),
        )
        evaluated.append(
            _OutputEval(
                scene_id=scene.scene_id, key=output.key, result=result, ap_input=output.ap_input
            )
        )
    return evaluated


# --- A.8: headline rows ------------------------------------------------------------------


def _pooled_row(key: OutputKey | None, evals: Sequence[_OutputEval], run: RunSpec) -> PooledResult:
    """A.8: one headline row over ``(scene, output)`` pairs given in pooling order.

    ``key`` is the output of the row, or ``None`` for the row over every output.
    """
    tp = sum(entry.result.totals.tp for entry in evals)
    fp = sum(entry.result.totals.fp for entry in evals)
    gt = sum(entry.result.totals.gt for entry in evals)

    # recovered and lost_by_fusion. For one output: the sum of its per-scene counts. For
    # the row over every output they are not per-output facts (every output of an agent
    # carries the same value), so they are summed once per (scene, agent), from that
    # agent's first output in the scene. ``evals`` is in scene order, then output order.
    if key is None:
        counted: set[tuple[str, str]] = set()  # (scene_id, agent_id) already taken
        cooperative_of: list[_OutputEval] = []
        for entry in evals:
            scene_agent = (entry.scene_id, entry.key[0])
            if scene_agent not in counted:
                counted.add(scene_agent)
                cooperative_of.append(entry)
    else:
        cooperative_of = list(evals)

    return PooledResult(
        agent_id=None if key is None else key[0],
        source=None if key is None else key[1],
        gt=gt,
        predictions=sum(entry.result.totals.predictions for entry in evals),
        tp=tp,
        fp=fp,
        miss=sum(entry.result.totals.miss for entry in evals),
        precision=_ratio(tp, tp + fp),
        recall=_ratio(tp, gt),
        recovered=sum(entry.result.cooperative.recovered for entry in cooperative_of),
        lost_by_fusion=sum(entry.result.cooperative.lost_by_fusion for entry in cooperative_of),
        ap=_ap_table(
            [entry.ap_input for entry in evals], run.categories, len(run.config.thresholds)
        ),
    )


def evaluate(run: RunSpec) -> RunResult:
    """Evaluate a run: A.2 to A.9 for every evaluated scene, then the headline rows of A.8."""
    display_index = validate_config(run.config)

    # A.8: a scene is evaluated when the run has a prediction file for it, that is when
    # it has at least one prediction row. Scenes are taken in ascending scene_id order.
    scenes = sorted(
        (scene for scene in run.scenes if scene.predictions), key=lambda scene: scene.scene_id
    )
    for earlier, later in zip(scenes, scenes[1:], strict=False):
        if earlier.scene_id == later.scene_id:
            raise ValueError(f"scene {later.scene_id!r} is given twice")

    scene_results: list[SceneResult] = []
    every_output: list[_OutputEval] = []  # ordered by scene, then by output order
    by_output: dict[OutputKey, list[_OutputEval]] = {}  # outputs in first-appearance order
    for scene in scenes:
        evals = _evaluate_scene(scene, run, display_index)
        scene_results.append(
            SceneResult(scene_id=scene.scene_id, outputs=tuple(entry.result for entry in evals))
        )
        for entry in evals:
            every_output.append(entry)
            by_output.setdefault(entry.key, []).append(entry)

    pooled = [_pooled_row(key, evals, run) for key, evals in by_output.items()]
    pooled.append(_pooled_row(None, every_output, run))
    return RunResult(scenes=tuple(scene_results), pooled=tuple(pooled))
