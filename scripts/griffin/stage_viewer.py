#!/usr/bin/env python
# SPDX-License-Identifier: Apache-2.0
"""Stage the Griffin-25m ``val`` scenes for the viewer without copying the dataset.

Runs inside a Slurm allocation and writes only under ``--scratch``, which is
``/local/$SLURM_JOB_ID/coperceive-viewer`` by default. A scratch under ``/home``
is refused (cluster rule: no job data on ``/home``).

```text
<scratch>/
  raw/<side>/scene_infos.json      val scenes only, dataset order, both sides
  raw/<side>/{calib,pose,label}    real copies for the val frames (rsync --files-from)
  raw/vehicle-side/lidar/lidar_top val frames only, ~150 MB (skipped with --no-lidar)
  scenes/<scene>/                  schema 1 scenes from ``coperceive convert griffin``
  scenes/<scene>/<side>/camera     symlink -> <release>/<side>/camera (absolute)
  maps/<Town>/                     ``coperceive maps ingest`` then ``coperceive maps align``
```

Why this shape. The adapter resolves symlinks and refuses files outside its raw
root, so everything it opens (``scene_infos.json``, calib, pose, label, and the
lidar it must read to write ``points.bin``) is a real copy. Images are recorded
with ``require_file: false`` as ``<side>/camera/<sensor>/<frame>.png`` relative
to the scene directory, so one symlink per side makes them resolve into the
release, and the server allows that with ``--media-root <release>``. Camera
PNGs (about 2.85 MB each, about 17 GB for the val frames) are never copied.

The OpenDRIVE files are checked against the SHA-256 pins of ADR 0008 before
anything is staged. The trimming rules are ``scene_infos_val.py`` next to this
file; this script reuses its functions and lists lidar instead of cameras.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import time
from collections.abc import Generator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq
from coperceive.griffin.convert import ConvertError, ConvertReport, convert_griffin, format_report
from coperceive.hdmap.align import align_maps
from coperceive.hdmap.commands import CARLA_PACKAGE_URL, ingest_command, sha256_file
from coperceive.hdmap.store import map_slug

try:
    from scripts.griffin import scene_infos_val as trim
except ImportError:  # run as a script: sys.path[0] is scripts/griffin
    import scene_infos_val as trim

SUBSET = "25m"
SIDES = trim.SIDES
LIDAR_RELATIVE = "vehicle-side/lidar/lidar_top"
FORBIDDEN_SCRATCH_ROOT = Path("/home")

# docs/adr/0008-hd-map.md, "OpenDRIVE SHA-256, the files extracted on this machine".
XODR_SHA256: dict[str, str] = {
    "Town03": "abf177bcd9b66cfefb7ac4919f6456815f1384f4229f1343c8a6a90bbb7713c9",
    "Town06": "a90b613175fa837fdc128ddfaf5ddad25797b128fdbd8cd945974bc21a60496e",
    "Town07": "be730652769e993e27f748ba5793dba9e49d299205146a7a8db0bcd17af877bf",
    "Town10HD": "5d883b799f634030af92be1e9d79d107845540ba04338e8c60e095be1aef7be7",
}


class StagingError(RuntimeError):
    """A staging step refused to run or did not finish."""


@dataclass(frozen=True)
class StageOptions:
    raw: Path
    split: Path
    scratch: Path
    xodr_dir: Path
    workers: int = 1
    predictions: Path | None = None
    static: Path | None = None
    port: int = 8731
    lidar: bool = True
    allow_unpinned_xodr: bool = False


@dataclass(frozen=True)
class ValPlan:
    """The val scenes of the split, in dataset order, before anything is written."""

    scenes: tuple[dict[str, Any], ...]
    names: tuple[str, ...]
    frames: tuple[str, ...]
    map_names: tuple[str, ...]


@dataclass(frozen=True)
class XodrFile:
    town: str
    map_name: str
    path: Path
    sha256: str
    pinned: bool


@dataclass(frozen=True)
class CameraLinks:
    links: int
    images_recorded: int
    images_resolved: int


@dataclass(frozen=True)
class MapReport:
    town: str
    map_name: str
    measured: bool
    chosen: str | None
    residual_median_m: float | None
    reason: str | None


@dataclass(frozen=True)
class StagingReport:
    scratch: Path
    raw_root: Path
    scenes_root: Path
    maps_root: Path
    scenes: tuple[str, ...]
    frames: int
    files_staged: int
    clouds_per_scene: tuple[tuple[str, int], ...]
    points_per_scene: tuple[tuple[str, int], ...]
    camera_links: int
    images_recorded: int
    images_resolved: int
    maps: tuple[MapReport, ...]
    serve_command: str
    timings: tuple[tuple[str, float], ...]


def _say(text: str) -> None:
    print(text, flush=True)


@contextmanager
def _step(label: str, timings: list[tuple[str, float]]) -> Generator[None, None, None]:
    _say(f"== {label}")
    start = time.monotonic()
    yield
    elapsed = time.monotonic() - start
    timings.append((label, elapsed))
    _say(f"-- {label}: {elapsed:.1f} s")


def check_scratch(scratch: Path) -> Path:
    """Absolute scratch path; refuses anything under ``/home``."""
    absolute = scratch.absolute()
    for candidate in (absolute, absolute.resolve()):
        if candidate == FORBIDDEN_SCRATCH_ROOT or FORBIDDEN_SCRATCH_ROOT in candidate.parents:
            raise StagingError(
                f"--scratch {scratch} is under {FORBIDDEN_SCRATCH_ROOT}; job data belongs "
                "on /local/$SLURM_JOB_ID"
            )
    return absolute


def default_scratch() -> Path | None:
    job = os.environ.get("SLURM_JOB_ID", "")
    if job == "":
        return None
    return Path("/local") / job / "coperceive-viewer"


def allocated_cpus() -> int:
    """CPUs this process may use (the Slurm allocation, not the whole node), at most 8."""
    try:
        count = len(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        count = os.cpu_count() or 1
    return max(1, min(8, count))


def plan_val(raw: Path, split: Path) -> ValPlan:
    """Select the val scenes exactly as ``scene_infos_val.py`` does."""
    try:
        infos = {
            side: trim.load_scene_infos(str(raw / side / "scene_infos.json")) for side in SIDES
        }
    except OSError as exc:
        raise StagingError(f"cannot read scene_infos.json under {raw}: {exc}") from exc
    if infos[SIDES[0]] != infos[SIDES[1]]:
        raise StagingError("vehicle-side and drone-side scene_infos.json differ")
    scenes = infos[SIDES[0]]
    trim.check_blocks(scenes)
    picked = trim.select_val(scenes, trim.val_scene_names(str(split)))
    trimmed = [scene for _, scene in picked]
    trim.check_blocks(trimmed)
    frames: list[str] = []
    map_names: set[str] = set()
    for scene in trimmed:
        frames.extend(trim.frames_of(scene))
        map_name = scene["info"].get("map_name")
        if not isinstance(map_name, str) or map_name == "":
            raise StagingError(f"scene {scene['name']}: info.map_name is missing")
        map_names.add(map_name)
    return ValPlan(
        scenes=tuple(trimmed),
        names=tuple(str(scene["name"]) for scene in trimmed),
        frames=tuple(frames),
        map_names=tuple(sorted(map_names)),
    )


def verify_xodr(
    xodr_dir: Path, map_names: Sequence[str], *, allow_unpinned: bool
) -> tuple[XodrFile, ...]:
    """One ``<Town>.xodr`` per map name, each checked against the ADR 0008 pin."""
    files: list[XodrFile] = []
    for map_name in map_names:
        town = map_slug(map_name)
        path = xodr_dir / f"{town}.xodr"
        if not path.is_file():
            raise StagingError(f"{path}: no OpenDRIVE file for {map_name}")
        digest = sha256_file(path)
        pin = XODR_SHA256.get(town)
        pinned = pin is not None and digest == pin
        if not pinned:
            expected = pin if pin is not None else "no pin for this town"
            message = f"{path}: SHA-256 {digest} is not the ADR 0008 pin ({expected})"
            if not allow_unpinned:
                raise StagingError(f"{message}; pass --allow-unpinned-xodr to use it anyway")
            _say(f"warning: {message}; used because of --allow-unpinned-xodr")
        files.append(XodrFile(town, map_name, path, digest, pinned))
    return tuple(files)


def staged_files(raw: Path, frames: Sequence[str], *, lidar: bool) -> list[str]:
    """rsync ``--files-from`` entries: calib of both sides, pose+label per frame, vehicle lidar."""
    files: list[str] = []
    for side in SIDES:
        files.extend(f"{side}/calib/{name}" for name in sorted(os.listdir(raw / side / "calib")))
    for frame in frames:
        for side in SIDES:
            files.append(f"{side}/pose/{frame}.json")
            files.append(f"{side}/label/{frame}.txt")
        if lidar:
            files.append(f"{LIDAR_RELATIVE}/{frame}.ply")
    return files


def stage_metadata(options: StageOptions, plan: ValPlan) -> int:
    """Trimmed ``scene_infos.json`` for both sides, then rsync the listed files."""
    raw_root = options.scratch / "raw"
    for side in SIDES:
        (raw_root / side).mkdir(parents=True, exist_ok=True)
        (raw_root / side / "scene_infos.json").write_text(
            json.dumps(list(plan.scenes), indent=4), encoding="utf-8"
        )
    files = staged_files(options.raw, plan.frames, lidar=options.lidar)
    missing = [item for item in files if not (options.raw / item).is_file()]
    if missing:
        raise StagingError(
            f"{len(missing)} listed files are missing under {options.raw}, e.g. {missing[:3]}"
        )
    listing = options.scratch / "stage-files.txt"
    listing.write_text("\n".join(files) + "\n", encoding="utf-8")
    command = ["rsync", "-a", f"--files-from={listing}", f"{options.raw}/", f"{raw_root}/"]
    _say(shlex.join(command))
    try:
        subprocess.run(command, check=True)
    except FileNotFoundError as exc:
        raise StagingError("rsync is not on PATH") from exc
    except subprocess.CalledProcessError as exc:
        raise StagingError(f"rsync exited with status {exc.returncode}") from exc
    return len(files)


def prune_scenes(options: StageOptions, names: Sequence[str]) -> tuple[str, ...]:
    """Remove staged scenes a re-run must not keep: ones outside this split, and, when
    lidar is requested, ones converted without it (``COMPLETE.json`` would skip them).
    ``rmtree`` does not follow the camera links, so the release is never touched."""
    scenes_root = options.scratch / "scenes"
    if not scenes_root.is_dir():
        return ()
    removed: list[str] = []
    for child in sorted(scenes_root.iterdir()):
        if not child.is_dir() or not (child / "manifest.json").is_file():
            continue
        stale = child.name not in names
        if not stale and options.lidar:
            index = child / "point_index.parquet"
            stale = not index.is_file() or pq.read_table(index, columns=["count"]).num_rows == 0
        if stale:
            shutil.rmtree(child)
            removed.append(child.name)
    if removed:
        _say(f"removed {len(removed)} staged scenes to convert afresh: {', '.join(removed)}")
    return tuple(removed)


def convert_scenes(options: StageOptions, names: Sequence[str]) -> ConvertReport:
    """``coperceive convert griffin --subset 25m`` on the staged raw tree, in process."""
    prune_scenes(options, names)
    try:
        report = convert_griffin(
            options.scratch / "raw",
            options.scratch / "scenes",
            subset=SUBSET,
            workers=options.workers,
        )
    except ConvertError as exc:
        raise StagingError("convert griffin failed:\n" + format_report(exc.report)) from exc
    except (ValueError, RuntimeError, OSError) as exc:
        raise StagingError(f"convert griffin failed: {exc}") from exc
    _say(format_report(report))
    done = set(report.written) | set(report.skipped) | set(report.resumed)
    if done != set(names):
        raise StagingError(f"converted scenes {sorted(done)} are not the val scenes {list(names)}")
    if options.lidar:
        empty = [
            name
            for name in names
            if pq.read_table(
                options.scratch / "scenes" / name / "point_index.parquet", columns=["count"]
            ).num_rows
            == 0
        ]
        if empty:
            raise StagingError(f"lidar was staged but these scenes have no clouds: {empty}")
    return report


def link_cameras(options: StageOptions, names: Sequence[str]) -> CameraLinks:
    """``<scene>/<side>/camera -> <release>/<side>/camera`` so recorded image paths resolve."""
    scenes_root = options.scratch / "scenes"
    links = 0
    for side in SIDES:
        target = (options.raw / side / "camera").resolve()
        if not target.is_dir():
            raise StagingError(f"{target} is not a directory")
        for name in names:
            side_dir = scenes_root / name / side
            side_dir.mkdir(exist_ok=True)
            link = side_dir / "camera"
            if link.is_symlink():
                if os.readlink(link) == str(target):
                    links += 1
                    continue
                link.unlink()
            elif link.exists():
                raise StagingError(f"{link} exists and is not a symlink; refusing to replace it")
            link.symlink_to(target, target_is_directory=True)
            links += 1
    recorded = 0
    resolved = 0
    for name in names:
        scene_dir = scenes_root / name
        for row in pq.read_table(scene_dir / "images.parquet", columns=["path"]).to_pylist():
            recorded += 1
            if (scene_dir / str(row["path"])).is_file():
                resolved += 1
    return CameraLinks(links, recorded, resolved)


def build_maps(options: StageOptions, xodrs: Sequence[XodrFile]) -> tuple[MapReport, ...]:
    """``coperceive maps ingest`` per town, then ``coperceive maps align`` against the scenes."""
    maps_root = options.scratch / "maps"
    maps_root.mkdir(parents=True, exist_ok=True)
    for item in xodrs:
        # The CLI's own function, so the manifest's source record is the one ingest writes.
        namespace = argparse.Namespace(
            xodr=str(item.path),
            map_name=item.map_name,
            out=str(maps_root),
            step_m=1.0,
            source_url=CARLA_PACKAGE_URL if item.pinned else "",
        )
        try:
            ingest_command(namespace)
        except SystemExit as exc:
            raise StagingError(f"maps ingest failed for {item.path} (exit {exc.code})") from exc
    try:
        reports = align_maps(maps_root, options.scratch / "scenes")
    except (OSError, ValueError) as exc:
        raise StagingError(f"maps align failed: {exc}") from exc
    return tuple(_map_report(raw) for raw in reports)


def _map_report(raw: dict[str, object]) -> MapReport:
    name = raw.get("map_name")
    map_name = name if isinstance(name, str) else ""
    chosen = raw.get("chosen")
    residual = raw.get("residual_median_m")
    reason = raw.get("reason")
    return MapReport(
        town=map_slug(map_name) if map_name else "",
        map_name=map_name,
        measured=raw.get("measured") is True,
        chosen=chosen if isinstance(chosen, str) else None,
        residual_median_m=(
            float(residual)
            if isinstance(residual, (int, float)) and not isinstance(residual, bool)
            else None
        ),
        reason=reason if isinstance(reason, str) else None,
    )


def point_counts(
    scenes_root: Path, names: Sequence[str]
) -> tuple[tuple[tuple[str, int], ...], tuple[tuple[str, int], ...]]:
    """Per scene: rows of ``point_index.parquet`` (clouds) and the sum of ``count`` (points)."""
    clouds: list[tuple[str, int]] = []
    points: list[tuple[str, int]] = []
    for name in names:
        table = pq.read_table(scenes_root / name / "point_index.parquet", columns=["count"])
        clouds.append((name, table.num_rows))
        points.append((name, sum(int(row["count"]) for row in table.to_pylist())))
    return tuple(clouds), tuple(points)


def serve_command(options: StageOptions) -> str:
    """The ``coperceive serve`` line for this staging, with the venv's executable when known."""
    venv_exe = Path(sys.executable).with_name("coperceive")
    argv = [
        str(venv_exe) if venv_exe.is_file() else "coperceive",
        "serve",
        "--host",
        "127.0.0.1",
        "--port",
        str(options.port),
        "--root",
        str(options.scratch / "scenes"),
        "--maps",
        str(options.scratch / "maps"),
    ]
    if options.predictions is not None:
        argv += ["--predictions", str(options.predictions)]
    argv += ["--media-root", str(options.raw)]
    if options.static is not None:
        argv += ["--static", str(options.static)]
    return shlex.join(argv)


