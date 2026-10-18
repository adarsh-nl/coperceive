# SPDX-License-Identifier: Apache-2.0
"""The OPV2V and V2XSet manifests on a synthetic OpenCOOD-style release (ADR 0012)."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, cast

import numpy as np
import pytest
import yaml
from coperceive.adapters.build import ManifestAdapter
from coperceive.adapters.ingest import ingest
from coperceive.adapters.registry import list_adapters, resolve_adapter
from coperceive.adapters.spec import (
    Manifest,
    SpecError,
    builtin_manifest_path,
    load_manifest,
    parse_manifest,
)
from coperceive.cli import main
from coperceive.model import AgentType, Modality, Scene
from coperceive.se3 import SE3
from coperceive.store import read_scene

from tests.opencood_release import AgentTrack, Box, SceneSpec, two_cav_scene, write_release

Vec3 = tuple[float, float, float]


def _adapter(name: str = "opv2v") -> ManifestAdapter:
    return ManifestAdapter(load_manifest(builtin_manifest_path(name)))


def _ingested(tmp_path: Path, scene: SceneSpec | None = None, name: str = "opv2v") -> Scene:
    scene = scene or two_cav_scene()
    write_release(tmp_path / "raw", "train", [scene])
    ingest(_adapter(name), tmp_path / "raw", tmp_path / "out", params={"split": "train"}, workers=1)
    return read_scene(tmp_path / "out" / scene.name)


def _points(scene: Scene, agent_id: str, time_us: int) -> list[Vec3]:
    cloud = next(c for c in scene.point_clouds if c.agent_id == agent_id and c.time_us == time_us)
    flat = np.frombuffer(cloud.xyz_i_f32, dtype=np.float32)  # pyright: ignore[reportUnknownMemberType]
    values = [float(v) for v in cast(list[float], flat.tolist())]
    return [(values[i], values[i + 1], values[i + 2]) for i in range(0, len(values), 4)]


def _distance(a: Vec3, b: Vec3) -> float:
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b, strict=True)))


def _opv2v_payload() -> dict[str, Any]:
    loaded = yaml.safe_load(builtin_manifest_path("opv2v").read_text(encoding="utf-8"))
    return cast(dict[str, Any], loaded)


def _parse(payload: dict[str, Any]) -> Manifest:
    return parse_manifest(payload, source="test")


# --- Task 1: manifests, discovery, clock ---------------------------------------------------


def test_both_manifests_load_and_list() -> None:
    names = {row.name: row.source for row in list_adapters()}
    assert names["opv2v"] == "builtin" and names["v2xset"] == "builtin"
    for name in ("opv2v", "v2xset"):
        spec = load_manifest(builtin_manifest_path(name))
        assert spec.licence is not None
        assert spec.licence.status == "unknown"
        assert any("github.com" in link for link in spec.licence.links)
        assert spec.clock.scale_us == 50000
        assert "assumption" in spec.clock.scale_note
        assert "fixed_delta_seconds" in spec.clock.scale_note
        assert spec.conventions.source_handedness == "left"
    assert load_manifest(builtin_manifest_path("opv2v")).params["split"].allowed == (
        "train",
        "validate",
        "test",
        "test_culver_city",
    )
    assert load_manifest(builtin_manifest_path("v2xset")).params["split"].allowed == (
        "train",
        "validate",
        "test",
    )


def test_scenes_and_agents_are_discovered_per_split(tmp_path: Path) -> None:
    write_release(
        tmp_path,
        "train",
        [two_cav_scene("2021_08_16_22_26_54"), two_cav_scene("2021_08_18_09_02_56")],
    )
    write_release(tmp_path, "validate", [two_cav_scene("2021_08_20_21_48_35")])
    adapter = _adapter()
    refs = adapter.scenes(tmp_path, {"split": "train"})
    assert [ref.scene_id for ref in refs] == ["2021_08_16_22_26_54", "2021_08_18_09_02_56"]
    assert refs[0].samples == (69, 71, 73)
    assert dict(refs[0].platform) == {"split": "train"}
    scene = adapter.load(tmp_path, refs[0])
    assert [agent.agent_id for agent in scene.agents] == ["641", "650"]
    assert [ref.scene_id for ref in adapter.scenes(tmp_path, {"split": "validate"})] == [
        "2021_08_20_21_48_35"
    ]


def test_clock_is_fifty_ms_per_tick(tmp_path: Path) -> None:
    write_release(tmp_path, "train", [two_cav_scene()])
    adapter = _adapter()
    scene = adapter.load(tmp_path, adapter.scenes(tmp_path, {"split": "train"})[0])
    assert scene.pose_times("641") == (3_450_000, 3_550_000, 3_650_000)
    assert "fixed_delta_seconds" in scene.clock.scale_note


def test_agents_with_different_stems_keep_their_own_samples(tmp_path: Path) -> None:
    short = AgentTrack("650", poses=[((10.0, 40.0, 1.9), (0.0, 90.0, 0.0))])  # stem 69 only
    full = two_cav_scene().agents[0]
    write_release(tmp_path, "train", [SceneSpec("2021_08_16_22_26_54", [full, short])])
    adapter = _adapter()
    ref = adapter.scenes(tmp_path, {"split": "train"})[0]
    assert ref.samples == (69, 71, 73)
    scene = adapter.load(tmp_path, ref)
    assert scene.pose_times("641") == (3_450_000, 3_550_000, 3_650_000)
    assert scene.pose_times("650") == (3_450_000,)


def test_split_param_is_required_and_checked(tmp_path: Path) -> None:
    write_release(tmp_path, "train", [two_cav_scene()])
    with pytest.raises(Exception, match="split"):
        _adapter().scenes(tmp_path, {})
    with pytest.raises(Exception, match="train, validate, test"):
        _adapter().scenes(tmp_path, {"split": "nope"})
    assert resolve_adapter("v2xset").name == "v2xset"


def test_fixture_boxes_are_written_as_a_mapping_keyed_by_actor_id(tmp_path: Path) -> None:
    write_release(tmp_path, "train", [two_cav_scene()])
    record = cast(
        dict[str, Any],
        yaml.safe_load(
            (tmp_path / "train/2021_08_16_22_26_54/641/000069.yaml").read_text(encoding="utf-8")
        ),
    )
    assert set(record["vehicles"]) == {650, 677}
    assert record["vehicles"][677]["center"] == [1.0, 0.0, 0.75]
    assert record["lidar_pose"] == [10.0, 20.0, 1.9, 0.0, 90.0, 0.0]
    assert Box(1, (0.0, 0.0, 0.0)).extent == (2.4, 1.0, 0.75)


# --- ADR 0012 decision 1: the reflection ----------------------------------------------------


def test_reflection_keeps_forward_along_travel(tmp_path: Path) -> None:
    scene = _ingested(tmp_path)
    poses = scene.agent("641").poses
    a, b = poses[0].world_from_body, poses[1].world_from_body
    ahead = a.apply((1.0, 0.0, 0.0))
    forward = (ahead[0] - a.tx, ahead[1] - a.ty, ahead[2] - a.tz)
    step = (b.tx - a.tx, b.ty - a.ty, b.tz - a.tz)
    norm = math.hypot(*step)
    dot = sum(f * s / norm for f, s in zip(forward, step, strict=True))
    assert dot > 0.99
    # Source y is schema -y: the vehicle moved towards source +y.
    assert (a.tx, a.ty, a.tz) == pytest.approx((10.0, -20.0, 1.9))
    assert step == pytest.approx((0.0, -2.0, 0.0))


def test_box_centres_sit_inside_their_lidar_points(tmp_path: Path) -> None:
    """Known answer: after conversion every box still holds the points seeded inside it."""
    scene = _ingested(tmp_path)
    checked = 0
    for ann in scene.annotations:
        points = _points(scene, ann.agent_id, ann.time_us)
        pose = next(p for p in scene.agent(ann.agent_id).poses if p.time_us == ann.time_us)
        world = [pose.world_from_body.apply(p) for p in points]
        box_from_world = ann.box_from_rpy.inverse()
        half = tuple(v / 2.0 for v in ann.size_lwh)
        inside = 0
        for w in world:
            d = (w[0] - ann.center[0], w[1] - ann.center[1], w[2] - ann.center[2])
            local = box_from_world.apply(d)
            if all(abs(local[i]) <= half[i] + 1e-6 for i in range(3)):
                inside += 1
        assert inside >= 9, (ann.agent_id, ann.track_id, inside)
        checked += 1
    assert checked == 5 * 3  # 2 + 3 boxes per sample, three samples


def test_pcd_points_are_reflected_like_the_poses(tmp_path: Path) -> None:
    scene = _ingested(tmp_path)
    points = _points(scene, "641", 3_450_000)
    # Box 677's centre, source world (17, 30, 0.75), seen from the lidar at (10, 20, 1.9)
    # with yaw 90: lidar-frame (10, -7, -1.15) in the source, hence (10, 7, -1.15) here.
    assert min(_distance(p, (10.0, 7.0, -1.15)) for p in points) < 1e-4
    assert min(_distance(p, (10.0, -7.0, -1.15)) for p in points) > 1.0


# --- decision 2: boxes ---------------------------------------------------------------------


def test_centre_offset_is_rotated_by_the_box_angle(tmp_path: Path) -> None:
    agent = AgentTrack(
        "641",
        poses=[((0.0, 0.0, 1.9), (0.0, 0.0, 0.0))],
        boxes=[Box(700, location=(0.0, 0.0, 0.0), angle=(0.0, 90.0, 0.0), center=(1.0, 0.0, 0.0))],
    )
    scene = _ingested(tmp_path, SceneSpec("2021_08_16_22_26_54", [agent], stems=(69,)))
    ann = scene.annotations[0]
    # R(yaw 90) · (1, 0, 0) is source (0, 1, 0); reflected it is schema (0, -1, 0).
    assert ann.center == pytest.approx((0.0, -1.0, 0.0))
    yaw = ann.box_from_rpy.apply((1.0, 0.0, 0.0))
    assert yaw == pytest.approx((0.0, -1.0, 0.0), abs=1e-9)


def test_half_extents_are_doubled(tmp_path: Path) -> None:
    scene = _ingested(tmp_path)
    ann = next(a for a in scene.annotations if a.track_id == "650")
    assert ann.size_lwh == pytest.approx((4.8, 2.0, 1.5))
    assert ann.center == pytest.approx((10.0, -40.0, 0.75))
    assert ann.category == "vehicle"
    assert ann.frame.value == "world"


def test_track_ids_are_strings_and_the_ego_is_not_its_own_box(tmp_path: Path) -> None:
    scene = _ingested(tmp_path)
    by_agent = {
        agent: sorted({str(a.track_id) for a in scene.annotations if a.agent_id == agent})
        for agent in ("641", "650")
    }
    assert by_agent == {"641": ["650", "677"], "650": ["641", "677", "689"]}
    assert all(isinstance(a.track_id, str) for a in scene.annotations)


def test_shared_ids_are_one_object_with_identical_world_centres(tmp_path: Path) -> None:
    scene = _ingested(tmp_path)
    assert [(o.track_id, o.category) for o in scene.objects] == [("677", "vehicle")]
    centres = {
        a.agent_id: a.center
        for a in scene.annotations
        if a.track_id == "677" and a.time_us == 3_450_000
    }
    assert centres["641"] == pytest.approx(centres["650"], abs=1e-9)
    assert centres["641"] == pytest.approx((17.0, -30.0, 0.75))


def test_per_agent_sets_are_not_duplicated(tmp_path: Path) -> None:
    scene = _ingested(tmp_path)
    per_sample = [a for a in scene.annotations if a.time_us == 3_450_000]
    assert len(per_sample) == 5  # 2 for 641 + 3 for 650, nothing copied across


# --- decision 4: typed discovery; decision 5: sensors ---------------------------------------


def test_rsu_folder_becomes_infrastructure(tmp_path: Path) -> None:
    scene = _ingested(tmp_path, two_cav_scene(rsu=True), name="v2xset")
    types = {a.agent_id: a.agent_type for a in scene.agents}
    assert types == {
        "-1": AgentType.INFRASTRUCTURE,
        "641": AgentType.VEHICLE,
        "650": AgentType.VEHICLE,
    }
    assert scene.agent("-1").poses[0].world_from_body.tz == pytest.approx(4.3)


def test_rsu_cameras_come_from_the_extrinsic(tmp_path: Path) -> None:
    scene = _ingested(tmp_path, two_cav_scene(rsu=True), name="v2xset")
    t = scene.sensor("-1", "camera0").body_from_sensor
    assert (t.tx, t.ty, t.tz) == pytest.approx((-3.0, 0.0, 0.9))


def test_four_cameras_and_one_lidar_per_agent(tmp_path: Path) -> None:
    scene = _ingested(tmp_path)
    for agent in ("641", "650"):
        sensors = [s for s in scene.sensors if s.agent_id == agent]
        assert sorted(s.sensor_id for s in sensors) == [
            "camera0",
            "camera1",
            "camera2",
            "camera3",
            "lidar",
        ]
        lidar = scene.sensor(agent, "lidar")
        assert lidar.modality is Modality.LIDAR
        assert lidar.body_from_sensor.almost_equal(SE3.identity())
        cam = scene.sensor(agent, "camera0")
        assert cam.modality is Modality.CAMERA
        assert (cam.fx, cam.cx, cam.cy) == pytest.approx((335.639852470912, 400.0, 300.0))
        assert (cam.image_width_px, cam.image_height_px) == (800, 600)


def test_camera_extrinsic_is_camera_to_lidar_child_to_parent(tmp_path: Path) -> None:
    scene = _ingested(tmp_path)
    front = scene.sensor("641", "camera0").body_from_sensor
    assert (front.tx, front.ty, front.tz) == pytest.approx((-3.0, 0.0, 0.9))
    # The camera looks along sensor +z (RDF); the front camera looks along body +x.
    look = front.apply((0.0, 0.0, 1.0))
    assert (look[0] - front.tx, look[1] - front.ty, look[2] - front.tz) == pytest.approx(
        (1.0, 0.0, 0.0), abs=1e-9
    )
    # camera1 has a source yaw offset of +100 deg (towards source +y, the right side);
    # mirrored, it looks at -100 deg from body +x.
    side = scene.sensor("641", "camera1").body_from_sensor
    look = side.apply((0.0, 0.0, 1.0))
    direction = (look[0] - side.tx, look[1] - side.ty)
    assert direction == pytest.approx(
        (math.cos(math.radians(-100.0)), math.sin(math.radians(-100.0))), abs=1e-9
    )
    assert (side.tx, side.ty) == pytest.approx((-0.21, -0.54))


def test_image_refs_name_the_png_files(tmp_path: Path) -> None:
    scene = _ingested(tmp_path)
    refs = sorted(i.path for i in scene.images if i.agent_id == "641" and i.time_us == 3_450_000)
    assert refs == [f"train/2021_08_16_22_26_54/641/000069_camera{k}.png" for k in range(4)]


# --- spec rules -----------------------------------------------------------------------------


def test_boxes_centre_and_cx_fields_are_exclusive() -> None:
    payload = _opv2v_payload()
    payload["streams"]["boxes"]["fields"]["cx"] = "location.0"
    with pytest.raises(SpecError, match=r"streams\.boxes\.centre"):
        _parse(payload)


def test_half_extents_and_length_are_exclusive() -> None:
    payload = _opv2v_payload()
    payload["streams"]["boxes"]["fields"]["length"] = "extent.0"
    with pytest.raises(SpecError, match=r"streams\.boxes\.size"):
        _parse(payload)


def test_left_handed_manifest_refuses_other_axes() -> None:
    payload = _opv2v_payload()
    payload["conventions"]["reflect_axis"] = "x"
    with pytest.raises(SpecError, match="reflect_axis"):
        _parse(payload)
    payload = _opv2v_payload()
    payload["conventions"]["source_handedness"] = "right"
    with pytest.raises(SpecError, match="reflect_axis"):
        _parse(payload)


def test_types_override_rejects_unknown_type() -> None:
    payload = _opv2v_payload()
    payload["agents"]["discover"]["types"] = {"-1": "drone"}
    with pytest.raises(SpecError, match=r"agents\.discover\.types\.-1"):
        _parse(payload)


def test_check_prints_the_licence_status(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_release(tmp_path, "train", [two_cav_scene()])
    main(["adapters", "check", "opv2v", "--raw-root", str(tmp_path), "--param", "split=train"])
    out = capsys.readouterr().out
    assert "licence unknown: No licence is published." in out
    assert "scenes 1" in out
