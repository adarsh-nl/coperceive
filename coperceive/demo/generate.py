# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501, E741
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownParameterType=false, reportMissingParameterType=false, reportArgumentType=false, reportPrivateUsage=false, reportUnknownLambdaType=false, reportIndexIssue=false, reportAttributeAccessIssue=false, reportOptionalMemberAccess=false, reportPossiblyUnboundVariable=false, reportUnusedFunction=false, reportGeneralTypeIssues=false, reportOptionalOperand=false, reportOperatorIssue=false
"""Deterministic, fully synthetic cooperative demo scene.

The output is a generic multi-view dataset (``scene.yaml`` and per-agent files),
ingested to schema-1 scenes and scored with a small noisy prediction set. No
third-party bytes: every image, point and box is generated here.

The junction is dressed for the product tour: dense ray-cast LiDAR that also
sees buildings, trees and parked cars, and camera frames rendered from the same
world description. LiDAR samples are written in the recording agent's body
frame (the LiDAR sensor's extrinsic is identity), which is the frame the
generic format and the app read them in; the app applies the agent pose once.
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
WIDTH, HEIGHT = 512, 384
FX, FY, CX, CY = 400.0, 400.0, 256.0, 192.0

# Dense enough that a sweep reads as a LiDAR scan (126 x 24 beams) while the
# float32 files, their ingested mirror and the rendered cameras still fit the
# 15 MB demo budget.
LIDAR_AZ = 64
LIDAR_EL = 16
LIDAR_MIN_EL_DEG = -24.0
LIDAR_MAX_EL_DEG = 8.0
LIDAR_RANGE = 70.0
MAX_BYTES = 15_000_000

# Camera sensor on both CAVs: the same extrinsic the scene.yaml records.
CAMERA_EXTRINSIC = SE3.from_rpy_deg(-90, 0, -90).translation(1.8, 0.0, 1.2)
LIGHT_DIR = np.array([0.35, 0.25, 0.9]) / float(np.linalg.norm([0.35, 0.25, 0.9]))

PALETTE: dict[str, list[tuple[int, int, int]]] = {
    "building": [(172, 164, 154), (146, 154, 166), (188, 178, 160), (126, 130, 138)],
    "tree": [(94, 72, 52)],
    "crown": [(74, 116, 64), (62, 102, 60), (82, 122, 70)],
    "pole": [(72, 72, 76)],
    "kerb": [(168, 168, 162)],
    "car": [(198, 54, 46), (44, 74, 142), (226, 226, 228), (42, 42, 46), (150, 150, 155)],
    "parked": [(178, 182, 188), (38, 44, 58), (150, 60, 52), (214, 210, 200), (60, 92, 110)],
    "cyclist": [(32, 142, 182)],
    "pedestrian": [(212, 172, 62), (82, 92, 122), (162, 62, 92)],
}


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


@dataclass(frozen=True)
class _Static:
    center: tuple[float, float, float]
    size: tuple[float, float, float]
    yaw_deg: float
    kind: str


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


def _static_world() -> tuple[_Static, ...]:
    """Deterministic street furniture: blocks, trees, parked cars, poles, kerbs."""
    rng = random.Random(11)
    items: list[_Static] = []
    # Two rows of buildings down each side of both roads (four quadrants).
    for sx in (-1, 1):
        for sy in (-1, 1):
            for k in (0, 1):
                w = rng.uniform(9.0, 15.0)
                d = rng.uniform(10.0, 16.0)
                h = rng.uniform(7.0, 20.0)
                x = sx * (14.0 + 18.0 * k + w / 2.0)
                y = sy * (14.0 + d / 2.0)
                items.append(_Static((x, y, h / 2.0), (w, d, h), 0.0, "building"))
                items.append(
                    _Static(
                        (sx * (14.0 + d / 2.0), sy * (14.0 + 18.0 * k + w / 2.0), h / 2.0),
                        (d, w, h),
                        0.0,
                        "building",
                    )
                )
    # Street trees along both pavements (trunk + crown).
    for coord in range(-66, 67, 9):
        if abs(coord) < 12:
            continue
        for side in (-1, 1):
            for cx, cy in ((float(coord), side * 10.6), (side * 10.6, float(coord))):
                items.append(_Static((cx, cy, 2.6), (0.5, 0.5, 5.2), 0.0, "tree"))
                items.append(_Static((cx, cy, 6.4), (3.4, 3.4, 2.8), 0.0, "crown"))
    # Parked cars along the kerbs; not annotated (they are set dressing).
    for x, side, yaw in (
        (-24.0, -1, 0.0),
        (-33.0, -1, 0.0),
        (-45.0, -1, 0.0),
        (-20.0, 1, 180.0),
        (18.0, 1, 180.0),
        (27.0, 1, 180.0),
        (36.0, 1, 180.0),
        (44.0, -1, 0.0),
    ):
        items.append(_Static((x, side * 8.35, 0.75), (4.4, 1.8, 1.5), yaw, "parked"))
    for y, side, yaw in ((-32.0, -1, 90.0), (30.0, 1, 270.0), (-28.0, 1, 270.0), (34.0, -1, 90.0)):
        items.append(_Static((side * 8.35, y, 0.75), (1.8, 4.4, 1.5), yaw, "parked"))
    # Signal poles at the four corners.
    for px, py in ((9.5, 9.5), (-9.5, 9.5), (9.5, -9.5), (-9.5, -9.5)):
        items.append(_Static((px, py, 2.6), (0.25, 0.25, 5.2), 0.0, "pole"))
    # Kerbs along both roads.
    for side in (-1, 1):
        items.append(_Static((0.0, side * 9.05, 0.08), (140.0, 0.35, 0.16), 0.0, "kerb"))
        items.append(_Static((side * 9.05, 0.0, 0.08), (0.35, 140.0, 0.16), 0.0, "kerb"))
    return tuple(items)


STATIC = _static_world()


# -- vectorised geometry -------------------------------------------------------------


def _quaternion_matrix(pose: SE3) -> np.ndarray:
    w, x, y, z = pose.qw, pose.qx, pose.qy, pose.qz
    return np.array(
        [
            [1.0 - 2.0 * (y * y + z * z), 2.0 * (x * y - z * w), 2.0 * (x * z + y * w)],
            [2.0 * (x * y + z * w), 1.0 - 2.0 * (x * x + z * z), 2.0 * (y * z - x * w)],
            [2.0 * (x * z - y * w), 2.0 * (y * z + x * w), 1.0 - 2.0 * (x * x + y * y)],
        ]
    )


def _apply(pose: SE3, points: np.ndarray) -> np.ndarray:
    """``R @ p + t`` for an (N, 3) float64 array."""
    return points @ _quaternion_matrix(pose).T + np.array([pose.tx, pose.ty, pose.tz])


def _apply_inverse(pose: SE3, points: np.ndarray) -> np.ndarray:
    """``R.T @ (p - t)``: world points into the pose's source (body) frame."""
    return (points - np.array([pose.tx, pose.ty, pose.tz])) @ _quaternion_matrix(pose)


