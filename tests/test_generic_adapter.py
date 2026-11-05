# SPDX-License-Identifier: Apache-2.0
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownParameterType=false, reportMissingParameterType=false, reportArgumentType=false, reportPrivateUsage=false, reportUnknownLambdaType=false, reportIndexIssue=false, reportAttributeAccessIssue=false, reportOptionalMemberAccess=false, reportPossiblyUnboundVariable=false, reportUnusedFunction=false
"""Generic multi-view adapter: validation, two examples, ingest."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from coperceive.adapters.generic import GenericAdapter, GenericError
from coperceive.adapters.ingest import ingest
from coperceive.adapters.registry import list_adapters, resolve_adapter
from coperceive.remote.recognise import recognise
from coperceive.store import read_scene

EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "generic"


def _copy_example(tmp_path: Path, name: str) -> Path:
    import shutil

    target = tmp_path / name
    shutil.copytree(EXAMPLES / name, target)
    return target


def _write_scene(root: Path, text: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "scene.yaml").write_text(text, encoding="utf-8")
    return root


def test_both_examples_validate_and_expose_expected_agents(tmp_path: Path) -> None:
    for name in ("driving", "inspection"):
        root = _copy_example(tmp_path, name)
        report = GenericAdapter().check(root, {})
        assert report.errors == (), (name, report.errors)
        assert report.scenes == 1
        loaded = GenericAdapter().load(root, GenericAdapter().scenes(root, {})[0])
        if name == "driving":
            assert {a.agent_type.value for a in loaded.agents} == {"vehicle"}
            assert loaded.annotations
        else:
            assert {a.agent_type.value for a in loaded.agents} == {"infrastructure"}
            assert loaded.annotations == ()
            assert {s.modality.value for s in loaded.sensors} == {"camera", "lidar"}
            assert loaded.images
            assert loaded.point_clouds


def test_both_examples_ingest(tmp_path: Path) -> None:
    for name in ("driving", "inspection"):
        root = _copy_example(tmp_path, name)
        out = tmp_path / f"out-{name}"
        ingest(resolve_adapter("generic"), root, out, params={}, workers=1)
        scene_dir = out / ("generic-driving" if name == "driving" else "generic-inspection")
        assert (scene_dir / "manifest.json").is_file()
        assert read_scene(scene_dir).scene_id == scene_dir.name


def test_cli_format_validation_names_the_field(tmp_path: Path) -> None:
    root = _copy_example(tmp_path, "inspection")
    scene = yaml.safe_load((root / "scene.yaml").read_text(encoding="utf-8"))
    scene["agents"][1]["sensors"][0]["extrinsic"] = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
    (root / "scene.yaml").write_text(yaml.safe_dump(scene), encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "coperceive.cli",
            "adapters",
            "check",
            "--format",
            "generic",
            str(root),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "agent cam_south: sensors[0]: extrinsic must be 4×4" in result.stderr


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda s: s.pop("schema"), "schema must be coperceive-generic-1"),
        (lambda s: s.update(agents=[]), "agents must be a non-empty list"),
        (lambda s: s.update(handedness="up"), "handedness must be right or left"),
        (lambda s: s.pop("frame_rate_hz"), "needs timestamps_us"),
        (
            lambda s: s["agents"][0]["sensors"][0].update(modality="radar"),
            "modality must be camera or lidar",
        ),
        (
            lambda s: s["agents"][0]["sensors"][0].update(intrinsic=[1, 2, 3]),
            "intrinsic must be [fx, fy, cx, cy]",
        ),
        (lambda s: s["agents"][0].pop("fixed_pose"), "exactly one of fixed_pose, poses"),
    ],
)
def test_validator_errors_are_clear(tmp_path: Path, mutate, message: str) -> None:
    root = _copy_example(tmp_path, "inspection")
    scene = yaml.safe_load((root / "scene.yaml").read_text(encoding="utf-8"))
    mutate(scene)
    (root / "scene.yaml").write_text(yaml.safe_dump(scene), encoding="utf-8")
    import re

    with pytest.raises(GenericError, match=re.escape(message)):
        GenericAdapter().validate(root)


def test_generic_is_listed_and_detected_by_the_marker(tmp_path: Path) -> None:
    root = _copy_example(tmp_path, "driving")
    assert "generic" in {row.name for row in list_adapters()}
    assert recognise(root) == "Generic multi-view"


def _load(root: Path):
    adapter = GenericAdapter()
    return adapter, adapter.load(root, adapter.scenes(root, {})[0])


def test_moving_pose_yaw_is_yaw_not_pitch(tmp_path: Path) -> None:
    root = _copy_example(tmp_path, "driving")
    poses = root / "agents/ego/poses.csv"
    _header = poses.read_text(encoding="utf-8").splitlines()[0]
    poses.write_text(
        "timestamp_us,x,y,z,roll_deg,pitch_deg,yaw_deg\n"
        "0,0,0,1.9,0,0,90\n"
        "500000,1,0,1.9,0,0,90\n"
        "1000000,2,0,1.9,0,0,90\n",
        encoding="utf-8",
    )
    _adapter, scene = _load(root)
    ego = scene.agent("ego")
    x_world = ego.poses[0].world_from_body.apply((1.0, 0.0, 0.0))
    assert x_world[0] == pytest.approx(0.0, abs=1e-9)
    assert x_world[1] == pytest.approx(1.0, abs=1e-9)


def test_fixed_pose_yaw_is_yaw_not_pitch(tmp_path: Path) -> None:
    root = _copy_example(tmp_path, "inspection")
    scene = yaml.safe_load((root / "scene.yaml").read_text(encoding="utf-8"))
    scene["agents"][0]["fixed_pose"]["rotation_rpy_deg"] = [0.0, 0.0, 90.0]
    (root / "scene.yaml").write_text(yaml.safe_dump(scene), encoding="utf-8")
    _adapter, loaded = _load(root)
    cam = loaded.agent("cam_north")
    pose = cam.poses[0].world_from_body
    direction = tuple(
        a - b
        for a, b in zip(pose.apply((1.0, 0.0, 0.0)), pose.apply((0.0, 0.0, 0.0)), strict=False)
    )
    assert direction[1] == pytest.approx(1.0, abs=1e-9)


def test_camera_optical_axis_is_plus_z_and_projects_to_centre(tmp_path: Path) -> None:
    root = _copy_example(tmp_path, "inspection")
    _adapter, scene = _load(root)
    cam_agent = scene.agent("cam_north")
    camera = next(sensor for sensor in scene.sensors if sensor.agent_id == "cam_north")
    world_from_camera = cam_agent.poses[0].world_from_body.compose(camera.body_from_sensor)
    world_point = world_from_camera.apply((0.0, 0.0, 5.0))
    camera_point = world_from_camera.inverse().apply(world_point)
    assert camera_point == pytest.approx((0.0, 0.0, 5.0), abs=1e-9)
    fx, fy, cx, cy = camera.fx, camera.fy, camera.cx, camera.cy
    assert fx is not None and fy is not None and cx is not None and cy is not None
    assert fx * camera_point[0] / camera_point[2] + cx == pytest.approx(cx)
    assert fy * camera_point[1] / camera_point[2] + cy == pytest.approx(cy)


@pytest.mark.parametrize("bad", ["/etc/passwd", "../outside/{frame:06d}.png"])
def test_absolute_and_parent_paths_are_refused(tmp_path: Path, bad: str) -> None:
    root = _copy_example(tmp_path, "inspection")
    scene = yaml.safe_load((root / "scene.yaml").read_text(encoding="utf-8"))
    scene["agents"][0]["sensors"][0]["files"] = bad
    (root / "scene.yaml").write_text(yaml.safe_dump(scene), encoding="utf-8")
    with pytest.raises(GenericError, match="must stay inside the dataset folder"):
        GenericAdapter().validate(root)


def test_symlink_escaping_the_root_is_refused(tmp_path: Path) -> None:
    root = _copy_example(tmp_path, "inspection")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "000000.png").write_bytes(b"x")
    camera = root / "agents/cam_north/camera"
    camera.rename(tmp_path / "camera-real")
    camera.symlink_to(outside, target_is_directory=True)
    with pytest.raises(GenericError, match="must stay inside the dataset folder"):
        GenericAdapter().validate(root)


def test_template_with_another_field_is_refused(tmp_path: Path) -> None:
    root = _copy_example(tmp_path, "inspection")
    scene = yaml.safe_load((root / "scene.yaml").read_text(encoding="utf-8"))
    scene["agents"][0]["sensors"][0]["files"] = "agents/cam_north/camera/{frame:03d}.png"
    (root / "scene.yaml").write_text(yaml.safe_dump(scene), encoding="utf-8")
    with pytest.raises(GenericError, match="exactly one {frame:06d} field"):
        GenericAdapter().validate(root)


def test_missing_frames_warn_and_load_skips_them(tmp_path: Path) -> None:
    root = _copy_example(tmp_path, "inspection")
    (root / "agents/cam_north/camera/000001.png").unlink()
    adapter = GenericAdapter()
    report = adapter.check(root, {})
    assert report.errors == ()
    assert report.warnings == (
        "agent cam_north: sensor camera: 1 of 2 frames missing (first: 000001)",
    )
    scene = adapter.load(root, adapter.scenes(root, {})[0])
    assert len(scene.images) == 3
    assert not any(
        image.sensor_id == "camera"
        and image.agent_id == "cam_north"
        and image.path.endswith("000001.png")
        for image in scene.images
    )


def test_pose_timestamps_must_match_scene_timestamps(tmp_path: Path) -> None:
    root = _copy_example(tmp_path, "driving")
    poses = root / "agents/ego/poses.csv"
    poses.write_text(
        "timestamp_us,x,y,z,roll_deg,pitch_deg,yaw_deg\n"
        "1,0,0,1.9,0,0,0\n"
        "500000,1,0,1.9,0,0,0\n"
        "1000000,2,0,1.9,0,0,0\n",
        encoding="utf-8",
    )
    with pytest.raises(GenericError, match="pose timestamps must match scene timestamps exactly"):
        _load(root)
