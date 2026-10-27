# SPDX-License-Identifier: Apache-2.0
"""The per-server profile a user creates for their own cluster.

A profile stores only what is needed to run coperceive on that server. It never
stores credentials: the user's ssh-agent and ``~/.ssh/config`` authenticate, and
anything secret must come from the environment on the server. Every host,
partition, account, QoS, GRES name, time, memory and path is either chosen by the
user or discovered from the scheduler; the product ships no default partition and
no cluster hostname.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, cast

ENVIRONMENT_KINDS = ("none", "modules", "conda", "venv", "container")
# Argument names that would invite a credential into argv or a job script.
_FORBIDDEN_ARG_NAMES = ("token", "password", "passwd", "secret", "api_key", "private_key")
_TIME_RE = re.compile(r"^(?:[0-9]+-)?[0-9]{1,2}:[0-9]{2}:[0-9]{2}$")
_MEMORY_RE = re.compile(r"^[0-9]+[KMGT]?$")


class ProfileError(ValueError):
    """A profile cannot be used. The message says what to fix."""


class SchedulerKind(str, Enum):
    """Which scheduler the server runs, as far as coperceive can tell."""

    SLURM = "slurm"
    NONE = "none"

    @classmethod
    def parse(cls, text: object) -> SchedulerKind:
        try:
            return cls(str(text))
        except ValueError as exc:
            raise ProfileError(f"Unknown scheduler {text!r}. Choose 'slurm' or 'none'.") from exc


class EnvironmentKind(str, Enum):
    """How coperceive is made importable on the server's compute nodes."""

    NONE = "none"
    MODULES = "modules"
    CONDA = "conda"
    VENV = "venv"
    CONTAINER = "container"

    @classmethod
    def parse(cls, text: object) -> EnvironmentKind:
        try:
            return cls(str(text))
        except ValueError as exc:
            raise ProfileError(
                f"Unknown environment kind {text!r}. Choose one of {', '.join(ENVIRONMENT_KINDS)}."
            ) from exc


def _check_absolute(path: str, field_name: str) -> None:
    if not path:
        raise ProfileError(f"{field_name} must not be empty.")
    if not path.startswith("/") and not path.startswith("~"):
        raise ProfileError(
            f"{field_name} must be an absolute or ~ path, got {path!r}. "
            "Use the path as it exists on the server."
        )


@dataclass(frozen=True)
class EnvironmentSetup:
    """How to reach coperceive on this server, detected then confirmed by the user.

    ``compute_has_internet`` defaults to False because compute nodes commonly have
    no network. When it is False, job scripts contain no installs and no downloads
    and use the preinstalled interpreter, container or wheelhouse instead.
    """

    kind: EnvironmentKind = EnvironmentKind.NONE
    modules: tuple[str, ...] = ()
    """Module names loaded before the run, in order (module purge first)."""
    interpreter: str | None = None
    """Path to python on the server for conda/venv, e.g. ``/home/alice/venv/bin/python``."""
    container_image: str | None = None
    """Apptainer/Singularity image path for the container kind."""
    coperceive_command: tuple[str, ...] = ("coperceive",)
    """How to invoke coperceive on the server, as argv elements."""
    wheelhouse: str | None = None
    """Directory of wheels for offline installs; never used unless the user asks."""
    compute_has_internet: bool = False

    def validate(self) -> None:
        if self.kind is EnvironmentKind.MODULES and not self.modules:
            raise ProfileError(
                "Modules environment needs at least one module to load. "
                "Add the modules your cluster documents for coperceive's dependencies."
            )
        if self.kind is EnvironmentKind.CONDA and not self.interpreter:
            raise ProfileError(
                "Conda environment needs the path to its python (for example "
                "/home/you/miniconda3/envs/coperceive/bin/python)."
            )
        if self.kind is EnvironmentKind.VENV and not self.interpreter:
            raise ProfileError(
                "Virtualenv environment needs the path to its python (for example "
                "/home/you/venv/bin/python)."
            )
        if self.kind is EnvironmentKind.CONTAINER and not self.container_image:
            raise ProfileError(
                "Container environment needs the image path (an .sif or a docker URI "
                "the cluster allows)."
            )
        if self.interpreter is not None:
            _check_absolute(self.interpreter, "environment.interpreter")
        if not self.coperceive_command:
            raise ProfileError(
                "coperceive_command must have at least one argv element (usually 'coperceive')."
            )
        for element in self.coperceive_command:
            if not element:
                raise ProfileError("coperceive_command elements must not be empty.")
        if self.wheelhouse is not None:
            _check_absolute(self.wheelhouse, "environment.wheelhouse")

    def to_json(self) -> dict[str, object]:
        return {
            "kind": self.kind.value,
            "modules": list(self.modules),
            "interpreter": self.interpreter,
            "container_image": self.container_image,
            "coperceive_command": list(self.coperceive_command),
            "wheelhouse": self.wheelhouse,
            "compute_has_internet": self.compute_has_internet,
        }

    @classmethod
    def from_json(cls, data: Mapping[str, object]) -> EnvironmentSetup:
        _reject_unknown(data, cls)
        modules_raw = data.get("modules", ())
        command_raw = data.get("coperceive_command", ("coperceive",))
        if not isinstance(modules_raw, (list, tuple)) or not all(
            isinstance(item, str) for item in cast(Sequence[object], modules_raw)
        ):
            raise ProfileError("environment.modules must be a list of module names.")
        if not isinstance(command_raw, (list, tuple)) or not all(
            isinstance(item, str) for item in cast(Sequence[object], command_raw)
        ):
            raise ProfileError("environment.coperceive_command must be a list of strings.")
        setup = cls(
            kind=EnvironmentKind.parse(data.get("kind", "none")),
            modules=tuple(cast(Sequence[str], modules_raw)),
            interpreter=_optional_str(data.get("interpreter"), "environment.interpreter"),
            container_image=_optional_str(
                data.get("container_image"), "environment.container_image"
            ),
            coperceive_command=tuple(cast(Sequence[str], command_raw)),
            wheelhouse=_optional_str(data.get("wheelhouse"), "environment.wheelhouse"),
            compute_has_internet=bool(data.get("compute_has_internet", False)),
        )
        setup.validate()
        return setup


