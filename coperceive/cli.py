# SPDX-License-Identifier: Apache-2.0
"""Command line for coperceive."""

from __future__ import annotations

import argparse
import sys

from coperceive import __version__
from coperceive.model import SCHEMA_VERSION

_LOOPBACK = {"127.0.0.1", "localhost", "::1"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="coperceive",
        description=("Cooperative multi-agent perception data tools."),
    )
    parser.add_argument("--version", action="version", version=f"coperceive {__version__}")
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

    download = sub.add_parser("download", help="Download a dataset.")
    download_sub = download.add_subparsers(dest="dataset", required=True)
    griffin_download = download_sub.add_parser(
        "griffin",
        help="Download Griffin archives from the Hugging Face dataset.",
    )
    griffin_download.add_argument(
        "--subset",
        required=True,
        choices=("25m", "40m", "55m", "random"),
    )
    griffin_download.add_argument(
        "--components",
        default=None,
        help="Comma-separated list: metadata, lidar, or an archive basename.",
    )
    griffin_download.add_argument("--preset", default=None, choices=("dev",))
    griffin_download.add_argument(
        "--dest",
        required=True,
        help="Directory outside this repository.",
    )
    griffin_download.add_argument(
        "--dry-run",
        action="store_true",
        help="Print exact Hub sizes and the licence status. Write nothing.",
    )
    griffin_download.add_argument(
        "--accept-griffin-terms",
        action="store_true",
        help="Record the MIT acknowledgement once for this machine, outside the repository.",
    )
    griffin_download.set_defaults(func=_download_griffin)

    convert = sub.add_parser("convert", help="Convert a raw dataset.")
    convert_sub = convert.add_subparsers(dest="dataset", required=True)
    griffin_convert = convert_sub.add_parser(
        "griffin",
        help="Convert an extracted Griffin release to schema 1 scenes.",
    )
    griffin_convert.add_argument(
        "--subset",
        required=True,
        choices=("25m", "40m", "55m", "random"),
    )
    griffin_convert.add_argument(
        "--raw-root",
        required=True,
        help="Extracted griffin-release directory.",
    )
    griffin_convert.add_argument(
        "--out",
        required=True,
        help="One hybrid directory per scene is written here.",
    )
    griffin_convert.add_argument(
        "--workers",
        type=int,
        default=0,
        help="Parallel scenes. 0 uses up to 8 processes.",
    )
    griffin_convert.set_defaults(func=_convert_griffin)
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


def _download_griffin(args: argparse.Namespace) -> None:
    import importlib

    module = importlib.import_module("coperceive.griffin.commands")
    module.download_command(args)  # type: ignore[attr-defined]


def _convert_griffin(args: argparse.Namespace) -> None:
    import importlib

    module = importlib.import_module("coperceive.griffin.commands")
    module.convert_command(args)  # type: ignore[attr-defined]


def _serve(args: argparse.Namespace) -> None:
    if args.host not in _LOOPBACK and not args.token:
        print("refusing to bind a non-loopback host without --token", file=sys.stderr)
        raise SystemExit(2)
    import importlib

    module = importlib.import_module("coperceive.app.serve")
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
