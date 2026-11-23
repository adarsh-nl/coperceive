# SPDX-License-Identifier: Apache-2.0
"""``model run --evaluate``: the run is scored as ``eval run <run-id>`` would score it
(docs/guides/evaluate.md, "Score a model run in one step"; spec model-plugin §Exit codes).
"""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest
from coperceive.cli import main
from coperceive.evaluation import scoring
from coperceive.evaluation.config import DEFAULT_CONFIG, load_config
from coperceive.evaluation.evaluate import RunReport
from coperceive.evaluation.predictions import read_run_meta, write_run
from coperceive.evaluation.report import config_protocol_label
from coperceive.evaluation.scoring import EVALUATION_FILE, config_sha256, read_record
from coperceive.models import commands, slurm
from coperceive.models.batch import EXIT_EVAL, EXIT_OK, EXIT_PROTOCOL, EXIT_TIMEOUT, RunOptions
from coperceive.models.commands import combined_exit_code
from coperceive.store import write_scene

from tests.cooperative import cooperative_scene
from tests.fake_model_plugin import MANIFEST as FAKE


@pytest.fixture
def scenes(tmp_path: Path) -> Path:
    root = tmp_path / "scenes"
    write_scene(replace(cooperative_scene(), scene_id="coop"), root / "coop")
    return root


def _run(scenes: Path, out: Path, run_id: str, *extra: str) -> list[str]:
    return [
        "model",
        "run",
        "--manifest",
        str(FAKE),
        "--scenes",
        str(scenes),
        "--out",
        str(out),
        "--run-id",
        run_id,
        *extra,
    ]


def _code(argv: list[str]) -> int:
    try:
        main(argv)
    except SystemExit as exc:
        return int(exc.code or 0)
    return 0


def _metrics(out: Path, run_id: str) -> dict[str, Any]:
    path = out / run_id / "coop" / "metrics.json"
    return cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))


def _without_time(metrics: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in metrics.items() if key != "created_utc"}


def _slurm(tmp_path: Path) -> list[str]:
    return [
        "--slurm",
        "--slurm-partition",
        "cpu",
        "--slurm-scratch-template",
        "/scratch/$USER/$SLURM_JOB_ID",
        "--slurm-logs",
        str(tmp_path / "logs"),
        "--slurm-results",
        str(tmp_path / "results"),
    ]


# --- the same numbers as eval run ------------------------------------------------------------


def test_evaluate_writes_the_metrics_eval_run_writes(scenes: Path, tmp_path: Path) -> None:
    out = tmp_path / "predictions"
    assert _code(_run(scenes, out, "two-steps")) == EXIT_OK
    assert not (out / "two-steps" / "coop" / "metrics.json").exists()
    main(["eval", "run", "two-steps", "--root", str(scenes), "--predictions", str(out)])
    assert _code(_run(scenes, out, "one-step", "--evaluate")) == EXIT_OK
    one, two = _metrics(out, "one-step"), _metrics(out, "two-steps")
    assert _without_time(one) == {**_without_time(two), "run_id": "one-step"}
    # evaluation.json says scoring ran, how, and with which resolved config.
    record = read_record(out, "one-step")
    assert record is not None
    assert record["status"] == "ok" and record["command"] == "model run --evaluate"
    assert record["evaluated"] == ["coop"] and record["errors"] == []
    assert record["config_sha256"] == config_sha256(DEFAULT_CONFIG)
    canonical = json.dumps(one["config"], sort_keys=True, separators=(",", ":"))
    assert record["config_sha256"] == hashlib.sha256(canonical.encode()).hexdigest()
    assert record["protocol"] == config_protocol_label(one["config"])
    # eval run writes the same record, so the newest scoring is the one on disk.
    two_record = read_record(out, "two-steps")
    assert two_record is not None and two_record["command"] == "eval run"
    assert two_record["config_sha256"] == record["config_sha256"]


def test_without_evaluate_nothing_is_scored(scenes: Path, tmp_path: Path) -> None:
    out = tmp_path / "predictions"
    assert _code(_run(scenes, out, "r1")) == EXIT_OK
    assert not (out / "r1" / EVALUATION_FILE).exists()
    assert not (out / "r1" / "coop" / "metrics.json").exists()


# --- protocol precedence ----------------------------------------------------------------------


def _scored_scopes(out: Path, run_id: str) -> tuple[str, str]:
    config = _metrics(out, run_id)["config"]
    return str(config["ranking_scope"]), str(config["gt_scope"])


