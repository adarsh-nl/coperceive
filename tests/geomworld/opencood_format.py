# SPDX-License-Identifier: Apache-2.0
# pyright: reportUnknownMemberType=false
"""What the OPV2V, V2XSet and V2V4Real exporters share: OpenCOOD-format files.

The three datasets are read by one code base (OpenCOOD, and its fork in the V2V4Real
repository) and share one layout: a split folder, one folder per scenario, one folder
per agent named by an integer, and per frame ``<stem>.yaml`` beside ``<stem>.pcd`` (and,
for the CARLA datasets, ``<stem>_camera0.png`` .. ``<stem>_camera3.png``). This module
holds what the three exporters have in common:

* the CARLA frame and rotation, for OPV2V and V2XSet, which were rendered in CARLA
  (:func:`handedness_map`, :func:`stored`, :func:`euler_matrix`, :func:`euler_deg`);
* the PCD writer (:func:`write_pcd`), written from the PCD format description;
* the PNG with the projected box corners drawn (:func:`write_corner_image`);
* the YAML writer, including the numpy-tagged 4 × 4 arrays of V2V4Real
  (:class:`NumpyMatrix`, :func:`write_yaml`);
* the OPV2V/V2XSet scenario writer (:func:`write_carla_scenario`) and its readings
  (:data:`CARLA_READINGS`).

Clean room: this was written from the datasets' documentation and papers, the CARLA
documentation and this workflow's research notes (only the claims their cite-check marks
supported or partly supported). The adapters that read these formats, their fixtures and
the repository's pages on their conventions were not opened. A fact that only a
toolkit's code states is marked "code only" with the function it comes from; no code was
copied from OpenCOOD, v2x-vit, OpenCDA or CARLA.

Sources (each use gives its locator):

* [OC-intro] OpenCOOD docs, "Data Preparation", ``docs/md_files/data_intro.md``.
* [OC-ann] OpenCOOD docs, "Tutorial 2: Data Annotation Introduction",
  ``docs/md_files/data_annotation_tutorial.md``.
* [VIT-intro] v2x-vit repository, ``docs/data_intro.md`` (V2XSet).
* [OPV2V] Xu et al., "OPV2V", arXiv:2109.07644. [V2X-ViT] Xu et al., arXiv:2203.10638.
  [V2V4R] Xu et al., "V2V4Real", arXiv:2303.07601.
* [CARLA-api] CARLA 0.9.12 Python API reference. [CARLA-sensors] CARLA 0.9.12 sensors
  reference. [CARLA-coords] CARLA (latest), "Coordinates and transformations".
* [PCD] PCL documentation, "The PCD (Point Cloud Data) file format".

It imports nothing from ``coperceive``.
"""

from __future__ import annotations

import base64
import colorsys
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import yaml
from PIL import Image, ImageDraw

from tests.geomworld.expectation import Expectation
from tests.geomworld.world import (
    FORWARD_CAMERA,
    Agent,
    Array,
    Camera,
    CameraTruth,
    Pose,
    World,
    WorldObject,
    rot_x,
    rot_y,
    rot_z,
)

# "The sensor data is streamed at 20 Hz and recorded at 10 Hz." [OPV2V §III-A]; "We
# record LiDAR point clouds at 10 Hz" [V2X-ViT App. C]; "We sample the frames at 10Hz"
# [V2V4R §3.1]. No file of the three datasets holds a time: a frame is known by its file
# name only ("Those timestamps are represented by five digits integers as the prefix of
# the filenames" [OC-intro §3]). A world with another period cannot be written.
PERIOD_S = 0.1

# The world's classes that are vehicles. OPV2V and V2XSet annotate vehicles only ("the
# surrounding vehicles that have at least one LiDAR point hit from the agent" [OC-ann,
# ``vehicles``]); V2V4Real has "five object classes in total, including cars, vans,
# pickup trucks, semi-truck, and buses" [V2V4R §3.2]. A pedestrian is written by none.
VEHICLE_CLASSES: tuple[str, ...] = ("car", "truck")

# "`camera0`, `camera1`, `camera2`, and `camera3` represent the front, right rear, left
# rear, and back cameras respectively." [OC-intro §3.2; VIT-intro §3.2]
CAMERA_SLOTS: tuple[str, ...] = ("camera0", "camera1", "camera2", "camera3")

# The y-reflection between the world's right-handed numbers and CARLA's left-handed ones.
MIRROR_Y: Array = np.diag([1.0, -1.0, 1.0])
_IDENTITY: Array = np.eye(3)
_DOT_RADIUS_PX = 2

