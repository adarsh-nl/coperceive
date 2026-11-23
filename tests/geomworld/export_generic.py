# SPDX-License-Identifier: Apache-2.0
# pyright: reportUnknownMemberType=false
"""The synthetic world written as a generic multi-view dataset (``coperceive-generic-1``).

This file was written from ``docs/spec/generic-multiview.md`` alone. The adapter that
reads the format, its tests and the example datasets were not opened, so a misreading
of the specification here is not shared with them. Every conversion below follows a
sentence of the specification, quoted next to it.

Where the specification is silent, or allows two ways of writing the same thing,
:func:`export` takes a keyword. Its default is the most literal reading; the other
values write the alternative. :data:`READINGS` lists every keyword and its values.

What is written under ``root`` (the dataset root; ``scene.yaml`` names every file)::

    scene.yaml
    timestamps.txt                         only with timing="timestamp_file"
    agents/<agent_id>/poses.csv            not for an agent written as fixed_pose
    agents/<agent_id>/boxes.csv
    agents/<agent_id>/lidar/{frame:06d}.bin            or .pcd
    agents/<agent_id>/camera_<camera_id>/{frame:06d}.png

It imports nothing from ``coperceive``.
"""

from __future__ import annotations

import colorsys
import csv
import math
from collections.abc import Mapping, Sequence
from pathlib import Path

import numpy as np
import yaml
from PIL import Image, ImageDraw

from tests.geomworld.expectation import Expectation, Export
from tests.geomworld.world import Agent, Array, Camera, CameraTruth, Pose, World

# "schema: coperceive-generic-1"
SCHEMA = "coperceive-generic-1"
SCENE_ID = "geomworld"

# Every keyword of :func:`export` that selects a reading, with the values it takes.
# The first value is the default: the most literal reading of the specification.
READINGS: Mapping[str, tuple[str, ...]] = {
    # "**Frames:** `timestamps_us` (integers, strictly increasing), a `timestamp_file`,
    # or `frame_rate_hz` + `frame_count` (time in µs)." Three ways to say the same
    # times. With ``frame_rate_hz`` the time of frame i is not written anywhere; it is
    # read here as round(i * 1e6 / frame_rate_hz), frame 0 at 0.
    "timing": ("timestamps_us", "frame_rate_hz", "timestamp_file"),
    # "a `fixed_pose` (same pose every frame) or `poses.csv`": an agent that does not
    # move is written as ``fixed_pose``, or like the others as a ``poses.csv``.
    "static_pose": ("fixed_pose", "poses_csv"),
    # "`body_from_sensor` as translation/rpy **or** a 4×4 `extrinsic` matrix".
    "sensor_extrinsic": ("body_from_sensor", "extrinsic"),
    # "PCD ASCII/binary with `x y z`, or a binary `.bin` of float32 `x y z i` records."
    # The ``.bin`` record is the only one whose bytes the specification spells out.
    "lidar_format": ("bin", "pcd_ascii", "pcd_binary"),
    # The specification does not say in which frame the points of a LiDAR file are.
    # ``sensor``: the frame ``body_from_sensor`` starts from. ``body`` and ``world``
    # write the same returns already moved into that frame (the extrinsic and the
    # agent pose are written unchanged).
    "points_frame": ("sensor", "body", "world"),
    # "x,y,z" of a box: the specification does not say which point of the box.
    # ``center``: its centre. ``bottom_center``: the point half a height below the
    # centre along world z.
    "box_origin": ("center", "bottom_center"),
    # A box row has ``yaw_deg`` only. ``yaw_only`` writes a tilted object with its
    # heading and drops its roll and pitch; ``omit`` leaves tilted objects out.
    "tilted_boxes": ("yaw_only", "omit"),
    # The specification does not say whether a sensor ``id`` has to be unique in the
    # scene or only in its agent. ``per_agent``: the world's ids (every agent has a
    # ``lidar``). ``scene_unique``: ``<agent_id>_<sensor_id>``.
    "sensor_ids": ("per_agent", "scene_unique"),
    # "World frame: right-handed, metres, +z up. `handedness: left` applies one
    # y-reflection (ADR 0012) to poses, extrinsics, points and boxes". ``right``
    # writes the world as it is. ``left`` writes its mirror image in y and declares
    # ``handedness: left``, so the reader's reflection has to give the world back.
    "handedness": ("right", "left"),
    # Only with handedness="left": what the reflection does to a camera extrinsic.
    # ``mirrored``: like every other pose, both of its frames are reflected, so the
    # stored camera frame is x right, y up, z forward. ``optical``: the camera frame
    # stays x right, y down, z forward and only the body side is reflected; that is
    # not a rotation, so it can only be written as the 4×4 ``extrinsic``.
    "left_camera": ("mirrored", "optical"),
}

