# SPDX-License-Identifier: Apache-2.0
"""``coperceive robustness sweep``: a grid of seeded perturbations of one model and split.

A sweep is a cartesian grid of perturbation axes (``pose_noise_xy: [0, 0.2, 0.5]``)
crossed with a list of seeds. Every cell is one ``coperceive model run --perturb``
of the same model over the same scenes, followed by the evaluation of that run
under every eval config; the mAP of a cell is the pooled AP that ``coperceive eval
report`` prints as its headline (the ``mAP@<t> all`` column of ``metrics.json``).

Cells are the product of the axes and the seeds, generated in a fixed order: the
axes in the canonical perturbation order, the values ascending, the seeds
ascending. The run id of a cell is deterministic from the manifest digest, the
model, the full spec and every option that changes the predictions, so re-running
the same sweep reuses the same run directories: the scenes are skipped, and with
``--force`` they are re-emitted from the per-scene cache with no child work.

``sweep.json`` (format ``coperceive-robustness-sweep`` version 1, schema
``docs/spec/robustness-sweep.schema.json``) holds, with no wall-clock timestamp so
the same command writes the same bytes:

* ``cells`` -- one per (perturbation combination, seed) with the full spec, its
  canonical text, the run id and the mAP of every eval config;
* ``aggregates`` -- one per perturbation combination, mean, sample standard
  deviation and count over the seeds;
* ``charts`` -- for every swept axis except ``max_agents``, the points of mAP
  against that axis with every *other* swept axis at its neutral value 0, plus
  the all-neutral baseline. ``max_agents`` is excluded because 0 is outside its
  valid range; a grid that sweeps it produces no neutral slice for the others.
"""

from __future__ import annotations

import hashlib
import json
import math
import statistics
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

import yaml

from coperceive.errors import CoperceiveError
from coperceive.evaluation.config import DEFAULT_CONFIG, EvalConfig, config_json
from coperceive.evaluation.evaluate import evaluate_run
from coperceive.evaluation.predictions import PredictionError, check_run_id
from coperceive.evaluation.report import aggregate, ap_column, collect
from coperceive.models.batch import BatchReport, RunOptions, run_batch, utc_now
from coperceive.models.slurm import plan_sbatch, write_plan
from coperceive.models.spec import Manifest, ModelSpec, manifest_sha256, select_model
from coperceive.robustness.spec import PerturbationError, PerturbationSpec
from coperceive.store import read_scene
from coperceive.viewdata import scene_directories

SWEEP_FILE = "sweep.json"
SWEEP_FORMAT = "coperceive-robustness-sweep"
SWEEP_VERSION = 1
DEFAULT_MAX_CELLS = 64

#: The perturbation fields a sweep can vary, in the canonical spec order.
AXES: tuple[str, ...] = (
    "latency_frames",
    "latency_ms",
    "packet_loss",
    "pose_noise_xy",
    "pose_noise_yaw",
    "max_agents",
    "agent_dropout",
)
SEED_KEY = "seeds"

UNITS: Mapping[str, str] = {
    "latency_frames": "frames",
    "latency_ms": "ms",
    "packet_loss": "fraction",
    "pose_noise_xy": "m",
    "pose_noise_yaw": "deg",
    "max_agents": "agents",
    "agent_dropout": "fraction",
}

AxisValue = int | float


class SweepError(CoperceiveError):
    """A user error of ``coperceive robustness sweep``."""


# --- the grid -------------------------------------------------------------------------


@dataclass(frozen=True)
class SweepAxis:
    """One varied perturbation field and its ascending values."""

    name: str
    values: tuple[AxisValue, ...]


@dataclass(frozen=True)
class SweepCell:
    """One point of the grid: the axis values and the seed that fixes every draw."""

    index: int
    values: Mapping[str, AxisValue]
    spec: PerturbationSpec


