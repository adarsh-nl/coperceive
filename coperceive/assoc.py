# SPDX-License-Identifier: Apache-2.0
"""Per-agent and cross-agent box queries.

A track id that is in ``scene.objects`` is the dataset's shared id. Pairing
boxes by world-centre distance is computed and is not ground truth, even when
the two boxes happen to carry the same id that ``shared_objects`` rejected.
"""

from __future__ import annotations

import enum
import math
from dataclasses import dataclass

from coperceive.diagnostics import annotation_center_world
from coperceive.model import Annotation, Scene


class AssociationKind(enum.Enum):
    SHARED_TRACK = "shared_track"
    COMPUTED = "computed"


@dataclass(frozen=True)
class CoAnnotation:
    """One track id used by two agents.

    ``ground_truth`` is true only when the id is a scene object. The same id
    on both agents with disagreeing categories is not ground truth.
    """

    track_id: str
    ground_truth: bool
    annotations: tuple[Annotation, ...]


@dataclass(frozen=True)
class Association:
    """One pair of boxes.

    ``ground_truth`` is true only for :attr:`AssociationKind.SHARED_TRACK`.
    A computed pair is a nearest-centre match within the gate. It is not
    written into ``scene.objects``.
    """

    left: Annotation
    right: Annotation
    kind: AssociationKind
    ground_truth: bool
    center_distance_m: float


def agent_annotations(
    scene: Scene,
    agent_id: str,
    *,
    time_us: int | None = None,
) -> tuple[Annotation, ...]:
    """Boxes from one agent, optionally at one timestamp."""
    scene.agent(agent_id)
    return tuple(
        ann
        for ann in scene.annotations
        if ann.agent_id == agent_id and (time_us is None or ann.time_us == time_us)
    )


def exclusive_annotations(
    scene: Scene,
    agent_id: str,
    *,
    time_us: int | None = None,
) -> tuple[Annotation, ...]:
    """Boxes whose track id is not used by any other agent in the same filter.

    A null track id is exclusive to the box that carries it. The filter is the
    whole scene, or one ``time_us`` when that is set.
    """
    scene.agent(agent_id)
    others: set[str] = set()
    for ann in scene.annotations:
        if ann.agent_id == agent_id:
            continue
        if time_us is not None and ann.time_us != time_us:
            continue
        if ann.track_id is not None:
            others.add(ann.track_id)
    return tuple(
        ann
        for ann in agent_annotations(scene, agent_id, time_us=time_us)
        if ann.track_id is None or ann.track_id not in others
    )


def co_annotations(
    scene: Scene,
    agent_a: str,
    agent_b: str,
    *,
    time_us: int | None = None,
) -> tuple[CoAnnotation, ...]:
    """Track ids that both agents use.

    ``ground_truth`` follows ``scene.objects``. An id both agents use that is
    not a scene object is still returned, with ``ground_truth`` false.
    """
    if agent_a == agent_b:
        raise ValueError("co_annotations needs two agents")
    left = _ids(agent_annotations(scene, agent_a, time_us=time_us))
    right = _ids(agent_annotations(scene, agent_b, time_us=time_us))
    shared_ids = {obj.track_id for obj in scene.objects}
    rows: list[CoAnnotation] = []
    for track_id in sorted(left & right):
        boxes = tuple(
            ann
            for ann in scene.annotations
            if ann.track_id == track_id
            and ann.agent_id in {agent_a, agent_b}
            and (time_us is None or ann.time_us == time_us)
        )
        rows.append(CoAnnotation(track_id, track_id in shared_ids, boxes))
    return tuple(rows)


def shared_track_annotations(scene: Scene, track_id: str) -> tuple[Annotation, ...]:
    """Boxes for a ground-truth shared id. Other ids raise ``KeyError``."""
    if track_id not in {obj.track_id for obj in scene.objects}:
        raise KeyError(track_id)
    return tuple(ann for ann in scene.annotations if ann.track_id == track_id)


def associate(
    scene: Scene,
    agent_a: str,
    agent_b: str,
    *,
    time_us: int,
    max_gap_us: int,
    max_center_m: float,
    size_fraction: float | None = None,
) -> tuple[Association, ...]:
    """Pair boxes at one timestamp.

    Shared scene-object ids are paired first and marked ground truth. The
    remaining boxes are paired greedily by world-centre distance, closest
    first, one box to one box. A pair is kept only when the distance is at
    most the gate. With ``size_fraction`` omitted the gate is ``max_center_m``.
    With ``size_fraction`` set, the gate for that pair is
    ``max(max_center_m, size_fraction * the longer box length)``. Those pairs
    are ``computed`` and not ground truth.
    """
    if agent_a == agent_b:
        raise ValueError("associate needs two agents")
    limit = _center_limit(max_center_m)
    fraction = _optional_fraction(size_fraction)
    left = agent_annotations(scene, agent_a, time_us=time_us)
    right = agent_annotations(scene, agent_b, time_us=time_us)
    shared_ids = {obj.track_id for obj in scene.objects}
    used_left: set[int] = set()
    used_right: set[int] = set()
    pairs: list[Association] = []
    for i, ann_a in enumerate(left):
        if ann_a.track_id is None or ann_a.track_id not in shared_ids:
            continue
        for j, ann_b in enumerate(right):
            if j in used_right or ann_b.track_id != ann_a.track_id:
                continue
            pairs.append(
                Association(
                    ann_a,
                    ann_b,
                    AssociationKind.SHARED_TRACK,
                    True,
                    _distance(scene, ann_a, ann_b, max_gap_us),
                )
            )
            used_left.add(i)
            used_right.add(j)
            break
    candidates: list[tuple[float, int, int]] = []
    for i, ann_a in enumerate(left):
        if i in used_left:
            continue
        for j, ann_b in enumerate(right):
            if j in used_right:
                continue
            distance = _distance(scene, ann_a, ann_b, max_gap_us)
            pair_limit = _pair_gate(limit, fraction, ann_a, ann_b)
            if distance <= pair_limit:
                candidates.append((distance, i, j))
    candidates.sort()
    for distance, i, j in candidates:
        if i in used_left or j in used_right:
            continue
        pairs.append(
            Association(
                left[i],
                right[j],
                AssociationKind.COMPUTED,
                False,
                distance,
            )
        )
        used_left.add(i)
        used_right.add(j)
    return tuple(pairs)


def _pair_gate(
    floor_m: float,
    size_fraction: float | None,
    left: Annotation,
    right: Annotation,
) -> float:
    if size_fraction is None:
        return floor_m
    longer = max(left.size_lwh[0], right.size_lwh[0])
    return max(floor_m, size_fraction * longer)


def _optional_fraction(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("size_fraction must be a finite number or None")
    number = float(value)
    if not math.isfinite(number) or number < 0.0:
        raise ValueError("size_fraction must be a finite number >= 0")
    return number


def _center_limit(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("max_center_m must be a finite number")
    limit = float(value)
    if not math.isfinite(limit) or limit < 0.0:
        raise ValueError("max_center_m must be a finite number >= 0")
    return limit


def _ids(annotations: tuple[Annotation, ...]) -> set[str]:
    return {ann.track_id for ann in annotations if ann.track_id is not None}


def _distance(scene: Scene, left: Annotation, right: Annotation, max_gap_us: int) -> float:
    a = annotation_center_world(scene, left, max_gap_us=max_gap_us)
    b = annotation_center_world(scene, right, max_gap_us=max_gap_us)
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2) ** 0.5
