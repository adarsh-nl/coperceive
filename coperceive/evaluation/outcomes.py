# SPDX-License-Identifier: Apache-2.0
"""Per-object outcomes shared by ``eval compare`` and ``eval failures``.

One record is one ground-truth box or one prediction of one output
(``agent_id``, ``source``) at one time. A ground-truth key does not include
the run, so the same scene yields the same key every time:

``gt:<scene_id>:<agent_id>:<time_us>:<annotation row>``

A false positive's key includes the run, because that row number is the
run's own ``boxes.parquet`` row:

``fp:<run_id>:<scene_id>:<agent_id>:<source>:<time_us>:<prediction row>``

Distance is the horizontal distance from the reference agent to the box
centre. The reference agent is the first agent in the scene, which is the
rule ``CooperativeDataset`` uses when the caller does not name an agent.
Occlusion comes from ``visibility`` and is ``unknown`` when that value is
null. The agent count is how many agents annotate the object at that time,
including the annotating agent. Pairing uses the cooperative gate (a shared
track, or a computed pair within 2 m). ``eval run`` writes ``outcomes.json``
next to ``metrics.json`` so ``--from-scores`` can skip reopening the scene.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from coperceive.assoc import associate
from coperceive.errors import TimeQueryError
from coperceive.evaluation.evaluate import (
    ASSOCIATE_MAX_CENTER_M,
    ASSOCIATE_SIZE_FRACTION,
    KIND_GT,
    KIND_PREDICTION,
    MATCH_SCHEMA,
    EvaluationError,
    read_matches,
    read_metrics,
    scene_file,
    threshold_count,
)
from coperceive.evaluation.geometry import WorldBox, horizontal_range_m
from coperceive.model import Scene
from coperceive.se3 import SE3
from coperceive.transform import lookup_pose

OUTCOMES_FILE = "outcomes.json"
OUTCOMES_FORMAT = "coperceive-outcomes"
OUTCOMES_VERSION = 1

# Default edges of a visibility fraction in [0, 1]. These names are used only
# for this default. Other edges get numeric labels.
DEFAULT_VISIBILITY_EDGES: tuple[float, ...] = (0.2, 0.6)
DEFAULT_DISTANCE_EDGES: tuple[float, ...] = (0.0, 30.0, 50.0)
_DEFAULT_OCCLUSION_NAMES = ("heavy", "partial", "clear")


def reference_agent_id(scene: Scene) -> str:
    """The first agent in the scene."""
    if not scene.agents:
        raise EvaluationError(f"scene {scene.scene_id!r} has no agents")
    return scene.agents[0].agent_id


def distance_bin(distance_m: float | None, edges: Sequence[float]) -> str:
    """Label for a horizontal distance. ``None`` and anything below the first edge are ``unknown``.

    A value in ``[edges[i], edges[i+1])`` is ``"<lo>-<hi>m"``. A value at or
    beyond the last edge is ``"<last>m+"``.
    """
    if distance_m is None:
        return "unknown"
    points = tuple(float(edge) for edge in edges)
    if not points or distance_m < points[0]:
        return "unknown"
    for index in range(len(points) - 1):
        lo, hi = points[index], points[index + 1]
        if distance_m < hi:
            return f"{_edge(lo)}-{_edge(hi)}m"
    return f"{_edge(points[-1])}m+"


def distance_bin_names(edges: Sequence[float]) -> tuple[str, ...]:
    """Bin labels for ``edges``, in order. ``unknown`` is not included."""
    points = tuple(float(edge) for edge in edges)
    names = [f"{_edge(points[i])}-{_edge(points[i + 1])}m" for i in range(len(points) - 1)]
    if points:
        names.append(f"{_edge(points[-1])}m+")
    return tuple(names)


def _edge(value: float) -> str:
    if value == int(value):
        return str(int(value))
    return format(value, "g")


def occlusion_bucket(
    visibility: float | None, edges: Sequence[float] = DEFAULT_VISIBILITY_EDGES
) -> str:
    """Visibility bucket. A null visibility is ``unknown`` and is never given a name.

    Default edges ``(0.2, 0.6)`` on a fraction in ``[0, 1]`` are ``heavy``,
    ``partial`` and ``clear`` (half-open, and 1 belongs to ``clear``). Other
    edges use numeric labels. A number outside ``[0, 1]`` is
    ``visibility:<value>`` so a dataset that does not store a fraction is not
    described as heavily occluded.
    """
    if visibility is None:
        return "unknown"
    if visibility < 0.0 or visibility > 1.0:
        return f"visibility:{visibility:.3f}"
    points = (0.0, *tuple(float(edge) for edge in edges), 1.0)
    named = tuple(float(edge) for edge in edges) == DEFAULT_VISIBILITY_EDGES
    for index in range(len(points) - 1):
        hi = points[index + 1]
        last = index == len(points) - 2
        if visibility < hi or (last and visibility <= hi):
            if named and index < len(_DEFAULT_OCCLUSION_NAMES):
                return _DEFAULT_OCCLUSION_NAMES[index]
            return f"{points[index]:.3f}-{hi:.3f}"
    return "unknown"


def occlusion_bin_names(edges: Sequence[float] = DEFAULT_VISIBILITY_EDGES) -> tuple[str, ...]:
    """Bucket names for ``edges``. The caller adds ``unknown`` when it appears."""
    if tuple(float(edge) for edge in edges) == DEFAULT_VISIBILITY_EDGES:
        return _DEFAULT_OCCLUSION_NAMES
    points = (0.0, *tuple(float(edge) for edge in edges), 1.0)
    return tuple(f"{points[index]:.3f}-{points[index + 1]:.3f}" for index in range(len(points) - 1))


def gt_key(scene_id: str, agent_id: str, time_us: int, row: int) -> str:
    return f"gt:{scene_id}:{agent_id}:{time_us}:{row}"


def fp_key(run_id: str, scene_id: str, agent_id: str, source: str, time_us: int, row: int) -> str:
    return f"fp:{run_id}:{scene_id}:{agent_id}:{source}:{time_us}:{row}"


@dataclass(frozen=True)
class OutcomeRecord:
    """One match row plus the attributes a breakdown filters on."""

    kind: str
    key: str
    scene_id: str
    run_id: str
    agent_id: str
    source: str
    time_us: int
    row: int
    track_id: str | None
    category: str
    score: float | None
    distance_m: float | None
    visibility: float | None
    agent_count: int | None
    agent_bin: str
    status: str
    status_at: tuple[str, ...]
    pair_id: int | None
    iou_bev: float | None
    centre_error_m: float | None
    yaw_error_deg: float | None
    status_single: str | None
    status_fused: str | None
    center: tuple[float, float, float]
    size_lwh: tuple[float, float, float]
    rotation: tuple[float, float, float, float]

    def to_json(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "key": self.key,
            "scene_id": self.scene_id,
            "run_id": self.run_id,
            "agent_id": self.agent_id,
            "source": self.source,
            "time_us": self.time_us,
            "row": self.row,
            "track_id": self.track_id,
            "category": self.category,
            "score": _round(self.score),
            "distance_m": _round(self.distance_m),
            "visibility": _round(self.visibility),
            "agent_count": self.agent_count,
            "agent_bin": self.agent_bin,
            "status": self.status,
            "status_at": list(self.status_at),
            "pair_id": self.pair_id,
            "iou_bev": _round(self.iou_bev),
            "centre_error_m": _round(self.centre_error_m),
            "yaw_error_deg": _round(self.yaw_error_deg),
            "status_single": self.status_single,
            "status_fused": self.status_fused,
            "center": [round(float(value), 6) for value in self.center],
            "size_lwh": [round(float(value), 6) for value in self.size_lwh],
            "rotation": [round(float(value), 6) for value in self.rotation],
        }


def _round(value: float | None) -> float | None:
    if value is None:
        return None
    rounded = round(float(value), 6)
    return 0.0 if rounded == 0.0 else rounded


def records_from_rows(
    scene: Scene | None,
    rows: Sequence[Mapping[str, object]],
    *,
    run_id: str,
    scene_id: str,
) -> tuple[OutcomeRecord, ...]:
    """Records for one scene. Without ``scene``, distance is unknown.

    Agent count without a scene is 1 when ``seen_by_other_gt`` is false,
    ``2+`` when it is true (the match table stores the boolean, not the
    count) and ``unknown`` when the column is null.
    """
    reference = reference_agent_id(scene) if scene is not None else ""
    bins = _agent_bins(scene) if scene is not None else {}
    out: list[OutcomeRecord] = []
    for item in rows:
        kind = str(item.get("kind"))
        if kind not in (KIND_GT, KIND_PREDICTION):
            continue
        time_us = _as_int(item.get("time_us"))
        index = _as_int(item.get("row"))
        if time_us is None or index is None:
            continue
        agent_id = str(item.get("agent_id"))
        source = str(item.get("source"))
        center = (
            _as_float(item.get("cx")) or 0.0,
            _as_float(item.get("cy")) or 0.0,
            _as_float(item.get("cz")) or 0.0,
        )
        distance = _distance(scene, reference, time_us, center) if scene is not None else None
        if kind == KIND_GT and scene is not None:
            agent_count, agent_bin = bins.get(index, (1, "1"))
            visibility = _as_float(item.get("visibility"))
        elif kind == KIND_GT:
            visibility = _as_float(item.get("visibility"))
            agent_count, agent_bin = _agent_from_flag(item.get("seen_by_other_gt"))
        else:
            visibility = None
            agent_count, agent_bin = None, "unknown"
        status = str(item.get("status") or "")
        if kind == KIND_GT:
            key = gt_key(scene_id, agent_id, time_us, index)
        else:
            prefix = "fp" if status == "fp" else "pred"
            key = f"{prefix}:{run_id}:{scene_id}:{agent_id}:{source}:{time_us}:{index}"
        out.append(
            OutcomeRecord(
                kind=kind,
                key=key,
                scene_id=scene_id,
                run_id=run_id,
                agent_id=agent_id,
                source=source,
                time_us=time_us,
                row=index,
                track_id=_as_str(item.get("track_id")),
                category=str(item.get("category") or ""),
                score=_as_float(item.get("score")),
                distance_m=distance,
                visibility=visibility,
                agent_count=agent_count,
                agent_bin=agent_bin,
                status=status,
                status_at=_status_at(item),
                pair_id=_as_int(item.get("pair_id")),
                iou_bev=_as_float(item.get("iou_bev")),
                centre_error_m=_as_float(item.get("centre_error_m")),
                yaw_error_deg=_as_float(item.get("yaw_error_deg")),
                status_single=_as_str(item.get("status_single")),
                status_fused=_as_str(item.get("status_fused")),
                center=center,
                size_lwh=(
                    _as_float(item.get("length")) or 0.0,
                    _as_float(item.get("width")) or 0.0,
                    _as_float(item.get("height")) or 0.0,
                ),
                rotation=(
                    _component(item.get("qw"), 1.0),
                    _component(item.get("qx"), 0.0),
                    _component(item.get("qy"), 0.0),
                    _component(item.get("qz"), 0.0),
                ),
            )
        )
    return tuple(out)


def _agent_from_flag(value: object) -> tuple[int | None, str]:
    seen = _as_bool(value)
    if seen is True:
        return None, "2+"
    if seen is False:
        return 1, "1"
    return None, "unknown"


def _agent_bins(scene: Scene) -> dict[int, tuple[int | None, str]]:
    """Annotation row → ``(count, bin)``. The count includes the annotating agent."""
    found: dict[int, tuple[int | None, str]] = {}
    for row, ann in enumerate(scene.annotations):
        count = 1
        for other in scene.agents:
            if other.agent_id == ann.agent_id:
                continue
            if not _other_has_time(scene, other.agent_id, ann.time_us):
                continue
            pairs = associate(
                scene,
                ann.agent_id,
                other.agent_id,
                time_us=ann.time_us,
                max_gap_us=0,
                max_center_m=ASSOCIATE_MAX_CENTER_M,
                size_fraction=ASSOCIATE_SIZE_FRACTION,
            )
            if any(pair.left is ann or pair.right is ann for pair in pairs):
                count += 1
        found[row] = (count, str(count))
    return found


def _other_has_time(scene: Scene, agent_id: str, time_us: int) -> bool:
    return any(ann.agent_id == agent_id and ann.time_us == time_us for ann in scene.annotations)


def _distance(
    scene: Scene, reference: str, time_us: int, center: tuple[float, float, float]
) -> float | None:
    try:
        pose = lookup_pose(scene.agent(reference), time_us, max_gap_us=0)
    except TimeQueryError:
        return None
    box = WorldBox(center=center, size_lwh=(1.0, 1.0, 1.0), rotation=SE3.identity())
    return horizontal_range_m(box, (pose.tx, pose.ty, pose.tz))


def _status_at(row: Mapping[str, object]) -> tuple[str, ...]:
    found: list[tuple[int, str]] = []
    for key, value in row.items():
        if not key.startswith("status_at_") or not isinstance(value, str):
            continue
        suffix = key.removeprefix("status_at_")
        if suffix.isdigit():
            found.append((int(suffix), value))
    found.sort()
    return tuple(value for _index, value in found)


def match_rows(directory: Path) -> list[dict[str, object]]:
    """The scene's match table, including every ``status_at_<i>`` column."""
    metrics = read_metrics(directory)
    names = [*MATCH_SCHEMA.names, *status_columns(threshold_count(metrics))]
    table = read_matches(directory, columns=names)
    rows: list[dict[str, object]] = []
    for item in table.to_pylist():
        rows.append({str(key): value for key, value in item.items()})
    return rows


