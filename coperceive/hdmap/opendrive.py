# SPDX-License-Identifier: Apache-2.0
"""OpenDRIVE roads as polylines.

The file's plan-view geometry is sampled along the road's s coordinate. Lane
centres and carriageway edges are lateral offsets from that reference, using
the lane-width polynomials and the lane offset. Vertices stay in the
OpenDRIVE frame: x, y from the plan view, z from the elevation profile.

Geometry kinds this reader accepts are ``line``, ``arc``, ``spiral``,
``poly3``, and ``paramPoly3``. Anything else raises. A spiral is integrated
with a fixed 0.05 m step. ``poly3`` uses the geometry length as the local u
range. ``paramPoly3`` uses p in [0, 1] when ``pRange`` is missing or
``normalized``, and p in [0, length] when ``pRange`` is ``arcLength``.
"""

from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

LAYER_REFERENCE = "reference"
LAYER_LANE_CENTRE = "lane_centre"
LAYER_ROAD_EDGE = "road_edge"

_LANE_CENTRE_TYPES = frozenset({"driving", "bidirectional"})
_SURFACE_TYPES = frozenset(
    {
        "driving",
        "bidirectional",
        "shoulder",
        "parking",
        "stop",
        "biking",
        "border",
        "entry",
        "exit",
        "onramp",
        "offramp",
        "connectingramp",
    }
)
_STOP_TYPES = frozenset({"sidewalk", "walking"})
_SPIRAL_STEP_M = 0.05


@dataclass(frozen=True)
class MapPolyline:
    layer: str
    road_id: str
    lane_id: int | None
    side: str | None
    lane_type: str | None
    junction_id: str | None
    xyz: tuple[tuple[float, float, float], ...]


@dataclass(frozen=True)
class OpenDriveMap:
    name: str
    rev_major: str
    rev_minor: str
    geo_reference: str
    north: float | None
    south: float | None
    east: float | None
    west: float | None
    sample_step_m: float
    polylines: tuple[MapPolyline, ...]


def parse_opendrive(path: Path, *, sample_step_m: float = 1.0) -> OpenDriveMap:
    """Read one ``.xodr`` file into reference, lane-centre, and road-edge lines."""
    if sample_step_m <= 0.0 or not math.isfinite(sample_step_m):
        raise ValueError("sample_step_m must be a positive finite number")
    root = ET.parse(path).getroot()
    if _local(root.tag) != "OpenDRIVE":
        raise ValueError(f"{path} root is {_local(root.tag)}, expected OpenDRIVE")
    header = _one(root, "header")
    polylines: list[MapPolyline] = []
    for road_node in _children(root, "road"):
        road = _Road.from_xml(road_node)
        polylines.extend(road.polylines(sample_step_m))
    return OpenDriveMap(
        name=header.attrib.get("name", ""),
        rev_major=header.attrib.get("revMajor", ""),
        rev_minor=header.attrib.get("revMinor", ""),
        geo_reference=_geo_reference(header),
        north=_optional_float(header, "north"),
        south=_optional_float(header, "south"),
        east=_optional_float(header, "east"),
        west=_optional_float(header, "west"),
        sample_step_m=sample_step_m,
        polylines=tuple(polylines),
    )


def _geo_reference(header: ET.Element) -> str:
    nodes = _children(header, "geoReference")
    if not nodes:
        return ""
    return "".join(nodes[0].itertext()).strip()


@dataclass
class _Poly:
    s: float
    a: float
    b: float
    c: float
    d: float

    def at(self, station: float) -> float:
        ds = station - self.s
        return self.a + ds * (self.b + ds * (self.c + ds * self.d))


@dataclass
class _Width:
    s_offset: float
    a: float
    b: float
    c: float
    d: float

    def at(self, along_section: float) -> float:
        ds = along_section - self.s_offset
        return self.a + ds * (self.b + ds * (self.c + ds * self.d))


