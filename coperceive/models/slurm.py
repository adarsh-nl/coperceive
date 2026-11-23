# SPDX-License-Identifier: Apache-2.0
"""``coperceive model run --slurm``: emit (and on request submit) the Slurm job for the same
batch run (ADR 0011 decision 5).

The script stages exactly the scene files and raw files the selected scenes need into the
cluster profile's ``scratch_template`` (for example ``/scratch/$USER/$SLURM_JOB_ID``) with
``rsync --files-from``, runs the same ``coperceive model run`` there, and copies the run home
before the trap wipes the scratch, then the trap removes it. Both the scratch template and
the partition come from the server profile: ``--slurm-scratch-template`` /
``COPERCEIVE_SLURM_SCRATCH_TEMPLATE`` and ``--slurm-partition`` /
``COPERCEIVE_SLURM_PARTITION``. There is no hard-coded site name, node or path; the profile
value is passed through until pipeline submission uses the profile directly. ``--time``
comes from the manifest's
``seconds_per_sample`` and ``staging_seconds`` until a run of the same plugin and model under
``--out`` exists, then from that run's ``provenance.timings``. The script carries no value
from the parent's environment: secret names appear in one comment, nothing else; compute
nodes install nothing and reach no index.
"""

from __future__ import annotations

import math
import re
import shlex
import subprocess
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from coperceive.errors import CoperceiveError
from coperceive.evaluation.predictions import list_runs, read_run_meta
from coperceive.model import Scene
from coperceive.models.batch import EXIT_EVAL, RunOptions, select_agents
from coperceive.models.inputs import raw_files, selected_sensors
from coperceive.models.spec import (
    Manifest,
    ModelSpec,
    ModelSpecError,
    check_artifact_name,
    check_model_key,
    check_module_name,
    check_options,
    check_secret_name,
    check_slurm_constraint,
)
from coperceive.repro.environment import environment_capture_lines
from coperceive.viewdata import pose_times

IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg"})
SLURM_DIR = ".slurm"
# What of the work directory comes home: every *.log at any depth and the artefacts, not the
# inputs or a toolkit copy. `--include='*/'` keeps directories traversable (an `--exclude='*'`
# alone would drop them) and `--prune-empty-dirs` drops the ones that end up empty.
WORK_COPY_FILTER = (
    "--include='*/'",
    "--include='*.log'",
    "--include='artifacts/***'",
    "--exclude='*'",
    "--prune-empty-dirs",
)
_SOFTWARE_PYTHON = re.compile(r"/software/python/([^/]+)/")


_SLURM_PARTITION_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-.,"
)


def validate_partition(partition: str) -> str:
    """Check the profile/CLI partition name before it is embedded in a script."""
    if not partition or not partition.strip():
        raise SlurmError(
            "Choose a Slurm partition for this run with --slurm-partition (or set "
            "COPERCEIVE_SLURM_PARTITION). Coperceive never guesses a site partition."
        )
    if any(ch.isspace() or ord(ch) < 32 for ch in partition):
        raise SlurmError(
            f"partition {partition!r} must not contain spaces, newlines or control characters."
        )
    if any(ch not in _SLURM_PARTITION_CHARS for ch in partition):
        raise SlurmError(
            f"partition {partition!r} has characters a Slurm partition name cannot contain; "
            "use the name the cluster lists (letters, digits, '-', '_', '.' and ',')."
        )
    return partition


_SCRATCH_PLAIN = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_./-")
# The variables a scratch template may name, as $NAME or ${NAME}: the same two the desktop
# app's validator (src-tauri/src/hpc.rs) and scripts/griffin/stage_viewer.py expand.
_SCRATCH_VARIABLES = frozenset({"USER", "SLURM_JOB_ID"})
_SHELL_NAME = re.compile(r"[A-Za-z0-9_]*")


def validate_scratch_template(template: str) -> str:
    """Check a cluster profile's scratch template before it is embedded in a script.

    The template is the user/profile choice (for example
    ``/scratch/$USER/$SLURM_JOB_ID``); it must be a safe shell word with a
    per-job variable so parallel jobs never share a directory. The job's EXIT trap
    removes this directory, so it may name only ``$USER`` and ``$SLURM_JOB_ID`` (or
    ``${USER}``/``${SLURM_JOB_ID}``), never a ``..`` segment or a ``~`` (which stays
    literal inside the job's double quotes): those could make the trap remove a parent.
    """
    if not template or not template.strip():
        raise SlurmError(
            "No scratch template for the job. Set the cluster profile's scratch_template "
            "(for example /scratch/$USER/$SLURM_JOB_ID) and pass it with "
            "--slurm-scratch-template or COPERCEIVE_SLURM_SCRATCH_TEMPLATE."
        )
    names = _scratch_variables(template)
    if any(segment == ".." for segment in template.split("/")):
        raise SlurmError(
            f"scratch template {template!r} has a '..' segment; the job's EXIT trap removes "
            "the scratch directory, so it must not climb to a parent."
        )
    if "SLURM_JOB_ID" not in names:
        raise SlurmError(
            f"scratch template {template!r} needs $SLURM_JOB_ID so each job gets its own "
            "directory (for example /scratch/$USER/$SLURM_JOB_ID)."
        )
    return template