@dataclass(frozen=True)
class SweepGrid:
    """The parsed sweep definition: the axes and the seeds crossed with them."""

    axes: tuple[SweepAxis, ...]
    seeds: tuple[int, ...]

    @property
    def combination_count(self) -> int:
        count = 1
        for axis in self.axes:
            count *= len(axis.values)
        return count

    @property
    def cell_count(self) -> int:
        return self.combination_count * len(self.seeds)

    def combinations(self) -> tuple[dict[str, AxisValue], ...]:
        """Every cartesian point of the axes, in the canonical axis order."""
        rows: list[dict[str, AxisValue]] = [{}]
        for axis in self.axes:
            rows = [{**row, axis.name: value} for row in rows for value in axis.values]
        return tuple(rows)

    def cells(self) -> tuple[SweepCell, ...]:
        """Every (combination, seed), combination-major, seeds ascending."""
        cells: list[SweepCell] = []
        index = 0
        for row in self.combinations():
            for seed in self.seeds:
                cells.append(SweepCell(index, dict(row), _spec_for(row, seed)))
                index += 1
        return tuple(cells)


def load_grid(text: str) -> SweepGrid:
    """Parse a YAML (or JSON, which is YAML) sweep definition."""
    stripped = text.strip()
    if not stripped:
        raise SweepError("the sweep definition is empty")
    try:
        payload: object = yaml.safe_load(stripped)
    except yaml.YAMLError as error:
        raise SweepError(f"the sweep definition is not YAML: {error}") from None
    return parse_grid(payload)


def parse_grid(payload: object) -> SweepGrid:
    """Validate a decoded sweep definition: a mapping of axes to value lists and seeds."""
    if not isinstance(payload, Mapping):
        raise SweepError(
            "the sweep definition must be a mapping of perturbation axes to value lists, "
            "for example pose_noise_xy: [0, 0.5] with seeds: [0, 1]"
        )
    node = cast(Mapping[object, object], payload)
    known = set(AXES) | {SEED_KEY}
    unknown = [key for key in node if key not in known]
    if unknown:
        names = ", ".join(AXES + (SEED_KEY,))
        raise SweepError(f"unknown sweep key {str(unknown[0])!r}; sweep keys are {names}")
    axes: list[SweepAxis] = []
    for name in AXES:
        if name in node:
            axes.append(SweepAxis(name, _axis_values(name, node[name])))
    if not axes:
        names = ", ".join(AXES)
        raise SweepError(
            f"the sweep definition has no perturbation axes; add at least one of {names}"
        )
    seeds = _seeds(node.get(SEED_KEY, [0]))
    return SweepGrid(tuple(axes), seeds)


def check_cell_limit(grid: SweepGrid, max_cells: int) -> None:
    """Refuse a grid whose runs exceed ``max_cells``, naming the count and its factors."""
    if max_cells < 1:
        raise SweepError("--max-cells must be an integer of at least 1")
    count = grid.cell_count
    if count > max_cells:
        raise SweepError(
            f"the sweep has {count} cells ({grid.combination_count} perturbation "
            f"combinations x {len(grid.seeds)} seeds), over the limit of {max_cells}; "
            "reduce the grid or raise it with --max-cells"
        )


def _axis_values(name: str, raw: object) -> tuple[AxisValue, ...]:
    if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence):
        raise SweepError(
            f"sweep axis {name!r} must be a list of numbers, for example {name}: [0, 1]"
        )
    values: list[AxisValue] = []
    for item in cast(Sequence[object], raw):
        value = _axis_value(name, item)
        if any(value == seen for seen in values):
            raise SweepError(f"sweep axis {name!r} repeats the value {value!r}")
        values.append(value)
    if not values:
        raise SweepError(f"sweep axis {name!r} has no values")
    return tuple(sorted(values, key=float))


