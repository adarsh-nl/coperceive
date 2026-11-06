# SPDX-License-Identifier: Apache-2.0
"""Pure conversions between Coperceive schema 1 and OpenCOOD's input and output conventions.

Standard library only, and no import of coperceive or OpenCOOD: the worker passes the
protocol's plain JSON values in and receives plain lists out. The conventions come from
public documentation only:

- Schema 1 poses are right-handed ``world <- body`` (``FLU`` body: +x forward, +y left,
  +z up) and the OPV2V/V2XSet adapters applied the ADR 0012 reflection ``M = diag(1,-1,1)``
  to the left-handed CARLA source; this module inverts that once.
- OpenCOOD reads a pose as ``[x, y, z, roll, yaw, pitch]`` in **degrees** in the CARLA
  world and builds the source-to-world matrix with ``x_to_world``
  (``opencood/utils/transformation_utils.py``). Its numeric rotation is
  ``Rz(yaw) @ Ry(-pitch) @ Rx(-roll)``; the inverse of the ADR reflection therefore turns
  schema ``(x, y, z, roll, yaw, pitch)`` into CARLA ``(x, -y, z, roll, -yaw, -pitch)``. On
  OPV2V/V2XSet roll and pitch are zero in the files, so in practice this is the y reflection
  plus a negated yaw, as ADR 0012 says.
- A prediction comes back as ego-frame corners ``(N, 8, 3)`` in the order
  ``boxes_to_corners_3d`` writes (or as ``(N, 7)`` [x, y, z, then three sizes in the
  config's ``postprocess.order``, then yaw]); ``postprocess.order`` is read from the
  checkpoint's own config, never assumed.
"""

from __future__ import annotations

import math

Vec3 = tuple  # no runtime alias: the worker may run on Python 3.7
DEG = math.pi / 180.0


class ConvertError(ValueError):
    """A pose, a point cloud or a box does not follow the shape this module converts."""


# -- small rigid-transform helpers -----------------------------------------------------------


def _matmul(a: list[list[float]], b: list[list[float]]) -> list[list[float]]:
    return [
        [sum(a[i][k] * b[k][j] for k in range(len(b))) for j in range(len(b[0]))]
        for i in range(len(a))
    ]


def _matvec(matrix: list[list[float]], vector: tuple[float, float, float]) -> tuple:
    return (
        matrix[0][0] * vector[0] + matrix[0][1] * vector[1] + matrix[0][2] * vector[2],
        matrix[1][0] * vector[0] + matrix[1][1] * vector[1] + matrix[1][2] * vector[2],
        matrix[2][0] * vector[0] + matrix[2][1] * vector[1] + matrix[2][2] * vector[2],
    )


def _identity4() -> list[list[float]]:
    return [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0], [0.0, 0.0, 1.0, 0.0], [0.0, 0.0, 0.0, 1.0]]


def rotation_matrix(qw: float, qx: float, qy: float, qz: float) -> list[list[float]]:
    """The 3x3 rotation of a quaternion ``(qw, qx, qy, qz)``; it need not be normalised."""
    norm = math.sqrt(qw * qw + qx * qx + qy * qy + qz * qz)
    if not math.isfinite(norm) or norm == 0.0:
        raise ConvertError("quaternion has zero or non-finite norm")
    qw, qx, qy, qz = qw / norm, qx / norm, qy / norm, qz / norm
    return [
        [
            1.0 - 2.0 * (qy * qy + qz * qz),
            2.0 * (qx * qy - qz * qw),
            2.0 * (qx * qz + qy * qw),
        ],
        [
            2.0 * (qx * qy + qz * qw),
            1.0 - 2.0 * (qx * qx + qz * qz),
            2.0 * (qy * qz - qx * qw),
        ],
        [
            2.0 * (qx * qz - qy * qw),
            2.0 * (qy * qz + qx * qw),
            1.0 - 2.0 * (qx * qx + qy * qy),
        ],
    ]


