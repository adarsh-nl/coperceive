# SPDX-License-Identifier: Apache-2.0
# pyright: reportUnknownMemberType=false
"""The synthetic world written as DAIR-V2X-C with the complemented annotations.

This file was written from the dataset's public description alone: the DAIR-V2X
repository's ``data/README.md``, ``data/README_zh-CN.md``, ``docs/get_started.md``,
``docs/apis/dataloaders.md`` and ``tools/dataset_converter/README.md``, the paper
(arXiv 2204.05575), the dataset page (air.tsinghua.edu.cn/DAIR-V2X/english/cheluduan.html),
the release page of the complemented annotations
(siheng-chen.github.io/dataset/dair-v2x-c-complemented/), the official split file
``data/split_datas/cooperative-split-data.json`` and the PCL page on the PCD format.
Where those documents are silent, a claim taken from the official toolkit's code
says so ("code:"); one conversion script (``tools/dataset_converter/label_world2v.py``)
was opened to settle how ``data_info.json`` paths are joined, because no document
says. The adapter that reads the format, its tests and fixtures were not opened.

Where the sources are silent or disagree, :func:`export` takes a keyword. Its
default is the reading the sources support best; :data:`READINGS` lists every
keyword, its values and the sentences behind it. Every reading is unverified
against real frames: none were downloaded.

What is written under ``root`` (the directory handed to the adapter)::

    cooperative-vehicle-infrastructure/
        vehicle-side/
            image/{vid}.jpg
            velodyne/{vid}.pcd                      vehicle LiDAR frame
            calib/camera_intrinsic/{vid}.json       cam_K, cam_D
            calib/lidar_to_camera/{vid}.json
            calib/lidar_to_novatel/{vid}.json       {"transform": {rotation, translation}}
            calib/novatel_to_world/{vid}.json
            label/camera/{vid}.json
            label/lidar/{vid}.json                  the complemented labels (release step 1)
            data_info.json
        infrastructure-side/
            image/{iid}.jpg
            velodyne/{iid}.pcd                      virtual LiDAR frame
            calib/camera_intrinsic/{iid}.json       or calib/camera/ (infra_intrinsic_dir)
            calib/virtuallidar_to_world/{iid}.json
            calib/virtuallidar_to_camera/{iid}.json
            label/camera/{iid}.json
            label/virtuallidar/{iid}.json
            data_info.json
        cooperative/
            label_world/{vid}.json                  the complemented labels (release step 2)
            data_info.json
        train.json, val.json                        release step 3
    DAIR-V2X-C_Complemented_Anno/
        train.json, val.json                        vehicle frame ids of the split
        new_labels/vehicle-side_label/lidar/{vid}.json
        new_labels/cooperative_label/label_world/{vid}.json

``{vid}`` and ``{iid}`` are six-digit frame ids, numbered independently on the two
sides; all frames are one batch, and the batch id is the ``scene_id`` returned.

What DAIR-V2X-C cannot hold, and is therefore not written and not in the returned
world or expectation:

* **agents**: one vehicle and one infrastructure side per cooperative frame. The
  first ``vehicle`` and the first ``infrastructure`` agent of the world are
  written; the other vehicle and the aerial agent are left out.
* **cameras**: one image per side and frame. The vehicle keeps its ``front``
  camera (or its first), the roadside unit its first. Both are written at
  1920 × 1080, the documented resolution, with the horizontal field of view kept.
* **the tilt of the roadside LiDAR**: the release holds the infrastructure cloud
  in a levelled "virtual LiDAR" frame only. The returned world's roadside LiDAR is
  that levelled frame (same origin, heading of the physical LiDAR's x axis).
* **the tilt of the vehicle LiDAR** (reading ``vehicle_lidar``): a box is a yaw
  about the label frame's z, so an upright box can only be written in a level
  frame. By default the vehicle LiDAR is mounted level on the (still tilted) body.
* **objects with roll or pitch**, and **classes outside Car, Truck, Van and Bus**:
  every label file of the export lists the same objects in the same order (see
  ``object_ids``), so an object is written only if every file can hold it. A
  single-view box has no roll or pitch, and a cooperative file has four classes.
  Such objects still return LiDAR points; they are in no label file.
* **identities**: the documented label files carry no object id (``object_ids``).

It imports nothing from ``coperceive``.
"""

from __future__ import annotations

import colorsys
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from tests.geomworld.expectation import Expectation, Export
from tests.geomworld.world import Agent, Array, Camera, CameraTruth, Lidar, Pose, World, rot_z

ADAPTER = "dair-v2x-c"
# data/README.md, "Data Structure": "cooperative-vehicle-infrastructure # DAIR-V2X-C",
# with "infrastructure-side", "vehicle-side" and "cooperative" below it.
RELEASE = "cooperative-vehicle-infrastructure"
VEHICLE_SIDE = "vehicle-side"
INFRA_SIDE = "infrastructure-side"
COOPERATIVE = "cooperative"
# The folder of the complemented annotations, as the interface of the reader names it.
# Release page, "Installation": the download holds ``new_labels/vehicle-side_label/lidar``,
# ``new_labels/cooperative_label/label_world`` and the split files ``train.json`` /
# ``val.json``. That ``new_labels/`` sits directly in this folder is unverified (the
# page shows the archive's tree as an image only); the same labels are also installed
# in the release tree, as steps 1 and 2 of the page do.
COMPLEMENTED = "DAIR-V2X-C_Complemented_Anno"
SPLITS = ("train", "val")

# data/split_datas/cooperative-split-data.json, "batch_split": batch ids are numeric
# strings without padding ("0", "15", "119"). The value itself is arbitrary.
BATCH_ID = "3"
# "intersection_loc": "name of the intersection" (docs/apis/dataloaders.md); any name.
INTERSECTION = "geomworld"
# The first frame id of each side. README_zh-CN, 目录结构: "车端id与路端id非一一对应"
# (vehicle ids and infrastructure ids do not correspond one to one), so the two sides
# are numbered apart on purpose. Six digits: code, v2x/dataset/dataset_utils/frame.py
# takes the id as ``path[-10:-4]`` and ``id_to_str`` pads to ``digits=6``.
VEHICLE_FIRST_ID = 1200
INFRA_FIRST_ID = 7400
# Frame 0 is at this many microseconds. The epoch of the timestamps is not documented.
EPOCH_US = 1_600_000_000_000_000

# Paper, Table 2: both cameras "RGB, ... 1920x1080 resolution, JPEG compressed".
IMAGE_WIDTH = 1920
IMAGE_HEIGHT = 1080