def _scratch_variables(template: str) -> list[str]:
    """The variable names ``template`` uses, in order; a SlurmError for any other text
    than plain path characters, ``$USER``/``${USER}`` and ``$SLURM_JOB_ID``/``${SLURM_JOB_ID}``.
    A ``$NAME`` takes the longest name, as bash does: ``$USER_x`` is ``USER_x``."""
    names: list[str] = []
    rest = template
    while True:
        index = rest.find("$")
        plain = rest if index < 0 else rest[:index]
        for ch in plain:
            if ch in _SCRATCH_PLAIN:
                continue
            hint = (
                " A '~' is not expanded inside the job's quotes; give the absolute path."
                if ch == "~"
                else ""
            )
            raise SlurmError(
                f"scratch template {template!r} contains {ch!r}, which is unsafe in a job "
                "script. Use letters, digits, '_', '.', '/', '-' and the tokens $USER, "
                f"${{USER}}, $SLURM_JOB_ID, ${{SLURM_JOB_ID}}.{hint}"
            )
        if index < 0:
            return names
        after = rest[index + 1 :]
        if after.startswith("{"):
            end = after.find("}")
            if end < 0:
                raise SlurmError(
                    f"scratch template {template!r} has an unterminated ${{...}} token; "
                    "close it with '}'."
                )
            name = after[1:end]
            token = "${" + name + "}"
            rest = after[end + 1 :]
        else:
            match = _SHELL_NAME.match(after)
            name = "" if match is None else match.group(0)
            token = "$" + name
            rest = after[len(name) :]
        if name not in _SCRATCH_VARIABLES:
            raise SlurmError(
                f"scratch template {template!r} uses {token!r}, which the job does not "
                "expand for scratch. Use $USER or $SLURM_JOB_ID."
            )
        names.append(name)


_RUN_ID = re.compile(r"[A-Za-z0-9._-]+")


def validate_run_id(run_id: str) -> str:
    """Check a run id before it is written into ``#SBATCH --job-name``, the header comment
    and the ``.slurm/<run_id>.*`` file names: the rule ``coperceive model run`` applies."""
    if run_id in {"", ".", ".."} or _RUN_ID.fullmatch(run_id) is None:
        raise SlurmError(
            f"run id {run_id!r} cannot go into a job script; a run id may contain only "
            "letters, digits, '.', '_' and '-'."
        )
    return run_id


def _has_control_character(text: str) -> bool:
    """A line break or any other control character (Unicode category Cc)."""
    return any(unicodedata.category(ch) == "Cc" for ch in text)


class SlurmError(CoperceiveError):
    """The job could not be planned or submitted."""


def validate_logs_dir(logs_dir: Path) -> Path:
    """Check the ``--slurm-logs`` folder before it becomes ``#SBATCH --output=``/``--error=``.

    Slurm reads an ``#SBATCH`` value up to the first blank, so a folder with a space, a tab
    or a line break would send the job's logs to another path (or break the script). It is
    refused with the reason instead of being quoted, which ``#SBATCH`` lines do not honour.
    """
    text = str(logs_dir.resolve())
    if any(ch.isspace() or ord(ch) < 32 or ord(ch) == 0x7F for ch in text):
        raise SlurmError(
            f"The Slurm log folder {text!r} (--slurm-logs) contains a space or a control "
            "character; #SBATCH --output/--error cannot hold one. Choose a log folder whose "
            "path has no spaces."
        )
    return logs_dir


