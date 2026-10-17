# SPDX-License-Identifier: Apache-2.0
"""CLI actions for ``coperceive adapters list|check|ingest``."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from coperceive.adapters.build import BuildError
from coperceive.adapters.ingest import IngestError, format_report, ingest
from coperceive.adapters.paths import PathError
from coperceive.adapters.registry import RegistryError, list_adapters, resolve_adapter
from coperceive.adapters.spec import SpecError

_USER_ERRORS = (RegistryError, SpecError, BuildError, PathError, ValueError, OSError)


def add_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:  # type: ignore[type-arg]
    adapters = sub.add_parser("adapters", help="List, check and run dataset adapters (ADR 0009).")
    commands = adapters.add_subparsers(dest="adapters_command", required=True)

    listing = commands.add_parser("list", help="Show built-in and installed adapters.")
    listing.set_defaults(func=list_command)

    check = commands.add_parser(
        "check", help="Validate a manifest against a raw tree. Writes nothing."
    )
    _common(check)
    check.set_defaults(func=check_command)

    run = commands.add_parser("ingest", help="Write schema 1 scenes from a raw dataset tree.")
    _common(run)
    run.add_argument("--out", required=True, help="One scene directory per scene is written here.")
    run.add_argument(
        "--workers", type=int, default=0, help="Parallel scenes. 0 uses up to 8 processes."
    )
    run.set_defaults(func=ingest_command)


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "adapter", help="A built-in name, an installed adapter, or a manifest file."
    )
    parser.add_argument("--raw-root", required=True, help="The extracted dataset directory.")
    parser.add_argument(
        "--param",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help="A manifest parameter, for example subset=25m. Repeatable.",
    )
    parser.add_argument("--scenes", default=None, help="Comma-separated scene ids. Default: all.")


def list_command(_args: argparse.Namespace) -> None:
    rows = list_adapters()
    width = max((len(row.name) for row in rows), default=4)
    for row in rows:
        print(f"{row.name.ljust(width)}  {row.source.ljust(11)}  {row.location}")


def check_command(args: argparse.Namespace) -> None:
    try:
        adapter = resolve_adapter(args.adapter)
        report = adapter.check(
            Path(args.raw_root), _params(args.param), scene_ids=_scenes(args.scenes)
        )
    except _USER_ERRORS as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from None
    print(f"adapter {adapter.name}")
    print(f"scenes {report.scenes}")
    for scene_id, counts in report.counts.items():
        rendered = " ".join(f"{name}={count}" for name, count in counts)
        print(f"{scene_id}\t{rendered}")
    if report.variant_counts:
        print("label_fields " + ", ".join(f"{w}:{c}" for w, c in report.variant_counts))
    for error in report.errors:
        print(f"error\t{error}")
    if report.errors:
        raise SystemExit(1)


def ingest_command(args: argparse.Namespace) -> None:
    workers = args.workers
    try:
        adapter = resolve_adapter(args.adapter)
        report = ingest(
            adapter,
            Path(args.raw_root),
            Path(args.out),
            params=_params(args.param),
            workers=None if workers == 0 else workers,
            scene_ids=_scenes(args.scenes),
        )
    except IngestError as exc:
        print(format_report(exc.report), file=sys.stderr)
        raise SystemExit(1) from None
    except _USER_ERRORS as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from None
    print(format_report(report))


def _params(items: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in items:
        name, sep, value = item.partition("=")
        if not sep or not name:
            raise ValueError(f"--param expects NAME=VALUE, got {item!r}")
        out[name] = value
    return out


def _scenes(text: str | None) -> list[str] | None:
    if text is None:
        return None
    return [part for part in text.split(",") if part]