# Every keyword of :func:`export` that selects a reading, with the values it takes.
# The first value is the default: the reading the sources support best.
READINGS: Mapping[str, tuple[str, ...]] = {
    # Frame ids. The release pairs a vehicle frame with an infrastructure frame through
    # cooperative/data_info.json, so the two sides' ids are independent and any pair is
    # valid: ``independent`` writes different numbers on each side, ``shared`` the same
    # number. A layout choice, not a geometric one. (Added by the conformance suite's
    # author, who has read the adapter: it pairs frames only when the ids are equal.)
    "frame_ids": ("independent", "shared"),
    # The base of the paths inside a side's data_info.json. The documents list the keys
    # (image_path, pointcloud_path, calib_*_path) but no example value, so it is
    # unverified whether they start at the side folder ("image/000000.jpg") or at the
    # release root ("vehicle-side/image/000000.jpg"). (Added by the suite's author.)
    "info_paths": ("side", "release"),
    # lidar_to_novatel: the official code reads lidar2novatel["transform"]; whether the
    # file always nests it is not documented. ``top_level`` writes rotation and
    # translation at the top, like every other calib file. (Added by the suite's author.)
    "novatel_nesting": ("transform", "top_level"),
    # The single-view ``rotation`` of a box. The sources disagree (all unverified):
    # README_zh-CN, 单侧标注格式: "3D bounding box绕中心点z轴正方向为旋转轴，从y轴正方向开始
    # 旋转的角度" (about the box's +z, starting from the +y axis): ``y_axis``.
    # Dataset page, §3 "3D box": "(rotation_y) ... rotates about the y-axis in Virtual
    # LiDAR Coordinate System": read as KITTI's camera rotation_y, ``kitti_rotation_y``
    # (-yaw - π/2). Code, tools/dataset_converter/utils.py get_label_lidar_rotation:
    # "偏航角yaw绕z轴与x轴夹角，方向符合右手规则" (yaw about z from the x axis, right-hand
    # rule), corners rotated by [[cos, -sin], [sin, cos]] with l along x: ``x_axis``.
    # The default is the code's reading, which tools/dataset_converter/README.md also
    # draws ("forward direction: 3 -> 0" with corners 0 and 3 at +l/2 and -l/2).
    "rotation_zero": ("x_axis", "y_axis", "kitti_rotation_y"),
    # No document gives the unit of ``rotation``. The same file's alpha is "从[-pi, pi]
    # 中取值" (README_zh-CN) and code (utils.py) documents "rotation_z (-pi,pi) rad".
    "rotation_unit": ("rad", "deg"),
    # The corner order of ``world_8_points``: data/README.md says only "8 corners of 3d
    # bounding box". tools/dataset_converter/README.md documents for corners made from
    # a 7-value label: 0-3 bottom, 4-7 top, 0=(+l/2,+w/2), 1=(+l/2,-w/2),
    # 2=(-l/2,-w/2), 3=(-l/2,+w/2), "forward direction: 3 -> 0": ``dair_lidar``. The
    # loader uses stored and generated corners alike (code, label.py), but nothing
    # fixes which end is the front (cite-check: only modulo 180°): ``turned_pi`` is
    # the same order for the box turned by π. ``reversed_ring`` is the permutation
    # [3, 2, 1, 0, 7, 6, 5, 4] the evaluation applies (code, eval_utils.py perm_label).
    "corner_order": ("dair_lidar", "turned_pi", "reversed_ring"),
    # Not a reading of the source: how the exporter makes an upright box writable.
    # Labels are "in Vehicle LiDAR Coordinate System" (data/README.md) and a box is "x,
    # y, z, width, length, height, and yaw angle" (paper §3.2). ``level``: the vehicle
    # LiDAR is mounted level on the tilted body, so every upright object is a yaw in
    # its frame. ``as_world``: the world's mount is kept; the vehicle's label files
    # then list the objects only if each is a pure yaw in its frame (none here).
    "vehicle_lidar": ("level", "as_world"),
    # Paper §4.1: "after we provide the tracking ID in future work"; no id field is
    # documented. ``list_index``: nothing is added, an object is named by its 0-based
    # position in a file, and every file lists the same objects in the same order.
    # ``track_id_key``: each entry also carries an undocumented ``track_id`` key with
    # the world object id.
    "object_ids": ("list_index", "track_id_key"),
    # The release names no sensor. ``folder``: the data folders, ``velodyne`` for the
    # LiDAR and ``image`` for the camera. ``calib``: the frame names of the calibration
    # files, ``lidar`` / ``virtuallidar`` and ``camera`` (lidar_to_camera,
    # virtuallidar_to_camera). ``agent``: the LiDAR named after its side folder, the
    # camera ``image``.
    "sensor_names": ("folder", "calib", "agent"),
    # data/README.md: "Car, Truck, Van, Bus" (cooperative) and "Truck" (single-view);
    # README_zh-CN's class table: "卡车/大货车 | Trunk".
    "truck_label": ("Truck", "Trunk"),
    # The infrastructure intrinsics folder: data/README.md's tree has "calib/camera",
    # docs/get_started.md's tree "calib/camera_intrinsic", and the data_info key is
    # "calib_camera_intrinsic_path" in both.
    "infra_intrinsic_dir": ("camera_intrinsic", "camera"),
    # tools/dataset_converter/README.md, "Transform point": "Rotation Matrix and
    # Translation Matrix can be obtained from the json files"; its trans_point returns
    # rotation @ point + translation for a file "from A coordinate system to B": an
    # ``A_to_B`` file holds B_from_A (``a_to_b``). ``b_to_a`` writes the inverse.
    "calib_direction": ("a_to_b", "b_to_a"),
    # Code (utils.py, transformation_utils.py) reshapes ``rotation`` to (3, 3) and
    # ``translation`` to (3, 1); the stored shape is not documented.
    # ``rotation_nested``: a 3 x 3 rotation beside a flat translation, which the same
    # reshape also accepts (added by the suite's author; still unverified).
    "matrix_layout": ("nested", "flat", "rotation_nested"),
    # Code, transformation_utils.py get_lidar2world: ``relative_error`` has ``delta_x``
    # and ``delta_y`` and an empty string is read as 0. The world has no error.
    "relative_error": ("zero", "empty"),
    # Code, label.py wraps l, w, h, x, y, z and rotation in float(); label_json2kitti.py
    # eval()s ``rotation``: the numbers may be stored as strings. Applies to alpha,
    # 2d_box, 3d_dimensions, 3d_location and rotation; the two states and
    # ``world_8_points`` stay numbers.
    "label_numbers": ("number", "string"),
    # data/README.md documents "label_lidar_path" and "label_camera_path"; code
    # (dair_v2x_for_detection.py) reads "label_lidar_std_path" / "label_camera_std_path".
    "label_keys": ("both", "documented", "std"),
    # README_zh-CN lists "alpha": "观察者视角，从[-pi, pi]中取值"; data/README.md omits it.
    # Its definition is not given, so it is written as 0 (no geometry depends on it).
    "alpha": ("zero", "omit"),
    # README_zh-CN, 融合标注格式, on world_8_points: "当前标注文件中其他3D信息不可用" (other 3D
    # information in the file is not usable), yet code (label.py, label_world2v.py)
    # reads "3d_dimensions" and "3d_location" of every cooperative entry.
    # ``size_location`` writes them (location: the world-frame centre).
    "cooperative_extras": ("size_location", "documented_only"),
    # The name of a cooperative label file is not documented; label_world2v.py keeps it
    # as it is. Release page: frame "vehicle frame id 002234" is left out of
    # train.json, so a split lists vehicle frame ids. ``vehicle_id``: the file is
    # named by the vehicle frame id; ``own_id``: by its 0-based row in
    # cooperative/data_info.json.
    "cooperative_label_name": ("vehicle_id", "own_id"),
    # Code, dair_v2x_for_detection.py prev_inf_frame: int(timestamp) - int(...); the
    # stored type is not documented.
    "timestamp_type": ("string", "integer"),
    # The same code divides a difference by 1000.0 for a time the fusion docs give in
    # ms, which implies microseconds; no document states the unit.
    "timestamp_unit": ("us", "ms"),
    # README_zh-CN: velodyne "{id}.pcd". The DATA kind of the released files is not
    # documented (the tools write binary_compressed when they rewrite a cloud; that
    # kind is not written here). Code, file_io.py read_pcd reads the fields x, y, z
    # and intensity.
    "pcd_data": ("binary", "ascii"),
}

