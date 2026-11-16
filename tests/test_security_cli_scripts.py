# SPDX-License-Identifier: Apache-2.0
"""Security review, group "cli-scripts": values that reach the CLI's job script and archive
readers (ADR 0019 findings SCRIPTS-08, -09, -11, -12, the interpreter comment, ARC-6, ARC-8).

Each test names the finding it covers. The refusals are paired with the legitimate values
that must keep working, so a fix that refuses real data fails here too.
"""

from __future__ import annotations

import gzip
import io
import os
import shutil
import subprocess
import sys
import tarfile
from dataclasses import replace
from pathlib import Path
from typing import IO, Any, cast

import pytest
import yaml
from coperceive.cli import main
from coperceive.errors import CoperceiveError
from coperceive.evaluation.predictions import PredictionError, check_run_id
from coperceive.hdmap.commands import (
    CARLA_TOWNS,
    MAX_TAR_HEADER_BYTES,
    MAX_TAR_HEADER_CHAIN,
    extract_towns,
)
from coperceive.hdmap.install import MAX_MAP_BYTES
from coperceive.model import Scene
from coperceive.models.batch import RunOptions
from coperceive.models.slurm import (
    SlurmError,
    checkpoint_files,
    plan_sbatch,
    validate_scratch_template,
)
from coperceive.models.spec import (
    Manifest,
    ModelSpecError,
    load_manifest,
    parse_manifest,
    select_model,
)
from coperceive.robustness.sweep import (
    SlurmSettings,
    SweepError,
    SweepOptions,
    default_eval,
    emit_slurm,
    load_grid,
)
from coperceive.store import write_scene

from tests.cooperative import cooperative_scene
from tests.fake_model_plugin import MANIFEST as FAKE

REPO = Path(__file__).resolve().parents[1]
CHECKER = REPO / "scripts" / "check_bundle_assets.py"
CHECKPOINT_MANIFEST = FAKE.with_name("model-checkpoint.yaml")
TWO = FAKE.with_name("model-two.yaml")
SCRATCH = "/scratch/$USER/$SLURM_JOB_ID"


@pytest.fixture
def roots(tmp_path: Path) -> tuple[Path, Path, Scene]:
    scene = replace(cooperative_scene(), scene_id="coop")
    scenes = tmp_path / "scenes"
    write_scene(scene, scenes / "coop")
    return scenes, tmp_path / "predictions", scene


def _payload(source: Path = FAKE) -> dict[str, Any]:
    return cast(dict[str, Any], yaml.safe_load(source.read_text(encoding="utf-8")))


def _plan(
    roots: tuple[Path, Path, Scene],
    manifest: Manifest,
    tmp_path: Path,
    *,
    run_id: str = "r1",
    python: Path = Path("/opt/venv/bin/python"),
    options: RunOptions | None = None,
    scratch_template: str = SCRATCH,
    model: str = "default",
) -> Any:
    scenes, out, scene = roots
    return plan_sbatch(
        manifest,
        select_model(manifest, model),
        scenes_root=scenes,
        scenes={"coop": scene},
        run_id=run_id,
        out_root=out,
        options=options or RunOptions(),
        python=python,
        logs_dir=tmp_path / "logs",
        results_dir=tmp_path / "results",
        partition="test-partition",
        scratch_template=scratch_template,
    )


def _bash() -> str:
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("no bash")
    return bash


# --- SCRIPTS-08: the scratch template ----------------------------------------------------


@pytest.mark.parametrize(
    "template",
    [
        "$HOME/$SLURM_JOB_ID/../projects",  # the trap would rm -rf $HOME/projects
        "/scratch/$SLURM_JOB_ID/..",
        "/scratch/../$SLURM_JOB_ID",
        "~/scratch/$SLURM_JOB_ID",  # a literal '~' directory inside the double quotes
        "/scratch/~alice/$SLURM_JOB_ID",
        "/scratch/${HOME}/$SLURM_JOB_ID",
        "/scratch/$TMPDIR/$SLURM_JOB_ID",
        "/scratch/$USERNAME/$SLURM_JOB_ID",
        "/scratch/${SLURM_JOB_ID",
        "/scratch/$/$SLURM_JOB_ID",
        "/scratch/$USER/$SLURM_JOB_ID\n",  # '$' matched before a final newline
        "/scratch/$USER/{$SLURM_JOB_ID}",
    ],
)
def test_scratch_template_refuses_parents_tilde_and_other_variables(template: str) -> None:
    with pytest.raises(SlurmError, match="scratch template"):
        validate_scratch_template(template)


@pytest.mark.parametrize(
    "template",
    [
        "/scratch/$USER/$SLURM_JOB_ID",
        "/local/$USER/$SLURM_JOB_ID",
        "/scratch/${USER}/$SLURM_JOB_ID",
        "/scratch/${USER}/${SLURM_JOB_ID}",
        "/scratch/$USER/job-$SLURM_JOB_ID",
        "/scratch/$USER/$SLURM_JOB_ID/",
        "/gpfs/scratch.fast/u-1/x.$SLURM_JOB_ID",
        "/scratch/$USER/$SLURM_JOB_ID/work",
    ],
)
def test_scratch_template_keeps_profile_values_that_work_today(template: str) -> None:
    assert validate_scratch_template(template) == template