# "`poses.csv` with header `timestamp_us,x,y,z,roll_deg,pitch_deg,yaw_deg`"
POSE_HEADER = ("timestamp_us", "x", "y", "z", "roll_deg", "pitch_deg", "yaw_deg")
# "`timestamp_us,track_id,category,x,y,z,length,width,height,yaw_deg`"
BOX_HEADER = (
    "timestamp_us",
    "track_id",
    "category",
    "x",
    "y",
    "z",
    "length",
    "width",
    "height",
    "yaw_deg",
)

# The y-reflection of ``handedness: left``: (x, y, z) -> (x, -y, z).
_MIRROR_Y: Array = np.diag([1.0, -1.0, 1.0])
# Roll or pitch below this many degrees is no tilt (an upright object has exactly 0).
_TILT_DEG = 1e-9
_DOT_RADIUS_PX = 2


def spec_rotation(roll_deg: float, pitch_deg: float, yaw_deg: float) -> Array:
    """The rotation matrix the specification gives to three angles.

    "translation is metres, angles degrees, composition ``Rz(yaw) Ry(pitch) Rx(roll)``
    (intrinsic z-y-x)." The elementary rotations are the right-handed ones; that is
    the sense in which the specification's own example holds: ``rotation_rpy_deg:
    [-90, 0, -90]`` is ``[[0, 0, 1], [-1, 0, 0], [0, -1, 0]]``.
    """
    roll, pitch, yaw = math.radians(roll_deg), math.radians(pitch_deg), math.radians(yaw_deg)
    cr, sr = math.cos(roll), math.sin(roll)
    cp, sp = math.cos(pitch), math.sin(pitch)
    cy, sy = math.cos(yaw), math.sin(yaw)
    about_z = np.array([[cy, -sy, 0.0], [sy, cy, 0.0], [0.0, 0.0, 1.0]])
    about_y = np.array([[cp, 0.0, sp], [0.0, 1.0, 0.0], [-sp, 0.0, cp]])
    about_x = np.array([[1.0, 0.0, 0.0], [0.0, cr, -sr], [0.0, sr, cr]])
    return about_z @ about_y @ about_x


