# SPDX-License-Identifier: Apache-2.0
"""Hybrid on-disk scene: Parquet tables plus a float32 point blob.

Layout under ``path``:

* ``manifest.json`` — schema version, world frame, clock, agent and sensor rows
* ``poses.parquet``, ``annotations.parquet``, ``objects.parquet``, ``images.parquet``
* ``point_index.parquet`` and ``points.bin`` — little-endian float32 ``x,y,z,intensity``

Images are paths, not bytes. Extra Parquet columns are ignored on read.
A different ``schema_version`` raises :class:`SchemaVersionError`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from coperceive.errors import SchemaVersionError
from coperceive.model import (
    SCHEMA_VERSION,
    Agent,
    Annotation,
    Clock,
    ImageRef,
    PointCloud,
    Scene,
    Sensor,
    SharedObject,
    StampedPose,
)
from coperceive.se3 import SE3


def write_scene(scene: Scene, path: Path) -> None:
    if scene.schema_version != SCHEMA_VERSION:
        raise SchemaVersionError(
            f"writer supports schema {SCHEMA_VERSION}, scene has {scene.schema_version}"
        )
    path.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema_version": scene.schema_version,
        "scene_id": scene.scene_id,
        "world_frame": scene.world_frame,
        "clock": {"unit": scene.clock.unit, "scale_note": scene.clock.scale_note},
        "agents": [{"agent_id": a.agent_id, "agent_type": a.agent_type} for a in scene.agents],
        "sensors": [_sensor_row(s) for s in scene.sensors],
    }
    (path / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    pq.write_table(_pose_table(scene), path / "poses.parquet")
    pq.write_table(_annotation_table(scene), path / "annotations.parquet")
    pq.write_table(_object_table(scene), path / "objects.parquet")
    pq.write_table(_image_table(scene), path / "images.parquet")
    _write_points(scene, path)


def read_scene(path: Path) -> Scene:
    manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
    version = int(manifest["schema_version"])
    if version != SCHEMA_VERSION:
        raise SchemaVersionError(f"file schema {version} != reader schema {SCHEMA_VERSION}")
    poses = pq.read_table(path / "poses.parquet").to_pylist()
    annotations = pq.read_table(
        path / "annotations.parquet",
        columns=[
            "agent_id",
            "time_us",
            "track_id",
            "category",
            "cx",
            "cy",
            "cz",
            "length",
            "width",
            "height",
            "qw",
            "qx",
            "qy",
            "qz",
            "visibility",
        ],
    ).to_pylist()
    objects = pq.read_table(path / "objects.parquet").to_pylist()
    images = pq.read_table(path / "images.parquet").to_pylist()
    by_agent: dict[str, list[StampedPose]] = {a["agent_id"]: [] for a in manifest["agents"]}
    for row in poses:
        by_agent[row["agent_id"]].append(
            StampedPose(
                time_us=int(row["time_us"]),
                world_from_body=SE3(
                    float(row["qw"]),
                    float(row["qx"]),
                    float(row["qy"]),
                    float(row["qz"]),
                    float(row["tx"]),
                    float(row["ty"]),
                    float(row["tz"]),
                ),
            )
        )
    agents = tuple(
        Agent(
            agent_id=a["agent_id"],
            agent_type=a["agent_type"],
            poses=tuple(sorted(by_agent[a["agent_id"]], key=lambda p: p.time_us)),
        )
        for a in manifest["agents"]
    )
    sensors = tuple(_sensor_from_row(row) for row in manifest["sensors"])
    return Scene(
        schema_version=version,
        scene_id=str(manifest["scene_id"]),
        world_frame=str(manifest["world_frame"]),
        clock=Clock(
            unit=str(manifest["clock"]["unit"]),
            scale_note=str(manifest["clock"]["scale_note"]),
        ),
        agents=agents,
        sensors=sensors,
        annotations=tuple(_annotation_from_row(row) for row in annotations),
        objects=tuple(
            SharedObject(track_id=str(row["track_id"]), category=str(row["category"]))
            for row in objects
        ),
        point_clouds=_read_points(path),
        images=tuple(
            ImageRef(
                agent_id=str(row["agent_id"]),
                sensor_id=str(row["sensor_id"]),
                time_us=int(row["time_us"]),
                path=str(row["path"]),
            )
            for row in images
        ),
    )


def read_points_at(path: Path, *, agent_id: str, sensor_id: str, time_us: int) -> PointCloud:
    """Random-access one sweep. Used by worker processes."""
    table = pq.read_table(path / "point_index.parquet")
    for row in table.to_pylist():
        if (
            row["agent_id"] == agent_id
            and row["sensor_id"] == sensor_id
            and int(row["time_us"]) == time_us
        ):
            with (path / "points.bin").open("rb") as handle:
                handle.seek(int(row["offset"]))
                blob = handle.read(int(row["nbytes"]))
            return PointCloud(
                agent_id=agent_id,
                sensor_id=sensor_id,
                time_us=time_us,
                count=int(row["count"]),
                xyz_i_f32=blob,
            )
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
        "modality": sensor.modality,
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
    def optional_int(key: str) -> int | None:
        value = row.get(key)
        return None if value is None else int(value)  # type: ignore[arg-type]

    def optional_float(key: str) -> float | None:
        value = row.get(key)
        return None if value is None else float(value)  # type: ignore[arg-type]

    return Sensor(
        sensor_id=str(row["sensor_id"]),
        agent_id=str(row["agent_id"]),
        modality=str(row["modality"]),
        body_from_sensor=SE3(
            float(row["qw"]),  # type: ignore[arg-type]
            float(row["qx"]),  # type: ignore[arg-type]
            float(row["qy"]),  # type: ignore[arg-type]
            float(row["qz"]),  # type: ignore[arg-type]
            float(row["tx"]),  # type: ignore[arg-type]
            float(row["ty"]),  # type: ignore[arg-type]
            float(row["tz"]),  # type: ignore[arg-type]
        ),
        image_width_px=optional_int("image_width_px"),
        image_height_px=optional_int("image_height_px"),
        fx=optional_float("fx"),
        fy=optional_float("fy"),
        cx=optional_float("cx"),
        cy=optional_float("cy"),
    )


def _pose_table(scene: Scene) -> pa.Table:
    rows: list[dict[str, object]] = []
    for agent in scene.agents:
        for pose in agent.poses:
            tf = pose.world_from_body
            rows.append(
                {
                    "agent_id": agent.agent_id,
                    "time_us": pose.time_us,
                    "qw": tf.qw,
                    "qx": tf.qx,
                    "qy": tf.qy,
                    "qz": tf.qz,
                    "tx": tf.tx,
                    "ty": tf.ty,
                    "tz": tf.tz,
                }
            )
    return pa.Table.from_pylist(rows)


def _annotation_table(scene: Scene) -> pa.Table:
    rows: list[dict[str, object]] = []
    for ann in scene.annotations:
        rows.append(
            {
                "agent_id": ann.agent_id,
                "time_us": ann.time_us,
                "track_id": ann.track_id,
                "category": ann.category,
                "cx": ann.center_body[0],
                "cy": ann.center_body[1],
                "cz": ann.center_body[2],
                "length": ann.size_lwh[0],
                "width": ann.size_lwh[1],
                "height": ann.size_lwh[2],
                "qw": ann.box_from_rpy.qw,
                "qx": ann.box_from_rpy.qx,
                "qy": ann.box_from_rpy.qy,
                "qz": ann.box_from_rpy.qz,
                "visibility": ann.visibility,
            }
        )
    if not rows:
        return pa.table(
            {
                "agent_id": pa.array([], type=pa.string()),
                "time_us": pa.array([], type=pa.int64()),
                "track_id": pa.array([], type=pa.string()),
                "category": pa.array([], type=pa.string()),
                "cx": pa.array([], type=pa.float64()),
                "cy": pa.array([], type=pa.float64()),
                "cz": pa.array([], type=pa.float64()),
                "length": pa.array([], type=pa.float64()),
                "width": pa.array([], type=pa.float64()),
                "height": pa.array([], type=pa.float64()),
                "qw": pa.array([], type=pa.float64()),
                "qx": pa.array([], type=pa.float64()),
                "qy": pa.array([], type=pa.float64()),
                "qz": pa.array([], type=pa.float64()),
                "visibility": pa.array([], type=pa.float64()),
            }
        )
    return pa.Table.from_pylist(rows)


def _annotation_from_row(row: dict[str, object]) -> Annotation:
    visibility = row["visibility"]
    return Annotation(
        agent_id=str(row["agent_id"]),
        time_us=int(row["time_us"]),  # type: ignore[arg-type]
        track_id=str(row["track_id"]),
        category=str(row["category"]),
        center_body=(float(row["cx"]), float(row["cy"]), float(row["cz"])),  # type: ignore[arg-type]
        size_lwh=(float(row["length"]), float(row["width"]), float(row["height"])),  # type: ignore[arg-type]
        box_from_rpy=SE3(
            float(row["qw"]),  # type: ignore[arg-type]
            float(row["qx"]),  # type: ignore[arg-type]
            float(row["qy"]),  # type: ignore[arg-type]
            float(row["qz"]),  # type: ignore[arg-type]
            0.0,
            0.0,
            0.0,
        ),
        visibility=None if visibility is None else float(visibility),  # type: ignore[arg-type]
    )


def _object_table(scene: Scene) -> pa.Table:
    rows = [{"track_id": obj.track_id, "category": obj.category} for obj in scene.objects]
    if not rows:
        return pa.table(
            {
                "track_id": pa.array([], type=pa.string()),
                "category": pa.array([], type=pa.string()),
            }
        )
    return pa.Table.from_pylist(rows)


def _image_table(scene: Scene) -> pa.Table:
    rows = [
        {
            "agent_id": image.agent_id,
            "sensor_id": image.sensor_id,
            "time_us": image.time_us,
            "path": image.path,
        }
        for image in scene.images
    ]
    if not rows:
        return pa.table(
            {
                "agent_id": pa.array([], type=pa.string()),
                "sensor_id": pa.array([], type=pa.string()),
                "time_us": pa.array([], type=pa.int64()),
                "path": pa.array([], type=pa.string()),
            }
        )
    return pa.Table.from_pylist(rows)


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
    if not rows:
        pq.write_table(
            pa.table(
                {
                    "agent_id": pa.array([], type=pa.string()),
                    "sensor_id": pa.array([], type=pa.string()),
                    "time_us": pa.array([], type=pa.int64()),
                    "offset": pa.array([], type=pa.int64()),
                    "nbytes": pa.array([], type=pa.int64()),
                    "count": pa.array([], type=pa.int64()),
                }
            ),
            path / "point_index.parquet",
        )
        return
    pq.write_table(pa.Table.from_pylist(rows), path / "point_index.parquet")


def _read_points(path: Path) -> tuple[PointCloud, ...]:
    index_path = path / "point_index.parquet"
    if not index_path.exists():
        return ()
    clouds: list[PointCloud] = []
    with (path / "points.bin").open("rb") as handle:
        for row in pq.read_table(index_path).to_pylist():
            handle.seek(int(row["offset"]))
            blob = handle.read(int(row["nbytes"]))
            clouds.append(
                PointCloud(
                    agent_id=str(row["agent_id"]),
                    sensor_id=str(row["sensor_id"]),
                    time_us=int(row["time_us"]),
                    count=int(row["count"]),
                    xyz_i_f32=blob,
                )
            )
    return tuple(clouds)
