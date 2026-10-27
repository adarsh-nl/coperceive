#!/usr/bin/env python3
"""Render still images of the landing page hero from the committed clip.

``python scripts/site/render_poster.py og`` writes ``site/public/og.png`` (1200×630):
the hero in the page's style (lane lines, glowing lidar points, the drone glow and
its tether, the same orbit camera as ``site/src/components/RealClip.tsx``), the
wordmark and the headline, nothing else. Deterministic: same clip, same bytes.

``python scripts/site/render_poster.py icon`` writes
``site/public/apple-touch-icon.png`` (180×180), the favicon mark on an opaque square.

``python scripts/site/render_poster.py poster`` is the older matplotlib poster
(``site/public/hero/poster.png`` and ``.webp``). Its labels carry numbers; the
committed poster is a capture of the player, not this output.

Fonts are the self-hosted Geist files from ``site/node_modules/@fontsource-variable``.
"""

from __future__ import annotations

import json
import struct
import sys
from dataclasses import dataclass
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from PIL import Image, ImageDraw, ImageFont

SCRIPT_DIR = Path(__file__).resolve().parent
SITE_DIR = SCRIPT_DIR.parent.parent / "site"
PUBLIC_DIR = SITE_DIR / "public"
PUBLIC_HERO_DIR = PUBLIC_DIR / "hero"
FONT_DIR = SITE_DIR / "node_modules" / "@fontsource-variable"
GEIST = FONT_DIR / "geist" / "files" / "geist-latin-wght-normal.woff2"
GEIST_MONO = FONT_DIR / "geist-mono" / "files" / "geist-mono-latin-wght-normal.woff2"

# site/src/tokens.ts
TEXT = (245, 245, 247)
LANES = (125, 206, 160)
VEHICLE = (245, 138, 31)
DRONE = (60, 141, 255)
CURSOR = (224, 58, 34)

# site/src/components/RealClip.tsx
FOV_DEG = 52.0
ORBIT_RADIUS = 48.0
ORBIT_HEIGHT = 54.0
ORBIT_LOOK_Z = 6.0
ORBIT_START = -0.8


