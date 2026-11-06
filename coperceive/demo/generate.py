# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501, E741
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownParameterType=false, reportMissingParameterType=false, reportArgumentType=false, reportPrivateUsage=false, reportUnknownLambdaType=false, reportIndexIssue=false, reportAttributeAccessIssue=false, reportOptionalMemberAccess=false, reportPossiblyUnboundVariable=false, reportUnusedFunction=false, reportGeneralTypeIssues=false, reportOptionalOperand=false, reportOperatorIssue=false
"""Deterministic, fully synthetic cooperative demo scene.

The output is a generic multi-view dataset (``scene.yaml`` and per-agent files),
ingested to schema-1 scenes and scored with a small noisy prediction set. No
third-party bytes: every image, point and box is generated here.
"""

from __future__ import annotations

import json
import math
import random
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import numpy as np
import yaml
from PIL import Image, ImageDraw

from coperceive.adapters.generic import GenericAdapter
from coperceive.adapters.ingest import ingest
from coperceive.evaluation.config import DEFAULT_CONFIG
from coperceive.evaluation.evaluate import evaluate_run, read_metrics
from coperceive.evaluation.predictions import PredictionBox, RunMeta, write_run
from coperceive.model import BoxFrame
from coperceive.se3 import SE3
from coperceive.transform import lookup_pose

SCENE_ID = "demo-junction"
RUN_ID = "demo"
FRAMES = 100
RATE_HZ = 10
WIDTH, HEIGHT = 640, 480
FX, FY, CX, CY = 500.0, 500.0, 320.0, 240.0
LIDAR_AZ = 32
LIDAR_EL = 16
LIDAR_RANGE = 60.0
MAX_BYTES = 15_000_000


@dataclass(frozen=True)
class DemoScene:
    """Where ``create`` put the demo and what it measured."""

    root: Path
    raw: Path
    scenes: Path
    predictions: Path
    seed: int
    seconds: float
    bytes: int
    map_50: float


@dataclass(frozen=True)
class _Object:
    track_id: str
    category: str
    length: float
    width: float
    height: float
    yaw_deg: float
    position: tuple[float, float]
    velocity: tuple[float, float]


def _objects() -> tuple[_Object, ...]:
    # A four-way junction near the origin. Ten moving actors: four cars, one
    # cyclist, five pedestrians. Positions are metres in the world (x east, y
    # north); all velocities are m/s and constant over the 10 s clip.
    return (
        _Object("car-1", "car", 4.4, 1.8, 1.5, 0.0, (-45.0, -1.75), (12.0, 0.0)),
        _Object("car-2", "car", 4.4, 1.8, 1.5, 180.0, (45.0, 1.75), (-11.0, 0.0)),
        _Object("car-3", "car", 4.4, 1.8, 1.5, 90.0, (1.75, -45.0), (0.0, 11.0)),
        _Object("car-4", "car", 4.4, 1.8, 1.5, -90.0, (-1.75, 45.0), (0.0, -12.0)),
        _Object("cyclist-1", "cyclist", 1.7, 0.7, 1.7, 0.0, (-30.0, -3.2), (7.0, 0.0)),
        _Object("ped-1", "pedestrian", 0.6, 0.6, 1.75, 0.0, (-4.0, -8.0), (0.9, 0.0)),
        _Object("ped-2", "pedestrian", 0.6, 0.6, 1.75, 0.0, (4.0, 8.0), (-0.8, 0.0)),
        _Object("ped-3", "pedestrian", 0.6, 0.6, 1.75, 0.0, (8.0, -4.0), (0.0, 0.7)),
        _Object("ped-4", "pedestrian", 0.6, 0.6, 1.75, 0.0, (-8.0, 4.0), (0.0, -0.6)),
        _Object("ped-5", "pedestrian", 0.6, 0.6, 1.75, 0.0, (12.0, 2.0), (-0.5, 0.0)),
    )


def _actor_at(obj: _Object, t: float) -> tuple[tuple[float, float, float], float]:
    cx = obj.position[0] + obj.velocity[0] * t
    cy = obj.position[1] + obj.velocity[1] * t
    return (cx, cy, obj.height / 2.0), math.radians(obj.yaw_deg)


def _agent_pose(agent_id: str, frame: int) -> SE3:
    t = frame / RATE_HZ
    if agent_id == "cav-a":
        return SE3.identity().translation(-35.0 + 10.5 * t, -1.75, 1.9)
    if agent_id == "cav-b":
        return SE3.from_rpy_deg(0, 0, 90).translation(1.75, -35.0 + 10.5 * t, 1.9)
    return SE3.identity().translation(0.0, -12.0, 6.0)