# Every keyword of :func:`write_carla_scenario` that selects a reading, with its values;
# the first value is the default. ``UNVERIFIED`` lists those whose default no written
# documentation settles (it rests on a toolkit's code, on an inference, or the documents
# disagree). OPV2V and V2XSet take the same readings.
CARLA_READINGS: Mapping[str, tuple[str, ...]] = {
    # "CARLA uses the Unreal Engine coordinates system. This is a Z-up left-handed
    # system." [CARLA-api, carla.Rotation]; the poses are "under CARLA map coordinate
    # system" [OC-ann, ``lidar_pose``]; asked whether the OPV2V frame is left-handed, the
    # OpenCOOD owner answered "yes" [OpenCOOD issue #67]. ``left``: every pose, box and
    # point is written as the world's mirror image in y (see :func:`handedness_map`).
    # ``right``: the world's numbers unchanged.
    "handedness": ("left", "right"),
    # How a stored angle triple becomes a rotation (see :func:`euler_matrix`). ``carla``:
    # CARLA's own matrix, Rz(yaw) Ry(-pitch) Rx(-roll) in right-handed elementary
    # rotations (code only). ``rzryrx``: the textbook Rz(yaw) Ry(pitch) Rx(roll). They
    # differ only where roll or pitch is not zero.
    "euler": ("carla", "rzryrx"),
    # Which point of a listed vehicle ``location`` is, and what ``center`` adds to it.
    # "location: # x, y ,z position of the center in the frontal axis of the vehicle"
    # and "center: # the relative position from bounding box center to the frontal axis
    # of this vehicle" [OC-ann, ``vehicles``]. In CARLA the bounding box's "location and
    # rotation are relative to the vehicle it is attached to" [CARLA-api,
    # carla.Vehicle.bounding_box], and a vehicle's origin is "very close to the lowest
    # face of the bounding box in Z" [CARLA-coords, 'Actor coordinates'].
    # ``actor_origin``: ``location`` the centre of the bottom face, ``center`` = (0, 0,
    # h/2) in the vehicle's own frame, so the centre is location + R center.
    # ``actor_origin_world_offset``: the same ``center`` added along the map axes, which
    # is how the OpenCOOD loader sums them (code only: box_utils.project_world_objects
    # adds location and center component-wise); the two differ for a tilted box.
    # ``centre``: ``location`` the box centre and ``center`` zero; every reading of the
    # sum agrees on it.
    "box_location": ("actor_origin", "actor_origin_world_offset", "centre"),
    # Frame of the points of a .pcd. "All the sensors use the UE coordinate system
    # (x-forward, y-right, z-up), and return coordinates in local space."
    # [CARLA-sensors, 'Important' note, 0.9.11 and later]; the OpenCOOD loader moves them
    # with ``lidar_pose`` (code only). Which CARLA version rendered the release is not
    # stated, and before 0.9.10 the LiDAR frame differed (CARLA changelog 0.9.10). ``body``
    # and ``world`` write the same returns already moved into the vehicle or the map frame
    # (``lidar_pose`` is written unchanged).
    "points_frame": ("sensor", "body", "world"),
    # Direction of a camera's ``extrinsic``. The docs label it "extrinsic matrix from
    # camera to LiDAR" [OC-ann, ``camera0``], but their own example numbers (translation
    # about (-3.0, 0, 0.9) for the front camera, recomputed from the example ``cords`` and
    # ``lidar_pose``) and the dumper ("dump extrinsic matrix lidar2camera", OpenCDA
    # data_dumper.py, code only) map LiDAR points into the camera.
    "camera_extrinsic": ("lidar_to_camera", "camera_to_lidar"),
    # Axes of the camera side of ``extrinsic``. In the docs example the front camera's
    # extrinsic rotation is the identity while camera and LiDAR have the same yaw [OC-ann,
    # ``camera0``], so it is the camera's sensor frame (x forward, y right, z up), not the
    # optical frame. ``optical``: the camera side is x right, y down, z forward, so K @
    # extrinsic projects LiDAR points directly. ``cords`` is six angles either way and
    # always the sensor frame (the optical frame is not a rotation of the map's numbers).
    "camera_axes": ("sensor", "optical"),
    # "The LiDAR data is saved with Open3d package and has a postfix '.pcd' in the name."
    # [OC-intro §3.1]: the encoding is not stated. ``ascii``: the dumper's
    # ``write_ascii=True`` (code only, OpenCDA data_dumper.save_lidar_points).
    "pcd_encoding": ("ascii", "binary"),
    # Fields of a .pcd. Code only: the loader reads the points and takes intensity from
    # the first colour channel ("we save the intensity in the first channel", OpenCOOD
    # pcd_utils.pcd_to_np), so the file has a colour field; [PCD]'s own example is
    # ``FIELDS x y z rgb``. The world has no intensity: every colour is 0, and so is the
    # packed ``rgb`` value under any packing. ``xyz_intensity`` and ``xyz`` are the other
    # layouts a PCD of a LiDAR sweep commonly has.
    "pcd_fields": ("xyz_rgb", "xyz_intensity", "xyz"),
    # Digits of a frame name. "Those timestamps are represented by five digits integers"
    # [OC-intro §3; VIT-intro §3], but the only path naming a real file is
    # "2021_08_24_21_29_28/4805/000069.yaml" [OC-ann, first paragraph], six digits, as
    # are the loader's docstring example and the dumper's names (code only).
    "frame_digits": ("6", "5"),
    # Numbers of the frames. ``from_zero``: 0, 1, 2, ... ("the point clouds data from
    # timestamp 0 to 700" [OC-intro, directory tree]). ``dumper``: 60, 62, 64, ... (code
    # only: OpenCDA main skips the first steps and writes every second one; the docs'
    # example 000069 is odd, so the release may number otherwise).
    "frame_numbering": ("from_zero", "dumper"),
    # Unit of the speeds the docs give no unit for: a listed vehicle's ``speed``
    # ("speed: 19.47 # vehicle's speed" [OC-ann]) and the speed of a ``plan_trajectory``
    # entry. ``ego_speed`` is documented: "ego_speed: 18.13 # agent's current speed,
    # km/h" [OC-ann].
    "speed_unit": ("km/h", "m/s"),
}
CARLA_UNVERIFIED: frozenset[str] = frozenset(
    {
        "euler",
        "box_location",
        "points_frame",
        "camera_extrinsic",
        "pcd_encoding",
        "pcd_fields",
        "frame_digits",
        "frame_numbering",
        "speed_unit",
    }
)