def render_poster() -> None:
    manifest_path = PUBLIC_HERO_DIR / "manifest.json"
    bin_path = PUBLIC_HERO_DIR / "clip.bin"

    _manifest = json.loads(manifest_path.read_text())
    blob = bin_path.read_bytes()

    # Parse header
    magic = blob[:8]
    assert magic == b"CPCVCLIP", f"Invalid magic: {magic}"
    offset = 8
    ver, ox, oy, oz, num_polylines, num_frames = struct.unpack_from("<HfffII", blob, offset)
    offset += 2 + 12 + 4 + 4

    # Parse polylines
    polylines = []
    for _ in range(num_polylines):
        layer_id, nverts = struct.unpack_from("<BH", blob, offset)
        offset += 3
        verts = []
        for _ in range(nverts):
            dx, dy, dz = struct.unpack_from("<hhh", blob, offset)
            offset += 6
            verts.append((ox + dx * 0.01, oy + dy * 0.01, oz + dz * 0.01))
        polylines.append((layer_id, np.array(verts)))

    # Skip to frame 52
    target_frame = 52
    cur_frame_data = None
    for f in range(num_frames):
        t_us = struct.unpack_from("<Q", blob, offset)[0]
        offset += 8
        v_pose = struct.unpack_from("<fffffff", blob, offset)
        offset += 28
        d_pose = struct.unpack_from("<fffffff", blob, offset)
        offset += 28
        n_pts = struct.unpack_from("<H", blob, offset)[0]
        offset += 2
        pts = []
        for _ in range(n_pts):
            dx, dy, dz = struct.unpack_from("<hhh", blob, offset)
            offset += 6
            pts.append((ox + dx * 0.01, oy + dy * 0.01, oz + dz * 0.01))
        n_boxes = struct.unpack_from("<H", blob, offset)[0]
        offset += 2
        boxes = []
        for _ in range(n_boxes):
            bdata = struct.unpack_from("<iBBffffffffff", blob, offset)
            offset += 4 + 2 + 40
            boxes.append(bdata)

        if f == target_frame:
            cur_frame_data = {
                "time_us": t_us,
                "v_pose": v_pose,
                "d_pose": d_pose,
                "pts": np.array(pts) if pts else np.empty((0, 3)),
                "boxes": boxes,
            }
            break

    assert cur_frame_data is not None, "Frame 52 not found"

    # Set up dark canvas figure
    plt.style.use("dark_background")
    fig = plt.figure(figsize=(16, 9), dpi=120)
    ax = fig.add_subplot(111, projection="3d")
    fig.patch.set_facecolor("#000000")
    ax.set_facecolor("#000000")

    # Perspective camera settings
    v_tx, v_ty, v_tz = cur_frame_data["v_pose"][:3]
    d_tx, d_ty, d_tz = cur_frame_data["d_pose"][:3]

    # Draw map polylines
    for layer_id, verts in polylines:
        if len(verts) < 2:
            continue
        color = "#7dcea0" if layer_id == 1 else "#86868b"
        alpha = 0.85 if layer_id == 1 else 0.4
        lw = 1.6 if layer_id == 1 else 1.0
        ax.plot(verts[:, 0], verts[:, 1], verts[:, 2], color=color, alpha=alpha, linewidth=lw)

    # Draw LiDAR points with radial distance fade
    pts = cur_frame_data["pts"]
    if len(pts) > 0:
        dists = np.hypot(pts[:, 0] - v_tx, pts[:, 1] - v_ty)
        alphas = np.clip(1.0 - (dists / 45.0), 0.15, 0.9)
        # Vehicle orange color with variable alpha
        colors = np.zeros((len(pts), 4))
        colors[:, 0] = 0.902  # R
        colors[:, 1] = 0.471  # G
        colors[:, 2] = 0.157  # B
        colors[:, 3] = alphas
        ax.scatter(pts[:, 0], pts[:, 1], pts[:, 2], s=2.2, c=colors, depthshade=False)

    # Draw Vehicle position (orange marker + label)
    ax.scatter(
        [v_tx],
        [v_ty],
        [v_tz + 0.8],
        color="#e67828",
        s=180,
        marker="s",
        edgecolors="white",
        linewidth=1.5,
        zorder=10,
    )
    ax.text(
        v_tx,
        v_ty - 2.5,
        v_tz + 2.0,
        "Vehicle (CAV)",
        color="#e67828",
        fontsize=11,
        fontweight="bold",
    )

    # Draw Drone position (UAV blue marker + altitude ray)
    ax.scatter(
        [d_tx],
        [d_ty],
        [d_tz],
        color="#2878dc",
        s=220,
        marker="o",
        edgecolors="white",
        linewidth=1.5,
        zorder=10,
    )
    ax.plot(
        [d_tx, d_tx],
        [d_ty, d_ty],
        [0, d_tz],
        color="#2878dc",
        linestyle="--",
        alpha=0.6,
        linewidth=1.2,
    )
    ax.text(
        d_tx,
        d_ty - 2.5,
        d_tz + 2.5,
        "Drone UAV (alt 27m)",
        color="#2878dc",
        fontsize=11,
        fontweight="bold",
    )

    # Drone downward camera footprint pyramid lines to ground
    frustum_size = 14.0
    corners = [
        (d_tx - frustum_size, d_ty - frustum_size, 0),
        (d_tx + frustum_size, d_ty - frustum_size, 0),
        (d_tx + frustum_size, d_ty + frustum_size, 0),
        (d_tx - frustum_size, d_ty + frustum_size, 0),
    ]
    for c in corners:
        ax.plot([d_tx, c[0]], [d_ty, c[1]], [d_tz, c[2]], color="#2878dc", alpha=0.3, linewidth=1.0)
    corners_arr = np.array(corners + [corners[0]])
    ax.plot(
        corners_arr[:, 0],
        corners_arr[:, 1],
        corners_arr[:, 2],
        color="#2878dc",
        alpha=0.45,
        linewidth=1.4,
    )

    # Draw boxes
    for b in cur_frame_data["boxes"]:
        num_tid, flag, cat_id, cx, cy, cz, length_m, width_m, height_m = b[:9]
        if num_tid == 265:
            # Occluded pedestrian! Highlight in status warn / UAV color
            ax.scatter(
                [cx],
                [cy],
                [cz + height_m / 2.0],
                color="#f0b64a",
                s=140,
                marker="^",
                edgecolors="white",
                linewidth=1.5,
                zorder=15,
            )
            ax.text(
                cx + 1.5,
                cy,
                cz + 2.5,
                "Pedestrian #265\n(0 LiDAR returns, seen by UAV at 17.5m)",
                color="#f0b64a",
                fontsize=10,
                fontweight="bold",
            )
        else:
            color = "#e67828" if flag == 1 else ("#2878dc" if flag == 2 else "#5dd39e")
            alpha = 0.5 if cat_id != 1 else 0.7
            ax.scatter([cx], [cy], [cz], color=color, s=25, alpha=alpha, depthshade=False)

    # Camera viewpoint angle
    ax.view_init(elev=34, azim=-62)
    ax.set_xlim(v_tx - 40, v_tx + 30)
    ax.set_ylim(v_ty - 35, v_ty + 35)
    ax.set_zlim(0, 32)

    # Turn off axes for clean poster look
    ax.set_axis_off()
    fig.subplots_adjust(left=0, right=1, bottom=0, top=1)

    out_png = PUBLIC_HERO_DIR / "poster.png"
    out_webp = PUBLIC_HERO_DIR / "poster.webp"
    fig.savefig(out_png, facecolor=fig.get_facecolor(), edgecolor="none", dpi=120)
    plt.close(fig)

    # Also convert to WebP
    with Image.open(out_png) as im:
        im.save(out_webp, "WEBP", quality=90)

    print("Generated poster images:")
    print(f"  {out_png}: {out_png.stat().st_size:,} bytes")
    print(f"  {out_webp}: {out_webp.stat().st_size:,} bytes")


