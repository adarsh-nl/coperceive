# SPDX-License-Identifier: Apache-2.0
"""``coperceive robustness sweep``: run a grid of perturbations and write ``sweep.json``."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import cast

from coperceive.errors import CoperceiveError
from coperceive.evaluation.config import ConfigError, load_config
from coperceive.models.batch import BatchError
from coperceive.models.cache import CacheError
from coperceive.models.registry import RegistryError, resolve_manifest
from coperceive.models.slurm import (
    SlurmError,
    validate_partition,
    validate_scratch_template,
)
from coperceive.models.spec import ModelSpecError
from coperceive.robustness.sweep import (
    DEFAULT_MAX_CELLS,
    EvalChoice,
    SlurmSettings,
    SweepError,
    SweepOptions,
    default_eval,
    emit_slurm,
    load_grid,
    run_sweep,
)

_USER_ERRORS = (
    CoperceiveError,
    BatchError,
    CacheError,
    ConfigError,
    ModelSpecError,
    RegistryError,
    SlurmError,
    SweepError,
    OSError,
    ValueError,
)


def add_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:  # type: ignore[type-arg]
    robustness = sub.add_parser(
        "robustness", help="Robustness sweeps of one model over one scene split."
    )
    commands = robustness.add_subparsers(dest="robustness_command", required=True)
    sweep = commands.add_parser(
        "sweep",
        help="Run a grid of seeded perturbations, evaluate every cell and write sweep.json.",
    )
    sweep.add_argument(
        "definition",
        help=(
            "Sweep definition: a YAML or JSON file path, or the YAML itself when it is not "
            "an existing file."
        ),
    )
    _plugin_arguments(sweep)
    sweep.add_argument("--model", default=None, help="One model of the plugin; default: default.")
    sweep.add_argument("--scenes", required=True, help="Directory of schema 1 scenes.")
    sweep.add_argument("--scene-ids", default=None, help="Comma-separated scene ids.")
    sweep.add_argument("--out", required=True, help="Predictions root; sweep.json is written here.")
    sweep.add_argument(
        "--run-id",
        default="sweep",
        help="Run id prefix of the cells; default sweep (each cell gets a stable suffix).",
    )
    sweep.add_argument("--agents", default=None, help="Comma-separated agent ids.")
    sweep.add_argument("--source", default=None)
    sweep.add_argument("--raw-root", default=None)
    sweep.add_argument("--media-root", action="append", default=[])
    sweep.add_argument("--option", action="append", default=[], metavar="K=V")
    sweep.add_argument("--frame-timeout", type=float, default=None)
    sweep.add_argument("--run-timeout", type=float, default=None)
    sweep.add_argument("--no-cache", action="store_true")
    sweep.add_argument("--hash-inputs", action="store_true")
    sweep.add_argument("--work", default=None, help="Work root; each cell uses <work>/<run_id>.")
    sweep.add_argument(
        "--force",
        action="store_true",
        help="Re-emit every cell even when its run exists, reusing the per-scene cache.",
    )
    sweep.add_argument(
        "--config",
        action="append",
        default=[],
        metavar="[NAME=]FILE",
        help=(
            "Evaluation config YAML; repeatable. Without a name the file stem is used; "
            "default: the eval spec's default config, recorded as 'default'."
        ),
    )
    sweep.add_argument(
        "--max-cells",
        type=int,
        default=DEFAULT_MAX_CELLS,
        help=f"Refuse a sweep with more runs than this; default {DEFAULT_MAX_CELLS}.",
    )
    sweep.add_argument(
        "--slurm",
        action="store_true",
        help="Emit one sbatch per cell under <out>/.slurm/ and print it; run nothing.",
    )
    sweep.add_argument(
        "--slurm-python", default=None, help="Interpreter on the node; default this one."
    )
    sweep.add_argument("--slurm-partition", default=None, help="Slurm partition for --slurm.")
    sweep.add_argument("--slurm-logs", default=None, help="Slurm log directory; default ~/logs.")
    sweep.add_argument(
        "--slurm-results", default=None, help="Per-job results root; default ~/results."
    )
    sweep.add_argument(
        "--slurm-scratch-template",
        default=None,
        help="Scratch template for the job (default: $COPERCEIVE_SLURM_SCRATCH_TEMPLATE).",
    )
    sweep.set_defaults(func=sweep_command)


def _plugin_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("plugin", nargs="?", default=None, help="Plugin name (entry point).")
    parser.add_argument("--manifest", default=None, help="Manifest file instead of a name.")


def sweep_command(args: argparse.Namespace) -> None:
    _guarded(_sweep, args)


def _guarded(action: Callable[[argparse.Namespace], None], args: argparse.Namespace) -> None:
    try:
        action(args)
    except _USER_ERRORS as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from None


def _sweep(args: argparse.Namespace) -> None:
    grid = load_grid(_definition_text(args))
    manifest_path = (
        Path(args.manifest).expanduser().resolve() if isinstance(args.manifest, str) else None
    )
    manifest = resolve_manifest(args.plugin, manifest=manifest_path)
    choices = _eval_choices(cast(list[object], args.config))
    options = SweepOptions(
        manifest=manifest,
        scenes=Path(args.scenes),
        out=Path(args.out),
        run_id=str(args.run_id),
        grid=grid,
        evals=choices,
        model=args.model,
        scene_ids=_csv(args.scene_ids),
        agent_ids=_csv(args.agents),
        source=args.source,
        raw_root=Path(args.raw_root) if isinstance(args.raw_root, str) else None,
        media_roots=tuple(Path(str(item)) for item in cast(list[object], args.media_root)),
        options=_options(cast(list[object], args.option)),
        frame_timeout_s=args.frame_timeout,
        run_timeout_s=args.run_timeout,
        cache=not bool(args.no_cache),
        hash_inputs=bool(args.hash_inputs),
        work_dir=Path(args.work) if isinstance(args.work, str) else None,
        force=bool(args.force),
        max_cells=int(args.max_cells),
        slurm=None,
    )
    if args.slurm:
        options = _with_slurm(options, args)
        paths = emit_slurm(options)
        for path in paths:
            print(f"wrote {path}")
        print(f"emitted {len(paths)} sbatch files for {grid.cell_count} cells; no job submitted")
        print("after the jobs copy their runs home, run the same command without --slurm")
        return
    result = run_sweep(options, progress=print)
    print(f"wrote {result.path}")
    seeds = f"{len(grid.seeds)} seed" if len(grid.seeds) == 1 else f"{len(grid.seeds)} seeds"
    print(
        f"cells {grid.cell_count} ({grid.combination_count} perturbation combinations "
        f"x {seeds}), eval {len(choices)}"
    )


def _with_slurm(options: SweepOptions, args: argparse.Namespace) -> SweepOptions:
    scratch = args.slurm_scratch_template or os.environ.get("COPERCEIVE_SLURM_SCRATCH_TEMPLATE")
    if not scratch:
        raise SweepError(
            "No scratch template for the Slurm jobs. Set the cluster profile's "
            "scratch_template (for example /scratch/$USER/$SLURM_JOB_ID) and pass it with "
            "--slurm-scratch-template or COPERCEIVE_SLURM_SCRATCH_TEMPLATE."
        )
    partition = (args.slurm_partition or os.environ.get("COPERCEIVE_SLURM_PARTITION") or "").strip()
    if not partition:
        raise SweepError(
            "Choose a Slurm partition with --slurm-partition (or set "
            "COPERCEIVE_SLURM_PARTITION). Coperceive never guesses a site partition."
        )
    home = Path.home()
    settings = SlurmSettings(
        python=Path(args.slurm_python)
        if isinstance(args.slurm_python, str)
        else Path(sys.executable),
        logs_dir=Path(args.slurm_logs) if isinstance(args.slurm_logs, str) else home / "logs",
        results_dir=(
            Path(args.slurm_results) if isinstance(args.slurm_results, str) else home / "results"
        ),
        partition=validate_partition(partition),
        scratch_template=validate_scratch_template(scratch),
    )
    return SweepOptions(
        manifest=options.manifest,
        scenes=options.scenes,
        out=options.out,
        run_id=options.run_id,
        grid=options.grid,
        evals=options.evals,
        model=options.model,
        scene_ids=options.scene_ids,
        agent_ids=options.agent_ids,
        source=options.source,
        raw_root=options.raw_root,
        media_roots=options.media_roots,
        options=options.options,
        frame_timeout_s=options.frame_timeout_s,
        run_timeout_s=options.run_timeout_s,
        cache=options.cache,
        hash_inputs=options.hash_inputs,
        work_dir=options.work_dir,
        force=options.force,
        max_cells=options.max_cells,
        slurm=settings,
    )


def _definition_text(args: argparse.Namespace) -> str:
    raw = str(args.definition)
    path = Path(raw)
    if path.is_file():
        return path.read_text(encoding="utf-8")
    return raw


def _eval_choices(values: list[object]) -> tuple[EvalChoice, ...]:
    if not values:
        return (default_eval(),)
    choices: list[EvalChoice] = []
    names: set[str] = set()
    for value in values:
        text = str(value)
        name, separator, path_text = text.partition("=")
        if separator == "":
            path_text = text
            name = Path(text).stem or "eval"
        else:
            name = name.strip()
        if not name:
            raise SweepError(f"--config {text!r} has an empty name")
        if name in names:
            raise SweepError(f"--config names {name!r} twice")
        path = Path(path_text)
        choices.append(EvalChoice(name, load_config(path), str(path)))
        names.add(name)
    return tuple(choices)


def _options(values: list[object]) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in values:
        key, separator, value = str(item).partition("=")
        if separator == "" or not key:
            raise SweepError(f"--option expects K=V, got {item!r}")
        out[key] = value
    return out


def _csv(value: object) -> tuple[str, ...] | None:
    if not isinstance(value, str):
        return None
    return tuple(part.strip() for part in value.split(",") if part.strip())


__all__ = ["add_parser"]