def print_summary(report: StagingReport) -> None:
    _say("")
    _say(
        f"staged {len(report.scenes)} val scenes, {report.frames} frames, "
        f"{report.files_staged} files copied into {report.raw_root}"
    )
    clouds = dict(report.clouds_per_scene)
    points = dict(report.points_per_scene)
    _say(f"{'scene':<24}{'clouds':>8}{'points':>12}")
    for name in report.scenes:
        _say(f"{name:<24}{clouds.get(name, 0):>8}{points.get(name, 0):>12}")
    _say(
        f"camera links: {report.camera_links}; images recorded {report.images_recorded}, "
        f"resolving through the links {report.images_resolved}"
    )
    for item in report.maps:
        if item.measured:
            _say(
                f"map {item.map_name}: measured, chosen {item.chosen}, "
                f"residual median {item.residual_median_m} m"
            )
        else:
            _say(f"map {item.map_name}: not measured ({item.reason})")
    _say("timings: " + ", ".join(f"{label} {secs:.1f} s" for label, secs in report.timings))
    _say("serve on this node (tunnel: HANDOVER.md, 'Viewing from a laptop'):")
    _say(report.serve_command)


def stage(options: StageOptions) -> StagingReport:
    """Run the five steps and return what was staged. Nothing is written outside ``scratch``."""
    # The camera links point at the resolved release, so the serve line's --media-root
    # must name the same path whatever the working directory is at serve time.
    options = replace(options, scratch=check_scratch(options.scratch), raw=options.raw.resolve())
    timings: list[tuple[str, float]] = []
    _say(f"release {options.raw}")
    _say(f"split   {options.split}")
    _say(f"scratch {options.scratch}")
    with _step("0/5 plan: val scenes and OpenDRIVE pins", timings):
        plan = plan_val(options.raw, options.split)
        _say(f"val scenes ({len(plan.names)}): {', '.join(plan.names)}")
        _say(f"frames {len(plan.frames)}; maps {', '.join(plan.map_names)}")
        xodrs = verify_xodr(
            options.xodr_dir, plan.map_names, allow_unpinned=options.allow_unpinned_xodr
        )
        for item in xodrs:
            _say(f"{item.path} sha256 {item.sha256} {'pinned' if item.pinned else 'UNPINNED'}")
    options.scratch.mkdir(parents=True, exist_ok=True)
    copied = "metadata + lidar" if options.lidar else "metadata"
    with _step(f"1/5 trim scene_infos and rsync {copied}", timings):
        files_staged = stage_metadata(options, plan)
        _say(f"{files_staged} files listed for rsync")
    with _step(f"2/5 convert griffin --subset {SUBSET} --workers {options.workers}", timings):
        convert_scenes(options, plan.names)
    with _step("3/5 link camera directories into the release", timings):
        cameras = link_cameras(options, plan.names)
    with _step("4/5 maps ingest + align", timings):
        maps = build_maps(options, xodrs)
    with _step("5/5 summary", timings):
        clouds, points = point_counts(options.scratch / "scenes", plan.names)
        report = StagingReport(
            scratch=options.scratch,
            raw_root=options.scratch / "raw",
            scenes_root=options.scratch / "scenes",
            maps_root=options.scratch / "maps",
            scenes=plan.names,
            frames=len(plan.frames),
            files_staged=files_staged,
            clouds_per_scene=clouds,
            points_per_scene=points,
            camera_links=cameras.links,
            images_recorded=cameras.images_recorded,
            images_resolved=cameras.images_resolved,
            maps=maps,
            serve_command=serve_command(options),
            timings=tuple(timings),
        )
    print_summary(report)
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--raw", required=True, type=Path, help="griffin-release root on /datasets (read only)"
    )
    parser.add_argument(
        "--split",
        required=True,
        type=Path,
        help="toolkit split file, data/split_datas/griffin_50scenes_25m.json",
    )
    parser.add_argument(
        "--scratch",
        type=Path,
        default=default_scratch(),
        help="staging root; default /local/$SLURM_JOB_ID/coperceive-viewer inside a job",
    )
    parser.add_argument(
        "--xodr-dir",
        required=True,
        type=Path,
        help="directory holding Town03.xodr, Town06.xodr, Town07.xodr, Town10HD.xodr",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=allocated_cpus(),
        help="parallel scenes for convert (default: CPUs of this allocation, at most 8)",
    )
    parser.add_argument("--predictions", type=Path, default=None, help="predictions root to serve")
    parser.add_argument("--static", type=Path, default=None, help="web build to serve instead")
    parser.add_argument("--port", type=int, default=8731)
    parser.add_argument(
        "--no-lidar",
        action="store_true",
        help="do not stage the vehicle lidar; scenes get an empty point table",
    )
    parser.add_argument(
        "--allow-unpinned-xodr",
        action="store_true",
        help="accept .xodr files whose SHA-256 is not the ADR 0008 pin (tests only)",
    )
    args = parser.parse_args(argv)
    if args.scratch is None:
        parser.error("--scratch is required outside a Slurm job (SLURM_JOB_ID is not set)")
    options = StageOptions(
        raw=Path(args.raw),
        split=Path(args.split),
        scratch=Path(args.scratch),
        xodr_dir=Path(args.xodr_dir),
        workers=int(args.workers),
        predictions=None if args.predictions is None else Path(args.predictions),
        static=None if args.static is None else Path(args.static),
        port=int(args.port),
        lidar=not bool(args.no_lidar),
        allow_unpinned_xodr=bool(args.allow_unpinned_xodr),
    )
    try:
        stage(options)
    except StagingError as exc:
        print(f"stage_viewer: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