def status_columns(n_thresholds: int) -> list[str]:
    return [f"status_at_{index}" for index in range(n_thresholds)]


def scene_outcomes_document(
    scene: Scene, rows: Sequence[Mapping[str, object]], run_id: str
) -> dict[str, object]:
    """The ``outcomes.json`` object for one evaluated scene."""
    records = records_from_rows(scene, rows, run_id=run_id, scene_id=scene.scene_id)
    return {
        "format": OUTCOMES_FORMAT,
        "format_version": OUTCOMES_VERSION,
        "scene_id": scene.scene_id,
        "run_id": run_id,
        "reference_agent": reference_agent_id(scene),
        "distance_source": "scene",
        "records": [record.to_json() for record in records],
    }


def write_scene_outcomes(
    directory: Path, scene: Scene, rows: Sequence[Mapping[str, object]], run_id: str
) -> None:
    """Write ``outcomes.json`` under the scene's evaluation directory."""
    document = scene_outcomes_document(scene, rows, run_id)
    text = json.dumps(document, indent=2, allow_nan=False) + "\n"
    scene_file(directory, OUTCOMES_FILE).write_text(text, encoding="utf-8")


def load_run_records(
    scenes_root: Path,
    predictions_root: Path,
    run_id: str,
    *,
    from_scores: bool,
) -> tuple[tuple[OutcomeRecord, ...], str]:
    """Every evaluated scene of ``run_id``.

    The second value says where distance came from: ``scene`` when the scene
    was opened, ``outcomes`` when ``outcomes.json`` supplied it, or
    ``unavailable`` when ``--from-scores`` had to use the match table alone.
    """
    from coperceive.evaluation.evaluate import METRICS_FILE, scene_output_dir
    from coperceive.evaluation.predictions import check_run_id
    from coperceive.store import read_scene
    from coperceive.viewdata import scene_directories

    check_run_id(run_id)
    records: list[OutcomeRecord] = []
    origins: list[str] = []
    for scene_dir in scene_directories(scenes_root):
        directory = scene_output_dir(predictions_root, run_id, scene_dir.name)
        if not (directory / METRICS_FILE).is_file():
            continue
        stored = directory / OUTCOMES_FILE
        if from_scores and stored.is_file():
            records.extend(read_outcome_records(stored))
            origins.append("outcomes")
            continue
        rows = match_rows(directory)
        if from_scores:
            records.extend(records_from_rows(None, rows, run_id=run_id, scene_id=scene_dir.name))
            origins.append("unavailable")
            continue
        scene = read_scene(scene_dir)
        records.extend(records_from_rows(scene, rows, run_id=run_id, scene_id=scene.scene_id))
        origins.append("scene")
    if not from_scores:
        return tuple(records), "scene"
    if origins and all(origin == "outcomes" for origin in origins):
        return tuple(records), "outcomes"
    return tuple(records), "unavailable"


