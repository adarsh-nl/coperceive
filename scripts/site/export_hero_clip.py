#!/usr/bin/env python3
"""Export a compact binary clip of Town03-000 for the coperceive landing page hero animation.

Reads:
- Scene: Town03-000 (Griffin 25 m subset)
- Map: Town03 OpenDRIVE geometry, not committed

Writes:
- site/public/hero/clip.bin: Compact binary representation of 10s clip (100 frames @ 10 Hz)
- site/public/hero/manifest.json: Metadata, attributions, agent specs, and occlusion timestamps
"""

from __future__ import annotations

import argparse
import array
import gzip
import json
import struct
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple, TypedDict, cast

import pyarrow as pa
import pyarrow.parquet as pq
from coperceive.store import read_scene
from coperceive.viewdata import frame_document, frame_points

ORIGIN_X = -105.0
ORIGIN_Y = -25.0
ORIGIN_Z = 0.0

MAP_X_MIN = -160.0
MAP_X_MAX = -50.0
MAP_Y_MIN = -80.0
MAP_Y_MAX = 20.0

START_TIME_US = 1118500000
NUM_FRAMES = 100
TIME_STEP_US = 100000  # 10 Hz

CAT_MAP: dict[str, int] = {
    "car": 0,
    "vehicle": 0,
    "truck": 0,
    "van": 0,
    "bus": 0,
    "pedestrian": 1,
    "cyclist": 2,
    "bicycle": 2,
    "motorcycle": 2,
}


class PoseDict(TypedDict):
    qw: float
    qx: float
    qy: float
    qz: float
    tx: float
    ty: float
    tz: float


@dataclass
class TrackAccumulator:
    track_id: str
    category: str
    center: tuple[float, float, float]
    size_lwh: tuple[float, float, float]
    pose: PoseDict
    agents: set[str | None]


class BoxRecord(NamedTuple):
    num_tid: int
    flag: int
    cat_id: int
    cx: float
    cy: float
    cz: float
    sx: float
    sy: float
    sz: float
    qx: float
    qy: float
    qz: float
    qw: float


def _as_float(value: object, name: str) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    raise TypeError(f"Expected numeric {name}, got {type(value).__name__}")


def _as_int(value: object, name: str) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    raise TypeError(f"Expected int {name}, got {type(value).__name__}")


def _as_str(value: object, name: str) -> str:
    if isinstance(value, str):
        return value
    raise TypeError(f"Expected str {name}, got {type(value).__name__}")


