# SPDX-License-Identifier: Apache-2.0
# pyright: reportUnknownMemberType=false
"""The synthetic world written as the Griffin air-ground dataset (KITTI-style release).

This file is a second, independent description of the format. It was written from the
Griffin paper (arXiv 2503.06983 v2, "the paper" below), the release documentation
``docs/Dataset_Preparation.md`` and, where those are silent, the release's own
conversion toolkit ``tools/griffin_data_converter/`` (``data_utils.py``,
``space_utils.py``, ``trans_kitti2nuscenes.py``, ``visual_kitti.py``), all at commit
``9c02ba4a37201edfc2b95ddbcdc2ff9aff47e7f4`` of github.com/wang-jh18-SVM/Griffin.
A point settled only by the toolkit says so next to it. The adapter that reads the
format, its tests, its fixtures and its documentation were not opened, and no code
was copied from the toolkit.

Where no source settles a convention, :func:`export` takes a keyword. Its default is
the reading the sources support best; the other values write the alternatives.
:data:`READINGS` lists every keyword, its values and why, and marks it unverified.

What is written under ``root`` (the dataset root that holds the subset folders)::

    griffin_50scenes_25m/                   one folder per subset, see SUBSET_FOLDERS
      griffin-release/
        vehicle-side/
          calib/{front,back,left,right}.json    {"extrinsic": 4x4, "intrinsic": 3x3}
          calib/lidar_top.json                  {"extrinsic": 4x4}
          camera/{front,back,left,right}/{frame}.png
          label/{frame}.txt
          lidar/lidar_top/{frame}.bin
          pose/{frame}.json
          scene_infos.json
        drone-side/
          calib/<camera>.json                   one of front, back, left, right, bottom
          calib/lidar_virtual.json              {"extrinsic": 4x4}
          camera/<camera>/{frame}.png
          label/{frame}.txt
          pose/{frame}.json
          scene_infos.json

``{frame}`` is a six-digit stem, ``000000`` for frame 0 of the world. Only the cameras
the world has are written.

What the world has and Griffin cannot hold, and is therefore left out:

* **agents**: a Griffin scene has one ground vehicle and one drone ("Each holds
  griffin-release/vehicle-side and griffin-release/drone-side", Dataset_Preparation.md
  folder tree, lines 21-58). The first agent of kind ``vehicle`` becomes
  ``vehicle-side`` and the first of kind ``uav`` becomes ``drone-side``. Every other
  agent (the second vehicle, the roadside unit) is dropped; the returned world is
  :func:`tests.geomworld.scenes.subset` of the two.
* **drone LiDAR returns**: "Stores LiDAR point cloud data (.bin files), available only
  for the ground vehicle." (Dataset_Preparation.md line 147). The drone gets no
  ``lidar/`` folder. Its LiDAR mount is still written, as ``calib/lidar_virtual.json``,
  which the toolkit loads for the drone (trans_kitti2nuscenes.py lines 181-192). The
  returned world keeps the drone's LiDAR (an ``Agent`` cannot be without one), so a
  check that wants a drone sweep will not find one.
* **instance segmentation maps** (``camera/instance_*``): their encoding is not
  documented, they carry no geometry, and they are not written.
* **object classes** Griffin does not have: the paper lists "six object categories:
  pedestrian, car, truck, bus, motorcycle, and bicycle" (§3.2, Occlusion-Aware
  Annotation). An object of another class is left out of the labels and of
  ``annotated``. The standard world's car, truck and pedestrian are all kept.

Nothing about roll and pitch is lost: a Griffin box has "a 9-DoF bounding box defined
by x, y, z, length, width, height, roll, pitch, and yaw" (paper §3.2), and a pose has
roll, pitch and yaw (data_utils.py lines 38-48).

Things a reader may trip on that this export cannot avoid:

* The world has three frames. The toolkit's NuScenes converter drops any run of
  consecutive frames shorter than ``MIN_SCENE_LENGTH = 10`` (trans_kitti2nuscenes.py
  line 33, lines 305-312). That is a rule of the converter, not of the release.
* The release's images are "1920×1080" (paper §3.1, Sensor Configuration). The
  world's cameras are written at their own size and intrinsics; a calib file holds
  no image size, so the size is the PNG's own.
* The release has four vehicle and five drone cameras. The world's drone has one;
  the other drone calib files are not written.

It imports nothing from ``coperceive``.
"""

from __future__ import annotations

import colorsys
import itertools
import json
import math
from collections.abc import Mapping, Sequence
from pathlib import Path

import numpy as np
import numpy.typing as npt
from PIL import Image, ImageDraw

from tests.geomworld.expectation import Expectation, Export
from tests.geomworld.scenes import subset as world_subset
from tests.geomworld.world import Agent, Array, Camera, CameraTruth, Pose, World, WorldObject

ADAPTER = "griffin"

