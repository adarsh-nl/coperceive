# SPDX-License-Identifier: Apache-2.0
"""A synthetic KITTI-flavoured cooperative dataset for the adapter docs and tests.

Layout, one directory per scene and one per agent:

    scene_0001/car_a/poses.csv                 frame,stamp_s,x,y,z,qx,qy,qz,qw
    scene_0001/car_a/calib/cam_front.yaml      translation, rotation (deg), intrinsic, image
    scene_0001/car_a/calib/velodyne.yaml       translation, rotation (deg)
    scene_0001/car_a/labels/000000.txt         type h w l x y z yaw track
    scene_0001/car_a/velodyne/000000.bin       float32 x y z intensity

Body axes are x right, y forward, z up (``RFU``). Boxes are bottom-centred.
The camera is OpenCV-style (``RDF``) and looks along body +y.
"""

from __future__ import annotations

import math
import struct
from pathlib import Path

MANIFEST = """\
adapter: toy-coop
spec_version: 1

world_frame: map

clock:
  from: field
  field: stamp_s
  unit: s
  rounding: nearest
  scale_note: "stamp_s is a float second count in poses.csv, rounded to the nearest microsecond."

scenes:
  glob:
    path: "{scene}/"
    sample_source: boxes

agents:
  discover: {glob: "{scene}/{agent}/", type: vehicle}

conventions:
  length_unit: m
  angle_unit: deg
  body_axes: RFU
  camera_axes: RDF

streams:
  poses:
    reader: csv_rows
    path: "{agent_root}/poses.csv"
    sample_field: frame
    translation: [x, y, z]
    rotation:
      quaternion: {fields: [qx, qy, qz, qw], order: xyzw}

  sensors:
    reader: yaml_record
    path: "{agent_root}/calib/{sensor}.yaml"
    extrinsic:
      translation: [translation.0, translation.1, translation.2]
      rotation:
        euler: {fields: [rotation.roll, rotation.pitch, rotation.yaw], order: zyx, frame: intrinsic}
      direction: child_to_parent
    intrinsic: {fields: [intrinsic.fx, intrinsic.fy, intrinsic.cx, intrinsic.cy], optional: true}
    image_size: {fields: [image.width, image.height]}
    modality:
      - {has_intrinsic: true, modality: camera}
      - {default: lidar}

  boxes:
    reader: text_rows
    path: "{agent_root}/labels/{frame:06d}.txt"
    frame: body
    centre_origin: bottom
    variants:
      9: [category, height, width, length, cx, cy, cz, yaw, track_id]
    rotation:
      yaw_only: {field: yaw, axis: z}
    class_map: {Car: car, Pedestrian: pedestrian}
    class_map_unknown: error

  images:
    for_each: camera
    path: "{agent_root}/image/{sensor}/{frame:06d}.png"
    require_file: false

  lidar:
    reader: bin_f32
    sensor: velodyne
    path: "{agent_root}/velodyne/{frame:06d}.bin"
    stride: 4
    offsets: {x: 0, y: 1, z: 2, intensity: 3}

checks:
  required_per_sample: [poses, boxes, lidar]
"""


def write_toy(root: Path, *, scenes: int = 2, frames: int = 3) -> None:
    for scene_index in range(1, scenes + 1):
        scene = root / f"scene_{scene_index:04d}"
        for agent_index, agent in enumerate(("car_a", "car_b")):
            base = scene / agent
            (base / "calib").mkdir(parents=True)
            (base / "labels").mkdir()
            (base / "velodyne").mkdir()
            rows = ["frame,stamp_s,x,y,z,qx,qy,qz,qw"]
            for frame in range(frames):
                # car_a yaws 90 degrees about z; car_b drives straight.
                yaw = math.pi / 2 if agent == "car_a" else 0.0
                qz, qw = math.sin(yaw / 2), math.cos(yaw / 2)
                x = 1.0 + frame + 10.0 * agent_index
                rows.append(f"{frame},{100.0 + 0.1 * frame:.1f},{x},2.0,0.0,0,0,{qz},{qw}")
                (base / "labels" / f"{frame:06d}.txt").write_text(
                    "Car 1.5 1.8 4.0 3.0 10.0 0.0 90 7\nPedestrian 1.7 0.6 0.6 -1.0 5.0 0.0 0 8\n",
                    encoding="utf-8",
                )
                (base / "velodyne" / f"{frame:06d}.bin").write_bytes(
                    struct.pack("<8f", 1.0, 2.0, 3.0, 0.5, 0.0, 10.0, 0.0, 0.25)
                )
            (base / "poses.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")
            (base / "calib" / "cam_front.yaml").write_text(
                "translation: [0.5, 1.0, 1.2]\n"
                "rotation: {roll: -90, pitch: 0, yaw: 0}\n"
                "intrinsic: {fx: 700, fy: 700, cx: 640, cy: 360}\n"
                "image: {width: 1280, height: 720}\n",
                encoding="utf-8",
            )
            (base / "calib" / "velodyne.yaml").write_text(
                "translation: [0.0, 0.0, 1.7]\nrotation: {roll: 0, pitch: 0, yaw: 0}\n",
                encoding="utf-8",
            )
