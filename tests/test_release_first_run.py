# SPDX-License-Identifier: Apache-2.0
"""v0.1.0 first run, up to the network: Get data → Griffin "One scene with images".

docs/release/v0.1.0-checklist.md, check R1. The catalog offers the preset, the
dry-run measures exactly its archives from the dataset host's listing (a fake
host here: no network), the confirm step refuses to download until the terms
are accepted, and nothing is written outside the temporary library. The
download, conversion and the opened scene are the manual part of R1.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest
from coperceive.catalog import dry_run_preset, load_catalog
from coperceive.catalog import runner as catalog_runner
from coperceive.griffin.download import Hub, RemoteFile, download_griffin
from coperceive.griffin.licence import LicenceError

FOLDER = "datasets/griffin_50scenes_25m"
CAMERAS = [
    f"{side}_camera_{view}"
    for side, views in (
        ("drone", ("front", "back", "left", "right", "bottom")),
        ("vehicle", ("front", "back", "left", "right")),
    )
    for view in views
]
WANTED = ["drone_metadata", "vehicle_metadata", "vehicle_lidar", *CAMERAS]
LEFT_OUT = ["drone_instance_seg_front", "vehicle_instance_seg_front", "drone_lidar"]


class _Host(Hub):
    """The Griffin 25 m folder as the Hub lists it; sizes are made up."""

    def __init__(self) -> None:
        self.sizes = {name: 1_000 * (index + 1) for index, name in enumerate(WANTED + LEFT_OUT)}
        self.listed = 0

    def list_files(self, folder: str) -> list[RemoteFile]:
        assert f"datasets/{folder}" == FOLDER
        self.listed += 1
        files = [RemoteFile(f"{FOLDER}/md5.txt", 64)]
        files += [RemoteFile(f"{FOLDER}/{name}.zip", size) for name, size in self.sizes.items()]
        return files

    def read_dataset_card(self) -> str:
        return "---\nlicense: mit\n---\n"

    def read_toolkit_license(self) -> str:
        return "MIT License\n\nCopyright (c) [2025] [Jiahao Wang]\n"


@pytest.fixture
def fresh_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A first run: an empty home, so nothing reads or writes the real one."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_DATA_HOME", str(home / ".local" / "share"))
    yield home
    assert not (home / "coperceive-data").exists(), "touched the default library"


def _files(root: Path) -> list[str]:
    return sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())


def test_the_catalog_offers_one_scene_with_images_for_town03() -> None:
    griffin = next(ds for ds in load_catalog() if ds["id"] == "griffin")
    assert griffin["status"] == "ready" and griffin["requires_terms"] is True
    preset = next(p for p in griffin["presets"] if p["id"] == "one-scene")
    assert preset["name"] == "One scene with images"
    assert preset["open_scene"] == "Town03-000"
    components = preset["components"].split(",")
    assert "lidar" in components and "metadata" in components
    assert {c for c in components if "camera" in c} == set(CAMERAS)
    assert not [c for c in components if "instance" in c]


def test_the_dry_run_measures_exactly_the_presets_archives(
    fresh_home: Path, tmp_path: Path
) -> None:
    library = tmp_path / "library"
    host = _Host()
    result = dry_run_preset("griffin", "one-scene", library, hub=host)
    expected = 64 + sum(host.sizes[name] for name in WANTED)
    assert result["event"] == "dry_run" and result["preset_id"] == "one-scene"
    assert result["download_bytes"] == expected
    assert result["disk_bytes"] >= expected
    assert result["requires_terms"] is True  # a first run has not accepted them
    assert result["licence"] == "MIT"
    assert host.listed == 1
    # The dry-run writes nothing: no download, no acknowledgement.
    assert _files(library) == []
    assert not (fresh_home / ".local" / "share" / "coperceive").exists()


def test_confirm_without_accepting_the_terms_downloads_nothing(
    fresh_home: Path, tmp_path: Path
) -> None:
    library = tmp_path / "library"
    host = _Host()

    def offline(*args: object, **kwargs: object) -> object:
        return download_griffin(*args, hub=host, **kwargs)  # type: ignore[arg-type]

    out = StringIO()
    with (
        patch.object(catalog_runner, "download_griffin", offline),
        pytest.raises(LicenceError),
    ):
        catalog_runner.run_dataset_job("griffin", "one-scene", library, accept_terms=False, out=out)
    events = [json.loads(line) for line in out.getvalue().splitlines() if line.strip()]
    # The app shows this line under the licence checkbox.
    assert events[-1] == {
        "event": "error",
        "message": "Tick the Griffin licence acknowledgement, then try the download again.",
    }, events
    assert not [path for path in _files(library) if path.endswith(".zip")]
    assert not (fresh_home / ".local" / "share" / "coperceive").exists()
