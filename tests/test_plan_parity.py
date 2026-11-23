# SPDX-License-Identifier: Apache-2.0
"""Plan parity: the app's Griffin job equals ``coperceive model run --slurm`` (ADR 0018).

For each case of ``tests/fixtures/plan_parity/cases.json`` the CLI renders its job script
with ``coperceive model run --slurm`` (never ``--submit``). The app's script is the file
``src-tauri/tests/plan_parity.rs`` holds the app to (``app/<case>.sbatch``). The two
WHOLE scripts are compared line by line: a line that is only in one of them must match
an entry of ``allowed_differences.json`` (with its reason); every app-side entry must
match in every case it applies to, so the app cannot drop a listed line, and every
CLI-side entry must still match somewhere. The run block (``set +e``, ``model run``,
``RC=$?``, ``set -e``, the exit echo), environment.json, the work copy and the exit with
the run's status are checked in place on both sides, and so is the scratch setup before
it (``set -euo pipefail``, the scratch directory, its ``mkdir``, the EXIT ``trap``, ``TMPDIR``
and the work directory). The one line that differs by design
in both, ``model run``, is then parsed by the real ``build_parser()`` on both sides and
compared field by field, and the app's options go through the manifest's
``check_options``.

The ``refusals`` of cases.json are inputs the app refuses: plan_parity.rs holds the
app to the exact message, and this file checks that ``model run --slurm`` takes the same
input (so the refusal stays a listed difference, not a silent one).

Python cannot run the app's renderer: ``src-tauri/tests/plan_parity.rs`` owns staleness.
It re-renders every case and compares the committed ``app/<case>.sbatch`` byte for byte,
so a change to what the app renders fails there (``cargo test`` in CI) until the renders
are rewritten with ``COPERCEIVE_UPDATE_PARITY=1``.

Nothing here submits a job or opens an SSH connection.
"""

from __future__ import annotations

import argparse
import contextlib
import difflib
import io
import json
import re
import shlex
import shutil
import subprocess
from importlib import metadata
from pathlib import Path
from typing import Any
from unittest import mock

import pytest
from coperceive.cli import build_parser, main
from coperceive.models import registry, slurm
from coperceive.models.spec import check_options, load_manifest, select_model

from tests.test_eval_pipeline_dry_run import (
    _build_fixture,  # pyright: ignore[reportPrivateUsage]
)

REPO = Path(__file__).resolve().parents[1]
PARITY = REPO / "tests" / "fixtures" / "plan_parity"
CASES: dict[str, Any] = json.loads((PARITY / "cases.json").read_text(encoding="utf-8"))
LEDGER: dict[str, Any] = json.loads(
    (PARITY / "allowed_differences.json").read_text(encoding="utf-8")
)
GRIFFIN = REPO / "plugins" / "griffin" / "coperceive_griffin_model" / "model.yaml"
# No undecided row: every difference is decided and explained (ADR 0021).
KINDS = ("intended",)
UPDATE = (
    "COPERCEIVE_UPDATE_PARITY=1 cargo test --manifest-path src-tauri/Cargo.toml "
    "--test plan_parity, then review the diff"
)
# Each side's run block, in this order, then the work copy, then the exit with the
# run's status (the CLI exits at the end, the app before its own post-run checks).
RUN_BLOCK_BEFORE = "set +e"
RUN_BLOCK_AFTER = ("RC=$?", "set -e", 'echo "model run exit $RC"')
EXIT_WITH_RC = {"cli": 'exit "$RC"', "app": 'if [ "$RC" -ne 0 ]; then exit "$RC"; fi'}


def _argv_fields() -> set[str]:
    """Every field ``build_parser()`` gives a ``model run`` line: the whole parsed
    namespace is compared, except the fields ``allowed_differences.json`` lists with a
    reason (``argv_fields``)."""
    parsed = build_parser().parse_args(
        ["model", "run", "--scenes", "s", "--out", "o", "--run-id", "r"]
    )
    return set(vars(parsed))


def _case_names() -> list[str]:
    return [case["name"] for case in CASES["cases"]]


def _case(name: str) -> dict[str, Any]:
    return next(case for case in CASES["cases"] if case["name"] == name)


