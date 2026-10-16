# SPDX-License-Identifier: Apache-2.0
"""Path templates for adapter manifests.

A template is a relative path with a fixed set of placeholders. It can be
expanded (values to path) or inverted against the files that exist (path to
values). There is no regex. Every resolved path has to stay under the raw root.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from coperceive.errors import CoperceiveError

PLACEHOLDERS = frozenset({"scene", "agent", "agent_root", "sensor", "frame", "sample"})
_FIELD = re.compile(r"\{([a-z_][a-z0-9_]*)(?::(0?)(\d*)([ds]))?\}")


class PathError(CoperceiveError):
    """A template is malformed or a path leaves the raw root."""


@dataclass(frozen=True)
class _Field:
    name: str
    kind: str  # "d" or "s"
    width: int
    zero: bool


@dataclass(frozen=True)
class Template:
    text: str
    extra: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if self.text.startswith("/") or self.text.startswith("\\"):
            raise PathError(f"template {self.text!r} is absolute")
        parts = re.split(r"[\\/]", self.text)
        if ".." in parts:
            raise PathError(f"template {self.text!r} contains '..'")
        self._parse()

    def _parse(self) -> list[_Field]:
        fields: list[_Field] = []
        rest = self.text
        for brace in re.finditer(r"\{[^{}]*\}", rest):
            match = _FIELD.fullmatch(brace.group(0))
            if match is None:
                raise PathError(f"template {self.text!r}: bad format in {brace.group(0)!r}")
            name, zero, width, kind = match.groups()
            if name not in PLACEHOLDERS and name not in self.extra:
                raise PathError(f"template {self.text!r}: unknown placeholder {name!r}")
            if kind is None:
                kind, zero, width = "s", "", ""
            if kind == "s" and (zero or width):
                raise PathError(f"template {self.text!r}: bad format in {brace.group(0)!r}")
            fields.append(_Field(name, kind, int(width) if width else 0, bool(zero)))
        return fields

    @property
    def fields(self) -> tuple[_Field, ...]:
        return tuple(self._parse())

    @property
    def placeholders(self) -> tuple[str, ...]:
        seen: list[str] = []
        for field in self.fields:
            if field.name not in seen:
                seen.append(field.name)
        return tuple(seen)

    def kind_of(self, name: str) -> str:
        for field in self.fields:
            if field.name == name:
                return field.kind
        raise PathError(f"template {self.text!r} has no placeholder {name!r}")

    def expand(self, values: Mapping[str, object]) -> str:
        def sub(match: re.Match[str]) -> str:
            field = next(f for f in self.fields if match.group(0).startswith("{" + f.name))
            if field.name not in values:
                raise PathError(f"template {self.text!r} needs {field.name!r}")
            return _render(field, values[field.name], self.text)

        return re.sub(r"\{[^{}]*\}", sub, self.text)

    def discover(
        self,
        root: Path,
        known: Mapping[str, object],
        free: str,
    ) -> tuple[dict[str, object], ...]:
        """Values of ``free`` for which a file exists, in sorted order.

        Every other placeholder has to be in ``known``. A file name that does
        not expand back to itself (``7.json`` against ``{frame:06d}``) is
        skipped. Directories are skipped unless the template ends in ``/``.
        """
        kind = self.kind_of(free)
        pattern = _expand_with_marker(self, known, free).replace("\x00", "*")
        prefix, _, suffix = pattern.partition("*")
        want_dir = self.text.endswith("/")
        found: list[dict[str, object]] = []
        base = confine(root, ".")
        for path in sorted(base.glob(pattern.rstrip("/"))):
            if want_dir != path.is_dir():
                continue
            relative = path.relative_to(base).as_posix() + ("/" if want_dir else "")
            if not (relative.startswith(prefix) and relative.endswith(suffix)):
                continue
            raw = relative[len(prefix) : len(relative) - len(suffix)]
            if "/" in raw or raw == "":
                continue
            value: object
            if kind == "d":
                if not raw.isdigit():
                    continue
                value = int(raw)
            else:
                value = raw
            candidate = {**known, free: value}
            if self.expand(candidate) != relative:
                continue
            found.append({free: value})
        return tuple(found)


def _expand_with_marker(template: Template, known: Mapping[str, object], free: str) -> str:
    def sub(match: re.Match[str]) -> str:
        field = next(f for f in template.fields if match.group(0).startswith("{" + f.name))
        if field.name == free:
            return "\x00"
        if field.name not in known:
            raise PathError(f"template {template.text!r} needs {field.name!r}")
        return _render(field, known[field.name], template.text)

    return re.sub(r"\{[^{}]*\}", sub, template.text)


def _render(field: _Field, value: object, text: str) -> str:
    if field.kind == "d":
        if isinstance(value, bool) or not isinstance(value, int):
            raise PathError(f"template {text!r}: {field.name!r} must be an int")
        return f"{value:0{field.width}d}" if field.zero else f"{value:{field.width}d}"
    if not isinstance(value, str):
        raise PathError(f"template {text!r}: {field.name!r} must be a string")
    if "/" in value or "\\" in value or value in {".", ".."}:
        raise PathError(f"template {text!r}: {field.name!r} value {value!r} is not a name")
    return value


def confine(root: Path, relative: str) -> Path:
    """Resolve ``relative`` under ``root`` and refuse anything that escapes it."""
    base = root.resolve()
    target = (base / relative).resolve()
    if target != base and not target.is_relative_to(base):
        raise PathError(f"path {relative!r} leaves {root}")
    return target
