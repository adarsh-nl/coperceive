# SPDX-License-Identifier: Apache-2.0
"""One-click install of the official CARLA Town03 OpenDRIVE map.

The bytes are fetched from a public mirror whose SHA-256 is the one measured for
the CARLA 0.9.15 package in ADR 0008 (and re-checked against the pinned hash
below), then parsed into the shared map layout and aligned against the scene
poses. No CARLA tarball is downloaded and no map bytes are committed.

Licence: the CARLA 0.9.15 README (assets section) says CARLA-specific assets are
CC-BY; it does not state a version. The source mirror's own notice says MIT, but
the town files travel in the CARLA assets, so this code records CC-BY and does
not claim a version.
"""

from __future__ import annotations

import hashlib
import shutil
import tempfile
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

from coperceive.hdmap.align import align_maps
from coperceive.hdmap.opendrive import parse_opendrive
from coperceive.hdmap.store import map_slug, write_map

TOWN03_MAP_NAME = "Carla/Maps/Town03"
TOWN03_MIRROR_COMMIT = "13d68ba8e9ea7c45f13250c37f6c2350829146f6"
TOWN03_URL = (
    "https://raw.githubusercontent.com/hihuaweizhu/scenic-carla/"
    f"{TOWN03_MIRROR_COMMIT}/maps/CARLA/Town03.xodr"
)
TOWN03_SHA256 = "abf177bcd9b66cfefb7ac4919f6456815f1384f4229f1343c8a6a90bbb7713c9"
TOWN03_BYTES = 4_589_872
MAX_MAP_BYTES = 20 * 1024 * 1024
LICENCE = "CC-BY"
LICENCE_URL = "https://raw.githubusercontent.com/carla-simulator/carla/0.9.15/README.md"
LICENCE_NOTE = (
    "CARLA 0.9.15 README: CARLA-specific assets are distributed under CC-BY; it "
    "does not state a CC-BY version. Official town OpenDRIVE files are packaged "
    "CARLA assets, not the MIT code. Source mirror bytes were checked against the "
    "SHA-256 measured from the CARLA 0.9.15 package (ADR 0008)."
)

Progress = Callable[[str, int, str], None]

MAP_SOURCE_UNAVAILABLE = (
    "Map source unavailable; install from a CARLA package with coperceive maps ingest"
)


class MapSourceUnavailable(ValueError):
    """The pinned mirror could not be fetched or did not produce the pinned bytes."""


def _noop(stage: str, percent: int, message: str) -> None:
    return None


def _download(url: str, expected_sha256: str, progress: Progress) -> bytes:
    progress("download", 5, f"downloading {url}")
    try:
        with urllib.request.urlopen(url, timeout=60) as response:  # noqa: S310 (pinned https URL)
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = response.read(1 << 16)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_MAP_BYTES:
                    raise MapSourceUnavailable(MAP_SOURCE_UNAVAILABLE) from None
                chunks.append(chunk)
    except (urllib.error.URLError, OSError) as exc:
        raise MapSourceUnavailable(MAP_SOURCE_UNAVAILABLE) from exc
    payload = b"".join(chunks)
    digest = hashlib.sha256(payload).hexdigest()
    if digest != expected_sha256:
        raise MapSourceUnavailable(MAP_SOURCE_UNAVAILABLE) from None
    progress("download", 35, f"downloaded {len(payload)} bytes, sha256 verified")
    return payload


def install_town03(
    maps_root: Path,
    scenes_root: Path,
    *,
    url: str = TOWN03_URL,
    expected_sha256: str = TOWN03_SHA256,
    progress: Progress | None = None,
) -> dict[str, Any]:
    """Download, verify, ingest and align Town03 into ``maps_root/Town03``."""
    report_progress = progress or _noop
    slug = map_slug(TOWN03_MAP_NAME)
    payload = _download(url, expected_sha256, report_progress)
    # Build and align in a staging directory on the same filesystem; only a
    # measured map is moved to maps_root/<slug>. A failed alignment therefore
    # leaves no unaligned map behind (and leaves a previous good map alone).
    maps_root.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".town03-install-", dir=maps_root.parent))
    try:
        staging_maps = staging / "maps"
        xodr = staging / "Town03.xodr"
        xodr.write_bytes(payload)
        report_progress("ingest", 45, "parsing OpenDRIVE")
        parsed = parse_opendrive(xodr, sample_step_m=1.0)
        source = {
            "format": "opendrive",
            "filename": "Town03.xodr",
            "sha256": expected_sha256,
            "retrieved_from": url,
            "licence": LICENCE,
            "licence_url": LICENCE_URL,
            "licence_note": LICENCE_NOTE,
            "attribution": "CARLA 0.9.15 assets, Computer Vision Center (CVC), UAB",
        }
        write_map(parsed, staging_maps / slug, map_name=TOWN03_MAP_NAME, source=source)
        report_progress("ingest", 65, f"wrote {len(parsed.polylines)} polylines")
        report_progress("align", 75, "aligning against the scene poses")
        reports = align_maps(staging_maps, scenes_root)
        alignment = next(
            (item for item in reports if item.get("map_name") == TOWN03_MAP_NAME), None
        )
        if alignment is None or alignment.get("measured") is not True:
            raise ValueError(
                "the map was ingested but no scene in this library names it, so it could not be "
                "aligned; open a Town03 scene and try again"
            )
        maps_root.mkdir(parents=True, exist_ok=True)
        destination = maps_root / slug
        backup: Path | None = None
        if destination.exists():
            backup = destination.with_name(f".{slug}.old-{staging.name}")
            if backup.exists():
                shutil.rmtree(backup, ignore_errors=True)
            destination.rename(backup)
        try:
            shutil.move(str(staging_maps / slug), str(destination))
        except OSError:
            if backup is not None:
                backup.rename(destination)
            raise
        if backup is not None:
            shutil.rmtree(backup, ignore_errors=True)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    report_progress("done", 100, f"map installed at {maps_root / slug}")
    return {
        "map_name": TOWN03_MAP_NAME,
        "maps_root": str(maps_root),
        "directory": str(maps_root / slug),
        "source": "CARLA 0.9.15",
        "licence": LICENCE,
        "licence_url": LICENCE_URL,
        "sha256": expected_sha256,
        "alignment": alignment,
    }
