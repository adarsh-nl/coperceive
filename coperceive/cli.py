# SPDX-License-Identifier: Apache-2.0
"""Command line for coperceive."""

from __future__ import annotations

import argparse
import os
import sys
import warnings
from pathlib import Path
from typing import cast

from coperceive import __version__
from coperceive.adapters.commands import add_parser as add_adapters_parser
from coperceive.evaluation.commands import add_parser as add_evaluation_parser
from coperceive.hdmap.commands import add_parser
from coperceive.model import SCHEMA_VERSION
from coperceive.models.commands import add_parser as add_models_parser
from coperceive.remote.commands import add_parser as add_remote_parser

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
        help="Bearer token (deprecated for security; use COPERCEIVE_TOKEN or --token-file).",
    )
    serve.add_argument(
        "--token-file",
        default=None,
        metavar="PATH",
        help="Path to a file containing the bearer token. Must be mode 0600 or 0400.",
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
    serve.add_argument(
        "--predictions",
        default=None,
        help="Predictions root. Defaults to ROOT/predictions when it holds a run.",
    )
    serve.add_argument(
        "--static",
        default=None,
        help="Serve this web build (a vite build directory with index.html) instead of "
        "the packaged one.",
    )
    serve.add_argument(
        "--media-root",
        action="append",
        default=[],
        metavar="DIR",
        help="Directory a scene's image links may resolve into, e.g. the dataset copy a "
        "staged scene links its camera directories to. Repeatable.",
    )
    serve.add_argument(
        "--allow-origin",
        default=None,
        help="Exact origin allowed for cross-origin requests.",
    )
    serve.set_defaults(func=_serve)

    scene = sub.add_parser("scene", help="Copy a scene into a self-contained library folder.")
    scene_sub = scene.add_subparsers(dest="scene_command", required=True)
    pack = scene_sub.add_parser(
        "pack",
        help="Pack one scene. Image paths are materialised as real files.",
    )
    pack.add_argument("source", help="Scene directory (manifest.json plus tables).")
    pack.add_argument("dest", help="Library directory. The scene is written as a child of this.")
    pack.add_argument(
        "--images",
        default="full",
        choices=("full", "preview", "none"),
        help="full copies image bytes, preview writes 640 px JPEG quality 85, none skips images.",
    )
    pack.add_argument(
        "--media-root",
        action="append",
        default=[],
        metavar="DIR",
        help="A symlink target may resolve here. Repeatable.",
    )
    pack.add_argument(
        "--griffin-root",
        default=None,
        metavar="DIR",
        help="Raw Griffin release root. Image paths may resolve here.",
    )
    pack.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the byte count for the chosen image option and do not write.",
    )
    pack.add_argument(
        "--force",
        action="store_true",
        help="Pack even if the plan is more than 3× the listed images plus scene files.",
    )
    pack.set_defaults(func=_scene_pack)

    add_parser(sub)
    add_adapters_parser(sub)
    add_evaluation_parser(sub)
    add_models_parser(sub)
    add_remote_parser(sub)

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
    validate.add_argument("root", help="A scene directory, or a directory of scene directories.")
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
    bev_export = export_sub.add_parser(
        "bev",
        help="Write a bird's-eye PNG or SVG of one sample.",
    )
    bev_export.add_argument("scene", help="One schema 1 scene directory.")
    bev_export.add_argument("--out", required=True, help="Path ending in .png or .svg.")
    bev_export.add_argument("--time-us", type=int, default=None, help="Recorded sample, µs.")
    bev_export.add_argument("--maps", default=None, help="Directory of sidecar maps.")
    bev_export.add_argument(
        "--format",
        choices=("png", "svg"),
        default=None,
        help="png or svg. Default follows the --out suffix.",
    )
    bev_export.add_argument("--theme", choices=("dark", "light"), default="dark")
    bev_export.add_argument("--color", choices=("agent", "class"), default="agent")
    bev_export.add_argument(
        "--run", default=None, help="Draw the match rows of this evaluated run at the sample."
    )
    bev_export.add_argument(
        "--predictions",
        default=None,
        help="Predictions root. Defaults to <scene>/../predictions when it holds a run.",
    )
    bev_export.set_defaults(func=_export_bev)
    overlay_export = export_sub.add_parser(
        "overlay",
        help="Write a camera image with boxes and lidar projected on it.",
    )
    overlay_export.add_argument("scene", help="One schema 1 scene directory.")
    overlay_export.add_argument("--out", required=True, help="Path ending in .png or .svg.")
    overlay_export.add_argument("--agent", required=True, help="Agent that owns the camera.")
    overlay_export.add_argument("--sensor", required=True, help="Camera sensor id.")
    overlay_export.add_argument("--time-us", type=int, default=None, help="Recorded sample, µs.")
    overlay_export.add_argument(
        "--format",
        choices=("png", "svg"),
        default=None,
        help="png or svg. Default follows the --out suffix.",
    )
    overlay_export.add_argument("--theme", choices=("dark", "light"), default="dark")
    overlay_export.add_argument("--color", choices=("agent", "class"), default="agent")
    overlay_export.set_defaults(func=_export_overlay)

    dataset = sub.add_parser("dataset", help="Dataset catalog and operations.")
    dataset_sub = dataset.add_subparsers(dest="dataset_command", required=True)

    catalog_parser = dataset_sub.add_parser("catalog", help="Print dataset catalog as JSON.")
    catalog_parser.set_defaults(func=_dataset_catalog)

    dry_run_parser = dataset_sub.add_parser("dry-run", help="Dry run dataset preset download.")
    dry_run_parser.add_argument("dataset", help="Dataset ID (e.g. griffin).")
    dry_run_parser.add_argument("preset", help="Preset ID (e.g. dev, one-scene, full).")
    dry_run_parser.add_argument("--dest", required=True, help="Destination directory.")
    dry_run_parser.set_defaults(func=_dataset_dry_run)

    detect_parser = dataset_sub.add_parser(
        "detect", help="Identify a raw cluster dataset's built-in adapter and split."
    )
    detect_parser.add_argument("--path", required=True, help="A raw dataset or split directory.")
    detect_parser.add_argument("--max-scenes", type=int, default=5000)
    detect_parser.set_defaults(func=_dataset_detect)

    index_parser = dataset_sub.add_parser(
        "index", help="List every scene of a raw cluster dataset (metadata only)."
    )
    index_parser.add_argument("--adapter", required=True, choices=("griffin", "opv2v", "v2xset"))
    index_parser.add_argument("--raw-root", required=True)
    index_parser.add_argument("--max-scenes", type=int, default=5000)
    index_parser.add_argument("--max-entries", type=int, default=200000)
    index_parser.add_argument("--timeout", type=float, default=30.0)
    index_parser.set_defaults(func=_dataset_index)

    job_parser = dataset_sub.add_parser("download", help="Download and convert dataset preset.")
    job_parser.add_argument("dataset", help="Dataset ID (e.g. griffin).")
    job_parser.add_argument("preset", help="Preset ID (e.g. dev, one-scene, full).")
    job_parser.add_argument("--dest", required=True, help="Destination library root.")
    job_parser.add_argument(
        "--accept-terms", action="store_true", help="Accept dataset licence terms."
    )
    job_parser.set_defaults(func=_dataset_download)

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    func = getattr(args, "func", None)
    if func is None:
        parser.print_help()
        return
    func(args)


