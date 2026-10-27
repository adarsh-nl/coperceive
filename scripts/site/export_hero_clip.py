#!/usr/bin/env python3
"""Export a compact binary clip of one stored scene for the landing page hero.

Reads a scene written by ``coperceive.store.write_scene`` and, optionally, a
``coperceive maps ingest`` map directory, whose polylines are mapped into the
scene world with the stored ``world_from_opendrive``. Neither is committed.

Writes ``clip.bin`` in the ``CPCVCLP2`` format below and a ``manifest.json``
holding the same agent table. The page reads both this format and the older
``CPCVCLIP`` one (one vehicle sweep plus a drone pose per frame), which the
committed Griffin clip still uses.

``CPCVCLP2`` layout, little-endian:

* ``b"CPCVCLP2"``, ``u16`` version (2), ``f32 x3`` origin in metres
* ``u8`` agent count N (at least 2), then per agent: ``u8`` id length, the id in
  UTF-8, ``u8`` kind (0 vehicle, 1 drone, 2 infrastructure, 3 other),
  ``u8 x3`` colour RGB
* ``u32`` polyline count, ``u32`` frame count
* per polyline: ``u8`` layer (1 lane centre, 2 road edge), ``u16`` vertex count,
  then ``i16 x3`` per vertex, centimetres from the origin
* per frame: ``u64`` time in microseconds, then for each agent in table order:
  ``u8`` flags (bit 0: the pose is recorded at this time), ``f32 x7`` pose
  ``tx ty tz qx qy qz qw`` (identity when absent), ``u32`` point count, then
  ``i16 x3`` per point, centimetres from the origin, in the world frame
"""

from __future__ import annotations

import argparse
import array
import gzip
import json
import struct
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from coperceive.errors import CoperceiveError
from coperceive.hdmap.opendrive import LAYER_LANE_CENTRE, LAYER_ROAD_EDGE
from coperceive.hdmap.store import load_map
from coperceive.model import AgentType, Modality, Scene
from coperceive.store import read_scene
from coperceive.transform import lookup_pose
from coperceive.viewdata import frame_points, pose_times

MAGIC_V2 = b"CPCVCLP2"
VERSION_V2 = 2
INT16_MIN, INT16_MAX = -32768, 32767

KIND_CODES: dict[AgentType, int] = {
    AgentType.VEHICLE: 0,
    AgentType.UAV: 1,
    AgentType.INFRASTRUCTURE: 2,
    AgentType.OTHER: 3,
}
KIND_NAMES = ("vehicle", "drone", "infrastructure", "other")
MAP_LAYERS = frozenset({LAYER_LANE_CENTRE, LAYER_ROAD_EDGE})

# Page tokens (site/src/tokens.ts): vehicle, vehicleAlt, miss, hit for ground
# agents; drone first for aerial agents. Lanes (#7dcea0) is never used.
VEHICLE_PALETTE = ("#F58A1F", "#4CC9F0", "#8E6CEF", "#1FA3A3", "#E0A431")
DRONE_PALETTE = ("#3C8DFF", "#E0A431", "#8E6CEF")
OTHER_PALETTE = ("#E0A431", "#8E6CEF", "#1FA3A3")


@dataclass(frozen=True)
class ClipAgent:
    agent_id: str
    kind: int
    color: str


@dataclass(frozen=True)
class AgentFrame:
    present: bool
    pose: tuple[float, ...]  # tx ty tz qx qy qz qw
    points: list[tuple[float, float, float]]


@dataclass(frozen=True)
class ClipFrame:
    time_us: int
    agents: list[AgentFrame]


@dataclass(frozen=True)
class DecodedClip:
    origin: tuple[float, float, float]
    agents: list[dict[str, str]]
    polylines: list[tuple[int, list[tuple[float, float, float]]]]
    frames: list[ClipFrame]


@dataclass
class ExportStats:
    num_polylines: int
    num_frames: int
    total_points: int
    clamped: int
    file_size_bytes: int
    gzip_size_bytes: int