def _axis_value(name: str, raw: object) -> AxisValue:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise SweepError(f"sweep axis {name!r} value {raw!r} is not a number")
    try:
        spec = PerturbationSpec.from_json({name: raw, "seed": 0})
    except PerturbationError as error:
        raise SweepError(f"sweep axis {name!r} value {raw!r}: {error}") from None
    value = getattr(spec, name)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SweepError(f"sweep axis {name!r} value {raw!r} is not a number")
    if not math.isfinite(float(value)):
        raise SweepError(f"sweep axis {name!r} value {raw!r} is not finite")
    return value


def _seeds(raw: object) -> tuple[int, ...]:
    if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence):
        raise SweepError("sweep seeds must be a list of integers, for example seeds: [0, 1, 2]")
    items = cast(Sequence[object], raw)
    if not items:
        raise SweepError("sweep seeds has no values")
    seeds: list[int] = []
    for item in items:
        if isinstance(item, bool) or not isinstance(item, int):
            raise SweepError(f"sweep seed {item!r} is not an integer")
        if item < 0:
            raise SweepError(f"sweep seed {item} is negative")
        if item in seeds:
            raise SweepError(f"sweep seed {item} is repeated")
        seeds.append(item)
    return tuple(sorted(seeds))


def _spec_for(values: Mapping[str, AxisValue], seed: int) -> PerturbationSpec:
    try:
        return PerturbationSpec.from_json({**dict(values), "seed": seed})
    except PerturbationError as error:  # the values were validated with a seed of 0
        raise SweepError(f"perturbation for seed {seed}: {error}") from None


# --- the run --------------------------------------------------------------------------


@dataclass(frozen=True)
class EvalChoice:
    """One eval config of the sweep: the name it is recorded under and the config."""

    name: str
    config: EvalConfig
    path: str | None = None


@dataclass(frozen=True)
class SlurmSettings:
    """The site values ``--slurm`` needs, the same ones ``model run --slurm`` takes."""

    python: Path
    logs_dir: Path
    results_dir: Path
    partition: str
    scratch_template: str


@dataclass(frozen=True)
class SweepOptions:
    """Everything one sweep run needs; the CLI fills it from its arguments."""

    manifest: Manifest
    scenes: Path
    out: Path
    run_id: str
    grid: SweepGrid
    evals: tuple[EvalChoice, ...]
    model: str | None = None
    scene_ids: tuple[str, ...] | None = None
    agent_ids: tuple[str, ...] | None = None
    source: str | None = None
    raw_root: Path | None = None
    media_roots: tuple[Path, ...] = ()
    options: Mapping[str, str] = field(default_factory=lambda: {})
    frame_timeout_s: float | None = None
    run_timeout_s: float | None = None
    cache: bool = True
    hash_inputs: bool = False
    work_dir: Path | None = None
    force: bool = False
    max_cells: int = DEFAULT_MAX_CELLS
    slurm: SlurmSettings | None = None


@dataclass(frozen=True)
class EvalResult:
    """The mAP of one cell under one eval config, in the same shape for every cell."""

    criterion: str
    thresholds: tuple[float, ...]
    display_threshold: float
    maps: Mapping[str, float | None]  # the metrics.json field name -> pooled mAP

    @property
    def display_field(self) -> str:
        return ap_column(self.display_threshold)

    @property
    def display_value(self) -> float | None:
        return self.maps[self.display_field]


@dataclass(frozen=True)
class SweepCellResult:
    """One cell: its run id, the batch report and the mAP per eval config."""

    cell: SweepCell
    run_id: str
    report: BatchReport
    evaluations: Mapping[str, EvalResult]


@dataclass(frozen=True)
class Stats:
    """Mean, sample standard deviation and count over the seeds of one combination."""

    mean: float | None
    std: float | None
    n: int

    def to_json(self) -> dict[str, object]:
        return {"mean": self.mean, "std": self.std, "n": self.n}


