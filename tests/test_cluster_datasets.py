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


def test_index_lists_the_other_scenes_when_one_scene_is_broken(tmp_path: Path) -> None:
    raw = tmp_path / "opv2v"
    write_opencood(
        raw,
        "test",
        [two_cav_scene(name) for name in ("scene_0001", "scene_0002", "scene_0003")],
    )
    broken = raw / "test" / "scene_0002" / "641" / "000069.yaml"
    broken.write_text('camera0: !!python/object/apply:os.system ["true"]\n', encoding="utf-8")

    result = index_cluster_dataset("opv2v", raw / "test")

    assert [scene["scene_id"] for scene in result["scenes"]] == ["scene_0001", "scene_0003"]
    assert result["scene_count"] == 2
    assert len(result["errors"]) == 1
    error = result["errors"][0]
    assert error.startswith("scene_0002: ")
    assert "000069.yaml" in error and "python/object/apply:os.system" in error
    assert "\n" not in error


def test_index_reads_numpy_tagged_records_and_counts_every_sample(tmp_path: Path) -> None:
    import numpy as np
    import yaml

    raw = _opv2v(tmp_path / "opv2v")
    for path in sorted((raw / "test").glob("*/*/*.yaml")):
        record = yaml.safe_load(path.read_text(encoding="utf-8"))
        if "lidar_pose" in record:
            record["lidar_pose"] = [np.float64(v) for v in record["lidar_pose"]]
            record["ego_speed"] = np.float32(record["ego_speed"])
            path.write_text(yaml.dump(record, Dumper=yaml.Dumper), encoding="utf-8")
    assert "numpy" in next((raw / "test").glob("*/641/*.yaml")).read_text(encoding="utf-8")

    result = index_cluster_dataset("opv2v", raw / "test")

    assert result["errors"] == []
    for scene in result["scenes"]:
        assert scene["agent_count"] == 2
        # 4 cameras + lidar per agent; every one of the 3 samples, not a subset.
        assert scene["sensors"] == {"sensors": 10, "camera_samples": 24, "lidar_samples": 6}


def _two_splits(root: Path) -> Path:
    """train has more scenes than test, as on the real cluster."""
    write_opencood(
        root,
        "train",
        [two_cav_scene(f"train_{index:04d}") for index in range(3)],
    )
    write_opencood(root, "test", [two_cav_scene("test_0001")])
    return root


def test_index_with_a_split_indexes_exactly_that_split(tmp_path: Path) -> None:
    raw = _two_splits(tmp_path / "opv2v")
    result = index_cluster_dataset("opv2v", raw, split="test")
    assert result["split"] == "test"
    assert result["raw_root"] == str(raw)
    assert result["params"] == {"split": "test"}
    assert [scene["scene_id"] for scene in result["scenes"]] == ["test_0001"]
    assert result["cache"]["relative_path"].endswith("/test")
    # The split directory itself, with or without a matching --split, is the same split.
    assert index_cluster_dataset("opv2v", raw / "test")["scene_count"] == 1
    assert index_cluster_dataset("opv2v", raw / "test", split="test")["scene_count"] == 1
    assert index_cluster_dataset("opv2v", raw, split="train")["scene_count"] == 3


def test_index_without_a_split_refuses_a_root_with_several(tmp_path: Path) -> None:
    raw = _two_splits(tmp_path / "opv2v")
    with pytest.raises(DatasetError) as caught:
        index_cluster_dataset("opv2v", raw)
    assert str(caught.value) == f"{raw} holds several splits (train, test); choose one"


def test_index_refuses_a_split_that_does_not_match_the_path(tmp_path: Path) -> None:
    raw = _two_splits(tmp_path / "opv2v")
    with pytest.raises(DatasetError, match="is the test split, not train"):
        index_cluster_dataset("opv2v", raw / "test", split="train")
    with pytest.raises(DatasetError, match="no validate split under .*; it has: train, test"):
        index_cluster_dataset("opv2v", raw, split="validate")
    with pytest.raises(DatasetError, match="not a opv2v split"):
        index_cluster_dataset("opv2v", raw, split="../train")


def test_prepare_with_a_split_prepares_only_that_split(tmp_path: Path) -> None:
    raw = _two_splits(tmp_path / "opv2v")
    cache = _cache(tmp_path, raw)
    result = prepare_cluster_dataset("opv2v", raw, cache, split="test")
    assert {scene["scene_id"]: scene["state"] for scene in result["scenes"]} == {
        "test_0001": "ready"
    }
    with pytest.raises(DatasetError, match="holds several splits"):
        prepare_cluster_dataset("opv2v", raw, tmp_path / "other-cache")


