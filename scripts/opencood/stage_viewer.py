#!/usr/bin/env python
# SPDX-License-Identifier: Apache-2.0
"""Stage OPV2V or V2XSet scenes for the viewer on a compute node (ADR 0012).

Reads the dataset in place (never written), writes everything under a scratch root that
must not be under /home, and prints the serve line. Steps, each timed:

0. plan: the adapter, the scenes of the split, the OpenDRIVE pins of every town in --xodr-dir;
1. poses pass: ingest the scenes with lidar and images switched off (fast) to get poses;
2. maps: ingest every pinned town into <scratch>/maps;
3. identify: name each scene's town by the ADR 0008 residual (coperceive maps identify rules);
4. ingest: the full scenes with the town table as --platform-table into <scratch>/scenes;
5. media: a <split> symlink in each scene directory into the dataset, so images resolve
   through --media-root without copying a byte;
6. align: measure the identified maps against the scenes; count points; print the serve line.

Both datasets can be staged into one scratch root by running the script twice; the maps
are ingested once. Licence of both datasets is unknown: nothing staged here may leave
the HPC or enter the repository (docs/adr/0012-left-handed-sources.md).
"""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Generator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from coperceive.adapters.build import ManifestAdapter  # noqa: E402
from coperceive.adapters.ingest import IngestError, format_report, ingest  # noqa: E402
from coperceive.adapters.spec import builtin_manifest_path, load_manifest  # noqa: E402
from coperceive.hdmap.align import align_maps  # noqa: E402
from coperceive.hdmap.commands import sha256_file  # noqa: E402
from coperceive.hdmap.identify import TownGuess, identify_towns, write_town_table  # noqa: E402
from coperceive.hdmap.opendrive import parse_opendrive  # noqa: E402
from coperceive.hdmap.store import map_slug, write_map  # noqa: E402
from scripts.griffin.stage_viewer import (  # noqa: E402
    XODR_SHA256,
    StagingError,
    check_scratch,
    point_counts,
)

DATASETS = ("opv2v", "v2xset")


class OpencoodStagingError(StagingError):
    """Staging stopped; the message says at which step."""


@dataclass(frozen=True)
class StageOptions:
    dataset: str
    raw: Path
    split: str
    scenes: tuple[str, ...]
    scratch: Path
    xodr_dir: Path
    static: Path | None = None
    workers: int = 1
    accept_m: float = 0.5
    margin_m: float = 2.0
    port: int = 8731
    allow_unpinned_xodr: bool = False


@dataclass(frozen=True)
class TownFile:
    town: str
    path: Path
    sha256: str
    pinned: bool


@dataclass(frozen=True)
class StagingReport:
    dataset: str
    scenes: tuple[str, ...]
    samples: tuple[tuple[str, int], ...]
    towns: tuple[TownGuess, ...]
    maps_ingested: tuple[str, ...]
    maps_aligned: tuple[tuple[str, str, float | None], ...]  # map, chosen, residual
    clouds_per_scene: tuple[tuple[str, int], ...]
    points_per_scene: tuple[tuple[str, int], ...]
    serve_command: str
    timings: tuple[tuple[str, float], ...]


def _say(text: str) -> None:
    print(text, flush=True)


@contextmanager
def _step(label: str, timings: list[tuple[str, float]]) -> Generator[None, None, None]:
    _say(f"== {label}")
    start = time.monotonic()
    yield
    seconds = time.monotonic() - start
    timings.append((label, seconds))
    _say(f"-- {label}: {seconds:.1f} s")


def adapter_for(dataset: str) -> ManifestAdapter:
    if dataset not in DATASETS:
        raise OpencoodStagingError(f"dataset must be one of {', '.join(DATASETS)}")
    return ManifestAdapter(load_manifest(builtin_manifest_path(dataset)))


def poses_only(adapter: ManifestAdapter) -> ManifestAdapter:
    """The same manifest without lidar and images: enough for town identification."""
    streams = replace(adapter.spec.streams, lidar=None, images=None)
    return ManifestAdapter(replace(adapter.spec, streams=streams))


