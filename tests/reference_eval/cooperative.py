# SPDX-License-Identifier: Apache-2.0
"""Cooperative columns and counts of the reference evaluator: ``docs/eval-spec.md`` Part A, A.9.

Written from Part A alone; imports nothing from ``coperceive``.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass

from tests.evalcheck.scenario import (
    OTHER_KIND_COMPUTED,
    OTHER_KIND_SHARED_TRACK,
    STATUS_MISS,
    STATUS_TP,
    CooperativeResult,
    GtCooperativeResult,
)
from tests.reference_eval.geometry import PlacedBox, centre_distance_3d_m

MIN_GATE_M = 2.0
GATE_LENGTH_FACTOR = 0.5


@dataclass(frozen=True, eq=False)
class CoopBox:
    """One annotation as A.9 pairs it: its row, its track id and its world placement."""

    row: int
    track_id: str | None
    placed: PlacedBox


def pair_boxes(
    xs: Sequence[CoopBox], ys: Sequence[CoopBox], shared_track_ids: Collection[str]
) -> list[tuple[int, int, str]]:
    """A.9 "Other agents' boxes paired with g": pair ``X``'s boxes at ``t`` with ``Y``'s at ``t``.

    ``xs`` and ``ys`` are in row order. Returns ``(position in xs, position in ys, kind)``
    with ``kind`` ``shared_track`` (step 1) or ``computed`` (step 2).
    """
    used_x: set[int] = set()
    used_y: set[int] = set()
    pairs: list[tuple[int, int, str]] = []

    # Step 1, shared track: each box of X in row order whose track id is shared takes the
    # first unused box of Y in row order with the same track id.
    for i, a in enumerate(xs):
        if a.track_id is None or a.track_id not in shared_track_ids:
            continue
        for j, b in enumerate(ys):
            if j not in used_y and b.track_id == a.track_id:
                used_x.add(i)
                used_y.add(j)
                pairs.append((i, j, OTHER_KIND_SHARED_TRACK))
                break

    # Step 2, computed: every pair of leftover boxes within its gate, by 3D centre distance.
    gated: list[tuple[float, int, int]] = []
    for i, a in enumerate(xs):
        if i in used_x:
            continue
        for j, b in enumerate(ys):
            if j in used_y:
                continue
            distance = centre_distance_3d_m(a.placed, b.placed)
            gate = max(
                MIN_GATE_M, GATE_LENGTH_FACTOR * max(a.placed.size_lwh[0], b.placed.size_lwh[0])
            )
            if distance <= gate:
                gated.append((distance, i, j))
    # By distance, then the position of a, then the position of b; taken greedily.
    gated.sort()
    for _distance, i, j in gated:
        if i in used_x or j in used_y:
            continue
        used_x.add(i)
        used_y.add(j)
        pairs.append((i, j, OTHER_KIND_COMPUTED))

    return pairs


def other_kind_of(kinds: Collection[str]) -> str | None:
    """A.9 ``other_kind``: ``shared_track`` if any pair came from step 1, else ``computed``."""
    if OTHER_KIND_SHARED_TRACK in kinds:
        return OTHER_KIND_SHARED_TRACK
    if kinds:
        return OTHER_KIND_COMPUTED
    return None


def cooperative_counts(columns: Iterable[GtCooperativeResult]) -> CooperativeResult:
    """A.9 counts, over the boxes whose ``status_single`` and ``status_fused`` are both non-null."""
    recovered = 0
    lost_by_fusion = 0
    only_other_agent = 0
    double_miss = 0
    nobody = 0
    for column in columns:
        single = column.status_single
        fused = column.status_fused
        if single is None or fused is None:
            continue
        if single == STATUS_MISS and fused == STATUS_TP:
            recovered += 1
        if single == STATUS_TP and fused == STATUS_MISS:
            lost_by_fusion += 1
        if single == STATUS_MISS and column.other_agent_tp is True:
            only_other_agent += 1
        if single == STATUS_MISS and fused == STATUS_MISS:
            double_miss += 1
            if column.other_agent_tp is not True:
                nobody += 1
    denominator = recovered + double_miss
    return CooperativeResult(
        recovered=recovered,
        lost_by_fusion=lost_by_fusion,
        only_other_agent=only_other_agent,
        nobody=nobody,
        recovery_rate=recovered / denominator if denominator else None,
    )
