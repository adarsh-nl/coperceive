# SPDX-License-Identifier: Apache-2.0
"""Numbers derived from match results (spec §Metrics and §Cooperative views).

Everything here is arithmetic over statuses and pairs the matcher produced:
average precision of one category at one threshold, the per-sample and total
counts with their precision and recall, the error summary over true positives,
and the cooperative counts of one agent. Nothing reads a scene or a file; the
scene evaluator feeds these and places the results in ``metrics.json``.

Absent values are ``None`` so that ``metrics.json`` carries ``null`` and never
``NaN``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from statistics import fmean, median

from coperceive.evaluation.matching import FP, IGNORED, MISS, TP, MatchResult, Pairing

Size = tuple[float, float, float]


def average_precision(records: Sequence[tuple[float, str]], n_gt: int) -> float | None:
    """AP of one category at one threshold, steps 1–5 of §Metrics.

    ``records`` are ``(score, status)`` of every prediction of the category in
    file order, with the status of the AP pass (``score_threshold`` ignored).
    They are sorted by score descending with a stable sort, so equal scores keep
    file order. Ignored predictions are skipped; ``tp`` and ``fp`` accumulate.
    The precision envelope is made monotone from the right and AP is the area
    under the recall/envelope steps. ``None`` when the category has no ground
    truth; ``0.0`` when it has ground truth and no prediction.
    """
    if n_gt <= 0:
        return None
    ordered = sorted(records, key=lambda record: -record[0])
    recalls: list[float] = []
    precisions: list[float] = []
    tp = 0
    fp = 0
    for _score, status in ordered:
        if status == TP:
            tp += 1
        elif status == FP:
            fp += 1
        else:
            continue
        recalls.append(tp / n_gt)
        precisions.append(tp / (tp + fp))
    envelope = [0.0] * len(precisions)
    best = 0.0
    for index in range(len(precisions) - 1, -1, -1):
        best = max(best, precisions[index])
        envelope[index] = best
    ap = 0.0
    previous = 0.0
    for recall, precision in zip(recalls, envelope, strict=True):
        ap += (recall - previous) * precision
        previous = recall
    return ap


@dataclass(frozen=True)
class Counts:
    """``tp``, ``fp``, ``miss`` and ``ignored_gt`` of one sample or of a whole output."""

    tp: int
    fp: int
    miss: int
    ignored_gt: int

    @property
    def precision(self) -> float | None:
        """``tp / (tp + fp)``; ``None`` when there is no prediction counted."""
        total = self.tp + self.fp
        return None if total == 0 else self.tp / total

    @property
    def recall(self) -> float | None:
        """``tp / (tp + miss)``; ``None`` when there is no kept ground truth."""
        total = self.tp + self.miss
        return None if total == 0 else self.tp / total


def counts_of(result: MatchResult) -> Counts:
    """The operating-point counts of one matched sample.

    ``tp`` and ``fp`` are counted over the prediction statuses, ``miss`` and
    ``ignored_gt`` over the ground-truth statuses; ``below_threshold`` and
    ``ignored`` predictions count nowhere.
    """
    pred_statuses = tuple(result.pred_status.values())
    gt_statuses = tuple(result.gt_status.values())
    return Counts(
        tp=pred_statuses.count(TP),
        fp=pred_statuses.count(FP),
        miss=gt_statuses.count(MISS),
        ignored_gt=gt_statuses.count(IGNORED),
    )


def tp_errors(
    pairs: Sequence[Pairing],
    sizes: Mapping[int, tuple[Size, Size]],
) -> dict[str, object]:
    """The §Metrics ``tp_errors`` object over the true positives at the operating point.

    ``sizes`` maps a pair's ``pred_row`` to ``(pred_size, gt_size)``; each size is
    ``(length, width, height)``. Every leaf is ``None`` when there is no pair.
    """
    if not pairs:
        return {
            "centre_m": {"mean": None, "median": None},
            "yaw_deg": {"mean_abs": None},
            "size_m": {"length": None, "width": None, "height": None},
            "iou_bev": {"mean": None},
        }
    centre = [pair.centre_error_m for pair in pairs]
    yaw = [abs(pair.yaw_error_deg) for pair in pairs]
    iou = [pair.iou_bev for pair in pairs]
    size_errors: list[list[float]] = [[], [], []]
    for pair in pairs:
        pred_size, gt_size = sizes[pair.pred_row]
        for axis, (p, g) in enumerate(zip(pred_size, gt_size, strict=True)):
            size_errors[axis].append(abs(p - g))
    return {
        "centre_m": {"mean": fmean(centre), "median": median(centre)},
        "yaw_deg": {"mean_abs": fmean(yaw)},
        "size_m": {
            "length": fmean(size_errors[0]),
            "width": fmean(size_errors[1]),
            "height": fmean(size_errors[2]),
        },
        "iou_bev": {"mean": fmean(iou)},
    }


@dataclass(frozen=True)
class GtCooperative:
    """The §Cooperative views columns of one ground-truth box."""

    status_single: str | None
    status_fused: str | None
    seen_by_other_gt: bool | None
    other_kind: str | None
    other_agent_tp: bool | None


def cooperative_counts(rows: Sequence[GtCooperative]) -> dict[str, object]:
    """The §Cooperative views counts and ``recovery_rate`` of one agent.

    Only boxes with both a ``status_single`` and a ``status_fused`` enter, as the
    spec says; an agent with a single output alone gets zeros and a ``null``
    rate. ``recovery_rate`` is ``recovered / (recovered + miss under both)``,
    ``None`` when that denominator is 0.
    """
    recovered = 0
    lost_by_fusion = 0
    only_other_agent = 0
    nobody = 0
    double_miss = 0
    for row in rows:
        single, fused = row.status_single, row.status_fused
        if single is None or fused is None:
            continue
        if single == MISS and fused == TP:
            recovered += 1
        elif single == TP and fused == MISS:
            lost_by_fusion += 1
        if single == MISS and row.other_agent_tp is True:
            only_other_agent += 1
        if single == MISS and fused == MISS:
            double_miss += 1
            if row.other_agent_tp is not True:
                nobody += 1
    denominator = recovered + double_miss
    return {
        "recovered": recovered,
        "lost_by_fusion": lost_by_fusion,
        "only_other_agent": only_other_agent,
        "nobody": nobody,
        "recovery_rate": None if denominator == 0 else recovered / denominator,
    }
