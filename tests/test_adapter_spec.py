# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from coperceive.adapters.spec import SpecError, builtin_manifest_path, load_manifest


def _griffin() -> dict[str, object]:
    return yaml.safe_load(builtin_manifest_path("griffin").read_text(encoding="utf-8"))


def _write(tmp_path: Path, payload: dict[str, object]) -> Path:
    path = tmp_path / "m.yaml"
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    return path


def test_griffin_manifest_loads() -> None:
    spec = load_manifest(builtin_manifest_path("griffin"))
    assert spec.adapter == "griffin"
    assert spec.spec_version == 1
    assert spec.params["subset"].allowed == ("25m", "40m", "55m", "random")
    assert spec.clock.scale_us == 100000
    assert spec.clock.scale_note.startswith("Griffin pose")
    assert spec.scenes.index is not None
    assert spec.scenes.index.must_equal == ("drone-side/scene_infos.json",)
    assert [agent.id for agent in spec.agents.fixed] == ["vehicle", "drone"]
    assert spec.agents.fixed[1].type.value == "uav"
    assert spec.conventions.body_axes == "FLU"
    assert spec.streams.poses.rotation.euler is not None
    assert spec.streams.poses.rotation.euler.order == "zyx"
    assert spec.streams.boxes is not None
    assert 9 in spec.streams.boxes.variants
    assert spec.streams.boxes.rotation.missing_angles is not None
    assert spec.streams.lidar is not None
    assert spec.streams.lidar.fields["intensity"] == "I"
    assert spec.checks.required_per_sample == ("poses", "boxes")
    assert spec.report.variant_counts == "boxes"


def test_unknown_key_names_its_path(tmp_path: Path) -> None:
    payload = _griffin()
    payload["streams"]["boxes"]["colour"] = "red"  # type: ignore[index]
    with pytest.raises(SpecError, match=r"streams\.boxes: unknown key 'colour'"):
        load_manifest(_write(tmp_path, payload))


def test_missing_scale_note_is_an_error(tmp_path: Path) -> None:
    payload = _griffin()
    del payload["clock"]["scale_note"]  # type: ignore[index]
    with pytest.raises(SpecError, match=r"clock\.scale_note"):
        load_manifest(_write(tmp_path, payload))


def test_frame_clock_needs_scale_us(tmp_path: Path) -> None:
    payload = _griffin()
    del payload["clock"]["scale_us"]  # type: ignore[index]
    with pytest.raises(SpecError, match=r"clock\.scale_us"):
        load_manifest(_write(tmp_path, payload))


def test_bad_template_placeholder_is_reported_at_its_key(tmp_path: Path) -> None:
    payload = _griffin()
    payload["streams"]["poses"]["path"] = "{agent_root}/pose/{nope}.json"  # type: ignore[index]
    with pytest.raises(SpecError, match=r"streams\.poses\.path.*unknown placeholder"):
        load_manifest(_write(tmp_path, payload))


def test_a_param_can_be_a_template_placeholder(tmp_path: Path) -> None:
    payload = _griffin()
    payload["streams"]["poses"]["path"] = "{subset}/{agent_root}/pose/{frame:06d}.json"  # type: ignore[index]
    spec = load_manifest(_write(tmp_path, payload))
    assert "subset" in spec.streams.poses.path.placeholders


def test_rotation_needs_exactly_one_encoding(tmp_path: Path) -> None:
    payload = _griffin()
    payload["streams"]["poses"]["rotation"]["quaternion"] = {"fields": ["a", "b", "c", "d"]}  # type: ignore[index]
    with pytest.raises(SpecError, match=r"streams\.poses\.rotation: exactly one"):
        load_manifest(_write(tmp_path, payload))


def test_zero_fill_needs_a_reason(tmp_path: Path) -> None:
    payload = _griffin()
    del payload["streams"]["boxes"]["rotation"]["missing_angles"]["reason"]  # type: ignore[index]
    with pytest.raises(SpecError, match=r"missing_angles\.reason"):
        load_manifest(_write(tmp_path, payload))


def test_spec_version_and_adapter_name(tmp_path: Path) -> None:
    payload = _griffin()
    payload["spec_version"] = 2
    with pytest.raises(SpecError, match="spec_version"):
        load_manifest(_write(tmp_path, payload))
    payload = _griffin()
    payload["adapter"] = "Bad Name"
    with pytest.raises(SpecError, match="adapter"):
        load_manifest(_write(tmp_path, payload))


def test_agents_can_be_discovered(tmp_path: Path) -> None:
    payload = _griffin()
    payload["agents"] = {"discover": {"glob": "{scene}/{agent}/", "type": "vehicle"}}
    payload["scenes"] = {"glob": {"path": "{scene}/", "sample_source": "poses"}}
    payload["streams"]["poses"]["path"] = "{scene}/{agent}/{frame:06d}.yaml"  # type: ignore[index]
    payload["streams"]["sensors"]["path"] = "{scene}/{agent}/{sensor}.yaml"  # type: ignore[index]
    payload["streams"]["boxes"]["path"] = "{scene}/{agent}/{frame:06d}.txt"  # type: ignore[index]
    payload["streams"]["images"]["path"] = "{scene}/{agent}/{sensor}/{frame:06d}.png"  # type: ignore[index]
    payload["streams"]["lidar"]["path"] = "{scene}/{agent}/{frame:06d}.pcd"  # type: ignore[index]
    payload["streams"]["lidar"]["directory"] = "{scene}/{agent}"  # type: ignore[index]
    payload["streams"]["lidar"]["agents"] = None  # type: ignore[index]
    spec = load_manifest(_write(tmp_path, payload))
    assert spec.agents.discover is not None
    assert spec.agents.discover.exclude == ()
    assert spec.scenes.glob is not None
    assert spec.scenes.glob.sample_source == "poses"
