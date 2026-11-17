# SPDX-License-Identifier: Apache-2.0
"""The post-release Griffin vehicle-single dry run: no submit, no real cluster."""

from __future__ import annotations

import contextlib
import io
import shutil
import subprocess
from pathlib import Path

import pytest
from coperceive.cli import main
from coperceive.model import Agent, AgentType, Clock, Modality, Scene, Sensor, StampedPose
from coperceive.se3 import SE3
from coperceive.store import write_scene

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "eval_pipeline"
REPO = Path(__file__).resolve().parents[1]
RUN_ID = "griffin25m-vehicle-plugin"


def _build_fixture(root: Path) -> tuple[Path, Path, Path]:
    scenes = root / "scenes"
    raw = root / "raw"
    out = root / "predictions"
    raw.mkdir(parents=True)
    # The committed raw list says exactly which release paths the plan stages.
    for line in (FIXTURE / "griffin-vehicle.raw.txt").read_text().splitlines():
        path = raw / line
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fixture\n")
    poses = tuple(
        StampedPose(i * 100_000, SE3.identity().translation(float(i), 0.0, 0.0)) for i in range(149)
    )
    sensors = tuple(
        Sensor(
            s, "vehicle", Modality.CAMERA, SE3.identity(), 1600, 900, 1000.0, 1000.0, 800.0, 450.0
        )
        for s in ("front", "back", "left", "right")
    )
    for index in range(10):
        scene_id = f"val{index:02d}"
        scene = Scene(
            1,
            scene_id,
            "ENU",
            Clock("us", "griffin val fixture"),
            (Agent("vehicle", AgentType.VEHICLE, poses),),
            sensors,
            (),
            (),
            (),
        )
        write_scene(scene, scenes / scene_id)
    return scenes, raw, out


def _normalise(text: str, root: Path) -> str:
    for value in (str(root.resolve()), str(root)):
        text = text.replace(value, "<FIXTURE>")
    for value in (str(REPO.resolve()), str(REPO)):
        text = text.replace(value, "<REPO>")
    text = text.replace("/home/tester", "<HOME>")
    return text.replace("/System/Volumes/Data<HOME>", "<HOME>").replace(
        "/private<FIXTURE>", "<FIXTURE>"
    )


def test_griffin_vehicle_sbatch_matches_the_golden(tmp_path: Path) -> None:
    scenes, raw, out = _build_fixture(tmp_path)
    args = [
        "model",
        "run",
        "--manifest",
        str(REPO / "plugins/griffin/coperceive_griffin_model/model.yaml"),
        "--model",
        "vehicle-side",
        "--scenes",
        str(scenes),
        "--raw-root",
        str(raw),
        "--media-root",
        str(raw),
        "--out",
        str(out),
        "--run-id",
        RUN_ID,
        "--slurm",
        "--slurm-python",
        "/opt/venv/bin/python",
        "--slurm-partition",
        "gpu-a40",
        "--slurm-results",
        "/home/tester/results",
        "--slurm-logs",
        "/home/tester/logs",
        "--slurm-scratch-template",
        "/scratch/$USER/$SLURM_JOB_ID",
    ]
    captured = io.StringIO()
    with contextlib.redirect_stdout(captured):
        main(args)
    output = captured.getvalue()
    start = output.index("#!/bin/bash")
    normalized = _normalise(output[start:], tmp_path)
    assert normalized == (FIXTURE / "griffin-vehicle.sbatch").read_text()
    for suffix in ("scenes.txt", "raw.txt"):
        generated = (out / ".slurm" / f"{RUN_ID}.{suffix}").read_text()
        assert generated == (FIXTURE / f"griffin-vehicle.{suffix}").read_text()
    subprocess.run(["bash", "-n", str(out / ".slurm" / f"{RUN_ID}.sbatch")], check=True)
    shellcheck = shutil.which("shellcheck")
    if shellcheck is not None:
        result = subprocess.run(
            [shellcheck, str(out / ".slurm" / f"{RUN_ID}.sbatch")],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stdout + result.stderr


def test_documented_eval_config_matches_the_expected_table() -> None:
    from coperceive.evaluation.config import load_config

    config = load_config(FIXTURE / "griffin-eval.yaml")
    assert config.max_range_m == 50.0
    assert config.score_threshold == 0.4


def test_a_shell_shaped_run_id_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    scenes, raw, out = _build_fixture(tmp_path)
    with pytest.raises(SystemExit) as caught:
        main(
            [
                "model",
                "run",
                "--manifest",
                str(REPO / "plugins/griffin/coperceive_griffin_model/model.yaml"),
                "--model",
                "vehicle-side",
                "--scenes",
                str(scenes),
                "--raw-root",
                str(raw),
                "--media-root",
                str(raw),
                "--out",
                str(out),
                "--run-id",
                'x"$(id)',
                "--slurm",
                "--slurm-python",
                "/opt/venv/bin/python",
                "--slurm-partition",
                "gpu-a40",
                "--slurm-results",
                "/home/tester/results",
                "--slurm-logs",
                "/home/tester/logs",
                "--slurm-scratch-template",
                "/scratch/$USER/$SLURM_JOB_ID",
            ]
        )
    assert caught.value.code == 1
    assert "run id" in capsys.readouterr().err


def test_dry_run_never_submits_and_never_uses_a_real_profile(tmp_path: Path) -> None:
    scenes, raw, out = _build_fixture(tmp_path)
    captured = io.StringIO()
    with contextlib.redirect_stdout(captured):
        main(
            [
                "model",
                "run",
                "--manifest",
                str(REPO / "plugins/griffin/coperceive_griffin_model/model.yaml"),
                "--model",
                "vehicle-side",
                "--scenes",
                str(scenes),
                "--raw-root",
                str(raw),
                "--media-root",
                str(raw),
                "--out",
                str(out),
                "--run-id",
                RUN_ID,
                "--slurm",
                "--slurm-python",
                "/opt/venv/bin/python",
                "--slurm-partition",
                "gpu-a40",
                "--slurm-results",
                "/home/tester/results",
                "--slurm-logs",
                "/home/tester/logs",
                "--slurm-scratch-template",
                "/scratch/$USER/$SLURM_JOB_ID",
            ]
        )
    assert "sbatch" not in captured.getvalue().splitlines()[-1]
    assert "job " not in captured.getvalue()
    assert (out / ".slurm" / f"{RUN_ID}.sbatch").is_file()