# --- readings, frames and numbers -------------------------------------------------------


def check_readings(readings: Mapping[str, str], table: Mapping[str, tuple[str, ...]]) -> None:
    """Refuse a value that is not one of the readings ``table`` lists."""
    for name, value in readings.items():
        if value not in table[name]:
            raise ValueError(f"{name}={value!r}: expected one of {', '.join(table[name])}")


def check_period(world: World) -> None:
    """The three datasets are 10 Hz and store no time (:data:`PERIOD_S`)."""
    if world.n_frames == 0:
        raise ValueError("the world has no frame")
    if abs(world.period_s - PERIOD_S) > 1e-9:
        raise ValueError(f"a period of {world.period_s} s cannot be written: the files are 10 Hz")


def frame_stems(n_frames: int, *, digits: int, numbering: str) -> list[str]:
    """The file-name stem of every frame (see ``frame_digits``, ``frame_numbering``)."""
    numbers = [index if numbering == "from_zero" else 60 + 2 * index for index in range(n_frames)]
    return [f"{number:0{digits}d}" for number in numbers]


def floats(values: Array) -> list[float]:
    """Plain Python floats, a negative zero written as 0.0."""
    return [float(value) + 0.0 for value in np.asarray(values, dtype=np.float64).ravel().tolist()]


def matrix_rows(matrix: Array) -> list[list[float]]:
    """A matrix as nested lists of rows, the way the yaml files hold one."""
    return [floats(row) for row in np.asarray(matrix, dtype=np.float64)]


# --- frames and rotations -----------------------------------------------------------------


def handedness_map(handedness: str) -> Array:
    """The map from the world's right-handed numbers to the numbers the files hold.

    For ``left``, CARLA's frame: "Z - Up, X - Forward, Y - Right" for an observer at the
    origin facing +X [CARLA-coords, 'Global coordinates'], and every sensor "x-forward,
    y-right, z-up" [CARLA-sensors, 'Important' note]. The world's LiDAR and body frames
    are x forward, y left, z up, so the left-handed numbers of a point are the world's
    with y negated: one reflection ``S = diag(1, -1, 1)``, the same for every frame. For
    ``right``, the identity.
    """
    return MIRROR_Y if handedness == "left" else _IDENTITY


def stored(pose: Pose, handedness: str) -> Pose:
    """A ``parent_from_child`` pose as the files hold it: ``(S R S, S t)``.

    Both frames of the pose are reflected. ``S R S`` is a rotation again (determinant
    +1), so it has angles. In those angles roll is kept and pitch and yaw change sign
    under :func:`euler_matrix` ``carla``.
    """
    mirror = handedness_map(handedness)
    return Pose(mirror @ pose.rotation @ mirror, mirror @ pose.translation)


def stored_points(points: Array, handedness: str) -> Array:
    """Points ``(N, 3)`` as the files hold them."""
    return points @ handedness_map(handedness).T