def test_scratch_template_trap_cannot_remove_a_parent(tmp_path: Path) -> None:
    """The finding's repro: the emitted SCRATCH/mkdir/trap lines under bash."""
    bash = _bash()
    home = tmp_path / "fakehome"
    (home / "projects").mkdir(parents=True)
    (home / "projects" / "keep.txt").write_text("keep\n", encoding="utf-8")
    template = "$HOME/$SLURM_JOB_ID/../projects"
    try:
        checked = validate_scratch_template(template)
    except SlurmError:
        checked = None
    if checked is not None:  # the old check let it through: run what the job would run
        script = "\n".join(
            [
                "set -euo pipefail",
                f'SCRATCH="{checked}"',
                'mkdir -p "$SCRATCH"',
                "trap 'rm -rf \"$SCRATCH\"' EXIT",
                "",
            ]
        )
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(home)}
        env["SLURM_JOB_ID"] = "42"
        subprocess.run([bash, "-c", script], env=env, check=False)
    assert (home / "projects" / "keep.txt").is_file(), "the EXIT trap removed a parent"
    assert checked is None


# --- SCRIPTS-09: manifest values in the sbatch header -----------------------------------


def test_manifest_names_with_a_trailing_newline_are_refused() -> None:
    payload = _payload()
    payload["name"] = "fake\n"
    with pytest.raises(ModelSpecError, match="name"):
        parse_manifest(payload, source=FAKE)

    payload = _payload()
    payload["models"]["default\n"] = payload["models"].pop("default")
    with pytest.raises(ModelSpecError, match="model keys"):
        parse_manifest(payload, source=FAKE)

    payload = _payload()
    options = payload["models"]["default"]["options"]
    options["extra\n"] = options.pop("extra")
    with pytest.raises(ModelSpecError, match="option names"):
        parse_manifest(payload, source=FAKE)


@pytest.mark.parametrize(
    "constraint",
    [
        'a100"\ntouch marker-constraint\n#',
        "a40\n",
        "a40\r",
        "a40 a100",
        'a40"',
        "a40;id",
        "a40$(id)",
        "a40`id`",
        "a40\\",
    ],
)
def test_slurm_constraint_must_be_a_feature_expression(constraint: str) -> None:
    payload = _payload()
    payload["models"]["default"]["resources"]["slurm_constraint"] = constraint
    with pytest.raises(ModelSpecError, match="slurm_constraint"):
        parse_manifest(payload, source=FAKE)


@pytest.mark.parametrize(
    "constraint",
    [
        "a40",
        "a40|a100",
        "intel&gpu",
        "[rack1|rack2|rack3]",
        "[rack1*2&rack2*4]",
        "(knl&snc4&flat)*4&haswell",
        "gpu_a100-80g.sxm",
    ],
)
def test_slurm_constraint_keeps_slurm_feature_syntax(
    roots: tuple[Path, Path, Scene], tmp_path: Path, constraint: str
) -> None:
    payload = _payload()
    payload["models"]["default"]["resources"]["slurm_constraint"] = constraint
    manifest = parse_manifest(payload, source=FAKE)
    plan = _plan(roots, manifest, tmp_path)
    assert f'#SBATCH --constraint="{constraint}"' in plan.script.splitlines()


@pytest.mark.parametrize(
    "secret", ["HF_TOKEN\ntouch marker-secrets", "HF_TOKEN\n", "HF-TOKEN", "1TOKEN", "A B"]
)
def test_secret_names_must_be_environment_variable_names(secret: str) -> None:
    payload = _payload()
    payload["environment"]["secrets"] = [secret]
    with pytest.raises(ModelSpecError, match="secrets"):
        parse_manifest(payload, source=FAKE)


def test_secret_names_that_work_today_still_parse(
    roots: tuple[Path, Path, Scene], tmp_path: Path
) -> None:
    payload = _payload()
    payload["environment"]["secrets"] = ["HF_TOKEN", "_PRIVATE", "wandb_api_key2"]
    manifest = parse_manifest(payload, source=FAKE)
    plan = _plan(roots, manifest, tmp_path)
    assert (
        "# secrets read from the job environment: HF_TOKEN, _PRIVATE, wandb_api_key2"
        in plan.script.splitlines()
    )


