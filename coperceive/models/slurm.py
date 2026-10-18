# SPDX-License-Identifier: Apache-2.0
"""``coperceive model run --slurm``: emit (and on request submit) the Slurm job for the same
batch run (ADR 0011 decision 5).

The script stages exactly the scene files and raw files the selected scenes need into
``/local/$SLURM_JOB_ID`` with ``rsync --files-from``, runs the same ``coperceive model run``
there, and copies the run home before the trap wipes the scratch. Partitions are
``ps,main-gpu`` (``resources.gpu``) or ``ps,main-cpu``. ``--time`` comes from the manifest's
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
CPUS = 8


class SlurmError(CoperceiveError):
    """The job could not be planned or submitted."""


def time_limit(seconds_per_sample: float, staging_seconds: float, samples: int) -> str:
    """``staging + per_sample × samples``, rounded up to the next 600 s, doubled, ``HH:MM:SS``."""
    estimate = staging_seconds + seconds_per_sample * samples
    rounded = max(600, int(math.ceil(estimate / 600.0)) * 600)
    total = rounded * 2
    hours, rest = divmod(total, 3600)
    minutes, seconds = divmod(rest, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def estimate_from_runs(out_root: Path, plugin: str, model: str) -> tuple[float, float] | None:
    """``(per_sample_s, staging_s)`` of the newest run of ``plugin``/``model`` under
    ``out_root``, by ``provenance.ended_utc``; None without one."""
    best: tuple[str, float, float] | None = None
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
        ended = str(provenance.get("ended_utc") or "")
        if not isinstance(per_sample, (int, float)) or not isinstance(staging, (int, float)):
            continue
        if per_sample <= 0:
            continue
        if best is None or ended > best[0]:
            best = (ended, float(per_sample), float(staging))
    return None if best is None else (best[1], best[2])


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
    partition: str
    gres: str | None
    mem_gb: int
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
) -> SlurmPlan:
    """The sbatch of ADR 0011 decision 5 for ``coperceive model run`` over ``scenes``."""
    agent_ids = {
        sid: select_agents(scene, spec, options.agent_ids) for sid, scene in scenes.items()
    }
    samples = sum(len(pose_times(scene)) for scene in scenes.values())
    scene_files, raw = staged_files(
        scenes_root, scenes, spec, raw_root=options.raw_root, agent_ids=agent_ids
    )
    measured = estimate_from_runs(out_root, manifest.name, spec.key)
    if measured is not None:
        per_sample, staging = measured
        source = f"provenance.timings of the newest {manifest.name} {spec.key} run under {out_root}"
    else:
        per_sample, staging = spec.resources.seconds_per_sample, spec.resources.staging_seconds
        source = "the manifest's resources (no earlier run under --out)"
    limit = time_limit(per_sample, staging, samples)
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
        f"#   {per_sample:g} s/sample x {samples} samples + {staging:g} s staging, "
        "rounded up to 10 min, doubled.",
        "# Compute nodes install nothing and reach no index; the plugin is already in the .venv.",
        f"#SBATCH --job-name={job_name}",
        f"#SBATCH --partition={partition}",
    ]
    if gpu:
        lines.append("#SBATCH --gres=gpu:1")
    lines.append(f"#SBATCH --cpus-per-task={CPUS}")
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
            'SCRATCH="/local/$SLURM_JOB_ID"',
            'mkdir -p "$SCRATCH"',
            "trap 'rm -rf \"$SCRATCH\"' EXIT",
            'export TMPDIR="$SCRATCH/tmp"',
            'mkdir -p "$TMPDIR" "$SCRATCH/scenes" "$SCRATCH/out" "$SCRATCH/work"',
            "",
            "module purge",
        ]
    )
    for name in manifest.environment.modules:
        lines.append(f"module load {shlex.quote(name)}")
    lines.append("")
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
    lines.append("")
    lines.append(" ".join(command))
    lines.append("")
    results = results_dir.resolve()
    target = out_root.resolve() / run_id
    lines.extend(
        [
            f'mkdir -p {shlex.quote(str(results))}/"$SLURM_JOB_ID"',
            f'rsync -a "$SCRATCH/out/" {shlex.quote(str(results))}/"$SLURM_JOB_ID"/',
            f"mkdir -p {shlex.quote(str(target))}",
            f'rsync -a "$SCRATCH/out/{run_id}/" {shlex.quote(str(target))}/',
            f'echo "done: {shlex.quote(str(target))}"',
            "",
        ]
    )
    return SlurmPlan(
        job_name=job_name,
        partition=partition,
        gres="gpu:1" if gpu else None,
        mem_gb=spec.resources.memory_gb,
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
    "SlurmError",
    "SlurmPlan",
    "estimate_from_runs",
    "plan_sbatch",
    "staged_files",
    "submit",
    "time_limit",
    "write_plan",
]
