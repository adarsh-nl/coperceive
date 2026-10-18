# SPDX-License-Identifier: Apache-2.0
"""The per-scene cache under the run directory (ADR 0011 decision 7)."""

from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest
from coperceive.evaluation.config import DEFAULT_CONFIG
from coperceive.evaluation.evaluate import evaluate_run
from coperceive.evaluation.predictions import PredictionError, check_scene_id, read_boxes
from coperceive.model import Scene
from coperceive.models import batch
from coperceive.models.batch import RunOptions, run_batch
from coperceive.models.cache import CacheKey, SceneCache, prune
from coperceive.models.inputs import InputFile
from coperceive.models.protocol import BoxRow, Result
from coperceive.models.spec import load_manifest
from coperceive.store import write_scene

from tests.cooperative import cooperative_scene
from tests.fake_model_plugin import MANIFEST as FAKE

CREATED = "2026-10-18T12:00:00Z"
MD5 = "0123456789abcdef0123456789abcdef"
ENV = {"python": "3.13", "checkpoint_md5": MD5}


def _env() -> dict[str, str]:
    return {"PATH": os.environ.get("PATH", "/usr/bin"), "HOME": os.environ.get("HOME", "/tmp")}


@pytest.fixture
def roots(tmp_path: Path) -> tuple[Path, Path, Scene]:
    scene = replace(cooperative_scene(), scene_id="coop")
    scenes = tmp_path / "scenes"
    write_scene(scene, scenes / "coop")
    return scenes, tmp_path / "predictions", scene


def _result(frame_id: int, agent_id: str = "vehicle") -> Result:
    box = BoxRow(
        agent_id,
        "single",
        "car",
        0.9,
        None,
        (5.0, 0.0, 0.0),
        (4.5, 1.9, 1.6),
        (1, 0, 0, 0),
        "world",
    )
    return Result(id=frame_id, boxes=(box,), artifacts=())


def test_path_is_plugin_model_md5_scene(tmp_path: Path) -> None:
    cache = SceneCache(tmp_path / "r1", plugin="fake", model="default")
    key = CacheKey("fake", "default", MD5, "coop")
    assert cache.path_for(key) == tmp_path / "r1" / "cache" / "fake" / "default" / MD5 / "coop"
    with pytest.raises(PredictionError):
        cache.path_for(CacheKey("fake", "default", MD5, "../x"))


def test_put_get_round_trip(roots: tuple[Path, Path, Scene], tmp_path: Path) -> None:
    _, _, scene = roots
    cache = SceneCache(tmp_path / "r1", plugin="fake", model="default")
    key = cache.key_for(scene, ENV)
    inputs = (InputFile("points.bin", tmp_path / "points.bin", 16, 1),)
    results = ((0, _result(1)), (100_000, _result(2)))
    directory = cache.put(
        key,
        manifest_sha256="a" * 64,
        options={"extra": False},
        environment=ENV,
        inputs=inputs,
        results=results,
        artifacts=None,
    )
    assert (directory / "result.json").is_file()
    stored = json.loads((directory / "result.json").read_text(encoding="utf-8"))
    assert stored["inputs"] == [{"path": "points.bin", "size": 16, "mtime_ns": 1}]
    assert stored["environment"] == ENV
    assert (
        cache.get(key, scene=scene, manifest_sha256="a" * 64, options={"extra": False}) == results
    )


def test_other_options_or_manifest_miss(roots: tuple[Path, Path, Scene], tmp_path: Path) -> None:
    _, _, scene = roots
    cache = SceneCache(tmp_path / "r1", plugin="fake", model="default")
    key = cache.key_for(scene, ENV)
    cache.put(
        key,
        manifest_sha256="a" * 64,
        options={"extra": False},
        environment=ENV,
        inputs=(),
        results=((0, _result(1)),),
        artifacts=None,
    )
    assert cache.get(key, scene=scene, manifest_sha256="a" * 64, options={"extra": True}) is None
    assert cache.get(key, scene=scene, manifest_sha256="b" * 64, options={"extra": False}) is None
    assert (cache.path_for(key) / "result.json").is_file()  # a stale entry stays until overwritten