# "The example uses `griffin_50scenes_25m`, but all subsets follow the same
# structure" (Dataset_Preparation.md line 17); the tree (lines 21-58) names
# griffin_50scenes_25m, griffin_50scenes_40m, griffin_50scenes_55m and
# griffin_100scenes_random. The subsets are cut by drone altitude: "Griffin-25m:
# 25 ± 2 meters" and so on (lines 3-7).
SUBSET_FOLDERS: Mapping[str, str] = {
    "25m": "griffin_50scenes_25m",
    "40m": "griffin_50scenes_40m",
    "55m": "griffin_50scenes_55m",
    "random": "griffin_100scenes_random",
}
RELEASE_FOLDER = "griffin-release"
VEHICLE_SIDE = "vehicle-side"
DRONE_SIDE = "drone-side"

# The calib file names of the LiDARs: ``load_calibration(self.source_dir,
# 'lidar_top')`` for the vehicle and ``load_calibration(self.source_dir,
# 'lidar_virtual')`` for the drone (trans_kitti2nuscenes.py lines 181-192); a calib
# file is ``calib/{sensor}.json`` (data_utils.py line 211). The vehicle's point
# clouds sit in ``lidar/lidar_top`` (Dataset_Preparation.md lines 36-37).
VEHICLE_LIDAR = "lidar_top"
DRONE_LIDAR = "lidar_virtual"

# Camera folders: vehicle back, front, left, right; drone back, bottom, front, left,
# right (Dataset_Preparation.md lines 26-34 and 42-52). The toolkit's sensor lists
# agree: ``["front", "back", "left", "right"]`` for the vehicle and ``["bottom",
# "front", "back", "left", "right"]`` for the drone (visual_kitti.py lines 24-27).
VEHICLE_CAMERAS = ("front", "back", "left", "right")
DRONE_CAMERAS = ("front", "back", "left", "right", "bottom")

# "an 80-beam LiDAR operating at 10 Hz" (paper §3.1) and "Each of the 255 scenes
# lasts ~15 seconds (~150 frames)" (Dataset_Preparation.md line 9). The toolkit
# turns a frame stem into time with "each digit represents 0.1 seconds"
# (trans_kitti2nuscenes.py line 848): stem n is at n * 0.1 s.
FRAME_PERIOD_S = 0.1
FIRST_FRAME = 0

# The ``name`` of the one scene written to ``scene_infos.json``. Its form in the
# release is not documented; the split files name scenes ``scene-0026-Town07-001``
# (data/split_datas/griffin_50scenes_25m.json), which the converter builds as
# ``f'scene-{scene_num:04}-{scene_info["name"]}'`` (trans_kitti2nuscenes.py line 715),
# so a raw name looks like ``<town>-<index>``. This one follows that shape.
SCENE_NAME = "geomworld-001"
# ``scene_info["info"]["weather"]`` becomes the scene description (line 716). Its form
# in the release is not documented; any string is accepted by that use.
SCENE_WEATHER = "synthetic"

# The six categories of the paper (§3.2), by the world's class names.
_GRIFFIN_CLASSES = ("pedestrian", "car", "truck", "bus", "motorcycle", "bicycle")

