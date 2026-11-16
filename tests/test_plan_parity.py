# SPDX-License-Identifier: Apache-2.0
"""Plan parity: the app and the CLI write the same job, or say why not (ADR 0018).

For each case of ``tests/fixtures/plan_parity/cases.json`` the CLI renders its job script
with ``coperceive model run --slurm`` (never ``--submit``). The app's script, or its
refusal, is the file ``src-tauri/tests/plan_parity.rs`` holds the app to. The two are
compared section by section; every difference must be listed, with a reason, in
``allowed_differences.json``, and every listed difference must still occur.

Nothing here submits a job or opens an SSH connection.
"""

from __future__ import annotations

import contextlib
import io
import json
import re
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from coperceive.cli import build_parser, main
from coperceive.models.spec import check_options, load_manifest, select_model

from tests.test_eval_pipeline_dry_run import build_griffin_fixture

REPO = Path(__file__).resolve().parents[1]
PARITY = REPO / "tests" / "fixtures" / "plan_parity"
CASES: dict[str, Any] = json.loads((PARITY / "cases.json").read_text(encoding="utf-8"))
LEDGER: dict[str, Any] = json.loads(
    (PARITY / "allowed_differences.json").read_text(encoding="utf-8")
)
KINDS = ("intended", "defect", "undecided")

# The inner command's fields, compared after parsing (coperceive.cli.build_parser).
ARGV_FIELDS = (
    "manifest",
    "plugin",
    "scenes",
    "out",
    "run_id",
    "model",
    "raw_root",
    "media_root",
    "work",
    "staging_seconds",
    "option",
    "agents",
    "source",
    "scene_ids",
    "no_cache",
    "force",
)


def _case_names() -> list[str]:
    return [case["name"] for case in CASES["cases"]]


def _case(name: str) -> dict[str, Any]:
    return next(case for case in CASES["cases"] if case["name"] == name)


def _under(tmp_path: Path, logical: str) -> Path:
    """``logical`` (an absolute path) re-rooted under ``tmp_path``."""
    return tmp_path / logical.lstrip("/")


def _cli_script(case: dict[str, Any], tmp_path: Path) -> str:
    """The CLI's script for ``case``, with every machine path mapped back to its logical one."""
    root = _under(tmp_path, case["root"])
    root.parent.mkdir(parents=True, exist_ok=True)
    scenes, raw, out = build_griffin_fixture(root)
    profile = case["profile"]
    python_logical = profile["coperceive_command"][0]
    python = _under(tmp_path, python_logical)
    python.parent.mkdir(parents=True, exist_ok=True)
    python.write_text("", encoding="utf-8")
    pythons = [m for m in profile["modules"] if m.startswith("python/")]
    if pythons:
        # The CLI takes the interpreter's module from the venv (slurm.py interpreter_module),
        # the app from the profile: give the venv the module the profile names.
        version = pythons[0].removeprefix("python/")
        (python.parent.parent / "pyvenv.cfg").write_text(
            f"home = /software/python/{version}/bin\n", encoding="utf-8"
        )
    args = [
        "model",
        "run",
        "--manifest",
        str(REPO / "plugins/griffin/coperceive_griffin_model/model.yaml"),
        "--model",
        case["model"],
        "--scenes",
        str(scenes),
        "--raw-root",
        str(raw),
        *(["--media-root", str(raw)] if case["media_root"] else []),
        *[word for extra in case["extras"] for word in ("--option", extra)],
        "--out",
        str(out),
        "--run-id",
        CASES["run_id"],
        "--slurm",
        "--slurm-python",
        str(python),
        "--slurm-partition",
        profile["partition"],
        "--slurm-results",
        profile["results_root"],
        "--slurm-logs",
        case["cli_logs"],
        "--slurm-scratch-template",
        profile["scratch_template"],
    ]
    captured = io.StringIO()
    with contextlib.redirect_stdout(captured):
        main(args)
    text = captured.getvalue()
    text = text[text.index("#!/bin/bash") :]
    return _logical(text, tmp_path)


def _logical(text: str, tmp_path: Path) -> str:
    """Replace this machine's paths with the logical ones of cases.json."""
    for value in (str(tmp_path.resolve()), str(tmp_path)):
        text = text.replace(value, "")
    for value in (str(REPO.resolve()), str(REPO)):
        text = text.replace(value, CASES["code_root"])
    # macOS resolves /home through its data volume.
    return text.replace("/System/Volumes/Data/", "/")


@dataclass(frozen=True)
class Sections:
    sbatch: dict[str, str]
    env: list[str]
    command: str
    capture: str
    capture_body: str
    flags: dict[str, object]


def _sections(text: str) -> Sections:
    lines = text.splitlines()
    sbatch: dict[str, str] = {}
    for line in lines:
        found = re.match(r"#SBATCH --([a-z-]+)=(.*)$", line)
        if found:
            sbatch[found.group(1)] = found.group(2)
    env = [line for line in lines if re.match(r"(module |export (?!TMPDIR))", line)]
    command = next(line for line in lines if " -m coperceive model run " in line)
    capture = next(line for line in lines if line.endswith("<<'PY' || true"))
    body = text[text.index(capture) + len(capture) :]
    body = body[: body.index("\nPY\n")]
    bash_n = 0
    if shutil.which("bash") is not None:
        bash_n = subprocess.run(["bash", "-n", "-c", text], capture_output=True).returncode
    flags: dict[str, object] = {
        "staging": any(line.startswith("rsync -a --files-from=") for line in lines),
        "rc_capture": "RC=$?" in lines,
        "copy_home": any(line.startswith("rsync") and '"$SLURM_JOB_ID"' in line for line in lines),
        "contract_checks": [line.split()[2] for line in lines if line.startswith("test -s ")],
        "bash_n": bash_n,
    }
    return Sections(sbatch, env, command, capture, body, flags)


