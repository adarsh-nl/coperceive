# SPDX-License-Identifier: Apache-2.0
"""Rotation encodings and axis remaps for adapter manifests.

Euler angles are always given as ``(about_x, about_y, about_z)``. ``order``
says how they compose: intrinsic ``abc`` is ``R = Ra @ Rb @ Rc`` (rotate about
``a``, then the new ``b``, then the new ``c``); extrinsic ``abc`` is
``R = Rc @ Rb @ Ra`` (fixed axes, ``a`` first). Intrinsic ``zyx`` is
:meth:`SE3.from_rpy_deg`.

An :class:`AxisRemap` takes a source axis convention (three letters from
F/B/L/R/U/D naming what source ``+x``, ``+y``, ``+z`` point at) onto the
library convention, which is ``FLU``: ``+x`` forward, ``+y`` left, ``+z`` up.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import cast

from coperceive.errors import CoperceiveError
from coperceive.se3 import SE3

Vec3 = tuple[float, float, float]
Rows3 = tuple[Vec3, Vec3, Vec3]

_UNIT: dict[str, Vec3] = {
    "F": (1.0, 0.0, 0.0),
    "B": (-1.0, 0.0, 0.0),
    "L": (0.0, 1.0, 0.0),
    "R": (0.0, -1.0, 0.0),
    "U": (0.0, 0.0, 1.0),
    "D": (0.0, 0.0, -1.0),
}


class RotationError(CoperceiveError):
    """A rotation encoding or an axis convention is malformed."""


def euler_to_se3(
    angles: tuple[float, float, float],
    *,
    order: str,
    intrinsic: bool,
    degrees: bool,
) -> SE3:
    if len(order) != 3 or set(order) != {"x", "y", "z"}:
        raise RotationError(f"euler order must be a permutation of xyz, got {order!r}")
    about = {"x": angles[0], "y": angles[1], "z": angles[2]}
    if not degrees:
        about = {axis: math.degrees(value) for axis, value in about.items()}
    sequence = order if intrinsic else order[::-1]
    result = SE3.identity()
    for axis in sequence:
        result = result.compose(_single(axis, about[axis]))
    return result


def _single(axis: str, degrees: float) -> SE3:
    if axis == "x":
        return SE3.from_rpy_deg(degrees, 0.0, 0.0)
    if axis == "y":
        return SE3.from_rpy_deg(0.0, degrees, 0.0)
    return SE3.from_rpy_deg(0.0, 0.0, degrees)


def quaternion_to_se3(values: Sequence[float], *, order: str) -> SE3:
    if len(values) != 4:
        raise RotationError(f"quaternion needs 4 values, got {len(values)}")
    if order == "wxyz":
        w, x, y, z = (float(v) for v in values)
    elif order == "xyzw":
        x, y, z, w = (float(v) for v in values)
    else:
        raise RotationError(f"quaternion order must be wxyz or xyzw, got {order!r}")
    norm = math.sqrt(w * w + x * x + y * y + z * z)
    if not math.isfinite(norm) or norm == 0.0:
        raise RotationError("quaternion has zero norm")
    return SE3(w / norm, x / norm, y / norm, z / norm, 0.0, 0.0, 0.0)


def matrix_to_se3(values: object, *, shape: str, column_major: bool = False) -> SE3:
    """Decode a nested or flat ``3x3``, ``3x4`` or ``4x4`` matrix."""
    try:
        rows, cols = (int(part) for part in shape.split("x"))
    except ValueError:
        raise RotationError(f"matrix shape must be 3x3, 3x4 or 4x4, got {shape!r}") from None
    if (rows, cols) not in {(3, 3), (3, 4), (4, 4)}:
        raise RotationError(f"matrix shape must be 3x3, 3x4 or 4x4, got {shape!r}")
    flat = _flatten(values)
    if len(flat) != rows * cols:
        raise RotationError(f"matrix {shape} needs {rows * cols} numbers, got {len(flat)}")
    grid = [[flat[r * cols + c] for c in range(cols)] for r in range(rows)]
    if column_major:
        grid = [[flat[c * rows + r] for c in range(cols)] for r in range(rows)]
    rotation: Rows3 = (
        (grid[0][0], grid[0][1], grid[0][2]),
        (grid[1][0], grid[1][1], grid[1][2]),
        (grid[2][0], grid[2][1], grid[2][2]),
    )
    translation: Vec3 = (0.0, 0.0, 0.0)
    if cols == 4:
        translation = (grid[0][3], grid[1][3], grid[2][3])
    if rows == 4 and [round(v, 12) for v in grid[3]] != [0.0, 0.0, 0.0, 1.0]:
        raise RotationError("matrix 4x4 last row must be 0 0 0 1")
    return SE3.from_matrix(rotation, translation)


def _flatten(values: object) -> list[float]:
    if isinstance(values, (list, tuple)):
        out: list[float] = []
        for item in cast("list[object] | tuple[object, ...]", values):
            out.extend(_flatten(item))
        return out
    if isinstance(values, bool) or not isinstance(values, (int, float)):
        raise RotationError("matrix entries must be numbers")
    return [float(values)]


@dataclass(frozen=True)
class AxisRemap:
    """``p_flu = M @ p_source``. ``M`` is a signed permutation with det +1."""

    rows: Rows3

    @staticmethod
    def parse(letters: str) -> AxisRemap:
        if len(letters) != 3 or any(ch not in _UNIT for ch in letters):
            raise RotationError(f"axes must be three letters from FBLRUD, got {letters!r}")
        columns = [_UNIT[ch] for ch in letters]
        used = {tuple(abs(v) for v in col) for col in columns}
        if len(used) != 3:
            raise RotationError(f"axes {letters!r} repeat an axis")
        rows: Rows3 = (
            (columns[0][0], columns[1][0], columns[2][0]),
            (columns[0][1], columns[1][1], columns[2][1]),
            (columns[0][2], columns[1][2], columns[2][2]),
        )
        if _det(rows) < 0:
            raise RotationError(f"axes {letters!r} are left-handed")
        return AxisRemap(rows)

    @staticmethod
    def parse_camera(letters: str) -> AxisRemap:
        """Source camera axes onto the library camera convention, ``RDF``."""
        return AxisRemap.parse("RDF").inverse().then(AxisRemap.parse(letters))

    def inverse(self) -> AxisRemap:
        r = self.rows
        return AxisRemap(
            ((r[0][0], r[1][0], r[2][0]), (r[0][1], r[1][1], r[2][1]), (r[0][2], r[1][2], r[2][2]))
        )

    def then(self, first: AxisRemap) -> AxisRemap:
        """``self @ first``: apply ``first``, then ``self``."""
        a, b = self.rows, first.rows
        rows = tuple(
            tuple(sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)) for i in range(3)
        )
        return AxisRemap(rows)  # type: ignore[arg-type]

    @property
    def is_identity(self) -> bool:
        return self.rows == ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))

    def point(self, p: Vec3) -> Vec3:
        return (
            self.rows[0][0] * p[0] + self.rows[0][1] * p[1] + self.rows[0][2] * p[2],
            self.rows[1][0] * p[0] + self.rows[1][1] * p[1] + self.rows[1][2] * p[2],
            self.rows[2][0] * p[0] + self.rows[2][1] * p[1] + self.rows[2][2] * p[2],
        )

    def as_se3(self) -> SE3:
        return SE3.from_matrix(self.rows)

    def inverse_se3(self) -> SE3:
        return self.inverse().as_se3()

    def world_from_body(self, world_from_source_body: SE3) -> SE3:
        """The same pose with the body axes expressed in FLU. The origin stays."""
        return world_from_source_body.compose(self.inverse_se3())

    def rotation(self, source_rotation: SE3) -> SE3:
        """Conjugate a rotation expressed in source axes: ``M R Mᵀ``."""
        return self.as_se3().compose(source_rotation).compose(self.inverse_se3())


def remap_transform(parent: AxisRemap, child: AxisRemap, parent_from_child: SE3) -> SE3:
    """Re-express ``parent ← child`` when both frames change axes."""
    return parent.as_se3().compose(parent_from_child).compose(child.inverse_se3())


def _det(r: Rows3) -> float:
    return (
        r[0][0] * (r[1][1] * r[2][2] - r[1][2] * r[2][1])
        - r[0][1] * (r[1][0] * r[2][2] - r[1][2] * r[2][0])
        + r[0][2] * (r[1][0] * r[2][1] - r[1][1] * r[2][0])
    )