def _rgb(color: str) -> tuple[int, int, int]:
    value = color.lstrip("#")
    if len(value) != 6:
        raise ValueError(f"colour must be #rrggbb, got {color!r}")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def agent_table(
    scene: Scene, agent_ids: Sequence[str] | None, colors: dict[str, str]
) -> list[ClipAgent]:
    """Agents in the order given (default: scene order) with a kind and a colour each."""
    ids = list(agent_ids) if agent_ids else [agent.agent_id for agent in scene.agents]
    if len(ids) < 2:
        raise ValueError("a clip needs at least two agents")
    if len(ids) > 255:
        raise ValueError("a clip holds at most 255 agents")
    used = {"vehicle": 0, "drone": 0, "other": 0}
    table: list[ClipAgent] = []
    for agent_id in ids:
        agent = scene.agent(agent_id)
        kind = KIND_CODES[agent.agent_type]
        if kind == 0:
            group, palette = "vehicle", VEHICLE_PALETTE
        elif kind == 1:
            group, palette = "drone", DRONE_PALETTE
        else:
            group, palette = "other", OTHER_PALETTE
        color = colors.get(agent_id) or palette[used[group] % len(palette)]
        used[group] += 1
        _rgb(color)
        table.append(ClipAgent(agent_id, kind, color.upper()))
    return table


def _cm(value: float, origin: float) -> tuple[int, bool]:
    cm = int(round((value - origin) * 100))
    clamped = max(INT16_MIN, min(INT16_MAX, cm))
    return clamped, clamped != cm


def _floats(blob: bytes) -> array.array[float]:
    values = array.array("f")
    values.frombytes(blob)
    if sys.byteorder != "little":
        values.byteswap()
    return values


def _map_polylines(
    map_dir: Path, origin: tuple[float, float, float], radius_m: float
) -> list[tuple[int, list[tuple[float, float, float]]]]:
    """Lane centres and road edges in the scene world with a vertex within ``radius_m``."""
    selected: list[tuple[int, list[tuple[float, float, float]]]] = []
    for polyline in load_map(map_dir).world_polylines(MAP_LAYERS):
        verts = list(polyline.xyz)
        if any(
            abs(x - origin[0]) <= radius_m and abs(y - origin[1]) <= radius_m for x, y, _ in verts
        ):
            selected.append((1 if polyline.layer == LAYER_LANE_CENTRE else 2, verts))
    return selected


