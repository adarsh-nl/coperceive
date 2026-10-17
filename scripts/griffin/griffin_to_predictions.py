# SPDX-License-Identifier: Apache-2.0
"""Convert a Griffin toolkit ``results_nusc_det.json`` into coperceive predictions format 1.

Runs with coperceive's own environment (Python >= 3.10), not the Griffin env.
Destination: ``scripts/griffin_to_predictions.py`` in coperceive.

Input (``wang-jh18-SVM/Griffin``, ``projects/mmdet3d_plugin/datasets/griffin_dataset.py``
``_format_bbox_det`` -> ``results_nusc_det.json``), per box::

    sample_token, translation [x, y, z], size [w, l, h], rotation [qw, qx, qy, qz],
    velocity, detection_name, detection_score, attribute_name

The boxes are in the global (ENU world) frame: ``lidar_nusc_box_to_global`` in
``datasets/data_utils/data_utils.py`` rotates/translates each box with
``info['ego2global_rotation']`` / ``info['ego2global_translation']`` after a per-class
radius cut (``class_range`` 50 m). ``size`` is nuScenes ``wlh``; ``rotation`` is a
pyquaternion ``[w, x, y, z]``.

``sample.json`` / ``scene.json`` (``tools/griffin_data_converter/trans_kitti2nuscenes.py``):
``sample`` rows carry ``token`` (``sample_<timestamp>``), ``timestamp`` and
``scene_token``; ``scene`` rows carry ``token`` and ``name``
(``f'scene-{scene_num:04}-{scene_info["name"]}'``). The timestamp is
``_frame_number_to_nuscenes_timestamp``: ``datetime(2025, 1, 1, 9, 0, 0) +
timedelta(seconds=int(frame) * 0.1)`` in microseconds since the Unix epoch, so
``frame_id = round((timestamp - BASE_US) / 100_000)`` and coperceive's
``time_us = frame_id * 100_000`` (``griffin.yaml`` ``clock.scale_us: 100000``).

Mapping to ``PredictionBox``: ``scene_id`` = the part of the scene name after
``scene-NNNN-`` (coperceive ``griffin.yaml`` ``scene_id: name``), ``agent_id``
``vehicle``, ``source`` ``single``, ``category`` = ``detection_name``,
``score`` = ``detection_score``, ``track_id`` None, ``center`` = ``translation``,
``size_lwh`` = ``(size[1], size[0], size[2])``, ``box_from_rpy`` = the unit
quaternion as a pure rotation, ``frame`` = ``world``.

Every scene is checked with ``validate_boxes(read_scene(...), meta, rows)`` before
``write_run`` writes anything.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import cast

from coperceive.errors import CoperceiveError
from coperceive.evaluation.predictions import (
    PredictionBox,
    PredictionError,
    RunMeta,
    validate_boxes,
    write_run,
)
from coperceive.model import BoxFrame
from coperceive.se3 import SE3
from coperceive.store import read_scene

BASE_US = int((datetime(2025, 1, 1, 9, 0, 0) - datetime(1970, 1, 1)).total_seconds() * 1_000_000)
STEP_US = 100_000
TIMESTAMP_SLACK_US = 1_000
QUATERNION_SLACK = 1e-3
DEFAULT_CATEGORIES = ("car", "bicycle", "pedestrian")


class ConvertError(Exception):
    """The input does not look like a Griffin detection JSON plus its nuScenes tables."""


def _load_json(path: Path) -> object:
    try:
        return cast(object, json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError) as error:
        raise ConvertError(f"{path}: {error}") from error


def _obj(value: object, where: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ConvertError(f"{where}: expected an object")
    return cast(dict[str, object], value)


def _list(value: object, where: str) -> list[object]:
    if not isinstance(value, list):
        raise ConvertError(f"{where}: expected a list")
    return cast(list[object], value)


def _str(node: dict[str, object], key: str, where: str) -> str:
    value = node.get(key)
    if not isinstance(value, str) or value == "":
        raise ConvertError(f"{where}.{key}: expected a non-empty string")
    return value


def _int(node: dict[str, object], key: str, where: str) -> int:
    value = node.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConvertError(f"{where}.{key}: expected an int")
    return value


def _float(node: dict[str, object], key: str, where: str) -> float:
    value = node.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConvertError(f"{where}.{key}: expected a number")
    number = float(value)
    if not math.isfinite(number):
        raise ConvertError(f"{where}.{key}: expected a finite number")
    return number


def _vec(node: dict[str, object], key: str, size: int, where: str) -> tuple[float, ...]:
    items = _list(node.get(key), f"{where}.{key}")
    if len(items) != size:
        raise ConvertError(f"{where}.{key}: expected {size} numbers")
    out: list[float] = []
    for i, item in enumerate(items):
        if isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(item):
            raise ConvertError(f"{where}.{key}[{i}]: expected a finite number")
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
    frame_id = round(delta / STEP_US)
    if frame_id < 0 or abs(delta - frame_id * STEP_US) > TIMESTAMP_SLACK_US:
        raise ConvertError(f"{where}: timestamp {timestamp_us} is not BASE + frame_id * 0.1 s")
    return frame_id


def read_samples(nusc_root: Path) -> dict[str, tuple[str, int]]:
    """``sample_token -> (scene_id, time_us)`` from ``sample.json`` and ``scene.json``."""
    scenes: dict[str, str] = {}
    for i, row in enumerate(_list(_load_json(nusc_root / "scene.json"), "scene.json")):
        node = _obj(row, f"scene.json[{i}]")
        scenes[_str(node, "token", f"scene.json[{i}]")] = scene_id_of(
            _str(node, "name", f"scene.json[{i}]")
        )
    samples: dict[str, tuple[str, int]] = {}
    for i, row in enumerate(_list(_load_json(nusc_root / "sample.json"), "sample.json")):
        where = f"sample.json[{i}]"
        node = _obj(row, where)
        token = _str(node, "token", where)
        scene_token = _str(node, "scene_token", where)
        if scene_token not in scenes:
            raise ConvertError(f"{where}: scene_token {scene_token!r} is not in scene.json")
        time_us = frame_id_of(_int(node, "timestamp", where), where) * STEP_US
        samples[token] = (scenes[scene_token], time_us)
    if not samples:
        raise ConvertError(f"{nusc_root / 'sample.json'}: no samples")
    return samples


def unit_rotation(elements: tuple[float, ...], where: str) -> SE3:
    qw, qx, qy, qz = elements
    norm = math.sqrt(qw * qw + qx * qx + qy * qy + qz * qz)
    if abs(norm - 1.0) > QUATERNION_SLACK:
        raise ConvertError(f"{where}: rotation norm {norm:.4f} is not 1")
    return SE3(qw / norm, qx / norm, qy / norm, qz / norm, 0.0, 0.0, 0.0)


def convert_boxes(
    det_json: Path,
    samples: dict[str, tuple[str, int]],
    *,
    agent_id: str,
    source: str,
    min_score: float,
) -> tuple[dict[str, list[PredictionBox]], int]:
    """Rows grouped by scene (file order kept), plus the count dropped below ``min_score``."""
    results = _obj(_obj(_load_json(det_json), "det json").get("results"), "det json.results")
    by_scene: dict[str, list[PredictionBox]] = {}
    dropped = 0
    for token, annos in results.items():
        if token not in samples:
            raise ConvertError(f"sample_token {token!r} is not in sample.json")
        scene_id, time_us = samples[token]
        for i, anno in enumerate(_list(annos, f"results[{token!r}]")):
            where = f"results[{token!r}][{i}]"
            node = _obj(anno, where)
            score = _float(node, "detection_score", where)
            if score < min_score:
                dropped += 1
                continue
            width, length, height = _vec(node, "size", 3, where)
            cx, cy, cz = _vec(node, "translation", 3, where)
            try:
                box = PredictionBox(
                    scene_id=scene_id,
                    agent_id=agent_id,
                    source=source,
                    time_us=time_us,
                    category=_str(node, "detection_name", where),
                    score=score,
                    track_id=None,
                    center=(cx, cy, cz),
                    size_lwh=(length, width, height),
                    box_from_rpy=unit_rotation(_vec(node, "rotation", 4, where), where),
                    frame=BoxFrame.WORLD,
                )
            except PredictionError as error:
                raise ConvertError(f"{where}: {error}") from error
            by_scene.setdefault(scene_id, []).append(box)
    return by_scene, dropped


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Griffin results_nusc_det.json -> coperceive predictions format 1."
    )
    parser.add_argument("--det-json", required=True, type=Path, help="results_nusc_det.json")
    parser.add_argument(
        "--nusc-root",
        required=True,
        type=Path,
        help="Directory holding sample.json and scene.json "
        "(<griffin-nuscenes>/vehicle-side/v1.0-trainval).",
    )
    parser.add_argument("--scenes", required=True, type=Path, help="coperceive scenes root.")
    parser.add_argument(
        "--predictions",
        type=Path,
        default=None,
        help="Predictions root. Default: SCENES/predictions.",
    )
    parser.add_argument("--run", required=True, help="Run id; the directory name.")
    parser.add_argument("--model", default="", help="run.json model: name, checkpoint, commit.")
    parser.add_argument("--score-note", required=True, help="run.json score_note.")
    parser.add_argument("--notes", default=None, help="run.json notes (licence, citation).")
    parser.add_argument("--agent", default="vehicle", help="agent_id of every row.")
    parser.add_argument("--source", default="single", help="source of every row.")
    parser.add_argument(
        "--source-note",
        default="vehicle-side camera-only model, no fusion",
        help="run.json sources note.",
    )
    parser.add_argument(
        "--min-score",
        type=float,
        default=0.0,
        help="Drop boxes with detection_score below this (the operating point; 0 keeps all).",
    )
    parser.add_argument(
        "--categories",
        default=",".join(DEFAULT_CATEGORIES),
        help="run.json categories, comma-separated.",
    )
    parser.add_argument("--force", action="store_true", help="Overwrite an existing boxes.parquet.")
    return parser


def run(args: argparse.Namespace) -> int:
    det_json = cast(Path, args.det_json)
    nusc_root = cast(Path, args.nusc_root)
    scenes_root = cast(Path, args.scenes)
    predictions_override = cast("Path | None", args.predictions)
    predictions_root = (
        predictions_override if predictions_override is not None else scenes_root / "predictions"
    )
    run_id = cast(str, args.run)
    min_score = float(cast(float, args.min_score))
    categories = tuple(c for c in cast(str, args.categories).split(",") if c)

    samples = read_samples(nusc_root)
    by_scene, dropped = convert_boxes(
        det_json,
        samples,
        agent_id=cast(str, args.agent),
        source=cast(str, args.source),
        min_score=min_score,
    )
    if not by_scene:
        raise ConvertError("no boxes left after the score cut")
    unknown = sorted({box.category for rows in by_scene.values() for box in rows} - set(categories))
    if unknown:
        raise ConvertError(f"categories {unknown} are not in --categories {list(categories)}")

    meta = RunMeta(
        run_id=run_id,
        model=cast(str, args.model),
        score_note=cast(str, args.score_note),
        categories=categories,
        sources=((cast(str, args.source), cast(str, args.source_note)),),
        created_utc=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        notes=cast("str | None", args.notes),
    )

    missing = [
        scene_id
        for scene_id in sorted(by_scene)
        if not (scenes_root / scene_id / "manifest.json").is_file()
    ]
    if missing:
        raise ConvertError(f"scenes {missing} are not under {scenes_root}")
    for scene_id in sorted(by_scene):
        scene = read_scene(scenes_root / scene_id)
        validate_boxes(scene, meta, by_scene[scene_id])

    rows: list[PredictionBox] = [box for scene_id in sorted(by_scene) for box in by_scene[scene_id]]
    written = write_run(predictions_root, run_id, meta, rows, force=bool(cast(bool, args.force)))

    per_category: Counter[str] = Counter(box.category for box in rows)
    print(
        f"run {run_id}: {len(rows)} boxes in {len(written)} scenes -> {predictions_root / run_id}"
    )
    print(f"dropped below score {min_score:g}: {dropped}")
    for scene_id in written:
        print(f"  {scene_id}: {len(by_scene[scene_id])} boxes")
    print(
        "  categories: "
        + ", ".join(f"{name}={count}" for name, count in sorted(per_category.items()))
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return run(args)
    except (ConvertError, CoperceiveError, OSError, ValueError) as error:
        print(f"griffin_to_predictions: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
