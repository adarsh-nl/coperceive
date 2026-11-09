"""Capture-only: a denser, more realistic synthetic demo, built on coperceive.demo.generate.

Everything is still generated here (no third-party bytes). Not committed to the repo.
Usage: python rich_demo.py OUT_DIR
"""

from __future__ import annotations

import math
import random
import sys
from pathlib import Path

import coperceive.demo.generate as g
import numpy as np
from coperceive.se3 import SE3
from PIL import Image, ImageDraw, ImageFilter

g.MAX_BYTES = 2_000_000_000
g.WIDTH, g.HEIGHT = 960, 600
g.FX = g.FY = 620.0
g.CX, g.CY = 480.0, 300.0
AZ, EL = 720, 48
RANGE = 80.0
Obj = g._Object


def _objects():
    moving = (
        Obj("car-1", "car", 4.6, 1.9, 1.5, 0.0, (-48.0, -1.75), (11.0, 0.0)),
        Obj("car-2", "car", 4.4, 1.8, 1.5, 180.0, (46.0, 1.75), (-10.5, 0.0)),
        Obj("car-3", "car", 4.5, 1.8, 1.6, 90.0, (1.75, -52.0), (0.0, 9.0)),
        Obj("car-4", "car", 4.4, 1.8, 1.5, -90.0, (-1.75, 46.0), (0.0, -11.0)),
        Obj("car-5", "car", 4.8, 2.0, 1.8, 180.0, (62.0, 5.25), (-9.0, 0.0)),
        Obj("car-6", "car", 4.3, 1.8, 1.5, 0.0, (-70.0, -5.25), (12.5, 0.0)),
        Obj("car-7", "car", 4.5, 1.8, 1.5, -90.0, (-5.25, 70.0), (0.0, -10.0)),
        Obj("truck-1", "car", 8.5, 2.5, 3.2, 180.0, (80.0, 1.75), (-8.0, 0.0)),
        Obj("cyclist-1", "cyclist", 1.7, 0.7, 1.7, 0.0, (-30.0, -3.4), (6.5, 0.0)),
        Obj("cyclist-2", "cyclist", 1.7, 0.7, 1.7, 90.0, (3.4, -28.0), (0.0, 5.5)),
        Obj("ped-1", "pedestrian", 0.6, 0.6, 1.75, 0.0, (-9.0, -9.0), (0.9, 0.0)),
        Obj("ped-2", "pedestrian", 0.6, 0.6, 1.7, 0.0, (9.0, 9.5), (-0.8, 0.0)),
        Obj("ped-3", "pedestrian", 0.6, 0.6, 1.8, 0.0, (9.5, -12.0), (0.0, 0.7)),
        Obj("ped-4", "pedestrian", 0.6, 0.6, 1.65, 0.0, (-9.5, 12.0), (0.0, -0.6)),
        Obj("ped-5", "pedestrian", 0.6, 0.6, 1.75, 0.0, (16.0, 9.0), (-0.5, 0.0)),
        Obj("ped-6", "pedestrian", 0.6, 0.6, 1.7, 0.0, (-20.0, -9.5), (0.7, 0.0)),
        Obj("ped-7", "pedestrian", 0.6, 0.6, 1.8, 0.0, (24.0, -9.0), (-0.6, 0.0)),
        Obj("ped-8", "pedestrian", 0.6, 0.6, 1.6, 0.0, (-9.0, -26.0), (0.0, 0.5)),
    )
    parked = tuple(
        Obj(
            f"parked-{i}",
            "car",
            4.4,
            1.8,
            1.5,
            0.0 if side < 0 else 180.0,
            (x, side * 8.3),
            (0.0, 0.0),
        )
        for i, (x, side) in enumerate(
            [(-22, -1), (-28, -1), (-40, -1), (18, 1), (25, 1), (33, 1), (-30, 1), (40, -1)]
        )
    )
    return moving + parked


g._objects = _objects

# Static world (not annotated): buildings, trees, poles, kerbs.
_rng = random.Random(7)
STATIC: list[tuple[tuple[float, float, float], tuple[float, float, float], float, str]] = []
for sx in (-1, 1):
    for sy in (-1, 1):
        x0 = 13.0
        while x0 < 75:
            w = _rng.uniform(9, 16)
            d = _rng.uniform(10, 18)
            h = _rng.uniform(7, 22)
            cx = sx * (x0 + w / 2)
            cy = sy * (13.0 + d / 2)
            STATIC.append(((cx, cy, h / 2), (w, d, h), 0.0, "building"))
            STATIC.append(
                ((sy * (13.0 + d / 2), sx * (x0 + w / 2), h / 2), (d, w, h), 0.0, "building")
            )
            x0 += w + _rng.uniform(1.5, 4)