# Paper §3.1: "The origin of the LiDAR coordinate system is located at the center of
# the LiDAR sensor, the x-axis is positive forwards, the y-axis is positive to the
# left, and the z-axis is positive upwards" — the world's LiDAR frame, unchanged.

# tools/dataset_converter/README.md, get_lidar_3d_8points: x [l/2, l/2, -l/2, -l/2, ...],
# y [w/2, -w/2, -w/2, w/2, ...], z [-h/2 x4, h/2 x4].
_DAIR_CORNER_SIGNS: Array = np.array(
    [
        [1.0, 1.0, -1.0],
        [1.0, -1.0, -1.0],
        [-1.0, -1.0, -1.0],
        [-1.0, 1.0, -1.0],
        [1.0, 1.0, 1.0],
        [1.0, -1.0, 1.0],
        [-1.0, -1.0, 1.0],
        [-1.0, 1.0, 1.0],
    ]
)
# Code, v2x/v2x_utils/eval_utils.py: perm_label = [3, 2, 1, 0, 7, 6, 5, 4].
_REVERSED_RING = (3, 2, 1, 0, 7, 6, 5, 4)
# data/README.md, Cooperative Annotation File: "We only consider the four class
# ["Car", "Truck", "Van", "Bus"]". The world's class name -> the label's type.
_COOPERATIVE_TYPES = {"car": "Car", "truck": "Truck", "van": "Van", "bus": "Bus"}
# A rotation is a yaw when it equals Rz(yaw) to this many units.
_YAW_ONLY_ATOL = 1e-9
_DOT_RADIUS_PX = 3


