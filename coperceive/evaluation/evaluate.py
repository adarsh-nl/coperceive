# SPDX-License-Identifier: Apache-2.0
"""Scene evaluation: the match table, the metrics file and the run driver.

This is where ``docs/spec/predictions.md`` §Matching, §Metrics, §Cooperative
views, §``matches.parquet`` and §``metrics.json`` meet a scene: boxes are placed
in the world frame, every ``(agent_id, source)`` output is matched per pose time
and per category, AP is computed at every threshold, the cooperative columns
are derived from the other agents' annotations, and the two files are written
beside ``boxes.parquet``. Nothing here writes into a scene directory.

Samples are the agent's pose times (§Metrics). A ground-truth box of an
evaluated agent at a time that is not one of its pose times cannot be placed
with ``max_gap_us = 0`` and belongs to no sample, so it is an error rather than
a silently dropped box. ``metrics.json`` is written with ``allow_nan=False``;
absent values are ``null``.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

import pyarrow as pa
import pyarrow.parquet as pq

from coperceive.adapters.ingest import coperceive_version
from coperceive.adapters.paths import PathError, confine
from coperceive.assoc import Association, AssociationKind, agent_annotations, associate
from coperceive.errors import CoperceiveError
from coperceive.evaluation.config import EvalConfig, config_json, display_index, threshold_key
from coperceive.evaluation.geometry import WorldBox, horizontal_range_m
from coperceive.evaluation.matching import (
    IGNORED,
    TP,
    GtBox,
    MatchResult,
    Pairing,
    PredBox,
    match_sample,
)
from coperceive.evaluation.metrics import (
    Counts,
    GtCooperative,
    average_precision,
    cooperative_counts,
    counts_of,
    tp_errors,
)
from coperceive.evaluation.predictions import (
    BOXES_FILE,
    PredictionBox,
    PredictionError,
    RunMeta,
    check_run_id,
    check_scene_id,
    read_boxes,
    read_run_meta,
    validate_boxes,
)
from coperceive.model import Annotation, BoxFrame, Scene
from coperceive.se3 import SE3
from coperceive.store import read_scene
from coperceive.transform import lookup_pose
from coperceive.viewdata import scene_directories

METRICS_FORMAT = "coperceive-eval"
METRICS_VERSION = 1
MATCHES_FILE = "matches.parquet"
METRICS_FILE = "metrics.json"

KIND_GT = "gt"
KIND_PREDICTION = "prediction"
SOURCE_SINGLE = "single"
SOURCE_FUSED = "fused"

# §Cooperative views: the ``associate`` gate for a computed pair.
ASSOCIATE_MAX_CENTER_M = 2.0
ASSOCIATE_SIZE_FRACTION = 0.5

MATCH_SCHEMA = pa.schema(
    [
        pa.field("kind", pa.string(), nullable=False),
        pa.field("agent_id", pa.string(), nullable=False),
        pa.field("source", pa.string(), nullable=False),
        pa.field("time_us", pa.int64(), nullable=False),
        pa.field("category", pa.string(), nullable=False),
        pa.field("row", pa.int64(), nullable=True),
        pa.field("score", pa.float64(), nullable=True),
        pa.field("track_id", pa.string(), nullable=True),
        pa.field("visibility", pa.float64(), nullable=True),
        pa.field("status", pa.string(), nullable=False),
        pa.field("pair_id", pa.int64(), nullable=True),
        pa.field("iou_bev", pa.float64(), nullable=True),
        pa.field("centre_error_m", pa.float64(), nullable=True),
        pa.field("yaw_error_deg", pa.float64(), nullable=True),
        pa.field("cx", pa.float64(), nullable=False),
        pa.field("cy", pa.float64(), nullable=False),
        pa.field("cz", pa.float64(), nullable=False),
        pa.field("length", pa.float64(), nullable=False),
        pa.field("width", pa.float64(), nullable=False),
        pa.field("height", pa.float64(), nullable=False),
        pa.field("qw", pa.float64(), nullable=False),
        pa.field("qx", pa.float64(), nullable=False),
        pa.field("qy", pa.float64(), nullable=False),
        pa.field("qz", pa.float64(), nullable=False),
        pa.field("status_single", pa.string(), nullable=True),
        pa.field("status_fused", pa.string(), nullable=True),
        pa.field("other_kind", pa.string(), nullable=True),
        pa.field("seen_by_other_gt", pa.bool_(), nullable=True),
        pa.field("other_agent_tp", pa.bool_(), nullable=True),
    ]
)


class EvaluationError(CoperceiveError):
    """A scene cannot be evaluated as the spec describes, or a result file is malformed."""


def world_box(
    scene: Scene,
    agent_id: str,
    time_us: int,
    center: tuple[float, float, float],
    size_lwh: tuple[float, float, float],
    rotation: SE3,
    frame: BoxFrame,
) -> WorldBox:
    """Place a box in the scene world frame (§Geometry).

    A ``world`` box is taken as is. A ``body`` box goes through the agent pose at
    ``time_us`` with ``max_gap_us = 0``, so a time that is not a pose sample
    raises :class:`~coperceive.errors.TimeQueryError`.
    """
    if frame is BoxFrame.WORLD:
        return WorldBox(center=center, size_lwh=size_lwh, rotation=_pure_rotation(rotation))
    pose = lookup_pose(scene.agent(agent_id), time_us, max_gap_us=0)
    return WorldBox(
        center=pose.apply(center),
        size_lwh=size_lwh,
        rotation=_pure_rotation(pose.compose(rotation)),
    )


def _pure_rotation(value: SE3) -> SE3:
    return SE3(value.qw, value.qx, value.qy, value.qz, 0.0, 0.0, 0.0)


@dataclass(frozen=True)
class SceneEvaluation:
    """The rows of ``matches.parquet`` and the object of ``metrics.json`` for one scene."""

    matches: tuple[dict[str, object], ...]
    metrics: dict[str, object]


@dataclass(frozen=True)
class RunReport:
    """What :func:`evaluate_run` did per scene; ``errors`` are ``"<scene_id>: <message>"``."""

    evaluated: tuple[str, ...]
    skipped: tuple[str, ...]
    errors: tuple[str, ...]


# --- internal placement -----------------------------------------------------------


@dataclass(frozen=True)
class _PlacedGt:
    annotation: Annotation
    box: GtBox
    world: WorldBox


@dataclass(frozen=True)
class _PlacedPred:
    prediction: PredictionBox
    box: PredBox
    world: WorldBox


@dataclass(frozen=True)
class _ApRecord:
    """One prediction's part in the AP walk of its category at one threshold."""

    row: int
    score: float
    status: str