# Every keyword of :func:`export` that selects a reading, with the values it takes.
# The first value is the default. Every one of them is unverified: no written source
# settles it.
READINGS: Mapping[str, tuple[str, ...]] = {
    # Which point of the box ``x y z`` is. UNVERIFIED in the written docs. The
    # toolkit's visualiser builds the corners at ``dx = obj.l / 2``, ``dy = obj.w /
    # 2``, ``dz = obj.h / 2`` on both sides of ``(x, y, z)`` before applying the
    # box pose (visual_kitti.py lines 48-81), i.e. ``center``; the paper calls
    # ``center(o)`` "the box's geometric center" (§4.1). ``bottom_center`` is the
    # KITTI habit the "KITTI-style" name could suggest: the centre of the bottom face,
    # half a height down the box's own z axis.
    "box_origin": ("center", "bottom_center"),
    # The case of the ``type`` field. UNVERIFIED. The paper writes the classes in
    # lower case (§3.2). The toolkit lower-cases the field before it maps it
    # (``self.obj_type_mapping[ann.type.lower()]``, trans_kitti2nuscenes.py lines
    # 577 and 676), and its label class tests capitalised names (``"Alfa"``,
    # ``"Charlie"``, and a commented-out ``["Car", "Truck", "Bus"]``, data_utils.py
    # lines 78-97), which is why ``capitalised`` is the default.
    "type_case": ("capitalised", "lowercase"),
    # The record of a ``.bin`` sweep. UNVERIFIED: no document or toolkit file reads
    # the raw sweeps. ``xyzi``: float32 x, y, z, intensity, the KITTI record the
    # "KITTI-Style Raw Data Structure" title (Dataset_Preparation.md line 18) points
    # to. ``xyz``: float32 x, y, z. ``xyz6``: six float32 per point, x, y, z first,
    # the default ``load_dim=6`` of the repository's point loader
    # (projects/mmdet3d_plugin/datasets/pipelines/loading.py line 16), which the
    # Griffin configs do not use; the three fields after x, y, z are written as 0.
    # Little-endian in every case; the world has no intensity, written as 0.
    "lidar_record": ("xyzi", "xyz", "xyz6"),
    # The frame of the points in a ``.bin`` sweep. UNVERIFIED. ``sensor``: the
    # LiDAR frame, "LiDAR | FLU (R) | LiDAR center" (paper Table 2), which
    # ``calib/lidar_top.json`` takes to the vehicle ego frame. ``ego``: the same
    # returns already moved into the vehicle ego frame with that extrinsic.
    "points_frame": ("sensor", "ego"),
    # Whether a LiDAR calib file has an ``intrinsic`` key. UNVERIFIED. The toolkit
    # reads it with ``data.get('intrinsic', None)`` (data_utils.py line 221), so it
    # may be ``absent``; ``null`` writes the key with a JSON null.
    "lidar_intrinsic": ("absent", "null"),
    # Which objects an agent's label file lists. The rule is documented, its
    # threshold is not: "we sample points within each ground-truth bounding box and
    # project them onto the segmentation image ... Targets with low visibility from a
    # single agent's perspective are filtered out" (paper §3.2). ``visible``: the
    # objects whose visibility (computed as described at :func:`_visibility`) is
    # above 0. ``all``: every object of the frame, each with its visibility, 0 for
    # an object the agent does not see. UNVERIFIED.
    "visibility_filter": ("visible", "all"),
    # The id of the scene a reader makes of it. UNVERIFIED: the release has no
    # folder per scene, only ``scene_infos.json`` entries with a ``name``. ``name``:
    # that name. ``nuscenes``: the name the toolkit converter gives it,
    # ``scene-{n:04}-{name}`` with the first scene numbered 0
    # (trans_kitti2nuscenes.py lines 704-715).
    "scene_name": ("name", "nuscenes"),
    # Extra keys in each scene_infos entry's "info". ``documented``: frames and weather
    # only, what the toolkit reads. ``with_platform``: also "map_name" and
    # "cruise_altitude" (metres), metadata that is not geometry and that no document
    # describes; the values written are neutral. (Added by the conformance suite's
    # author, who has read the adapter: it requires both keys.)
    "scene_info_fields": ("documented", "with_platform"),
    # A "timestamp" key in each pose file. ``absent``: the six keys the toolkit reads.
    # ``frame``: also "timestamp", the integer frame id of the file's stem; no document
    # describes it. (Added by the conformance suite's author, who has read the adapter:
    # it requires the key and checks it against the stem.)
    "pose_timestamp": ("absent", "frame"),
    # The point file. ``bin``: Dataset_Preparation.md, "Stores LiDAR point cloud data
    # (.bin files)". ``ply_ascii``: an ASCII PLY with float x, y, z and I (intensity), which
    # is what the repository's own notes report finding in the released 25 m archive
    # (docs/geometry/conventions.md GRF-8); the documents and the release disagree.
    # (Added by the conformance suite's author.)
    "lidar_file": ("bin", "ply_ascii"),
}

# Points sampled inside a box to measure its visibility: a 3 × 3 × 3 grid at these
# fractions of its length, width and height around its centre.
_SAMPLE_FRACTIONS = (-0.4, 0.0, 0.4)
# A camera whose optical axis is at least this far below the body's xy plane is the
# drone's ``bottom`` camera; the others are named by the direction they face.
_BOTTOM_ELEVATION_DEG = -80.0
_DOT_RADIUS_PX = 2


def griffin_rotation(roll_deg: float, pitch_deg: float, yaw_deg: float) -> Array:
    """The rotation Griffin gives to three angles in degrees.

    The toolkit builds both an agent pose and a box pose as
    ``R.from_euler('xyz', [roll, pitch, yaw], degrees=True)`` (space_utils.py lines
    28-30 and 48-50). In SciPy "{'x', 'y', 'z'} for extrinsic rotations" (SciPy
    ``Rotation.from_euler``, Parameters > seq): roll about the fixed x axis first,
    then pitch about y, then yaw about z, which is the matrix
    ``Rz(yaw) @ Ry(pitch) @ Rx(roll)`` of right-handed elementary rotations.

    The sign comments agree with that reading: "+ indicates left side up" (roll),
    "+ indicates nose down" (pitch), "0 indicates East, + indicates counterclockwise"
    (yaw) (data_utils.py lines 46-48). ``Ry(p)`` takes forward ``(1, 0, 0)`` to
    ``(cos p, 0, -sin p)``, nose down; ``Rx(r)`` takes left ``(0, 1, 0)`` to
    ``(0, cos r, sin r)``, left side up.
    """
    roll, pitch, yaw = math.radians(roll_deg), math.radians(pitch_deg), math.radians(yaw_deg)
    cr, sr = math.cos(roll), math.sin(roll)
    cp, sp = math.cos(pitch), math.sin(pitch)
    cy, sy = math.cos(yaw), math.sin(yaw)
    about_z = np.array([[cy, -sy, 0.0], [sy, cy, 0.0], [0.0, 0.0, 1.0]])
    about_y = np.array([[cp, 0.0, sp], [0.0, 1.0, 0.0], [-sp, 0.0, cp]])
    about_x = np.array([[1.0, 0.0, 0.0], [0.0, cr, -sr], [0.0, sr, cr]])
    return about_z @ about_y @ about_x