def export(
    world: World,
    root: Path,
    *,
    split: str = "train",
    rotation_zero: str = "x_axis",
    rotation_unit: str = "rad",
    corner_order: str = "dair_lidar",
    vehicle_lidar: str = "level",
    object_ids: str = "list_index",
    sensor_names: str = "folder",
    truck_label: str = "Truck",
    infra_intrinsic_dir: str = "camera_intrinsic",
    calib_direction: str = "a_to_b",
    matrix_layout: str = "nested",
    relative_error: str = "zero",
    label_numbers: str = "number",
    label_keys: str = "both",
    alpha: str = "zero",
    cooperative_extras: str = "size_location",
    cooperative_label_name: str = "vehicle_id",
    timestamp_type: str = "string",
    timestamp_unit: str = "us",
    pcd_data: str = "binary",
    frame_ids: str = "independent",
    info_paths: str = "side",
    novatel_nesting: str = "transform",
) -> Export:
    """Write one vehicle and one roadside unit of ``world`` as DAIR-V2X-C under ``root``.

    ``root`` gets ``cooperative-vehicle-infrastructure/`` and
    ``DAIR-V2X-C_Complemented_Anno/`` and is the directory handed to the adapter.
    ``split`` is the split (``train`` or ``val``) whose file lists the frames written.
    Every other keyword is a reading, see :data:`READINGS`; the values used are
    returned in ``Export.readings``.
    """
    readings = {
        "frame_ids": frame_ids,
        "info_paths": info_paths,
        "novatel_nesting": novatel_nesting,
        "rotation_zero": rotation_zero,
        "rotation_unit": rotation_unit,
        "corner_order": corner_order,
        "vehicle_lidar": vehicle_lidar,
        "object_ids": object_ids,
        "sensor_names": sensor_names,
        "truck_label": truck_label,
        "infra_intrinsic_dir": infra_intrinsic_dir,
        "calib_direction": calib_direction,
        "matrix_layout": matrix_layout,
        "relative_error": relative_error,
        "label_numbers": label_numbers,
        "label_keys": label_keys,
        "alpha": alpha,
        "cooperative_extras": cooperative_extras,
        "cooperative_label_name": cooperative_label_name,
        "timestamp_type": timestamp_type,
        "timestamp_unit": timestamp_unit,
        "pcd_data": pcd_data,
    }
    for name, value in readings.items():
        if value not in READINGS[name]:
            raise ValueError(f"{name}={value!r}: expected one of {', '.join(READINGS[name])}")
    if split not in SPLITS:
        raise ValueError(f"split={split!r}: expected one of {', '.join(SPLITS)}")
    if world.n_frames == 0:
        raise ValueError("the world has no frame")

    vehicle, infra = _written_agents(world, level_vehicle=vehicle_lidar == "level")
    # A fresh World: its ray-cast cache belongs to these agents, not to the input's.
    written = World(objects=world.objects, agents=(vehicle, infra), period_s=world.period_s)
    types = {**_COOPERATIVE_TYPES, "truck": truck_label}

    # One list of objects for every label file (see ``object_ids``).
    label_agents = (vehicle, infra) if vehicle_lidar == "level" else (infra,)
    listed = _listed_objects(written, types, label_agents)
    vehicle_lists = vehicle_lidar == "level" or _all_yaw_only(written, vehicle, listed)
    vehicle_listed = listed if vehicle_lists else ()

    n_frames = written.n_frames
    vehicle_ids = tuple(f"{VEHICLE_FIRST_ID + frame:06d}" for frame in range(n_frames))
    infra_first = VEHICLE_FIRST_ID if frame_ids == "shared" else INFRA_FIRST_ID
    infra_ids = tuple(f"{infra_first + frame:06d}" for frame in range(n_frames))
    coop_names = (
        vehicle_ids
        if cooperative_label_name == "vehicle_id"
        else tuple(f"{row:06d}" for row in range(n_frames))
    )
    stamps = _timestamps(written, timestamp_unit, timestamp_type)
    release = root / RELEASE
    release.mkdir(parents=True, exist_ok=True)

    label_style = _LabelStyle(
        rotation_zero=rotation_zero,
        rotation_unit=rotation_unit,
        as_string=label_numbers == "string",
        alpha=alpha == "zero",
        track_id_key=object_ids == "track_id_key",
    )
    calib_style = (calib_direction, matrix_layout)

    # --- vehicle side ------------------------------------------------------------------
    vehicle_dir = release / VEHICLE_SIDE
    vehicle_camera = vehicle.cameras[0]
    vehicle_rows: list[dict[str, object]] = []
    for frame, vid in enumerate(vehicle_ids):
        _write_frame_sensors(written, vehicle, vehicle_dir, vid, frame, pcd_data)
        body_from_lidar = vehicle.lidar.body_from_lidar
        # README_zh-CN, 目录结构: lidar_to_novatel "LiDAR坐标系到NovAtel坐标系变换参数" and
        # novatel_to_world "NovAtel在世界坐标系下定位坐标". The NovAtel frame's axes are not
        # documented; the world's body frame is taken as it, so LiDAR -> NovAtel is
        # body_from_lidar and NovAtel -> world is world_from_body, applied in that order
        # (code, transformation_utils.py Coord_Vehicle_lidar2world).
        _write_json(
            vehicle_dir / f"calib/lidar_to_novatel/{vid}.json",
            # Code, transformation_utils.py get_lidar2novatel: lidar2novatel["transform"]
            # ["rotation"] / ["translation"], one level deeper than the other files.
            (
                {"transform": _calib(body_from_lidar, *calib_style)}
                if novatel_nesting == "transform"
                else _calib(body_from_lidar, *calib_style)
            ),
        )
        _write_json(
            vehicle_dir / f"calib/novatel_to_world/{vid}.json",
            _calib(vehicle.world_from_body[frame], *calib_style),
        )
        # README_zh-CN: lidar_to_camera "LiDAR坐标系到相机坐标系变换参数": camera_from_lidar.
        camera_from_lidar = vehicle_camera.body_from_camera.inverse().compose(body_from_lidar)
        _write_json(
            vehicle_dir / f"calib/lidar_to_camera/{vid}.json",
            _calib(camera_from_lidar, *calib_style),
        )
        _write_json(vehicle_dir / f"calib/camera_intrinsic/{vid}.json", _intrinsic(vehicle_camera))
        entries = _single_view_entries(written, vehicle, frame, vehicle_listed, types, label_style)
        # data/README.md: label/camera "Labeled data in Vehicle LiDAR Coordinate System
        # fitting objects in image based on image frame time", label/lidar the same "in
        # point cloud based on point cloud frame time". The world's camera and LiDAR are
        # synchronous, so the two files hold the same boxes. The original camera-view
        # limit of label/camera is not reproduced.
        _write_json(vehicle_dir / f"label/camera/{vid}.json", entries)
        # Release page: "complemented vehicle-side labels", installed in label/lidar.
        _write_json(vehicle_dir / f"label/lidar/{vid}.json", entries)
        _write_json(
            root / COMPLEMENTED / f"new_labels/vehicle-side_label/lidar/{vid}.json", entries
        )
        row: dict[str, object] = {
            "image_path": f"image/{vid}.jpg",
            "image_timestamp": stamps[frame],
            "pointcloud_path": f"velodyne/{vid}.pcd",
            "pointcloud_timestamp": stamps[frame],
        }
        row.update(_label_paths("lidar", vid, label_keys))
        row.update(
            {
                "calib_lidar_to_camera_path": f"calib/lidar_to_camera/{vid}.json",
                "calib_lidar_to_novatel_path": f"calib/lidar_to_novatel/{vid}.json",
                "calib_novatel_to_world_path": f"calib/novatel_to_world/{vid}.json",
                "calib_camera_intrinsic_path": f"calib/camera_intrinsic/{vid}.json",
            }
        )
        row.update(_batch(vehicle_ids))
        vehicle_rows.append(row)
    # README_zh-CN, 索引文件格式: "json文件由一个列表组织而成" (a list, one entry per frame).
    _write_json(vehicle_dir / "data_info.json", _rooted(vehicle_rows, VEHICLE_SIDE, info_paths))

    # --- infrastructure side -----------------------------------------------------------
    infra_dir = release / INFRA_SIDE
    infra_camera = infra.cameras[0]
    infra_rows: list[dict[str, object]] = []
    for frame, iid in enumerate(infra_ids):
        # README_zh-CN: infrastructure velodyne "点云（为方便研究，已转到虚拟LiDAR坐标系下）"
        # (already in the virtual LiDAR frame). The returned world's roadside LiDAR is
        # that frame, so its returns are written as they are.
        _write_frame_sensors(written, infra, infra_dir, iid, frame, pcd_data)
        body_from_virtual = infra.lidar.body_from_lidar
        # docs/apis/dataloaders.md, InfFrame: "path of the calibration file from
        # virtuallidar coordinate system to world coordinate system".
        world_from_virtual = infra.world_from_body[frame].compose(body_from_virtual)
        to_world = _calib(world_from_virtual, *calib_style)
        error: object = "" if relative_error == "empty" else 0.0
        to_world["relative_error"] = {"delta_x": error, "delta_y": error}
        _write_json(infra_dir / f"calib/virtuallidar_to_world/{iid}.json", to_world)
        # README_zh-CN: virtuallidar_to_camera "虚拟LiDAR坐标系到相机坐标系变换参数".
        camera_from_virtual = infra_camera.body_from_camera.inverse().compose(body_from_virtual)
        _write_json(
            infra_dir / f"calib/virtuallidar_to_camera/{iid}.json",
            _calib(camera_from_virtual, *calib_style),
        )
        intrinsic_path = f"calib/{infra_intrinsic_dir}/{iid}.json"
        _write_json(infra_dir / intrinsic_path, _intrinsic(infra_camera))
        entries = _single_view_entries(written, infra, frame, listed, types, label_style)
        # data/README.md: label/virtuallidar "Labeled data in Infrastructure Virtual
        # LiDAR Coordinate System fitting objects in point cloud ..."; label/camera the
        # same frame at the image time.
        _write_json(infra_dir / f"label/camera/{iid}.json", entries)
        _write_json(infra_dir / f"label/virtuallidar/{iid}.json", entries)
        row = {
            "image_path": f"image/{iid}.jpg",
            "image_timestamp": stamps[frame],
            "pointcloud_path": f"velodyne/{iid}.pcd",
            "pointcloud_timestamp": stamps[frame],
        }
        # README_zh-CN's infrastructure table names the key "label_lidar_path" although
        # the folder is label/virtuallidar.
        row.update(_label_paths("virtuallidar", iid, label_keys))
        row.update(
            {
                "calib_virtuallidar_to_world_path": f"calib/virtuallidar_to_world/{iid}.json",
                "calib_virtuallidar_to_camera_path": f"calib/virtuallidar_to_camera/{iid}.json",
                "calib_camera_intrinsic_path": intrinsic_path,
            }
        )
        # data/README.md: "Infrastructure and vehicle frame with the same "batch_id"
        # share the same segments."
        row.update(_batch(infra_ids))
        infra_rows.append(row)
    _write_json(infra_dir / "data_info.json", _rooted(infra_rows, INFRA_SIDE, info_paths))

    # --- cooperative ---------------------------------------------------------------------
    coop_dir = release / COOPERATIVE
    coop_rows: list[dict[str, object]] = []
    for frame, (vid, iid, name) in enumerate(zip(vehicle_ids, infra_ids, coop_names, strict=True)):
        entries = _cooperative_entries(
            written, frame, listed, types, label_style, corner_order, cooperative_extras
        )
        _write_json(coop_dir / f"label_world/{name}.json", entries)
        _write_json(
            root / COMPLEMENTED / f"new_labels/cooperative_label/label_world/{name}.json", entries
        )
        # README_zh-CN, 车路协同融合标注data_info.json: the five path keys. Code,
        # label_world2v.py joins these values onto the release root directly (and
        # prepends the side folder only to the side files' own paths), so they carry
        # the side folder. Code, calib_i2v.py also reads "system_error_offset" here
        # (docs/apis/dataloaders.md calls it a time difference in ms); the world has
        # neither an offset nor a time difference, so both deltas are 0.
        coop_rows.append(
            {
                "infrastructure_image_path": f"{INFRA_SIDE}/image/{iid}.jpg",
                "infrastructure_pointcloud_path": f"{INFRA_SIDE}/velodyne/{iid}.pcd",
                "vehicle_image_path": f"{VEHICLE_SIDE}/image/{vid}.jpg",
                "vehicle_pointcloud_path": f"{VEHICLE_SIDE}/velodyne/{vid}.pcd",
                "cooperative_label_path": f"{COOPERATIVE}/label_world/{name}.json",
                "system_error_offset": {"delta_x": 0.0, "delta_y": 0.0},
            }
        )
    _write_json(coop_dir / "data_info.json", coop_rows)

    # --- splits --------------------------------------------------------------------------
    # Release page: "individual train/val split files from official splits"; the frame
    # left out of train.json is named by its "vehicle frame id". A split is written as
    # a JSON list of those ids; the other split is an empty list. Step 3 of the page
    # places the files under cooperative-vehicle-infrastructure/ as well.
    for name in SPLITS:
        ids = list(vehicle_ids) if name == split else []
        _write_json(root / COMPLEMENTED / f"{name}.json", ids)
        _write_json(release / f"{name}.json", ids)

    # --- expectation ---------------------------------------------------------------------
    lidar_names, camera_name = _sensor_names(sensor_names)
    annotated: dict[tuple[str, int], tuple[int, ...]] = {}
    for frame in range(n_frames):
        annotated[(vehicle.agent_id, frame)] = vehicle_listed
        annotated[(infra.agent_id, frame)] = listed
    by_id = {obj.object_id: obj for obj in written.objects[0]}
    expectation = Expectation(
        agents={vehicle.agent_id: VEHICLE_SIDE, infra.agent_id: INFRA_SIDE},
        lidars={vehicle.agent_id: lidar_names[0], infra.agent_id: lidar_names[1]},
        cameras={
            (vehicle.agent_id, vehicle_camera.sensor_id): camera_name,
            (infra.agent_id, infra_camera.sensor_id): camera_name,
        },
        annotated=annotated,
        track_ids={
            object_id: str(object_id) if label_style.track_id_key else str(position)
            for position, object_id in enumerate(listed)
        },
        categories={
            by_id[object_id].category: types[by_id[object_id].category] for object_id in listed
        },
    )
    return Export(
        adapter=ADAPTER,
        raw_root=root,
        params={"split": split},
        scene_id=BATCH_ID,
        world=written,
        expectation=expectation,
        readings=readings,
    )


