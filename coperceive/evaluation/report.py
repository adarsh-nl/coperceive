# SPDX-License-Identifier: Apache-2.0
"""``coperceive eval report`` and ``eval compare`` tables (spec §``metrics.json``).

One row per scene and output with the counts, precision, recall, the AP mean per
threshold and the two cooperative counts; then one ``all`` row per output and a
final ``all`` row over every output. The ``all`` rows sum the counts, recompute
precision and recall from the sums, and take their ``AP@<t>`` from the **pooled**
curve (§Metrics, Pooled AP): every prediction of every scene of the run, one
precision/recall walk per class, ``n_gt`` summed over scenes, then the mean over
the classes that have a value. A second table lists that pooled AP per class.
``compare`` prints the same tables with ``b − a`` deltas over every row of
either run, ``n/a`` where a run lacks the row, and a footer with the counts.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from coperceive.evaluation.config import threshold_key
from coperceive.evaluation.evaluate import (
    KIND_GT,
    KIND_PREDICTION,
    METRICS_FILE,
    read_matches,
    read_metrics,
    scene_output_dir,
    status_at_column,
    threshold_count,
)
from coperceive.evaluation.matching import IGNORED
from coperceive.evaluation.metrics import average_precision
from coperceive.evaluation.predictions import check_run_id, read_run_meta
from coperceive.viewdata import scene_directories

AGGREGATE_LABEL = "all"
MISSING = "n/a"
COUNT_KEYS = ("gt", "predictions", "tp", "fp", "miss")
COOPERATIVE_KEYS = ("recovered", "lost_by_fusion")
POOL_COLUMNS = ("kind", "agent_id", "source", "category", "row", "score", "status")

Record = tuple[float, str]
OutputKey = tuple[str, str]


@dataclass(frozen=True)
class ApPool:
    """What one scene and output contributes to the pooled AP (§Metrics, Pooled AP).

    ``categories`` are the keys of the output's ``ap`` table (the union of
    ``run.json``, ground truth and predictions); ``n_gt`` the non-ignored
    ground-truth boxes per category; ``records`` per threshold index, per
    category, the ``(score, status_at_<i>)`` of every prediction in
    ``boxes.parquet`` row order.
    """

    categories: tuple[str, ...]
    n_gt: Mapping[str, int]
    records: Mapping[int, Mapping[str, tuple[Record, ...]]]


@dataclass(frozen=True)
class Row:
    """One scene and output of a run.

    ``values`` holds ``gt``, ``predictions``, ``tp``, ``fp``, ``miss``,
    ``precision``, ``recall``, one ``AP@<t>`` entry per threshold (the per-scene
    mean over categories, or ``None``), ``recovered``, ``lost_by_fusion`` and
    ``thresholds`` (the run's thresholds, so a caller can render without
    reopening ``metrics.json``). ``pool`` is what the row adds to the pooled AP;
    a row without one adds nothing.
    """

    scene_id: str
    agent_id: str
    source: str
    values: Mapping[str, object]
    run_id: str = ""
    pool: ApPool | None = None
    model_categories: tuple[str, ...] = ()

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.scene_id, self.agent_id, self.source)

    @property
    def output(self) -> OutputKey:
        return (self.agent_id, self.source)


def ap_column(threshold: float) -> str:
    """Main-table column: the pooled mean AP over every class with ground truth."""
    return f"mAP@{threshold_key(threshold)} all"


def model_ap_column(threshold: float) -> str:
    """Main-table column: the pooled mean AP over the run's ``run.json`` categories."""
    return f"mAP@{threshold_key(threshold)} model"


def class_ap_column(threshold: float) -> str:
    """Per-class table column."""
    return f"AP@{threshold_key(threshold)}"


