# SPDX-License-Identifier: Apache-2.0
"""A synthetic Griffin release tree for tests. No dataset bytes."""

from __future__ import annotations

import json
from pathlib import Path


def write_release(
    root: Path,
    scenes: dict[str, list[int]],
    *,
    lidar: bool = True,
    lidar_frames: set[int] | None = None,
    drop_pose: tuple[str, str, int] | None = None,
) -> None:
    infos = [
        {
            "name": name,
            "info": {
                "map_name": "Carla/Maps/Town03",
                "frames": [f"{frame:06d}" for frame in frames],
                "cruise_altitude": 25.5,
            },
        }
        for name, frames in scenes.items()
    ]
    extrinsic = [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]
    for side, height in (("vehicle-side", 0.0), ("drone-side", 30.0)):
        base = root / side
        (base / "pose").mkdir(parents=True)
        (base / "label").mkdir()
        (base / "calib").mkdir()
        (base / "scene_infos.json").write_text(json.dumps(infos), encoding="utf-8")
        (base / "calib" / "front.json").write_text(
            json.dumps(
                {
                    "intrinsic": [[10, 0, 2], [0, 10, 1], [0, 0, 1]],
                    "extrinsic": extrinsic,
                }
            ),
            encoding="utf-8",
        )
        if side == "vehicle-side":
            (base / "calib" / "lidar_top.json").write_text(
                json.dumps({"extrinsic": extrinsic}),
                encoding="utf-8",
            )
        for name, frames in scenes.items():
            for frame in frames:
                pose = {
                    "x": 1.0,
                    "y": 2.0,
                    "z": height,
                    "roll": 0.0,
                    "pitch": 0.0,
                    "yaw": 0.0,
                    "timestamp": frame,
                }
                if drop_pose != (side, name, frame):
                    (base / "pose" / f"{frame:06d}.json").write_text(
                        json.dumps(pose),
                        encoding="utf-8",
                    )
                (base / "label" / f"{frame:06d}.txt").write_text(
                    "car 0 0 0.5 4 2 1.5 0 0 0 7 1.0\n",
                    encoding="utf-8",
                )
    if not lidar:
        return
    lidar_dir = root / "vehicle-side" / "lidar" / "lidar_top"
    lidar_dir.mkdir(parents=True)
    for frames in scenes.values():
        for frame in frames:
            if lidar_frames is not None and frame not in lidar_frames:
                continue
            (lidar_dir / f"{frame:06d}.ply").write_text(
                "ply\nformat ascii 1.0\nelement vertex 1\n"
                "property float32 x\nproperty float32 y\nproperty float32 z\nproperty float32 I\n"
                "end_header\n1 0 0 0.25\n",
                encoding="utf-8",
            )
