# SPDX-License-Identifier: Apache-2.0
"""Bird's-eye-view geometry of boxes in the world frame (spec §Geometry).

A box's footprint is the rectangle in the world XY plane with its ``length``
along the box x axis and its ``width`` along the box y axis, rotated by the
yaw of its world rotation as ``coperceive.validate.yaw_deg`` computes it. Roll
and pitch are ignored. BEV IoU is footprint intersection over footprint union,
the intersection by Sutherland–Hodgman clipping and the areas by the shoelace
formula.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from coperceive.se3 import SE3
from coperceive.validate import yaw_deg

Point2 = tuple[float, float]

# Unit-box corners in the box frame, counter-clockwise, as (x sign, y sign).
_FOOTPRINT_SIGNS = ((-1.0, -1.0), (1.0, -1.0), (1.0, 1.0), (-1.0, 1.0))


@dataclass(frozen=True)
class WorldBox:
    """A 3D box already placed in the scene world frame.

    ``rotation`` is a pure rotation; only its quaternion is read, ``center``
    carries the position. ``size_lwh`` is ``(length, width, height)``.
    """

    center: tuple[float, float, float]
    size_lwh: tuple[float, float, float]
    rotation: SE3


def _yaw_rad(box: WorldBox) -> float:
    return math.radians(yaw_deg(box.rotation))


def footprint(box: WorldBox) -> tuple[Point2, ...]:
    """Four XY corners of the box in the world frame, counter-clockwise.

    The first corner is ``(-length/2, -width/2)`` of the box frame.
    """
    yaw = _yaw_rad(box)
    cos_yaw, sin_yaw = math.cos(yaw), math.sin(yaw)
    cx, cy = box.center[0], box.center[1]
    half_length, half_width = box.size_lwh[0] / 2.0, box.size_lwh[1] / 2.0
    corners: list[Point2] = []
    for sign_x, sign_y in _FOOTPRINT_SIGNS:
        x = sign_x * half_length
        y = sign_y * half_width
        corners.append((cx + x * cos_yaw - y * sin_yaw, cy + x * sin_yaw + y * cos_yaw))
    return tuple(corners)


def _twice_signed_area(points: Sequence[Point2]) -> float:
    count = len(points)
    if count < 3:
        return 0.0
    total = 0.0
    for index in range(count):
        x0, y0 = points[index]
        x1, y1 = points[(index + 1) % count]
        total += x0 * y1 - x1 * y0
    return total


def polygon_area(points: Sequence[Point2]) -> float:
    """Absolute area of a simple polygon by the shoelace formula.

    Fewer than three points, or collinear points, give ``0.0``.
    """
    return abs(_twice_signed_area(points)) / 2.0


def _side(a: Point2, b: Point2, p: Point2) -> float:
    """Cross product of ``b - a`` and ``p - a``: positive left of the directed edge."""
    return (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])


def _crossing(p: Point2, q: Point2, side_p: float, side_q: float) -> Point2:
    """Point of segment ``p -> q`` on the edge line, given the two signed sides."""
    fraction = side_p / (side_p - side_q)
    return (p[0] + fraction * (q[0] - p[0]), p[1] + fraction * (q[1] - p[1]))


def clip_convex(subject: Sequence[Point2], clip: Sequence[Point2]) -> tuple[Point2, ...]:
    """Intersection of two convex polygons by Sutherland–Hodgman.

    ``subject`` is clipped against every edge of ``clip``, which must be convex;
    either winding is accepted for ``clip``, the subject's winding is kept.
    Points on a clip edge count as inside, so two polygons that only touch give
    a degenerate result of zero area, and disjoint polygons give an empty tuple.
    """
    if len(subject) < 3 or len(clip) < 3:
        return ()
    edges = list(clip)
    if _twice_signed_area(edges) < 0.0:
        edges.reverse()
    output: list[Point2] = list(subject)
    for index in range(len(edges)):
        if not output:
            break
        a = edges[index]
        b = edges[(index + 1) % len(edges)]
        inputs = output
        output = []
        previous = inputs[-1]
        side_previous = _side(a, b, previous)
        for current in inputs:
            side_current = _side(a, b, current)
            if side_current >= 0.0:
                if side_previous < 0.0:
                    output.append(_crossing(previous, current, side_previous, side_current))
                output.append(current)
            elif side_previous >= 0.0:
                output.append(_crossing(previous, current, side_previous, side_current))
            previous, side_previous = current, side_current
    return tuple(output)


def bev_iou(a: WorldBox, b: WorldBox) -> float:
    """Footprint intersection over footprint union, in ``[0, 1]``.

    A union of area 0 gives ``0.0``.
    """
    footprint_a = footprint(a)
    footprint_b = footprint(b)
    area_a = polygon_area(footprint_a)
    area_b = polygon_area(footprint_b)
    # The intersection cannot exceed either footprint; the min only removes
    # rounding from the clipped corners.
    intersection = min(polygon_area(clip_convex(footprint_a, footprint_b)), area_a, area_b)
    union = area_a + area_b - intersection
    if union <= 0.0:
        return 0.0
    return intersection / union


def centre_distance_m(a: WorldBox, b: WorldBox) -> float:
    """Horizontal distance between the two centres; ``z`` does not count."""
    return math.hypot(a.center[0] - b.center[0], a.center[1] - b.center[1])


def yaw_error_deg(a: WorldBox, b: WorldBox) -> float:
    """Absolute yaw difference wrapped into ``[0, 180]`` degrees."""
    difference = abs(yaw_deg(a.rotation) - yaw_deg(b.rotation)) % 360.0
    return min(difference, 360.0 - difference)


def horizontal_range_m(box: WorldBox, origin: tuple[float, float, float]) -> float:
    """Horizontal distance from ``origin`` to the box centre; ``z`` does not count."""
    return math.hypot(box.center[0] - origin[0], box.center[1] - origin[1])