@dataclass
class _Output:
    """One ``(agent_id, source)`` output and everything matched for it."""

    agent_id: str
    source: str
    preds: dict[int, list[_PlacedPred]] = field(default_factory=dict[int, list[_PlacedPred]])
    display: dict[int, MatchResult] = field(default_factory=dict[int, MatchResult])
    # threshold index -> category -> (row, score, status) records, collected per pose
    # time; ``_ap_records`` puts them back into file order before the AP walk.
    ap_records: dict[int, dict[str, list[_ApRecord]]] = field(
        default_factory=dict[int, dict[str, list[_ApRecord]]]
    )

    @property
    def key(self) -> tuple[str, str]:
        return (self.agent_id, self.source)

    def all_preds(self) -> list[_PlacedPred]:
        return [placed for rows in self.preds.values() for placed in rows]


@dataclass(frozen=True)
class _OtherBox:
    """Another agent's box paired with a ground-truth box (§Cooperative views)."""

    agent_id: str
    row: int
    kind: AssociationKind


def _agent_origin(scene: Scene, agent_id: str, time_us: int) -> tuple[float, float, float]:
    pose = lookup_pose(scene.agent(agent_id), time_us, max_gap_us=0)
    return (pose.tx, pose.ty, pose.tz)


def _place_gt(scene: Scene, agent_id: str) -> dict[int, list[_PlacedGt]]:
    """The agent's annotations per pose time, placed in the world frame, in row order."""
    times = scene.pose_times(agent_id)
    by_time: dict[int, list[_PlacedGt]] = {time_us: [] for time_us in times}
    origins = {time_us: _agent_origin(scene, agent_id, time_us) for time_us in times}
    for row, ann in enumerate(scene.annotations):
        if ann.agent_id != agent_id:
            continue
        if ann.time_us not in by_time:
            raise EvaluationError(
                f"annotation row {row}: time_us {ann.time_us} is not a pose time of agent "
                f"{agent_id!r}, so it belongs to no sample"
            )
        world = world_box(
            scene, agent_id, ann.time_us, ann.center, ann.size_lwh, ann.box_from_rpy, ann.frame
        )
        by_time[ann.time_us].append(
            _PlacedGt(
                annotation=ann,
                box=GtBox(
                    row=row,
                    category=ann.category,
                    track_id=ann.track_id,
                    visibility=ann.visibility,
                    world=world,
                    range_m=horizontal_range_m(world, origins[ann.time_us]),
                ),
                world=world,
            )
        )
    return by_time


