# SPDX-License-Identifier: Apache-2.0
"""The Slurm emitter of ``coperceive model run --slurm`` (ADR 0011 decision 5)."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest
import yaml
from coperceive.cli import main
from coperceive.evaluation.predictions import read_run_meta, write_run
from coperceive.model import Scene
from coperceive.models.batch import RunOptions, run_batch
from coperceive.models.slurm import (
    SlurmError,
    estimate_from_runs,
    interpreter_module,
    model_closure,
    plan_sbatch,
    staged_files,
    submit,
    time_limit,
    validate_partition,
    validate_scratch_template,
    work_copy_filter,
    write_plan,
)
from coperceive.models.spec import Manifest, load_manifest, parse_manifest, select_model
from coperceive.store import write_scene

from tests.cooperative import cooperative_scene
from tests.fake_model_plugin import MANIFEST as FAKE

CREATED = "2026-10-18T12:00:00Z"


@pytest.fixture
def roots(tmp_path: Path) -> tuple[Path, Path, Scene]:
    scene = replace(cooperative_scene(), scene_id="coop")
    scenes = tmp_path / "scenes"
    write_scene(scene, scenes / "coop")
    return scenes, tmp_path / "predictions", scene


def _manifest(**changes: object) -> Manifest:
    payload = cast(dict[str, Any], yaml.safe_load(FAKE.read_text(encoding="utf-8")))
    resources = payload["models"]["default"]["resources"]
    for key in (
        "gpu",
        "cpus",
        "memory_gb",
        "slurm_constraint",
        "seconds_per_sample",
        "staging_seconds",
    ):
        if key in changes:
            resources[key] = changes[key]
    if "modules" in changes:
        payload["environment"]["modules"] = changes["modules"]
    if "secrets" in changes:
        payload["environment"]["secrets"] = changes["secrets"]
    return parse_manifest(payload, source=FAKE)


def _plan(roots: tuple[Path, Path, Scene], manifest: Manifest, tmp_path: Path, **kwargs: object):  # noqa: ANN202
    scenes, out, scene = roots
    spec = select_model(manifest, "default")
    options = RunOptions(**kwargs)  # type: ignore[arg-type]
    return plan_sbatch(
        manifest,
        spec,
        scenes_root=scenes,
        scenes={"coop": scene},
        run_id="r1",
        out_root=out,
        options=options,
        python=Path("/opt/venv/bin/python"),
        logs_dir=tmp_path / "logs",
        results_dir=tmp_path / "results",
        partition="test-partition",
        scratch_template="/scratch/$USER/$SLURM_JOB_ID",
    )


def test_resources_default_to_one_cpu_and_two_gb() -> None:
    payload = cast(dict[str, Any], yaml.safe_load(FAKE.read_text(encoding="utf-8")))
    resources = payload["models"]["default"]["resources"]
    del resources["cpus"]
    del resources["memory_gb"]
    spec = select_model(parse_manifest(payload, source=FAKE), "default")
    assert spec.resources.cpus == 1 and spec.resources.memory_gb == 2


def test_module_load_for_the_venv_interpreter(
    roots: tuple[Path, Path, Scene], tmp_path: Path
) -> None:
    """The interpreter's module comes from the venv's pyvenv.cfg, not from a constant."""
    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True)
    python = venv / "bin" / "python"
    python.write_text("", encoding="utf-8")
    (venv / "pyvenv.cfg").write_text(
        "home = /software/python/3.13.14/bin\nversion = 3.13.14\n", encoding="utf-8"
    )
    assert interpreter_module(python) == "python/3.13.14"
    # The venv's bin/python is a symlink to the base interpreter, and the venv itself may be
    # reached through a symlink (the worktrees link .venv): both still find pyvenv.cfg.
    base = tmp_path / "software" / "python" / "3.13.14" / "bin"
    base.mkdir(parents=True)
    (base / "python3.13").write_text("", encoding="utf-8")
    python.unlink()
    python.symlink_to(base / "python3.13")
    assert interpreter_module(python) == "python/3.13.14"
    linked = tmp_path / "wt" / ".venv"
    linked.parent.mkdir()
    linked.symlink_to(venv)
    assert interpreter_module(linked / "bin" / "python") == "python/3.13.14"
    scenes, out, scene = roots
    manifest = _manifest(modules=["nvidia/cuda-11.8"])
    plan = plan_sbatch(
        manifest,
        select_model(manifest, "default"),
        scenes_root=scenes,
        scenes={"coop": scene},
        run_id="r1",
        out_root=out,
        options=RunOptions(),
        python=python,
        logs_dir=tmp_path / "logs",
        results_dir=tmp_path / "results",
        partition="test-partition",
        scratch_template="/scratch/$USER/$SLURM_JOB_ID",
    )
    lines = plan.script.splitlines()
    purge = lines.index("module purge")
    assert lines[purge + 1].startswith("module load python/3.13.14")
    assert lines[purge + 2] == "module load nvidia/cuda-11.8"
    # A version line alone is enough; no pyvenv.cfg means no module line.
    (venv / "pyvenv.cfg").write_text("version = 3.12.1\n", encoding="utf-8")
    assert interpreter_module(python) == "python/3.12.1"
    bare = tmp_path / "bare" / "bin" / "python"
    bare.parent.mkdir(parents=True)
    bare.write_text("", encoding="utf-8")
    assert interpreter_module(bare) is None
    plan = _plan(roots, manifest, tmp_path)  # /opt/venv/bin/python: no cfg there
    assert "module load python/" not in plan.script


def test_time_limit_griffin() -> None:
    assert time_limit(0.39, 700, 1490) == "01:00:00"  # 1281.1 s -> 1800 -> 3600


def test_time_limit_small() -> None:
    assert time_limit(0.01, 0, 4) == "00:20:00"


def test_gpu_script_lines(roots: tuple[Path, Path, Scene], tmp_path: Path) -> None:
    manifest = _manifest(
        gpu=True, cpus=8, memory_gb=64, slurm_constraint="a40|a100", modules=["nvidia/cuda-11.8"]
    )
    plan = _plan(roots, manifest, tmp_path)
    script = plan.script
    for needle in (
        "#SBATCH --partition=test-partition",
        "#SBATCH --gres=gpu:1",
        "#SBATCH --cpus-per-task=8",
        "#SBATCH --mem=64G",
        '#SBATCH --constraint="a40|a100"',
        "#SBATCH --mail-type=END,FAIL",
        "module purge",
        "module load nvidia/cuda-11.8",
        'SCRATCH="/scratch/$USER/$SLURM_JOB_ID"',
        "trap 'rm -rf \"$SCRATCH\"' EXIT",
        "--files-from=",
        "model run --manifest",
        '--scenes "$SCRATCH/scenes"',
        '--work "$SCRATCH/work"',
        '--out "$SCRATCH/out"',
        "--model default",
        "export PYTHONPATH=",
        "set +e",
        "RC=$?",
        "--include='*/' --include='*.log' --include='artifacts/***' "
        "--exclude='*' --prune-empty-dirs",
        'exit "$RC"',
    ):
        assert needle in script, needle
    assert "--slurm" not in script
    assert "pip " not in script and "pypi" not in script.lower()
    assert script.rstrip().endswith('exit "$RC"')
    assert "export PYTHONPATH=" + str(Path(__file__).resolve().parents[1]) in script
    assert plan.partition == "test-partition" and plan.gres == "gpu:1"
    assert plan.estimate_source.startswith("the manifest")


def test_cpu_script_has_no_gres(roots: tuple[Path, Path, Scene], tmp_path: Path) -> None:
    plan = _plan(roots, load_manifest(FAKE), tmp_path)
    assert "#SBATCH --partition=test-partition" in plan.script
    assert "--gres" not in plan.script and plan.gres is None
    assert "#SBATCH --cpus-per-task=1" in plan.script and "#SBATCH --mem=2G" in plan.script
    assert plan.cpus == 1 and plan.mem_gb == 2
    assert "#SBATCH --time=00:20:00" in plan.script  # 7 samples x 0.01 s -> 600 -> 1200


def test_sbatch_carries_no_environment_values(
    roots: tuple[Path, Path, Scene], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HF_TOKEN", "hunter22")
    manifest = _manifest(secrets=["HF_TOKEN"])
    plan = _plan(roots, manifest, tmp_path)
    assert "hunter22" not in plan.script
    lines = [line for line in plan.script.splitlines() if "HF_TOKEN" in line]
    assert lines == ["# secrets read from the job environment: HF_TOKEN"]


def test_staged_files_for_a_camera_model(roots: tuple[Path, Path, Scene]) -> None:
    scenes, _, scene = roots
    camera_only = select_model(load_manifest(FAKE), "known-answer")  # modalities [camera]
    files, raw = staged_files(
        scenes, {"coop": scene}, camera_only, raw_root=None, agent_ids={"coop": ("vehicle",)}
    )
    assert "coop/manifest.json" in files and "coop/annotations.parquet" in files
    assert "coop/points.bin" not in files and raw == []
    both = select_model(load_manifest(FAKE), "default")
    files, _ = staged_files(scenes, {"coop": scene}, both, raw_root=None, agent_ids={})
    assert "coop/points.bin" in files


def test_script_passes_bash_n(roots: tuple[Path, Path, Scene], tmp_path: Path) -> None:
    if shutil.which("bash") is None:
        pytest.skip("no bash")
    _, out, _ = roots
    plan = _plan(roots, load_manifest(FAKE), tmp_path, options={"extra": "true"})
    path = write_plan(plan, out, "r1")
    assert path == out / ".slurm" / "r1.sbatch"
    assert (out / ".slurm" / "r1.scenes.txt").read_text(encoding="utf-8").startswith("coop/")
    done = subprocess.run(["bash", "-n", str(path)], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


def test_slurm_flag_emits_without_running(
    roots: tuple[Path, Path, Scene], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    scenes, out, _ = roots
    main(
        [
            "model",
            "run",
            "--manifest",
            str(FAKE),
            "--scenes",
            str(scenes),
            "--out",
            str(out),
            "--run-id",
            "r1",
            "--slurm",
            "--slurm-partition",
            "test-partition",
            "--slurm-logs",
            str(tmp_path / "logs"),
            "--slurm-results",
            str(tmp_path / "results"),
            "--slurm-scratch-template",
            "/scratch2/$USER/$SLURM_JOB_ID",
        ]
    )
    text = capsys.readouterr().out
    assert str(out / ".slurm" / "r1.sbatch") in text
    assert "#SBATCH --partition=test-partition" in text
    assert 'SCRATCH="/scratch2/$USER/$SLURM_JOB_ID"' in text
    assert not (out / "r1").exists()  # nothing ran
    assert not (out / ".work").exists()


def test_submit_parses_job_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    sbatch = fake_bin / "sbatch"
    sbatch.write_text("#!/bin/sh\necho 'Submitted batch job 123'\n", encoding="utf-8")
    sbatch.chmod(sbatch.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}")
    script = tmp_path / "job.sbatch"
    script.write_text("#!/bin/bash\n", encoding="utf-8")
    assert submit(script) == "123"


def test_time_limit_formula_has_the_three_parts() -> None:
    # staging 700 + conversion 100 + 0.394 x 1,490 = 1,387 s -> 1,800 -> 3,600 s
    assert time_limit(0.394, 700, 1490, 100) == "01:00:00"
    # conversion counts: 700 + 0.5 x 1,000 = 1,200 s -> 1,200 -> doubled 2,400 s
    assert time_limit(0.5, 0, 1000, 700) == "00:40:00"
    assert time_limit(0.5, 0, 1000) == "00:20:00"  # 500 s -> 600 -> 1,200 s without it


def test_time_limit_prefers_the_last_runs_timings(
    roots: tuple[Path, Path, Scene], tmp_path: Path
) -> None:
    scenes, out, _ = roots
    manifest = load_manifest(FAKE)
    assert estimate_from_runs(out, "fake", "default") is None
    env = {"PATH": os.environ.get("PATH", "/usr/bin"), "HOME": os.environ.get("HOME", "/tmp")}
    run_batch(manifest, scenes, out, "earlier", RunOptions(), created_utc=CREATED, parent_env=env)
    meta = read_run_meta(out, "earlier")
    assert meta.provenance is not None
    # A legacy run (no inference_s: per_sample_s is the whole worker time) never counts.
    legacy = {"staging_s": 100.0, "per_sample_s": 0.479, "wall_s": 5.0}
    write_run(
        out,
        "earlier",
        replace(meta, provenance={**meta.provenance, "timings": legacy}),
        [],
        force=True,
    )
    assert estimate_from_runs(out, "fake", "default") is None
    plan = _plan(roots, manifest, tmp_path)
    assert plan.estimate_source.startswith("the manifest")
    # A run with the split counts: staging + conversion + per_sample x N.
    split = {
        "staging_s": 100.0,
        "conversion_s": 200.0,
        "inference_s": 70.0,
        "per_sample_s": 0.1,
        "wall_s": 300.0,
    }
    write_run(
        out,
        "earlier",
        replace(meta, provenance={**meta.provenance, "timings": split}),
        [],
        force=True,
    )
    assert estimate_from_runs(out, "fake", "default") == (0.1, 100.0, 200.0)
    plan = _plan(roots, manifest, tmp_path)
    # 100 + 200 + 7 x 0.1 = 300.7 s -> 600 -> 1,200 s
    assert "#SBATCH --time=00:20:00" in plan.script
    assert plan.estimate_source.startswith("provenance.timings")
    assert "100 s staging + 200 s conversion + 0.1 s/sample x 7 samples" in plan.script
    # The job times its own rsync and hands the wall time to the parent.
    assert "STAGE_START=$SECONDS" in plan.script
    assert "STAGING=$((SECONDS - STAGE_START))" in plan.script
    assert '--staging-seconds "$STAGING"' in plan.script


def _two(seconds_a: float, seconds_b: float, raw_root: Path) -> Manifest:
    """model-two.yaml with measurable costs and one raw file per model."""
    payload = cast(
        dict[str, Any],
        yaml.safe_load(FAKE.with_name("model-two.yaml").read_text(encoding="utf-8")),
    )
    payload["models"]["a"]["resources"]["seconds_per_sample"] = seconds_a
    payload["models"]["b"]["resources"]["seconds_per_sample"] = seconds_b
    payload["models"]["a"]["inputs"]["raw"] = ["x/from-a.json"]
    payload["models"]["b"]["inputs"]["raw"] = ["x/from-b.json"]
    (raw_root / "x").mkdir(parents=True, exist_ok=True)
    for name in ("from-a.json", "from-b.json"):
        (raw_root / "x" / name).write_text("{}", encoding="utf-8")
    return parse_manifest(payload, source=FAKE.with_name("model-two.yaml"))


def test_the_emitter_covers_the_dependency_closure(
    roots: tuple[Path, Path, Scene], tmp_path: Path
) -> None:
    """A model with depends_on stages its dependency's files, adds its time and keeps its
    transient artefacts from going home; --artifacts or --no-auto-dependencies drop the
    dependency from all three."""
    scenes, out, scene = roots
    raw = tmp_path / "raw"
    manifest = _two(100.0, 50.0, raw)
    spec = select_model(manifest, "b")
    assert [m.key for m in model_closure(manifest, spec, RunOptions())] == ["a", "b"]
    assert [m.key for m in model_closure(manifest, spec, RunOptions(artifacts_dir=tmp_path))] == [
        "b"
    ]
    assert [m.key for m in model_closure(manifest, spec, RunOptions(auto_dependencies=False))] == [
        "b"
    ]

    def plan(**kwargs: object):  # noqa: ANN202
        return plan_sbatch(
            manifest,
            spec,
            scenes_root=scenes,
            scenes={"coop": scene},
            run_id="r1",
            out_root=out,
            options=RunOptions(raw_root=raw, **kwargs),  # type: ignore[arg-type]
            python=Path("/opt/venv/bin/python"),
            logs_dir=tmp_path / "logs",
            results_dir=tmp_path / "results",
            partition="test-partition",
            scratch_template="/scratch/$USER/$SLURM_JOB_ID",
        )

    both = plan()
    assert set(both.raw_files) == {"x/from-a.json", "x/from-b.json"}
    assert "#SBATCH --time=00:40:00" in both.script  # (100 + 50) x 7 = 1,050 s -> 1,200 -> 2,400
    assert "summed over the models this run needs: a " in both.script
    excluded = "--exclude='artifacts/a/scratch/***'"
    assert excluded in both.script
    assert both.script.index(excluded) < both.script.index("--include='*/'")
    # The dependency is a run of its own and comes home beside the requested run.
    assert f'rsync -a "$SCRATCH/out/r1-a/" {out.resolve() / "r1-a"}/' in both.script
    alone = plan(artifacts_dir=tmp_path)
    assert set(alone.raw_files) == {"x/from-b.json"}
    assert "#SBATCH --time=00:20:00" in alone.script  # 50 x 7 = 350 s -> 600 -> 1,200
    assert "summed over" not in alone.script and "--exclude='artifacts/a/" not in alone.script
    assert "out/r1-a/" not in alone.script


def _rsync_filter_args(transient: list[tuple[str, str]]) -> list[str]:
    """The filter items of the emitted rsync line, unquoted as the shell would."""
    return [item.replace("'", "") for item in work_copy_filter(transient)]


def test_work_copy_filter_keeps_nested_logs_and_artifacts(tmp_path: Path) -> None:
    """rsync with the filter on a fake work tree: logs at any depth and artefacts come home,
    inputs and a toolkit copy do not, empty directories are pruned, and a transient artefact
    stays behind (it is 11 GB for Griffin's drone track queries)."""
    if shutil.which("rsync") is None:
        pytest.skip("no rsync")
    work = tmp_path / "work"
    for relative in (
        "parent.log",
        "plugin.log",
        "griffin-toolkit.log",
        "griffin-drone-side/work_dirs/drone-side/logs/test_0101.log",
        "artifacts/drone-side/det_json",
        "artifacts/drone-side/track_query/sample_1.pkl",
        "artifacts/drone-side/track_query/deeper/sample_2.pkl",
        "inputs/17/vehicle-lidar_top.bin",
        "griffin-drone-side/tools/test.py",
        "datasets/prefix/release/vehicle-side/pose/000001.json",
    ):
        target = work / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x", encoding="utf-8")
    (work / "empty").mkdir()

    def copied(transient: list[tuple[str, str]]) -> list[str]:
        dest = tmp_path / f"home-{len(transient)}"
        argv = ["rsync", "-a", *_rsync_filter_args(transient), f"{work}/", f"{dest}/"]
        done = subprocess.run(argv, capture_output=True, text=True)
        assert done.returncode == 0, done.stderr
        assert not (dest / "empty").exists() and not (dest / "inputs").exists()
        assert not (dest / "datasets").exists()
        return sorted(str(p.relative_to(dest)) for p in dest.rglob("*") if p.is_file())

    everything = copied([])
    assert everything == [
        "artifacts/drone-side/det_json",
        "artifacts/drone-side/track_query/deeper/sample_2.pkl",
        "artifacts/drone-side/track_query/sample_1.pkl",
        "griffin-drone-side/work_dirs/drone-side/logs/test_0101.log",
        "griffin-toolkit.log",
        "parent.log",
        "plugin.log",
    ]
    without = copied([("drone-side", "track_query")])
    assert without == [p for p in everything if "track_query" not in p]


def test_slurm_requires_a_partition_and_never_guesses_one(
    roots: tuple[Path, Path, Scene],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    scenes, out, _ = roots
    monkeypatch.delenv("COPERCEIVE_SLURM_PARTITION", raising=False)
    with pytest.raises(SystemExit):
        main(
            [
                "model",
                "run",
                "--manifest",
                str(FAKE),
                "--scenes",
                str(scenes),
                "--out",
                str(out),
                "--run-id",
                "r1",
                "--slurm",
                "--slurm-scratch-template",
                "/scratch/$USER/$SLURM_JOB_ID",
                "--slurm-logs",
                str(tmp_path / "logs"),
                "--slurm-results",
                str(tmp_path / "results"),
            ]
        )
    assert "Choose a Slurm partition" in capsys.readouterr().err
    assert not (out / ".slurm" / "r1.sbatch").exists()


def test_missing_scratch_template_is_a_plain_error(
    roots: tuple[Path, Path, Scene],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    scenes, out, _ = roots
    monkeypatch.delenv("COPERCEIVE_SLURM_SCRATCH_TEMPLATE", raising=False)
    with pytest.raises(SystemExit):
        main(
            [
                "model",
                "run",
                "--manifest",
                str(FAKE),
                "--scenes",
                str(scenes),
                "--out",
                str(out),
                "--run-id",
                "r1",
                "--slurm",
                "--slurm-partition",
                "test-partition",
                "--slurm-logs",
                str(tmp_path / "logs"),
                "--slurm-results",
                str(tmp_path / "results"),
            ]
        )
    captured = capsys.readouterr()
    assert "scratch_template" in captured.err + captured.out


def test_partition_can_come_from_the_profile_environment_variable(
    roots: tuple[Path, Path, Scene], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scenes, out, _ = roots
    monkeypatch.setenv("COPERCEIVE_SLURM_PARTITION", "profile-partition")
    main(
        [
            "model",
            "run",
            "--manifest",
            str(FAKE),
            "--scenes",
            str(scenes),
            "--out",
            str(out),
            "--run-id",
            "r1",
            "--slurm",
            "--slurm-scratch-template",
            "/scratch/$USER/$SLURM_JOB_ID",
            "--slurm-logs",
            str(tmp_path / "logs"),
            "--slurm-results",
            str(tmp_path / "results"),
        ]
    )
    text = (out / ".slurm" / "r1.sbatch").read_text(encoding="utf-8")
    assert "#SBATCH --partition=profile-partition" in text


def test_unsafe_or_shared_scratch_templates_are_refused() -> None:
    for template in ('/scratch/x"; rm -rf ~;"', "/scratch/fixed", "/scratch/$USER"):
        with pytest.raises(SlurmError, match=r"scratch template|\$SLURM_JOB_ID"):
            validate_scratch_template(template)
    assert validate_scratch_template("/scratch/$USER/$SLURM_JOB_ID")


def test_slurm_partition_characters_are_validated(
    roots: tuple[Path, Path, Scene],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    scenes, out, _ = roots
    for bad in ("ps;rm -rf /", "ps\nmain", "gpu a100", "gpu$(id)"):
        with pytest.raises(SystemExit):
            main(
                [
                    "model",
                    "run",
                    "--manifest",
                    str(FAKE),
                    "--scenes",
                    str(scenes),
                    "--out",
                    str(out),
                    "--run-id",
                    "r1",
                    "--slurm",
                    "--slurm-partition",
                    bad,
                    "--slurm-scratch-template",
                    "/scratch/$USER/$SLURM_JOB_ID",
                ]
            )
        captured = capsys.readouterr()
        assert "partition" in captured.err + captured.out
    for bad in ("bad name", "bad;name", "bad\nname", "/abs"):
        with pytest.raises(SlurmError):
            validate_partition(bad)
    assert validate_partition("gpu-a100") == "gpu-a100"
    assert validate_partition("cpu,main-gpu") == "cpu,main-gpu"
