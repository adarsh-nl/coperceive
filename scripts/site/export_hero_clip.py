#!/usr/bin/env python3
"""Export a compact binary clip of Town03-000 for the coperceive landing page hero animation.

Reads:
- Scene: Town03-000 (Griffin dataset, 25m preset, CARLA-AirSim simulation)
- Map: Town03 (OpenDRIVE, CARLA CC-BY assets)

Writes:
- site/public/hero/clip.bin: Compact binary representation of 10s clip (100 frames @ 10 Hz)
- site/public/hero/manifest.json: Metadata, attributions, agent specs, and occlusion timestamps
"""

from __future__ import annotations

import array
import gzip
import json
import struct
import sys
from pathlib import Path

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

CAT_MAP = {
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
    map_rows = map_table.to_pydict()
    blob = (map_dir / "vertices.bin").read_bytes()
    values = array.array("f")
    values.frombytes(blob)
    if sys.byteorder != "little":
        values.byteswap()

    selected_polylines: list[tuple[int, list[tuple[float, float, float]]]] = []
    for i in range(len(map_rows["polyline_id"])):
        layer_str = map_rows["layer"][i]
        layer_id = 1 if "lane_centre" in layer_str else 2
        offset = map_rows["vertex_offset"][i]
        count = map_rows["vertex_count"][i]
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

    default_pose = {"tx": 0, "ty": 0, "tz": 0, "qw": 1, "qx": 0, "qy": 0, "qz": 0}
    # Frames
    for _frame_idx, t in enumerate(times):
        doc = frame_document(scene, t)
        v_agents = [a for a in doc["agents"] if a["agent_id"] == "vehicle"]
        d_agents = [a for a in doc["agents"] if a["agent_id"] == "drone"]

        v_pose = v_agents[0]["pose"] if v_agents else default_pose
        d_pose = d_agents[0]["pose"] if d_agents else default_pose

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
        track_map: dict[str, dict[str, object]] = {}
        for box in doc["boxes"]:
            tid = str(box.get("track_id", ""))
            if not tid:
                continue
            aid = box.get("agent_id")
            if tid not in track_map:
                track_map[tid] = {
                    "track_id": tid,
                    "category": box.get("category", "car"),
                    "center": box["center"],
                    "size": box["size_lwh"],
                    "pose": box["pose"],
                    "agents": {aid},
                }
            else:
                track_map[tid]["agents"].add(aid)

        box_records = []
        for tid, tdata in track_map.items():
            agents = tdata["agents"]
            if "vehicle" in agents and "drone" in agents:
                flag = 3
            elif "drone" in agents:
                flag = 2
            else:
                flag = 1

            cat_id = CAT_MAP.get(str(tdata["category"]).lower(), 3)
            try:
                num_tid = int(tid)
            except ValueError:
                num_tid = 0

            center = tdata["center"]
            size = tdata["size"]
            pose = tdata["pose"]
            box_records.append(
                (
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
                    rec[0],  # num_tid
                    rec[1],  # flag
                    rec[2],  # cat_id
                    rec[3],
                    rec[4],
                    rec[5],  # center
                    rec[6],
                    rec[7],
                    rec[8],  # size
                    rec[9],
                    rec[10],
                    rec[11],
                    rec[12],  # quat (qx, qy, qz, qw)
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
        "platform": "CARLA–AirSim simulation",
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
                    "lidar": "Vehicle top LiDAR (1,500 pts/sweep)",
                    "cameras": "4 surrounding cameras (front, back, left, right)",
                },
            },
            "drone": {
                "id": "drone",
                "label": "Aerial Drone (UAV)",
                "type": "uav",
                "color": "#2878dc",
                "altitude_m": 27.0,
                "sensors": {
                    "cameras": (
                        "5 downward & surrounding cameras (front, back, left, right, bottom)"
                    ),
                },
            },
        },
        "occlusion_event": {
            "time_us": 1123700000,
            "frame_index": 52,
            "track_id": "265",
            "category": "pedestrian",
            "distance_m": 17.5,
            "description": (
                "0 vehicle LiDAR returns on the pedestrian at this time; "
                "the UAV camera sees it (visibility 1.0) at 17.5m from vehicle."
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
            "dataset": (
                "Griffin dataset is used under its academic research terms (Wang et al., 2025)."
            ),
            "map": (
                "CARLA Town03 OpenDRIVE HD Map assets are distributed under the "
                "Creative Commons Attribution (CC-BY) License (CARLA Simulator, carla.org)."
            ),
            "software": "coperceive is open source under the MIT License.",
        },
    }

    out_manifest.parent.mkdir(parents=True, exist_ok=True)
    out_manifest.write_text(json.dumps(manifest, indent=2))
    print(f"Exported manifest.json: {out_manifest.stat().st_size:,} bytes")


if __name__ == "__main__":
    export_hero(
        scene_dir=Path(
            "/home/nanjaiyalathaa/results/reference-stepb-20261020/staged-scenes/Town03-000"
        ),
        map_dir=Path("/home/nanjaiyalathaa/results/628385/maps/Town03"),
        out_bin=Path("/home/nanjaiyalathaa/wt/landing/site/public/hero/clip.bin"),
        out_manifest=Path("/home/nanjaiyalathaa/wt/landing/site/public/hero/manifest.json"),
    )