def _box_corners(center: tuple[float, float, float], size: tuple[float, float, float], yaw: float) -> list[tuple[float, float, float]]:
    l, w, h = size
    local = [
        (l / 2, w / 2, h / 2), (l / 2, w / 2, -h / 2), (l / 2, -w / 2, h / 2), (l / 2, -w / 2, -h / 2),
        (-l / 2, w / 2, h / 2), (-l / 2, w / 2, -h / 2), (-l / 2, -w / 2, h / 2), (-l / 2, -w / 2, -h / 2),
    ]
    c, s = math.cos(yaw), math.sin(yaw)
    return [(center[0] + c * x - s * y, center[1] + s * x + c * y, center[2] + z) for x, y, z in local]


def _ray_box(origin: np.ndarray, direction: np.ndarray, center: np.ndarray, yaw: float, size: tuple[float, float, float]) -> float | None:
    c, s = math.cos(-yaw), math.sin(-yaw)
    o = origin - center
    o = np.array([c * o[0] - s * o[1], s * o[0] + c * o[1], o[2]])
    d = np.array([c * direction[0] - s * direction[1], s * direction[0] + c * direction[1], direction[2]])
    half = np.array([size[0] / 2, size[1] / 2, size[2] / 2])
    tmin, tmax = -np.inf, np.inf
    for axis in range(3):
        if abs(d[axis]) < 1e-9:
            if abs(o[axis]) > half[axis]:
                return None
            continue
        a = (-half[axis] - o[axis]) / d[axis]
        b = (half[axis] - o[axis]) / d[axis]
        tmin, tmax = max(tmin, min(a, b)), min(tmax, max(a, b))
        if tmin > tmax:
            return None
    return float(tmin) if tmax >= max(tmin, 0) else None


def _lidar(agent_id: str, frame: int, objects: tuple[_Object, ...], rng: random.Random) -> np.ndarray:
    pose = _agent_pose(agent_id, frame)
    origin = np.array(pose.apply((0.0, 0.0, 1.8)))
    # Direction in body FLU, rotated by the agent's yaw only.
    yaw = math.atan2(pose.qz, pose.qw) * 2.0
    points: list[tuple[float, float, float, float]] = []
    for az_i in range(LIDAR_AZ):
        az = 2 * math.pi * az_i / LIDAR_AZ
        for el_i in range(LIDAR_EL):
            el = math.radians(-25.0 + 40.0 * el_i / (LIDAR_EL - 1))
            body = np.array([math.cos(el) * math.cos(az), math.cos(el) * math.sin(az), math.sin(el)])
            world_dir = np.array([math.cos(yaw) * body[0] - math.sin(yaw) * body[1], math.sin(yaw) * body[0] + math.cos(yaw) * body[1], body[2]])
            best = LIDAR_RANGE
            if world_dir[2] < -1e-6:
                best = min(best, float(-origin[2] / world_dir[2]))
            for obj in objects:
                center, obj_yaw = _actor_at(obj, frame / RATE_HZ)
                t = _ray_box(origin, world_dir, np.array(center), obj_yaw, (obj.length, obj.width, obj.height))
                if t is not None and 0.1 < t < best:
                    best = t
            if best < LIDAR_RANGE:
                point = origin + world_dir * best
                # centimetre-scale beam noise, deterministic from the seed
                point = point + np.array([rng.gauss(0, 0.01), rng.gauss(0, 0.01), rng.gauss(0, 0.01)])
                points.append((float(point[0]), float(point[1]), float(point[2]), 0.0))
    # A few centre-directed beams guarantee a surface return for every box that
    # is not occluded, including thin pedestrians between the azimuth grid lines.
    for obj in objects:
        center, obj_yaw = _actor_at(obj, frame / RATE_HZ)
        for dz in (0.0, 0.35):
            direction = np.array(center) + np.array([0.0, 0.0, dz]) - origin
            length = float(np.linalg.norm(direction))
            if length <= 1e-6 or length >= LIDAR_RANGE:
                continue
            direction = direction / length
            best = LIDAR_RANGE
            for other in objects:
                oc, oyaw = _actor_at(other, frame / RATE_HZ)
                t = _ray_box(origin, direction, np.array(oc), oyaw, (other.length, other.width, other.height))
                if t is not None and 0.1 < t < best:
                    best = t
            if best < LIDAR_RANGE:
                point = origin + direction * best
                points.append((float(point[0]), float(point[1]), float(point[2]), 0.0))
    return np.asarray(points, dtype=np.float32)


