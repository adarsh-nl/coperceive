# SPDX-License-Identifier: Apache-2.0
"""``coperceive model list|inspect|check|run`` and ``model cache prune`` (spec §CLI)."""

from __future__ import annotations

import argparse
import dataclasses
import os
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import cast

import yaml

from coperceive.adapters.paths import PathError, Template
from coperceive.evaluation.predictions import PredictionError
from coperceive.model import Scene
from coperceive.models.batch import BatchError, BatchReport, RunOptions, run_batch, utc_now
from coperceive.models.cache import CacheError, prune
from coperceive.models.check import check_plugin, render_check
from coperceive.models.inputs import InputError
from coperceive.models.registry import RegistryError, list_plugins, resolve_manifest
from coperceive.models.runner import resolve_interpreter
from coperceive.models.slurm import SlurmError, plan_sbatch, submit, write_plan
from coperceive.models.spec import Manifest, ModelSpecError, select_model
from coperceive.store import read_scene
from coperceive.viewdata import scene_directories

_USER_ERRORS = (
    ModelSpecError,
    RegistryError,
    BatchError,
    PredictionError,
    InputError,
    CacheError,
    SlurmError,
    PathError,
    OSError,
)


def add_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:  # type: ignore[type-arg]
    model = sub.add_parser("model", help="Model plugins: list, inspect, check, run (ADR 0011).")
    commands = model.add_subparsers(dest="model_command", required=True)

    listing = commands.add_parser("list", help="Installed model plugins.")
    listing.set_defaults(func=list_command)

    inspect = commands.add_parser("inspect", help="Print the validated manifest and interpreter.")
    _plugin_arguments(inspect)
    inspect.set_defaults(func=inspect_command)

    check = commands.add_parser("check", help="Run the conformance rules over a synthetic scene.")
    _plugin_arguments(check)
    check.add_argument("--model", default=None, help="One model; default every model.")
    check.add_argument("--keep", action="store_true", help="Keep the work directory.")
    check.add_argument("--work", default=None, help="Work root; default a temporary directory.")
    check.add_argument("--raw-root", default=None, help="Raw release for models with raw inputs.")
    check.add_argument("--option", action="append", default=[], metavar="K=V")
    check.set_defaults(func=check_command)

    run = commands.add_parser("run", help="Run a model over scenes; write a predictions run.")
    _plugin_arguments(run)
    run.add_argument("--scenes", required=True, help="Directory of schema 1 scenes.")
    run.add_argument("--out", required=True, help="Predictions root.")
    run.add_argument("--run-id", required=True)
    run.add_argument("--model", default=None)
    run.add_argument("--agents", default=None, help="Comma-separated agent ids.")
    run.add_argument("--source", default=None)
    run.add_argument("--scene-ids", default=None, help="Comma-separated scene ids.")
    run.add_argument("--raw-root", default=None)
    run.add_argument("--media-root", action="append", default=[])
    run.add_argument("--artifacts", default=None, help="An earlier run's cache for depends_on.")
    run.add_argument("--no-auto-dependencies", action="store_true")
    run.add_argument("--option", action="append", default=[], metavar="K=V")
    run.add_argument("--frame-timeout", type=float, default=None)
    run.add_argument("--run-timeout", type=float, default=None)
    run.add_argument("--no-cache", action="store_true")
    run.add_argument("--hash-inputs", action="store_true")
    run.add_argument("--work", default=None)
    run.add_argument("--force", action="store_true")
    run.add_argument(
        "--slurm",
        action="store_true",
        help="Emit the sbatch for this run under <out>/.slurm/ and print it; run nothing.",
    )
    run.add_argument("--submit", action="store_true", help="With --slurm: also sbatch it.")
    run.add_argument(
        "--slurm-python", default=None, help="Interpreter on the node; default this one."
    )
    run.add_argument("--slurm-logs", default=None, help="Slurm log directory; default ~/logs.")
    run.add_argument(
        "--slurm-results", default=None, help="Per-job results root; default ~/results."
    )
    run.set_defaults(func=run_command)

    cache = commands.add_parser("cache", help="The per-scene result cache of runs.")
    cache_commands = cache.add_subparsers(dest="cache_command", required=True)
    prune_parser = cache_commands.add_parser("prune", help="Delete cache directories.")
    prune_parser.add_argument("--out", required=True)
    prune_parser.add_argument("--run-id", default=None)
    prune_parser.add_argument("--plugin", default=None)
    prune_parser.add_argument("--model", default=None)
    prune_parser.set_defaults(func=prune_command)


