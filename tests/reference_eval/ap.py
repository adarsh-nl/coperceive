# SPDX-License-Identifier: Apache-2.0
"""Average precision of the reference evaluator: ``docs/eval-spec.md`` Part A, A.5 and A.8.

Written from Part A alone; imports nothing from ``coperceive``.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence

from tests.evalcheck.scenario import STATUS_FP, STATUS_TP

# One prediction row of the list of A.5 step 3: (score, AP-pass status, sample time).
ApRecord = tuple[float, str, int]


def average_precision(records: Sequence[tuple[float, str]], n_gt: int) -> float | None:
    """A.5 steps 2 to 6 on ``(score, status)`` records, under ``ranking_scope: pooled``.

    ``records`` must already be in the tie order of step 3: row order for one scene,
    scene order then row order for a pool (A.8). They are sorted once by score, highest
    first; the sort is stable, so equal scores keep the order they were given in.
    ``n_gt`` is ``n_gt(c)`` of step 1.
    """
    ordered = sorted(records, key=lambda record: -record[0])
    return walk_average_precision([status for _score, status in ordered], n_gt)


def per_frame_order(records: Sequence[ApRecord]) -> list[ApRecord]:
    """A.5 step 3 under ``ranking_scope: per_frame``, for the rows of one ``(scene, output)``.

    ``records`` are in row order. They are put in sample-time order, earliest first,
    and within one time by score, highest first; the sort is stable, so equal scores
    stay in row order.
    """
    return sorted(records, key=lambda record: (record[2], -record[0]))


def walk_average_precision(statuses: Sequence[str], n_gt: int) -> float | None:
    """A.5 steps 2 and 4 to 6 on statuses already in walking order (no sort here).

    This is the walk of ``ranking_scope: per_frame``, where the order is the per-frame
    order of each ``(scene, output)`` and the pool concatenates those lists (A.8).
    """
    # Step 2: no ground truth to find, no AP.
    if n_gt == 0:
        return None

    # Step 4: walk the list; a point after each tp or fp, anything else is skipped.
    recalls: list[float] = []
    precisions: list[float] = []
    tp = 0
    fp = 0
    for status in statuses:
        if status == STATUS_TP:
            tp += 1
        elif status == STATUS_FP:
            fp += 1
        else:
            continue
        recalls.append(tp / n_gt)
        precisions.append(tp / (tp + fp))

    # Step 5: envelope p'_k = max(p_j for j >= k), filled from the last point backwards.
    envelope = list(precisions)
    for k in range(len(envelope) - 2, -1, -1):
        envelope[k] = max(envelope[k], envelope[k + 1])

    # Step 6: AP = sum_k (r_k - r_{k-1}) p'_k with r_0 = 0; an empty sum is 0.
    terms: list[float] = []
    previous_recall = 0.0
    for recall, precision in zip(recalls, envelope, strict=True):
        terms.append((recall - previous_recall) * precision)
        previous_recall = recall
    return math.fsum(terms)


def mean_of_non_null(values: Iterable[float | None]) -> float | None:
    """A.5 / A.8: the mean of the non-null values; null when there is none."""
    kept = [value for value in values if value is not None]
    if not kept:
        return None
    return math.fsum(kept) / len(kept)
