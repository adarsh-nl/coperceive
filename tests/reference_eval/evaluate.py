# SPDX-License-Identifier: Apache-2.0
"""Scenes, outputs and headline rows of the reference evaluator.

``docs/eval-spec.md`` Part A, sections A.0 and A.4 to A.9: the outputs and samples
of a run and of a scene, the ground-truth sets, the passes over every sample, the
per-scene tables and the rows pooled over scenes. Written from Part A alone; imports
nothing from ``coperceive``.
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
    Vec3,
)
from tests.reference_eval.ap import (
    ApRecord,
    average_precision,
    mean_of_non_null,
    per_frame_order,
    walk_average_precision,
)
from tests.reference_eval.cooperative import (
    CoopBox,
    cooperative_counts,
    other_kind_of,
    pair_boxes,
)
from tests.reference_eval.geometry import (
    BoxInterior,
    PlacedBox,
    Rectangle,
    check_prediction_box,
    horizontal_range_m,
    place_box,
    rectangle_of,
)
from tests.reference_eval.gtset import Sweep, connected_agents, union_additions
from tests.reference_eval.matching import (
    GT_SCOPE_CONNECTED_UNION,
    RANKING_POOLED,
    GtBox,
    PairMeasures,
    PredBox,
    match_sample,
    validate_config,
)

OutputKey = tuple[str, str]  # (agent_id, source), A.0
ApRecords = dict[str, list[ApRecord]]  # category -> (score, status, time) in row order


@dataclass(frozen=True)
class _ApInput:
    """What A.5 and A.8 need from one ``(scene, output)`` pair.

    ``n_gt[c]`` is ``n_gt(c)`` of A.5 step 1. ``records[i][c]`` is the list of A.5
    step 3 at threshold index ``i`` before ordering: the output's predictions of
    category ``c`` in row order, each with its status in the AP pass at ``i`` and its
    sample time. ``categories`` is the category set of the output.
    """

    n_gt: dict[str, int]
    records: tuple[ApRecords, ...]
    categories: frozenset[str]


@dataclass(frozen=True)
class _Matched:
    """The passes of A.4 over every sample of one output, gathered by row."""

    key: OutputKey
    pred_status: dict[int, str]  # operating-point pass
    gt_status: dict[int, str]  # operating-point pass, every box of the ground-truth sets
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
        self.shared_track_ids = frozenset(track_id for track_id, _category in scene.shared_tracks)

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

        # A.0: the (agent, source) pairs of the scene's own rows, ordered by the row where
        # they first occur, with their rows.
        self.pred_rows: dict[OutputKey, list[int]] = {}
        for row, pred in enumerate(scene.predictions):
            self.pred_rows.setdefault((pred.agent_id, pred.source), []).append(row)

        # The annotations of every agent by time, in row order (A.4, A.9).
        self.gt_rows: dict[str, dict[int, list[int]]] = {}
        for row, gt in enumerate(scene.ground_truth):
            self.gt_rows.setdefault(gt.agent_id, {}).setdefault(gt.time_us, []).append(row)

        # The LiDAR returns of every agent by time, in body coordinates (A.4 "Observed").
        # Two sweeps of one agent at one time are taken together.
        self._sweep_points: dict[tuple[str, int], list[Vec3]] = {}
        for sweep in scene.lidar:
            key = (sweep.agent_id, sweep.time_us)
            self._sweep_points.setdefault(key, []).extend(sweep.points)

        # The outputs of the scene, set by ``set_outputs`` once the run's are known.
        self.outputs: list[OutputKey] = []

        self._placed_gt: dict[int, PlacedBox] = {}
        self._rectangles: dict[int, Rectangle] = {}
        self._own: dict[tuple[str, int], tuple[GtBox, ...]] = {}
        self._union: dict[tuple[str, int], tuple[GtBox, ...]] = {}
        self._sweeps: dict[tuple[str, int], Sweep] = {}
        self._interiors: dict[int, BoxInterior] = {}
        self._hit: dict[tuple[int, str], bool] = {}

    def set_outputs(self, run_outputs: Sequence[OutputKey]) -> None:
        """A.0: the outputs of the scene, from the outputs of the run.

        They are the run outputs whose agent is an agent of the scene: first those that
        occur in the scene's own rows, in the order of their first row, then the others
        in run order. Every one is evaluated here, with or without rows.
        """
        with_rows = list(self.pred_rows)
        others = [key for key in run_outputs if key[0] in self.poses and key not in self.pred_rows]
        self.outputs = with_rows + others

        # A.4: an annotation of an evaluated agent must sit on one of its pose times.
        evaluated_agents = {agent_id for agent_id, _source in self.outputs}
        for row, gt in enumerate(self.scene.ground_truth):
            if gt.agent_id in evaluated_agents and self.pose(gt.agent_id, gt.time_us) is None:
                raise ValueError(
                    f"scene {self.scene.scene_id!r}: annotation row {row} of evaluated agent "
                    f"{gt.agent_id!r} is at time {gt.time_us}, which is not one of its pose times"
                )

    def rows_of(self, key: OutputKey) -> list[int]:
        """The prediction rows of an output in the scene, in row order (none is possible)."""
        return self.pred_rows.get(key, [])

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

    def coop_box(self, row: int) -> CoopBox:
        """An annotation as the pairing of A.9 sees it."""
        return CoopBox(
            row=row, track_id=self.scene.ground_truth[row].track_id, placed=self.placed_gt(row)
        )

    def _gt_box(self, row: int, ego_pose: PoseSample, added_by: str | None) -> GtBox:
        """One box of a ground-truth set, with rule 1 measured from the output's agent."""
        gt = self.scene.ground_truth[row]
        placed = self.placed_gt(row)
        rectangle = self._rectangles.get(row)
        if rectangle is None:
            rectangle = rectangle_of(placed)
            self._rectangles[row] = rectangle
        return GtBox(
            row=row,
            # A.2 gt_class_map, applied to ground truth before anything else.
            category=self.config.gt_class_map.get(gt.category, gt.category),
            placed=placed,
            rectangle=rectangle,
            ignored=self._gt_ignored(gt.visibility, placed, ego_pose),
            added_by=added_by,
        )

    def own_boxes(self, agent_id: str, time_us: int) -> tuple[GtBox, ...]:
        """The agent's own annotations at one of its pose times, in row order (A.4)."""
        key = (agent_id, time_us)
        boxes = self._own.get(key)
        if boxes is None:
            pose = self.poses[agent_id][time_us]
            rows = self.gt_rows.get(agent_id, {}).get(time_us, [])
            boxes = tuple(self._gt_box(row, pose, None) for row in rows)
            self._own[key] = boxes
        return boxes

    def gt_set(self, key: OutputKey, time_us: int) -> tuple[GtBox, ...]:
        """A.4 "Ground-truth set" of an output at one of its agent's pose times, in its order.

        ``(X, fused)`` under ``gt_scope: connected_union`` gets the union over the agents
        connected to ``X``; every other output, and every output under ``ego_only``,
        gets its agent's own annotations.
        """
        agent_id, source = key
        if source == SOURCE_FUSED and self.config.gt_scope == GT_SCOPE_CONNECTED_UNION:
            return self._union_boxes(agent_id, time_us)
        return self.own_boxes(agent_id, time_us)

    def _union_boxes(self, agent_id: str, time_us: int) -> tuple[GtBox, ...]:
        """A.4 rules 1 to 3: ``X``'s own boxes, then those its connected agents add."""
        key = (agent_id, time_us)
        boxes = self._union.get(key)
        if boxes is not None:
            return boxes
        ego_pose = self.poses[agent_id][time_us]
        own = self.own_boxes(agent_id, time_us)

        # Rule 1: connected agents, in scene order.
        poses_at_t = [
            (agent.agent_id, self.pose(agent.agent_id, time_us)) for agent in self.scene.agents
        ]
        connected = connected_agents(agent_id, ego_pose, poses_at_t, self.config.comm_range_m)

        # Rule 2: the sweeps recorded at t by X or by a connected agent. With none of
        # them, every annotation of every connected agent counts as observed.
        sweep_agents = [
            sweep_agent
            for sweep_agent in [agent_id, *connected]
            if (sweep_agent, time_us) in self._sweep_points
        ]
        contributions: list[tuple[str, list[CoopBox]]] = []
        for other_id in connected:
            rows = self.gt_rows.get(other_id, {}).get(time_us, [])
            observed = [
                row
                for row in rows
                if not sweep_agents
                or any(self._hit_by(row, sweep_agent, time_us) for sweep_agent in sweep_agents)
            ]
            contributions.append((other_id, [self.coop_box(row) for row in observed]))

        # Rule 3: own boxes first, then each connected agent's unpaired observed boxes.
        added = union_additions(
            [self.coop_box(box.row) for box in own], contributions, self.shared_track_ids
        )
        boxes = own + tuple(self._gt_box(box.row, ego_pose, adder) for box, adder in added)
        self._union[key] = boxes
        return boxes

    def _hit_by(self, row: int, sweep_agent: str, time_us: int) -> bool:
        """A.4 rule 2: whether a return of ``sweep_agent``'s sweep at ``t`` is inside the box."""
        key = (row, sweep_agent)
        hit = self._hit.get(key)
        if hit is None:
            sweep = self._sweeps.get((sweep_agent, time_us))
            if sweep is None:
                # A sweep is only asked for at a time its agent has a pose sample at.
                pose = self.poses[sweep_agent][time_us]
                sweep = Sweep(pose, self._sweep_points[(sweep_agent, time_us)])
                self._sweeps[(sweep_agent, time_us)] = sweep
            interior = self._interiors.get(row)
            if interior is None:
                interior = BoxInterior(self.placed_gt(row))
                self._interiors[row] = interior
            hit = sweep.hits(interior)
            self._hit[key] = hit
        return hit

    def pred_boxes(self, key: OutputKey) -> dict[int, list[PredBox]]:
        """A.4 "P" of every sample of an output: its predictions by time, in row order.

        Every row was checked when the scene was indexed (A.0), so each one has the
        pose sample of its agent at its time and belongs to exactly one sample.
        """
        agent_id, _source = key
        by_time: dict[int, list[PredBox]] = {}
        for row in self.rows_of(key):
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
    categories = set(declared)

    # A.0: every pose time of the agent is a sample, with or without boxes.
    for time_us in index.pose_times(agent_id):
        gts = index.gt_set(key, time_us)
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

        for gt in gts:
            # A.5 step 1: n_gt(c) counts the boxes of the sets that rule 1 does not ignore.
            if not gt.ignored:
                n_gt[gt.category] = n_gt.get(gt.category, 0) + 1
            # A.5 category set: the mapped categories of all boxes of the sets.
            categories.add(gt.category)

    # A.5 step 3: per threshold index and category, the output's rows in row order with
    # their AP-pass status and their sample time. Every row is in exactly one sample (A.0).
    records: list[ApRecords] = [{} for _ in range(n_thresholds)]
    for row in index.rows_of(key):
        pred = scene.predictions[row]
        categories.add(pred.category)
        for i in range(n_thresholds):
            records[i].setdefault(pred.category, []).append(
                (pred.score, pred_status_at[i][row], pred.time_us)
            )

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


