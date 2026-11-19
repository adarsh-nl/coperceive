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

import array
import gzip
import json
import math
import struct
import sys
from pathlib import Path
from typing import cast

import numpy as np
import pyarrow.parquet as pq
from coperceive.store import read_scene
from coperceive.viewdata import frame_document, frame_points

# One colour per collaborator. These are the landing tokens, not new names.
AGENT_COLORS = {
    "car-0": "#F58A1F",
    "car-1": "#4CC9F0",
    "car-2": "#8E6CEF",
    "drone": "#3C8DFF",
}
POINTS_PER_SWEEP = 1500

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


def carla_rotation_matrix(
    pitch_deg: float, yaw_deg: float, roll_deg: float
) -> np.ndarray[tuple[int, int], np.dtype[np.float64]]:
    """Rotation used by CARLA 0.9.15 ``Rotation::RotateVector``.

    The formula is the one in LibCarla ``geom/Rotation.h``: roll, then pitch, then yaw.
    """
    yaw = math.radians(yaw_deg)
    pitch = math.radians(pitch_deg)
    roll = math.radians(roll_deg)
    cy, sy = math.cos(yaw), math.sin(yaw)
    cp, sp = math.cos(pitch), math.sin(pitch)
    cr, sr = math.cos(roll), math.sin(roll)
    matrix = np.array(
        [
            [cp * cy, cy * sp * sr - sy * cr, -cy * sp * cr - sy * sr],
            [cp * sy, sy * sp * sr + cy * cr, -sy * sp * cr + cy * sr],
            [sp, -cp * sr, cp * cr],
        ],
        dtype=np.float64,
    )
    return cast(np.ndarray[tuple[int, int], np.dtype[np.float64]], matrix)


def _quaternion(rotation: np.ndarray) -> tuple[float, float, float, float]:
    """qx, qy, qz, qw from a proper rotation matrix."""
    trace = float(rotation[0, 0] + rotation[1, 1] + rotation[2, 2])
    if trace > 0.0:
        scale = math.sqrt(trace + 1.0) * 2.0
        qw = 0.25 * scale
        qx = (rotation[2, 1] - rotation[1, 2]) / scale
        qy = (rotation[0, 2] - rotation[2, 0]) / scale
        qz = (rotation[1, 0] - rotation[0, 1]) / scale
    elif rotation[0, 0] > rotation[1, 1] and rotation[0, 0] > rotation[2, 2]:
        scale = math.sqrt(1.0 + rotation[0, 0] - rotation[1, 1] - rotation[2, 2]) * 2.0
        qw = (rotation[2, 1] - rotation[1, 2]) / scale
        qx = 0.25 * scale
        qy = (rotation[0, 1] + rotation[1, 0]) / scale
        qz = (rotation[0, 2] + rotation[2, 0]) / scale
    elif rotation[1, 1] > rotation[2, 2]:
        scale = math.sqrt(1.0 + rotation[1, 1] - rotation[0, 0] - rotation[2, 2]) * 2.0
        qw = (rotation[0, 2] - rotation[2, 0]) / scale
        qx = (rotation[0, 1] + rotation[1, 0]) / scale
        qy = 0.25 * scale
        qz = (rotation[1, 2] + rotation[2, 1]) / scale
    else:
        scale = math.sqrt(1.0 + rotation[2, 2] - rotation[0, 0] - rotation[1, 1]) * 2.0
        qw = (rotation[1, 0] - rotation[0, 1]) / scale
        qx = (rotation[0, 2] + rotation[2, 0]) / scale
        qy = (rotation[1, 2] + rotation[2, 1]) / scale
        qz = 0.25 * scale
    return (float(qx), float(qy), float(qz), float(qw))


def _sensor_points(path: Path) -> np.ndarray:
    raw = np.fromfile(path, dtype="<f4")
    if raw.size % 4 != 0:
        raise ValueError(f"{path} is not xyz+intensity float32")
    return np.asarray(raw.reshape(-1, 4)[:, :3], dtype=np.float64)


