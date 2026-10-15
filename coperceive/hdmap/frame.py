# SPDX-License-Identifier: Apache-2.0
"""A 3×3 linear map plus a translation.

This is not an SE3. A reflection (determinant −1) is a legal OpenDRIVE-to-world
alignment, and a quaternion cannot store one. Vertices on disk stay in the
OpenDRIVE frame. Callers apply this map when they want scene-world metres.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import cast

Matrix3 = tuple[
    tuple[float, float, float],
    tuple[float, float, float],
    tuple[float, float, float],
]

_IDENTITY: Matrix3 = (
    (1.0, 0.0, 0.0),
    (0.0, 1.0, 0.0),
    (0.0, 0.0, 1.0),
)


def _finite(value: float, what: str) -> float:
    if not math.isfinite(value):
        raise ValueError(f"{what} must be finite")
    return value


@dataclass(frozen=True)
class FrameMap:
    """``p_world = R @ p_opendrive + t``, in metres."""

    r: Matrix3
    t: tuple[float, float, float]
    measured: bool

    def __post_init__(self) -> None:
        rows: list[tuple[float, float, float]] = []
        for index, row in enumerate(self.r):
            if len(row) != 3:
                raise ValueError("world_from_opendrive.r must be 3×3")
            rows.append(
                (
                    _finite(float(row[0]), f"r[{index}][0]"),
                    _finite(float(row[1]), f"r[{index}][1]"),
                    _finite(float(row[2]), f"r[{index}][2]"),
                )
            )
        object.__setattr__(self, "r", (rows[0], rows[1], rows[2]))
        object.__setattr__(
            self,
            "t",
            (
                _finite(float(self.t[0]), "t[0]"),
                _finite(float(self.t[1]), "t[1]"),
                _finite(float(self.t[2]), "t[2]"),
            ),
        )

    @staticmethod
    def identity(*, measured: bool = False) -> FrameMap:
        return FrameMap(_IDENTITY, (0.0, 0.0, 0.0), measured)

    def determinant(self) -> float:
        r = self.r
        return (
            r[0][0] * (r[1][1] * r[2][2] - r[1][2] * r[2][1])
            - r[0][1] * (r[1][0] * r[2][2] - r[1][2] * r[2][0])
            + r[0][2] * (r[1][0] * r[2][1] - r[1][1] * r[2][0])
        )

    def apply(self, point: tuple[float, float, float]) -> tuple[float, float, float]:
        x, y, z = point
        r = self.r
        return (
            r[0][0] * x + r[0][1] * y + r[0][2] * z + self.t[0],
            r[1][0] * x + r[1][1] * y + r[1][2] * z + self.t[1],
            r[2][0] * x + r[2][1] * y + r[2][2] * z + self.t[2],
        )

    def as_json(self) -> dict[str, object]:
        return {
            "r": [list(row) for row in self.r],
            "t": [self.t[0], self.t[1], self.t[2]],
            "measured": self.measured,
            "determinant": self.determinant(),
        }


def frame_from_json(value: object) -> FrameMap:
    payload = _as_dict(value, "world_from_opendrive")
    raw_r = _as_list(payload.get("r"), "world_from_opendrive.r")
    raw_t = _as_list(payload.get("t"), "world_from_opendrive.t")
    measured = payload.get("measured")
    if len(raw_r) != 3:
        raise ValueError("world_from_opendrive.r must be 3×3")
    rows: list[tuple[float, float, float]] = []
    for row in raw_r:
        cells = _as_list(row, "world_from_opendrive.r")
        if len(cells) != 3:
            raise ValueError("world_from_opendrive.r must be 3×3")
        rows.append(
            (
                _number(cells[0], "world_from_opendrive.r"),
                _number(cells[1], "world_from_opendrive.r"),
                _number(cells[2], "world_from_opendrive.r"),
            )
        )
    if len(raw_t) != 3:
        raise ValueError("world_from_opendrive.t must be three numbers")
    if not isinstance(measured, bool):
        raise ValueError("world_from_opendrive.measured must be a bool")
    return FrameMap(
        (rows[0], rows[1], rows[2]),
        (
            _number(raw_t[0], "world_from_opendrive.t"),
            _number(raw_t[1], "world_from_opendrive.t"),
            _number(raw_t[2], "world_from_opendrive.t"),
        ),
        measured,
    )


def _as_dict(value: object, what: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError(f"{what} must be an object")
    result: dict[str, object] = {}
    for key, item in cast(dict[object, object], value).items():
        if not isinstance(key, str):
            raise ValueError(f"{what} has a non-string key")
        result[key] = item
    return result


def _as_list(value: object, what: str) -> list[object]:
    if not isinstance(value, list):
        raise ValueError(f"{what} must be a list")
    return list(cast(list[object], value))


def _number(value: object, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{what} must be numeric")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{what} must be finite")
    return number