def _place_pred(scene: Scene, row: int, box: PredictionBox) -> _PlacedPred:
    world = world_box(
        scene, box.agent_id, box.time_us, box.center, box.size_lwh, box.box_from_rpy, box.frame
    )
    origin = _agent_origin(scene, box.agent_id, box.time_us)
    return _PlacedPred(
        prediction=box,
        box=PredBox(
            row=row,
            category=box.category,
            score=box.score,
            track_id=box.track_id,
            world=world,
            range_m=horizontal_range_m(world, origin),
        ),
        world=world,
    )


# --- matching per output ----------------------------------------------------------


def _collect_outputs(scene: Scene, boxes: Sequence[PredictionBox]) -> list[_Output]:
    """Outputs in first-appearance order with their predictions placed, per time."""
    outputs: dict[tuple[str, str], _Output] = {}
    for row, box in enumerate(boxes):
        key = (box.agent_id, box.source)
        output = outputs.get(key)
        if output is None:
            output = _Output(agent_id=box.agent_id, source=box.source)
            outputs[key] = output
        output.preds.setdefault(box.time_us, []).append(_place_pred(scene, row, box))
    return list(outputs.values())


def _match_output(
    output: _Output,
    gt_by_time: Mapping[int, Sequence[_PlacedGt]],
    config: EvalConfig,
) -> None:
    """Rules 1–6 at the display threshold per sample, then rule 7 at every threshold."""
    for time_us in output.preds:
        if time_us not in gt_by_time:
            # validate_boxes guarantees a pose time; a stale table reaches here.
            raise EvaluationError(
                f"{output.agent_id}/{output.source}: time_us {time_us} is not a pose time"
            )
    shown = display_index(config)
    for time_us, placed_gt in gt_by_time.items():
        gt = [item.box for item in placed_gt]
        preds = [item.box for item in output.preds.get(time_us, ())]
        output.display[time_us] = match_sample(
            gt, preds, config, threshold_index=shown, apply_score_threshold=True
        )
        for index in range(len(config.thresholds)):
            result = match_sample(
                gt, preds, config, threshold_index=index, apply_score_threshold=False
            )
            records = output.ap_records.setdefault(index, {})
            for pred in preds:
                records.setdefault(pred.category, []).append(
                    _ApRecord(row=pred.row, score=pred.score, status=result.pred_status[pred.row])
                )


def _ap_records(records: Sequence[_ApRecord]) -> list[tuple[float, str]]:
    """``(score, status)`` in ``boxes.parquet`` row order (§Metrics AP step 1).

    The records of one category are collected per pose time, so two equal-score
    predictions at different times would otherwise be walked in time order.
    :func:`~coperceive.evaluation.metrics.average_precision` sorts by score with
    a stable sort, so handing it the file order makes ties follow the file.
    """
    return [(item.score, item.status) for item in sorted(records, key=lambda item: item.row)]


