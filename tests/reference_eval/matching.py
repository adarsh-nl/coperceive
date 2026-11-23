# SPDX-License-Identifier: Apache-2.0
"""Matching one sample in the reference evaluator: ``docs/eval-spec.md`` Part A, A.2 and A.4.

Written from Part A alone; imports nothing from ``coperceive``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from tests.evalcheck.scenario import (
    CRITERION_CENTRE,
    CRITERION_IOU_BEV,
    STATUS_BELOW_THRESHOLD,
    STATUS_FP,
    STATUS_IGNORED,
    STATUS_MISS,
    STATUS_TP,
    ConfigSpec,
    PairResult,
)
from tests.reference_eval.geometry import (
    PlacedBox,
    Rectangle,
    centre_distance_m,
    ious_of_rectangles,
    yaw_error_deg,
)

RANKING_PER_FRAME = "per_frame"
RANKING_POOLED = "pooled"
GT_SCOPE_CONNECTED_UNION = "connected_union"
GT_SCOPE_EGO_ONLY = "ego_only"


@dataclass(frozen=True, eq=False)
class GtBox:
    """One box of a sample's ground-truth set ``G`` (A.4).

    Placed, category mapped, rule 1 applied (ranges from the output's agent). ``row``
    is its annotation row index. ``added_by`` is the connected agent that added it to
    the set of a fused output (A.4 rule 3), ``None`` for one of the agent's own boxes.
    """

    row: int
    category: str
    placed: PlacedBox
    rectangle: Rectangle
    ignored: bool
    added_by: str | None = None


@dataclass(frozen=True, eq=False)
class PredBox:
    """One prediction of a sample's ``P`` (A.4): placed, rule 1 applied."""

    row: int
    category: str
    score: float
    placed: PlacedBox
    rectangle: Rectangle
    out_of_range: bool


@dataclass(frozen=True)
class SampleMatch:
    """The outcome of one pass over one sample: statuses by row, and the true-positive pairs.

    ``pairs`` is in the order the pairs were recorded, that is the visiting order of rule 2.
    """

    pred_status: dict[int, str]
    gt_status: dict[int, str]
    pairs: tuple[PairResult, ...]


def _check_threshold_list(values: Sequence[float], criterion: str, name: str) -> None:
    """A.2: the order and range rules of one threshold list under a criterion."""
    steps = list(zip(values, values[1:], strict=False))
    if criterion == CRITERION_IOU_BEV:
        if any(not 0.0 < value <= 1.0 for value in values):
            raise ValueError(f"{name}: iou_bev thresholds must each lie in (0, 1] (A.2)")
        if any(not earlier < later for earlier, later in steps):
            raise ValueError(f"{name}: iou_bev thresholds must be strictly increasing (A.2)")
    elif criterion == CRITERION_CENTRE:
        if any(not value > 0.0 for value in values):
            raise ValueError(f"{name}: centre thresholds must each be positive (A.2)")
        if any(not earlier > later for earlier, later in steps):
            raise ValueError(f"{name}: centre thresholds must be strictly decreasing (A.2)")
    else:
        raise ValueError(f"unknown criterion {criterion!r} (A.2)")


def validate_config(config: ConfigSpec) -> int:
    """A.2: refuse a config that breaks a rule of the table; return the display index ``d``.

    ``ConfigSpec`` has a fixed set of fields, so an unknown key cannot occur here, and
    ``thresholds`` is always present (its default is refused under ``centre`` by the
    order rule).
    """
    # The protocol keys.
    if config.ranking_scope not in (RANKING_PER_FRAME, RANKING_POOLED):
        raise ValueError(
            f"ranking_scope must be {RANKING_PER_FRAME!r} or {RANKING_POOLED!r}, "
            f"got {config.ranking_scope!r} (A.2)"
        )
    if config.gt_scope not in (GT_SCOPE_CONNECTED_UNION, GT_SCOPE_EGO_ONLY):
        raise ValueError(
            f"gt_scope must be {GT_SCOPE_CONNECTED_UNION!r} or {GT_SCOPE_EGO_ONLY!r}, "
            f"got {config.gt_scope!r} (A.2)"
        )
    if not config.comm_range_m > 0.0:
        raise ValueError(f"comm_range_m must be positive, got {config.comm_range_m!r} (A.2)")

    thresholds = config.thresholds
    _check_threshold_list(thresholds, config.criterion, "thresholds")
    # display_threshold must equal an entry of thresholds exactly.
    if config.display_threshold not in thresholds:
        raise ValueError("display_threshold must equal an entry of thresholds exactly (A.2)")
    # A class list has the length of thresholds and obeys the same order and range rules.
    for category, values in config.class_thresholds.items():
        name = f"class_thresholds[{category!r}]"
        if len(values) != len(thresholds):
            raise ValueError(f"{name} must have the length of thresholds (A.2)")
        _check_threshold_list(values, config.criterion, name)
    return thresholds.index(config.display_threshold)


def threshold_for(config: ConfigSpec, category: str, index: int) -> float:
    """A.2: the threshold a pair is judged at, for a threshold index and a prediction category."""
    if category in config.class_thresholds:
        return config.class_thresholds[category][index]
    return config.thresholds[index]


class PairMeasures:
    """BEV IoU and centre distance of the (prediction, annotation) pairs of one sample (A.3).

    Every pass over the sample asks for the same pairs, so each value is computed once.
    Under ``iou_bev`` the IoU of every pair that rule 3 can ask for (a prediction in
    range and an annotation of its category) is computed up front, in one call.
    """

    def __init__(self, gts: Sequence[GtBox], preds: Sequence[PredBox], config: ConfigSpec) -> None:
        self._iou: dict[tuple[int, int], float] = {}
        self._distance: dict[tuple[int, int], float] = {}
        if config.criterion == CRITERION_IOU_BEV:
            wanted = [
                (pred, gt)
                for pred in preds
                if not pred.out_of_range
                for gt in gts
                if gt.category == pred.category
            ]
            values = ious_of_rectangles([(pred.rectangle, gt.rectangle) for pred, gt in wanted])
            for (pred, gt), value in zip(wanted, values, strict=True):
                self._iou[(pred.row, gt.row)] = value

    def iou(self, pred: PredBox, gt: GtBox) -> float:
        key = (pred.row, gt.row)
        value = self._iou.get(key)
        if value is None:
            value = ious_of_rectangles([(pred.rectangle, gt.rectangle)])[0]
            self._iou[key] = value
        return value

    def distance(self, pred: PredBox, gt: GtBox) -> float:
        key = (pred.row, gt.row)
        value = self._distance.get(key)
        if value is None:
            value = centre_distance_m(pred.placed, gt.placed)
            self._distance[key] = value
        return value


def match_sample(
    gts: Sequence[GtBox],
    preds: Sequence[PredBox],
    *,
    config: ConfigSpec,
    threshold_index: int,
    operating: bool,
    measures: PairMeasures,
) -> SampleMatch:
    """A.4: one pass over one sample.

    ``gts`` is ``G`` in the order of the ground-truth set (an agent's own annotations
    in row order, then the boxes connected agents added) and ``preds`` is ``P`` in row
    order. ``operating`` is true for the operating-point pass and false for an AP pass.
    """
    use_iou = config.criterion == CRITERION_IOU_BEV
    if not use_iou and config.criterion != CRITERION_CENTRE:
        raise ValueError(f"unknown criterion {config.criterion!r} (A.2)")

    # Rule 2: by score, highest first; the sort is stable, so equal scores stay in row order.
    visiting_order = sorted(preds, key=lambda pred: -pred.score)

    matched: set[int] = set()  # rows of the annotations already matched
    pred_status: dict[int, str] = {}
    pairs: list[PairResult] = []

    # Rule 3: each prediction in turn.
    for pred in visiting_order:
        # 3.1 out of range.
        if pred.out_of_range:
            pred_status[pred.row] = STATUS_IGNORED
            continue
        # 3.2 below the operating point (a score equal to the threshold is kept).
        if operating and pred.score < config.score_threshold:
            pred_status[pred.row] = STATUS_BELOW_THRESHOLD
            continue
        # 3.3 candidates: same category (rule 5), not yet matched; ignored boxes included.
        candidates = [gt for gt in gts if gt.category == pred.category and gt.row not in matched]
        if not candidates:
            pred_status[pred.row] = STATUS_FP
            continue
        # 3.4 the best candidate; among equals the earliest in the order of the ground-truth
        # set (strict comparison while walking the candidates in that order).
        best = candidates[0]
        if use_iou:
            best_value = measures.iou(pred, best)
            for gt in candidates[1:]:
                value = measures.iou(pred, gt)
                if value > best_value:
                    best, best_value = gt, value
        else:
            best_value = measures.distance(pred, best)
            for gt in candidates[1:]:
                value = measures.distance(pred, gt)
                if value < best_value:
                    best, best_value = gt, value
        # 3.5 only the best candidate is tested, at the threshold of A.2.
        threshold = threshold_for(config, pred.category, threshold_index)
        passes = best_value >= threshold if use_iou else best_value <= threshold
        if not passes:
            pred_status[pred.row] = STATUS_FP
            continue
        # 3.6 absorbed by an ignored box, which stays unmatched.
        if best.ignored:
            pred_status[pred.row] = STATUS_IGNORED
            continue
        # 3.7 a true positive; IoU, distance and yaw error are recorded under either criterion.
        pred_status[pred.row] = STATUS_TP
        matched.add(best.row)
        pairs.append(
            PairResult(
                pred_row=pred.row,
                gt_row=best.row,
                iou_bev=measures.iou(pred, best),
                centre_error_m=measures.distance(pred, best),
                yaw_error_deg=yaw_error_deg(pred.placed, best.placed),
            )
        )

    # Rule 4: ground truth.
    gt_status: dict[int, str] = {}
    for gt in gts:
        if gt.row in matched:
            gt_status[gt.row] = STATUS_TP
        elif gt.ignored:
            gt_status[gt.row] = STATUS_IGNORED
        else:
            gt_status[gt.row] = STATUS_MISS

    return SampleMatch(pred_status=pred_status, gt_status=gt_status, pairs=tuple(pairs))