def spec_rpy_deg(rotation: Array) -> tuple[float, float, float]:
    """``(roll, pitch, yaw)`` in degrees such that :func:`spec_rotation` gives ``rotation``.

    "The rpy order is ``[roll, pitch, yaw]`` degrees." Read off the product
    ``Rz(yaw) Ry(pitch) Rx(roll)``, whose first column is ``(cy cp, sy cp, -sp)`` and
    whose last row is ``(-sp, cp sr, cp cr)``.
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
    if not np.allclose(spec_rotation(*angles), rotation, rtol=0.0, atol=1e-9):
        raise ValueError("roll, pitch and yaw cannot express this matrix: it is not a rotation")
    return angles


def export(
    world: World,
    root: Path,
    *,
    timing: str = "timestamps_us",
    static_pose: str = "fixed_pose",
    sensor_extrinsic: str = "body_from_sensor",
    lidar_format: str = "bin",
    points_frame: str = "sensor",
    box_origin: str = "center",
    tilted_boxes: str = "yaw_only",
    sensor_ids: str = "per_agent",
    handedness: str = "right",
    left_camera: str = "mirrored",
) -> Export:
    """Write ``world`` under ``root`` as one generic multi-view dataset.

    ``root`` is the dataset root: it gets ``scene.yaml`` and is the directory a reader
    of the format is pointed at. Every keyword is a reading, see :data:`READINGS`;
    the values used are returned in ``Export.readings``.
    """
    readings = {
        "timing": timing,
        "static_pose": static_pose,
        "sensor_extrinsic": sensor_extrinsic,
        "lidar_format": lidar_format,
        "points_frame": points_frame,
        "box_origin": box_origin,
        "tilted_boxes": tilted_boxes,
        "sensor_ids": sensor_ids,
        "handedness": handedness,
        "left_camera": left_camera,
    }
    for name, value in readings.items():
        if value not in READINGS[name]:
            raise ValueError(f"{name}={value!r}: expected one of {', '.join(READINGS[name])}")
    agent_ids = [agent.agent_id for agent in world.agents]
    if len(set(agent_ids)) != len(agent_ids):
        raise ValueError(f"agent ids are not unique: {agent_ids}")
    left = handedness == "left"
    stamps = _timestamps_us(world)
    root.mkdir(parents=True, exist_ok=True)

    # "handedness: right                 # right | left"
    document: dict[str, object] = {
        "schema": SCHEMA,
        "scene_id": SCENE_ID,
        "handedness": handedness,
    }
    if timing == "frame_rate_hz":
        # "frame_rate_hz: 10                 # OR timestamps_us: [...]"
        # "frame_count: 3                    # required with frame_rate_hz"
        document["frame_rate_hz"] = _frame_rate_hz(world, stamps)
        document["frame_count"] = world.n_frames
    elif timing == "timestamp_file":
        # "timestamp_file: timestamps.txt  # one integer µs per line"
        document["timestamp_file"] = "timestamps.txt"
        lines = "".join(f"{stamp}\n" for stamp in stamps)
        (root / "timestamps.txt").write_text(lines, encoding="utf-8")
    else:
        # "`timestamps_us` (integers, strictly increasing)"
        document["timestamps_us"] = stamps

    entries: list[dict[str, object]] = []
    lidars: dict[str, str] = {}
    cameras: dict[tuple[str, str], str] = {}
    annotated: dict[tuple[str, int], tuple[int, ...]] = {}
    for agent in world.agents:
        local_ids = [agent.lidar.sensor_id, *(camera.sensor_id for camera in agent.cameras)]
        if len(set(local_ids)) != len(local_ids):
            raise ValueError(f"sensor ids of agent {agent.agent_id} are not unique: {local_ids}")
        folder = f"agents/{agent.agent_id}"
        (root / folder).mkdir(parents=True, exist_ok=True)

        # "role: vehicle" / "word matching maps a role containing `vehicle`,
        # `uav`/`drone`, or `infrastructure`/`roadside`/`fixed` to the schema type":
        # the world's kinds (vehicle, infrastructure, uav) are three of those words.
        entry: dict[str, object] = {"id": agent.agent_id, "role": agent.kind}

        # "fixed_pose:                   # exactly one of fixed_pose / poses"
        if static_pose == "fixed_pose" and _is_static(agent.world_from_body):
            entry["fixed_pose"] = _translation_rpy(_stored(agent.world_from_body[0], left))
        else:
            entry["poses"] = f"{folder}/poses.csv"
            _write_csv(root / folder / "poses.csv", POSE_HEADER, _pose_rows(agent, stamps, left))

        # "boxes: ego/boxes.csv"
        entry["boxes"] = f"{folder}/boxes.csv"
        rows, listed = _box_rows(
            world, agent, stamps, left=left, box_origin=box_origin, tilted_boxes=tilted_boxes
        )
        _write_csv(root / folder / "boxes.csv", BOX_HEADER, rows)
        annotated.update(listed)

        sensors: list[dict[str, object]] = []
        lidar_id = _sensor_id(agent.agent_id, agent.lidar.sensor_id, sensor_ids)
        lidars[agent.agent_id] = lidar_id
        sensors.append(
            _lidar_sensor(
                world,
                agent,
                root,
                lidar_id,
                lidar_format=lidar_format,
                points_frame=points_frame,
                sensor_extrinsic=sensor_extrinsic,
                left=left,
            )
        )
        for camera in agent.cameras:
            camera_id = _sensor_id(agent.agent_id, camera.sensor_id, sensor_ids)
            cameras[(agent.agent_id, camera.sensor_id)] = camera_id
            sensors.append(
                _camera_sensor(
                    world,
                    agent,
                    camera,
                    root,
                    camera_id,
                    sensor_extrinsic=sensor_extrinsic,
                    left=left,
                    left_camera=left_camera,
                )
            )
        entry["sensors"] = sensors
        entries.append(entry)
    document["agents"] = entries

    # "One small `scene.yaml` describes **any** set of viewpoints".
    text = yaml.safe_dump(document, sort_keys=False, default_flow_style=None, allow_unicode=True)
    (root / "scene.yaml").write_text(text, encoding="utf-8")

    objects = [obj for frame_objects in world.objects for obj in frame_objects]
    expectation = Expectation(
        agents={agent_id: agent_id for agent_id in agent_ids},
        lidars=lidars,
        cameras=cameras,
        annotated=annotated,
        # "no track/category is required": both columns are free strings. The track
        # id is the object id, the category the world's class name.
        track_ids={obj.object_id: str(obj.object_id) for obj in objects},
        categories={obj.category: obj.category for obj in objects},
    )
    return Export(
        adapter="generic",
        # "`coperceive adapters check --format generic <dir>`": the dataset root.
        raw_root=root,
        params={},
        scene_id=SCENE_ID,
        world=world,
        expectation=expectation,
        readings=readings,
    )


def _timestamps_us(world: World) -> list[int]:
    """Frame ``i`` is at ``i * period_s`` seconds, written in integer microseconds.

    "`timestamps_us` (integers, strictly increasing)".
    """
    if world.n_frames == 0:
        raise ValueError("the world has no frame")
    stamps = [round(frame * world.period_s * 1_000_000) for frame in range(world.n_frames)]
    if any(later <= earlier for earlier, later in zip(stamps, stamps[1:], strict=False)):
        raise ValueError(f"a period of {world.period_s} s does not give increasing microseconds")
    return stamps


def _frame_rate_hz(world: World, stamps: Sequence[int]) -> int | float:
    """The frame rate that says the same times as ``stamps``.

    "`frame_rate_hz` + `frame_count` (time in µs)": the time of a frame is not given
    by a formula. It is read as ``round(i * 1e6 / frame_rate_hz)`` with frame 0 at 0,
    and a world whose times that reading does not reproduce is refused.
    """
    rate = 1.0 / world.period_s
    value: int | float = round(rate) if abs(rate - round(rate)) < 1e-9 else rate
    if [round(frame * 1_000_000 / value) for frame in range(len(stamps))] != list(stamps):
        raise ValueError(f"a period of {world.period_s} s cannot be written as frame_rate_hz")
    return value


def _is_static(poses: Sequence[Pose]) -> bool:
    """True when every frame has the first pose: "a `fixed_pose` (same pose every frame)"."""
    first = poses[0]
    return all(
        np.array_equal(pose.rotation, first.rotation)
        and np.array_equal(pose.translation, first.translation)
        for pose in poses[1:]
    )


def _stored(pose: Pose, left: bool) -> Pose:
    """The pose as the files hold it.

    "`handedness: left` applies one y-reflection (ADR 0012) to poses, extrinsics,
    points and boxes, so a left-handed source can say so instead of converting
    files." A left-handed file is therefore the mirror image in y of the right-handed
    world: with ``S = diag(1, -1, 1)`` a pose ``(R, t)`` between two reflected frames
    is ``(S R S, S t)``. In angles that is roll and yaw negated, pitch kept.
    """
    if not left:
        return pose
    return Pose(_MIRROR_Y @ pose.rotation @ _MIRROR_Y, _MIRROR_Y @ pose.translation)


def _floats(values: Array) -> list[float]:
    """Plain Python floats, a negative zero written as 0.0."""
    return [float(value) + 0.0 for value in values.tolist()]


def _translation_rpy(pose: Pose) -> dict[str, object]:
    """``{translation_m, rotation_rpy_deg}`` of a ``parent_from_child`` pose.

    "translation_m: [0, -3, 2]" / "rotation_rpy_deg: [0, 0, 0]"; "translation is
    metres, angles degrees"; "The rpy order is `[roll, pitch, yaw]` degrees."
    """
    return {
        "translation_m": _floats(pose.translation),
        "rotation_rpy_deg": list(spec_rpy_deg(pose.rotation)),
    }


def _matrix_rows(rotation: Array, translation: Array) -> list[list[float]]:
    """A 4 × 4 matrix as four rows.

    "a 4×4 `extrinsic` matrix (row-major, sensor→body)", written like the example
    "`[[0,0,1,0], [-1,0,0,0], [0,-1,0,0], [0,0,0,1]]`".
    """
    rows = [[*_floats(rotation[index]), float(translation[index]) + 0.0] for index in range(3)]
    rows.append([0.0, 0.0, 0.0, 1.0])
    return rows


def _extrinsic(pose: Pose, sensor_extrinsic: str) -> dict[str, object]:
    """The extrinsic of a sensor in the form ``sensor_extrinsic`` selects.

    "**Sensor extrinsic:** `body_from_sensor` as translation/rpy **or** a 4×4
    `extrinsic` matrix (row-major, sensor→body)." The world's sensor poses are
    ``body_from_sensor`` already, so nothing is inverted.
    """
    if sensor_extrinsic == "extrinsic":
        return {"extrinsic": _matrix_rows(pose.rotation, pose.translation)}
    return {"body_from_sensor": _translation_rpy(pose)}


def _sensor_id(agent_id: str, sensor_id: str, sensor_ids: str) -> str:
    return sensor_id if sensor_ids == "per_agent" else f"{agent_id}_{sensor_id}"


def _write_csv(path: Path, header: Sequence[str], rows: Sequence[Sequence[str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(header)
        writer.writerows(rows)


def _pose_rows(agent: Agent, stamps: Sequence[int], left: bool) -> list[list[str]]:
    """One row per frame of ``poses.csv``.

    "Pose is `world_from_agent_body`; translation is metres, angles degrees,
    composition `Rz(yaw) Ry(pitch) Rx(roll)` (intrinsic z-y-x). Pose timestamps must
    match the scene timestamps exactly". The world's ``world_from_body`` is that
    pose; its angles are taken from its matrix and written in degrees.
    """
    rows: list[list[str]] = []
    for stamp, pose in zip(stamps, agent.world_from_body, strict=True):
        stored = _stored(pose, left)
        x, y, z = _floats(stored.translation)
        roll, pitch, yaw = spec_rpy_deg(stored.rotation)
        rows.append([str(stamp), repr(x), repr(y), repr(z), repr(roll), repr(pitch), repr(yaw)])
    return rows


def _box_rows(
    world: World,
    agent: Agent,
    stamps: Sequence[int],
    *,
    left: bool,
    box_origin: str,
    tilted_boxes: str,
) -> tuple[list[list[str]], dict[tuple[str, int], tuple[int, ...]]]:
    """The rows of an agent's ``boxes.csv`` and the object ids listed at each frame.

    "**Boxes CSV** (optional, per agent):
    `timestamp_us,track_id,category,x,y,z,length,width,height,yaw_deg`, in the
    **world** frame." The objects are those the agent's LiDAR hits at least once. A
    box of the world is already in the world frame, so its pose is written as it is:
    ``x, y, z`` its centre in metres, ``length, width, height`` its size along its
    own x, y and z, ``yaw_deg`` the yaw of ``Rz(yaw) Ry(pitch) Rx(roll)``, the angle
    from world +x to the box's length axis about world +z. There is no column for
    roll and pitch.
    """
    rows: list[list[str]] = []
    listed: dict[tuple[str, int], tuple[int, ...]] = {}
    for frame, stamp in enumerate(stamps):
        by_id = {obj.object_id: obj for obj in world.objects[frame]}
        written: list[int] = []
        for object_id in world.seen_ids(agent.agent_id, frame):
            obj = by_id[object_id]
            stored = _stored(obj.world_from_box, left)
            roll, pitch, yaw = spec_rpy_deg(stored.rotation)
            if tilted_boxes == "omit" and (abs(roll) > _TILT_DEG or abs(pitch) > _TILT_DEG):
                continue
            length, width, height = obj.size_lwh
            x, y, z = _floats(stored.translation)
            if box_origin == "bottom_center":
                # A row has a yaw only, so its box stands upright: the bottom is half
                # a height below the centre along world z.
                z -= height / 2.0
            rows.append(
                [
                    str(stamp),
                    str(object_id),
                    obj.category,
                    repr(x),
                    repr(y),
                    repr(z),
                    repr(float(length)),
                    repr(float(width)),
                    repr(float(height)),
                    repr(yaw),
                ]
            )
            written.append(object_id)
        listed[(agent.agent_id, frame)] = tuple(written)
    return rows, listed


def _lidar_sensor(
    world: World,
    agent: Agent,
    root: Path,
    sensor_id: str,
    *,
    lidar_format: str,
    points_frame: str,
    sensor_extrinsic: str,
    left: bool,
) -> dict[str, object]:
    """Write the sweeps of the agent's LiDAR and return its ``sensors`` entry."""
    extension = "bin" if lidar_format == "bin" else "pcd"
    # "**Templates are free-form**: they are relative to the dataset root, must stay
    # inside it, and must contain exactly the single field `{frame:06d}`, starting at
    # frame 0."
    template = f"agents/{agent.agent_id}/lidar/{{frame:06d}}.{extension}"
    for frame in range(world.n_frames):
        # The returns are in the LiDAR frame (x forward, y left, z up), the frame
        # ``body_from_sensor`` starts from. The order of the returns is kept.
        points = world.lidar_returns(agent.agent_id, frame).points
        if points_frame == "body":
            points = agent.lidar.body_from_lidar.apply(points)
        elif points_frame == "world":
            points = world.world_from_lidar(agent.agent_id, frame).apply(points)
        if left:
            # "`handedness: left` applies one y-reflection [...] to [...] points".
            points = points @ _MIRROR_Y
        path = root / template.format(frame=frame)
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_points(path, points, lidar_format)
    return {
        "id": sensor_id,
        "modality": "lidar",
        "files": template,
        **_extrinsic(_stored(agent.lidar.body_from_lidar, left), sensor_extrinsic),
    }


