# SPDX-License-Identifier: Apache-2.0
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
"""Read one frame of a real dataset and say which reading of an open convention fits.

The geometry conformance suite (``docs/geometry/conventions.md``) lists conventions that
no public document settles. This probe looks at one frame of real data and scores each
reading against the data itself. It is read-only: it opens files for reading and writes
nothing anywhere (no cache, no temporary file, no bytecode).

Subcommands, one per dataset:

* ``opv2v`` / ``v2xset`` SCENARIO AGENT FRAME: OPV-7, the direction of a camera's
  ``extrinsic``, and OPV-6, the signs of roll and pitch.
* ``v2v4real`` SCENARIO AGENT FRAME: V4R-7, the frame of the boxes, and V4R-1,
  handedness.
* ``dair`` ROOT FRAME_ID: DAIR-10, the order of ``world_8_points``.

The output holds the dataset name, the frame id and numbers only: no path, no user name.
Error messages name the file by its role ("the agent YAML is missing"), never by its
path. See ``scripts/verify/README.md`` for how to run it and read the scores.

Only numpy, PyYAML and the repository's safe numpy-tag YAML loader are used.
"""

from __future__ import annotations

import sys

# Read-only: no ``__pycache__`` anywhere, whichever way this file is started.
sys.dont_write_bytecode = True

import argparse  # noqa: E402
import json  # noqa: E402
import math  # noqa: E402
from collections.abc import Callable, Mapping, Sequence  # noqa: E402
from dataclasses import dataclass  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any  # noqa: E402

import numpy as np  # noqa: E402
import numpy.typing as npt  # noqa: E402

# This checkout's ``coperceive``, not whichever one the venv has installed.
_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from coperceive.adapters.readers.numpy_yaml import load_dataset_yaml  # noqa: E402

Array = npt.NDArray[np.float64]
Mask = npt.NDArray[np.bool_]

PROBE_VERSION = "1"
# A sweep is cut to this many points (fixed seed) so one frame takes seconds.
MAX_POINTS = 100_000
SEED = 0
# No file this probe reads should be larger; a bigger one is refused, not loaded.
MAX_FILE_BYTES = 512 * 1024 * 1024
# A point is inside a box grown by this much (points lie on the box's surface).
INSIDE_MARGIN_M = 0.1
# A point is near a box inside the box grown by this much along box x, y and z.
NEAR_GROW_M: tuple[float, float, float] = (1.0, 1.0, 0.5)
# Roll or pitch below this (degrees) everywhere: the Euler question has no evidence.
TILT_EVIDENCE_DEG = 0.5
# A verdict needs at least this score margin and this many points behind it.
MIN_MARGIN = 0.05
MIN_POINTS = 30
# DAIR: a cooperative box and a single-view box are one object within this distance (m);
# their forward directions agree within this angle (degrees).
MATCH_DISTANCE_M = 1.5
HEADING_AGREE_DEG = 20.0
# A box is in front of a camera when all its corners are at least this deep (m).
MIN_DEPTH_M = 0.2

# camera optical axes (x right, y down, z forward) from CARLA sensor axes (x forward,
# y right, z up): (y, -z, x), as OpenCDA's project_lidar_to_camera does.
SENSOR_TO_OPTICAL: Array = np.array([[0.0, 1.0, 0.0], [0.0, 0.0, -1.0], [1.0, 0.0, 0.0]])
MIRROR_Y: Array = np.diag([1.0, -1.0, 1.0])