def read_outcome_records(path: Path) -> tuple[OutcomeRecord, ...]:
    """Records stored by :func:`write_scene_outcomes`."""
    try:
        loaded: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise EvaluationError(f"{path}: {error}") from error
    if not isinstance(loaded, dict):
        raise EvaluationError(f"{path}: expected a JSON object")
    document = cast(dict[str, object], loaded)
    if document.get("format") != OUTCOMES_FORMAT:
        raise EvaluationError(f"{path}: format must be {OUTCOMES_FORMAT!r}")
    if document.get("format_version") != OUTCOMES_VERSION:
        raise EvaluationError(f"{path}: format_version must be {OUTCOMES_VERSION}")
    raw = document.get("records")
    if not isinstance(raw, list):
        raise EvaluationError(f"{path}: records must be a list")
    rows = cast(list[object], raw)
    return tuple(_record_from_json(item, path) for item in rows)


def _record_from_json(value: object, path: Path) -> OutcomeRecord:
    if not isinstance(value, dict):
        raise EvaluationError(f"{path}: a record must be an object")
    item = cast(dict[str, object], value)
    try:
        return OutcomeRecord(
            kind=str(item["kind"]),
            key=str(item["key"]),
            scene_id=str(item["scene_id"]),
            run_id=str(item["run_id"]),
            agent_id=str(item["agent_id"]),
            source=str(item["source"]),
            time_us=_required_int(item.get("time_us"), path, "time_us"),
            row=_required_int(item.get("row"), path, "row"),
            track_id=_as_str(item.get("track_id")),
            category=str(item.get("category") or ""),
            score=_as_float(item.get("score")),
            distance_m=_as_float(item.get("distance_m")),
            visibility=_as_float(item.get("visibility")),
            agent_count=_as_int(item.get("agent_count")),
            agent_bin=str(item.get("agent_bin") or "unknown"),
            status=str(item.get("status") or ""),
            status_at=_string_tuple(item.get("status_at")),
            pair_id=_as_int(item.get("pair_id")),
            iou_bev=_as_float(item.get("iou_bev")),
            centre_error_m=_as_float(item.get("centre_error_m")),
            yaw_error_deg=_as_float(item.get("yaw_error_deg")),
            status_single=_as_str(item.get("status_single")),
            status_fused=_as_str(item.get("status_fused")),
            center=_vec3(item.get("center"), path, "center"),
            size_lwh=_vec3(item.get("size_lwh"), path, "size_lwh"),
            rotation=_vec4(item.get("rotation"), path),
        )
    except KeyError as error:
        raise EvaluationError(f"{path}: record missing {error}") from error