def town_files(xodr_dir: Path, *, allow_unpinned: bool) -> tuple[TownFile, ...]:
    """Every ``<Town>.xodr`` in the directory, checked against the ADR 0008 pins."""
    files: list[TownFile] = []
    for path in sorted(xodr_dir.glob("*.xodr")):
        town = path.stem
        digest = sha256_file(path)
        pin = XODR_SHA256.get(town)
        pinned = pin is not None and digest == pin
        if not pinned:
            message = f"{path}: SHA-256 {digest} is not the ADR 0008 pin"
            if not allow_unpinned:
                raise OpencoodStagingError(f"{message}; pass --allow-unpinned-xodr to use it")
            _say(f"warning: {message}; used because of --allow-unpinned-xodr")
        files.append(TownFile(town, path, digest, pinned))
    if not files:
        raise OpencoodStagingError(f"{xodr_dir}: no .xodr files")
    return tuple(files)


def ingest_maps(files: Sequence[TownFile], maps_root: Path) -> tuple[str, ...]:
    """Parse each town once; a town already under ``maps_root`` is kept."""
    done: list[str] = []
    for item in files:
        map_name = f"Carla/Maps/{item.town}"
        directory = maps_root / map_slug(map_name)
        if (directory / "manifest.json").is_file():
            done.append(map_name)
            continue
        parsed = parse_opendrive(item.path, sample_step_m=1.0)
        write_map(
            parsed,
            directory,
            map_name=map_name,
            source={
                "format": "opendrive",
                "filename": item.path.name,
                "sha256": item.sha256,
                "retrieved_from": "",
                "licence": "MIT",
                "licence_note": "CARLA simulator, MIT licence; see docs/adr/0008-hd-map.md.",
            },
        )
        done.append(map_name)
    return tuple(done)


def media_link(scene_dir: Path, raw: Path, split: str) -> Path:
    """``<scene>/<split>`` -> ``<raw>/<split>``, so stored image paths resolve in place."""
    link = scene_dir / split
    target = (raw / split).resolve()
    if link.is_symlink():
        if link.resolve() != target:
            link.unlink()
            link.symlink_to(target, target_is_directory=True)
    elif link.exists():
        raise OpencoodStagingError(f"{link} exists and is not a link")
    else:
        link.symlink_to(target, target_is_directory=True)
    return link


def serve_command(options: StageOptions) -> str:
    parts = [
        "coperceive serve --host 127.0.0.1",
        f"--port {options.port}",
        f"--root {options.scratch / 'scenes'}",
        f"--maps {options.scratch / 'maps'}",
        f"--media-root {options.raw.resolve()}",
    ]
    if options.static is not None:
        parts.append(f"--static {options.static}")
    return " ".join(parts)