@dataclass(frozen=True)
class SweepAggregate:
    """One perturbation combination: the stats over its seeds per eval config."""

    index: int
    values: Mapping[str, AxisValue]
    evaluations: Mapping[str, Mapping[str, Stats]]


@dataclass(frozen=True)
class SweepResult:
    """What a sweep wrote, plus the per-cell reports the tests and the CLI inspect."""

    document: Mapping[str, object]
    cells: tuple[SweepCellResult, ...]
    aggregates: tuple[SweepAggregate, ...]
    path: Path


def run_sweep(
    options: SweepOptions, *, progress: Callable[[str], None] | None = None
) -> SweepResult:
    """Run every cell, evaluate it, and write ``<out>/sweep.json``."""
    model = _prepare(options)
    cells = options.grid.cells()
    options.out.mkdir(parents=True, exist_ok=True)
    created_utc = utc_now()
    results: list[SweepCellResult] = []
    for cell in cells:
        run_id = run_id_for(options, model, cell.spec)
        cell_options = _run_options(options, model, cell.spec, run_id)
        report = run_batch(
            options.manifest,
            options.scenes,
            options.out,
            run_id,
            cell_options,
            created_utc=created_utc,
        )
        if report.exit_code != 0:
            raise SweepError(
                f"cell {cell.index} ({cell.spec.canonical()}) run {run_id} failed: "
                f"{report.status}: {report.reason}"
            )
        result = SweepCellResult(cell, run_id, report, _evaluate_cell(options, run_id))
        results.append(result)
        if progress is not None:
            progress(_progress_line(result, len(cells), options.evals))
    aggregates = _aggregates(options, tuple(results))
    document = _document(options, model, tuple(results), aggregates)
    path = options.out / SWEEP_FILE
    _write_json(path, document)
    return SweepResult(document, tuple(results), aggregates, path)


def emit_slurm(options: SweepOptions) -> tuple[Path, ...]:
    """Write one sbatch per cell under ``<out>/.slurm/``; nothing is submitted.

    Each script is the existing ``model run --slurm`` plan for that cell's spec, so it
    stages exactly the scene files the scenes need to the profile's scratch, runs the
    same ``model run --perturb`` there, and copies the run home before the trap wipes
    the scratch; results never live in the scratch. The sweep writes ``sweep.json``
    when it is run again without ``--slurm`` once the jobs have copied their runs home.
    """
    if options.slurm is None:
        raise SweepError("--slurm needs the site settings of SlurmSettings")
    model = _prepare(options)
    directories = _scene_directories(options.scenes, options.scene_ids)
    selected = _selected(directories, options.scene_ids)
    scenes = {scene_id: read_scene(directories[scene_id]) for scene_id in selected}
    settings = options.slurm
    paths: list[Path] = []
    for cell in options.grid.cells():
        run_id = run_id_for(options, model, cell.spec)
        plan = plan_sbatch(
            options.manifest,
            model,
            scenes_root=options.scenes,
            scenes=scenes,
            run_id=run_id,
            out_root=options.out,
            options=_run_options(options, model, cell.spec, run_id),
            python=settings.python,
            logs_dir=settings.logs_dir,
            results_dir=settings.results_dir,
            partition=settings.partition,
            scratch_template=settings.scratch_template,
        )
        paths.append(write_plan(plan, options.out, run_id))
    return tuple(paths)


def run_id_for(options: SweepOptions, model: ModelSpec, spec: PerturbationSpec) -> str:
    """The cell's run id: stable for the same manifest, model, spec and prediction options.

    Stability is what makes a re-run of the same sweep reuse the run directory, the
    cache under it and the evaluated files; a changed option changes the id so two
    different predictions never share a run directory.
    """
    payload: dict[str, object] = {
        "manifest_sha256": manifest_sha256(options.manifest),
        "model": model.key,
        "spec": spec.canonical(),
        "agents": list(options.agent_ids) if options.agent_ids is not None else None,
        "source": options.source,
        "scenes": list(options.scene_ids) if options.scene_ids is not None else None,
        "raw_root": None if options.raw_root is None else str(options.raw_root.resolve()),
        "media_roots": [str(root.resolve()) for root in options.media_roots],
        "options": {key: options.options[key] for key in sorted(options.options)},
    }
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    digest = hashlib.blake2s(text.encode("utf-8"), digest_size=5).hexdigest()
    return f"{options.run_id}-{digest}"


