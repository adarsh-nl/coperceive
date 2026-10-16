# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import math

import pytest
from coperceive.adapters.rotation import (
    AxisRemap,
    RotationError,
    euler_to_se3,
    matrix_to_se3,
    quaternion_to_se3,
)
from coperceive.se3 import SE3


def test_zyx_intrinsic_degrees_is_from_rpy_deg() -> None:
    got = euler_to_se3((10.0, -20.0, 35.0), order="zyx", intrinsic=True, degrees=True)
    assert got.almost_equal(SE3.from_rpy_deg(10.0, -20.0, 35.0))


def test_radians_are_converted() -> None:
    got = euler_to_se3((0.0, 0.0, math.pi / 2), order="zyx", intrinsic=True, degrees=False)
    assert got.almost_equal(SE3.from_rpy_deg(0.0, 0.0, 90.0))


def test_extrinsic_xyz_equals_intrinsic_zyx() -> None:
    # Extrinsic xyz (fixed axes, x first) is the same product as intrinsic zyx.
    a = euler_to_se3((10.0, 20.0, 30.0), order="xyz", intrinsic=False, degrees=True)
    b = euler_to_se3((10.0, 20.0, 30.0), order="zyx", intrinsic=True, degrees=True)
    assert a.almost_equal(b)


def test_unknown_order_is_an_error() -> None:
    with pytest.raises(RotationError, match="order"):
        euler_to_se3((0.0, 0.0, 0.0), order="zzz", intrinsic=True, degrees=True)


def test_quaternion_field_order_and_normalisation() -> None:
    yaw90 = SE3.from_rpy_deg(0.0, 0.0, 90.0)
    wxyz = (yaw90.qw, yaw90.qx, yaw90.qy, yaw90.qz)
    assert quaternion_to_se3(wxyz, order="wxyz").almost_equal(yaw90)
    xyzw = (yaw90.qx, yaw90.qy, yaw90.qz, yaw90.qw)
    assert quaternion_to_se3(xyzw, order="xyzw").almost_equal(yaw90)
    scaled = tuple(2.0 * v for v in wxyz)
    assert quaternion_to_se3(scaled, order="wxyz").almost_equal(yaw90)
    with pytest.raises(RotationError, match="zero"):
        quaternion_to_se3((0.0, 0.0, 0.0, 0.0), order="wxyz")


def test_matrix_4x4_row_major_gives_rotation_and_translation() -> None:
    rows = [[0.0, -1.0, 0.0, 1.0], [1.0, 0.0, 0.0, 2.0], [0.0, 0.0, 1.0, 3.0], [0.0, 0.0, 0.0, 1.0]]
    got = matrix_to_se3(rows, shape="4x4")
    assert got.almost_equal(SE3.from_rpy_deg(0.0, 0.0, 90.0).translation(1.0, 2.0, 3.0))
    flat = [v for row in rows for v in row]
    assert matrix_to_se3(flat, shape="4x4").almost_equal(got)
    with pytest.raises(RotationError, match="16"):
        matrix_to_se3(flat[:-1], shape="4x4")


def test_matrix_3x4_and_3x3() -> None:
    rows = [[1.0, 0.0, 0.0, 0.5], [0.0, 1.0, 0.0, 0.0], [0.0, 0.0, 1.0, 0.0]]
    assert matrix_to_se3(rows, shape="3x4").almost_equal(SE3.identity().translation(0.5, 0, 0))
    assert matrix_to_se3([[1, 0, 0], [0, 1, 0], [0, 0, 1]], shape="3x3").almost_equal(
        SE3.identity()
    )


def test_axis_remap_flu_is_identity_and_rdf_to_flu_sends_forward_to_x() -> None:
    assert AxisRemap.parse("FLU").is_identity
    remap = AxisRemap.parse("RDF")  # OpenCV camera: x right, y down, z forward
    # A point 1 m ahead of an RDF camera is (0, 0, 1). In FLU it is (1, 0, 0).
    assert remap.point((0.0, 0.0, 1.0)) == pytest.approx((1.0, 0.0, 0.0))
    # Right in RDF is +x. In FLU, right is -y.
    assert remap.point((1.0, 0.0, 0.0)) == pytest.approx((0.0, -1.0, 0.0))
    with pytest.raises(RotationError, match="axes"):
        AxisRemap.parse("FFU")
    with pytest.raises(RotationError, match="axes"):
        AxisRemap.parse("FLB")


def test_axis_remap_conjugates_a_pose() -> None:
    # A body in RFU (x right, y forward, z up) with yaw 90° about z. After the
    # remap into FLU the same physical rotation is still yaw 90° about z.
    remap = AxisRemap.parse("RFU")
    pose = SE3.from_rpy_deg(0.0, 0.0, 90.0).translation(1.0, 2.0, 3.0)
    world_from_body_flu = remap.world_from_body(pose)
    # World stays the source world; only the body axes changed. The origin
    # does not move.
    assert (world_from_body_flu.tx, world_from_body_flu.ty, world_from_body_flu.tz) == (
        1.0,
        2.0,
        3.0,
    )
    # Body FLU forward (1,0,0) is source body (0,1,0); the source pose takes
    # (0,1,0) to (-1, 0, 0) + t.
    ahead = world_from_body_flu.apply((1.0, 0.0, 0.0))
    assert ahead == pytest.approx((0.0, 2.0, 3.0))
