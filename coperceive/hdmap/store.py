# SPDX-License-Identifier: Apache-2.0
"""On-disk HD map: one manifest, one polyline table, one vertex blob.

```text
Town03/
  manifest.json
  polylines.parquet
  vertices.bin
```

``vertices.bin`` is little-endian float32 ``x, y, z`` in the OpenDRIVE frame,
12 bytes per vertex, no header. ``polylines.parquet`` addresses that blob by
vertex index. ``world_from_opendrive`` in the manifest is applied by readers.
It is not baked into the blob.
"""

from __future__ import annotations

import array
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import pyarrow as pa
import pyarrow.parquet as pq

from coperceive.hdmap.frame import FrameMap, frame_from_json
from coperceive.hdmap.opendrive import (
    LAYER_LANE_CENTRE,
    LAYER_REFERENCE,
    LAYER_ROAD_EDGE,
    MapPolyline,
    OpenDriveMap,
)
from coperceive.model import Scene

MAP_FORMAT = "coperceive-hdmap"
MAP_FORMAT_VERSION = 1
VERTEX_BYTES = 12

POLYLINE_SCHEMA = pa.schema(
    [
        pa.field("polyline_id", pa.int64(), nullable=False),
        pa.field("layer", pa.string(), nullable=False),
        pa.field("road_id", pa.string(), nullable=False),
        pa.field("lane_id", pa.int64(), nullable=True),
        pa.field("side", pa.string(), nullable=True),
        pa.field("lane_type", pa.string(), nullable=True),
        pa.field("junction_id", pa.string(), nullable=True),
        pa.field("vertex_offset", pa.int64(), nullable=False),
        pa.field("vertex_count", pa.int64(), nullable=False),
    ]
)

_LAYERS = frozenset({LAYER_REFERENCE, LAYER_LANE_CENTRE, LAYER_ROAD_EDGE})


@dataclass(frozen=True)
class HdMap:
    map_name: str
    directory: Path
    sample_step_m: float
    geo_reference: str
    world_from_opendrive: FrameMap
    source: dict[str, object]
    alignment: dict[str, object]
    polylines: tuple[MapPolyline, ...]

    def world_polylines(self, layers: frozenset[str] | None = None) -> tuple[MapPolyline, ...]:
        """Polylines with vertices mapped into the scene world."""
        chosen = (
            self.polylines
            if layers is None
            else tuple(item for item in self.polylines if item.layer in layers)
        )
        mapped: list[MapPolyline] = []
        frame = self.world_from_opendrive
        for item in chosen:
            mapped.append(
                MapPolyline(
                    layer=item.layer,
                    road_id=item.road_id,
                    lane_id=item.lane_id,
                    side=item.side,
                    lane_type=item.lane_type,
                    junction_id=item.junction_id,
                    xyz=tuple(frame.apply(point) for point in item.xyz),
                )
            )
        return tuple(mapped)

    def lines_blob(self, *, include_reference: bool = False) -> bytes:
        """Viewer blob: world-frame lane centres and road edges.

        Layout, little-endian: ``uint32`` polyline count, then for each
        polyline a ``uint32`` layer code (1 lane centre, 2 road edge), a
        ``uint32`` vertex count, and float32 ``x, y, z``.
        """
        layers = {LAYER_LANE_CENTRE, LAYER_ROAD_EDGE}
        if include_reference:
            layers.add(LAYER_REFERENCE)
        selected = [item for item in self.world_polylines(frozenset(layers)) if len(item.xyz) >= 2]
        parts = [len(selected).to_bytes(4, "little")]
        for item in selected:
            code = (
                1 if item.layer == LAYER_LANE_CENTRE else 2 if item.layer == LAYER_ROAD_EDGE else 3
            )
            values = array.array("f")
            for point in item.xyz:
                values.extend(point)
            if sys.byteorder != "little":
                values.byteswap()
            parts.append(code.to_bytes(4, "little"))
            parts.append(len(item.xyz).to_bytes(4, "little"))
            parts.append(values.tobytes())
        return b"".join(parts)