def griffin_rpy_deg(rotation: Array) -> tuple[float, float, float]:
    """``(roll, pitch, yaw)`` in degrees such that :func:`griffin_rotation` gives ``rotation``.

    Read off ``Rz(yaw) Ry(pitch) Rx(roll)``, whose first column is
    ``(cy cp, sy cp, -sp)`` and whose last row is ``(-sp, cp sr, cp cr)``. Pitch is
    in ``[-90°, 90°]``, roll and yaw in ``[-180°, 180°]``.
    """
    sin_pitch = max(-1.0, min(1.0, -float(rotation[2, 0])))
    pitch = math.asin(sin_pitch)
    if abs(sin_pitch) < 1.0 - 1e-12:
        roll = math.atan2(float(rotation[2, 1]), float(rotation[2, 2]))
        yaw = math.atan2(float(rotation[1, 0]), float(rotation[0, 0]))
    else:
        # Pitch of ±90°: roll and yaw turn about the same axis. All of it goes to yaw.
        roll = 0.0
        yaw = math.atan2(-float(rotation[0, 1]), float(rotation[1, 1]))
    # ``+ 0.0`` writes a negative zero as 0.0.
    angles = (math.degrees(roll) + 0.0, math.degrees(pitch) + 0.0, math.degrees(yaw) + 0.0)
    if not np.allclose(griffin_rotation(*angles), rotation, rtol=0.0, atol=1e-9):
        raise ValueError("roll, pitch and yaw cannot express this matrix: it is not a rotation")
    return angles


def export(
    world: World,
    root: Path,
    *,
    subset: str = "25m",
    box_origin: str = "center",
    type_case: str = "capitalised",
    lidar_record: str = "xyzi",
    points_frame: str = "sensor",
    lidar_intrinsic: str = "absent",
    visibility_filter: str = "visible",
    scene_name: str = "name",
    scene_info_fields: str = "documented",
    pose_timestamp: str = "absent",
    lidar_file: str = "bin",
) -> Export:
    """Write ``world`` under ``root`` as one scene of one Griffin subset.

    ``root`` is the dataset root: it gets the subset folder (``subset`` is one of
    :data:`SUBSET_FOLDERS`) and is the directory a reader is pointed at, with
    ``params={"subset": subset}``. Every other keyword is a reading, see
    :data:`READINGS`; the values used are returned in ``Export.readings``.
    """
    readings = {
        "scene_info_fields": scene_info_fields,
        "pose_timestamp": pose_timestamp,
        "lidar_file": lidar_file,
        "box_origin": box_origin,
        "type_case": type_case,
        "lidar_record": lidar_record,
        "points_frame": points_frame,
        "lidar_intrinsic": lidar_intrinsic,
        "visibility_filter": visibility_filter,
        "scene_name": scene_name,
    }
    for name, value in readings.items():
        if value not in READINGS[name]:
            raise ValueError(f"{name}={value!r}: expected one of {', '.join(READINGS[name])}")
    if subset not in SUBSET_FOLDERS:
        raise ValueError(f"subset={subset!r}: expected one of {', '.join(SUBSET_FOLDERS)}")
    vehicle = _first_of_kind(world, "vehicle")
    drone = _first_of_kind(world, "uav")
    stems = _frame_stems(world)
    release = root / SUBSET_FOLDERS[subset] / RELEASE_FOLDER

    sides = (
        (vehicle, VEHICLE_SIDE, VEHICLE_LIDAR, VEHICLE_CAMERAS, False),
        (drone, DRONE_SIDE, DRONE_LIDAR, DRONE_CAMERAS, True),
    )
    cameras: dict[tuple[str, str], str] = {}
    annotated: dict[tuple[str, int], tuple[int, ...]] = {}
    for agent, side, lidar_name, allowed, is_drone in sides:
        side_dir = release / side
        side_dir.mkdir(parents=True, exist_ok=True)
        _write_lidar_calib(side_dir, agent, lidar_name, lidar_intrinsic)
        names = _camera_names(agent, allowed, is_drone)
        for camera in agent.cameras:
            name = names[camera.sensor_id]
            cameras[(agent.agent_id, camera.sensor_id)] = name
            _write_camera(world, agent, camera, side_dir, name, stems)
        for frame, stem in enumerate(stems):
            _write_pose(
                side_dir / "pose" / f"{stem}.json",
                agent.world_from_body[frame],
                int(stem) if pose_timestamp == "frame" else None,
            )
            annotated[(agent.agent_id, frame)] = _write_labels(
                world,
                agent,
                frame,
                side_dir / "label" / f"{stem}.txt",
                box_origin=box_origin,
                type_case=type_case,
                visibility_filter=visibility_filter,
            )
            if not is_drone:
                _write_sweep(
                    world,
                    agent,
                    frame,
                    side_dir
                    / "lidar"
                    / VEHICLE_LIDAR
                    / f"{stem}.{'ply' if lidar_file == 'ply_ascii' else 'bin'}",
                    lidar_record=lidar_record,
                    points_frame=points_frame,
                )
        _write_scene_infos(side_dir, stems, scene_info_fields)

    objects = [
        obj
        for frame_objects in world.objects
        for obj in frame_objects
        if obj.category in _GRIFFIN_CLASSES
    ]
    expectation = Expectation(
        # The folder names (Dataset_Preparation.md lines 24 and 40).
        agents={vehicle.agent_id: VEHICLE_SIDE, drone.agent_id: DRONE_SIDE},
        lidars={vehicle.agent_id: VEHICLE_LIDAR, drone.agent_id: DRONE_LIDAR},
        cameras=cameras,
        annotated=annotated,
        # "a persistent ID" (paper §3.2), read as ``int(self.id)`` (data_utils.py
        # line 111): the world's object id, the same in both sides' files.
        track_ids={obj.object_id: str(obj.object_id) for obj in objects},
        categories={obj.category: _type_string(obj.category, type_case) for obj in objects},
        # "lidar: ... available only for the ground vehicle" (Dataset_Preparation.md
        # line 147): the drone's returns are not written.
        without_sweeps=frozenset({drone.agent_id}),
    )
    return Export(
        adapter=ADAPTER,
        raw_root=root,
        params={"subset": subset},
        scene_id=SCENE_NAME if scene_name == "name" else f"scene-{0:04}-{SCENE_NAME}",
        world=world_subset(world, (vehicle.agent_id, drone.agent_id)),
        expectation=expectation,
        readings=readings,
    )