def quaternion_from_rpy_deg(roll: float, pitch: float, yaw: float) -> list[float]:
    """``Rz(yaw) @ Ry(pitch) @ Rx(roll)`` as ``[qw, qx, qy, qz]`` (schema 1's convention)."""
    cr, sr = math.cos(roll * DEG / 2.0), math.sin(roll * DEG / 2.0)
    cp, sp = math.cos(pitch * DEG / 2.0), math.sin(pitch * DEG / 2.0)
    cy, sy = math.cos(yaw * DEG / 2.0), math.sin(yaw * DEG / 2.0)
    return [
        cr * cp * cy + sr * sp * sy,
        sr * cp * cy - cr * sp * sy,
        cr * sp * cy + sr * cp * sy,
        cr * cp * sy - sr * sp * cy,
    ]


def quaternion_multiply(a: list[float], b: list[float]) -> list[float]:
    aw, ax, ay, az = a
    bw, bx, by, bz = b
    return [
        aw * bw - ax * bx - ay * by - az * bz,
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
    ]


def quaternion_rotate(q: list[float], point: tuple[float, float, float]) -> tuple:
    """``q * (0, point) * q^-1``; ``q`` is normalised first."""
    norm = math.sqrt(sum(value * value for value in q))
    if not math.isfinite(norm) or norm == 0.0:
        raise ConvertError("quaternion has zero or non-finite norm")
    qw, qx, qy, qz = (value / norm for value in q)
    # Unit-quaternion rotation: p' = p + 2 * q_vec x (q_vec x p + qw p).
    vx, vy, vz = qx, qy, qz
    px, py, pz = point
    tx = vy * pz - vz * py + qw * px
    ty = vz * px - vx * pz + qw * py
    tz = vx * py - vy * px + qw * pz
    return (
        px + 2.0 * (vy * tz - vz * ty),
        py + 2.0 * (vz * tx - vx * tz),
        pz + 2.0 * (vx * ty - vy * tx),
    )


def _unit(values: list[float], where: str) -> list[float]:
    norm = math.sqrt(sum(value * value for value in values))
    if not math.isfinite(norm) or norm == 0.0:
        raise ConvertError(f"{where}: zero or non-finite norm")
    return [value / norm for value in values]


def _pose_quat_translation(pose: object, where: str) -> tuple[list[float], tuple]:
    """``(qw, qx, qy, qz)`` and ``(tx, ty, tz)`` from the protocol's pose object."""
    if not isinstance(pose, dict):
        raise ConvertError(f"{where}: expected a pose object")
    values = []
    for key in ("qw", "qx", "qy", "qz", "tx", "ty", "tz"):
        value = pose.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ConvertError(f"{where}: {key} must be a number")
        number = float(value)
        if not math.isfinite(number):
            raise ConvertError(f"{where}: {key} must be finite")
        values.append(number)
    return _unit(values[:4], where), (values[4], values[5], values[6])


def euler_zyx_deg(rotation: list[list[float]]) -> tuple[float, float, float]:
    """``(roll, pitch, yaw)`` in degrees so that ``R = Rz(yaw) @ Ry(pitch) @ Rx(roll)``."""
    clamped = max(-1.0, min(1.0, -rotation[2][0]))
    pitch = math.asin(clamped)
    roll = math.atan2(rotation[2][1], rotation[2][2])
    yaw = math.atan2(rotation[1][0], rotation[0][0])
    return roll / DEG, pitch / DEG, yaw / DEG


# -- schema -> OpenCOOD ---------------------------------------------------------------------


def carla_pose_from_schema(pose: object, where: str = "pose") -> list[float]:
    """The OpenCOOD ``[x, y, z, roll, yaw, pitch]`` (degrees) for a schema pose object.

    This is the inverse of the ADR 0012 reflection expressed in OpenCOOD's own angle
    convention: CARLA ``(x, y, z)`` is ``(x, -y, z)``, and the CARLA angles are schema
    ``(roll, -yaw, -pitch)`` once the schema rotation is read as ``Rz(yaw)Ry(pitch)Rx(roll)``.
    """
    quat, translation = _pose_quat_translation(pose, where)
    roll, pitch, yaw = euler_zyx_deg(rotation_matrix(*quat))
    return [translation[0], -translation[1], translation[2], roll, -yaw, -pitch]