# --- the agents that are written ------------------------------------------------------------


def _written_agents(world: World, *, level_vehicle: bool) -> tuple[Agent, Agent]:
    """The vehicle and the roadside unit as DAIR-V2X-C holds them.

    Paper §3.1 / dataset page §2.D: the "Virtual LiDAR Coordinate System" has its
    origin "at the center of the LiDAR sensor, the x-y plane is parallel to the ground
    plane, and the z-axis is positive upwards"; the infrastructure LiDAR frame "is
    converted from its original system which has an inclination angle with the
    ground". The heading of the virtual frame is not documented (and not released);
    it is taken as that of the physical LiDAR's x axis.
    """
    vehicle = next((agent for agent in world.agents if agent.kind == "vehicle"), None)
    infra = next((agent for agent in world.agents if agent.kind == "infrastructure"), None)
    if vehicle is None or infra is None:
        raise ValueError("DAIR-V2X-C needs a vehicle and an infrastructure agent")
    front = next((c for c in vehicle.cameras if c.sensor_id == "front"), None)
    vehicle_camera = front if front is not None else _first_camera(vehicle)
    vehicle = replace(
        vehicle,
        lidar=_level_lidar(vehicle) if level_vehicle else vehicle.lidar,
        cameras=(_native_camera(vehicle_camera),),
    )
    infra = replace(
        infra, lidar=_level_lidar(infra), cameras=(_native_camera(_first_camera(infra)),)
    )
    return vehicle, infra