def _first_of_kind(world: World, kind: str) -> Agent:
    for agent in world.agents:
        if agent.kind == kind:
            return agent
    raise ValueError(f"the world has no agent of kind {kind!r}; Griffin needs a vehicle and a uav")


def _frame_stems(world: World) -> list[str]:
    """The six-digit stem of every frame.

    "Input should be a 6-digit string, e.g., '000620'." (trans_kitti2nuscenes.py
    line 846). Stem ``n`` is at ``n * 0.1`` s (line 848), so frame ``i`` of a world
    with a 0.1 s period is stem ``i``; consecutive frames differ by exactly 1, which
    the converter needs to chain them (``int(self.frame_list[idx + 1]) - int(frame)
    == 1``, line 461).
    """
    if world.n_frames == 0:
        raise ValueError("the world has no frame")
    if abs(world.period_s - FRAME_PERIOD_S) > 1e-12:
        raise ValueError(f"Griffin frames are {FRAME_PERIOD_S} s apart, not {world.period_s} s")
    last = FIRST_FRAME + world.n_frames - 1
    if last > 999_999:
        raise ValueError(f"{world.n_frames} frames do not fit six-digit stems")
    return [f"{FIRST_FRAME + frame:06d}" for frame in range(world.n_frames)]


def _floats(values: Array) -> list[float]:
    """Plain Python floats, a negative zero written as 0.0."""
    return [float(value) + 0.0 for value in values.reshape(-1).tolist()]


def _rows(matrix: Array) -> list[list[float]]:
    """A matrix as a nested list of rows, read back by ``np.array(data[...])``."""
    return [_floats(row) for row in matrix]


def _write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _write_lidar_calib(side_dir: Path, agent: Agent, name: str, lidar_intrinsic: str) -> None:
    """``calib/lidar_top.json`` or ``calib/lidar_virtual.json``.

    "calib: Provides sensor calibration parameters, including intrinsics and
    extrinsics, in .json files." (Dataset_Preparation.md line 148.) The file is
    ``calib/{sensor}.json`` with an ``extrinsic`` (data_utils.py lines 211-222):
    "Matrix to project points from camera to Ego coordinate system, shape (4, 4)"
    (line 29), taken as ``T_sensor_to_ego = sensor_extrinsic`` (space_utils.py line
    15). The LiDAR's is ``top_lidar2ego_matrix`` and the drone's virtual one
    ``top_air_lidar2air_matrix`` (trans_kitti2nuscenes.py lines 216 and 232): both
    LiDAR to ego. The world's ``body_from_lidar`` is that matrix. The LiDAR frame is
    "FLU (R)" (paper Table 2), the world's x forward, y left, z up, so nothing is
    turned.
    """
    data: dict[str, object] = {"extrinsic": _rows(agent.lidar.body_from_lidar.matrix())}
    if lidar_intrinsic == "null":
        data["intrinsic"] = None
    _write_json(side_dir / "calib" / f"{name}.json", data)