for x in range(-70, 71, 9):
    if abs(x) < 12:
        continue
    for side in (-1, 1):
        STATIC.append(((x, side * 10.6, 3.0), (0.5, 0.5, 6.0), 0.0, "tree"))
        STATIC.append(((x, side * 10.6, 6.2), (3.2, 3.2, 2.6), 0.0, "crown"))
        STATIC.append(((side * 10.6, x, 3.0), (0.5, 0.5, 6.0), 0.0, "tree"))
        STATIC.append(((side * 10.6, x, 6.2), (3.2, 3.2, 2.6), 0.0, "crown"))
for px, py in ((9.5, 9.5), (-9.5, 9.5), (9.5, -9.5), (-9.5, -9.5)):
    STATIC.append(((px, py, 2.5), (0.25, 0.25, 5.0), 0.0, "pole"))
for i in range(-70, 71, 2):
    if abs(i) < 9:
        continue
    for side in (-1, 1):
        STATIC.append(((i + 1, side * 9.2, 0.075), (2.0, 0.3, 0.15), 0.0, "kerb"))
        STATIC.append(((side * 9.2, i + 1, 0.075), (0.3, 2.0, 0.15), 0.0, "kerb"))


def _boxes_at(t: float):
    out = list(STATIC)
    for obj in _objects():
        c, yaw = g._actor_at(obj, t)
        out.append((c, (obj.length, obj.width, obj.height), yaw, obj.category))
    return out


def _slab(origin, dirs, boxes):
    """Nearest hit distance for each ray (N,3) against yaw-rotated boxes."""
    best = np.full(dirs.shape[0], np.inf)
    for (cx, cy, cz), (length, w, h), yaw, _ in boxes:
        c, s = math.cos(-yaw), math.sin(-yaw)
        o = origin - np.array([cx, cy, cz])
        o = np.array([c * o[0] - s * o[1], s * o[0] + c * o[1], o[2]])
        d = np.stack(
            [c * dirs[:, 0] - s * dirs[:, 1], s * dirs[:, 0] + c * dirs[:, 1], dirs[:, 2]], 1
        )
        half = np.array([length / 2, w / 2, h / 2])
        with np.errstate(divide="ignore", invalid="ignore"):
            a = (-half - o) / d
            b = (half - o) / d
        tmin = np.nanmax(np.minimum(a, b), 1)
        tmax = np.nanmin(np.maximum(a, b), 1)
        hit = (tmax >= np.maximum(tmin, 0)) & (tmin > 0.3)
        best = np.where(hit & (tmin < best), tmin, best)
    return best


_az = np.linspace(0, 2 * np.pi, AZ, endpoint=False)
_el = np.radians(np.linspace(-24.0, 6.0, EL))


def _lidar(agent_id, frame, objects, rng):
    pose = g._agent_pose(agent_id, frame)
    origin = np.array(pose.apply((0.0, 0.0, 1.8)))
    yaw = math.atan2(pose.qz, pose.qw) * 2.0
    A, E = np.meshgrid(_az + yaw, _el)
    dirs = np.stack([np.cos(E) * np.cos(A), np.cos(E) * np.sin(A), np.sin(E)], -1).reshape(-1, 3)
    best = _slab(origin, dirs, _boxes_at(frame / g.RATE_HZ))
    with np.errstate(divide="ignore", invalid="ignore"):
        ground = np.where(dirs[:, 2] < -1e-6, -origin[2] / dirs[:, 2], np.inf)
    best = np.minimum(best, ground)
    keep = best < RANGE
    nr = np.random.default_rng(abs(hash((agent_id, frame))) % (2**32))
    keep &= nr.random(keep.shape[0]) > 0.04  # dropouts
    pts = origin + dirs[keep] * best[keep, None]
    pts += nr.normal(0, 0.015, pts.shape)
    inten = np.where(pts[:, 2] < 0.05, 0.15, 0.6).astype(np.float32)
    return np.concatenate([pts.astype(np.float32), inten[:, None]], 1)


g._lidar = _lidar

PALETTE = {
    "building": [(176, 168, 158), (150, 158, 168), (190, 180, 160), (128, 132, 140)],
    "tree": [(92, 70, 50)],
    "crown": [(70, 112, 62), (60, 100, 58)],
    "pole": [(70, 70, 74)],
    "kerb": [(170, 170, 165)],
    "car": [(196, 52, 44), (40, 70, 140), (225, 225, 228), (40, 40, 44), (150, 150, 155)],
    "cyclist": [(30, 140, 180)],
    "pedestrian": [(210, 170, 60), (80, 90, 120), (160, 60, 90)],
}