def _code_root(case: dict[str, Any]) -> str:
    """The case's code root: where the manifest lives and what PYTHONPATH names."""
    return case.get("code_root", CASES["code_root"])


def _under(tmp_path: Path, logical: str) -> Path:
    return tmp_path / logical.lstrip("/")


def _logical(text: str, tmp_path: Path, code_root: str | None = None) -> str:
    """This machine's paths back to the logical ones of cases.json."""
    for value in (str(tmp_path.resolve()), str(tmp_path)):
        text = text.replace(value, "")
    for value in (str(REPO.resolve()), str(REPO)):
        text = text.replace(value, code_root or CASES["code_root"])
    return text


def _cli_script(case: dict[str, Any], tmp_path: Path, *, with_ckpt_root: bool = True) -> str:
    """The CLI's script for ``case`` with every path mapped back to its logical one.

    ``with_ckpt_root=False`` leaves out the required Griffin option, for the refusal test.
    """
    root = _under(tmp_path, case["root"])
    root.parent.mkdir(parents=True, exist_ok=True)
    scenes, raw, out = _build_fixture(root)
    profile = case["profile"]
    python = _under(tmp_path, case["python"])
    python.parent.mkdir(parents=True, exist_ok=True)
    python.write_text("", encoding="utf-8")
    # Every case's interpreter is a venv on this cluster's /software/python/<version>
    # base: the CLI reads its pyvenv.cfg here (slurm.py interpreter_module), the app over
    # its plan-time interpreter check (the Rust fake cluster answers with the same text).
    (python.parent.parent / "pyvenv.cfg").write_text(case["pyvenv_cfg"], encoding="utf-8")
    # A case with its own code_root: the CLI emits from there. The Griffin plugin is copied
    # to it and slurm.coperceive_root is patched to it, so a code root with a space reaches
    # the CLI's script as it reaches the app's. Every other case runs the real
    # coperceive_root (this checkout, mapped back to code_root by _logical), so the CLI's
    # own PYTHONPATH is checked end to end.
    patched: contextlib.AbstractContextManager[object] = contextlib.nullcontext()
    manifest_path = REPO / case.get(
        "manifest", "plugins/griffin/coperceive_griffin_model/model.yaml"
    )
    manifest = manifest_path
    if "code_root" in case:
        code = _under(tmp_path, case["code_root"])
        manifest = code / manifest_path.relative_to(REPO)
        if not manifest.is_file():
            shutil.copytree(manifest_path.parent, manifest.parent)
        patched = mock.patch.object(slurm, "coperceive_root", return_value=code)
    media = ["--media-root", str(raw)] if case.get("media_root", True) else []
    raw_args = [] if case.get("raw_root", True) is False else ["--raw-root", str(raw)]
    # Griffin's ckpt_root has no manifest default, so a Griffin case gives the worker one.
    # `"ckpt_root": false` is a model that does not declare it (OpenCOOD CoBEVT).
    if case.get("ckpt_root", True) is False or not with_ckpt_root:
        ckpt: list[str] = []
    else:
        ckpt_root = _under(tmp_path, case.get("ckpt_root", f"{case['root']}/ckpts"))
        ckpt = ["--option", f"ckpt_root={ckpt_root}"]
    checkpoint_args: list[str] = []
    checkpoint = case.get("checkpoint")
    if isinstance(checkpoint, str) and checkpoint:
        src = _under(tmp_path, checkpoint)
        src.parent.mkdir(parents=True, exist_ok=True)
        src.write_bytes(b"checkpoint\n")
        checkpoint_args = ["--option", f"checkpoint={src}"]
    options = [word for extra in case.get("extras", []) for word in ("--option", extra)]
    protocol = case.get("eval_protocol")
    override: list[str] = []
    if "plugin_python" in case:
        # The override must exist and be executable where the job is planned.
        interpreter = _under(tmp_path, case["plugin_python"])
        interpreter.parent.mkdir(parents=True, exist_ok=True)
        interpreter.write_text("", encoding="utf-8")
        interpreter.chmod(0o755)
        override = ["--plugin-python", str(interpreter)]
    args = [
        "model",
        "run",
        "--manifest",
        str(manifest),
        "--model",
        case["model"],
        *(["--protocol", str(protocol)] if protocol is not None else []),
        "--scenes",
        str(scenes),
        *raw_args,
        *media,
        *(ckpt if with_ckpt_root else []),
        *checkpoint_args,
        *options,
        *(["--evaluate"] if case.get("evaluate") else []),
        *override,
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
        str(_under(tmp_path, profile["results_root"])),
        "--slurm-logs",
        str(_under(tmp_path, case["cli_logs"])),
        "--slurm-scratch-template",
        profile["scratch_template"],
    ]
    captured = io.StringIO()
    with contextlib.redirect_stdout(captured), patched:
        main(args)
    text = captured.getvalue()
    return _logical(text[text.index("#!/bin/bash") :], tmp_path, _code_root(case))