def _write_points(path: Path, points: Array, lidar_format: str) -> None:
    """One sweep, one point per return, in the order given.

    "**LiDAR:** PCD ASCII/binary with `x y z`, or a binary `.bin` of float32
    `x y z i` records."
    """
    xyz = np.ascontiguousarray(points, dtype="<f4").reshape(-1, 3)
    count = int(xyz.shape[0])
    if lidar_format == "bin":
        # float32 x y z i, one record after the other, little-endian; the world has
        # no intensity, so i is 0.
        records = np.zeros((count, 4), dtype="<f4")
        records[:, :3] = xyz
        path.write_bytes(records.tobytes())
        return
    # A PCD v0.7 file with the fields "x y z" and nothing else, unorganised
    # (HEIGHT 1), the identity as its viewpoint.
    header = (
        "# .PCD v0.7 - Point Cloud Data file format\n"
        "VERSION 0.7\n"
        "FIELDS x y z\n"
        "SIZE 4 4 4\n"
        "TYPE F F F\n"
        "COUNT 1 1 1\n"
        f"WIDTH {count}\n"
        "HEIGHT 1\n"
        "VIEWPOINT 0 0 0 1 0 0 0\n"
        f"POINTS {count}\n"
        f"DATA {'ascii' if lidar_format == 'pcd_ascii' else 'binary'}\n"
    ).encode("ascii")
    if lidar_format == "pcd_ascii":
        # Nine significant digits give a float32 back exactly.
        body = "".join(f"{x:.9g} {y:.9g} {z:.9g}\n" for x, y, z in xyz.tolist()).encode("ascii")
    else:
        body = xyz.tobytes()
    path.write_bytes(header + body)


