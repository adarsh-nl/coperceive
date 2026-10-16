# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import io
import struct
from pathlib import Path
from typing import cast

import pytest
from coperceive.cli import main
from coperceive.hdmap.opendrive import parse_opendrive
from coperceive.hdmap.store import load_map, write_map
from coperceive.model import (
    SCHEMA_VERSION,
    Agent,
    AgentType,
    Annotation,
    BoxFrame,
    Clock,
    ImageRef,
    Modality,
    PointCloud,
    Scene,
    Sensor,
    StampedPose,
)
from coperceive.plot import PlotError, PlotNotFound, render_bev, render_overlay
from coperceive.se3 import SE3
from coperceive.store import read_scene, write_scene
from PIL import Image

_XODR = """<?xml version="1.0" encoding="UTF-8"?>
<OpenDRIVE>
  <header revMajor="1" revMinor="4" name="fixture" north="10" south="-10" east="20" west="-20">
    <geoReference><![CDATA[+proj=tmerc +units=m +no_defs]]></geoReference>
  </header>
  <road name="main" length="40" id="1" junction="-1">
    <planView>
      <geometry s="0" x="0" y="0" hdg="0" length="40"><line/></geometry>
    </planView>
    <lanes>
      <laneSection s="0">
        <left>
          <lane id="1" type="driving" level="false">
            <width sOffset="0" a="3.5" b="0" c="0" d="0"/>
          </lane>
        </left>
        <center><lane id="0" type="none" level="false"/></center>
        <right>
          <lane id="-1" type="driving" level="false">
            <width sOffset="0" a="3.5" b="0" c="0" d="0"/>
          </lane>
        </right>
      </laneSection>
    </lanes>
  </road>
</OpenDRIVE>
"""


def _png(color: tuple[int, int, int] = (30, 40, 50)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (16, 16), color).save(buffer, format="PNG")
    return buffer.getvalue()


def _scene() -> Scene:
    vehicle = Agent(
        "vehicle",
        AgentType.VEHICLE,
        (
            StampedPose(0, SE3.identity()),
            StampedPose(100_000, SE3.identity().translation(10.0, 0.0, 0.0)),
        ),
        (("map_name", "Carla/Maps/Town03"),),
    )
    drone = Agent(
        "drone",
        AgentType.UAV,
        (
            StampedPose(0, SE3.identity().translation(0.0, 8.0, 5.0)),
            StampedPose(100_000, SE3.identity().translation(2.0, 8.0, 5.0)),
        ),
        (("map_name", "Carla/Maps/Town03"),),
    )
    camera = Sensor(
        "front",
        "vehicle",
        Modality.CAMERA,
        SE3.identity(),
        16,
        16,
        8.0,
        8.0,
        8.0,
        8.0,
    )
    lidar = Sensor("top", "vehicle", Modality.LIDAR, SE3.identity())
    cloud = PointCloud(
        "vehicle",
        "top",
        0,
        2,
        struct.pack("<ffffffff", 1.0, 0.5, 4.0, 1.0, 0.0, 0.0, -2.0, 1.0),
    )
    box = Annotation(
        "vehicle",
        0,
        None,
        "car",
        (0.0, 0.0, 8.0),
        (2.0, 1.0, 1.0),
        SE3.identity(),
        None,
        BoxFrame.WORLD,
    )
    scene = Scene(
        schema_version=SCHEMA_VERSION,
        scene_id="desk",
        world_frame="ENU",
        clock=Clock("us", "plot fixture"),
        agents=(vehicle, drone),
        sensors=(camera, lidar),
        annotations=(box,),
        objects=(),
        point_clouds=(cloud,),
        images=(ImageRef("vehicle", "front", 0, "camera/front/0.png"),),
    )
    return scene


def _write(root: Path) -> Path:
    scene = _scene()
    scene_dir = root / "desk"
    write_scene(scene, scene_dir)
    image = scene_dir / "camera" / "front" / "0.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(_png())
    return scene_dir


def _map(root: Path):
    xodr = root / "Town03.xodr"
    xodr.write_text(_XODR, encoding="utf-8")
    directory = root / "maps" / "Town03"
    write_map(
        parse_opendrive(xodr, sample_step_m=10),
        directory,
        map_name="Carla/Maps/Town03",
        source={"licence": "MIT"},
    )
    return load_map(directory)


