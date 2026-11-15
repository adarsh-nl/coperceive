# SPDX-License-Identifier: Apache-2.0
"""Average precision of the reference evaluator: ``docs/eval-spec.md`` Part A, section A.5.

Written from Part A alone; imports nothing from ``coperceive``.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence

from tests.evalcheck.scenario import STATUS_FP, STATUS_TP


def average_precision(records: Sequence[tuple[float, str]], n_gt: int) -> float | None:
    """A.5 steps 2 to 6 on ``(score, status)`` records.

    ``records`` must already be in the tie order of step 3: row order for one scene,
    scene order then row order for a pool (A.8). ``n_gt`` is ``n_gt(c)`` of step 1.
    """
    # Step 2: no ground truth to find, no AP.
    if n_gt == 0:
        return None

    # Step 3: by score, highest first. The sort is stable, so equal scores keep the
    # order they were given in.
    ordered = sorted(records, key=lambda record: -record[0])

    # Step 4: walk the list; a point after each tp or fp, anything else is skipped.
    recalls: list[float] = []
    precisions: list[float] = []
    tp = 0
    fp = 0
    for _score, status in ordered:
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
