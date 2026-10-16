# SPDX-License-Identifier: Apache-2.0
"""Record readers: JSON, YAML, CSV and whitespace text rows.

A field path is dotted keys with integer list indices (``info.frames.0``). It
is not JSONPath: no wildcards and no filters.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

from coperceive.errors import CoperceiveError


class FieldError(CoperceiveError):
    """A record does not have the shape or the field the manifest named."""


def get_field(record: object, path: str) -> object:
    current = record
    walked: list[str] = []
    for part in path.split("."):
        walked.append(part)
        where = ".".join(walked)
        if isinstance(current, dict):
            if part not in current:
                raise FieldError(f"field {where!r} is missing")
            current = current[part]  # type: ignore[index]
        elif isinstance(current, list):
            if not part.isdigit() or int(part) >= len(current):  # type: ignore[arg-type]
                raise FieldError(f"field {where!r} is missing")
            current = current[int(part)]  # type: ignore[index]
        else:
            raise FieldError(f"field {where!r}: cannot index into {type(current).__name__}")
    return current


def read_json_record(path: Path) -> dict[str, object]:
    return _as_record(json.loads(path.read_text(encoding="utf-8")), path)


def read_yaml_record(path: Path) -> dict[str, object]:
    import yaml

    return _as_record(yaml.safe_load(path.read_text(encoding="utf-8")), path)


def read_json_table(path: Path) -> list[dict[str, object]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise FieldError(f"{path} is not a JSON list")
    rows: list[dict[str, object]] = []
    for index, item in enumerate(payload):  # type: ignore[arg-type]
        if not isinstance(item, dict):
            raise FieldError(f"{path} row {index} is not an object")
        rows.append(item)  # type: ignore[arg-type]
    return rows


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def read_text_rows(path: Path) -> list[list[str]]:
    rows: list[list[str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if parts:
            rows.append(parts)
    return rows


def _as_record(payload: object, path: Path) -> dict[str, object]:
    if not isinstance(payload, dict):
        raise FieldError(f"{path} is not a JSON/YAML object")
    return payload  # type: ignore[return-value]