def _agent_points(scene: Scene, time_us: int, agent_id: str) -> list[tuple[float, float, float]]:
    points: list[tuple[float, float, float]] = []
    for sensor in scene.sensors:
        if sensor.agent_id != agent_id or sensor.modality is not Modality.LIDAR:
            continue
        values = _floats(frame_points(scene, time_us, agent_id, sensor.sensor_id))
        for k in range(len(values) // 3):
            points.append((values[k * 3], values[k * 3 + 1], values[k * 3 + 2]))
    return points


def _subsample(
    points: list[tuple[float, float, float]], limit: int
) -> list[tuple[float, float, float]]:
    if len(points) <= limit:
        return points
    step = len(points) / float(limit)
    return [points[int(i * step)] for i in range(limit)]


def default_window(scene: Scene, start_us: int | None, num_frames: int, step_us: int) -> list[int]:
    times = pose_times(scene)
    if not times:
        raise ValueError("the scene has no poses")
    start = times[0] if start_us is None else start_us
    end = times[-1]
    window = [start + i * step_us for i in range(num_frames) if start + i * step_us <= end]
    if not window:
        raise ValueError(f"start {start} us is after the last pose at {end} us")
    return window


def default_origin(
    scene: Scene, agents: Sequence[ClipAgent], time_us: int
) -> tuple[float, float, float]:
    """Centroid of the agents' positions at ``time_us``, rounded to the metre, z = 0."""
    xs: list[float] = []
    ys: list[float] = []
    for entry in agents:
        try:
            pose = lookup_pose(scene.agent(entry.agent_id), time_us, max_gap_us=0)
        except CoperceiveError:
            continue
        xs.append(pose.tx)
        ys.append(pose.ty)
    if not xs:
        raise ValueError(f"no agent has a pose at {time_us} us; pass --origin")
    return float(round(sum(xs) / len(xs))), float(round(sum(ys) / len(ys))), 0.0


def encode_clip(
    scene: Scene,
    agents: Sequence[ClipAgent],
    times: Sequence[int],
    origin: tuple[float, float, float],
    polylines: Sequence[tuple[int, list[tuple[float, float, float]]]],
    points_per_frame: int,
) -> tuple[bytes, int, int]:
    """``CPCVCLP2`` bytes, the number of points written and the number clamped to int16."""
    ox, oy, oz = origin
    buf = bytearray(MAGIC_V2)
    buf += struct.pack("<Hfff", VERSION_V2, ox, oy, oz)
    buf += struct.pack("<B", len(agents))
    for entry in agents:
        name = entry.agent_id.encode("utf-8")
        if len(name) > 255:
            raise ValueError(f"agent id longer than 255 bytes: {entry.agent_id!r}")
        buf += struct.pack("<B", len(name)) + name
        buf += struct.pack("<BBBB", entry.kind, *_rgb(entry.color))
    buf += struct.pack("<II", len(polylines), len(times))

    clamped = 0
    for layer_id, verts in polylines:
        buf += struct.pack("<BH", layer_id, len(verts))
        for x, y, z in verts:
            (dx, cx), (dy, cy), (dz, cz) = _cm(x, ox), _cm(y, oy), _cm(z, oz)
            clamped += cx + cy + cz
            buf += struct.pack("<hhh", dx, dy, dz)

    sweeps = {
        entry.agent_id: any(
            s.agent_id == entry.agent_id and s.modality is Modality.LIDAR for s in scene.sensors
        )
        for entry in agents
    }
    with_points = max(1, sum(sweeps.values()))
    per_agent = max(1, points_per_frame // with_points)

    total = 0
    for time_us in times:
        buf += struct.pack("<Q", time_us)
        for entry in agents:
            try:
                pose = lookup_pose(scene.agent(entry.agent_id), time_us, max_gap_us=0)
                flags = 1
                values = (pose.tx, pose.ty, pose.tz, pose.qx, pose.qy, pose.qz, pose.qw)
            except CoperceiveError:
                flags = 0
                values = (0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0)
            buf += struct.pack("<B7f", flags, *values)
            pts: list[tuple[float, float, float]] = []
            if flags and sweeps[entry.agent_id]:
                pts = _subsample(_agent_points(scene, time_us, entry.agent_id), per_agent)
            buf += struct.pack("<I", len(pts))
            for x, y, z in pts:
                (dx, cx), (dy, cy), (dz, cz) = _cm(x, ox), _cm(y, oy), _cm(z, oz)
                clamped += cx + cy + cz
                buf += struct.pack("<hhh", dx, dy, dz)
            total += len(pts)
    return bytes(buf), total, clamped


def decode_clip(data: bytes) -> DecodedClip:
    """Read ``CPCVCLP2`` bytes back. Positions are metres in the world frame."""
    if data[:8] != MAGIC_V2:
        raise ValueError(f"not a CPCVCLP2 clip: {data[:8]!r}")
    off = 8
    version, ox, oy, oz = struct.unpack_from("<Hfff", data, off)
    off += 14
    if version != VERSION_V2:
        raise ValueError(f"unsupported clip version {version}")
    (n_agents,) = struct.unpack_from("<B", data, off)
    off += 1
    agents: list[dict[str, str]] = []
    for _ in range(n_agents):
        (n,) = struct.unpack_from("<B", data, off)
        off += 1
        agent_id = data[off : off + n].decode("utf-8")
        off += n
        kind, r, g, b = struct.unpack_from("<BBBB", data, off)
        off += 4
        color = f"#{r:02X}{g:02X}{b:02X}"
        agents.append({"id": agent_id, "kind": KIND_NAMES[kind], "color": color})
    n_poly, n_frames = struct.unpack_from("<II", data, off)
    off += 8
    polylines: list[tuple[int, list[tuple[float, float, float]]]] = []
    for _ in range(n_poly):
        layer, nv = struct.unpack_from("<BH", data, off)
        off += 3
        verts: list[tuple[float, float, float]] = []
        for _ in range(nv):
            dx, dy, dz = struct.unpack_from("<hhh", data, off)
            off += 6
            verts.append((ox + dx / 100.0, oy + dy / 100.0, oz + dz / 100.0))
        polylines.append((layer, verts))
    frames: list[ClipFrame] = []
    for _ in range(n_frames):
        (time_us,) = struct.unpack_from("<Q", data, off)
        off += 8
        per_agent: list[AgentFrame] = []
        for _ in range(n_agents):
            flags, *pose = struct.unpack_from("<B7f", data, off)
            off += 29
            (npts,) = struct.unpack_from("<I", data, off)
            off += 4
            pts: list[tuple[float, float, float]] = []
            for _ in range(npts):
                dx, dy, dz = struct.unpack_from("<hhh", data, off)
                off += 6
                pts.append((ox + dx / 100.0, oy + dy / 100.0, oz + dz / 100.0))
            per_agent.append(AgentFrame(bool(flags & 1), tuple(pose), pts))
        frames.append(ClipFrame(time_us, per_agent))
    if off != len(data):
        raise ValueError(f"{len(data) - off} trailing bytes after the last frame")
    return DecodedClip((ox, oy, oz), agents, polylines, frames)


def export_hero(
    scene_dir: Path,
    out_dir: Path,
    *,
    map_dir: Path | None = None,
    agent_ids: Sequence[str] | None = None,
    colors: dict[str, str] | None = None,
    origin: tuple[float, float, float] | None = None,
    start_us: int | None = None,
    num_frames: int = 100,
    step_us: int = 100_000,
    map_radius_m: float = 60.0,
    points_per_frame: int = 2400,
) -> ExportStats:
    """Write ``clip.bin`` and ``manifest.json`` into ``out_dir``."""
    scene = read_scene(scene_dir)
    agents = agent_table(scene, agent_ids, colors or {})
    times = default_window(scene, start_us, num_frames, step_us)
    if origin is None:
        origin = default_origin(scene, agents, times[0])
    polylines = _map_polylines(map_dir, origin, map_radius_m) if map_dir else []
    data, total, clamped = encode_clip(scene, agents, times, origin, polylines, points_per_frame)

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "clip.bin").write_bytes(data)
    gz = len(gzip.compress(data, compresslevel=9))
    stats = ExportStats(len(polylines), len(times), total, clamped, len(data), gz)
    # No dataset, scene or map names: the manifest is served with the page.
    manifest = {
        "format": "CPCVCLP2",
        "origin": list(origin),
        "time_start_us": times[0],
        "time_step_us": step_us,
        "num_frames": len(times),
        "duration_s": round(len(times) * step_us / 1e6, 2),
        "fps": round(1e6 / step_us, 3),
        "agents": [
            {"id": a.agent_id, "kind": KIND_NAMES[a.kind], "color": a.color} for a in agents
        ],
        "stats": {
            "num_polylines": stats.num_polylines,
            "num_frames": stats.num_frames,
            "total_points": stats.total_points,
            "file_size_bytes": stats.file_size_bytes,
            "gzip_size_bytes": stats.gzip_size_bytes,
        },
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return stats


def _color_arg(text: str) -> tuple[str, str]:
    agent_id, sep, color = text.partition("=")
    if not sep:
        raise argparse.ArgumentTypeError("expected AGENT=#rrggbb")
    _rgb(color)
    return agent_id, color


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--scene", type=Path, required=True, help="stored scene directory")
    parser.add_argument("--out", type=Path, required=True, help="directory for clip.bin")
    parser.add_argument("--map", type=Path, help="maps ingest directory (optional)")
    parser.add_argument("--agents", nargs="+", help="agent ids in legend order (default: all)")
    parser.add_argument("--color", type=_color_arg, action="append", default=[])
    parser.add_argument("--origin", type=float, nargs=3, metavar=("X", "Y", "Z"))
    parser.add_argument("--start-us", type=int, help="first frame time (default: first pose)")
    parser.add_argument("--frames", type=int, default=100)
    parser.add_argument("--step-us", type=int, default=100_000)
    parser.add_argument("--map-radius", type=float, default=60.0, help="metres around origin")
    parser.add_argument(
        "--points-per-frame", type=int, default=2400, help="split across agents with lidar"
    )
    args = parser.parse_args(argv)
    stats = export_hero(
        args.scene,
        args.out,
        map_dir=args.map,
        agent_ids=args.agents,
        colors=dict(args.color),
        origin=tuple(args.origin) if args.origin else None,
        start_us=args.start_us,
        num_frames=args.frames,
        step_us=args.step_us,
        map_radius_m=args.map_radius,
        points_per_frame=args.points_per_frame,
    )
    print(
        f"clip.bin {stats.file_size_bytes:,} bytes, gzip {stats.gzip_size_bytes:,} bytes; "
        f"{stats.num_frames} frames, {stats.num_polylines} polylines, "
        f"{stats.total_points:,} points, {stats.clamped} coordinates clamped to int16"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