@dataclass
class _Lane:
    lane_id: int
    lane_type: str
    widths: tuple[_Width, ...]

    def width(self, along_section: float) -> float:
        if not self.widths:
            return 0.0
        chosen = self.widths[0]
        for record in self.widths:
            if record.s_offset <= along_section + 1e-9:
                chosen = record
            else:
                break
        return chosen.at(along_section)


@dataclass
class _Section:
    s: float
    lanes: tuple[_Lane, ...]


@dataclass
class _Geom:
    s: float
    x: float
    y: float
    hdg: float
    length: float
    kind: str
    curvature: float = 0.0
    curv_start: float = 0.0
    curv_end: float = 0.0
    a: float = 0.0
    b: float = 0.0
    c: float = 0.0
    d: float = 0.0
    au: float = 0.0
    bu: float = 0.0
    cu: float = 0.0
    du: float = 0.0
    av: float = 0.0
    bv: float = 0.0
    cv: float = 0.0
    dv: float = 0.0
    p_range: str = "normalized"
    _spiral: list[tuple[float, float, float, float]] | None = None

    def pose(self, station: float) -> tuple[float, float, float]:
        """Return ``(x, y, heading)`` at an absolute station on this geometry."""
        ds = min(max(station - self.s, 0.0), self.length)
        if self.kind == "line":
            return (
                self.x + ds * math.cos(self.hdg),
                self.y + ds * math.sin(self.hdg),
                self.hdg,
            )
        if self.kind == "arc":
            return _arc(self.x, self.y, self.hdg, self.curvature, ds)
        if self.kind == "spiral":
            return self._spiral_pose(ds)
        if self.kind == "poly3":
            return _poly3(self.x, self.y, self.hdg, ds, self.a, self.b, self.c, self.d)
        if self.kind == "paramPoly3":
            return _param_poly3(self, ds)
        raise ValueError(f"unsupported geometry {self.kind}")

    def _spiral_pose(self, ds: float) -> tuple[float, float, float]:
        table = self._spiral_table()
        if ds <= 0.0:
            return (self.x, self.y, self.hdg)
        upper = table[-1]
        for row in table:
            if row[0] >= ds:
                upper = row
                break
        lower = table[0]
        for row in table:
            if row[0] <= ds:
                lower = row
            else:
                break
        span = upper[0] - lower[0]
        if span <= 1e-12:
            local = lower
        else:
            u = (ds - lower[0]) / span
            local = (
                ds,
                lower[1] + u * (upper[1] - lower[1]),
                lower[2] + u * (upper[2] - lower[2]),
                _lerp_angle(lower[3], upper[3], u),
            )
        lx, ly, dh = local[1], local[2], local[3]
        cos_h = math.cos(self.hdg)
        sin_h = math.sin(self.hdg)
        return (
            self.x + lx * cos_h - ly * sin_h,
            self.y + lx * sin_h + ly * cos_h,
            _wrap(self.hdg + dh),
        )

    def _spiral_table(self) -> list[tuple[float, float, float, float]]:
        cached = self._spiral
        if cached is not None:
            return cached
        length = self.length
        if length <= 0.0:
            table = [(0.0, 0.0, 0.0, 0.0)]
            self._spiral = table
            return table
        step = min(_SPIRAL_STEP_M, length)
        count = max(1, int(math.ceil(length / step)))
        ds = length / count
        heading = 0.0
        x = 0.0
        y = 0.0
        table = [(0.0, 0.0, 0.0, 0.0)]
        for index in range(count):
            s0 = index * ds
            s1 = length if index == count - 1 else (index + 1) * ds
            mid = 0.5 * (s0 + s1)
            k = self.curv_start + (self.curv_end - self.curv_start) * (mid / length)
            heading_mid = heading + k * (mid - s0)
            step_len = s1 - s0
            x += step_len * math.cos(heading_mid)
            y += step_len * math.sin(heading_mid)
            heading += k * step_len
            table.append((s1, x, y, heading))
        self._spiral = table
        return table


