# SPDX-License-Identifier: Apache-2.0
"""Normative values of ``docs/spec/predictions.md`` §Geometry."""

from __future__ import annotations

import pytest
from coperceive.evaluation.geometry import (
    WorldBox,
    bev_iou,
    centre_distance_m,
    clip_convex,
    footprint,
    horizontal_range_m,
    polygon_area,
    yaw_error_deg,
)
from coperceive.se3 import SE3

_UNIT_SQUARE = ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0))


def _box(cx: float, cy: float, length: float, width: float, *, yaw: float = 0.0) -> WorldBox:
    return WorldBox(
        center=(cx, cy, 0.0),
        size_lwh=(length, width, 1.5),
        rotation=SE3.from_rpy_deg(0.0, 0.0, yaw),
    )


def test_footprint_is_counter_clockwise_with_length_along_x() -> None:
    assert footprint(_box(1.0, 2.0, 4.0, 2.0)) == (
        (-1.0, 1.0),
        (3.0, 1.0),
        (3.0, 3.0),
        (-1.0, 3.0),
    )


def test_footprint_turns_with_the_yaw() -> None:
    corners = footprint(_box(0.0, 0.0, 4.0, 2.0, yaw=90.0))
    expected = ((1.0, -2.0), (1.0, 2.0), (-1.0, 2.0), (-1.0, -2.0))
    for got, want in zip(corners, expected, strict=True):
        assert got == pytest.approx(want, abs=1e-12)


def test_identical_boxes_have_iou_one() -> None:
    assert bev_iou(_box(0.0, 0.0, 4.0, 2.0), _box(0.0, 0.0, 4.0, 2.0)) == 1.0


def test_shift_of_two_along_the_length_is_one_third() -> None:
    assert bev_iou(_box(0.0, 0.0, 4.0, 2.0), _box(2.0, 0.0, 4.0, 2.0)) == pytest.approx(1 / 3)


def test_four_by_two_against_two_by_four_on_one_centre_is_one_third() -> None:
    assert bev_iou(_box(0.0, 0.0, 4.0, 2.0), _box(0.0, 0.0, 2.0, 4.0)) == pytest.approx(1 / 3)


def test_yaw_zero_against_yaw_ninety_is_one_third() -> None:
    a = _box(0.0, 0.0, 4.0, 2.0)
    b = _box(0.0, 0.0, 4.0, 2.0, yaw=90.0)
    assert bev_iou(a, b) == pytest.approx(1 / 3)


def test_boxes_touching_along_an_edge_have_iou_zero() -> None:
    assert bev_iou(_box(0.0, 0.0, 4.0, 2.0), _box(4.0, 0.0, 4.0, 2.0)) == 0.0


def test_a_one_by_one_box_inside_a_four_by_two_is_one_eighth() -> None:
    assert bev_iou(_box(0.0, 0.0, 4.0, 2.0), _box(0.5, 0.25, 1.0, 1.0)) == pytest.approx(0.125)


def test_centre_distance_ignores_z() -> None:
    a = WorldBox((0.0, 0.0, 0.0), (4.0, 2.0, 1.5), SE3.identity())
    b = WorldBox((3.0, 4.0, 10.0), (4.0, 2.0, 1.5), SE3.identity())
    assert centre_distance_m(a, b) == 5.0


def test_yaw_error_between_ten_and_minus_ten_is_twenty() -> None:
    a = _box(0.0, 0.0, 4.0, 2.0, yaw=10.0)
    b = _box(0.0, 0.0, 4.0, 2.0, yaw=-10.0)
    assert yaw_error_deg(a, b) == pytest.approx(20.0)


def test_yaw_error_wraps_across_one_hundred_eighty() -> None:
    a = _box(0.0, 0.0, 4.0, 2.0, yaw=170.0)
    b = _box(0.0, 0.0, 4.0, 2.0, yaw=-170.0)
    assert yaw_error_deg(a, b) == pytest.approx(20.0)


def test_horizontal_range_ignores_z() -> None:
    box = WorldBox((3.0, 4.0, 10.0), (4.0, 2.0, 1.5), SE3.identity())
    assert horizontal_range_m(box, (0.0, 0.0, 0.0)) == 5.0
    assert horizontal_range_m(box, (3.0, 0.0, -7.0)) == 4.0


def test_polygon_area_of_the_unit_square_is_one() -> None:
    assert polygon_area(_UNIT_SQUARE) == 1.0


def test_clip_convex_of_unit_squares_offset_by_a_half_has_area_a_quarter() -> None:
    shifted = tuple((x + 0.5, y + 0.5) for x, y in _UNIT_SQUARE)
    assert polygon_area(clip_convex(_UNIT_SQUARE, shifted)) == pytest.approx(0.25)


# World coordinates of a projected map frame: hundreds of kilometres east, thousands north.
_FAR_ORIGINS = (
    (400_000.3, 4_000_000.7),
    (-3_217_645.11, 812_904.53),
    (1.0e7 + 0.37, -1.0e7 - 0.91),
)


def _moved(box: WorldBox, origin: tuple[float, float]) -> WorldBox:
    centre = (box.center[0] + origin[0], box.center[1] + origin[1], box.center[2])
    return WorldBox(centre, box.size_lwh, box.rotation)


@pytest.mark.parametrize("origin", _FAR_ORIGINS)
def test_iou_does_not_depend_on_where_the_world_origin_is(origin: tuple[float, float]) -> None:
    """The same two boxes far from the world origin have the IoU they have at it.

    The offset is added to the centres only, so the pair is the same pair up to the
    rounding of a centre (under 2e-9 m at these magnitudes). Before the footprints
    were taken about a local origin, the car pair was off by 6e-5 at the first
    origin and the 5 cm pair was unusable.
    """
    pairs = (
        (_box(0.0, 0.0, 4.5, 1.9, yaw=17.0), _box(0.6, -0.3, 4.3, 2.0, yaw=24.0)),
        (_box(0.0, 0.0, 0.6, 0.6, yaw=63.0), _box(0.1, 0.05, 0.6, 0.6, yaw=11.0)),
        (_box(0.0, 0.0, 8.0, 2.5, yaw=-171.0), _box(1.5, 0.4, 7.6, 2.6, yaw=178.0)),
        (_box(0.0, 0.0, 0.05, 0.05, yaw=31.0), _box(0.01, 0.0, 0.05, 0.05, yaw=31.0)),
    )
    for a, b in pairs:
        near = bev_iou(a, b)
        assert 0.3 < near < 0.9
        assert bev_iou(_moved(a, origin), _moved(b, origin)) == pytest.approx(near, abs=1e-6)
        assert bev_iou(_moved(b, origin), _moved(a, origin)) == pytest.approx(near, abs=1e-6)
