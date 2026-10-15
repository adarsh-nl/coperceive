# SPDX-License-Identifier: Apache-2.0
"""Right-handed rigid transforms.

``p_dst = R @ p_src + t``. The rotation is a unit quaternion ``(qw, qx, qy, qz)``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from coperceive.errors import ExtrapolationError


def _dot(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2] + a[3] * b[3]


def _mul(
    a: tuple[float, float, float, float], b: tuple[float, float, float, float]
) -> tuple[float, float, float, float]:
    aw, ax, ay, az = a
    bw, bx, by, bz = b
    return (
        aw * bw - ax * bx - ay * by - az * bz,
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
    )


def _rotate(
    q: tuple[float, float, float, float], p: tuple[float, float, float]
) -> tuple[float, float, float]:
    # p' = q * (0, p) * q_conj
    conj = (q[0], -q[1], -q[2], -q[3])
    _w, x, y, z = _mul(_mul(q, (0.0, p[0], p[1], p[2])), conj)
    return (x, y, z)


def _from_matrix(r: tuple[tuple[float, float, float], ...]) -> tuple[float, float, float, float]:
    trace = r[0][0] + r[1][1] + r[2][2]
    if trace > 0.0:
        s = math.sqrt(trace + 1.0) * 2.0
        return (
            0.25 * s,
            (r[2][1] - r[1][2]) / s,
            (r[0][2] - r[2][0]) / s,
            (r[1][0] - r[0][1]) / s,
        )
    if r[0][0] > r[1][1] and r[0][0] > r[2][2]:
        s = math.sqrt(1.0 + r[0][0] - r[1][1] - r[2][2]) * 2.0
        return (
            (r[2][1] - r[1][2]) / s,
            0.25 * s,
            (r[0][1] + r[1][0]) / s,
            (r[0][2] + r[2][0]) / s,
        )
    if r[1][1] > r[2][2]:
        s = math.sqrt(1.0 + r[1][1] - r[0][0] - r[2][2]) * 2.0
        return (
            (r[0][2] - r[2][0]) / s,
            (r[0][1] + r[1][0]) / s,
            0.25 * s,
            (r[1][2] + r[2][1]) / s,
        )
    s = math.sqrt(1.0 + r[2][2] - r[0][0] - r[1][1]) * 2.0
    return (
        (r[1][0] - r[0][1]) / s,
        (r[0][2] + r[2][0]) / s,
        (r[1][2] + r[2][1]) / s,
        0.25 * s,
    )


Matrix3 = tuple[
    tuple[float, float, float],
    tuple[float, float, float],
    tuple[float, float, float],
]
Quad = tuple[float, float, float, float]


def _components(pose: SE3) -> Quad:
    return (pose.qw, pose.qx, pose.qy, pose.qz)


def _as_quad(values: tuple[float, ...]) -> Quad:
    return (values[0], values[1], values[2], values[3])


@dataclass(frozen=True)
class SE3:
    """Rigid transform taking a point in the source frame into the destination frame."""

    qw: float
    qx: float
    qy: float
    qz: float
    tx: float
    ty: float
    tz: float

    def __post_init__(self) -> None:
        for name in ("qw", "qx", "qy", "qz", "tx", "ty", "tz"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"SE3.{name} must be a finite float")
            if not math.isfinite(float(value)):
                raise ValueError(f"SE3.{name} must be a finite float")

    @staticmethod
    def identity() -> SE3:
        return SE3(1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)

    @staticmethod
    def from_matrix(
        rotation: Matrix3,
        translation: tuple[float, float, float] = (0.0, 0.0, 0.0),
    ) -> SE3:
        qw, qx, qy, qz = _from_matrix(rotation)
        return SE3(qw, qx, qy, qz, translation[0], translation[1], translation[2])

    @staticmethod
    def from_rpy_deg(roll_deg: float, pitch_deg: float, yaw_deg: float) -> SE3:
        """Build ``R = Rz(yaw) @ Ry(pitch) @ Rx(roll)`` with angles in degrees.

        This is the composition that put Griffin vehicle and drone boxes on the
        same world points (maximum residual about 1e-13 m on the scenes checked).
        """
        roll = math.radians(roll_deg)
        pitch = math.radians(pitch_deg)
        yaw = math.radians(yaw_deg)
        cr, sr = math.cos(roll), math.sin(roll)
        cp, sp = math.cos(pitch), math.sin(pitch)
        cy, sy = math.cos(yaw), math.sin(yaw)
        # Rows of Rz @ Ry @ Rx.
        r00 = cy * cp
        r01 = cy * sp * sr - sy * cr
        r02 = cy * sp * cr + sy * sr
        r10 = sy * cp
        r11 = sy * sp * sr + cy * cr
        r12 = sy * sp * cr - cy * sr
        r20 = -sp
        r21 = cp * sr
        r22 = cp * cr
        qw, qx, qy, qz = _from_matrix(((r00, r01, r02), (r10, r11, r12), (r20, r21, r22)))
        return SE3(qw, qx, qy, qz, 0.0, 0.0, 0.0)

    def translation(self, tx: float, ty: float, tz: float) -> SE3:
        return SE3(self.qw, self.qx, self.qy, self.qz, tx, ty, tz)

    def _q(self) -> tuple[float, float, float, float]:
        return (self.qw, self.qx, self.qy, self.qz)

    def apply(self, point: tuple[float, float, float]) -> tuple[float, float, float]:
        x, y, z = _rotate(self._q(), point)
        return (x + self.tx, y + self.ty, z + self.tz)

    def inverse(self) -> SE3:
        q = (self.qw, -self.qx, -self.qy, -self.qz)
        rx, ry, rz = _rotate(q, (self.tx, self.ty, self.tz))
        return SE3(q[0], q[1], q[2], q[3], -rx, -ry, -rz)

    def compose(self, other: SE3) -> SE3:
        """Return ``self ∘ other``. Apply ``other`` first, then ``self``."""
        q = _mul(self._q(), other._q())
        tx, ty, tz = self.apply((other.tx, other.ty, other.tz))
        return SE3(q[0], q[1], q[2], q[3], tx, ty, tz)

    def almost_equal(self, other: SE3, *, abs_tol: float = 1e-9) -> bool:
        q_same = math.isclose(abs(_dot(self._q(), other._q())), 1.0, abs_tol=abs_tol)
        t_same = (
            math.isclose(self.tx, other.tx, abs_tol=abs_tol)
            and math.isclose(self.ty, other.ty, abs_tol=abs_tol)
            and math.isclose(self.tz, other.tz, abs_tol=abs_tol)
        )
        return q_same and t_same


def slerp(a: SE3, b: SE3, u: float) -> SE3:
    """Interpolate. ``u`` must lie in ``[0, 1]``. Values outside that interval raise."""
    if u < 0.0 or u > 1.0:
        raise ExtrapolationError(f"interpolation fraction {u} is outside [0, 1]")
    if u == 0.0:
        return a
    if u == 1.0:
        return b
    qa = _components(a)
    qb = list(_components(b))
    dot = _dot(qa, (qb[0], qb[1], qb[2], qb[3]))
    if dot < 0.0:
        qb = [-c for c in qb]
        dot = -dot
    if dot > 0.9995:
        mixed = tuple((1.0 - u) * qa[i] + u * qb[i] for i in range(4))
        norm = math.sqrt(_dot(_as_quad(mixed), _as_quad(mixed)))
        qn = _as_quad(tuple(c / norm for c in mixed))
    else:
        theta = math.acos(max(-1.0, min(1.0, dot)))
        s = math.sin(theta)
        w0 = math.sin((1.0 - u) * theta) / s
        w1 = math.sin(u * theta) / s
        qn = _as_quad(tuple(w0 * qa[i] + w1 * qb[i] for i in range(4)))
    tx = (1.0 - u) * a.tx + u * b.tx
    ty = (1.0 - u) * a.ty + u * b.ty
    tz = (1.0 - u) * a.tz + u * b.tz
    return SE3(qn[0], qn[1], qn[2], qn[3], tx, ty, tz)