@dataclass
class _Road:
    road_id: str
    length: float
    junction_id: str | None
    geoms: tuple[_Geom, ...]
    elevations: tuple[_Poly, ...]
    superelevations: tuple[_Poly, ...]
    lane_offsets: tuple[_Poly, ...]
    sections: tuple[_Section, ...]

    @staticmethod
    def from_xml(node: ET.Element) -> _Road:
        road_id = node.attrib.get("id", "")
        length = _required_float(node, "length", f"road {road_id}")
        junction = node.attrib.get("junction", "-1")
        plan = _one(node, "planView")
        geoms = tuple(_geom_from_xml(child, road_id) for child in _children(plan, "geometry"))
        if not geoms:
            raise ValueError(f"road {road_id} has no planView geometry")
        elevations = _profile(node, "elevationProfile", "elevation")
        superelevations = _profile(node, "lateralProfile", "superelevation")
        lanes_node = _one(node, "lanes")
        offsets = tuple(_poly_from(child, "s") for child in _children(lanes_node, "laneOffset"))
        sections = tuple(
            _section_from(child, road_id) for child in _children(lanes_node, "laneSection")
        )
        if not sections:
            raise ValueError(f"road {road_id} has no laneSection")
        return _Road(
            road_id=road_id,
            length=length,
            junction_id=None if junction in {"", "-1"} else junction,
            geoms=geoms,
            elevations=elevations,
            superelevations=superelevations,
            lane_offsets=offsets,
            sections=sections,
        )

    def polylines(self, step: float) -> list[MapPolyline]:
        if self.length <= 0.0:
            return []
        stations = self._stations(step)
        reference: list[tuple[float, float, float]] = []
        lanes: dict[int, list[tuple[float, float, float]]] = {}
        lane_types: dict[int, str] = {}
        edges: dict[str, list[tuple[float, float, float]]] = {"left": [], "right": []}
        open_lanes: set[int] = set()
        open_edges: set[str] = set()
        finished: list[MapPolyline] = []
        for station in stations:
            pose = self._frame(station)
            reference.append((pose[0], pose[1], pose[2]))
            present = self._lane_centres(station)
            for lane_id in list(open_lanes):
                if lane_id not in present:
                    finished.append(
                        self._lane_line(lane_id, lane_types.get(lane_id), lanes.pop(lane_id, []))
                    )
                    open_lanes.discard(lane_id)
            for lane_id, (point, lane_type) in present.items():
                lanes.setdefault(lane_id, []).append(point)
                lane_types[lane_id] = lane_type
                open_lanes.add(lane_id)
            edge_points = self._edges(station)
            for side in ("left", "right"):
                point = edge_points[side]
                if point is None:
                    if side in open_edges:
                        finished.append(self._edge_line(side, edges[side]))
                        edges[side] = []
                        open_edges.discard(side)
                else:
                    edges[side].append(point)
                    open_edges.add(side)
        finished.append(
            MapPolyline(
                LAYER_REFERENCE,
                self.road_id,
                None,
                None,
                None,
                self.junction_id,
                _dedupe(reference),
            )
        )
        for lane_id, points in lanes.items():
            finished.append(self._lane_line(lane_id, lane_types.get(lane_id), points))
        for side, points in edges.items():
            if points:
                finished.append(self._edge_line(side, points))
        return [item for item in finished if len(item.xyz) >= 2]

    def _lane_line(
        self,
        lane_id: int,
        lane_type: str | None,
        points: list[tuple[float, float, float]],
    ) -> MapPolyline:
        return MapPolyline(
            LAYER_LANE_CENTRE,
            self.road_id,
            lane_id,
            None,
            lane_type,
            self.junction_id,
            _dedupe(points),
        )

    def _edge_line(self, side: str, points: list[tuple[float, float, float]]) -> MapPolyline:
        return MapPolyline(
            LAYER_ROAD_EDGE,
            self.road_id,
            None,
            side,
            None,
            self.junction_id,
            _dedupe(points),
        )

    def _stations(self, step: float) -> list[float]:
        marks = {0.0, self.length}
        for geom in self.geoms:
            marks.add(_clamp(geom.s, self.length))
            marks.add(_clamp(geom.s + geom.length, self.length))
        for section in self.sections:
            marks.add(_clamp(section.s, self.length))
        station = 0.0
        while station < self.length:
            marks.add(station)
            station += step
        ordered = sorted(mark for mark in marks if 0.0 <= mark <= self.length + 1e-9)
        deduped: list[float] = []
        for mark in ordered:
            if not deduped or mark - deduped[-1] > 1e-6:
                deduped.append(min(mark, self.length))
        if deduped[-1] != self.length:
            deduped.append(self.length)
        return deduped

    def _frame(self, station: float) -> tuple[float, float, float, float, float]:
        """``x, y, z, heading, superelevation`` on the reference line."""
        geom = self.geoms[-1]
        for candidate in self.geoms:
            if candidate.s <= station + 1e-9:
                geom = candidate
            else:
                break
        x, y, heading = geom.pose(station)
        z = _eval_profile(self.elevations, station)
        roll = _eval_profile(self.superelevations, station)
        return (x, y, z, heading, roll)

    def _offset_point(
        self,
        station: float,
        lateral: float,
    ) -> tuple[float, float, float]:
        x, y, z, heading, roll = self._frame(station)
        lateral_h = lateral * math.cos(roll)
        return (
            x - lateral_h * math.sin(heading),
            y + lateral_h * math.cos(heading),
            z + lateral * math.sin(roll),
        )

    def _lane_centres(self, station: float) -> dict[int, tuple[tuple[float, float, float], str]]:
        section = self._section(station)
        along = station - section.s
        cursor_left = _eval_profile(self.lane_offsets, station)
        cursor_right = cursor_left
        found: dict[int, tuple[tuple[float, float, float], str]] = {}
        left = sorted(
            (lane for lane in section.lanes if lane.lane_id > 0), key=lambda lane: lane.lane_id
        )
        right = sorted(
            (lane for lane in section.lanes if lane.lane_id < 0), key=lambda lane: -lane.lane_id
        )
        for lane in left:
            width = lane.width(along)
            inner = cursor_left
            cursor_left = inner + width
            if lane.lane_type in _LANE_CENTRE_TYPES:
                found[lane.lane_id] = (
                    self._offset_point(station, 0.5 * (inner + cursor_left)),
                    lane.lane_type,
                )
        for lane in right:
            width = lane.width(along)
            inner = cursor_right
            cursor_right = inner - width
            if lane.lane_type in _LANE_CENTRE_TYPES:
                found[lane.lane_id] = (
                    self._offset_point(station, 0.5 * (inner + cursor_right)),
                    lane.lane_type,
                )
        return found

    def _edges(self, station: float) -> dict[str, tuple[float, float, float] | None]:
        section = self._section(station)
        along = station - section.s
        offset = _eval_profile(self.lane_offsets, station)
        return {
            "left": self._one_edge(section, along, offset, positive=True, station=station),
            "right": self._one_edge(section, along, offset, positive=False, station=station),
        }

    def _one_edge(
        self,
        section: _Section,
        along: float,
        offset: float,
        *,
        positive: bool,
        station: float,
    ) -> tuple[float, float, float] | None:
        if positive:
            lanes = sorted(
                (lane for lane in section.lanes if lane.lane_id > 0), key=lambda lane: lane.lane_id
            )
            sign = 1.0
        else:
            lanes = sorted(
                (lane for lane in section.lanes if lane.lane_id < 0), key=lambda lane: -lane.lane_id
            )
            sign = -1.0
        cursor = offset
        edge: float | None = None
        for lane in lanes:
            kind = lane.lane_type
            width = lane.width(along)
            outer = cursor + sign * width
            if kind in _STOP_TYPES:
                break
            if kind in _SURFACE_TYPES:
                edge = outer
            cursor = outer
        if edge is None:
            return None
        return self._offset_point(station, edge)

    def _section(self, station: float) -> _Section:
        chosen = self.sections[0]
        for section in self.sections:
            if section.s <= station + 1e-9:
                chosen = section
            else:
                break
        return chosen