def _app_script(name: str) -> str:
    path = PARITY / "app" / f"{name}.sbatch"
    if not path.is_file():
        raise AssertionError(f"no app render for {name}: run the Rust test plan_parity first")
    return path.read_text(encoding="utf-8")


def _lines(text: str) -> list[str]:
    """The script's lines, a trailing ``  # comment`` dropped from command lines."""
    return [
        line if line.startswith("#") else re.sub(r"  # .*$", "", line) for line in text.splitlines()
    ]


def _one_sided_lines(cli: str, app: str) -> list[tuple[str, str]]:
    """``(side, line)`` for every line only the CLI's or only the app's script has."""
    a, b = _lines(cli), _lines(app)
    found: list[tuple[str, str]] = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag in ("replace", "delete"):
            found += [("cli", line) for line in a[i1:i2]]
        if tag in ("replace", "insert"):
            found += [("app", line) for line in b[j1:j2]]
    return found


def _entry_matches(entry: dict[str, Any], side: str, line: str) -> bool:
    return entry["side"] in (side, "both") and re.fullmatch(entry["match"], line) is not None


def _applies(entry: dict[str, Any], name: str) -> bool:
    return name in entry.get("cases", _case_names())


def _run_block(text: str, side: str) -> None:
    """The run block is whole and in order, then the work copy, environment.json and
    the exit with the run's status: a job that drops ``set +e`` dies before its logs
    come home, one without ``RC=$?`` dies under ``set -u``, one without the exit line
    reports success for a failed run."""
    lines = text.splitlines()
    (run,) = [i for i, line in enumerate(lines) if _RUN_LINE.match(line)]
    assert lines[run - 1] == RUN_BLOCK_BEFORE, (side, lines[run - 1])
    assert tuple(lines[run + 1 : run + 4]) == RUN_BLOCK_AFTER, (side, lines[run + 1 : run + 4])
    (work,) = [i for i, line in enumerate(lines) if line == _work_copy_line(text)]
    exits = [i for i, line in enumerate(lines) if line == EXIT_WITH_RC[side]]
    assert len(exits) == 1, (side, EXIT_WITH_RC[side], exits)
    assert run + 3 < work < exits[0], (side, run, work, exits)
    (capture,) = [i for i, line in enumerate(lines) if line.endswith("<<'PY' || true")]
    assert run + 3 < capture < exits[0], (side, "environment.json after the run", capture)
    assert lines.count("set +e") == 1 and lines.count("set -e") == 1, side


# The scratch steps both sides take before ``module purge``, in this order. Each side's
# other lines there (the CLI's RUN_ID and staging folders, the app's OUT_DIR) are one-sided
# lines of allowed_differences.json.
SCRATCH_STEPS = (
    ("strict", "set -euo pipefail"),
    ("scratch", None),
    ("mkdir", "<SCRATCH>"),
    ("trap", "rm -rf <SCRATCH>", "EXIT"),
    ("tmpdir", "<SCRATCH>/tmp"),
    ("mkdir", "$TMPDIR"),
    ("mkdir", "<SCRATCH>/work"),
)