def test_corrupt_entry_is_dropped(roots: tuple[Path, Path, Scene], tmp_path: Path) -> None:
    _, _, scene = roots
    cache = SceneCache(tmp_path / "r1", plugin="fake", model="default")
    key = cache.key_for(scene, ENV)
    directory = cache.path_for(key)
    directory.mkdir(parents=True)
    (directory / "result.json").write_text("{", encoding="utf-8")
    assert cache.get(key, scene=scene, manifest_sha256="a" * 64, options={}) is None
    assert not directory.exists()
    # Well-formed JSON whose rows fail validation is dropped too.
    directory.mkdir(parents=True)
    bad = _result(1, agent_id="ghost")
    cache.put(
        key,
        manifest_sha256="a" * 64,
        options={},
        environment=ENV,
        inputs=(),
        results=((0, bad),),
        artifacts=None,
    )
    assert cache.get(key, scene=scene, manifest_sha256="a" * 64, options={}) is None
    assert not directory.exists()


def _run(roots: tuple[Path, Path, Scene], **kwargs: object) -> batch.BatchReport:
    scenes, out, _ = roots
    options = RunOptions(**kwargs)  # type: ignore[arg-type]
    return run_batch(
        load_manifest(FAKE), scenes, out, "r1", options, created_utc=CREATED, parent_env=_env()
    )


def test_second_batch_run_is_all_hits_and_starts_no_child(
    roots: tuple[Path, Path, Scene], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, out, _ = roots
    first = _run(roots)
    assert first.cache_hits == 0 and first.frames_run > 0
    cache_dir = out / "r1" / "cache" / "fake" / "default"
    assert [p.name for p in cache_dir.iterdir()][0].isalnum()
    rows_first = read_boxes(out, "r1", "coop")

    def no_child(*args: object, **kwargs: object) -> object:
        raise AssertionError("a cached run must not start a plugin process")

    monkeypatch.setattr(batch.PluginProcess, "start", no_child)
    second = _run(roots, force=True)
    assert second.cache_hits == 1 and second.frames_run == 0
    assert second.completed == ("coop",) and second.status == "complete"
    assert read_boxes(out, "r1", "coop") == rows_first
    provenance = batch.read_run_meta(out, "r1").provenance
    assert provenance is not None and provenance["cache_hits"] == 1


def test_no_cache_option_sends_every_frame(roots: tuple[Path, Path, Scene]) -> None:
    _, out, _ = roots
    first = _run(roots, cache=False)
    assert not (out / "r1" / "cache").exists()
    second = _run(roots, cache=False, force=True)
    assert second.cache_hits == 0 and second.frames_run == first.frames_run > 0


def test_cache_dir_is_not_a_scene(roots: tuple[Path, Path, Scene]) -> None:
    scenes, out, _ = roots
    _run(roots)
    with pytest.raises(PredictionError, match="cache"):
        check_scene_id("cache")
    report = evaluate_run(scenes, out, "r1", DEFAULT_CONFIG, created_utc=CREATED)
    assert report.evaluated == ("coop",) and report.errors == ()
    with pytest.raises(PredictionError):
        read_boxes(out, "r1", "cache")


def test_dependency_artifacts_come_from_the_cache(roots: tuple[Path, Path, Scene]) -> None:
    _, out, scene = roots
    _run(roots, options={"misbehave": "marker"})
    provenance = batch.read_run_meta(out, "r1").provenance
    assert provenance is not None
    environment = cast(dict[str, str], provenance["environment"])
    cache = SceneCache(out / "r1", plugin="fake", model="default")
    key = cache.key_for(scene, environment)
    artifacts = cache.artifact_dir(key)
    assert artifacts is not None
    assert (artifacts / "marker" / "done.txt").read_text(encoding="utf-8") == "done\n"


def test_prune_counts(roots: tuple[Path, Path, Scene]) -> None:
    _, out, _ = roots
    _run(roots)
    assert prune(out, run_id="r1", plugin="other", model=None) == 0
    assert prune(out, run_id="r1", plugin=None, model=None) == 1
    assert not (out / "r1" / "cache").exists()
    assert prune(out, run_id=None, plugin=None, model=None) == 0
