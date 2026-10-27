# SPDX-License-Identifier: Apache-2.0
"""Scheduler adapters: discover the cluster read-only, render and run jobs.

Slurm is the first adapter. The interface is intentionally small so PBS, LSF or a
plain-SSH "no scheduler" target can be added without touching the profile, the
pipeline registry or the desktop UI. A "none" scheduler never runs heavy work
without the user's explicit consent (the adapter refuses and says so).
"""

from __future__ import annotations

import shlex
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol

from coperceive.hpc.profile import JobDefaults, ProfileError, SchedulerKind, ServerProfile

_SINFO_PIPE_COLUMNS = 6
_SCONTROL_KEYS = (
    "PartitionName",
    "Default",
    "MaxTime",
    "TotalNodes",
    "TotalCPUs",
    "TRES",
    "State",
    "AllowGroups",
    "QoS",
    "Nodes",
)


class SchedulerError(RuntimeError):
    """A scheduler operation failed. The message says what to do next."""


Runner = Callable[..., str]
"""Runs one command as an argv list and returns stdout.

Implemented as ``runner(argv)`` for a plain command and
``runner(argv, stdin=text)`` when a script is fed on standard input.

The caller builds the argv: locally for a login-node probe, or as
``("ssh", target, ...)`` for a remote command. Nothing here builds a shell string.
"""


@dataclass(frozen=True)
class Partition:
    """One partition as the scheduler shows it."""

    name: str
    nodes: int | None = None
    state: str | None = None
    max_time: str | None = None
    gres: str | None = None
    gpu_types: tuple[str, ...] = ()

    def to_json(self) -> dict[str, object]:
        return {
            "name": self.name,
            "nodes": self.nodes,
            "state": self.state,
            "max_time": self.max_time,
            "gres": self.gres,
            "gpu_types": list(self.gpu_types),
        }


@dataclass(frozen=True)
class Discovery:
    """Read-only scheduler facts, offered to the user as choices."""

    scheduler: SchedulerKind
    partitions: tuple[Partition, ...] = ()
    gpu_types: tuple[str, ...] = ()
    raw: Mapping[str, str] = field(default_factory=dict[str, str])
    notes: tuple[str, ...] = ()

    def names(self) -> tuple[str, ...]:
        return tuple(part.name for part in self.partitions)