def _arc(x: float, y: float, hdg: float, curvature: float, ds: float) -> tuple[float, float, float]:
    if abs(curvature) < 1e-12:
        return (x + ds * math.cos(hdg), y + ds * math.sin(hdg), hdg)
    heading = hdg + curvature * ds
    return (
        x + (math.sin(heading) - math.sin(hdg)) / curvature,
        y + (math.cos(hdg) - math.cos(heading)) / curvature,
        _wrap(heading),
    )


def _poly3(
    x: float,
    y: float,
    hdg: float,
    u: float,
    a: float,
    b: float,
    c: float,
    d: float,
) -> tuple[float, float, float]:
    v = a + u * (b + u * (c + u * d))
    dv = b + u * (2.0 * c + u * 3.0 * d)
    return _local_to_world(x, y, hdg, u, v, 1.0, dv)


def _param_poly3(geom: _Geom, ds: float) -> tuple[float, float, float]:
    if geom.p_range == "arcLength":
        p = ds
    else:
        p = 0.0 if geom.length == 0.0 else ds / geom.length
    u = geom.au + p * (geom.bu + p * (geom.cu + p * geom.du))
    v = geom.av + p * (geom.bv + p * (geom.cv + p * geom.dv))
    du = geom.bu + p * (2.0 * geom.cu + p * 3.0 * geom.du)
    dv = geom.bv + p * (2.0 * geom.cv + p * 3.0 * geom.dv)
    return _local_to_world(geom.x, geom.y, geom.hdg, u, v, du, dv)


