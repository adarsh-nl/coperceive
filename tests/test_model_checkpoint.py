# SPDX-License-Identifier: Apache-2.0
"""A model whose checkpoint is a file named by one of its options (spec §Checkpoint staging)."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest
import yaml
from coperceive.cli import main
from coperceive.evaluation.predictions import read_run_meta
from coperceive.model import Scene
from coperceive.models.batch import (
    BatchError,
    RunOptions,
    resolve_checkpoint_option,
    run_batch,
)
from coperceive.models.slurm import (
    SlurmError,
    checkpoint_files,
    plan_sbatch,
    write_plan,
)
from coperceive.models.spec import (
    ModelSpecError,
    load_manifest,
    parse_manifest,
    render_argv,
    select_model,
)
from coperceive.store import write_scene

from tests.cooperative import cooperative_scene
from tests.fake_model_plugin import MANIFEST as FAKE

CHECKPOINT_MANIFEST = FAKE.with_name("model-checkpoint.yaml")
CREATED = "2026-10-18T12:00:00Z"


def _payload() -> dict[str, Any]:
    """The fake manifest with one model: a `{option:...}` command applies to every model."""
    payload = cast(dict[str, Any], yaml.safe_load(FAKE.read_text(encoding="utf-8")))
    payload["models"] = {"default": payload["models"]["default"]}
    return payload


def _checkpoint_payload() -> dict[str, Any]:
    """The fake manifest with the checkpoint option and key (tests/fake_model_plugin)."""
    return cast(dict[str, Any], yaml.safe_load(CHECKPOINT_MANIFEST.read_text(encoding="utf-8")))


def _parse(payload: dict[str, Any], source: Path = FAKE) -> Any:
    return parse_manifest(cast(object, payload), source=source)


def _env() -> dict[str, str]:
    return {"PATH": os.environ.get("PATH", "/usr/bin"), "HOME": os.environ.get("HOME", "/tmp")}


@pytest.fixture
def roots(tmp_path: Path) -> tuple[Path, Path, Scene]:
    scene = replace(cooperative_scene(), scene_id="coop")
    scenes = tmp_path / "scenes"
    write_scene(scene, scenes / "coop")
    return scenes, tmp_path / "predictions", scene


@pytest.fixture
def checkpoint(tmp_path: Path) -> Path:
    path = tmp_path / "cobevt_lidar.zip"
    path.write_bytes(b"PK\x03\x04 not really a zip; the fake worker only hashes it")
    return path


def _manifest() -> Any:
    return load_manifest(CHECKPOINT_MANIFEST)


# -- the manifest key and the argv placeholder ------------------------------------------------


def test_checkpoint_key_requires_a_declared_file_option() -> None:
    payload = _payload()
    payload["models"]["default"]["checkpoint"] = {"option": "nope"}
    with pytest.raises(ModelSpecError, match=r"checkpoint.option|no option 'nope'"):
        _parse(payload)

    payload = _payload()
    payload["models"]["default"]["options"]["checkpoint"] = {"type": "int", "default": 0}
    payload["models"]["default"]["checkpoint"] = {"option": "checkpoint"}
    with pytest.raises(ModelSpecError, match="must be str"):
        _parse(payload)


def test_checkpoint_key_rejects_an_unknown_nested_key() -> None:
    payload = _payload()
    payload["models"]["default"]["checkpoint"] = {"option": "checkpoint", "directory": True}
    with pytest.raises(ModelSpecError, match="checkpoint.directory"):
        _parse(payload)


def test_option_placeholder_must_be_an_option_of_every_model() -> None:
    payload = _payload()
    payload["commands"]["batch"] = [
        "{python}",
        "{plugin_dir}/worker.py",
        "--checkpoint",
        "{option:checkpoint}",
    ]
    with pytest.raises(ModelSpecError, match=r"option:checkpoint.*not an option"):
        _parse(payload)

    payload["models"]["default"]["options"]["checkpoint"] = {"type": "str", "default": ""}
    manifest = _parse(payload)
    assert manifest.commands.batch[-1] == "{option:checkpoint}"


def test_unknown_and_unbalanced_placeholders_are_refused() -> None:
    payload = _payload()
    payload["commands"]["batch"] = ["{python}", "{option:}", "{option:Bad}"]
    with pytest.raises(ModelSpecError, match="option names"):
        _parse(payload)

    payload = _payload()
    payload["commands"]["batch"] = ["{python}", "{wat}"]
    with pytest.raises(ModelSpecError, match="unknown placeholder"):
        _parse(payload)

    payload = _payload()
    payload["commands"]["batch"] = ["{python}", "{unclosed"]
    with pytest.raises(ModelSpecError, match="unbalanced braces"):
        _parse(payload)


def test_render_argv_renders_typed_option_values(tmp_path: Path) -> None:
    payload = _payload()
    payload["models"]["default"]["options"].update(
        {
            "checkpoint": {"type": "str", "default": ""},
            "level": {"type": "int", "default": 2},
            "ratio": {"type": "float", "default": 0.5},
            "half": {"type": "bool", "default": True},
        }
    )
    payload["commands"]["batch"] = [
        "{python}",
        "--checkpoint",
        "{option:checkpoint}",
        "--level",
        "{option:level}",
        "--ratio",
        "{option:ratio}",
        "--half",
        "{option:half}",
        "--model",
        "{model}",
    ]
    manifest = _parse(payload)
    argv = render_argv(
        manifest,
        "batch",
        python=Path("/usr/bin/python"),
        plugin_dir=tmp_path,
        work_dir=tmp_path / "work",
        model="default",
        options={"checkpoint": "/data/ckpt.zip", "level": 2, "ratio": 0.5, "half": True},
    )
    # One value per argv element: a path with a space or a quote is not split or quoted.
    assert argv == [
        "/usr/bin/python",
        "--checkpoint",
        "/data/ckpt.zip",
        "--level",
        "2",
        "--ratio",
        "0.5",
        "--half",
        "true",
        "--model",
        "default",
    ]
    with pytest.raises(ModelSpecError, match="no value"):
        render_argv(
            manifest,
            "batch",
            python=Path("/usr/bin/python"),
            plugin_dir=tmp_path,
            work_dir=tmp_path / "work",
            model="default",
        )


# -- `model run` refuses a run without a checkpoint -------------------------------------------


def test_model_run_requires_the_checkpoint_option(
    roots: tuple[Path, Path, Scene], capsys: pytest.CaptureFixture[str]
) -> None:
    scenes, out, _ = roots
    for value in (None, "{tmp}/missing.zip"):
        args = [
            "model",
            "run",
            "--manifest",
            str(CHECKPOINT_MANIFEST),
            "--scenes",
            str(scenes),
            "--out",
            str(out),
            "--run-id",
            "r1",
        ]
        if value is not None:
            args.extend(["--option", f"checkpoint={value.format(tmp=out.parent)}"])
        with pytest.raises(SystemExit) as caught:
            main(args)
        assert caught.value.code == 1
    captured = capsys.readouterr()
    assert "--option checkpoint=FILE" in captured.err + captured.out


def test_resolve_checkpoint_option_makes_it_absolute(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The helper both check and run use: relative -> absolute, in place; None without a key."""
    monkeypatch.chdir(tmp_path)
    archive = tmp_path / "ck.zip"
    archive.write_bytes(b"x")
    with_checkpoint = select_model(load_manifest(CHECKPOINT_MANIFEST), None)
    effective: dict[str, object] = {"checkpoint": "ck.zip"}
    assert resolve_checkpoint_option(with_checkpoint, effective) == str(archive.resolve())
    assert effective["checkpoint"] == str(archive.resolve())
    without = select_model(load_manifest(FAKE), None)
    assert resolve_checkpoint_option(without, {}) is None