# Corner signs (box x, y, z) of each documented ``world_8_points`` order; the values of
# ``tests/geomworld/export_dair_v2x_c.py`` READINGS["corner_order"].
# ``dair_lidar``: tools/dataset_converter/README.md, get_lidar_3d_8points: 0-3 bottom,
# 4-7 top, 0 = (+l/2, +w/2), 1 = (+l/2, -w/2), 2 = (-l/2, -w/2), 3 = (-l/2, +w/2).
_DAIR_SIGNS: Array = np.array(
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
CORNER_ORDERS: dict[str, Array] = {
    "dair_lidar": _DAIR_SIGNS,
    # The same order for the box turned by pi (front and back swapped).
    "turned_pi": _DAIR_SIGNS * np.array([-1.0, -1.0, 1.0]),
    # The evaluator's permutation [3, 2, 1, 0, 7, 6, 5, 4] (eval_utils.perm_label).
    "reversed_ring": _DAIR_SIGNS[[3, 2, 1, 0, 7, 6, 5, 4]],
}
_BOX_SIGNS: Array = _DAIR_SIGNS

DAIR_RELEASE = "cooperative-vehicle-infrastructure"
DAIR_COMPLEMENTED = "DAIR-V2X-C_Complemented_Anno"
DAIR_SIDES = {"vehicle": "vehicle-side", "infrastructure": "infrastructure-side"}


class ProbeError(Exception):
    """A problem with the input. The message names files by role, never by path."""


# --- reading files ------------------------------------------------------------------------


def _read_bytes(path: Path, what: str) -> bytes:
    """A file's bytes, opened read-only. ``what`` names the file in errors."""
    try:
        if not path.is_file():
            raise ProbeError(f"{what} is missing")
        size = path.stat().st_size
        if size > MAX_FILE_BYTES:
            raise ProbeError(f"{what} is larger than {MAX_FILE_BYTES // (1024 * 1024)} MiB")
        with path.open("rb") as handle:
            return handle.read()
    except OSError:
        raise ProbeError(f"{what} cannot be read") from None


def _read_yaml(path: Path, what: str) -> object:
    text = _read_bytes(path, what).decode("utf-8", errors="replace")
    try:
        return load_dataset_yaml(text)
    except Exception:  # noqa: BLE001 - any parser error; its text may quote the file
        raise ProbeError(
            f"{what} is not valid YAML (or holds a tag the safe loader refuses)"
        ) from None


def _read_json(path: Path, what: str) -> object:
    try:
        return json.loads(_read_bytes(path, what).decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise ProbeError(f"{what} is not valid JSON") from None


def _mapping(value: object, what: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProbeError(f"{what} is not a mapping")
    return {str(key): item for key, item in value.items()}  # pyright: ignore[reportUnknownVariableType]


def _sequence(value: object, what: str) -> list[Any]:
    if not isinstance(value, list | tuple):
        raise ProbeError(f"{what} is not a list")
    return list(value)  # pyright: ignore[reportUnknownArgumentType]


def _numbers(value: object, shape: tuple[int, ...], what: str) -> Array:
    """``value`` (nested or flat lists, numbers or numeric strings) as an array of ``shape``."""
    try:
        array = np.asarray(value, dtype=np.float64)
    except (TypeError, ValueError):
        raise ProbeError(f"{what} is not numeric") from None
    if array.size != int(np.prod(shape)):
        raise ProbeError(f"{what} has {array.size} values, expected {int(np.prod(shape))}")
    array = array.reshape(shape)
    if not bool(np.all(np.isfinite(array))):
        raise ProbeError(f"{what} holds a value that is not finite")
    return array


# --- PCD ------------------------------------------------------------------------------------

_PCD_KINDS = {"F": "f", "U": "u", "I": "i"}


def read_pcd_xyz(data: bytes, what: str) -> Array:
    """``x, y, z`` of a PCD 0.7 file (``ascii``, ``binary`` or ``binary_compressed``).

    Rows with a non-finite coordinate are dropped.
    """
    names: list[str] = []
    sizes: list[int] = []
    kinds: list[str] = []
    counts: list[int] = []
    points = -1
    mode = ""
    offset = 0
    try:
        while offset < len(data):
            newline = data.find(b"\n", offset)
            if newline < 0:
                raise ProbeError(f"{what} has no DATA line")
            parts = data[offset:newline].decode("ascii", errors="replace").split()
            offset = newline + 1
            if not parts or parts[0].startswith("#"):
                continue
            key = parts[0].upper()
            if key == "FIELDS":
                names = parts[1:]
            elif key == "SIZE":
                sizes = [int(v) for v in parts[1:]]
            elif key == "TYPE":
                kinds = [v.upper() for v in parts[1:]]
            elif key == "COUNT":
                counts = [int(v) for v in parts[1:]]
            elif key == "POINTS":
                points = int(parts[1])
            elif key == "DATA":
                mode = parts[1].lower() if len(parts) > 1 else ""
                break
    except (ValueError, IndexError):
        raise ProbeError(f"{what} has a malformed header") from None
    if not counts:
        counts = [1] * len(names)
    if not names or len(sizes) != len(names) or len(kinds) != len(names):
        raise ProbeError(f"{what} has a malformed header")
    if len(counts) != len(names) or points < 0:
        raise ProbeError(f"{what} has a malformed header")
    for axis in ("x", "y", "z"):
        if axis not in names:
            raise ProbeError(f"{what} has no field {axis}")
    widths = [int(c) for c in counts]
    starts = [sum(widths[:i]) for i in range(len(widths))]
    if mode == "ascii":
        try:
            flat = np.array(data[offset:].split(), dtype=np.float64)
        except ValueError:
            raise ProbeError(f"{what} has a value that is not a number") from None
        total = sum(widths)
        if flat.size < points * total:
            raise ProbeError(f"{what} has fewer values than its header says")
        table = flat[: points * total].reshape(points, total)
        xyz = np.stack([table[:, starts[names.index(axis)]] for axis in ("x", "y", "z")], axis=1)
    elif mode in ("binary", "binary_compressed"):
        dtypes: list[np.dtype[Any]] = []
        for kind, size in zip(kinds, sizes, strict=True):
            if kind not in _PCD_KINDS or size not in (1, 2, 4, 8):
                raise ProbeError(f"{what} has a field type this probe does not read")
            dtypes.append(np.dtype(f"<{_PCD_KINDS[kind]}{size}"))
        if mode == "binary":
            record = np.dtype(
                [
                    (f"f{i}", dtype, (width,))
                    for i, (dtype, width) in enumerate(zip(dtypes, widths, strict=True))
                ]
            )
            if len(data) - offset < points * record.itemsize:
                raise ProbeError(f"{what} is shorter than its header says")
            rows = np.frombuffer(data, dtype=record, count=points, offset=offset)
            columns = {
                name: np.asarray(rows[f"f{i}"][:, 0], dtype=np.float64)
                for i, name in enumerate(names)
            }
        else:
            raw = _pcd_decompress(data[offset:], what)
            columns = {}
            position = 0
            for name, dtype, width in zip(names, dtypes, widths, strict=True):
                length = points * width * dtype.itemsize
                if position + length > len(raw):
                    raise ProbeError(f"{what} is shorter than its header says")
                block = np.frombuffer(raw, dtype=dtype, count=points * width, offset=position)
                columns[name] = np.asarray(block.reshape(width, points)[0], dtype=np.float64)
                position += length
        xyz = np.stack([columns["x"], columns["y"], columns["z"]], axis=1)
    else:
        raise ProbeError(f"{what} has a DATA kind this probe does not read")
    return xyz[np.all(np.isfinite(xyz), axis=1)]


def _pcd_decompress(payload: bytes, what: str) -> bytes:
    """The data block of ``DATA binary_compressed``: two uint32 sizes and an LZF stream."""
    if len(payload) < 8:
        raise ProbeError(f"{what} is shorter than its header says")
    compressed = int.from_bytes(payload[0:4], "little")
    size = int.from_bytes(payload[4:8], "little")
    if size > MAX_FILE_BYTES or compressed > len(payload) - 8:
        raise ProbeError(f"{what} has an impossible compressed size")
    return lzf_decompress(payload[8 : 8 + compressed], size, what)


def lzf_decompress(source: bytes, size: int, what: str) -> bytes:
    """LZF (liblzf) decompression, as PCL uses for ``binary_compressed``."""
    out = bytearray()
    index = 0
    end = len(source)
    while index < end:
        control = source[index]
        index += 1
        if control < 32:
            run = control + 1
            if index + run > end:
                raise ProbeError(f"{what} has a broken compressed block")
            out += source[index : index + run]
            index += run
            continue
        length = control >> 5
        if length == 7:
            if index >= end:
                raise ProbeError(f"{what} has a broken compressed block")
            length += source[index]
            index += 1
        if index >= end:
            raise ProbeError(f"{what} has a broken compressed block")
        back = ((control & 0x1F) << 8) + source[index] + 1
        index += 1
        length += 2
        start = len(out) - back
        if start < 0:
            raise ProbeError(f"{what} has a broken compressed block")
        if back >= length:
            out += out[start : start + length]
        else:
            for k in range(length):
                out.append(out[start + k])
        if len(out) > size:
            raise ProbeError(f"{what} decompresses to more than its header says")
    if len(out) != size:
        raise ProbeError(f"{what} decompresses to a different size than its header says")
    return bytes(out)


def _sweep(path: Path, what: str) -> tuple[Array, int]:
    """The sweep's points (at most :data:`MAX_POINTS`, fixed seed) and the count read."""
    points = read_pcd_xyz(_read_bytes(path, what), what)
    total = int(points.shape[0])
    if total > MAX_POINTS:
        keep = np.sort(np.random.default_rng(SEED).choice(total, MAX_POINTS, replace=False))
        points = points[keep]
    return points, total


# --- geometry -----------------------------------------------------------------------------


def rot_x(angle: float) -> Array:
    c, s = math.cos(angle), math.sin(angle)
    return np.array([[1.0, 0.0, 0.0], [0.0, c, -s], [0.0, s, c]])


def rot_y(angle: float) -> Array:
    c, s = math.cos(angle), math.sin(angle)
    return np.array([[c, 0.0, s], [0.0, 1.0, 0.0], [-s, 0.0, c]])


def rot_z(angle: float) -> Array:
    c, s = math.cos(angle), math.sin(angle)
    return np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])


# OPV-6: ``source`` is OpenCOOD's x_to_world / CARLA's GetMatrix, Rz(yaw) Ry(-pitch)
# Rx(-roll); ``adapter`` is the manifest builder's Rz(yaw) Ry(pitch) Rx(roll) on the raw
# numbers. The probe prints ``adapter`` and ``flipped`` (= ``source``).
EULER_READINGS = ("adapter", "flipped")


def euler_rotation(roll_deg: float, pitch_deg: float, yaw_deg: float, reading: str) -> Array:
    roll, pitch, yaw = math.radians(roll_deg), math.radians(pitch_deg), math.radians(yaw_deg)
    if reading == "adapter":
        return rot_z(yaw) @ rot_y(pitch) @ rot_x(roll)
    return rot_z(yaw) @ rot_y(-pitch) @ rot_x(-roll)


def homogeneous(rotation: Array, translation: Array) -> Array:
    out = np.eye(4)
    out[:3, :3] = rotation
    out[:3, 3] = translation
    return out


def pose_six(values: Array, reading: str) -> Array:
    """``[x, y, z, roll, yaw, pitch]`` (metres, degrees) as a 4 x 4 ``parent_from_child``."""
    x, y, z, roll, yaw, pitch = (float(v) for v in values)
    return homogeneous(euler_rotation(roll, pitch, yaw, reading), np.array([x, y, z]))


def invert(matrix: Array) -> Array:
    rotation = matrix[:3, :3]
    return homogeneous(rotation.T, -(rotation.T @ matrix[:3, 3]))


def apply(matrix: Array, points: Array) -> Array:
    return points @ matrix[:3, :3].T + matrix[:3, 3]


@dataclass(frozen=True)
class Box:
    """A box: ``centre``, ``rotation`` (``parent_from_box``) and half sizes."""

    centre: Array
    rotation: Array
    half: Array

    def corners(self, signs: Array = _BOX_SIGNS) -> Array:
        return (signs * self.half) @ self.rotation.T + self.centre

    def moved(self, parent_from_frame: Array) -> Box:
        """This box (given in ``frame``) in ``parent``."""
        return Box(
            apply(parent_from_frame, self.centre[None, :])[0],
            parent_from_frame[:3, :3] @ self.rotation,
            self.half,
        )


def owners(points: Array, boxes: Sequence[Box], grow: Array) -> npt.NDArray[np.int64]:
    """Index of the first box (grown by ``grow`` along its axes) holding each point, or -1."""
    owner = np.full(points.shape[0], -1, dtype=np.int64)
    for index, box in enumerate(boxes):
        reach = float(np.linalg.norm(box.half + grow))
        candidates = np.flatnonzero(
            (owner < 0) & (np.sum((points - box.centre) ** 2, axis=1) <= reach * reach)
        )
        if candidates.size == 0:
            continue
        local = (points[candidates] - box.centre) @ box.rotation
        inside = np.all(np.abs(local) <= box.half + grow, axis=1)
        owner[candidates[inside]] = index
    return owner


@dataclass(frozen=True)
class Containment:
    inside: int
    near: int

    @property
    def fraction(self) -> float:
        return self.inside / self.near if self.near else float("nan")


def containment(
    points_by_reading: Mapping[str, Array], boxes_by_reading: Mapping[str, Sequence[Box]]
) -> dict[str, Containment]:
    """Per reading: points inside some box, out of the points near a box under any reading.

    The denominator is shared, so a reading that puts the boxes where no points are
    scores low instead of scoring on a handful of stray points.
    """
    grow = np.array(NEAR_GROW_M)
    margin = np.full(3, INSIDE_MARGIN_M)
    near_any: Mask | None = None
    inside: dict[str, Mask] = {}
    for reading, points in points_by_reading.items():
        boxes = boxes_by_reading[reading]
        near = owners(points, boxes, grow) >= 0
        near_any = near if near_any is None else (near_any | near)
        inside[reading] = owners(points, boxes, margin) >= 0
    assert near_any is not None
    return {
        reading: Containment(
            int(np.count_nonzero(mask & near_any)), int(np.count_nonzero(near_any))
        )
        for reading, mask in inside.items()
    }


def convex_hull(points: Array) -> Array:
    """Counter-clockwise hull (Andrew's monotone chain) of 2D points."""
    unique = sorted({(float(u), float(v)) for u, v in points.tolist()})
    if len(unique) < 3:
        return np.array(unique, dtype=np.float64).reshape(-1, 2)

    def cross(o: tuple[float, float], a: tuple[float, float], b: tuple[float, float]) -> float:
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower: list[tuple[float, float]] = []
    for p in unique:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper: list[tuple[float, float]] = []
    for p in reversed(unique):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return np.array(lower[:-1] + upper[:-1], dtype=np.float64)


def in_hull(pixels: Array, hull: Array) -> Mask:
    if hull.shape[0] < 3:
        return np.zeros(pixels.shape[0], dtype=bool)
    ok = np.ones(pixels.shape[0], dtype=bool)
    for i in range(hull.shape[0]):
        a, b = hull[i], hull[(i + 1) % hull.shape[0]]
        side = (b[0] - a[0]) * (pixels[:, 1] - a[1]) - (b[1] - a[1]) * (pixels[:, 0] - a[0])
        ok &= side >= -1e-6
    return ok


# --- report -------------------------------------------------------------------------------


@dataclass(frozen=True)
class Score:
    reading: str
    value: float
    support: int


def verdict(row: str, question: str, scores: Sequence[Score]) -> str:
    """``verdict ROW QUESTION: BEST margin M`` or ``... undecided (reason)``."""
    ranked = sorted(
        (s for s in scores if not math.isnan(s.value)), key=lambda s: s.value, reverse=True
    )
    head = f"verdict {row} {question}:"
    if len(ranked) < 2:
        return f"{head} undecided (no evidence on this frame)"
    best, second = ranked[0], ranked[1]
    margin = best.value - second.value
    if best.support < MIN_POINTS:
        return f"{head} undecided (only {best.support} points) margin {margin:.3f}"
    if margin < MIN_MARGIN:
        return f"{head} undecided (best {best.reading} by {margin:.3f} only) margin {margin:.3f}"
    return f"{head} {best.reading} margin {margin:.3f} (next {second.reading} {second.value:.3f})"


def _fmt(value: float) -> str:
    return "  n/a" if math.isnan(value) else f"{value:.3f}"


# --- OPV2V / V2XSet -----------------------------------------------------------------------


def _carla_boxes(vehicles: Mapping[str, Any], reading: str) -> list[Box]:
    """OPV2V ``vehicles``: centre = location + R center (CARLA's bounding box offset)."""
    boxes: list[Box] = []
    for key, entry in vehicles.items():
        what = f"vehicle {key}" if str(key).lstrip("-").isdigit() else "a vehicle entry"
        item = _mapping(entry, what)
        roll, yaw, pitch = _numbers(item.get("angle"), (3,), f"{what} angle")
        rotation = euler_rotation(float(roll), float(pitch), float(yaw), reading)
        location = _numbers(item.get("location"), (3,), f"{what} location")
        center = _numbers(item.get("center", [0.0, 0.0, 0.0]), (3,), f"{what} center")
        extent = _numbers(item.get("extent"), (3,), f"{what} extent")
        boxes.append(Box(location + rotation @ center, rotation, np.abs(extent)))
    return boxes


def probe_opencood(dataset: str, scenario: Path, agent: str, frame: str) -> list[str]:
    folder = scenario / agent
    document = _mapping(_read_yaml(folder / f"{frame}.yaml", "the agent YAML"), "the agent YAML")
    points, total = _sweep(folder / f"{frame}.pcd", "the agent point cloud")
    lidar_six = _numbers(document.get("lidar_pose"), (6,), "lidar_pose")
    vehicles = _mapping(document.get("vehicles", {}) or {}, "vehicles")
    lines = [
        f"conventions_probe {PROBE_VERSION}",
        f"dataset: {dataset}",
        f"frame: {frame}",
        f"points: {total} read, {points.shape[0]} used; boxes: {len(vehicles)}",
        "",
    ]

    # OPV-6: roll and pitch signs, on pose and boxes at once.
    tilts = [abs(float(lidar_six[3])), abs(float(lidar_six[5]))]
    for key, entry in vehicles.items():
        angle = _numbers(_mapping(entry, f"vehicle {key}").get("angle"), (3,), "a vehicle angle")
        tilts += [abs(float(angle[0])), abs(float(angle[2]))]
    largest = max(tilts)
    pose_tilt = max(abs(float(lidar_six[3])), abs(float(lidar_six[5])))
    lines.append("OPV-6 roll and pitch signs: box containment of the LiDAR points")
    lines.append(
        f"  largest |roll| or |pitch|: lidar_pose {pose_tilt:.2f} deg, all {largest:.2f} deg"
    )
    world_points = {r: apply(pose_six(lidar_six, r), points) for r in EULER_READINGS}
    boxes = {r: _carla_boxes(vehicles, r) for r in EULER_READINGS}
    euler = containment(world_points, boxes)
    lines.append("  reading   inside    near  fraction")
    for reading in EULER_READINGS:
        c = euler[reading]
        lines.append(f"  {reading:<8} {c.inside:7d} {c.near:7d}  {_fmt(c.fraction)}")
    euler_scores = [Score(r, euler[r].fraction, euler[r].near) for r in EULER_READINGS]
    if largest < TILT_EVIDENCE_DEG:
        lines.append(
            f"  every roll and pitch is below {TILT_EVIDENCE_DEG} deg: this frame cannot tell"
        )
        lines.append("verdict OPV-6 euler: undecided (all roll and pitch near zero)")
        chosen = "flipped"
    else:
        lines.append(verdict("OPV-6", "euler", euler_scores))
        chosen = max(euler_scores, key=lambda s: -1.0 if math.isnan(s.value) else s.value).reading
    lines.append("")

    # OPV-7: extrinsic direction. In-box points go through the extrinsic; the box corners
    # go through the camera's own world pose (``cords``) and lidar_pose. Sending both
    # through the extrinsic would move points and corners by one rigid transform, which
    # no containment can see.
    lidar_world = pose_six(lidar_six, chosen)
    world_boxes = boxes[chosen]
    owner = owners(world_points[chosen], world_boxes, np.full(3, INSIDE_MARGIN_M))
    in_box = owner >= 0
    box_points = points[in_box]
    box_owner = owner[in_box]
    cameras = sorted(k for k in document if k.startswith("camera") and k[6:].isdigit())
    lines.append(
        "OPV-7 camera extrinsic direction: in-box points inside their box's image footprint"
    )
    lines.append(
        "  footprint from cords and lidar_pose; points through the extrinsic; "
        f"{int(np.count_nonzero(in_box))} in-box points"
    )
    if not cameras:
        lines.append("  the YAML has no camera blocks")
        lines.append("verdict OPV-7 camera_extrinsic: undecided (no camera)")
        return lines
    combos = [
        (direction, axes)
        for direction in ("lidar_to_camera", "camera_to_lidar")
        for axes in ("sensor", "optical")
    ]
    hits: dict[tuple[str, str], list[tuple[int, int]]] = {c: [] for c in combos}
    mismatch: dict[tuple[str, str], list[float]] = {c: [] for c in combos}
    for name in cameras:
        block = _mapping(document[name], f"{name}")
        extrinsic = _numbers(block.get("extrinsic"), (4, 4), f"{name} extrinsic")
        intrinsic = _numbers(block.get("intrinsic"), (3, 3), f"{name} intrinsic")
        cords = _numbers(block.get("cords"), (6,), f"{name} cords")
        camera_world = pose_six(cords, chosen)
        camera_from_world = invert(camera_world)
        width, height = 2.0 * float(intrinsic[0, 2]), 2.0 * float(intrinsic[1, 2])
        hulls: dict[int, Array] = {}
        for index, box in enumerate(world_boxes):
            corners = apply(camera_from_world, box.corners()) @ SENSOR_TO_OPTICAL.T
            if float(corners[:, 2].min()) < MIN_DEPTH_M:
                continue
            pixels = _project(intrinsic, corners)
            if pixels[:, 0].max() < 0 or pixels[:, 0].min() > width:
                continue
            if pixels[:, 1].max() < 0 or pixels[:, 1].min() > height:
                continue
            hulls[index] = convex_hull(pixels)
        lidar_to_camera_cords = camera_from_world @ lidar_world
        # Counted: points of a box in view that the cords path itself puts in the image,
        # so the right reading scores near 1 whatever part of a box the image cuts off.
        _, seen = _in_image(
            intrinsic, apply(lidar_to_camera_cords, box_points) @ SENSOR_TO_OPTICAL.T, width, height
        )
        counted = seen & np.isin(box_owner, np.array(sorted(hulls), dtype=np.int64))
        for direction, axes in combos:
            camera_from_lidar = extrinsic if direction == "lidar_to_camera" else invert(extrinsic)
            optical_from_lidar = camera_from_lidar.copy()
            sensor_from_lidar = camera_from_lidar.copy()
            if axes == "sensor":
                optical_from_lidar[:3, :] = SENSOR_TO_OPTICAL @ camera_from_lidar[:3, :]
            else:
                sensor_from_lidar[:3, :] = SENSOR_TO_OPTICAL.T @ camera_from_lidar[:3, :]
            mismatch[(direction, axes)].append(
                float(np.linalg.norm(sensor_from_lidar[:3, 3] - lidar_to_camera_cords[:3, 3]))
            )
            local = apply(optical_from_lidar, box_points[counted])
            owners_here = box_owner[counted]
            pixels, visible = _in_image(intrinsic, local, width, height)
            good = 0
            for index, hull in hulls.items():
                mine = visible & (owners_here == index)
                if np.any(mine):
                    good += int(np.count_nonzero(in_hull(pixels[mine], hull)))
            hits[(direction, axes)].append((good, int(np.count_nonzero(counted))))
    header = "  extrinsic        axes     fraction  points  t_mismatch_m"
    lines.append(header + "".join(f"  {name:>7}" for name in cameras))
    scores: dict[str, Score] = {}
    for combo in combos:
        good = sum(g for g, _ in hits[combo])
        support = sum(n for _, n in hits[combo])
        fraction = good / support if support else float("nan")
        per_camera = "".join(f"  {_fmt(g / n if n else float('nan')):>7}" for g, n in hits[combo])
        t_mismatch = float(np.mean(mismatch[combo]))
        lines.append(
            f"  {combo[0]:<16} {combo[1]:<8} {_fmt(fraction):>8}  {support:6d}  "
            f"{t_mismatch:12.3f}{per_camera}"
        )
        direction = combo[0]
        if direction not in scores or (
            not math.isnan(fraction)
            and (math.isnan(scores[direction].value) or fraction > scores[direction].value)
        ):
            scores[direction] = Score(direction, fraction, support)
    lines.append(
        "  (axes: the camera side of the extrinsic, CARLA sensor axes or optical axes; "
        "each direction is scored with its better axes)"
    )
    lines.append(verdict("OPV-7", "camera_extrinsic", list(scores.values())))
    return lines


def _in_image(intrinsic: Array, optical: Array, width: float, height: float) -> tuple[Array, Mask]:
    """Pixels of optical-frame points, and which are in front of the camera and in the image."""
    front = optical[:, 2] > 1e-3
    pixels = _project(intrinsic, np.where(front[:, None], optical, 1.0))
    visible = (
        front
        & (pixels[:, 0] >= 0)
        & (pixels[:, 0] < width)
        & (pixels[:, 1] >= 0)
        & (pixels[:, 1] < height)
    )
    return pixels, visible


def _project(intrinsic: Array, optical: Array) -> Array:
    depth = np.where(np.abs(optical[:, 2]) < 1e-9, 1e-9, optical[:, 2])
    u = intrinsic[0, 0] * optical[:, 0] / depth + intrinsic[0, 1] * optical[:, 1] / depth
    v = intrinsic[1, 1] * optical[:, 1] / depth
    return np.stack([u + intrinsic[0, 2], v + intrinsic[1, 2]], axis=1)


# --- V2V4Real -------------------------------------------------------------------------------


def _v2v4real_pose(value: object) -> tuple[Array, str]:
    """``lidar_pose`` as a 4 x 4: a (numpy-tagged) 4 x 4, 16 flat values, or six values."""
    try:
        array = np.asarray(value, dtype=np.float64)
    except (TypeError, ValueError):
        raise ProbeError("lidar_pose is neither a 4x4 matrix nor six numbers") from None
    if array.size == 16:
        matrix = array.reshape(4, 4)
        if not bool(np.all(np.isfinite(matrix))):
            raise ProbeError("lidar_pose holds a value that is not finite")
        return matrix, "4x4 matrix"
    if array.size == 6:
        # The loader's x_to_world for a six-value pose (V4R-4).
        return pose_six(array.reshape(6), "flipped"), "six values"
    raise ProbeError("lidar_pose is neither a 4x4 matrix nor six numbers")


def _v2v4real_boxes(vehicles: Mapping[str, Any]) -> list[tuple[Box, float]]:
    """Boxes as written (centre = location + center, unrotated, as the loader adds them)."""
    out: list[tuple[Box, float]] = []
    for key, entry in vehicles.items():
        what = f"vehicle {key}" if str(key).lstrip("-").isdigit() else "a vehicle entry"
        item = _mapping(entry, what)
        roll, yaw, pitch = _numbers(item.get("angle"), (3,), f"{what} angle")
        rotation = euler_rotation(float(roll), float(pitch), float(yaw), "flipped")
        location = _numbers(item.get("location"), (3,), f"{what} location")
        center = _numbers(item.get("center", [0.0, 0.0, 0.0]), (3,), f"{what} center")
        extent = _numbers(item.get("extent"), (3,), f"{what} extent")
        out.append((Box(location + center, rotation, np.abs(extent)), float(yaw)))
    return out


def _mirror_box(box: Box) -> Box:
    return Box(MIRROR_Y @ box.centre, MIRROR_Y @ box.rotation @ MIRROR_Y, box.half)


def probe_v2v4real(scenario: Path, agent: str, frame: str) -> list[str]:
    folder = scenario / agent
    document = _mapping(_read_yaml(folder / f"{frame}.yaml", "the agent YAML"), "the agent YAML")
    points, total = _sweep(folder / f"{frame}.pcd", "the agent point cloud")
    if "lidar_pose" not in document:
        raise ProbeError("the agent YAML has no lidar_pose")
    lidar_world, pose_form = _v2v4real_pose(document["lidar_pose"])
    vehicles = _mapping(document.get("vehicles", {}) or {}, "vehicles")
    written = _v2v4real_boxes(vehicles)
    lines = [
        f"conventions_probe {PROBE_VERSION}",
        "dataset: v2v4real",
        f"frame: {frame}",
        f"points: {total} read, {points.shape[0]} used; boxes: {len(written)}; "
        f"lidar_pose: {pose_form}",
        "",
        "V4R-7 box frame x V4R-1 handedness: box containment of the LiDAR points",
    ]
    lidar_from_world = invert(lidar_world)
    mirror4 = homogeneous(MIRROR_Y, np.zeros(3))
    points_by: dict[str, Array] = {}
    boxes_by: dict[str, list[Box]] = {}
    for handedness in ("right", "left"):
        # ``left``: everything mirrored in y before use, as the adapter does.
        flip = handedness == "left"
        pts = points @ MIRROR_Y.T if flip else points
        pose = mirror4 @ lidar_from_world @ mirror4 if flip else lidar_from_world
        for frame_name in ("lidar", "world"):
            boxes = [_mirror_box(b) if flip else b for b, _ in written]
            if frame_name == "world":
                boxes = [b.moved(pose) for b in boxes]
            key = f"{frame_name}/{handedness}"
            points_by[key] = pts
            boxes_by[key] = boxes
    result = containment(points_by, boxes_by)
    lines.append("  box_frame handedness  inside    near  fraction")
    for key, c in result.items():
        frame_name, handedness = key.split("/")
        lines.append(
            f"  {frame_name:<9} {handedness:<10} {c.inside:7d} {c.near:7d}  {_fmt(c.fraction)}"
        )
    frame_scores = [
        Score(
            f,
            max(result[f"{f}/right"].fraction, result[f"{f}/left"].fraction),
            result[f"{f}/right"].near,
        )
        for f in ("lidar", "world")
    ]
    lines.append(verdict("V4R-7", "box_frame", frame_scores))
    best_frame = max(frame_scores, key=lambda s: -1.0 if math.isnan(s.value) else s.value).reading
    right, left = result[f"{best_frame}/right"], result[f"{best_frame}/left"]
    lines.append(
        f"verdict V4R-1 handedness: undecided by containment "
        f"(right {_fmt(right.fraction)}, left {_fmt(left.fraction)}; "
        "a frame and its mirror image hold the same points in the same boxes)"
    )
    lines.append("")
    lines += _traffic_side(written, lidar_from_world if best_frame == "world" else None)
    return lines


def _traffic_side(
    written: Sequence[tuple[Box, float]], lidar_from_world: Array | None
) -> list[str]:
    """Where oncoming vehicles are: a handedness cue that needs real right-hand traffic."""
    left = right = 0
    for box, _ in written:
        moved = box.moved(lidar_from_world) if lidar_from_world is not None else box
        heading = math.degrees(math.atan2(float(moved.rotation[1, 0]), float(moved.rotation[0, 0])))
        x, y = float(moved.centre[0]), float(moved.centre[1])
        if abs(abs(heading) - 180.0) > 30.0 or abs(x) > 40.0 or abs(y) < 1.0 or abs(y) > 15.0:
            continue
        if y > 0:
            left += 1
        else:
            right += 1
    return [
        "V4R-1 traffic-side cue (real data only; assumes LiDAR +x points forward)",
        f"  oncoming boxes (heading 180 +- 30 deg, |x| < 40 m): +y side {left}, -y side {right}",
        "  in right-hand traffic a right-handed x-forward frame has oncoming vehicles on +y",
    ]


# --- DAIR-V2X-C -----------------------------------------------------------------------------


def _relative(release: Path, side_folder: str | None, value: object, what: str) -> Path:
    """A ``data_info`` path, relative to the release folder or to the side folder."""
    if not isinstance(value, str) or not value:
        raise ProbeError(f"{what} has no path")
    parts = Path(value).parts
    if Path(value).is_absolute() or ".." in parts:
        raise ProbeError(f"{what} is not a relative path inside the release")
    candidates = [release / value]
    if side_folder is not None and (not parts or parts[0] != side_folder):
        candidates.insert(0, release / side_folder / value)
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise ProbeError(f"{what} is missing")


def _calib(document: object, what: str) -> Array:
    """``rotation`` + ``translation`` (nested or flat; maybe under ``transform``) as 4 x 4."""
    item = _mapping(document, what)
    if "rotation" not in item and isinstance(item.get("transform"), dict):
        item = _mapping(item["transform"], what)
    rotation = _numbers(item.get("rotation"), (3, 3), f"{what} rotation")
    translation = _numbers(item.get("translation"), (3,), f"{what} translation")
    return homogeneous(rotation, translation)


def _stem(value: object) -> str:
    return Path(value).stem if isinstance(value, str) else ""


def _side_row(release: Path, side: str, pointcloud: object) -> dict[str, Any]:
    """The side's ``data_info.json`` row of the cooperative row's point cloud."""
    folder = DAIR_SIDES[side]
    rows = _sequence(
        _read_json(release / folder / "data_info.json", f"the {side} data_info.json"),
        f"the {side} data_info.json",
    )
    if not isinstance(pointcloud, str):
        raise ProbeError(f"the cooperative row has no {side} point cloud path")
    wanted = Path(pointcloud).parts
    wanted = wanted[1:] if wanted and wanted[0] == folder else wanted
    for row in rows:
        if not isinstance(row, dict):
            continue
        own = _mapping(row, f"a {side} data_info row")
        value = own.get("pointcloud_path")
        if not isinstance(value, str):
            continue
        parts = Path(value).parts
        parts = parts[1:] if parts and parts[0] == folder else parts
        if parts == wanted:
            return own
    raise ProbeError(f"the {side} data_info.json has no row for the paired point cloud")


def _side_pose(release: Path, side: str, row: Mapping[str, Any]) -> Array:
    folder = DAIR_SIDES[side]
    if side == "vehicle":
        to_novatel = _calib(
            _read_json(
                _relative(
                    release, folder, row.get("calib_lidar_to_novatel_path"), "lidar_to_novatel"
                ),
                "lidar_to_novatel",
            ),
            "lidar_to_novatel",
        )
        to_world = _calib(
            _read_json(
                _relative(
                    release, folder, row.get("calib_novatel_to_world_path"), "novatel_to_world"
                ),
                "novatel_to_world",
            ),
            "novatel_to_world",
        )
        return to_world @ to_novatel
    return _calib(
        _read_json(
            _relative(
                release,
                folder,
                row.get("calib_virtuallidar_to_world_path"),
                "virtuallidar_to_world",
            ),
            "virtuallidar_to_world",
        ),
        "virtuallidar_to_world",
    )


def _single_view(
    release: Path, side: str, row: Mapping[str, Any], world_from_side: Array
) -> list[tuple[Array, Array]] | None:
    """Single-view label boxes of a side as (world centre, world forward), or ``None``."""
    folder = DAIR_SIDES[side]
    path: Path | None = None
    for key in ("label_lidar_std_path", "label_lidar_path", "label_virtuallidar_path"):
        if key in row:
            try:
                path = _relative(release, folder, row[key], f"the {side} single-view label")
                break
            except ProbeError:
                continue
    if path is None:
        return None
    entries = _sequence(_read_json(path, f"the {side} single-view label"), "a single-view label")
    out: list[tuple[Array, Array]] = []
    for entry in entries:
        item = _mapping(entry, "a single-view label entry")
        location = _mapping(item.get("3d_location"), "a single-view 3d_location")
        centre = _numbers([location.get(k) for k in ("x", "y", "z")], (3,), "a 3d_location")
        yaw = float(_numbers(item.get("rotation"), (1,), "a single-view rotation")[0])
        forward = np.array([math.cos(yaw), math.sin(yaw), 0.0])
        out.append((apply(world_from_side, centre[None, :])[0], world_from_side[:3, :3] @ forward))
    return out


def _cooperative_row(release: Path, frame_id: str) -> dict[str, Any]:
    rows = _sequence(
        _read_json(release / "cooperative" / "data_info.json", "the cooperative data_info.json"),
        "the cooperative data_info.json",
    )
    by_vehicle: dict[str, Any] | None = None
    for row in rows:
        if not isinstance(row, dict):
            continue
        own = _mapping(row, "a cooperative data_info row")
        if _stem(own.get("cooperative_label_path")) == frame_id:
            return own
        if by_vehicle is None and _stem(own.get("vehicle_pointcloud_path")) == frame_id:
            by_vehicle = own
    if by_vehicle is not None:
        return by_vehicle
    raise ProbeError("the cooperative data_info.json has no row for this frame id")


def probe_dair(root: Path, frame_id: str) -> list[str]:
    release = root / DAIR_RELEASE
    if not release.is_dir():
        raise ProbeError("the release folder cooperative-vehicle-infrastructure is missing")
    row = _cooperative_row(release, frame_id)
    label_name = Path(str(row.get("cooperative_label_path", f"{frame_id}.json"))).name
    complemented = (
        root / DAIR_COMPLEMENTED / "new_labels" / "cooperative_label" / "label_world" / label_name
    )
    if complemented.is_file():
        label_path, label_source = complemented, "complemented"
    else:
        label_path = _relative(
            release, None, row.get("cooperative_label_path"), "the cooperative label"
        )
        label_source = "release"
    entries = _sequence(_read_json(label_path, "the cooperative label"), "the cooperative label")
    corners: list[Array] = []
    for entry in entries:
        item = _mapping(entry, "a cooperative label entry")
        corners.append(_numbers(item.get("world_8_points"), (8, 3), "world_8_points"))
    lines = [
        f"conventions_probe {PROBE_VERSION}",
        "dataset: dair-v2x-c",
        f"frame: {frame_id}",
        f"cooperative boxes: {len(corners)} ({label_source} labels)",
        "",
    ]
    # The solid: the same for every order (the orders keep ring adjacency and 0-3 bottom).
    solids = [_solid(c) for c in corners]
    references: list[tuple[Array, Array]] = []
    lines.append("DAIR-10 corner order")
    lines.append(
        "  containment of each side's points in the world boxes (the same for every order)"
    )
    lines.append("  side            points  inside    near  fraction")
    for side in ("vehicle", "infrastructure"):
        side_row = _side_row(release, side, row.get(f"{side}_pointcloud_path"))
        pcd = _relative(
            release, DAIR_SIDES[side], side_row.get("pointcloud_path"), f"the {side} point cloud"
        )
        points, total = _sweep(pcd, f"the {side} point cloud")
        world_from_side = _side_pose(release, side, side_row)
        world_points = apply(world_from_side, points)
        c = containment({"any": world_points}, {"any": solids})["any"]
        lines.append(f"  {side:<15} {total:6d} {c.inside:7d} {c.near:7d}  {_fmt(c.fraction)}")
        labels = _single_view(release, side, side_row, world_from_side)
        if labels:
            references += labels
    lines.append("")
    lines.append("  order          right_handed  forward_agrees  matched  score  offset_deg")
    scores: list[Score] = []
    for order, signs in CORNER_ORDERS.items():
        right_handed = 0
        agrees = 0
        matched = 0
        offsets: list[int] = []
        for box in corners:
            axes = signs.T @ box  # rows: sum of signed corners along box x, y, z
            handed = float(np.linalg.det(axes)) > 0.0
            right_handed += int(handed)
            reference = _nearest(references, box.mean(axis=0))
            if reference is None:
                continue
            matched += 1
            angle = _angle_deg(axes[0], reference)
            offsets.append(int(round(angle / 90.0)) % 4 * 90)
            agrees += int(handed and _wrap(angle) <= HEADING_AGREE_DEG)
        score = agrees / matched if matched else float("nan")
        common = max(set(offsets), key=offsets.count) if offsets else -1
        offset_text = f"{common:10d}" if common >= 0 else "       n/a"
        lines.append(
            f"  {order:<14} {right_handed:5d} of {len(corners):<4} {agrees:9d}       "
            f"{matched:6d}  {_fmt(score)}  {offset_text}"
        )
        scores.append(Score(order, score, matched * 8))
    lines.append(
        "  (forward: the order's box +x against the single-view label's rotation, read as "
        "radians from +x; offset_deg: the most common heading difference)"
    )
    lines.append(verdict("DAIR-10", "corner_order", scores))
    return lines


def _solid(corners: Array) -> Box:
    """The box the eight corners span, whatever their order within the documented rings."""
    centre = corners.mean(axis=0)
    edge_a = corners[1] - corners[0]
    edge_b = corners[3] - corners[0]
    edge_c = corners[4] - corners[0]
    axes = [edge_a, edge_b, edge_c]
    lengths = [float(np.linalg.norm(e)) for e in axes]
    if min(lengths) < 1e-6:
        return Box(centre, np.eye(3), np.zeros(3))
    x = edge_a / lengths[0]
    z = edge_c - x * float(x @ edge_c)
    z = z / max(float(np.linalg.norm(z)), 1e-9)
    y = np.cross(z, x)
    rotation = np.stack([x, y, z], axis=1)
    local = (corners - centre) @ rotation
    return Box(centre, rotation, np.abs(local).max(axis=0))


def _nearest(references: Sequence[tuple[Array, Array]], centre: Array) -> Array | None:
    best: Array | None = None
    best_distance = MATCH_DISTANCE_M
    for position, forward in references:
        distance = float(np.linalg.norm((position - centre)[:2]))
        if distance <= best_distance:
            best, best_distance = forward, distance
    return best


def _angle_deg(a: Array, b: Array) -> float:
    """Counter-clockwise angle from ``b`` to ``a`` in the ground plane, in [0, 360)."""
    angle = math.degrees(
        math.atan2(float(a[1]), float(a[0])) - math.atan2(float(b[1]), float(b[0]))
    )
    return angle % 360.0


def _wrap(angle_deg: float) -> float:
    return min(angle_deg, 360.0 - angle_deg)


# --- command line ---------------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="conventions_probe",
        description="Score the readings of open geometry conventions on one real frame. "
        "Read-only; prints numbers only.",
    )
    sub = parser.add_subparsers(dest="dataset", required=True)
    for name in ("opv2v", "v2xset"):
        p = sub.add_parser(name, help=f"{name}: camera extrinsic direction and Euler signs")
        p.add_argument("scenario", type=Path, help="the scenario folder")
        p.add_argument("agent", help="the agent folder name, e.g. 650 or -1")
        p.add_argument("frame", help="the frame stem, e.g. 000068")
    p = sub.add_parser("v2v4real", help="v2v4real: box frame and handedness")
    p.add_argument("scenario", type=Path, help="the scenario folder")
    p.add_argument("agent", help="the agent folder name, e.g. 0")
    p.add_argument("frame", help="the frame stem, e.g. 000000")
    p = sub.add_parser("dair", help="dair-v2x-c: world_8_points corner order")
    p.add_argument("root", type=Path, help="the folder holding cooperative-vehicle-infrastructure")
    p.add_argument("frame", help="the cooperative frame id, e.g. 000010")
    return parser