def _first_camera(agent: Agent) -> Camera:
    if not agent.cameras:
        raise ValueError(f"agent {agent.agent_id} has no camera; DAIR-V2X-C has one per side")
    return agent.cameras[0]


def _level_lidar(agent: Agent) -> Lidar:
    """The agent's LiDAR mounted so that at frame 0 its z axis is world z.

    Same origin; x along the heading of the original LiDAR's x axis. The mount is
    fixed, so a body whose roll and pitch change between frames tilts it again; a
    label frame that is not level holds no upright box and lists none
    (:func:`_label_box`).
    """
    world_from_lidar = agent.world_from_body[0].compose(agent.lidar.body_from_lidar)
    heading = math.atan2(
        float(world_from_lidar.rotation[1, 0]), float(world_from_lidar.rotation[0, 0])
    )
    mount = agent.world_from_body[0].rotation.T @ rot_z(heading)
    return replace(
        agent.lidar, body_from_lidar=Pose(mount, agent.lidar.body_from_lidar.translation)
    )


def _native_camera(camera: Camera) -> Camera:
    """The camera at 1920 × 1080 with the same pose and horizontal field of view."""
    if (camera.width, camera.height) == (IMAGE_WIDTH, IMAGE_HEIGHT):
        return camera
    scale = IMAGE_WIDTH / camera.width
    return replace(
        camera,
        width=IMAGE_WIDTH,
        height=IMAGE_HEIGHT,
        fx=camera.fx * scale,
        fy=camera.fy * scale,
        cx=IMAGE_WIDTH / 2.0,
        cy=IMAGE_HEIGHT / 2.0,
    )


# --- which objects the labels list ----------------------------------------------------------


def _label_box(world_from_label: Pose, world_from_box: Pose) -> tuple[Array, float] | None:
    """The centre and yaw of a box in a label frame, or ``None`` when it is no pure yaw.

    README_zh-CN, 单侧标注格式: "3d_location": "3D bounding box中心点坐标" (the centre);
    "3d_dimensions": "3D bounding box长宽高" (full length, width, height). Code,
    utils.py get_lidar_3d_8points: corners at ±l/2 along the rotated x axis, ±w/2
    along y, ±h/2 along z: the length is the box's own x, the world's convention.
    """
    label_from_world = world_from_label.inverse()
    rotation = label_from_world.rotation @ world_from_box.rotation
    yaw = math.atan2(float(rotation[1, 0]), float(rotation[0, 0]))
    if not np.allclose(rotation, rot_z(yaw), rtol=0.0, atol=_YAW_ONLY_ATOL):
        return None
    return label_from_world.apply(world_from_box.translation), yaw


def _listed_objects(
    world: World, types: Mapping[str, str], label_agents: Sequence[Agent]
) -> tuple[int, ...]:
    """Ids of the objects every label file of every frame can hold, ascending.

    An object is listed when it is in every frame, its class is one of the cooperative
    four, and its box is a pure yaw in the label frame of every agent that writes
    labels. The same list, in the same order, goes into every file, so under
    ``object_ids="list_index"`` a position names one object everywhere. No document
    gives the rule by which a file lists an object (the complemented release
    "supplement[s] the missing 3D box annotations to enable the 360 degree
    detection"), so every object that can be written is.
    """
    frames = [{obj.object_id: obj for obj in objects} for objects in world.objects]
    listed: list[int] = []
    for object_id in sorted(frames[0]):
        keep = True
        for frame, objects in enumerate(frames):
            obj = objects.get(object_id)
            if obj is None or obj.category not in types:
                keep = False
                break
            for agent in label_agents:
                frame_pose = world.world_from_lidar(agent.agent_id, frame)
                if _label_box(frame_pose, obj.world_from_box) is None:
                    keep = False
                    break
            if not keep:
                break
        if keep:
            listed.append(object_id)
    return tuple(listed)


def _all_yaw_only(world: World, agent: Agent, listed: Sequence[int]) -> bool:
    """True when every listed object is a pure yaw in the agent's LiDAR frame at every frame."""
    for frame, objects in enumerate(world.objects):
        by_id = {obj.object_id: obj for obj in objects}
        frame_pose = world.world_from_lidar(agent.agent_id, frame)
        for object_id in listed:
            if _label_box(frame_pose, by_id[object_id].world_from_box) is None:
                return False
    return bool(listed)


# --- labels -----------------------------------------------------------------------------------


class _LabelStyle:
    """How the scalar fields of a label entry are written (see :data:`READINGS`)."""

    def __init__(
        self,
        *,
        rotation_zero: str,
        rotation_unit: str,
        as_string: bool,
        alpha: bool,
        track_id_key: bool,
    ) -> None:
        self.rotation_zero = rotation_zero
        self.rotation_unit = rotation_unit
        self.as_string = as_string
        self.alpha = alpha
        self.track_id_key = track_id_key

    def number(self, value: float) -> float | str:
        """A plain float (a negative zero written as 0.0), or its repr as a string."""
        plain = float(value) + 0.0
        return repr(plain) if self.as_string else plain

    def rotation(self, yaw: float) -> float | str:
        """``rotation`` of a box whose length axis is at ``yaw`` from label +x, about +z."""
        if self.rotation_zero == "y_axis":
            # Measured from +y about +z: the length axis is Rz(rotation) e_y.
            angle = yaw - math.pi / 2.0
        elif self.rotation_zero == "kitti_rotation_y":
            # KITTI's camera rotation_y of a LiDAR yaw.
            angle = -yaw - math.pi / 2.0
        else:
            angle = yaw
        # Code, utils.py: "rotation_z (-pi,pi) rad".
        angle = math.atan2(math.sin(angle), math.cos(angle))
        return self.number(math.degrees(angle) if self.rotation_unit == "deg" else angle)