def test_cli_index_split_flag_and_the_several_splits_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from coperceive import cli

    raw = _two_splits(tmp_path / "opv2v")
    cli.main(["dataset", "index", "--adapter", "opv2v", "--raw-root", str(raw), "--split", "test"])
    document = json.loads(capsys.readouterr().out)
    assert document["split"] == "test" and document["scene_count"] == 1
    with pytest.raises(SystemExit) as exited:
        cli.main(["dataset", "index", "--adapter", "opv2v", "--raw-root", str(raw)])
    assert exited.value.code == 1
    captured = capsys.readouterr()
    assert (
        json.loads(captured.out)["error"] == f"{raw} holds several splits (train, test); choose one"
    )
    assert captured.err.splitlines()[0] == (
        f"coperceive dataset index: {raw} holds several splits (train, test); choose one"
    )


def test_detect_counts_scenes_without_listing_frames(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from coperceive.adapters import paths

    raw = _two_splits(tmp_path / "opv2v")

    def no_frames(*_args: object, **_kwargs: object) -> list[str]:
        raise AssertionError("detect listed frames")

    monkeypatch.setattr(paths, "_matches", no_frames)
    result = detect_cluster_dataset(raw / "test")
    assert (result["status"], result["split"], result["scene_count"]) == ("ok", "test", 1)
    assert detect_cluster_dataset(raw / "train")["scene_count"] == 3


def test_index_does_not_run_past_its_timeout_after_a_truncated_walk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import coperceive.cluster_datasets as module

    raw = _two_splits(tmp_path / "opv2v")
    scene = raw / "train" / "train_0000"
    assert module._directory_bytes(scene, max_entries=50_000) > 0
    calls = {"n": 0}

    def clock() -> float:
        # The first reading starts the index; every later one is past its deadline.
        calls["n"] += 1
        return 0.0 if calls["n"] == 1 else 1e9

    monkeypatch.setattr(module.time, "monotonic", clock)
    result = index_cluster_dataset("opv2v", raw, split="train", timeout_s=1.0)
    assert result["fingerprint"]["truncated"] is True
    assert result["scene_count"] == 3
    # The fallback size walk stops at the deadline instead of walking each scene.
    assert result["estimated_cache_bytes"] == 0


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
    assert states == {"scene_0001": "ready", "scene_0002": "not_prepared"}
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


def test_status_marks_ready_scenes_stale_when_the_raw_files_change(tmp_path: Path) -> None:
    """Stale: the raw split's fingerprint changed since the scenes were prepared."""
    raw = _opv2v(tmp_path / "opv2v")
    cache = _cache(tmp_path, raw)
    prepare_cluster_dataset("opv2v", raw / "test", cache)
    before = {scene["scene_id"]: scene for scene in dataset_status_document(cache)["scenes"]}
    assert {scene["state"] for scene in before.values()} == {"ready"}

    # A raw file appears after the prepare: the fingerprint no longer matches.
    (raw / "test" / "scene_0001" / "641" / "000999.yaml").write_text("{}", encoding="utf-8")

    unchanged = dataset_status_document(cache, refresh_stale=False)
    assert {scene["state"] for scene in unchanged["scenes"]} == {"ready"}
    after = {scene["scene_id"]: scene for scene in dataset_status_document(cache)["scenes"]}
    assert {scene["state"] for scene in after.values()} == {"stale"}
    assert all(scene["reason"] == "raw dataset fingerprint changed" for scene in after.values())
    # A stale scene is still on disk: it can be opened or re-prepared.
    assert all((cache / scene_id / "COMPLETE.json").is_file() for scene_id in after)


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


@pytest.mark.parametrize("verbose", [False, True])
def test_a_failed_dataset_command_prints_one_line(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    verbose: bool,
) -> None:
    import yaml
    from coperceive import cli, cluster_datasets

    def explode(*_args: object, **_kwargs: object) -> None:
        yaml.safe_load("a: !!python/object/apply:numpy.core.multiarray.scalar [x]\n")

    monkeypatch.setattr(cluster_datasets, "index_cluster_dataset", explode)
    argv = ["dataset", "index", "--adapter", "opv2v", "--raw-root", str(tmp_path)]
    with pytest.raises(SystemExit) as stopped:
        cli.main(argv + (["--verbose"] if verbose else []))
    assert stopped.value.code == 1
    out, err = capsys.readouterr()
    document = json.loads(out)
    assert document["status"] == "error"
    assert "\n" not in document["error"]
    assert document["error"].startswith("could not read a YAML file: could not determine")
    first = err.splitlines()[0]
    assert first == f"coperceive dataset index: {document['error']}"
    assert ("Traceback" in err) is verbose


def test_capabilities_lists_cluster_datasets(capsys: pytest.CaptureFixture[str]) -> None:
    from coperceive import cli

    cli.main(["capabilities"])
    document = json.loads(capsys.readouterr().out)
    assert document["schema"] == "coperceive-capabilities-1"
    assert "cluster-datasets-2" in document["capabilities"]
    assert "cluster-datasets-3" in document["capabilities"]
    assert "cluster-datasets-5" in document["capabilities"]
    assert document["version"]