def euler_matrix(roll_deg: float, pitch_deg: float, yaw_deg: float, euler: str) -> Array:
    """The rotation three stored angles stand for, in the files' own numbers.

    Axes: "pitch (float - degrees) Y-axis rotation angle", "yaw ... Z-axis", "roll ...
    X-axis" [CARLA-api, carla.Rotation]; order: "Rotations are applied intrinsically in
    the order yaw, pitch, roll" [CARLA-coords, 'Rotation']. The sense of each angle is
    not written in prose; code only (LibCarla 0.9.12 ``Transform::GetMatrix`` and
    ``Math::GetForwardVector``; OpenCOOD ``transformation_utils.x_to_world`` builds the
    same matrix): the forward vector is (cos p cos y, cos p sin y, sin p), so positive
    yaw turns +x toward +y and positive pitch lifts +x toward +z, and positive roll tips
    +y toward -z. That is ``Rz(yaw) Ry(-pitch) Rx(-roll)`` with the right-handed
    elementary rotations of ``world.py`` (the research note checked this numerically).
    ``rzryrx`` is the textbook ``Rz(yaw) Ry(pitch) Rx(roll)``.
    """
    roll, pitch, yaw = math.radians(roll_deg), math.radians(pitch_deg), math.radians(yaw_deg)
    if euler == "carla":
        return rot_z(yaw) @ rot_y(-pitch) @ rot_x(-roll)
    return rot_z(yaw) @ rot_y(pitch) @ rot_x(roll)


def euler_deg(rotation: Array, euler: str) -> tuple[float, float, float]:
    """``(roll, pitch, yaw)`` in degrees such that :func:`euler_matrix` gives ``rotation``.

    Read off ``Rz(c) Ry(b) Rx(a)``, whose first column is ``(cc cb, sc cb, -sb)`` and
    whose last row is ``(-sb, cb sa, cb ca)``; for ``carla`` the roll and pitch are the
    negatives of ``a`` and ``b``. Pitch lies in [-90°, 90°], roll and yaw in (-180°, 180°].
    """
    sin_b = max(-1.0, min(1.0, -float(rotation[2, 0])))
    b = math.asin(sin_b)
    if abs(sin_b) < 1.0 - 1e-12:
        a = math.atan2(float(rotation[2, 1]), float(rotation[2, 2]))
        c = math.atan2(float(rotation[1, 0]), float(rotation[0, 0]))
    else:
        # Pitch of ±90°: roll and yaw turn about one axis. All of it goes to yaw.
        a = 0.0
        c = math.atan2(-float(rotation[0, 1]), float(rotation[1, 1]))
    roll, pitch = (-a, -b) if euler == "carla" else (a, b)
    angles = (math.degrees(roll) + 0.0, math.degrees(pitch) + 0.0, math.degrees(c) + 0.0)
    if not np.allclose(euler_matrix(*angles, euler), rotation, rtol=0.0, atol=1e-9):
        raise ValueError("three angles cannot express this matrix: it is not a rotation")
    return angles


def pose_six(pose: Pose, euler: str) -> list[float]:
    """``[x, y, z, roll, yaw, pitch]``, metres and degrees.

    "cords: # the x,y,z,roll,yaw,pitch under CARLA map coordinate" and
    "predicted_ego_pos: # agent's localization (x,y,z,roll,yaw,pitch) gained from GPS"
    [OC-ann]: yaw is the fifth value, pitch the sixth. Degrees: "pitch (float - degrees)"
    [CARLA-api, carla.Rotation].
    """
    x, y, z = floats(pose.translation)
    roll, pitch, yaw = euler_deg(pose.rotation, euler)
    return [x, y, z, roll, yaw, pitch]


def world_from_camera_sensor(world: World, agent_id: str, camera: Camera, frame: int) -> Pose:
    """The camera's x-forward, y-left, z-up frame (right-handed) in the world.

    The world's camera frame is the optical one (x right, y down, z forward), and
    ``FORWARD_CAMERA`` takes it to the x-forward frame. :func:`stored` with ``left`` then
    gives CARLA's camera frame, "x-forward, y-right, z-up" [CARLA-sensors, 'Important'
    note], the one ``cords`` and ``extrinsic`` are in (see ``camera_axes``).
    """
    optical = world.world_from_camera(agent_id, camera.sensor_id, frame)
    return Pose(optical.rotation @ FORWARD_CAMERA.T, optical.translation)


def camera_slots(agent: Agent) -> dict[str, str]:
    """``camera id -> cameraK`` for the agent's cameras, by where each one looks.

    "`camera0`, `camera1`, `camera2`, and `camera3` represent the front, right rear, left
    rear, and back cameras respectively." [OC-intro §3.2]. A camera whose optical axis
    is within 45° of the body's +x is the front one, within 45° of -x the back one;
    otherwise it is the right rear (looking to body -y) or the left rear one (+y).
    """
    slots: dict[str, str] = {}
    for camera in agent.cameras:
        axis = camera.body_from_camera.rotation[:, 2]
        azimuth = math.degrees(math.atan2(float(axis[1]), float(axis[0])))
        if abs(azimuth) <= 45.0:
            slot = CAMERA_SLOTS[0]
        elif abs(azimuth) >= 135.0:
            slot = CAMERA_SLOTS[3]
        elif azimuth < 0.0:
            slot = CAMERA_SLOTS[1]
        else:
            slot = CAMERA_SLOTS[2]
        if slot in slots.values():
            raise ValueError(f"agent {agent.agent_id}: two cameras would both be {slot}")
        slots[camera.sensor_id] = slot
    return slots


