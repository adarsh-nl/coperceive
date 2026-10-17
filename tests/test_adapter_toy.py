# SPDX-License-Identifier: Apache-2.0
"""The toy dataset from the "add a dataset in 20 minutes" doc, end to end."""

from __future__ import annotations

import struct
from pathlib import Path

import pytest
import yaml
from coperceive.adapters.build import ManifestAdapter
from coperceive.adapters.ingest import ingest
from coperceive.adapters.spec import parse_manifest
from coperceive.cli import main
from coperceive.model import Modality
from coperceive.store import read_scene

from tests.toy_dataset import MANIFEST, write_toy


def _adapter() -> ManifestAdapter:
    return ManifestAdapter(parse_manifest(yaml.safe_load(MANIFEST), source="toy"))


def test_scenes_and_agents_are_discovered_from_folders(tmp_path: Path) -> None:
    write_toy(tmp_path, scenes=2, frames=3)
    refs = _adapter().scenes(tmp_path, {})
    assert [ref.scene_id for ref in refs] == ["scene_0001", "scene_0002"]
    assert refs[0].samples == (0, 1, 2)
    scene = _adapter().load(tmp_path, refs[0])
    assert [a.agent_id for a in scene.agents] == ["car_a", "car_b"]


def test_clock_from_a_seconds_field_rounds_to_microseconds(tmp_path: Path) -> None:
    write_toy(tmp_path, scenes=1, frames=3)
    scene = _adapter().load(tmp_path, _adapter().scenes(tmp_path, {})[0])
    assert scene.pose_times("car_a") == (100_000_000, 100_100_000, 100_200_000)
    assert scene.clock.scale_note.startswith("stamp_s")


def test_body_axes_are_remapped_from_rfu_to_flu(tmp_path: Path) -> None:
    write_toy(tmp_path, scenes=1, frames=1)
    scene = _adapter().load(tmp_path, _adapter().scenes(tmp_path, {})[0])
    pose = scene.agent("car_a").poses[0].world_from_body
    # Source: body y is forward, yaw 90 deg, origin (1, 2, 0). One metre ahead
    # of the car is source body (0, 1, 0) -> world (0, 2, 0). In FLU, ahead is +x.
    assert pose.apply((1.0, 0.0, 0.0)) == pytest.approx((0.0, 2.0, 0.0), abs=1e-9)
    assert pose.apply((0.0, 0.0, 0.0)) == pytest.approx((1.0, 2.0, 0.0), abs=1e-9)
    straight = scene.agent("car_b").poses[0].world_from_body
    assert straight.apply((1.0, 0.0, 0.0)) == pytest.approx((11.0, 3.0, 0.0), abs=1e-9)


def test_sensors_use_translation_plus_euler_and_explicit_image_size(tmp_path: Path) -> None:
    write_toy(tmp_path, scenes=1, frames=1)
    scene = _adapter().load(tmp_path, _adapter().scenes(tmp_path, {})[0])
    cam = scene.sensor("car_a", "cam_front")
    assert cam.modality is Modality.CAMERA
    assert (cam.fx, cam.fy, cam.cx, cam.cy) == (700.0, 700.0, 640.0, 360.0)
    assert (cam.image_width_px, cam.image_height_px) == (1280, 720)
    # The camera sits 0.5 right, 1.0 forward, 1.2 up and looks forward. One
    # metre along its optical axis (+z in RDF) is FLU body (2.0, -0.5, 1.2).
    assert cam.body_from_sensor.apply((0.0, 0.0, 1.0)) == pytest.approx((2.0, -0.5, 1.2), abs=1e-9)
    # Camera up (-y in RDF) is body +z.
    assert cam.body_from_sensor.apply((0.0, -1.0, 0.0)) == pytest.approx((1.0, -0.5, 2.2), abs=1e-9)
    lidar = scene.sensor("car_a", "velodyne")
    assert lidar.modality is Modality.LIDAR
    assert lidar.fx is None and lidar.image_width_px is None
    assert lidar.body_from_sensor.apply((1.0, 0.0, 0.0)) == pytest.approx((1.0, 0.0, 1.7), abs=1e-9)