def _ap_table(
    output: _Output,
    gt_by_time: Mapping[int, Sequence[_PlacedGt]],
    meta: RunMeta,
    config: EvalConfig,
) -> dict[str, object]:
    """``ap`` of §``metrics.json``: per threshold key, per category, plus ``mean``.

    The categories are the run's ``categories``, the agent's ground-truth
    categories and the output's predicted categories, so a class listed in
    ``run.json`` without a prediction gets ``0.0`` (when it has ground truth)
    and a class the model emitted outside the list is still reported.
    """
    n_gt: dict[str, int] = {}
    for time_us, placed_gt in gt_by_time.items():
        statuses = output.display[time_us].gt_status
        for item in placed_gt:
            n_gt.setdefault(item.box.category, 0)
            if statuses[item.box.row] != IGNORED:
                n_gt[item.box.category] += 1
    categories = set(meta.categories) | set(n_gt)
    for placed in output.all_preds():
        categories.add(placed.box.category)
    table: dict[str, object] = {}
    for index, threshold in enumerate(config.thresholds):
        records = output.ap_records.get(index, {})
        per_category: dict[str, object] = {}
        values: list[float] = []
        for category in sorted(categories):
            ap = average_precision(_ap_records(records.get(category, ())), n_gt.get(category, 0))
            per_category[category] = ap
            if ap is not None:
                values.append(ap)
        per_category["mean"] = sum(values) / len(values) if values else None
        table[threshold_key(threshold)] = per_category
    return table


def _samples_and_totals(
    output: _Output, gt_by_time: Mapping[int, Sequence[_PlacedGt]]
) -> tuple[list[dict[str, object]], dict[str, object]]:
    samples: list[dict[str, object]] = []
    tp = fp = miss = ignored = 0
    for time_us in gt_by_time:
        counts = counts_of(output.display[time_us])
        samples.append(
            {
                "time_us": time_us,
                "tp": counts.tp,
                "fp": counts.fp,
                "miss": counts.miss,
                "ignored_gt": counts.ignored_gt,
                "precision": counts.precision,
                "recall": counts.recall,
            }
        )
        tp += counts.tp
        fp += counts.fp
        miss += counts.miss
        ignored += counts.ignored_gt
    total = Counts(tp=tp, fp=fp, miss=miss, ignored_gt=ignored)
    totals: dict[str, object] = {
        "tp": tp,
        "fp": fp,
        "miss": miss,
        "ignored_gt": ignored,
        "gt": tp + miss,
        "predictions": len(output.all_preds()),
        "precision": total.precision,
        "recall": total.recall,
    }
    return samples, totals


def _tp_errors(output: _Output, gt_by_time: Mapping[int, Sequence[_PlacedGt]]) -> dict[str, object]:
    pairs: list[Pairing] = []
    sizes: dict[int, tuple[tuple[float, float, float], tuple[float, float, float]]] = {}
    for time_us, placed_gt in gt_by_time.items():
        gt_sizes = {item.box.row: item.world.size_lwh for item in placed_gt}
        pred_sizes = {item.box.row: item.world.size_lwh for item in output.preds.get(time_us, ())}
        for pair in output.display[time_us].pairs:
            pairs.append(pair)
            sizes[pair.pred_row] = (pred_sizes[pair.pred_row], gt_sizes[pair.gt_row])
    return tp_errors(pairs, sizes)


# --- cooperative views --------------------------------------------------------------


def _gt_status(
    outputs: Mapping[tuple[str, str], _Output], agent_id: str, source: str, time_us: int, row: int
) -> str | None:
    output = outputs.get((agent_id, source))
    if output is None:
        return None
    return output.display[time_us].gt_status[row]


def _row_of(scene: Scene, agent_id: str, time_us: int, ann: Annotation, used: set[int]) -> int:
    """The ``annotations.parquet`` row of ``ann`` among the agent's boxes at ``time_us``."""
    for row, candidate in enumerate(scene.annotations):
        if (
            row not in used
            and candidate.agent_id == agent_id
            and candidate.time_us == time_us
            and candidate == ann
        ):
            used.add(row)
            return row
    raise EvaluationError(f"associate returned a box that is not a row of agent {agent_id!r}")