def _ap_of_category(
    inputs: Sequence[_ApInput], category: str, i: int, ranking_scope: str
) -> float | None:
    """A.5 steps 1 to 6 (one input) or the pooled AP of A.8 (several, in pooling order)."""
    n_gt = sum(ap_input.n_gt.get(category, 0) for ap_input in inputs)
    if ranking_scope == RANKING_POOLED:
        # Concatenated in the order given, row order within each, then one score sort.
        records = [
            (score, status)
            for ap_input in inputs
            for score, status, _time in ap_input.records[i].get(category, [])
        ]
        return average_precision(records, n_gt)
    # per_frame: each (scene, output) list in per-frame order, concatenated, not re-sorted.
    statuses = [
        status
        for ap_input in inputs
        for _score, status, _time in per_frame_order(ap_input.records[i].get(category, []))
    ]
    return walk_average_precision(statuses, n_gt)


def _ap_table(
    inputs: Sequence[_ApInput], declared: Sequence[str], config: ConfigSpec
) -> tuple[ApResult, ...]:
    """AP per threshold index over ``(scene, output)`` pairs given in pooling order.

    With one pair this is the per-scene table of A.5. With several it is the pooled
    table of A.8: ``n_gt(c)`` summed, the lists of step 3 joined in the order given by
    the ranking scope, steps 2 to 6 run once, and the category sets united.
    """
    categories: set[str] = set()
    for ap_input in inputs:
        categories.update(ap_input.categories)
    ordered = _ordered_categories(categories, declared)
    declared_set = dict.fromkeys(declared)

    table: list[ApResult] = []
    for i in range(len(config.thresholds)):
        per_category: dict[str, float | None] = {
            category: _ap_of_category(inputs, category, i, config.ranking_scope)
            for category in ordered
        }
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
    """A.9: the cooperative columns of every box of the set of ``(X, fused)``, by row.

    The boxes are those of the ground-truth set of ``(X, fused)`` at each pose time of
    ``X`` (A.4), or ``X``'s own boxes when the scene has no ``(X, fused)`` output.
    ``gt_status_of`` holds, for every output of the scene, the ground-truth statuses of
    its operating-point pass by row.
    """
    scene = index.scene
    fused_key = (agent_id, SOURCE_FUSED)
    single = gt_status_of.get((agent_id, SOURCE_SINGLE))
    fused = gt_status_of.get(fused_key)

    columns: dict[int, GtCooperativeResult] = {}
    for time_us in index.pose_times(agent_id):
        own_rows = [box.row for box in index.own_boxes(agent_id, time_us)]
        objects = index.gt_set(fused_key, time_us) if fused is not None else None

        # For each own box g of X at t: the boxes of other agents paired with it, as
        # (other agent, row of its box, kind of the pair).
        partners: dict[int, list[tuple[str, int, str]]] = {row: [] for row in own_rows}
        if own_rows:
            xs = [index.coop_box(row) for row in own_rows]
            for other in scene.agents:  # each other agent Y of the scene, in scene order
                other_id = other.agent_id
                if other_id == agent_id:
                    continue
                other_rows = index.gt_rows.get(other_id, {}).get(time_us, [])
                if not other_rows:
                    continue  # Y has no annotation at t
                ys = [index.coop_box(row) for row in other_rows]
                for i, j, kind in pair_boxes(xs, ys, index.shared_track_ids):
                    partners[xs[i].row].append((other_id, ys[j].row, kind))

        for row in own_rows:
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
                added_by=None,
            )

        # A box another agent Y added: no pairing; status_single is absent (null here).
        for box in objects or ():
            adder = box.added_by
            if adder is None:
                continue
            adder_single = gt_status_of.get((adder, SOURCE_SINGLE))
            columns[box.row] = GtCooperativeResult(
                status_single=None,
                status_fused=None if fused is None else fused[box.row],
                seen_by_other_gt=True,
                other_kind=None,
                other_agent_tp=None if adder_single is None else adder_single[box.row] == STATUS_TP,
                added_by=adder,
            )
    return dict(sorted(columns.items()))