def test_plan_sbatch_refuses_header_values_a_parsed_manifest_cannot_hold(
    roots: tuple[Path, Path, Scene], tmp_path: Path
) -> None:
    """A Manifest built in code skips the parser; the emitter checks again."""
    manifest = load_manifest(FAKE)
    spec = manifest.models["default"]
    bad_constraint = replace(
        spec,
        resources=replace(spec.resources, slurm_constraint='a100"\ntouch marker\n#'),
    )
    with pytest.raises(CoperceiveError, match="slurm_constraint|constraint"):
        _plan(roots, replace(manifest, models={"default": bad_constraint}), tmp_path)

    bad_secret = replace(
        manifest, environment=replace(manifest.environment, secrets=("HF_TOKEN\ntouch marker",))
    )
    with pytest.raises(CoperceiveError, match="secret"):
        _plan(roots, bad_secret, tmp_path)

    with pytest.raises(CoperceiveError, match="name"):
        _plan(roots, replace(manifest, name="fake\n"), tmp_path)


# --- SCRIPTS-09 (round 2): every manifest string that reaches the job script ------------------

# The skeptic's payload: written raw inside --exclude='artifacts/a/<name>/***'.
TRANSIENT_INJECTION = "x'$(touch marker-transient)'"
BAD_TOKENS = [
    TRANSIENT_INJECTION,
    'x"$(touch marker)"',
    "a b",
    "a/b",
    "..",
    ".",
    "scratch\n",
    "a\\b",
    "a*b",
    "a\x00b",
]


@pytest.mark.parametrize("bad", BAD_TOKENS)
@pytest.mark.parametrize("transient", [True, False])
def test_artifact_names_must_be_plain_tokens(bad: str, transient: bool) -> None:
    payload = _payload(TWO)
    outputs = payload["models"]["a"]["outputs"]
    outputs["artifacts"] = ["marker", "scratch", bad]
    if transient:
        outputs["transient_artifacts"] = ["scratch", bad]
    with pytest.raises(ModelSpecError, match=r"artifacts: .*artifact names are"):
        parse_manifest(payload, source=TWO)


@pytest.mark.parametrize(
    "bad", [t for t in BAD_TOKENS if t != "a/b"] + ["nvidia/../cuda", "cuda;id", "cuda$HOME"]
)
def test_module_names_must_be_plain_tokens(bad: str) -> None:
    payload = _payload(TWO)
    payload["environment"]["modules"] = ["nvidia/cuda-11.8", bad]
    with pytest.raises(ModelSpecError, match=r"environment.modules: .*module names are"):
        parse_manifest(payload, source=TWO)


@pytest.mark.parametrize("name", ["det_json", "det_pkl", "track_query", "Seen", "v1.2-x"])
def test_artifact_names_that_work_today_still_plan(
    roots: tuple[Path, Path, Scene], tmp_path: Path, name: str
) -> None:
    payload = _payload(TWO)
    payload["models"]["a"]["outputs"]["artifacts"] = ["marker", name]
    payload["models"]["a"]["outputs"]["transient_artifacts"] = [name]
    payload["models"]["b"]["depends_on"][1]["artifact"] = name
    manifest = parse_manifest(payload, source=TWO)
    plan = _plan(roots, manifest, tmp_path, model="b")
    assert f"--exclude='artifacts/a/{name}/***'" in plan.script


@pytest.mark.parametrize(
    "module",
    ["nvidia/cuda-11.8", "nvidia/cuda-11.x_cudnn-8.6", "gcc/12.2.0", "CUDA/12.1+cu", "foss@2023a"],
)
def test_module_names_that_work_today_still_plan(
    roots: tuple[Path, Path, Scene], tmp_path: Path, module: str
) -> None:
    payload = _payload(TWO)
    payload["environment"]["modules"] = [module]
    plan = _plan(roots, parse_manifest(payload, source=TWO), tmp_path, model="b")
    assert f"module load {module}" in plan.script.splitlines()


def test_plan_sbatch_refuses_a_transient_artifact_a_parsed_manifest_cannot_hold(
    roots: tuple[Path, Path, Scene], tmp_path: Path
) -> None:
    """The skeptic's bypass, as a Manifest built in code (no parser): the emitter refuses."""
    manifest = load_manifest(TWO)
    a = manifest.models["a"]
    outputs = replace(
        a.outputs,
        artifacts=(*a.outputs.artifacts, TRANSIENT_INJECTION),
        transient_artifacts=(TRANSIENT_INJECTION,),
    )
    models = {"a": replace(a, outputs=outputs), "b": manifest.models["b"]}
    with pytest.raises(SlurmError, match=r"models\.a\.outputs\.transient_artifacts"):
        _plan(roots, replace(manifest, models=models), tmp_path, model="b")
    assert not (roots[1] / ".slurm").exists()


def test_plan_sbatch_refuses_a_dependency_model_key_a_parsed_manifest_cannot_hold(
    roots: tuple[Path, Path, Scene], tmp_path: Path
) -> None:
    """A dependency key reaches the header comment and `rsync "$SCRATCH/out/r1-<key>/"`."""
    manifest = load_manifest(TWO)
    bad = "a$(touch marker-key)"
    b = manifest.models["b"]
    models = {
        bad: replace(manifest.models["a"], key=bad),
        "b": replace(b, depends_on=tuple(replace(d, model=bad) for d in b.depends_on)),
    }
    with pytest.raises(SlurmError, match="model key"):
        _plan(roots, replace(manifest, models=models), tmp_path, model="b")


