# SPDX-License-Identifier: Apache-2.0
"""``scripts/griffin/check_staged_scenes.py`` in headless Chromium against a staged root.

The viewer opens on the first scene in name order. The check must open the scene it was
asked for whatever the page opened on: staging job 629165 asked for the OPV2V scene first
while the V2XSet scene sorted first, and the check waited 60 s for a scene the picker had
never been asked to select. Skipped like the other browser tests when no Chromium starts.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path

from coperceive.model import (
    SCHEMA_VERSION,
    Agent,
    AgentType,
    Clock,
    ImageRef,
    Modality,
    PointCloud,
    Scene,
    Sensor,
    StampedPose,
)
from coperceive.se3 import SE3
from coperceive.store import write_scene
from playwright.sync_api import Error as PlaywrightError
from scripts.griffin import check_staged_scenes as check

from tests.test_viewer_browser import (
    _PNG,  # pyright: ignore[reportPrivateUsage]
    _bundle_dir,  # pyright: ignore[reportPrivateUsage]
    _unavailable,  # pyright: ignore[reportPrivateUsage]
)

FIRST_BY_NAME = "2021_08_20_20_39_00"  # the page opens on this one
ASKED_FIRST = "2021_08_20_21_10_24"  # the check is asked for this one first
SAMPLES = 3


def _scene(scene_id: str) -> Scene:
    """One vehicle, three samples, two lidar points and one camera image at every sample."""
    poses: list[StampedPose] = []
    clouds: list[PointCloud] = []
    images: list[ImageRef] = []
    for index in range(SAMPLES):
        time_us = index * 100_000
        poses.append(StampedPose(time_us, SE3.identity().translation(float(index), 0.0, 0.0)))
        clouds.append(
            PointCloud(
                "vehicle",
                "lidar_top",
                time_us,
                2,
                struct.pack("<8f", 5.0, 1.0, 0.2, 0.5, 6.0, -1.0, 0.2, 0.5),
            )
        )
        path = f"vehicle/cam_front/{time_us:07d}.png"
        images.append(ImageRef("vehicle", "cam_front", time_us, path))
    sensors = (
        Sensor("lidar_top", "vehicle", Modality.LIDAR, SE3.identity().translation(0.0, 0.0, 1.5)),
        Sensor(
            "cam_front",
            "vehicle",
            Modality.CAMERA,
            SE3.identity().translation(1.2, 0.0, 1.4),
            1280,
            720,
            1000.0,
            1000.0,
            640.0,
            360.0,
        ),
    )
    return Scene(
        schema_version=SCHEMA_VERSION,
        scene_id=scene_id,
        world_frame="carla-map",
        clock=Clock("us", "check fixture, 10 Hz"),
        agents=(Agent("vehicle", AgentType.VEHICLE, tuple(poses)),),
        sensors=sensors,
        annotations=(),
        objects=(),
        point_clouds=tuple(clouds),
        images=tuple(images),
    )


def _write(root: Path, scene_id: str) -> None:
    scene = _scene(scene_id)
    write_scene(scene, root / scene_id)
    for image in scene.images:
        target = root / scene_id / image.path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(_PNG)


def test_check_opens_the_scene_it_was_asked_for_whatever_the_page_opened_on(
    tmp_path: Path,
) -> None:
    bundle = _bundle_dir()
    if bundle is None:
        _unavailable("no web bundle: set COPERCEIVE_WEB_DIST to a vite build directory")
    scenes = tmp_path / "scenes"
    maps = tmp_path / "maps"
    maps.mkdir()
    _write(scenes, FIRST_BY_NAME)
    _write(scenes, ASKED_FIRST)
    out = tmp_path / "viewer-check"
    argv = [
        "--root",
        str(scenes),
        "--maps",
        str(maps),
        "--static",
        str(bundle),
        "--scenes",
        ASKED_FIRST,
        FIRST_BY_NAME,
        "--allow-no-map",
        "--out",
        str(out),
    ]
    try:
        rc = check.main(argv)
    except PlaywrightError as exc:  # no browser build or missing system libraries
        _unavailable(f"chromium cannot start here: {str(exc).splitlines()[0]}")
    assert rc == 0
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    rows = report["scenes"]
    assert [row["scene_id"] for row in rows] == [ASKED_FIRST, FIRST_BY_NAME]
    for row in rows:
        assert str(row["status"]).startswith(row["scene_id"]), row["status"]
        assert row["points"] == 2, row["status"]
        assert row["map_drawn"] is False
        assert row["thumbnails"] >= 1
        # No predictions root here: the page's one probe of it answers 404 and the pill
        # names the gap. Nothing else may fail.
        assert [url.rsplit("/", 1)[-1] for url in row["http_errors"]] == ["predictions"]
        pills = row["missing_pill"]
        assert any(pill.startswith("Predictions: no predictions") for pill in pills)
        assert (out / f"{row['scene_id']}.png").is_file()
        assert (out / f"{row['scene_id']}-split.png").is_file()
    assert report["page_errors"] == []