def carla_x_to_world(pose: list[float]) -> list[list[float]]:
    """OpenCOOD's source-to-world matrix for ``[x, y, z, roll, yaw, pitch]`` degrees.

    Same matrix as ``x_to_world`` of ``opencood/utils/transformation_utils.py``:
    ``R = Rz(yaw) @ Ry(-pitch) @ Rx(-roll)`` with the translation in the last column.
    Written here from the rotation convention alone; no OpenCOOD code is copied.
    """
    if len(pose) != 6:
        raise ConvertError(f"a CARLA pose is [x, y, z, roll, yaw, pitch], got {len(pose)} values")
    x, y, z, roll, yaw, pitch = (float(value) for value in pose)
    # R = Rz(yaw) @ Ry(-pitch) @ Rx(-roll): the same angles with the pitch and roll negated.
    cr, sr = math.cos(-roll * DEG), math.sin(-roll * DEG)
    cp, sp = math.cos(-pitch * DEG), math.sin(-pitch * DEG)
    cy, sy = math.cos(yaw * DEG), math.sin(yaw * DEG)
    rotation = [
        [cy * cp, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr],
        [sy * cp, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr],
        [-sp, cp * sr, cp * cr],
    ]
    return [
        [rotation[0][0], rotation[0][1], rotation[0][2], x],
        [rotation[1][0], rotation[1][1], rotation[1][2], y],
        [rotation[2][0], rotation[2][1], rotation[2][2], z],
        [0.0, 0.0, 0.0, 1.0],
    ]


def _transpose(matrix: list[list[float]]) -> list[list[float]]:
    return [[matrix[j][i] for j in range(len(matrix))] for i in range(len(matrix[0]))]


def x1_to_x2(pose1: list[float], pose2: list[float]) -> list[list[float]]:
    """OpenCOOD's ``x1_to_x2``: source frame 1 to source frame 2 (fine to re-derive)."""
    x1 = carla_x_to_world(pose1)
    x2 = carla_x_to_world(pose2)
    rotation = _transpose([row[:3] for row in x2[:3]])
    translation = _matvec(rotation, (-x2[0][3], -x2[1][3], -x2[2][3]))
    inverse = [
        [rotation[0][0], rotation[0][1], rotation[0][2], translation[0]],
        [rotation[1][0], rotation[1][1], rotation[1][2], translation[1]],
        [rotation[2][0], rotation[2][1], rotation[2][2], translation[2]],
        [0.0, 0.0, 0.0, 1.0],
    ]
    return _matmul(inverse, x1)


def points_to_carla(points: object, where: str = "points") -> list[list[float]]:
    """``[x, y, z, intensity]`` rows from the schema sensor frame to OpenCOOD's CARLA frame."""
    if not isinstance(points, (list, tuple)):
        raise ConvertError(f"{where}: expected a list of [x, y, z, intensity] rows")
    out: list[list[float]] = []
    for index, row in enumerate(points):
        if not isinstance(row, (list, tuple)) or len(row) != 4:
            raise ConvertError(f"{where}[{index}]: expected [x, y, z, intensity]")
        x, y, z, intensity = (float(value) for value in row)
        out.append([x, -y, z, intensity])
    return out


# -- OpenCOOD -> schema ---------------------------------------------------------------------


def _centre_size_lwh(values: list[float], order: str) -> list[float]:
    """``[length, width, height]`` from a 7-vector's three sizes in the config's order.

    ``postprocess.order`` reorders only the 7-vector: ``hwl`` stores ``[h, w, l]``, ``lwh``
    stores ``[l, w, h]``. (The corner format is order-independent; upstream reorders the
    7-vector before writing corners and always uses the l/w/h template, see
    ``opencood_corners``.)
    """
    if order == "lwh":
        return [values[3], values[4], values[5]]
    if order == "hwl":
        return [values[5], values[4], values[3]]
    raise ConvertError(f"postprocess order must be lwh or hwl, got {order!r}")