# --- files ------------------------------------------------------------------------------


def write_pcd(path: Path, points: Array, *, encoding: str, fields: str) -> None:
    """One sweep as a PCD v0.7 file, one point per return, in the order given.

    [PCD]: "The header entries must be specified precisely in the above order":
    VERSION, FIELDS, SIZE, TYPE, COUNT, WIDTH, HEIGHT, VIEWPOINT, POINTS, DATA. Every
    field is a 4-byte float (``TYPE F``); the cloud is unorganised (HEIGHT 1) with the
    identity viewpoint. For ``binary`` the data is "a complete memory copy" of the points
    [PCD, 'DATA']: the records one after the other, little-endian. The fourth field
    (``rgb`` or ``intensity``) is 0: the world has no intensity.
    """
    xyz = np.ascontiguousarray(points, dtype="<f4").reshape(-1, 3)
    count = int(xyz.shape[0])
    names = {"xyz_rgb": ("x", "y", "z", "rgb"), "xyz_intensity": ("x", "y", "z", "intensity")}
    field_names = names.get(fields, ("x", "y", "z"))
    width = len(field_names)
    records = np.zeros((count, width), dtype="<f4")
    records[:, :3] = xyz
    header = (
        "# .PCD v0.7 - Point Cloud Data file format\n"
        "VERSION 0.7\n"
        f"FIELDS {' '.join(field_names)}\n"
        f"SIZE {' '.join('4' for _ in field_names)}\n"
        f"TYPE {' '.join('F' for _ in field_names)}\n"
        f"COUNT {' '.join('1' for _ in field_names)}\n"
        f"WIDTH {count}\n"
        "HEIGHT 1\n"
        "VIEWPOINT 0 0 0 1 0 0 0\n"
        f"POINTS {count}\n"
        f"DATA {encoding}\n"
    ).encode("ascii")
    if encoding == "ascii":
        # Nine significant digits give a float32 back exactly.
        lines = (" ".join(f"{value:.9g}" for value in row) for row in records.tolist())
        body = "".join(f"{line}\n" for line in lines).encode("ascii")
    else:
        body = records.tobytes()
    path.write_bytes(header + body)


def write_corner_image(path: Path, camera: Camera, truth: Mapping[int, CameraTruth]) -> None:
    """An RGB PNG of the camera's size with the corners of every box drawn as dots.

    "4x Camera RGB, 800 × 600 resolution" [OPV2V, Table I]; the images are ".png files"
    [OC-intro §3]. A corner is drawn when it lies in front of the camera and inside the
    image; all corners of one object have one colour. A mirrored (left-handed) export
    has the same images: a reflection of the numbers does not change what a camera sees.
    """
    image = Image.new("RGB", (camera.width, camera.height), (0, 0, 0))
    draw = ImageDraw.Draw(image)
    for object_id, corners in truth.items():
        red, green, blue = colorsys.hsv_to_rgb((object_id * 0.618034) % 1.0, 0.85, 1.0)
        colour = (round(red * 255), round(green * 255), round(blue * 255))
        for (u, v), in_front in zip(
            corners.pixels.tolist(), corners.in_front.tolist(), strict=True
        ):
            if in_front and 0.0 <= u < camera.width and 0.0 <= v < camera.height:
                r = _DOT_RADIUS_PX
                draw.ellipse((u - r, v - r, u + r, v + r), fill=colour)
    image.save(path, format="PNG")


@dataclass(frozen=True, eq=False)
class NumpyMatrix:
    """A 4 × 4 float64 array, written the way PyYAML writes a numpy array.

    V2V4Real's ``lidar_pose`` is one: the yaml excerpt in V2V4Real issue #5 shows a
    ``numpy.core.multiarray._reconstruct`` object of shape (4, 4) and dtype f8, and the
    maintainer answered "I used pyyaml to save the annotation." The same object written
    twice becomes a YAML anchor and alias, as ``true_ego_pos: *id002`` there.
    """

    values: Array


class _Dumper(yaml.SafeDumper):
    """A safe dumper that also writes :class:`NumpyMatrix`."""


_PY = "tag:yaml.org,2002:python/"


def _scalar(tag: str, value: str, style: str | None = None) -> yaml.ScalarNode:
    return yaml.ScalarNode(f"tag:yaml.org,2002:{tag}", value, style=style)


def _tuple(items: Sequence[yaml.Node]) -> yaml.SequenceNode:
    return yaml.SequenceNode(f"{_PY}tuple", list(items))


def _binary(data: bytes) -> yaml.ScalarNode:
    return _scalar("binary", base64.encodebytes(data).decode("ascii"), style="|")