def test_plan_sbatch_refuses_a_module_a_parsed_manifest_cannot_hold(
    roots: tuple[Path, Path, Scene], tmp_path: Path
) -> None:
    manifest = load_manifest(FAKE)
    environment = replace(manifest.environment, modules=("cuda\ntouch marker-module",))
    with pytest.raises(SlurmError, match="environment.modules"):
        _plan(roots, replace(manifest, environment=environment), tmp_path)


def test_plan_sbatch_refuses_a_checkpoint_option_a_parsed_manifest_cannot_hold(
    roots: tuple[Path, Path, Scene], tmp_path: Path
) -> None:
    """The option name is written inside "$SCRATCH/checkpoints/<option>"."""
    manifest = load_manifest(CHECKPOINT_MANIFEST)
    spec = manifest.models["default"]
    assert spec.checkpoint is not None
    bad = replace(spec, checkpoint=replace(spec.checkpoint, option='c"$(touch marker)"'))
    with pytest.raises(SlurmError, match=r"checkpoint\.option"):
        _plan(roots, replace(manifest, models={"default": bad}), tmp_path)


# --- SCRIPTS-11: the run id of a sweep cell ---------------------------------------------


@pytest.mark.parametrize("bad", ["sweep\ntouch marker-sweep\n#", "a\rb", "a\x00b", "a\x7fb"])
def test_check_run_id_refuses_line_breaks_and_control_characters(bad: str) -> None:
    with pytest.raises(PredictionError, match="run_id"):
        check_run_id(bad)


@pytest.mark.parametrize("good", ["det-v1", "sweep-0a1b2c3d4e", "my run", "änderung", "r_1.2"])
def test_check_run_id_keeps_directory_names_that_work_today(good: str) -> None:
    assert check_run_id(good) == good


@pytest.mark.parametrize(
    "bad", ["r1\ntouch marker\n#", "r1 x", "r1$(id)", 'r1"', "r1;id", "", ".", ".."]
)
def test_plan_sbatch_refuses_an_unsafe_run_id(
    roots: tuple[Path, Path, Scene], tmp_path: Path, bad: str
) -> None:
    with pytest.raises(SlurmError, match="run id"):
        _plan(roots, load_manifest(FAKE), tmp_path, run_id=bad)


def _sweep_options(scenes: Path, out: Path, run_id: str, tmp_path: Path) -> SweepOptions:
    return SweepOptions(
        manifest=load_manifest(FAKE),
        scenes=scenes,
        out=out,
        run_id=run_id,
        grid=load_grid("pose_noise_xy: [0.0, 1.0]\nseeds: [0]"),
        evals=(default_eval(),),
        slurm=SlurmSettings(
            python=Path("/opt/venv/bin/python"),
            logs_dir=tmp_path / "logs",
            results_dir=tmp_path / "results",
            partition="test-partition",
            scratch_template=SCRATCH,
        ),
    )


@pytest.mark.parametrize("bad", ["sweep\ntouch marker-sweep\n#", "sweep$(id)", "my sweep"])
def test_sweep_slurm_refuses_an_unsafe_run_id(
    roots: tuple[Path, Path, Scene], tmp_path: Path, bad: str
) -> None:
    scenes, out, _ = roots
    with pytest.raises(SweepError, match="--run-id"):
        emit_slurm(_sweep_options(scenes, out, bad, tmp_path))
    assert not (out / ".slurm").exists()


