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
from coperceive.store import write_scene

from tests.test_hdmap import straight_road

SOURCE = {"format": "opendrive", "licence": "MIT"}


def _maps(tmp_path: Path) -> Path:
    """Two towns: a road along x at y = 0 (Town03) and one shifted to y = 200 (Town05)."""
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
        xodr.read_text(encoding="utf-8").replace('x="0" y="0"', 'x="0" y="200"'), encoding="utf-8"
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


def test_unidentified_town_leaves_map_name_empty(tmp_path: Path) -> None:
    maps = _maps(tmp_path)
    scenes = tmp_path / "scenes"
    _scene(scenes, "far", y=50.0)  # 48 m from either road
    _scene(scenes, "between", y=100.0)  # equidistant: no margin
    guesses = {g.scene_id: g for g in identify_towns(maps, scenes, accept_m=100.0, margin_m=2.0)}
    assert guesses["far"].map_name == "Carla/Maps/Town03"  # 48.25 m accepted with a 100 m threshold
    assert guesses["between"].map_name is None
    assert guesses["between"].status == "unidentified"
    assert "within 2.0 m" in guesses["between"].reason
    strict = identify_town(
        __import__("coperceive.store", fromlist=["read_scene"]).read_scene(scenes / "far"),
        [load_map(maps / "Town03"), load_map(maps / "Town05")],
    )
    assert strict.map_name is None and "above 0.5 m" in strict.reason


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