def _represent_numpy_matrix(dumper: _Dumper, data: NumpyMatrix) -> yaml.Node:
    """The node PyYAML's full dumper makes of a C-ordered little-endian float64 array.

    ``_reconstruct(ndarray, (0,), b'b')`` with the state ``(1, shape, dtype, False,
    bytes)``; the dtype is ``numpy.dtype('f8', False, True)`` with its state. The module
    is written ``numpy.core.multiarray``, as in the issue #5 excerpt (2023 release).
    """
    values = np.ascontiguousarray(data.values, dtype="<f8")
    if values.shape != (4, 4):
        raise ValueError(f"expected a 4 × 4 matrix, got shape {values.shape}")
    # Every node is its own object: a node met twice would be written as an anchor and
    # an alias, which PyYAML does not do for a numpy array's nulls.
    nulls = [_scalar("null", "null") for _ in range(3)]
    dtype = yaml.MappingNode(
        f"{_PY}object/apply:numpy.dtype",
        [
            (
                _scalar("str", "args"),
                yaml.SequenceNode(
                    "tag:yaml.org,2002:seq",
                    [_scalar("str", "f8"), _scalar("bool", "false"), _scalar("bool", "true")],
                ),
            ),
            (
                _scalar("str", "state"),
                _tuple(
                    [
                        _scalar("int", "3"),
                        _scalar("str", "<"),
                        *nulls,
                        _scalar("int", "-1"),
                        _scalar("int", "-1"),
                        _scalar("int", "0"),
                    ]
                ),
            ),
        ],
    )
    node = yaml.MappingNode(
        f"{_PY}object/apply:numpy.core.multiarray._reconstruct",
        [
            (
                _scalar("str", "args"),
                yaml.SequenceNode(
                    "tag:yaml.org,2002:seq",
                    [
                        yaml.ScalarNode(f"{_PY}name:numpy.ndarray", ""),
                        _tuple([_scalar("int", "0")]),
                        _binary(b"b"),
                    ],
                ),
            ),
            (
                _scalar("str", "state"),
                _tuple(
                    [
                        _scalar("int", "1"),
                        _tuple([_scalar("int", "4"), _scalar("int", "4")]),
                        dtype,
                        _scalar("bool", "false"),
                        _binary(values.tobytes()),
                    ]
                ),
            ),
        ],
    )
    # The same object written again becomes an alias of this node.
    if dumper.alias_key is not None:
        dumper.represented_objects[dumper.alias_key] = node
    return node


_Dumper.add_representer(NumpyMatrix, _represent_numpy_matrix)


def write_yaml(path: Path, document: Mapping[object, object]) -> None:
    """A yaml file with its keys sorted, as in the docs' example (``camera0`` .. ``vehicles``).

    Floats are written with ``repr``, so they read back exactly. Only a
    :class:`NumpyMatrix` (or a list passed twice) produces a tag or an alias.
    """
    text = yaml.dump(
        dict(document), Dumper=_Dumper, sort_keys=True, default_flow_style=False, allow_unicode=True
    )
    path.write_text(text, encoding="utf-8")


# --- the OPV2V / V2XSet scenario ----------------------------------------------------------


def carla_agent_names(agents: Sequence[Agent], first_vehicle_id: int) -> dict[str, str]:
    """World agent id -> agent folder name, for the agents OPV2V or V2XSet can hold.

    "Each folder is named by the agent's unique id, e.g., 1732." and "A negative id
    means infrastructure." [VIT-intro §3]. The vehicles get consecutive ids from
    ``first_vehicle_id`` in world order, so the world's first vehicle sorts first (code
    only: the loader takes the first agent of the sorted list as the ego); roadside units
    get -1, -2, ...
    """
    names: dict[str, str] = {}
    vehicles = 0
    infrastructure = 0
    for agent in agents:
        if agent.kind == "vehicle":
            names[agent.agent_id] = str(first_vehicle_id + vehicles)
            vehicles += 1
        elif agent.kind == "infrastructure":
            infrastructure += 1
            names[agent.agent_id] = str(-infrastructure)
        else:
            raise ValueError(f"agent {agent.agent_id}: no place for a {agent.kind}")
    return names


def _speed_mps(positions: Sequence[Array | None], frame: int) -> float:
    """Speed at ``frame`` from the step to the next frame (to the previous at the last one)."""
    pairs = [(frame, frame + 1), (frame - 1, frame)]
    for first, second in pairs:
        if 0 <= first and second < len(positions):
            a, b = positions[first], positions[second]
            if a is not None and b is not None:
                return float(np.linalg.norm(b - a)) / PERIOD_S
    return 0.0


def _unit(speed_mps: float, speed_unit: str) -> float:
    return speed_mps * 3.6 if speed_unit == "km/h" else speed_mps


