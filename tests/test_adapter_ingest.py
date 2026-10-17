# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest
from coperceive.adapters.build import ManifestAdapter
from coperceive.adapters.ingest import IngestError, format_report, ingest
from coperceive.adapters.spec import builtin_manifest_path, load_manifest
from coperceive.store import read_scene

from tests.griffin_release import write_release


def _griffin() -> ManifestAdapter:
    return ManifestAdapter(load_manifest(builtin_manifest_path("griffin")))


def test_ingest_writes_scenes_and_a_complete_marker_with_provenance(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    write_release(raw, {"Town03-000": [11185, 11186]})
    out = tmp_path / "out"
    report = ingest(_griffin(), raw, out, params={"subset": "25m"}, workers=1)
    assert report.written == ("Town03-000",)
    assert report.variant_counts == ((12, 4),)
    scene = read_scene(out / "Town03-000")
    assert dict(scene.agent("vehicle").platform)["subset"] == "25m"
    marker = json.loads((out / "Town03-000" / "COMPLETE.json").read_text(encoding="utf-8"))
    assert marker["scene_id"] == "Town03-000"
    assert marker["schema_version"] == 1
    assert marker["frames"] == [11185, 11186]
    assert marker["label_field_counts"] == {"12": 4}
    assert marker["adapter"] == "griffin"
    digest = hashlib.sha256(builtin_manifest_path("griffin").read_bytes()).hexdigest()
    assert marker["manifest_sha256"] == digest
    assert isinstance(marker["coperceive_version"], str) and marker["coperceive_version"]
    assert "frames 2: 1 scenes" in format_report(report)


def test_ingest_skips_a_complete_scene_and_resumes_a_finished_partial(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    write_release(raw, {"Town03-000": [1, 2]})
    out = tmp_path / "out"
    ingest(_griffin(), raw, out, params={"subset": "25m"}, workers=1)
    stamp = (out / "Town03-000" / "manifest.json").stat().st_mtime_ns
    second = ingest(_griffin(), raw, out, params={"subset": "25m"}, workers=1)
    assert second.skipped == ("Town03-000",)
    assert (out / "Town03-000" / "manifest.json").stat().st_mtime_ns == stamp
    partial = out / ".partial" / "Town03-000"
    partial.parent.mkdir(exist_ok=True)
    (out / "Town03-000").rename(partial)
    third = ingest(_griffin(), raw, out, params={"subset": "25m"}, workers=1)
    assert third.resumed == ("Town03-000",)
    assert not partial.exists()


def test_ingest_skips_an_old_marker_without_provenance_keys(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    write_release(raw, {"Town03-000": [1]})
    out = tmp_path / "out"
    ingest(_griffin(), raw, out, params={"subset": "25m"}, workers=1)
    marker = out / "Town03-000" / "COMPLETE.json"
    payload = json.loads(marker.read_text(encoding="utf-8"))
    for key in ("adapter", "manifest_sha256", "coperceive_version"):
        del payload[key]
    marker.write_text(json.dumps(payload), encoding="utf-8")
    report = ingest(_griffin(), raw, out, params={"subset": "25m"}, workers=1)
    assert report.skipped == ("Town03-000",)


def test_ingest_keeps_finished_scenes_when_one_fails(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    write_release(
        raw, {"Town03-000": [1], "Town03-001": [2]}, drop_pose=("drone-side", "Town03-001", 2)
    )
    out = tmp_path / "out"
    with pytest.raises(IngestError) as caught:
        ingest(_griffin(), raw, out, params={"subset": "25m"}, workers=2)
    assert (out / "Town03-000" / "manifest.json").is_file()
    assert not (out / "Town03-001").exists()
    assert not (out / ".partial" / "Town03-001").exists()
    assert any(error.startswith("Town03-001:") for error in caught.value.report.errors)


def test_ingest_scene_filter_and_rebuilds_junk_partial(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    write_release(raw, {"Town03-000": [1], "Town03-001": [2]})
    out = tmp_path / "out"
    junk = out / ".partial" / "Town03-001"
    junk.mkdir(parents=True)
    (junk / "junk.txt").write_text("x", encoding="utf-8")
    report = ingest(
        _griffin(), raw, out, params={"subset": "25m"}, workers=1, scene_ids=["Town03-001"]
    )
    assert report.written == ("Town03-001",)
    assert not (out / "Town03-000").exists()
    assert not junk.exists()
    shutil.rmtree(out)
    with pytest.raises(IngestError, match="Town03-9"):
        ingest(_griffin(), raw, out, params={"subset": "25m"}, workers=1, scene_ids=["Town03-9"])