def default_eval() -> EvalChoice:
    """The eval config used when ``--config`` is not passed."""
    return EvalChoice("default", DEFAULT_CONFIG, None)


def _prepare(options: SweepOptions) -> ModelSpec:
    """Validate everything that can be refused before a run: the limit, ids and scenes."""
    model = select_model(options.manifest, options.model)
    check_cell_limit(options.grid, options.max_cells)
    try:
        check_run_id(options.run_id)
    except PredictionError as error:
        raise SweepError(f"--run-id: {error}") from None
    _scene_directories(options.scenes, options.scene_ids)
    return model


def _scene_directories(root: Path, scene_ids: Sequence[str] | None) -> dict[str, Path]:
    try:
        found = scene_directories(root)
    except FileNotFoundError:
        raise SweepError(f"not a directory: {root}") from None
    if not found:
        raise SweepError(f"no scene directories under {root}")
    directories = {path.name: path for path in found}
    if scene_ids is not None:
        for scene_id in scene_ids:
            if scene_id not in directories:
                raise SweepError(f"no scene directory {scene_id!r} under {root}")
    return directories


def _selected(directories: Mapping[str, Path], scene_ids: Sequence[str] | None) -> list[str]:
    if scene_ids is None:
        return list(directories)
    return list(dict.fromkeys(scene_ids))


def _run_options(
    options: SweepOptions, model: ModelSpec, spec: PerturbationSpec, run_id: str
) -> RunOptions:
    work_dir = None if options.work_dir is None else options.work_dir / run_id
    return RunOptions(
        model=model.key,
        agent_ids=options.agent_ids,
        source=options.source,
        scene_ids=options.scene_ids,
        raw_root=options.raw_root,
        media_roots=options.media_roots,
        options=options.options,
        frame_timeout_s=options.frame_timeout_s,
        run_timeout_s=options.run_timeout_s,
        cache=options.cache,
        hash_inputs=options.hash_inputs,
        work_dir=work_dir,
        force=options.force,
        perturbation=spec,
    )


def _evaluate_cell(options: SweepOptions, run_id: str) -> Mapping[str, EvalResult]:
    out: dict[str, EvalResult] = {}
    for choice in options.evals:
        report = evaluate_run(
            options.scenes,
            options.out,
            run_id,
            choice.config,
            scene_ids=options.scene_ids,
            created_utc=utc_now(),
        )
        if report.errors:
            raise SweepError(f"eval {choice.name!r} of run {run_id}: " + "; ".join(report.errors))
        rows = collect(options.scenes, options.out, run_id)
        if not rows:
            raise SweepError(f"eval {choice.name!r} of run {run_id}: no evaluated scene")
        totals = aggregate(rows, choice.config.thresholds)
        maps: dict[str, float | None] = {}
        for threshold in choice.config.thresholds:
            raw = totals.get(ap_column(threshold))
            maps[ap_column(threshold)] = None if raw is None else float(cast(float, raw))
        out[choice.name] = EvalResult(
            criterion=choice.config.criterion,
            thresholds=choice.config.thresholds,
            display_threshold=choice.config.display_threshold,
            maps=maps,
        )
    return out


