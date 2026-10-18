# SPDX-License-Identifier: Apache-2.0
"""A synthetic OpenCOOD-style release (OPV2V / V2XSet layout) for tests. No dataset bytes.

Layout, as the real releases: ``<split>/<scene>/<agent>/NNNNNN.yaml`` with the lidar
pose, four cameras and the boxes that agent sees, ``NNNNNN.pcd`` (ASCII, fields
``x y z rgb``), ``NNNNNN_cameraK.png``, and ``<split>/<scene>/data_protocol.yaml``.

Everything is written in the source convention: a left-handed CARLA world (x forward,
y right, z up), angles in degrees, rotations composed as ``Rz(yaw) Ry(pitch) Rx(roll)``
in that left-handed frame, ``extent`` half sizes, ``center`` an offset from the actor's
``location`` to the box centre in the box's own axes. The point cloud of every agent
holds points inside every box that agent lists, expressed in the agent's lidar frame,
so a test can check that the converted boxes still contain the converted points.
"""

from __future__ import annotations

import base64
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import yaml

PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
FX = 335.639852470912
CX, CY = 400.0, 300.0
CAMERAS = ("camera0", "camera1", "camera2", "camera3")
# Camera-to-lidar extrinsics (rotation, translation) in the source lidar frame.
CAMERA_OFFSETS: dict[str, tuple[float, tuple[float, float, float]]] = {
    "camera0": (0.0, (-3.0, 0.0, 0.9)),
    "camera1": (100.0, (-0.21, 0.54, 0.1)),
    "camera2": (-100.0, (-0.21, -0.54, 0.1)),
    "camera3": (180.0, (2.5, 0.0, 0.3)),
}

Vec3 = tuple[float, float, float]


@dataclass(frozen=True)
class Box:
    track_id: int
    location: Vec3
    angle: Vec3 = (0.0, 0.0, 0.0)  # roll, yaw, pitch, degrees
    center: Vec3 = (0.0, 0.0, 0.75)
    extent: Vec3 = (2.4, 1.0, 0.75)
    speed: float = 0.0


@dataclass(frozen=True)
class AgentTrack:
    agent_id: str
    poses: Sequence[tuple[Vec3, Vec3]]  # (x, y, z), (roll, yaw, pitch) per frame
    boxes: Sequence[Box] = ()
    cameras: bool = True
    ego_speed: float = 5.0


@dataclass(frozen=True)
class SceneSpec:
    name: str
    agents: Sequence[AgentTrack]
    stems: Sequence[int] = (69, 71, 73)
    protocol: Mapping[str, object] = field(
        default_factory=lambda: {"world": {"fixed_delta_seconds": 0.05, "sync_mode": True}}
    )


def rotation(roll: float, yaw: float, pitch: float) -> list[list[float]]:
    """``Rz(yaw) Ry(pitch) Rx(roll)`` with degrees, the UE composition used as-is."""
    r, p, w = (math.radians(v) for v in (roll, pitch, yaw))
    cy, sy, cp, sp, cr, sr = math.cos(w), math.sin(w), math.cos(p), math.sin(p), math.cos(r), math.sin(r)
    rz = [[cy, -sy, 0.0], [sy, cy, 0.0], [0.0, 0.0, 1.0]]
    ry = [[cp, 0.0, sp], [0.0, 1.0, 0.0], [-sp, 0.0, cp]]
    rx = [[1.0, 0.0, 0.0], [0.0, cr, -sr], [0.0, sr, cr]]
    return _mul(_mul(rz, ry), rx)


def _mul(a: list[list[float]], b: list[list[float]]) -> list[list[float]]:
    return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def _apply(m: list[list[float]], v: Vec3) -> Vec3:
    return (
        m[0][0] * v[0] + m[0][1] * v[1] + m[0][2] * v[2],
        m[1][0] * v[0] + m[1][1] * v[1] + m[1][2] * v[2],
        m[2][0] * v[0] + m[2][1] * v[1] + m[2][2] * v[2],
    )


def _transpose(m: list[list[float]]) -> list[list[float]]:
    return [[m[j][i] for j in range(3)] for i in range(3)]


def box_centre_world(box: Box) -> Vec3:
    """``location + R(angle) · center`` in the source world."""
    r = rotation(*box.angle)
    off = _apply(r, box.center)
    return (box.location[0] + off[0], box.location[1] + off[1], box.location[2] + off[2])


def box_points_world(box: Box) -> list[Vec3]:
    """Eight points well inside the box plus its centre, in the source world."""
    r = rotation(*box.angle)
    c = box_centre_world(box)
    out: list[Vec3] = [c]
    for sx in (-0.5, 0.5):
        for sy in (-0.5, 0.5):
            for sz in (-0.5, 0.5):
                local = (sx * box.extent[0], sy * box.extent[1], sz * box.extent[2])
                d = _apply(r, local)
                out.append((c[0] + d[0], c[1] + d[1], c[2] + d[2]))
    return out


def world_to_lidar(pose: tuple[Vec3, Vec3], point: Vec3) -> Vec3:
    t, (roll, yaw, pitch) = pose
    rt = _transpose(rotation(roll, yaw, pitch))
    return _apply(rt, (point[0] - t[0], point[1] - t[1], point[2] - t[2]))


