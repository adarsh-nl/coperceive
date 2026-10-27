# SPDX-License-Identifier: Apache-2.0
"""User-registered model pipelines: a script plus a typed argument schema.

A pipeline is not tied to any model codebase. The user registers a script and the
arguments it takes; coperceive validates values, passes them only as argv
elements, and reads the pipeline's open output contract
(``docs/spec/pipeline-output.md``) so results stream into the viewer.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import cast

from coperceive.hpc.profile import EnvironmentSetup, ProfileError

_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_FORBIDDEN_ARG_NAMES = frozenset(
    {"token", "password", "passwd", "secret", "api_key", "private_key", "credentials"}
)
OUTPUT_CONTRACT = "coperceive-pipeline-1"


class PipelineValidationError(ValueError):
    """A pipeline spec or its argument values are invalid. Says what to fix."""


class ArgumentKind(str, Enum):
    STR = "str"
    INT = "int"
    FLOAT = "float"
    BOOL = "bool"
    PATH = "path"
    ENUM = "enum"

    @classmethod
    def parse(cls, text: object) -> ArgumentKind:
        try:
            return cls(str(text))
        except ValueError as exc:
            raise PipelineValidationError(
                f"Unknown argument kind {text!r}. Choose one of "
                f"{', '.join(kind.value for kind in cls)}."
            ) from exc


@dataclass(frozen=True)
class ArgumentSpec:
    """One typed argument the pipeline accepts."""

    name: str
    kind: ArgumentKind
    help: str = ""
    required: bool = False
    default: object = None
    choices: tuple[str, ...] = ()
    multiple: bool = False

    def validate(self) -> None:
        if not _NAME_RE.match(self.name):
            raise PipelineValidationError(
                f"Argument name {self.name!r} must be lower-case letters, digits, '-' or "
                "'_' and start with a letter."
            )
        if self.name in _FORBIDDEN_ARG_NAMES or any(
            word in self.name for word in ("token", "password", "secret", "api_key")
        ):
            raise PipelineValidationError(
                f"Argument {self.name!r} looks like a credential. Secrets must come from "
                "the environment, never from a pipeline argument."
            )
        if self.kind is ArgumentKind.ENUM:
            if not self.choices:
                raise PipelineValidationError(
                    f"Argument {self.name!r} is an enum but has no choices."
                )
            if len(set(self.choices)) != len(self.choices):
                raise PipelineValidationError(f"Argument {self.name!r} has duplicate choices.")
        elif self.choices:
            raise PipelineValidationError(f"Argument {self.name!r} has choices but is not an enum.")
        if self.default is not None:
            self.coerce(self.default)
        if self.required and self.default is not None:
            raise PipelineValidationError(
                f"Argument {self.name!r} is required and has a default; pick one."
            )

    def coerce(self, value: object) -> object:
        """Return the typed value, or raise a plain-language error."""
        if self.multiple:
            if not isinstance(value, (list, tuple)):
                raise PipelineValidationError(f"Argument {self.name!r} takes a list of values.")
            return tuple(self._coerce_one(item) for item in cast(Sequence[object], value))
        return self._coerce_one(value)

    def _coerce_one(self, value: object) -> object:
        if self.kind is ArgumentKind.STR:
            if not isinstance(value, str):
                raise PipelineValidationError(f"Argument {self.name!r} must be text.")
            return value
        if self.kind is ArgumentKind.PATH:
            if not isinstance(value, str) or not value:
                raise PipelineValidationError(f"Argument {self.name!r} must be a path.")
            return value
        if self.kind is ArgumentKind.INT:
            if isinstance(value, bool) or not isinstance(value, int):
                raise PipelineValidationError(f"Argument {self.name!r} must be a whole number.")
            return value
        if self.kind is ArgumentKind.FLOAT:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise PipelineValidationError(f"Argument {self.name!r} must be a number.")
            return float(value)
        if self.kind is ArgumentKind.BOOL:
            if not isinstance(value, bool):
                raise PipelineValidationError(f"Argument {self.name!r} must be true or false.")
            return value
        if self.kind is ArgumentKind.ENUM:
            if not isinstance(value, str) or value not in self.choices:
                raise PipelineValidationError(
                    f"Argument {self.name!r} must be one of: {', '.join(self.choices)}."
                )
            return value
        raise PipelineValidationError(f"Argument {self.name!r} has an unknown kind.")


@dataclass(frozen=True)
class PipelineSpec:
    """A registered pipeline: script, interpreter, environment and arguments."""

    name: str
    script: str
    """Path to the script inside the profile's data roots (absolute or relative)."""
    interpreter: tuple[str, ...] = ("python3",)
    """Argv prefix used to run the script, e.g. ("python3",) or ("bash",)."""
    environment: EnvironmentSetup = field(default_factory=EnvironmentSetup)
    arguments: tuple[ArgumentSpec, ...] = ()
    output_contract: str = OUTPUT_CONTRACT
    description: str = ""

    def validate(self) -> None:
        if not _NAME_RE.match(self.name):
            raise PipelineValidationError(
                f"Pipeline name {self.name!r} must be lower-case letters, digits, '-' or "
                "'_' and start with a letter."
            )
        if not self.script.strip():
            raise PipelineValidationError("Pipeline script path is required.")
        if "\x00" in self.script:
            raise PipelineValidationError("Pipeline script path contains a NUL byte.")
        if self.script.startswith("/") or ".." in self.script.replace("\\", "/").split("/"):
            raise PipelineValidationError(
                f"Pipeline script {self.script!r} must stay inside the server's data roots; "
                "absolute paths and '..' are refused."
            )
        if not self.interpreter:
            raise PipelineValidationError(
                "Pipeline interpreter must have at least one argv element (e.g. python3)."
            )
        for element in self.interpreter:
            if not element:
                raise PipelineValidationError("Pipeline interpreter elements must be non-empty.")
        names = [argument.name for argument in self.arguments]
        if len(set(names)) != len(names):
            raise PipelineValidationError(
                "Pipeline argument names must be unique: " + ", ".join(sorted(names)) + "."
            )
        for argument in self.arguments:
            argument.validate()
        if self.output_contract != OUTPUT_CONTRACT:
            raise PipelineValidationError(
                f"Unsupported output contract {self.output_contract!r}. "
                f"This version reads {OUTPUT_CONTRACT!r} (see docs/spec/pipeline-output.md)."
            )
        self.environment.validate()

    def validate_values(self, values: Mapping[str, object]) -> dict[str, object]:
        """Check the user's argument values and return them typed."""
        self.validate()
        known = {argument.name: argument for argument in self.arguments}
        unknown = sorted(set(values) - set(known))
        if unknown:
            raise PipelineValidationError(
                "Unknown pipeline argument(s): " + ", ".join(unknown) + "."
            )
        typed: dict[str, object] = {}
        for argument in self.arguments:
            if argument.name in values:
                typed[argument.name] = argument.coerce(values[argument.name])
            elif argument.required:
                raise PipelineValidationError(
                    f"Argument {argument.name!r} is required. {argument.help}".strip()
                )
            elif argument.default is not None:
                typed[argument.name] = argument.coerce(argument.default)
        return typed

    def build_argv(
        self,
        values: Mapping[str, object],
        *,
        output_dir: str,
    ) -> tuple[str, ...]:
        """Build the full argv for one run. Values are argv elements, never shell text."""
        typed = self.validate_values(values)
        if not output_dir:
            raise PipelineValidationError("An output directory is required for a run.")
        argv: list[str] = [*self.interpreter, self.script]
        for argument in self.arguments:
            if argument.name not in typed:
                continue
            value = typed[argument.name]
            if argument.multiple:
                for item in cast(Sequence[object], value):
                    argv.extend([f"--{argument.name}", _argv_value(item)])
                continue
            if argument.kind is ArgumentKind.BOOL and value is True:
                argv.append(f"--{argument.name}")
                continue
            if argument.kind is ArgumentKind.BOOL and value is False:
                continue
            argv.extend([f"--{argument.name}", _argv_value(value)])
        argv.extend(["--output-dir", output_dir])
        return tuple(argv)