def _jsonl(path: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line:
            continue
        loaded: object = json.loads(line)
        if not isinstance(loaded, dict):
            raise ValueError(f"{path} has a row that is not an object")
        rows.append(loaded)
    return rows


def _subsample(points: np.ndarray, limit: int) -> np.ndarray:
    if len(points) <= limit:
        return points
    index = np.linspace(0, len(points) - 1, limit).astype(np.int64)
    return points[index]


def _cm(value: float, origin: float) -> int:
    return max(-32768, min(32767, int(round((value - origin) * 100.0))))


def _category(type_id: str) -> int:
    if type_id.startswith("walker."):
        return 1
    if type_id.startswith("vehicle."):
        return 0
    return 3


def export_capture(
    capture_dir: Path,
    map_dir: Path,
    out_bin: Path,
    out_manifest: Path,
    *,
    expected_frames: int = 100,
    points_per_sweep: int = POINTS_PER_SWEEP,
) -> dict[str, object]:
    """Write a version-2 clip from a CARLA capture. One point cloud per collaborator."""
    manifest_path = capture_dir / "manifest.json"
    capture = json.loads(manifest_path.read_text(encoding="utf-8"))
    if capture.get("tick_rate_hz") != 10 or capture.get("frames") != expected_frames:
        raise ValueError(
            f"capture is {capture.get('frames')} frames at {capture.get('tick_rate_hz')} Hz, "
            f"expected {expected_frames} at 10"
        )
    agents = capture.get("agents")
    if not isinstance(agents, list) or len(agents) < 1:
        raise ValueError("capture manifest has no agents")
    agent_ids = [str(row["agent_id"]) for row in agents]
    missing = [agent_id for agent_id in agent_ids if agent_id not in AGENT_COLORS]
    if missing:
        raise ValueError(f"no colour for {missing}")

    sweeps: list[list[np.ndarray]] = []
    poses: list[list[tuple[np.ndarray, np.ndarray]]] = []
    for agent_id in agent_ids:
        pose_rows = _jsonl(capture_dir / "agents" / agent_id / "poses.jsonl")
        if [int(row["frame_id"]) for row in pose_rows] != list(range(expected_frames)):
            raise ValueError(f"{agent_id} does not have frames 0..{expected_frames - 1}")
        agent_poses: list[tuple[np.ndarray, np.ndarray]] = []
        agent_points: list[np.ndarray] = []
        for frame_id, row in enumerate(pose_rows):
            location = row["location"]
            rotation = row["rotation"]
            if not isinstance(location, dict) or not isinstance(rotation, dict):
                raise ValueError(f"{agent_id} frame {frame_id} pose is not an object")
            matrix = carla_rotation_matrix(
                float(rotation["pitch"]), float(rotation["yaw"]), float(rotation["roll"])
            )
            place = np.array(
                [float(location["x"]), float(location["y"]), float(location["z"])],
                dtype=np.float64,
            )
            sensor = _subsample(
                _sensor_points(capture_dir / "agents" / agent_id / "lidar" / f"{frame_id:06d}.bin"),
                points_per_sweep,
            )
            world = sensor @ matrix.T + place
            agent_poses.append((place, matrix))
            agent_points.append(world)
        poses.append(agent_poses)
        sweeps.append(agent_points)

    car_indexes = [i for i, agent_id in enumerate(agent_ids) if agent_id != "drone"]
    origin_xyz = np.mean([poses[i][0][0] for i in car_indexes], axis=0)
    origin = (float(origin_xyz[0]), float(origin_xyz[1]), 0.0)
    places = np.stack([pose[0] for agent in poses for pose in agent])
    pad = 40.0
    window = (
        float(places[:, 0].min() - pad),
        float(places[:, 0].max() + pad),
        float(places[:, 1].min() - pad),
        float(places[:, 1].max() + pad),
    )
    selected = _map_polylines(map_dir, origin, window)

    box_rows = _jsonl(capture_dir / "boxes.jsonl")
    if [int(row["frame_id"]) for row in box_rows] != list(range(expected_frames)):
        raise ValueError("box frames do not match the capture")

    buf = _pack_clip(origin, selected, agent_ids, poses, sweeps, box_rows)

    out_bin.parent.mkdir(parents=True, exist_ok=True)
    out_bin.write_bytes(buf)
    gzip_size = len(gzip.compress(buf, compresslevel=9))
    total_points = int(sum(len(cloud) for agent in sweeps for cloud in agent))
    clip_manifest = {
        "scene_id": f"town03-seed-{capture.get('seed')}",
        "dataset_name": "",
        "dataset_paper": "",
        "platform": "",
        "subset": "",
        "origin": [origin[0], origin[1], origin[2]],
        "scale_cm": 100.0,
        "time_start_us": 0,
        "time_step_us": 100000,
        "num_frames": expected_frames,
        "duration_s": expected_frames / 10,
        "fps": 10,
        "format_version": 2,
        "agents": [
            {
                "id": agent_id,
                "label": agent_id,
                "type": "uav" if agent_id == "drone" else "vehicle",
                "color": AGENT_COLORS[agent_id],
            }
            for agent_id in agent_ids
        ],
        "stats": {
            "num_polylines": len(selected),
            "num_frames": expected_frames,
            "num_agents": len(agent_ids),
            "total_lidar_points": total_points,
            "avg_lidar_points_per_frame": round(total_points / expected_frames, 1),
            "file_size_bytes": len(buf),
            "gzip_size_bytes": gzip_size,
        },
        "attributions": {"dataset": "", "map": "", "software": ""},
    }
    out_manifest.parent.mkdir(parents=True, exist_ok=True)
    out_manifest.write_text(json.dumps(clip_manifest, indent=2) + "\n", encoding="utf-8")
    return {
        "gzip_size_bytes": gzip_size,
        "file_size_bytes": len(buf),
        "total_lidar_points": total_points,
        "num_agents": len(agent_ids),
        "num_frames": expected_frames,
    }


def _map_polylines(
    map_dir: Path,
    origin: tuple[float, float, float],
    window: tuple[float, float, float, float],
) -> list[tuple[int, list[tuple[float, float, float]]]]:
    table = pq.read_table(map_dir / "polylines.parquet")
    rows = table.to_pydict()
    blob = (map_dir / "vertices.bin").read_bytes()
    values = array.array("f")
    values.frombytes(blob)
    if sys.byteorder != "little":
        values.byteswap()
    x_min, x_max, y_min, y_max = window
    selected: list[tuple[int, list[tuple[float, float, float]]]] = []
    for i in range(len(rows["polyline_id"])):
        layer = rows["layer"][i]
        layer_id = 1 if "lane_centre" in layer else 2
        offset = int(rows["vertex_offset"][i])
        count = int(rows["vertex_count"][i])
        verts = [
            (
                float(values[(offset + j) * 3]),
                float(values[(offset + j) * 3 + 1]),
                float(values[(offset + j) * 3 + 2]),
            )
            for j in range(count)
        ]
        if not any(x_min <= x <= x_max and y_min <= y <= y_max for x, y, _z in verts):
            continue
        if any(abs(x - origin[0]) > 320.0 or abs(y - origin[1]) > 320.0 for x, y, _z in verts):
            continue
        selected.append((layer_id, verts))
    return selected


def _pack_clip(
    origin: tuple[float, float, float],
    polylines: list[tuple[int, list[tuple[float, float, float]]]],
    agent_ids: list[str],
    poses: list[list[tuple[np.ndarray, np.ndarray]]],
    sweeps: list[list[np.ndarray]],
    box_rows: list[dict[str, object]],
) -> bytes:
    buf = bytearray()
    buf.extend(b"CPCVCLIP")
    frames = len(sweeps[0])
    buf.extend(
        struct.pack(
            "<HfffIIH",
            2,
            origin[0],
            origin[1],
            origin[2],
            len(polylines),
            frames,
            len(agent_ids),
        )
    )
    for layer_id, verts in polylines:
        packed = [(_cm(x, origin[0]), _cm(y, origin[1]), _cm(z, origin[2])) for x, y, z in verts]
        buf.extend(struct.pack("<BH", layer_id, len(packed)))
        for triple in packed:
            buf.extend(struct.pack("<hhh", *triple))
    for frame_id in range(frames):
        buf.extend(struct.pack("<Q", frame_id * 100_000))
        for agent_index in range(len(agent_ids)):
            place, matrix = poses[agent_index][frame_id]
            qx, qy, qz, qw = _quaternion(matrix)
            buf.extend(
                struct.pack(
                    "<fffffff",
                    float(place[0]),
                    float(place[1]),
                    float(place[2]),
                    qx,
                    qy,
                    qz,
                    qw,
                )
            )
            cloud = sweeps[agent_index][frame_id]
            buf.extend(struct.pack("<H", len(cloud)))
            for point in cloud:
                buf.extend(
                    struct.pack(
                        "<hhh",
                        _cm(float(point[0]), origin[0]),
                        _cm(float(point[1]), origin[1]),
                        _cm(float(point[2]), origin[2]),
                    )
                )
        boxes = box_rows[frame_id]["boxes"]
        if not isinstance(boxes, list):
            raise ValueError(f"frame {frame_id} boxes are not a list")
        buf.extend(struct.pack("<H", len(boxes)))
        for box in boxes:
            if not isinstance(box, dict):
                raise ValueError(f"frame {frame_id} has a box that is not an object")
            center = box["center"]
            extent = box["half_extent"]
            rotation = box["rotation"]
            if (
                not isinstance(center, dict)
                or not isinstance(extent, dict)
                or not isinstance(rotation, dict)
            ):
                raise ValueError(f"frame {frame_id} box fields are not objects")
            matrix = carla_rotation_matrix(
                float(rotation["pitch"]), float(rotation["yaw"]), float(rotation["roll"])
            )
            qx, qy, qz, qw = _quaternion(matrix)
            buf.extend(
                struct.pack(
                    "<iBBffffffffff",
                    int(box["actor_id"]),
                    0,
                    _category(str(box["type_id"])),
                    float(center["x"]),
                    float(center["y"]),
                    float(center["z"]),
                    float(extent["x"]) * 2.0,
                    float(extent["y"]) * 2.0,
                    float(extent["z"]) * 2.0,
                    qx,
                    qy,
                    qz,
                    qw,
                )
            )
    return bytes(buf)


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Export an N-agent hero clip from a CARLA capture")
    parser.add_argument("--capture", type=Path, required=True)
    parser.add_argument("--map", type=Path, required=True)
    parser.add_argument("--out-bin", type=Path, required=True)
    parser.add_argument("--out-manifest", type=Path, required=True)
    args = parser.parse_args()
    result = export_capture(args.capture, args.map, args.out_bin, args.out_manifest)
    print(
        f"clip {result['file_size_bytes']} bytes, gzip {result['gzip_size_bytes']} bytes, "
        f"{result['num_agents']} agents, {result['num_frames']} frames, "
        f"{result['total_lidar_points']} points"
    )


if __name__ == "__main__":
    main()