def opencood_corners(
    center: tuple[float, float, float],
    size_lwh: tuple[float, float, float],
    heading_deg: float,
) -> list[tuple[float, float, float]]:
    """The eight corners upstream ``boxes_to_corners_3d`` writes for an l/w/h box.

    Upstream's corner template is always length, width, height on its x, y, z axes; the
    ``order`` config key only reorders the 7-vector *before* this step
    (``boxes3d[:, [0, 1, 2, 5, 4, 3, 6]]`` for ``hwl``). The corner output is therefore the
    same geometry for both orders, which is what ``center_from_corners`` inverts.
    """
    dims = [size_lwh[0] / 2.0, size_lwh[1] / 2.0, size_lwh[2] / 2.0]
    cy, sy = math.cos(heading_deg * DEG), math.sin(heading_deg * DEG)
    signs = (
        (1.0, -1.0, -1.0),
        (1.0, 1.0, -1.0),
        (-1.0, 1.0, -1.0),
        (-1.0, -1.0, -1.0),
        (1.0, -1.0, 1.0),
        (1.0, 1.0, 1.0),
        (-1.0, 1.0, 1.0),
        (-1.0, -1.0, 1.0),
    )
    corners: list[tuple[float, float, float]] = []
    for sx, sy_, sz in signs:
        lx, ly, lz = sx * dims[0], sy_ * dims[1], sz * dims[2]
        corners.append(
            (
                center[0] + cy * lx - sy * ly,
                center[1] + sy * lx + cy * ly,
                center[2] + lz,
            )
        )
    return corners


def center_from_corners(corners: object) -> tuple[list[float], list[float], float]:
    """``([cx, cy, cz], [l, w, h], heading_deg)`` from eight OpenCOOD ego-frame corners.

    The corner template is always l, w, h (upstream ``corner_to_center`` takes the two
    sizes from the horizontal edges and the height from z for both config orders), so no
    order argument is involved and an ``hwl`` config cannot swap length and height here.
    """
    rows = _corner_rows(corners)
    center = [
        sum(row[0] for row in rows) / 8.0,
        sum(row[1] for row in rows) / 8.0,
        sum(row[2] for row in rows) / 8.0,
    ]
    edges = []
    for first, second in ((0, 3), (1, 0), (4, 0)):  # template x, y then z axis
        dx = rows[first][0] - rows[second][0]
        dy = rows[first][1] - rows[second][1]
        dz = rows[first][2] - rows[second][2]
        edges.append(math.sqrt(dx * dx + dy * dy + dz * dz))
    first, second, third = edges
    if first == 0.0 or second == 0.0 or third == 0.0:
        raise ConvertError("degenerate prediction box: a corner edge has zero length")
    heading = math.degrees(math.atan2(rows[0][1] - rows[3][1], rows[0][0] - rows[3][0]))
    # Template axis 0 is length, 1 is width, 2 is height, for both config orders.
    return center, [first, second, third], heading


def _corner_rows(corners: object) -> list[tuple[float, float, float]]:
    if not isinstance(corners, (list, tuple)) or len(corners) != 8:
        raise ConvertError("a corner box is a list of eight [x, y, z] rows")
    rows: list[tuple[float, float, float]] = []
    for index, row in enumerate(corners):
        if not isinstance(row, (list, tuple)) or len(row) != 3:
            raise ConvertError(f"corner {index}: expected [x, y, z]")
        rows.append((float(row[0]), float(row[1]), float(row[2])))
    return rows


def _center7(center7: object, order: str) -> tuple[list[float], list[float], float]:
    """``([x, y, z], [l, w, h], heading_deg)`` from a 7-vector in the config's order.

    The 7-vector **yaw is in radians**, not degrees: OpenCOOD's
    ``VoxelPostprocessor.delta_to_boxes3d`` computes ``yaw = delta + anchor_heading`` where
    ``generate_anchor_box`` made the anchor headings ``math.radians(r)`` for the config's
    degree list ``r: [0, 90]``, and ``boxes_to_corners_3d`` rotates with that radian value.
    This converts it to the protocol's degrees. (The corner path needs no conversion: it is
    geometry.) See ``opencood/data_utils/post_processor/voxel_postprocessor.py``.
    """
    if not isinstance(center7, (list, tuple)) or len(center7) != 7:
        raise ConvertError("a centre box is [x, y, z, d0, d1, d2, yaw]")
    values = [float(value) for value in center7]
    return values[:3], _centre_size_lwh(values, order), math.degrees(values[6])


