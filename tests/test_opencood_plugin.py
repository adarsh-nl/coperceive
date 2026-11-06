# SPDX-License-Identifier: Apache-2.0
"""The OpenCOOD plugin: manifest, worker chain through the real runner, and its Slurm job."""

from __future__ import annotations

import ast
import hashlib
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest
from coperceive.cli import main
from coperceive.evaluation.predictions import BoxFrame, read_boxes, read_run_meta
from coperceive.models.batch import RunOptions
from coperceive.models.slurm import plan_sbatch, write_plan
from coperceive.models.spec import load_manifest, render_argv, select_model
from coperceive.store import write_scene

from tests.cooperative import cooperative_scene
from tests.opencood_plugin import (
    FAKE_OPENCOOD_PARENT,
    MANIFEST,
    PLUGIN_DIR,
    copy_plugin_for_tests,
    load,
    torch_weights_bytes,
    write_checkpoint,
)

MODEL = "cobevt-lidar-v2xset"
PREDICTIONS = "opencood-fake"
SCENE_ID = "coop"


# -- the package and its manifest ------------------------------------------------------------


def test_manifest_declares_the_first_model_and_its_checkpoint_option() -> None:
    manifest = load_manifest(MANIFEST)
    assert manifest.name == "opencood" and manifest.version == "0.1.0"
    assert manifest.environment.kind == "conda"
    assert manifest.environment.value == "opencood"
    spec = select_model(manifest, None)
    assert spec.key == MODEL
    assert spec.checkpoint is not None and spec.checkpoint.option == "checkpoint"
    assert spec.options["checkpoint"].type == "str"
    assert spec.options["checkpoint"].default == ""
    assert spec.inputs.modalities == ("lidar",)
    assert spec.inputs.agents == ("vehicle", "infrastructure")
    assert spec.inputs.frames == "single"
    assert spec.outputs.categories == ("vehicle",)
    assert spec.outputs.agent == "vehicle"
    assert spec.resources.gpu is True
    assert spec.resources.max_inflight == 1
    argv = render_argv(
        manifest,
        "batch",
        python=Path("/usr/bin/python"),
        plugin_dir=PLUGIN_DIR,
        work_dir=Path("/tmp/work"),
        model=MODEL,
        options={"checkpoint": "/data/cobevt_lidar.zip"},
    )
    assert argv[-1] == "/data/cobevt_lidar.zip"
    assert argv[argv.index("--checkpoint") + 1] == "/data/cobevt_lidar.zip"


def test_manifest_states_the_open_cood_licence_truthfully() -> None:
    """Upstream OpenCOOD is the UCLA Academic Software License, not Apache-2.0."""
    manifest = load_manifest(MANIFEST)
    notice = manifest.licence.notice
    assert manifest.licence.spdx == "Apache-2.0"  # the plugin's own code only
    assert "OpenCOOD is NOT Apache-2.0" in notice
    assert "UCLA" in notice and "TDG-Attribution-NonCommercial-NoDistrib" in notice
    assert "github.com/DerrickXuNu/OpenCOOD/blob/main/LICENSE" in notice
    assert "checkpoint" in notice.lower() and "licence review" in notice


def test_plugin_code_imports_neither_coperceive_nor_opencood_statically() -> None:
    """The worker speaks the stdio protocol and imports OpenCOOD dynamically (opencood_engine)."""
    for path in sorted(PLUGIN_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".")[0]
                    assert root not in ("coperceive", "opencood"), f"{path.name}: {alias.name}"
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                # The real package is ``opencood``; the plugin's own siblings are
                # ``opencood_convert``, ``opencood_engine``, … and are allowed.
                assert module != "opencood" and not module.startswith(
                    ("coperceive.", "opencood.")
                ), f"{path.name}: from {module}"
    # No vendored OpenCOOD package and no cluster path anywhere in the plugin.
    assert not list(PLUGIN_DIR.rglob("opencood/__init__.py"))
    for path in [*PLUGIN_DIR.glob("*.py"), MANIFEST, PLUGIN_DIR.parent / "README.md"]:
        text = path.read_text(encoding="utf-8")
        assert "/datasets/" not in text, path.name
        assert "eemcs" not in text, path.name


def test_frame_seed_is_stable_per_scene_and_time() -> None:
    """Two model check passes reseed the same shuffle; different frames do not collide."""
    engine = load("engine")
    assert engine.frame_seed("coop", 100_000) == engine.frame_seed("coop", 100_000)
    assert engine.frame_seed("coop", 100_000) != engine.frame_seed("coop", 100_001)
    assert engine.frame_seed("coop", 100_000) != engine.frame_seed("other", 100_000)
    assert 0 <= engine.frame_seed("", 0) < 2**32