def _plugin_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("plugin", nargs="?", default=None, help="Plugin name (entry point).")
    parser.add_argument("--manifest", default=None, help="Manifest file instead of a name.")


def _guarded(action: Callable[[argparse.Namespace], None], args: argparse.Namespace) -> None:
    try:
        action(args)
    except _USER_ERRORS as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from None


def _manifest(args: argparse.Namespace) -> Manifest:
    manifest = Path(args.manifest) if args.manifest else None
    return resolve_manifest(args.plugin, manifest=manifest)


def _options(values: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in values:
        key, sep, value = item.partition("=")
        if not sep or not key:
            raise ModelSpecError(f"--option expects K=V, got {item!r}")
        out[key] = value
    return out


def _csv(value: str | None) -> tuple[str, ...] | None:
    if value is None:
        return None
    return tuple(part for part in value.split(",") if part)


# -- commands ----------------------------------------------------------------------------------


def list_command(_args: argparse.Namespace) -> None:
    rows = list_plugins()
    width = max((len(row.name) for row in rows), default=4)
    for row in rows:
        print(f"{row.name.ljust(width)}  {row.source.ljust(11)}  {row.location}")
    if not rows:
        print("no model plugins installed (entry point group coperceive.models)")


def inspect_command(args: argparse.Namespace) -> None:
    _guarded(_inspect, args)


def _inspect(args: argparse.Namespace) -> None:
    manifest = _manifest(args)
    payload = _as_plain(manifest)
    print(yaml.safe_dump(payload, sort_keys=False), end="")
    assert manifest.source is not None
    try:
        interpreter = resolve_interpreter(manifest.environment, plugin_dir=manifest.source.parent)
        print(f"interpreter: {interpreter}")
    except ModelSpecError as exc:
        print(f"interpreter: unresolved ({exc})")


def _as_plain(value: object) -> object:
    if isinstance(value, dict):
        return {str(k): _as_plain(v) for k, v in value.items()}  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
    if isinstance(value, (list, tuple)):
        return [_as_plain(v) for v in value]  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (set, frozenset)):
        items = cast(set[object], value)  # pyright: ignore[reportUnknownArgumentType]
        return sorted((_as_plain(v) for v in items), key=str)
    if isinstance(value, Template):  # before the dataclass branch: a Template is one
        return value.text
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        # Field by field, not asdict(): asdict would turn a nested Template into a dict
        # with its frozenset before this function could replace it by its text.
        return {f.name: _as_plain(getattr(value, f.name)) for f in dataclasses.fields(value)}
    return value


def check_command(args: argparse.Namespace) -> None:
    _guarded(_check, args)


def _check(args: argparse.Namespace) -> None:
    import tempfile

    manifest = _manifest(args)
    if args.model is not None:
        select_model(manifest, args.model)
    work_root = Path(args.work) if args.work else Path(tempfile.mkdtemp(prefix="coperceive-check-"))
    results = check_plugin(
        manifest,
        model=args.model,
        work_root=work_root,
        keep=bool(args.keep),
        raw_root=Path(args.raw_root) if args.raw_root else None,
        parent_env=dict(os.environ),
        options=_options(list(args.option)) or None,
    )
    print(render_check(manifest.name, results), end="")
    if args.keep:
        print(f"kept {work_root}")
    if any(result.status == "fail" for result in results):
        raise SystemExit(1)


def run_command(args: argparse.Namespace) -> None:
    _guarded(_run, args)


