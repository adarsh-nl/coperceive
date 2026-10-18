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
    estimate_from_runs,
    plan_sbatch,
    staged_files,
    submit,
    time_limit,
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
    for key in ("gpu", "memory_gb", "slurm_constraint", "seconds_per_sample", "staging_seconds"):
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
    )


def test_time_limit_griffin() -> None:
    assert time_limit(0.39, 700, 1490) == "01:00:00"  # 1281.1 s -> 1800 -> 3600


def test_time_limit_small() -> None:
    assert time_limit(0.01, 0, 4) == "00:20:00"


def test_gpu_script_lines(roots: tuple[Path, Path, Scene], tmp_path: Path) -> None:
    manifest = _manifest(
        gpu=True, memory_gb=64, slurm_constraint="a40|a100", modules=["nvidia/cuda-11.8"]
    )
    plan = _plan(roots, manifest, tmp_path)
    script = plan.script
    for needle in (
        "#SBATCH --partition=ps,main-gpu",
        "#SBATCH --gres=gpu:1",
        "#SBATCH --mem=64G",
        '#SBATCH --constraint="a40|a100"',
        "#SBATCH --mail-type=END,FAIL",
        "module purge",
        "module load nvidia/cuda-11.8",
        'SCRATCH="/local/$SLURM_JOB_ID"',
        "trap 'rm -rf \"$SCRATCH\"' EXIT",
        "--files-from=",
        "model run --manifest",
        '--scenes "$SCRATCH/scenes"',
        '--work "$SCRATCH/work"',
        '--out "$SCRATCH/out"',
        "--model default",
    ):
        assert needle in script, needle
    assert "--slurm" not in script
    assert "pip " not in script and "pypi" not in script.lower()
    assert plan.partition == "ps,main-gpu" and plan.gres == "gpu:1"
    assert plan.estimate_source.startswith("the manifest")


def test_cpu_script_has_no_gres(roots: tuple[Path, Path, Scene], tmp_path: Path) -> None:
    plan = _plan(roots, load_manifest(FAKE), tmp_path)
    assert "#SBATCH --partition=ps,main-cpu" in plan.script
    assert "--gres" not in plan.script and plan.gres is None
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
            "--slurm-logs",
            str(tmp_path / "logs"),
            "--slurm-results",
            str(tmp_path / "results"),
        ]
    )
    text = capsys.readouterr().out
    assert str(out / ".slurm" / "r1.sbatch") in text
    assert "#SBATCH --partition=ps,main-cpu" in text
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
    timings = {"staging_s": 100.0, "per_sample_s": 0.1, "wall_s": 5.0}
    write_run(
        out,
        "earlier",
        replace(meta, provenance={**meta.provenance, "timings": timings}),
        [],
        force=True,
    )
    assert estimate_from_runs(out, "fake", "default") == (0.1, 100.0)
    plan = _plan(roots, manifest, tmp_path)
    assert "#SBATCH --time=00:20:00" in plan.script  # 100 + 7 x 0.1 = 100.7 s -> 600 -> 1200
    assert plan.estimate_source.startswith("provenance.timings")