def interpreter_module(python: Path) -> str | None:
    """The ``module load`` the interpreter needs on a compute node, from the venv's
    ``pyvenv.cfg`` (``home = /software/python/<version>/bin`` on this cluster, else the
    ``version`` line); None when ``python`` is not inside a venv (Adarsh, 2026-10-18)."""
    # The venv's bin/python is itself a symlink to the base interpreter, so resolve the
    # directory, not the file: <venv>/bin -> <venv>/pyvenv.cfg.
    cfg = python.parent.resolve().parent / "pyvenv.cfg"
    if not cfg.is_file():
        cfg = python.resolve().parent.parent / "pyvenv.cfg"
    if not cfg.is_file():
        return None
    home = version = None
    for line in cfg.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.partition("=")
        if not sep:
            continue
        if key.strip() == "home":
            home = value.strip()
        elif key.strip() == "version":
            version = value.strip()
    if home is not None:
        match = _SOFTWARE_PYTHON.search(home + "/")
        if match is not None:
            return f"python/{match.group(1)}"
    if version:
        return f"python/{version}"
    return None


def coperceive_root() -> Path:
    """The repository root of the running coperceive, for the job's PYTHONPATH: the shared
    venv's editable install points at the main checkout, not at a worktree."""
    import coperceive

    return Path(coperceive.__file__).resolve().parents[1]


def model_closure(manifest: Manifest, spec: ModelSpec, options: RunOptions) -> list[ModelSpec]:
    """``spec`` and, unless ``--artifacts`` supplies them or auto dependencies are off, the
    models its ``depends_on`` entries need (recursively), each once, dependencies first."""
    out: list[ModelSpec] = []
    seen: set[str] = set()

    def visit(current: ModelSpec) -> None:
        if current.key in seen:
            return
        seen.add(current.key)
        if options.artifacts_dir is None and options.auto_dependencies:
            for dependency in current.depends_on:
                visit(manifest.models[dependency.model])
        out.append(current)

    visit(spec)
    return out


def work_copy_filter(transient: Sequence[tuple[str, str]] = ()) -> tuple[str, ...]:
    """The rsync filter for the work directory's logs and artefacts. Transient artefacts
    (``(model, name)`` pairs) are excluded first: rsync takes the first rule that matches."""
    excludes = tuple(f"--exclude='artifacts/{model}/{name}/***'" for model, name in transient)
    return excludes + WORK_COPY_FILTER