def _camera_image(agent_id, frame, objects):
    W, H = g.WIDTH, g.HEIGHT
    img = Image.new("RGB", (W, H))
    dr = ImageDraw.Draw(img)
    for y in range(H):
        k = y / H
        dr.line([(0, y), (W, y)], fill=(int(120 + 80 * k), int(160 + 60 * k), int(210 + 30 * k)))
    pose = g._agent_pose(agent_id, frame)
    cam = pose.compose(SE3.from_rpy_deg(-90, 0, -90).translation(1.8, 0.0, 1.2))
    inv = cam.inverse()

    def proj(p):
        q = inv.apply(p)
        if q[2] < 0.6:
            return q, (0.0, 0.0)
        return q, (g.FX * q[0] / q[2] + g.CX, g.FY * q[1] / q[2] + g.CY)

    polys = []
    # ground: grass/pavement tiles, asphalt roads, lane dashes
    for gx in range(-90, 90, 3):
        for gy in range(-90, 90, 3):
            cx, cy = gx + 1.5, gy + 1.5
            on_road = abs(cy) < 7.5 or abs(cx) < 7.5
            pave = (abs(cy) < 12 or abs(cx) < 12) and not on_road
            col = (74, 76, 80) if on_road else ((150, 148, 142) if pave else (96, 124, 78))
            quad = [(gx, gy, 0), (gx + 3, gy, 0), (gx + 3, gy + 3, 0), (gx, gy + 3, 0)]
            polys.append((quad, col))
    for x in range(-90, 90, 6):
        for ax in (0, 1):
            for off in (0.0, -3.5, 3.5):
                if abs(x) < 9:
                    continue
                w = 0.08 if off == 0 else 0.06
                col = (230, 200, 70) if off == 0 else (235, 235, 235)
                if ax == 0:
                    q = [
                        (x, off - w, 0.01),
                        (x + 3, off - w, 0.01),
                        (x + 3, off + w, 0.01),
                        (x, off + w, 0.01),
                    ]
                else:
                    q = [
                        (off - w, x, 0.01),
                        (off + w, x, 0.01),
                        (off + w, x + 3, 0.01),
                        (off - w, x + 3, 0.01),
                    ]
                polys.append((q, col))
    faces = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 2, 6, 4), (1, 5, 7, 3), (2, 3, 7, 6), (0, 4, 5, 1)]
    light = np.array([0.4, 0.3, 0.86])
    parts = []
    for i, (c, size, yaw, cat) in enumerate(_boxes_at(frame / g.RATE_HZ)):
        base = PALETTE[cat][i % len(PALETTE[cat])]
        if cat == "car":
            length, w, h = size
            parts.append(((c[0], c[1], h * 0.3), (length, w, h * 0.6), yaw, base))
            off = -0.08 * length
            parts.append(
                (
                    (c[0] + off * math.cos(yaw), c[1] + off * math.sin(yaw), h * 0.78),
                    (length * 0.52, w * 0.88, h * 0.4),
                    yaw,
                    (52, 62, 74),
                )
            )
        else:
            parts.append((c, size, yaw, base))
    for c, size, yaw, base in parts:
        corners = g._box_corners(c, size, yaw)
        for f in faces:
            pts = [corners[j] for j in f]
            n = np.cross(np.subtract(pts[1], pts[0]), np.subtract(pts[2], pts[0]))
            nn = np.linalg.norm(n)
            n = n / nn if nn else n
            k = 0.55 + 0.45 * abs(float(n @ light))
            polys.append((pts, tuple(int(v * k) for v in base)))
    drawn = []
    for pts, col in polys:
        qs = [proj(p) for p in pts]
        if any(q[0][2] < 0.6 for q in qs):
            continue
        uv = [q[1] for q in qs]
        if (
            max(u for u, _ in uv) < -50
            or min(u for u, _ in uv) > W + 50
            or min(v for _, v in uv) > H + 50
        ):
            continue
        depth = float(np.mean([q[0][2] for q in qs]))
        if depth > 160:
            continue
        haze = min(1.0, depth / 160)
        col = tuple(
            int(c * (1 - 0.45 * haze) + s * 0.45 * haze)
            for c, s in zip(col, (190, 205, 225), strict=False)
        )
        drawn.append((depth, uv, col))
    drawn.sort(key=lambda d: -d[0])
    for _, uv, col in drawn:
        dr.polygon(uv, fill=col)
    img = img.filter(ImageFilter.SMOOTH)
    return img


g._camera_image = _camera_image


if __name__ == "__main__":
    out = Path(sys.argv[1])
    res = g.create(out, seed=3)
    print(
        "mAP@0.5",
        round(res.map_50, 3),
        "(simulated)",
        "bytes",
        res.bytes,
        "s",
        round(res.seconds, 1),
    )
