# SPDX-License-Identifier: Apache-2.0
"""Hybrid on-disk scene: Parquet tables plus a float32 point blob.

See ``docs/spec/format.md``. One directory is one scene and holds every agent.
Images are paths, not bytes. A reader that names a column list ignores extra
columns. A different ``schema_version`` raises :class:`SchemaVersionError`.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import cast

import pyarrow as pa
import pyarrow.parquet as pq

from coperceive.diagnostics import require_shared_object_table
from coperceive.errors import SchemaVersionError
from coperceive.model import (
    SCHEMA_VERSION,
    Agent,
    AgentType,
    Annotation,
    BoxFrame,
    Clock,
    ImageRef,
    Modality,
    PointCloud,
    Scene,
    Sensor,
    SharedObject,
    StampedPose,
    require_time_us,
)
from coperceive.se3 import SE3

POSE_SCHEMA = pa.schema(
    [
        pa.field("agent_id", pa.string(), nullable=False),
        pa.field("time_us", pa.int64(), nullable=False),
        pa.field("qw", pa.float64(), nullable=False),
        pa.field("qx", pa.float64(), nullable=False),
        pa.field("qy", pa.float64(), nullable=False),
        pa.field("qz", pa.float64(), nullable=False),
        pa.field("tx", pa.float64(), nullable=False),
        pa.field("ty", pa.float64(), nullable=False),
        pa.field("tz", pa.float64(), nullable=False),
    ]
)

ANNOTATION_SCHEMA = pa.schema(
    [
        pa.field("agent_id", pa.string(), nullable=False),
        pa.field("time_us", pa.int64(), nullable=False),
        pa.field("track_id", pa.string(), nullable=True),
        pa.field("category", pa.string(), nullable=False),
        pa.field("cx", pa.float64(), nullable=False),
        pa.field("cy", pa.float64(), nullable=False),
        pa.field("cz", pa.float64(), nullable=False),
        pa.field("length", pa.float64(), nullable=False),
        pa.field("width", pa.float64(), nullable=False),
        pa.field("height", pa.float64(), nullable=False),
        pa.field("qw", pa.float64(), nullable=False),
        pa.field("qx", pa.float64(), nullable=False),
        pa.field("qy", pa.float64(), nullable=False),
        pa.field("qz", pa.float64(), nullable=False),
        pa.field("visibility", pa.float64(), nullable=True),
        pa.field("frame", pa.string(), nullable=False),
    ]
)

OBJECT_SCHEMA = pa.schema(
    [
        pa.field("track_id", pa.string(), nullable=False),
        pa.field("category", pa.string(), nullable=False),
    ]
)

IMAGE_SCHEMA = pa.schema(
    [
        pa.field("agent_id", pa.string(), nullable=False),
        pa.field("sensor_id", pa.string(), nullable=False),
        pa.field("time_us", pa.int64(), nullable=False),
        pa.field("path", pa.string(), nullable=False),
    ]
)

POINT_INDEX_SCHEMA = pa.schema(
    [
        pa.field("agent_id", pa.string(), nullable=False),
        pa.field("sensor_id", pa.string(), nullable=False),
        pa.field("time_us", pa.int64(), nullable=False),
        pa.field("offset", pa.int64(), nullable=False),
        pa.field("nbytes", pa.int64(), nullable=False),
        pa.field("count", pa.int64(), nullable=False),
    ]
)


def _agent_row(agent: Agent) -> dict[str, object]:
    row: dict[str, object] = {
        "agent_id": agent.agent_id,
        "agent_type": agent.agent_type.value,
        "platform": {key: value for key, value in agent.platform},
    }
    if agent.display_name is not None:  # optional; a manifest without it reads as before
        row["display_name"] = agent.display_name
    return row


def write_scene(scene: Scene, path: Path) -> None:
    if scene.schema_version != SCHEMA_VERSION:
        raise SchemaVersionError(
            f"writer supports schema {SCHEMA_VERSION}, scene has {scene.schema_version}"
        )
    require_shared_object_table(scene)
    path.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema_version": scene.schema_version,
        "scene_id": scene.scene_id,
        "world_frame": scene.world_frame,
        "clock": {"unit": scene.clock.unit, "scale_note": scene.clock.scale_note},
        "agents": [_agent_row(agent) for agent in scene.agents],
        "sensors": [_sensor_row(sensor) for sensor in scene.sensors],
    }
    (path / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    _write_table(_pose_rows(scene), POSE_SCHEMA, path / "poses.parquet")
    _write_table(_annotation_rows(scene), ANNOTATION_SCHEMA, path / "annotations.parquet")
    _write_table(_object_rows(scene), OBJECT_SCHEMA, path / "objects.parquet")
    _write_table(_image_rows(scene), IMAGE_SCHEMA, path / "images.parquet")
    _write_points(scene, path)


def read_scene(path: Path) -> Scene:
    manifest = _read_manifest(path / "manifest.json")
    version = manifest["schema_version"]
    if type(version) is not int:
        raise SchemaVersionError("schema_version must be an int")
    if version != SCHEMA_VERSION:
        raise SchemaVersionError(f"file schema {version} != reader schema {SCHEMA_VERSION}")
    poses = _rows(pq.read_table(path / "poses.parquet", columns=list(POSE_SCHEMA.names)))
    annotations = _rows(
        pq.read_table(path / "annotations.parquet", columns=list(ANNOTATION_SCHEMA.names))
    )
    objects = _rows(pq.read_table(path / "objects.parquet", columns=list(OBJECT_SCHEMA.names)))
    images = _rows(pq.read_table(path / "images.parquet", columns=list(IMAGE_SCHEMA.names)))
    agent_rows = _object_list(manifest.get("agents"), "agents")
    by_agent: dict[str, list[StampedPose]] = {}
    agents: list[Agent] = []
    for row in agent_rows:
        agent_id = _req_str(row, "agent_id")
        by_agent[agent_id] = []
        platform_raw = row.get("platform", {})
        platform_map = _as_dict(platform_raw, f"agent {agent_id} platform")
        platform: list[tuple[str, str]] = []
        for key, value in platform_map.items():
            if not isinstance(value, str):
                raise ValueError(f"platform[{key}] must be a string")
            platform.append((key, value))
        display_name = row.get("display_name")
        if display_name is not None and not isinstance(display_name, str):
            raise ValueError(f"agent {agent_id} display_name must be a string")
        agents.append(
            Agent(
                agent_id=agent_id,
                agent_type=AgentType.parse(row.get("agent_type")),
                poses=(),
                platform=tuple(platform),
                display_name=display_name,
            )
        )
    for row in poses:
        agent_id = _req_str(row, "agent_id")
        if agent_id not in by_agent:
            raise ValueError(f"pose names unknown agent {agent_id}")
        by_agent[agent_id].append(
            StampedPose(
                time_us=_req_time(row, "time_us"),
                world_from_body=_se3(row),
            )
        )
    posed = tuple(
        Agent(
            agent_id=agent.agent_id,
            agent_type=agent.agent_type,
            poses=tuple(sorted(by_agent[agent.agent_id], key=lambda pose: pose.time_us)),
            platform=agent.platform,
            display_name=agent.display_name,
        )
        for agent in agents
    )
    clock = _as_dict(manifest.get("clock"), "clock")
    scene = Scene(
        schema_version=version,
        scene_id=_req_str(manifest, "scene_id"),
        world_frame=_req_str(manifest, "world_frame"),
        clock=Clock(unit=_req_str(clock, "unit"), scale_note=_req_str(clock, "scale_note")),
        agents=posed,
        sensors=tuple(
            _sensor_from_row(row) for row in _object_list(manifest.get("sensors"), "sensors")
        ),
        annotations=tuple(_annotation_from_row(row) for row in annotations),
        objects=tuple(
            SharedObject(track_id=_req_str(row, "track_id"), category=_req_str(row, "category"))
            for row in objects
        ),
        point_clouds=_read_points(path),
        images=tuple(
            ImageRef(
                agent_id=_req_str(row, "agent_id"),
                sensor_id=_req_str(row, "sensor_id"),
                time_us=_req_time(row, "time_us"),
                path=_req_str(row, "path"),
            )
            for row in images
        ),
    )
    require_shared_object_table(scene)
    return scene


def read_points_at(path: Path, *, agent_id: str, sensor_id: str, time_us: int) -> PointCloud:
    """Random-access one sweep. Used by worker processes."""
    table = pq.read_table(path / "point_index.parquet", columns=list(POINT_INDEX_SCHEMA.names))
    for row in _rows(table):
        if (
            row["agent_id"] == agent_id
            and row["sensor_id"] == sensor_id
            and _req_time(row, "time_us") == time_us
        ):
            return _cloud_from_index(path, row)
    raise KeyError((agent_id, sensor_id, time_us))


def point_byte_count(path: Path) -> int:
    blob = path / "points.bin"
    if not blob.exists():
        return 0
    return blob.stat().st_size


def count_point_bytes(path: str) -> int:
    """Spawn-safe helper for worker tests and DataLoader processes."""
    return point_byte_count(Path(path))


def _sensor_row(sensor: Sensor) -> dict[str, object]:
    pose = sensor.body_from_sensor
    return {
        "sensor_id": sensor.sensor_id,
        "agent_id": sensor.agent_id,
        "modality": sensor.modality.value,
        "qw": pose.qw,
        "qx": pose.qx,
        "qy": pose.qy,
        "qz": pose.qz,
        "tx": pose.tx,
        "ty": pose.ty,
        "tz": pose.tz,
        "image_width_px": sensor.image_width_px,
        "image_height_px": sensor.image_height_px,
        "fx": sensor.fx,
        "fy": sensor.fy,
        "cx": sensor.cx,
        "cy": sensor.cy,
    }


def _sensor_from_row(row: dict[str, object]) -> Sensor:
    return Sensor(
        sensor_id=_req_str(row, "sensor_id"),
        agent_id=_req_str(row, "agent_id"),
        modality=Modality.parse(row.get("modality")),
        body_from_sensor=_se3(row),
        image_width_px=_opt_int(row, "image_width_px"),
        image_height_px=_opt_int(row, "image_height_px"),
        fx=_opt_float(row, "fx"),
        fy=_opt_float(row, "fy"),
        cx=_opt_float(row, "cx"),
        cy=_opt_float(row, "cy"),
    )


def _pose_rows(scene: Scene) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for agent in scene.agents:
        for pose in agent.poses:
            rows.append(
                {
                    "agent_id": agent.agent_id,
                    "time_us": pose.time_us,
                    **_se3_row(pose.world_from_body),
                }
            )
    return rows


def _annotation_rows(scene: Scene) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for ann in scene.annotations:
        rows.append(
            {
                "agent_id": ann.agent_id,
                "time_us": ann.time_us,
                "track_id": ann.track_id,
                "category": ann.category,
                "cx": ann.center[0],
                "cy": ann.center[1],
                "cz": ann.center[2],
                "length": ann.size_lwh[0],
                "width": ann.size_lwh[1],
                "height": ann.size_lwh[2],
                "qw": ann.box_from_rpy.qw,
                "qx": ann.box_from_rpy.qx,
                "qy": ann.box_from_rpy.qy,
                "qz": ann.box_from_rpy.qz,
                "visibility": ann.visibility,
                "frame": ann.frame.value,
            }
        )
    return rows


def _annotation_from_row(row: dict[str, object]) -> Annotation:
    return Annotation(
        agent_id=_req_str(row, "agent_id"),
        time_us=_req_time(row, "time_us"),
        track_id=_opt_str(row, "track_id"),
        category=_req_str(row, "category"),
        center=(_req_float(row, "cx"), _req_float(row, "cy"), _req_float(row, "cz")),
        size_lwh=(
            _req_float(row, "length"),
            _req_float(row, "width"),
            _req_float(row, "height"),
        ),
        box_from_rpy=SE3(
            _req_float(row, "qw"),
            _req_float(row, "qx"),
            _req_float(row, "qy"),
            _req_float(row, "qz"),
            0.0,
            0.0,
            0.0,
        ),
        visibility=_opt_float(row, "visibility"),
        frame=BoxFrame.parse(row.get("frame")),
    )


def _object_rows(scene: Scene) -> list[dict[str, object]]:
    return [{"track_id": obj.track_id, "category": obj.category} for obj in scene.objects]


def _image_rows(scene: Scene) -> list[dict[str, object]]:
    return [
        {
            "agent_id": image.agent_id,
            "sensor_id": image.sensor_id,
            "time_us": image.time_us,
            "path": image.path,
        }
        for image in scene.images
    ]


def _write_points(scene: Scene, path: Path) -> None:
    rows: list[dict[str, object]] = []
    blob = path / "points.bin"
    with blob.open("wb") as handle:
        offset = 0
        for cloud in scene.point_clouds:
            handle.write(cloud.xyz_i_f32)
            rows.append(
                {
                    "agent_id": cloud.agent_id,
                    "sensor_id": cloud.sensor_id,
                    "time_us": cloud.time_us,
                    "offset": offset,
                    "nbytes": len(cloud.xyz_i_f32),
                    "count": cloud.count,
                }
            )
            offset += len(cloud.xyz_i_f32)
    _write_table(rows, POINT_INDEX_SCHEMA, path / "point_index.parquet")


def _read_points(path: Path) -> tuple[PointCloud, ...]:
    index_path = path / "point_index.parquet"
    if not index_path.exists():
        return ()
    return tuple(
        _cloud_from_index(path, row)
        for row in _rows(pq.read_table(index_path, columns=list(POINT_INDEX_SCHEMA.names)))
    )


def _cloud_from_index(path: Path, row: dict[str, object]) -> PointCloud:
    offset = _req_offset(row, "offset")
    nbytes = _req_offset(row, "nbytes")
    count = _req_offset(row, "count")
    if nbytes != count * 16:
        raise ValueError("point index nbytes must equal count * 16")
    with (path / "points.bin").open("rb") as handle:
        handle.seek(offset)
        blob = handle.read(nbytes)
    return PointCloud(
        agent_id=_req_str(row, "agent_id"),
        sensor_id=_req_str(row, "sensor_id"),
        time_us=_req_time(row, "time_us"),
        count=count,
        xyz_i_f32=blob,
    )


def _write_table(rows: list[dict[str, object]], schema: pa.Schema, path: Path) -> None:
    if rows:
        table = pa.Table.from_pylist(rows, schema=schema)
    else:
        table = schema.empty_table()
    pq.write_table(table, path)


def _se3(row: dict[str, object]) -> SE3:
    return SE3(
        _req_float(row, "qw"),
        _req_float(row, "qx"),
        _req_float(row, "qy"),
        _req_float(row, "qz"),
        _req_float(row, "tx"),
        _req_float(row, "ty"),
        _req_float(row, "tz"),
    )


def _se3_row(pose: SE3) -> dict[str, float]:
    return {
        "qw": pose.qw,
        "qx": pose.qx,
        "qy": pose.qy,
        "qz": pose.qz,
        "tx": pose.tx,
        "ty": pose.ty,
        "tz": pose.tz,
    }


def _read_manifest(path: Path) -> dict[str, object]:
    loaded: object = json.loads(path.read_text(encoding="utf-8"))
    return _as_dict(loaded, "manifest.json")


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


def _object_list(value: object, what: str) -> list[dict[str, object]]:
    if not isinstance(value, list):
        raise ValueError(f"{what} must be a list")
    return [_as_dict(item, what) for item in cast(list[object], value)]


def _req_str(row: dict[str, object], key: str) -> str:
    value = row.get(key)
    if not isinstance(value, str) or value == "":
        raise ValueError(f"{key} must be a non-empty string")
    return value


def _opt_str(row: dict[str, object], key: str) -> str | None:
    value = row.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or value == "":
        raise ValueError(f"{key} must be a non-empty string or null")
    return value


def _req_time(row: dict[str, object], key: str) -> int:
    return require_time_us(row.get(key), key)


def _req_offset(row: dict[str, object], key: str) -> int:
    value = row.get(key)
    if type(value) is not int or value < 0:
        raise ValueError(f"{key} must be a non-negative int")
    return value


def _req_float(row: dict[str, object], key: str) -> float:
    value = row.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{key} must be a float")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{key} must be finite")
    return number


def _opt_float(row: dict[str, object], key: str) -> float | None:
    if key not in row or row[key] is None:
        return None
    return _req_float(row, key)


def _opt_int(row: dict[str, object], key: str) -> int | None:
    if key not in row or row[key] is None:
        return None
    value = row[key]
    if type(value) is not int or value <= 0:
        raise ValueError(f"{key} must be a positive int or null")
    return value
