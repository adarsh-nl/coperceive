# SPDX-License-Identifier: Apache-2.0
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
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


# --- D3: all towns with an ADR-0008 verified hash -------------------------
MIRROR_COMMIT = TOWN03_MIRROR_COMMIT
MIRROR_BASE = (
    f"https://raw.githubusercontent.com/hihuaweizhu/scenic-carla/{MIRROR_COMMIT}/maps/CARLA/"
)
TOWN_SHA256 = {
    "Town01": "97a7f6ac67812567e5c8ee0599cd823b23f80f30f3f97c502212e38b72e2b709",
    "Town02": "953c05f17def231239ffcadba3307628a82d0d335b67b4e29c3098f7aed1dd7d",
    "Town03": TOWN03_SHA256,
    "Town04": "4dddd1560ab7812b0e89cd4a523d8b6f2b93947eafb496d9eeb83cc772116c85",
    "Town05": "7edc53b0be840e177598da7a4ed9571146d7b2595ce280d154c90f92e8a48851",
    "Town06": "a90b613175fa837fdc128ddfaf5ddad25797b128fdbd8cd945974bc21a60496e",
    "Town07": "be730652769e993e27f748ba5793dba9e49d299205146a7a8db0bcd17af877bf",
    "Town10HD": "5d883b799f634030af92be1e9d79d107845540ba04338e8c60e095be1aef7be7",
}
TOWN_BYTES = {
    "Town01": 973_108,
    "Town02": 915_949,
    "Town03": 4_589_872,
    "Town04": 3_403_393,
    "Town05": 4_651_162,
    "Town06": 3_004_885,
    "Town07": 2_229_151,
    "Town10HD": 1_857_682,
}
VERIFIED_TOWNS = tuple(TOWN_SHA256)
# ADR 0008 has two strengths of evidence: four hashes measured from the CARLA
# 0.9.15 package, four pinned to byte-identical public mirrors only.
TOWN_VERIFICATION = {
    "Town01": "public-mirrors-not-package",
    "Town02": "public-mirrors-not-package",
    "Town03": "carla-0.9.15-package",
    "Town04": "public-mirrors-not-package",
    "Town05": "public-mirrors-not-package",
    "Town06": "carla-0.9.15-package",
    "Town07": "carla-0.9.15-package",
    "Town10HD": "carla-0.9.15-package",
}
VERIFICATION_LABEL = {
    "carla-0.9.15-package": "Verified against CARLA 0.9.15",
    "public-mirrors-not-package": "Matches public mirrors (not checked against the CARLA package)",
}


def town_map_name(town: str) -> str:
    return f"Carla/Maps/{town}"


def town_url(town: str) -> str:
    if town not in TOWN_SHA256:
        raise ValueError(f"no verified one-click source for {town!r}")
    return f"{MIRROR_BASE}{town}.xodr"


def _town_source(town: str, url: str, sha256: str) -> dict[str, Any]:
    return {
        "format": "opendrive",
        "filename": f"{town}.xodr",
        "sha256": sha256,
        "retrieved_from": url,
        "licence": LICENCE,
        "licence_url": LICENCE_URL,
        "licence_note": LICENCE_NOTE,
        "attribution": "CARLA 0.9.15 assets, Computer Vision Center (CVC), UAB",
        "verification": TOWN_VERIFICATION[town],
        "verified_against": VERIFICATION_LABEL[TOWN_VERIFICATION[town]],
    }


def _apply_town_to_scenes(scenes_root: Path, guesses: Any, map_name: str) -> int:
    """Write map_name into scene manifests for guesses naming this town."""
    import json

    changed = 0
    for guess in guesses:
        if guess.map_name != map_name:
            continue
        manifest_path = scenes_root / guess.scene_id / "manifest.json"
        if not manifest_path.is_file():
            continue
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        agents = payload.get("agents")
        if not isinstance(agents, list):
            continue
        for agent in agents:
            if not isinstance(agent, dict):
                continue
            platform = agent.get("platform")
            if not isinstance(platform, dict):
                platform = {}
            platform["map_name"] = map_name
            agent["platform"] = platform
        manifest_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        changed += 1
    return changed


def install_verified_town(
    town: str,
    maps_root: Path,
    scenes_root: Path,
    *,
    url: str | None = None,
    expected_sha256: str | None = None,
    progress: Progress | None = None,
) -> dict[str, Any]:
    """Install one town whose bytes match ADR 0008. Town03 reuses its installer."""
    if town not in VERIFIED_TOWNS:
        raise ValueError(f"no verified one-click source for {town!r}")
    if town == "Town03" and url is None and expected_sha256 is None:
        return install_town03(maps_root, scenes_root, progress=progress)
    expected = expected_sha256 or TOWN_SHA256[town]
    source_url = url or town_url(town)
    report = progress or _noop
    payload = _download(source_url, expected, report)
    maps_root.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{town.lower()}-install-", dir=maps_root.parent))
    slug = map_slug(town_map_name(town))
    try:
        staging_maps = staging / "maps"
        xodr = staging / f"{town}.xodr"
        xodr.write_bytes(payload)
        report("ingest", 45, "parsing OpenDRIVE")
        parsed = parse_opendrive(xodr, sample_step_m=1.0)
        write_map(
            parsed,
            staging_maps / slug,
            map_name=town_map_name(town),
            source=_town_source(town, source_url, expected),
        )
        report("ingest", 65, f"wrote {len(parsed.polylines)} polylines")
        report("align", 75, "aligning against the scene poses")
        reports = align_maps(staging_maps, scenes_root)
        alignment = next(
            (item for item in reports if item.get("map_name") == town_map_name(town)), None
        )
        if alignment is None or alignment.get("measured") is not True:
            from coperceive.hdmap.identify import identify_towns

            guesses = identify_towns(staging_maps, scenes_root)
            _apply_town_to_scenes(scenes_root, guesses, town_map_name(town))
            reports = align_maps(staging_maps, scenes_root)
            alignment = next(
                (item for item in reports if item.get("map_name") == town_map_name(town)), None
            )
        if alignment is None or alignment.get("measured") is not True:
            raise ValueError(
                f"the {town} map was ingested but no library scene identifies as it, "
                "so it could not be aligned; open a scene from this town and try again"
            )
        maps_root.mkdir(parents=True, exist_ok=True)
        destination = maps_root / slug
        backup: Path | None = None
        if destination.exists():
            backup = destination.with_name(f".{slug}.old-{staging.name}")
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
    report("done", 100, f"map installed at {maps_root / slug}")
    return {
        "map_name": town_map_name(town),
        "town": town,
        "maps_root": str(maps_root),
        "directory": str(maps_root / slug),
        "source": "CARLA 0.9.15 (scenic-carla mirror, SHA-256 verified)",
        "source_url": source_url,
        "licence": LICENCE,
        "licence_url": LICENCE_URL,
        "sha256": expected,
        "alignment": alignment,
    }