def _parse(command: str, runtime: dict[str, str]) -> tuple[str, Any, str | None]:
    """``(python, namespace or None, parser error)`` of the ``model run`` line."""
    words = shlex.split(command)
    assert words[1:3] == ["-m", "coperceive"], words[:3]
    argv = [runtime.get(word, word) for word in words[3:]]
    errors = io.StringIO()
    try:
        with contextlib.redirect_stderr(errors):
            return words[0], build_parser().parse_args(argv), None
    except SystemExit:
        return words[0], None, errors.getvalue().strip().splitlines()[-1]


def _without(command: str, flag: str) -> str:
    words = shlex.split(command)
    if flag in words:
        index = words.index(flag)
        del words[index : index + 2]
    return shlex.join(words)


def differences(cli_text: str, app_text: str, case: dict[str, Any]) -> dict[str, str]:
    """Each difference between the two scripts, by key, with both values."""
    cli, app = _sections(cli_text), _sections(app_text)
    found: dict[str, str] = {}
    for key in sorted(set(cli.sbatch) | set(app.sbatch)):
        if cli.sbatch.get(key) != app.sbatch.get(key):
            found[f"sbatch.{key}"] = f"cli={cli.sbatch.get(key)!r} app={app.sbatch.get(key)!r}"
    if cli.env != app.env:
        found["env"] = f"cli={cli.env} app={app.env}"
    if cli.capture != app.capture:
        found["capture.line"] = f"cli={cli.capture!r} app={app.capture!r}"
    if cli.capture_body != app.capture_body:
        found["capture.body"] = "the environment.json programs differ"
    for key in cli.flags:
        if cli.flags[key] != app.flags[key]:
            found[key] = f"cli={cli.flags[key]} app={app.flags[key]}"
    root = case["root"]
    cli_runtime = {
        "$SCRATCH/scenes": f"{root}/scenes",
        "$SCRATCH/raw": f"{root}/raw",
        "$SCRATCH/out": "<SCRATCH>/out",
        "$SCRATCH/work": "<SCRATCH>/work",
        "$STAGING": "0",
    }
    py_cli, ns_cli, err_cli = _parse(cli.command, cli_runtime)
    py_app, ns_app, err_app = _parse(app.command, {})
    if py_cli != py_app:
        found["argv.python"] = f"cli={py_cli} app={py_app}"
    if err_cli:
        found["argv.cli_unparseable"] = err_cli
    if err_app:
        found["argv.app_unparseable"] = err_app
        # Compare the rest of the line without the flag model run rejects.
        _py, ns_app, _err = _parse(_without(app.command, "--output-dir"), {})
    if ns_cli is not None and ns_app is not None:
        for field in ARGV_FIELDS:
            a, b = getattr(ns_cli, field), getattr(ns_app, field)
            if a != b:
                found[f"argv.{field}"] = f"cli={a!r} app={b!r}"
        manifest_path = str(ns_app.manifest).replace(CASES["code_root"], str(REPO))
        manifest = load_manifest(Path(manifest_path))
        for side, ns in (("cli", ns_cli), ("app", ns_app)):
            given = dict(option.split("=", 1) for option in ns.option)
            try:
                check_options(select_model(manifest, ns.model), given)
            except Exception as error:  # noqa: BLE001 - any refusal is the finding
                found[f"argv.{side}_options_rejected"] = str(error)
    return found


def _app_side(name: str) -> tuple[str | None, str | None]:
    """``(script, refusal)``: the app's committed render for the case."""
    app = PARITY / "app"
    script, refusal = app / f"{name}.sbatch", app / f"{name}.refused.txt"
    if script.is_file():
        return script.read_text(encoding="utf-8"), None
    if refusal.is_file():
        return None, refusal.read_text(encoding="utf-8").strip()
    raise AssertionError(f"no app render for {name}: run the Rust test plan_parity first")


@pytest.mark.parametrize("name", _case_names())
def test_the_app_and_the_cli_write_the_same_job_or_the_ledger_says_why(
    name: str, tmp_path: Path
) -> None:
    case = _case(name)
    cli_text = _cli_script(case, tmp_path)
    app_text, refusal = _app_side(name)
    if app_text is None:
        found = {"app_refused": str(refusal)}
    else:
        found = differences(cli_text, app_text, case)
    listed: dict[str, Any] = LEDGER["cases"].get(name, {})
    unlisted = {key: value for key, value in found.items() if key not in listed}
    gone = sorted(key for key in listed if key not in found)
    message = [f"case {name}:"]
    message += [f"  not in the ledger: {key}: {value}" for key, value in unlisted.items()]
    message += [f"  in the ledger but no longer different: {key}" for key in gone]
    assert not unlisted and not gone, "\n".join(message)


def test_every_ledger_entry_says_why() -> None:
    for name, entries in LEDGER["cases"].items():
        assert name in _case_names(), name
        for key, entry in entries.items():
            assert entry["kind"] in KINDS, (name, key)
            assert entry["why"].strip(), (name, key)
            assert entry["cite"].strip(), (name, key)
            if entry["kind"] == "defect":
                assert entry["owner"].strip(), (name, key)


def test_the_cli_script_is_valid_shell_in_every_case(tmp_path: Path) -> None:
    """Case (a) used to fail here (slurm.py's last echo); every case must pass bash -n."""
    if shutil.which("bash") is None:
        pytest.skip("no bash")
    for name in _case_names():
        text = _cli_script(_case(name), tmp_path / name)
        done = subprocess.run(["bash", "-n", "-c", text], capture_output=True, text=True)
        assert done.returncode == 0, (name, done.stderr)