def test_the_runs_protocol_is_used_unless_the_config_says_otherwise(
    scenes: Path, tmp_path: Path
) -> None:
    out = tmp_path / "predictions"
    legacy = tmp_path / "legacy.yaml"
    legacy.write_text("protocol: legacy\n", encoding="utf-8")
    # No choice: the default protocol.
    assert _code(_run(scenes, out, "plain", "--evaluate")) == EXIT_OK
    assert _scored_scopes(out, "plain") == ("per_frame", "connected_union")
    # --protocol is recorded in run.json and scored under.
    assert _code(_run(scenes, out, "proto", "--protocol", "legacy", "--evaluate")) == EXIT_OK
    assert _scored_scopes(out, "proto") == ("pooled", "ego_only")
    # The config file's protocol applies when --protocol is not given.
    argv = _run(scenes, out, "config", "--evaluate", "--eval-config", str(legacy))
    assert _code(argv) == EXIT_OK
    assert _scored_scopes(out, "config") == ("pooled", "ego_only")
    record = read_record(out, "config")
    assert record is not None and record["config_file"] == str(legacy)
    assert record["config_sha256"] == config_sha256(load_config(legacy))
    # Saying the same thing twice is fine.
    argv = _run(
        scenes, out, "both", "--protocol", "legacy", "--evaluate", "--eval-config", str(legacy)
    )
    assert _code(argv) == EXIT_OK
    assert _scored_scopes(out, "both") == ("pooled", "ego_only")


