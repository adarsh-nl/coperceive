# SPDX-License-Identifier: Apache-2.0
"""Command line. ``pkg`` is the placeholder name."""

from __future__ import annotations

import argparse
import sys

from pkg import __version__
from pkg.model import SCHEMA_VERSION

_LOOPBACK = {"127.0.0.1", "localhost", "::1"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pkg",
        description=(
            "Cooperative multi-agent perception data tools. The program name is a placeholder."
        ),
    )
    parser.add_argument("--version", action="version", version=f"pkg {__version__}")
    sub = parser.add_subparsers(dest="command")

    schema = sub.add_parser("schema", help="Print the on-disk schema version.")
    schema.set_defaults(func=_schema)

    serve = sub.add_parser("serve", help="Serve the local web app.")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8731)
    serve.add_argument("--root", required=True, help="Directory the server is allowed to touch.")
    serve.add_argument(
        "--token",
        default=None,
        help="Bearer token. Required when --host is not loopback.",
    )
    serve.add_argument(
        "--root-path",
        default="",
        help="URL prefix when the app sits behind a JupyterHub or similar proxy.",
    )
    serve.set_defaults(func=_serve)
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    func = getattr(args, "func", None)
    if func is None:
        parser.print_help()
        return
    func(args)


def _schema(_args: argparse.Namespace) -> None:
    print(SCHEMA_VERSION)


def _serve(args: argparse.Namespace) -> None:
    if args.host not in _LOOPBACK and not args.token:
        print("refusing to bind a non-loopback host without --token", file=sys.stderr)
        raise SystemExit(2)
    import importlib

    module = importlib.import_module("pkg.app.serve")
    run = module.run  # type: ignore[attr-defined]
    run(
        host=args.host,
        port=args.port,
        root=args.root,
        token=args.token,
        root_path=args.root_path,
    )


if __name__ == "__main__":
    main()
