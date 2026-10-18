# SPDX-License-Identifier: Apache-2.0
"""``coperceive maps identify``: a scene's town from the alignment residual (ADR 0012)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from coperceive.adapters.ingest import read_platform_table
from coperceive.cli import main
from coperceive.hdmap.identify import identify_town, identify_towns, write_town_table
from coperceive.hdmap.opendrive import parse_opendrive
from coperceive.hdmap.store import load_map, write_map
from coperceive.model import SCHEMA_VERSION, Agent, AgentType, Clock, Scene, StampedPose
from coperceive.se3 import SE3
from coperceive.store import read_scene, write_scene

from tests.test_hdmap import straight_road

SOURCE = {"format": "opendrive", "licence": "MIT"}


def _maps(tmp_path: Path, shift: float = 200.0) -> Path:
    """Two towns: a road along x at y = 0 (Town03) and one shifted to y = ``shift`` (Town05)."""
    maps = tmp_path / "maps"
    xodr = tmp_path / "Town03.xodr"
    straight_road(xodr)
    write_map(
        parse_opendrive(xodr, sample_step_m=5),
        maps / "Town03",
        map_name="Carla/Maps/Town03",
        source=SOURCE,
    )
    shifted = tmp_path / "Town05.xodr"
    shifted.write_text(
        xodr.read_text(encoding="utf-8").replace('x="0" y="0"', f'x="0" y="{shift:g}"'),
        encoding="utf-8",
    )
    write_map(
        parse_opendrive(shifted, sample_step_m=5),
        maps / "Town05",
        map_name="Carla/Maps/Town05",
        source=SOURCE,
    )
    return maps


def _scene(root: Path, scene_id: str, *, y: float, count: int = 5) -> None:
    poses = tuple(
        StampedPose(i * 100_000, SE3.identity().translation(20.0 + 10.0 * i, y, 0.4))
        for i in range(count)
    )
    scene = Scene(
        schema_version=SCHEMA_VERSION,
        scene_id=scene_id,
        world_frame="carla-map",
        clock=Clock("us", "fixture"),
        agents=(Agent("641", AgentType.VEHICLE, poses),),
        sensors=(),
        annotations=(),
        objects=(),
    )
    write_scene(scene, root / scene_id)


def test_identify_picks_the_map_the_poses_follow(tmp_path: Path) -> None:
    maps = _maps(tmp_path)
    scenes = tmp_path / "scenes"
    _scene(scenes, "2021_08_16_22_26_54", y=-1.75)  # on Town03's right lane
    _scene(scenes, "2021_08_18_09_02_56", y=201.75)  # on Town05's left lane
    guesses = identify_towns(maps, scenes)
    assert [(g.scene_id, g.map_name, g.status) for g in guesses] == [
        ("2021_08_16_22_26_54", "Carla/Maps/Town03", "identified"),
        ("2021_08_18_09_02_56", "Carla/Maps/Town05", "identified"),
    ]
    assert guesses[0].best_m == pytest.approx(0.0, abs=1e-6)
    assert guesses[0].runner_up == "Carla/Maps/Town05"
    assert guesses[0].runner_up_m == pytest.approx(200.0, abs=0.5)


def test_unidentified_when_the_best_is_far_or_the_runner_up_is_not_ten_times_worse(
    tmp_path: Path,
) -> None:
    maps = _maps(tmp_path)
    scenes = tmp_path / "scenes"
    _scene(scenes, "near", y=5.0)  # 3.25 m from Town03's left lane, 193.25 m from Town05's
    _scene(scenes, "between", y=100.0)  # equidistant: the runner-up is 1× the best
    loose = {g.scene_id: g for g in identify_towns(maps, scenes, accept_m=100.0)}
    assert loose["near"].map_name == "Carla/Maps/Town03"  # 3.25 m accepted under a 100 m threshold
    assert loose["near"].best_m == pytest.approx(3.25, abs=0.05)
    assert loose["between"].map_name is None
    assert loose["between"].status == "unidentified"
    assert "under 10×" in loose["between"].reason
    strict = identify_town(
        read_scene(scenes / "near"), [load_map(maps / "Town03"), load_map(maps / "Town05")]
    )
    assert strict.map_name is None and "above 0.5 m" in strict.reason


def test_unidentified_when_the_runner_up_is_within_the_absolute_gap(tmp_path: Path) -> None:
    maps = _maps(tmp_path, shift=0.3)  # two towns whose roads differ by 0.3 m
    scenes = tmp_path / "scenes"
    _scene(scenes, "on", y=-1.75)  # exactly on Town03's lane, 0.3 m from Town05's
    guess = identify_towns(maps, scenes)[0]
    assert guess.best_m == pytest.approx(0.0, abs=1e-6)
    assert guess.runner_up_m == pytest.approx(0.3, abs=0.02)
    assert guess.map_name is None and guess.status == "unidentified"
    assert "within 0.5 m" in guess.reason  # the ratio passes (10 × 0 m), the gap does not


def test_table_round_trips_through_the_platform_loader(tmp_path: Path) -> None:
    maps = _maps(tmp_path)
    scenes = tmp_path / "scenes"
    _scene(scenes, "on", y=-1.75)
    _scene(scenes, "off", y=100.0)
    table = tmp_path / "scene_towns.yaml"
    write_town_table(identify_towns(maps, scenes), table)
    raw = yaml.safe_load(table.read_text(encoding="utf-8"))
    assert raw["on"]["map_name"] == "Carla/Maps/Town03"
    assert "map_name" not in raw["off"]
    assert raw["off"]["_status"] == "unidentified"
    loaded = read_platform_table(table)
    assert loaded == {"on": {"map_name": "Carla/Maps/Town03"}, "off": {}}


def test_cli_prints_one_line_per_scene(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    maps = _maps(tmp_path)
    scenes = tmp_path / "scenes"
    _scene(scenes, "on", y=-1.75)
    out = tmp_path / "towns.yaml"
    main(["maps", "identify", "--maps", str(maps), "--scenes", str(scenes), "--out", str(out)])
    text = capsys.readouterr().out
    assert "on identified Carla/Maps/Town03" in text
    assert "identified 1 of 1 scenes" in text
    assert out.is_file()