def test_worker_without_a_checkpoint_is_a_clean_skip_in_model_check(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Conformance for the real OpenCOOD needs a checkpoint; without one the model skips."""
    manifest = copy_plugin_for_tests(tmp_path)
    main(["model", "check", "--manifest", str(manifest)])
    out = capsys.readouterr().out
    assert "check opencood:" in out
    assert " 0 fail" in out
    assert "skipped" in out
    assert "--option checkpoint=FILE is required" in out  # the worker's actionable message
    assert "Traceback" not in out


# -- the full chain with the fake OpenCOOD ---------------------------------------------------


def test_model_run_eval_run_chain_with_the_fake_open_cood(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pytest.importorskip("torch")
    plugin_manifest = copy_plugin_for_tests(tmp_path)
    checkpoint = write_checkpoint(tmp_path / "cobevt_lidar.zip", weights=torch_weights_bytes())
    scene = replace(cooperative_scene(), scene_id=SCENE_ID)
    scenes = tmp_path / "scenes"
    write_scene(scene, scenes / SCENE_ID)
    predictions = tmp_path / "predictions"

    main(
        [
            "model",
            "run",
            "--manifest",
            str(plugin_manifest),
            "--scenes",
            str(scenes),
            "--out",
            str(predictions),
            "--run-id",
            PREDICTIONS,
            "--option",
            f"checkpoint={checkpoint}",
        ]
    )
    rows = read_boxes(predictions, PREDICTIONS, SCENE_ID)
    assert len(rows) == 1
    box = rows[0]
    # The fake OpenCOOD predicts a box at CARLA-ego (8, 2, -1.5), yaw 90, size lwh
    # (4, 2, 1.5), score 0.9; the ego is at schema world (10, 0, 0) heading 0 at 100_000 us.
    assert box.center == pytest.approx((18.0, -2.0, -1.5), abs=1e-9)
    assert box.size_lwh == pytest.approx((4.0, 2.0, 1.5), abs=1e-9)
    assert box.box_from_rpy.qw == pytest.approx(2**-0.5, abs=1e-9)  # schema yaw -90
    assert box.box_from_rpy.qz == pytest.approx(-(2**-0.5), abs=1e-9)
    assert box.category == "vehicle" and box.source == "fused"
    assert box.agent_id == "vehicle" and box.time_us == 100_000
    assert box.score == pytest.approx(0.9)
    assert box.frame is BoxFrame.WORLD

    meta = read_run_meta(predictions, PREDICTIONS)
    provenance = meta.provenance
    assert provenance is not None
    assert provenance["plugin"] == "opencood"
    assert provenance["model"] == MODEL
    assert provenance["status"] == "complete"
    environment = cast(dict[str, str], provenance["environment"])
    assert environment["checkpoint_md5"] == hashlib.md5(checkpoint.read_bytes()).hexdigest()  # noqa: S324
    assert environment["checkpoint_path"] == str(checkpoint.resolve())
    assert str(FAKE_OPENCOOD_PARENT) in environment["opencood_path"]
    record = cast(dict[str, object], provenance["checkpoint"])
    assert record["md5"] == environment["checkpoint_md5"]
    cache = (
        predictions
        / PREDICTIONS
        / "cache"
        / "opencood"
        / MODEL
        / environment["checkpoint_md5"]
        / SCENE_ID
    )
    assert (cache / "result.json").is_file()
    # The extracted checkpoint is a temporary tree: nothing large is left in the work dir.
    work_dir = predictions / ".work" / PREDICTIONS
    leftovers = [path.name for path in work_dir.iterdir() if "checkpoint" in path.name]
    assert leftovers == []
    log = (work_dir / "plugin.log").read_text(encoding="utf-8")
    assert "cobevt-lidar/config.yaml" in log
    assert "cobevt-lidar/net_epoch_bestval_at_10.pth" in log

    # The run is an ordinary predictions run: eval reads it unchanged.
    capsys.readouterr()
    main(
        [
            "eval",
            "run",
            PREDICTIONS,
            "--root",
            str(scenes),
            "--predictions",
            str(predictions),
        ]
    )
    out = capsys.readouterr().out
    # The synthetic ground truth has a pedestrian, which this run's categories do not; a
    # warning is fine, the evaluation must still score the scene.
    assert "evaluated 1 skipped 0 errors 0" in out


# -- Slurm -----------------------------------------------------------------------------------


def _plan(tmp_path: Path, checkpoint: Path):  # noqa: ANN202 - an slurm plan object
    scene = replace(cooperative_scene(), scene_id=SCENE_ID)
    scenes = tmp_path / "scenes"
    write_scene(scene, scenes / SCENE_ID)
    manifest = load_manifest(MANIFEST)
    return plan_sbatch(
        manifest,
        select_model(manifest, MODEL),
        scenes_root=scenes,
        scenes={SCENE_ID: scene},
        run_id="r1",
        out_root=tmp_path / "predictions",
        options=RunOptions(options={"checkpoint": str(checkpoint)}),
        python=Path("/opt/venv/bin/python"),
        logs_dir=tmp_path / "logs",
        results_dir=tmp_path / "results",
        partition="test-partition",
        scratch_template="/scratch/$USER/$SLURM_JOB_ID",
    )


def test_slurm_golden_stages_the_checkpoint_and_rewrites_the_option(tmp_path: Path) -> None:
    checkpoint = write_checkpoint(tmp_path / "cobevt_lidar.zip")
    plan = _plan(tmp_path, checkpoint)
    staged = f"$SCRATCH/checkpoints/checkpoint/{checkpoint.name}"
    assert plan.checkpoints == (("checkpoint", str(checkpoint.resolve()), staged),)
    script = plan.script
    # GPU job for the plugin, with the manifest's resources and no hard-coded cluster path.
    for needle in (
        "#SBATCH --partition=test-partition",
        "#SBATCH --gres=gpu:1",
        "#SBATCH --cpus-per-task=8",
        "#SBATCH --mem=16G",
        "#SBATCH --time=",
        'SCRATCH="/scratch/$USER/$SLURM_JOB_ID"',
        "module purge",
        "export PYTHONPATH=",
        "--manifest",
        f"--model {MODEL}",
        '--scenes "$SCRATCH/scenes"',
        '--out "$SCRATCH/out"',
        '--work "$SCRATCH/work"',
        "STAGING=$((SECONDS - STAGE_START))",
        '--staging-seconds "$STAGING"',
    ):
        assert needle in script, needle
    # The checkpoint is staged before the run, and the inner command reads the staged copy.
    stage_line = f'rsync -a {checkpoint.resolve()} "$SCRATCH/checkpoints/checkpoint/"'
    assert stage_line in script
    command = " ".join(plan.command)
    assert f'--option "checkpoint={staged}"' in command
    assert str(checkpoint.resolve()) not in command
    # Staged inside the timed rsync section, before the inner model run starts.
    assert script.index("STAGE_START=$SECONDS") < script.index(stage_line)
    assert script.index(stage_line) < script.index(" ".join(plan.command))
    # Never staged back home: only the staging lines mention the source path.
    assert script.count(str(checkpoint.resolve())) == 1
    copy_home = [line for line in script.splitlines() if 'rsync -a "$SCRATCH/out/' in line]
    assert copy_home and all("checkpoints" not in line for line in copy_home)
    # The scenes list is the lidar plugin's: points.bin travels, camera bytes never exist.
    sbatch_path = write_plan(plan, tmp_path / "predictions", "r1")
    scenes_list = sbatch_path.with_suffix(".scenes.txt")
    staged_files = scenes_list.read_text(encoding="utf-8").split()
    assert f"{SCENE_ID}/points.bin" in staged_files
    assert f"{SCENE_ID}/manifest.json" in staged_files
    assert not any(name.endswith(".png") for name in staged_files)
    if shutil.which("bash") is not None:
        done = subprocess.run(["bash", "-n", str(sbatch_path)], capture_output=True, text=True)
        assert done.returncode == 0, done.stderr


def test_slurm_run_requires_the_checkpoint_option(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    scene = replace(cooperative_scene(), scene_id=SCENE_ID)
    scenes = tmp_path / "scenes"
    write_scene(scene, scenes / SCENE_ID)
    with pytest.raises(SystemExit) as caught:
        main(
            [
                "model",
                "run",
                "--manifest",
                str(MANIFEST),
                "--scenes",
                str(scenes),
                "--out",
                str(tmp_path / "predictions"),
                "--run-id",
                "r1",
                "--slurm",
                "--slurm-partition",
                "test-partition",
                "--slurm-scratch-template",
                "/scratch/$USER/$SLURM_JOB_ID",
            ]
        )
    assert caught.value.code == 1
    captured = capsys.readouterr()
    assert "--option checkpoint=FILE" in captured.err + captured.out
    assert not (tmp_path / "predictions" / ".slurm" / "r1.sbatch").exists()