# --- one scene ---------------------------------------------------------------------------


def _evaluate_scene(index: _SceneIndex, run: RunSpec, display_index: int) -> list[_OutputEval]:
    """Everything of A.4 to A.9 for one evaluated scene, one entry per output in output order."""
    scene = index.scene

    # A.4 to A.6: the passes of every output.
    matched = [_match_output(index, key, run.categories, display_index) for key in index.outputs]

    # A.9: the columns depend on the operating-point statuses of other outputs, so they
    # come after every output is matched. The counts are the same for every output of
    # an agent; each output shows the columns of the boxes of its own ground-truth sets.
    gt_status_of = {output.key: output.gt_status for output in matched}
    columns_of: dict[str, dict[int, GtCooperativeResult]] = {}
    counts_of: dict[str, CooperativeResult] = {}
    for agent_id, _source in index.outputs:
        if agent_id not in columns_of:
            columns = _cooperative_columns(index, agent_id, gt_status_of)
            columns_of[agent_id] = columns
            counts_of[agent_id] = cooperative_counts(
                columns.values(),
                has_single_and_fused=(agent_id, SOURCE_SINGLE) in gt_status_of
                and (agent_id, SOURCE_FUSED) in gt_status_of,
            )

    evaluated: list[_OutputEval] = []
    for output in matched:
        agent_id, source = output.key
        columns = columns_of[agent_id]
        result = OutputResult(
            agent_id=agent_id,
            source=source,
            pred_status=output.pred_status,
            gt_status=output.gt_status,
            pred_status_at=output.pred_status_at,
            pairs=output.pairs,
            samples=output.samples,
            totals=_totals(output.samples, len(index.rows_of(output.key))),
            ap=_ap_table([output.ap_input], run.categories, run.config),
            tp_errors=_tp_errors(scene, output.pairs),
            cooperative=counts_of[agent_id],
            gt_cooperative={row: columns[row] for row in output.gt_status},
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
        ap=_ap_table([entry.ap_input for entry in evals], run.categories, run.config),
    )


