# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import math
from pathlib import Path
from typing import cast

import pytest
from coperceive.cli import main
from coperceive.hdmap.align import align_maps
from coperceive.hdmap.opendrive import MapPolyline, OpenDriveMap, parse_opendrive
from coperceive.hdmap.store import load_map, write_map
from coperceive.model import (
    SCHEMA_VERSION,
    Agent,
    AgentType,
    Clock,
    Scene,
    StampedPose,
)
from coperceive.se3 import SE3
from coperceive.store import write_scene
from coperceive.validate import validate_root

_HEADER = """<?xml version="1.0" encoding="UTF-8"?>
<OpenDRIVE>
  <header revMajor="1" revMinor="4" name="fixture" north="10" south="-10" east="20" west="-20">
    <geoReference><![CDATA[+proj=tmerc +units=m +no_defs]]></geoReference>
  </header>
  {roads}
</OpenDRIVE>
"""


def _lane(lane_id: int, lane_type: str, width: float) -> str:
    return f"""
          <lane id="{lane_id}" type="{lane_type}" level="false">
            <width sOffset="0" a="{width}" b="0" c="0" d="0"/>
          </lane>"""


def _straight(
    path: Path,
    *,
    sidewalk: bool = False,
    shoulder: bool = False,
    lane_offset: float = 0.0,
) -> None:
    left = _lane(1, "driving", 3.5)
    next_id = 2
    if shoulder:
        left += _lane(next_id, "shoulder", 1.5)
        next_id += 1
    if sidewalk:
        left += _lane(next_id, "sidewalk", 2.0)
    offset = ""
    if lane_offset != 0.0:
        offset = f'<laneOffset s="0" a="{lane_offset}" b="0" c="0" d="0"/>'
    road = f"""
  <road name="main" length="100" id="1" junction="-1">
    <planView>
      <geometry s="0" x="0" y="0" hdg="0" length="100"><line/></geometry>
    </planView>
    <elevationProfile>
      <elevation s="0" a="1" b="0" c="0" d="0"/>
    </elevationProfile>
    <lanes>
      {offset}
      <laneSection s="0">
        <left>{left}</left>
        <center><lane id="0" type="none" level="false"/></center>
        <right>{_lane(-1, "driving", 3.5)}</right>
      </laneSection>
    </lanes>
  </road>"""
    path.write_text(_HEADER.format(roads=road), encoding="utf-8")


def _layer(parsed: OpenDriveMap, name: str) -> list[MapPolyline]:
    return [item for item in parsed.polylines if item.layer == name]


def _object_list(value: object) -> list[object]:
    assert isinstance(value, list)
    return cast(list[object], value)


