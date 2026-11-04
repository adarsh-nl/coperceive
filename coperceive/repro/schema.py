# SPDX-License-Identifier: Apache-2.0
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
"""Validate a repro card against ``repro-card.schema.json``.

The checker understands the slice of JSON Schema this file uses (type, const,
enum, required, properties, additionalProperties, items, anyOf, pattern,
minimum, minLength, and local ``$ref``). Version 1 is checked in full. A
higher ``schema_version`` is an envelope only: later cards may add fields.
"""

from __future__ import annotations

import json
import re
from importlib.resources import files
from pathlib import Path

from coperceive.errors import CoperceiveError

SCHEMA_NAME = "coperceive-repro-card"
SCHEMA_VERSION = 1
_REF = re.compile(r"^#/(.+)$")


class ReproError(CoperceiveError):
    """A repro card could not be built or does not check out."""


def schema_text() -> str:
    """The schema shipped with this package (the same file as ``docs/spec``)."""
    return files("coperceive.repro").joinpath("repro-card.schema.json").read_text(encoding="utf-8")


def load_schema() -> dict[str, object]:
    loaded: object = json.loads(schema_text())
    if not isinstance(loaded, dict):
        raise ReproError("The repro card schema is not a JSON object.")
    return {str(key): value for key, value in loaded.items()}


def repo_schema_path() -> Path:
    return Path(__file__).resolve().parents[2] / "docs" / "spec" / "repro-card.schema.json"


def validate_card(instance: object, schema: dict[str, object] | None = None) -> list[str]:
    """Problems that stop a version-1 card. Empty means the schema accepts it.

    A ``schema_version`` above 1 returns no schema problems. Callers still scan
    the document for secrets and absolute paths.
    """
    root = load_schema() if schema is None else schema
    version = _schema_version(instance)
    if version is None:
        return ["schema_version is missing or not an integer"]
    if version > SCHEMA_VERSION:
        problems: list[str] = []
        if not isinstance(instance, dict) or instance.get("schema") != SCHEMA_NAME:
            problems.append(f"schema must be {SCHEMA_NAME!r}")
        return problems
    if version < SCHEMA_VERSION:
        return [f"schema_version {version} is older than {SCHEMA_VERSION}"]
    return _check(instance, root, root, "$")


def _schema_version(instance: object) -> int | None:
    if not isinstance(instance, dict):
        return None
    version = instance.get("schema_version")
    if type(version) is int:
        return version
    return None


def _resolve(node: object, root: dict[str, object]) -> dict[str, object]:
    if not isinstance(node, dict):
        raise ReproError("schema node is not an object")
    ref = node.get("$ref")
    if not isinstance(ref, str):
        return {str(key): value for key, value in node.items()}
    match = _REF.match(ref)
    if match is None:
        raise ReproError(f"unsupported schema $ref {ref!r}")
    cursor: object = root
    for part in match.group(1).split("/"):
        if not isinstance(cursor, dict) or part not in cursor:
            raise ReproError(f"schema $ref {ref!r} does not resolve")
        cursor = cursor[part]
    if not isinstance(cursor, dict):
        raise ReproError(f"schema $ref {ref!r} is not an object")
    return {str(key): value for key, value in cursor.items()}


def _check(instance: object, node: object, root: dict[str, object], path: str) -> list[str]:
    schema = _resolve(node, root)
    if "anyOf" in schema:
        options = schema["anyOf"]
        if not isinstance(options, list) or not options:
            return [f"{path}: schema anyOf is empty"]
        best: list[str] | None = None
        for option in options:
            problems = _check(instance, option, root, path)
            if not problems:
                return []
            if best is None or len(problems) < len(best):
                best = problems
        return best or [f"{path}: matches no alternative"]
    problems: list[str] = []
    if "const" in schema and instance != schema["const"]:
        problems.append(f"{path}: expected {schema['const']!r}")
    if "enum" in schema:
        allowed = schema["enum"]
        if not isinstance(allowed, list) or instance not in allowed:
            problems.append(f"{path}: value is not one of the allowed names")
    expected = schema.get("type")
    if expected is not None and not _type_ok(instance, expected):
        problems.append(f"{path}: unexpected type")
        return problems
    if isinstance(instance, str):
        pattern = schema.get("pattern")
        if isinstance(pattern, str) and re.search(pattern, instance) is None:
            problems.append(f"{path}: does not match {pattern}")
        min_length = schema.get("minLength")
        if type(min_length) is int and len(instance) < min_length:
            problems.append(f"{path}: is too short")
    if type(instance) is int:
        minimum = schema.get("minimum")
        if type(minimum) is int and instance < minimum:
            problems.append(f"{path}: is below {minimum}")
    if isinstance(instance, dict):
        problems.extend(_check_object(instance, schema, root, path))
    if isinstance(instance, list):
        item_schema = schema.get("items")
        if item_schema is not None:
            for index, item in enumerate(instance):
                problems.extend(_check(item, item_schema, root, f"{path}[{index}]"))
    return problems


def _check_object(
    instance: dict[object, object],
    schema: dict[str, object],
    root: dict[str, object],
    path: str,
) -> list[str]:
    problems: list[str] = []
    required = schema.get("required", [])
    if isinstance(required, list):
        for name in required:
            if isinstance(name, str) and name not in instance:
                problems.append(f"{path}.{name}: is required")
    properties = schema.get("properties", {})
    if not isinstance(properties, dict):
        properties = {}
    additional = schema.get("additionalProperties", True)
    for key, value in instance.items():
        if not isinstance(key, str):
            problems.append(f"{path}: key is not a string")
            continue
        if key in properties:
            problems.extend(_check(value, properties[key], root, f"{path}.{key}"))
            continue
        if additional is False:
            problems.append(f"{path}.{key}: is not part of the repro card")
        elif isinstance(additional, dict):
            problems.extend(_check(value, additional, root, f"{path}.{key}"))
    return problems


def _type_ok(instance: object, expected: object) -> bool:
    names = [expected] if isinstance(expected, str) else expected
    if not isinstance(names, list):
        return False
    for name in names:
        if name == "object" and isinstance(instance, dict):
            return True
        if name == "array" and isinstance(instance, list):
            return True
        if name == "string" and isinstance(instance, str):
            return True
        if name == "boolean" and type(instance) is bool:
            return True
        if name == "integer" and type(instance) is int:
            return True
        if name == "number" and type(instance) in (int, float):
            return True
        if name == "null" and instance is None:
            return True
    return False
