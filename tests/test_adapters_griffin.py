# SPDX-License-Identifier: Apache-2.0
"""Regression: the Griffin adapter writes the same scenes as the hand-written converter.

"Identical" is defined in ADR 0009: ``manifest.json`` and ``points.bin`` byte
for byte, every Parquet table equal when read back, equal ``read_scene``
results, and the same ``COMPLETE.json`` apart from the provenance keys.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pyarrow.parquet as pq
import pytest
from coperceive.adapters.build import ManifestAdapter
from coperceive.adapters.ingest import ingest
from coperceive.adapters.spec import builtin_manifest_path, load_manifest
from coperceive.griffin.convert import convert_griffin
from coperceive.store import read_scene
from tests.griffin_release import write_release

_TABLES = ("poses", "annotations", "objects", "images", "point_index")
_PROVENANCE = {"adapter", "manifest_sha256", "coperceive_version"}


def assert_identical_scene_dirs(old: Path, new: Path) -> None:
    assert (old / "manifest.json").read_bytes() == (new / "manifest.json").read_bytes()
    assert (old / "points.bin").read_bytes() == (new / "points.bin").read_bytes()
    for name in _TABLES:
        a = pq.read_table(old / f"{name}.parquet")
        b = pq.read_table(new / f"{name}.parquet")
        assert a.schema.equals(b.schema, check_metadata=False), name
        assert a.equals(b, check_metadata=False), name
    assert read_scene(old) == read_scene(new)
    old_marker = json.loads((old / "COMPLETE.json").read_text(encoding="utf-8"))
    new_marker = json.loads((new / "COMPLETE.json").read_text(encoding="utf-8"))
    assert {k: v for k, v in new_marker.items() if k not in _PROVENANCE} == old_marker


def assert_identical_trees(old_root: Path, new_root: Path) -> None:
    old_scenes = sorted(p.name for p in old_root.iterdir() if (p / "manifest.json").is_file())
    new_scenes = sorted(p.name for p in new_root.iterdir() if (p / "manifest.json").is_file())
    assert old_scenes == new_scenes
    for name in old_scenes:
        assert_identical_scene_dirs(old_root / name, new_root / name)


def _run_both(raw: Path, out: Path, *, workers: int = 1) -> None:
    convert_griffin(raw, out / "old", subset="25m", workers=workers)
    adapter = ManifestAdapter(load_manifest(builtin_manifest_path("griffin")))
    ingest(adapter, raw, out / "new", params={"subset": "25m"}, workers=workers)


def test_adapter_matches_converter_on_a_synthetic_release(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    write_release(raw, {"Town03-000": [11185, 11186, 11187], "Town06-001": [40, 41]})
    # Label widths the converter accepts: 12, 11 and 9 fields, on both sides.
    label = raw / "vehicle-side" / "label" / "011186.txt"
    label.write_text(
        "car 1 2 0.5 4 1.8 1.5 0 0 90 7 0.5\n"
        "truck 1 2 0.5 4 1.8 1.5 0 0 90 8\n"
        "car 1 2 0.5 4 1.8 1.5 90 9.0\n",
        encoding="utf-8",
    )
    (raw / "drone-side" / "label" / "011186.txt").write_text(
        "car 1 2 0.5 4 1.8 1.5 90 9\n", encoding="utf-8"
    )
    # A drone lidar calib with no clouds, a vehicle camera with an intrinsic.
    (raw / "drone-side" / "calib" / "lidar_virtual.json").write_text(
        json.dumps({"extrinsic": [[0, -1, 0, 0.1], [1, 0, 0, 0.2], [0, 0, 1, 0.3], [0, 0, 0, 1]]}),
        encoding="utf-8",
    )
    # A richer point file: three points, non-trivial decimals.
    (raw / "vehicle-side" / "lidar" / "lidar_top" / "011185.ply").write_text(
        "ply\nformat ascii 1.0\nelement vertex 3\n"
        "property float32 x\nproperty float32 y\nproperty float32 z\nproperty float32 I\n"
        "end_header\n1.1 -2.2 0.3 0.25\n12.345678 0.1 -0.1 0.999\n0 0 0 0\n",
        encoding="utf-8",
    )
    _run_both(raw, tmp_path / "out")
    assert_identical_trees(tmp_path / "out" / "old", tmp_path / "out" / "new")


def test_adapter_matches_converter_on_a_metadata_only_release(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    write_release(raw, {"Town03-000": [3, 4]}, lidar=False)
    _run_both(raw, tmp_path / "out")
    assert_identical_trees(tmp_path / "out" / "old", tmp_path / "out" / "new")
    assert read_scene(tmp_path / "out" / "new" / "Town03-000").point_clouds == ()


@pytest.mark.slow
def test_adapter_matches_converter_on_the_real_release(tmp_path: Path) -> None:
    raw = os.environ.get("COPERCEIVE_GRIFFIN_RAW")
    if not raw:
        pytest.skip("COPERCEIVE_GRIFFIN_RAW is not set")
    out = Path(os.environ.get("COPERCEIVE_REGRESSION_OUT", str(tmp_path)))
    _run_both(Path(raw), out, workers=4)
    assert_identical_trees(out / "old", out / "new")
