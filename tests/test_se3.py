# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import math

import pytest
from coperceive.errors import ExtrapolationError
from coperceive.se3 import SE3, slerp


def test_yaw_90_maps_x_to_y() -> None:
    pose = SE3.from_rpy_deg(0.0, 0.0, 90.0)
    x, y, z = pose.apply((1.0, 0.0, 0.0))
    assert math.isclose(x, 0.0, abs_tol=1e-9)
    assert math.isclose(y, 1.0, abs_tol=1e-9)
    assert math.isclose(z, 0.0, abs_tol=1e-9)


def test_inverse_compose_is_identity() -> None:
    pose = SE3.from_rpy_deg(5.0, -3.0, 40.0).translation(1.0, -2.0, 3.5)
    eye = pose.inverse().compose(pose)
    assert eye.almost_equal(SE3.identity())


def test_slerp_endpoints_and_refuses_extrapolation() -> None:
    a = SE3.identity().translation(0.0, 0.0, 0.0)
    b = SE3.from_rpy_deg(0.0, 0.0, 90.0).translation(2.0, 0.0, 0.0)
    assert slerp(a, b, 0.0).almost_equal(a)
    assert slerp(a, b, 1.0).almost_equal(b)
    mid = slerp(a, b, 0.5)
    assert math.isclose(mid.tx, 1.0, abs_tol=1e-9)
    with pytest.raises(ExtrapolationError):
        slerp(a, b, 1.1)
    with pytest.raises(ExtrapolationError):
        slerp(a, b, -0.01)