def _other_boxes(
    scene: Scene, agent_id: str, time_us: int, placed_gt: Sequence[_PlacedGt]
) -> dict[int, list[_OtherBox]]:
    """Per ground-truth row of ``agent_id`` at ``time_us``, the other agents' paired boxes.

    Shared track ids come first inside :func:`associate`; the rest are computed
    pairs under the §Cooperative views gate. Every other agent of the scene is
    tried in scene order.
    """
    found: dict[int, list[_OtherBox]] = {item.box.row: [] for item in placed_gt}
    if not placed_gt:
        return found
    for other in scene.agents:
        if other.agent_id == agent_id or not agent_annotations(
            scene, other.agent_id, time_us=time_us
        ):
            continue
        pairs: tuple[Association, ...] = associate(
            scene,
            agent_id,
            other.agent_id,
            time_us=time_us,
            max_gap_us=0,
            max_center_m=ASSOCIATE_MAX_CENTER_M,
            size_fraction=ASSOCIATE_SIZE_FRACTION,
        )
        used_left: set[int] = set()
        used_right: set[int] = set()
        for pair in pairs:
            left = _row_of(scene, agent_id, time_us, pair.left, used_left)
            right = _row_of(scene, other.agent_id, time_us, pair.right, used_right)
            found[left].append(_OtherBox(agent_id=other.agent_id, row=right, kind=pair.kind))
    return found


def _cooperative_columns(
    scene: Scene,
    outputs: Mapping[tuple[str, str], _Output],
    agent_id: str,
    gt_by_time: Mapping[int, Sequence[_PlacedGt]],
) -> dict[int, GtCooperative]:
    """The §Cooperative views columns per ground-truth row of ``agent_id``."""
    columns: dict[int, GtCooperative] = {}
    for time_us, placed_gt in gt_by_time.items():
        others = _other_boxes(scene, agent_id, time_us, placed_gt)
        for item in placed_gt:
            row = item.box.row
            paired = others[row]
            kinds = {other.kind for other in paired}
            other_kind: str | None = None
            if AssociationKind.SHARED_TRACK in kinds:
                other_kind = AssociationKind.SHARED_TRACK.value
            elif kinds:
                other_kind = AssociationKind.COMPUTED.value
            other_tp: bool | None = None
            for other in paired:
                status = _gt_status(outputs, other.agent_id, SOURCE_SINGLE, time_us, other.row)
                if status is None:
                    continue
                other_tp = bool(other_tp) or status == TP
            columns[row] = GtCooperative(
                status_single=_gt_status(outputs, agent_id, SOURCE_SINGLE, time_us, row),
                status_fused=_gt_status(outputs, agent_id, SOURCE_FUSED, time_us, row),
                seen_by_other_gt=bool(paired),
                other_kind=other_kind,
                other_agent_tp=other_tp,
            )
    return columns


# --- rows and the metrics object ----------------------------------------------------


def _box_columns(world: WorldBox) -> dict[str, object]:
    rotation = world.rotation
    return {
        "cx": world.center[0],
        "cy": world.center[1],
        "cz": world.center[2],
        "length": world.size_lwh[0],
        "width": world.size_lwh[1],
        "height": world.size_lwh[2],
        "qw": rotation.qw,
        "qx": rotation.qx,
        "qy": rotation.qy,
        "qz": rotation.qz,
    }


def _match_rows(
    output: _Output,
    gt_by_time: Mapping[int, Sequence[_PlacedGt]],
    cooperative: Mapping[int, GtCooperative],
    next_pair_id: int,
) -> tuple[list[dict[str, object]], int]:
    """The §``matches.parquet`` rows of one output: per time, gt rows then predictions.

    ``pair_id`` is handed out in row order, so it counts from 0 through the file.
    """
    rows: list[dict[str, object]] = []
    for time_us, placed_gt in gt_by_time.items():
        result = output.display[time_us]
        by_gt = {pair.gt_row: pair for pair in result.pairs}
        by_pred = {pair.pred_row: pair for pair in result.pairs}
        pair_ids: dict[int, int] = {}
        for item in placed_gt:
            gt_row = item.box.row
            pair = by_gt.get(gt_row)
            pair_id: int | None = None
            if pair is not None:
                pair_id = next_pair_id
                pair_ids[pair.pred_row] = pair_id
                next_pair_id += 1
            coop = cooperative[gt_row]
            rows.append(
                {
                    "kind": KIND_GT,
                    "agent_id": output.agent_id,
                    "source": output.source,
                    "time_us": time_us,
                    "category": item.box.category,
                    "row": gt_row,
                    "score": None,
                    "track_id": item.box.track_id,
                    "visibility": item.box.visibility,
                    "status": result.gt_status[gt_row],
                    "pair_id": pair_id,
                    "iou_bev": None if pair is None else pair.iou_bev,
                    "centre_error_m": None if pair is None else pair.centre_error_m,
                    "yaw_error_deg": None if pair is None else pair.yaw_error_deg,
                    **_box_columns(item.world),
                    "status_single": coop.status_single,
                    "status_fused": coop.status_fused,
                    "other_kind": coop.other_kind,
                    "seen_by_other_gt": coop.seen_by_other_gt,
                    "other_agent_tp": coop.other_agent_tp,
                }
            )
        for placed in output.preds.get(time_us, ()):
            pred_row = placed.box.row
            pair = by_pred.get(pred_row)
            rows.append(
                {
                    "kind": KIND_PREDICTION,
                    "agent_id": output.agent_id,
                    "source": output.source,
                    "time_us": time_us,
                    "category": placed.box.category,
                    "row": pred_row,
                    "score": placed.box.score,
                    "track_id": placed.box.track_id,
                    "visibility": None,
                    "status": result.pred_status[pred_row],
                    "pair_id": None if pair is None else pair_ids[pred_row],
                    "iou_bev": None if pair is None else pair.iou_bev,
                    "centre_error_m": None if pair is None else pair.centre_error_m,
                    "yaw_error_deg": None if pair is None else pair.yaw_error_deg,
                    **_box_columns(placed.world),
                    "status_single": None,
                    "status_fused": None,
                    "other_kind": None,
                    "seen_by_other_gt": None,
                    "other_agent_tp": None,
                }
            )
    return rows, next_pair_id


