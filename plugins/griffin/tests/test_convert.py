# SPDX-License-Identifier: Apache-2.0
"""The plugin's conversion and staging rules (Python 3.8 modules, run under any interpreter)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1] / "coperceive_griffin_model"
sys.path.insert(0, str(PLUGIN))

import convert  # noqa: E402
import stage  # noqa: E402

FIXTURES = Path(__file__).with_name("fixtures")


def test_wlh_becomes_lwh() -> None:
    anno = {
        "translation": [1.0, 2.0, 3.0],
        "size": [1.9, 4.5, 1.6],
        "rotation": [1.0, 0.0, 0.0, 0.0],
        "detection_name": "car",
        "detection_score": 0.5,
    }
    box = convert.box_from_detection(anno, "x", agent_id="vehicle", source="single")
    assert box["size_lwh"] == [4.5, 1.9, 1.6]
    assert box["center"] == [1.0, 2.0, 3.0] and box["frame"] == "world"
    assert box["agent_id"] == "vehicle" and box["source"] == "single"


def test_quaternion_slack() -> None:
    assert convert.unit_rotation((1.0005, 0.0, 0.0, 0.0), "x")[0] == pytest.approx(1.0)
    with pytest.raises(convert.ConvertError, match="norm"):
        convert.unit_rotation((1.1, 0.0, 0.0, 0.0), "x")


def test_scene_id_of() -> None:
    assert convert.scene_id_of("scene-0000-Town03-000") == "Town03-000"
    with pytest.raises(convert.ConvertError):
        convert.scene_id_of("Town03-000")


def test_frame_id_of() -> None:
    assert convert.frame_id_of(convert.BASE_US + 3 * convert.STEP_US, "x") == 3
    with pytest.raises(convert.ConvertError):
        convert.frame_id_of(convert.BASE_US + 3 * convert.STEP_US + 5_000, "x")


def test_min_score_drops_and_index_maps_scene_and_time() -> None:
    samples = convert.read_samples(str(FIXTURES))
    assert samples[f"sample_{convert.BASE_US}"] == ("synthetic-check", 0)
    index, dropped = convert.index_detections(
        str(FIXTURES / "results_nusc_det.json"),
        samples,
        agent_id="vehicle",
        source="single",
        min_score=0.05,
    )
    assert dropped == 1
    assert [b["category"] for b in index[("synthetic-check", 0)]] == ["car", "pedestrian"]
    assert len(index[("synthetic-check", 100_000)]) == 1
    rotation = index[("synthetic-check", 100_000)][0]["quaternion"]
    assert rotation[0] == pytest.approx(rotation[3]) and sum(
        q * q for q in rotation
    ) == pytest.approx(1.0)


def test_md5_of(tmp_path: Path) -> None:
    path = tmp_path / "w.bin"
    path.write_bytes(b"abc")
    assert convert.md5_of(str(path)) == "900150983cd24fb0d6963f7d28e17f72"


def _infos() -> list:
    return [
        {"name": "Town03-000", "info": {"frames": ["000010", "000011", "000012"]}},
        {"name": "Town03-006", "info": {"frames": ["000020", "000021"]}},
        {"name": "Town07-011", "info": {"frames": ["000030", "000031"]}},
    ]


def test_present_scenes_and_renumbered_split(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    for side in stage.SIDES:
        (raw / side / "pose").mkdir(parents=True)
        (raw / side / "scene_infos.json").write_text(json.dumps(_infos()), encoding="utf-8")
    for frame in ("000010", "000011", "000012", "000030", "000031"):  # scenes 0 and 2 staged
        (raw / "vehicle-side" / "pose" / f"{frame}.json").write_text("{}", encoding="utf-8")
    infos = stage.load_scene_infos(str(raw / "vehicle-side" / "scene_infos.json"))
    stage.check_blocks(infos)
    picked = stage.present_scenes(infos, str(raw))
    assert [index for index, _ in picked] == [0, 2]
    split = tmp_path / "split.json"
    split.write_text(
        json.dumps(
            {
                "batch_split": {
                    "train": [],
                    "val": ["scene-0002-Town07-011", "scene-0000-Town03-000"],
                }
            }
        ),
        encoding="utf-8",
    )
    stage.check_against_split(picked, str(split))
    release = tmp_path / "release"
    names = stage.write_trimmed(str(release), picked, str(tmp_path / "split-out.json"))
    assert names == ["scene-0000-Town03-000", "scene-0001-Town07-011"]
    trimmed = json.loads(
        (release / "vehicle-side" / "scene_infos.json").read_text(encoding="utf-8")
    )
    assert [s["name"] for s in trimmed] == ["Town03-000", "Town07-011"]
    out = json.loads((tmp_path / "split-out.json").read_text(encoding="utf-8"))
    assert out == {"batch_split": {"train": [], "val": names}}
    stage.link_release(str(raw), str(release))
    assert (release / "vehicle-side" / "pose").is_symlink()


def test_check_against_split_refuses_a_shifted_index(tmp_path: Path) -> None:
    split = tmp_path / "split.json"
    split.write_text(
        json.dumps({"batch_split": {"train": [], "val": ["scene-0001-Town03-000"]}}),
        encoding="utf-8",
    )
    with pytest.raises(stage.StageError, match="published index"):
        stage.check_against_split([(0, _infos()[0])], str(split))
