# SPDX-License-Identifier: Apache-2.0
"""The ground-truth set of a fused output: ``docs/eval-spec.md`` Part A, A.4 "Ground-truth set".

Under ``gt_scope: connected_union`` the set of ``(X, fused)`` at a time ``t`` is ``X``'s
own annotations, then, for each connected agent in scene order, its observed annotations
that are not the same object as a box already in the set. This module holds the two
pieces that are not plain bookkeeping: the LiDAR sweeps that decide "observed", and the
union itself. Written from Part A alone; imports nothing from ``coperceive``.
"""

from __future__ import annotations

import bisect
import math
from collections.abc import Collection, Sequence

from tests.evalcheck.scenario import PoseSample, Vec3
from tests.reference_eval.cooperative import CoopBox, pair_boxes
from tests.reference_eval.geometry import BoxInterior, transform_points


class Sweep:
    """The LiDAR returns one agent recorded at one time, placed in the world (A.4 rule 2).

    The returns are kept sorted by world x so that a box only looks at the returns whose
    x lies within its reach; the test itself is :meth:`BoxInterior.contains`.
    """

    def __init__(self, pose: PoseSample, points_body: Sequence[Vec3]) -> None:
        self.points = sorted(transform_points(pose, points_body))
        self.xs = [point[0] for point in self.points]

    def hits(self, interior: BoxInterior) -> bool:
        """Whether at least one return of the sweep lies inside the box."""
        if interior.reach < math.inf:
            low = bisect.bisect_left(self.xs, interior.centre[0] - interior.reach)
            high = bisect.bisect_right(self.xs, interior.centre[0] + interior.reach)
        else:
            low, high = 0, len(self.points)
        return any(interior.contains(self.points[k]) for k in range(low, high))


def connected_agents(
    ego_id: str,
    ego_pose: PoseSample,
    poses_at_t: Sequence[tuple[str, PoseSample | None]],
    comm_range_m: float,
) -> list[str]:
    """A.4 rule 1: the agents ``Y ≠ X`` connected to ``X`` at ``t``, in scene order.

    ``poses_at_t`` lists every agent of the scene in scene order with its pose sample
    at exactly ``t`` (``None`` when it has none). ``Y`` is connected when it has one and
    the horizontal distance between the two pose translations is at most ``comm_range_m``.
    """
    ex, ey, _ez = ego_pose.translation
    connected: list[str] = []
    for agent_id, pose in poses_at_t:
        if agent_id == ego_id or pose is None:
            continue
        tx, ty, _tz = pose.translation
        if math.hypot(tx - ex, ty - ey) <= comm_range_m:
            connected.append(agent_id)
    return connected


def union_additions(
    own: Sequence[CoopBox],
    contributions: Sequence[tuple[str, Sequence[CoopBox]]],
    shared_track_ids: Collection[str],
) -> list[tuple[CoopBox, str]]:
    """A.4 rule 3: the boxes the connected agents add to ``X``'s own, in set order.

    ``own`` is ``X``'s annotations at ``t`` in row order: they come first and always
    stay. ``contributions`` holds, for each connected agent ``Y`` in scene order, its
    observed annotations at ``t`` in row order. For each ``Y`` in turn, the pairing of
    A.9 is run between the boxes already in the set (as the first agent, in set order)
    and ``Y``'s observed boxes (as the second); ``Y``'s unpaired boxes are appended in
    row order. Returns the appended boxes with the agent that added each one.
    """
    in_set: list[CoopBox] = list(own)
    added: list[tuple[CoopBox, str]] = []
    for agent_id, observed in contributions:
        if not observed:
            continue
        paired = {j for _i, j, _kind in pair_boxes(in_set, observed, shared_track_ids)}
        for j, box in enumerate(observed):
            if j not in paired:
                in_set.append(box)
                added.append((box, agent_id))
    return added
