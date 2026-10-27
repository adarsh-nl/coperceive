# SPDX-License-Identifier: Apache-2.0
"""``scripts/site/export_hero_clip.py``: a 4-agent scene round-trips through ``CPCVCLP2``.

The scene and map are generated here and written under ``tmp_path``; nothing is
committed. Three ground vehicles and one drone each carry a lidar.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path

import pytest
from coperceive.hdmap.frame import FrameMap
from coperceive.hdmap.opendrive import (
    LAYER_LANE_CENTRE,
    LAYER_ROAD_EDGE,
    MapPolyline,
    OpenDriveMap,
)
from coperceive.hdmap.store import write_alignment, write_map
from coperceive.model import (
    SCHEMA_VERSION,
    Agent,
    AgentType,
    Clock,
    Modality,
    PointCloud,
    Scene,
    Sensor,
    StampedPose,
)
from coperceive.se3 import SE3
from coperceive.store import write_scene
from scripts.site import export_hero_clip as exporter

STEP_US = 100_000
FRAMES = 5
# Start positions (x, y, z) in metres; each agent moves +1 m in x per frame.
STARTS = {
    "car_a": (200.0, 50.0, 0.0),
    "car_b": (210.0, 40.0, 0.0),
    "car_c": (190.0, 60.0, 0.0),
    "uav": (204.0, 50.0, 30.0),
}
KINDS = {
    "car_a": AgentType.VEHICLE,
    "car_b": AgentType.VEHICLE,
    "car_c": AgentType.VEHICLE,
    "uav": AgentType.UAV,
}


def _scene() -> Scene:
    agents: list[Agent] = []
    sensors: list[Sensor] = []
    clouds: list[PointCloud] = []
    for index, (agent_id, (x, y, z)) in enumerate(STARTS.items()):
        poses = tuple(
            StampedPose(f * STEP_US, SE3.identity().translation(x + f, y, z)) for f in range(FRAMES)
        )
        agents.append(Agent(agent_id=agent_id, agent_type=KINDS[agent_id], poses=poses))
        sensors.append(
            Sensor("lidar", agent_id, Modality.LIDAR, SE3.identity().translation(0.0, 0.0, 1.0))
        )
        for f in range(FRAMES):
            # index + 1 points per sweep, sensor frame (k, -k, 0.5), intensity 1
            n = index + 1
            payload = b"".join(struct.pack("<4f", float(k), float(-k), 0.5, 1.0) for k in range(n))
            clouds.append(PointCloud(agent_id, "lidar", f * STEP_US, n, payload))
    return Scene(
        schema_version=SCHEMA_VERSION,
        scene_id="hero-four-agents",
        world_frame="ENU",
        clock=Clock(unit="us", scale_note="synthetic hero test"),
        agents=tuple(agents),
        sensors=tuple(sensors),
        annotations=(),
        objects=(),
        point_clouds=tuple(clouds),
    )


def _map(path: Path) -> None:
    """Two polylines near the agents and one far away, shifted +5 m in x into the world."""
    lines = [
        (LAYER_LANE_CENTRE, ((190.0, 45.0, 0.0), (210.0, 45.0, 0.0))),
        (LAYER_ROAD_EDGE, ((190.0, 55.0, 0.1), (210.0, 55.0, 0.1), (210.0, 65.0, 0.1))),
        (LAYER_LANE_CENTRE, ((900.0, 900.0, 0.0), (910.0, 900.0, 0.0))),
    ]
    parsed = OpenDriveMap(
        name="synthetic",
        rev_major="1",
        rev_minor="6",
        geo_reference="",
        north=None,
        south=None,
        east=None,
        west=None,
        sample_step_m=1.0,
        polylines=tuple(
            MapPolyline(layer, "1", -1, "right", "driving", None, xyz) for layer, xyz in lines
        ),
    )
    write_map(parsed, path, map_name="synthetic", source={"format": "test"})
    identity = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
    write_alignment(path, FrameMap(identity, (5.0, 0.0, 0.0), True), {"measured": True})


def test_four_agents_round_trip(tmp_path: Path) -> None:
    scene_dir = tmp_path / "scene"
    write_scene(_scene(), scene_dir)
    _map(tmp_path / "map")
    out = tmp_path / "out"

    stats = exporter.export_hero(scene_dir, out, map_dir=tmp_path / "map", num_frames=FRAMES)

    clip = exporter.decode_clip((out / "clip.bin").read_bytes())
    # Default origin: centroid of the four start positions, rounded, z = 0.
    assert clip.origin == (201.0, 50.0, 0.0)
    assert clip.agents == [
        {"id": "car_a", "kind": "vehicle", "color": "#F58A1F"},
        {"id": "car_b", "kind": "vehicle", "color": "#4CC9F0"},
        {"id": "car_c", "kind": "vehicle", "color": "#8E6CEF"},
        {"id": "uav", "kind": "drone", "color": "#3C8DFF"},
    ]
    polylines = clip.polylines
    assert [layer for layer, _ in polylines] == [1, 2]  # the far polyline is dropped
    # world_from_opendrive (+5 m in x) is applied before storing.
    assert polylines[1][1][2] == pytest.approx((215.0, 65.0, 0.1), abs=0.005)

    frames = clip.frames
    assert [frame.time_us for frame in frames] == [f * STEP_US for f in range(FRAMES)]
    for f, frame in enumerate(frames):
        for index, (agent_id, (x, y, z)) in enumerate(STARTS.items()):
            entry = frame.agents[index]
            assert entry.present is True, agent_id
            assert entry.pose[:3] == pytest.approx((x + f, y, z))
            assert entry.pose[3:] == pytest.approx((0.0, 0.0, 0.0, 1.0))
            # World point = pose + (k, -k, 0.5 + 1.0 mount), stored to the centimetre.
            expected = [(x + f + k, y - k, z + 1.5) for k in range(index + 1)]
            assert entry.points == pytest.approx(expected, abs=0.005)

    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["format"] == "CPCVCLP2"
    assert manifest["agents"] == clip.agents
    assert manifest["num_frames"] == FRAMES
    assert stats.total_points == FRAMES * (1 + 2 + 3 + 4)
    assert stats.clamped == 0
    text = json.dumps(manifest)
    assert "hero-four-agents" not in text  # no scene name is served with the page


def test_arguments_override_origin_window_and_colour(tmp_path: Path) -> None:
    scene_dir = tmp_path / "scene"
    write_scene(_scene(), scene_dir)
    out = tmp_path / "out"
    code = exporter.main(
        [
            "--scene",
            str(scene_dir),
            "--out",
            str(out),
            "--agents",
            "uav",
            "car_b",
            "--color",
            "car_b=#123456",
            "--origin",
            "100",
            "0",
            "0",
            "--start-us",
            str(2 * STEP_US),
            "--frames",
            "10",
        ]
    )
    assert code == 0
    clip = exporter.decode_clip((out / "clip.bin").read_bytes())
    assert clip.origin == (100.0, 0.0, 0.0)
    assert clip.agents == [
        {"id": "uav", "kind": "drone", "color": "#3C8DFF"},
        {"id": "car_b", "kind": "vehicle", "color": "#123456"},
    ]
    frames = clip.frames
    # The window stops at the last recorded pose.
    assert [frame.time_us for frame in frames] == [2 * STEP_US, 3 * STEP_US, 4 * STEP_US]
    assert frames[0].agents[1].pose[:3] == pytest.approx((212.0, 40.0, 0.0))
    assert clip.polylines == []


def test_one_agent_is_refused(tmp_path: Path) -> None:
    scene_dir = tmp_path / "scene"
    write_scene(_scene(), scene_dir)
    with pytest.raises(ValueError, match="at least two agents"):
        exporter.export_hero(scene_dir, tmp_path / "out", agent_ids=["car_a"])


def test_points_are_capped_per_agent(tmp_path: Path) -> None:
    scene_dir = tmp_path / "scene"
    write_scene(_scene(), scene_dir)
    stats = exporter.export_hero(scene_dir, tmp_path / "out", points_per_frame=8)
    # 8 points split over 4 agents with lidar is 2 each: 1 + 2 + 2 + 2 per frame.
    assert stats.total_points == FRAMES * 7