def evaluate_scene(
    scene: Scene,
    meta: RunMeta,
    boxes: Sequence[PredictionBox],
    config: EvalConfig,
    *,
    created_utc: str,
) -> SceneEvaluation:
    """Match every output of ``boxes`` against ``scene`` and build both result objects.

    ``boxes`` are the rows of ``boxes.parquet`` in file order, already checked by
    :func:`~coperceive.evaluation.predictions.validate_boxes`. Outputs are
    ordered by first appearance; each is matched per pose time of its agent at
    the display threshold (statuses, counts, pairs) and once more per threshold
    without the score threshold (AP). The cooperative columns are computed per
    agent once every output is matched, since ``other_agent_tp`` reads another
    output's statuses.
    """
    outputs = _collect_outputs(scene, boxes)
    by_key = {output.key: output for output in outputs}
    gt_by_agent: dict[str, dict[int, list[_PlacedGt]]] = {}
    for output in outputs:
        if output.agent_id not in gt_by_agent:
            gt_by_agent[output.agent_id] = _place_gt(scene, output.agent_id)
        _match_output(output, gt_by_agent[output.agent_id], config)
    cooperative_by_agent = {
        agent_id: _cooperative_columns(scene, by_key, agent_id, gt_by_time)
        for agent_id, gt_by_time in gt_by_agent.items()
    }
    matches: list[dict[str, object]] = []
    metrics_outputs: list[dict[str, object]] = []
    next_pair_id = 0
    for output in outputs:
        gt_by_time = gt_by_agent[output.agent_id]
        cooperative = cooperative_by_agent[output.agent_id]
        rows, next_pair_id = _match_rows(output, gt_by_time, cooperative, next_pair_id)
        matches.extend(rows)
        samples, totals = _samples_and_totals(output, gt_by_time)
        metrics_outputs.append(
            {
                "agent_id": output.agent_id,
                "source": output.source,
                "samples": samples,
                "totals": totals,
                "ap": _ap_table(output, gt_by_time, meta, config),
                "tp_errors": _tp_errors(output, gt_by_time),
                "cooperative": cooperative_counts(tuple(cooperative.values())),
            }
        )
    metrics: dict[str, object] = {
        "format": METRICS_FORMAT,
        "format_version": METRICS_VERSION,
        "run_id": meta.run_id,
        "scene_id": scene.scene_id,
        "created_utc": created_utc,
        "coperceive_version": coperceive_version(),
        "config": config_json(config),
        "outputs": metrics_outputs,
    }
    return SceneEvaluation(
        matches=tuple(matches), metrics=cast(dict[str, object], _json_safe(metrics))
    )


# --- files ------------------------------------------------------------------------


