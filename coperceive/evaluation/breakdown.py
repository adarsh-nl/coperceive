# SPDX-License-Identifier: Apache-2.0
"""Comparison of 2–4 evaluated runs, with breakdowns and a per-object table.

The legacy two-run Markdown table stays :func:`coperceive.evaluation.report.render_compare`.
This module builds the machine-readable ``compare.json`` and the extra Markdown
sections a caller asks for with ``--by``. Average precision is the same pooled
curve as ``eval report``: ground truth and predictions are filtered into a bin,
then :func:`coperceive.evaluation.metrics.average_precision` walks that subset.

A bin with no ground truth says ``no objects``. It is never reported as AP 0.
Every AP carries the number of ground-truth objects in the bin.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import cast

from coperceive.evaluation.config import threshold_key
from coperceive.evaluation.evaluate import KIND_GT, KIND_PREDICTION, EvaluationError
from coperceive.evaluation.metrics import average_precision
from coperceive.evaluation.outcomes import (
    DEFAULT_DISTANCE_EDGES,
    DEFAULT_VISIBILITY_EDGES,
    OutcomeRecord,
    distance_bin,
    distance_bin_names,
    occlusion_bin_names,
    occlusion_bucket,
)
from coperceive.evaluation.report import Row, aggregate, ap_column, render_report

COMPARE_FORMAT = "coperceive-compare"
COMPARE_VERSION = 1
BREAKDOWNS = ("distance", "occlusion", "agents", "class", "scene")
NO_OBJECTS = "no objects"

DEFINITIONS = {
    "distance": (
        "Horizontal metres from the reference agent (the first agent in the scene) "
        "to the box centre at that box's time. The reference agent is the one "
        "CooperativeDataset uses when no agent list is passed."
    ),
    "occlusion": (
        "From the ground-truth visibility fraction. Null visibility is unknown. "
        "The default edges 0.2 and 0.6 name heavy, partial and clear. A value "
        "outside [0, 1] keeps its own visibility:<value> label."
    ),
    "agents": (
        "How many agents annotate the object at that time, including the "
        "annotating agent. Pairing is the cooperative gate (shared track, or a "
        "computed pair within 2 m). 2+ means the match table only recorded that "
        "another agent sees it."
    ),
    "reference_agent": "The first agent in scene.agents.",
}


def build_compare(
    runs: Sequence[tuple[str, Sequence[OutcomeRecord]]],
    *,
    thresholds: Sequence[float],
    breakdowns: Sequence[str],
    distance_edges: Sequence[float] = DEFAULT_DISTANCE_EDGES,
    visibility_edges: Sequence[float] = DEFAULT_VISIBILITY_EDGES,
    distance_source: str,
) -> dict[str, object]:
    """The ``compare.json`` document. ``runs`` is ``(run_id, records)`` in caller order."""
    unknown = [name for name in breakdowns if name not in BREAKDOWNS]
    if unknown:
        raise EvaluationError(
            f"unknown breakdown {unknown[0]!r}; choose from {', '.join(BREAKDOWNS)}"
        )
    _check_status_width(runs, len(thresholds))
    outputs = _outputs(runs)
    document: dict[str, object] = {
        "format": COMPARE_FORMAT,
        "format_version": COMPARE_VERSION,
        "runs": [run_id for run_id, _records in runs],
        "thresholds": [float(threshold) for threshold in thresholds],
        "distance_edges_m": [float(edge) for edge in distance_edges],
        "visibility_edges": [float(edge) for edge in visibility_edges],
        "breakdowns": list(breakdowns),
        "distance_source": distance_source,
        "definitions": dict(DEFINITIONS),
        "outputs": [
            _output_block(
                agent_id,
                source,
                runs,
                thresholds=thresholds,
                breakdowns=breakdowns,
                distance_edges=distance_edges,
                visibility_edges=visibility_edges,
            )
            for agent_id, source in outputs
        ],
        "objects": _objects(runs, distance_edges=distance_edges, visibility_edges=visibility_edges),
    }
    return document


def write_compare(path: Path, document: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def render_breakdowns(document: Mapping[str, object]) -> str:
    """Markdown for the breakdowns in ``document``. Empty bins say ``no objects``."""
    thresholds = _floats(document.get("thresholds"))
    names = _strings(document.get("breakdowns"))
    outputs = document.get("outputs")
    if not isinstance(outputs, list):
        return ""
    blocks: list[str] = []
    for output in cast(list[object], outputs):
        if not isinstance(output, dict):
            continue
        item = cast(dict[str, object], output)
        agent = str(item.get("agent_id") or "")
        source = str(item.get("source") or "")
        breakdowns = item.get("breakdowns")
        if not isinstance(breakdowns, dict):
            continue
        table = cast(dict[str, object], breakdowns)
        for name in names:
            rows = table.get(name)
            if not isinstance(rows, list):
                continue
            blocks.append(
                _markdown_section(name, agent, source, cast(list[object], rows), thresholds)
            )
    return "\n".join(blocks)


def render_multi(runs: Sequence[tuple[str, Sequence[Row]]], thresholds: Sequence[float]) -> str:
    """One summary table of each run's pooled totals, then each run's full report."""
    columns = ["gt", "tp", "fp", "miss", *(ap_column(threshold) for threshold in thresholds)]
    lines = [
        "| run | " + " | ".join(columns) + " |",
        "|" + "|".join(" --- " for _ in range(len(columns) + 1)) + "|",
    ]
    for run_id, rows in runs:
        totals = aggregate(rows, thresholds)
        cells = [_markdown_cell(column, totals.get(column)) for column in columns]
        lines.append("| " + " | ".join([run_id, *cells]) + " |")
    parts = ["\n".join(lines) + "\n"]
    for run_id, rows in runs:
        parts.append(f"## {run_id}\n\n" + render_report(rows, thresholds))
    return "\n".join(parts)


def _markdown_cell(column: str, value: object) -> str:
    if "AP@" in column:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return "n/a"
        return f"{float(value):.3f}"
    if isinstance(value, bool) or not isinstance(value, int):
        return "n/a"
    return str(value)


def _output_block(
    agent_id: str,
    source: str,
    runs: Sequence[tuple[str, Sequence[OutcomeRecord]]],
    *,
    thresholds: Sequence[float],
    breakdowns: Sequence[str],
    distance_edges: Sequence[float],
    visibility_edges: Sequence[float],
) -> dict[str, object]:
    selected = [(run_id, _of_output(records, agent_id, source)) for run_id, records in runs]
    blocks: dict[str, object] = {}
    for name in breakdowns:
        bins = _bin_names(name, selected, distance_edges, visibility_edges)
        blocks[name] = [
            _bin_block(
                name,
                label,
                selected,
                thresholds=thresholds,
                distance_edges=distance_edges,
                visibility_edges=visibility_edges,
            )
            for label in bins
        ]
    return {"agent_id": agent_id, "source": source, "breakdowns": blocks}


def _bin_block(
    dimension: str,
    label: str,
    runs: Sequence[tuple[str, Sequence[OutcomeRecord]]],
    *,
    thresholds: Sequence[float],
    distance_edges: Sequence[float],
    visibility_edges: Sequence[float],
) -> dict[str, object]:
    per_run: dict[str, object] = {}
    cells: list[dict[str, dict[str, object]]] = []
    for run_id, records in runs:
        gts, preds = _split(records, dimension, label, distance_edges, visibility_edges)
        aps: dict[str, object] = {}
        one: dict[str, dict[str, object]] = {}
        for index, threshold in enumerate(thresholds):
            cell = _ap_cell(gts, preds, index)
            key = threshold_key(threshold)
            aps[key] = cell
            one[key] = cell
        per_run[run_id] = {"n_objects": _kept(gts), "ap": aps}
        cells.append(one)
    delta: object = None
    if len(runs) == 2:
        delta = _delta(cells[0], cells[1], thresholds)
    return {"bin": label, "runs": per_run, "delta_b_minus_a": delta}


def _ap_cell(
    gts: Sequence[OutcomeRecord], preds: Sequence[OutcomeRecord], threshold_index: int
) -> dict[str, object]:
    kept = [record for record in gts if record.status != "ignored"]
    n_objects = len(kept)
    categories = sorted(
        {record.category for record in kept}
        | {record.category for record in preds if record.category}
    )
    ordered = sorted(preds, key=lambda record: (record.scene_id, record.row))
    by_class: dict[str, object] = {}
    values: list[float] = []
    for category in categories:
        n_gt = sum(1 for record in kept if record.category == category)
        scored = [
            (record.score if record.score is not None else 0.0, record.status_at[threshold_index])
            for record in ordered
            if record.category == category and threshold_index < len(record.status_at)
        ]
        ap = average_precision(scored, n_gt)
        note = NO_OBJECTS if n_gt == 0 else None
        by_class[category] = {"ap": _num(ap), "n_objects": n_gt, "note": note}
        if ap is not None:
            values.append(ap)
    if n_objects == 0:
        mean: float | None = None
        note = NO_OBJECTS
    else:
        mean = _num(sum(values) / len(values)) if values else None
        note = None
    return {"mean": mean, "n_objects": n_objects, "note": note, "by_class": by_class}


def _delta(
    left: Mapping[str, Mapping[str, object]],
    right: Mapping[str, Mapping[str, object]],
    thresholds: Sequence[float],
) -> dict[str, object]:
    out: dict[str, object] = {}
    for threshold in thresholds:
        key = threshold_key(threshold)
        a = left.get(key, {})
        b = right.get(key, {})
        out[key] = {
            "mean": _subtract(a.get("mean"), b.get("mean")),
            "by_class": _class_delta(a.get("by_class"), b.get("by_class")),
        }
    return out


def _class_delta(left: object, right: object) -> dict[str, object]:
    a: dict[str, object] = cast(dict[str, object], left) if isinstance(left, dict) else {}
    b: dict[str, object] = cast(dict[str, object], right) if isinstance(right, dict) else {}
    names = sorted(set(a) | set(b))
    out: dict[str, object] = {}
    for name in names:
        left_cell = cast(dict[str, object], a.get(name, {}))
        right_cell = cast(dict[str, object], b.get(name, {}))
        out[name] = _subtract(left_cell.get("ap"), right_cell.get("ap"))
    return out


def _subtract(left: object, right: object) -> float | None:
    if isinstance(left, bool) or isinstance(right, bool):
        return None
    if not isinstance(left, (int, float)) or not isinstance(right, (int, float)):
        return None
    return _num(float(right) - float(left))


def _split(
    records: Sequence[OutcomeRecord],
    dimension: str,
    label: str,
    distance_edges: Sequence[float],
    visibility_edges: Sequence[float],
) -> tuple[list[OutcomeRecord], list[OutcomeRecord]]:
    lookup = _pair_lookup(records)
    gts = [
        record
        for record in records
        if record.kind == KIND_GT
        and _bin(record, dimension, distance_edges, visibility_edges) == label
    ]
    preds: list[OutcomeRecord] = []
    for record in records:
        if record.kind != KIND_PREDICTION:
            continue
        assigned = _assigned(record, lookup, dimension, distance_edges, visibility_edges)
        if assigned == label:
            preds.append(record)
    return gts, preds


PairKey = tuple[str, str, str, int]


def _pair_lookup(records: Sequence[OutcomeRecord]) -> dict[PairKey, OutcomeRecord]:
    found: dict[PairKey, OutcomeRecord] = {}
    for record in records:
        if record.kind == KIND_GT and record.pair_id is not None:
            found[(record.scene_id, record.agent_id, record.source, record.pair_id)] = record
    return found


def _assigned(
    record: OutcomeRecord,
    lookup: Mapping[PairKey, OutcomeRecord],
    dimension: str,
    distance_edges: Sequence[float],
    visibility_edges: Sequence[float],
) -> str:
    if record.pair_id is not None:
        matched = lookup.get((record.scene_id, record.agent_id, record.source, record.pair_id))
        if matched is not None:
            return _bin(matched, dimension, distance_edges, visibility_edges)
    return _bin(record, dimension, distance_edges, visibility_edges)


def _bin(
    record: OutcomeRecord,
    dimension: str,
    distance_edges: Sequence[float],
    visibility_edges: Sequence[float],
) -> str:
    if dimension == "distance":
        return distance_bin(record.distance_m, distance_edges)
    if dimension == "occlusion":
        # A prediction has no visibility of its own. An unmatched one is unknown.
        visibility = record.visibility if record.kind == KIND_GT else None
        return occlusion_bucket(visibility, visibility_edges)
    if dimension == "agents":
        return record.agent_bin if record.kind == KIND_GT else "unknown"
    if dimension == "class":
        return record.category
    if dimension == "scene":
        return record.scene_id
    raise EvaluationError(f"unknown breakdown {dimension!r}")


def _bin_names(
    dimension: str,
    runs: Sequence[tuple[str, Sequence[OutcomeRecord]]],
    distance_edges: Sequence[float],
    visibility_edges: Sequence[float],
) -> tuple[str, ...]:
    seen: list[str] = []
    for _run_id, records in runs:
        lookup = _pair_lookup(records)
        for record in records:
            if record.kind == KIND_GT:
                label = _bin(record, dimension, distance_edges, visibility_edges)
            elif record.kind == KIND_PREDICTION:
                label = _assigned(record, lookup, dimension, distance_edges, visibility_edges)
            else:
                continue
            if label not in seen:
                seen.append(label)
    if dimension == "distance":
        names = list(distance_bin_names(distance_edges))
        if "unknown" in seen:
            names.append("unknown")
        return tuple(names)
    if dimension == "occlusion":
        names = list(occlusion_bin_names(visibility_edges))
        extras = sorted(label for label in seen if label not in names and label != "unknown")
        if "unknown" in seen or True:
            # unknown is always listed so an empty visibility column says no objects
            names.append("unknown")
        names.extend(extras)
        return tuple(dict.fromkeys(names))
    if dimension == "agents":
        numeric = sorted({label for label in seen if label.isdigit()}, key=int)
        rest = [label for label in ("2+", "unknown") if label in seen]
        return tuple([*numeric, *rest])
    return tuple(sorted(seen))


def _objects(
    runs: Sequence[tuple[str, Sequence[OutcomeRecord]]],
    *,
    distance_edges: Sequence[float],
    visibility_edges: Sequence[float],
) -> list[dict[str, object]]:
    grouped: dict[str, list[OutcomeRecord]] = {}
    order: list[str] = []
    fps: list[OutcomeRecord] = []
    for _run_id, records in runs:
        for record in records:
            if record.kind == KIND_GT:
                if record.key not in grouped:
                    grouped[record.key] = []
                    order.append(record.key)
                grouped[record.key].append(record)
            elif record.kind == KIND_PREDICTION and record.status == "fp":
                fps.append(record)
    objects = [_gt_object(grouped[key], distance_edges, visibility_edges) for key in sorted(order)]
    fps.sort(key=lambda record: record.key)
    objects.extend(_fp_object(record, distance_edges) for record in fps)
    return objects


def _gt_object(
    records: Sequence[OutcomeRecord],
    distance_edges: Sequence[float],
    visibility_edges: Sequence[float],
) -> dict[str, object]:
    first = records[0]
    distance = next(
        (record.distance_m for record in records if record.distance_m is not None), None
    )
    visibility = next(
        (record.visibility for record in records if record.visibility is not None), None
    )
    counted = next((record for record in records if record.agent_count is not None), None)
    if counted is not None and counted.agent_count is not None:
        agent_count: int | None = counted.agent_count
        agent_bin = str(counted.agent_count)
    else:
        agent_count = None
        agent_bin = first.agent_bin
    runs: dict[str, dict[str, object]] = {}
    for record in records:
        runs.setdefault(record.run_id, {})[record.source] = {
            "outcome": _gt_outcome(record.status),
            "iou_bev": _num(record.iou_bev),
            "centre_error_m": _num(record.centre_error_m),
            "yaw_error_deg": _num(record.yaw_error_deg),
        }
    return {
        "key": first.key,
        "kind": "gt",
        "scene_id": first.scene_id,
        "agent_id": first.agent_id,
        "source": None,
        "run_id": None,
        "time_us": first.time_us,
        "row": first.row,
        "track_id": first.track_id,
        "category": first.category,
        "score": None,
        "distance_m": _num(distance),
        "occlusion": occlusion_bucket(visibility, visibility_edges),
        "visibility": _num(visibility),
        "agent_count": agent_count,
        "agent_bin": agent_bin,
        "outcome": None,
        "iou_bev": None,
        "centre_error_m": None,
        "yaw_error_deg": None,
        "runs": runs,
        "distance_bin": distance_bin(distance, distance_edges),
    }


def _fp_object(record: OutcomeRecord, distance_edges: Sequence[float]) -> dict[str, object]:
    return {
        "key": record.key,
        "kind": "fp",
        "scene_id": record.scene_id,
        "agent_id": record.agent_id,
        "source": record.source,
        "run_id": record.run_id,
        "time_us": record.time_us,
        "row": record.row,
        "track_id": record.track_id,
        "category": record.category,
        "score": _num(record.score),
        "distance_m": _num(record.distance_m),
        "occlusion": "unknown",
        "visibility": None,
        "agent_count": None,
        "agent_bin": "unknown",
        "outcome": "fp",
        "iou_bev": _num(record.iou_bev),
        "centre_error_m": _num(record.centre_error_m),
        "yaw_error_deg": _num(record.yaw_error_deg),
        "runs": None,
        "distance_bin": distance_bin(record.distance_m, distance_edges),
    }


def _gt_outcome(status: str) -> str:
    if status == "tp":
        return "tp"
    if status == "miss":
        return "fn"
    return "ignored"


def _of_output(
    records: Sequence[OutcomeRecord], agent_id: str, source: str
) -> tuple[OutcomeRecord, ...]:
    return tuple(
        record for record in records if record.agent_id == agent_id and record.source == source
    )


def _outputs(runs: Sequence[tuple[str, Sequence[OutcomeRecord]]]) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for _run_id, records in runs:
        for record in records:
            key = (record.agent_id, record.source)
            if key not in found:
                found.append(key)
    return sorted(found)


def _kept(records: Sequence[OutcomeRecord]) -> int:
    return sum(1 for record in records if record.status != "ignored")


def _check_status_width(
    runs: Sequence[tuple[str, Sequence[OutcomeRecord]]], n_thresholds: int
) -> None:
    if n_thresholds == 0:
        return
    for run_id, records in runs:
        for record in records:
            if record.kind == KIND_PREDICTION and len(record.status_at) < n_thresholds:
                raise EvaluationError(
                    f"run {run_id!r} scene {record.scene_id!r} has no status_at_"
                    f"{len(record.status_at)} column; run `coperceive eval run` again"
                )


def _markdown_section(
    name: str,
    agent: str,
    source: str,
    rows: Sequence[object],
    thresholds: Sequence[float],
) -> str:
    header = ["bin", "class", "run", "n_objects", *(f"AP@{threshold_key(t)}" for t in thresholds)]
    lines = [
        f"## {name}",
        "",
        f"{agent} / {source}",
        "",
        "| " + " | ".join(header) + " |",
        "|" + "|".join(" --- " for _ in header) + "|",
    ]
    for row in rows:
        if not isinstance(row, dict):
            continue
        item = cast(dict[str, object], row)
        label = str(item.get("bin"))
        per_run = item.get("runs")
        if not isinstance(per_run, dict):
            continue
        per_run_map = cast(dict[str, object], per_run)
        for run_id, cell in per_run_map.items():
            if not isinstance(cell, dict):
                continue
            body = cast(dict[str, object], cell)
            ap = body.get("ap")
            if not isinstance(ap, dict):
                continue
            ap_map = cast(dict[str, object], ap)
            classes = _classes_of(ap_map, thresholds)
            for class_name in classes:
                values = [_ap_text(ap_map, threshold, class_name) for threshold in thresholds]
                n_objects = _n_text(ap_map, thresholds, class_name, body.get("n_objects"))
                lines.append(
                    "| " + " | ".join([label, class_name, str(run_id), n_objects, *values]) + " |"
                )
    return "\n".join(lines) + "\n"


def _classes_of(ap_map: Mapping[str, object], thresholds: Sequence[float]) -> list[str]:
    names: set[str] = set()
    for threshold in thresholds:
        cell = ap_map.get(threshold_key(threshold))
        if isinstance(cell, dict):
            raw = cast(dict[str, object], cell).get("by_class")
            if isinstance(raw, dict):
                names.update(str(key) for key in cast(dict[str, object], raw))
    return ["all", *sorted(names)]


def _ap_text(ap_map: Mapping[str, object], threshold: float, class_name: str) -> str:
    cell = ap_map.get(threshold_key(threshold))
    if not isinstance(cell, dict):
        return "n/a"
    item = cast(dict[str, object], cell)
    if class_name == "all":
        if item.get("note") == NO_OBJECTS:
            return NO_OBJECTS
        return _format_ap(item.get("mean"))
    raw_classes = item.get("by_class")
    if not isinstance(raw_classes, dict) or class_name not in raw_classes:
        return "n/a"
    entry = cast(dict[str, object], raw_classes)[class_name]
    if not isinstance(entry, dict):
        return "n/a"
    typed = cast(dict[str, object], entry)
    if typed.get("note") == NO_OBJECTS:
        return NO_OBJECTS
    return _format_ap(typed.get("ap"))


def _n_text(
    ap_map: Mapping[str, object],
    thresholds: Sequence[float],
    class_name: str,
    bin_count: object,
) -> str:
    if not thresholds:
        return "0"
    cell = ap_map.get(threshold_key(thresholds[0]))
    if not isinstance(cell, dict):
        return "0"
    item = cast(dict[str, object], cell)
    if class_name == "all":
        count = item.get("n_objects")
        return str(count) if isinstance(count, int) else str(bin_count or 0)
    raw_classes = item.get("by_class")
    if isinstance(raw_classes, dict):
        classes = cast(dict[str, object], raw_classes)
        entry = classes.get(class_name)
        if not isinstance(entry, dict):
            return "0"
        count = cast(dict[str, object], entry).get("n_objects")
        if isinstance(count, int):
            return str(count)
    return "0"


def _format_ap(value: object) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "n/a"
    return f"{float(value):.3f}"


def _num(value: float | None) -> float | None:
    if value is None:
        return None
    rounded = round(float(value), 6)
    return 0.0 if rounded == 0.0 else rounded


def _floats(value: object) -> tuple[float, ...]:
    if not isinstance(value, list):
        return ()
    out: list[float] = []
    for item in cast(list[object], value):
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            continue
        out.append(float(item))
    return tuple(out)


def _strings(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(str(item) for item in cast(list[object], value) if isinstance(item, str))