def _camera_image(agent_id: str, frame: int, objects: tuple[_Object, ...]) -> Image.Image:
    image = Image.new("RGB", (WIDTH, HEIGHT), (135, 170, 205))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, HEIGHT // 2, WIDTH, HEIGHT), fill=(105, 105, 105))
    pose = _agent_pose(agent_id, frame)
    camera = pose.compose(SE3.from_rpy_deg(-90, 0, -90).translation(1.8, 0.0, 1.2))
    for obj in objects:
        center, yaw = _actor_at(obj, frame / RATE_HZ)
        corners = _box_corners(center, (obj.length, obj.width, obj.height), yaw)
        projected: list[tuple[float, float]] = []
        visible = True
        for corner in corners:
            p = camera.inverse().apply(corner)
            if p[2] <= 0.5:
                visible = False
                break
            projected.append((FX * p[0] / p[2] + CX, FY * p[1] / p[2] + CY))
        if not visible:
            continue
        faces = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 2, 6, 4), (1, 5, 7, 3), (2, 3, 7, 6), (0, 4, 5, 1)]
        shade = {"car": (200, 60, 50), "cyclist": (40, 150, 190), "pedestrian": (220, 180, 40)}[obj.category]
        for face in faces:
            pts = [projected[i] for i in face]
            if all(p[2] > 1.0 for p in [camera.inverse().apply(corners[i]) for i in face]):
                draw.polygon(pts, fill=shade, outline=(30, 30, 30))
    return image


