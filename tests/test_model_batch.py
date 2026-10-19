# SPDX-License-Identifier: Apache-2.0
"""Batch mode over the cooperative fixture with the fake plugin (ADR 0011 decision 3)."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest
import yaml
from coperceive.cli import main
from coperceive.evaluation.config import DEFAULT_CONFIG
from coperceive.evaluation.evaluate import evaluate_run, read_metrics, scene_output_dir
from coperceive.evaluation.predictions import read_boxes, read_run_meta, write_run
from coperceive.model import Scene
from coperceive.models.batch import RunOptions, run_batch
from coperceive.models.inputs import build_frame
from coperceive.models.spec import ModelSpecError, load_manifest, select_model
from coperceive.store import write_scene
from coperceive.viewdata import pose_times

from tests.cooperative import cooperative_scene
from tests.fake_model_plugin import MANIFEST as FAKE

CREATED = "2026-10-18T12:00:00Z"


def _env() -> dict[str, str]:
    return {"PATH": os.environ.get("PATH", "/usr/bin"), "HOME": os.environ.get("HOME", "/tmp")}


@pytest.fixture
def roots(tmp_path: Path) -> tuple[Path, Path, Scene]:
    scene = replace(cooperative_scene(), scene_id="coop")
    scenes = tmp_path / "scenes"
    write_scene(scene, scenes / "coop")
    return scenes, tmp_path / "predictions", scene


def _run(
    roots: tuple[Path, Path, Scene], run_id: str = "r1", **kwargs: object
) -> tuple[Path, Path]:
    scenes, out, _ = roots
    options = RunOptions(**kwargs)  # type: ignore[arg-type]
    report = run_batch(
        load_manifest(FAKE), scenes, out, run_id, options, created_utc=CREATED, parent_env=_env()
    )
    _run.last = report  # type: ignore[attr-defined]
    return scenes, out


def test_batch_writes_a_complete_run(roots: tuple[Path, Path, Scene]) -> None:
    scenes, out, scene = roots
    report = run_batch(
        load_manifest(FAKE), scenes, out, "r1", RunOptions(), created_utc=CREATED, parent_env=_env()
    )
    assert report.status == "complete" and report.reason is None
    assert report.completed == ("coop",) and report.failed == () and report.skipped == ()
    assert report.frames_run == len(pose_times(scene))
    assert report.exit_code == 0
    meta = read_run_meta(out, "r1")
    assert meta.model == "fake default 0.0.1"
    assert meta.notes is not None and meta.notes.startswith("fixture, no licence")
    provenance = meta.provenance
    assert provenance is not None
    assert provenance["plugin"] == "fake" and provenance["status"] == "complete"
    assert re.fullmatch(r"[0-9a-f]{64}", str(provenance["manifest_sha256"]))
    environment = cast(Mapping[str, str], provenance["environment"])
    assert re.fullmatch(r"[0-9a-f]{32}", environment["checkpoint_md5"])
    assert provenance["completed_scenes"] == ["coop"]
    assert provenance["frames_run"] == report.frames_run and provenance["cache_hits"] == 0
    assert provenance["gpu"] is None
    timings = cast(Mapping[str, float], provenance["timings"])
    assert timings["wall_s"] > 0 and timings["staging_s"] == 0
    assert (out / "r1" / ".." / ".work" / "r1" / "parent.log").resolve().is_file()


def test_rows_are_time_major_and_single(roots: tuple[Path, Path, Scene]) -> None:
    _, out = _run(roots)
    _, _, scene = roots
    rows = read_boxes(out, "r1", "coop")
    expected = [
        (time_us, agent.agent_id)
        for time_us in pose_times(scene)
        for agent in scene.agents
        if time_us in scene.pose_times(agent.agent_id)
    ]
    assert [(row.time_us, row.agent_id) for row in rows] == expected
    assert {row.source for row in rows} == {"single"}
    assert {row.category for row in rows} == {"car"}


def test_rerun_skips_a_complete_scene(roots: tuple[Path, Path, Scene]) -> None:
    _run(roots)
    _run(roots)
    report = _run.last  # type: ignore[attr-defined]
    assert report.skipped == ("coop",) and report.completed == ()
    assert report.frames_run == 0 and report.status == "complete"


def test_box_for_an_unrequested_agent_fails_the_scene(roots: tuple[Path, Path, Scene]) -> None:
    _, out = _run(roots, options={"misbehave": "bad-agent"})
    report = _run.last  # type: ignore[attr-defined]
    assert report.failed == ("coop",) and report.completed == ()
    assert not (out / "r1" / "coop" / "boxes.parquet").exists()
    assert report.status == "partial"
    assert report.reason is not None and report.reason.startswith("plugin_error")
    assert report.exit_code == 3
    provenance = read_run_meta(out, "r1").provenance
    assert provenance is not None and provenance["status"] == "partial"


def test_resume_refuses_a_different_manifest_without_force(
    roots: tuple[Path, Path, Scene], tmp_path: Path
) -> None:
    scenes, out, _ = roots
    manifest = load_manifest(FAKE)
    run_batch(manifest, scenes, out, "r1", RunOptions(), created_utc=CREATED, parent_env=_env())
    payload = cast(dict[str, Any], yaml.safe_load(FAKE.read_text(encoding="utf-8")))
    payload["notes"] = "edited copy: a different manifest digest, the same worker"
    copy = tmp_path / "plugin" / "model.yaml"
    copy.parent.mkdir()
    copy.write_text(yaml.safe_dump(payload), encoding="utf-8")
    (copy.parent / "worker.py").write_bytes((FAKE.parent / "worker.py").read_bytes())
    other = load_manifest(copy)
    with pytest.raises(ModelSpecError, match="manifest_sha256"):
        run_batch(other, scenes, out, "r1", RunOptions(), created_utc=CREATED, parent_env=_env())
    report = run_batch(
        other, scenes, out, "r1", RunOptions(force=True), created_utc=CREATED, parent_env=_env()
    )
    assert report.completed == ("coop",)


def test_run_timeout_marks_partial(roots: tuple[Path, Path, Scene]) -> None:
    _, out = _run(roots, options={"misbehave": "sleep"}, run_timeout_s=0.5)
    report = _run.last  # type: ignore[attr-defined]
    assert report.status == "partial"
    assert report.reason is not None and report.reason.startswith("timeout")
    assert report.exit_code == 4
    assert not (out / "r1" / "coop" / "boxes.parquet").exists()
    provenance = read_run_meta(out, "r1").provenance
    assert provenance is not None and provenance["status"] == "partial"
    assert str(provenance["reason"]).startswith("timeout")


def test_frame_timeout_fails_the_scene_and_the_run_goes_on(
    roots: tuple[Path, Path, Scene], tmp_path: Path
) -> None:
    scenes, out, scene = roots
    write_scene(replace(scene, scene_id="coop2"), scenes / "coop2")
    options = RunOptions(options={"misbehave": "sleep"}, frame_timeout_s=0.5, scene_ids=("coop",))
    report = run_batch(
        load_manifest(FAKE), scenes, out, "r1", options, created_utc=CREATED, parent_env=_env()
    )
    assert report.failed == ("coop",) and report.status == "partial"
    assert report.reason is not None and report.reason.startswith("timeout: frame")
    assert report.exit_code == 4


def test_build_frame_extracts_sweeps(roots: tuple[Path, Path, Scene], tmp_path: Path) -> None:
    scenes, _, scene = roots
    spec = select_model(load_manifest(FAKE), "default")
    frame, files = build_frame(
        scene,
        scenes / "coop",
        spec,
        frame_id=7,
        time_us=100_000,
        agent_ids=("vehicle", "uav", "mast"),
        work_dir=tmp_path / "work",
        media_roots=(),
        raw_root=None,
    )
    assert frame.id == 7 and set(frame.poses) >= {"vehicle"}
    assert len(frame.points) == 1
    sweep = frame.points[0]
    assert sweep.layout == "xyzi_f32le" and sweep.agent_id == "vehicle"
    assert Path(sweep.path).stat().st_size == sweep.count * 16
    assert Path(sweep.path).is_relative_to(tmp_path / "work" / "inputs" / "7")
    assert frame.images == ()  # the fixture's image row has no file
    assert [f.relative for f in files] == ["points.bin"]


def _partial_run(roots: tuple[Path, Path, Scene], run_id: str, partial: bool) -> None:
    """A hand-written run over ``coop`` only, with a second scene directory present."""
    scenes, out, scene = roots
    if not (scenes / "coop2").is_dir():
        write_scene(replace(scene, scene_id="coop2"), scenes / "coop2")
    manifest = load_manifest(FAKE)
    report = run_batch(
        manifest,
        scenes,
        out,
        run_id,
        RunOptions(scene_ids=("coop",)),
        created_utc=CREATED,
        parent_env=_env(),
    )
    assert report.completed == ("coop",)
    meta = read_run_meta(out, run_id)
    assert meta.provenance is not None
    status = "partial" if partial else "complete"
    write_run(
        out, run_id, replace(meta, provenance={**meta.provenance, "status": status}), [], force=True
    )


def test_partial_run_is_marked_in_metrics_and_report(
    roots: tuple[Path, Path, Scene], capsys: pytest.CaptureFixture[str]
) -> None:
    scenes, out, _ = roots
    _partial_run(roots, "part", partial=True)
    _partial_run(roots, "full", partial=False)
    for run_id in ("part", "full"):
        evaluate_run(scenes, out, run_id, DEFAULT_CONFIG, created_utc=CREATED)
    partial = read_metrics(scene_output_dir(out, "part", "coop"))
    assert partial["run_status"] == "partial" and partial["missing_scenes"] == ["coop2"]
    full = read_metrics(scene_output_dir(out, "full", "coop"))
    assert full["run_status"] == "complete" and "missing_scenes" not in full
    main(["eval", "report", "part", "--root", str(scenes), "--predictions", str(out)])
    text = capsys.readouterr().out
    assert text.startswith("partial: 1 of 2 scenes\n")
    main(["eval", "report", "full", "--root", str(scenes), "--predictions", str(out)])
    assert "partial:" not in capsys.readouterr().out
    main(["eval", "compare", "full", "part", "--root", str(scenes), "--predictions", str(out)])
    text = capsys.readouterr().out
    assert "part: partial: 1 of 2 scenes" in text and "full: partial" not in text


def _totals(out: Path, run_id: str) -> list[dict[str, object]]:
    metrics = read_metrics(scene_output_dir(out, run_id, "coop"))
    outputs = cast(list[dict[str, object]], metrics["outputs"])
    return [cast(dict[str, object], item["totals"]) for item in outputs]


def test_known_answer_model_matches_every_gt_box_at_offset_zero(
    roots: tuple[Path, Path, Scene],
) -> None:
    scenes, out, _ = roots
    report = run_batch(
        load_manifest(FAKE),
        scenes,
        out,
        "ka0",
        RunOptions(model="known-answer"),
        created_utc=CREATED,
        parent_env=_env(),
    )
    assert report.status == "complete" and report.boxes > 0
    evaluate_run(scenes, out, "ka0", DEFAULT_CONFIG, created_utc=CREATED)
    totals = _totals(out, "ka0")
    assert totals, "no outputs evaluated"
    for total in totals:
        assert total["gt"] == total["tp"] and total["fp"] == 0 and total["miss"] == 0, total
        assert cast(int, total["gt"]) > 0


def test_known_answer_model_reproduces_its_fixture_boxes_exactly(
    roots: tuple[Path, Path, Scene],
) -> None:
    """At offset 0 the written rows are the ground truth, value for value."""
    scenes, out, scene = roots
    run_batch(
        load_manifest(FAKE),
        scenes,
        out,
        "ka0",
        RunOptions(model="known-answer"),
        created_utc=CREATED,
        parent_env=_env(),
    )
    rows = read_boxes(out, "ka0", "coop")
    written = {
        (
            r.agent_id,
            r.time_us,
            r.track_id,
            r.category,
            r.center,
            r.size_lwh,
            r.box_from_rpy,
            r.frame,
        )
        for r in rows
    }
    expected = {
        (
            a.agent_id,
            a.time_us,
            a.track_id,
            a.category,
            a.center,
            a.size_lwh,
            a.box_from_rpy,
            a.frame,
        )
        for a in scene.annotations
    }
    assert written == expected
    assert len(rows) == len(scene.annotations)


def test_known_answer_model_misses_everything_beyond_the_threshold(
    roots: tuple[Path, Path, Scene],
) -> None:
    scenes, out, _ = roots
    run_batch(
        load_manifest(FAKE),
        scenes,
        out,
        "ka10",
        RunOptions(model="known-answer", options={"offset_m": "10"}),
        created_utc=CREATED,
        parent_env=_env(),
    )
    evaluate_run(scenes, out, "ka10", DEFAULT_CONFIG, created_utc=CREATED)
    for total in _totals(out, "ka10"):
        assert total["tp"] == 0 and total["fp"] == total["gt"] and total["miss"] == total["gt"], (
            total
        )


def test_progress_before_configured_is_tolerated(roots: tuple[Path, Path, Scene]) -> None:
    """A model may report its start-up (the Griffin toolkit run) before it says configured."""
    _, out = _run(roots, options={"misbehave": "progress-first"})
    report = _run.last  # type: ignore[attr-defined]
    assert report.status == "complete" and report.completed == ("coop",)
    assert (out / "r1" / "coop" / "boxes.parquet").is_file()


def test_provenance_splits_staging_conversion_and_inference(
    roots: tuple[Path, Path, Scene],
) -> None:
    """The child reports conversion_s and inference_s; per_sample_s is inference only; the
    parent writes staging_s from the rsync wall time the Slurm job passes."""
    _, out = _run(roots, options={"misbehave": "timings"}, staging_seconds=41.5)
    report = _run.last  # type: ignore[attr-defined]
    assert report.status == "complete" and report.frames_run == 7
    provenance = read_run_meta(out, "r1").provenance
    assert provenance is not None
    timings = cast(Mapping[str, float], provenance["timings"])
    assert timings["staging_s"] == 41.5
    assert timings["conversion_s"] == 2.0 and timings["inference_s"] == 7.0
    assert timings["per_sample_s"] == pytest.approx(7.0 / 7)  # inference over frames, not wall
    assert timings["wall_s"] > 0


def test_a_child_that_reports_nothing_has_no_split(roots: tuple[Path, Path, Scene]) -> None:
    _, out = _run(roots)
    provenance = read_run_meta(out, "r1").provenance
    assert provenance is not None
    timings = cast(Mapping[str, float], provenance["timings"])
    assert "inference_s" not in timings and timings["staging_s"] == 0.0