@dataclass(frozen=True)
class HeroFrame:
    origin: tuple[float, float, float]
    lanes: list[tuple[int, np.ndarray]]
    sensing: list[tuple[np.ndarray, np.ndarray, tuple[int, int, int]]]  # pose xyz, points, colour
    drones: list[tuple[np.ndarray, tuple[int, int, int]]]
    ground: list[np.ndarray]


def _rgb(hex_colour: str) -> tuple[int, int, int]:
    v = hex_colour.lstrip("#")
    return int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16)


def read_frame(blob: bytes, frame_index: int) -> HeroFrame:
    """One frame of a ``CPCVCLIP`` or ``CPCVCLP2`` clip, in metres."""
    magic = blob[:8]
    if magic not in (b"CPCVCLIP", b"CPCVCLP2"):
        raise ValueError(f"not a hero clip: {magic!r}")
    v2 = magic == b"CPCVCLP2"
    off = 10
    ox, oy, oz = struct.unpack_from("<fff", blob, off)
    off += 12
    agents: list[tuple[int, tuple[int, int, int]]] = []  # kind, colour
    if v2:
        (n,) = struct.unpack_from("<B", blob, off)
        off += 1
        for _ in range(n):
            (k,) = struct.unpack_from("<B", blob, off)
            off += 1 + k
            kind, r, g, b = struct.unpack_from("<BBBB", blob, off)
            off += 4
            agents.append((kind, (r, g, b)))
    else:
        agents = [(0, VEHICLE), (1, DRONE)]
    n_poly, n_frames = struct.unpack_from("<II", blob, off)
    off += 8
    origin = np.array([ox, oy, oz])

    def cm(count: int) -> np.ndarray:
        nonlocal off
        raw = np.frombuffer(blob, dtype="<i2", count=count * 3, offset=off).reshape(-1, 3)
        off += count * 6
        return origin + raw.astype(np.float64) * 0.01

    lanes: list[tuple[int, np.ndarray]] = []
    for _ in range(n_poly):
        layer, nv = struct.unpack_from("<BH", blob, off)
        off += 3
        lanes.append((layer, cm(nv)))
    if not 0 <= frame_index < n_frames:
        raise ValueError(f"frame {frame_index} outside 0..{n_frames - 1}")
    for f in range(n_frames):
        off += 8
        poses: list[np.ndarray] = []
        points: list[np.ndarray] = []
        if v2:
            for _ in agents:
                off += 1
                poses.append(np.array(struct.unpack_from("<7f", blob, off)[:3], dtype=np.float64))
                off += 28
                (npts,) = struct.unpack_from("<I", blob, off)
                off += 4
                points.append(cm(npts))
        else:
            for _ in range(2):
                poses.append(np.array(struct.unpack_from("<7f", blob, off)[:3], dtype=np.float64))
                off += 28
            (npts,) = struct.unpack_from("<H", blob, off)
            off += 2
            points = [cm(npts), np.empty((0, 3))]
            (nboxes,) = struct.unpack_from("<H", blob, off)
            off += 2 + nboxes * 46
        if f == frame_index:
            sensing = [
                (poses[i], points[i], c) for i, (_, c) in enumerate(agents) if len(points[i])
            ]
            drones = [(poses[i], c) for i, (k, c) in enumerate(agents) if k == 1]
            ground = [poses[i] for i, (k, _) in enumerate(agents) if k != 1]
            return HeroFrame((ox, oy, oz), lanes, sensing, drones, ground)
    raise AssertionError("unreachable")