def _object_dict(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    return cast(dict[str, object], value)


def _first(values: list[object]) -> object:
    assert values
    return values[0]


def test_straight_road_centres_and_edges(tmp_path: Path) -> None:
    path = tmp_path / "straight.xodr"
    _straight(path, shoulder=True, sidewalk=True)
    parsed = parse_opendrive(path, sample_step_m=10)
    centres = _layer(parsed, "lane_centre")
    assert {item.lane_id for item in centres} == {-1, 1}
    left = next(item for item in centres if item.lane_id == 1)
    right = next(item for item in centres if item.lane_id == -1)
    assert left.xyz[0][1] == pytest.approx(1.75)
    assert right.xyz[0][1] == pytest.approx(-1.75)
    assert left.xyz[0][2] == pytest.approx(1.0)
    assert left.xyz[-1][0] == pytest.approx(100.0)
    edges = {item.side: item.xyz[0][1] for item in _layer(parsed, "road_edge")}
    assert edges["left"] == pytest.approx(5.0)
    assert edges["right"] == pytest.approx(-3.5)
    reference = _layer(parsed, "reference")
    assert reference[0].xyz[0][1] == pytest.approx(0.0)


def test_lane_offset_shifts_the_centre(tmp_path: Path) -> None:
    path = tmp_path / "offset.xodr"
    _straight(path, lane_offset=1.0)
    parsed = parse_opendrive(path, sample_step_m=50)
    left = next(item for item in _layer(parsed, "lane_centre") if item.lane_id == 1)
    assert left.xyz[0][1] == pytest.approx(2.75)
    assert _layer(parsed, "reference")[0].xyz[0][1] == pytest.approx(0.0)


def test_arc_ends_at_the_quarter_circle(tmp_path: Path) -> None:
    length = math.pi * 5.0
    road = f"""
  <road name="bend" length="{length}" id="7" junction="-1">
    <planView>
      <geometry s="0" x="0" y="0" hdg="0" length="{length}">
        <arc curvature="0.1"/>
      </geometry>
    </planView>
    <lanes>
      <laneSection s="0">
        <center><lane id="0" type="none" level="false"/></center>
        <right>{_lane(-1, "driving", 0)}</right>
      </laneSection>
    </lanes>
  </road>"""
    path = tmp_path / "arc.xodr"
    path.write_text(_HEADER.format(roads=road), encoding="utf-8")
    parsed = parse_opendrive(path, sample_step_m=length)
    end = _layer(parsed, "reference")[0].xyz[-1]
    assert end[0] == pytest.approx(10.0, abs=1e-9)
    assert end[1] == pytest.approx(10.0, abs=1e-9)


def test_param_poly3_and_zero_spiral_match_a_line(tmp_path: Path) -> None:
    road = """
  <road name="poly" length="40" id="3" junction="-1">
    <planView>
      <geometry s="0" x="0" y="0" hdg="0" length="20">
        <paramPoly3 aU="0" bU="20" cU="0" dU="0" aV="0" bV="0" cV="0" dV="0" pRange="normalized"/>
      </geometry>
      <geometry s="20" x="20" y="0" hdg="0" length="20">
        <spiral curvStart="0" curvEnd="0"/>
      </geometry>
    </planView>
    <lanes>
      <laneSection s="0">
        <center><lane id="0" type="none" level="false"/></center>
      </laneSection>
    </lanes>
  </road>"""
    path = tmp_path / "poly.xodr"
    path.write_text(_HEADER.format(roads=road), encoding="utf-8")
    parsed = parse_opendrive(path, sample_step_m=10)
    end = _layer(parsed, "reference")[0].xyz[-1]
    assert end[0] == pytest.approx(40.0, abs=1e-2)
    assert end[1] == pytest.approx(0.0, abs=1e-2)


def test_unknown_geometry_is_rejected(tmp_path: Path) -> None:
    road = """
  <road name="nope" length="10" id="1" junction="-1">
    <planView>
      <geometry s="0" x="0" y="0" hdg="0" length="10"><weird/></geometry>
    </planView>
    <lanes><laneSection s="0"><center><lane id="0" type="none"/></center></laneSection></lanes>
  </road>"""
    path = tmp_path / "bad.xodr"
    path.write_text(_HEADER.format(roads=road), encoding="utf-8")
    with pytest.raises(ValueError, match="not supported"):
        parse_opendrive(path)


straight_road = _straight  # for tests of maps identify


def _scene_on_lane(root: Path, *, y: float = -1.75) -> None:
    pose = StampedPose(0, SE3.identity().translation(40.0, y, 0.4))
    agent = Agent(
        "vehicle",
        AgentType.VEHICLE,
        (pose,),
        platform=(("map_name", "Carla/Maps/Town03"),),
    )
    scene = Scene(
        schema_version=SCHEMA_VERSION,
        scene_id="Town03-000",
        world_frame="ENU",
        clock=Clock("us", "hdmap fixture"),
        agents=(agent,),
        sensors=(),
        annotations=(),
        objects=(),
    )
    write_scene(scene, root / "Town03-000")


def test_store_roundtrip_keeps_opendrive_metres(tmp_path: Path) -> None:
    xodr = tmp_path / "Town03.xodr"
    _straight(xodr)
    parsed = parse_opendrive(xodr, sample_step_m=10)
    directory = tmp_path / "maps" / "Town03"
    write_map(
        parsed,
        directory,
        map_name="Carla/Maps/Town03",
        source={
            "format": "opendrive",
            "filename": "Town03.xodr",
            "sha256": "abc",
            "licence": "MIT",
        },
    )
    loaded = load_map(directory)
    assert loaded.world_from_opendrive.measured is False
    assert loaded.world_from_opendrive.determinant() == pytest.approx(1.0)
    right = next(item for item in loaded.polylines if item.lane_id == -1)
    assert right.xyz[0][1] == pytest.approx(-1.75)
    blob = (directory / "vertices.bin").read_bytes()
    assert len(blob) % 12 == 0
    assert len(blob) == sum(len(item.xyz) for item in loaded.polylines) * 12


def test_align_keeps_identity_and_records_the_residual(tmp_path: Path) -> None:
    xodr = tmp_path / "Town03.xodr"
    _straight(xodr)
    maps = tmp_path / "maps"
    parsed = parse_opendrive(xodr, sample_step_m=10)
    write_map(
        parsed,
        maps / "Town03",
        map_name="Carla/Maps/Town03",
        source={"format": "opendrive", "licence": "MIT"},
    )
    scenes = tmp_path / "scenes"
    _scene_on_lane(scenes)
    reports = align_maps(maps, scenes)
    assert reports[0]["chosen"] == "identity"
    assert reports[0]["translation_applied"] is False
    assert reports[0]["residual_median_m"] == pytest.approx(0.0, abs=1e-6)
    loaded = load_map(maps / "Town03")
    assert loaded.world_from_opendrive.measured is True
    assert "OpenDRIVE +X" in str(reports[0]["note"])
    assert "tmerc" in str(reports[0]["note"]) or "easting" in str(reports[0]["note"])


def test_align_stores_a_large_shift(tmp_path: Path) -> None:
    xodr = tmp_path / "Town03.xodr"
    _straight(xodr)
    maps = tmp_path / "maps"
    write_map(
        parse_opendrive(xodr, sample_step_m=10),
        maps / "Town03",
        map_name="Carla/Maps/Town03",
        source={"licence": "MIT"},
    )
    scenes = tmp_path / "scenes"
    _scene_on_lane(scenes, y=-1.75 + 12.0)
    reports = align_maps(maps, scenes)
    assert reports[0]["translation_applied"] is True
    assert reports[0]["residual_median_m"] == pytest.approx(0.0, abs=1e-6)
    loaded = load_map(maps / "Town03")
    # y = 10.25 is nearer the left lane (1.75) than the right lane (-1.75).
    assert loaded.world_from_opendrive.t[1] == pytest.approx(8.5, abs=1e-6)


def test_validate_reports_the_centreline_and_the_claim(tmp_path: Path) -> None:
    xodr = tmp_path / "Town03.xodr"
    _straight(xodr)
    maps = tmp_path / "maps"
    write_map(
        parse_opendrive(xodr, sample_step_m=10),
        maps / "Town03",
        map_name="Carla/Maps/Town03",
        source={"licence": "MIT"},
    )
    scenes = tmp_path / "scenes"
    _scene_on_lane(scenes)
    align_maps(maps, scenes)
    report = validate_root(scenes, maps=maps)
    scene = _object_dict(_first(_object_list(report["scenes"])))
    centre = _object_dict(scene["centreline"])
    distances = _object_dict(centre["vehicle_pose_to_lane_centre_m"])
    assert distances["median"] == pytest.approx(0.0, abs=1e-6)
    assert centre["lane_centre_median_minus_claim_m"] == pytest.approx(-0.24)
    summary = _object_dict(_object_dict(report["aggregate"])["centreline"])
    assert summary["status"] == "measured"
    assert summary["claim_m"] == 0.24


def test_validate_without_maps_records_that_it_was_not_measured(tmp_path: Path) -> None:
    _scene_on_lane(tmp_path)
    report = validate_root(tmp_path)
    summary = _object_dict(_object_dict(report["aggregate"])["centreline"])
    assert summary["status"] == "not measured"


def test_cli_ingest(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    xodr = tmp_path / "Town03.xodr"
    _straight(xodr)
    main(
        [
            "maps",
            "ingest",
            "--xodr",
            str(xodr),
            "--map-name",
            "Carla/Maps/Town03",
            "--out",
            str(tmp_path / "maps"),
            "--source-url",
            "https://downloads.carlasim.com/Linux/CARLA_0.9.15.tar.gz",
        ]
    )
    assert "wrote" in capsys.readouterr().out
    loaded = load_map(tmp_path / "maps" / "Town03")
    assert loaded.map_name == "Carla/Maps/Town03"
    assert loaded.source["licence"] == "CC-BY"
    assert "CC-BY" in str(loaded.source["licence_note"])
    assert "0.9.15/README.md" in str(loaded.source["licence_note"])