def _camera_names(agent: Agent, allowed: Sequence[str], is_drone: bool) -> dict[str, str]:
    """The Griffin name of each camera of the agent, by the direction it faces.

    The release names its cameras by direction only (``front``, ``back``, ``left``,
    ``right``, and ``bottom`` on the drone, whose cameras are "five
    downward-oriented cameras", paper §3.1); their mounting angles are not documented.
    The world's cameras are named here by their optical axis in the body: a drone
    camera that looks at least 80° below the body's xy plane is ``bottom``; any other
    is ``front`` within 45° of body +x, ``left`` or ``right`` within 45° of ±y, else
    ``back``. This naming is this exporter's choice; the extrinsic in the calib file
    carries the camera's real pose either way.
    """
    names: dict[str, str] = {}
    for camera in agent.cameras:
        axis = camera.body_from_camera.rotation[:, 2]
        horizontal = math.hypot(float(axis[0]), float(axis[1]))
        elevation = math.degrees(math.atan2(float(axis[2]), horizontal))
        azimuth = math.degrees(math.atan2(float(axis[1]), float(axis[0])))
        if is_drone and elevation <= _BOTTOM_ELEVATION_DEG:
            name = "bottom"
        elif -45.0 <= azimuth <= 45.0:
            name = "front"
        elif 45.0 < azimuth < 135.0:
            name = "left"
        elif -135.0 < azimuth < -45.0:
            name = "right"
        else:
            name = "back"
        if name not in allowed:
            raise ValueError(f"camera {camera.sensor_id} of {agent.agent_id} has no Griffin name")
        if name in names.values():
            raise ValueError(f"two cameras of {agent.agent_id} would both be {name!r}")
        names[camera.sensor_id] = name
    return names


def _write_camera(
    world: World, agent: Agent, camera: Camera, side_dir: Path, name: str, stems: Sequence[str]
) -> None:
    """``calib/<name>.json`` and ``camera/<name>/{frame}.png``.

    The extrinsic is camera to ego (data_utils.py line 29, space_utils.py line 15).
    The camera frame is "Camera | RDF (R) | Camera optical center" (paper Table 2):
    x right, y down, z forward, the world's optical frame, so ``body_from_camera``
    is written as it is. The intrinsic is "Matrix to project points from camera to
    pixel coordinate system, shape (3, 3)" (data_utils.py line 30), applied as
    ``intrinsic @ pts_cam`` and divided by depth (space_utils.py lines 211-233): the
    pinhole matrix ``[[fx, 0, cx], [0, fy, cy], [0, 0, 1]]``. No distortion is
    written: the toolkit writes ``'camera_distortion': [],  # Placeholder``
    (trans_kitti2nuscenes.py line 394) and its projection applies none.

    Images are ``camera/{direction}/{frame}.png`` (data_utils.py line 261); the
    written docs give no file name.
    """
    _write_json(
        side_dir / "calib" / f"{name}.json",
        {
            "extrinsic": _rows(camera.body_from_camera.matrix()),
            "intrinsic": _rows(camera.intrinsic()),
        },
    )
    folder = side_dir / "camera" / name
    folder.mkdir(parents=True, exist_ok=True)
    for frame, stem in enumerate(stems):
        _write_image(
            folder / f"{stem}.png",
            camera,
            world.camera_truth(agent.agent_id, camera.sensor_id, frame),
        )


def _write_pose(path: Path, world_from_body: Pose, timestamp: int | None) -> None:
    """``pose/{frame}.json``: ``{"x", "y", "z", "roll", "pitch", "yaw"}``.

    "pose: Includes the ego-pose for each agent at every timestamp."
    (Dataset_Preparation.md line 150.) The file is ``pose/{frame}.json`` and the
    toolkit reads exactly ``data['x']``, ``['y']``, ``['z']``, ``['pitch']``,
    ``['roll']``, ``['yaw']`` (data_utils.py lines 234-248): "Stores Ego pose data in
    ENU coordinate system, xyz in meters, RPY euler angles in degrees." (line 40).
    The pose is ego to ENU: ``T_ego_to_ENU`` with ``t_ego_to_ENU = [x, y, z]``
    (space_utils.py lines 28-36), the world's ``world_from_body``.

    The world frame is "World | Geodetic | ENU (R) | Fixed reference point" (paper
    Table 2), and the release is in it: "converts all simulator-native 3D
    annotations into a unified right-handed coordinate system" (paper §3.2,
    Spatio-Temporal Alignment). The world is right-handed and z up; its x is taken as
    East and its y as North, so nothing is reflected. The ego frame is FLU, "Vehicle
    | FLU(R) | Vehicle center" and "Drone | FLU (R) | Drone center" (Table 2), the
    world's body frame.
    """
    roll, pitch, yaw = griffin_rpy_deg(world_from_body.rotation)
    x, y, z = _floats(world_from_body.translation)
    record: dict[str, object] = {"x": x, "y": y, "z": z, "roll": roll, "pitch": pitch, "yaw": yaw}
    if timestamp is not None:
        record["timestamp"] = timestamp
    _write_json(path, record)


def _type_string(category: str, type_case: str) -> str:
    return category.capitalize() if type_case == "capitalised" else category


