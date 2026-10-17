# SPDX-License-Identifier: Apache-2.0
"""Camera calibration decoding."""

from __future__ import annotations

from typing import cast


def pinhole_from_matrix(values: object) -> tuple[float, float, float, float]:
    """``(fx, fy, cx, cy)`` from a 3×3 intrinsic matrix, nested or flat."""
    flat: list[float] = []
    _flatten(values, flat)
    if len(flat) != 9:
        raise ValueError(f"intrinsic must be a 3x3 matrix, got {len(flat)} numbers")
    return (flat[0], flat[4], flat[2], flat[5])


def _flatten(values: object, out: list[float]) -> None:
    if isinstance(values, (list, tuple)):
        for item in cast("list[object] | tuple[object, ...]", values):
            _flatten(item, out)
        return
    if isinstance(values, bool) or not isinstance(values, (int, float)):
        raise ValueError("intrinsic entries must be numbers")
    out.append(float(values))
