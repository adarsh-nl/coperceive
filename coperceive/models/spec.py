# SPDX-License-Identifier: Apache-2.0
"""The model plugin manifest (spec §Manifest): YAML to frozen dataclasses with key-path errors.

Read without importing the plugin: its environment is not coperceive's, and the manifest
bytes are hashed for provenance and the cache key.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import yaml

from coperceive.adapters.paths import PathError, Template
from coperceive.errors import CoperceiveError

SPEC_VERSION = 1
GROUP = "coperceive.models"
PLACEHOLDERS = frozenset({"python", "plugin_dir", "work_dir", "model"})
RAW_PLACEHOLDERS = frozenset({"scene", "agent", "agent_root", "sensor", "time_us", "frame"})
MODALITIES = ("camera", "lidar", "radar")
AGENT_TYPES = ("vehicle", "uav", "infrastructure", "other")
OPTION_TYPES = ("str", "int", "float", "bool")
ERROR_CODES = ("unsupported", "configure_failed", "frame_failed", "cancelled", "bad_message")

_NAME = re.compile(r"^[a-z0-9_-]+$")
_ARGV_FIELD = re.compile(r"\{([^{}]*)\}")


class ModelSpecError(CoperceiveError):
    """The manifest is malformed, or an option or model name is wrong."""


# --- dataclasses (spec §Manifest) ---------------------------------------------------------


@dataclass(frozen=True)
class Licence:
    spdx: str | None
    notice: str
    citation: str | None


@dataclass(frozen=True)
class Environment:
    kind: str  # conda | python | current | container
    value: str | None  # env name or prefix, interpreter path, or container image
    modules: tuple[str, ...]
    secrets: tuple[str, ...]
    inherit_ld_library_path: bool
    container_runtime: str | None = None


@dataclass(frozen=True)
class Inputs:
    modalities: tuple[str, ...]
    agents: tuple[str, ...] | None  # None means any
    sensors: tuple[str, ...]
    frames: str  # single | sequence
    raw: tuple[Template, ...]
    raw_agent_roots: Mapping[str, str]
    raw_clock: tuple[int, int] | None  # (scale_us, offset_us)
    other_runs: bool


@dataclass(frozen=True)
class Outputs:
    categories: tuple[str, ...]
    score_note: str
    sources: tuple[tuple[str, str], ...]
    agent: str
    artifacts: tuple[str, ...]
    deterministic: bool


@dataclass(frozen=True)
class Resources:
    gpu: bool
    memory_gb: int
    seconds_per_sample: float
    staging_seconds: float
    frame_timeout_s: float
    run_timeout_s: float | None
    grace_s: float
    slurm_constraint: str | None
    max_inflight: int


@dataclass(frozen=True)
class Dependency:
    model: str
    artifact: str


@dataclass(frozen=True)
class OptionSpec:
    type: str
    default: object
    choices: tuple[object, ...] | None
    description: str | None


@dataclass(frozen=True)
class ModelSpec:
    key: str
    description: str | None
    checkpoint_md5: str | None
    inputs: Inputs
    outputs: Outputs
    resources: Resources
    depends_on: tuple[Dependency, ...]
    options: Mapping[str, OptionSpec]


@dataclass(frozen=True)
class Commands:
    batch: tuple[str, ...]
    live: tuple[str, ...]


@dataclass(frozen=True)
class Manifest:
    spec_version: int
    name: str
    version: str
    licence: Licence
    environment: Environment
    models: Mapping[str, ModelSpec]
    commands: Commands
    notes: str | None
    source: Path | None


# --- loading ------------------------------------------------------------------------------


def load_manifest(path: Path) -> Manifest:
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ModelSpecError(f"cannot read {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ModelSpecError(f"{path}: not valid YAML: {exc}") from exc
    return parse_manifest(payload, source=path)


def parse_manifest(payload: object, *, source: Path | None) -> Manifest:
    root = _Node("", payload)
    version = root.int("spec_version")
    if version != SPEC_VERSION:
        raise ModelSpecError(f"spec_version: expected {SPEC_VERSION}, got {version}")
    name = root.str("name")
    if not _NAME.match(name):
        raise ModelSpecError(f"name: expected [a-z0-9_-]+, got {name!r}")
    plugin_version = root.str("version")
    licence = _licence(root.sub("licence"))
    environment = _environment(root.sub("environment"))
    models = _models(root.sub("models"))
    commands = _commands(root.sub("commands"))
    notes = root.opt_str("notes")
    root.done()
    return Manifest(
        spec_version=version,
        name=name,
        version=plugin_version,
        licence=licence,
        environment=environment,
        models=models,
        commands=commands,
        notes=notes,
        source=source,
    )


def manifest_sha256(manifest: Manifest) -> str:
    """SHA-256 of the manifest file bytes (provenance and the cache key)."""
    if manifest.source is None:
        raise ModelSpecError("manifest has no source file to hash")
    return hashlib.sha256(manifest.source.read_bytes()).hexdigest()


def render_argv(
    manifest: Manifest,
    command: str,
    *,
    python: Path,
    plugin_dir: Path,
    work_dir: Path,
    model: str,
) -> list[str]:
    """``commands.<command>`` with the closed placeholder set substituted; no shell."""
    template = manifest.commands.batch if command == "batch" else manifest.commands.live
    values = {
        "python": str(python),
        "plugin_dir": str(plugin_dir),
        "work_dir": str(work_dir),
        "model": model,
    }
    return [_ARGV_FIELD.sub(lambda match: values[match.group(1)], arg) for arg in template]


def select_model(manifest: Manifest, name: str | None) -> ModelSpec:
    """The one model, or the named one; an error listing the names when ambiguous."""
    names = ", ".join(manifest.models)
    if name is None:
        if len(manifest.models) != 1:
            raise ModelSpecError(f"--model is required; {manifest.name} has models: {names}")
        return next(iter(manifest.models.values()))
    spec = manifest.models.get(name)
    if spec is None:
        raise ModelSpecError(f"{manifest.name} has no model {name!r}; models: {names}")
    return spec


def check_options(spec: ModelSpec, given: Mapping[str, str]) -> dict[str, object]:
    """Typed option values with defaults filled; an unknown name or a bad value is an error."""
    out: dict[str, object] = {key: option.default for key, option in spec.options.items()}
    for key, text in given.items():
        option = spec.options.get(key)
        if option is None:
            known = ", ".join(spec.options) or "none"
            raise ModelSpecError(f"unknown option {key!r} for model {spec.key}; known: {known}")
        value = _coerce_option(key, option, text)
        if option.choices is not None and value not in option.choices:
            choices = ", ".join(str(choice) for choice in option.choices)
            raise ModelSpecError(f"option {key}: expected one of {choices}, got {text!r}")
        out[key] = value
    return out


def _coerce_option(key: str, option: OptionSpec, text: str) -> object:
    try:
        if option.type == "str":
            return text
        if option.type == "int":
            return int(text)
        if option.type == "float":
            return float(text)
        lowered = text.strip().lower()
        if lowered in ("true", "1", "yes", "on"):
            return True
        if lowered in ("false", "0", "no", "off"):
            return False
        raise ValueError(text)
    except ValueError:
        raise ModelSpecError(f"option {key}: expected {option.type}, got {text!r}") from None


# --- key-path node --------------------------------------------------------------------------


class _Node:
    """One mapping in the manifest, with its key path for error messages."""

    def __init__(self, path: str, payload: object) -> None:
        self.path = path
        if payload is None:
            payload = {}
        if not isinstance(payload, dict):
            raise ModelSpecError(f"{path or '<root>'}: expected a mapping")
        self.payload: dict[str, object] = {str(k): v for k, v in payload.items()}  # type: ignore[union-attr]
        self.seen: set[str] = set()

    def at(self, key: str) -> str:
        return f"{self.path}.{key}" if self.path else key

    def has(self, key: str) -> bool:
        return key in self.payload and self.payload[key] is not None

    def any(self, key: str, *, optional: bool = False) -> object:
        self.seen.add(key)
        if key not in self.payload or self.payload[key] is None:
            if optional:
                return None
            raise ModelSpecError(f"{self.at(key)}: required")
        return self.payload[key]

    def sub(self, key: str, *, optional: bool = False) -> _Node | None:
        value = self.any(key, optional=optional)
        if value is None:
            return None
        return _Node(self.at(key), value)

    def str(self, key: str, *, choices: tuple[str, ...] | None = None) -> str:
        value = self.any(key)
        if not isinstance(value, str) or value == "":
            raise ModelSpecError(f"{self.at(key)}: expected a non-empty string")
        if choices is not None and value not in choices:
            raise ModelSpecError(
                f"{self.at(key)}: expected one of {', '.join(choices)}, got {value!r}"
            )
        return value

    def opt_str(self, key: str, choices: tuple[str, ...] | None = None) -> str | None:
        if not self.has(key):
            self.seen.add(key)
            return None
        return self.str(key, choices=choices)

    def int(self, key: str) -> int:
        value = self.any(key)
        if isinstance(value, bool) or not isinstance(value, int):
            raise ModelSpecError(f"{self.at(key)}: expected an integer")
        return value

    def opt_int(self, key: str, default: int) -> int:
        if not self.has(key):
            self.seen.add(key)
            return default
        return self.int(key)

    def float(self, key: str) -> float:
        value = self.any(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ModelSpecError(f"{self.at(key)}: expected a number")
        return float(value)

    def opt_float(self, key: str, default: float | None) -> float | None:
        if not self.has(key):
            self.seen.add(key)
            return default
        return self.float(key)

    def bool(self, key: str, default: bool | None = None) -> bool:
        if not self.has(key):
            self.seen.add(key)
            if default is None:
                raise ModelSpecError(f"{self.at(key)}: required")
            return default
        value = self.any(key)
        if not isinstance(value, bool):
            raise ModelSpecError(f"{self.at(key)}: expected true or false")
        return value

    def strs(self, key: str, *, optional: bool = False) -> tuple[str, ...]:
        value = self.any(key, optional=optional)
        if value is None:
            return ()
        if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):  # type: ignore[union-attr]
            raise ModelSpecError(f"{self.at(key)}: expected a list of non-empty strings")
        return tuple(str(v) for v in value)  # type: ignore[union-attr]

    def str_map(self, key: str, *, optional: bool = False) -> dict[str, str]:
        node = self.sub(key, optional=optional)
        if node is None:
            return {}
        out: dict[str, str] = {}
        for k in list(node.payload):
            out[k] = node.str(k)
        node.done()
        return out

    def done(self) -> None:
        unknown = sorted(set(self.payload) - self.seen)
        if unknown:
            raise ModelSpecError(f"{self.at(unknown[0])}: unknown key")


# --- section parsers ------------------------------------------------------------------------


def _licence(node: _Node | None) -> Licence:
    assert node is not None
    licence = Licence(
        spdx=node.opt_str("spdx"), notice=node.str("notice"), citation=node.opt_str("citation")
    )
    node.done()
    return licence


def _environment(node: _Node | None) -> Environment:
    assert node is not None
    kinds = [kind for kind in ("conda", "python", "current", "container") if node.has(kind)]
    if len(kinds) != 1:
        raise ModelSpecError(
            f"{node.path}: exactly one of conda, python, current, container is required"
        )
    kind = kinds[0]
    value: str | None = None
    runtime: str | None = None
    if kind == "current":
        if node.bool("current") is not True:
            raise ModelSpecError(f"{node.at('current')}: expected true")
    elif kind == "container":
        sub = node.sub("container")
        assert sub is not None
        value = sub.str("image")
        runtime = sub.str("runtime", choices=("singularity",))
        sub.done()
    else:
        value = node.str(kind)
    environment = Environment(
        kind=kind,
        value=value,
        modules=node.strs("modules", optional=True),
        secrets=node.strs("secrets", optional=True),
        inherit_ld_library_path=node.bool("inherit_ld_library_path", False),
        container_runtime=runtime,
    )
    node.done()
    return environment


def _models(node: _Node | None) -> dict[str, ModelSpec]:
    assert node is not None
    if not node.payload:
        raise ModelSpecError(f"{node.path}: at least one model is required")
    out: dict[str, ModelSpec] = {}
    for key in list(node.payload):
        if not _NAME.match(key):
            raise ModelSpecError(f"{node.at(key)}: model keys are [a-z0-9_-]+")
        sub = node.sub(key)
        assert sub is not None
        out[key] = _model(key, sub)
    node.done()
    for spec in out.values():
        for dependency in spec.depends_on:
            other = out.get(dependency.model)
            if other is None:
                raise ModelSpecError(
                    f"{node.at(spec.key)}.depends_on: no model {dependency.model!r}"
                )
            if dependency.artifact not in other.outputs.artifacts:
                raise ModelSpecError(
                    f"{node.at(spec.key)}.depends_on: {dependency.model} writes no artifact "
                    f"{dependency.artifact!r}"
                )
    return out


def _model(key: str, node: _Node) -> ModelSpec:
    spec = ModelSpec(
        key=key,
        description=node.opt_str("description"),
        checkpoint_md5=node.opt_str("checkpoint_md5"),
        inputs=_inputs(node.sub("inputs")),
        outputs=_outputs(node.sub("outputs")),
        resources=_resources(node.sub("resources")),
        depends_on=_depends_on(node),
        options=_options(node.sub("options", optional=True)),
    )
    node.done()
    return spec


def _inputs(node: _Node | None) -> Inputs:
    assert node is not None
    modalities = node.strs("modalities")
    for modality in modalities:
        if modality not in MODALITIES:
            raise ModelSpecError(
                f"{node.at('modalities')}: expected {', '.join(MODALITIES)}, got {modality!r}"
            )
    agents: tuple[str, ...] | None
    raw_agents = node.any("agents", optional=True)
    if raw_agents is None or raw_agents == "any":
        node.seen.add("agents")
        agents = None
    else:
        agents = node.strs("agents")
        for kind in agents:
            if kind not in AGENT_TYPES:
                raise ModelSpecError(f"{node.at('agents')}: unknown agent type {kind!r}")
    sensors = node.strs("sensors", optional=True) or ("*",)
    frames = node.str("frames", choices=("single", "sequence"))
    raw: list[Template] = []
    for text in node.strs("raw", optional=True):
        try:
            raw.append(Template(text, frozenset({"time_us"})))
        except PathError as exc:
            raise ModelSpecError(f"{node.at('raw')}: {exc}") from None
    roots = node.str_map("raw_agent_roots", optional=True)
    clock_node = node.sub("raw_clock", optional=True)
    clock: tuple[int, int] | None = None
    if clock_node is not None:
        clock = (clock_node.int("scale_us"), clock_node.opt_int("offset_us", 0))
        clock_node.done()
        if clock[0] <= 0:
            raise ModelSpecError(f"{clock_node.at('scale_us')}: expected a positive integer")
    used = {name for template in raw for name in template.placeholders}
    if "frame" in used and clock is None:
        raise ModelSpecError(f"{node.at('raw_clock')}: required when a template uses {{frame}}")
    if "agent_root" in used and not roots:
        raise ModelSpecError(
            f"{node.at('raw_agent_roots')}: required when a template uses {{agent_root}}"
        )
    inputs = Inputs(
        modalities=modalities,
        agents=agents,
        sensors=sensors,
        frames=frames,
        raw=tuple(raw),
        raw_agent_roots=roots,
        raw_clock=clock,
        other_runs=node.bool("other_runs", False),
    )
    node.done()
    return inputs


def _outputs(node: _Node | None) -> Outputs:
    assert node is not None
    sources = node.str_map("sources")
    if not sources:
        raise ModelSpecError(f"{node.at('sources')}: at least one source is required")
    outputs = Outputs(
        categories=node.strs("categories"),
        score_note=node.str("score_note"),
        sources=tuple(sources.items()),
        agent=node.opt_str("agent") or "request",
        artifacts=node.strs("artifacts", optional=True),
        deterministic=node.bool("deterministic", True),
    )
    if outputs.agent != "request" and outputs.agent not in AGENT_TYPES:
        raise ModelSpecError(f"{node.at('agent')}: expected request or an agent type")
    node.done()
    return outputs


def _resources(node: _Node | None) -> Resources:
    assert node is not None
    resources = Resources(
        gpu=node.bool("gpu"),
        memory_gb=node.int("memory_gb"),
        seconds_per_sample=node.float("seconds_per_sample"),
        staging_seconds=node.opt_float("staging_seconds", 0.0) or 0.0,
        frame_timeout_s=node.opt_float("frame_timeout_s", 600.0) or 600.0,
        run_timeout_s=node.opt_float("run_timeout_s", None),
        grace_s=node.opt_float("grace_s", 10.0) or 10.0,
        slurm_constraint=node.opt_str("slurm_constraint"),
        max_inflight=node.opt_int("max_inflight", 1),
    )
    if resources.max_inflight < 1:
        raise ModelSpecError(f"{node.at('max_inflight')}: expected at least 1")
    node.done()
    return resources


def _depends_on(node: _Node) -> tuple[Dependency, ...]:
    value = node.any("depends_on", optional=True)
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ModelSpecError(f"{node.at('depends_on')}: expected a list")
    out: list[Dependency] = []
    items = cast(list[object], value)
    for index, item in enumerate(items):
        sub = _Node(f"{node.at('depends_on')}[{index}]", item)
        out.append(Dependency(model=sub.str("model"), artifact=sub.str("artifact")))
        sub.done()
    return tuple(out)


def _options(node: _Node | None) -> dict[str, OptionSpec]:
    if node is None:
        return {}
    out: dict[str, OptionSpec] = {}
    for key in list(node.payload):
        if not _NAME.match(key):
            raise ModelSpecError(f"{node.at(key)}: option names are [a-z0-9_-]+")
        sub = node.sub(key)
        assert sub is not None
        kind = sub.str("type", choices=OPTION_TYPES)
        default = sub.any("default")
        _check_option_value(sub.at("default"), kind, default)
        choices_raw = sub.any("choices", optional=True)
        choices: tuple[object, ...] | None = None
        if choices_raw is not None:
            if not isinstance(choices_raw, list):
                raise ModelSpecError(f"{sub.at('choices')}: expected a list")
            choices = tuple(choices_raw)  # type: ignore[arg-type]
            for choice in choices:
                _check_option_value(sub.at("choices"), kind, choice)
        out[key] = OptionSpec(
            type=kind, default=default, choices=choices, description=sub.opt_str("description")
        )
        sub.done()
    node.done()
    return out


def _check_option_value(path: str, kind: str, value: object) -> None:
    ok = (
        (kind == "str" and isinstance(value, str))
        or (kind == "bool" and isinstance(value, bool))
        or (kind == "int" and isinstance(value, int) and not isinstance(value, bool))
        or (kind == "float" and isinstance(value, (int, float)) and not isinstance(value, bool))
    )
    if not ok:
        raise ModelSpecError(f"{path}: expected a {kind}")


def _commands(node: _Node | None) -> Commands:
    assert node is not None
    batch = _argv(node, "batch")
    live = _argv(node, "live") if node.has("live") else batch
    node.done()
    return Commands(batch=batch, live=live)


def _argv(node: _Node, key: str) -> tuple[str, ...]:
    argv = node.strs(key)
    for arg in argv:
        for match in _ARGV_FIELD.finditer(arg):
            if match.group(1) not in PLACEHOLDERS:
                raise ModelSpecError(f"{node.at(key)}: unknown placeholder {match.group(0)!r}")
        if "{" in _ARGV_FIELD.sub("", arg) or "}" in _ARGV_FIELD.sub("", arg):
            raise ModelSpecError(f"{node.at(key)}: unbalanced braces in {arg!r}")
    return argv


__all__ = [
    "AGENT_TYPES",
    "GROUP",
    "MODALITIES",
    "PLACEHOLDERS",
    "RAW_PLACEHOLDERS",
    "SPEC_VERSION",
    "Commands",
    "Dependency",
    "Environment",
    "Inputs",
    "Licence",
    "Manifest",
    "ModelSpec",
    "ModelSpecError",
    "OptionSpec",
    "Outputs",
    "Resources",
    "check_options",
    "load_manifest",
    "manifest_sha256",
    "parse_manifest",
    "render_argv",
    "select_model",
]
