# SPDX-License-Identifier: Apache-2.0
"""Evaluation view data for the in-app viewer (``docs/spec/predictions.md`` §HTTP).

The three bodies the page fetches — the runs of a scene, the match rows at one
sample and the per-sample series — built from ``run.json``, ``matches.parquet``
and ``metrics.json`` under a predictions root. This module does not import a
web framework; ``coperceive.app.server`` maps :class:`EvalViewError` to an HTTP
status. Every path goes through the confine check of
:func:`coperceive.evaluation.evaluate.scene_output_dir` after the ``run_id`` and
``scene_id`` pass the scene-id rule. A ``run_id`` or ``scene_id`` that fails the
rule is a 400; a run, a scene without predictions or a scene that has not been
evaluated is a 404. Nothing here writes.

The match rows of one ``(run_id, scene_id)`` are kept in a small per-process
cache keyed by the ``mtime_ns`` of ``matches.parquet``, so scrubbing through a
scene reads the file once and a re-evaluation is picked up without a restart.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import cast

from coperceive.errors import CoperceiveError
from coperceive.evaluation.evaluate import (
    MATCHES_FILE,
    METRICS_FILE,
    EvaluationError,
    read_matches,
    read_metrics,
    scene_output_dir,
)
from coperceive.evaluation.predictions import (
    BOXES_FILE,
    RUN_FILE,
    PredictionError,
    check_run_id,
    check_scene_id,
    list_runs,
    read_run_meta,
)
from coperceive.se3 import SE3
from coperceive.viewdata import box_lines

CACHE_LIMIT = 8
"""How many ``(run_id, scene_id)`` match tables stay in memory; the oldest goes first."""

SERIES_KEYS = ("tp", "fp", "miss", "precision", "recall")
RUN_OUTPUT_KEYS = ("agent_id", "source", "totals", "ap", "cooperative")


class EvalViewError(CoperceiveError):
    """A view request that cannot be answered; ``status`` is the HTTP status it maps to."""

    def __init__(self, message: str, *, status: int) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class _CacheEntry:
    path: Path
    rows: tuple[dict[str, object], ...]


_cache: dict[tuple[str, str, int], _CacheEntry] = {}


# --- bodies -----------------------------------------------------------------------


def runs_for_scene(root: Path, scene_id: str) -> dict[str, object]:
    """The ``/predictions`` body: every run with ``<scene_id>/boxes.parquet`` under ``root``."""
    _check(scene_id=scene_id)
    runs: list[dict[str, object]] = []
    for run_id in list_runs(root):
        try:
            directory = scene_output_dir(root, run_id, scene_id)
        except PredictionError:
            continue  # a run directory that resolves outside the root is not listed
        if not (directory / BOXES_FILE).is_file():
            continue
        meta = read_run_meta(root, run_id)
        evaluated = (directory / METRICS_FILE).is_file()
        config: object = None
        outputs: list[dict[str, object]] = []
        if evaluated:
            metrics = read_metrics(directory)
            config = metrics.get("config")
            outputs = [
                {key: output.get(key) for key in RUN_OUTPUT_KEYS} for output in _outputs(metrics)
            ]
        runs.append(
            {
                "run_id": run_id,
                "run": meta.to_json(),
                "evaluated": evaluated,
                "config": config,
                "outputs": outputs,
            }
        )
    return {"runs": runs}


def frame_rows(
    root: Path, run_id: str, scene_id: str, time_us: int, *, compare: str | None
) -> dict[str, object]:
    """The ``/frame`` body: the ``matches.parquet`` rows of ``run_id`` at ``time_us``.

    Each row carries the §``matches.parquet`` columns plus ``center``, ``size_lwh``,
    ``pose`` and ``lines`` (the 24-vertex edge list the scene frame uses for boxes).
    ``compare`` names a second run whose rows are returned under ``"compare"``.
    """
    body = _frame(root, run_id, scene_id, time_us)
    body["compare"] = _frame(root, compare, scene_id, time_us) if compare is not None else None
    return body


def series(root: Path, run_id: str, scene_id: str, *, compare: str | None) -> dict[str, object]:
    """The ``/series`` body: per output, the per-sample counts of ``metrics.json`` as lists."""
    body = _series(root, run_id, scene_id)
    body["compare"] = _series(root, compare, scene_id) if compare is not None else None
    return body


# --- cache ------------------------------------------------------------------------


def clear_cache() -> None:
    """Forget every cached match table (tests, and a server that changes root)."""
    _cache.clear()


def cache_keys() -> tuple[tuple[str, str, int], ...]:
    """The ``(run_id, scene_id, mtime_ns)`` keys currently cached, oldest first."""
    return tuple(_cache)


def _match_rows(run_id: str, scene_id: str, directory: Path) -> tuple[dict[str, object], ...]:
    path = directory / MATCHES_FILE
    try:
        mtime_ns = path.stat().st_mtime_ns
    except OSError as error:
        raise EvalViewError(
            f"run {run_id!r} has no {MATCHES_FILE} for scene {scene_id!r}: "
            f"run `coperceive eval run {run_id}`",
            status=404,
        ) from error
    key = (run_id, scene_id, mtime_ns)
    entry = _cache.get(key)
    if entry is not None and entry.path == path:
        return entry.rows
    rows = tuple(
        cast(dict[str, object], dict(item)) for item in read_matches(directory).to_pylist()
    )
    for stale in [k for k in _cache if k[:2] == key[:2]]:
        del _cache[stale]
    while len(_cache) >= CACHE_LIMIT:
        del _cache[next(iter(_cache))]
    _cache[key] = _CacheEntry(path=path, rows=rows)
    return rows


# --- helpers ----------------------------------------------------------------------


def _check(*, run_id: str | None = None, scene_id: str | None = None) -> None:
    try:
        if run_id is not None:
            check_run_id(run_id)
        if scene_id is not None:
            check_scene_id(scene_id)
    except PredictionError as error:
        raise EvalViewError(str(error), status=400) from error


def _evaluated_dir(root: Path, run_id: str, scene_id: str) -> Path:
    """``<root>/<run_id>/<scene_id>`` when the run exists, has boxes and has been evaluated."""
    _check(run_id=run_id, scene_id=scene_id)
    try:
        directory = scene_output_dir(root, run_id, scene_id)
    except PredictionError as error:
        raise EvalViewError(str(error), status=400) from error
    if not (root / run_id / RUN_FILE).is_file():
        raise EvalViewError(f"no run {run_id!r} under the predictions root", status=404)
    if not (directory / BOXES_FILE).is_file():
        raise EvalViewError(f"run {run_id!r} has no predictions for scene {scene_id!r}", status=404)
    if not (directory / METRICS_FILE).is_file():
        raise EvalViewError(
            f"run {run_id!r} is not evaluated for scene {scene_id!r}: "
            f"run `coperceive eval run {run_id}`",
            status=404,
        )
    return directory


def _frame(root: Path, run_id: str, scene_id: str, time_us: int) -> dict[str, object]:
    directory = _evaluated_dir(root, run_id, scene_id)
    metrics = read_metrics(directory)
    config = cast(dict[str, object], metrics.get("config") or {})
    matches = _match_rows(run_id, scene_id, directory)
    rows = [_row_json(row) for row in matches if row["time_us"] == time_us]
    return {
        "run_id": run_id,
        "time_us": time_us,
        "display_threshold": config.get("display_threshold"),
        "score_threshold": config.get("score_threshold"),
        "rows": rows,
    }


def _series(root: Path, run_id: str, scene_id: str) -> dict[str, object]:
    directory = _evaluated_dir(root, run_id, scene_id)
    metrics = read_metrics(directory)
    outputs: list[dict[str, object]] = []
    for output in _outputs(metrics):
        samples = cast(list[dict[str, object]], output.get("samples") or [])
        item: dict[str, object] = {
            "agent_id": output.get("agent_id"),
            "source": output.get("source"),
            "times_us": [sample.get("time_us") for sample in samples],
        }
        for key in SERIES_KEYS:
            item[key] = [sample.get(key) for sample in samples]
        outputs.append(item)
    return {"run_id": run_id, "outputs": outputs}


def _outputs(metrics: dict[str, object]) -> list[dict[str, object]]:
    return cast(list[dict[str, object]], metrics.get("outputs") or [])


def _row_json(row: dict[str, object]) -> dict[str, object]:
    center = (_float(row["cx"]), _float(row["cy"]), _float(row["cz"]))
    size = (_float(row["length"]), _float(row["width"]), _float(row["height"]))
    rotation = SE3(
        _float(row["qw"]), _float(row["qx"]), _float(row["qy"]), _float(row["qz"]), 0.0, 0.0, 0.0
    )
    out: dict[str, object] = dict(row)
    out["center"] = list(center)
    out["size_lwh"] = list(size)
    out["pose"] = {
        "qw": rotation.qw,
        "qx": rotation.qx,
        "qy": rotation.qy,
        "qz": rotation.qz,
        "tx": center[0],
        "ty": center[1],
        "tz": center[2],
    }
    out["lines"] = box_lines(center, size, rotation)
    return out


def _float(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EvaluationError(f"{MATCHES_FILE}: box column is not a number: {value!r}")
    return float(value)