class _Camera:
    """three.js PerspectiveCamera with z up, looking at a target; projects to pixels."""

    def __init__(self, eye: np.ndarray, target: np.ndarray, width: int, height: int) -> None:
        forward = target - eye
        self.f = forward / np.linalg.norm(forward)
        right = np.cross(self.f, np.array([0.0, 0.0, 1.0]))
        self.r = right / np.linalg.norm(right)
        self.u = np.cross(self.r, self.f)
        self.eye = eye
        self.w, self.h = width, height
        self.focal = (height / 2) / np.tan(np.radians(FOV_DEG) / 2)

    def project(self, pts: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        d = pts - self.eye
        depth = d @ self.f
        x = self.w / 2 + (d @ self.r) / np.maximum(depth, 1e-6) * self.focal
        y = self.h / 2 - (d @ self.u) / np.maximum(depth, 1e-6) * self.focal
        return np.stack([x, y], axis=1), depth


def _splat(buf: np.ndarray, x: float, y: float, radius: float, rgb: np.ndarray) -> None:
    """Additive round sprite: intensity glow² with glow = exp(-16 |p|²), as POINT_FRAGMENT."""
    h, w, _ = buf.shape
    x0, x1 = int(np.floor(x - radius)), int(np.ceil(x + radius))
    y0, y1 = int(np.floor(y - radius)), int(np.ceil(y + radius))
    if x1 < 0 or y1 < 0 or x0 >= w or y0 >= h:
        return
    xs = np.arange(max(x0, 0), min(x1, w - 1) + 1)
    ys = np.arange(max(y0, 0), min(y1, h - 1) + 1)
    px = (xs[None, :] + 0.5 - x) / (2 * radius)
    py = (ys[:, None] + 0.5 - y) / (2 * radius)
    r2 = px**2 + py**2
    glow = np.exp(-r2 * 16.0) * (r2 <= 0.25)
    buf[ys[0] : ys[-1] + 1, xs[0] : xs[-1] + 1] += (1.15 * glow**2)[..., None] * rgb


def _drone_glow(buf: np.ndarray, x: float, y: float, radius: float, rgb: np.ndarray) -> None:
    """The drone sprite's radial gradient (glowTexture in RealClip.tsx), added."""
    h, w, _ = buf.shape
    r = int(np.ceil(radius))
    xs = np.arange(max(int(x) - r, 0), min(int(x) + r, w - 1) + 1)
    ys = np.arange(max(int(y) - r, 0), min(int(y) + r, h - 1) + 1)
    if not len(xs) or not len(ys):
        return
    t = np.sqrt((xs[None, :] + 0.5 - x) ** 2 + (ys[:, None] + 0.5 - y) ** 2) / radius
    stops = [0.0, 0.2, 0.42, 0.7, 1.0]
    white, pale = np.array([1.0, 1.0, 1.0]), np.array([190, 214, 255]) / 255
    colours = [white, pale, rgb, rgb, rgb]
    alphas = [1.0, 1.0, 0.95, 0.28, 0.0]
    out = np.zeros((len(ys), len(xs), 3))
    for i in range(len(stops) - 1):
        a, b = stops[i], stops[i + 1]
        m = (t >= a) & (t < b)
        k = ((t - a) / (b - a))[..., None]
        c = colours[i] * (1 - k) + colours[i + 1] * k
        al = alphas[i] * (1 - k) + alphas[i + 1] * k
        out += np.where(m[..., None], c * al, 0.0)
    buf[ys[0] : ys[-1] + 1, xs[0] : xs[-1] + 1] += out


def _font(path: Path, size: int, weight: int) -> ImageFont.FreeTypeFont:
    if not path.is_file():
        raise FileNotFoundError(f"{path} missing: run npm ci in site/")
    font = ImageFont.truetype(str(path), size)
    font.set_variation_by_axes([weight])
    return font


def render_hero(
    frame: HeroFrame, width: int, height: int, angle: float = ORBIT_START
) -> Image.Image:
    """The hero canvas for one frame, supersampled 2× then downsampled."""
    ss = 2
    w, h = width * ss, height * ss
    anchor_pts = [p for p, _, _ in frame.sensing] or frame.ground
    anchor = np.mean(np.stack(anchor_pts), axis=0)
    eye = np.array(
        [
            anchor[0] + np.cos(angle) * ORBIT_RADIUS,
            anchor[1] + np.sin(angle) * ORBIT_RADIUS,
            ORBIT_HEIGHT,
        ]
    )
    cam = _Camera(eye, np.array([anchor[0], anchor[1], ORBIT_LOOK_Z]), w, h)

    lines = Image.new("RGB", (w, h), (0, 0, 0))
    draw = ImageDraw.Draw(lines)
    for _, verts in frame.lanes:
        xy, depth = cam.project(verts)
        # RealClip draws every polyline in the lane colour (no vertex colours).
        colour = LANES
        for i in range(len(xy) - 1):
            if depth[i] > 0.5 and depth[i + 1] > 0.5:
                draw.line([tuple(xy[i]), tuple(xy[i + 1])], fill=colour, width=ss)
    for drone, colour in frame.drones:
        if not frame.ground:
            continue
        near = min(frame.ground, key=lambda g: float(np.hypot(*(g[:2] - drone[:2]))))
        ends, _ = cam.project(np.stack([drone, near + np.array([0, 0, 1.2])]))
        tether = tuple(int(round(0.22 * c)) for c in colour)
        draw.line([tuple(ends[0]), tuple(ends[1])], fill=tether, width=ss)

    buf = np.asarray(lines, dtype=np.float64) / 255.0
    near_colour = np.array([1.0, 0.98, 0.94])
    for pose, pts, colour in frame.sensing:
        xy, depth = cam.project(pts)
        far = np.array(colour) / 255.0
        dist = np.linalg.norm(pts - pose, axis=1)
        t = np.clip((dist - 2.0) / 10.0, 0.0, 1.0)
        t = t * t * (3 - 2 * t)
        for (x, y), z, tt in zip(xy, depth, t, strict=True):
            if z <= 0.5:
                continue
            size = float(np.clip(820.0 / z, 2.0, 22.0)) * ss
            _splat(buf, float(x), float(y), size / 2, near_colour * (1 - tt) + far * tt)
    for drone, colour in frame.drones:
        xy, depth = cam.project(drone[None, :])
        if depth[0] > 0.5:
            radius = 3.6 / 2 / depth[0] * cam.focal
            _drone_glow(buf, float(xy[0, 0]), float(xy[0, 1]), radius, np.array(colour) / 255)

    img = Image.fromarray((np.clip(buf, 0, 1) * 255 + 0.5).astype(np.uint8), "RGB")
    return img.resize((width, height), Image.Resampling.LANCZOS)


def render_og(out: Path | None = None, frame_index: int = 0) -> Path:
    """``og.png``: the hero at ``frame_index``, the wordmark and the headline."""
    width, height = 1200, 630
    frame = read_frame((PUBLIC_HERO_DIR / "clip.bin").read_bytes(), frame_index)
    img = render_hero(frame, width, height)

    # The page's bottom fade (from-black to-transparent over the lower 2/5) under the text.
    fade = np.zeros((height, 1), dtype=np.float64)
    start = int(height * 0.6)
    fade[start:, 0] = np.linspace(0.0, 1.0, height - start)
    pixels = np.asarray(img, dtype=np.float64) * (1 - fade[..., None])
    img = Image.fromarray((pixels + 0.5).astype(np.uint8), "RGB")

    draw = ImageDraw.Draw(img)
    mono = _font(GEIST_MONO, 40, 500)
    draw.text((56, 48), "coperceive", font=mono, fill=TEXT)
    right = draw.textbbox((56, 48), "coperceive", font=mono)[2]
    base = draw.textbbox((56, 48), "c", font=mono)[3]
    block_h, block_w = int(40 * 0.72), int(40 * 0.55)
    x0 = right + int(40 * 0.14)
    draw.rounded_rectangle((x0, base - block_h + 2, x0 + block_w, base + 2), radius=2, fill=CURSOR)

    headline = _font(GEIST, 76, 600)
    draw.text((56, height - 56), "Every agent. One view.", font=headline, fill=TEXT, anchor="ls")

    out = out or PUBLIC_DIR / "og.png"
    img.save(out, "PNG", optimize=True)
    return out


def render_touch_icon(out: Path | None = None) -> Path:
    """``apple-touch-icon.png``: the favicon mark (c and the red block) on opaque black."""
    size, ss = 180, 4
    img = Image.new("RGB", (size * ss, size * ss), (0, 0, 0))
    draw = ImageDraw.Draw(img)
    scale = size * ss / 32  # favicon.svg is a 32-unit square
    mono = _font(GEIST_MONO, int(16 * scale), 500)
    draw.text((6.2 * scale, 21.5 * scale), "c", font=mono, fill=TEXT, anchor="ls")
    draw.rounded_rectangle(
        (16.6 * scale, 17.6 * scale, 24.8 * scale, 20.2 * scale), radius=0.3 * scale, fill=CURSOR
    )
    img = img.resize((size, size), Image.Resampling.LANCZOS)
    out = out or PUBLIC_DIR / "apple-touch-icon.png"
    img.save(out, "PNG", optimize=True)
    return out


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    what = args[0] if args else ""
    if what == "og":
        path = render_og()
    elif what == "icon":
        path = render_touch_icon()
    elif what == "poster":
        render_poster()
        return 0
    else:
        print("usage: render_poster.py og|icon|poster", file=sys.stderr)
        return 2
    print(f"{path}: {path.stat().st_size:,} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