def _run(args: argparse.Namespace) -> None:
    manifest = _manifest(args)
    options = _run_options(args)
    out = Path(args.out)
    if args.slurm:
        _emit_slurm(args, manifest, options, out)
        return
    if args.submit:
        raise BatchError("--submit needs --slurm")
    report = run_batch(
        manifest,
        Path(args.scenes),
        out,
        str(args.run_id),
        options,
        created_utc=utc_now(),
    )
    _print_report(report, out, str(args.run_id))
    if report.completed == () and report.skipped and not options.force:
        print(
            f"run {args.run_id!r} already has every selected scene; use --force to redo it",
            file=sys.stderr,
        )
        raise SystemExit(1)
    if report.exit_code != 0:
        print(f"run {report.status}: {report.reason}", file=sys.stderr)
        raise SystemExit(report.exit_code)


def _emit_slurm(
    args: argparse.Namespace, manifest: Manifest, options: RunOptions, out: Path
) -> None:
    """Plan and write the sbatch; print its path and text; submit only with --submit."""
    spec = select_model(manifest, options.model)
    scenes_root = Path(args.scenes)
    directories = {path.name: path for path in scene_directories(scenes_root)}
    selected = list(directories) if options.scene_ids is None else list(options.scene_ids)
    scenes: dict[str, Scene] = {}
    for scene_id in selected:
        if scene_id not in directories:
            raise BatchError(f"no scene directory {scene_id!r} under {scenes_root}")
        scenes[scene_id] = read_scene(directories[scene_id])
    home = Path.home()
    plan = plan_sbatch(
        manifest,
        spec,
        scenes_root=scenes_root,
        scenes=scenes,
        run_id=str(args.run_id),
        out_root=out,
        options=options,
        python=Path(args.slurm_python) if args.slurm_python else Path(sys.executable),
        logs_dir=Path(args.slurm_logs) if args.slurm_logs else home / "logs",
        results_dir=Path(args.slurm_results) if args.slurm_results else home / "results",
    )
    path = write_plan(plan, out, str(args.run_id))
    print(f"wrote {path}")
    print(f"--time from {plan.estimate_source}")
    print(plan.script, end="")
    if args.submit:
        print(f"job {submit(path)}")


def _run_options(args: argparse.Namespace) -> RunOptions:
    return RunOptions(
        model=args.model,
        agent_ids=_csv(args.agents),
        source=args.source,
        scene_ids=_csv(args.scene_ids),
        raw_root=Path(args.raw_root) if args.raw_root else None,
        media_roots=tuple(Path(p) for p in args.media_root),
        artifacts_dir=Path(args.artifacts) if args.artifacts else None,
        auto_dependencies=not bool(args.no_auto_dependencies),
        options=_options(list(args.option)),
        frame_timeout_s=args.frame_timeout,
        run_timeout_s=args.run_timeout,
        cache=not bool(args.no_cache),
        hash_inputs=bool(args.hash_inputs),
        work_dir=Path(args.work) if args.work else None,
        force=bool(args.force),
    )


def _print_report(report: BatchReport, out: Path, run_id: str) -> None:
    for outcome in report.scenes:
        if outcome.error is not None:
            print(f"scene {outcome.scene_id}: failed: {outcome.error}")
        elif outcome.scene_id in report.skipped:
            print(f"scene {outcome.scene_id}: skipped, already written")
        else:
            cached = 1 if outcome.cached else 0
            print(
                f"scene {outcome.scene_id}: {outcome.frames} frames, {cached} cached, "
                f"{outcome.boxes} boxes"
            )
    print(f"wrote {out / run_id}")
    print(
        f"scenes {len(report.completed)} frames {report.frames_run} "
        f"cached {report.cache_hits} boxes {report.boxes}"
    )


def prune_command(args: argparse.Namespace) -> None:
    _guarded(_prune, args)


def _prune(args: argparse.Namespace) -> None:
    root = Path(args.out)
    if not root.is_dir():
        raise CacheError(f"not a directory: {root}")
    count = prune(root, run_id=args.run_id, plugin=args.plugin, model=args.model)
    print(f"pruned {count}")


def environment_for_run(parent: Mapping[str, str] | None = None) -> dict[str, str]:
    return dict(os.environ if parent is None else parent)


__all__ = ["add_parser"]
