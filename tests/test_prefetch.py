# SPDX-License-Identifier: Apache-2.0
# pyright: reportPrivateUsage=false
"""Raw files read ahead of the parser (coperceive/adapters/prefetch.py). Synthetic data."""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

import pytest
from coperceive.adapters import prefetch
from coperceive.adapters.ingest import ingest
from coperceive.adapters.readers import numpy_yaml
from coperceive.adapters.registry import resolve_adapter
from coperceive.cluster_datasets import TIMING_LOG, prepare_cluster_dataset

from tests.opencood_release import two_cav_scene
from tests.opencood_release import write_release as write_opencood

SCENE = "scene_0001"


def _raw(tmp_path: Path) -> Path:
    raw = tmp_path / "datasets" / "opv2v"
    write_opencood(raw, "test", [two_cav_scene(SCENE)])
    return raw


def _scene_bytes(scene: Path) -> dict[str, bytes]:
    return {
        path.name: path.read_bytes()
        for path in sorted(scene.iterdir())
        if path.is_file() and path.name not in {"COMPLETE.json"}
    }


def test_prefetched_and_sequential_prepare_write_the_same_scene(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = _raw(tmp_path)
    monkeypatch.setenv("COPERCEIVE_IO_THREADS", "0")
    prepare_cluster_dataset("opv2v", raw, tmp_path / "sequential", split="test")
    monkeypatch.setenv("COPERCEIVE_IO_THREADS", "16")
    prepare_cluster_dataset("opv2v", raw, tmp_path / "prefetched", split="test")
    sequential = _scene_bytes(tmp_path / "sequential" / SCENE)
    assert sequential and sequential == _scene_bytes(tmp_path / "prefetched" / SCENE)


def test_each_record_is_parsed_once_per_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = _raw(tmp_path)
    parsed: list[str] = []
    load = numpy_yaml.load_dataset_yaml

    def counting(text: str) -> object:
        parsed.append(text)
        return load(text)

    monkeypatch.setattr(numpy_yaml, "load_dataset_yaml", counting)
    ingest(resolve_adapter("opv2v"), raw, tmp_path / "out", params={"split": "test"}, workers=1)
    yaml_files = [p for p in (raw / "test" / SCENE).rglob("*.yaml") if p.parent.name != SCENE]
    assert yaml_files and len(parsed) == len(yaml_files)


def test_reads_overlap_on_a_slow_filesystem(tmp_path: Path) -> None:
    files: list[Path] = []
    for index in range(32):
        path = tmp_path / f"{index:02d}.bin"
        path.write_bytes(bytes([index]) * 100)
        files.append(path)
    in_flight = 0
    most = 0
    lock = threading.Lock()
    original = Path.read_bytes

    def slow(self: Path) -> bytes:
        nonlocal in_flight, most
        with lock:
            in_flight += 1
            most = max(most, in_flight)
        time.sleep(0.05)
        with lock:
            in_flight -= 1
        return original(self)

    Path.read_bytes = slow  # type: ignore[method-assign]
    try:
        os.environ["COPERCEIVE_IO_THREADS"] = "8"
        started = time.perf_counter()
        with prefetch.collecting() as stats, prefetch.prefetching(tmp_path, files):
            got = [prefetch.read_bytes(path) for path in files]
        elapsed = time.perf_counter() - started
    finally:
        Path.read_bytes = original  # type: ignore[method-assign]
        del os.environ["COPERCEIVE_IO_THREADS"]
    assert got == [bytes([index]) * 100 for index in range(32)]
    assert most > 1 and elapsed < 32 * 0.05 / 2, (most, elapsed)
    assert (stats.files, stats.bytes) == (32, 3200)


def test_a_planned_file_that_leads_out_of_the_root_is_not_prefetched(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    secret = tmp_path / "secret.bin"
    secret.write_bytes(b"no")
    (root / "link.bin").symlink_to(secret)
    (root / "ok.bin").write_bytes(b"yes")
    prefetcher = prefetch.Prefetcher(root, [root / "link.bin", root / "ok.bin"], threads=2)
    try:
        assert prefetcher.take(root / "link.bin") is None  # the caller's confine() refuses it
        assert prefetcher.take(root / "ok.bin") == b"yes"
        assert prefetcher.take(root / "unplanned.bin") is None
    finally:
        prefetcher.close()


def test_each_prepared_scene_logs_its_timing(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    raw = _raw(tmp_path)
    cache = tmp_path / "cache"
    prepare_cluster_dataset("opv2v", raw, cache, split="test")
    (line,) = (cache / TIMING_LOG).read_text().splitlines()
    record = json.loads(line)
    assert record["scene_id"] == SCENE and record["state"] == "ready"
    assert record["files_read"] > 0 and record["mb_read"] >= 0
    for key in ("read_wait_s", "build_s", "write_s", "previews_s", "total_s", "cpu_s"):
        assert record[key] >= 0, key
    assert record["yaml_loader"] in {"libyaml", "pure-python"}
    assert f"coperceive-timing {line}" in capfd.readouterr().out