@dataclass(frozen=True)
class JobDefaults:
    """Scheduler choices the user confirmed for this server. All optional.

    The product never supplies a partition or account of its own; when a value is
    missing the adapter asks for it (and discovery offers the scheduler's values).
    """

    partition: str | None = None
    account: str | None = None
    qos: str | None = None
    gres: str | None = None
    """e.g. ``gpu:1`` or the site's own GRES name, exactly as the scheduler shows it."""
    time_limit: str | None = None
    """Slurm time, e.g. ``02:00:00`` or ``1-00:00:00``."""
    memory: str | None = None
    """Slurm memory, e.g. ``8G`` or ``64000M``."""
    cpus_per_task: int | None = None

    def validate(self) -> None:
        for name in ("partition", "account", "qos", "gres"):
            value = getattr(self, name)
            if value is not None and not str(value).strip():
                raise ProfileError(f"defaults.{name} is blank; omit it instead.")
            if value is not None and any(ch.isspace() for ch in str(value)):
                raise ProfileError(f"defaults.{name} must not contain whitespace, got {value!r}.")
        if self.time_limit is not None and not _TIME_RE.match(self.time_limit):
            raise ProfileError(
                f"defaults.time_limit {self.time_limit!r} is not Slurm time "
                "(use HH:MM:SS or D-HH:MM:SS)."
            )
        if self.memory is not None and not _MEMORY_RE.match(self.memory):
            raise ProfileError(
                f"defaults.memory {self.memory!r} is not a Slurm memory size (e.g. 8G)."
            )
        if self.cpus_per_task is not None and self.cpus_per_task < 1:
            raise ProfileError("defaults.cpus_per_task must be at least 1.")

    def to_json(self) -> dict[str, object]:
        return {
            "partition": self.partition,
            "account": self.account,
            "qos": self.qos,
            "gres": self.gres,
            "time_limit": self.time_limit,
            "memory": self.memory,
            "cpus_per_task": self.cpus_per_task,
        }

    @classmethod
    def from_json(cls, data: Mapping[str, object]) -> JobDefaults:
        _reject_unknown(data, cls)
        cpus = data.get("cpus_per_task")
        if cpus is not None and (not isinstance(cpus, int) or isinstance(cpus, bool)):
            raise ProfileError("defaults.cpus_per_task must be an integer.")
        defaults = cls(
            partition=_optional_str(data.get("partition"), "defaults.partition"),
            account=_optional_str(data.get("account"), "defaults.account"),
            qos=_optional_str(data.get("qos"), "defaults.qos"),
            gres=_optional_str(data.get("gres"), "defaults.gres"),
            time_limit=_optional_str(data.get("time_limit"), "defaults.time_limit"),
            memory=_optional_str(data.get("memory"), "defaults.memory"),
            cpus_per_task=cpus,
        )
        defaults.validate()
        return defaults

    def with_overrides(self, **changes: object) -> JobDefaults:
        cleaned = {key: value for key, value in changes.items() if value is not None}
        merged = replace(self, **cast(Any, cleaned))
        merged.validate()
        return merged


