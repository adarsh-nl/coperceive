# SPDX-License-Identifier: Apache-2.0
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportPrivateUsage=false
"""Synthetic adapter layouts for cluster dataset detect/index. No dataset bytes."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import cast

import pytest
from coperceive.cluster_datasets import (
    CACHE_DIR,
    DETECT_SCHEMA,
    INDEX_SCHEMA,
    SCENE_STATUS_SCHEMA,
    DatasetError,
    cache_relative_path,
    dataset_status_document,
    detect_cluster_dataset,
    index_cluster_dataset,
    prepare_cluster_dataset,
)

from tests.griffin_release import write_release as write_griffin
from tests.opencood_release import two_cav_scene
from tests.opencood_release import write_release as write_opencood


def _opv2v(root: Path, *, rsu: bool = False) -> Path:
    write_opencood(
        root,
        "test",
        [two_cav_scene("scene_0001", rsu=rsu), two_cav_scene("scene_0002", rsu=False)],
    )
    return root


def _snapshot(root: Path) -> dict[str, tuple[int, int]]:
    return {
        str(path.relative_to(root)): (path.stat().st_size, path.stat().st_mtime_ns)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_detect_finds_an_opv2v_split_and_licence(tmp_path: Path) -> None:
    raw = _opv2v(tmp_path / "opv2v")
    result = detect_cluster_dataset(raw / "test")
    assert result["schema"] == DETECT_SCHEMA
    assert result["status"] == "ok"
    assert result["adapter"] == "opv2v"
    assert result["raw_root"] == str(raw)
    assert result["split"] == "test"
    assert result["params"] == {"split": "test"}
    assert result["scene_count"] == 2
    assert result["licence"]["status"] == "unknown"


def test_detect_prefers_v2xset_when_a_scene_has_an_rsu(tmp_path: Path) -> None:
    raw = _opv2v(tmp_path / "v2xset", rsu=True)
    result = detect_cluster_dataset(raw / "test")
    assert result["adapter"] == "v2xset"
    assert result["split"] == "test"


def test_detect_finds_a_griffin_release(tmp_path: Path) -> None:
    raw = tmp_path / "griffin"
    write_griffin(raw, {"scene-a": [0, 1], "scene-b": [2]})
    result = detect_cluster_dataset(raw)
    assert result["adapter"] == "griffin"
    assert result["raw_root"] == str(raw)
    assert result["split"] is None
    assert result["params"] == {"subset": "25m"}
    assert result["scene_count"] == 2
    assert result["licence"] is None


def test_index_reads_every_scene_and_leaves_the_raw_tree_alone(tmp_path: Path) -> None:
    raw = _opv2v(tmp_path / "opv2v")
    before = _snapshot(raw)
    result = index_cluster_dataset("opv2v", raw / "test")
    assert result["schema"] == INDEX_SCHEMA
    assert result["adapter"] == "opv2v"
    assert result["split"] == "test"
    assert result["scene_count"] == 2
    assert {scene["scene_id"] for scene in result["scenes"]} == {"scene_0001", "scene_0002"}
    for scene in result["scenes"]:
        assert scene["agent_count"] == 2
        assert scene["sample_count"] == 3
        assert scene["duration_s"] == 0.15
        assert scene["sensors"]["sensors"] > 0
    assert result["estimated_cache_bytes"] > 0
    assert "sum of file sizes" in result["estimated_cache_bytes_method"]
    assert result["fingerprint"]["algorithm"] == "sha1-mtime-size-v1"
    assert len(result["fingerprint"]["digest"]) == 40
    assert result["fingerprint"]["entries"] > 0
    assert result["cache"]["relative_path"].startswith(f"{CACHE_DIR}/opv2v/")
    assert result["cache"]["relative_path"].endswith("/test")
    assert _snapshot(raw) == before, "index must not write under the raw tree"


def test_index_griffin_is_metadata_only(tmp_path: Path) -> None:
    raw = tmp_path / "griffin"
    write_griffin(raw, {"scene-a": [0, 1]})
    before = _snapshot(raw)
    result = index_cluster_dataset("griffin", raw)
    assert result["scene_count"] == 1
    assert result["scenes"][0]["sample_count"] == 2
    assert result["scenes"][0]["duration_s"] == 0.2
    assert result["scenes"][0]["agent_count"] == 2
    assert result["cache"]["relative_path"].endswith("/all")
    assert _snapshot(raw) == before


def test_detect_reports_none_for_an_unrelated_directory(tmp_path: Path) -> None:
    unrelated = tmp_path / "notes"
    unrelated.mkdir()
    (unrelated / "readme.txt").write_text("not a dataset\n", encoding="utf-8")
    result = detect_cluster_dataset(unrelated)
    assert result["status"] == "none"
    assert result["adapter"] is None
    assert result["reason"] is None


def test_cli_prints_the_detect_document(tmp_path: Path) -> None:
    raw = _opv2v(tmp_path / "opv2v")
    result = subprocess.run(
        [sys.executable, "-m", "coperceive.cli", "dataset", "detect", "--path", str(raw / "test")],
        check=True,
        capture_output=True,
        text=True,
    )
    document = json.loads(result.stdout)
    assert document["adapter"] == "opv2v"
    assert document["split"] == "test"


def test_cache_relative_path_uses_realpath_hash_and_all_for_griffin(tmp_path: Path) -> None:
    root = tmp_path / "dataset"
    root.mkdir()
    relative = cache_relative_path("griffin", root, None)
    assert relative.startswith(f"{CACHE_DIR}/griffin/")
    assert relative.endswith("/all")
    assert ".." not in relative


def _cache(tmp_path: Path, raw: Path) -> Path:
    return tmp_path / "cache" / "opv2v" / "hash" / "test"


def test_prepare_one_scene_is_atomic_incremental_and_idempotent(tmp_path: Path) -> None:
    raw = _opv2v(tmp_path / "opv2v")
    cache = _cache(tmp_path, raw)
    first = prepare_cluster_dataset("opv2v", raw / "test", cache, scenes=["scene_0001"])
    states = {scene["scene_id"]: scene["state"] for scene in first["scenes"]}
    assert states == {"scene_0001": "ready"}
    assert (cache / "scene_0001" / "manifest.json").is_file()
    assert (cache / "scene_0001" / "COMPLETE.json").is_file()
    assert not (cache / "scene_0002").exists()
    assert (cache / "dataset.json").is_file()

    second = prepare_cluster_dataset("opv2v", raw / "test", cache)
    states = {scene["scene_id"]: scene["state"] for scene in second["scenes"]}
    assert states == {"scene_0001": "ready", "scene_0002": "ready"}
    status = dataset_status_document(cache)
    assert status["schema"] == SCENE_STATUS_SCHEMA
    assert {scene["scene_id"] for scene in status["scenes"]} == {"scene_0001", "scene_0002"}


def test_one_failed_scene_does_not_stop_the_others(tmp_path: Path) -> None:
    raw = tmp_path / "opv2v"
    write_opencood(
        raw,
        "test",
        [two_cav_scene("scene_0001"), two_cav_scene("scene_0002")],
    )
    (raw / "test" / "scene_0002" / "641" / "000069.yaml").write_text(
        "not: [valid", encoding="utf-8"
    )
    cache = _cache(tmp_path, raw)
    result = prepare_cluster_dataset("opv2v", raw / "test", cache)
    states = {scene["scene_id"]: scene for scene in result["scenes"]}
    assert states["scene_0001"]["state"] == "ready"
    assert states["scene_0002"]["state"] == "failed"
    assert states["scene_0002"]["reason"]
    assert not (cache / "scene_0002").exists()


def test_cache_inside_the_raw_root_is_refused(tmp_path: Path) -> None:
    raw = _opv2v(tmp_path / "opv2v")
    with pytest.raises(DatasetError, match="must not be inside"):
        prepare_cluster_dataset("opv2v", raw / "test", raw / "cache")


def test_a_partial_cache_lists_only_ready_scenes_to_the_viewer(tmp_path: Path) -> None:
    raw = _opv2v(tmp_path / "opv2v")
    cache = _cache(tmp_path, raw)
    prepare_cluster_dataset("opv2v", raw / "test", cache, scenes=["scene_0001"])
    (cache / "scene_9999").mkdir()  # a preparing/partial-looking directory
    from coperceive.app.server import create_app
    from fastapi.testclient import TestClient

    client = TestClient(create_app(root=cache, token=None))
    response = client.get("/api/scenes")
    assert response.status_code == 200
    payload: object = response.json()
    assert isinstance(payload, list)
    scenes = {
        cast(dict[str, object], item)["scene_id"] for item in payload if isinstance(item, dict)
    }
    assert scenes == {"scene_0001"}


def test_two_concurrent_prepare_processes_share_a_cache_without_losing_records(
    tmp_path: Path,
) -> None:
    raw = _opv2v(tmp_path / "opv2v")
    cache = _cache(tmp_path, raw)
    command = [
        sys.executable,
        "-m",
        "coperceive.cli",
        "dataset",
        "prepare",
        "--adapter",
        "opv2v",
        "--raw-root",
        str(raw / "test"),
        "--cache",
        str(cache),
        "--all",
        "--workers",
        "2",
    ]
    first = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    second = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    first.communicate(timeout=120)
    second.communicate(timeout=120)
    status = dataset_status_document(cache)
    assert {scene["scene_id"] for scene in status["scenes"]} == {"scene_0001", "scene_0002"}
    assert all(scene["state"] == "ready" for scene in status["scenes"])
    dataset = json.loads((cache / "dataset.json").read_text(encoding="utf-8"))
    assert set(dataset["scenes"]) == {"scene_0001", "scene_0002"}


def _seed_cache(raw: Path, cache: Path, scene_id: str = "scene_0001") -> None:
    import coperceive.cluster_datasets as cd

    cache.mkdir(parents=True, exist_ok=True)
    cd._write_dataset(
        cache,
        {
            "schema": cd.CACHE_SCHEMA,
            "adapter": "opv2v",
            "raw_root": str(raw),
            "split": "test",
            "params": {"split": "test"},
            "cache_root": str(cache),
            "raw_fingerprint": {},
            "created_unix": 0,
            "updated_unix": 0,
            "scenes": {
                scene_id: {
                    "state": "not_prepared",
                    "reason": None,
                    "job_id": None,
                    "prepared_unix": None,
                    "scene_dir": scene_id,
                }
            },
        },
    )


def test_an_unexpected_worker_exception_is_a_per_scene_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import coperceive.cluster_datasets as cd

    raw = _opv2v(tmp_path / "opv2v")
    cache = _cache(tmp_path, raw)
    _seed_cache(raw, cache)

    def explode(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(cd, "ingest", explode)
    scene_id, state, reason, _prepared = cd._claim_and_prepare_scene(
        "opv2v", str(raw), str(cache), "scene_0001", {"split": "test"}, None
    )
    assert scene_id == "scene_0001"
    assert state == "failed"
    assert reason and "RuntimeError: boom" in reason


def test_a_superseded_owner_does_not_overwrite_the_new_claim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import coperceive.cluster_datasets as cd

    raw = _opv2v(tmp_path / "opv2v")
    cache = _cache(tmp_path, raw)
    _seed_cache(raw, cache)

    def takeover(_adapter: object, _raw: object, partial: Path, **_kwargs: object) -> None:
        produced = Path(partial) / "scene_0001"
        produced.mkdir(parents=True)
        (produced / "manifest.json").write_text("{}", encoding="utf-8")
        (produced / "COMPLETE.json").write_text("{}", encoding="utf-8")
        with cd._cache_lock(cache):
            dataset = cd._read_dataset(cache)
            dataset["scenes"]["scene_0001"]["job_id"] = "new-owner"
            cd._write_dataset(cache, dataset)

    monkeypatch.setattr(cd, "ingest", takeover)
    _scene_id, state, reason, _prepared = cd._claim_and_prepare_scene(
        "opv2v", str(raw), str(cache), "scene_0001", {"split": "test"}, None
    )
    assert state == "preparing"
    assert reason and "superseded" in reason
    stored = json.loads((cache / "dataset.json").read_text(encoding="utf-8"))
    assert stored["scenes"]["scene_0001"]["job_id"] == "new-owner"