def map_slug(map_name: str) -> str:
    """``Carla/Maps/Town03`` becomes ``Town03``. The slug is a single path segment."""
    text = map_name.strip()
    if text == "":
        raise ValueError("map_name must be a non-empty string")
    slug = text.split("/")[-1]
    if slug in {"", ".", ".."} or "/" in slug or "\\" in slug or slug.startswith("."):
        raise ValueError(f"map_name {map_name!r} has no usable directory name")
    return slug


def scene_map_name(scene: Scene) -> str | None:
    """The scene's ``map_name`` when every agent that sets it agrees."""
    found: set[str] = set()
    for agent in scene.agents:
        for key, value in agent.platform:
            if key == "map_name" and value != "":
                found.add(value)
    if len(found) == 1:
        return next(iter(found))
    return None


def write_map(
    parsed: OpenDriveMap,
    directory: Path,
    *,
    map_name: str,
    source: dict[str, str],
) -> None:
    """Write a map directory. The world transform starts unmeasured, as identity."""
    directory.mkdir(parents=True, exist_ok=True)
    vertices = array.array("f")
    rows: list[dict[str, object]] = []
    layers: list[str] = []
    cursor = 0
    kept = 0
    for polyline in parsed.polylines:
        if polyline.layer not in _LAYERS or len(polyline.xyz) < 2:
            continue
        rows.append(
            {
                "polyline_id": kept,
                "layer": polyline.layer,
                "road_id": polyline.road_id,
                "lane_id": polyline.lane_id,
                "side": polyline.side,
                "lane_type": polyline.lane_type,
                "junction_id": polyline.junction_id,
                "vertex_offset": cursor,
                "vertex_count": len(polyline.xyz),
            }
        )
        layers.append(polyline.layer)
        for x, y, z in polyline.xyz:
            vertices.extend((x, y, z))
        cursor += len(polyline.xyz)
        kept += 1
    if sys.byteorder != "little":
        vertices.byteswap()
    (directory / "vertices.bin").write_bytes(vertices.tobytes())
    table = (
        pa.Table.from_pylist(rows, schema=POLYLINE_SCHEMA)
        if rows
        else POLYLINE_SCHEMA.empty_table()
    )
    pq.write_table(table, directory / "polylines.parquet")
    frame = FrameMap.identity(measured=False)
    manifest = {
        "map_format": MAP_FORMAT,
        "map_format_version": MAP_FORMAT_VERSION,
        "map_name": map_name,
        "length_unit": "m",
        "sample_step_m": parsed.sample_step_m,
        "opendrive": {
            "name": parsed.name,
            "rev_major": parsed.rev_major,
            "rev_minor": parsed.rev_minor,
            "geo_reference": parsed.geo_reference,
            "north": parsed.north,
            "south": parsed.south,
            "east": parsed.east,
            "west": parsed.west,
        },
        "source": source,
        "world_from_opendrive": frame.as_json(),
        "alignment": {"measured": False},
        "layers": sorted({layer for layer in layers}),
        "polyline_count": kept,
        "vertex_count": cursor,
    }
    (directory / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )


def load_map(directory: Path) -> HdMap:
    manifest_path = directory / "manifest.json"
    payload = _read_json_object(manifest_path)
    if payload.get("map_format") != MAP_FORMAT:
        raise ValueError(f"{manifest_path} map_format is not {MAP_FORMAT}")
    if payload.get("map_format_version") != MAP_FORMAT_VERSION:
        raise ValueError(f"{manifest_path} map_format_version is not {MAP_FORMAT_VERSION}")
    map_name = payload.get("map_name")
    if not isinstance(map_name, str) or map_name == "":
        raise ValueError(f"{manifest_path} map_name is missing")
    step = payload.get("sample_step_m")
    if (
        isinstance(step, bool)
        or not isinstance(step, (int, float))
        or not math.isfinite(float(step))
    ):
        raise ValueError(f"{manifest_path} sample_step_m is missing")
    raw_geo = _dict_or_empty(payload.get("opendrive")).get("geo_reference", "")
    geo = raw_geo if isinstance(raw_geo, str) else ""
    source = _dict_or_empty(payload.get("source"))
    alignment = _dict_or_empty(payload.get("alignment"))
    frame = frame_from_json(payload.get("world_from_opendrive"))
    polylines = _read_polylines(directory)
    return HdMap(
        map_name=map_name,
        directory=directory,
        sample_step_m=float(step),
        geo_reference=geo,
        world_from_opendrive=frame,
        source=source,
        alignment=alignment,
        polylines=polylines,
    )