def _argv_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return repr(value)
    return str(value)


def pipeline_from_json(data: Mapping[str, object]) -> PipelineSpec:
    """Read a pipeline registration from JSON (no secrets, no absolute script path)."""
    unknown = set(data) - {
        "name",
        "script",
        "interpreter",
        "environment",
        "arguments",
        "output_contract",
        "description",
    }
    if unknown:
        raise PipelineValidationError(
            "Unknown pipeline field(s): " + ", ".join(sorted(unknown)) + "."
        )
    environment = data.get("environment", {})
    if not isinstance(environment, Mapping):
        raise PipelineValidationError("environment must be an object.")
    raw_arguments = data.get("arguments", [])
    if not isinstance(raw_arguments, (list, tuple)):
        raise PipelineValidationError("arguments must be a list.")
    interpreter_raw = data.get("interpreter", ["python3"])
    if not isinstance(interpreter_raw, (list, tuple)) or not all(
        isinstance(item, str) for item in cast(Sequence[object], interpreter_raw)
    ):
        raise PipelineValidationError("interpreter must be a list of strings.")
    interpreter = tuple(cast(Sequence[str], interpreter_raw))
    arguments: list[ArgumentSpec] = []
    for raw_item in cast(Sequence[object], raw_arguments):
        if not isinstance(raw_item, Mapping):
            raise PipelineValidationError("every argument must be an object.")
        raw = cast(Mapping[str, object], raw_item)
        unknown = set(raw) - {
            "name",
            "kind",
            "help",
            "required",
            "default",
            "choices",
            "multiple",
        }
        if unknown:
            raise PipelineValidationError(
                "Unknown argument field(s): " + ", ".join(sorted(unknown)) + "."
            )
        choices_raw = raw.get("choices", ())
        if not isinstance(choices_raw, (list, tuple)) or not all(
            isinstance(item, str) for item in cast(Sequence[object], choices_raw)
        ):
            raise PipelineValidationError("argument choices must be a list of strings.")
        choices = cast(Sequence[str], choices_raw)
        spec = ArgumentSpec(
            name=str(raw.get("name", "")),
            kind=ArgumentKind.parse(raw.get("kind", "str")),
            help=str(raw.get("help", "")),
            required=bool(raw.get("required", False)),
            default=raw.get("default"),
            choices=tuple(choices),
            multiple=bool(raw.get("multiple", False)),
        )
        spec.validate()
        arguments.append(spec)
    pipeline = PipelineSpec(
        name=str(data.get("name", "")),
        script=str(data.get("script", "")),
        interpreter=interpreter,
        environment=EnvironmentSetup.from_json(cast(Mapping[str, object], environment)),
        arguments=tuple(arguments),
        output_contract=str(data.get("output_contract", OUTPUT_CONTRACT)),
        description=str(data.get("description", "")),
    )
    pipeline.validate()
    return pipeline


def assert_pipeline_has_no_site_values(pipeline: PipelineSpec) -> None:
    """A registered pipeline carries no cluster values; it is user data."""
    try:
        pipeline.validate()
    except ProfileError as exc:  # pragma: no cover - defensive
        raise PipelineValidationError(str(exc)) from exc
    if any("=" in element for element in pipeline.interpreter):
        raise PipelineValidationError(
            "Pipeline interpreter must be argv elements, not shell assignments."
        )