def test_sweep_slurm_cli_refuses_a_run_id_with_a_line_break(
    roots: tuple[Path, Path, Scene], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    scenes, out, _ = roots
    with pytest.raises(SystemExit) as exc:
        main(
            [
                "robustness",
                "sweep",
                "pose_noise_xy: [0.0, 1.0]\nseeds: [0]",
                "--manifest",
                str(FAKE),
                "--scenes",
                str(scenes),
                "--out",
                str(out),
                "--run-id",
                "sweep\ntouch marker-sweep\n#",
                "--slurm",
                "--slurm-partition",
                "test-partition",
                "--slurm-scratch-template",
                SCRATCH,
                "--slurm-python",
                "/opt/venv/bin/python",
                "--slurm-logs",
                str(tmp_path / "logs"),
                "--slurm-results",
                str(tmp_path / "results"),
            ]
        )
    # Not an argparse usage error (exit 2, and its usage text names --run-id): the sweep's
    # own user-error exit (robustness/commands.py `_guarded`), with the rule the id broke.
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "usage:" not in err
    assert "--run-id: " in err
    assert "line break or control character" in err
    assert not list(out.glob(".slurm/*.sbatch"))


def test_sweep_slurm_keeps_a_plain_run_id(roots: tuple[Path, Path, Scene], tmp_path: Path) -> None:
    scenes, out, _ = roots
    paths = emit_slurm(_sweep_options(scenes, out, "job.v2_a-1", tmp_path))
    assert len(paths) == 2
    for path in paths:
        assert path.name.startswith("job.v2_a-1-")


# --- SCRIPTS-12: a checkpoint file name ending in a backslash ------------------------------


@pytest.mark.parametrize("name", ["weights\\", "wei\\ghts.pth", "weights\x01.pth"])
def test_checkpoint_name_with_a_backslash_or_control_character_is_refused(
    tmp_path: Path, name: str
) -> None:
    checkpoint = tmp_path / name
    checkpoint.write_bytes(b"fixture")
    manifest = load_manifest(CHECKPOINT_MANIFEST)
    spec = select_model(manifest, "default")
    with pytest.raises(SlurmError, match="checkpoint file name"):
        checkpoint_files(manifest, spec, RunOptions(options={"checkpoint": str(checkpoint)}))


@pytest.mark.parametrize(
    "name", ["net_epoch_bestval_at23.pth", "cobevt_lidar.zip", "model-v1.0+cu111.ckpt", "w=1.pt"]
)
def test_checkpoint_names_that_work_today_still_stage(
    roots: tuple[Path, Path, Scene], tmp_path: Path, name: str
) -> None:
    checkpoint = tmp_path / name
    checkpoint.write_bytes(b"fixture")
    manifest = load_manifest(CHECKPOINT_MANIFEST)
    plan = _plan(
        roots, manifest, tmp_path, options=RunOptions(options={"checkpoint": str(checkpoint)})
    )
    assert f'--option "checkpoint=$SCRATCH/checkpoints/checkpoint/{name}"' in plan.script
    path = tmp_path / "job.sbatch"
    path.write_text(plan.script, encoding="utf-8")
    done = subprocess.run([_bash(), "-n", str(path)], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


# --- slurm.py:458: the interpreter path in a comment ---------------------------------------


def test_interpreter_path_with_a_line_break_is_refused(
    roots: tuple[Path, Path, Scene], tmp_path: Path
) -> None:
    venv = tmp_path / "venv\ntouch marker-python\n#"
    (venv / "bin").mkdir(parents=True)
    python = venv / "bin" / "python"
    python.write_text("", encoding="utf-8")
    (venv / "pyvenv.cfg").write_text("version = 3.12.1\n", encoding="utf-8")
    with pytest.raises(SlurmError, match="interpreter"):
        _plan(roots, load_manifest(FAKE), tmp_path, python=python)


def test_interpreter_path_with_spaces_still_plans(
    roots: tuple[Path, Path, Scene], tmp_path: Path
) -> None:
    venv = tmp_path / "my venv"
    (venv / "bin").mkdir(parents=True)
    python = venv / "bin" / "python"
    python.write_text("", encoding="utf-8")
    (venv / "pyvenv.cfg").write_text("version = 3.12.1\n", encoding="utf-8")
    plan = _plan(roots, load_manifest(FAKE), tmp_path, python=python)
    assert f"module load python/3.12.1  # the interpreter of {python}" in plan.script


# --- ARC-6: check_bundle_assets.py check_deb ------------------------------------------------


def _ar(members: list[tuple[str, bytes]]) -> bytes:
    out = io.BytesIO()
    out.write(b"!<arch>\n")
    for name, data in members:
        header = (
            f"{name:<16}{0:<12}{0:<6}{0:<6}{0o100644:<8o}{len(data):<10}".encode("ascii") + b"`\n"
        )
        assert len(header) == 60
        out.write(header)
        out.write(data)
        if len(data) % 2:
            out.write(b"\n")
    return out.getvalue()


def _tar_gz(entries: list[tarfile.TarInfo | tuple[tarfile.TarInfo, bytes]]) -> bytes:
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w:gz") as archive:
        for entry in entries:
            if isinstance(entry, tuple):
                info, data = entry
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
            else:
                archive.addfile(entry)
    return raw.getvalue()


def _file(name: str, data: bytes) -> tuple[tarfile.TarInfo, bytes]:
    info = tarfile.TarInfo(name)
    info.mode = 0o644
    return info, data


def _dir(name: str) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.type = tarfile.DIRTYPE
    info.mode = 0o755
    return info


def _link(name: str, target: str) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.type = tarfile.SYMTYPE
    info.linkname = target
    return info


STATIC = "./usr/lib/Coperceive/resources/coperceive/app/static"


def _static_entries() -> list[tarfile.TarInfo | tuple[tarfile.TarInfo, bytes]]:
    index = (
        b'<link rel="stylesheet" href="./assets/app.css"><script src="./assets/app.js"></script>'
    )
    return [
        _dir("./"),
        _dir("./usr/"),
        _dir("./usr/lib/"),
        _dir("./usr/lib/Coperceive/"),
        _dir("./usr/lib/Coperceive/resources/"),
        _dir("./usr/lib/Coperceive/resources/python/"),
        _dir("./usr/lib/Coperceive/resources/python/bin/"),
        _file("./usr/lib/Coperceive/resources/python/bin/python3.12", b"#!fake\n"),
        _link("./usr/lib/Coperceive/resources/python/bin/python3", "python3.12"),
        _dir("./usr/lib/Coperceive/resources/coperceive/"),
        _dir("./usr/lib/Coperceive/resources/coperceive/app/"),
        _dir(f"{STATIC}/"),
        _dir(f"{STATIC}/assets/"),
        _file(f"{STATIC}/index.html", index),
        _file(f"{STATIC}/assets/app.js", b"console.log('ok')\n"),
        _file(f"{STATIC}/assets/app.css", b"body{}\n"),
        _file("./usr/bin/coperceive", b"\x7fELF fake"),
    ]


def _deb(path: Path, entries: list[tarfile.TarInfo | tuple[tarfile.TarInfo, bytes]]) -> Path:
    control = _tar_gz([_file("./control", b"Package: coperceive\n")])
    path.write_bytes(
        _ar(
            [
                ("debian-binary", b"2.0\n"),
                ("control.tar.gz", control),
                ("data.tar.gz", _tar_gz(entries)),
            ]
        )
    )
    return path


def _check_deb(deb: Path, tmp_base: Path) -> subprocess.CompletedProcess[str]:
    tmp_base.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, TMPDIR=str(tmp_base))
    return subprocess.run(
        [sys.executable, str(CHECKER), "--app", str(deb)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def test_check_deb_passes_a_tauri_shaped_package(tmp_path: Path) -> None:
    deb = _deb(tmp_path / "coperceive_0.1.0_amd64.deb", _static_entries())
    result = _check_deb(deb, tmp_path / "tmpbase")
    assert result.returncode == 0, result.stderr
    assert "viewer bundle check passed" in result.stdout


def test_check_deb_does_not_write_outside_its_temporary_directory(tmp_path: Path) -> None:
    tmp_base = tmp_path / "tmpbase"
    outside = tmp_path / "outside"
    outside.mkdir()
    entries = _static_entries()
    entries.append(_file("../ESCAPED_BY_DOTDOT.txt", b"escaped\n"))
    deb = _deb(tmp_path / "dotdot.deb", entries)
    result = _check_deb(deb, tmp_base)
    assert not (tmp_base / "ESCAPED_BY_DOTDOT.txt").exists(), "a '..' member left the tmp dir"
    assert result.returncode == 1

    entries = _static_entries()
    entries.append(_link("./up", str(outside)))
    entries.append(_file("./up/ESCAPED_THROUGH_SYMLINK.txt", b"escaped\n"))
    deb = _deb(tmp_path / "symlink.deb", entries)
    result = _check_deb(deb, tmp_base)
    assert not (outside / "ESCAPED_THROUGH_SYMLINK.txt").exists(), "a symlink was followed"
    assert result.returncode == 1


# --- ARC-8: maps fetch reads tar members whole ----------------------------------------------


def _package(path: Path, members: dict[str, bytes]) -> Path:
    with tarfile.open(path, "w:gz") as archive:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return path


def _xodr(town: str) -> bytes:
    return f'<?xml version="1.0"?>\n<OpenDRIVE><header name="{town}"/></OpenDRIVE>\n'.encode()


def test_maps_fetch_extracts_the_towns_of_a_package(tmp_path: Path) -> None:
    prefix = "CarlaUE4/Content/Carla/Maps/OpenDrive"
    package = _package(
        tmp_path / "carla.tar.gz", {f"{prefix}/{town}.xodr": _xodr(town) for town in CARLA_TOWNS}
    )
    dest = tmp_path / "dest"
    dest.mkdir()
    written = extract_towns(package, dest)
    assert [town for town, _ in written] == list(CARLA_TOWNS)
    for town, path in written:
        assert path == dest / f"{town}.xodr"
        assert path.read_bytes() == _xodr(town)
    assert sorted(p.name for p in dest.iterdir()) == sorted(f"{t}.xodr" for t in CARLA_TOWNS)


def test_maps_fetch_reads_members_in_bounded_chunks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sizes: list[int] = []
    original = tarfile.TarFile.extractfile

    def recording(self: tarfile.TarFile, member: str | tarfile.TarInfo) -> IO[bytes] | None:
        handle = original(self, member)
        if handle is None:
            return None
        read = handle.read

        def bounded(size: int | None = -1) -> bytes:
            asked = -1 if size is None else size  # read() and read(None) read it all
            sizes.append(asked)
            return read(asked)

        handle.read = bounded  # type: ignore[method-assign]
        return handle

    monkeypatch.setattr(tarfile.TarFile, "extractfile", recording)
    package = _package(tmp_path / "carla.tar.gz", {"x/Town03.xodr": _xodr("Town03") * 4096})
    dest = tmp_path / "dest"
    dest.mkdir()
    written = extract_towns(package, dest)
    assert [town for town, _ in written] == ["Town03"]
    assert (dest / "Town03.xodr").read_bytes() == _xodr("Town03") * 4096
    assert sizes, "the member was not read through extractfile"
    assert all(0 < size <= 1 << 20 for size in sizes), sizes


def test_maps_fetch_refuses_an_oversized_member(tmp_path: Path) -> None:
    raw = tmp_path / "bomb.tar"
    with tarfile.open(raw, "w") as archive:
        info = tarfile.TarInfo("CarlaUE4/Content/Carla/Maps/OpenDrive/Town07.xodr")
        info.size = MAX_MAP_BYTES + 1
        archive.addfile(info, io.BytesIO(b"\0" * info.size))
    package = tmp_path / "bomb.tar.gz"
    with raw.open("rb") as source, gzip.open(package, "wb") as target:
        shutil.copyfileobj(source, target)
    raw.unlink()
    assert package.stat().st_size < 1 << 20  # a small download that inflates past the cap
    dest = tmp_path / "dest"
    dest.mkdir()
    (dest / "Town07.xodr").write_bytes(_xodr("Town07"))  # an earlier good fetch stays
    with pytest.raises(ValueError, match="Town07.xodr"):
        extract_towns(package, dest)
    assert (dest / "Town07.xodr").read_bytes() == _xodr("Town07")
    assert sorted(p.name for p in dest.iterdir()) == ["Town07.xodr"]


def test_maps_fetch_cli_reports_an_oversized_member(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w:gz") as archive:
        info = tarfile.TarInfo("Town01.xodr")
        info.size = MAX_MAP_BYTES + 1
        archive.addfile(info, io.BytesIO(b"\0" * info.size))
    package = tmp_path / "carla.tar.gz"
    package.write_bytes(raw.getvalue())
    with pytest.raises(SystemExit) as exc:
        main(["maps", "fetch", "--dest", str(tmp_path / "dest"), "--package", str(package)])
    assert exc.value.code == 2
    assert "Town01.xodr" in capsys.readouterr().err
    assert not (tmp_path / "dest" / "Town01.xodr").exists()


# --- ARC-8 (round 2): tar header payloads and the member list ---------------------------------


def _gz(path: Path, raw: bytes) -> Path:
    path.write_bytes(gzip.compress(raw, compresslevel=9))
    return path


def _tar_bytes(fmt: int, members: list[tarfile.TarInfo | tuple[tarfile.TarInfo, bytes]]) -> bytes:
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=fmt) as archive:
        for entry in members:
            info, data = entry if isinstance(entry, tuple) else (entry, b"")
            archive.addfile(info, io.BytesIO(data))
    return raw.getvalue()


def _town(name: str, town: str = "Town03") -> tuple[tarfile.TarInfo, bytes]:
    data = _xodr(town)
    info = tarfile.TarInfo(name)
    info.size = len(data)
    return info, data


def test_maps_fetch_refuses_an_oversized_gnu_long_name(tmp_path: Path) -> None:
    """A GNU 'L' header payload is read whole by tarfile before any member is seen."""
    long_name = "a/" * (MAX_TAR_HEADER_BYTES // 2 + 1) + "Town03.xodr"
    package = _gz(
        tmp_path / "carla.tar.gz",
        _tar_bytes(tarfile.GNU_FORMAT, [tarfile.TarInfo(long_name), _town("x/Town03.xodr")]),
    )
    dest = tmp_path / "dest"
    dest.mkdir()
    with pytest.raises(ValueError, match="long name header"):
        extract_towns(package, dest)
    assert list(dest.iterdir()) == []


@pytest.mark.parametrize("kind", ["member", "global"])
def test_maps_fetch_refuses_an_oversized_pax_header(tmp_path: Path, kind: str) -> None:
    padding = {"comment": "x" * (MAX_TAR_HEADER_BYTES + 1)}
    raw = io.BytesIO()
    pax_headers = padding if kind == "global" else None
    with tarfile.open(
        fileobj=raw, mode="w", format=tarfile.PAX_FORMAT, pax_headers=pax_headers
    ) as archive:
        info = tarfile.TarInfo("x/readme")
        if kind == "member":
            info.pax_headers = padding
        archive.addfile(info)
        town, data = _town("x/Town03.xodr")
        archive.addfile(town, io.BytesIO(data))
    package = _gz(tmp_path / "carla.tar.gz", raw.getvalue())
    dest = tmp_path / "dest"
    dest.mkdir()
    with pytest.raises(ValueError, match="pax header"):
        extract_towns(package, dest)
    assert list(dest.iterdir()) == []


def test_maps_fetch_refuses_a_long_chain_of_header_extensions(tmp_path: Path) -> None:
    """Each pax header in front of a member is one more recursion in tarfile."""
    record = b"16 comment=abcd\n"
    chain: list[tarfile.TarInfo | tuple[tarfile.TarInfo, bytes]] = []
    for index in range(MAX_TAR_HEADER_CHAIN + 1):
        info = tarfile.TarInfo(f"PaxHeader/{index}")
        info.type = tarfile.XHDTYPE
        info.size = len(record)
        chain.append((info, record))
    chain.append(_town("x/Town03.xodr"))
    package = _gz(tmp_path / "carla.tar.gz", _tar_bytes(tarfile.GNU_FORMAT, chain))
    dest = tmp_path / "dest"
    dest.mkdir()
    with pytest.raises(ValueError, match="header extensions"):
        extract_towns(package, dest)
    assert list(dest.iterdir()) == []


def test_maps_fetch_refuses_a_gnu_sparse_member(tmp_path: Path) -> None:
    """A GNU sparse map can chain any number of extension blocks into one header."""
    info = tarfile.TarInfo("x/sparse")
    info.type = tarfile.GNUTYPE_SPARSE
    package = _gz(tmp_path / "carla.tar.gz", _tar_bytes(tarfile.GNU_FORMAT, [info]))
    dest = tmp_path / "dest"
    dest.mkdir()
    with pytest.raises(ValueError, match="sparse"):
        extract_towns(package, dest)


def test_maps_fetch_cli_reports_an_oversized_header(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    long_name = "a/" * (MAX_TAR_HEADER_BYTES // 2 + 1) + "b"
    package = _gz(
        tmp_path / "carla.tar.gz", _tar_bytes(tarfile.GNU_FORMAT, [tarfile.TarInfo(long_name)])
    )
    with pytest.raises(SystemExit) as exc:
        main(["maps", "fetch", "--dest", str(tmp_path / "dest"), "--package", str(package)])
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "long name header" in err
    assert "Traceback" not in err


def test_maps_fetch_keeps_long_names_and_pax_paths_of_a_real_package(tmp_path: Path) -> None:
    """Deep paths (a GNU long name, a pax path) well under the header limit still extract."""
    deep = "CarlaUE4/" + "Content/" * 40 + "Carla/Maps/OpenDrive"
    gnu = _tar_bytes(tarfile.GNU_FORMAT, [_town(f"{deep}/Town03.xodr", "Town03")])
    pax = _tar_bytes(tarfile.PAX_FORMAT, [_town(f"{deep}/Town06.xodr", "Town06")])
    for fmt_name, raw, town in (("gnu", gnu, "Town03"), ("pax", pax, "Town06")):
        dest = tmp_path / fmt_name
        dest.mkdir()
        written = extract_towns(_gz(tmp_path / f"{fmt_name}.tar.gz", raw), dest)
        assert [t for t, _ in written] == [town]
        assert (dest / f"{town}.xodr").read_bytes() == _xodr(town)


def test_maps_fetch_does_not_keep_every_member_header(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Millions of empty members cost a few MB compressed; the list of their headers must
    not grow with them."""
    peak: list[int] = []
    original = tarfile.TarFile.next

    def recording(self: tarfile.TarFile) -> tarfile.TarInfo | None:
        member = original(self)
        peak.append(len(cast(list[object], vars(self)["members"])))
        return member

    monkeypatch.setattr(tarfile.TarFile, "next", recording)
    members: list[tarfile.TarInfo | tuple[tarfile.TarInfo, bytes]] = [
        tarfile.TarInfo(f"empty/{index}") for index in range(2000)
    ]
    members.append(_town("x/Town03.xodr"))
    package = _gz(tmp_path / "carla.tar.gz", _tar_bytes(tarfile.GNU_FORMAT, members))
    dest = tmp_path / "dest"
    dest.mkdir()
    written = extract_towns(package, dest)
    assert [t for t, _ in written] == ["Town03"]
    assert peak and max(peak) <= 2, max(peak)


def test_maps_fetch_refuses_a_member_that_yields_more_than_its_header_says(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The copy's own cap, not only the header pre-check: a reader that keeps yielding past
    MAX_MAP_BYTES is stopped, nothing is renamed into place and no .part file is left."""
    original = tarfile.TarFile.extractfile

    def endless(self: tarfile.TarFile, member: str | tarfile.TarInfo) -> IO[bytes] | None:
        if original(self, member) is None:
            return None
        return io.BytesIO(b"\0" * (MAX_MAP_BYTES + 1))

    monkeypatch.setattr(tarfile.TarFile, "extractfile", endless)
    package = _package(tmp_path / "carla.tar.gz", {"x/Town05.xodr": _xodr("Town05")})
    dest = tmp_path / "dest"
    dest.mkdir()
    (dest / "Town05.xodr").write_bytes(_xodr("Town05"))
    with pytest.raises(ValueError, match="Town05.xodr"):
        extract_towns(package, dest)
    assert (dest / "Town05.xodr").read_bytes() == _xodr("Town05")
    assert sorted(p.name for p in dest.iterdir()) == ["Town05.xodr"]