def _json_safe(value: object) -> object:
    """Copy ``value`` with every non-finite float replaced by ``None``."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {
            str(key): _json_safe(item) for key, item in cast(dict[object, object], value).items()
        }
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in cast(Sequence[object], value)]
    return value


def write_scene_evaluation(directory: Path, result: SceneEvaluation) -> None:
    """Write ``matches.parquet`` and ``metrics.json`` into ``directory``."""
    directory.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pylist(list(result.matches), schema=MATCH_SCHEMA)
    pq.write_table(table, directory / MATCHES_FILE)
    text = json.dumps(_json_safe(result.metrics), indent=2, allow_nan=False)
    (directory / METRICS_FILE).write_text(text + "\n", encoding="utf-8")


def read_metrics(directory: Path) -> dict[str, object]:
    """The ``metrics.json`` object of one evaluated scene."""
    path = directory / METRICS_FILE
    try:
        loaded: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise EvaluationError(f"{path}: {error}") from error
    if not isinstance(loaded, dict):
        raise EvaluationError(f"{path}: expected a JSON object")
    metrics: dict[str, object] = {}
    for key, item in cast(dict[object, object], loaded).items():
        if not isinstance(key, str):
            raise EvaluationError(f"{path}: non-string key")
        metrics[key] = item
    if metrics.get("format") != METRICS_FORMAT:
        raise EvaluationError(f"{path}: format must be {METRICS_FORMAT!r}")
    version = metrics.get("format_version")
    if type(version) is not int or version != METRICS_VERSION:
        raise EvaluationError(f"{path}: format_version must be {METRICS_VERSION}")
    return metrics


def read_matches(directory: Path) -> pa.Table:
    """The ``matches.parquet`` table of one evaluated scene, §``matches.parquet`` columns."""
    path = directory / MATCHES_FILE
    try:
        return pq.read_table(path, columns=list(MATCH_SCHEMA.names))
    except Exception as error:  # pyarrow error types are not in the stubs
        raise EvaluationError(f"{path}: {error}") from error


# --- run driver -------------------------------------------------------------------


def scene_output_dir(predictions_root: Path, run_id: str, scene_id: str) -> Path:
    """``<predictions_root>/<run_id>/<scene_id>``, confined to the predictions root."""
    try:
        return confine(predictions_root, f"{run_id}/{scene_id}")
    except PathError as error:
        raise PredictionError(str(error)) from error


def evaluate_run(
    scenes_root: Path,
    predictions_root: Path,
    run_id: str,
    config: EvalConfig,
    *,
    scene_ids: Sequence[str] | None = None,
    created_utc: str,
) -> RunReport:
    """Evaluate every scene of ``run_id`` that has ``boxes.parquet`` and write its files.

    Scenes are the directories of ``scenes_root`` (or ``scene_ids`` among them).
    A scene without ``boxes.parquet`` is skipped; a scene id that names no
    directory, a scene that fails to load, and a validation or evaluation
    failure go into ``errors`` as ``"<scene_id>: <message>"`` and the run goes
    on. Nothing is written for a scene that failed.
    """
    check_run_id(run_id)
    meta = read_run_meta(predictions_root, run_id)
    directories = {path.name: path for path in scene_directories(scenes_root)}
    if scene_ids is None:
        selected = list(directories)
    else:
        selected = list(dict.fromkeys(scene_ids))
    evaluated: list[str] = []
    skipped: list[str] = []
    errors: list[str] = []
    for scene_id in selected:
        try:
            check_scene_id(scene_id)
            scene_dir = directories.get(scene_id)
            if scene_dir is None:
                raise EvaluationError(f"no scene directory under {scenes_root}")
            output_dir = scene_output_dir(predictions_root, run_id, scene_id)
            if not (output_dir / BOXES_FILE).is_file():
                skipped.append(scene_id)
                continue
            scene = read_scene(scene_dir)
            boxes = read_boxes(predictions_root, run_id, scene_id)
            validate_boxes(scene, meta, boxes)
            result = evaluate_scene(scene, meta, boxes, config, created_utc=created_utc)
            write_scene_evaluation(output_dir, result)
        except (CoperceiveError, ValueError, OSError) as error:
            errors.append(f"{scene_id}: {error}")
            continue
        evaluated.append(scene_id)
    return RunReport(evaluated=tuple(evaluated), skipped=tuple(skipped), errors=tuple(errors))
