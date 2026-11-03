# SPDX-License-Identifier: Apache-2.0
# pyright: reportPrivateUsage=false
"""A prepare job array: one task per scene, all writing one dataset.json. Synthetic data."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from coperceive import cluster_datasets
from coperceive.cluster_datasets import _cache_lock, _slurm_job_id, dataset_status_document

from tests.opencood_release import two_cav_scene
from tests.opencood_release import write_release as write_opencood

SCENES = [f"scene_{index:04d}" for index in range(6)]


def test_array_tasks_prepare_their_scenes_without_losing_each_others_records(
    tmp_path: Path,
) -> None:
    raw = tmp_path / "datasets" / "opv2v"
    write_opencood(raw, "test", [two_cav_scene(scene) for scene in SCENES])
    cache = tmp_path / "results" / "cache" / "test"
    tasks: list[subprocess.Popen[bytes]] = []
    for index, scene in enumerate(SCENES):
        env = {
            **os.environ,
            "SLURM_JOB_ID": str(800_000 + index),
            "SLURM_ARRAY_JOB_ID": "799999",
            "SLURM_ARRAY_TASK_ID": str(index),
        }
        argv = [sys.executable, "-m", "coperceive", "dataset", "prepare", "--adapter", "opv2v",
                "--raw-root", str(raw), "--split", "test", "--cache", str(cache),
                "--workers", "1", "--scenes", scene]  # fmt: skip
        tasks.append(subprocess.Popen(argv, env=env, stdout=subprocess.DEVNULL))
    assert [task.wait(timeout=300) for task in tasks] == [0] * len(SCENES)
    status = dataset_status_document(cache)
    records = {row["scene_id"]: row for row in status["scenes"]}
    assert {scene: records[scene]["state"] for scene in SCENES} == dict.fromkeys(SCENES, "ready")
    assert {records[scene]["job_id"] for scene in SCENES} == {
        f"799999_{index}" for index in range(len(SCENES))
    }
    assert not (cache / ".dataset.json.lockdir").exists()
    assert not list(cache.glob(".dataset.json.*.tmp"))
    json.loads((cache / "dataset.json").read_text())


def test_the_array_task_id_names_the_claim(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SLURM_JOB_ID", "800004")
    monkeypatch.setenv("SLURM_ARRAY_JOB_ID", "799999")
    monkeypatch.setenv("SLURM_ARRAY_TASK_ID", "4")
    assert _slurm_job_id() == "799999_4"
    monkeypatch.delenv("SLURM_ARRAY_TASK_ID")
    assert _slurm_job_id() == "800004"
    monkeypatch.delenv("SLURM_JOB_ID")
    assert _slurm_job_id() is None


def test_a_lock_left_by_a_dead_task_is_taken_over(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock = tmp_path / ".dataset.json.lockdir"
    lock.mkdir()
    old = time.time() - 600
    os.utime(lock, (old, old))
    with _cache_lock(tmp_path):
        assert lock.is_dir()
    assert not lock.exists()
    # A live holder is waited for, up to the limit.
    lock.mkdir()
    monkeypatch.setattr(cluster_datasets, "_LOCK_WAIT_S", 0.2)
    with pytest.raises(cluster_datasets.DatasetError, match="timed out"):
        with _cache_lock(tmp_path):
            pass