def _fixed_list(value: object, count: int) -> list[object] | None:
    if not isinstance(value, list):
        return None
    items = cast(list[object], value)
    if len(items) != count:
        return None
    return items


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(str(item) for item in cast(list[object], value) if isinstance(item, str))


def _vec3(value: object, path: Path, name: str) -> tuple[float, float, float]:
    items = _fixed_list(value, 3)
    if items is None:
        raise EvaluationError(f"{path}: {name} must be three numbers")
    numbers = [_as_float(item) for item in items]
    if any(item is None for item in numbers):
        raise EvaluationError(f"{path}: {name} must be three numbers")
    return (numbers[0] or 0.0, numbers[1] or 0.0, numbers[2] or 0.0)


def _vec4(value: object, path: Path) -> tuple[float, float, float, float]:
    items = _fixed_list(value, 4)
    if items is None:
        raise EvaluationError(f"{path}: rotation must be four numbers")
    numbers = [_as_float(item) for item in items]
    if any(item is None for item in numbers):
        raise EvaluationError(f"{path}: rotation must be four numbers")
    return (numbers[0] or 0.0, numbers[1] or 0.0, numbers[2] or 0.0, numbers[3] or 0.0)


def _required_int(value: object, path: Path, name: str) -> int:
    parsed = _as_int(value)
    if parsed is None:
        raise EvaluationError(f"{path}: {name} must be an integer")
    return parsed


def _as_int(value: object) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def _component(value: object, default: float) -> float:
    parsed = _as_float(value)
    return default if parsed is None else parsed


def _as_float(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if number != number:  # NaN
        return None
    return number


def _as_str(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return None


def _as_bool(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    return None