def _scratch_setup(text: str, side: str) -> list[tuple[str, ...]]:
    """The scratch steps of ``text`` from ``set -euo pipefail`` to ``module purge``, with
    the side's scratch variable written ``<SCRATCH>``. ``set -euo pipefail`` must be the
    first command after the ``#SBATCH`` header. Up to the EXIT trap only the scratch
    assignment, its ``mkdir`` and a plain assignment of a literal word (the CLI's
    ``RUN_ID=<id>``, which runs nothing) may come: any other command there would run
    before the trap, so scratch would be left behind if it failed. After the trap, until
    ``module purge``, TMPDIR and the work folder are checked in order; each side's other
    lines there are one-sided ledger rows. A job without strict mode first runs on past a
    failed step."""
    lines = text.splitlines()
    end = lines.index("module purge")
    commands = [line for line in lines[:end] if line.strip() and not line.startswith("#")]
    assert commands and commands[0] == "set -euo pipefail", (side, "first command", commands[:1])
    variable = ""
    trapped = False
    steps: list[tuple[str, ...]] = [("strict", commands[0])]
    for line in commands[1:]:
        assigned = re.fullmatch(r"(SCRATCH|SCRATCH_DIR)=(.*)", line)
        if assigned and not variable:
            variable = assigned.group(1)
            steps.append(("scratch", shlex.split(assigned.group(2))[0]))
            continue
        words = shlex.split(line.replace(f"${variable}", "<SCRATCH>")) if variable else []
        if words[:1] == ["mkdir"]:
            # -p, the app's -m 0700 and -- are options; the folders are what matter.
            paths = [w for w in words[1:] if w not in ("-p", "-m", "0700", "--")]
            if not trapped:
                assert paths == ["<SCRATCH>"], (side, "before the EXIT trap", line)
            steps += [("mkdir", path) for path in paths if path in _SHARED_MKDIRS]
        elif words[:1] == ["trap"]:
            removed = [w for w in shlex.split(words[1]) if w != "--"]
            steps.append(("trap", " ".join(removed), *words[2:]))
            trapped = True
        elif words[:2] == ["export", "TMPDIR=<SCRATCH>/tmp"]:
            steps.append(("tmpdir", "<SCRATCH>/tmp"))
        elif not trapped:
            assert re.fullmatch(r"[A-Z_][A-Z0-9_]*=[A-Za-z0-9._/-]+", line), (
                side,
                "before the EXIT trap",
                line,
            )
    template = next((step[1] for step in steps if step[0] == "scratch"), None)
    expected = [("scratch", template) if step[0] == "scratch" else step for step in SCRATCH_STEPS]
    assert steps == expected, (side, steps)
    return steps


_SHARED_MKDIRS = ("<SCRATCH>", "$TMPDIR", "<SCRATCH>/work")


def _work_copy_line(text: str) -> str:
    (line,) = [
        line
        for line in text.splitlines()
        if line.startswith("rsync -a ") and "--prune-empty-dirs" in line and "/work/" in line
    ]
    return line


_RUN_LINE = re.compile(r"^\S+( -m coperceive|/coperceive) model run ")


def _run_line(text: str) -> str:
    (line,) = [line for line in text.splitlines() if _RUN_LINE.match(line)]
    return line


def _parse(line: str, runtime: dict[str, str]) -> tuple[str, str, argparse.Namespace]:
    """``(program, interpreter, namespace)`` of a ``model run`` line: ``<python> -m
    coperceive`` or the console script ``<bin>/coperceive``, whose interpreter is the
    ``python`` beside it (the one its pip-written shebang names)."""
    words = shlex.split(line)
    if words[1:3] == ["-m", "coperceive"]:
        program, python, rest = " ".join(words[:3]), words[0], words[3:]
    else:
        assert words[0].endswith("/coperceive"), words[:1]
        program, python, rest = words[0], words[0].removesuffix("coperceive") + "python", words[1:]

    def apply(word: str) -> str:
        if word in runtime:
            return runtime[word]
        for key in sorted(runtime, key=len, reverse=True):
            if key in word:
                return word.replace(key, runtime[key])
        return word

    argv = [apply(word) for word in rest]
    errors = io.StringIO()
    try:
        with contextlib.redirect_stderr(errors):
            return program, python, build_parser().parse_args(argv)
    except SystemExit:
        raise AssertionError(f"model run rejects this line: {errors.getvalue()}") from None


