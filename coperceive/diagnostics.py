# SPDX-License-Identifier: Apache-2.0
"""Cooperative checks that do not fuse detections."""

from __future__ import annotations

from dataclasses import dataclass

from coperceive.model import Annotation, Scene, SharedObject
from coperceive.query import TimeMode, query_time


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
    """Promote a track id only when at least two agents use it and agree on category."""
    by_track: dict[str, dict[str, set[str]]] = {}
    for ann in annotations:
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


def alignment(
    scene: Scene,
    query_us: int,
    *,
    tolerance_us: int,
    mode: TimeMode = TimeMode.NEAREST,
) -> tuple[AlignmentRow, ...]:
    """Per-agent ``dt_us`` of a discrete pose query, for every ordered pair."""
    hits = {
        agent.agent_id: query_time(
            scene.pose_times(agent.agent_id),
            query_us,
            mode,
            tolerance_us=tolerance_us,
        )
        for agent in scene.agents
    }
    rows: list[AlignmentRow] = []
    for dst in scene.agents:
        for src in scene.agents:
            if dst.agent_id == src.agent_id:
                continue
            rows.append(
                AlignmentRow(
                    dst_agent=dst.agent_id,
                    src_agent=src.agent_id,
                    query_us=query_us,
                    dst_dt_us=hits[dst.agent_id].dt_us,
                    src_dt_us=hits[src.agent_id].dt_us,
                )
            )
    return tuple(rows)