def _write_yaml(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def _write_agents(root: Path, objects: tuple[_Object, ...], seed: int) -> None:
    timestamps = [i * 100_000 for i in range(FRAMES)]
    (root / "agents").mkdir(parents=True)
    for agent_id in ("cav-a", "cav-b", "rsu"):
        agent_root = root / "agents" / agent_id
        (agent_root / "lidar").mkdir(parents=True)
        if agent_id != "rsu":
            (agent_root / "camera").mkdir()
            (agent_root / "boxes.csv").write_text(
                "timestamp_us,track_id,category,x,y,z,length,width,height,yaw_deg\n"
                + "".join(
                    f"{t},{o.track_id},{o.category},{_actor_at(o, t / 1e6)[0][0]},"
                    f"{_actor_at(o, t / 1e6)[0][1]},{_actor_at(o, t / 1e6)[0][2]},"
                    f"{o.length},{o.width},{o.height},{o.yaw_deg}\n"
                    for t in timestamps for o in objects
                ),
                encoding="utf-8",
            )
            (agent_root / "poses.csv").write_text(
                "timestamp_us,x,y,z,roll_deg,pitch_deg,yaw_deg\n"
                + "".join(
                    f"{i*100_000},{_agent_pose(agent_id, i).tx},{_agent_pose(agent_id, i).ty},"
                    f"{_agent_pose(agent_id, i).tz},0,0,{0 if agent_id == 'cav-a' else 90}\n"
                    for i in range(FRAMES)
                ),
                encoding="utf-8",
            )
        for frame in range(FRAMES):
            rng = random.Random(seed * 1_000_003 + frame * 17 + sum(ord(c) for c in agent_id))
            points = _lidar(agent_id, frame, objects, rng)
            (agent_root / "lidar" / f"{frame:06d}.bin").write_bytes(points.tobytes())
            if agent_id != "rsu":
                _camera_image(agent_id, frame, objects).save(agent_root / "camera" / f"{frame:06d}.png")
    _write_yaml(
        root / "scene.yaml",
        {
            "schema": "coperceive-generic-1",
            "scene_id": SCENE_ID,
            "handedness": "right",
            "frame_rate_hz": RATE_HZ,
            "frame_count": FRAMES,
            "agents": [
                {
                    "id": "cav-a", "role": "vehicle", "poses": "agents/cav-a/poses.csv",
                    "boxes": "agents/cav-a/boxes.csv",
                    "sensors": [
                        {"id": "lidar", "modality": "lidar", "files": "agents/cav-a/lidar/{frame:06d}.bin"},
                        {"id": "front", "modality": "camera", "files": "agents/cav-a/camera/{frame:06d}.png",
                         "intrinsic": [FX, FY, CX, CY], "image_size": [WIDTH, HEIGHT],
                         "body_from_sensor": {"translation_m": [1.8, 0.0, 1.2], "rotation_rpy_deg": [-90, 0, -90]}},
                    ],
                },
                {
                    "id": "cav-b", "role": "vehicle", "poses": "agents/cav-b/poses.csv",
                    "boxes": "agents/cav-b/boxes.csv",
                    "sensors": [
                        {"id": "lidar", "modality": "lidar", "files": "agents/cav-b/lidar/{frame:06d}.bin"},
                        {"id": "front", "modality": "camera", "files": "agents/cav-b/camera/{frame:06d}.png",
                         "intrinsic": [FX, FY, CX, CY], "image_size": [WIDTH, HEIGHT],
                         "body_from_sensor": {"translation_m": [1.8, 0.0, 1.2], "rotation_rpy_deg": [-90, 0, -90]}},
                    ],
                },
                {
                    "id": "rsu", "role": "roadside unit", "fixed_pose": {"translation_m": [0.0, -12.0, 6.0], "rotation_rpy_deg": [0, 0, 0]},
                    "sensors": [{"id": "lidar", "modality": "lidar", "files": "agents/rsu/lidar/{frame:06d}.bin"}],
                },
            ],
        },
    )


def _prediction_rows(scenes: Path, seed: int) -> list[PredictionBox]:
    rng = random.Random(seed + 99)
    rows: list[PredictionBox] = []
    for scene_dir in sorted(p for p in scenes.iterdir() if (p / "manifest.json").is_file()):
        from coperceive.store import read_scene

        scene = read_scene(scene_dir)
        for ann in scene.annotations:
            if ann.agent_id == "rsu":
                continue
            if ann.track_id is not None and ann.track_id.endswith("5") and rng.random() < 0.12:
                continue  # a deterministic miss
            world_from_body = lookup_pose(scene.agent(ann.agent_id), ann.time_us, max_gap_us=0)
            placed = ann.box_from_rpy.translation(*ann.center) if ann.frame is BoxFrame.WORLD else world_from_body.compose(ann.box_from_rpy.translation(*ann.center))
            noisy = placed.translation(placed.tx + rng.gauss(0, 0.18), placed.ty + rng.gauss(0, 0.18), placed.tz + rng.gauss(0, 0.05))
            body = world_from_body.inverse().compose(noisy)
            rows.append(PredictionBox(
                scene_id=scene.scene_id, agent_id=ann.agent_id, source="noisy", time_us=ann.time_us,
                category=ann.category, score=round(0.95 - 0.03 * rng.random(), 4), track_id=ann.track_id,
                center=(body.tx, body.ty, body.tz), size_lwh=ann.size_lwh,
                box_from_rpy=SE3(body.qw, body.qx, body.qy, body.qz, 0, 0, 0), frame=BoxFrame.BODY,
            ))
    return rows


def create(path: str | Path, seed: int = 0) -> DemoScene:
    """Write the demo dataset, ingest it and score the synthetic predictions."""
    started = time.monotonic()
    root = Path(path).expanduser()
    raw = root / "raw"
    scenes = root / "scenes"
    predictions = root / "predictions"
    for child in (raw, scenes, predictions):
        if child.exists():
            raise FileExistsError(f"{child} already exists")
    raw.mkdir(parents=True)
    _write_agents(raw, _objects(), seed)
    ingest(GenericAdapter(), raw, scenes, params={}, workers=1)
    # Ingest keeps image paths relative to the raw root; copy the generated
    # camera frames beside the manifest so the viewer strip resolves them.
    from coperceive.store import read_scene

    scene = read_scene(scenes / SCENE_ID)
    for image in scene.images:
        source = raw / image.path
        target = scenes / SCENE_ID / image.path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
    rows = _prediction_rows(scenes, seed)
    meta = RunMeta(
        run_id=RUN_ID, model="synthetic demo", score_note="Simulated noisy boxes; not a model result.",
        categories=("car", "cyclist", "pedestrian"), sources=(("noisy", "simulated detector"),),
        created_utc="2026-01-01T00:00:00Z",
    )
    write_run(predictions, RUN_ID, meta, rows)
    # The plain JSONL view of the same predictions, for readers that do not use parquet.
    with (predictions / RUN_ID / "predictions.jsonl").open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps({
                "scene_id": row.scene_id, "agent_id": row.agent_id, "source": row.source,
                "time_us": row.time_us, "category": row.category, "score": row.score,
                "track_id": row.track_id, "cx": row.center[0], "cy": row.center[1], "cz": row.center[2],
                "length": row.size_lwh[0], "width": row.size_lwh[1], "height": row.size_lwh[2],
                "qw": row.box_from_rpy.qw, "qx": row.box_from_rpy.qx, "qy": row.box_from_rpy.qy,
                "qz": row.box_from_rpy.qz, "frame": "body",
            }) + "\n")
    report = evaluate_run(scenes, predictions, RUN_ID, DEFAULT_CONFIG, created_utc="2026-01-01T00:00:00Z")
    if report.errors:
        raise RuntimeError("; ".join(report.errors))
    metrics = read_metrics(predictions / RUN_ID / SCENE_ID)
    aps: list[float] = []
    for output in cast(list[dict[str, object]], metrics.get("outputs", [])):
        ap = cast(dict[str, dict[str, float]], output.get("ap", {}))
        aps.extend(float(v) for v in ap.get("0.5", {}).values())
    map_50 = sum(aps) / len(aps) if aps else 0.0
    total = sum(p.stat().st_size for p in root.rglob("*") if p.is_file())
    if total > MAX_BYTES:
        shutil.rmtree(root)
        raise RuntimeError(f"demo dataset is {total} bytes, over the {MAX_BYTES} budget")
    return DemoScene(root, raw, scenes, predictions, seed, time.monotonic() - started, total, map_50)
