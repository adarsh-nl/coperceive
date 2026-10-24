#!/usr/bin/env python3
"""Render a deterministic fallback poster image for the coperceive hero section.

Creates a dark-mode isometric rendering of Town03-000 at t=1123700000 us (frame 52),
featuring the ground vehicle with cyan LiDAR sweep, aerial drone hovering above,
green lane lines, and the occluded pedestrian detected around the corner.

Outputs:
- site/public/hero/poster.png
- site/public/hero/poster.webp
"""

from __future__ import annotations

import json
import struct
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from PIL import Image

SCRIPT_DIR = Path(__file__).resolve().parent
PUBLIC_HERO_DIR = SCRIPT_DIR.parent.parent / "site" / "public" / "hero"


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


if __name__ == "__main__":
    render_poster()
