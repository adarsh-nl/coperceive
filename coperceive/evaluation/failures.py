# SPDX-License-Identifier: Apache-2.0
"""Rank the worst frames of one run and tag why each one failed.

Rules only: no learned detector. A frame is one output at one scene time,
``(scene_id, time_us, agent_id, source)``. Badness sums operating-point false
negatives and false positives. Each term is ``class_weight(category) *
distance_weight(distance_m)``. Vulnerable classes (pedestrian, cyclist,
bicycle, motorcycle, rider) default to weight 2 and every other class to 1.
Distance weight is 2 under 20 m, 1 from 20 m up to 50 m, 0.5 at 50 m and
beyond, and 1 when the distance is unknown. Ignored boxes and predictions
below the score threshold add nothing.

Tags, and the case that does not receive them:

* ``missed_vru`` — a false negative of a vulnerable class within ``vru_within_m``
  (default 20 m). A miss farther than that, or of another class, is not this tag.
* ``ghost_box`` — a false positive whose centre is farther than ``ghost_m``
  (default 2 m) from every ground-truth centre at that time, or there is no
  ground truth. A false positive near a ground-truth box is not a ghost.
* ``duplicate_boxes`` — a false positive whose bird's-eye IoU with a
  true-positive ground-truth box of the same class is at least ``duplicate_iou``
  (default 0.5). A lone false positive far from every true positive is not.
* ``localisation_error`` — a true positive whose centre error is at least
  ``loc_centre_m`` (default 1 m) or whose absolute yaw error is at least
  ``loc_yaw_deg`` (default 15°). A tight match is not.
* ``far_range_miss`` — a false negative at least ``far_miss_m`` away (default
  50 m). A nearer miss is not.
* ``occluded_miss`` — a false negative whose visibility is a number below
  ``occluded_visibility_below`` (default 0.6). Null visibility is never called
  occluded.
* ``fusion_disagreement`` — ground truth the single output matched and the
  fused output missed, on the fused frame, and only when both statuses exist.
* ``latency_sensitive`` — the same ground-truth key is a true positive here and
  a false negative in a latency run. The tag is omitted unless that run is
  passed in; it is not guessed.

Ranking is badness descending, then scene, time, agent and source. Ties do
not move between runs.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from coperceive.evaluation.evaluate import KIND_GT, KIND_PREDICTION, EvaluationError
from coperceive.evaluation.geometry import WorldBox, bev_iou
from coperceive.evaluation.outcomes import OutcomeRecord
from coperceive.se3 import SE3

FAILURES_FORMAT = "coperceive-failures"
FAILURES_VERSION = 1
FORMULA = (
    "badness = sum over operating-point false negatives and false positives of "
    "class_weight(category) * distance_weight(distance_m)"
)
VRU_CLASSES = ("pedestrian", "cyclist", "bicycle", "motorcycle", "rider")
TAG_ORDER = (
    "missed_vru",
    "ghost_box",
    "duplicate_boxes",
    "localisation_error",
    "far_range_miss",
    "occluded_miss",
    "fusion_disagreement",
    "latency_sensitive",
)


def _empty_class_weights() -> dict[str, float]:
    return {}


@dataclass(frozen=True)
class FailureWeights:
    """Every knob of the badness sum and the tags. Defaults are the documented ones."""

    class_weights: Mapping[str, float] = field(default_factory=_empty_class_weights)
    default_class_weight: float = 1.0
    vru_classes: tuple[str, ...] = VRU_CLASSES
    vru_weight: float = 2.0
    near_m: float = 20.0
    mid_m: float = 50.0
    near_weight: float = 2.0
    mid_weight: float = 1.0
    far_weight: float = 0.5
    unknown_distance_weight: float = 1.0
    vru_within_m: float = 20.0
    ghost_m: float = 2.0
    duplicate_iou: float = 0.5
    loc_centre_m: float = 1.0
    loc_yaw_deg: float = 15.0
    far_miss_m: float = 50.0
    occluded_visibility_below: float = 0.6

    def class_weight(self, category: str) -> float:
        if category in self.class_weights:
            return float(self.class_weights[category])
        if category in self.vru_classes:
            return self.vru_weight
        return self.default_class_weight

    def distance_weight(self, distance_m: float | None) -> float:
        if distance_m is None:
            return self.unknown_distance_weight
        if distance_m < self.near_m:
            return self.near_weight
        if distance_m < self.mid_m:
            return self.mid_weight
        return self.far_weight

    def to_json(self) -> dict[str, object]:
        return {
            "class_weights": {key: self.class_weights[key] for key in sorted(self.class_weights)},
            "default_class_weight": self.default_class_weight,
            "vru_classes": list(self.vru_classes),
            "vru_weight": self.vru_weight,
            "near_m": self.near_m,
            "mid_m": self.mid_m,
            "near_weight": self.near_weight,
            "mid_weight": self.mid_weight,
            "far_weight": self.far_weight,
            "unknown_distance_weight": self.unknown_distance_weight,
            "vru_within_m": self.vru_within_m,
            "ghost_m": self.ghost_m,
            "duplicate_iou": self.duplicate_iou,
            "loc_centre_m": self.loc_centre_m,
            "loc_yaw_deg": self.loc_yaw_deg,
            "far_miss_m": self.far_miss_m,
            "occluded_visibility_below": self.occluded_visibility_below,
        }


def weights_from_json(payload: Mapping[str, object]) -> FailureWeights:
    """Parse a weights file. An unknown key is an error."""
    known = set(FailureWeights().to_json())
    unexpected = sorted(set(payload) - known)
    if unexpected:
        raise EvaluationError(f"unknown failure weight {unexpected[0]!r}")
    defaults = FailureWeights()
    raw_weights = payload.get("class_weights", {})
    if not isinstance(raw_weights, dict):
        raise EvaluationError("class_weights must be an object")
    class_weights: dict[str, float] = {}
    for key, value in cast(dict[object, object], raw_weights).items():
        if not isinstance(key, str):
            raise EvaluationError("class_weights keys must be strings")
        class_weights[key] = _required_float(value, f"class_weights.{key}")
    vru = payload.get("vru_classes", list(defaults.vru_classes))
    if not isinstance(vru, list):
        raise EvaluationError("vru_classes must be a list of strings")
    vru_items = cast(list[object], vru)
    if any(not isinstance(item, str) for item in vru_items):
        raise EvaluationError("vru_classes must be a list of strings")
    names = tuple(str(item) for item in vru_items)
    return FailureWeights(
        class_weights=class_weights,
        default_class_weight=_float_or(
            payload, "default_class_weight", defaults.default_class_weight
        ),
        vru_classes=names,
        vru_weight=_float_or(payload, "vru_weight", defaults.vru_weight),
        near_m=_float_or(payload, "near_m", defaults.near_m),
        mid_m=_float_or(payload, "mid_m", defaults.mid_m),
        near_weight=_float_or(payload, "near_weight", defaults.near_weight),
        mid_weight=_float_or(payload, "mid_weight", defaults.mid_weight),
        far_weight=_float_or(payload, "far_weight", defaults.far_weight),
        unknown_distance_weight=_float_or(
            payload, "unknown_distance_weight", defaults.unknown_distance_weight
        ),
        vru_within_m=_float_or(payload, "vru_within_m", defaults.vru_within_m),
        ghost_m=_float_or(payload, "ghost_m", defaults.ghost_m),
        duplicate_iou=_float_or(payload, "duplicate_iou", defaults.duplicate_iou),
        loc_centre_m=_float_or(payload, "loc_centre_m", defaults.loc_centre_m),
        loc_yaw_deg=_float_or(payload, "loc_yaw_deg", defaults.loc_yaw_deg),
        far_miss_m=_float_or(payload, "far_miss_m", defaults.far_miss_m),
        occluded_visibility_below=_float_or(
            payload, "occluded_visibility_below", defaults.occluded_visibility_below
        ),
    )


def declares_latency(document: Mapping[str, object]) -> bool:
    """True when a perturbation object delays other agents."""
    frames = document.get("latency_frames")
    millis = document.get("latency_ms")
    return _is_positive(frames) or _is_positive(millis)


def mine_failures(
    records: Sequence[OutcomeRecord],
    *,
    run_id: str,
    weights: FailureWeights | None = None,
    top: int = 50,
    latency_records: Sequence[OutcomeRecord] | None = None,
) -> dict[str, object]:
    """``failures.json`` for one run. ``top`` keeps the worst frames."""
    if top < 1:
        raise EvaluationError("--top must be at least 1")
    chosen = weights or FailureWeights()
    latency = _latency_index(latency_records) if latency_records is not None else None
    grouped: dict[tuple[str, int, str, str], list[OutcomeRecord]] = {}
    for record in records:
        if record.run_id != run_id:
            continue
        key = (record.scene_id, record.time_us, record.agent_id, record.source)
        grouped.setdefault(key, []).append(record)
    frames: list[dict[str, object]] = []
    for key in sorted(grouped):
        frame = _frame(key, grouped[key], chosen, latency)
        if frame is not None:
            frames.append(frame)
    frames.sort(
        key=lambda item: (
            -float(cast(float, item["badness"])),
            str(item["scene_id"]),
            int(cast(int, item["time_us"])),
            str(item["agent_id"]),
            str(item["source"]),
        )
    )
    ranked = frames[:top]
    for index, frame in enumerate(ranked, start=1):
        frame["rank"] = index
    return {
        "format": FAILURES_FORMAT,
        "format_version": FAILURES_VERSION,
        "run_id": run_id,
        "formula": FORMULA,
        "weights": chosen.to_json(),
        "latency_compared": latency is not None,
        "frames": ranked,
    }


def render_failures(document: Mapping[str, object]) -> str:
    """One plain line per ranked frame."""
    frames = document.get("frames")
    if not isinstance(frames, list):
        return ""
    lines: list[str] = []
    for item in cast(list[object], frames):
        if not isinstance(item, dict):
            continue
        frame = cast(dict[str, object], item)
        tags = frame.get("tags")
        tag_text = ""
        if isinstance(tags, list):
            tag_text = ",".join(str(tag) for tag in cast(list[object], tags))
        badness = frame.get("badness")
        number = 0.0
        if isinstance(badness, (int, float)) and not isinstance(badness, bool):
            number = float(badness)
        lines.append(
            f"{frame.get('rank')}\t{frame.get('scene_id')}\t{frame.get('time_us')}\t"
            f"{frame.get('agent_id')}/{frame.get('source')}\tbadness {number:.3f}\t{tag_text}"
        )
    return "\n".join(lines) + ("\n" if lines else "")


def write_failures(path: Path, document: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def _frame(
    key: tuple[str, int, str, str],
    records: Sequence[OutcomeRecord],
    weights: FailureWeights,
    latency: Mapping[tuple[str, str, str], str] | None,
) -> dict[str, object] | None:
    scene_id, time_us, agent_id, source = key
    gts = [record for record in records if record.kind == KIND_GT]
    preds = [record for record in records if record.kind == KIND_PREDICTION]
    misses = [record for record in gts if record.status == "miss"]
    falses = [record for record in preds if record.status == "fp"]
    badness = 0.0
    for record in (*misses, *falses):
        term = weights.class_weight(record.category) * weights.distance_weight(record.distance_m)
        badness += term
    evidence: dict[str, dict[str, object]] = {}
    _tag_misses(evidence, misses, weights)
    _tag_ghosts(evidence, falses, gts, weights)
    _tag_duplicates(evidence, falses, gts, weights)
    _tag_localisation(evidence, gts, weights)
    if source == "fused":
        _tag_fusion(evidence, gts)
    if latency is not None:
        _tag_latency(evidence, gts, latency)
    tags = [name for name in TAG_ORDER if name in evidence]
    if badness == 0.0 and not tags:
        return None
    involved = sorted({record_id for tag in evidence.values() for record_id in _ids(tag)})
    return {
        "rank": 0,
        "scene_id": scene_id,
        "time_us": time_us,
        "agent_id": agent_id,
        "source": source,
        "badness": _round(badness),
        "tags": tags,
        "object_ids": involved,
        "evidence": {name: evidence[name] for name in tags},
    }


def _tag_misses(
    evidence: dict[str, dict[str, object]], misses: Sequence[OutcomeRecord], weights: FailureWeights
) -> None:
    vru = [
        record
        for record in misses
        if record.category in weights.vru_classes
        and record.distance_m is not None
        and record.distance_m <= weights.vru_within_m
    ]
    far = [
        record
        for record in misses
        if record.distance_m is not None and record.distance_m >= weights.far_miss_m
    ]
    occluded = [
        record
        for record in misses
        if record.visibility is not None and record.visibility < weights.occluded_visibility_below
    ]
    if vru:
        evidence["missed_vru"] = _evidence(vru, distances=True)
    if far:
        evidence["far_range_miss"] = _evidence(far, distances=True)
    if occluded:
        evidence["occluded_miss"] = _evidence(occluded, distances=True)


def _tag_ghosts(
    evidence: dict[str, dict[str, object]],
    falses: Sequence[OutcomeRecord],
    gts: Sequence[OutcomeRecord],
    weights: FailureWeights,
) -> None:
    ghosts = [record for record in falses if _nearest_gt(record, gts) > weights.ghost_m]
    if ghosts:
        evidence["ghost_box"] = _evidence(ghosts, distances=True)


def _tag_duplicates(
    evidence: dict[str, dict[str, object]],
    falses: Sequence[OutcomeRecord],
    gts: Sequence[OutcomeRecord],
    weights: FailureWeights,
) -> None:
    matched = [record for record in gts if record.status == "tp"]
    duplicates = [record for record in falses if _overlaps_true_positive(record, matched, weights)]
    if duplicates:
        evidence["duplicate_boxes"] = _evidence(duplicates)


def _tag_localisation(
    evidence: dict[str, dict[str, object]], gts: Sequence[OutcomeRecord], weights: FailureWeights
) -> None:
    loose = [
        record
        for record in gts
        if record.status == "tp"
        and (
            (record.centre_error_m is not None and record.centre_error_m >= weights.loc_centre_m)
            or (record.yaw_error_deg is not None and record.yaw_error_deg >= weights.loc_yaw_deg)
        )
    ]
    if loose:
        evidence["localisation_error"] = _evidence(loose, errors=True)


def _tag_fusion(evidence: dict[str, dict[str, object]], gts: Sequence[OutcomeRecord]) -> None:
    lost = [
        record for record in gts if record.status_single == "tp" and record.status_fused == "miss"
    ]
    if lost:
        evidence["fusion_disagreement"] = _evidence(lost)


def _tag_latency(
    evidence: dict[str, dict[str, object]],
    gts: Sequence[OutcomeRecord],
    latency: Mapping[tuple[str, str, str], str],
) -> None:
    lost: list[OutcomeRecord] = []
    for record in gts:
        key = (record.key, record.source, record.scene_id)
        if record.status == "tp" and latency.get(key) == "miss":
            lost.append(record)
    if lost:
        evidence["latency_sensitive"] = _evidence(lost)


def _latency_index(records: Sequence[OutcomeRecord]) -> dict[tuple[str, str, str], str]:
    return {
        (record.key, record.source, record.scene_id): record.status
        for record in records
        if record.kind == KIND_GT
    }


def _evidence(
    records: Sequence[OutcomeRecord], *, distances: bool = False, errors: bool = False
) -> dict[str, object]:
    ordered = sorted(records, key=lambda record: record.key)
    body: dict[str, object] = {
        "count": len(ordered),
        "object_ids": [record.key for record in ordered],
        "distances_m": [_round(record.distance_m) for record in ordered] if distances else [],
        "centre_errors_m": [_round(record.centre_error_m) for record in ordered] if errors else [],
        "yaw_errors_deg": [_round(record.yaw_error_deg) for record in ordered] if errors else [],
    }
    return body


def _ids(evidence: Mapping[str, object]) -> list[str]:
    raw = evidence.get("object_ids")
    if not isinstance(raw, list):
        return []
    return [str(item) for item in cast(list[object], raw)]


def _nearest_gt(pred: OutcomeRecord, gts: Sequence[OutcomeRecord]) -> float:
    if not gts:
        return math.inf
    return min(_horizontal(pred.center, record.center) for record in gts)


def _horizontal(left: tuple[float, float, float], right: tuple[float, float, float]) -> float:
    return math.hypot(left[0] - right[0], left[1] - right[1])


def _overlaps_true_positive(
    pred: OutcomeRecord, matched: Sequence[OutcomeRecord], weights: FailureWeights
) -> bool:
    box = _world(pred)
    for record in matched:
        if record.category != pred.category:
            continue
        if bev_iou(box, _world(record)) >= weights.duplicate_iou:
            return True
    return False


def _world(record: OutcomeRecord) -> WorldBox:
    qw, qx, qy, qz = record.rotation
    rotation = SE3(qw, qx, qy, qz, 0.0, 0.0, 0.0)
    return WorldBox(center=record.center, size_lwh=record.size_lwh, rotation=rotation)


def _round(value: float | None) -> float | None:
    if value is None:
        return None
    rounded = round(float(value), 6)
    return 0.0 if rounded == 0.0 else rounded


def _required_float(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EvaluationError(f"{name} must be a number")
    number = float(value)
    if not math.isfinite(number):
        raise EvaluationError(f"{name} must be finite")
    return number


def _float_or(payload: Mapping[str, object], name: str, default: float) -> float:
    if name not in payload:
        return default
    return _required_float(payload[name], name)


def _is_positive(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return float(value) > 0.0
