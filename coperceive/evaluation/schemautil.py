# SPDX-License-Identifier: Apache-2.0
"""A small checker for the JSON Schemas this package commits.

It covers the subset those documents use (type, enum, const, required,
properties, additionalProperties, items, minimum, maximum, minItems) so a
test can validate a document against the file on disk without a new
dependency. It is not a general JSON Schema implementation.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import cast


class SchemaError(ValueError):
    """``document`` does not match ``schema``. The message starts with the path."""


def validate_schema(
    document: object,
    schema: Mapping[str, object],
    *,
    path: str = "$",
    root: Mapping[str, object] | None = None,
) -> None:
    """Raise :class:`SchemaError` when ``document`` fails ``schema``.

    ``$ref`` is resolved against ``root`` (the schema itself on the first call)
    for pointers of the form ``#/$defs/name``.
    """
    base = schema if root is None else root
    if "$ref" in schema:
        ref = schema.get("$ref")
        if not isinstance(ref, str):
            raise SchemaError(f"{path}: $ref is not a string")
        validate_schema(document, _resolve(base, ref), path=path, root=base)
        return
    type_name = schema.get("type")
    if type_name is not None:
        _check_type(document, type_name, path)
    if "const" in schema and document != schema["const"]:
        raise SchemaError(f"{path}: expected {schema['const']!r}")
    enum = schema.get("enum")
    if isinstance(enum, Sequence) and not isinstance(enum, (str, bytes)):
        choices = cast(Sequence[object], enum)
        if document not in choices:
            raise SchemaError(f"{path}: {document!r} is not one of {list(choices)}")
    if isinstance(document, bool) or not isinstance(document, (int, float)):
        number = None
    else:
        number = float(document)
    if number is not None:
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        if isinstance(minimum, (int, float)) and not isinstance(minimum, bool):
            if number < float(minimum):
                raise SchemaError(f"{path}: {number} is below {minimum}")
        if isinstance(maximum, (int, float)) and not isinstance(maximum, bool):
            if number > float(maximum):
                raise SchemaError(f"{path}: {number} is above {maximum}")
    if isinstance(document, str):
        minimum_length = schema.get("minLength")
        if (
            isinstance(minimum_length, int)
            and not isinstance(minimum_length, bool)
            and len(document) < minimum_length
        ):
            raise SchemaError(f"{path}: shorter than {minimum_length}")
    if isinstance(document, Mapping):
        _check_object(cast(Mapping[object, object], document), schema, path, base)
    if isinstance(document, Sequence) and not isinstance(document, (str, bytes)):
        _check_array(cast(Sequence[object], document), schema, path, base)


def _check_type(document: object, type_name: object, path: str) -> None:
    raw = cast(list[object], type_name) if isinstance(type_name, list) else [type_name]
    allowed: list[str] = []
    for name in raw:
        if not isinstance(name, str):
            raise SchemaError(f"{path}: schema type is not a string")
        allowed.append(name)
    if any(_matches(document, name) for name in allowed):
        return
    raise SchemaError(f"{path}: expected {' or '.join(allowed)}")


def _matches(document: object, name: str) -> bool:
    if name == "object":
        return isinstance(document, Mapping)
    if name == "array":
        return isinstance(document, Sequence) and not isinstance(document, (str, bytes))
    if name == "string":
        return isinstance(document, str)
    if name == "boolean":
        return isinstance(document, bool)
    if name == "integer":
        return isinstance(document, int) and not isinstance(document, bool)
    if name == "number":
        return isinstance(document, (int, float)) and not isinstance(document, bool)
    if name == "null":
        return document is None
    raise SchemaError(f"unsupported schema type {name!r}")


def _resolve(root: Mapping[str, object], ref: str) -> Mapping[str, object]:
    if not ref.startswith("#/"):
        raise SchemaError(f"unsupported $ref {ref!r}")
    current: object = root
    for part in ref[2:].split("/"):
        if not isinstance(current, dict):
            raise SchemaError(f"unresolved $ref {ref}")
        table: dict[str, object] = {}
        for key, value in current.items():  # pyright: ignore[reportUnknownVariableType]
            if not isinstance(key, str):
                raise SchemaError(f"unresolved $ref {ref}")
            table[key] = cast(object, value)
        if part not in table:
            raise SchemaError(f"unresolved $ref {ref}")
        current = table[part]
    if not isinstance(current, dict):
        raise SchemaError(f"unresolved $ref {ref}")
    return cast(dict[str, object], current)


def _check_object(
    document: Mapping[object, object],
    schema: Mapping[str, object],
    path: str,
    root: Mapping[str, object],
) -> None:
    required = schema.get("required")
    if isinstance(required, list):
        for key in cast(list[object], required):
            if isinstance(key, str) and key not in document:
                raise SchemaError(f"{path}: missing {key}")
    properties = schema.get("properties")
    defined: dict[str, object] = {}
    if isinstance(properties, Mapping):
        items = cast(Mapping[object, object], properties).items()
        defined = {str(key): value for key, value in items}
    additional = schema.get("additionalProperties", True)
    for key, value in document.items():
        if not isinstance(key, str):
            raise SchemaError(f"{path}: non-string key")
        child: object | None = defined.get(key)
        if child is None:
            if additional is False:
                raise SchemaError(f"{path}: unexpected {key}")
            if isinstance(additional, Mapping):
                child = cast(Mapping[str, object], additional)
        if isinstance(child, Mapping):
            validate_schema(
                value, cast(Mapping[str, object], child), path=f"{path}.{key}", root=root
            )


def _check_array(
    document: Sequence[object],
    schema: Mapping[str, object],
    path: str,
    root: Mapping[str, object],
) -> None:
    minimum = schema.get("minItems")
    if isinstance(minimum, int) and not isinstance(minimum, bool) and len(document) < minimum:
        raise SchemaError(f"{path}: fewer than {minimum} items")
    maximum = schema.get("maxItems")
    if isinstance(maximum, int) and not isinstance(maximum, bool) and len(document) > maximum:
        raise SchemaError(f"{path}: more than {maximum} items")
    items = schema.get("items")
    if isinstance(items, Mapping):
        item_schema = cast(Mapping[str, object], items)
        for index, value in enumerate(document):
            validate_schema(value, item_schema, path=f"{path}[{index}]", root=root)