def _vehicle_entry(
    obj: WorldObject, speed_mps: float, readings: Mapping[str, str]
) -> dict[str, object]:
    """One entry of ``vehicles``: ``angle``, ``center``, ``extent``, ``location``, ``speed``.

    "angle: # roll, yaw, pitch under CARLA map coordinate system" and "extent: # half
    length, width and height of the vehicle in meter" [OC-ann, ``vehicles``]: the angles
    are the box's own rotation in the map frame, yaw in the middle; the extent is half the
    box's size along its own x, y, z ("The value in each axis equals half the size of the
    box for that axis." [CARLA-api, carla.BoundingBox.extent]). ``location`` and
    ``center`` follow ``box_location``.
    """
    handedness = readings["handedness"]
    length, width, height = obj.size_lwh
    pose = obj.world_from_box
    up = np.array([0.0, 0.0, height / 2.0])
    if readings["box_location"] == "actor_origin":
        origin, center = pose.translation - pose.rotation @ up, up
    elif readings["box_location"] == "actor_origin_world_offset":
        origin, center = pose.translation - up, up
    else:
        origin, center = pose.translation, np.zeros(3)
    box = stored(Pose(pose.rotation, origin), handedness)
    roll, pitch, yaw = euler_deg(box.rotation, readings["euler"])
    return {
        "angle": [roll, yaw, pitch],
        "center": floats(handedness_map(handedness) @ center),
        "extent": [float(length) / 2.0, float(width) / 2.0, float(height) / 2.0],
        "location": floats(box.translation),
        "speed": _unit(speed_mps, readings["speed_unit"]),
    }


def _camera_block(
    world: World,
    agent: Agent,
    camera: Camera,
    frame: int,
    lidar: Pose,
    readings: Mapping[str, str],
) -> dict[str, object]:
    """``cords``, ``extrinsic`` and ``intrinsic`` of one camera at one frame.

    "cords: # the x,y,z,roll,yaw,pitch under CARLA map coordinate" [OC-ann, ``camera0``]:
    the camera's pose in the map, in its sensor frame (see ``camera_axes``). ``extrinsic``
    is a 4 × 4 between the camera and the LiDAR (``lidar_pose``), direction per
    ``camera_extrinsic``. "intrinsic: # camera intrinsic matrix" [OC-ann]; the docs'
    example is ``[[fx, 0, cx], [0, fy, cy], [0, 0, 1]]`` (335.64, 400, 300 for an 800 × 600
    image) and no distortion is stored, so the world's pinhole is written as it is.
    """
    handedness = readings["handedness"]
    sensor = stored(world_from_camera_sensor(world, agent.agent_id, camera, frame), handedness)
    lidar_to_camera = sensor.inverse().compose(lidar).matrix()
    if readings["camera_axes"] == "optical":
        # Stored sensor numbers -> x-forward, y-left, z-up -> optical (FORWARD_CAMERA^T).
        to_optical = np.eye(4)
        to_optical[:3, :3] = FORWARD_CAMERA.T @ handedness_map(handedness)
        lidar_to_camera = to_optical @ lidar_to_camera
    if readings["camera_extrinsic"] == "camera_to_lidar":
        extrinsic = np.linalg.inv(lidar_to_camera)
    else:
        extrinsic = lidar_to_camera
    return {
        "cords": pose_six(sensor, readings["euler"]),
        "extrinsic": matrix_rows(extrinsic),
        "intrinsic": matrix_rows(camera.intrinsic()),
    }


def _sweep(world: World, agent: Agent, frame: int, points_frame: str, handedness: str) -> Array:
    """The agent's returns at ``frame`` in the frame ``points_frame`` names, as stored."""
    points = world.lidar_returns(agent.agent_id, frame).points
    if points_frame == "body":
        points = agent.lidar.body_from_lidar.apply(points)
    elif points_frame == "world":
        points = world.world_from_lidar(agent.agent_id, frame).apply(points)
    return stored_points(points, handedness)