def _boxes_at(
    t: float,
) -> list[tuple[tuple[float, float, float], tuple[float, float, float], float, str]]:
    """Every static and moving box at time ``t``: (centre, size l/w/h, yaw, kind)."""
    boxes: list[tuple[tuple[float, float, float], tuple[float, float, float], float, str]] = [
        (item.center, item.size, item.yaw_deg, item.kind) for item in STATIC
    ]
    for obj in _objects():
        center, yaw = _actor_at(obj, t)
        boxes.append((center, (obj.length, obj.width, obj.height), math.degrees(yaw), obj.category))
    return boxes


def _arrays(
    boxes: list[tuple[tuple[float, float, float], tuple[float, float, float], float, str]],
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    centers = np.array([box[0] for box in boxes], dtype=np.float64)
    half = np.array([box[1] for box in boxes], dtype=np.float64) / 2.0
    yaw = np.radians([box[2] for box in boxes])
    return centers, half, yaw


def _slab(
    origin: np.ndarray, dirs: np.ndarray, centers: np.ndarray, half: np.ndarray, yaw: np.ndarray
) -> np.ndarray:
    """Nearest yaw-rotated-box hit distance for each ray (R, 3); inf when none.

    Vectorised over both boxes and rays: one frame is ~150 boxes x ~1000 rays.
    """
    rays = dirs.shape[0]
    boxes = centers.shape[0]
    c = np.cos(-yaw)
    s = np.sin(-yaw)
    rel = origin[None, :] - centers
    local_origin = np.stack(
        [c * rel[:, 0] - s * rel[:, 1], s * rel[:, 0] + c * rel[:, 1], rel[:, 2]], axis=1
    )
    local_dirs = np.stack(
        [
            c[:, None] * dirs[None, :, 0] - s[:, None] * dirs[None, :, 1],
            s[:, None] * dirs[None, :, 0] + c[:, None] * dirs[None, :, 1],
            np.broadcast_to(dirs[None, :, 2], (boxes, rays)),
        ],
        axis=2,
    )
    tmin = np.full((boxes, rays), -np.inf)
    tmax = np.full((boxes, rays), np.inf)
    for axis in range(3):
        with np.errstate(divide="ignore", invalid="ignore"):
            low = (-half[:, axis][:, None] - local_origin[:, axis][:, None]) / local_dirs[
                :, :, axis
            ]
            high = (half[:, axis][:, None] - local_origin[:, axis][:, None]) / local_dirs[
                :, :, axis
            ]
        tmin = np.maximum(tmin, np.minimum(low, high))
        tmax = np.minimum(tmax, np.maximum(low, high))
    hit = (tmax >= np.maximum(tmin, 0.0)) & (tmin > 0.25)
    return np.where(hit, tmin, np.inf).min(axis=0)


def _lidar(agent_id: str, frame: int, seed: int) -> np.ndarray:
    """One sweep, in the agent body frame (x forward, y left, z up)."""
    pose = _agent_pose(agent_id, frame)
    rotation = _quaternion_matrix(pose)
    origin = np.array(pose.apply((0.0, 0.0, 1.8)))
    az = np.linspace(0.0, 2.0 * math.pi, LIDAR_AZ, endpoint=False)
    el = np.radians(np.linspace(LIDAR_MIN_EL_DEG, LIDAR_MAX_EL_DEG, LIDAR_EL))
    a, e = np.meshgrid(az, el)
    body = np.stack([np.cos(e) * np.cos(a), np.cos(e) * np.sin(a), np.sin(e)], axis=-1).reshape(
        -1, 3
    )
    dirs = body @ rotation.T

    rng = np.random.default_rng(
        seed * 1_000_003 + frame * 17 + {"cav-a": 1, "cav-b": 2, "rsu": 3}[agent_id]
    )
    centers, half, yaw = _arrays(_boxes_at(frame / RATE_HZ))
    best = _slab(origin, dirs, centers, half, yaw)
    with np.errstate(divide="ignore", invalid="ignore"):
        ground = np.where(dirs[:, 2] < -1e-6, -origin[2] / dirs[:, 2], np.inf)
    on_ground = ground <= best
    best = np.minimum(best, ground)
    keep = (best < LIDAR_RANGE) & (rng.random(dirs.shape[0]) > 0.25)
    world = origin + dirs[keep] * best[keep, None]
    world += rng.normal(0.0, 0.015, world.shape)
    intensity = np.where(on_ground[keep], 0.16, 0.58) + rng.uniform(-0.04, 0.04, world.shape[0])

    # A few centre-directed beams guarantee a surface return on every actor that
    # is visible, even for thin pedestrians between the azimuth grid lines.
    extra: list[tuple[float, float, float, float]] = []
    for obj in _objects():
        center, _yaw = _actor_at(obj, frame / RATE_HZ)
        for dz in (0.0, 0.35):
            target = np.array(center) + np.array([0.0, 0.0, dz])
            direction = target - origin
            distance = float(np.linalg.norm(direction))
            if distance <= 1e-6 or distance >= LIDAR_RANGE:
                continue
            direction = direction / distance
            hit = _slab(origin, direction[None, :], centers, half, yaw)[0]
            if hit < distance + 0.8:
                point = origin + direction * hit
                extra.append((float(point[0]), float(point[1]), float(point[2]), 0.62))
    body_points = _apply_inverse(pose, world)
    points = np.concatenate(
        [
            np.column_stack([body_points, intensity]),
            np.array(extra, dtype=np.float64).reshape(-1, 4),
        ],
        axis=0,
    )
    return points.astype(np.float32)


# -- camera --------------------------------------------------------------------------

# The background is rendered at half resolution and upscaled: perspective ground
# and sky are smooth, and this keeps 200 camera frames fast for the demo budget.
_BG_SCALE = 2
_FACES = np.array(
    ((0, 1, 3, 2), (4, 6, 7, 5), (0, 2, 6, 4), (1, 5, 7, 3), (2, 3, 7, 6), (0, 4, 5, 1)),
    dtype=np.int64,
)
_FACE_LIGHT = float(np.linalg.norm(LIGHT_DIR))


def _ground_frame(camera: SE3) -> Image.Image:
    """Procedural sky, roads, pavements, lane markings and grass."""
    width, height = WIDTH // _BG_SCALE, HEIGHT // _BG_SCALE
    u = (np.arange(width, dtype=np.float64) + 0.5) * _BG_SCALE - 0.5
    v = (np.arange(height, dtype=np.float64) + 0.5) * _BG_SCALE - 0.5
    dcam = np.stack(
        [
            np.broadcast_to((u - CX)[None, :] / FX, (height, width)),
            np.broadcast_to((v - CY)[:, None] / FY, (height, width)),
            np.ones((height, width)),
        ],
        axis=-1,
    )
    dirs = dcam @ _quaternion_matrix(camera).T
    origin = np.array([camera.tx, camera.ty, camera.tz])
    with np.errstate(divide="ignore", invalid="ignore"):
        distance = np.where(dirs[..., 2] < -1e-5, -origin[2] / dirs[..., 2], np.inf)
    valid = (distance > 0.8) & (distance < 190.0)
    ground = origin[None, None, :] + dirs * np.where(valid, distance, 1.0)[..., None]
    x = ground[..., 0]
    y = ground[..., 1]
    on_road = (np.abs(x) < 7.4) | (np.abs(y) < 7.4)
    on_pavement = (~on_road) & ((np.abs(x) < 13.0) | (np.abs(y) < 13.0))
    color = np.empty((height, width, 3), dtype=np.float64)
    row = v / HEIGHT
    row_colors = np.stack([120.0 + 70.0 * row, 160.0 + 55.0 * row, 208.0 + 30.0 * row], axis=-1)
    color[:] = row_colors[:, None, :]
    grass = np.array([96.0, 124.0, 78.0])
    pavement = np.array([150.0, 148.0, 142.0])
    road = np.array([74.0, 76.0, 80.0])
    surface = np.where(on_road[..., None], road, np.where(on_pavement[..., None], pavement, grass))
    on_x_road = np.abs(y) < 7.2
    on_y_road = np.abs(x) < 7.2
    junction = on_x_road & on_y_road
    dash = (np.floor(x * 0.5) % 2.0) == 0.0
    dash_y = (np.floor(y * 0.5) % 2.0) == 0.0
    white = (
        on_x_road
        & ~junction
        & dash
        & ((np.abs(np.abs(y) - 1.75) < 0.1) | (np.abs(np.abs(y) - 5.25) < 0.1))
    ) | (
        on_y_road
        & ~junction
        & dash_y
        & ((np.abs(np.abs(x) - 1.75) < 0.1) | (np.abs(np.abs(x) - 5.25) < 0.1))
    )
    yellow = (on_x_road & ~junction & (np.abs(y) < 0.12)) | (
        on_y_road & ~junction & (np.abs(x) < 0.12)
    )
    surface = np.where(white[..., None], np.array([232.0, 232.0, 226.0]), surface)
    surface = np.where(yellow[..., None], np.array([228.0, 196.0, 72.0]), surface)
    haze = np.clip(distance / 170.0, 0.0, 1.0) * 0.42
    blended = surface * (1.0 - haze[..., None]) + color * haze[..., None]
    color = np.where(valid[..., None], blended, color)
    small = Image.fromarray(np.clip(color, 0.0, 255.0).astype(np.uint8))
    return small.resize((WIDTH, HEIGHT), Image.Resampling.BILINEAR)


def _corners_for(
    center: tuple[float, float, float], size: tuple[float, float, float], yaw: float
) -> np.ndarray:
    length, width, height = size
    local = np.array(
        [
            (length / 2, width / 2, height / 2),
            (length / 2, width / 2, -height / 2),
            (length / 2, -width / 2, height / 2),
            (length / 2, -width / 2, -height / 2),
            (-length / 2, width / 2, height / 2),
            (-length / 2, width / 2, -height / 2),
            (-length / 2, -width / 2, height / 2),
            (-length / 2, -width / 2, -height / 2),
        ]
    )
    c, s = math.cos(yaw), math.sin(yaw)
    rotated = np.stack(
        [c * local[:, 0] - s * local[:, 1], s * local[:, 0] + c * local[:, 1], local[:, 2]], axis=1
    )
    return rotated + np.array(center)


def _box_corners(
    center: tuple[float, float, float], size: tuple[float, float, float], yaw: float
) -> list[tuple[float, float, float]]:
    """List form kept for the capture-only scripts/site/tour/rich_demo.py patch."""
    corners: list[tuple[float, float, float]] = []
    for corner in _corners_for(center, size, yaw):
        corners.append((float(corner[0]), float(corner[1]), float(corner[2])))
    return corners


def _render_parts(
    boxes: list[tuple[tuple[float, float, float], tuple[float, float, float], float, str]],
) -> tuple[np.ndarray, list[tuple[int, int, int]]]:
    """Corner arrays and base colours for every box face the camera draws."""
    corners: list[np.ndarray] = []
    colours: list[tuple[int, int, int]] = []
    for index, (center, size, yaw_deg, kind) in enumerate(boxes):
        yaw = math.radians(yaw_deg)
        base = PALETTE[kind][index % len(PALETTE[kind])]
        if kind in {"car", "parked"}:
            length, width, height = size
            corners.append(
                _corners_for(
                    (center[0], center[1], center[2] - height * 0.2),
                    (length, width, height * 0.6),
                    yaw,
                )
            )
            colours.append(base)
            corners.append(
                _corners_for(
                    (
                        center[0] - 0.08 * length * math.cos(yaw),
                        center[1] - 0.08 * length * math.sin(yaw),
                        center[2] + height * 0.28,
                    ),
                    (length * 0.52, width * 0.88, height * 0.4),
                    yaw,
                )
            )
            colours.append((52, 62, 74))
        else:
            corners.append(_corners_for(center, size, yaw))
            colours.append(base)
    return np.stack(corners), colours


_STATIC_BOXES: list[tuple[tuple[float, float, float], tuple[float, float, float], float, str]] = [
    (item.center, item.size, item.yaw_deg, item.kind) for item in STATIC
]
_STATIC_CORNERS, _STATIC_COLOURS = _render_parts(_STATIC_BOXES)


def _camera_image(frame: int, agent_id: str) -> Image.Image:
    camera = _agent_pose(agent_id, frame).compose(CAMERA_EXTRINSIC)
    image = _ground_frame(camera)
    draw = ImageDraw.Draw(image)
    dynamic_boxes = _boxes_at(frame / RATE_HZ)[len(STATIC) :]
    if dynamic_boxes:
        dynamic_corners, dynamic_colours = _render_parts(dynamic_boxes)
        corners = np.concatenate([_STATIC_CORNERS, dynamic_corners], axis=0)
        colours = _STATIC_COLOURS + dynamic_colours
    else:
        corners, colours = _STATIC_CORNERS, _STATIC_COLOURS
    flat = corners.reshape(-1, 3)
    local = _apply_inverse(camera, flat).reshape(-1, 8, 3)
    with np.errstate(divide="ignore", invalid="ignore"):
        u = FX * local[..., 0] / local[..., 2] + CX
        v = FY * local[..., 1] / local[..., 2] + CY
    depths = local[..., 2]
    face_depths = depths[:, _FACES].mean(axis=2)
    face_uv = np.stack([u[:, _FACES], v[:, _FACES]], axis=-1)
    in_front = (depths[:, _FACES] >= 0.5).all(axis=2)
    on_screen = (
        (face_uv[..., 0].max(axis=2) > -40)
        & (face_uv[..., 0].min(axis=2) < WIDTH + 40)
        & (face_uv[..., 1].max(axis=2) > -40)
        & (face_uv[..., 1].min(axis=2) < HEIGHT + 40)
        & (face_depths < 170.0)
    )
    p0 = corners[:, _FACES[:, 0]]
    p1 = corners[:, _FACES[:, 1]]
    p2 = corners[:, _FACES[:, 2]]
    normals = np.cross(p1 - p0, p2 - p0)
    norm = np.linalg.norm(normals, axis=2)
    normals = normals / np.where(norm > 0.0, norm, 1.0)[..., None]
    shade = 0.55 + 0.45 * np.abs(np.einsum("nfk,k->nf", normals, LIGHT_DIR / _FACE_LIGHT))
    haze = np.clip(face_depths / 170.0, 0.0, 1.0) * 0.4
    colours_array = np.array(colours, dtype=np.float64)
    sky = np.array([190.0, 205.0, 225.0])
    keep = in_front & on_screen
    selected = np.argwhere(keep)
    order = np.argsort(-face_depths[keep])
    for flat_index in selected[order]:
        face = int(flat_index[1])
        tessellation = int(flat_index[0])
        colour = colours_array[tessellation]
        level = float(shade[tessellation, face])
        fade = float(haze[tessellation, face])
        fill = colour * level * (1.0 - fade) + sky * fade
        draw.polygon(
            [tuple(map(float, point)) for point in face_uv[tessellation, face]],
            fill=tuple(int(round(channel)) for channel in fill),
        )
    return image


# -- dataset writing -----------------------------------------------------------------


def _write_yaml(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def _write_agents(root: Path, seed: int) -> None:
    timestamps = [i * 100_000 for i in range(FRAMES)]
    (root / "agents").mkdir(parents=True)
    objects = _objects()
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
                    for t in timestamps
                    for o in objects
                ),
                encoding="utf-8",
            )
            (agent_root / "poses.csv").write_text(
                "timestamp_us,x,y,z,roll_deg,pitch_deg,yaw_deg\n"
                + "".join(
                    f"{i * 100_000},{_agent_pose(agent_id, i).tx},{_agent_pose(agent_id, i).ty},"
                    f"{_agent_pose(agent_id, i).tz},0,0,{0 if agent_id == 'cav-a' else 90}\n"
                    for i in range(FRAMES)
                ),
                encoding="utf-8",
            )
        for frame in range(FRAMES):
            points = _lidar(agent_id, frame, seed)
            (agent_root / "lidar" / f"{frame:06d}.bin").write_bytes(points.tobytes())
            if agent_id != "rsu":
                _camera_image(frame, agent_id).quantize(colors=64).save(
                    agent_root / "camera" / f"{frame:06d}.png", optimize=True
                )
    sensors = []
    for agent_id in ("cav-a", "cav-b"):
        sensors.append(
            {
                "id": "front",
                "modality": "camera",
                "files": f"agents/{agent_id}/camera/{{frame:06d}}.png",
                "intrinsic": [FX, FY, CX, CY],
                "image_size": [WIDTH, HEIGHT],
                "body_from_sensor": {
                    "translation_m": [1.8, 0.0, 1.2],
                    "rotation_rpy_deg": [-90, 0, -90],
                },
            }
        )
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
                    "id": "cav-a",
                    "role": "vehicle",
                    "poses": "agents/cav-a/poses.csv",
                    "boxes": "agents/cav-a/boxes.csv",
                    "sensors": [
                        {
                            "id": "lidar",
                            "modality": "lidar",
                            "files": "agents/cav-a/lidar/{frame:06d}.bin",
                        },
                        sensors[0],
                    ],
                },
                {
                    "id": "cav-b",
                    "role": "vehicle",
                    "poses": "agents/cav-b/poses.csv",
                    "boxes": "agents/cav-b/boxes.csv",
                    "sensors": [
                        {
                            "id": "lidar",
                            "modality": "lidar",
                            "files": "agents/cav-b/lidar/{frame:06d}.bin",
                        },
                        sensors[1],
                    ],
                },
                {
                    "id": "rsu",
                    "role": "roadside unit",
                    "fixed_pose": {
                        "translation_m": [0.0, -12.0, 6.0],
                        "rotation_rpy_deg": [0, 0, 0],
                    },
                    "sensors": [
                        {
                            "id": "lidar",
                            "modality": "lidar",
                            "files": "agents/rsu/lidar/{frame:06d}.bin",
                        }
                    ],
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
            placed = (
                ann.box_from_rpy.translation(*ann.center)
                if ann.frame is BoxFrame.WORLD
                else world_from_body.compose(ann.box_from_rpy.translation(*ann.center))
            )
            noisy = placed.translation(
                placed.tx + rng.gauss(0, 0.18),
                placed.ty + rng.gauss(0, 0.18),
                placed.tz + rng.gauss(0, 0.05),
            )
            body = world_from_body.inverse().compose(noisy)
            rows.append(
                PredictionBox(
                    scene_id=scene.scene_id,
                    agent_id=ann.agent_id,
                    source="noisy",
                    time_us=ann.time_us,
                    category=ann.category,
                    score=round(0.95 - 0.03 * rng.random(), 4),
                    track_id=ann.track_id,
                    center=(body.tx, body.ty, body.tz),
                    size_lwh=ann.size_lwh,
                    box_from_rpy=SE3(body.qw, body.qx, body.qy, body.qz, 0, 0, 0),
                    frame=BoxFrame.BODY,
                )
            )
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
    _write_agents(raw, seed)
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
        run_id=RUN_ID,
        model="synthetic demo",
        score_note="Simulated noisy boxes; not a model result.",
        categories=("car", "cyclist", "pedestrian"),
        sources=(("noisy", "simulated detector"),),
        created_utc="2026-01-01T00:00:00Z",
    )
    write_run(predictions, RUN_ID, meta, rows)
    # The plain JSONL view of the same predictions, for readers that do not use parquet.
    with (predictions / RUN_ID / "predictions.jsonl").open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(
                json.dumps(
                    {
                        "scene_id": row.scene_id,
                        "agent_id": row.agent_id,
                        "source": row.source,
                        "time_us": row.time_us,
                        "category": row.category,
                        "score": row.score,
                        "track_id": row.track_id,
                        "cx": row.center[0],
                        "cy": row.center[1],
                        "cz": row.center[2],
                        "length": row.size_lwh[0],
                        "width": row.size_lwh[1],
                        "height": row.size_lwh[2],
                        "qw": row.box_from_rpy.qw,
                        "qx": row.box_from_rpy.qx,
                        "qy": row.box_from_rpy.qy,
                        "qz": row.box_from_rpy.qz,
                        "frame": "body",
                    }
                )
                + "\n"
            )
    complete = scenes / SCENE_ID / "COMPLETE.json"
    if complete.is_file():
        marker = json.loads(complete.read_text(encoding="utf-8"))
        marker["generator"] = "coperceive demo"
        marker["dataset_id"] = "demo"
        complete.write_text(json.dumps(marker, indent=2) + "\n", encoding="utf-8")
    report = evaluate_run(
        scenes, predictions, RUN_ID, DEFAULT_CONFIG, created_utc="2026-01-01T00:00:00Z"
    )
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
    return DemoScene(
        root, raw, scenes, predictions, seed, time.monotonic() - started, total, map_50
    )