def test_model_run_hashes_the_checkpoint_file(
    roots: tuple[Path, Path, Scene], checkpoint: Path
) -> None:
    scenes, out, _ = roots
    report = run_batch(
        _manifest(),
        scenes,
        out,
        "r1",
        RunOptions(options={"checkpoint": str(checkpoint)}),
        created_utc=CREATED,
        parent_env=_env(),
    )
    assert report.status == "complete"
    meta = read_run_meta(out, "r1")
    assert meta.provenance is not None
    record = cast(Mapping[str, Any], meta.provenance["checkpoint"])
    digest = hashlib.md5(checkpoint.read_bytes()).hexdigest()  # noqa: S324 - the protocol's digest
    assert record["path"] == str(checkpoint.resolve())
    assert record["md5"] == digest
    environment = cast(Mapping[str, str], meta.provenance["environment"])
    assert environment["checkpoint_md5"] == digest
    assert environment["checkpoint_path"] == str(checkpoint.resolve())
    assert (out / "r1" / "cache" / "fake" / "default" / digest / "coop").is_dir()


def test_checkpoint_must_exist_before_the_child_starts(
    roots: tuple[Path, Path, Scene], tmp_path: Path
) -> None:
    scenes, out, _ = roots
    with pytest.raises(BatchError, match="is not a file"):
        run_batch(
            _manifest(),
            scenes,
            out,
            "r1",
            RunOptions(options={"checkpoint": str(tmp_path / "nope.zip")}),
            created_utc=CREATED,
            parent_env=_env(),
        )