def _camera_sensor(
    world: World,
    agent: Agent,
    camera: Camera,
    root: Path,
    sensor_id: str,
    *,
    sensor_extrinsic: str,
    left: bool,
    left_camera: str,
) -> dict[str, object]:
    """Write the images of one camera and return its ``sensors`` entry."""
    template = f"agents/{agent.agent_id}/camera_{camera.sensor_id}/{{frame:06d}}.png"
    for frame in range(world.n_frames):
        path = root / template.format(frame=frame)
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_image(path, camera, world.camera_truth(agent.agent_id, camera.sensor_id, frame))
    entry: dict[str, object] = {
        "id": sensor_id,
        "modality": "camera",
        "files": template,
        # "**Cameras:** `intrinsic: [fx, fy, cx, cy]` and optional
        # `image_size: [width, height]`; files PNG/JPEG." With "`u = fx·x/z + cx`,
        # `v = fy·y/z + cy`", the world's own model: the four numbers are unchanged.
        "intrinsic": [float(camera.fx), float(camera.fy), float(camera.cx), float(camera.cy)],
        "image_size": [int(camera.width), int(camera.height)],
    }
    # "**Camera frame:** depth is **+Z**, `x` right and `y` down" is the world's
    # optical frame, and the extrinsic is body ← camera ("R(body ← camera) =
    # [[0, 0, 1], [-1, 0, 0], [0, -1, 0]]" for a camera that looks along body +x, the
    # world's ``FORWARD_CAMERA``). ``body_from_camera`` is therefore written as it is.
    pose = camera.body_from_camera
    if left and left_camera == "optical":
        # Only the body side is reflected: (S R, S t). Its determinant is -1, so no
        # roll, pitch and yaw express it and the matrix form is the only one.
        entry["extrinsic"] = _matrix_rows(_MIRROR_Y @ pose.rotation, _MIRROR_Y @ pose.translation)
    else:
        entry.update(_extrinsic(_stored(pose, left), sensor_extrinsic))
    return entry


def _write_image(path: Path, camera: Camera, truth: Mapping[int, CameraTruth]) -> None:
    """A PNG of the camera's size with the corners of the objects it sees as dots.

    A corner is drawn when it lies in front of the camera and inside the image; all
    corners of one object have one colour. A mirrored (left-handed) export has the
    same images: a reflection of the coordinates does not change what a camera sees.
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
    image.save(path, format="PNG")