def write_carla_scenario(
    world: World,
    scenario: Path,
    names: Mapping[str, str],
    readings: Mapping[str, str],
) -> Expectation:
    """Write one OPV2V/V2XSet scenario folder and say what a reader should make of it.

    ``names`` maps every world agent to its folder name (:func:`carla_agent_names`).
    Per agent and frame: ``<stem>.pcd``, ``<stem>.yaml`` and one ``<stem>_cameraK.png`` per
    camera ("There are three types of files inside the agent folders: LiDAR point clouds
    (`.pcd` files), camera images (`.png` files), and metadata (`.yaml` files)."
    [VIT-intro §3]). The scenario also gets ``data_protocol.yaml``, "the simulation
    parameters used to collect the data in Carla" [OC-intro, directory tree]; its keys
    are not documented and the loader does not read it, so it is an empty mapping.

    A frame's ``vehicles`` lists "the surrounding vehicles that have at least one LiDAR
    point hit from the agent" [OC-ann]: the world's cars and trucks with at least one
    return of that agent's LiDAR, keyed by their id ("4796: # the id of the vehicle (i.e.
    object)" [OC-ann]). There is no class field, so no class string is written.
    """
    check_period(world)
    handedness = readings["handedness"]
    euler = readings["euler"]
    stems = frame_stems(
        world.n_frames, digits=int(readings["frame_digits"]), numbering=readings["frame_numbering"]
    )
    by_frame = [{obj.object_id: obj for obj in objects} for objects in world.objects]
    vehicle_ids = sorted(
        {
            obj.object_id
            for objects in world.objects
            for obj in objects
            if obj.category in VEHICLE_CLASSES
        }
    )
    # Agents and objects share one id space ("The connected automated vehicle's id"
    # [OC-intro, tree]; object ids are simulator ids [OC-ann]): they must not collide.
    clash = {str(object_id) for object_id in vehicle_ids} & set(names.values())
    if clash:
        raise ValueError(f"agent folder names collide with object ids: {sorted(clash)}")
    centres: dict[int, list[Array | None]] = {
        object_id: [
            by_frame[frame][object_id].world_from_box.translation
            if object_id in by_frame[frame]
            else None
            for frame in range(world.n_frames)
        ]
        for object_id in vehicle_ids
    }

    scenario.mkdir(parents=True, exist_ok=True)
    write_yaml(scenario / "data_protocol.yaml", {})
    lidars: dict[str, str] = {}
    cameras: dict[tuple[str, str], str] = {}
    annotated: dict[tuple[str, int], tuple[int, ...]] = {}
    for agent in world.agents:
        name = names[agent.agent_id]
        folder = scenario / name
        folder.mkdir(parents=True, exist_ok=True)
        # One LiDAR per agent and no name for it: the agent's name stands for it.
        lidars[agent.agent_id] = name
        slots = camera_slots(agent)
        for camera_id, slot in slots.items():
            cameras[(agent.agent_id, camera_id)] = slot
        body_positions: list[Array | None] = [pose.translation for pose in agent.world_from_body]
        for frame, stem in enumerate(stems):
            write_pcd(
                folder / f"{stem}.pcd",
                _sweep(world, agent, frame, readings["points_frame"], handedness),
                encoding=readings["pcd_encoding"],
                fields=readings["pcd_fields"],
            )
            # "lidar_pose: # LiDAR pose under CARLA map coordinate system" [OC-ann]: the
            # LiDAR's own pose (LiDAR to map), six values like ``cords``.
            lidar = stored(world.world_from_lidar(agent.agent_id, frame), handedness)
            document: dict[object, object] = {}
            for camera in agent.cameras:
                slot = slots[camera.sensor_id]
                truth = world.camera_truth(agent.agent_id, camera.sensor_id, frame)
                write_corner_image(folder / f"{stem}_{slot}.png", camera, truth)
                document[slot] = _camera_block(world, agent, camera, frame, lidar, readings)
            # "ego_speed: 18.13 # agent's current speed, km/h" [OC-ann].
            document["ego_speed"] = _speed_mps(body_positions, frame) * 3.6
            document["lidar_pose"] = pose_six(lidar, euler)
            body = stored(agent.world_from_body[frame], handedness)
            if agent.kind == "vehicle":
                # "plan_trajectory: # agent's planning trajectory" [OC-ann], "future
                # planning trajectories" [OC-intro §3.3]. Entries [x, y, speed], vehicles
                # only (code only: OpenCDA data_dumper). Here: the frames still to come.
                document["plan_trajectory"] = [
                    [
                        *floats(stored(agent.world_from_body[later], handedness).translation)[:2],
                        _unit(_speed_mps(body_positions, later), readings["speed_unit"]),
                    ]
                    for later in range(frame + 1, world.n_frames)
                ]
            # "predicted_ego_pos: # agent's localization (x,y,z,roll,yaw,pitch) gained from
            # GPS" and "true_ego_pos: # agent's true localization" [OC-ann]: the body pose.
            # The world's localisation is exact, so both are the true pose.
            document["predicted_ego_pos"] = pose_six(body, euler)
            document["true_ego_pos"] = pose_six(body, euler)
            listed = [
                object_id
                for object_id in world.seen_ids(agent.agent_id, frame)
                if object_id in by_frame[frame]
                and by_frame[frame][object_id].category in VEHICLE_CLASSES
            ]
            document["vehicles"] = {
                object_id: _vehicle_entry(
                    by_frame[frame][object_id], _speed_mps(centres[object_id], frame), readings
                )
                for object_id in listed
            }
            annotated[(agent.agent_id, frame)] = tuple(listed)
            write_yaml(folder / f"{stem}.yaml", document)

    present = {obj.category for objects in world.objects for obj in objects}
    return Expectation(
        agents=dict(names),
        lidars=lidars,
        cameras=cameras,
        annotated=annotated,
        track_ids={object_id: str(object_id) for object_id in vehicle_ids},
        # No class is written: "OPV2V only has vehicles" [OC-intro, V2XSet §2]. The
        # dataset's one class is named by the key its objects are listed under.
        categories={category: "vehicle" for category in VEHICLE_CLASSES if category in present},
    )
