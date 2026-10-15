# SPDX-License-Identifier: Apache-2.0
"""``coperceive maps`` : fetch official CARLA OpenDRIVE files, ingest, align."""

from __future__ import annotations

import argparse
import hashlib
import sys
import tarfile
import urllib.request
from pathlib import Path
from typing import Any

from coperceive.hdmap.align import align_maps
from coperceive.hdmap.opendrive import parse_opendrive
from coperceive.hdmap.store import map_slug, write_map

CARLA_VERSION = "0.9.15"
CARLA_PACKAGE_URL = "https://downloads.carlasim.com/Linux/CARLA_0.9.15.tar.gz"
CARLA_TOWNS = ("Town03", "Town06", "Town07", "Town10HD")
_LICENCE = "MIT"
_LICENCE_NOTE = (
    "CARLA simulator, MIT licence, https://github.com/carla-simulator/carla. "
    f"Package CARLA_{CARLA_VERSION}. The .xodr bytes are not committed here."
)


def add_parser(sub: Any) -> None:
    maps = sub.add_parser("maps", help="Ingest a shared HD map and align it to scenes.")
    maps_sub = maps.add_subparsers(dest="maps_command", required=True)

    fetch = maps_sub.add_parser(
        "fetch",
        help="Extract official CARLA OpenDRIVE files for Town03, Town06, Town07, and Town10HD.",
    )
    fetch.add_argument(
        "--dest", required=True, help="Directory for the .xodr files. Not the git tree."
    )
    fetch.add_argument(
        "--package",
        default=None,
        help="Local CARLA tar.gz. Omit to download the pinned 0.9.15 package.",
    )
    fetch.set_defaults(func=fetch_command)

    ingest = maps_sub.add_parser("ingest", help="Parse one .xodr into a map directory.")
    ingest.add_argument("--xodr", required=True)
    ingest.add_argument("--map-name", required=True, help="For example Carla/Maps/Town03.")
    ingest.add_argument(
        "--out", required=True, help="Maps root. The slug is the last path segment."
    )
    ingest.add_argument("--step-m", type=float, default=1.0)
    ingest.add_argument("--source-url", default="")
    ingest.set_defaults(func=ingest_command)

    align = maps_sub.add_parser(
        "align",
        help="Store world_from_opendrive by measuring vehicle poses against lane centres.",
    )
    align.add_argument("--maps", required=True, help="Maps root written by ingest.")
    align.add_argument("--scenes", required=True, help="Directory of schema 1 scenes.")
    align.set_defaults(func=align_command)


def fetch_command(args: argparse.Namespace) -> None:
    dest = Path(args.dest)
    dest.mkdir(parents=True, exist_ok=True)
    package = Path(args.package) if args.package else dest / f"CARLA_{CARLA_VERSION}.tar.gz"
    if args.package is None and not package.is_file():
        print(f"downloading {CARLA_PACKAGE_URL}")
        urllib.request.urlretrieve(CARLA_PACKAGE_URL, package)
    if not package.is_file():
        print(f"package not found: {package}", file=sys.stderr)
        raise SystemExit(2)
    written = extract_towns(package, dest)
    for town, path in written:
        print(f"wrote {path} sha256 {sha256_file(path)} map_name Carla/Maps/{town}")
    if len(written) != len(CARLA_TOWNS):
        found = ", ".join(town for town, _path in written) or "none"
        print(f"expected {', '.join(CARLA_TOWNS)}; found {found}", file=sys.stderr)
        raise SystemExit(2)


def extract_towns(package: Path, dest: Path) -> list[tuple[str, Path]]:
    """Pull the four town OpenDRIVE files out of an official CARLA package."""
    wanted = {f"{town}.xodr": town for town in CARLA_TOWNS}
    found: dict[str, Path] = {}
    with tarfile.open(package, "r:*") as archive:
        for member in archive:
            name = Path(member.name).name
            town = wanted.get(name)
            if town is None or not member.isfile():
                continue
            handle = archive.extractfile(member)
            if handle is None:
                continue
            target = dest / name
            target.write_bytes(handle.read())
            found[town] = target
    return [(town, found[town]) for town in CARLA_TOWNS if town in found]


def ingest_command(args: argparse.Namespace) -> None:
    xodr = Path(args.xodr)
    if not xodr.is_file():
        print(f"not a file: {xodr}", file=sys.stderr)
        raise SystemExit(2)
    try:
        slug = map_slug(str(args.map_name))
        parsed = parse_opendrive(xodr, sample_step_m=float(args.step_m))
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2) from exc
    directory = Path(args.out) / slug
    source = {
        "format": "opendrive",
        "filename": xodr.name,
        "sha256": sha256_file(xodr),
        "retrieved_from": str(args.source_url),
        "licence": _LICENCE,
        "licence_note": _LICENCE_NOTE,
    }
    write_map(parsed, directory, map_name=str(args.map_name), source=source)
    print(f"wrote {directory}")
    print(f"polylines {len(parsed.polylines)}")


def align_command(args: argparse.Namespace) -> None:
    maps_root = Path(args.maps)
    scenes_root = Path(args.scenes)
    try:
        reports = align_maps(maps_root, scenes_root)
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2) from exc
    if not reports:
        print("no map directories", file=sys.stderr)
        raise SystemExit(2)
    for report in reports:
        name = report.get("map_name")
        if report.get("measured") is not True:
            print(f"{name} not measured: {report.get('reason')}")
            continue
        print(
            f"{name} chosen {report.get('chosen')} "
            f"median_m {report.get('residual_median_m')} "
            f"heading_deg {report.get('heading_median_deg')} "
            f"translation {report.get('translation_applied')}"
        )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1 << 20)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()
