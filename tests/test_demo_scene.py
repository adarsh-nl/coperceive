# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportOptionalMemberAccess=false, reportOperatorIssue=false, reportGeneralTypeIssues=false, reportCallIssue=false, reportIndexIssue=false
"""Demo scene: deterministic, valid generic format, ingestible and scoreable.

The LiDAR checks read the demo the way the app does: ``coperceive.viewdata``
maps the stored sensor-frame sweep through the agent pose (and the sensor's
extrinsic), and only then are the points compared with the world-frame boxes.
That is the path that exposed the double-pose bug: the old generator stored
world points in the sensor frame, so the app moved every cloud a second time.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import numpy as np
import pytest
from coperceive.adapters.generic import GenericAdapter
from coperceive.demo import DemoScene, create
from coperceive.store import read_scene
from coperceive.viewdata import world_points


def _hash_tree(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


@pytest.fixture(scope="module")
def demo(tmp_path_factory: pytest.TempPathFactory) -> DemoScene:
    return create(tmp_path_factory.mktemp("demo-scene") / "d", seed=0)


def _world(scene: object, cloud: object) -> np.ndarray:
    """The exact float32 world points the app serves for one sweep."""
    packed = world_points(scene, cloud)  # type: ignore[arg-type]
    return np.frombuffer(packed, dtype="<f4").reshape(-1, 3).astype(np.float64)


def _inside_outside(
    points: np.ndarray, ann: object, tolerance: float = 0.1
) -> tuple[np.ndarray, np.ndarray]:
    """Inside mask (with sensor-noise tolerance) and outside distance to one box."""
    yaw = 2.0 * math.atan2(ann.box_from_rpy.qz, ann.box_from_rpy.qw)  # type: ignore[attr-defined]
    c, s = math.cos(-yaw), math.sin(-yaw)
    delta = points - np.array(ann.center)  # type: ignore[attr-defined]
    local = np.stack(
        [c * delta[:, 0] - s * delta[:, 1], s * delta[:, 0] + c * delta[:, 1], delta[:, 2]],
        axis=1,
    )
    half = np.array(ann.size_lwh) / 2.0  # type: ignore[attr-defined]
    outside = np.linalg.norm(np.maximum(np.abs(local) - half, 0.0), axis=1)
    inside = np.all(np.abs(local) <= half + tolerance, axis=1)
    return inside, outside


def test_demo_is_deterministic_for_a_seed(tmp_path: Path) -> None:
    first = create(tmp_path / "a", seed=7)
    second = create(tmp_path / "b", seed=7)
    assert _hash_tree(first.root) == _hash_tree(second.root)
    assert first.bytes <= 15_000_000
    assert first.seconds > 0


def test_demo_validates_and_ingests(demo: DemoScene) -> None:
    report = GenericAdapter().check(demo.raw, {})
    assert report.errors == ()
    scene = read_scene(demo.scenes / "demo-junction")
    assert scene.scene_id == "demo-junction"
    assert {a.agent_type.value for a in scene.agents} == {"vehicle", "infrastructure"}
    assert len(scene.point_clouds) == 300  # 3 lidars x 100 frames
    assert len(scene.images) == 200  # 2 cameras x 100 frames
    # The bundled marker lets the result-bundle viewer allow synthetic sensors.
    marker = json.loads((demo.scenes / "demo-junction" / "COMPLETE.json").read_text())
    assert marker["generator"] == "coperceive demo"
    assert marker["dataset_id"] == "demo"


def test_demo_lidar_points_are_in_the_sensor_frame_and_land_inside_boxes(
    demo: DemoScene,
) -> None:
    """Read every sweep exactly as the app does, then check box containment.

    A sensor-frame sweep goes through world_from_body @ body_from_sensor; the
    old bug stored world points here, so the app applied the pose twice and no
    object return landed in its box. Every agent must now agree with the world
    boxes (>= 95 % of the returns that belong to an object are inside it).
    """
    scene = read_scene(demo.scenes / "demo-junction")
    # The world boxes are shared by the agents: the RSU has no boxes.csv of its
    # own, so compare every cloud with the per-frame world boxes any CAV recorded.
    boxes_by_time: dict[int, dict[str, object]] = {}
    for ann in scene.annotations:
        if ann.track_id is not None:
            boxes_by_time.setdefault(ann.time_us, {})[ann.track_id] = ann
    per_agent: dict[str, list[int]] = {}
    for cloud in scene.point_clouds:
        points = _world(scene, cloud)
        inside_any = np.zeros(len(points), dtype=bool)
        outside_min = np.full(len(points), np.inf)
        for ann in boxes_by_time[cloud.time_us].values():
            inside, outside = _inside_outside(points, ann)
            inside_any |= inside
            outside_min = np.minimum(outside_min, outside)
        # Ground returns next to a box are near it too; only above the road
        # surface are they object hits. Box-coordinate tolerance absorbs the
        # 1.5 cm beam noise (which can put a surface return just outside a face).
        above_ground = points[:, 2] > 0.2
        belongs = (outside_min <= 0.35) & above_ground
        stats = per_agent.setdefault(cloud.agent_id, [0, 0, 0])
        stats[0] += int(belongs.sum())
        stats[1] += int((belongs & inside_any).sum())
        stats[2] += len(points)

    for agent_id, (belongs, inside, total) in sorted(per_agent.items()):
        assert total > 0, agent_id
        assert belongs > 100, f"{agent_id}: no object returns reached the boxes"
        containment = inside / belongs
        assert containment >= 0.95, (
            f"{agent_id}: {containment:.1%} of object points inside their boxes"
        )

    # The dense look: the median sweep is much denser than the old ~306 points.
    counts = sorted(cloud.count for cloud in scene.point_clouds)
    assert counts[len(counts) // 2] >= 600, f"median sweep has {counts[len(counts) // 2]} points"


def test_demo_clouds_from_different_agents_line_up_in_world_space(demo: DemoScene) -> None:
    """Both CAVs see the same car boxes at the same world coordinates.

    For shared car observations, the points from every agent that hit the car
    must lie in that car's world box, and the two agents' surface points must
    nearly touch (median nearest-neighbour distance), which fails if either
    cloud is rotated or translated independently.
    """
    scene = read_scene(demo.scenes / "demo-junction")
    clouds = {(cloud.agent_id, cloud.time_us): _world(scene, cloud) for cloud in scene.point_clouds}
    anns = {
        (ann.agent_id, ann.time_us, ann.track_id): ann
        for ann in scene.annotations
        if ann.track_id is not None and ann.category == "car"
    }
    shared = 0
    for time_us in sorted({key[1] for key in anns}):
        for track_id in ("car-1", "car-2", "car-3", "car-4"):
            ann = anns.get(("cav-a", time_us, track_id))
            if ann is None:
                continue
            hits: dict[str, np.ndarray] = {}
            for agent_id in ("cav-a", "cav-b"):
                points = clouds[(agent_id, time_us)]
                inside, outside = _inside_outside(points, ann)
                chosen = points[inside | (outside <= 0.25)]
                if len(chosen) >= 10:
                    hits[agent_id] = chosen
            if len(hits) < 2:
                continue
            for agent_id, chosen in hits.items():
                _inside, outside = _inside_outside(chosen, ann)
                assert outside.max() <= 0.35, (agent_id, track_id, float(outside.max()))
            first, second = hits["cav-a"], hits["cav-b"]
            # Median distance from each CAV's car surface points to the other's.
            sample = first[:: max(1, len(first) // 200)]
            distances = np.linalg.norm(
                sample[:, None, :] - second[None, :: max(1, len(second) // 200), :], axis=2
            )
            median = float(np.median(distances.min(axis=1)))
            assert median <= 1.5, (track_id, time_us, median)
            shared += 1
    assert shared >= 20, f"only {shared} shared car observations"


def test_demo_cameras_render_the_world(demo: DemoScene) -> None:
    scene = read_scene(demo.scenes / "demo-junction")
    for agent_id in ("cav-a", "cav-b"):
        agent = scene.agent(agent_id)
        camera = next(s for s in scene.sensors if s.agent_id == agent_id and s.sensor_id == "front")
        world_from_camera = agent.poses[0].world_from_body.compose(camera.body_from_sensor)
        assert camera.fx is not None and camera.fy is not None and camera.cx is not None
        assert camera.cy is not None and camera.image_width_px is not None
        assert camera.image_height_px is not None
        inside = 0
        for ann in (a for a in scene.annotations if a.agent_id == agent_id and a.time_us == 0):
            point = world_from_camera.inverse().apply(ann.center)
            if point[2] <= 0:
                continue
            u = camera.fx * point[0] / point[2] + camera.cx
            v = camera.fy * point[1] / point[2] + camera.cy
            if 0 <= u < camera.image_width_px and 0 <= v < camera.image_height_px:
                inside += 1
        assert inside > 0, agent_id
        # The rendered frame has a sky, a road and objects: many distinct colours.
        from PIL import Image

        image = Image.open(demo.scenes / "demo-junction" / next(iter(scene.images)).path)
        assert image.size == (512, 384)
        # Palette-compressed render: still many shades of sky, road and objects.
        assert len(set(image.tobytes())) > 30


def test_demo_predictions_score_with_a_nontrivial_map(demo: DemoScene) -> None:
    assert demo.map_50 > 0.5
    rows = (
        (demo.predictions / "demo" / "predictions.jsonl").read_text(encoding="utf-8").splitlines()
    )
    assert rows and all(json.loads(row)["source"] == "noisy" for row in rows)
