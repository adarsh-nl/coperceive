# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from dataclasses import replace
from multiprocessing import get_context
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from pkg.diagnostics import alignment, shared_objects
from pkg.errors import SchemaVersionError
from pkg.model import SCHEMA_VERSION
from pkg.query import TimeMode
from pkg.store import read_points_at, read_scene, write_scene

from tests.conftest import sample_scene


def test_roundtrip_and_shared_ids(tmp_path: Path) -> None:
    scene = sample_scene()
    promoted = shared_objects(scene.annotations)
    assert [obj.track_id for obj in promoted] == ["7"]
    stored = replace(scene, objects=promoted)
    write_scene(stored, tmp_path / "scene")
    loaded = read_scene(tmp_path / "scene")
    assert loaded.scene_id == "synthetic"
    assert loaded.world_frame == "ENU"
    assert loaded.objects[0].category == "car"
    assert loaded.images[0].path.endswith(".png")
    cloud = read_points_at(
        tmp_path / "scene",
        agent_id="vehicle",
        sensor_id="lidar_top",
        time_us=100_000,
    )
    assert cloud.count == 1
    rows = alignment(loaded, 100_000, tolerance_us=0, mode=TimeMode.EXACT)
    assert rows[0].dst_dt_us == 0
    assert rows[0].src_dt_us == 0


def test_schema_version_and_extra_column(tmp_path: Path) -> None:
    scene = sample_scene()
    write_scene(scene, tmp_path / "scene")
    manifest = (tmp_path / "scene" / "manifest.json").read_text(encoding="utf-8")
    (tmp_path / "scene" / "manifest.json").write_text(
        manifest.replace('"schema_version": 1', '"schema_version": 99'),
        encoding="utf-8",
    )
    with pytest.raises(SchemaVersionError):
        read_scene(tmp_path / "scene")
    (tmp_path / "scene" / "manifest.json").write_text(manifest, encoding="utf-8")
    poses = pq.read_table(tmp_path / "scene" / "poses.parquet")
    evolved = poses.append_column("quality", pa.nulls(poses.num_rows, pa.float32()))
    pq.write_table(evolved, tmp_path / "scene" / "poses.parquet")
    loaded = read_scene(tmp_path / "scene")
    assert loaded.schema_version == SCHEMA_VERSION
    assert loaded.agent("vehicle").poses[1].world_from_body.tx == pytest.approx(1.0)


def test_spawn_workers_read_the_blob(tmp_path: Path) -> None:
    from pkg.store import count_point_bytes

    scene_dir = tmp_path / "scene"
    write_scene(sample_scene(), scene_dir)
    ctx = get_context("spawn")
    with ctx.Pool(2) as pool:
        counts = pool.map(count_point_bytes, [str(scene_dir), str(scene_dir)])
    assert counts == [16, 16]