def _scene_pack(args: argparse.Namespace) -> None:
    from coperceive.scene.pack import PackError, pack_scene

    try:
        pack_scene(
            Path(args.source),
            Path(args.dest),
            images=args.images,
            media_roots=[Path(item) for item in args.media_root],
            griffin_root=None if args.griffin_root is None else Path(args.griffin_root),
            dry_run=args.dry_run,
            force=args.force,
        )
    except PackError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(3) from exc


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


def _scene_dir(args: argparse.Namespace) -> Path:
    scene_dir = Path(args.scene)
    if not (scene_dir / "manifest.json").is_file():
        print(f"not a scene directory: {scene_dir}", file=sys.stderr)
        raise SystemExit(2)
    return scene_dir


def _export_bev(args: argparse.Namespace) -> None:
    from pathlib import Path

    from coperceive.errors import CoperceiveError
    from coperceive.evaluation.predictions import predictions_root
    from coperceive.evalview import EvalViewError, frame_rows
    from coperceive.hdmap.store import find_map, load_map, scene_map_name
    from coperceive.plot import PlotError, PredictionDraw, prediction_draws, render_bev
    from coperceive.store import read_scene
    from coperceive.viewdata import pose_times

    scene_dir = _scene_dir(args)
    scene = read_scene(scene_dir)
    hdmap = None
    maps = args.maps
    root = Path(maps) if isinstance(maps, str) else scene_dir.parent / "maps"
    name = scene_map_name(scene)
    if name is not None and root.is_dir():
        directory = find_map(root, name)
        if directory is not None:
            hdmap = load_map(directory)
    time_us = args.time_us if isinstance(args.time_us, int) else None
    predictions: tuple[PredictionDraw, ...] = ()
    run_id = args.run
    if isinstance(run_id, str):
        override = Path(args.predictions) if isinstance(args.predictions, str) else None
        store = predictions_root(scene_dir.resolve().parent, override)
        if store is None:
            print("no predictions root", file=sys.stderr)
            raise SystemExit(2)
        times = pose_times(scene)
        when = time_us if time_us is not None else (times[0] if times else None)
        if when is None:
            print("scene has no pose times", file=sys.stderr)
            raise SystemExit(2)
        try:
            body = frame_rows(store, run_id, scene_dir.resolve().name, when, compare=None)
            predictions = prediction_draws(cast(list[dict[str, object]], body["rows"]))
        except (EvalViewError, PlotError, CoperceiveError, ValueError, OSError) as exc:
            print(f"predictions: {exc}", file=sys.stderr)
            raise SystemExit(2) from exc
    try:
        result = render_bev(
            scene,
            Path(args.out),
            time_us=time_us,
            hdmap=hdmap,
            format=args.format if isinstance(args.format, str) else None,
            theme=args.theme,
            color_by=args.color,
            predictions=predictions,
        )
    except PlotError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2) from exc
    print(f"wrote {result.dest}")
    print(
        f"time_us {result.time_us} width {result.width} height {result.height} "
        f"points {result.points} boxes {result.boxes} map {int(result.map_drawn)} "
        f"predictions {result.predictions}"
    )