def _plain(value: str, what: str) -> str:
    """An id argument: a single path component, no separators."""
    if not value or "/" in value or "\\" in value or value in (".", ".."):
        raise ProbeError(f"the {what} must be a plain name, not a path")
    return value


def run(argv: Sequence[str] | None = None) -> list[str]:
    """The report lines for ``argv``; raises :class:`ProbeError` on bad input."""
    args = _parser().parse_args(argv)
    dataset: str = args.dataset
    if dataset in ("opv2v", "v2xset"):
        return probe_opencood(
            dataset, args.scenario, _plain(args.agent, "agent"), _plain(args.frame, "frame")
        )
    if dataset == "v2v4real":
        return probe_v2v4real(
            args.scenario, _plain(args.agent, "agent"), _plain(args.frame, "frame")
        )
    return probe_dair(args.root, _plain(args.frame, "frame id"))


def _print(text: str) -> None:
    print(text, flush=True)


def main(argv: Sequence[str] | None = None, *, write: Callable[[str], None] | None = None) -> int:
    out = write if write is not None else _print
    try:
        lines = run(argv)
    except ProbeError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    except MemoryError:
        print("error: out of memory", file=sys.stderr)
        return 2
    except Exception as error:  # noqa: BLE001 - no traceback: it would print paths
        print(f"error: internal ({type(error).__name__}); please report this line", file=sys.stderr)
        return 3
    for line in lines:
        out(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