@dataclass(frozen=True)
class ServerProfile:
    """One server the user added. Stored locally with no secrets."""

    name: str
    """A label the user recognises, e.g. "Lab cluster"."""
    ssh_alias: str | None = None
    """A ``~/.ssh/config`` Host the user already uses (ProxyJump, 2FA, agent)."""
    host: str | None = None
    """Used instead of an alias when the user types the host by hand."""
    user: str | None = None
    data_roots: tuple[str, ...] = ()
    """Directories on the server coperceive may read (the user picks them)."""
    scratch_template: str = ""
    """Per-user scratch for job work, e.g. ``/scratch/$USER/$SLURM_JOB_ID``."""
    scheduler: SchedulerKind = SchedulerKind.SLURM
    defaults: JobDefaults = field(default_factory=JobDefaults)
    environment: EnvironmentSetup = field(default_factory=EnvironmentSetup)
    notes: str | None = None

    def validate(self) -> None:
        if not self.name.strip():
            raise ProfileError("The server needs a name.")
        if self.ssh_alias is None and self.host is None:
            raise ProfileError(
                "Choose a host from ~/.ssh/config or type a host name. "
                "coperceive never asks for a password or a private key."
            )
        if self.ssh_alias is not None:
            if not self.ssh_alias.strip() or any(ch.isspace() for ch in self.ssh_alias):
                raise ProfileError(
                    f"ssh_alias {self.ssh_alias!r} must be one ssh config Host name."
                )
            if self.host is not None:
                raise ProfileError("Set either ssh_alias or host, not both.")
        if self.user is not None and (
            not self.user.strip() or any(ch.isspace() for ch in self.user)
        ):
            raise ProfileError(f"user {self.user!r} must be one user name.")
        if not self.data_roots:
            raise ProfileError(
                "Add at least one data root on the server (the directories coperceive may read)."
            )
        for root in self.data_roots:
            _check_absolute(root, "data_roots entry")
        if not self.scratch_template.strip():
            raise ProfileError(
                "A per-user scratch path is required for job work, for example "
                "/scratch/$USER/$SLURM_JOB_ID."
            )
        self.defaults.validate()
        self.environment.validate()

    @property
    def ssh_target(self) -> str:
        """The argv element to ssh to, from the alias or host (+ user)."""
        self.validate()
        if self.ssh_alias is not None:
            return self.ssh_alias
        assert self.host is not None
        return f"{self.user}@{self.host}" if self.user else self.host

    def to_json(self) -> dict[str, object]:
        return {
            "name": self.name,
            "ssh_alias": self.ssh_alias,
            "host": self.host,
            "user": self.user,
            "data_roots": list(self.data_roots),
            "scratch_template": self.scratch_template,
            "scheduler": self.scheduler.value,
            "defaults": self.defaults.to_json(),
            "environment": self.environment.to_json(),
            "notes": self.notes,
        }

    @classmethod
    def from_json(cls, data: Mapping[str, object]) -> ServerProfile:
        _reject_unknown(data, cls)
        roots_raw = data.get("data_roots", ())
        if not isinstance(roots_raw, (list, tuple)) or not all(
            isinstance(item, str) for item in cast(Sequence[object], roots_raw)
        ):
            raise ProfileError("data_roots must be a list of paths.")
        defaults_data = data.get("defaults", {})
        env_data = data.get("environment", {})
        if not isinstance(defaults_data, Mapping) or not isinstance(env_data, Mapping):
            raise ProfileError("defaults and environment must be objects.")
        profile = cls(
            name=_required_str(data, "name"),
            ssh_alias=_optional_str(data.get("ssh_alias"), "ssh_alias"),
            host=_optional_str(data.get("host"), "host"),
            user=_optional_str(data.get("user"), "user"),
            data_roots=tuple(cast(Sequence[str], roots_raw)),
            scratch_template=str(data.get("scratch_template", "")),
            scheduler=SchedulerKind.parse(data.get("scheduler", "slurm")),
            defaults=JobDefaults.from_json(cast(Mapping[str, object], defaults_data)),
            environment=EnvironmentSetup.from_json(cast(Mapping[str, object], env_data)),
            notes=_optional_str(data.get("notes"), "notes"),
        )
        profile.validate()
        return profile

    def to_json_text(self) -> str:
        return json.dumps(self.to_json(), indent=2, sort_keys=True) + "\n"


def _optional_str(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ProfileError(f"{field_name} must be a string or null.")
    return value


def _required_str(data: Mapping[str, object], field_name: str) -> str:
    value = data.get(field_name)
    if not isinstance(value, str) or not value:
        raise ProfileError(f"{field_name} is required and must be a non-empty string.")
    return value


def _reject_unknown(data: Mapping[str, object], cls: type) -> None:
    import dataclasses

    allowed = {item.name for item in dataclasses.fields(cls)}
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ProfileError(
            f"Unknown {cls.__name__} field(s): {', '.join(unknown)}. "
            "Remove them; profiles never carry credentials or extra keys."
        )


def assert_no_credentials(profile: ServerProfile) -> None:
    """Refuse a profile whose fields look like credentials.

    Profiles have no credential fields, so this only catches a hand-edited JSON
    file or a future field; the check is deliberately blunt.
    """
    text = profile.to_json_text().lower()
    for needle in _FORBIDDEN_ARG_NAMES:
        if f'"{needle}"' in text:
            raise ProfileError(
                f"The profile contains a '{needle}' field. Credentials must come from "
                "the environment, ssh-agent or the OS keyring, never from a profile."
            )