def _single_view_entries(
    world: World,
    agent: Agent,
    frame: int,
    listed: Sequence[int],
    types: Mapping[str, str],
    style: _LabelStyle,
) -> list[dict[str, object]]:
    """One single-view label file: the listed objects in the agent's LiDAR frame.

    README_zh-CN, 单侧标注格式: a list of entries with "type", "truncated_state",
    "occluded_state", "alpha", "2d_box" {xmin, ymin, xmax, ymax}, "3d_dimensions"
    {h, w, l}, "3d_location" {x, y, z} and "rotation". The world has no truncation
    or occlusion model: both states are 0 ("0" is "不截断" / "不遮挡").
    """
    by_id = {obj.object_id: obj for obj in world.objects[frame]}
    world_from_label = world.world_from_lidar(agent.agent_id, frame)
    camera = agent.cameras[0]
    truth = world.camera_truth(agent.agent_id, camera.sensor_id, frame)
    entries: list[dict[str, object]] = []
    for object_id in listed:
        obj = by_id[object_id]
        box = _label_box(world_from_label, obj.world_from_box)
        if box is None:
            raise ValueError(f"object {object_id} is not a pure yaw in {agent.agent_id}'s frame")
        centre, yaw = box
        length, width, height = obj.size_lwh
        entry: dict[str, object] = {
            "type": types[obj.category],
            "truncated_state": 0,
            "occluded_state": 0,
        }
        if style.alpha:
            entry["alpha"] = style.number(0.0)
        entry["2d_box"] = _box_2d(truth[object_id], camera, style)
        entry["3d_dimensions"] = {
            "h": style.number(height),
            "w": style.number(width),
            "l": style.number(length),
        }
        x, y, z = (float(v) for v in centre.tolist())
        entry["3d_location"] = {
            "x": style.number(x),
            "y": style.number(y),
            "z": style.number(z),
        }
        entry["rotation"] = style.rotation(yaw)
        if style.track_id_key:
            entry["track_id"] = str(object_id)
        entries.append(entry)
    return entries


def _box_2d(truth: CameraTruth, camera: Camera, style: _LabelStyle) -> dict[str, object]:
    """The image box of the projected corners, clipped to the image; zeros when not seen.

    "2d_box": "图像中2D bounding box框" (README_zh-CN). How an object outside the image
    is written is not documented; such a box is all zeros here. No geometry is read
    from it.
    """
    u_min, v_min, u_max, v_max = truth.box_2d
    u_min, u_max = max(u_min, 0.0), min(u_max, float(camera.width))
    v_min, v_max = max(v_min, 0.0), min(v_max, float(camera.height))
    if not bool(np.all(truth.in_front)) or u_max <= u_min or v_max <= v_min:
        u_min = v_min = u_max = v_max = 0.0
    return {
        "xmin": style.number(u_min),
        "ymin": style.number(v_min),
        "xmax": style.number(u_max),
        "ymax": style.number(v_max),
    }


def _cooperative_entries(
    world: World,
    frame: int,
    listed: Sequence[int],
    types: Mapping[str, str],
    style: _LabelStyle,
    corner_order: str,
    cooperative_extras: str,
) -> list[dict[str, object]]:
    """One cooperative label file: the listed objects as eight corners in the world.

    data/README.md, Cooperative Annotation File: "type", "world_8_points": "8 corners
    of 3d bounding box", "system_error_offset" {delta_x, delta_y}. README_zh-CN: the
    corners are "位于世界坐标系" (in the world frame) and the offset is "人工二次修正"
    (a manual correction of the relative calibration); the world needs none, so it is 0.
    The release page: the cooperative labels "follow the original cooperative label
    format". The paper's union GT = GT_v ∪ GT_i is the one list both sides share.
    """
    by_id = {obj.object_id: obj for obj in world.objects[frame]}
    entries: list[dict[str, object]] = []
    for object_id in listed:
        obj = by_id[object_id]
        corners = _world_corners(obj.world_from_box, obj.size_lwh, corner_order)
        entry: dict[str, object] = {
            "type": types[obj.category],
            "world_8_points": [[float(v) + 0.0 for v in row] for row in corners.tolist()],
            "system_error_offset": {"delta_x": 0.0, "delta_y": 0.0},
        }
        if cooperative_extras == "size_location":
            length, width, height = obj.size_lwh
            entry["3d_dimensions"] = {
                "h": style.number(height),
                "w": style.number(width),
                "l": style.number(length),
            }
            x, y, z = (float(v) for v in obj.world_from_box.translation.tolist())
            entry["3d_location"] = {
                "x": style.number(x),
                "y": style.number(y),
                "z": style.number(z),
            }
        if style.track_id_key:
            entry["track_id"] = str(object_id)
        entries.append(entry)
    return entries


def _world_corners(
    world_from_box: Pose, size_lwh: tuple[float, float, float], corner_order: str
) -> Array:
    """The eight corners of a box in the world, in the order ``corner_order`` selects."""
    signs = _DAIR_CORNER_SIGNS
    if corner_order == "turned_pi":
        # The documented order for the same box with its front and back swapped.
        signs = signs * np.array([-1.0, -1.0, 1.0])
    elif corner_order == "reversed_ring":
        signs = signs[list(_REVERSED_RING)]
    return world_from_box.apply(signs * (np.array(size_lwh) / 2.0))


def _label_paths(lidar_folder: str, frame_id: str, label_keys: str) -> dict[str, object]:
    """The label keys of a side's data_info row (see ``label_keys``)."""
    lidar = f"label/{lidar_folder}/{frame_id}.json"
    camera = f"label/camera/{frame_id}.json"
    paths: dict[str, object] = {}
    if label_keys in ("both", "documented"):
        paths["label_lidar_path"] = lidar
        paths["label_camera_path"] = camera
    if label_keys in ("both", "std"):
        paths["label_lidar_std_path"] = lidar
        paths["label_camera_std_path"] = camera
    return paths


def _batch(frame_ids: Sequence[str]) -> dict[str, object]:
    """``batch_id``, ``intersection_loc``, ``batch_start_id``, ``batch_end_id`` of a row.

    README_zh-CN, 备注: "路端图像、点云编号从[ batch_start_id, batch_end_id ]为一段 ...
    按时间顺序排列" (ids from batch_start_id to batch_end_id are one segment, in time
    order). Every frame written is one segment.
    """
    return {
        "batch_id": BATCH_ID,
        "intersection_loc": INTERSECTION,
        "batch_start_id": frame_ids[0],
        "batch_end_id": frame_ids[-1],
    }


def _sensor_names(sensor_names: str) -> tuple[tuple[str, str], str]:
    """``(vehicle LiDAR, infrastructure LiDAR), camera`` names of ``sensor_names``."""
    if sensor_names == "calib":
        return ("lidar", "virtuallidar"), "camera"
    if sensor_names == "agent":
        return (VEHICLE_SIDE, INFRA_SIDE), "image"
    return ("velodyne", "velodyne"), "image"


# --- calibration --------------------------------------------------------------------------


