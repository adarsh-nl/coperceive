# SPDX-License-Identifier: Apache-2.0
"""Differences between two results of the same :class:`~tests.evalcheck.scenario.RunSpec`.

Statuses, counts, pairings and null-ness must be equal. Floats must agree within
``tolerance`` (absolute). Every difference is one readable line that says where
it is, so a failing case explains itself.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping, Sequence

from tests.evalcheck.scenario import (
    ApResult,
    OutputResult,
    PooledResult,
    RunResult,
    SceneResult,
)

TOLERANCE = 1e-6


def _number(where: str, a: object, b: object, tolerance: float, out: list[str]) -> None:
    """One scalar: ints, strings, bools and null-ness exactly, floats within ``tolerance``."""
    if a is None or b is None:
        if a is not b:
            out.append(f"{where}: {a!r} != {b!r}")
        return
    if isinstance(a, float) or isinstance(b, float):
        if not isinstance(a, (int, float)) or not isinstance(b, (int, float)):
            out.append(f"{where}: {a!r} != {b!r}")
        elif abs(float(a) - float(b)) > tolerance:
            out.append(f"{where}: {a!r} != {b!r} (|Δ| = {abs(float(a) - float(b)):.3e})")
        return
    if a != b:
        out.append(f"{where}: {a!r} != {b!r}")


def _fields(where: str, a: object, b: object, tolerance: float, out: list[str]) -> None:
    """Every field of two dataclass instances of one type, as scalars."""
    for item in dataclasses.fields(a):  # type: ignore[arg-type]
        _number(
            f"{where}.{item.name}", getattr(a, item.name), getattr(b, item.name), tolerance, out
        )


def _mapping(
    where: str,
    a: Mapping[int, object] | Mapping[str, object],
    b: Mapping[int, object] | Mapping[str, object],
    tolerance: float,
    out: list[str],
) -> None:
    if set(a) != set(b):
        only_a = sorted(str(key) for key in set(a) - set(b))
        only_b = sorted(str(key) for key in set(b) - set(a))
        out.append(f"{where}: keys differ, only left {only_a}, only right {only_b}")
        return
    for key in sorted(a, key=str):
        _number(f"{where}[{key!r}]", a[key], b[key], tolerance, out)  # type: ignore[index]


def _ap(
    where: str, a: Sequence[ApResult], b: Sequence[ApResult], tol: float, out: list[str]
) -> None:
    if len(a) != len(b):
        out.append(f"{where}: {len(a)} thresholds != {len(b)}")
        return
    for index, (left, right) in enumerate(zip(a, b, strict=True)):
        _mapping(f"{where}[{index}].per_category", left.per_category, right.per_category, tol, out)
        _number(f"{where}[{index}].mean", left.mean, right.mean, tol, out)
        _number(f"{where}[{index}].mean_model", left.mean_model, right.mean_model, tol, out)


def diff_outputs(
    where: str, a: OutputResult, b: OutputResult, tolerance: float = TOLERANCE
) -> list[str]:
    """Differences between two results of one output."""
    out: list[str] = []
    _mapping(f"{where}.pred_status", a.pred_status, b.pred_status, tolerance, out)
    _mapping(f"{where}.gt_status", a.gt_status, b.gt_status, tolerance, out)
    if len(a.pred_status_at) != len(b.pred_status_at):
        out.append(f"{where}.pred_status_at: {len(a.pred_status_at)} != {len(b.pred_status_at)}")
    else:
        for index, (left, right) in enumerate(zip(a.pred_status_at, b.pred_status_at, strict=True)):
            _mapping(f"{where}.pred_status_at[{index}]", left, right, tolerance, out)
    pairs_a = {pair.pred_row: pair for pair in a.pairs}
    pairs_b = {pair.pred_row: pair for pair in b.pairs}
    if {(p.pred_row, p.gt_row) for p in a.pairs} != {(p.pred_row, p.gt_row) for p in b.pairs}:
        left = sorted((p.pred_row, p.gt_row) for p in a.pairs)
        right = sorted((p.pred_row, p.gt_row) for p in b.pairs)
        out.append(f"{where}.pairs: (pred_row, gt_row) {left} != {right}")
    else:
        for pred_row in sorted(pairs_a):
            _fields(
                f"{where}.pairs[pred {pred_row}]",
                pairs_a[pred_row],
                pairs_b[pred_row],
                tolerance,
                out,
            )
    if [s.time_us for s in a.samples] != [s.time_us for s in b.samples]:
        out.append(
            f"{where}.samples: times {[s.time_us for s in a.samples]} != "
            f"{[s.time_us for s in b.samples]}"
        )
    else:
        for left, right in zip(a.samples, b.samples, strict=True):
            _fields(f"{where}.samples[{left.time_us}]", left, right, tolerance, out)
    _fields(f"{where}.totals", a.totals, b.totals, tolerance, out)
    _ap(f"{where}.ap", a.ap, b.ap, tolerance, out)
    _fields(f"{where}.tp_errors", a.tp_errors, b.tp_errors, tolerance, out)
    _fields(f"{where}.cooperative", a.cooperative, b.cooperative, tolerance, out)
    if set(a.gt_cooperative) != set(b.gt_cooperative):
        out.append(f"{where}.gt_cooperative: rows differ")
    else:
        for row in sorted(a.gt_cooperative):
            _fields(
                f"{where}.gt_cooperative[{row}]",
                a.gt_cooperative[row],
                b.gt_cooperative[row],
                tolerance,
                out,
            )
    return out


def diff_scenes(
    a: Sequence[SceneResult], b: Sequence[SceneResult], tolerance: float = TOLERANCE
) -> list[str]:
    """Differences between two lists of per-scene results."""
    out: list[str] = []
    if [scene.scene_id for scene in a] != [scene.scene_id for scene in b]:
        out.append(f"scenes: {[s.scene_id for s in a]} != {[s.scene_id for s in b]}")
        return out
    for left, right in zip(a, b, strict=True):
        keys_left = [(o.agent_id, o.source) for o in left.outputs]
        keys_right = [(o.agent_id, o.source) for o in right.outputs]
        if keys_left != keys_right:
            out.append(f"{left.scene_id}: outputs {keys_left} != {keys_right}")
            continue
        for output_left, output_right in zip(left.outputs, right.outputs, strict=True):
            where = f"{left.scene_id}/{output_left.agent_id}/{output_left.source}"
            out.extend(diff_outputs(where, output_left, output_right, tolerance))
    return out


def diff_pooled(
    a: Sequence[PooledResult], b: Sequence[PooledResult], tolerance: float = TOLERANCE
) -> list[str]:
    """Differences between two lists of headline rows."""
    out: list[str] = []
    keys_a = [(row.agent_id, row.source) for row in a]
    keys_b = [(row.agent_id, row.source) for row in b]
    if keys_a != keys_b:
        out.append(f"pooled: rows {keys_a} != {keys_b}")
        return out
    for left, right in zip(a, b, strict=True):
        where = f"pooled[{left.agent_id}/{left.source}]"
        for name in (
            "gt",
            "predictions",
            "tp",
            "fp",
            "miss",
            "precision",
            "recall",
            "recovered",
            "lost_by_fusion",
        ):
            _number(f"{where}.{name}", getattr(left, name), getattr(right, name), tolerance, out)
        _ap(f"{where}.ap", left.ap, right.ap, tolerance, out)
    return out


def diff_runs(a: RunResult, b: RunResult, tolerance: float = TOLERANCE) -> list[str]:
    """Differences between two full results: per scene, then the headline rows."""
    return [
        *diff_scenes(a.scenes, b.scenes, tolerance),
        *diff_pooled(a.pooled, b.pooled, tolerance),
    ]
