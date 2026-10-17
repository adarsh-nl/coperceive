# SPDX-License-Identifier: Apache-2.0
"""Greedy per-class matching of predictions to ground truth (spec §Matching).

:func:`match_sample` is rules 1–6 of §Matching for one agent, one output, one
sample time, every category at once: categories never cross, so the per-category
loop of the spec is the same as one pass with the candidate boxes filtered by
category. Rule 7, the AP pass, is the same function with
``apply_score_threshold=False`` and the threshold index of the AP threshold.

Inputs are already in the world frame and already carry their horizontal range
from the agent body origin; placing boxes and measuring ranges is the scene
evaluator's job. Statuses are the §Matching words: ``tp``, ``fp``, ``ignored``
and ``below_threshold`` for predictions; ``tp``, ``miss`` and ``ignored`` for
ground truth.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType

from coperceive.evaluation.config import EvalConfig, threshold_for
from coperceive.evaluation.geometry import (
    WorldBox,
    bev_iou,
    centre_distance_m,
    yaw_error_deg,
)

TP = "tp"
FP = "fp"
MISS = "miss"
IGNORED = "ignored"
BELOW_THRESHOLD = "below_threshold"


@dataclass(frozen=True)
class GtBox:
    """One ground-truth box of the agent at the sample time, in the world frame.

    ``row`` is its row index in ``annotations.parquet``; ``range_m`` is the
    horizontal distance from the agent body origin at that time.
    """

    row: int
    category: str
    track_id: str | None
    visibility: float | None
    world: WorldBox
    range_m: float


@dataclass(frozen=True)
class PredBox:
    """One prediction of the output at the sample time, in the world frame.

    ``row`` is its row index in ``boxes.parquet``; ``range_m`` as for :class:`GtBox`.
    """

    row: int
    category: str
    score: float
    track_id: str | None
    world: WorldBox
    range_m: float


@dataclass(frozen=True)
class Pairing:
    """A true positive: the prediction row, its ground-truth row and the pair's errors."""

    pred_row: int
    gt_row: int
    iou_bev: float
    centre_error_m: float
    yaw_error_deg: float


@dataclass(frozen=True)
class MatchResult:
    """Statuses keyed by row, and the true-positive pairs in match (score) order."""

    pred_status: Mapping[int, str]
    gt_status: Mapping[int, str]
    pairs: tuple[Pairing, ...]


def _gt_ignored(box: GtBox, config: EvalConfig) -> bool:
    """Rule 1 for ground truth: low visibility (a null visibility is kept) or out of range."""
    below = config.ignore_gt_visibility_below
    if below is not None and box.visibility is not None and box.visibility < below:
        return True
    return config.max_range_m is not None and box.range_m > config.max_range_m


def _pred_ignored(box: PredBox, config: EvalConfig) -> bool:
    """Rule 1 for predictions: out of range."""
    return config.max_range_m is not None and box.range_m > config.max_range_m


def _better(candidate: float, best: float, criterion: str) -> bool:
    """Strictly better under the criterion, so ties keep the earlier ground-truth box."""
    if criterion == "centre":
        return candidate < best
    return candidate > best


def _passes(value: float, threshold: float, criterion: str) -> bool:
    if criterion == "centre":
        return value <= threshold
    return value >= threshold


def _measure(pred: WorldBox, gt: WorldBox, criterion: str) -> float:
    if criterion == "centre":
        return centre_distance_m(pred, gt)
    return bev_iou(pred, gt)


def match_sample(
    gt: Sequence[GtBox],
    preds: Sequence[PredBox],
    config: EvalConfig,
    *,
    threshold_index: int,
    apply_score_threshold: bool,
) -> MatchResult:
    """Match one sample of one output under rules 1–6 of §Matching.

    ``threshold_index`` picks the entry of ``thresholds`` (and of each class's
    ``class_thresholds``) the pairs are judged at. With ``apply_score_threshold``
    a prediction with ``score < score_threshold`` is ``below_threshold`` and takes
    no box; without it every prediction competes (rule 7, the AP pass). Range
    ignoring (rule 1) applies in both passes and is decided before the score.

    Predictions are taken by score descending, equal scores in sequence order,
    so the same input always gives the same result. Each prediction considers the
    unmatched ground-truth boxes of its category, ignored ones included, and takes
    the best one: largest BEV IoU or smallest centre distance, the earlier box on
    a tie. Passing the threshold against a kept box is a ``tp``; against an
    ignored box the prediction is ``ignored`` and the box stays unmatched;
    anything else, no candidate included, is an ``fp``. A kept box nobody took
    is a ``miss``.
    """
    criterion = config.criterion
    gt_ignored = [_gt_ignored(box, config) for box in gt]
    matched = [False] * len(gt)
    pred_status: dict[int, str] = {}
    pairs: list[Pairing] = []

    # Rule 2: score descending, stable, so equal scores keep file order.
    for pred in sorted(preds, key=lambda p: -p.score):
        if _pred_ignored(pred, config):
            pred_status[pred.row] = IGNORED
            continue
        if apply_score_threshold and pred.score < config.score_threshold:
            pred_status[pred.row] = BELOW_THRESHOLD
            continue
        best_index: int | None = None
        best_value = 0.0
        for index, box in enumerate(gt):
            if matched[index] or box.category != pred.category:
                continue
            value = _measure(pred.world, box.world, criterion)
            if best_index is None or _better(value, best_value, criterion):
                best_index, best_value = index, value
        if best_index is None:
            pred_status[pred.row] = FP
            continue
        threshold = threshold_for(config, pred.category, threshold_index)
        if not _passes(best_value, threshold, criterion):
            pred_status[pred.row] = FP
            continue
        if gt_ignored[best_index]:
            pred_status[pred.row] = IGNORED
            continue
        matched[best_index] = True
        pred_status[pred.row] = TP
        best = gt[best_index]
        pairs.append(
            Pairing(
                pred_row=pred.row,
                gt_row=best.row,
                iou_bev=bev_iou(pred.world, best.world),
                centre_error_m=centre_distance_m(pred.world, best.world),
                yaw_error_deg=yaw_error_deg(pred.world, best.world),
            )
        )

    gt_status: dict[int, str] = {}
    for index, box in enumerate(gt):
        if matched[index]:
            gt_status[box.row] = TP
        elif gt_ignored[index]:
            gt_status[box.row] = IGNORED
        else:
            gt_status[box.row] = MISS

    return MatchResult(
        pred_status=MappingProxyType(pred_status),
        gt_status=MappingProxyType(gt_status),
        pairs=tuple(pairs),
    )
