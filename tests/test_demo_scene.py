# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportOptionalMemberAccess=false, reportOperatorIssue=false, reportGeneralTypeIssues=false, reportCallIssue=false, reportIndexIssue=false
"""Demo scene: deterministic, valid generic format, ingestible and scoreable."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

from coperceive.adapters.generic import GenericAdapter
from coperceive.demo import create
from coperceive.store import read_scene


def _hash_tree(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def test_demo_is_deterministic_for_a_seed(tmp_path: Path) -> None:
    first = create(tmp_path / "a", seed=7)
    second = create(tmp_path / "b", seed=7)
    assert _hash_tree(first.root) == _hash_tree(second.root)
    assert first.bytes <= 15_000_000
    assert first.seconds > 0


def test_demo_validates_and_ingests(tmp_path: Path) -> None:
    demo = create(tmp_path / "d", seed=0)
    report = GenericAdapter().check(demo.raw, {})
    assert report.errors == ()
    scene = read_scene(demo.scenes / "demo-junction")
    assert scene.scene_id == "demo-junction"
    assert {a.agent_type.value for a in scene.agents} == {"vehicle", "infrastructure"}
    assert len(scene.point_clouds) == 300  # 3 lidars x 100 frames
    assert len(scene.images) == 200  # 2 cameras x 100 frames


def test_lidar_points_land_on_object_surfaces_and_cameras_see_objects(tmp_path: Path) -> None:
    demo = create(tmp_path / "d", seed=0)
    scene = read_scene(demo.scenes / "demo-junction")
    clouds = {(c.agent_id, c.time_us): c for c in scene.point_clouds}
    assert clouds
    assert all(c.count > 0 for c in clouds.values())
    # Every ground-truth box in the first frame has a LiDAR return on its surface.
    import numpy as np

    points_by_agent = {
        agent_id: np.frombuffer(cloud.xyz_i_f32, dtype=np.float32).reshape(-1, 4)[:, :3]
        for (agent_id, time_us), cloud in clouds.items()
        if time_us == 0
    }
    for ann in (a for a in scene.annotations if a.agent_id == "cav-a" and a.time_us == 0):
        half = np.array(ann.size_lwh) / 2.0
        closest = math.inf
        for points in points_by_agent.values():
            local = points - np.array(ann.center)
            angle = 2.0 * math.atan2(ann.box_from_rpy.qz, ann.box_from_rpy.qw)
            c, s = math.cos(-angle), math.sin(-angle)
            local = np.stack(
                [c * local[:, 0] - s * local[:, 1], s * local[:, 0] + c * local[:, 1], local[:, 2]],
                axis=1,
            )
            outside = np.linalg.norm(np.maximum(np.abs(local) - half, 0.0), axis=1)
            closest = min(closest, float(outside.min()))
        assert closest < 0.2, (ann.track_id, closest)

    # Each camera projects at least one actor inside its image bounds.
    for agent_id in ("cav-a", "cav-b"):
        agent = scene.agent(agent_id)
        camera = next(s for s in scene.sensors if s.agent_id == agent_id and s.sensor_id == "front")
        world_from_camera = agent.poses[0].world_from_body.compose(camera.body_from_sensor)
        inside = 0
        for ann in (a for a in scene.annotations if a.agent_id == agent_id and a.time_us == 0):
            point = world_from_camera.inverse().apply(ann.center)
            if point[2] <= 0:
                continue
            assert camera.fx is not None and camera.fy is not None and camera.cx is not None
            assert camera.cy is not None and camera.image_width_px is not None
            assert camera.image_height_px is not None
            u = camera.fx * point[0] / point[2] + camera.cx
            v = camera.fy * point[1] / point[2] + camera.cy
            if 0 <= u < camera.image_width_px and 0 <= v < camera.image_height_px:
                inside += 1
        assert inside > 0, agent_id
    # Rendering: the camera image has a non-background colour (a box was drawn).
    from PIL import Image

    image = Image.open(demo.scenes / "demo-junction" / next(iter(scene.images)).path)
    assert image.size == (640, 480)
    assert len(set(image.tobytes())) > 5


def test_demo_predictions_score_with_a_nontrivial_map(tmp_path: Path) -> None:
    demo = create(tmp_path / "d", seed=0)
    assert demo.map_50 > 0.5
    rows = (
        (demo.predictions / "demo" / "predictions.jsonl").read_text(encoding="utf-8").splitlines()
    )
    assert rows and all(json.loads(row)["source"] == "noisy" for row in rows)
