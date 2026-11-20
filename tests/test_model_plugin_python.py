# SPDX-License-Identifier: Apache-2.0
"""``--plugin-python``: the plugin interpreter override of ``model run``, ``check`` and
``inspect`` (docs/models.md, "Plugin interpreter").

The manifest's ``environment`` stays the default. The override is a full interpreter path,
checked (an executable file) before anything runs or is submitted, passed to a Slurm job
exactly as given, and recorded in ``run.json`` provenance.
"""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import pytest
from coperceive.cli import main
from coperceive.evaluation.predictions import read_run_meta
from coperceive.models import slurm
from coperceive.models.runner import (
    PLUGIN_PYTHON_FLAG,
    plugin_interpreter,
    resolve_interpreter,
)
from coperceive.models.spec import Environment, ModelSpecError
from coperceive.store import write_scene

from tests.cooperative import cooperative_scene
from tests.fake_model_plugin import MANIFEST as FAKE

PYTHON = Path(sys.executable)


@pytest.fixture
def scenes(tmp_path: Path) -> Path:
    root = tmp_path / "scenes"
    write_scene(replace(cooperative_scene(), scene_id="coop"), root / "coop")
    return root


def _run(scenes: Path, out: Path, *extra: str) -> list[str]:
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
        "r1",
        *extra,
    ]


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


def _executable(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n", encoding="utf-8")
    path.chmod(0o755)
    return path


# --- resolution -------------------------------------------------------------------------------


def test_without_an_override_the_manifest_decides_as_before(tmp_path: Path) -> None:
    current = Environment("current", None, (), (), False)
    assert plugin_interpreter(current, plugin_dir=tmp_path) == (
        resolve_interpreter(current, plugin_dir=tmp_path),
        "manifest environment.current",
    )
    named = _executable(tmp_path / "venv" / "bin" / "python")
    python = Environment("python", str(named), (), (), False)
    assert plugin_interpreter(python, plugin_dir=tmp_path) == (named, "manifest environment.python")
    # A manifest whose environment does not resolve still fails as it did.
    missing = Environment("conda", "not-there", (), (), False)
    with pytest.raises(ModelSpecError, match="environment.conda"):
        plugin_interpreter(missing, plugin_dir=tmp_path)


def test_the_override_wins_over_the_manifest(tmp_path: Path) -> None:
    other = _executable(tmp_path / "envs" / "opencood-official" / "bin" / "python")
    # Even a manifest environment that would not resolve here is not consulted.
    missing = Environment("conda", "opencood", (), (), False)
    assert plugin_interpreter(missing, plugin_dir=tmp_path, override=other) == (
        other,
        PLUGIN_PYTHON_FLAG,
    )


def test_a_bad_override_is_refused_naming_the_flag(tmp_path: Path) -> None:
    current = Environment("current", None, (), (), False)
    with pytest.raises(ModelSpecError, match="--plugin-python: no interpreter at"):
        plugin_interpreter(current, plugin_dir=tmp_path, override=tmp_path / "nope" / "python")
    plain = tmp_path / "plain"
    plain.write_text("", encoding="utf-8")
    plain.chmod(0o644)
    with pytest.raises(ModelSpecError, match="--plugin-python: .* is not executable"):
        plugin_interpreter(current, plugin_dir=tmp_path, override=plain)


def test_inspect_shows_the_interpreter_and_where_it_came_from(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    main(["model", "inspect", "--manifest", str(FAKE)])
    lines = capsys.readouterr().out.strip().splitlines()
    assert lines[-2] == "interpreter_source: manifest environment.current"
    assert lines[-1] == f"interpreter: {PYTHON}"
    other = _executable(tmp_path / "bin" / "python")
    main(["model", "inspect", "--manifest", str(FAKE), "--plugin-python", str(other)])
    lines = capsys.readouterr().out.strip().splitlines()
    assert lines[-2:] == ["interpreter_source: --plugin-python", f"interpreter: {other}"]
    with pytest.raises(SystemExit):
        main(["model", "inspect", "--manifest", str(FAKE), "--plugin-python", str(tmp_path)])
    assert "--plugin-python: no interpreter at" in capsys.readouterr().out


# --- run, check and Slurm -----------------------------------------------------------------------


def test_run_json_records_the_override(scenes: Path, tmp_path: Path) -> None:
    out = tmp_path / "predictions"
    main(_run(scenes, out, "--plugin-python", str(PYTHON)))
    provenance = read_run_meta(out, "r1").provenance
    assert provenance is not None and provenance["status"] == "complete"
    assert provenance["plugin_python"] == str(PYTHON)
    # Without the flag the key is absent, so every other run.json is unchanged.
    main(_run(scenes, tmp_path / "plain"))
    plain = read_run_meta(tmp_path / "plain", "r1").provenance
    assert plain is not None and "plugin_python" not in plain


def test_check_runs_the_plugin_with_the_override(capsys: pytest.CaptureFixture[str]) -> None:
    main(
        ["model", "check", "--manifest", str(FAKE), "--model", "default"]
        + ["--plugin-python", str(PYTHON)]
    )
    out = capsys.readouterr().out
    assert "check fake:" in out and " 0 fail" in out


def test_a_bad_override_fails_before_anything_runs_or_is_submitted(
    scenes: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def no_submit(_path: Path) -> str:
        raise AssertionError("submitted")

    monkeypatch.setattr(slurm, "submit", no_submit)
    out = tmp_path / "predictions"
    bad = tmp_path / "envs" / "missing" / "bin" / "python"
    for extra in (["--plugin-python", str(bad)], ["--plugin-python", str(bad), *_slurm(tmp_path)]):
        with pytest.raises(SystemExit) as caught:
            main(_run(scenes, out, *extra, *(["--submit"] if "--slurm" in extra else [])))
        assert caught.value.code != 0
        assert f"--plugin-python: no interpreter at {bad}" in capsys.readouterr().err
    assert not out.exists()
    with pytest.raises(SystemExit):
        main(["model", "check", "--manifest", str(FAKE), "--plugin-python", str(bad)])
    assert "--plugin-python" in capsys.readouterr().err


def test_the_slurm_job_carries_the_override_exactly_as_given(
    scenes: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    other = _executable(tmp_path / "envs" / "opencood official" / "bin" / "python")
    out = tmp_path / "predictions"
    main(_run(scenes, out, "--plugin-python", str(other), *_slurm(tmp_path)))
    script = capsys.readouterr().out
    (line,) = [line for line in script.splitlines() if " -m coperceive model run " in line]
    assert line.endswith(f"--plugin-python '{other}'"), line
    # Without the flag, the job's model run names no interpreter for the plugin.
    main(_run(scenes, tmp_path / "plain", *_slurm(tmp_path)))
    plain = capsys.readouterr().out
    assert "--plugin-python" not in plain