def _local_to_world(
    x: float,
    y: float,
    hdg: float,
    u: float,
    v: float,
    du: float,
    dv: float,
) -> tuple[float, float, float]:
    cos_h = math.cos(hdg)
    sin_h = math.sin(hdg)
    heading = hdg if abs(du) < 1e-15 and abs(dv) < 1e-15 else hdg + math.atan2(dv, du)
    return (x + u * cos_h - v * sin_h, y + u * sin_h + v * cos_h, _wrap(heading))


def _geom_from_xml(node: ET.Element, road_id: str) -> _Geom:
    if _local(node.tag) != "geometry":
        raise ValueError(f"road {road_id} planView has {_local(node.tag)}")
    kinds = [child for child in list(node) if _local(child.tag) != "userdata"]
    if len(kinds) != 1:
        raise ValueError(f"road {road_id} geometry needs one shape, found {len(kinds)}")
    shape = kinds[0]
    kind = _local(shape.tag)
    geom = _Geom(
        s=_required_float(node, "s", f"road {road_id} geometry"),
        x=_required_float(node, "x", f"road {road_id} geometry"),
        y=_required_float(node, "y", f"road {road_id} geometry"),
        hdg=_required_float(node, "hdg", f"road {road_id} geometry"),
        length=_required_float(node, "length", f"road {road_id} geometry"),
        kind=kind,
    )
    if geom.length < 0.0:
        raise ValueError(f"road {road_id} geometry length is negative")
    if kind == "line":
        return geom
    if kind == "arc":
        geom.curvature = _required_float(shape, "curvature", f"road {road_id} arc")
        return geom
    if kind == "spiral":
        geom.curv_start = _required_float(shape, "curvStart", f"road {road_id} spiral")
        geom.curv_end = _required_float(shape, "curvEnd", f"road {road_id} spiral")
        return geom
    if kind == "poly3":
        geom.a = _required_float(shape, "a", f"road {road_id} poly3")
        geom.b = _required_float(shape, "b", f"road {road_id} poly3")
        geom.c = _required_float(shape, "c", f"road {road_id} poly3")
        geom.d = _required_float(shape, "d", f"road {road_id} poly3")
        return geom
    if kind == "paramPoly3":
        for axis, prefix in (("U", "u"), ("V", "v")):
            for coef in ("a", "b", "c", "d"):
                setattr(
                    geom,
                    f"{prefix}{coef}",
                    _required_float(shape, f"{coef}{axis}", f"road {road_id} paramPoly3"),
                )
        p_range = shape.attrib.get("pRange", "normalized")
        if p_range not in {"normalized", "arcLength"}:
            raise ValueError(f"road {road_id} paramPoly3 pRange {p_range} is not supported")
        geom.p_range = p_range
        return geom
    raise ValueError(f"road {road_id} geometry {kind} is not supported")


