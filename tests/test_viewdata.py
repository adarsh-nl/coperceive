# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import math
import struct
from pathlib import Path
from typing import cast

import pytest
from coperceive.model import (
    SCHEMA_VERSION,
    Agent,
    AgentType,
    Annotation,
    BoxFrame,
    Clock,
    Modality,
    PointCloud,
    Scene,
    Sensor,
    StampedPose,
)
from coperceive.rerun_export import export_rerun
from coperceive.se3 import SE3
from coperceive.store import write_scene
from coperceive.viewdata import (
    dataset_root_error,
    fov_y_rad,
    frame_document,
    frame_points,
    scene_directories,
    summarize,
    world_points,
)


def _scene() -> Scene:
    pose = StampedPose(0, SE3.identity().translation(10.0, 0.0, 0.0))
    later = StampedPose(100_000, SE3.identity().translation(11.0, 0.0, 0.0))
    agent = Agent("vehicle", AgentType.VEHICLE, (pose, later))
    lidar = Sensor("lidar_top", "vehicle", Modality.LIDAR, SE3.identity())
    camera = Sensor(
        "front",
        "vehicle",
        Modality.CAMERA,
        SE3.identity(),
        200,
        100,
        100.0,
        100.0,
        100.0,
        50.0,
    )
    box = Annotation(
        "vehicle",
        0,
        None,
        "car",
        (1.0, 0.0, 0.0),
        (2.0, 2.0, 2.0),
        SE3.identity(),
        None,
        BoxFrame.BODY,
    )
    cloud = PointCloud(
        "vehicle",
        "lidar_top",
        0,
        1,
        struct.pack("<ffff", 1.0, 0.0, 0.0, 0.5),
    )
    return Scene(
        schema_version=SCHEMA_VERSION,
        scene_id="desk",
        world_frame="ENU",
        clock=Clock("us", "viewer fixture"),
        agents=(agent,),
        sensors=(lidar, camera),
        annotations=(box,),
        objects=(),
        point_clouds=(cloud,),
    )


def test_world_point_and_box_follow_the_body_pose() -> None:
    scene = _scene()
    blob = world_points(scene, scene.point_clouds[0])
    point = struct.unpack("<fff", blob)
    assert point == pytest.approx((11.0, 0.0, 0.0))
    frame = frame_document(scene, 0)
    boxes = frame["boxes"]
    assert isinstance(boxes, list)
    box_rows = cast(list[object], boxes)
    assert len(box_rows) == 1
    row = box_rows[0]
    assert isinstance(row, dict)
    box = cast(dict[object, object], row)
    assert box["center"] == pytest.approx([11.0, 0.0, 0.0])
    frustums = frame["frustums"]
    assert isinstance(frustums, list)
    frustum_rows = cast(list[object], frustums)
    assert len(frustum_rows) == 1
    frustum_item = frustum_rows[0]
    assert isinstance(frustum_item, dict)
    frustum = cast(dict[object, object], frustum_item)
    assert frustum["fov_y_rad"] == pytest.approx(2.0 * math.atan(0.5))
    assert frustum["aspect"] == pytest.approx(2.0)
    assert frustum["image_width_px"] == 200
    assert frustum["fx"] == pytest.approx(100.0)
    assert frustum["cy"] == pytest.approx(50.0)
    summary = summarize(scene)
    assert summary["times_us"] == [0, 100_000]
    later = frame_points(scene, 100_000, "vehicle", "lidar_top")
    assert later == b""


def test_listing_requires_complete_when_asked(tmp_path: Path) -> None:
    scene = _scene()
    write_scene(scene, tmp_path / "desk")
    assert scene_directories(tmp_path) == [tmp_path / "desk"]
    assert scene_directories(tmp_path, require_complete=True) == []
    assert dataset_root_error(tmp_path) is not None
    (tmp_path / "desk" / "COMPLETE.json").write_text("{}\n", encoding="utf-8")
    assert scene_directories(tmp_path, require_complete=True) == [tmp_path / "desk"]
    assert dataset_root_error(tmp_path) is None
    missing = tmp_path / "gone"
    message = dataset_root_error(missing)
    assert message is not None
    assert "does not exist" in message


def test_camera_without_a_pinhole_has_no_frustum() -> None:
    sensor = Sensor("lidar_top", "vehicle", Modality.LIDAR, SE3.identity())
    assert fov_y_rad(sensor) is None


def test_rerun_export_writes_a_recording(tmp_path: Path) -> None:
    pytest.importorskip("rerun")
    scene = _scene()
    root = tmp_path / "desk"
    write_scene(scene, root)
    dest = tmp_path / "desk.rrd"
    samples = export_rerun(scene, dest)
    assert samples == 2
    assert dest.is_file()
    assert dest.stat().st_size > 0
