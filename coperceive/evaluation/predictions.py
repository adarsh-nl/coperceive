# SPDX-License-Identifier: Apache-2.0
"""Predictions format 1: ``run.json`` and ``boxes.parquet`` beside schema 1 scenes.

The contract is ``docs/spec/predictions.md`` (§Layout, §``run.json``,
§``boxes.parquet``, §Import). Nothing here writes into a scene directory.
Every path under the predictions root goes through :func:`confine`, and a
``run_id`` or ``scene_id`` has to pass the scene-id rule first.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import pyarrow as pa
import pyarrow.parquet as pq

from coperceive.adapters.paths import PathError, confine
from coperceive.errors import CoperceiveError, TimeQueryError
from coperceive.model import BoxFrame, Scene, require_time_us
from coperceive.se3 import SE3
from coperceive.transform import lookup_pose

FORMAT = "coperceive-predictions"
FORMAT_VERSION = 1
RUN_FILE = "run.json"
BOXES_FILE = "boxes.parquet"
QUATERNION_TOLERANCE = 1e-6

BOX_SCHEMA = pa.schema(
    [
        pa.field("agent_id", pa.string(), nullable=False),
        pa.field("source", pa.string(), nullable=False),
        pa.field("time_us", pa.int64(), nullable=False),
        pa.field("category", pa.string(), nullable=False),
        pa.field("score", pa.float64(), nullable=False),
        pa.field("track_id", pa.string(), nullable=True),
        pa.field("cx", pa.float64(), nullable=False),
        pa.field("cy", pa.float64(), nullable=False),
        pa.field("cz", pa.float64(), nullable=False),
        pa.field("length", pa.float64(), nullable=False),
        pa.field("width", pa.float64(), nullable=False),
        pa.field("height", pa.float64(), nullable=False),
        pa.field("qw", pa.float64(), nullable=False),
        pa.field("qx", pa.float64(), nullable=False),
        pa.field("qy", pa.float64(), nullable=False),
        pa.field("qz", pa.float64(), nullable=False),
        pa.field("frame", pa.string(), nullable=False),
    ]
)

_NULLABLE_COLUMNS = frozenset({"track_id"})


class PredictionError(CoperceiveError):
    """A prediction file, run or row does not follow format 1."""


def check_run_id(value: str) -> str:
    """Return ``value`` when it passes the scene-id rule (§Layout)."""
    return _check_name(value, "run_id")


def check_scene_id(value: str) -> str:
    return _check_name(value, "scene_id")


def _check_name(value: object, what: str) -> str:
    if not isinstance(value, str) or value == "" or value in {".", ".."}:
        raise PredictionError(f"{what} {value!r} is not a directory name")
    if "/" in value or "\\" in value or value.startswith("."):
        raise PredictionError(f"{what} {value!r} is not a directory name")
    return value


@dataclass(frozen=True)
class RunMeta:
    """The content of ``run.json`` (§``run.json``)."""

    run_id: str
    model: str
    score_note: str
    categories: tuple[str, ...]
    sources: tuple[tuple[str, str], ...]
    created_utc: str
    notes: str | None = None
    # Optional, written by `coperceive model run` (model-plugin spec §Provenance); a JSON
    # object that readers which do not know it ignore.
    provenance: Mapping[str, object] | None = None

    def __post_init__(self) -> None:
        check_run_id(self.run_id)
        _text(self.model, "model", allow_empty=True)
        _text(self.score_note, "score_note")
        _text(self.created_utc, "created_utc")
        for category in self.categories:
            _text(category, "categories entry")
        if len(set(self.categories)) != len(self.categories):
            raise PredictionError("categories has a duplicate")
        names: list[str] = []
        for name, note in self.sources:
            _text(name, "sources name")
            _text(note, "sources note", allow_empty=True)
            names.append(name)
        if len(set(names)) != len(names):
            raise PredictionError("sources has a duplicate name")
        if self.notes is not None:
            _text(self.notes, "notes", allow_empty=True)

    @property
    def source_names(self) -> frozenset[str]:
        return frozenset(name for name, _ in self.sources)

    def to_json(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "format": FORMAT,
            "format_version": FORMAT_VERSION,
            "run_id": self.run_id,
            "model": self.model,
            "created_utc": self.created_utc,
            "score_note": self.score_note,
            "categories": list(self.categories),
            "sources": {name: note for name, note in self.sources},
        }
        if self.notes is not None:
            payload["notes"] = self.notes
        if self.provenance is not None:
            payload["provenance"] = dict(self.provenance)
        return payload

    @staticmethod
    def from_json(payload: object, *, directory_name: str) -> RunMeta:
        """Parse a ``run.json`` object. Unknown keys are ignored (§``run.json``)."""
        data = _as_dict(payload, RUN_FILE)
        if data.get("format") != FORMAT:
            raise PredictionError(f"{RUN_FILE} format must be {FORMAT!r}")
        version = data.get("format_version")
        if type(version) is not int or version != FORMAT_VERSION:
            raise PredictionError(f"{RUN_FILE} format_version must be {FORMAT_VERSION}")
        run_id = _req_str(data, "run_id")
        if run_id != directory_name:
            raise PredictionError(
                f"{RUN_FILE} run_id {run_id!r} does not match directory {directory_name!r}"
            )
        categories_raw = data.get("categories")
        if not isinstance(categories_raw, list):
            raise PredictionError(f"{RUN_FILE} categories must be a list of strings")
        categories: list[str] = []
        for item in cast(list[object], categories_raw):
            if not isinstance(item, str):
                raise PredictionError(f"{RUN_FILE} categories must be a list of strings")
            categories.append(item)
        sources_raw = data.get("sources")
        if not isinstance(sources_raw, dict):
            raise PredictionError(f"{RUN_FILE} sources must be an object")
        sources: list[tuple[str, str]] = []
        for key, value in cast(dict[object, object], sources_raw).items():
            if not isinstance(key, str) or not isinstance(value, str):
                raise PredictionError(f"{RUN_FILE} sources must map strings to strings")
            sources.append((key, value))
        notes = data.get("notes")
        if notes is not None and not isinstance(notes, str):
            raise PredictionError(f"{RUN_FILE} notes must be a string")
        provenance_raw = data.get("provenance")
        if provenance_raw is not None and not isinstance(provenance_raw, dict):
            raise PredictionError(f"{RUN_FILE} provenance must be an object")
        provenance = (
            {str(k): v for k, v in cast(dict[object, object], provenance_raw).items()}
            if provenance_raw is not None
            else None
        )
        try:
            return RunMeta(
                run_id=run_id,
                model=_req_str(data, "model", allow_empty=True),
                score_note=_req_str(data, "score_note"),
                categories=tuple(categories),
                sources=tuple(sources),
                created_utc=_req_str(data, "created_utc"),
                notes=notes,
                provenance=provenance,
            )
        except PredictionError as error:
            raise PredictionError(f"{RUN_FILE}: {error}") from error


@dataclass(frozen=True)
class PredictionBox:
    """One row of ``boxes.parquet``, plus the scene it belongs to when known."""

    scene_id: str | None
    agent_id: str
    source: str
    time_us: int
    category: str
    score: float
    track_id: str | None
    center: tuple[float, float, float]
    size_lwh: tuple[float, float, float]
    box_from_rpy: SE3
    frame: BoxFrame

    def __post_init__(self) -> None:
        if self.scene_id is not None:
            _text(self.scene_id, "scene_id")
        _text(self.agent_id, "agent_id", allow_empty=True)
        _text(self.source, "source", allow_empty=True)
        _text(self.category, "category", allow_empty=True)
        try:
            require_time_us(self.time_us, "time_us")
        except ValueError as error:
            raise PredictionError(str(error)) from error
        object.__setattr__(self, "score", _finite(self.score, "score"))
        if self.track_id is not None:
            _text(self.track_id, "track_id")
        object.__setattr__(self, "center", _vec3(self.center, "center"))
        size = _vec3(self.size_lwh, "size_lwh")
        if any(side <= 0.0 for side in size):
            raise PredictionError("size_lwh must be positive")
        object.__setattr__(self, "size_lwh", size)
        _rotation(self.box_from_rpy)
        _frame(self.frame)


def _rotation(value: object) -> None:
    if not isinstance(value, SE3):
        raise PredictionError("box_from_rpy must be an SE3")
    if value.tx != 0.0 or value.ty != 0.0 or value.tz != 0.0:
        raise PredictionError("box_from_rpy must be a pure rotation")
    norm = math.sqrt(value.qw**2 + value.qx**2 + value.qy**2 + value.qz**2)
    if abs(norm - 1.0) > QUATERNION_TOLERANCE:
        raise PredictionError(
            f"box_from_rpy must be a unit quaternion within {QUATERNION_TOLERANCE}"
        )


def _frame(value: object) -> None:
    if not isinstance(value, BoxFrame):
        raise PredictionError("frame must be body or world")


def _text(value: object, what: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (value == "" and not allow_empty):
        raise PredictionError(f"{what} must be a non-empty string")
    return value


def predictions_root(scenes_root: Path, override: Path | None) -> Path | None:
    """The §Layout default rule: ``override``, else ``<scenes root>/predictions``
    when that directory has a child with ``run.json``, else ``None``."""
    if override is not None:
        return override
    candidate = scenes_root / "predictions"
    if list_runs(candidate):
        return candidate
    return None


def list_runs(root: Path) -> tuple[str, ...]:
    """Run ids under ``root`` (children with ``run.json``), sorted."""
    if not root.is_dir():
        return ()
    found: list[str] = []
    for child in sorted(root.iterdir()):
        name = child.name
        if name.startswith(".") or not child.is_dir():
            continue
        try:
            check_run_id(name)
        except PredictionError:
            continue
        if (child / RUN_FILE).is_file():
            found.append(name)
    return tuple(found)


def read_run_meta(root: Path, run_id: str) -> RunMeta:
    path = _run_path(root, run_id, RUN_FILE)
    if not path.is_file():
        raise PredictionError(f"run {run_id!r} has no {RUN_FILE} under {root}")
    try:
        loaded: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise PredictionError(f"{path}: {error}") from error
    return RunMeta.from_json(loaded, directory_name=run_id)


def read_boxes(root: Path, run_id: str, scene_id: str) -> tuple[PredictionBox, ...]:
    """The rows of ``<root>/<run_id>/<scene_id>/boxes.parquet`` in file order."""
    check_scene_id(scene_id)
    path = _run_path(root, run_id, f"{scene_id}/{BOXES_FILE}")
    if not path.is_file():
        raise PredictionError(f"run {run_id!r} has no {BOXES_FILE} for scene {scene_id!r}")
    rows = _parquet_rows(path)
    return tuple(_box_from_row({**row, "scene_id": scene_id}, i) for i, row in enumerate(rows))


def write_run(
    root: Path,
    run_id: str,
    meta: RunMeta,
    rows: Iterable[PredictionBox],
    *,
    force: bool = False,
) -> tuple[str, ...]:
    """Write ``run.json`` and one ``boxes.parquet`` per scene; return the scene ids.

    Rows are grouped by ``scene_id`` and keep their order inside a scene. Every
    row needs a ``scene_id`` and a ``source`` listed in ``meta``. Checks against
    the scene itself (agent, pose time) are :func:`validate_boxes`, run by the
    caller before this. A scene that already has ``boxes.parquet`` is refused
    unless ``force``; nothing is written before every row and target passed.
    """
    check_run_id(run_id)
    if meta.run_id != run_id:
        raise PredictionError(f"meta run_id {meta.run_id!r} is not {run_id!r}")
    by_scene: dict[str, list[dict[str, object]]] = {}
    for i, box in enumerate(rows):
        if box.scene_id is None:
            raise PredictionError(f"row {i}: scene_id is required to write a run")
        try:
            check_scene_id(box.scene_id)
        except PredictionError as error:
            raise PredictionError(f"row {i}: {error}") from error
        if box.source not in meta.source_names:
            raise PredictionError(f"row {i}: source {box.source!r} is not in run.json sources")
        if box.category == "":
            raise PredictionError(f"row {i}: category must be non-empty")
        by_scene.setdefault(box.scene_id, []).append(_row_from_box(box))
    run_dir = _run_path(root, run_id, ".")
    targets: dict[str, Path] = {}
    for scene_id in by_scene:
        target = _run_path(root, run_id, f"{scene_id}/{BOXES_FILE}")
        if target.exists() and not force:
            raise PredictionError(
                f"scene {scene_id!r} of run {run_id!r} already has {BOXES_FILE}; use force"
            )
        targets[scene_id] = target
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / RUN_FILE).write_text(json.dumps(meta.to_json(), indent=2) + "\n", encoding="utf-8")
    for scene_id, target in targets.items():
        target.parent.mkdir(parents=True, exist_ok=True)
        table = pa.Table.from_pylist(by_scene[scene_id], schema=BOX_SCHEMA)
        pq.write_table(table, target)
    return tuple(targets)


def validate_boxes(scene: Scene, meta: RunMeta, boxes: Sequence[PredictionBox]) -> None:
    """Check every row against ``scene`` and ``meta``; the first failure names its row."""
    agent_ids = {agent.agent_id for agent in scene.agents}
    sources = meta.source_names
    for i, box in enumerate(boxes):
        if box.scene_id is not None and box.scene_id != scene.scene_id:
            raise PredictionError(
                f"row {i}: scene_id {box.scene_id!r} is not scene {scene.scene_id!r}"
            )
        if box.agent_id not in agent_ids:
            raise PredictionError(f"row {i}: agent {box.agent_id!r} is not an agent of the scene")
        if box.source not in sources:
            raise PredictionError(f"row {i}: source {box.source!r} is not in run.json sources")
        if box.category == "":
            raise PredictionError(f"row {i}: category must be non-empty")
        try:
            lookup_pose(scene.agent(box.agent_id), box.time_us, max_gap_us=0)
        except TimeQueryError as error:
            raise PredictionError(
                f"row {i}: time_us {box.time_us} is not a pose time of agent "
                f"{box.agent_id!r} ({error})"
            ) from error


def load_rows(path: Path) -> tuple[PredictionBox, ...]:
    """Rows of a ``.jsonl`` or ``.parquet`` import file; ``scene_id`` is optional."""
    suffix = path.suffix.lower()
    if suffix == ".jsonl":
        rows = _jsonl_rows(path)
    elif suffix == ".parquet":
        rows = _parquet_rows(path)
    else:
        raise PredictionError(f"{path.name}: import file must be .jsonl or .parquet")
    return tuple(_box_from_row(row, i) for i, row in enumerate(rows))


def _run_path(root: Path, run_id: str, relative: str) -> Path:
    check_run_id(run_id)
    try:
        return confine(root, f"{run_id}/{relative}")
    except PathError as error:
        raise PredictionError(str(error)) from error


def _jsonl_rows(path: Path) -> list[dict[str, object]]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise PredictionError(f"{path}: {error}") from error
    rows: list[dict[str, object]] = []
    for line in text.splitlines():
        if line.strip() == "":
            continue
        index = len(rows)
        try:
            loaded: object = json.loads(line)
        except ValueError as error:
            raise PredictionError(f"row {index}: {error}") from error
        try:
            rows.append(_as_dict(loaded, "row"))
        except PredictionError as error:
            raise PredictionError(f"row {index}: {error}") from error
    return rows


def _parquet_rows(path: Path) -> list[dict[str, object]]:
    try:
        table = pq.read_table(path)
    except Exception as error:  # pyarrow error types are not in the stubs
        raise PredictionError(f"{path}: {error}") from error
    present = set(table.schema.names)
    missing = [name for name in BOX_SCHEMA.names if name not in present]
    if missing:
        raise PredictionError(f"{path.name} is missing column(s) {', '.join(missing)}")
    rows: list[dict[str, object]] = []
    for item in table.to_pylist():
        converted: dict[str, object] = {}
        for key, value in item.items():
            converted[key] = value
        rows.append(converted)
    return rows


def _row_from_box(box: PredictionBox) -> dict[str, object]:
    rotation = box.box_from_rpy
    return {
        "agent_id": box.agent_id,
        "source": box.source,
        "time_us": box.time_us,
        "category": box.category,
        "score": box.score,
        "track_id": box.track_id,
        "cx": box.center[0],
        "cy": box.center[1],
        "cz": box.center[2],
        "length": box.size_lwh[0],
        "width": box.size_lwh[1],
        "height": box.size_lwh[2],
        "qw": rotation.qw,
        "qx": rotation.qx,
        "qy": rotation.qy,
        "qz": rotation.qz,
        "frame": box.frame.value,
    }


def _box_from_row(row: dict[str, object], index: int) -> PredictionBox:
    try:
        for name in BOX_SCHEMA.names:
            if name not in row and name not in _NULLABLE_COLUMNS:
                raise PredictionError(f"missing {name}")
        scene_id = row.get("scene_id")
        if scene_id is not None and not isinstance(scene_id, str):
            raise PredictionError("scene_id must be a string or null")
        frame_raw = row.get("frame")
        try:
            frame = BoxFrame.parse(frame_raw)
        except ValueError as error:
            raise PredictionError(str(error)) from error
        return PredictionBox(
            scene_id=scene_id,
            agent_id=_req_str(row, "agent_id"),
            source=_req_str(row, "source"),
            time_us=_req_time(row, "time_us"),
            category=_req_str(row, "category", allow_empty=True),
            score=_req_float(row, "score"),
            track_id=_opt_str(row, "track_id"),
            center=(_req_float(row, "cx"), _req_float(row, "cy"), _req_float(row, "cz")),
            size_lwh=(
                _req_float(row, "length"),
                _req_float(row, "width"),
                _req_float(row, "height"),
            ),
            box_from_rpy=SE3(
                _req_float(row, "qw"),
                _req_float(row, "qx"),
                _req_float(row, "qy"),
                _req_float(row, "qz"),
                0.0,
                0.0,
                0.0,
            ),
            frame=frame,
        )
    except (PredictionError, ValueError) as error:
        raise PredictionError(f"row {index}: {error}") from error


def _as_dict(value: object, what: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise PredictionError(f"{what} must be a JSON object")
    result: dict[str, object] = {}
    for key, item in cast(dict[object, object], value).items():
        if not isinstance(key, str):
            raise PredictionError(f"{what} has a non-string key")
        result[key] = item
    return result


def _req_str(row: dict[str, object], key: str, *, allow_empty: bool = False) -> str:
    value = row.get(key)
    if not isinstance(value, str) or (value == "" and not allow_empty):
        raise PredictionError(f"{key} must be a non-empty string")
    return value


def _opt_str(row: dict[str, object], key: str) -> str | None:
    value = row.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or value == "":
        raise PredictionError(f"{key} must be a non-empty string or null")
    return value


def _req_time(row: dict[str, object], key: str) -> int:
    try:
        return require_time_us(row.get(key), key)
    except ValueError as error:
        raise PredictionError(str(error)) from error


def _req_float(row: dict[str, object], key: str) -> float:
    return _finite(row.get(key), key)


def _finite(value: object, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PredictionError(f"{what} must be a finite float")
    number = float(value)
    if not math.isfinite(number):
        raise PredictionError(f"{what} must be a finite float")
    return number


def _vec3(value: object, what: str) -> tuple[float, float, float]:
    if not isinstance(value, tuple):
        raise PredictionError(f"{what} must be a tuple of three floats")
    items = cast(tuple[object, ...], value)
    if len(items) != 3:
        raise PredictionError(f"{what} must be a tuple of three floats")
    return (_finite(items[0], what), _finite(items[1], what), _finite(items[2], what))
