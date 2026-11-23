# SPDX-License-Identifier: Apache-2.0
"""Geometry of the reference evaluator: ``docs/eval-spec.md`` Part A, sections A.1 and A.3.

Rotations, placing a box in the world, yaw, the BEV footprint, BEV IoU, centre
distance, yaw error and range. Written from Part A alone; imports nothing from
``coperceive``. The footprint intersection area is an exact polygon overlay by
GEOS through ``shapely``; nothing here clips polygons by hand. Following the
numerical note of A.3, the two footprints of a pair are handed to GEOS in
coordinates relative to the midpoint of the pair's centres.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import shapely
from shapely.geometry.base import BaseGeometry

from tests.evalcheck.scenario import FRAME_BODY, FRAME_WORLD, BoxSpec, PoseSample, Quat, Vec3

Matrix3 = tuple[Vec3, Vec3, Vec3]
Point2 = tuple[float, float]

# A.1 item 6: how far the norm of a prediction's quaternion may be from 1.
UNIT_NORM_TOLERANCE = 1e-6


@dataclass(frozen=True)
class PlacedBox:
    """A box placed in the world (A.3): world centre, unchanged size, world rotation."""

    center: Vec3
    size_lwh: Vec3
    rotation: Quat


# --- A.1 rotations ----------------------------------------------------------------


def rotation_matrix(q: Quat) -> Matrix3:
    """A.1 item 3: the matrix ``R(q)`` of ``q = (w, x, y, z)``, used as given (A.1 item 6)."""
    w, x, y, z = q
    return (
        (1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)),
        (2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)),
        (2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)),
    )


def hamilton_product(a: Quat, b: Quat) -> Quat:
    """A.1 item 3: ``a ∘ b`` (apply ``b`` first), so that ``R(a ∘ b) = R(a) R(b)``."""
    aw, ax, ay, az = a
    bw, bx, by, bz = b
    return (
        aw * bw - ax * bx - ay * by - az * bz,
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
    )


def transform_point(pose: PoseSample, p_body: Vec3) -> Vec3:
    """A.1 item 4: ``p_world = R(q) p_body + t`` for the pose ``world_from_body = (q, t)``."""
    r = rotation_matrix(pose.rotation)
    t = pose.translation
    return (
        r[0][0] * p_body[0] + r[0][1] * p_body[1] + r[0][2] * p_body[2] + t[0],
        r[1][0] * p_body[0] + r[1][1] * p_body[1] + r[1][2] * p_body[2] + t[1],
        r[2][0] * p_body[0] + r[2][1] * p_body[1] + r[2][2] * p_body[2] + t[2],
    )


# --- A.3 placing a box --------------------------------------------------------------


def place_box(box: BoxSpec, pose: PoseSample | None) -> PlacedBox:
    """A.3 "Placing a box in the world".

    ``pose`` is the pose sample of the row's agent whose time is exactly the row's
    time, or ``None`` when the agent has no pose sample at that time. A world-frame
    box does not use it. A body-frame box without it is an error (no interpolation).
    The size is never changed.
    """
    if box.frame == FRAME_WORLD:
        return PlacedBox(center=box.center, size_lwh=box.size_lwh, rotation=box.rotation)
    if box.frame == FRAME_BODY:
        if pose is None:
            raise ValueError(
                "a body-frame box needs the pose sample of its agent at exactly its time (A.3)"
            )
        return PlacedBox(
            center=transform_point(pose, box.center),
            size_lwh=box.size_lwh,
            rotation=hamilton_product(pose.rotation, box.rotation),
        )
    raise ValueError(f"unknown box frame {box.frame!r}: expected 'body' or 'world' (A.1 item 5)")


def check_prediction_box(box: BoxSpec) -> None:
    """The checks the box of a prediction row must pass before evaluation.

    A.1 item 6: its quaternion is refused unless ``| |q| - 1 | <= 1e-6``.
    A.3 "Footprint": its sizes are refused unless all three are positive.
    Pose quaternions and annotation boxes are not checked.
    """
    w, x, y, z = box.rotation
    norm = math.sqrt(w * w + x * x + y * y + z * z)
    if not abs(norm - 1.0) <= UNIT_NORM_TOLERANCE:
        raise ValueError(
            f"the quaternion of a prediction must have unit norm within "
            f"{UNIT_NORM_TOLERANCE}, got norm {norm!r} (A.1 item 6)"
        )
    if not all(size > 0.0 for size in box.size_lwh):
        raise ValueError(
            f"the sizes of a prediction must all be positive, got {box.size_lwh!r} (A.3)"
        )


# --- A.3 yaw and footprint ----------------------------------------------------------


def yaw_rad(rotation: Quat) -> float:
    """A.3 "Yaw": ``atan2(R[1][0], R[0][0])``, with ``atan2(0, 0) = 0``.

    For a box whose x axis is vertical only to within rounding, both arguments are
    rounding noise and the result is arbitrary: such a box has no heading in BEV.
    """
    r = rotation_matrix(rotation)
    sin_term = r[1][0]  # 2(xy + wz)
    cos_term = r[0][0]  # 1 - 2(y² + z²)
    if sin_term == 0.0 and cos_term == 0.0:
        # Exactly vertical x axis. Written out so that signed zeros cannot turn it into ±π.
        return 0.0
    return math.atan2(sin_term, cos_term)


@dataclass(frozen=True)
class Rectangle:
    """The footprint of a placed box (A.3), in a form that can be written about any origin.

    ``centre`` is ``(c_w.x, c_w.y)``, ``half_length`` is ``(length/2)(cos ψ, sin ψ)``,
    ``half_width`` is ``(width/2)(-sin ψ, cos ψ)`` and ``area`` is ``length · width``,
    all from the sizes as given: a tilted box is not projected.
    """

    centre: Point2
    half_length: Point2
    half_width: Point2
    area: float


def rectangle_of(box: PlacedBox) -> Rectangle:
    """A.3 "Footprint": the centre, the two half sides and the area of a placed box."""
    psi = yaw_rad(box.rotation)
    cos_psi = math.cos(psi)
    sin_psi = math.sin(psi)
    half_length = box.size_lwh[0] / 2
    half_width = box.size_lwh[1] / 2
    return Rectangle(
        centre=(box.center[0], box.center[1]),
        half_length=(half_length * cos_psi, half_length * sin_psi),
        half_width=(half_width * -sin_psi, half_width * cos_psi),
        area=box.size_lwh[0] * box.size_lwh[1],
    )


def corners_about(rectangle: Rectangle, origin: Point2) -> tuple[Point2, ...]:
    """The four corners of a footprint relative to ``origin`` (A.3 "Footprint").

    ``origin`` is subtracted from the centre first and the half sides are added
    after (A.3 "Numerical note"). The corners are in ring order, for ``(s_x, s_y)``
    equal to ``(+1, +1), (-1, +1), (-1, -1), (+1, -1)``.
    """
    x = rectangle.centre[0] - origin[0]
    y = rectangle.centre[1] - origin[1]
    lx, ly = rectangle.half_length
    wx, wy = rectangle.half_width
    # centre + s_x · half_length + s_y · half_width, written out for the four sign pairs.
    return (
        (x + lx + wx, y + ly + wy),
        (x - lx + wx, y - ly + wy),
        (x - lx - wx, y - ly - wy),
        (x + lx - wx, y + ly - wy),
    )


def footprint(box: PlacedBox) -> tuple[Point2, ...]:
    """A.3 "Footprint": the four corners of the rectangle in the world XY plane.

    The corners are world coordinates, in ring order, for ``(s_x, s_y)`` equal to
    ``(+1, +1), (-1, +1), (-1, -1), (+1, -1)``.
    """
    return corners_about(rectangle_of(box), (0.0, 0.0))


# --- A.3 pair measures --------------------------------------------------------------


def _bounds_apart(a: Rectangle, b: Rectangle) -> bool:
    """Whether the axis-aligned bounds of two footprints are apart by more than rounding.

    A footprint lies within ``centre ± (|hl.x| + |hw.x|, |hl.y| + |hw.y|)``; when the two
    bounds are separated along x or y (with a margin of ``1e-6`` m plus ``1e-9`` relative,
    far above the rounding of the corners), the footprints are disjoint.
    """
    reach_x = (
        abs(a.half_length[0]) + abs(a.half_width[0]) + abs(b.half_length[0]) + abs(b.half_width[0])
    )
    reach_y = (
        abs(a.half_length[1]) + abs(a.half_width[1]) + abs(b.half_length[1]) + abs(b.half_width[1])
    )
    apart_x = abs(a.centre[0] - b.centre[0]) > reach_x * (1.0 + 1e-9) + 1e-6
    apart_y = abs(a.centre[1] - b.centre[1]) > reach_y * (1.0 + 1e-9) + 1e-6
    return apart_x or apart_y


def ious_of_rectangles(pairs: Sequence[tuple[Rectangle, Rectangle]]) -> list[float]:
    """A.3 "BEV IoU" of pairs of footprints, with a single call into GEOS for all of them.

    ``IoU = I / (A_a + A_b - I)`` and ``0`` when ``A_a + A_b - I <= 0``, with
    ``A = length · width`` and ``I`` the area of the GEOS overlay of the two
    footprints. Height and z do not enter. The value is not clamped.

    A.3 "Numerical note": the two footprints of a pair are written relative to the
    midpoint of their two centres before they are handed to GEOS.
    """
    rings_a: list[tuple[Point2, ...]] = []
    rings_b: list[tuple[Point2, ...]] = []
    overlaid: list[int] = []  # positions of the pairs that are handed to GEOS
    for position, (a, b) in enumerate(pairs):
        if a.area == 0.0 or b.area == 0.0:
            # A footprint with a zero side is a segment or a point: it has no area to
            # share, and it is not a valid polygon to hand to the overlay.
            continue
        if _bounds_apart(a, b):
            # The footprints cannot meet: their overlay is empty and its area is 0, which
            # is what GEOS would return. Only a shortcut; it never decides an overlap.
            continue
        midpoint = ((a.centre[0] + b.centre[0]) / 2, (a.centre[1] + b.centre[1]) / 2)
        rings_a.append(corners_about(a, midpoint))
        rings_b.append(corners_about(b, midpoint))
        overlaid.append(position)

    intersections = [0.0] * len(pairs)
    if overlaid:
        # The array forms of Polygon(...) and .intersection(...): the same GEOS calls.
        polygons_a = shapely.polygons(rings_a)
        polygons_b = shapely.polygons(rings_b)
        overlays: list[BaseGeometry] = shapely.intersection(polygons_a, polygons_b).tolist()
        for position, overlay in zip(overlaid, overlays, strict=True):
            intersections[position] = overlay.area

    ious: list[float] = []
    for (a, b), intersection in zip(pairs, intersections, strict=True):
        union = a.area + b.area - intersection
        ious.append(0.0 if union <= 0.0 else intersection / union)
    return ious


def bev_iou(a: PlacedBox, b: PlacedBox) -> float:
    """A.3 "BEV IoU" of two placed boxes."""
    return ious_of_rectangles([(rectangle_of(a), rectangle_of(b))])[0]


def centre_distance_m(a: PlacedBox, b: PlacedBox) -> float:
    """A.3 "Centre distance": horizontal distance of the two world centres; z does not enter."""
    return math.hypot(a.center[0] - b.center[0], a.center[1] - b.center[1])


def yaw_error_deg(a: PlacedBox, b: PlacedBox) -> float:
    """A.3 "Yaw error": ``|ψ_a - ψ_b|`` in degrees, modulo 360, then ``min(e, 360 - e)``."""
    e = math.degrees(abs(yaw_rad(a.rotation) - yaw_rad(b.rotation))) % 360.0
    return min(e, 360.0 - e)


def horizontal_range_m(box: PlacedBox, origin: Vec3) -> float:
    """A.3 "Range": horizontal distance of the world centre from the agent body origin.

    ``origin`` is the translation of the agent's pose sample at the row's time.
    """
    return math.hypot(box.center[0] - origin[0], box.center[1] - origin[1])


def centre_distance_3d_m(a: PlacedBox, b: PlacedBox) -> float:
    """A.9 step 2: the 3D Euclidean distance between two world centres."""
    return math.dist(a.center, b.center)


# --- A.4 "Observed": LiDAR returns inside a box ----------------------------------------


def transform_points(pose: PoseSample, points: Sequence[Vec3]) -> list[Vec3]:
    """Place LiDAR returns in the world (A.4 "Observed", A.1 item 4).

    The sensor sits at the body origin with no rotation, so the mount is the identity
    and a return is placed by its agent's pose alone: ``p_world = R(q) p_body + t``,
    the same arithmetic as :func:`transform_point`.
    """
    r = rotation_matrix(pose.rotation)
    t = pose.translation
    return [
        (
            r[0][0] * p[0] + r[0][1] * p[1] + r[0][2] * p[2] + t[0],
            r[1][0] * p[0] + r[1][1] * p[1] + r[1][2] * p[2] + t[1],
            r[2][0] * p[0] + r[2][1] * p[1] + r[2][2] * p[2] + t[2],
        )
        for p in points
    ]


class BoxInterior:
    """The inside of a placed box, for the point test of A.4 "Observed".

    A world point ``p`` has box-frame coordinates ``R(q_w)ᵀ (p − c_w)``: the box frame
    is ``world_from_box = (q_w, c_w)``, and the transpose is the inverse of the rotation
    of a unit quaternion (A.1 item 6: every definition assumes unit quaternions). The
    point is inside when each coordinate lies within half the box size on its axis,
    boundary included: ``|p_box.x| ≤ length/2``, ``|p_box.y| ≤ width/2`` and
    ``|p_box.z| ≤ height/2``.

    ``reach`` bounds the world distance, along any axis, from the centre to a point that
    can be inside. It only lets :class:`tests.reference_eval.gtset.Sweep` skip points
    that are certainly outside; it never decides that a point is inside. It is
    ``math.inf`` (no skipping) unless the quaternion is a unit one to within ``1e-6``,
    because only then is ``R(q_w)ᵀ`` a rotation that keeps lengths (to ``O(1e-6)``,
    covered by the margin).
    """

    def __init__(self, box: PlacedBox) -> None:
        r = rotation_matrix(box.rotation)
        self.centre = box.center
        # The columns of R(q_w), that is the rows of its transpose.
        self.axis_x = (r[0][0], r[1][0], r[2][0])
        self.axis_y = (r[0][1], r[1][1], r[2][1])
        self.axis_z = (r[0][2], r[1][2], r[2][2])
        length, width, height = box.size_lwh
        self.half = (length / 2, width / 2, height / 2)
        w, x, y, z = box.rotation
        norm_squared = w * w + x * x + y * y + z * z
        reach = math.inf
        if abs(norm_squared - 1.0) <= 3e-6:
            reach = math.sqrt(length * length + width * width + height * height) / 2
            reach = reach * (1.0 + 1e-5) + 1e-9
        self.reach = reach if reach < math.inf else math.inf  # a NaN size: no skipping

    def contains(self, point: Vec3) -> bool:
        """Whether a world point lies inside the box, boundary included."""
        dx = point[0] - self.centre[0]
        dy = point[1] - self.centre[1]
        dz = point[2] - self.centre[2]
        ax, ay, az = self.axis_x
        if not abs(ax * dx + ay * dy + az * dz) <= self.half[0]:
            return False
        bx, by, bz = self.axis_y
        if not abs(bx * dx + by * dy + bz * dz) <= self.half[1]:
            return False
        cx, cy, cz = self.axis_z
        return abs(cx * dx + cy * dy + cz * dz) <= self.half[2]


def box_contains_point(box: PlacedBox, point: Vec3) -> bool:
    """A.4 "Observed": a world point inside a placed box, boundary included."""
    return BoxInterior(box).contains(point)