def _progress_line(result: SweepCellResult, total: int, evals: Sequence[EvalChoice]) -> str:
    values = ", ".join(
        f"{name}={_format_number(float(value))}" for name, value in result.cell.values.items()
    )
    maps = "; ".join(
        f"{choice.name} {result.evaluations[choice.name].display_field} "
        f"{_format_map(result.evaluations[choice.name].display_value)}"
        for choice in evals
    )
    work = (
        f"{len(result.report.completed)} run, {len(result.report.skipped)} skipped, "
        f"{result.report.cache_hits} cached"
    )
    return (
        f"cell {result.cell.index + 1}/{total}: run {result.run_id} "
        f"({values}, seed={result.cell.spec.seed}) {maps} [{work}]"
    )


# --- the document ---------------------------------------------------------------------


def _document(
    options: SweepOptions,
    model: ModelSpec,
    cells: Sequence[SweepCellResult],
    aggregates: Sequence[SweepAggregate],
) -> dict[str, object]:
    assert options.manifest.source is not None
    return {
        "format": SWEEP_FORMAT,
        "format_version": SWEEP_VERSION,
        "model": {
            "plugin": options.manifest.name,
            "model": model.key,
            "manifest": str(options.manifest.source),
            "manifest_sha256": manifest_sha256(options.manifest),
        },
        "dataset": {
            "scenes_root": str(options.scenes),
            "scene_ids": None if options.scene_ids is None else list(options.scene_ids),
        },
        "eval": [
            {"name": choice.name, "path": choice.path, "config": config_json(choice.config)}
            for choice in options.evals
        ],
        "grid": {
            "axes": [
                {"name": axis.name, "unit": UNITS[axis.name], "values": list(axis.values)}
                for axis in options.grid.axes
            ],
            "seeds": list(options.grid.seeds),
            "combinations": options.grid.combination_count,
            "cells": options.grid.cell_count,
        },
        "cells": [_cell_json(result) for result in cells],
        "aggregates": [_aggregate_json(result, options.evals) for result in aggregates],
        "charts": _charts(options, aggregates),
    }


def _cell_json(result: SweepCellResult) -> dict[str, object]:
    return {
        "index": result.cell.index,
        "run_id": result.run_id,
        "canonical": result.cell.spec.canonical(),
        "spec": result.cell.spec.to_json(),
        "eval": {name: _eval_json(value) for name, value in result.evaluations.items()},
    }


def _eval_json(evaluation: EvalResult) -> dict[str, object]:
    return {
        "criterion": evaluation.criterion,
        "thresholds": list(evaluation.thresholds),
        "mAP": {
            field: evaluation.maps[field]
            for field in (ap_column(threshold) for threshold in evaluation.thresholds)
        },
        "mAP_display": {
            "threshold": evaluation.display_threshold,
            "field": evaluation.display_field,
            "value": evaluation.display_value,
        },
    }


def _aggregates(
    options: SweepOptions, cells: Sequence[SweepCellResult]
) -> tuple[SweepAggregate, ...]:
    groups: dict[tuple[AxisValue, ...], list[SweepCellResult]] = {}
    order: list[tuple[AxisValue, ...]] = []
    for result in cells:
        key = tuple(result.cell.values[axis.name] for axis in options.grid.axes)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(result)
    rows: list[SweepAggregate] = []
    for index, key in enumerate(order):
        values = {axis.name: value for axis, value in zip(options.grid.axes, key, strict=True)}
        rows.append(SweepAggregate(index, values, _aggregate_evaluations(options, groups[key])))
    return tuple(rows)


def _aggregate_evaluations(
    options: SweepOptions, group: Sequence[SweepCellResult]
) -> Mapping[str, Mapping[str, Stats]]:
    out: dict[str, Mapping[str, Stats]] = {}
    for choice in options.evals:
        out[choice.name] = {
            field: _stats([result.evaluations[choice.name].maps[field] for result in group])
            for field in (ap_column(threshold) for threshold in choice.config.thresholds)
        }
    return out


