# SPDX-License-Identifier: Apache-2.0
"""Nearest horizontal distance from a point to a polyline layer."""

from __future__ import annotations

import math
from dataclasses import dataclass

from coperceive.hdmap.opendrive import LAYER_LANE_CENTRE, MapPolyline


@dataclass(frozen=True)
class NearHit:
    distance_m: float
    closest_x: float
    closest_y: float
    travel_heading_rad: float
    lane_id: int | None


@dataclass(frozen=True)
class _Segment:
    ax: float
    ay: float
    bx: float
    by: float
    heading: float
    lane_id: int | None

    def hit(self, x: float, y: float) -> NearHit:
        abx = self.bx - self.ax
        aby = self.by - self.ay
        denom = abx * abx + aby * aby
        if denom <= 1e-18:
            return NearHit(
                math.hypot(x - self.ax, y - self.ay), self.ax, self.ay, self.heading, self.lane_id
            )
        raw = ((x - self.ax) * abx + (y - self.ay) * aby) / denom
        clamped = 0.0 if raw < 0.0 else 1.0 if raw > 1.0 else raw
        px = self.ax + clamped * abx
        py = self.ay + clamped * aby
        return NearHit(math.hypot(x - px, y - py), px, py, self.heading, self.lane_id)


class SegmentIndex:
    """Uniform grid over polyline segments. Distance is in the xy plane."""

    def __init__(self, polylines: tuple[MapPolyline, ...], *, cell_m: float = 8.0) -> None:
        if cell_m <= 0.0:
            raise ValueError("cell_m must be positive")
        self.cell_m = cell_m
        self._grid: dict[tuple[int, int], list[_Segment]] = {}
        self._segments: list[_Segment] = []
        for polyline in polylines:
            points = polyline.xyz
            for index in range(len(points) - 1):
                ax, ay = points[index][0], points[index][1]
                bx, by = points[index + 1][0], points[index + 1][1]
                heading = math.atan2(by - ay, bx - ax)
                if (
                    polyline.layer == LAYER_LANE_CENTRE
                    and polyline.lane_id is not None
                    and polyline.lane_id > 0
                ):
                    # Positive OpenDRIVE lanes sit left of the reference and are
                    # driven against the s direction.
                    heading = math.atan2(math.sin(heading + math.pi), math.cos(heading + math.pi))
                segment = _Segment(ax, ay, bx, by, heading, polyline.lane_id)
                self._segments.append(segment)
                min_x = math.floor(min(ax, bx) / cell_m)
                max_x = math.floor(max(ax, bx) / cell_m)
                min_y = math.floor(min(ay, by) / cell_m)
                max_y = math.floor(max(ay, by) / cell_m)
                for ix in range(min_x, max_x + 1):
                    for iy in range(min_y, max_y + 1):
                        self._grid.setdefault((ix, iy), []).append(segment)

    def __len__(self) -> int:
        return len(self._segments)

    def nearest(self, x: float, y: float) -> NearHit | None:
        if not self._segments:
            return None
        cell = self.cell_m
        cx = math.floor(x / cell)
        cy = math.floor(y / cell)
        best: NearHit | None = None
        radius = 0
        limit = 10_000
        while radius < limit:
            for ix in range(cx - radius, cx + radius + 1):
                for iy in range(cy - radius, cy + radius + 1):
                    if radius > 0 and max(abs(ix - cx), abs(iy - cy)) != radius:
                        continue
                    for segment in self._grid.get((ix, iy), ()):
                        hit = segment.hit(x, y)
                        if best is None or hit.distance_m < best.distance_m:
                            best = hit
            if best is not None:
                searched_min_x = (cx - radius) * cell
                searched_max_x = (cx + radius + 1) * cell
                searched_min_y = (cy - radius) * cell
                searched_max_y = (cy + radius + 1) * cell
                outside = min(
                    x - searched_min_x,
                    searched_max_x - x,
                    y - searched_min_y,
                    searched_max_y - y,
                )
                if best.distance_m <= outside + 1e-9:
                    return best
            radius += 1
        return best
