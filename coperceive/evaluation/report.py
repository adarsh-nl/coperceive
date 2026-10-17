# SPDX-License-Identifier: Apache-2.0
"""``coperceive eval report`` and ``eval compare`` tables (spec §``metrics.json``).

One row per scene and output with the counts, precision, recall, the AP mean per
threshold and the two cooperative counts, then an aggregate row ``all`` that sums
the counts, recomputes precision and recall from the sums and averages the AP
means over the rows that have one. ``compare`` prints the same table with
``b − a`` deltas for the rows both runs have.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from coperceive.evaluation.config import threshold_key
from coperceive.evaluation.evaluate import METRICS_FILE, read_metrics, scene_output_dir
from coperceive.evaluation.predictions import check_run_id
from coperceive.viewdata import scene_directories

AGGREGATE_LABEL = "all"
MISSING = "n/a"
COUNT_KEYS = ("gt", "predictions", "tp", "fp", "miss")
COOPERATIVE_KEYS = ("recovered", "lost_by_fusion")


@dataclass(frozen=True)
class Row:
    """One scene and output of a run.

    ``values`` holds ``gt``, ``predictions``, ``tp``, ``fp``, ``miss``,
    ``precision``, ``recall``, one ``AP@<t>`` entry per threshold (the mean over
    categories, or ``None``), ``recovered``, ``lost_by_fusion`` and
    ``thresholds`` (the run's thresholds, so a caller can render without
    reopening ``metrics.json``).
    """

    scene_id: str
    agent_id: str
    source: str
    values: Mapping[str, object]

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.scene_id, self.agent_id, self.source)


def ap_column(threshold: float) -> str:
    return f"AP@{threshold_key(threshold)}"


def collect(scenes_root: Path, predictions_root: Path, run_id: str) -> tuple[Row, ...]:
    """Rows of every scene under ``scenes_root`` that ``run_id`` has evaluated, in name order."""
    check_run_id(run_id)
    rows: list[Row] = []
    for scene_dir in scene_directories(scenes_root):
        directory = scene_output_dir(predictions_root, run_id, scene_dir.name)
        if not (directory / METRICS_FILE).is_file():
            continue
        metrics = read_metrics(directory)
        config = _dict(metrics.get("config"))
        thresholds = tuple(
            float(cast(float, t)) for t in cast(Sequence[object], config.get("thresholds", ()))
        )
        for item in cast(Sequence[object], metrics.get("outputs", ())):
            output = _dict(item)
            totals = _dict(output.get("totals"))
            ap = _dict(output.get("ap"))
            cooperative = _dict(output.get("cooperative"))
            values: dict[str, object] = {key: totals.get(key) for key in COUNT_KEYS}
            values["precision"] = totals.get("precision")
            values["recall"] = totals.get("recall")
            for threshold in thresholds:
                values[ap_column(threshold)] = _dict(ap.get(threshold_key(threshold))).get("mean")
            for key in COOPERATIVE_KEYS:
                values[key] = cooperative.get(key)
            values["thresholds"] = thresholds
            rows.append(
                Row(
                    scene_id=scene_dir.name,
                    agent_id=str(output.get("agent_id")),
                    source=str(output.get("source")),
                    values=values,
                )
            )
    return tuple(rows)


def _dict(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    return {str(key): item for key, item in cast(dict[object, object], value).items()}


def _columns(thresholds: Sequence[float]) -> list[str]:
    return [
        *COUNT_KEYS,
        "precision",
        "recall",
        *(ap_column(t) for t in thresholds),
        *COOPERATIVE_KEYS,
    ]


def _is_ratio(column: str) -> bool:
    return column in {"precision", "recall"} or column.startswith("AP@")


def _count(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _int(value: object) -> int:
    return _count(value) or 0


def _float(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def aggregate(rows: Sequence[Row], thresholds: Sequence[float]) -> dict[str, object]:
    """Sums of the counts, precision and recall from the sums, AP means over rows that have one.

    A row is one scene and output, so each ``AP@<t>`` of the aggregate is the mean
    of the per-row (per scene, per output) means over the rows whose mean is not
    ``null``; a scene with two outputs weighs twice. A count a row lacks adds 0.
    """
    out: dict[str, object] = {}
    for column in (*COUNT_KEYS, *COOPERATIVE_KEYS):
        out[column] = sum(_int(row.values.get(column)) for row in rows)
    tp, fp, gt = _int(out["tp"]), _int(out["fp"]), _int(out["gt"])
    out["precision"] = None if tp + fp == 0 else tp / (tp + fp)
    out["recall"] = None if gt == 0 else tp / gt
    for threshold in thresholds:
        column = ap_column(threshold)
        values = [v for v in (_float(row.values.get(column)) for row in rows) if v is not None]
        out[column] = sum(values) / len(values) if values else None
    return out


def _cell(column: str, value: object) -> str:
    if _is_ratio(column):
        number = _float(value)
        return MISSING if number is None else f"{number:.3f}"
    count = _count(value)
    return MISSING if count is None else str(count)


def _delta_cell(column: str, a: object, b: object) -> str:
    if _is_ratio(column):
        left, right = _float(a), _float(b)
        if left is None or right is None:
            return MISSING
        return f"{right - left:+.3f}"
    left_count, right_count = _count(a), _count(b)
    if left_count is None or right_count is None:
        return MISSING
    return f"{right_count - left_count:+d}"


def _table(header: Sequence[str], lines: Sequence[Sequence[str]]) -> str:
    out = ["| " + " | ".join(header) + " |", "|" + "|".join(" --- " for _ in header) + "|"]
    for line in lines:
        out.append("| " + " | ".join(line) + " |")
    return "\n".join(out) + "\n"


def render_report(rows: Sequence[Row], thresholds: Sequence[float]) -> str:
    """The Markdown table of §``metrics.json``'s last paragraph, with the ``all`` row."""
    columns = _columns(thresholds)
    lines: list[list[str]] = []
    for row in rows:
        lines.append(
            [row.scene_id, row.agent_id, row.source]
            + [_cell(column, row.values.get(column)) for column in columns]
        )
    total = aggregate(rows, thresholds)
    lines.append([AGGREGATE_LABEL, "", ""] + [_cell(c, total.get(c)) for c in columns])
    return _table(["scene", "agent", "source", *columns], lines)


def render_compare(
    rows_a: Sequence[Row], rows_b: Sequence[Row], thresholds: Sequence[float]
) -> str:
    """The same table with ``b − a`` deltas, over the rows both runs have, in ``a``'s order.

    A ratio that is ``null`` on either side shows ``n/a``. The ``all`` row is the
    delta of the two aggregates over those shared rows.
    """
    columns = _columns(thresholds)
    by_key = {row.key: row for row in rows_b}
    shared_a: list[Row] = []
    shared_b: list[Row] = []
    lines: list[list[str]] = []
    for row in rows_a:
        other = by_key.get(row.key)
        if other is None:
            continue
        shared_a.append(row)
        shared_b.append(other)
        lines.append(
            [row.scene_id, row.agent_id, row.source]
            + [_delta_cell(c, row.values.get(c), other.values.get(c)) for c in columns]
        )
    total_a = aggregate(shared_a, thresholds)
    total_b = aggregate(shared_b, thresholds)
    lines.append(
        [AGGREGATE_LABEL, "", ""]
        + [_delta_cell(c, total_a.get(c), total_b.get(c)) for c in columns]
    )
    return _table(["scene", "agent", "source", *columns], lines)
