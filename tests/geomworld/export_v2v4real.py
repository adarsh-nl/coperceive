# SPDX-License-Identifier: Apache-2.0
# pyright: reportUnknownMemberType=false
"""The synthetic world written as a V2V4Real release in its OPV2V format.

Written from the V2V4Real paper, README, dataset page and issue threads, through this
workflow's cite-checked research notes; the adapter that reads V2V4Real, its fixtures and
the repository's convention pages were not opened. Facts that only the V2V4Real code
states are marked "code only" with the function they come from; no code was copied. The
PCD and YAML writers and the angle helpers are shared with OPV2V and V2XSet
(:mod:`tests.geomworld.opencood_format`).

Sources:

* [V2V4R] Xu et al., "V2V4Real", arXiv:2303.07601 (§3.1, §3.2, Table 2, App. B).
* [README] ucla-mobility/V2V4Real ``README.md``, 'Data Download'.
* [PAGE] V2V4Real dataset page (UCLA Mobility Lab), 'Download'.
* [#5], [#19], [#29], [#41]: issues of ucla-mobility/V2V4Real. #5 holds a user's excerpt
  of a released yaml, answered by the maintainer.

What is written under ``root`` (the release root that holds the split folders)::

    <split>/testoutput_CAV_data_2026-11-16-00-00-00_0/0/<stem>.pcd
    <split>/testoutput_CAV_data_2026-11-16-00-00-00_0/0/<stem>.yaml
    <split>/testoutput_CAV_data_2026-11-16-00-00-00_0/1/<stem>.pcd
    <split>/testoutput_CAV_data_2026-11-16-00-00-00_0/1/<stem>.yaml

[README] shows ``v2v4real/train/testoutput_CAV_data_2022-03-15-09-54-40_1`` with
``validate`` and ``test`` beside ``train``, "(OPV2V format)"; the adapter's ``split`` is
``train`` or ``test``. Agent folders: the code only requires names that parse as integers
(code only: basedataset.reinitialize); which names the release uses, and which is the
Tesla, is not documented. ``0`` is the world's first vehicle (the Tesla: "The origin of
the HDMap aligns with the initial frame of Tesla" [V2V4R §3.2]) and ``1`` its second (the
Ford Fusion). ``<stem>`` is ``000000``, ``000001``, ...: six digits as the loader's
docstring example ``'000078'`` (code only); the release's start and step are not
documented, and the loader only sorts the stems and pairs the agents by equal stems.

A frame's yaml holds ``ego_speed``, ``lidar_pose``, ``true_ego_pos`` and ``vehicles``
([#5] excerpt: "ego_speed, gps, lidar_pose, true_ego_pos, vehicles"). Not written:
``gps``, six unlabelled numbers whose meaning no source gives (the loader never reads
it); writing invented latitudes would be a fact made up.

What V2V4Real cannot hold, and so is neither written nor expected:

* the roadside unit and the aerial agent: the dataset is "two connected vehicles"
  (a Tesla and a Ford Fusion [V2V4R §3.1]). Only the world's first two vehicles are kept,
  with ``subset()``.
* cameras: "Camera Data: [next version]" [PAGE, 'Download']; no camera file, extrinsic
  or intrinsic is released. The kept agents are returned without their cameras.
* pedestrians: "There are five object classes in total, including cars, vans, pickup
  trucks, semi-truck, and buses." [V2V4R §3.2]. Their returns stay in the sweeps; they
  are in no ``vehicles`` list and not in ``annotated``.

Roll and pitch: the paper annotates a "7-degree-of-freedom 3D bounding box" with yaw
only [V2V4R §3.2], but the file's ``angle`` is a triple ``[roll, yaw, pitch]`` that the
loader composes in full (code only: box_utils.project_world_objects with
transformation_utils.x_to_world). The world's LiDARs are tilted, so every box has some
roll and pitch in its LiDAR's frame; they are written in the triple.

Where the sources are silent or disagree, :func:`export` takes a keyword; its default is
the reading the sources support best. :data:`READINGS` lists every keyword with its
values (default first) and :data:`UNVERIFIED` those whose default rests on code, an
inference or documents that disagree.

It imports nothing from ``coperceive``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

import numpy as np

from tests.geomworld.expectation import Expectation, Export
from tests.geomworld.opencood_format import (
    NumpyMatrix,
    check_period,
    check_readings,
    euler_deg,
    floats,
    matrix_rows,
    pose_six,
    stored,
    stored_points,
    write_pcd,
    write_yaml,
)
from tests.geomworld.scenes import subset
from tests.geomworld.world import Pose, World, WorldObject

ADAPTER = "v2v4real"
# A scenario folder named like [README]'s example.
SCENE_ID = "testoutput_CAV_data_2026-11-16-00-00-00_0"
# The adapter's ``split`` parameter -> the split folder [README, 'Data Download'].
SPLITS: Mapping[str, str] = {"train": "train", "test": "test"}
# Folder names of the two vehicles (see the module docstring).
AGENT_NAMES: tuple[str, str] = ("0", "1")
# "all the object ids in Tesla are labeled between 0 - 1000, while ids in Ford Fusion
# range from 1001 - 2000" [V2V4R §3.2].
FORD_ID_OFFSET = 1000

READINGS: Mapping[str, tuple[str, ...]] = {
    # Handedness of the files. No source states the axes or handedness of the LiDAR
    # frames or of the frame ``lidar_pose`` is in: the paper only names "the HDmap
    # coordinate and ECEF" and the two LiDAR frames [V2V4R §3.2], and the loader's
    # "carla world system" docstring is inherited from OpenCOOD (code only). ``right``:
    # real vehicles, no simulator, nothing that says left-handed; in the [#5] excerpt the
    # yaw of ``lidar_pose`` (132.7°) is close to the fourth ``gps`` value read as a
    # heading in radians (135.2°), which a mirrored frame would negate (weak: ``gps`` is
    # undocumented). ``left``: the files as CARLA's mirror image in y, like OPV2V.
    "handedness": ("right", "left"),
    # How a box's ``angle`` triple becomes a rotation: see
    # ``opencood_format.euler_matrix``. ``carla``: the loader's x_to_world,
    # Rz(yaw) Ry(-pitch) Rx(-roll) (code only). ``rzryrx``: Rz(yaw) Ry(pitch) Rx(roll). The
    # real annotations are yaw-only, so the data never tells them apart.
    "euler": ("carla", "rzryrx"),
    # How ``lidar_pose`` is written. ``numpy``: a 4 × 4 float64 array in PyYAML's numpy
    # tags, ``true_ego_pos`` an alias of it ([#5] excerpt; "3x3 rotation matrix, and 3x1
    # translation matrix" [#19]; "the LiDAR pose representation does not follow a 6-DoF
    # format" [#41]). ``list``: the same 4 × 4 as plain nested lists. ``six``: OPV2V's
    # ``[x, y, z, roll, yaw, pitch]`` in degrees, which [#41] says V2V4Real does not use
    # but the "(OPV2V format)" of [README] and the loader's add_loc_noise assume.
    "pose_encoding": ("numpy", "list", "six"),
    # Frame of a box. "We annotate the 3D bounding boxes separately based on each
    # vehicle's LiDAR coordinate system" [V2V4R §3.2]; the loader multiplies an object's
    # pose by the agent's LiDAR-to-ego matrix, not by a world pose (code only:
    # get_item_single_car, unless ``isSim``). ``world``: the map frame, as OPV2V.
    "box_frame": ("lidar", "world"),
    # Which point of a box ``location`` is. "x, y, z for the centroid position" [V2V4R
    # §3.2], and the [#5] excerpt has ``center: [0, 0, 0]``. ``bottom``: the centre of the
    # bottom face (``center`` still zero), as a KITTI-style reading would have it.
    "box_z": ("centroid", "bottom"),
    # Frame of the points of a .pcd: the agent's own LiDAR frame (code only: pcd_to_np
    # returns the points, which the loader then moves with ``lidar_pose``). ``world``:
    # the same returns already in the frame of ``lidar_pose`` (written unchanged).
    "points_frame": ("sensor", "world"),
    # How an object is keyed. The paper: per-vehicle id ranges (Tesla 0-1000, Ford
    # 1001-2000) and objects seen by both vehicles "assign[ed] the same object id"
    # [V2V4R §3.2]; the yaml has ``ass_id`` ([#5]: ``ass_id: -1``), which the loader uses
    # when it is not -1 and otherwise takes key + 100 * agent id (code only:
    # generate_object_center). ``shared``: the world id as the key in both files and as
    # ``ass_id``, so every rule gives one id. ``per_vehicle``: Tesla keys = world id,
    # Ford keys = 1000 + world id, ``ass_id`` = the world id for an object both vehicles
    # list (at any frame) and -1 otherwise.
    "object_ids": ("shared", "per_vehicle"),
    # Which objects a vehicle's file lists: no source states it. ``lidar_hit``: those
    # with at least one return of that vehicle's LiDAR (annotators label what that LiDAR
    # saw, "separately based on each vehicle's LiDAR" [V2V4R §3.2]; it is OPV2V's
    # documented rule). ``all``: every vehicle-class object of the frame.
    "annotation_rule": ("lidar_hit", "all"),
    # ``obj_type`` of the world's trucks. Only "Car" is documented ([#5] excerpt); the
    # strings of "vans, pickup trucks, semi-truck, and buses" [V2V4R §3.2] are not.
    # ``Truck``: written with that string. ``omit``: trucks left out like pedestrians.
    "truck_class": ("Truck", "omit"),
    # Encoding and fields of a .pcd: no source documents the PCD header of the release.
    # The loader reads points with Open3D and intensity from the first
    # colour channel (code only: pcd_utils.pcd_to_np), as OPV2V; see
    # ``opencood_format.CARLA_READINGS`` for the values.
    "pcd_encoding": ("ascii", "binary"),
    "pcd_fields": ("xyz_rgb", "xyz_intensity", "xyz"),
}
UNVERIFIED: frozenset[str] = frozenset(
    {
        "handedness",
        "euler",
        "points_frame",
        "object_ids",
        "annotation_rule",
        "truck_class",
        "pcd_encoding",
        "pcd_fields",
    }
)


def export(
    world: World,
    root: Path,
    *,
    split: str = "train",
    handedness: str = "right",
    euler: str = "carla",
    pose_encoding: str = "numpy",
    box_frame: str = "lidar",
    box_z: str = "centroid",
    points_frame: str = "sensor",
    object_ids: str = "shared",
    annotation_rule: str = "lidar_hit",
    truck_class: str = "Truck",
    pcd_encoding: str = "ascii",
    pcd_fields: str = "xyz_rgb",
) -> Export:
    """Write the world's first two vehicles under ``root`` as one V2V4Real scenario.

    ``root`` is the release root (it gets the split folder) and is what the reader is
    handed. Every other keyword is a reading, see :data:`READINGS`; the values used are
    returned in ``Export.readings``.
    """
    if split not in SPLITS:
        raise ValueError(f"split={split!r}: expected one of {', '.join(SPLITS)}")
    readings = {
        "handedness": handedness,
        "euler": euler,
        "pose_encoding": pose_encoding,
        "box_frame": box_frame,
        "box_z": box_z,
        "points_frame": points_frame,
        "object_ids": object_ids,
        "annotation_rule": annotation_rule,
        "truck_class": truck_class,
        "pcd_encoding": pcd_encoding,
        "pcd_fields": pcd_fields,
    }
    check_readings(readings, READINGS)
    # "We sample the frames at 10Hz" [V2V4R §3.1]; the files hold no time.
    check_period(world)
    vehicle_ids = [agent.agent_id for agent in world.agents if agent.kind == "vehicle"][:2]
    if len(vehicle_ids) < 2:
        raise ValueError("V2V4Real has two vehicles; the world has fewer")
    pair = subset(world, tuple(vehicle_ids))
    kept = replace(pair, agents=tuple(replace(agent, cameras=()) for agent in pair.agents))
    names = dict(zip(vehicle_ids, AGENT_NAMES, strict=True))

    # "obj_type: Car" [#5 excerpt]; trucks per ``truck_class``; no pedestrians.
    classes = {"car": "Car"} if truck_class == "omit" else {"car": "Car", "truck": truck_class}
    by_frame = [{obj.object_id: obj for obj in objects} for objects in kept.objects]
    listed: dict[tuple[str, int], tuple[int, ...]] = {}
    for agent_id in vehicle_ids:
        for frame in range(kept.n_frames):
            wanted = {i for i, obj in by_frame[frame].items() if obj.category in classes}
            if annotation_rule == "lidar_hit":
                ids = [i for i in kept.seen_ids(agent_id, frame) if i in wanted]
            else:
                ids = sorted(wanted)
            listed[(agent_id, frame)] = tuple(ids)
    keys, ass_ids, track_ids = _identities(vehicle_ids, listed, object_ids)

    scenario = root / SPLITS[split] / SCENE_ID
    for index, agent in enumerate(kept.agents):
        folder = scenario / AGENT_NAMES[index]
        folder.mkdir(parents=True, exist_ok=True)
        for frame in range(kept.n_frames):
            stem = f"{frame:06d}"
            world_from_lidar = kept.world_from_lidar(agent.agent_id, frame)
            points = kept.lidar_returns(agent.agent_id, frame).points
            if points_frame == "world":
                points = world_from_lidar.apply(points)
            write_pcd(
                folder / f"{stem}.pcd",
                stored_points(points, handedness),
                encoding=pcd_encoding,
                fields=pcd_fields,
            )
            vehicles: dict[object, object] = {}
            for object_id in listed[(agent.agent_id, frame)]:
                vehicles[keys[(agent.agent_id, object_id)]] = _vehicle_entry(
                    by_frame[frame][object_id],
                    world_from_lidar,
                    readings,
                    ass_id=ass_ids[(agent.agent_id, object_id)],
                    obj_type=classes[by_frame[frame][object_id].category],
                )
            # ``lidar_pose`` is the LiDAR's pose in the common frame: the loader takes
            # inv(lidar_pose_ego) @ lidar_pose_cav and applies it to the raw points (code
            # only: basedataset.reform_param). The same object twice makes
            # ``true_ego_pos`` an alias of it, as in [#5].
            lidar = stored(world_from_lidar, handedness)
            pose: object
            if pose_encoding == "numpy":
                pose = NumpyMatrix(lidar.matrix())
            elif pose_encoding == "list":
                pose = matrix_rows(lidar.matrix())
            else:
                pose = pose_six(lidar, euler)
            document: dict[object, object] = {
                # "ego_speed: 0" [#5 excerpt]; a user found it zero in every file [#29].
                "ego_speed": 0,
                "lidar_pose": pose,
                "true_ego_pos": pose,
                "vehicles": vehicles,
            }
            write_yaml(folder / f"{stem}.yaml", document)

    present = {obj.category for objects in kept.objects for obj in objects}
    expectation = Expectation(
        agents=names,
        # One LiDAR per vehicle ("Both vehicles are equipped with a Velodyne VLP-32 LiDAR
        # sensor" [V2V4R §3.1]) and no name for it: the agent's name stands for it.
        lidars=dict(names),
        cameras={},
        annotated=listed,
        track_ids=track_ids,
        categories={category: name for category, name in classes.items() if category in present},
    )
    return Export(
        adapter=ADAPTER,
        raw_root=root,
        params={"split": split},
        scene_id=SCENE_ID,
        world=kept,
        expectation=expectation,
        readings=readings,
    )


def _identities(
    agent_ids: list[str],
    listed: Mapping[tuple[str, int], tuple[int, ...]],
    object_ids: str,
) -> tuple[dict[tuple[str, int], int], dict[tuple[str, int], int], dict[int, str]]:
    """The key and ``ass_id`` of every (agent, object) and the track id of every object.

    The track id is the id the files give the object: ``ass_id`` where it is not -1,
    otherwise the key of the one vehicle that lists it (see ``object_ids``).
    """
    listers: dict[int, set[str]] = {}
    for (agent_id, _frame), ids in listed.items():
        for object_id in ids:
            listers.setdefault(object_id, set()).add(agent_id)
    keys: dict[tuple[str, int], int] = {}
    ass_ids: dict[tuple[str, int], int] = {}
    track_ids: dict[int, str] = {}
    for object_id, agents in sorted(listers.items()):
        if object_ids == "per_vehicle" and not 0 <= object_id <= FORD_ID_OFFSET:
            raise ValueError(f"object id {object_id} is outside the Tesla range 0-1000")
        for index, agent_id in enumerate(agent_ids):
            if agent_id not in agents:
                continue
            if object_ids == "shared":
                keys[(agent_id, object_id)] = object_id
                ass_ids[(agent_id, object_id)] = object_id
            else:
                keys[(agent_id, object_id)] = object_id + FORD_ID_OFFSET * index
                ass_ids[(agent_id, object_id)] = object_id if len(agents) == len(agent_ids) else -1
            if ass_ids[(agent_id, object_id)] != -1:
                track_ids[object_id] = str(ass_ids[(agent_id, object_id)])
            else:
                track_ids[object_id] = str(keys[(agent_id, object_id)])
    return keys, ass_ids, track_ids


def _vehicle_entry(
    obj: WorldObject,
    world_from_lidar: Pose,
    readings: Mapping[str, str],
    *,
    ass_id: int,
    obj_type: str,
) -> dict[str, object]:
    """One entry of ``vehicles``: ``angle``, ``ass_id``, ``center``, ``extent``,
    ``location``, ``obj_type``.

    The [#5] excerpt has, rounded: angle [0.0, 27.06, 0.0], ass_id -1, center [0, 0, 0],
    extent [2.035, 1.036, 0.850], location [53.37, 12.21, -1.46], obj_type Car. The angle
    triple is
    ``[roll, yaw, pitch]`` in degrees, yaw in the middle, and ``extent`` is half the size
    along the box's own x, y, z (code only: project_world_objects builds the pose
    ``location + center`` with ``angle`` and create_bbx the corners at ±extent; it matches
    the excerpt, where an extent of 2.04 m is half a car's length).
    """
    handedness = readings["handedness"]
    if readings["box_frame"] == "lidar":
        box = world_from_lidar.inverse().compose(obj.world_from_box)
    else:
        box = obj.world_from_box
    length, width, height = obj.size_lwh
    origin = box.translation
    if readings["box_z"] == "bottom":
        origin = origin - box.rotation @ np.array([0.0, 0.0, height / 2.0])
    written = stored(Pose(box.rotation, origin), handedness)
    roll, pitch, yaw = euler_deg(written.rotation, readings["euler"])
    return {
        "angle": [roll, yaw, pitch],
        "ass_id": ass_id,
        "center": [0.0, 0.0, 0.0],
        "extent": [float(length) / 2.0, float(width) / 2.0, float(height) / 2.0],
        "location": floats(written.translation),
        "obj_type": obj_type,
    }