class JobState(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class JobRequest:
    """One heavy job. Every field is a user/profile choice; there is no default."""

    job_name: str
    command_argv: tuple[str, ...]
    partition: str | None = None
    account: str | None = None
    qos: str | None = None
    gres: str | None = None
    time_limit: str | None = None
    memory: str | None = None
    cpus_per_task: int | None = None
    environment_modules: tuple[str, ...] = ()
    log_directory: str | None = None

    def validate(self) -> None:
        if not self.job_name.startswith("coperceive-"):
            raise SchedulerError(
                "App job names must start with 'coperceive-' so the app can find "
                f"the jobs it created; got {self.job_name!r}."
            )
        if not self.command_argv:
            raise SchedulerError("A job needs a command to run.")
        for element in self.command_argv:
            if not element:
                raise SchedulerError("Job command elements must be non-empty strings.")
        for name in ("partition", "account", "qos", "gres", "time_limit", "memory"):
            value = getattr(self, name)
            if value is not None and not str(value).strip():
                raise SchedulerError(f"{name} is blank; omit it instead.")

    def with_defaults(self, defaults: JobDefaults) -> JobRequest:
        merged = defaults.with_overrides(
            partition=self.partition,
            account=self.account,
            qos=self.qos,
            gres=self.gres,
            time_limit=self.time_limit,
            memory=self.memory,
            cpus_per_task=self.cpus_per_task,
        )
        return JobRequest(
            job_name=self.job_name,
            command_argv=self.command_argv,
            partition=merged.partition,
            account=merged.account,
            qos=merged.qos,
            gres=merged.gres,
            time_limit=merged.time_limit,
            memory=merged.memory,
            cpus_per_task=merged.cpus_per_task,
            environment_modules=self.environment_modules,
            log_directory=self.log_directory,
        )


class SchedulerAdapter(Protocol):
    """Everything the app needs from a scheduler. PBS/LSF extend this later."""

    kind: SchedulerKind

    def discover(self) -> Discovery:
        """Read-only facts (never submits anything, never installs anything)."""
        ...

    def render_job_script(self, profile: ServerProfile, request: JobRequest) -> str:
        """The complete script; the UI shows it and the user confirms before submit."""
        ...

    def submit(self, script_text: str) -> str:
        """Submit a confirmed script; returns the scheduler job id."""
        ...

    def status(self, job_id: str) -> JobState: ...

    def cancel(self, job_id: str) -> None: ...

    def logs(self, job_id: str) -> str: ...

    def list_own_jobs(self) -> tuple[tuple[str, JobState], ...]:
        """Only the jobs this app created and recorded, newest first."""
        ...


def quote_remote_argv(argv: Sequence[str]) -> str:
    """Quote an argv list for a remote shell string, POSIX single-quote style.

    Only for the rare place where a remote command line is unavoidable (e.g. a
    login-node probe through ``ssh host "..."``). Prefer passing argv elements to
    ``ssh`` directly. ``shlex.quote`` leaves harmless characters bare and wraps
    anything else in single quotes, so spaces, quotes and ``;`` stay data.
    """
    if not argv:
        raise SchedulerError("Refusing to build a remote command from an empty argv.")
    quoted = [shlex.quote(element) for element in argv]
    return " ".join(quoted)


def _gres_gpu_types(gres: str | None) -> tuple[str, ...]:
    if not gres:
        return ()
    types: list[str] = []
    for token in gres.replace(",", " ").split():
        bits = token.split(":")
        if bits[0] != "gpu":
            continue
        if len(bits) >= 2 and bits[1] and bits[1] not in ("0", "(null)"):
            if bits[1] not in types:
                types.append(bits[1])
    return tuple(types)


def parse_sinfo(text: str) -> tuple[Partition, ...]:
    """Parse ``sinfo`` output in two shapes.

    Accepted input:
    * a pipe-format dump (``sinfo -h -o "%P|%D|%t|%G|%l"``), and
    * the default column format ``PARTITION AVAIL TIMELIMIT NODES STATE NODELIST``.

    A trailing ``*`` on a partition name marks the scheduler default.
    """
    rows: list[Partition] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("PARTITION"):
            continue
        if "|" in stripped:
            fields = [field.strip() for field in stripped.split("|")]
            if len(fields) < _SINFO_PIPE_COLUMNS:
                continue
            name, nodes, state, gres, max_time, _nodelist = fields[:_SINFO_PIPE_COLUMNS]
            rows.append(
                Partition(
                    name=name.rstrip("*"),
                    nodes=_int_or_none(nodes),
                    state=state or None,
                    max_time=max_time or None,
                    gres=None if gres in ("", "(null)") else gres,
                    gpu_types=_gres_gpu_types(gres),
                )
            )
            continue
        fields = stripped.split()
        if len(fields) >= 5 and fields[1].lower() in ("up", "down", "idle", "drain"):
            name = fields[0].rstrip("*")
            rows.append(
                Partition(
                    name=name,
                    nodes=_int_or_none(fields[3]),
                    state=fields[4],
                    max_time=fields[2],
                )
            )
    return tuple(rows)


def parse_scontrol_partition(text: str) -> dict[str, dict[str, str]]:
    """Parse ``scontrol show partition [-o]`` into partition name -> attributes.

    Handles both the one-record-per-line ``-o`` form and the multi-line
    ``scontrol show partition`` form (records start at ``PartitionName=``).
    """
    records: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("PartitionName=") and current:
            records.append(current)
            current = {}
        for token in _split_scontrol_tokens(stripped):
            key, separator, value = token.partition("=")
            if separator and key in _SCONTROL_KEYS:
                current[key] = value
    if current:
        records.append(current)
    found: dict[str, dict[str, str]] = {}
    for record in records:
        name = record.get("PartitionName")
        if name:
            record["name"] = name
            found[name] = record
    return found


def _split_scontrol_tokens(line: str) -> list[str]:
    """Split ``key=value`` tokens, tolerating quoted values with spaces."""
    try:
        lexer = shlex.shlex(line, posix=True)
        lexer.whitespace_split = True
        lexer.commenters = ""
        return list(lexer)
    except ValueError as exc:  # unbalanced quote from truncated output
        raise SchedulerError(f"Could not read scheduler output: {exc}") from exc


def _int_or_none(text: object) -> int | None:
    try:
        return int(str(text))
    except (TypeError, ValueError):
        return None


def merge_discovery(
    *,
    scheduler: SchedulerKind,
    sinfo_text: str | None,
    scontrol_text: str | None,
) -> Discovery:
    """Combine ``sinfo`` and ``scontrol show partition`` into one choice list."""
    sinfo_rows = parse_sinfo(sinfo_text or "")
    scontrol = parse_scontrol_partition(scontrol_text or "")
    sinfo_by_name = {row.name: row for row in sinfo_rows}
    partitions: list[Partition] = []
    for name in sorted(set(sinfo_by_name) | set(scontrol)):
        info = scontrol.get(name, {})
        base = sinfo_by_name.get(name)
        gres = base.gres if base is not None else info.get("gres")
        if gres is None and "TRES" in info:
            gres = _tres_gres(info["TRES"])
        partitions.append(
            Partition(
                name=name,
                nodes=base.nodes if base is not None else _int_or_none(info.get("TotalNodes")),
                state=base.state if base is not None else info.get("State"),
                max_time=(
                    base.max_time if base is not None and base.max_time else info.get("MaxTime")
                ),
                gres=gres,
                gpu_types=_gres_gpu_types(gres),
            )
        )
    gpu_types: list[str] = []
    for partition in partitions:
        for gpu in partition.gpu_types:
            if gpu not in gpu_types:
                gpu_types.append(gpu)
    return Discovery(
        scheduler=scheduler,
        partitions=tuple(partitions),
        gpu_types=tuple(gpu_types),
        raw={"sinfo": sinfo_text or "", "scontrol": scontrol_text or ""},
    )


def _tres_gres(tres: str) -> str | None:
    """Turn ``cpu=64,mem=...,gres/gpu:a100=4`` into ``gpu:a100:4``."""
    parts: list[str] = []
    for item in tres.split(","):
        if not item.startswith("gres/"):
            continue
        key, _, value = item.partition("=")
        name = key.removeprefix("gres/")
        if "/" in name:
            gpu, kind = name.split("/", 1)
            parts.append(f"{gpu}:{kind}:{value}")
        else:
            parts.append(f"{name}:{value}")
    return ",".join(parts) or None


class SlurmAdapter:
    """Slurm behind the adapter interface. All work goes through the scheduler."""

    kind = SchedulerKind.SLURM

    def __init__(self, runner: Runner) -> None:
        self._runner = runner

    def discover(self) -> Discovery:
        sinfo = self._runner(
            [
                "sinfo",
                "-h",
                "-o",
                "%P|%D|%t|%G|%l|%N",
            ]
        )
        scontrol = self._runner(["scontrol", "show", "partition", "-o"])
        return merge_discovery(
            scheduler=SchedulerKind.SLURM,
            sinfo_text=sinfo,
            scontrol_text=scontrol,
        )

    def render_job_script(self, profile: ServerProfile, request: JobRequest) -> str:
        profile.validate()
        request.validate()
        resolved = request.with_defaults(profile.defaults)
        if resolved.partition is None:
            raise SchedulerError(
                "Choose a partition for this job. coperceive does not assume one; "
                "pick one from the partitions detected on the server."
            )
        lines: list[str] = [
            "#!/bin/bash",
            f"# {resolved.job_name}: generated by coperceive from the server profile.",
            f"#SBATCH --job-name={resolved.job_name}",
            f"#SBATCH --partition={resolved.partition}",
        ]
        optional = {
            "account": resolved.account,
            "qos": resolved.qos,
            "gres": resolved.gres,
            "time": resolved.time_limit,
            "mem": resolved.memory,
            "cpus-per-task": resolved.cpus_per_task,
        }
        for key, value in optional.items():
            if value is not None:
                lines.append(f"#SBATCH --{key}={value}")
        if resolved.log_directory:
            lines.append(f"#SBATCH --output={resolved.log_directory}/%x-%j.out")
            lines.append(f"#SBATCH --error={resolved.log_directory}/%x-%j.err")
        lines.extend(["", "set -euo pipefail"])
        modules = resolved.environment_modules or profile.environment.modules
        if modules:
            lines.append("module purge")
            for module in modules:
                lines.append(f"module load {shlex.quote(module)}")
        if profile.environment.kind.value == "container" and profile.environment.container_image:
            image = shlex.quote(profile.environment.container_image)
            command = " ".join(shlex.quote(element) for element in resolved.command_argv)
            lines.append(f"apptainer exec {image} {command}")
        else:
            command = " ".join(shlex.quote(element) for element in resolved.command_argv)
            lines.append(command)
        return "\n".join(lines) + "\n"

    def submit(self, script_text: str) -> str:
        output = self._runner(["sbatch", "--parsable", "-"], stdin=script_text)
        job_id = output.strip().split(";", 1)[0].strip()
        if not job_id:
            raise SchedulerError("The scheduler accepted the job but returned no job id.")
        return job_id

    def status(self, job_id: str) -> JobState:
        text = self._runner(["squeue", "-h", "-j", job_id, "-o", "%T"])
        state = text.strip().splitlines()[0].strip().lower() if text.strip() else ""
        return _slurm_state(state)

    def cancel(self, job_id: str) -> None:
        self._runner(["scancel", job_id])

    def logs(self, job_id: str) -> str:
        return self._runner(["scontrol", "show", "job", job_id])

    def list_own_jobs(self) -> tuple[tuple[str, JobState], ...]:
        text = self._runner(["squeue", "-h", "-o", "%i|%j|%T", "--me"])
        jobs: list[tuple[str, JobState]] = []
        for line in text.splitlines():
            job_id, _, rest = line.strip().partition("|")
            name, _, state = rest.partition("|")
            if not name.startswith("coperceive-"):
                continue
            jobs.append((job_id, _slurm_state(state.strip().lower())))
        return tuple(jobs)


def _slurm_state(state: str) -> JobState:
    mapping = {
        "pending": JobState.PENDING,
        "running": JobState.RUNNING,
        "completed": JobState.COMPLETED,
        "failed": JobState.FAILED,
        "cancelled": JobState.CANCELLED,
        "timeout": JobState.FAILED,
        "node_fail": JobState.FAILED,
    }
    return mapping.get(state, JobState.UNKNOWN)


class NullAdapter:
    """A server with no scheduler. Heavy work requires explicit user consent."""

    kind = SchedulerKind.NONE

    def discover(self) -> Discovery:
        return Discovery(scheduler=SchedulerKind.NONE)

    def render_job_script(self, profile: ServerProfile, request: JobRequest) -> str:
        raise SchedulerError(
            "This server has no scheduler, so coperceive will not run heavy work on it. "
            "Run the command in a terminal on the server yourself; coperceive can still "
            "read data over SSH."
        )

    def submit(self, script_text: str) -> str:
        raise SchedulerError(
            "This server has no scheduler, so coperceive will not submit jobs on it. "
            "Ask the user to run the command in a terminal on the server."
        )

    def status(self, job_id: str) -> JobState:
        return JobState.UNKNOWN

    def cancel(self, job_id: str) -> None:
        raise SchedulerError("This server has no scheduler, so there is no job to cancel.")

    def logs(self, job_id: str) -> str:
        raise SchedulerError("This server has no scheduler, so coperceive has no job logs to read.")

    def list_own_jobs(self) -> tuple[tuple[str, JobState], ...]:
        return ()


def adapter_for(kind: SchedulerKind, runner: Runner) -> object:
    """Build the adapter for a detected/selected scheduler kind."""
    if kind is SchedulerKind.SLURM:
        return SlurmAdapter(runner)
    if kind is SchedulerKind.NONE:
        return NullAdapter()
    raise ProfileError(f"Scheduler {kind.value!r} is not supported yet. Supported: slurm, none.")