def _camera_block(name: str, pose: tuple[Vec3, Vec3]) -> dict[str, object]:
    yaw_off, (dx, dy, dz) = CAMERA_OFFSETS[name]
    r = rotation(0.0, yaw_off, 0.0)
    extrinsic = [
        [r[0][0], r[0][1], r[0][2], dx],
        [r[1][0], r[1][1], r[1][2], dy],
        [r[2][0], r[2][1], r[2][2], dz],
        [0.0, 0.0, 0.0, 1.0],
    ]
    t, (roll, yaw, pitch) = pose
    world = _apply(rotation(roll, yaw, pitch), (dx, dy, dz))
    cords = [t[0] + world[0], t[1] + world[1], t[2] + world[2], roll, yaw + yaw_off, pitch]
    return {
        "cords": cords,
        "extrinsic": extrinsic,
        "intrinsic": [[FX, 0.0, CX], [0.0, FX, CY], [0.0, 0.0, 1.0]],
    }


def _pcd(points: Sequence[Vec3]) -> str:
    head = (
        "# .PCD v0.7 - Point Cloud Data file format\nVERSION 0.7\nFIELDS x y z rgb\n"
        "SIZE 4 4 4 4\nTYPE F F F F\nCOUNT 1 1 1 1\n"
        f"WIDTH {len(points)}\nHEIGHT 1\nVIEWPOINT 0 0 0 1 0 0 0\nPOINTS {len(points)}\nDATA ascii\n"
    )
    body = "".join(f"{x:.6f} {y:.6f} {z:.6f} 0\n" for x, y, z in points)
    return head + body


def write_release(root: Path, split: str, scenes: Sequence[SceneSpec]) -> None:
    for scene in scenes:
        base = root / split / scene.name
        base.mkdir(parents=True, exist_ok=True)
        (base / "data_protocol.yaml").write_text(
            yaml.safe_dump(dict(scene.protocol), sort_keys=True), encoding="utf-8"
        )
        for agent in scene.agents:
            folder = base / agent.agent_id
            folder.mkdir(exist_ok=True)
            for index, stem in enumerate(scene.stems):
                if index >= len(agent.poses):
                    break
                pose = agent.poses[index]
                t, (roll, yaw, pitch) = pose
                record: dict[str, object] = {}
                if agent.cameras:
                    for name in CAMERAS:
                        record[name] = _camera_block(name, pose)
                record["ego_speed"] = agent.ego_speed
                record["lidar_pose"] = [t[0], t[1], t[2], roll, yaw, pitch]
                record["true_ego_pos"] = [t[0], t[1], t[2] - 1.9, roll, yaw, pitch]
                record["vehicles"] = {
                    box.track_id: {
                        "angle": list(box.angle),
                        "center": list(box.center),
                        "extent": list(box.extent),
                        "location": list(box.location),
                        "speed": box.speed,
                    }
                    for box in agent.boxes
                }
                (folder / f"{stem:06d}.yaml").write_text(
                    yaml.safe_dump(record, sort_keys=True), encoding="utf-8"
                )
                points = [
                    world_to_lidar(pose, p) for box in agent.boxes for p in box_points_world(box)
                ]
                points.append((0.0, 0.0, -1.9))  # the ground under the sensor
                (folder / f"{stem:06d}.pcd").write_text(_pcd(points), encoding="utf-8")
                if agent.cameras:
                    for name in CAMERAS:
                        (folder / f"{stem:06d}_{name}.png").write_bytes(PNG_1X1)


def two_cav_scene(name: str = "2021_08_16_22_26_54", *, rsu: bool = False) -> SceneSpec:
    """Two CAVs driving along source +y (yaw 90) and, optionally, an RSU at a pole."""
    cav_a = AgentTrack(
        "641",
        poses=[((10.0, 20.0 + 2.0 * i, 1.9), (0.0, 90.0, 0.0)) for i in range(3)],
        boxes=[
            Box(650, location=(10.0, 40.0, 0.0), angle=(0.0, 90.0, 0.0)),
            Box(677, location=(16.0, 30.0, 0.0), angle=(0.0, 0.0, 0.0), center=(1.0, 0.0, 0.75)),
        ],
    )
    cav_b = AgentTrack(
        "650",
        poses=[((10.0, 40.0 + 2.0 * i, 1.9), (0.0, 90.0, 0.0)) for i in range(3)],
        boxes=[
            Box(641, location=(10.0, 20.0, 0.0), angle=(0.0, 90.0, 0.0)),
            Box(677, location=(16.0, 30.0, 0.0), angle=(0.0, 0.0, 0.0), center=(1.0, 0.0, 0.75)),
            Box(689, location=(30.0, 50.0, 0.0), angle=(5.0, 90.0, 2.0)),
        ],
    )
    agents = [cav_a, cav_b]
    if rsu:
        agents.append(
            AgentTrack(
                "-1",
                poses=[((0.0, 30.0, 4.3), (0.0, 0.0, 0.0))] * 3,
                boxes=[Box(641, location=(10.0, 20.0, 0.0), angle=(0.0, 90.0, 0.0))],
                ego_speed=0.0,
            )
        )
    return SceneSpec(name, agents)
