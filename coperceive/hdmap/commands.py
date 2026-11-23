# SPDX-License-Identifier: Apache-2.0
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
"""``coperceive maps`` : fetch official CARLA OpenDRIVE files, ingest, align."""

from __future__ import annotations

import argparse
import hashlib
import sys
import tarfile
import urllib.request
from pathlib import Path
from typing import IO, Any

from coperceive.hdmap.align import align_maps
from coperceive.hdmap.identify import (
    DEFAULT_ACCEPT_M,
    DEFAULT_GAP_M,
    DEFAULT_RATIO,
    identify_towns,
    write_town_table,
)
from coperceive.hdmap.install import (
    MAX_MAP_BYTES,
    TOWN_BYTES,
    TOWN_SHA256,
    TOWN_VERIFICATION,
    VERIFICATION_LABEL,
    VERIFIED_TOWNS,
    Progress,
    install_town03,
    install_verified_town,
    town_url,
)
from coperceive.hdmap.opendrive import parse_opendrive
from coperceive.hdmap.store import map_slug, write_map

CARLA_VERSION = "0.9.15"
CARLA_PACKAGE_URL = "https://downloads.carlasim.com/Linux/CARLA_0.9.15.tar.gz"
CARLA_TOWNS = ("Town01", "Town02", "Town03", "Town04", "Town05", "Town06", "Town07", "Town10HD")
_LICENCE = "CC-BY"
_LICENCE_NOTE = (
    "Town OpenDRIVE files are in the CARLA 0.9.15 package, not in the git tag. "
    "The 0.9.15 README says CARLA-specific code is MIT and CARLA-specific assets "
    "are CC-BY. It does not give a CC-BY version. "
    "https://github.com/carla-simulator/carla/blob/0.9.15/README.md "
    "checked 2026-10-25. The .xodr bytes are not committed here."
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

    install_town = maps_sub.add_parser(
        "install", help="Download, verify, ingest and align one CARLA town."
    )
    install_town.add_argument("--town", required=True, choices=VERIFIED_TOWNS)
    install_town.add_argument("--maps", required=True)
    install_town.add_argument("--scenes", required=True)
    install_town.add_argument("--url", default=None)
    install_town.add_argument("--progress-json", action="store_true")
    install_town.set_defaults(func=install_town_command)

    list_towns = maps_sub.add_parser("list", help="List verified towns and installed maps.")
    list_towns.add_argument("--maps", required=True)
    list_towns.set_defaults(func=list_command)

    remove = maps_sub.add_parser("remove", help="Remove one installed town map.")
    remove.add_argument("--town", required=True, choices=VERIFIED_TOWNS)
    remove.add_argument("--maps", required=True)
    remove.set_defaults(func=remove_command)

    install = maps_sub.add_parser(
        "install-town03",
        help="Download, verify, ingest and align Town03 (CARLA 0.9.15 assets).",
    )
    install.add_argument("--maps", required=True, help="Maps root (library_root/maps).")
    install.add_argument("--scenes", required=True, help="Library/data root with scenes.")
    install.add_argument("--url", default=None, help="Override the pinned mirror (tests).")
    install.add_argument(
        "--progress-json",
        action="store_true",
        help="Print one JSON progress event per line for the desktop app.",
    )
    install.set_defaults(func=install_command)

    identify = maps_sub.add_parser(
        "identify",
        help="Name each scene's town by the alignment residual; write a platform table.",
    )
    identify.add_argument("--maps", required=True, help="Maps root written by ingest.")
    identify.add_argument("--scenes", required=True, help="Directory of schema 1 scenes.")
    identify.add_argument("--out", required=True, help="YAML platform table to write.")
    identify.add_argument("--accept-m", type=float, default=DEFAULT_ACCEPT_M)
    identify.add_argument("--ratio", type=float, default=DEFAULT_RATIO)
    identify.add_argument("--gap-m", type=float, default=DEFAULT_GAP_M)
    identify.set_defaults(func=identify_command)


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
    try:
        written = extract_towns(package, dest)
    except (OSError, ValueError, tarfile.TarError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2) from exc
    for town, path in written:
        print(f"wrote {path} sha256 {sha256_file(path)} map_name Carla/Maps/{town}")
    if len(written) != len(CARLA_TOWNS):
        found = ", ".join(town for town, _path in written) or "none"
        print(f"expected {', '.join(CARLA_TOWNS)}; found {found}", file=sys.stderr)
        raise SystemExit(2)


def extract_towns(package: Path, dest: Path) -> list[tuple[str, Path]]:
    """Pull the four town OpenDRIVE files out of an official CARLA package.

    Each member is copied in chunks and refused (ValueError) over ``MAX_MAP_BYTES``, the
    cap the pinned installs use; the largest official town is under 5 MB. A refused member
    leaves an earlier file of the same name as it was.

    tarfile itself reads a GNU long-name/long-link payload and a pax header payload whole,
    before any member reaches this loop, and follows a GNU sparse map through any number of
    extension blocks; it also keeps every member header it has read. So the archive is read
    with ``_BoundedTarFile``: a long-name or pax payload over ``MAX_TAR_HEADER_BYTES``, more
    than ``MAX_TAR_HEADER_CHAIN`` such headers in front of one member, and any GNU sparse
    member are refused (ValueError), and the list of headers read so far is dropped after
    each member. Memory therefore stays bounded whatever the package holds. What is not
    bounded is time: a package of millions of empty members is read to its end.
    """
    wanted = {f"{town}.xodr": town for town in CARLA_TOWNS}
    found: dict[str, Path] = {}
    with _BoundedTarFile.open(package, "r:*") as archive:
        while (member := archive.next()) is not None:
            # TarFile.next() appends each header to .members; extractfile() of a regular
            # file does not need them, and two million empty members would cost ~800 MB.
            archive.members.clear()
            name = Path(member.name).name
            town = wanted.get(name)
            if town is None or not member.isfile():
                continue
            if member.size > MAX_MAP_BYTES:
                raise ValueError(_oversized(member.name, member.size))
            handle = archive.extractfile(member)
            if handle is None:
                continue
            target = dest / name
            _copy_capped(handle, target, member.name)
            found[town] = target
    return [(town, found[town]) for town in CARLA_TOWNS if town in found]


# A path or link name is at most PATH_MAX (4096) bytes on Linux, and the pax records of a
# real member (path, linkpath, times, uid/gid names) are a few hundred bytes.
MAX_TAR_HEADER_BYTES = 64 * 1024
# Header extensions in front of one member: a pax global, a pax extended, a GNU long name
# and a GNU long link is four. tarfile handles each one by recursion.
MAX_TAR_HEADER_CHAIN = 8
_LONG_TYPES = (tarfile.GNUTYPE_LONGNAME, tarfile.GNUTYPE_LONGLINK)
_PAX_TYPES = (tarfile.XHDTYPE, tarfile.XGLTYPE, tarfile.SOLARIS_XHDTYPE)


class TarHeaderError(ValueError):
    """A tar header extension that an OpenDRIVE package has no use for, or too large."""


class _BoundedTarInfo(tarfile.TarInfo):
    """A TarInfo that refuses header payloads tarfile would otherwise read whole."""

    def _proc_member(self, archive: Any) -> Any:
        # Called by TarInfo.fromtarfile() for every header, before tarfile reads the
        # header's payload.
        kind = self.type
        if kind == tarfile.GNUTYPE_SPARSE:
            raise TarHeaderError(_sparse(self.name))
        if kind not in _LONG_TYPES and kind not in _PAX_TYPES:
            return super()._proc_member(archive)  # pyright: ignore[reportAttributeAccessIssue]
        label = "long name header" if kind in _LONG_TYPES else "pax header"
        if self.size > MAX_TAR_HEADER_BYTES:
            raise TarHeaderError(
                f"a tar {label} of {self.size} bytes is over the {MAX_TAR_HEADER_BYTES} "
                f"byte limit; this is not the official CARLA {CARLA_VERSION} package"
            )
        chain: int = archive.header_chain + 1
        if chain > MAX_TAR_HEADER_CHAIN:
            raise TarHeaderError(
                f"more than {MAX_TAR_HEADER_CHAIN} tar header extensions in front of one "
                f"member; this is not the official CARLA {CARLA_VERSION} package"
            )
        archive.header_chain = chain
        try:
            return super()._proc_member(archive)  # pyright: ignore[reportAttributeAccessIssue]
        finally:
            archive.header_chain = chain - 1

    def _proc_gnusparse_10(self, next: Any, pax_headers: Any, tarfile: Any) -> None:
        # pax format 1.0 sparse: the map is read from the data blocks until it has the
        # number of entries the first line claims, with no bound.
        raise TarHeaderError(_sparse(self.name))


def _sparse(member: str) -> str:
    return (
        f"{member}: a GNU sparse member is not in an official CARLA {CARLA_VERSION} "
        "package; refusing it"
    )


class _BoundedTarFile(tarfile.TarFile):
    tarinfo = _BoundedTarInfo
    members: list[tarfile.TarInfo]  # set by TarFile.__init__; not in typeshed
    header_chain = 0


_COPY_CHUNK = 1 << 20


def _oversized(member: str, size: int) -> str:
    return (
        f"{member} in the package is {size} bytes, over the {MAX_MAP_BYTES} byte limit "
        f"for an OpenDRIVE file; this is not the official CARLA {CARLA_VERSION} package"
    )


def _copy_capped(handle: IO[bytes], target: Path, member: str) -> None:
    """Copy ``handle`` to ``target`` through a partial file, at most ``MAX_MAP_BYTES``."""
    partial = target.with_name(f".{target.name}.part")
    written = 0
    try:
        with partial.open("wb") as out:
            while chunk := handle.read(_COPY_CHUNK):
                written += len(chunk)
                if written > MAX_MAP_BYTES:
                    raise ValueError(_oversized(member, written))
                out.write(chunk)
        partial.replace(target)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise


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


def install_command(args: argparse.Namespace) -> None:
    import json

    from coperceive.hdmap.install import TOWN03_SHA256, TOWN03_URL

    progress: Progress | None = None
    if args.progress_json:

        def emit_progress(stage: str, percent: int, message: str) -> None:
            print(
                json.dumps(
                    {"stage": stage, "percent": percent, "message": message},
                    ensure_ascii=False,
                ),
                flush=True,
            )

        progress = emit_progress

    try:
        report = install_town03(
            Path(args.maps),
            Path(args.scenes),
            url=args.url or TOWN03_URL,
            expected_sha256=TOWN03_SHA256,
            progress=progress,
        )
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2) from exc
    if args.progress_json:
        print(json.dumps({"event": "result", "report": report}), flush=True)
    else:
        print(json.dumps(report, indent=2))