@pytest.mark.parametrize("name", _case_names())
def test_the_app_and_the_cli_write_the_same_griffin_job(name: str, tmp_path: Path) -> None:
    case = _case(name)
    cli, app = _cli_script(case, tmp_path), _app_script(name)

    # 1. The whole scripts: every one-sided line is a listed, explained difference, and
    # every app-side (or both-side) entry that applies to this case still matches one:
    # the app cannot drop a line the ledger lists for it.
    entries: list[dict[str, Any]] = [e for e in LEDGER["lines"] if _applies(e, name)]
    one_sided = _one_sided_lines(cli, app)
    unlisted = [
        f"  {side}: {line}"
        for side, line in one_sided
        if not any(_entry_matches(entry, side, line) for entry in entries)
    ]
    assert not unlisted, f"case {name}: lines not in allowed_differences.json:\n" + "\n".join(
        unlisted
    )
    missing = [
        f"  {entry['side']}: {entry['match']}"
        for entry in entries
        if entry["side"] in ("app", "both")
        and not any(_entry_matches(entry, side, line) for side, line in one_sided)
    ]
    assert not missing, f"case {name}: the app no longer writes these listed lines:\n" + (
        "\n".join(missing)
    )

    # The model needs a GPU (gpu: true): both sides request exactly gpu:1 and leave the
    # type to the manifest's --constraint, and both record environment.json with the
    # interpreter that runs the model, after the run, in the block checked in place.
    python = case["python"]
    for side, text in (("cli", cli), ("app", app)):
        assert re.findall(r"^#SBATCH --gres=.*$", text, re.MULTILINE) == ["#SBATCH --gres=gpu:1"], (
            name,
            side,
        )
        (capture,) = [line for line in text.splitlines() if line.endswith("<<'PY' || true")]
        assert capture.split()[0] == python, (name, side, capture)
        _run_block(text, side)
    # The scratch setup before anything runs: the same steps, in the same order, on the
    # profile's scratch template (the ledger excuses only the variable's name and the
    # app's 0700 mode; ADR 0021, "Scratch setup").
    setup = {side: _scratch_setup(text, side) for side, text in (("cli", cli), ("app", app))}
    assert setup["cli"] == setup["app"], (name, setup)
    assert ("scratch", case["profile"]["scratch_template"]) in setup["cli"], (name, setup)

    # 2. The model run line, field by field, with the CLI's staged copies mapped back.
    root = case["root"]
    staged = {
        "$SCRATCH/scenes": f"{root}/scenes",
        "$SCRATCH/raw": f"{root}/raw",
        "$SCRATCH/out": "<SCRATCH>/out",
        "$SCRATCH/work": "<SCRATCH>/work",
        "$STAGING": "0",
    }
    checkpoint = case.get("checkpoint")
    if isinstance(checkpoint, str) and checkpoint:
        staged[f"$SCRATCH/checkpoints/checkpoint/{Path(checkpoint).name}"] = checkpoint
    _, py_cli, ns_cli = _parse(_run_line(cli), staged)
    program, py_app, ns_app = _parse(_run_line(app), {"$SCRATCH_DIR/work": "<SCRATCH>/work"})
    assert py_app == py_cli == case["python"]
    assert program == " ".join(case["profile"]["coperceive_command"]), program
    allowed: dict[str, str] = LEDGER["argv_fields"]
    cli_fields: dict[str, object] = vars(ns_cli)
    app_fields: dict[str, object] = vars(ns_app)
    # Both lines are `model run` itself (the same subcommand, so the same fields), and
    # every field the ledger excuses really differs: an allowance that is no longer needed
    # must be removed, so it cannot hide a later difference.
    for ns in (ns_cli, ns_app):
        assert (ns.command, ns.model_command, ns.slurm, ns.submit) == ("model", "run", False, False)
    assert set(cli_fields) == set(app_fields), set(cli_fields) ^ set(app_fields)
    fields = set(cli_fields)
    for field in allowed:
        assert app_fields[field] != cli_fields[field], (name, field, "no longer differs")
    for field in sorted(fields - set(allowed)):
        assert app_fields.get(field) == cli_fields.get(field), (
            name,
            field,
            app_fields.get(field),
            cli_fields.get(field),
        )
    # The work directory comes home through the same rsync filter (work_copy_filter).
    filters = [
        [word for word in shlex.split(line) if word.startswith(("--include=", "--exclude="))]
        + [word for word in shlex.split(line) if word == "--prune-empty-dirs"]
        for line in (_work_copy_line(cli), _work_copy_line(app))
    ]
    assert filters[0] == filters[1] and filters[0], (name, filters)
    manifest = load_manifest(Path(str(ns_app.manifest).replace(_code_root(case), str(REPO))))
    given = dict(option.split("=", 1) for option in ns_app.option)
    check_options(select_model(manifest, ns_app.model), given)

    # 3. Both are valid shell.
    if shutil.which("bash") is not None:
        for side, text in (("cli", cli), ("app", app)):
            done = subprocess.run(["bash", "-n", "-c", text], capture_output=True, text=True)
            assert done.returncode == 0, (name, side, done.stderr)