def test_bev_png_and_svg_draw_trails_boxes_and_lidar(tmp_path: Path) -> None:
    scene = _scene()
    hdmap = _map(tmp_path)
    png = render_bev(scene, tmp_path / "bev.png", time_us=0, hdmap=hdmap, format="png")
    assert png.map_drawn is True
    assert png.points == 2
    assert png.boxes == 1
    assert png.time_us == 0
    assert png.width == 960
    assert png.height == 960
    image = cast(Image.Image, Image.open(png.dest))
    try:
        assert image.size == (960, 960)
    finally:
        image.close()
    svg = render_bev(
        scene,
        tmp_path / "bev.svg",
        time_us=0,
        hdmap=hdmap,
        theme="light",
        color_by="class",
        box_style="solid",
    )
    text = svg.dest.read_text(encoding="utf-8")
    assert "desk" in text
    assert "east (m)" in text
    assert "north (m)" in text
    assert "vehicle" in text
    assert "drone" in text
    hidden = render_bev(scene, tmp_path / "hidden.svg", time_us=0, hidden_classes=("car",))
    assert hidden.boxes == 0
    assert hidden.map_drawn is False


def test_bev_rejects_a_time_that_is_not_a_sample(tmp_path: Path) -> None:
    scene = _scene()
    with pytest.raises(PlotNotFound, match="not a recorded sample"):
        render_bev(scene, tmp_path / "bev.png", time_us=5)


def test_overlay_projects_the_point_in_front_and_confines_the_path(tmp_path: Path) -> None:
    scene_dir = _write(tmp_path)
    scene = read_scene(scene_dir)
    saved = render_overlay(
        scene,
        scene_dir,
        tmp_path / "overlay.png",
        agent_id="vehicle",
        sensor_id="front",
        time_us=0,
    )
    assert saved.points == 1
    assert saved.boxes == 1
    assert saved.width == 16
    assert saved.height == 16
    rendered = cast(Image.Image, Image.open(saved.dest))
    try:
        # The source photo is (30, 40, 50). A closed Pillow buffer used to
        # leave this figure the theme background instead of the photo.
        corner = cast(tuple[int, int, int], rendered.convert("RGB").getpixel((0, saved.height - 1)))
    finally:
        rendered.close()
    assert abs(corner[0] - 30) < 8
    assert abs(corner[1] - 40) < 8
    assert abs(corner[2] - 50) < 8
    svg = render_overlay(
        scene,
        scene_dir,
        tmp_path / "overlay.svg",
        agent_id="vehicle",
        sensor_id="front",
        time_us=0,
        format="svg",
    )
    text = svg.dest.read_text(encoding="utf-8")
    assert "vehicle/front" in text
    leaked = Scene(
        schema_version=scene.schema_version,
        scene_id=scene.scene_id,
        world_frame=scene.world_frame,
        clock=scene.clock,
        agents=scene.agents,
        sensors=scene.sensors,
        annotations=scene.annotations,
        objects=scene.objects,
        point_clouds=scene.point_clouds,
        images=(ImageRef("vehicle", "front", 0, "../secret.png"),),
    )
    with pytest.raises(PlotError, match="leaves the scene"):
        render_overlay(
            leaked,
            scene_dir,
            tmp_path / "leak.png",
            agent_id="vehicle",
            sensor_id="front",
            time_us=0,
        )


def test_cli_bev_and_overlay(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    scene_dir = _write(tmp_path)
    _map(tmp_path)
    out = tmp_path / "cli.svg"
    main(
        [
            "export",
            "bev",
            str(scene_dir),
            "--out",
            str(out),
            "--maps",
            str(tmp_path / "maps"),
            "--time-us",
            "0",
        ]
    )
    assert out.is_file()
    assert "map 1" in capsys.readouterr().out
    overlay = tmp_path / "cli-overlay.png"
    main(
        [
            "export",
            "overlay",
            str(scene_dir),
            "--out",
            str(overlay),
            "--agent",
            "vehicle",
            "--sensor",
            "front",
            "--time-us",
            "0",
        ]
    )
    assert overlay.is_file()
    assert "points 1" in capsys.readouterr().out
    with pytest.raises(SystemExit) as exc:
        main(["export", "bev", str(scene_dir), "--out", str(tmp_path / "bad.txt")])
    assert exc.value.code == 2
