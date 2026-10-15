# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json
from pathlib import Path

from coperceive.griffin.download import verify_md5
from coperceive.griffin.raw import load_scene, parse_label_line


def test_label_line_and_one_scene(tmp_path: Path) -> None:
    ann = parse_label_line(
        "car 1 2 0.5 4 1.8 1.5 0 0 90 7 0.5",
        agent_id="vehicle",
        time_us=100_000,
    )
    assert ann.track_id == "7"
    assert ann.category == "car"
    assert ann.visibility == 0.5

    frames = ["000000", "000001"]
    info = [
        {
            "name": "Town03-000",
            "info": {
                "map_name": "Carla/Maps/Town03",
                "frames": frames,
                "cruise_altitude": 25.0,
            },
        }
    ]
    for side, z in (("vehicle-side", 0.0), ("drone-side", 25.0)):
        root = tmp_path / side
        (root / "pose").mkdir(parents=True)
        (root / "label").mkdir()
        (root / "calib").mkdir()
        (root / "scene_infos.json").write_text(json.dumps(info), encoding="utf-8")
        for index, frame in enumerate(frames):
            pose = {
                "x": float(index),
                "y": 0.0,
                "z": z,
                "roll": 0.0,
                "pitch": 0.0,
                "yaw": 0.0,
                "velocity": 0.0,
                "timestamp": int(frame),
            }
            (root / "pose" / f"{frame}.json").write_text(json.dumps(pose), encoding="utf-8")
            (root / "label" / f"{frame}.txt").write_text(
                "car 0 0 0.5 4 2 1.5 0 0 0 7 1.0\n",
                encoding="utf-8",
            )
        extrinsic = [
            [1, 0, 0, 0.2],
            [0, 1, 0, 0.0],
            [0, 0, 1, 1.1],
            [0, 0, 0, 1],
        ]
        (root / "calib" / "front.json").write_text(
            json.dumps(
                {
                    "intrinsic": [[700, 0, 960], [0, 700, 540], [0, 0, 1]],
                    "distortion": [0, 0, 0, 0, 0],
                    "extrinsic": extrinsic,
                }
            ),
            encoding="utf-8",
        )
    lidar = tmp_path / "vehicle-side" / "lidar" / "lidar_top"
    lidar.mkdir(parents=True)
    (lidar / "000000.ply").write_text(
        "ply\nformat ascii 1.0\nelement vertex 1\n"
        "property float32 x\nproperty float32 y\nproperty float32 z\nproperty float32 I\n"
        "end_header\n1 0 0 0.5\n",
        encoding="utf-8",
    )
    scene = load_scene(tmp_path, "Town03-000")
    assert scene.scene_id == "Town03-000"
    assert {agent.agent_id for agent in scene.agents} == {"vehicle", "drone"}
    assert scene.objects[0].track_id == "7"
    assert scene.point_clouds[0].count == 1
    assert scene.clock.scale_note.startswith("Griffin pose")
    assert any(image.path.endswith("000000.png") for image in scene.images)


def test_md5_ignores_the_self_line(tmp_path: Path) -> None:
    payload = tmp_path / "drone_metadata.zip"
    payload.write_bytes(b"abc")
    digest = __import__("hashlib").md5(b"abc").hexdigest()
    (tmp_path / "md5.txt").write_text(
        f"{digest}  ./drone_metadata.zip\n00000000000000000000000000000000  ./md5.txt\n",
        encoding="utf-8",
    )
    verify_md5(tmp_path, ["drone_metadata.zip"])