def test_the_cli_refuses_a_missing_required_ckpt_root_before_writing_a_script(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Griffin's ckpt_root has no default. ``model run --slurm`` must stop at the option
    check, name the parameter, and write no sbatch (no submission is ever reached)."""
    with pytest.raises(SystemExit):
        _cli_script(_case("base"), tmp_path, with_ckpt_root=False)
    assert "ckpt_root" in capsys.readouterr().err
    assert not list(tmp_path.rglob("*.sbatch"))


@pytest.mark.parametrize(
    ("before", "inserted"),
    [
        ("set -euo pipefail\n", 'cd "$HOME"\n'),
        ("trap 'rm -rf", 'cp -r /data "$SCRATCH_DIR"\n'),
        ("trap 'rm -rf", 'echo "$(hostname)"\n'),
    ],
    ids=["before_strict_mode", "copy_before_the_trap", "echo_before_the_trap"],
)
def test_the_scratch_check_refuses_a_command_ahead_of_strict_mode_or_the_trap(
    before: str, inserted: str
) -> None:
    """``_scratch_setup`` does not only pick out the lines it knows: a command ahead of
    ``set -euo pipefail``, or between the scratch folder and its EXIT trap, fails it."""
    text = _app_script("base")
    _scratch_setup(text, "app")
    mutated = text.replace(before, inserted + before, 1)
    assert mutated != text
    with pytest.raises(AssertionError, match="first command|before the EXIT trap"):
        _scratch_setup(mutated, "app")


def test_the_cli_exports_the_checkout_that_emits_the_job() -> None:
    """The cases without their own code_root rely on the real ``coperceive_root`` being
    this checkout (mapped back to ``code_root``); a root one folder off would otherwise be
    mapped wrongly and only show up as a PYTHONPATH difference."""
    assert slurm.coperceive_root() == REPO.resolve()


def test_every_allowed_difference_still_occurs_and_says_why(tmp_path: Path) -> None:
    pairs = {
        name: _one_sided_lines(_cli_script(_case(name), tmp_path / name), _app_script(name))
        for name in _case_names()
    }
    for entry in LEDGER["lines"]:
        assert entry["kind"] in KINDS, entry
        assert entry["side"] in ("cli", "app", "both"), entry
        assert entry["why"].strip() and entry["cite"].strip(), entry
        assert set(entry.get("cases", [])) <= set(_case_names()), entry
        # One line per entry: an alternation would let the app drop one of its lines.
        assert "|" not in re.sub(r"\\\||\[[^]]*\]", "", entry["match"]), entry["match"]
        cases = [name for name in _case_names() if _applies(entry, name)]
        found = [
            name
            for name in cases
            if any(_entry_matches(entry, side, line) for side, line in pairs[name])
        ]
        assert found, f"no longer different, remove it from allowed_differences.json: {entry}"
        if entry["side"] in ("app", "both"):
            assert found == cases, (entry["match"], "missing in", set(cases) - set(found))
    for field, why in LEDGER["argv_fields"].items():
        assert field in _argv_fields() and why.strip(), field


def test_every_case_has_a_committed_app_render() -> None:
    """Staleness is plan_parity.rs's (it compares each render byte for byte); here, only
    that the committed renders are exactly the cases."""
    renders = {path.stem for path in (PARITY / "app").glob("*.sbatch")}
    assert renders == set(_case_names()), (renders ^ set(_case_names()), UPDATE)


def test_the_app_reads_what_model_list_json_prints_for_griffin(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """``model_list.json`` (the Rust fake cluster's answer) is the CLI's real output."""

    class _Griffin:
        name = "griffin"
        group = "coperceive.models"
        value = "coperceive_griffin_model:MANIFEST"

        def load(self) -> object:
            return GRIFFIN

    def entry_points(**_kwargs: object) -> list[_Griffin]:
        return [_Griffin()]

    monkeypatch.setattr(metadata, "entry_points", entry_points)
    registry.clear_cache()
    main(["model", "list", "--json"])
    registry.clear_cache()
    printed = json.loads(_logical(capsys.readouterr().out, Path("/nonexistent")))
    committed = json.loads((PARITY / CASES["model_list"]).read_text(encoding="utf-8"))
    assert printed == committed


def test_the_app_reads_what_model_list_json_prints_for_opencood(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """``model_list_no_raw.json`` is the CLI's listing of the OpenCOOD plugin, including
    ``raw_inputs: false`` for CoBEVT (its manifest declares no ``inputs.raw``)."""

    class _OpenCOOD:
        name = "opencood"
        group = "coperceive.models"
        value = "coperceive_opencood_model:MANIFEST"

        def load(self) -> object:
            return REPO / "plugins/opencood/coperceive_opencood_model/model.yaml"

    def entry_points(**_kwargs: object) -> list[_OpenCOOD]:
        return [_OpenCOOD()]

    monkeypatch.setattr(metadata, "entry_points", entry_points)
    registry.clear_cache()
    main(["model", "list", "--json"])
    registry.clear_cache()
    printed = json.loads(_logical(capsys.readouterr().out, Path("/nonexistent")))
    committed = json.loads((PARITY / "model_list_no_raw.json").read_text(encoding="utf-8"))
    assert printed == committed
    assert printed["plugins"][0]["models"]["cobevt-lidar-v2xset"]["raw_inputs"] is False


def _refusal_names() -> list[str]:
    return [case["name"] for case in CASES["refusals"]]


@pytest.mark.parametrize("name", _refusal_names())
def test_the_cli_takes_each_input_the_app_refuses(name: str, tmp_path: Path) -> None:
    """plan_parity.rs holds the app to the refusal's exact message; here, ``model run
    --slurm`` takes the same input and writes a valid script, so the refusal is a real,
    listed difference and not a gap in the harness."""
    (case,) = [case for case in CASES["refusals"] if case["name"] == name]
    assert name not in _case_names(), name
    assert not (PARITY / "app" / f"{name}.sbatch").exists(), name
    for key in ("refused", "why", "cite"):
        assert case[key].strip(), (name, key)
    cli = _cli_script(case, tmp_path)
    _, _, ns = _parse(_run_line(cli), {"$STAGING": "0"})
    media = [] if case.get("media_root", True) is False else [f"{case['root']}/raw"]
    assert ns.media_root == media, (name, ns.media_root)
    if shutil.which("bash") is not None:
        done = subprocess.run(["bash", "-n", "-c", cli], capture_output=True, text=True)
        assert done.returncode == 0, (name, done.stderr)


# The scoring step's lines both jobs write word for word (the CLI's ``evaluate_lines``,
# the app's ``job_submit::evaluate_lines``): the guard's shape, the indented strict-mode
# toggles that keep ``_run_block``'s counts, and the combined exit code.
SCORING_SHARED = (
    "EVAL_RC=0",
    "  set +e",
    "  EVAL_RC=$?",
    "  set -e",
    '  echo "eval run exit $EVAL_RC"',
    "else",
    "  EVAL_RC=1",
    "fi",
    'if [ "$RC" -eq 0 ] && [ "$EVAL_RC" -ne 0 ]; then RC=5; fi',
)


def _scoring_block(scored: str, base: str) -> tuple[list[str], str]:
    """The lines ``scored`` adds to ``base`` (one inserted block) and the line after it."""
    a, b = _lines(scored), _lines(base)
    opcodes = difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes()
    changed = [op for op in opcodes if op[0] != "equal"]
    assert [op[0] for op in changed] == ["delete"], changed  # lines only the scored job has
    (_, i1, i2, _, _) = changed[0]
    return a[i1:i2], a[i2]


def test_both_jobs_score_after_the_run_and_change_nothing_else(tmp_path: Path) -> None:
    """``evaluate_after_run``: each side's job is its base case's plus one scoring block
    after the run's copy home (CLI) or work copy (app), ending with the ``RC=5`` line
    right before the side's exit; the run line is the base case's, word for word."""
    case = _case("evaluate_after_run")
    assert case["evaluate"] is True
    assert "evaluate_after_run" not in [c["name"] for c in CASES.get("pending_app", [])]
    assert "evaluate_after_run" not in [e["case"] for e in LEDGER["pending_app_cases"]]
    sides = {
        "cli": (
            _cli_script(case, tmp_path / "evaluate"),
            _cli_script(_case("base"), tmp_path / "base"),
        ),
        "app": (_app_script("evaluate_after_run"), _app_script("base")),
    }
    after = {"cli": 'echo "done:"', "app": EXIT_WITH_RC["app"]}
    blocks: dict[str, list[str]] = {}
    for side, (scored, base) in sides.items():
        assert _run_line(scored) == _run_line(base), side
        block, following = _scoring_block(scored, base)
        assert block[0] == "" and block[-1] == SCORING_SHARED[-1], (side, block)
        assert following.startswith(after[side]), (side, following)
        for line in SCORING_SHARED:
            assert block.count(line) == 1, (side, line)
        blocks[side] = block
    # The scoring lines: ``eval run`` on the run, its scenes and its predictions.
    cli_score, app_score = (
        next(line for line in blocks[side] if " -m coperceive eval run " in line)
        for side in ("cli", "app")
    )
    assert '"$RUN_ID" --root "$SCRATCH/scenes" --predictions "$SCRATCH/out"' in cli_score
    assert app_score.split()[1:3] == ["-m", "coperceive"], app_score
    assert app_score.split()[3:] == [
        "eval",
        "run",
        CASES["run_id"],
        "--root",
        f"{case['root']}/scenes",
        "--predictions",
        '"$OUT_DIR"',
    ], app_score
    assert cli_score.split()[0] == app_score.split()[0] == case["python"]


def _pending_plugin_python_names() -> list[str]:
    # Only the --plugin-python cases: another pending case checks its own difference.
    return [case["name"] for case in CASES.get("pending_app", []) if "plugin_python" in case]


@pytest.mark.parametrize("name", _pending_plugin_python_names())
def test_the_cli_carries_the_plugin_python_override_the_app_does_not_yet(
    name: str, tmp_path: Path
) -> None:
    """A CLI-only case: the app half is pending (allowed_differences.json
    ``pending_app_cases``, with the follow-up named). The CLI's script differs from the
    base case's only by ``--plugin-python <path>`` on the run line, the path as given."""
    (case,) = [case for case in CASES["pending_app"] if case["name"] == name]
    assert name not in _case_names(), name
    assert not (PARITY / "app" / f"{name}.sbatch").exists(), name
    (pending,) = [e for e in LEDGER["pending_app_cases"] if e["case"] == name]
    for key in ("follow_up", "why", "cite"):
        assert pending[key].strip() and case.get("follow_up", "").strip(), (name, key)
    cli = _cli_script(case, tmp_path / "override")
    base = _cli_script(_case("base"), tmp_path / "base")
    _, _, ns = _parse(_run_line(cli), {"$STAGING": "0"})
    assert ns.plugin_python == case["plugin_python"]
    _, _, base_ns = _parse(_run_line(base), {"$STAGING": "0"})
    assert base_ns.plugin_python is None
    differing = [(a, b) for a, b in zip(_lines(cli), _lines(base), strict=True) if a != b]
    assert len(differing) == 1, differing
    ((line, base_line),) = differing
    assert line == f"{base_line} --plugin-python {case['plugin_python']}", line