def _export_overlay(args: argparse.Namespace) -> None:
    from pathlib import Path

    from coperceive.plot import PlotError, render_overlay
    from coperceive.store import read_scene

    scene_dir = _scene_dir(args)
    try:
        result = render_overlay(
            read_scene(scene_dir),
            scene_dir,
            Path(args.out),
            agent_id=args.agent,
            sensor_id=args.sensor,
            time_us=args.time_us,
            format=args.format if isinstance(args.format, str) else None,
            theme=args.theme,
            color_by=args.color,
        )
    except PlotError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2) from exc
    print(f"wrote {result.dest}")
    print(
        f"time_us {result.time_us} width {result.width} height {result.height} "
        f"points {result.points} boxes {result.boxes}"
    )


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


def _dataset_detect(args: argparse.Namespace) -> None:
    import json

    from coperceive.cluster_datasets import DatasetError, detect_cluster_dataset

    try:
        result = detect_cluster_dataset(args.path, max_scenes=args.max_scenes)
    except (DatasetError, ValueError, OSError) as exc:
        result = {
            "schema": "coperceive-cluster-detect-1",
            "status": "error",
            "reason": str(exc),
        }
        print(json.dumps(result, indent=2))
        raise SystemExit(1) from None
    print(json.dumps(result, indent=2))