def _as_object_dict(value: object, name: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise TypeError(f"Expected dict for {name}, got {type(value).__name__}")
    out: dict[str, object] = {}
    for key, item in cast(dict[object, object], value).items():
        if not isinstance(key, str):
            raise TypeError(f"Expected str key in {name}, got {type(key).__name__}")
        out[key] = item
    return out


def _as_object_list(value: object, name: str) -> list[object]:
    if isinstance(value, list):
        return list(cast(list[object], value))
    raise TypeError(f"Expected list for {name}, got {type(value).__name__}")


def _as_vec3(value: object, name: str) -> tuple[float, float, float]:
    items = _as_object_list(value, name)
    if len(items) != 3:
        raise ValueError(f"Expected 3 elements for {name}, got {len(items)}")
    return (
        _as_float(items[0], f"{name}[0]"),
        _as_float(items[1], f"{name}[1]"),
        _as_float(items[2], f"{name}[2]"),
    )


def _as_pose(value: object, name: str) -> PoseDict:
    raw = _as_object_dict(value, name)
    return PoseDict(
        qw=_as_float(raw["qw"], f"{name}.qw"),
        qx=_as_float(raw["qx"], f"{name}.qx"),
        qy=_as_float(raw["qy"], f"{name}.qy"),
        qz=_as_float(raw["qz"], f"{name}.qz"),
        tx=_as_float(raw["tx"], f"{name}.tx"),
        ty=_as_float(raw["ty"], f"{name}.ty"),
        tz=_as_float(raw["tz"], f"{name}.tz"),
    )


def _table_rows(table: pa.Table) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for item in table.to_pylist():
        row: dict[str, object] = {}
        for key, value in item.items():
            row[key] = value
        rows.append(row)
    return rows


def export_hero(
    scene_dir: Path,
    map_dir: Path,
    out_bin: Path,
    out_manifest: Path,
) -> None:
    scene = read_scene(scene_dir)
    print(f"Loaded scene {scene.scene_id} from {scene_dir}")

    # Read map polylines
    map_table = pq.read_table(map_dir / "polylines.parquet")
    map_rows = _table_rows(map_table)
    blob = (map_dir / "vertices.bin").read_bytes()
    values = array.array("f")
    values.frombytes(blob)
    if sys.byteorder != "little":
        values.byteswap()

    selected_polylines: list[tuple[int, list[tuple[float, float, float]]]] = []
    for row in map_rows:
        layer_str = _as_str(row["layer"], "layer")
        layer_id = 1 if "lane_centre" in layer_str else 2
        offset = _as_int(row["vertex_offset"], "vertex_offset")
        count = _as_int(row["vertex_count"], "vertex_count")
        verts = [
            (
                float(values[(offset + j) * 3]),
                float(values[(offset + j) * 3 + 1]),
                float(values[(offset + j) * 3 + 2]),
            )
            for j in range(count)
        ]
        if any(MAP_X_MIN <= p[0] <= MAP_X_MAX and MAP_Y_MIN <= p[1] <= MAP_Y_MAX for p in verts):
            selected_polylines.append((layer_id, verts))

    print(f"Extracted {len(selected_polylines)} map polylines")

    # Target timestamps
    times = [START_TIME_US + i * TIME_STEP_US for i in range(NUM_FRAMES)]

    buf = bytearray()
    # Magic
    buf.extend(b"CPCVCLIP")
    # Header: version(u16), origin(3*f32), num_polylines(u32), num_frames(u32)
    buf.extend(
        struct.pack(
            "<HfffII",
            1,
            ORIGIN_X,
            ORIGIN_Y,
            ORIGIN_Z,
            len(selected_polylines),
            len(times),
        )
    )

    # Map polylines
    for layer_id, verts in selected_polylines:
        buf.extend(struct.pack("<BH", layer_id, len(verts)))
        for x, y, z in verts:
            dx_cm = int(round((x - ORIGIN_X) * 100))
            dy_cm = int(round((y - ORIGIN_Y) * 100))
            dz_cm = int(round((z - ORIGIN_Z) * 100))
            # clamp to int16
            dx_cm = max(-32768, min(32767, dx_cm))
            dy_cm = max(-32768, min(32767, dy_cm))
            dz_cm = max(-32768, min(32767, dz_cm))
            buf.extend(struct.pack("<hhh", dx_cm, dy_cm, dz_cm))

    total_lidar_points = 0
    total_boxes = 0

    default_pose: PoseDict = {
        "tx": 0.0,
        "ty": 0.0,
        "tz": 0.0,
        "qw": 1.0,
        "qx": 0.0,
        "qy": 0.0,
        "qz": 0.0,
    }
    # Frames
    for _frame_idx, t in enumerate(times):
        doc = frame_document(scene, t)
        doc_agents = [_as_object_dict(a, "agent") for a in _as_object_list(doc["agents"], "agents")]
        v_agents = [a for a in doc_agents if a["agent_id"] == "vehicle"]
        d_agents = [a for a in doc_agents if a["agent_id"] == "drone"]

        v_pose = _as_pose(v_agents[0]["pose"], "vehicle.pose") if v_agents else default_pose
        d_pose = _as_pose(d_agents[0]["pose"], "drone.pose") if d_agents else default_pose

        # LiDAR points for vehicle
        pts_bytes = frame_points(scene, t, "vehicle", "lidar_top")
        n_pts = len(pts_bytes) // 12
        pts_floats = array.array("f")
        pts_floats.frombytes(pts_bytes)
        if sys.byteorder != "little":
            pts_floats.byteswap()

        pts_list: list[tuple[float, float, float]] = []
        for p_idx in range(n_pts):
            pts_list.append(
                (
                    pts_floats[p_idx * 3],
                    pts_floats[p_idx * 3 + 1],
                    pts_floats[p_idx * 3 + 2],
                )
            )

        # Subsample to keep bundle lean and fast: target 1500 points per sweep
        if len(pts_list) > 1500:
            step = len(pts_list) / 1500.0
            pts_list = [pts_list[int(i * step)] for i in range(1500)]

        total_lidar_points += len(pts_list)

        # Ground truth boxes
        # Deduplicate tracks, compute cooperative agent flag
        # flag: 1 = vehicle only, 2 = drone only, 3 = both / cooperative
        track_map: dict[str, TrackAccumulator] = {}
        doc_boxes = [_as_object_dict(b, "box") for b in _as_object_list(doc["boxes"], "boxes")]
        for box in doc_boxes:
            tid = str(box.get("track_id", ""))
            if not tid:
                continue
            raw_aid = box.get("agent_id")
            aid = raw_aid if isinstance(raw_aid, str) else None
            if tid not in track_map:
                raw_cat = box.get("category", "car")
                category = raw_cat if isinstance(raw_cat, str) else str(raw_cat)
                track_map[tid] = TrackAccumulator(
                    track_id=tid,
                    category=category,
                    center=_as_vec3(box["center"], "box.center"),
                    size_lwh=_as_vec3(box["size_lwh"], "box.size_lwh"),
                    pose=_as_pose(box["pose"], "box.pose"),
                    agents={aid},
                )
            else:
                track_map[tid].agents.add(aid)

        box_records: list[BoxRecord] = []
        for tid, tdata in track_map.items():
            agents = tdata.agents
            if "vehicle" in agents and "drone" in agents:
                flag = 3
            elif "drone" in agents:
                flag = 2
            else:
                flag = 1

            cat_id = CAT_MAP.get(tdata.category.lower(), 3)
            try:
                num_tid = int(tid)
            except ValueError:
                num_tid = 0

            center = tdata.center
            size = tdata.size_lwh
            pose = tdata.pose
            box_records.append(
                BoxRecord(
                    num_tid,
                    flag,
                    cat_id,
                    center[0],
                    center[1],
                    center[2],
                    size[0],
                    size[1],
                    size[2],
                    pose["qx"],
                    pose["qy"],
                    pose["qz"],
                    pose["qw"],
                )
            )

        total_boxes += len(box_records)

        # Write frame header
        buf.extend(struct.pack("<Q", t))
        # Vehicle pose (tx, ty, tz, qx, qy, qz, qw)
        buf.extend(
            struct.pack(
                "<fffffff",
                v_pose["tx"],
                v_pose["ty"],
                v_pose["tz"],
                v_pose["qx"],
                v_pose["qy"],
                v_pose["qz"],
                v_pose["qw"],
            )
        )
        # Drone pose (tx, ty, tz, qx, qy, qz, qw)
        buf.extend(
            struct.pack(
                "<fffffff",
                d_pose["tx"],
                d_pose["ty"],
                d_pose["tz"],
                d_pose["qx"],
                d_pose["qy"],
                d_pose["qz"],
                d_pose["qw"],
            )
        )
        # LiDAR points
        buf.extend(struct.pack("<H", len(pts_list)))
        for px, py, pz in pts_list:
            dx_cm = int(round((px - ORIGIN_X) * 100))
            dy_cm = int(round((py - ORIGIN_Y) * 100))
            dz_cm = int(round((pz - ORIGIN_Z) * 100))
            dx_cm = max(-32768, min(32767, dx_cm))
            dy_cm = max(-32768, min(32767, dy_cm))
            dz_cm = max(-32768, min(32767, dz_cm))
            buf.extend(struct.pack("<hhh", dx_cm, dy_cm, dz_cm))

        # Boxes
        buf.extend(struct.pack("<H", len(box_records)))
        for rec in box_records:
            buf.extend(
                struct.pack(
                    "<iBBffffffffff",
                    rec.num_tid,
                    rec.flag,
                    rec.cat_id,
                    rec.cx,
                    rec.cy,
                    rec.cz,
                    rec.sx,
                    rec.sy,
                    rec.sz,
                    rec.qx,
                    rec.qy,
                    rec.qz,
                    rec.qw,
                )
            )

    out_bin.parent.mkdir(parents=True, exist_ok=True)
    out_bin.write_bytes(buf)
    uncompressed_size = len(buf)
    compressed_bytes = gzip.compress(buf, compresslevel=9)
    compressed_size = len(compressed_bytes)

    print(
        f"Exported clip.bin: {uncompressed_size:,} bytes uncompressed, "
        f"{compressed_size:,} bytes gzipped"
    )
    print(
        f"Total LiDAR points across {len(times)} frames: {total_lidar_points:,} "
        f"(avg {total_lidar_points / len(times):.1f}/frame)"
    )
    print(
        f"Total boxes across {len(times)} frames: {total_boxes:,} "
        f"(avg {total_boxes / len(times):.1f}/frame)"
    )

    manifest = {
        "scene_id": "Town03-000",
        "dataset_name": (
            "Griffin: Aerial-Ground Cooperative Detection and Tracking Dataset and Benchmark"
        ),
        "dataset_paper": "Wang et al., arXiv 2503.06983",
        "platform": (
            "Griffin 25 m subset. Map name Carla/Maps/Town03. "
            "Recordings are not in this repository."
        ),
        "subset": "25m",
        "origin": [ORIGIN_X, ORIGIN_Y, ORIGIN_Z],
        "scale_cm": 100.0,
        "time_start_us": START_TIME_US,
        "time_step_us": TIME_STEP_US,
        "num_frames": NUM_FRAMES,
        "duration_s": round(NUM_FRAMES * TIME_STEP_US / 1e6, 2),
        "fps": 10,
        "agents": {
            "vehicle": {
                "id": "vehicle",
                "label": "Ground Vehicle (CAV)",
                "type": "vehicle",
                "color": "#e67828",
                "sensors": {
                    "lidar": "lidar_top",
                    "cameras": "front, back, left, right",
                },
            },
            "drone": {
                "id": "drone",
                "label": "Aerial Drone (UAV)",
                "type": "uav",
                "color": "#2878dc",
                "sensors": {
                    "cameras": "front, back, left, right, bottom",
                },
            },
        },
        "occlusion_event": {
            "time_us": 1123700000,
            "frame_index": 52,
            "track_id": "265",
            "category": "pedestrian",
            "description": (
                "Timestamp stored by this exporter. "
                "It does not state a visibility or a distance for this track."
            ),
        },
        "stats": {
            "num_polylines": len(selected_polylines),
            "num_frames": len(times),
            "total_lidar_points": total_lidar_points,
            "avg_lidar_points_per_frame": round(total_lidar_points / len(times), 1),
            "file_size_bytes": uncompressed_size,
            "gzip_size_bytes": compressed_size,
        },
        "attributions": {
            "dataset": "Griffin toolkit and dataset card: MIT. Recordings are not committed.",
            "map": "CARLA map assets, CC-BY. OpenDRIVE bytes are not committed.",
            "software": "coperceive is Apache-2.0.",
        },
    }

    out_manifest.parent.mkdir(parents=True, exist_ok=True)
    out_manifest.write_text(json.dumps(manifest, indent=2))
    print(f"Exported manifest.json: {out_manifest.stat().st_size:,} bytes")


def main(argv: list[str] | None = None) -> None:
    """Parse CLI paths and export the hero clip and manifest."""
    home = Path.home()
    parser = argparse.ArgumentParser(description="Export hero clip binary and manifest.")
    parser.add_argument(
        "--scene-dir",
        type=Path,
        default=home / "results/reference-stepb-20261020/staged-scenes/Town03-000",
    )
    parser.add_argument(
        "--map-dir",
        type=Path,
        default=home / "results/628385/maps/Town03",
    )
    parser.add_argument(
        "--out-bin",
        type=Path,
        default=home / "wt/landing/site/public/hero/clip.bin",
    )
    parser.add_argument(
        "--out-manifest",
        type=Path,
        default=home / "wt/landing/site/public/hero/manifest.json",
    )
    ns = parser.parse_args(argv)
    scene_dir = cast(Path, ns.scene_dir)
    map_dir = cast(Path, ns.map_dir)
    out_bin = cast(Path, ns.out_bin)
    out_manifest = cast(Path, ns.out_manifest)
    export_hero(
        scene_dir=scene_dir,
        map_dir=map_dir,
        out_bin=out_bin,
        out_manifest=out_manifest,
    )


if __name__ == "__main__":
    main()
