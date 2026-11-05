# SPDX-License-Identifier: Apache-2.0
"""Synthetic V2V4Real OpenCOOD tree: check, detect, index, transforms. No dataset bytes."""

from __future__ import annotations

import math
from pathlib import Path

import pytest
from coperceive.adapters.registry import resolve_adapter
from coperceive.cluster_datasets import detect_cluster_dataset, index_cluster_dataset

from tests.opencood_release import AgentTrack, Box, SceneSpec, two_cav_scene, write_release

SCENE = "testoutput_CAV_data_2022-06-01-10-00-00_1"


def _scene() -> SceneSpec:
    base = two_cav_scene(SCENE)
    agents = [
        AgentTrack("0", base.agents[0].poses, base.agents[0].boxes),
        AgentTrack("1", base.agents[1].poses, base.agents[1].boxes),
    ]
    return SceneSpec(SCENE, agents)


def _write(root: Path, split: str = "train") -> Path:
    write_release(root, split, [_scene()])
    return root


def test_adapter_check_accepts_a_synthetic_v2v4real_train_tree(tmp_path: Path) -> None:
    root = _write(tmp_path)
    adapter = resolve_adapter("v2v4real")
    report = adapter.check(root, {"split": "train"})
    assert report.scenes == 1
    assert report.errors == ()
    assert dict(report.counts[SCENE])["agents"] == 2


def test_split_is_required_and_limited_to_train_and_test(tmp_path: Path) -> None:
    root = _write(tmp_path)
    adapter = resolve_adapter("v2v4real")
    with pytest.raises(Exception, match="split"):
        adapter.scenes(root, {})
    with pytest.raises(Exception, match="train, test"):
        adapter.scenes(root, {"split": "validate"})


def test_detect_and_index_choose_v2v4real_from_the_scenario_and_agent_names(
    tmp_path: Path,
) -> None:
    root = _write(tmp_path)
    detected = detect_cluster_dataset(root / "train")
    assert detected["status"] == "ok"
    assert detected["adapter"] == "v2v4real"
    assert detected["split"] == "train"
    assert detected["scene_count"] == 1
    indexed = index_cluster_dataset("v2v4real", root / "train")
    assert indexed["adapter"] == "v2v4real"
    assert indexed["scene_count"] == 1
    assert indexed["scenes"][0]["agent_count"] == 2
    assert indexed["estimated_cache_bytes"] > 0


def test_a_partial_scenario_fails_with_the_missing_agent_reason(tmp_path: Path) -> None:
    root = _write(tmp_path)
    import shutil

    for agent in ("0", "1"):
        shutil.rmtree(root / "train" / SCENE / agent)
    with pytest.raises(Exception, match="no agents"):
        resolve_adapter("v2v4real").check(root, {"split": "train"})


def test_hand_computed_v2v4real_pose_box_and_point_transforms(tmp_path: Path) -> None:
    root = tmp_path / "v2v4real"
    scene = SceneSpec(
        SCENE,
        [
            AgentTrack(
                "0",
                poses=[((10.0, 20.0, 1.9), (0.0, 90.0, 0.0))],
                boxes=[Box(650, location=(16.0, 30.0, 0.0), angle=(0.0, 90.0, 0.0))],
            ),
            AgentTrack("1", poses=[((0.0, 0.0, 1.9), (0.0, 0.0, 0.0))]),
        ],
        stems=(69,),
    )
    write_release(root, "train", [scene])
    adapter = resolve_adapter("v2v4real")
    ref = adapter.scenes(root, {"split": "train"})[0]
    loaded = adapter.load(root, ref)

    pose = loaded.agent("0").poses[0]
    # ADR 0012: source (10, 20, 1.9) is schema (10, -20, 1.9).
    assert (
        pose.world_from_body.tx,
        pose.world_from_body.ty,
        pose.world_from_body.tz,
    ) == pytest.approx((10.0, -20.0, 1.9))
    ann = next(a for a in loaded.annotations if a.track_id == "650")
    # Source box centre (16, 30, 0.75) is schema (16, -30, 0.75).
    assert ann.center == pytest.approx((16.0, -30.0, 0.75))
    # The box centre is in both the pose-applied point cloud and the annotation;
    # the reflected world point is (16, -30, 0.75), never source (16, 30, 0.75).
    points = [pose.world_from_body.apply(point) for point in _points(loaded, "0", pose.time_us)]
    expected = (16.0, -30.0, 0.75)
    assert min(math.dist(point, expected) for point in points) < 1e-4
    assert min(math.dist(point, (16.0, 30.0, 0.75)) for point in points) > 1.0


def _points(scene: object, agent_id: str, time_us: int) -> list[tuple[float, float, float]]:
    import struct

    from coperceive.model import Scene

    assert isinstance(scene, Scene)
    cloud = next(
        item for item in scene.point_clouds if item.agent_id == agent_id and item.time_us == time_us
    )
    return [
        (values[0], values[1], values[2])
        for index in range(cloud.count)
        for values in [struct.unpack_from("<4f", cloud.xyz_i_f32, index * 16)]
    ]