def test_a_config_that_disagrees_or_is_bad_fails_before_the_model_runs(
    scenes: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "predictions"
    legacy = tmp_path / "legacy.yaml"
    legacy.write_text("protocol: legacy\n", encoding="utf-8")
    bad = tmp_path / "bad.yaml"
    bad.write_text("thresholds: nope\n", encoding="utf-8")
    cases = (
        ["--protocol", "default", "--evaluate", "--eval-config", str(legacy)],
        ["--evaluate", "--eval-config", str(bad)],
        ["--evaluate", "--eval-config", str(tmp_path / "missing.yaml")],
        ["--eval-config", str(legacy)],
    )
    for index, extra in enumerate(cases):
        run_id = f"r{index}"
        assert _code(_run(scenes, out, run_id, *extra)) == 1, extra
        assert not (out / run_id).exists(), extra
    assert "--eval-config needs --evaluate" in capsys.readouterr().err


# --- exit codes --------------------------------------------------------------------------------


def test_the_combined_exit_code_never_hides_the_models_status() -> None:
    assert combined_exit_code(EXIT_OK, True) == EXIT_OK
    assert combined_exit_code(EXIT_OK, False) == EXIT_EVAL == 5
    for model in (1, EXIT_PROTOCOL, EXIT_TIMEOUT):
        assert combined_exit_code(model, True) == model
        assert combined_exit_code(model, False) == model


def test_a_complete_run_whose_scoring_fails_exits_5_and_says_so(
    scenes: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    out = tmp_path / "predictions"

    def with_errors(*_args: object, **_kwargs: object) -> RunReport:
        return RunReport(evaluated=(), skipped=(), errors=("coop: synthetic failure",))

    monkeypatch.setattr(scoring, "evaluate_run", with_errors)
    assert _code(_run(scenes, out, "errors", "--evaluate")) == EXIT_EVAL
    assert "error\tcoop: synthetic failure" in capsys.readouterr().out
    record = read_record(out, "errors")
    assert record is not None and record["status"] == "errors"

    def crashes(*_args: object, **_kwargs: object) -> RunReport:
        raise RuntimeError("synthetic bug")

    monkeypatch.setattr(scoring, "evaluate_run", crashes)
    assert _code(_run(scenes, out, "crash", "--evaluate")) == EXIT_EVAL
    assert "synthetic bug" in capsys.readouterr().err
    record = read_record(out, "crash")
    assert record is not None and record["status"] == "failed"
    assert record["error"] == "synthetic bug"
    # The model's output is untouched by the failed scoring.
    assert (out / "crash" / "coop" / "boxes.parquet").is_file()


def test_a_partial_run_is_still_scored_and_keeps_its_own_exit_code(
    scenes: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = tmp_path / "predictions"
    timeout = ("--option", "misbehave=sleep", "--run-timeout", "0.5")
    assert _code(_run(scenes, out, "slow", *timeout, "--evaluate")) == EXIT_TIMEOUT
    record = read_record(out, "slow")
    assert record is not None and record["status"] == "ok"
    assert record["evaluated"] == [] and record["skipped"] == ["coop"]

    def crashes(*_args: object, **_kwargs: object) -> RunReport:
        raise RuntimeError("synthetic bug")

    monkeypatch.setattr(scoring, "evaluate_run", crashes)
    assert _code(_run(scenes, out, "slow2", *timeout, "--evaluate")) == EXIT_TIMEOUT


# --- the Slurm job ----------------------------------------------------------------------------


def _job_script(capsys: pytest.CaptureFixture[str]) -> str:
    text = capsys.readouterr().out
    return text[text.index("#!/bin/bash") :]


def test_the_job_scores_after_the_run_has_come_home(
    scenes: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "predictions"
    config = tmp_path / "eval config.yaml"
    config.write_text("protocol: legacy\n", encoding="utf-8")
    argv = _run(scenes, out, "r1", "--protocol", "legacy", "--evaluate", "--eval-config")
    main([*argv, str(config), *_slurm(tmp_path)])
    script = _job_script(capsys)
    lines = script.splitlines()
    (model,) = [i for i, line in enumerate(lines) if " -m coperceive model run " in line]
    (score,) = [i for i, line in enumerate(lines) if " -m coperceive eval run " in line]
    # The model's run line does not score: the job does, after copying the run home.
    assert "--evaluate" not in lines[model] and "--eval-config" not in lines[model]
    target = shlex.quote(str((out / "r1").resolve()))
    home = [
        i for i, line in enumerate(lines) if line.endswith(f'"$SCRATCH/out/$RUN_ID/" {target}/')
    ]
    assert len(home) == 2 and model < home[0] < score < home[1]
    assert lines[score].split() == [
        shlex.quote(sys.executable),
        "-m",
        "coperceive",
        "eval",
        "run",
        '"$RUN_ID"',
        "--root",
        '"$SCRATCH/scenes"',
        "--predictions",
        '"$SCRATCH/out"',
        "--protocol",
        "legacy",
        "--config",
        *shlex.quote(str(config.resolve())).split(),
    ]
    rc = lines.index('if [ "$RC" -eq 0 ] && [ "$EVAL_RC" -ne 0 ]; then RC=5; fi')
    assert home[1] < rc < lines.index('exit "$RC"')


def test_without_evaluate_the_job_has_no_scoring_step(
    scenes: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    main([*_run(scenes, tmp_path / "predictions", "r1"), *_slurm(tmp_path)])
    script = _job_script(capsys)
    assert "eval run" not in script and "EVAL_RC" not in script


def test_a_bad_eval_config_is_refused_before_a_job_is_written_or_submitted(
    scenes: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_submit(_path: Path) -> str:
        raise AssertionError("sbatch must not be called")

    monkeypatch.setattr(slurm, "submit", no_submit)
    out = tmp_path / "predictions"
    argv = _run(scenes, out, "r1", "--evaluate", "--eval-config", str(tmp_path / "missing.yaml"))
    assert _code([*argv, *_slurm(tmp_path), "--submit"]) == 1
    assert not (out / ".slurm").exists()


@pytest.mark.parametrize(
    ("model_rc", "eval_rc", "expected"),
    [(0, 0, 0), (0, 1, 5), (3, 0, 3), (3, 1, 3), (4, 1, 4), (1, 0, 1)],
)
def test_the_jobs_exit_code_combines_as_the_local_one(
    tmp_path: Path, model_rc: int, eval_rc: int, expected: int
) -> None:
    # Run the job's scoring block in bash with a stand-in interpreter that exits eval_rc.
    fake = tmp_path / "bin" / "python"
    fake.parent.mkdir()
    fake.write_text(f"#!/bin/sh\nexit {eval_rc}\n", encoding="utf-8")
    fake.chmod(0o755)
    scratch = tmp_path / "scratch"
    (scratch / "out" / "r1").mkdir(parents=True)
    (scratch / "out" / "r1" / "run.json").write_text("{}", encoding="utf-8")
    results, target = tmp_path / "results", tmp_path / "home" / "r1"
    (results / "7").mkdir(parents=True)
    target.mkdir(parents=True)
    block = slurm.evaluate_lines(fake, RunOptions(evaluate=True), results=results, target=target)
    script = "\n".join(["set -euo pipefail", f"RC={model_rc}", "RUN_ID=r1", *block, 'exit "$RC"'])
    env = {**os.environ, "SCRATCH": str(scratch), "SLURM_JOB_ID": "7"}
    done = subprocess.run(["bash", "-c", script], env=env, check=False, capture_output=True)
    assert done.returncode == expected, done.stderr
    assert (target / "run.json").is_file()  # the run directory came home again


@pytest.mark.parametrize(("model_rc", "expected"), [(0, EXIT_EVAL), (3, 3)])
def test_a_job_without_run_json_is_never_scored_silently(
    scenes: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    model_rc: int,
    expected: int,
) -> None:
    # Render the job, then run its tail (scoring onwards) in bash over a scratch dir
    # whose run directory has no run.json: a model that exits 0 without one is exit 5.
    main([*_run(scenes, tmp_path / "predictions", "r1", "--evaluate"), *_slurm(tmp_path)])
    lines = _job_script(capsys).splitlines()
    (start,) = [i for i, line in enumerate(lines) if line.startswith("# --evaluate: score")]
    assert lines[-1] == 'exit "$RC"'
    scratch = tmp_path / "scratch"
    (scratch / "out" / "r1").mkdir(parents=True)
    script = "\n".join(["set -euo pipefail", f"RC={model_rc}", "RUN_ID=r1", *lines[start:]])
    env = {**os.environ, "SCRATCH": str(scratch), "SLURM_JOB_ID": "7"}
    done = subprocess.run(["bash", "-c", script], env=env, check=False, capture_output=True)
    assert done.returncode == expected, done.stderr
    assert "eval run skipped: no run.json for r1" in done.stderr.decode()
    assert not (scratch / "out" / "r1" / "metrics.json").exists()


def test_a_partial_run_scores_its_finished_scenes(
    scenes: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The fake plugin has no fault that stops one scene of two, so the real run writes
    # both and the stand-in turns it into a partial one (spec §Partial runs): coop2's
    # boxes removed, run.json status partial, exit 3. Scoring scores coop.
    write_scene(replace(cooperative_scene(), scene_id="coop2"), scenes / "coop2")
    out = tmp_path / "predictions"
    real = commands.run_batch

    def partial(manifest: Any, root: Path, out_root: Path, run_id: str, *a: Any, **k: Any) -> Any:
        report = real(manifest, root, out_root, run_id, *a, **k)
        (out_root / run_id / "coop2" / "boxes.parquet").unlink()
        meta = read_run_meta(out_root, run_id)
        assert meta.provenance is not None
        provenance = {**meta.provenance, "status": "partial", "reason": "plugin_error: test"}
        write_run(out_root, run_id, replace(meta, provenance=provenance), [], force=True)
        return replace(report, status="partial", reason="plugin_error: test")

    monkeypatch.setattr(commands, "run_batch", partial)
    assert _code(_run(scenes, out, "r1", "--evaluate")) == EXIT_PROTOCOL
    record = read_record(out, "r1")
    assert record is not None and record["status"] == "ok"
    assert record["evaluated"] == ["coop"] and record["skipped"] == ["coop2"]
    metrics = _metrics(out, "r1")
    assert metrics["run_status"] == "partial" and metrics["missing_scenes"] == ["coop2"]


def test_the_protocol_scored_under_is_the_one_this_run_recorded(
    scenes: Path, tmp_path: Path
) -> None:
    # A rerun without --protocol records none: it is scored under the default, not under
    # the protocol an earlier run of the same id recorded.
    out = tmp_path / "predictions"
    assert _code(_run(scenes, out, "r1", "--protocol", "legacy", "--evaluate")) == EXIT_OK
    assert _scored_scopes(out, "r1") == ("pooled", "ego_only")
    assert _code(_run(scenes, out, "r1", "--force", "--evaluate")) == EXIT_OK
    run_json = json.loads((out / "r1" / "run.json").read_text(encoding="utf-8"))
    assert "eval_protocol" not in run_json["provenance"]
    assert _scored_scopes(out, "r1") == ("per_frame", "connected_union")


def test_eval_run_scores_the_files_a_job_stages_as_it_scores_the_full_scenes(
    scenes: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # The job scores on $SCRATCH/scenes, which holds only the files in <run>.scenes.txt
    # (no image bytes; no points.bin for a model without lidar). The numbers must be the
    # ones the full scene directories give.
    out = tmp_path / "predictions"
    assert _code(_run(scenes, out, "r1", "--evaluate")) == EXIT_OK
    main([*_run(scenes, tmp_path / "plan", "r1", "--evaluate"), *_slurm(tmp_path)])
    capsys.readouterr()
    listed = (tmp_path / "plan" / ".slurm" / "r1.scenes.txt").read_text(encoding="utf-8")
    staged = tmp_path / "staged"
    for name in listed.splitlines():
        (staged / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(scenes / name, staged / name)
    copy = tmp_path / "scratch-out"
    shutil.copytree(out, copy)
    for path in copy.rglob("metrics.json"):
        path.unlink()
    main(["eval", "run", "r1", "--root", str(staged), "--predictions", str(copy)])
    assert _without_time(_metrics(copy, "r1")) == _without_time(_metrics(out, "r1"))


def test_a_run_over_some_scenes_is_scored_on_those_scenes(scenes: Path, tmp_path: Path) -> None:
    # As in the job, which stages only the selected scenes: the other directory is not
    # scored, skipped or counted as missing.
    write_scene(replace(cooperative_scene(), scene_id="coop2"), scenes / "coop2")
    out = tmp_path / "predictions"
    assert _code(_run(scenes, out, "r1", "--scene-ids", "coop", "--evaluate")) == EXIT_OK
    record = read_record(out, "r1")
    assert record is not None
    assert record["evaluated"] == ["coop"] and record["skipped"] == []
