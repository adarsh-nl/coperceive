# SPDX-License-Identifier: Apache-2.0
"""CLI actions for ``coperceive predictions import`` and ``coperceive eval run|report|compare``.

The contract is ``docs/spec/predictions.md`` §Import and §``metrics.json``. Every
user-facing failure prints one line to stderr and exits 1; argparse keeps its
own exit 2 for a malformed command line.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import cast

from coperceive.errors import CoperceiveError
from coperceive.evaluation.breakdown import (
    BREAKDOWNS,
    build_compare,
    render_breakdowns,
    render_multi,
    write_compare,
)
from coperceive.evaluation.config import ConfigError, load_config
from coperceive.evaluation.evaluate import EvaluationError, evaluate_run
from coperceive.evaluation.failures import (
    FailureWeights,
    declares_latency,
    mine_failures,
    render_failures,
    weights_from_json,
    write_failures,
)
from coperceive.evaluation.outcomes import (
    DEFAULT_DISTANCE_EDGES,
    DEFAULT_VISIBILITY_EDGES,
    OutcomeRecord,
    load_run_records,
)
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
from coperceive.evaluation.report import Row, collect, partial_note, render_compare, render_report
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

    compare = eval_commands.add_parser(
        "compare",
        help="Compare 2 to 4 runs. Two runs keep the b - a table; --by adds breakdowns.",
    )
    compare.add_argument("runs", nargs="+", metavar="RUN", help="Two, three or four run ids.")
    _root_arguments(compare)
    compare.add_argument(
        "--by",
        default=None,
        help="Comma-separated breakdowns: distance, occlusion, agents, class, scene.",
    )
    compare.add_argument(
        "--json",
        default=None,
        help="Write compare.json (schema docs/spec/compare.schema.json) to this path.",
    )
    compare.add_argument(
        "--from-scores",
        action="store_true",
        help="Read outcomes.json or matches only; do not reopen scenes.",
    )
    compare.add_argument(
        "--distance-bins",
        default=None,
        help="Ascending distance edges in metres. Default: 0,30,50.",
    )
    compare.add_argument(
        "--visibility-bins",
        default=None,
        help="Ascending visibility-fraction edges inside (0, 1). Default: 0.2,0.6.",
    )
    compare.set_defaults(func=compare_command)

    failures = eval_commands.add_parser(
        "failures", help="Rank the worst frames of a run and tag why each failed."
    )
    failures.add_argument("run_id")
    _root_arguments(failures)
    failures.add_argument(
        "--top", type=int, default=50, help="How many frames to keep. Default 50."
    )
    failures.add_argument(
        "--json",
        nargs="?",
        const="-",
        default=None,
        help="Write failures.json to this path, or print it when the path is omitted.",
    )
    failures.add_argument("--weights", default=None, help="JSON file of badness and tag weights.")
    failures.add_argument(
        "--latency-run",
        default=None,
        help="A run whose perturbation delays other agents, for the latency-sensitive tag.",
    )
    failures.add_argument(
        "--from-scores",
        action="store_true",
        help="Read outcomes.json or matches only; do not reopen scenes.",
    )
    failures.set_defaults(func=failures_command)


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
    print("".join(f"warning\t{warning}\n" for warning in report.warnings), end="")
    if report.errors:
        raise SystemExit(1)


def report_command(args: argparse.Namespace) -> None:
    _guarded(_report, args)


def _report(args: argparse.Namespace) -> None:
    root = _scenes_root(args)
    predictions = _existing_predictions_root(args)
    rows = _evaluated_rows(root, predictions, args.run_id)
    note = partial_note(predictions, rows)
    if note:
        print(note)
    print(render_report(rows, _thresholds(rows)), end="")


def compare_command(args: argparse.Namespace) -> None:
    _guarded(_compare, args)


def _compare(args: argparse.Namespace) -> None:
    run_ids = [str(run_id) for run_id in cast(list[object], args.runs)]
    if len(run_ids) < 2 or len(run_ids) > 4:
        raise PredictionError("eval compare takes 2, 3 or 4 runs")
    root = _scenes_root(args)
    predictions = _existing_predictions_root(args)
    loaded = [(run_id, _evaluated_rows(root, predictions, run_id)) for run_id in run_ids]
    for run_id, rows in loaded:
        note = partial_note(predictions, rows)
        if note:
            print(f"{run_id}: {note}")
    thresholds = _thresholds(loaded[0][1])
    for run_id, rows in loaded[1:]:
        other = _thresholds(rows)
        if other != thresholds:
            raise PredictionError(
                f"run {run_id!r} thresholds {list(other)} differ from "
                f"{loaded[0][0]!r} {list(thresholds)}"
            )
    if len(loaded) == 2:
        print(render_compare(loaded[0][1], loaded[1][1], thresholds), end="")
    else:
        print(render_multi(loaded, thresholds), end="")
    by = _breakdown_names(args.by)
    if args.json or args.by:
        names = by if args.by else BREAKDOWNS
        document = _compare_document(args, root, predictions, loaded, thresholds, names)
        if args.by:
            print()
            print(render_breakdowns(document), end="")
        if isinstance(args.json, str):
            destination = Path(args.json)
            write_compare(destination, document)
            print(f"wrote {destination}", file=sys.stderr)


def failures_command(args: argparse.Namespace) -> None:
    _guarded(_failures, args)


def _failures(args: argparse.Namespace) -> None:
    if not isinstance(args.top, int) or isinstance(args.top, bool) or args.top < 1:
        raise PredictionError("--top must be at least 1")
    root = _scenes_root(args)
    predictions = _existing_predictions_root(args)
    _evaluated_rows(root, predictions, args.run_id)
    weights = FailureWeights()
    if isinstance(args.weights, str):
        try:
            loaded: object = json.loads(Path(args.weights).read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise PredictionError(f"--weights: {error}") from error
        if not isinstance(loaded, dict):
            raise PredictionError("--weights must be a JSON object")
        weights = weights_from_json(cast(dict[str, object], loaded))
    records, _origin = load_run_records(
        root, predictions, args.run_id, from_scores=bool(args.from_scores)
    )
    latency: tuple[OutcomeRecord, ...] | None = None
    if isinstance(args.latency_run, str):
        if not _declares_latency(predictions, args.latency_run):
            raise PredictionError(
                f"run {args.latency_run!r} has no latency perturbation "
                "(latency_frames or latency_ms in perturbation.json or provenance)"
            )
        _evaluated_rows(root, predictions, args.latency_run)
        latency, _latency_origin = load_run_records(
            root, predictions, args.latency_run, from_scores=bool(args.from_scores)
        )
    document = mine_failures(
        records, run_id=args.run_id, weights=weights, top=args.top, latency_records=latency
    )
    if args.json == "-":
        print(json.dumps(document, indent=2, allow_nan=False))
        return
    print(render_failures(document), end="")
    if isinstance(args.json, str):
        destination = Path(args.json)
        write_failures(destination, document)
        print(f"wrote {destination}", file=sys.stderr)


def _declares_latency(predictions: Path, run_id: str) -> bool:
    path = predictions / run_id / "perturbation.json"
    if path.is_file():
        try:
            loaded: object = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise PredictionError(f"{path}: {error}") from error
        if isinstance(loaded, dict) and declares_latency(cast(dict[str, object], loaded)):
            return True
    provenance = read_run_meta(predictions, run_id).provenance or {}
    raw = provenance.get("perturbation")
    return isinstance(raw, dict) and declares_latency(cast(dict[str, object], raw))


def _breakdown_names(value: object) -> tuple[str, ...]:
    if not isinstance(value, str) or not value.strip():
        return BREAKDOWNS
    names = tuple(item.strip() for item in value.split(",") if item.strip())
    unknown = [name for name in names if name not in BREAKDOWNS]
    if unknown:
        raise PredictionError(
            f"unknown breakdown {unknown[0]!r}; choose from {', '.join(BREAKDOWNS)}"
        )
    if not names:
        raise PredictionError(f"--by expects one of {', '.join(BREAKDOWNS)}")
    return names


def _edges(
    value: object, default: tuple[float, ...], name: str, *, upper: float | None
) -> tuple[float, ...]:
    if not isinstance(value, str) or not value.strip():
        return default
    try:
        edges = tuple(float(item) for item in value.split(",") if item.strip())
    except ValueError as error:
        raise PredictionError(f"--{name} must be comma-separated numbers") from error
    if not edges:
        raise PredictionError(f"--{name} needs at least one number")
    if any(edge != edge or edge in (float("inf"), float("-inf")) for edge in edges):
        raise PredictionError(f"--{name} must be finite")
    if upper is not None and any(edge <= 0.0 or edge >= upper for edge in edges):
        raise PredictionError(f"--{name} values must be between 0 and {upper}")
    if upper is None and any(edge < 0.0 for edge in edges):
        raise PredictionError(f"--{name} must be zero or positive")
    if any(right <= left for left, right in zip(edges, edges[1:], strict=False)):
        raise PredictionError(f"--{name} must be strictly increasing")
    return edges


def _compare_document(
    args: argparse.Namespace,
    root: Path,
    predictions: Path,
    loaded: Sequence[tuple[str, tuple[Row, ...]]],
    thresholds: Sequence[float],
    breakdowns: Sequence[str],
) -> dict[str, object]:
    distance_edges = _edges(args.distance_bins, DEFAULT_DISTANCE_EDGES, "distance-bins", upper=None)
    visibility_edges = _edges(
        args.visibility_bins, DEFAULT_VISIBILITY_EDGES, "visibility-bins", upper=1.0
    )
    runs: list[tuple[str, tuple[OutcomeRecord, ...]]] = []
    origins: list[str] = []
    for run_id, _rows in loaded:
        records, origin = load_run_records(
            root, predictions, run_id, from_scores=bool(args.from_scores)
        )
        runs.append((run_id, records))
        origins.append(origin)
    if all(origin == "scene" for origin in origins):
        distance_source = "scene"
    elif all(origin == "outcomes" for origin in origins):
        distance_source = "outcomes"
    else:
        distance_source = "unavailable"
    return build_compare(
        runs,
        thresholds=thresholds,
        breakdowns=breakdowns,
        distance_edges=distance_edges,
        visibility_edges=visibility_edges,
        distance_source=distance_source,
    )


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
