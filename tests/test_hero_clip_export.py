"""The N-agent hero clip is a version-2 CPCVCLIP file built from a capture."""

from __future__ import annotations

import json
import struct
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
from scripts.site.export_hero_clip import carla_rotation_matrix, export_capture


def test_carla_rotation_matches_the_published_basis() -> None:
    forward = np.array([1.0, 0.0, 0.0])
    yaw = carla_rotation_matrix(0.0, 90.0, 0.0)
    pitched = carla_rotation_matrix(-90.0, 0.0, 0.0)
    assert np.allclose(yaw @ forward, [0.0, 1.0, 0.0], atol=1e-9)
    assert np.allclose(pitched @ forward, [0.0, 0.0, -1.0], atol=1e-9)


def test_capture_exports_one_cloud_per_agent(tmp_path: Path) -> None:
    capture = tmp_path / "capture"
    _write_capture(capture)
    map_dir = tmp_path / "map"
    _write_map(map_dir)
    out_bin = tmp_path / "clip.bin"
    out_manifest = tmp_path / "manifest.json"
    result = export_capture(
        capture, map_dir, out_bin, out_manifest, expected_frames=2, points_per_sweep=10
    )
    assert result["num_agents"] == 2
    assert result["num_frames"] == 2
    blob = out_bin.read_bytes()
    assert blob[:8] == b"CPCVCLIP"
    version, _ox, _oy, _oz, polylines, frames, agents = struct.unpack_from("<HfffIIH", blob, 8)
    assert version == 2
    assert frames == 2
    assert agents == 2
    assert polylines == 1
    manifest = json.loads(out_manifest.read_text(encoding="utf-8"))
    assert [row["id"] for row in manifest["agents"]] == ["car-0", "drone"]
    assert manifest["fps"] == 10
    assert manifest["dataset_name"] == ""


def _write_capture(capture: Path) -> None:
    agents = capture / "agents"
    for agent_id, location in (("car-0", (10.0, 20.0, 2.4)), ("drone", (10.0, 20.0, 40.0))):
        folder = agents / agent_id / "lidar"
        folder.mkdir(parents=True)
        poses = agents / agent_id / "poses.jsonl"
        for frame_id in range(2):
            point = np.array([1.0, 0.0, 0.0, 1.0], dtype="<f4")
            (folder / f"{frame_id:06d}.bin").write_bytes(point.tobytes())
            row = {
                "frame_id": frame_id,
                "time_us": frame_id * 100000,
                "carla_frame": frame_id,
                "location": {"x": location[0], "y": location[1], "z": location[2]},
                "rotation": {"pitch": 0.0, "yaw": 0.0, "roll": 0.0},
                "point_count": 1,
            }
            with poses.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row) + "\n")
    boxes = capture / "boxes.jsonl"
    for frame_id in range(2):
        row = {
            "frame_id": frame_id,
            "boxes": [
                {
                    "actor_id": 7,
                    "type_id": "vehicle.audi.a2",
                    "center": {"x": 10.0, "y": 20.0, "z": 1.0},
                    "half_extent": {"x": 2.0, "y": 1.0, "z": 0.8},
                    "rotation": {"pitch": 0.0, "yaw": 0.0, "roll": 0.0},
                }
            ],
        }
        with boxes.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row) + "\n")
    manifest = {
        "seed": 20261026,
        "map": "Town03",
        "tick_rate_hz": 10,
        "frames": 2,
        "agents": [
            {"agent_id": "car-0"},
            {"agent_id": "drone"},
        ],
    }
    (capture / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def _write_map(map_dir: Path) -> None:
    map_dir.mkdir()
    verts = array_bytes([(10.0, 20.0, 0.0), (12.0, 20.0, 0.0)])
    (map_dir / "vertices.bin").write_bytes(verts)
    table = pa.table(
        {
            "polyline_id": pa.array([1], type=pa.int64()),
            "layer": pa.array(["lane_centre"]),
            "road_id": pa.array(["1"]),
            "lane_id": pa.array([0], type=pa.int64()),
            "side": pa.array(["center"]),
            "lane_type": pa.array(["driving"]),
            "junction_id": pa.array(["-1"]),
            "vertex_offset": pa.array([0], type=pa.int64()),
            "vertex_count": pa.array([2], type=pa.int64()),
        }
    )
    pq.write_table(table, map_dir / "polylines.parquet")


def array_bytes(points: list[tuple[float, float, float]]) -> bytes:
    raw = np.array(points, dtype="<f4")
    return raw.tobytes()
