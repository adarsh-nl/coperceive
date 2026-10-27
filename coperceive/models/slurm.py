# SPDX-License-Identifier: Apache-2.0
"""``coperceive model run --slurm``: emit (and on request submit) the Slurm job for the same
batch run (ADR 0011 decision 5).

The script stages exactly the scene files and raw files the selected scenes need into the
cluster profile's ``scratch_template`` (for example ``/scratch/$USER/$SLURM_JOB_ID``) with
``rsync --files-from``, runs the same ``coperceive model run`` there, and copies the run home
before the trap wipes the scratch. The template comes from the server profile, never from a
constant in this module; ``--slurm-scratch-template`` or
``COPERCEIVE_SLURM_SCRATCH_TEMPLATE`` supplies it until pipeline submission passes the
profile value directly. ``--time`` comes from the manifest's
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
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from coperceive.errors import CoperceiveError
from coperceive.evaluation.predictions import list_runs, read_run_meta
from coperceive.model import Scene
from coperceive.models.batch import RunOptions, select_agents
from coperceive.models.inputs import raw_files, selected_sensors
from coperceive.models.spec import Manifest, ModelSpec
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


_SCRATCH_TEMPLATE_RE = re.compile(r"^[A-Za-z0-9_./${}~-]+$")


def validate_scratch_template(template: str) -> str:
    """Check a cluster profile's scratch template before it is embedded in a script.

    The template is the user/profile choice (for example
    ``/scratch/$USER/$SLURM_JOB_ID``); it must be a safe shell word with a
    per-job variable so parallel jobs never share a directory.
    """
    if not template or not template.strip():
        raise SlurmError(
            "No scratch template for the job. Set the cluster profile's scratch_template "
            "(for example /scratch/$USER/$SLURM_JOB_ID) and pass it with "
            "--slurm-scratch-template or COPERCEIVE_SLURM_SCRATCH_TEMPLATE."
        )
    if not _SCRATCH_TEMPLATE_RE.match(template):
        raise SlurmError(
            f"scratch template {template!r} has characters that are unsafe in a job script. "
            "Use the cluster profile's path (letters, digits, _ . / $ { } ~ -)."
        )
    if "$SLURM_JOB_ID" not in template:
        raise SlurmError(
            f"scratch template {template!r} needs $SLURM_JOB_ID so each job gets its own "
            "directory (for example /scratch/$USER/$SLURM_JOB_ID)."
        )
    return template


class SlurmError(CoperceiveError):
    """The job could not be planned or submitted."""


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
    scratch_template: str,
    repo_root: Path | None = None,
) -> SlurmPlan:
    """The sbatch of ADR 0011 decision 5 for ``coperceive model run`` over ``scenes``."""
    scratch_template = validate_scratch_template(scratch_template)
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
    partition = "ps,main-gpu" if gpu else "ps,main-cpu"
    job_name = f"model-{manifest.name}-{run_id}"
    slurm_dir = out_root.resolve() / SLURM_DIR
    command = _run_command(
        manifest, spec, run_id=run_id, options=options, python=python, raw=bool(raw)
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
            f'mkdir -p {shlex.quote(str(results))}/"$SLURM_JOB_ID"',
            f'rsync -a "$SCRATCH/out/" {shlex.quote(str(results))}/"$SLURM_JOB_ID"/',
            "# The work directory's logs and artefacts, not its inputs or any toolkit copy.",
            "rsync -a " + " ".join(work_copy_filter(transient)) + " "
            f'"$SCRATCH/work/" {shlex.quote(str(results))}/"$SLURM_JOB_ID"/work/',
            f"mkdir -p {shlex.quote(str(target))}",
            f'rsync -a "$SCRATCH/out/{run_id}/" {shlex.quote(str(target))}/',
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
            f'echo "done: {shlex.quote(str(target))} (exit $RC)"',
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
    )


def _run_command(
    manifest: Manifest,
    spec: ModelSpec,
    *,
    run_id: str,
    options: RunOptions,
    python: Path,
    raw: bool,
) -> list[str]:
    """The same ``model run`` against the staged copy; never ``--slurm``."""
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
    for key, value in options.options.items():
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
    return argv


def write_plan(plan: SlurmPlan, out_root: Path, run_id: str) -> Path:
    """``<out_root>/.slurm/<run_id>.sbatch`` plus the two ``--files-from`` lists."""
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
    "coperceive_root",
    "estimate_from_runs",
    "interpreter_module",
    "plan_sbatch",
    "staged_files",
    "submit",
    "time_limit",
    "write_plan",
]
