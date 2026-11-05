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
            assert {a.agent_type.value for a in loaded.agents} == {"other"}
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