def install_town_command(args: argparse.Namespace) -> None:
    import json

    progress: Progress | None = None
    if args.progress_json:

        def emit(stage: str, percent: int, message: str) -> None:
            print(json.dumps({"stage": stage, "percent": percent, "message": message}), flush=True)

        progress = emit
    try:
        report = install_verified_town(
            args.town,
            Path(args.maps),
            Path(args.scenes),
            url=args.url,
            progress=progress,
        )
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2) from exc
    if args.progress_json:
        print(json.dumps({"event": "result", "report": report}), flush=True)
    else:
        print(json.dumps(report, indent=2))


def list_command(args: argparse.Namespace) -> None:
    import json

    from coperceive.hdmap.store import load_map

    maps_root = Path(args.maps)
    installed: dict[str, dict[str, object]] = {}
    if maps_root.is_dir():
        for path in maps_root.iterdir():
            if path.is_dir() and (path / "manifest.json").is_file():
                try:
                    hdmap = load_map(path)
                except ValueError:
                    continue
                size = sum(item.stat().st_size for item in path.rglob("*") if item.is_file())
                installed[hdmap.map_name] = {
                    "map_name": hdmap.map_name,
                    "directory": str(path),
                    "bytes": size,
                    "source": hdmap.source,
                }
    table = []
    for town in VERIFIED_TOWNS:
        name = f"Carla/Maps/{town}"
        table.append(
            {
                "town": town,
                "map_name": name,
                "download_bytes": TOWN_BYTES[town],
                "sha256": TOWN_SHA256[town],
                "source_url": town_url(town),
                "licence": "CC-BY",
                "verified": TOWN_VERIFICATION[town] == "carla-0.9.15-package",
                "verification": TOWN_VERIFICATION[town],
                "verification_label": VERIFICATION_LABEL[TOWN_VERIFICATION[town]],
                "installed": name in installed,
                "installed_info": installed.get(name),
            }
        )
    print(json.dumps({"schema": "coperceive-carla-maps-1", "towns": table}, indent=2))


def remove_command(args: argparse.Namespace) -> None:
    import shutil

    from coperceive.hdmap.store import map_slug

    slug = map_slug(f"Carla/Maps/{args.town}")
    target = Path(args.maps) / slug
    if not target.is_dir():
        print(f"not installed: {slug}", file=sys.stderr)
        raise SystemExit(2)
    shutil.rmtree(target)
    print(f"removed {target}")


def identify_command(args: argparse.Namespace) -> None:
    try:
        guesses = identify_towns(
            Path(args.maps),
            Path(args.scenes),
            accept_m=float(args.accept_m),
            ratio=float(args.ratio),
            gap_m=float(args.gap_m),
        )
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2) from exc
    if not guesses:
        print("no scenes", file=sys.stderr)
        raise SystemExit(2)
    write_town_table(guesses, Path(args.out))
    for guess in guesses:
        town = guess.map_name or "-"
        print(f"{guess.scene_id} {guess.status} {town} ({guess.reason}; {guess.poses} poses)")
    identified = sum(1 for g in guesses if g.map_name is not None)
    print(f"identified {identified} of {len(guesses)} scenes; wrote {args.out}")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1 << 20)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()
