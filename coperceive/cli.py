# SPDX-License-Identifier: Apache-2.0
"""Command line for coperceive."""

from __future__ import annotations

import argparse
import sys
from typing import cast

from coperceive import __version__
from coperceive.hdmap.commands import add_parser
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
    serve.add_argument(
        "--maps",
        default=None,
        help="Shared HD-map root. Defaults to ROOT/maps when that directory exists.",
    )
    serve.set_defaults(func=_serve)

    add_parser(sub)

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

    validate = sub.add_parser(
        "validate",
        help="Write a JSON report and a Markdown report for schema 1 scenes.",
    )
    validate.add_argument("root", help="Directory of scene directories.")
    validate.add_argument(
        "--json",
        default="coperceive-validate.json",
        help="JSON report path. Relative paths are written from the current directory.",
    )
    validate.add_argument(
        "--markdown",
        default="coperceive-validate.md",
        help="Markdown report path.",
    )
    validate.add_argument(
        "--strict",
        action="store_true",
        help="Exit 1 when the report contains a hard failure. The default exit is 0.",
    )
    validate.add_argument(
        "--maps",
        default=None,
        help="Shared HD-map root. Defaults to ROOT/maps when that directory holds a map.",
    )
    validate.set_defaults(func=_validate)

    export = sub.add_parser("export", help="Export a scene to another format.")
    export_sub = export.add_subparsers(dest="export_format", required=True)
    rerun_export = export_sub.add_parser(
        "rerun",
        help="Write a Rerun .rrd. Requires the viewer extra.",
    )
    rerun_export.add_argument("scene", help="One schema 1 scene directory.")
    rerun_export.add_argument("--out", required=True, help="Path of the .rrd file.")
    rerun_export.set_defaults(func=_export_rerun)
    sequence_export = export_sub.add_parser(
        "sequence",
        help="Write one camera's images to MP4, GIF, or PNG. MP4 needs ffmpeg.",
    )
    sequence_export.add_argument("scene", help="One schema 1 scene directory.")
    sequence_export.add_argument("--out", required=True, help="File, or a directory of PNG frames.")
    sequence_export.add_argument("--agent", required=True, help="Agent that owns the camera.")
    sequence_export.add_argument("--sensor", required=True, help="Camera sensor id.")
    sequence_export.add_argument("--start-us", type=int, default=None, help="Inclusive start, µs.")
    sequence_export.add_argument("--end-us", type=int, default=None, help="Inclusive end, µs.")
    sequence_export.add_argument("--fps", type=int, default=10, help="File rate. Default 10.")
    sequence_export.add_argument("--width", type=int, default=0, help="0 keeps the image width.")
    sequence_export.add_argument("--height", type=int, default=0, help="0 keeps the image height.")
    sequence_export.add_argument(
        "--format",
        choices=("mp4", "gif", "png"),
        default=None,
        help="mp4, gif, or png. Default follows the --out suffix, or png for a directory.",
    )
    sequence_export.set_defaults(func=_export_sequence)
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    func = getattr(args, "func", None)
    if func is None:
        parser.print_help()
        return
    func(args)


def _validate(args: argparse.Namespace) -> None:
    from pathlib import Path

    from coperceive.validate import validate_root, write_report

    root = Path(args.root)
    if not root.is_dir():
        print(f"not a directory: {root}", file=sys.stderr)
        raise SystemExit(2)
    report = validate_root(root, maps=None if args.maps is None else Path(args.maps))
    json_path = Path(args.json)
    markdown_path = Path(args.markdown)
    write_report(report, json_path, markdown_path)
    aggregate = report["aggregate"]
    print(f"wrote {json_path}")
    print(f"wrote {markdown_path}")
    if isinstance(aggregate, dict):
        values = cast(dict[object, object], aggregate)
        warning_count = _warning_count(values)
        hard = values.get("hard_failure_count")
        print(
            "scenes {scenes_read} failed {scenes_failed} missing_frames {missing_frames} "
            "warnings {warnings} hard {hard}".format(
                scenes_read=values.get("scenes_read"),
                scenes_failed=values.get("scenes_failed"),
                missing_frames=values.get("missing_frames"),
                warnings=warning_count,
                hard=hard,
            )
        )
        if args.strict and type(hard) is int and hard > 0:
            raise SystemExit(1)


def _warning_count(aggregate: dict[object, object]) -> int:
    total = 0
    for key in ("speed_warnings", "acceleration_warnings", "smoothed_acceleration_warnings"):
        value = aggregate.get(key)
        if type(value) is int:
            total += value
    return total


def _export_sequence(args: argparse.Namespace) -> None:
    from pathlib import Path

    from coperceive.sequence_export import SequenceExportError, export_camera_sequence
    from coperceive.store import read_scene

    scene_dir = Path(args.scene)
    if not (scene_dir / "manifest.json").is_file():
        print(f"not a scene directory: {scene_dir}", file=sys.stderr)
        raise SystemExit(2)
    dest = Path(args.out)
    fmt = args.format
    if not isinstance(fmt, str):
        suffix = dest.suffix.lower()
        if suffix == ".mp4":
            fmt = "mp4"
        elif suffix == ".gif":
            fmt = "gif"
        elif suffix == ".png":
            fmt = "png"
        else:
            fmt = "png"
    try:
        result = export_camera_sequence(
            read_scene(scene_dir),
            scene_dir,
            dest,
            agent_id=args.agent,
            sensor_id=args.sensor,
            start_us=args.start_us,
            end_us=args.end_us,
            fps=args.fps,
            width=args.width,
            height=args.height,
            format=fmt,
        )
    except SequenceExportError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2) from exc
    print(f"wrote {result.dest}")
    print(f"frames {result.frames} width {result.width} height {result.height} fps {result.fps}")


def _export_rerun(args: argparse.Namespace) -> None:
    from pathlib import Path

    from coperceive.rerun_export import RerunUnavailable, export_rerun
    from coperceive.store import read_scene

    scene_dir = Path(args.scene)
    if not (scene_dir / "manifest.json").is_file():
        print(f"not a scene directory: {scene_dir}", file=sys.stderr)
        raise SystemExit(2)
    try:
        samples = export_rerun(read_scene(scene_dir), Path(args.out))
    except RerunUnavailable as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2) from exc
    print(f"wrote {args.out}")
    print(f"samples {samples}")


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
        maps=args.maps,
    )


if __name__ == "__main__":
    main()