def _calib(b_from_a: Pose, direction: str, layout: str) -> dict[str, object]:
    """``rotation`` and ``translation`` of an ``A_to_B`` file.

    Code, utils.py / transformation_utils.py: the readers take the top-level
    "rotation" and "translation" (lidar_to_novatel nests them in "transform", which
    the caller adds). README trans_point: rotation @ point + translation.
    """
    stored = b_from_a if direction == "a_to_b" else b_from_a.inverse()
    values = [float(v) + 0.0 for v in stored.rotation.reshape(-1).tolist()]
    offset = [float(v) + 0.0 for v in stored.translation.tolist()]
    if layout == "flat":
        return {"rotation": values, "translation": offset}
    if layout == "rotation_nested":
        return {"rotation": [values[0:3], values[3:6], values[6:9]], "translation": offset}
    return {
        "rotation": [values[0:3], values[3:6], values[6:9]],
        "translation": [[value] for value in offset],
    }


def _intrinsic(camera: Camera) -> dict[str, object]:
    """A camera_intrinsic file.

    Code, gen_kitti/gen_calib2kitti.py: "cam_K" read as nine values row-major into a
    3 × 3 K, copied into KITTI's P2; "cam_D" is read and not applied. The world has
    no distortion; five zeros are written (the count and order of cam_D are not
    documented). The camera frame: dataset page §2.D, "the x-y plane is parallel to
    the image plane, and the z-axis is positive forwards", and the image frame has
    "the x-axis and the y-axis ... along the image width and height". With this K,
    whose focal lengths are positive, camera x runs with the image width (right) and
    camera y with its height (down): the world's optical frame. That the 3D axes are
    x right and y down is not stated in words (unverified); the official KITTI
    converter passes lidar_to_camera unchanged as Tr_velo_to_cam (code).
    """
    return {
        "cam_K": [
            float(camera.fx),
            0.0,
            float(camera.cx),
            0.0,
            float(camera.fy),
            float(camera.cy),
            0.0,
            0.0,
            1.0,
        ],
        "cam_D": [0.0, 0.0, 0.0, 0.0, 0.0],
    }


# --- sensor data ---------------------------------------------------------------------------


def _timestamps(world: World, unit: str, kind: str) -> list[object]:
    """``image_timestamp`` = ``pointcloud_timestamp`` of every frame.

    Dataset page §3: "we take point cloud timestamp from vehicle-side as the VIC
    annotation timestamp"; the paper pairs frames less than 10 ms apart. The world's
    agents and sensors are synchronous, so both sides and both sensors share a time.
    """
    scale = 1_000_000 if unit == "us" else 1_000
    base = EPOCH_US if unit == "us" else EPOCH_US // 1_000
    values = [base + round(frame * world.period_s * scale) for frame in range(world.n_frames)]
    if any(later <= earlier for earlier, later in zip(values, values[1:], strict=False)):
        raise ValueError(f"a period of {world.period_s} s does not give increasing {unit}")
    return [str(value) if kind == "string" else value for value in values]


def _write_frame_sensors(
    world: World, agent: Agent, side_dir: Path, frame_id: str, frame: int, pcd_data: str
) -> None:
    """The point cloud and the image of one side at one frame."""
    points = world.lidar_returns(agent.agent_id, frame).points
    path = side_dir / f"velodyne/{frame_id}.pcd"
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_pcd(path, points, pcd_data)
    camera = agent.cameras[0]
    image_path = side_dir / f"image/{frame_id}.jpg"
    image_path.parent.mkdir(parents=True, exist_ok=True)
    _write_image(image_path, camera, world.camera_truth(agent.agent_id, camera.sensor_id, frame))


def _write_pcd(path: Path, points: Array, data: str) -> None:
    """A PCD v0.7 file with the fields x, y, z and intensity, one point per return.

    PCL, "The PCD (Point Cloud Data) file format": the header entries VERSION, FIELDS,
    SIZE, TYPE, COUNT, WIDTH, HEIGHT, VIEWPOINT, POINTS, DATA "must be specified
    precisely in the above order"; an unorganised cloud has HEIGHT 1 and WIDTH the
    point count; with DATA binary the data is a memory copy of the points, with DATA
    ascii one point per line. The world has no intensity: 0 (the 0-255 scale the
    tools divide by is an inference, code: file_io.py).
    """
    count = int(points.shape[0])
    records = np.zeros((count, 4), dtype="<f4")
    records[:, :3] = points
    header = (
        "# .PCD v0.7 - Point Cloud Data file format\n"
        "VERSION 0.7\n"
        "FIELDS x y z intensity\n"
        "SIZE 4 4 4 4\n"
        "TYPE F F F F\n"
        "COUNT 1 1 1 1\n"
        f"WIDTH {count}\n"
        "HEIGHT 1\n"
        "VIEWPOINT 0 0 0 1 0 0 0\n"
        f"POINTS {count}\n"
        f"DATA {data}\n"
    ).encode("ascii")
    if data == "ascii":
        # Nine significant digits give a float32 back exactly.
        lines = (f"{x:.9g} {y:.9g} {z:.9g} {i:.9g}\n" for x, y, z, i in records.tolist())
        body = "".join(lines).encode("ascii")
    else:
        body = records.tobytes()
    path.write_bytes(header + body)


def _write_image(path: Path, camera: Camera, truth: Mapping[int, CameraTruth]) -> None:
    """A JPEG of the camera's size with the projected corners of every object as dots."""
    image = Image.new("RGB", (camera.width, camera.height), (0, 0, 0))
    draw = ImageDraw.Draw(image)
    for object_id, corners in truth.items():
        red, green, blue = colorsys.hsv_to_rgb((object_id * 0.618034) % 1.0, 0.85, 1.0)
        colour = (round(red * 255), round(green * 255), round(blue * 255))
        for (u, v), in_front in zip(
            corners.pixels.tolist(), corners.in_front.tolist(), strict=True
        ):
            if in_front and 0.0 <= u < camera.width and 0.0 <= v < camera.height:
                draw.ellipse(
                    (
                        u - _DOT_RADIUS_PX,
                        v - _DOT_RADIUS_PX,
                        u + _DOT_RADIUS_PX,
                        v + _DOT_RADIUS_PX,
                    ),
                    fill=colour,
                )
    image.save(path, format="JPEG", quality=92)


def _write_json(path: Path, document: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")


def _rooted(
    rows: Sequence[dict[str, object]], side: str, info_paths: str
) -> list[dict[str, object]]:
    """``rows`` with every ``*_path`` value based at the release root when asked."""
    if info_paths == "side":
        return list(rows)
    return [
        {
            key: f"{side}/{value}" if key.endswith("_path") and isinstance(value, str) else value
            for key, value in row.items()
        }
        for row in rows
    ]