def collect(scenes_root: Path, predictions_root: Path, run_id: str) -> tuple[Row, ...]:
    """Rows of every scene under ``scenes_root`` that ``run_id`` has evaluated, in name order."""
    check_run_id(run_id)
    model_categories = tuple(read_run_meta(predictions_root, run_id).categories)
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
        pools = _pools(directory, threshold_count(metrics))
        for item in cast(Sequence[object], metrics.get("outputs", ())):
            output = _dict(item)
            totals = _dict(output.get("totals"))
            ap = _dict(output.get("ap"))
            cooperative = _dict(output.get("cooperative"))
            values: dict[str, object] = {key: totals.get(key) for key in COUNT_KEYS}
            values["precision"] = totals.get("precision")
            values["recall"] = totals.get("recall")
            for threshold in thresholds:
                entry = _dict(ap.get(threshold_key(threshold)))
                values[ap_column(threshold)] = entry.get("mean")
                values[model_ap_column(threshold)] = entry.get("mean_model")
            for key in COOPERATIVE_KEYS:
                values[key] = cooperative.get(key)
            values["thresholds"] = thresholds
            agent_id = str(output.get("agent_id"))
            source = str(output.get("source"))
            partial = pools.get((agent_id, source))
            pool = None
            if partial is not None:
                first = _dict(ap.get(threshold_key(thresholds[0]))) if thresholds else {}
                categories = tuple(
                    sorted(key for key in first if key not in {"mean", "mean_model"})
                )
                pool = ApPool(
                    categories=categories,
                    n_gt=partial.n_gt,
                    records={
                        index: {
                            category: tuple(records) for category, records in by_category.items()
                        }
                        for index, by_category in partial.records.items()
                    },
                )
            rows.append(
                Row(
                    scene_id=scene_dir.name,
                    agent_id=agent_id,
                    source=source,
                    values=values,
                    run_id=run_id,
                    pool=pool,
                    model_categories=model_categories,
                )
            )
    return tuple(rows)


@dataclass
class _PartialPool:
    n_gt: dict[str, int]
    records: dict[int, dict[str, list[Record]]]


def _pools(directory: Path, n_thresholds: int) -> dict[OutputKey, _PartialPool]:
    """Per output of the scene's match table: ``n_gt`` per category and the AP records."""
    status_columns = [status_at_column(index) for index in range(n_thresholds)]
    table = read_matches(directory, columns=[*POOL_COLUMNS, *status_columns])
    pools: dict[OutputKey, _PartialPool] = {}
    items = sorted(
        table.to_pylist(),
        key=lambda item: cast(int, item["row"]) if item["row"] is not None else -1,
    )
    for item in items:
        key = (str(item["agent_id"]), str(item["source"]))
        pool = pools.setdefault(key, _PartialPool(n_gt={}, records={}))
        category = str(item["category"])
        if item["kind"] == KIND_GT:
            pool.n_gt.setdefault(category, 0)
            if item["status"] != IGNORED:
                pool.n_gt[category] += 1
        elif item["kind"] == KIND_PREDICTION:
            for index, column in enumerate(status_columns):
                pool.records.setdefault(index, {}).setdefault(category, []).append(
                    (float(cast(float, item["score"])), str(item[column]))
                )
    return pools


