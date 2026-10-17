# SPDX-License-Identifier: Apache-2.0
"""CLI actions for ``coperceive predictions import`` and ``coperceive eval run|report|compare``.

The contract is ``docs/spec/predictions.md`` §Import and §``metrics.json``. Every
user-facing failure prints one line to stderr and exits 1; argparse keeps its
own exit 2 for a malformed command line.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import cast

from coperceive.errors import CoperceiveError
from coperceive.evaluation.config import ConfigError, load_config
from coperceive.evaluation.evaluate import EvaluationError, evaluate_run
from coperceive.evaluation.predictions import (
    BOXES_FILE,
    PredictionBox,
    PredictionError,
    RunMeta,
    check_run_id,
    check_scene_id,
    load_rows,
    predictions_root,
    read_run_meta,
    validate_boxes,
    write_run,
)
from coperceive.evaluation.report import Row, collect, render_compare, render_report
from coperceive.model import Scene
from coperceive.store import read_scene

_USER_ERRORS = (
    PredictionError,
    ConfigError,
    EvaluationError,
    CoperceiveError,
    ValueError,
    OSError,
)
IMPORTED_NOTE = "imported"


def add_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:  # type: ignore[type-arg]
    predictions = sub.add_parser(
        "predictions", help="Import model predictions beside schema 1 scenes (ADR 0010)."
    )
    commands = predictions.add_subparsers(dest="predictions_command", required=True)
    importer = commands.add_parser(
        "import", help="Write run.json and one boxes.parquet per scene from JSONL or Parquet."
    )
    importer.add_argument("file", help="A .jsonl or .parquet file with the boxes.parquet columns.")
    importer.add_argument("--run", required=True, help="Run id; the directory name.")
    _root_arguments(importer)
    importer.add_argument(
        "--scene", default=None, help="Scene id for rows without a scene_id column."
    )
    importer.add_argument("--model", default="", help="Free text: name, checkpoint, commit.")
    importer.add_argument("--score-note", required=True, help="What score is (sigmoid, logit...).")
    importer.add_argument(
        "--categories", default=None, help="Comma-separated classes. Default: the classes seen."
    )
    importer.add_argument(
        "--source-note",
        action="append",
        default=[],
        metavar="NAME=TEXT",
        help="A source and its one-line note. Repeatable. Default note: imported.",
    )
    importer.add_argument(
        "--force", action="store_true", help="Overwrite a scene that already has boxes.parquet."
    )
    importer.set_defaults(func=import_command)

    evaluation = sub.add_parser("eval", help="Evaluate and report prediction runs (ADR 0010).")
    eval_commands = evaluation.add_subparsers(dest="eval_command", required=True)
    run = eval_commands.add_parser(
        "run", help="Match a run to ground truth; write matches.parquet and metrics.json."
    )
    run.add_argument("run_id")
    _root_arguments(run)
    run.add_argument("--config", default=None, help="Evaluation config YAML. Default: the spec.")
    run.add_argument("--scenes", default=None, help="Comma-separated scene ids. Default: all.")
    run.set_defaults(func=run_command)

    report = eval_commands.add_parser("report", help="Print the Markdown table of one run.")
    report.add_argument("run_id")
    _root_arguments(report)
    report.set_defaults(func=report_command)

    compare = eval_commands.add_parser("compare", help="Print the table with b - a deltas.")
    compare.add_argument("run_a")
    compare.add_argument("run_b")
    _root_arguments(compare)
    compare.set_defaults(func=compare_command)


def _root_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--root", required=True, help="Directory of scene directories.")
    parser.add_argument(
        "--predictions", default=None, help="Predictions root. Default: ROOT/predictions."
    )


def _guarded(action: Callable[[argparse.Namespace], None], args: argparse.Namespace) -> None:
    try:
        action(args)
    except _USER_ERRORS as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from None


def _scenes_root(args: argparse.Namespace) -> Path:
    root = Path(args.root)
    if not root.is_dir():
        raise PredictionError(f"not a directory: {root}")
    return root


def _override(args: argparse.Namespace) -> Path | None:
    value = args.predictions
    return Path(value) if isinstance(value, str) else None


def _existing_predictions_root(args: argparse.Namespace) -> Path:
    root = _scenes_root(args)
    found = predictions_root(root, _override(args))
    if found is None:
        raise PredictionError(
            f"no predictions root under {root} (no {root / 'predictions'}/<run>/run.json); "
            "pass --predictions"
        )
    return found


def _split(value: object) -> list[str] | None:
    if not isinstance(value, str):
        return None
    return [item.strip() for item in value.split(",") if item.strip()]


# --- predictions import ---------------------------------------------------------------


def import_command(args: argparse.Namespace) -> None:
    _guarded(_import, args)


def _import(args: argparse.Namespace) -> None:
    run_id = check_run_id(args.run)
    root = _scenes_root(args)
    target = _override(args) or root / "predictions"
    rows = _rows_with_scene(load_rows(Path(args.file)), args.scene)
    meta = RunMeta(
        run_id=run_id,
        model=args.model,
        score_note=args.score_note,
        categories=tuple(_split(args.categories) or sorted({box.category for box in rows})),
        sources=_sources(rows, args.source_note),
        created_utc=_now_utc(),
    )
    _validate_against_scenes(root, meta, rows)
    scene_ids = write_run(target, run_id, meta, rows, force=bool(args.force))
    print(f"wrote {target / run_id}")
    print(f"scenes {len(scene_ids)} rows {len(rows)}")


def _rows_with_scene(rows: Sequence[PredictionBox], scene: object) -> tuple[PredictionBox, ...]:
    """Fill ``scene_id`` from ``--scene``; a row that has neither is an error.

    ``--scene`` and every row's ``scene_id`` pass the scene-id rule here, before
    anything is joined to the scenes root.
    """
    flag = check_scene_id(scene) if isinstance(scene, str) else None
    out: list[PredictionBox] = []
    for i, box in enumerate(rows):
        if box.scene_id is None:
            if flag is None:
                raise PredictionError(f"row {i}: no scene_id column; pass --scene")
            out.append(replace(box, scene_id=flag))
            continue
        try:
            check_scene_id(box.scene_id)
        except PredictionError as error:
            raise PredictionError(f"row {i}: {error}") from None
        if flag is not None and box.scene_id != flag:
            raise PredictionError(f"row {i}: scene_id {box.scene_id!r} is not --scene {flag!r}")
        out.append(box)
    return tuple(out)


def _sources(rows: Sequence[PredictionBox], notes: object) -> tuple[tuple[str, str], ...]:
    """``--source-note NAME=TEXT`` entries, then every other source seen with ``imported``."""
    named: dict[str, str] = {}
    for item in cast(list[object], notes) if isinstance(notes, list) else ():
        text = str(item)
        name, sep, note = text.partition("=")
        if sep == "" or name == "":
            raise PredictionError(f"--source-note {text!r} is not NAME=TEXT")
        if name in named:
            raise PredictionError(f"--source-note names {name!r} twice")
        named[name] = note
    for box in rows:
        named.setdefault(box.source, IMPORTED_NOTE)
    return tuple(named.items())


def _validate_against_scenes(root: Path, meta: RunMeta, rows: Sequence[PredictionBox]) -> None:
    """Every row against its scene; the first failure names its file row."""
    scenes: dict[str, Scene] = {}
    for i, box in enumerate(rows):
        scene_id = box.scene_id
        if scene_id is None:  # _rows_with_scene filled it
            raise PredictionError(f"row {i}: no scene_id column; pass --scene")
        scene = scenes.get(scene_id)
        if scene is None:
            scene_dir = root / scene_id
            if not (scene_dir / "manifest.json").is_file():
                raise PredictionError(f"row {i}: scene {scene_id!r} is not a scene under {root}")
            try:
                scene = scenes[scene_id] = read_scene(scene_dir)
            except (KeyError, TypeError) as error:  # a malformed manifest.json
                raise PredictionError(
                    f"row {i}: scene {scene_id!r} under {root} is not readable: {error!r}"
                ) from None
        try:
            validate_boxes(scene, meta, (box,))
        except PredictionError as error:
            message = str(error).removeprefix("row 0: ")
            raise PredictionError(f"row {i}: {message}") from None


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --- eval run | report | compare -------------------------------------------------------


def run_command(args: argparse.Namespace) -> None:
    _guarded(_run, args)


def _run(args: argparse.Namespace) -> None:
    root = _scenes_root(args)
    predictions = _existing_predictions_root(args)
    config_path = args.config
    config = load_config(Path(config_path) if isinstance(config_path, str) else None)
    report = evaluate_run(
        root,
        predictions,
        args.run_id,
        config,
        scene_ids=_split(args.scenes),
        created_utc=_now_utc(),
    )
    print(
        f"evaluated {len(report.evaluated)} skipped {len(report.skipped)} "
        f"errors {len(report.errors)}"
    )
    for error in report.errors:
        print(f"error\t{error}")
    if report.errors:
        raise SystemExit(1)


def report_command(args: argparse.Namespace) -> None:
    _guarded(_report, args)


def _report(args: argparse.Namespace) -> None:
    root = _scenes_root(args)
    predictions = _existing_predictions_root(args)
    rows = _evaluated_rows(root, predictions, args.run_id)
    print(render_report(rows, _thresholds(rows)), end="")


def compare_command(args: argparse.Namespace) -> None:
    _guarded(_compare, args)


def _compare(args: argparse.Namespace) -> None:
    root = _scenes_root(args)
    predictions = _existing_predictions_root(args)
    rows_a = _evaluated_rows(root, predictions, args.run_a)
    rows_b = _evaluated_rows(root, predictions, args.run_b)
    print(render_compare(rows_a, rows_b, _thresholds(rows_a)), end="")


def _evaluated_rows(root: Path, predictions: Path, run_id: str) -> tuple[Row, ...]:
    read_run_meta(predictions, run_id)
    rows = collect(root, predictions, run_id)
    if not rows:
        raise PredictionError(
            f"run {run_id!r} has no evaluated scene under {root} "
            f"(no metrics.json next to a {BOXES_FILE}); run `coperceive eval run {run_id}` first"
        )
    return rows


def _thresholds(rows: Sequence[Row]) -> tuple[float, ...]:
    value = rows[0].values.get("thresholds")
    if not isinstance(value, tuple):
        return ()
    return tuple(float(cast(float, t)) for t in cast(tuple[object, ...], value))