def evaluate(run: RunSpec) -> RunResult:
    """Evaluate a run: A.2 to A.9 for every evaluated scene, then the headline rows of A.8."""
    display_index = validate_config(run.config)

    # A.0 / A.8: the run covers every one of its scenes, also those without a prediction,
    # and every covered scene is evaluated. Scenes are taken in ascending scene_id order.
    scenes = sorted(run.scenes, key=lambda scene: scene.scene_id)
    for earlier, later in zip(scenes, scenes[1:], strict=False):
        if earlier.scene_id == later.scene_id:
            raise ValueError(f"scene {later.scene_id!r} is given twice")
    indexes = [_SceneIndex(scene, run.config) for scene in scenes]

    # A.0: the outputs of the run, by first occurrence over the scenes in scene_id order.
    run_outputs = list(dict.fromkeys(key for index in indexes for key in index.pred_rows))
    for index in indexes:
        index.set_outputs(run_outputs)

    scene_results: list[SceneResult] = []
    every_output: list[_OutputEval] = []  # ordered by scene, then by output order
    by_output: dict[OutputKey, list[_OutputEval]] = {key: [] for key in run_outputs}
    for index in indexes:
        evals = _evaluate_scene(index, run, display_index)
        scene_results.append(
            SceneResult(
                scene_id=index.scene.scene_id, outputs=tuple(entry.result for entry in evals)
            )
        )
        for entry in evals:
            every_output.append(entry)
            by_output[entry.key].append(entry)

    # One row per output of the run, in run order, then the row over every output.
    pooled = [_pooled_row(key, by_output[key], run) for key in run_outputs]
    pooled.append(_pooled_row(None, every_output, run))
    return RunResult(scenes=tuple(scene_results), pooled=tuple(pooled))