def boxes_from_opencood(
    boxes: object, order: str, scores: object = None
) -> list[dict[str, object]]:
    """``[{center, size_lwh, heading, score}]`` from ego-frame corners or centre boxes.

    ``boxes`` is a sequence of ``(N, 8, 3)`` corners or ``(N, 7)`` centre boxes; ``order``
    is the checkpoint config's ``postprocess.order`` and applies only to the 7-vector sizes
    (the upstream corner template is always l, w, h). A 7-vector's yaw is in radians and is
    converted to degrees here; corners are pure geometry. The score list, when given, matches
    ``boxes``.
    """
    if not isinstance(boxes, (list, tuple)) or len(boxes) == 0:
        return []
    first = boxes[0]
    if not isinstance(first, (list, tuple)):
        raise ConvertError("each box must be a list or tuple")
    if len(first) == 8:
        kind = "corners"
    elif len(first) == 7:
        kind = "centres"
    else:
        raise ConvertError(f"each box has 7 (centre) or 8 (corners) values, got {len(first)}")
    if scores is None:
        scores = [None] * len(boxes)
    if not isinstance(scores, (list, tuple)) or len(scores) != len(boxes):
        raise ConvertError("scores must match the boxes")
    out: list[dict[str, object]] = []
    for index, box in enumerate(boxes):
        if kind == "corners":
            center, size, heading = center_from_corners(box)
        else:
            center, size, heading = _center7(box, order)
        score = scores[index]
        if score is None:
            out.append({"kind": kind, "center": center, "size_lwh": size, "heading": heading})
        else:
            out.append(
                {
                    "kind": kind,
                    "center": center,
                    "size_lwh": size,
                    "heading": heading,
                    "score": float(score),
                }
            )
    return out


def box_to_protocol(
    *,
    center: list[float],
    size_lwh: list[float],
    heading_deg: float,
    ego_pose: object,
    agent_id: str,
    source: str,
    category: str,
    score: float,
) -> dict[str, object]:
    """One protocol result box in ``frame: world`` from an OpenCOOD ego-frame prediction box.

    The prediction box is in the ego vehicle's OpenCOOD (CARLA) frame. With
    ``S = diag(1, -1, 1)`` and ``T_scene_ego`` the schema ego pose (``world <- body``), the
    input side sends CARLA poses such that ``x_to_world(carla_pose) = S · R · S`` and
    ``t_carla = S · t`` for the schema rotation ``R``, so a point of the OpenCOOD ego frame
    is ``S · p_ego`` in the schema ego frame and the box map is

        p_scene = T_scene_ego · S · p_ego

    (equivalently ``S · T_carla · p_ego`` with ``T_carla = S · T_scene_ego · S``). A box yaw
    ``θ`` in the OpenCOOD ego frame is ``Rz(-θ)`` in the schema ego frame; composing yaws
    gives ``yaw_world = yaw_ego_schema - θ``. Example: ego yaw 30° at ``(10, 20, 1.9)`` and
    OpenCOOD-ego box centre ``[0, 2, 0.5]`` heading 45° map to
    ``T_scene_ego · S · [0, 2, 0.5] = (11.0, 18.267949..., 2.4)`` with world yaw
    ``30° - 45° = -15°``. A yaw-90 box therefore points along schema ``-y``, matching the
    reflected dataset.
    """
    if len(center) != 3 or len(size_lwh) != 3:
        raise ConvertError("a prediction box needs a 3-vector centre and a 3-vector size")
    if any(value <= 0.0 for value in size_lwh):
        raise ConvertError("prediction box sizes must be positive")
    quat, translation = _pose_quat_translation(ego_pose, "ego pose")
    box_center_schema = (float(center[0]), -float(center[1]), float(center[2]))
    box_quat_schema = quaternion_from_rpy_deg(0.0, 0.0, -float(heading_deg))
    world_center = quaternion_rotate(quat, box_center_schema)
    world_center = (
        world_center[0] + translation[0],
        world_center[1] + translation[1],
        world_center[2] + translation[2],
    )
    world_quat = _unit(quaternion_multiply(quat, box_quat_schema), "world box quaternion")
    return {
        "agent_id": agent_id,
        "source": source,
        "category": category,
        "score": float(score),
        "track_id": None,
        "center": list(world_center),
        "size_lwh": [float(value) for value in size_lwh],
        "quaternion": list(world_quat),
        "frame": "world",
    }