def _dataset_index(args: argparse.Namespace) -> None:
    import json

    from coperceive.cluster_datasets import DatasetError, index_cluster_dataset

    try:
        result = index_cluster_dataset(
            args.adapter,
            args.raw_root,
            max_scenes=args.max_scenes,
            max_entries=args.max_entries,
            timeout_s=args.timeout,
        )
    except (DatasetError, ValueError, OSError) as exc:
        print(
            json.dumps(
                {
                    "schema": "coperceive-cluster-index-1",
                    "status": "error",
                    "error": str(exc),
                },
                indent=2,
            )
        )
        raise SystemExit(1) from None
    print(json.dumps(result, indent=2))


def _dataset_catalog(_args: argparse.Namespace) -> None:
    import json

    from coperceive.catalog import load_catalog

    print(json.dumps(load_catalog(), indent=2))


def _dataset_dry_run(args: argparse.Namespace) -> None:
    import json

    from coperceive.catalog import dry_run_preset

    result = dry_run_preset(args.dataset, args.preset, Path(args.dest))
    print(json.dumps(result, indent=2))


def _dataset_download(args: argparse.Namespace) -> None:
    from coperceive.catalog import run_dataset_job

    run_dataset_job(args.dataset, args.preset, Path(args.dest), accept_terms=args.accept_terms)


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


def _resolve_token(args: argparse.Namespace) -> str | None:
    token: str | None = None
    if getattr(args, "token_file", None) is not None:
        token_path = Path(args.token_file)
        if not token_path.is_file():
            print(f"--token-file {args.token_file} is not a file", file=sys.stderr)
            raise SystemExit(2)
        mode = token_path.stat().st_mode & 0o777
        if (mode & 0o077) != 0:
            print(
                f"--token-file {args.token_file} has loose permissions {oct(mode)} "
                "(must be mode 0600 or 0400)",
                file=sys.stderr,
            )
            raise SystemExit(2)
        token = token_path.read_text(encoding="utf-8").strip() or None
    elif "COPERCEIVE_TOKEN" in os.environ and os.environ["COPERCEIVE_TOKEN"].strip():
        token = os.environ["COPERCEIVE_TOKEN"].strip()
    elif getattr(args, "token", None):
        warnings.warn(
            "Passing --token on argv is deprecated for security (visible in ps on shared nodes); "
            "use COPERCEIVE_TOKEN or --token-file instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        token = args.token
    return token


def _serve(args: argparse.Namespace) -> None:
    token = _resolve_token(args)
    if args.host not in _LOOPBACK and not token:
        print("refusing to bind a non-loopback host without a token", file=sys.stderr)
        raise SystemExit(2)
    if args.static is not None and not (Path(args.static) / "index.html").is_file():
        print(f"--static {args.static} has no index.html", file=sys.stderr)
        raise SystemExit(2)
    for media in args.media_root:
        if not Path(media).is_dir():
            print(f"--media-root {media} is not a directory", file=sys.stderr)
            raise SystemExit(2)
    from coperceive.viewdata import dataset_root_error

    root_error = dataset_root_error(Path(args.root))
    if root_error is not None:
        print(root_error, file=sys.stderr)
        raise SystemExit(2)
    import importlib

    module = importlib.import_module("coperceive.app.serve")
    run = module.run  # type: ignore[attr-defined]
    run(
        host=args.host,
        port=args.port,
        root=args.root,
        token=token,
        root_path=args.root_path,
        maps=args.maps,
        predictions=args.predictions,
        static=args.static,
        media_roots=tuple(args.media_root),
        allow_origin=args.allow_origin,
    )


if __name__ == "__main__":
    main()
