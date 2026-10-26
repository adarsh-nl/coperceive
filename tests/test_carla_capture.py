# SPDX-License-Identifier: Apache-2.0
"""Writer and manifest checks. No CARLA process and no carla package."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from scripts.sim.capture_writer import (
    COLLABORATORS,
    LICENCE_LINE,
    Box,
    CaptureError,
    Location,
    Pose,
    Rotation,
    build_manifest,
    choose_spawn_indices,
    require_complete,
    time_us_for,
    write_agent_tick,
    write_boxes,
    write_manifest,
)


def _pose(frame_id: int) -> Pose:
    return Pose(
        location=Location(x=float(frame_id), y=1.0, z=2.0),
        rotation=Rotation(pitch=0.0, yaw=90.0, roll=0.0),
    )


def _box() -> Box:
    return Box(
        actor_id=7,
        type_id="vehicle.audi.a2",
        center=Location(x=0.0, y=0.0, z=0.0),
        half_extent=Location(x=2.0, y=1.0, z=0.8),
        rotation=Rotation(pitch=0.0, yaw=0.0, roll=0.0),
    )


def _write_capture(out: Path, n_frames: int) -> None:
    agents: list[dict[str, object]] = []
    sensors: list[dict[str, object]] = []
    for agent_id in COLLABORATORS:
        agents.append(
            {"agent_id": agent_id, "kind": "vehicle" if agent_id != "drone" else "sensor"}
        )
        attributes: dict[str, object] = {"channels": "32"}
        sensors.append({"agent_id": agent_id, "sensor_id": "lidar_top", "attributes": attributes})
    for frame_id in range(n_frames):
        for agent_id in COLLABORATORS:
            write_agent_tick(
                out,
                agent_id,
                frame_id,
                time_us_for(frame_id, 0.1),
                _pose(frame_id),
                b"\x00" * 16,
                carla_frame=1000 + frame_id,
            )
        write_boxes(out, frame_id, [_box()])
    write_manifest(
        out,
        build_manifest(
            seed=20261026,
            map_name="Carla/Maps/Town03",
            step_s=0.1,
            frames=n_frames,
            carla_version="0.9.15",
            agents=agents,
            sensors=sensors,
        ),
    )


def test_spawn_choice_follows_the_seed() -> None:
    assert choose_spawn_indices(20, 3, 20261026) == choose_spawn_indices(20, 3, 20261026)
    assert choose_spawn_indices(20, 3, 20261026) != choose_spawn_indices(20, 3, 1)


def test_manifest_records_the_licence_and_a_complete_capture(tmp_path: Path) -> None:
    _write_capture(tmp_path, 100)
    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["licence"] == LICENCE_LINE
    assert manifest["seed"] == 20261026
    assert manifest["map"] == "Carla/Maps/Town03"
    assert manifest["fixed_delta_seconds"] == 0.1
    assert manifest["carla_version"] == "0.9.15"
    assert [row["agent_id"] for row in manifest["agents"]] == list(COLLABORATORS)
    assert manifest["sensors"][0]["attributes"]["channels"] == "32"
    require_complete(tmp_path, COLLABORATORS, 100)
    pose = json.loads(
        (tmp_path / "agents" / "drone" / "poses.jsonl").read_text(encoding="utf-8").splitlines()[0]
    )
    assert pose["frame_id"] == 0
    assert pose["time_us"] == 0
    assert (tmp_path / "agents" / "car-0" / "lidar" / "000099.bin").stat().st_size == 16


def test_a_missing_frame_fails(tmp_path: Path) -> None:
    _write_capture(tmp_path, 100)
    (tmp_path / "agents" / "car-1" / "lidar" / "000050.bin").unlink()
    with pytest.raises(CaptureError, match="car-1 is missing lidar frame 50"):
        require_complete(tmp_path, COLLABORATORS, 100)


def test_a_gap_in_pose_ids_fails(tmp_path: Path) -> None:
    _write_capture(tmp_path, 4)
    pose_path = tmp_path / "agents" / "drone" / "poses.jsonl"
    lines = pose_path.read_text(encoding="utf-8").splitlines()
    pose_path.write_text("\n".join(lines[:2] + lines[3:]) + "\n", encoding="utf-8")
    with pytest.raises(CaptureError, match="drone pose frames"):
        require_complete(tmp_path, COLLABORATORS, 4)
