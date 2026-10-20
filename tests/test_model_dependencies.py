# SPDX-License-Identifier: Apache-2.0
"""``depends_on``: a model that consumes another model's artefacts (ADR 0011 decision 9)."""

from __future__ import annotations

import os
import shutil
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest
import yaml
from coperceive.evaluation.predictions import read_run_meta
from coperceive.model import Scene
from coperceive.models import batch
from coperceive.models.batch import BatchError, RunOptions, run_batch
from coperceive.models.cache import SceneCache
from coperceive.models.spec import ModelSpecError, load_manifest, parse_manifest
from coperceive.store import write_scene

from tests.cooperative import cooperative_scene
from tests.fake_model_plugin import MANIFEST

TWO = MANIFEST.with_name("model-two.yaml")
CREATED = "2026-10-19T12:00:00Z"


def _env() -> dict[str, str]:
    return {"PATH": os.environ.get("PATH", "/usr/bin"), "HOME": os.environ.get("HOME", "/tmp")}


@pytest.fixture
def roots(tmp_path: Path) -> tuple[Path, Path, Scene]:
    scene = replace(cooperative_scene(), scene_id="coop")
    write_scene(scene, tmp_path / "scenes" / "coop")
    return tmp_path / "scenes", tmp_path / "predictions", scene


def _run(
    roots: tuple[Path, Path, Scene], run_id: str = "r1", **kwargs: object
) -> batch.BatchReport:
    scenes, out, _ = roots
    options = RunOptions(model="b", **kwargs)  # type: ignore[arg-type]
    return run_batch(
        load_manifest(TWO), scenes, out, run_id, options, created_utc=CREATED, parent_env=_env()
    )


def test_dependency_missing_without_auto(roots: tuple[Path, Path, Scene]) -> None:
    with pytest.raises(BatchError, match="a/marker"):
        _run(roots, auto_dependencies=False)


def test_dependency_runs_first_and_its_path_reaches_the_child_through_configure(
    roots: tuple[Path, Path, Scene],
) -> None:
    _, out, scene = roots
    report = _run(roots)
    assert report.status == "complete" and report.completed == ("coop",)
    dependency = read_run_meta(out, "r1-a")  # the dependency is a run of its own
    assert dependency.provenance is not None and dependency.provenance["status"] == "complete"
    provenance = read_run_meta(out, "r1").provenance
    assert provenance is not None
    paths = cast(dict[str, dict[str, str]], provenance["dependencies"])
    work = out / ".work" / "r1"
    assert paths == {
        "a": {
            "marker": str(work / "artifacts" / "a" / "marker"),
            "scratch": str(work / "artifacts" / "a" / "scratch"),
        }
    }
    # b saw both artefacts by path (it wrote what it read into its own artefact `seen`).
    env = cast(dict[str, str], provenance["environment"])
    cache = SceneCache(out / "r1", plugin="fake", model="b")
    seen = cache.artifact_dir(cache.key_for(scene, env))
    assert seen is not None
    assert (seen / "seen" / "seen.txt").read_text(encoding="utf-8") == "done\nbig\n"


def test_transient_artifacts_are_not_cached(roots: tuple[Path, Path, Scene]) -> None:
    _, out, scene = roots
    _run(roots)
    provenance = read_run_meta(out, "r1-a").provenance
    assert provenance is not None
    env = cast(dict[str, str], provenance["environment"])
    cache = SceneCache(out / "r1-a", plugin="fake", model="a")
    artifacts = cache.artifact_dir(cache.key_for(scene, env))
    assert artifacts is not None
    assert (artifacts / "marker" / "done.txt").is_file()
    assert not (artifacts / "scratch").exists()  # transient: only the work directory has it
    assert (out / ".work" / "r1" / "artifacts" / "a" / "scratch" / "big.txt").is_file()


def test_dependency_from_artifacts_dir(roots: tuple[Path, Path, Scene], tmp_path: Path) -> None:
    _, out, _ = roots
    earlier = tmp_path / "earlier"
    (earlier / "a" / "marker").mkdir(parents=True)
    (earlier / "a" / "marker" / "done.txt").write_text("done\n", encoding="utf-8")
    (earlier / "a" / "scratch").mkdir(parents=True)
    (earlier / "a" / "scratch" / "big.txt").write_text("big\n", encoding="utf-8")
    report = _run(roots, artifacts_dir=earlier)
    assert report.status == "complete"
    assert not (out / "r1-a").exists()  # nothing ran for the dependency
    provenance = read_run_meta(out, "r1").provenance
    assert provenance is not None
    paths = cast(dict[str, dict[str, str]], provenance["dependencies"])
    assert paths["a"]["marker"] == str(earlier / "a" / "marker")
    with pytest.raises(BatchError, match="no a/marker"):
        _run(roots, run_id="r2", artifacts_dir=tmp_path / "nowhere")


def _count_starts(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    starts: list[int] = []
    original = batch.PluginProcess.start

    def counting(*args: object, **kwargs: object) -> object:
        starts.append(1)
        return original(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(batch.PluginProcess, "start", counting)
    return starts


def test_a_run_served_from_the_cache_runs_no_dependency(
    roots: tuple[Path, Path, Scene], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The transient artefact is gone, but b's own result is cached: nothing needs it."""
    _, out, _ = roots
    _run(roots)
    shutil.rmtree(out / ".work" / "r1" / "artifacts")
    starts = _count_starts(monkeypatch)
    report = _run(roots, force=True)
    assert report.status == "complete" and report.cache_hits == 1 and starts == []


def test_a_cached_dependency_runs_again_when_its_transient_artifact_is_gone(
    roots: tuple[Path, Path, Scene], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, out, _ = roots
    _run(roots)
    shutil.rmtree(out / ".work" / "r1" / "artifacts")
    starts = _count_starts(monkeypatch)
    report = _run(roots, force=True, cache=False)  # b needs a child, so its dependency
    assert report.status == "complete"
    assert len(starts) == 2  # the dependency (cache bypassed: nothing else writes scratch), then b
    assert (out / ".work" / "r1" / "artifacts" / "a" / "scratch" / "big.txt").is_file()


def test_manifest_rejects_an_undeclared_artifact() -> None:
    payload = cast(dict[str, Any], yaml.safe_load(TWO.read_text(encoding="utf-8")))
    payload["models"]["b"]["depends_on"] = [{"model": "a", "artifact": "nope"}]
    with pytest.raises(ModelSpecError, match="nope"):
        parse_manifest(payload, source=None)
    payload = cast(dict[str, Any], yaml.safe_load(TWO.read_text(encoding="utf-8")))
    payload["models"]["a"]["outputs"]["transient_artifacts"] = ["other"]
    with pytest.raises(ModelSpecError, match="transient_artifacts"):
        parse_manifest(payload, source=None)