def _dict(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    return {str(key): item for key, item in cast(dict[object, object], value).items()}


def _columns(thresholds: Sequence[float]) -> list[str]:
    return [
        *COUNT_KEYS,
        "precision",
        "recall",
        *(c for t in thresholds for c in (ap_column(t), model_ap_column(t))),
        *COOPERATIVE_KEYS,
    ]


def _is_ratio(column: str) -> bool:
    return column in {"precision", "recall"} or "AP@" in column


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


def partial_note(predictions_root: Path, rows: Sequence[Row]) -> str | None:
    """``partial: N of M scenes`` when the run's metrics say it stopped early, else None."""
    if not rows:
        return None
    first = rows[0]
    metrics = read_metrics(scene_output_dir(predictions_root, first.run_id, first.scene_id))
    if metrics.get("run_status") != "partial":
        return None
    missing = metrics.get("missing_scenes")
    count = len(cast(Sequence[object], missing)) if isinstance(missing, Sequence) else 0
    evaluated = len({row.scene_id for row in rows})
    return f"partial: {evaluated} of {evaluated + count} scenes"


def outputs_of(rows: Sequence[Row]) -> list[OutputKey]:
    """The ``(agent_id, source)`` outputs of ``rows`` in first-appearance order."""
    return list(dict.fromkeys(row.output for row in rows))


def pooled_ap(
    rows: Sequence[Row], thresholds: Sequence[float]
) -> dict[str, dict[str, float | None]]:
    """§Metrics, Pooled AP: per threshold key, per category, the AP of the pooled curve.

    The categories are the union over the rows' pools. For each, the records of
    every row are concatenated in row order (scenes in name order, then
    ``boxes.parquet`` order) and ``n_gt`` is summed; one
    :func:`~coperceive.evaluation.metrics.average_precision` walk gives the value,
    ``None`` when the pooled ``n_gt`` is 0. Rows without a pool add nothing.
    """
    pools = [row.pool for row in rows if row.pool is not None]
    categories = sorted({category for pool in pools for category in pool.categories})
    table: dict[str, dict[str, float | None]] = {}
    for index, threshold in enumerate(thresholds):
        per_category: dict[str, float | None] = {}
        for category in categories:
            records: list[Record] = []
            n_gt = 0
            for pool in pools:
                records.extend(pool.records.get(index, {}).get(category, ()))
                n_gt += pool.n_gt.get(category, 0)
            per_category[category] = average_precision(records, n_gt)
        table[threshold_key(threshold)] = per_category
    return table


def aggregate(rows: Sequence[Row], thresholds: Sequence[float]) -> dict[str, object]:
    """Sums of the counts, precision and recall from the sums, and the pooled ``AP@<t>``.

    Each ``AP@<t>`` is the mean of :func:`pooled_ap` over the categories that have
    a value, ``None`` when none has. A count a row lacks adds 0.
    """
    out: dict[str, object] = {}
    for column in (*COUNT_KEYS, *COOPERATIVE_KEYS):
        out[column] = sum(_int(row.values.get(column)) for row in rows)
    tp, fp, gt = _int(out["tp"]), _int(out["fp"]), _int(out["gt"])
    out["precision"] = None if tp + fp == 0 else tp / (tp + fp)
    out["recall"] = None if gt == 0 else tp / gt
    pooled = pooled_ap(rows, thresholds)
    model_categories = {c for row in rows for c in row.model_categories}
    for threshold in thresholds:
        per_category = pooled[threshold_key(threshold)]
        values = [v for v in per_category.values() if v is not None]
        out[ap_column(threshold)] = sum(values) / len(values) if values else None
        model = [v for c, v in per_category.items() if c in model_categories and v is not None]
        out[model_ap_column(threshold)] = sum(model) / len(model) if model else None
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


def _of_output(rows: Sequence[Row], output: OutputKey) -> list[Row]:
    return [row for row in rows if row.output == output]


def render_report(rows: Sequence[Row], thresholds: Sequence[float]) -> str:
    """The two Markdown tables of §``metrics.json``'s last paragraph.

    The main table: one line per row, one ``all`` line per output (its scenes
    pooled) and the ``all`` line over everything. Then, after a blank line, the
    pooled AP per class: ``agent``, ``source``, ``class`` and one ``AP@<t>`` per
    threshold, per output and then for ``all``.
    """
    columns = _columns(thresholds)
    lines: list[list[str]] = []
    for row in rows:
        lines.append(
            [row.scene_id, row.agent_id, row.source]
            + [_cell(column, row.values.get(column)) for column in columns]
        )
    for output in outputs_of(rows):
        total = aggregate(_of_output(rows, output), thresholds)
        lines.append([AGGREGATE_LABEL, *output] + [_cell(c, total.get(c)) for c in columns])
    total = aggregate(rows, thresholds)
    lines.append([AGGREGATE_LABEL, "", ""] + [_cell(c, total.get(c)) for c in columns])
    main = _table(["scene", "agent", "source", *columns], lines)

    class_lines: list[list[str]] = []
    for output in outputs_of(rows):
        class_lines.extend(_class_lines(list(output), _of_output(rows, output), thresholds))
    class_lines.extend(_class_lines([AGGREGATE_LABEL, ""], rows, thresholds))
    return main + "\n" + _table(_class_header(thresholds), class_lines)


def _class_header(thresholds: Sequence[float]) -> list[str]:
    return ["agent", "source", "class", *(class_ap_column(t) for t in thresholds)]


def _class_lines(
    label: Sequence[str], rows: Sequence[Row], thresholds: Sequence[float]
) -> list[list[str]]:
    pooled = pooled_ap(rows, thresholds)
    categories = sorted({c for per_category in pooled.values() for c in per_category})
    return [
        [*label, category]
        + [_cell("AP@", pooled[threshold_key(t)].get(category)) for t in thresholds]
        for category in categories
    ]


def _class_delta_lines(
    label: Sequence[str],
    rows_a: Sequence[Row],
    rows_b: Sequence[Row],
    thresholds: Sequence[float],
) -> list[list[str]]:
    pooled_a = pooled_ap(rows_a, thresholds)
    pooled_b = pooled_ap(rows_b, thresholds)
    categories = sorted(
        {c for per_category in (*pooled_a.values(), *pooled_b.values()) for c in per_category}
    )
    return [
        [*label, category]
        + [
            _delta_cell(
                "AP@",
                pooled_a[threshold_key(t)].get(category),
                pooled_b[threshold_key(t)].get(category),
            )
            for t in thresholds
        ]
        for category in categories
    ]


def render_compare(
    rows_a: Sequence[Row], rows_b: Sequence[Row], thresholds: Sequence[float]
) -> str:
    """The same tables with ``b − a`` deltas over every row of either run.

    ``a``'s rows come first in ``a``'s order, then the rows only ``b`` has. A row
    the other run lacks shows ``n/a`` in every cell, as does a ratio that is
    ``null`` on either side. The ``all`` lines (one per output, one over every
    output) are the deltas of the two pooled aggregates over the shared rows
    only, so an output with no shared row is ``n/a`` throughout. A footer under
    the main table counts the rows: ``rows: <n> shared, <n> only in <a>, <n>
    only in <b>``, naming the runs by the rows' ``run_id`` (``a`` and ``b`` when
    the rows carry none). The per-class table follows, over the shared rows.
    """
    columns = _columns(thresholds)
    missing = [MISSING] * len(columns)
    by_key_a = {row.key: row for row in rows_a}
    by_key_b = {row.key: row for row in rows_b}
    shared_a: list[Row] = []
    shared_b: list[Row] = []
    lines: list[list[str]] = []
    for row in rows_a:
        other = by_key_b.get(row.key)
        if other is None:
            lines.append([row.scene_id, row.agent_id, row.source, *missing])
            continue
        shared_a.append(row)
        shared_b.append(other)
        lines.append(
            [row.scene_id, row.agent_id, row.source]
            + [_delta_cell(c, row.values.get(c), other.values.get(c)) for c in columns]
        )
    only_b = [row for row in rows_b if row.key not in by_key_a]
    lines.extend([row.scene_id, row.agent_id, row.source, *missing] for row in only_b)
    shared_outputs = set(outputs_of(shared_a))
    for output in outputs_of([*rows_a, *rows_b]):
        if output not in shared_outputs:
            lines.append([AGGREGATE_LABEL, *output, *missing])
            continue
        total_a = aggregate(_of_output(shared_a, output), thresholds)
        total_b = aggregate(_of_output(shared_b, output), thresholds)
        lines.append(
            [AGGREGATE_LABEL, *output]
            + [_delta_cell(c, total_a.get(c), total_b.get(c)) for c in columns]
        )
    total_a = aggregate(shared_a, thresholds)
    total_b = aggregate(shared_b, thresholds)
    lines.append(
        [AGGREGATE_LABEL, "", ""]
        + [_delta_cell(c, total_a.get(c), total_b.get(c)) for c in columns]
    )
    main = _table(["scene", "agent", "source", *columns], lines)
    footer = (
        f"rows: {len(shared_a)} shared, {len(rows_a) - len(shared_a)} only in "
        f"{_run_label(rows_a, 'a')}, {len(only_b)} only in {_run_label(rows_b, 'b')}\n"
    )

    class_lines: list[list[str]] = []
    for output in outputs_of(shared_a):
        class_lines.extend(
            _class_delta_lines(
                list(output),
                _of_output(shared_a, output),
                _of_output(shared_b, output),
                thresholds,
            )
        )
    class_lines.extend(_class_delta_lines([AGGREGATE_LABEL, ""], shared_a, shared_b, thresholds))
    return main + footer + "\n" + _table(_class_header(thresholds), class_lines)


def _run_label(rows: Sequence[Row], fallback: str) -> str:
    """The ``run_id`` the rows carry, or ``fallback`` when none does."""
    return next((row.run_id for row in rows if row.run_id), fallback)