def time_limit(
    seconds_per_sample: float,
    staging_seconds: float,
    samples: int,
    conversion_seconds: float = 0.0,
) -> str:
    """``staging + conversion + per_sample × samples``, rounded up to the next 600 s, doubled,
    ``HH:MM:SS``. Never the whole worker time over the samples (Adarsh, 2026-10-19)."""
    estimate = staging_seconds + conversion_seconds + seconds_per_sample * samples
    rounded = max(600, int(math.ceil(estimate / 600.0)) * 600)
    total = rounded * 2
    hours, rest = divmod(total, 3600)
    minutes, seconds = divmod(rest, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def estimate_from_runs(
    out_root: Path, plugin: str, model: str
) -> tuple[float, float, float] | None:
    """``(per_sample_s, staging_s, conversion_s)`` of the newest run of ``plugin``/``model``
    under ``out_root``, by ``provenance.ended_utc``; None without one. Only a run whose child
    reported ``inference_s`` and ``conversion_s`` counts: ``per_sample_s`` of any other run
    is the whole worker time over its frames, which must never become an estimate."""
    best: tuple[str, float, float, float] | None = None
    for run_id in list_runs(out_root):
        try:
            provenance = read_run_meta(out_root, run_id).provenance
        except CoperceiveError:
            continue
        if provenance is None or provenance.get("plugin") != plugin:
            continue
        if provenance.get("model") != model:
            continue
        raw_timings = provenance.get("timings")
        if not isinstance(raw_timings, Mapping):
            continue
        timings = cast(Mapping[str, object], raw_timings)
        per_sample = timings.get("per_sample_s")
        staging = timings.get("staging_s")
        conversion = timings.get("conversion_s")
        inference = timings.get("inference_s")
        ended = str(provenance.get("ended_utc") or "")
        numbers = (per_sample, staging, conversion, inference)
        if not all(isinstance(n, (int, float)) and not isinstance(n, bool) for n in numbers):
            continue
        assert isinstance(per_sample, (int, float)) and isinstance(staging, (int, float))
        assert isinstance(conversion, (int, float))
        if per_sample <= 0:
            continue
        if best is None or ended > best[0]:
            best = (ended, float(per_sample), float(staging), float(conversion))
    return None if best is None else (best[1], best[2], best[3])


def staged_files(
    scenes_root: Path,
    scenes: Mapping[str, Scene],
    spec: ModelSpec,
    *,
    raw_root: Path | None,
    agent_ids: Mapping[str, Sequence[str]],
) -> tuple[list[str], list[str]]:
    """Scene files (every file except image bytes, and except ``points.bin`` when ``lidar``
    is not a modality) and raw files, relative, sorted, unique."""
    scene_files: set[str] = set()
    raw: set[str] = set()
    lidar = "lidar" in spec.inputs.modalities
    for scene_id, scene in scenes.items():
        directory = scenes_root / scene_id
        for path in directory.rglob("*"):
            if not path.is_file() or path.is_symlink():
                continue
            if path.suffix.lower() in IMAGE_SUFFIXES:
                continue
            if path.name == "points.bin" and not lidar:
                continue
            scene_files.add(str(path.relative_to(scenes_root)))
        if raw_root is None or not spec.inputs.raw:
            continue
        by_id = {agent.agent_id: agent for agent in scene.agents}
        ids = agent_ids.get(scene_id, ())
        sensors = selected_sensors(scene, spec, ids)
        for agent_id in ids:
            agent = by_id[agent_id]
            sensor_ids = [s.sensor_id for s in sensors if s.agent_id == agent_id]
            for time_us in pose_times(scene):
                for item in raw_files(
                    spec,
                    scene_id=scene_id,
                    agent_id=agent_id,
                    agent_type=agent.agent_type.value,
                    sensor_ids=sensor_ids,
                    time_us=time_us,
                    raw_root=raw_root,
                ):
                    raw.add(item.relative)
    return sorted(scene_files), sorted(raw)


@dataclass(frozen=True)
class SlurmPlan:
    job_name: str
    scratch_template: str
    partition: str
    gres: str | None
    cpus: int
    mem_gb: int
    interpreter_module: str | None
    time: str
    constraint: str | None
    modules: tuple[str, ...]
    scene_files: tuple[str, ...]
    raw_files: tuple[str, ...]
    command: tuple[str, ...]
    script: str
    estimate_source: str
    # (option name, absolute source path, staged shell path) for a model whose checkpoint is
    # a file named by an option. `checkpoint: {option: NAME}` in the manifest.
    checkpoints: tuple[tuple[str, str, str], ...] = ()


def checkpoint_files(
    manifest: Manifest, spec: ModelSpec, options: RunOptions
) -> tuple[tuple[str, str, str], ...]:
    """The checkpoint file a model's `checkpoint` key names, staged for scratch.

    Returns ``(option name, absolute source path, staged shell path)``. The staged path is a
    shell word under ``$SCRATCH/checkpoints/<option>/``; the inner command's
    ``--option <name>=<staged>`` points at it. A model without the key stages nothing.
    """
    checkpoint = spec.checkpoint
    if checkpoint is None:
        return ()
    for member in manifest.models.values():
        if member is not spec and member.checkpoint is not None:
            raise SlurmError(
                f"model {member.key} declares a checkpoint option, but only the requested "
                f"model's checkpoint is staged; run {member.key} as its own --slurm job"
            )
    effective = check_options(spec, options.options)
    value = effective.get(checkpoint.option)
    if not isinstance(value, str) or not value:
        raise SlurmError(
            f"model {spec.key} needs --option {checkpoint.option}=FILE (declared by "
            f"models.{spec.key}.checkpoint); it is staged into the job's scratch"
        )
    source = Path(value).expanduser()
    if not source.is_file():
        raise SlurmError(f"--option {checkpoint.option}={value!r} is not a file to stage")
    name = source.name
    # The staged path sits inside "..." in the job script: a backslash there would escape
    # the closing quote, and a control character has no place in a script line.
    if not name or any(
        ch.isspace() or ch in "\"'`$&;|<>()\\" or unicodedata.category(ch) == "Cc" for ch in name
    ):
        raise SlurmError(
            f"checkpoint file name {name!r} has characters a shell word cannot carry safely"
        )
    staged = f"$SCRATCH/checkpoints/{checkpoint.option}/{name}"
    return ((checkpoint.option, str(source.resolve()), staged),)


def _check_header_values(manifest: Manifest, spec: ModelSpec, run_id: str, python: Path) -> None:
    """Every manifest string and CLI value the script writes outside ``shlex.quote``.

    ``parse_manifest`` already refuses these; a Manifest built in code skips the parser,
    so the emitter checks again before any of them reaches a script line: the job name,
    the constraint, secret names, module names, the key of every model the job runs (header
    comment, ``"$SCRATCH/out/<run_id>-<key>/"``), their transient artefact names (inside
    ``--exclude='artifacts/<key>/<name>/***'``) and the checkpoint option name (inside
    ``"$SCRATCH/checkpoints/<option>"``).
    """
    job_name = f"model-{manifest.name}-{run_id}"
    if _RUN_ID.fullmatch(job_name) is None:
        raise SlurmError(
            f"job name {job_name!r} cannot go into #SBATCH --job-name; the manifest name "
            f"{manifest.name!r} must be [a-z0-9_-]+."
        )
    if spec.resources.slurm_constraint is not None:
        check_slurm_constraint(
            spec.resources.slurm_constraint, f"models.{spec.key}.resources.slurm_constraint"
        )
    for secret in manifest.environment.secrets:
        check_secret_name(secret, "environment.secrets")
    try:
        for module in manifest.environment.modules:
            check_module_name(module, "environment.modules")
        for key, member in manifest.models.items():
            check_model_key(key, "models")
            check_model_key(member.key, "models")
            for name in member.outputs.transient_artifacts:
                check_artifact_name(name, f"models.{key}.outputs.transient_artifacts")
            for dependency in member.depends_on:
                check_model_key(dependency.model, f"models.{key}.depends_on")
                check_artifact_name(dependency.artifact, f"models.{key}.depends_on")
            if member.checkpoint is not None:
                check_model_key(member.checkpoint.option, f"models.{key}.checkpoint.option")
    except ModelSpecError as error:
        raise SlurmError(f"{error}; this manifest value cannot go into a job script") from None
    if _has_control_character(str(python)):
        raise SlurmError(
            f"the interpreter path {str(python)!r} has a line break or control character; "
            "it cannot go into a job script (pass --slurm-python with a plain path)."
        )


def plan_sbatch(
    manifest: Manifest,
    spec: ModelSpec,
    *,
    scenes_root: Path,
    scenes: Mapping[str, Scene],
    run_id: str,
    out_root: Path,
    options: RunOptions,
    python: Path,
    logs_dir: Path,
    results_dir: Path,
    partition: str,
    scratch_template: str,
    repo_root: Path | None = None,
) -> SlurmPlan:
    """The sbatch of ADR 0011 decision 5 for ``coperceive model run`` over ``scenes``."""
    run_id = validate_run_id(run_id)
    scratch_template = validate_scratch_template(scratch_template)
    if options.eval_protocol not in (None, "default", "legacy"):
        raise SlurmError(
            f"model run --protocol must be default or legacy, got {options.eval_protocol!r}"
        )
    # Validate every option before anything is written: an unknown option, a bad value or a
    # required option with no value (a manifest option with no default) is refused here,
    # naming the option, instead of on the compute node after the job was submitted.
    check_options(spec, options.options)
    _check_header_values(manifest, spec, run_id, python)
    validate_logs_dir(logs_dir)
    samples = sum(len(pose_times(scene)) for scene in scenes.values())
    closure = model_closure(manifest, spec, options)
    scene_set: set[str] = set()
    raw_set: set[str] = set()
    per_sample = staging = conversion = 0.0
    parts: list[str] = []
    sources: list[str] = []
    for member in closure:
        requested = options.agent_ids if member is spec else None
        member_agents = {
            sid: select_agents(scene, member, requested) for sid, scene in scenes.items()
        }
        scene_files_m, raw_m = staged_files(
            scenes_root, scenes, member, raw_root=options.raw_root, agent_ids=member_agents
        )
        scene_set.update(scene_files_m)
        raw_set.update(raw_m)
        measured = estimate_from_runs(out_root, manifest.name, member.key)
        if measured is not None:
            m_per, m_stage, m_conv = measured
            newest = f"{manifest.name} {member.key}"
            sources.append(f"provenance.timings of the newest {newest} run under {out_root}")
        else:
            m_per = member.resources.seconds_per_sample
            m_stage = member.resources.staging_seconds
            m_conv = member.resources.conversion_seconds
            sources.append(
                "the manifest's resources (no earlier run with a timing split under --out)"
            )
        per_sample += m_per
        staging += m_stage
        conversion += m_conv
        parts.append(
            f"{member.key} {m_stage:g} s staging + {m_conv:g} s conversion + {m_per:g} s/sample"
        )
    scene_files = sorted(scene_set)
    raw = sorted(raw_set)
    source = (
        sources[0]
        if len(sources) == 1
        else "; ".join(
            f"{member.key}: {text}" for member, text in zip(closure, sources, strict=True)
        )
    )
    transient = [
        (member.key, name) for member in closure for name in member.outputs.transient_artifacts
    ]
    limit = time_limit(per_sample, staging, samples, conversion)
    gpu = spec.resources.gpu
    partition = validate_partition(partition)
    job_name = f"model-{manifest.name}-{run_id}"
    slurm_dir = out_root.resolve() / SLURM_DIR
    checkpoints = checkpoint_files(manifest, spec, options)
    staged_checkpoints = {option: staged for option, _source, staged in checkpoints}
    command = _run_command(
        manifest,
        spec,
        run_id=run_id,
        options=options,
        python=python,
        raw=bool(raw),
        staged_checkpoints=staged_checkpoints,
    )
    lines: list[str] = [
        "#!/bin/bash",
        f"# {job_name}: emitted by coperceive model run in Slurm mode; --time from {source}:",
        f"#   {staging:g} s staging + {conversion:g} s conversion + {per_sample:g} s/sample x "
        f"{samples} samples, rounded up to 10 min, doubled.",
        *(
            [f"#   summed over the models this run needs: {'; '.join(parts)}."]
            if len(closure) > 1
            else []
        ),
        "# Compute nodes install nothing and reach no index; the plugin is already in the .venv.",
        f"#SBATCH --job-name={job_name}",
        f"#SBATCH --partition={partition}",
    ]
    if gpu:
        lines.append("#SBATCH --gres=gpu:1")
    lines.append(f"#SBATCH --cpus-per-task={spec.resources.cpus}")
    lines.append(f"#SBATCH --mem={spec.resources.memory_gb}G")
    lines.append(f"#SBATCH --time={limit}")
    if spec.resources.slurm_constraint:
        lines.append(f'#SBATCH --constraint="{spec.resources.slurm_constraint}"')
    lines.append(f"#SBATCH --output={logs_dir.resolve()}/%x-%j.out")
    lines.append(f"#SBATCH --error={logs_dir.resolve()}/%x-%j.err")
    lines.append("#SBATCH --mail-type=END,FAIL")
    if manifest.environment.secrets:
        lines.append(
            "# secrets read from the job environment: " + ", ".join(manifest.environment.secrets)
        )
    lines.extend(
        [
            "",
            "set -euo pipefail",
            f'SCRATCH="{scratch_template}"',
            f"RUN_ID={shlex.quote(run_id)}",
            'mkdir -p "$SCRATCH"',
            "trap 'rm -rf \"$SCRATCH\"' EXIT",
            'export TMPDIR="$SCRATCH/tmp"',
            'mkdir -p "$TMPDIR" "$SCRATCH/scenes" "$SCRATCH/out" "$SCRATCH/work"',
            "",
            "module purge",
        ]
    )
    interpreter = interpreter_module(python)
    if interpreter is not None:
        lines.append(f"module load {shlex.quote(interpreter)}  # the interpreter of {python}")
    for name in manifest.environment.modules:
        lines.append(f"module load {shlex.quote(name)}")
    root = coperceive_root() if repo_root is None else repo_root.resolve()
    lines.append("")
    lines.append(
        f"export PYTHONPATH={shlex.quote(str(root))}  # the coperceive that emitted this job, "
        "not the venv's editable target"
    )
    lines.append("")
    lines.append("STAGE_START=$SECONDS")
    lines.append(
        f"rsync -a --files-from={shlex.quote(str(slurm_dir / f'{run_id}.scenes.txt'))} "
        f'{shlex.quote(str(scenes_root.resolve()) + "/")} "$SCRATCH/scenes/"'
    )
    if raw and options.raw_root is not None:
        lines.append('mkdir -p "$SCRATCH/raw"')
        lines.append(
            f"rsync -a --files-from={shlex.quote(str(slurm_dir / f'{run_id}.raw.txt'))} "
            f'{shlex.quote(str(options.raw_root.resolve()) + "/")} "$SCRATCH/raw/"'
        )
    if checkpoints:
        lines.append("# The checkpoint archive is staged with the inputs; the run below reads it")
        lines.append("# from scratch through the rewritten --option, never from the shared path.")
        for option, source, _staged in checkpoints:
            lines.append(f'mkdir -p "$SCRATCH/checkpoints/{option}"')
            lines.append(f"rsync -a {shlex.quote(source)} " + f'"$SCRATCH/checkpoints/{option}/"')
    lines.append("STAGING=$((SECONDS - STAGE_START))  # rsync wall time -> timings.staging_s")
    lines.append("")
    results = results_dir.resolve()
    target = out_root.resolve() / run_id
    lines.extend(
        [
            "# The run's exit status is kept: a partial run (3 or 4) still comes home below.",
            "set +e",
            " ".join(command),
            "RC=$?",
            "set -e",
            'echo "model run exit $RC"',
            "",
            'mkdir -p "$SCRATCH/out/$RUN_ID"',
            *environment_capture_lines(
                '"$SCRATCH/out/$RUN_ID/environment.json"',
                python=shlex.quote(str(python)),
            ),
            "",
            f'mkdir -p {shlex.quote(str(results))}/"$SLURM_JOB_ID"',
            f'rsync -a "$SCRATCH/out/" {shlex.quote(str(results))}/"$SLURM_JOB_ID"/',
            "# The work directory's logs and artefacts, not its inputs or any toolkit copy.",
            "rsync -a " + " ".join(work_copy_filter(transient)) + " "
            f'"$SCRATCH/work/" {shlex.quote(str(results))}/"$SLURM_JOB_ID"/work/',
            f"mkdir -p {shlex.quote(str(target))}",
            f'rsync -a "$SCRATCH/out/$RUN_ID/" {shlex.quote(str(target))}/',
            *[
                line
                for member in closure
                if member is not spec  # a dependency is a run of its own: <run_id>-<model>
                for line in (
                    f"mkdir -p {shlex.quote(str(out_root.resolve() / f'{run_id}-{member.key}'))}",
                    f'rsync -a "$SCRATCH/out/{run_id}-{member.key}/" '
                    f"{shlex.quote(str(out_root.resolve() / f'{run_id}-{member.key}'))}/",
                )
            ],
            *(
                evaluate_lines(python, options, results=results, target=target)
                if options.evaluate
                else []
            ),
            # The path is its own shell word: a quoted word inside "..." would not be.
            f'echo "done:" {shlex.quote(str(target))} "(exit $RC)"',
            'exit "$RC"',
            "",
        ]
    )
    return SlurmPlan(
        job_name=job_name,
        scratch_template=scratch_template,
        partition=partition,
        gres="gpu:1" if gpu else None,
        cpus=spec.resources.cpus,
        mem_gb=spec.resources.memory_gb,
        interpreter_module=interpreter,
        time=limit,
        constraint=spec.resources.slurm_constraint,
        modules=manifest.environment.modules,
        scene_files=tuple(scene_files),
        raw_files=tuple(raw),
        command=tuple(command),
        script="\n".join(lines),
        estimate_source=source,
        checkpoints=checkpoints,
    )


def evaluate_lines(python: Path, options: RunOptions, *, results: Path, target: Path) -> list[str]:
    """``--evaluate`` in the job: score the run after it has come home.

    The model's run line is unchanged. Its output is copied home first (above), so a
    scoring step stopped by ``--time`` or memory never costs a finished run. Then
    ``eval run`` scores it on the scenes this job staged, and the run directory, now with
    ``metrics.json`` and ``evaluation.json``, is copied home again. A run without
    ``run.json`` counts as a scoring failure, not a skip. A model failure keeps its code;
    a complete run whose scoring failed exits 5 (spec §Exit codes).
    """
    argv = [
        shlex.quote(str(python)),
        "-m",
        "coperceive",
        "eval",
        "run",
        '"$RUN_ID"',
        "--root",
        '"$SCRATCH/scenes"',
        "--predictions",
        '"$SCRATCH/out"',
    ]
    if options.eval_protocol is not None:
        argv.extend(["--protocol", shlex.quote(options.eval_protocol)])
    if options.eval_config is not None:
        argv.extend(["--config", shlex.quote(str(options.eval_config.resolve()))])
    return [
        "",
        "# --evaluate: score the run, as 'eval run' does, on the scenes staged above.",
        "EVAL_RC=0",
        'if [ -f "$SCRATCH/out/$RUN_ID/run.json" ]; then',
        "  set +e",
        "  " + " ".join(argv),
        "  EVAL_RC=$?",
        "  set -e",
        '  echo "eval run exit $EVAL_RC"',
        f'  rsync -a "$SCRATCH/out/" {shlex.quote(str(results))}/"$SLURM_JOB_ID"/',
        f'  rsync -a "$SCRATCH/out/$RUN_ID/" {shlex.quote(str(target))}/',
        "else",
        "  # No run.json means nothing to score: a failure, never a silent skip.",
        '  echo "eval run skipped: no run.json for $RUN_ID" >&2',
        "  EVAL_RC=1",
        "fi",
        "# The model's own failure wins; a complete run whose scoring failed exits 5.",
        f'if [ "$RC" -eq 0 ] && [ "$EVAL_RC" -ne 0 ]; then RC={EXIT_EVAL}; fi',
    ]


def _run_command(
    manifest: Manifest,
    spec: ModelSpec,
    *,
    run_id: str,
    options: RunOptions,
    python: Path,
    raw: bool,
    staged_checkpoints: Mapping[str, str] | None = None,
) -> list[str]:
    """The same ``model run`` against the staged copy; never ``--slurm``.

    An option in ``staged_checkpoints`` is rendered as its staged path under ``$SCRATCH``
    (double-quoted so the job expands it); other option values are quoted as usual.
    """
    assert manifest.source is not None
    argv: list[str] = [
        shlex.quote(str(python)),
        "-m",
        "coperceive",
        "model",
        "run",
        "--manifest",
        shlex.quote(str(manifest.source.resolve())),
        "--scenes",
        '"$SCRATCH/scenes"',
        "--out",
        '"$SCRATCH/out"',
        "--run-id",
        shlex.quote(run_id),
        "--work",
        '"$SCRATCH/work"',
        "--staging-seconds",
        '"$STAGING"',
        "--model",
        shlex.quote(spec.key),
    ]
    if options.eval_protocol is not None:
        argv.extend(["--protocol", shlex.quote(options.eval_protocol)])
    if raw:
        argv.extend(["--raw-root", '"$SCRATCH/raw"'])
    if options.agent_ids:
        argv.extend(["--agents", shlex.quote(",".join(options.agent_ids))])
    if options.source:
        argv.extend(["--source", shlex.quote(options.source)])
    if options.scene_ids:
        argv.extend(["--scene-ids", shlex.quote(",".join(options.scene_ids))])
    for root in options.media_roots:
        argv.extend(["--media-root", shlex.quote(str(root.resolve()))])
    if options.artifacts_dir is not None:
        argv.extend(["--artifacts", shlex.quote(str(options.artifacts_dir.resolve()))])
    if not options.auto_dependencies:
        argv.append("--no-auto-dependencies")
    staged = staged_checkpoints or {}
    for key, value in options.options.items():
        if key in staged:
            argv.extend(["--option", f'"{key}={staged[key]}"'])
        else:
            argv.extend(["--option", shlex.quote(f"{key}={value}")])
    if options.frame_timeout_s is not None:
        argv.extend(["--frame-timeout", f"{options.frame_timeout_s:g}"])
    if options.run_timeout_s is not None:
        argv.extend(["--run-timeout", f"{options.run_timeout_s:g}"])
    if not options.cache:
        argv.append("--no-cache")
    if options.hash_inputs:
        argv.append("--hash-inputs")
    if options.force:
        argv.append("--force")
    if options.perturbation is not None:
        argv.extend(["--perturb", shlex.quote(options.perturbation.canonical())])
    if options.plugin_python is not None:
        # Exactly as given: the node sees the path the user checked on the login node.
        argv.extend(["--plugin-python", shlex.quote(str(options.plugin_python))])
    return argv


def write_plan(plan: SlurmPlan, out_root: Path, run_id: str) -> Path:
    """``<out_root>/.slurm/<run_id>.sbatch`` plus the two ``--files-from`` lists."""
    run_id = validate_run_id(run_id)
    directory = out_root / SLURM_DIR
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{run_id}.scenes.txt").write_text(
        "".join(f"{item}\n" for item in plan.scene_files), encoding="utf-8"
    )
    if plan.raw_files:
        (directory / f"{run_id}.raw.txt").write_text(
            "".join(f"{item}\n" for item in plan.raw_files), encoding="utf-8"
        )
    path = directory / f"{run_id}.sbatch"
    path.write_text(plan.script, encoding="utf-8")
    return path


def submit(path: Path) -> str:
    """``sbatch <path>`` as an argv list; returns the job id."""
    try:
        done = subprocess.run(  # noqa: S603 - argv list, no shell
            ["sbatch", str(path)], shell=False, capture_output=True, text=True, check=True
        )
    except FileNotFoundError:
        raise SlurmError("sbatch is not on PATH") from None
    except subprocess.CalledProcessError as exc:
        raise SlurmError(f"sbatch failed: {exc.stderr.strip() or exc}") from None
    match = re.search(r"Submitted batch job (\d+)", done.stdout)
    if match is None:
        raise SlurmError(f"unexpected sbatch output: {done.stdout.strip()!r}")
    return match.group(1)


__all__ = [
    "SLURM_DIR",
    "WORK_COPY_FILTER",
    "model_closure",
    "work_copy_filter",
    "SlurmError",
    "SlurmPlan",
    "checkpoint_files",
    "coperceive_root",
    "estimate_from_runs",
    "interpreter_module",
    "plan_sbatch",
    "staged_files",
    "submit",
    "time_limit",
    "validate_logs_dir",
    "validate_run_id",
    "write_plan",
]
