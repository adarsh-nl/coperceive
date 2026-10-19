# SPDX-License-Identifier: Apache-2.0
"""The Griffin worker through ``model check`` and ``run_batch`` in det_json mode: no GPU, no
toolkit, no dataset; a manifest copy runs it in this interpreter against a stand-in
checkpoint file."""

from __future__ import annotations

import os
import shutil
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import yaml
from coperceive.evaluation.predictions import read_boxes, read_run_meta
from coperceive.models.batch import RunOptions, run_batch
from coperceive.models.check import check_plugin
from coperceive.models.spec import load_manifest
from coperceive.models.synthetic import synthetic_scene
from coperceive.store import write_scene

ROOT = Path(__file__).resolve().parents[1] / "plugins" / "griffin"
PLUGIN = ROOT / "coperceive_griffin_model"
FIXTURES = ROOT / "tests" / "fixtures"
CREATED = "2026-10-18T12:00:00Z"


def _env() -> dict[str, str]:
    return {"PATH": os.environ.get("PATH", "/usr/bin"), "HOME": os.environ.get("HOME", "/tmp")}


def _test_manifest(tmp_path: Path) -> Path:
    """The real manifest with the environment set to this interpreter, the checkpoint pin
    removed and the worker pointed at a stand-in checkpoint root."""
    plugin = tmp_path / "plugin"
    shutil.copytree(PLUGIN, plugin, ignore=shutil.ignore_patterns("__pycache__"))
    for relative in (
        "vehicle-side/iter_33024.pth",
        "drone-side/iter_33024.pth",
        "cooperative/instance_fusion/iter_33024.pth",
    ):
        ckpt = tmp_path / "ckpts" / "griffin_50scenes_25m" / relative
        ckpt.parent.mkdir(parents=True, exist_ok=True)
        ckpt.write_bytes(b"not a real checkpoint")
    payload = cast(
        dict[str, Any], yaml.safe_load((plugin / "model.yaml").read_text(encoding="utf-8"))
    )
    payload["environment"] = {"current": True}
    for model in payload["models"].values():
        model.pop("checkpoint_md5", None)
    payload["commands"]["batch"] = [
        "{python}",
        "{plugin_dir}/worker.py",
        "--model",
        "{model}",
        "--ckpt-root",
        str(tmp_path / "ckpts"),
        "--toolkit",
        str(tmp_path / "no-toolkit"),
    ]
    (plugin / "model.yaml").write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return plugin / "model.yaml"


def _options() -> dict[str, str]:
    return {"det_json": str(FIXTURES / "results_nusc_det.json"), "nusc_root": str(FIXTURES)}


def test_check_with_det_json_fixture(tmp_path: Path) -> None:
    results = check_plugin(
        load_manifest(_test_manifest(tmp_path)),
        model="vehicle-side",
        work_root=tmp_path / "check",
        keep=False,
        raw_root=None,
        parent_env=_env(),
        options=_options(),
    )
    failed = [r for r in results if r.status == "fail"]
    assert failed == [], failed
    by_number = {r.number: r for r in results}
    assert (
        by_number[1].status == "ok" and by_number[4].status == "ok" and by_number[8].status == "ok"
    )
    assert by_number[9].status == "skipped"  # capabilities.cancel is false


def test_run_batch_with_det_json_reproduces_the_fixture(tmp_path: Path) -> None:
    scenes = tmp_path / "scenes"
    write_scene(synthetic_scene(), scenes / "synthetic-check")
    out = tmp_path / "predictions"
    manifest = load_manifest(_test_manifest(tmp_path))
    report = run_batch(
        manifest,
        scenes,
        out,
        "griffin-fixture",
        RunOptions(model="vehicle-side", options=_options(), raw_root=tmp_path / "raw"),
        created_utc=CREATED,
        parent_env=_env(),
    )
    assert report.status == "complete", report.reason
    rows = read_boxes(out, "griffin-fixture", "synthetic-check")
    # Two boxes at 0 µs, one at 100000 µs (the 0.01 one is below min_score), none later.
    assert [(r.time_us, r.category) for r in rows] == [
        (0, "car"),
        (0, "pedestrian"),
        (100_000, "car"),
    ]
    assert rows[0].size_lwh == (4.0, 1.9, 1.6) and rows[0].agent_id == "vehicle"
    assert rows[0].source == "single"
    meta = read_run_meta(out, "griffin-fixture")
    assert meta.model == "griffin vehicle-side 0.1.0"
    assert meta.notes is not None and "arXiv:2503.06983" in meta.notes
    provenance = meta.provenance
    assert provenance is not None
    environment = cast(dict[str, str], provenance["environment"])
    assert environment["checkpoint_path"].endswith("vehicle-side/iter_33024.pth")
    assert environment["checkpoint_md5"] == cast(dict[str, Any], provenance["checkpoint"])["md5"]
    # min_score is an option: at 0.5 the pedestrian goes too.
    report = run_batch(
        manifest,
        scenes,
        out,
        "griffin-fixture-strict",
        RunOptions(
            model="vehicle-side",
            options={**_options(), "min_score": "0.5"},
            raw_root=tmp_path / "raw",
        ),
        created_utc=CREATED,
        parent_env=_env(),
    )
    assert report.status == "complete"
    strict = read_boxes(out, "griffin-fixture-strict", "synthetic-check")
    assert [(r.time_us, r.category) for r in strict] == [(0, "car"), (100_000, "car")]
    assert replace(rows[0], score=rows[0].score) == rows[0]


def test_instance_fusion_in_det_json_mode_needs_no_dependency_artifact(tmp_path: Path) -> None:
    """Through check_plugin, and through run_batch with a stand-in --artifacts directory;
    the rows are the fused source of the vehicle."""
    manifest = load_manifest(_test_manifest(tmp_path))
    results = check_plugin(
        manifest,
        model="instance-fusion",
        work_root=tmp_path / "check",
        keep=False,
        raw_root=None,
        parent_env=_env(),
        options=_options(),
    )
    assert [r for r in results if r.status == "fail"] == []
    scenes = tmp_path / "scenes"
    write_scene(synthetic_scene(), scenes / "synthetic-check")
    stand_in = tmp_path / "earlier"
    (stand_in / "drone-side" / "track_query").mkdir(parents=True)
    (stand_in / "drone-side" / "track_query" / "sample_1.pkl").write_bytes(b"x")
    out = tmp_path / "predictions"
    report = run_batch(
        manifest,
        scenes,
        out,
        "griffin-instance-fixture",
        RunOptions(
            model="instance-fusion",
            options=_options(),
            raw_root=tmp_path / "raw",
            artifacts_dir=stand_in,
        ),
        created_utc=CREATED,
        parent_env=_env(),
    )
    assert report.status == "complete", report.reason
    rows = read_boxes(out, "griffin-instance-fixture", "synthetic-check")
    assert {r.source for r in rows} == {"fused"} and {r.agent_id for r in rows} == {"vehicle"}
    meta = read_run_meta(out, "griffin-instance-fixture")
    provenance = meta.provenance
    assert provenance is not None
    dependencies = cast(dict[str, dict[str, str]], provenance["dependencies"])
    assert dependencies == {
        "drone-side": {"track_query": str(stand_in / "drone-side" / "track_query")}
    }
    assert meta.model == "griffin instance-fusion 0.1.0"
