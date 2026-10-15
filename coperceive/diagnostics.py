# SPDX-License-Identifier: Apache-2.0
"""Cooperative checks that do not fuse detections."""

from __future__ import annotations

import math
from dataclasses import dataclass

from coperceive.model import Annotation, BoxFrame, Scene, SharedObject
from coperceive.query import AgentTimeHit, TimeMode, query_agents
from coperceive.transform import lookup_pose


@dataclass(frozen=True)
class AlignmentRow:
    dst_agent: str
    src_agent: str
    query_us: int
    dst_dt_us: int
    src_dt_us: int


def shared_objects(
    annotations: tuple[Annotation, ...] | list[Annotation],
) -> tuple[SharedObject, ...]:
    """Promote a track id only when at least two agents use it and agree on category.

    An annotation with no track id is not a candidate. One agent using an id,
    or two agents using it under different categories, does not produce a row.
    Geometry stays on the boxes. On Griffin-25m Town03-000 the shared ids that
    pass this rule also agree in world coordinates to 5.7e-14 m; see
    ``docs/design/griffin-verification.md``.
    """
    by_track: dict[str, dict[str, set[str]]] = {}
    for ann in annotations:
        if ann.track_id is None:
            continue
        per_agent = by_track.setdefault(ann.track_id, {})
        per_agent.setdefault(ann.agent_id, set()).add(ann.category)
    promoted: list[SharedObject] = []
    for track_id in sorted(by_track):
        per_agent = by_track[track_id]
        if len(per_agent) < 2:
            continue
        categories: set[str] = set()
        for cats in per_agent.values():
            categories |= cats
        if len(categories) != 1:
            continue
        promoted.append(SharedObject(track_id=track_id, category=next(iter(categories))))
    return tuple(promoted)


def require_shared_object_table(scene: Scene) -> None:
    """Raise when ``scene.objects`` is not exactly :func:`shared_objects`."""
    expected = {(obj.track_id, obj.category) for obj in shared_objects(scene.annotations)}
    actual = [(obj.track_id, obj.category) for obj in scene.objects]
    if len(actual) != len(set(actual)) or set(actual) != expected:
        raise ValueError(
            "scene objects must be exactly the track ids shared by at least two agents "
            "that agree on one category"
        )


def annotation_center_world(
    scene: Scene,
    ann: Annotation,
    *,
    max_gap_us: int,
) -> tuple[float, float, float]:
    """Map a box center into the scene world frame. Does not extrapolate."""
    if ann.frame is BoxFrame.WORLD:
        return ann.center
    pose = lookup_pose(scene.agent(ann.agent_id), ann.time_us, max_gap_us=max_gap_us)
    return pose.apply(ann.center)


def track_center_spread_m(
    scene: Scene,
    track_id: str,
    *,
    max_gap_us: int,
) -> float | None:
    """Maximum world-frame distance between co-temporal boxes with this id.

    Returns ``None`` when no timestamp has the id on two agents. Two boxes
    from the same agent at one timestamp raise ``ValueError``.
    """
    groups: dict[int, dict[str, tuple[float, float, float]]] = {}
    seen = False
    for ann in scene.annotations:
        if ann.track_id != track_id:
            continue
        seen = True
        point = annotation_center_world(scene, ann, max_gap_us=max_gap_us)
        bucket = groups.setdefault(ann.time_us, {})
        if ann.agent_id in bucket:
            raise ValueError(f"track {track_id} is repeated on {ann.agent_id} at {ann.time_us}")
        bucket[ann.agent_id] = point
    if not seen:
        raise KeyError(track_id)
    worst: float | None = None
    for bucket in groups.values():
        points = list(bucket.values())
        if len(points) < 2:
            continue
        for i in range(len(points)):
            for j in range(i + 1, len(points)):
                distance = _distance(points[i], points[j])
                if worst is None or distance > worst:
                    worst = distance
    return worst


def alignment(
    scene: Scene,
    query_us: int,
    *,
    tolerance_us: int,
    mode: TimeMode = TimeMode.NEAREST,
) -> tuple[AlignmentRow, ...]:
    """Per-agent ``dt_us`` for every ordered pair where both agents matched.

    An agent that misses the tolerance, or that has no sample on that side of
    the query, is left out of every pair. Call :func:`query_agents` for the
    per-agent miss itself.
    """
    hits = {
        hit.agent_id: hit for hit in query_agents(scene, query_us, mode, tolerance_us=tolerance_us)
    }
    rows: list[AlignmentRow] = []
    for dst in scene.agents:
        for src in scene.agents:
            if dst.agent_id == src.agent_id:
                continue
            dst_hit: AgentTimeHit = hits[dst.agent_id]
            src_hit: AgentTimeHit = hits[src.agent_id]
            dst_dt = dst_hit.dt_us
            src_dt = src_hit.dt_us
            if not dst_hit.matched or not src_hit.matched or dst_dt is None or src_dt is None:
                continue
            rows.append(
                AlignmentRow(
                    dst_agent=dst.agent_id,
                    src_agent=src.agent_id,
                    query_us=query_us,
                    dst_dt_us=dst_dt,
                    src_dt_us=src_dt,
                )
            )
    return tuple(rows)


def _distance(
    a: tuple[float, float, float],
    b: tuple[float, float, float],
) -> float:
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2)