def test_boxes_bottom_centre_named_columns_yaw_only_and_class_map(tmp_path: Path) -> None:
    write_toy(tmp_path, scenes=1, frames=1)
    scene = _adapter().load(tmp_path, _adapter().scenes(tmp_path, {})[0])
    car, person = [a for a in scene.annotations if a.agent_id == "car_a"]
    assert car.category == "car" and person.category == "pedestrian"
    assert car.size_lwh == (4.0, 1.8, 1.5)
    # Source RFU bottom centre (3, 10, 0): FLU (10, -3, 0) plus half the height.
    assert car.center == pytest.approx((10.0, -3.0, 0.75))
    assert car.track_id == "7"
    # yaw 90 about z in RFU, conjugated into FLU, is still a 90 degree turn.
    assert car.box_from_rpy.apply((1.0, 0.0, 0.0)) == pytest.approx((0.0, 1.0, 0.0), abs=1e-9)
    assert person.center == pytest.approx((5.0, 1.0, 0.85))
    assert scene.objects[0].track_id == "7"  # both agents annotate track 7 as car


def test_unknown_class_is_an_error_when_the_map_says_so(tmp_path: Path) -> None:
    write_toy(tmp_path, scenes=1, frames=1)
    (tmp_path / "scene_0001" / "car_a" / "labels" / "000000.txt").write_text(
        "Bus 3 2.5 10 0 20 0 0 9\n", encoding="utf-8"
    )
    with pytest.raises(Exception, match="Bus"):
        _adapter().load(tmp_path, _adapter().scenes(tmp_path, {})[0])


def test_bin_lidar_points_are_remapped_and_images_are_refs(tmp_path: Path) -> None:
    write_toy(tmp_path, scenes=1, frames=2)
    scene = _adapter().load(tmp_path, _adapter().scenes(tmp_path, {})[0])
    cloud = scene.point_clouds[0]
    assert (cloud.agent_id, cloud.sensor_id, cloud.count) == ("car_a", "velodyne", 2)
    points = struct.unpack("<8f", cloud.xyz_i_f32)
    assert points[:4] == pytest.approx((2.0, -1.0, 3.0, 0.5))
    assert points[4:] == pytest.approx((10.0, 0.0, 0.0, 0.25))
    refs = [i for i in scene.images if i.agent_id == "car_b"]
    assert [i.path for i in refs] == [
        "scene_0001/car_b/image/cam_front/000000.png",
        "scene_0001/car_b/image/cam_front/000001.png",
    ]


def test_cli_check_and_ingest_the_toy(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    raw = tmp_path / "raw"
    write_toy(raw, scenes=2, frames=2)
    manifest = tmp_path / "toy.yaml"
    manifest.write_text(MANIFEST, encoding="utf-8")
    main(["adapters", "check", str(manifest), "--raw-root", str(raw)])
    out = capsys.readouterr().out
    assert "scenes 2" in out and "scene_0002\tagents=2" in out
    main(
        [
            "adapters",
            "ingest",
            str(manifest),
            "--raw-root",
            str(raw),
            "--out",
            str(tmp_path / "out"),
            "--workers",
            "1",
        ]
    )
    assert "wrote=2" in capsys.readouterr().out
    scene = read_scene(tmp_path / "out" / "scene_0002")
    assert len(scene.annotations) == 8
    report = ingest(_adapter(), raw, tmp_path / "out", params={}, workers=1)
    assert report.skipped == ("scene_0001", "scene_0002")


def test_the_doc_quotes_the_toy_manifest_verbatim() -> None:
    doc = Path(__file__).resolve().parents[1] / "docs" / "datasets" / "add-a-dataset.md"
    assert MANIFEST.strip() in doc.read_text(encoding="utf-8")