def _write_labels(
    world: World,
    agent: Agent,
    frame: int,
    path: Path,
    *,
    box_origin: str,
    type_case: str,
    visibility_filter: str,
) -> tuple[int, ...]:
    """``label/{frame}.txt`` of one agent; returns the object ids it lists.

    "label: Contains frame-by-frame 3D bounding box annotations."
    (Dataset_Preparation.md line 149.) The file is ``label/{frame}.txt``, one object
    per line, fields split on whitespace in the order ``type x y z l w h roll pitch
    yaw id visibility`` (data_utils.py lines 141 and 153-172). "Stores object data in
    Ego coordinates, xyzlwh in meters, RPY euler angles in degrees, visibility in
    [0, 1]." (line 57.) The box pose is object to ego, built like the agent pose
    (``R_obj_to_ego = R.from_euler('xyz', [obj.roll, obj.pitch, obj.yaw],
    degrees=True)``, space_utils.py lines 48-50), so the angles are those of
    ``ego_from_box = inverse(world_from_body) ∘ world_from_box``, in the labelling
    agent's own ego frame: the drone's labels in the drone's (tilted) body, which the
    converter composes with the drone pose (``T_ann_to_ENU = T_air_to_ENU @
    T_ann_to_air``, trans_kitti2nuscenes.py lines 632-633).

    ``l`` runs along the box's x, ``w`` along its y, ``h`` along its z (``dx = obj.l
    / 2``, ``dy = obj.w / 2``, ``dz = obj.h / 2``, visual_kitti.py lines 53-55), the
    world's ``size_lwh``. ``id`` is the world's object id. The visibility is
    :func:`_visibility`; which objects are listed is ``visibility_filter``.
    """
    visibility = _visibility(world, agent, frame)
    body_from_world = agent.world_from_body[frame].inverse()
    lines: list[str] = []
    listed: list[int] = []
    for obj in world.objects[frame]:
        if obj.category not in _GRIFFIN_CLASSES:
            continue
        rate = visibility[obj.object_id]
        if visibility_filter == "visible" and rate <= 0.0:
            continue
        ego_from_box = body_from_world.compose(obj.world_from_box)
        length, width, height = (float(v) for v in obj.size_lwh)
        centre = ego_from_box.translation
        if box_origin == "bottom_center":
            centre = centre - ego_from_box.rotation[:, 2] * (height / 2.0)
        x, y, z = _floats(centre)
        roll, pitch, yaw = griffin_rpy_deg(ego_from_box.rotation)
        fields = [
            _type_string(obj.category, type_case),
            repr(x),
            repr(y),
            repr(z),
            repr(length),
            repr(width),
            repr(height),
            repr(roll),
            repr(pitch),
            repr(yaw),
            str(obj.object_id),
            repr(round(rate, 6)),
        ]
        lines.append(" ".join(fields) + "\n")
        listed.append(obj.object_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(lines), encoding="utf-8")
    return tuple(listed)


def _visibility(world: World, agent: Agent, frame: int) -> dict[int, float]:
    """The visibility rate of every object of the frame for one agent, in ``[0, 1]``.

    The paper: "we sample points within each ground-truth bounding box and project
    them onto the segmentation image. Visibility rates for each agent are then
    calculated by comparing semantic categories and instance IDs of the sampled
    pixels against the corresponding target's." (§3.2.) The sampling, the way the
    agent's cameras are combined and the threshold are not given. Here: 27 points on
    a grid inside the box (:data:`_SAMPLE_FRACTIONS`); a point counts when, in at
    least one of the agent's cameras, it lies in front of the camera and inside the
    image and the first box its line of sight meets is its own (what the
    segmentation pixel would show). The rate is the fraction of points that count.
    This is an approximation of the dataset's rule, not the rule itself.
    """
    objects = world.objects[frame]
    grid = np.array(list(itertools.product(_SAMPLE_FRACTIONS, repeat=3)), dtype=np.float64)
    points = np.concatenate(
        [obj.world_from_box.apply(grid * np.array(obj.size_lwh)) for obj in objects]
    )
    owners = np.repeat(np.array([obj.object_id for obj in objects], dtype=np.int64), len(grid))
    seen = np.zeros(len(points), dtype=np.bool_)
    for camera in agent.cameras:
        world_from_camera = world.world_from_camera(agent.agent_id, camera.sensor_id, frame)
        pixels, in_front = camera.project(world_from_camera.inverse().apply(points))
        in_image = (
            in_front
            & (pixels[:, 0] >= 0.0)
            & (pixels[:, 0] < camera.width)
            & (pixels[:, 1] >= 0.0)
            & (pixels[:, 1] < camera.height)
        )
        first = _first_hit_ids(world_from_camera.translation, points, objects)
        seen |= in_image & (first == owners)
    return {obj.object_id: float(seen[owners == obj.object_id].mean()) for obj in objects}