def stage(options: StageOptions) -> StagingReport:
    options = replace(options, scratch=check_scratch(options.scratch), raw=options.raw.resolve())
    if not options.raw.is_dir():
        raise OpencoodStagingError(f"{options.raw}: not a directory")
    timings: list[tuple[str, float]] = []
    scenes_root = options.scratch / "scenes"
    maps_root = options.scratch / "maps"
    poses_root = options.scratch / "poses-only" / options.dataset
    _say(f"dataset {options.dataset}  raw {options.raw}  split {options.split}")
    _say(f"scratch {options.scratch}")
    with _step("0/6 plan: adapter, scenes, OpenDRIVE pins", timings):
        adapter = adapter_for(options.dataset)
        refs = adapter.scenes(options.raw, {"split": options.split})
        known = {ref.scene_id: ref for ref in refs}
        missing = [s for s in options.scenes if s not in known]
        if missing:
            where = f"{options.dataset}/{options.split}"
            raise OpencoodStagingError(f"not scenes of {where}: {missing}")
        chosen = tuple(options.scenes) if options.scenes else tuple(known)
        samples = tuple((s, len(known[s].samples)) for s in chosen)
        for scene_id, count in samples:
            _say(f"scene {scene_id}: {count} samples")
        towns = town_files(options.xodr_dir, allow_unpinned=options.allow_unpinned_xodr)
        for item in towns:
            _say(f"{item.path} sha256 {item.sha256} {'pinned' if item.pinned else 'UNPINNED'}")
    options.scratch.mkdir(parents=True, exist_ok=True)
    with _step("1/6 poses pass (no lidar, no images)", timings):
        _run_ingest(poses_only(adapter), options, poses_root, chosen, platform=None)
    with _step("2/6 maps ingest", timings):
        ingested = ingest_maps(towns, maps_root)
        _say(f"maps: {', '.join(ingested)}")
    with _step("3/6 identify towns", timings):
        guesses = identify_towns(
            maps_root, poses_root, accept_m=options.accept_m, margin_m=options.margin_m
        )
        table = options.scratch / f"scene_towns-{options.dataset}.yaml"
        write_town_table(guesses, table)
        for guess in guesses:
            _say(
                f"town {guess.scene_id}: {guess.status} {guess.map_name or '-'} "
                f"({guess.reason}; {guess.poses} poses)"
            )
        platform = {g.scene_id: {"map_name": g.map_name} for g in guesses if g.map_name}
    with _step(f"4/6 ingest {options.dataset} with lidar, workers {options.workers}", timings):
        _run_ingest(adapter, options, scenes_root, chosen, platform=platform or None)
    with _step("5/6 media links into the dataset", timings):
        for scene_id in chosen:
            media_link(scenes_root / scene_id, options.raw, options.split)
    with _step("6/6 align maps, count points", timings):
        aligned: list[tuple[str, str, float | None]] = []
        if platform:
            for report in align_maps(maps_root, scenes_root):
                if report.get("measured") is True:
                    median = report.get("residual_median_m")
                    aligned.append(
                        (
                            str(report.get("map_name")),
                            str(report.get("chosen")),
                            float(median) if isinstance(median, (int, float)) else None,
                        )
                    )
                    _say(
                        f"map {report.get('map_name')}: chosen {report.get('chosen')}, "
                        f"residual median {median} m"
                    )
        clouds, points = point_counts(scenes_root, chosen)
        for scene_id, count in points:
            _say(f"points {scene_id}: {count} in {dict(clouds)[scene_id]} clouds")
    report = StagingReport(
        dataset=options.dataset,
        scenes=chosen,
        samples=samples,
        towns=tuple(guesses),
        maps_ingested=ingested,
        maps_aligned=tuple(aligned),
        clouds_per_scene=clouds,
        points_per_scene=points,
        serve_command=serve_command(options),
        timings=tuple(timings),
    )
    _say("timings: " + ", ".join(f"{label} {seconds:.1f} s" for label, seconds in timings))
    _say("serve:")
    _say(report.serve_command)
    return report


def _run_ingest(
    adapter: ManifestAdapter,
    options: StageOptions,
    out: Path,
    scene_ids: Sequence[str],
    *,
    platform: dict[str, dict[str, str]] | None,
) -> None:
    try:
        report = ingest(
            adapter,
            options.raw,
            out,
            params={"split": options.split},
            workers=max(1, options.workers),
            scene_ids=list(scene_ids),
            platform=platform,
        )
    except IngestError as exc:
        raise OpencoodStagingError(format_report(exc.report)) from None
    _say(format_report(report))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--dataset", required=True, choices=DATASETS)
    parser.add_argument("--raw", required=True, type=Path, help="dataset root, read only")
    parser.add_argument("--split", required=True)
    parser.add_argument("--scenes", default="", help="comma-separated scene ids; default all")
    parser.add_argument("--scratch", required=True, type=Path, help="under /local/$SLURM_JOB_ID")
    parser.add_argument("--xodr-dir", required=True, type=Path)
    parser.add_argument("--static", type=Path, default=None)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--accept-m", type=float, default=0.5)
    parser.add_argument("--margin-m", type=float, default=2.0)
    parser.add_argument("--port", type=int, default=8731)
    parser.add_argument("--allow-unpinned-xodr", action="store_true")
    args = parser.parse_args(argv)
    options = StageOptions(
        dataset=args.dataset,
        raw=args.raw,
        split=args.split,
        scenes=tuple(s for s in str(args.scenes).split(",") if s),
        scratch=args.scratch,
        xodr_dir=args.xodr_dir,
        static=args.static,
        workers=args.workers,
        accept_m=args.accept_m,
        margin_m=args.margin_m,
        port=args.port,
        allow_unpinned_xodr=args.allow_unpinned_xodr,
    )
    try:
        stage(options)
    except (StagingError, OSError, ValueError) as exc:
        print(f"staging failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
