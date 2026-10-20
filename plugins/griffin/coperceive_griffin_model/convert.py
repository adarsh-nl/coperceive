# SPDX-License-Identifier: Apache-2.0
"""The conversion rules of ``scripts/griffin/griffin_to_predictions.py``, on Python 3.8 and
without coperceive: nuScenes-style detection JSON to protocol-1 ``result`` boxes.

Rules: ``translation`` is the world (ENU) centre; ``size`` is nuScenes ``wlh`` and becomes
``size_lwh = (size[1], size[0], size[2])``; ``rotation`` is ``[qw, qx, qy, qz]``, normalised
when its norm is within ``QUATERNION_SLACK`` of 1 and refused otherwise; ``detection_name``
is the category and ``detection_score`` the score; ``frame`` is ``world``; the sample token
maps to a scene id and a time through ``scene.json``, ``sample.json``, ``BASE_US`` and
``STEP_US``.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from datetime import datetime
from typing import Any, Dict, List, Tuple

BASE_US = int((datetime(2025, 1, 1, 9, 0, 0) - datetime(1970, 1, 1)).total_seconds() * 1_000_000)
STEP_US = 100_000
TIMESTAMP_SLACK_US = 1_000
QUATERNION_SLACK = 1e-3
DEFAULT_CATEGORIES = ("car", "bicycle", "pedestrian")

# Evaluated at runtime, so typing's names and not the 3.9 builtin generics.
Samples = Dict[str, Tuple[str, int]]  # sample_token -> (scene_id, time_us)
Index = Dict[Tuple[str, int], List[Dict[str, Any]]]  # (scene_id, time_us) -> boxes


class ConvertError(ValueError):
    """The detection JSON or the nuScenes tables do not follow the toolkit's shapes."""


def _load_json(path: str) -> Any:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _obj(value: Any, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConvertError(f"{where}: expected an object")
    return value


def _list(value: Any, where: str) -> list[Any]:
    if not isinstance(value, list):
        raise ConvertError(f"{where}: expected a list")
    return value


def _str(node: dict[str, Any], key: str, where: str) -> str:
    value = node.get(key)
    if not isinstance(value, str) or value == "":
        raise ConvertError(f"{where}: {key} must be a non-empty string")
    return value


def _int(node: dict[str, Any], key: str, where: str) -> int:
    value = node.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConvertError(f"{where}: {key} must be an integer")
    return value


def _float(node: dict[str, Any], key: str, where: str) -> float:
    value = node.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConvertError(f"{where}: {key} must be a number")
    value = float(value)
    if not math.isfinite(value):
        raise ConvertError(f"{where}: {key} must be finite")
    return value


def _vec(node: dict[str, Any], key: str, size: int, where: str) -> tuple[float, ...]:
    value = node.get(key)
    if not isinstance(value, list) or len(value) != size:
        raise ConvertError(f"{where}: {key} must be a list of {size} numbers")
    out: list[float] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(item):
            raise ConvertError(f"{where}: {key} must be a list of {size} finite numbers")
        out.append(float(item))
    return tuple(out)


def scene_id_of(scene_name: str) -> str:
    """``scene-0000-Town03-000`` -> ``Town03-000`` (coperceive's ``scene_id: name``)."""
    parts = scene_name.split("-", 2)
    if len(parts) != 3 or parts[0] != "scene" or not parts[1].isdigit() or parts[2] == "":
        raise ConvertError(f"scene name {scene_name!r} is not scene-NNNN-<name>")
    return parts[2]


def frame_id_of(timestamp_us: int, where: str) -> int:
    delta = timestamp_us - BASE_US
    frame_id = int(round(delta / STEP_US))
    if frame_id < 0 or abs(delta - frame_id * STEP_US) > TIMESTAMP_SLACK_US:
        raise ConvertError(f"{where}: timestamp {timestamp_us} is not BASE + frame_id * 0.1 s")
    return frame_id


def read_samples(nusc_root: str) -> Samples:
    """``sample_token -> (scene_id, time_us)`` from ``sample.json`` and ``scene.json``."""
    scenes: dict[str, str] = {}
    for i, row in enumerate(_list(_load_json(os.path.join(nusc_root, "scene.json")), "scene.json")):
        node = _obj(row, f"scene.json[{i}]")
        scenes[_str(node, "token", f"scene.json[{i}]")] = scene_id_of(
            _str(node, "name", f"scene.json[{i}]")
        )
    samples: Samples = {}
    for i, row in enumerate(
        _list(_load_json(os.path.join(nusc_root, "sample.json")), "sample.json")
    ):
        where = f"sample.json[{i}]"
        node = _obj(row, where)
        token = _str(node, "token", where)
        scene_token = _str(node, "scene_token", where)
        if scene_token not in scenes:
            raise ConvertError(f"{where}: scene_token {scene_token!r} is not in scene.json")
        time_us = frame_id_of(_int(node, "timestamp", where), where) * STEP_US
        samples[token] = (scenes[scene_token], time_us)
    if not samples:
        raise ConvertError(f"{os.path.join(nusc_root, 'sample.json')}: no samples")
    return samples


def unit_rotation(elements: tuple[float, ...], where: str) -> list[float]:
    """``[qw, qx, qy, qz]`` normalised; refused when its norm is not within 1e-3 of 1."""
    qw, qx, qy, qz = elements
    norm = math.sqrt(qw * qw + qx * qx + qy * qy + qz * qz)
    if abs(norm - 1.0) > QUATERNION_SLACK:
        raise ConvertError(f"{where}: rotation norm {norm:.4f} is not 1")
    return [qw / norm, qx / norm, qy / norm, qz / norm]


def box_from_detection(
    anno: dict[str, Any], where: str, *, agent_id: str, source: str
) -> dict[str, Any]:
    """One protocol ``result`` box from one detection record."""
    width, length, height = _vec(anno, "size", 3, where)
    cx, cy, cz = _vec(anno, "translation", 3, where)
    return {
        "agent_id": agent_id,
        "source": source,
        "category": _str(anno, "detection_name", where),
        "score": _float(anno, "detection_score", where),
        "track_id": None,
        "center": [cx, cy, cz],
        "size_lwh": [length, width, height],
        "quaternion": unit_rotation(_vec(anno, "rotation", 4, where), where),
        "frame": "world",
    }


def index_detections(
    det_json: str,
    samples: Samples,
    *,
    agent_id: str,
    source: str,
    min_score: float,
) -> tuple[Index, int]:
    """Boxes per ``(scene_id, time_us)`` in file order, plus the count dropped below
    ``min_score``. Every token of the JSON must be in ``samples``."""
    results = _obj(_obj(_load_json(det_json), "det json").get("results"), "det json.results")
    index: Index = {}
    dropped = 0
    for token, annos in results.items():
        if token not in samples:
            raise ConvertError(f"sample_token {token!r} is not in sample.json")
        key = samples[token]
        rows = index.setdefault(key, [])
        for i, anno in enumerate(_list(annos, f"results[{token!r}]")):
            where = f"results[{token!r}][{i}]"
            node = _obj(anno, where)
            if _float(node, "detection_score", where) < min_score:
                dropped += 1
                continue
            rows.append(box_from_detection(node, where, agent_id=agent_id, source=source))
    return index, dropped


def md5_of(path: str) -> str:
    digest = hashlib.md5()  # noqa: S324 - the toolkit documents MD5 digests
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()