def _first_hit_ids(
    origin: Array, targets: Array, objects: Sequence[WorldObject]
) -> npt.NDArray[np.int64]:
    """For rays from ``origin`` through each target point, the id of the first box met.

    A slab test in each box's frame; 0 where no box is met. A box the origin lies in
    is met at once.
    """
    directions = targets - origin
    best_t = np.full(len(targets), np.inf)
    best_id = np.zeros(len(targets), dtype=np.int64)
    for obj in objects:
        box_from_world = obj.world_from_box.inverse()
        start = box_from_world.apply(origin)
        direction = directions @ box_from_world.rotation.T
        half = np.array(obj.size_lwh) / 2.0
        with np.errstate(divide="ignore", invalid="ignore"):
            t_low = (-half - start) / direction
            t_high = (half - start) / direction
        parallel = direction == 0.0
        missed = (parallel & (np.abs(start) > half)).any(axis=1)
        t_near = np.where(parallel, -np.inf, np.minimum(t_low, t_high))
        t_far = np.where(parallel, np.inf, np.maximum(t_low, t_high))
        enter = np.maximum(t_near.max(axis=1), 0.0)
        leave = t_far.min(axis=1)
        hit = (enter <= leave) & (leave > 0.0) & ~missed & (enter < best_t)
        best_t = np.where(hit, enter, best_t)
        best_id = np.where(hit, obj.object_id, best_id)
    return best_id


def _write_sweep(
    world: World,
    agent: Agent,
    frame: int,
    path: Path,
    *,
    lidar_record: str,
    points_frame: str,
) -> None:
    """``lidar/lidar_top/{frame}.bin`` of the vehicle.

    "lidar: Stores LiDAR point cloud data (.bin files), available only for the
    ground vehicle." (Dataset_Preparation.md line 147.) The file name inside
    ``lidar/lidar_top/`` is not documented: ``{frame}.bin`` with the six-digit stem
    is UNVERIFIED, read from the other folders' ``{frame}.json``, ``{frame}.txt`` and
    ``{frame}.png``. The record and the frame of the points are readings. The world's
    returns are in the LiDAR frame; their order is kept.
    """
    points = world.lidar_returns(agent.agent_id, frame).points
    if points_frame == "ego":
        points = agent.lidar.body_from_lidar.apply(points)
    xyz = np.ascontiguousarray(points, dtype="<f4").reshape(-1, 3)
    width = {"xyzi": 4, "xyz": 3, "xyz6": 6}[lidar_record]
    records = np.zeros((xyz.shape[0], width), dtype="<f4")
    records[:, :3] = xyz
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix == ".ply":
        header = (
            "ply\nformat ascii 1.0\n"
            f"element vertex {xyz.shape[0]}\n"
            "property float x\nproperty float y\nproperty float z\n"
            "property float I\nend_header\n"
        )
        rows = "".join(f"{x!r} {y!r} {z!r} 0.0\n" for x, y, z in xyz.astype(float).tolist())
        path.write_text(header + rows, encoding="ascii")
        return
    path.write_bytes(records.tobytes())


def _write_scene_infos(side_dir: Path, stems: Sequence[str], fields: str) -> None:
    """``scene_infos.json``: one entry for the one scene written.

    The toolkit reads a JSON list whose elements have ``scene_info['info']['frames']``
    (data_utils.py lines 118-129), ``scene_info["name"]`` and
    ``scene_info["info"]["weather"]`` (trans_kitti2nuscenes.py lines 712-716). The
    frames are the six-digit stem strings: the converter looks a pose file stem up in
    them, ``frame2scene[self.frame_list[idx]]`` (line 712), and ``frame_list`` holds
    the stems of ``pose/*.json`` (lines 100-105). Both sides get the same file.
    """
    info: dict[str, object] = {"frames": list(stems), "weather": SCENE_WEATHER}
    if fields == "with_platform":
        info.update({"map_name": "geomworld", "cruise_altitude": 0})
    _write_json(side_dir / "scene_infos.json", [{"name": SCENE_NAME, "info": info}])


def _write_image(path: Path, camera: Camera, truth: Mapping[int, CameraTruth]) -> None:
    """A PNG of the camera's size with the corners of the objects it sees as dots.

    A corner is drawn when it lies in front of the camera and inside the image; all
    corners of one object have one colour.
    """
    image = Image.new("RGB", (camera.width, camera.height), (0, 0, 0))
    draw = ImageDraw.Draw(image)
    for object_id, corners in truth.items():
        red, green, blue = colorsys.hsv_to_rgb((object_id * 0.618034) % 1.0, 0.85, 1.0)
        colour = (round(red * 255), round(green * 255), round(blue * 255))
        pixels = corners.pixels.tolist()
        for (u, v), in_front in zip(pixels, corners.in_front.tolist(), strict=True):
            if in_front and 0.0 <= u < camera.width and 0.0 <= v < camera.height:
                box = (
                    u - _DOT_RADIUS_PX,
                    v - _DOT_RADIUS_PX,
                    u + _DOT_RADIUS_PX,
                    v + _DOT_RADIUS_PX,
                )
                draw.ellipse(box, fill=colour)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, format="PNG")
