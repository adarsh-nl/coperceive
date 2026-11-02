# SPDX-License-Identifier: Apache-2.0
"""``coperceive repro export|check|capture-env``."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path

from coperceive.repro.environment import capture_environment
from coperceive.repro.export import (
    ExportRequest,
    check_card,
    export_card,
    render_markdown,
    write_card,
)
from coperceive.repro.schema import ReproError


def add_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:  # type: ignore[type-arg]
    repro = sub.add_parser(
        "repro",
        help="Export or check a reproducibility card for a run.",
    )
    commands = repro.add_subparsers(dest="repro_command", required=True)

    export = commands.add_parser(
        "export",
        help="Write a repro card for one local evaluation or one saved server-profile run.",
    )
    export.add_argument("run_id", help="Predictions run id, or the saved plan id.")
    export.add_argument("--out", default=None, help="Where to write the card JSON.")
    export.add_argument(
        "--stdout",
        action="store_true",
        help="Print the card JSON to stdout. Used by the desktop app.",
    )
    export.add_argument("--root", default=None, help="Directory of scene directories.")
    export.add_argument(
        "--predictions",
        default=None,
        help="Predictions root. Default: ROOT/predictions.",
    )
    export.add_argument(
        "--profile",
        default=None,
        help="Server profile JSON. The plan and the run live under its results folder.",
    )
    export.add_argument("--markdown", default=None, help="Also write a short Markdown companion.")
    export.add_argument("--time-limit", default=None, help="Scheduler time the job requested.")
    export.add_argument(
        "--gpus",
        default=None,
        help="Scheduler GPU request, such as a GRES string.",
    )
    export.add_argument("--memory", default=None, help="Scheduler memory the job requested.")
    export.set_defaults(func=_guard(_export))

    check = commands.add_parser(
        "check",
        help="Validate a repro card: schema, secrets, and the predictions hash when asked.",
    )
    check.add_argument("card", help="Path to a repro card JSON file.")
    check.add_argument(
        "--run-dir",
        default=None,
        help="Run directory. Recomputes the sha256 of predictions.jsonl.",
    )
    check.add_argument(
        "--predictions",
        default=None,
        help="Predictions root. Compares embedded scores with each scene's metrics.json.",
    )
    check.add_argument(
        "--run-id",
        default=None,
        help="Run id under --predictions. Required when --predictions is set.",
    )
    check.set_defaults(func=_guard(_check))

    capture = commands.add_parser(
        "capture-env",
        help="Write environment.json for this process (python, packages, GPU summary).",
    )
    capture.add_argument("--out", required=True, help="Path of environment.json.")
    capture.set_defaults(func=_guard(_capture))


def _guard(action: Callable[[argparse.Namespace], None]) -> Callable[[argparse.Namespace], None]:
    def run(args: argparse.Namespace) -> None:
        try:
            action(args)
        except ReproError as exc:
            print(str(exc), file=sys.stderr)
            raise SystemExit(1) from None

    return run


def _export(args: argparse.Namespace) -> None:
    if not args.out and not args.stdout:
        raise ReproError("Pass --out to write the card, or --stdout to print it.")
    if args.predictions and not args.root and not args.profile:
        raise ReproError("--predictions is used with --root.")
    card = export_card(
        ExportRequest(
            run_id=str(args.run_id),
            scenes_root=None if args.root is None else Path(str(args.root)),
            predictions_root=None if args.predictions is None else Path(str(args.predictions)),
            profile_path=None if args.profile is None else Path(str(args.profile)),
            time_limit=None if args.time_limit is None else str(args.time_limit),
            gpus=None if args.gpus is None else str(args.gpus),
            memory=None if args.memory is None else str(args.memory),
        )
    )
    if args.out:
        dest = Path(str(args.out))
        write_card(card, dest)
        print(f"wrote {dest}")
    if args.markdown:
        markdown = Path(str(args.markdown))
        markdown.parent.mkdir(parents=True, exist_ok=True)
        markdown.write_text(render_markdown(card), encoding="utf-8")
        print(f"wrote {markdown}")
    if args.stdout:
        sys.stdout.write(json.dumps(card, indent=2, sort_keys=True, allow_nan=False) + "\n")


def _check(args: argparse.Namespace) -> None:
    path = Path(str(args.card))
    try:
        loaded: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ReproError(f"Could not read the card: {exc}") from exc
    if args.predictions and not args.run_id:
        raise ReproError("Pass --run-id with --predictions so the scene scores can be found.")
    problems = check_card(
        loaded,
        run_dir=None if args.run_dir is None else Path(str(args.run_dir)),
        predictions=None if args.predictions is None else Path(str(args.predictions)),
        run_id=None if args.run_id is None else str(args.run_id),
    )
    if problems:
        print(problems[0], file=sys.stderr)
        raise SystemExit(1)
    print(f"checked {path}")


def _capture(args: argparse.Namespace) -> None:
    dest = Path(str(args.out))
    capture_environment(dest)
    print(f"wrote {dest}")