def _aggregate_json(result: SweepAggregate, evals: Sequence[EvalChoice]) -> dict[str, object]:
    spec = _spec_for(result.values, 0).to_json()
    spec["seed"] = None
    evaluations: dict[str, object] = {}
    for choice in evals:
        fields = result.evaluations[choice.name]
        display_field = ap_column(choice.config.display_threshold)
        evaluations[choice.name] = {
            "criterion": choice.config.criterion,
            "thresholds": [float(threshold) for threshold in choice.config.thresholds],
            "mAP": {field: stats.to_json() for field, stats in fields.items()},
            "mAP_display": {
                "threshold": choice.config.display_threshold,
                "field": display_field,
                **fields[display_field].to_json(),
            },
        }
    return {"index": result.index, "spec": spec, "eval": evaluations}


def _charts(options: SweepOptions, aggregates: Sequence[SweepAggregate]) -> list[dict[str, object]]:
    charts: list[dict[str, object]] = []
    baseline = _baseline(options.grid, aggregates)
    for choice in options.evals:
        field = ap_column(choice.config.display_threshold)
        baseline_json: dict[str, object] | None = None
        if baseline is not None:
            baseline_json = {
                "threshold": choice.config.display_threshold,
                "field": field,
                **baseline.evaluations[choice.name][field].to_json(),
            }
        for axis in options.grid.axes:
            if axis.name == "max_agents":
                continue  # 0 is outside its range: no neutral slice is possible
            points: list[dict[str, object]] = []
            for value in axis.values:
                aggregate_row = _slice(options.grid, aggregates, axis.name, value)
                if aggregate_row is None:
                    continue
                points.append(
                    {
                        "value": value,
                        **aggregate_row.evaluations[choice.name][field].to_json(),
                    }
                )
            if not points:
                continue  # another swept axis has no neutral value in this grid
            charts.append(
                {
                    "axis": axis.name,
                    "unit": UNITS[axis.name],
                    "eval": choice.name,
                    "metric": field,
                    "values": list(axis.values),
                    "baseline": baseline_json,
                    "points": points,
                }
            )
    return charts


def _baseline(grid: SweepGrid, aggregates: Sequence[SweepAggregate]) -> SweepAggregate | None:
    for aggregate_row in aggregates:
        if all(aggregate_row.values[axis.name] == 0 for axis in grid.axes):
            return aggregate_row
    return None


def _slice(
    grid: SweepGrid, aggregates: Sequence[SweepAggregate], axis: str, value: AxisValue
) -> SweepAggregate | None:
    others = [candidate for candidate in grid.axes if candidate.name != axis]
    for aggregate_row in aggregates:
        if aggregate_row.values[axis] != value:
            continue
        if all(aggregate_row.values[other.name] == 0 for other in others):
            return aggregate_row
    return None


def _stats(values: Sequence[float | None]) -> Stats:
    numbers = [value for value in values if value is not None]
    if not numbers:
        return Stats(None, None, 0)
    mean = statistics.fmean(numbers)
    std = statistics.stdev(numbers) if len(numbers) > 1 else 0.0
    return Stats(mean, std, len(numbers))


def _format_map(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"


def _format_number(value: float) -> str:
    return str(int(value)) if value == int(value) else format(value, "g")


def _write_json(path: Path, document: Mapping[str, object]) -> None:
    text = json.dumps(document, indent=2, allow_nan=False) + "\n"
    path.write_text(text, encoding="utf-8")


__all__ = [
    "AXES",
    "DEFAULT_MAX_CELLS",
    "SWEEP_FILE",
    "SWEEP_FORMAT",
    "SWEEP_VERSION",
    "EvalChoice",
    "EvalResult",
    "SlurmSettings",
    "Stats",
    "SweepAggregate",
    "SweepAxis",
    "SweepCell",
    "SweepCellResult",
    "SweepError",
    "SweepGrid",
    "SweepOptions",
    "SweepResult",
    "check_cell_limit",
    "default_eval",
    "emit_slurm",
    "load_grid",
    "parse_grid",
    "run_id_for",
    "run_sweep",
]
