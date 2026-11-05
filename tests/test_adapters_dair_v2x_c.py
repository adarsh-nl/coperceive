# SPDX-License-Identifier: Apache-2.0
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownLambdaType=false, reportArgumentType=false, reportPrivateUsage=false, reportIndexIssue=false
"""Synthetic DAIR-V2X-C cooperative layout: check, split, transforms. No real data."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from coperceive.adapters.dair_v2x_c import _calib
from coperceive.adapters.registry import resolve_adapter
from coperceive.cluster_datasets import detect_cluster_dataset, index_cluster_dataset

PCD = (
    "# .PCD v0.7 - Point Cloud Data file format\nVERSION 0.7\n"
    "FIELDS x y z rgb\nSIZE 4 4 4 4\nTYPE F F F F\nCOUNT 1 1 1 1\n"
    "WIDTH 1\nHEIGHT 1\nVIEWPOINT 0 0 0 1 0 0 0\nPOINTS 1\nDATA ascii\n"
    "1.000000 2.000000 3.000000 0\n"
)
IDENTITY = {"rotation": [[1, 0, 0], [0, 1, 0], [0, 0, 1]], "translation": [0, 0, 0]}


def _write_calib(path: Path, translation: list[float]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"rotation": IDENTITY["rotation"], "translation": translation}),
        encoding="utf-8",
    )


def _release(root: Path) -> Path:
    cvi = root / "cooperative-vehicle-infrastructure"
    camp = root / "DAIR-V2X-C_Complemented_Anno"
    frame = "000001"
    for side in ("vehicle-side", "infrastructure-side"):
        (cvi / side / "velodyne").mkdir(parents=True)
        (cvi / side / "velodyne" / f"{frame}.pcd").write_text(PCD, encoding="utf-8")
        (cvi / side / "image").mkdir()
    (cvi / "cooperative").mkdir()
    _write_calib(cvi / "vehicle-side/calib/lidar_to_novatel" / f"{frame}.json", [0, 0, 0])
    _write_calib(cvi / "vehicle-side/calib/novatel_to_world" / f"{frame}.json", [0, 0, 0])
    _write_calib(
        cvi / "infrastructure-side/calib/virtuallidar_to_world" / f"{frame}.json", [5, 0, 0]
    )
    vehicle = [
        {
            "pointcloud_path": f"vehicle-side/velodyne/{frame}.pcd",
            "pointcloud_timestamp": "1000000",
            "batch_id": "batch-1",
            "calib_lidar_to_novatel_path": f"vehicle-side/calib/lidar_to_novatel/{frame}.json",
            "calib_novatel_to_world_path": f"vehicle-side/calib/novatel_to_world/{frame}.json",
        }
    ]
    infra = [
        {
            "pointcloud_path": f"infrastructure-side/velodyne/{frame}.pcd",
            "pointcloud_timestamp": "1000000",
            "batch_id": "batch-1",
            "calib_virtuallidar_to_world_path": (
                f"infrastructure-side/calib/virtuallidar_to_world/{frame}.json"
            ),
        }
    ]
    coop = [
        {
            "vehicle_pointcloud_path": f"vehicle-side/velodyne/{frame}.pcd",
            "infrastructure_pointcloud_path": f"infrastructure-side/velodyne/{frame}.pcd",
            "cooperative_label_path": f"cooperative/label_world/{frame}.json",
        }
    ]
    for path, payload in (
        (cvi / "vehicle-side/data_info.json", vehicle),
        (cvi / "infrastructure-side/data_info.json", infra),
        (cvi / "cooperative/data_info.json", coop),
    ):
        path.write_text(json.dumps(payload), encoding="utf-8")
    # Complemented annotation: one world-frame box. Corner order: x edge, y edge, z edge.
    x, y, z = 4.5, 2.0, 1.5
    corners = [
        [0, -y / 2, 0],
        [x, -y / 2, 0],
        [x, y / 2, 0],
        [0, y / 2, 0],
        [0, -y / 2, z],
        [x, -y / 2, z],
        [x, y / 2, z],
        [0, y / 2, z],
    ]
    labels = camp / "new_labels/cooperative_label/label_world"
    labels.mkdir(parents=True)
    (labels / f"{frame}.json").write_text(
        json.dumps([{"type": "Car", "world_8_points": corners}]), encoding="utf-8"
    )
    (camp / "train.json").write_text(json.dumps([frame]), encoding="utf-8")
    (camp / "val.json").write_text(json.dumps([frame]), encoding="utf-8")
    return root


def test_check_loads_the_cooperative_scene_and_two_agents(tmp_path: Path) -> None:
    root = _release(tmp_path / "dair")
    adapter = resolve_adapter("dair-v2x-c")
    report = adapter.check(root, {"split": "train"})
    assert report.errors == ()
    assert report.scenes == 1
    ref = adapter.scenes(root, {"split": "train"})[0]
    assert ref.scene_id == "batch-1"
    assert ref.samples == ("000001",)
    scene = adapter.load(root, ref)
    assert {agent.agent_id for agent in scene.agents} == {"vehicle", "infrastructure"}
    assert len(scene.annotations) == 1
    assert len(scene.point_clouds) == 2


def test_split_param_is_required_and_validated(tmp_path: Path) -> None:
    root = _release(tmp_path / "dair")
    adapter = resolve_adapter("dair-v2x-c")
    with pytest.raises(Exception, match="split"):
        adapter.scenes(root, {})
    with pytest.raises(Exception, match="train, val"):
        adapter.scenes(root, {"split": "test"})


def test_world_label_is_converted_to_the_expected_centre_and_size(tmp_path: Path) -> None:
    root = _release(tmp_path / "dair")
    adapter = resolve_adapter("dair-v2x-c")
    ref = adapter.scenes(root, {"split": "train"})[0]
    ann = adapter.load(root, ref).annotations[0]
    # Box x in [0, 4.5], y in [-1, 1], z in [0, 1.5].
    assert ann.center == pytest.approx((2.25, 0.0, 0.75))
    assert ann.size_lwh == pytest.approx((4.5, 2.0, 1.5))


def test_infrastructure_to_vehicle_calibration_is_hand_checked(tmp_path: Path) -> None:
    root = _release(tmp_path / "dair")
    cvi = root / "cooperative-vehicle-infrastructure"
    frame = "000001"
    tf_v = _calib(cvi / "vehicle-side/calib/novatel_to_world" / f"{frame}.json").compose(
        _calib(cvi / "vehicle-side/calib/lidar_to_novatel" / f"{frame}.json")
    )
    tf_i = _calib(cvi / "infrastructure-side/calib/virtuallidar_to_world" / f"{frame}.json")
    # The infrastructure sits at world (5, 0, 0); its origin in the vehicle
    # lidar frame is therefore (5, 0, 0) when the vehicle is at the origin.
    assert tf_v.inverse().compose(tf_i).apply((0.0, 0.0, 0.0)) == pytest.approx((5.0, 0.0, 0.0))


def test_a_missing_cooperative_frame_is_a_clear_error(tmp_path: Path) -> None:
    root = _release(tmp_path / "dair")
    (root / "cooperative-vehicle-infrastructure/cooperative/data_info.json").write_text(
        "[]", encoding="utf-8"
    )
    adapter = resolve_adapter("dair-v2x-c")
    with pytest.raises(Exception, match="no matching|no scenes|split"):
        adapter.scenes(root, {"split": "train"})


def test_detect_and_index_a_dair_tree(tmp_path: Path) -> None:
    root = _release(tmp_path / "dair")
    detected = detect_cluster_dataset(root)
    assert detected["status"] == "ok"
    assert detected["adapter"] == "dair-v2x-c"
    assert detected["split"] == "train"
    assert detected["scene_count"] == 1
    assert detected["licence"]["status"] == "unknown"
    indexed = index_cluster_dataset("dair-v2x-c", root)
    assert indexed["adapter"] == "dair-v2x-c"
    assert indexed["scene_count"] == 1
    assert indexed["estimated_cache_bytes"] == 0