def _section_from(node: ET.Element, road_id: str) -> _Section:
    lanes: list[_Lane] = []
    for side in ("left", "center", "right"):
        for side_node in _children(node, side):
            for lane_node in _children(side_node, "lane"):
                lanes.append(_lane_from(lane_node, road_id))
    return _Section(s=_required_float(node, "s", f"road {road_id} laneSection"), lanes=tuple(lanes))


def _lane_from(node: ET.Element, road_id: str) -> _Lane:
    lane_id = int(_required_float(node, "id", f"road {road_id} lane"))
    widths = tuple(
        _Width(
            s_offset=_required_float(child, "sOffset", f"road {road_id} width"),
            a=_required_float(child, "a", f"road {road_id} width"),
            b=_required_float(child, "b", f"road {road_id} width"),
            c=_required_float(child, "c", f"road {road_id} width"),
            d=_required_float(child, "d", f"road {road_id} width"),
        )
        for child in _children(node, "width")
    )
    ordered = tuple(sorted(widths, key=lambda item: item.s_offset))
    return _Lane(lane_id=lane_id, lane_type=node.attrib.get("type", "").lower(), widths=ordered)


def _profile(road: ET.Element, group: str, record: str) -> tuple[_Poly, ...]:
    records: list[_Poly] = []
    for container in _children(road, group):
        records.extend(_poly_from(child, "s") for child in _children(container, record))
    return tuple(sorted(records, key=lambda item: item.s))


def _poly_from(node: ET.Element, station_attr: str) -> _Poly:
    return _Poly(
        s=_required_float(node, station_attr, _local(node.tag)),
        a=_required_float(node, "a", _local(node.tag)),
        b=_required_float(node, "b", _local(node.tag)),
        c=_required_float(node, "c", _local(node.tag)),
        d=_required_float(node, "d", _local(node.tag)),
    )


def _eval_profile(records: tuple[_Poly, ...], station: float) -> float:
    if not records:
        return 0.0
    chosen = records[0]
    for record in records:
        if record.s <= station + 1e-9:
            chosen = record
        else:
            break
    return chosen.at(station)


def _dedupe(points: list[tuple[float, float, float]]) -> tuple[tuple[float, float, float], ...]:
    kept: list[tuple[float, float, float]] = []
    for point in points:
        if not kept:
            kept.append(point)
            continue
        prev = kept[-1]
        if (point[0] - prev[0]) ** 2 + (point[1] - prev[1]) ** 2 + (point[2] - prev[2]) ** 2 > 1e-8:
            kept.append(point)
    return tuple(kept)


def _clamp(station: float, length: float) -> float:
    if station < 0.0:
        return 0.0
    if station > length:
        return length
    return station


def _wrap(angle: float) -> float:
    return math.atan2(math.sin(angle), math.cos(angle))


def _lerp_angle(a: float, b: float, u: float) -> float:
    return a + u * math.atan2(math.sin(b - a), math.cos(b - a))


def _local(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[-1]
    return tag


def _children(node: ET.Element, name: str) -> list[ET.Element]:
    return [child for child in list(node) if _local(child.tag) == name]


def _one(node: ET.Element, name: str) -> ET.Element:
    found = _children(node, name)
    if len(found) != 1:
        raise ValueError(f"expected one {name} under {_local(node.tag)}, found {len(found)}")
    return found[0]


def _required_float(node: ET.Element, attr: str, what: str) -> float:
    raw = node.attrib.get(attr)
    if raw is None:
        raise ValueError(f"{what} is missing {attr}")
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{what} {attr} is not a float") from exc
    if not math.isfinite(value):
        raise ValueError(f"{what} {attr} is not finite")
    return value


def _optional_float(node: ET.Element, attr: str) -> float | None:
    raw = node.attrib.get(attr)
    if raw is None or raw == "":
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    if not math.isfinite(value):
        return None
    return value