def test_relative_checkpoint_option_is_absolute_before_the_child(
    roots: tuple[Path, Path, Scene], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The child's cwd is its work dir, so the parent resolves the path it passes in argv."""
    scenes, out, _ = roots
    checkpoint = tmp_path / "relative-ckpt.zip"
    checkpoint.write_bytes(b"PK\x03\x04 a fake archive the fake worker only hashes")
    monkeypatch.chdir(tmp_path)
    report = run_batch(
        _manifest(),
        scenes,
        out,
        "r1",
        RunOptions(options={"checkpoint": checkpoint.name}),
        created_utc=CREATED,
        parent_env=_env(),
    )
    assert report.status == "complete"
    meta = read_run_meta(out, "r1")
    assert meta.provenance is not None
    record = cast(Mapping[str, Any], meta.provenance["checkpoint"])
    assert record["path"] == str(checkpoint.resolve())
    options = cast(Mapping[str, object], meta.provenance["options"])
    assert options["checkpoint"] == str(checkpoint.resolve())


def test_a_declared_checkpoint_requires_hello_checkpoint_path(
    roots: tuple[Path, Path, Scene], tmp_path: Path
) -> None:
    """A hello without checkpoint_path would bypass digest verification: protocol violation."""
    scenes, out, _ = roots
    plugin = tmp_path / "plugin"
    plugin.mkdir()
    (plugin / "silent.py").write_text(
        "import json, sys\n"
        "print(json.dumps({\n"
        '    "type": "hello", "protocol": 1, "plugin": "fake", "version": "0.0.1",\n'
        '    "models": ["default"],\n'
        '    "capabilities": {"live": False, "cancel": False, "max_inflight": 1},\n'
        '    "environment": {"checkpoint_md5": "0" * 32},\n'
        "}), flush=True)\n"
        "sys.stdin.read()\n",
        encoding="utf-8",
    )
    checkpoint = tmp_path / "declared.zip"
    checkpoint.write_bytes(b"PK\x03\x04 a fake archive the silent worker never opens")
    payload = _checkpoint_payload()
    payload["environment"] = {"current": True}
    payload["commands"]["batch"] = [
        "{python}",
        "{plugin_dir}/silent.py",
        "--model",
        "{model}",
        "--checkpoint",
        "{option:checkpoint}",
    ]
    manifest_path = plugin / "model.yaml"
    manifest_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    report = run_batch(
        load_manifest(manifest_path),
        scenes,
        out,
        "r1",
        RunOptions(options={"checkpoint": str(checkpoint)}),
        created_utc=CREATED,
        parent_env=_env(),
    )
    assert report.status == "partial"
    assert report.reason is not None and "checkpoint_path is required" in report.reason
    assert report.exit_code == 3


def test_hello_checkpoint_path_must_be_the_declared_file(
    roots: tuple[Path, Path, Scene], tmp_path: Path
) -> None:
    """A child that hashes a different file than --option names is a protocol violation."""
    scenes, out, _ = roots
    plugin = tmp_path / "plugin"
    plugin.mkdir()
    other = tmp_path / "other.zip"
    other.write_bytes(b"PK\x03\x04 another file")
    declared = tmp_path / "declared.zip"
    declared.write_bytes(b"PK\x03\x04 the declared file")
    other_digest = hashlib.md5(other.read_bytes()).hexdigest()  # noqa: S324 - the protocol digest
    hello = {
        "type": "hello",
        "protocol": 1,
        "plugin": "fake",
        "version": "0.0.1",
        "models": ["default"],
        "capabilities": {"live": False, "cancel": False, "max_inflight": 1},
        "environment": {"checkpoint_md5": other_digest, "checkpoint_path": str(other)},
    }
    (plugin / "wrong.py").write_text(
        f"import json, sys\nprint(json.dumps({hello!r}), flush=True)\nsys.stdin.read()\n",
        encoding="utf-8",
    )
    payload = _checkpoint_payload()
    payload["environment"] = {"current": True}
    payload["commands"]["batch"] = [
        "{python}",
        "{plugin_dir}/wrong.py",
        "--model",
        "{model}",
        "--checkpoint",
        "{option:checkpoint}",
    ]
    manifest_path = plugin / "model.yaml"
    manifest_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    report = run_batch(
        load_manifest(manifest_path),
        scenes,
        out,
        "r1",
        RunOptions(options={"checkpoint": str(declared)}),
        created_utc=CREATED,
        parent_env=_env(),
    )
    assert report.status == "partial"
    assert report.reason is not None and "hello names checkpoint" in report.reason


# -- `--slurm` staging ------------------------------------------------------------------------


def _plan(roots: tuple[Path, Path, Scene], checkpoint: Path, tmp_path: Path) -> Any:
    scenes, out, scene = roots
    manifest = _manifest()
    return plan_sbatch(
        manifest,
        select_model(manifest, "default"),
        scenes_root=scenes,
        scenes={"coop": scene},
        run_id="r1",
        out_root=out,
        options=RunOptions(options={"checkpoint": str(checkpoint)}),
        python=Path("/opt/venv/bin/python"),
        logs_dir=tmp_path / "logs",
        results_dir=tmp_path / "results",
        partition="test-partition",
        scratch_template="/scratch/$USER/$SLURM_JOB_ID",
    )


def test_slurm_stages_the_checkpoint_and_rewrites_the_option(
    roots: tuple[Path, Path, Scene], checkpoint: Path, tmp_path: Path
) -> None:
    plan = _plan(roots, checkpoint, tmp_path)
    staged = f"$SCRATCH/checkpoints/checkpoint/{checkpoint.name}"
    assert plan.checkpoints == (("checkpoint", str(checkpoint.resolve()), staged),)
    script = plan.script
    # Staged with rsync before the run, into the job's scratch.
    stage_line = f'rsync -a {checkpoint.resolve()} "$SCRATCH/checkpoints/checkpoint/"'
    assert stage_line in script
    assert 'mkdir -p "$SCRATCH/checkpoints/checkpoint"' in script
    assert script.index(stage_line) < script.index("STAGING=$((SECONDS - STAGE_START))")
    # The inner command reads the staged copy, not the shared path.
    command = " ".join(plan.command)
    assert f'--option "checkpoint={staged}"' in command
    assert str(checkpoint.resolve()) not in command
    assert script.index(stage_line) < script.index(" ".join(plan.command))
    # Never a copy-home target: the checkpoint appears only in the staging lines.
    assert script.count(str(checkpoint.resolve())) == 1
    assert "--option" in script and "model run" in script
    assert "pip " not in script and "pypi" not in script.lower()
    if shutil.which("bash") is not None:
        path = write_plan(plan, roots[1], "r1")
        done = subprocess.run(["bash", "-n", str(path)], capture_output=True, text=True)
        assert done.returncode == 0, done.stderr


def test_slurm_refuses_an_empty_checkpoint_option(
    roots: tuple[Path, Path, Scene], tmp_path: Path
) -> None:
    scenes, out, scene = roots
    manifest = _manifest()
    with pytest.raises(SlurmError, match="--option checkpoint=FILE"):
        plan_sbatch(
            manifest,
            select_model(manifest, "default"),
            scenes_root=scenes,
            scenes={"coop": scene},
            run_id="r1",
            out_root=out,
            options=RunOptions(),
            python=Path("/opt/venv/bin/python"),
            logs_dir=tmp_path / "logs",
            results_dir=tmp_path / "results",
            partition="test-partition",
            scratch_template="/scratch/$USER/$SLURM_JOB_ID",
        )


def test_checkpoint_files_is_empty_for_a_model_without_the_key(tmp_path: Path) -> None:
    manifest = load_manifest(FAKE)
    spec = select_model(manifest, None)
    assert checkpoint_files(manifest, spec, RunOptions()) == ()