def find_map(maps_root: Path, map_name: str) -> Path | None:
    """Return the map directory under ``maps_root`` whose manifest names ``map_name``."""
    slug = map_slug(map_name)
    directory = (maps_root / slug).resolve()
    try:
        directory.relative_to(maps_root.resolve())
    except ValueError as exc:
        raise ValueError(f"map path escapes {maps_root}") from exc
    manifest = directory / "manifest.json"
    if not manifest.is_file():
        return None
    payload = _read_json_object(manifest)
    if payload.get("map_name") != map_name:
        raise ValueError(f"{manifest} map_name does not match {map_name}")
    return directory


def write_alignment(directory: Path, frame: FrameMap, alignment: dict[str, object]) -> None:
    """Replace the transform and the alignment record. Vertices stay in OpenDRIVE."""
    manifest_path = directory / "manifest.json"
    payload = _read_json_object(manifest_path)
    payload["world_from_opendrive"] = frame.as_json()
    payload["alignment"] = alignment
    manifest_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _read_polylines(directory: Path) -> tuple[MapPolyline, ...]:
    table = pq.read_table(directory / "polylines.parquet", columns=list(POLYLINE_SCHEMA.names))
    blob = (directory / "vertices.bin").read_bytes()
    values = array.array("f")
    values.frombytes(blob)
    if sys.byteorder != "little":
        values.byteswap()
    if len(values) % 3 != 0:
        raise ValueError(f"{directory / 'vertices.bin'} length is not a multiple of 12")
    polylines: list[MapPolyline] = []
    for row in _rows(table):
        layer = row.get("layer")
        offset = row.get("vertex_offset")
        number = row.get("vertex_count")
        if not isinstance(layer, str) or layer not in _LAYERS:
            raise ValueError(f"polyline layer {layer!r} is not a known layer")
        if type(offset) is not int or type(number) is not int or offset < 0 or number < 2:
            raise ValueError("polyline vertex span is invalid")
        end = offset + number
        if end * 3 > len(values):
            raise ValueError("polyline vertex span runs past vertices.bin")
        xyz = tuple(
            (
                float(values[vertex * 3]),
                float(values[vertex * 3 + 1]),
                float(values[vertex * 3 + 2]),
            )
            for vertex in range(offset, end)
        )
        lane_id = row.get("lane_id")
        side = row.get("side")
        lane_type = row.get("lane_type")
        junction = row.get("junction_id")
        road_id = row.get("road_id")
        if not isinstance(road_id, str):
            raise ValueError("road_id must be a string")
        polylines.append(
            MapPolyline(
                layer=layer,
                road_id=road_id,
                lane_id=lane_id if isinstance(lane_id, int) else None,
                side=side if isinstance(side, str) else None,
                lane_type=lane_type if isinstance(lane_type, str) else None,
                junction_id=junction if isinstance(junction, str) else None,
                xyz=xyz,
            )
        )
    return tuple(polylines)


def _read_json_object(path: Path) -> dict[str, object]:
    loaded: object = json.loads(path.read_text(encoding="utf-8"))
    return _as_dict(loaded, path.name)


def _rows(table: pa.Table) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for item in table.to_pylist():
        converted: dict[str, object] = {}
        for key, value in item.items():
            converted[key] = value
        rows.append(converted)
    return rows


def _as_dict(value: object, what: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError(f"{what} must be an object")
    result: dict[str, object] = {}
    for key, item in cast(dict[object, object], value).items():
        if not isinstance(key, str):
            raise ValueError(f"{what} has a non-string key")
        result[key] = item
    return result


def _dict_or_empty(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, object] = {}
    for key, item in cast(dict[object, object], value).items():
        if isinstance(key, str):
            result[key] = item
    return result
