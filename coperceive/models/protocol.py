# SPDX-License-Identifier: Apache-2.0
"""Protocol version 1 between coperceive and a model plugin (spec §Protocol).

One JSON object per line, UTF-8, ``\\n`` terminated. Every message carries ``type`` and
``protocol``. The parent speaks ``configure``, ``frame``, ``cancel`` and ``bye``; the child
answers ``hello``, ``configured``, ``result``, ``progress`` and ``error``. Payloads larger
than a line (images, sweeps) travel as paths. ``decode`` validates every field before use:
a bad line is a :class:`ProtocolError` with code ``bad_message``.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import IO, cast

from coperceive.errors import CoperceiveError
from coperceive.evaluation.predictions import PredictionBox, PredictionError
from coperceive.model import BoxFrame
from coperceive.se3 import SE3

PROTOCOL = 1
MAX_LINE = 16 * 1024 * 1024
ERROR_CODES = frozenset(
    {"unsupported", "configure_failed", "frame_failed", "cancelled", "bad_message", "internal"}
)
POINT_LAYOUTS = frozenset({"xyzi_f32le"})
_MD5 = re.compile(r"^[0-9a-f]{32}$")  # hello.environment.checkpoint_md5 (Adarsh, 2026-10-18)

PoseJson = Mapping[str, object]
SensorJson = Mapping[str, object]


class ProtocolError(CoperceiveError):
    """A line that is not a valid protocol message. ``code`` is a §Errors code."""

    def __init__(self, message: str, *, code: str = "bad_message") -> None:
        super().__init__(message)
        self.code = code


# --- messages (spec §Messages) --------------------------------------------------------------


@dataclass(frozen=True)
class Capabilities:
    live: bool
    cancel: bool
    max_inflight: int


@dataclass(frozen=True)
class Hello:
    plugin: str
    version: str
    models: tuple[str, ...]
    capabilities: Capabilities
    environment: Mapping[str, str]
    git_commit: str | None


@dataclass(frozen=True)
class AgentRef:
    agent_id: str
    agent_type: str


@dataclass(frozen=True)
class Configure:
    model: str
    scene_id: str
    agents: tuple[AgentRef, ...]
    source: str
    options: Mapping[str, object]
    work_dir: str
    scene_dir: str
    raw_root: str | None
    artifacts: Mapping[str, Mapping[str, str]]
    device: str
    sensors: tuple[SensorJson, ...]
    samples: tuple[int, ...]


@dataclass(frozen=True)
class Configured:
    scene_id: str
    # Optional (Adarsh, 2026-10-19): what the child measured while setting the scene up, in
    # seconds, e.g. {conversion_s, inference_s}; absent when it measured nothing.
    timings: Mapping[str, float] = field(default_factory=lambda: {})


@dataclass(frozen=True)
class ImageInput:
    agent_id: str
    sensor_id: str
    path: str


@dataclass(frozen=True)
class PointsInput:
    agent_id: str
    sensor_id: str
    path: str
    count: int
    layout: str


@dataclass(frozen=True)
class Frame:
    id: int
    scene_id: str
    time_us: int
    poses: Mapping[str, PoseJson]
    images: tuple[ImageInput, ...]
    points: tuple[PointsInput, ...]


@dataclass(frozen=True)
class BoxRow:
    agent_id: str
    source: str
    category: str
    score: float
    track_id: str | None
    center: tuple[float, float, float]
    size_lwh: tuple[float, float, float]
    quaternion: tuple[float, float, float, float]  # qw, qx, qy, qz
    frame: str  # body | world


@dataclass(frozen=True)
class Result:
    id: int
    boxes: tuple[BoxRow, ...]
    artifacts: tuple[str, ...]


@dataclass(frozen=True)
class Progress:
    id: int | None
    fraction: float | None
    text: str


@dataclass(frozen=True)
class Error:
    id: int | None
    code: str
    message: str


@dataclass(frozen=True)
class Cancel:
    id: int | None


@dataclass(frozen=True)
class Bye:
    pass


Message = Hello | Configure | Configured | Frame | Result | Progress | Error | Cancel | Bye

_TYPES: dict[type, str] = {
    Hello: "hello",
    Configure: "configure",
    Configured: "configured",
    Frame: "frame",
    Result: "result",
    Progress: "progress",
    Error: "error",
    Cancel: "cancel",
    Bye: "bye",
}


# --- encoding -------------------------------------------------------------------------------


def encode(message: Message) -> bytes:
    """One ``\\n``-terminated line without embedded newlines."""
    payload: dict[str, object] = {"type": _TYPES[type(message)], "protocol": PROTOCOL}
    payload.update(_fields(message))
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    if "\n" in text:  # json.dumps escapes newlines inside strings; this guards the contract
        raise ProtocolError("encoded message contains a newline")
    return text.encode("utf-8") + b"\n"


def _fields(message: Message) -> dict[str, object]:
    if isinstance(message, Hello):
        return {
            "plugin": message.plugin,
            "version": message.version,
            "models": list(message.models),
            "capabilities": {
                "live": message.capabilities.live,
                "cancel": message.capabilities.cancel,
                "max_inflight": message.capabilities.max_inflight,
            },
            "environment": dict(message.environment),
            "git_commit": message.git_commit,
        }
    if isinstance(message, Configure):
        out: dict[str, object] = {
            "model": message.model,
            "scene_id": message.scene_id,
            "agents": [
                {"agent_id": agent.agent_id, "agent_type": agent.agent_type}
                for agent in message.agents
            ],
            "source": message.source,
            "options": dict(message.options),
            "work_dir": message.work_dir,
            "scene_dir": message.scene_dir,
            "device": message.device,
            "sensors": [dict(sensor) for sensor in message.sensors],
            "samples": list(message.samples),
        }
        if message.raw_root is not None:
            out["raw_root"] = message.raw_root
        if message.artifacts:
            out["artifacts"] = {k: dict(v) for k, v in message.artifacts.items()}
        return out
    if isinstance(message, Configured):
        out_c: dict[str, object] = {"scene_id": message.scene_id}
        if message.timings:
            out_c["timings"] = dict(message.timings)
        return out_c
    if isinstance(message, Frame):
        return {
            "id": message.id,
            "scene_id": message.scene_id,
            "time_us": message.time_us,
            "poses": {k: dict(v) for k, v in message.poses.items()},
            "images": [
                {"agent_id": i.agent_id, "sensor_id": i.sensor_id, "path": i.path}
                for i in message.images
            ],
            "points": [
                {
                    "agent_id": p.agent_id,
                    "sensor_id": p.sensor_id,
                    "path": p.path,
                    "count": p.count,
                    "layout": p.layout,
                }
                for p in message.points
            ],
        }
    if isinstance(message, Result):
        return {
            "id": message.id,
            "boxes": [
                {
                    "agent_id": b.agent_id,
                    "source": b.source,
                    "category": b.category,
                    "score": b.score,
                    "track_id": b.track_id,
                    "center": list(b.center),
                    "size_lwh": list(b.size_lwh),
                    "quaternion": list(b.quaternion),
                    "frame": b.frame,
                }
                for b in message.boxes
            ],
            "artifacts": list(message.artifacts),
        }
    if isinstance(message, Progress):
        return {"id": message.id, "fraction": message.fraction, "text": message.text}
    if isinstance(message, Error):
        return {"id": message.id, "code": message.code, "message": message.message}
    if isinstance(message, Cancel):
        return {"id": message.id}
    return {}


# --- decoding -------------------------------------------------------------------------------


def decode(line: bytes) -> Message:
    """Parse and validate one line; any problem is a :class:`ProtocolError` (``bad_message``)."""
    if len(line) > MAX_LINE:
        raise ProtocolError(f"line of {len(line)} bytes exceeds {MAX_LINE} (16 MiB)")
    try:
        parsed: object = json.loads(line.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProtocolError(f"line is not JSON: {exc}") from None
    if not isinstance(parsed, dict):
        raise ProtocolError("line is not a JSON object")
    node = _Obj("message", cast(dict[str, object], parsed))
    kind = node.str("type")
    if node.any("protocol") != PROTOCOL:
        raise ProtocolError(f"message.protocol: expected {PROTOCOL}")
    decoder = _DECODERS.get(kind)
    if decoder is None:
        raise ProtocolError(f"message.type: unknown type {kind!r}")
    return decoder(node)


class _Obj:
    """A JSON object with typed field access and key-path errors."""

    def __init__(self, path: str, payload: Mapping[str, object]) -> None:
        self.path = path
        self.payload = payload

    def at(self, key: str) -> str:
        return f"{self.path}.{key}"

    def any(self, key: str, *, optional: bool = False) -> object:
        value = self.payload.get(key)
        if value is None:
            if optional:
                return None
            raise ProtocolError(f"{self.at(key)}: required")
        return value

    def str(self, key: str, *, optional: bool = False) -> str:
        value = self.any(key, optional=optional)
        if value is None:
            return ""
        if not isinstance(value, str):
            raise ProtocolError(f"{self.at(key)}: expected a string")
        return value

    def opt_str(self, key: str) -> str | None:
        value = self.any(key, optional=True)
        if value is None:
            return None
        if not isinstance(value, str):
            raise ProtocolError(f"{self.at(key)}: expected a string or null")
        return value

    def int(self, key: str) -> int:
        value = self.any(key)
        if isinstance(value, bool) or not isinstance(value, int):
            raise ProtocolError(f"{self.at(key)}: expected an integer")
        return value

    def opt_int(self, key: str) -> int | None:
        value = self.any(key, optional=True)
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int):
            raise ProtocolError(f"{self.at(key)}: expected an integer or null")
        return value

    def num(self, key: str) -> float:
        value = self.any(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ProtocolError(f"{self.at(key)}: expected a number")
        if not math.isfinite(value):
            raise ProtocolError(f"{self.at(key)}: expected a finite number")
        return float(value)

    def opt_num(self, key: str) -> float | None:
        if self.payload.get(key) is None:
            return None
        return self.num(key)

    def bool(self, key: str) -> bool:
        value = self.any(key)
        if not isinstance(value, bool):
            raise ProtocolError(f"{self.at(key)}: expected true or false")
        return value

    def list(self, key: str, *, optional: bool = False) -> list[object]:
        value = self.any(key, optional=optional)
        if value is None:
            return []
        if not isinstance(value, list):
            raise ProtocolError(f"{self.at(key)}: expected a list")
        return cast(list[object], value)

    def strs(self, key: str, *, optional: bool = False) -> tuple[str, ...]:
        items = self.list(key, optional=optional)
        if not all(isinstance(item, str) for item in items):
            raise ProtocolError(f"{self.at(key)}: expected a list of strings")
        return tuple(cast(list[str], items))

    def ints(self, key: str) -> tuple[int, ...]:
        items = self.list(key)
        if not all(isinstance(item, int) and not isinstance(item, bool) for item in items):
            raise ProtocolError(f"{self.at(key)}: expected a list of integers")
        return tuple(cast(list[int], items))

    def nums(self, key: str, count: int) -> tuple[float, ...]:
        items = self.list(key)
        if len(items) != count or not all(
            isinstance(item, (int, float)) and not isinstance(item, bool) for item in items
        ):
            raise ProtocolError(f"{self.at(key)}: expected {count} numbers")
        values = tuple(float(cast(float, item)) for item in items)
        if not all(math.isfinite(value) for value in values):
            raise ProtocolError(f"{self.at(key)}: expected finite numbers")
        return values

    def obj(self, key: str, *, optional: bool = False) -> _Obj | None:
        value = self.any(key, optional=optional)
        if value is None:
            return None
        if not isinstance(value, dict):
            raise ProtocolError(f"{self.at(key)}: expected an object")
        return _Obj(self.at(key), cast(dict[str, object], value))

    def objs(self, key: str, *, optional: bool = False) -> list[_Obj]:
        out: list[_Obj] = []
        for index, item in enumerate(self.list(key, optional=optional)):
            if not isinstance(item, dict):
                raise ProtocolError(f"{self.at(key)}[{index}]: expected an object")
            out.append(_Obj(f"{self.at(key)}[{index}]", cast(dict[str, object], item)))
        return out

    def str_map(self, key: str, *, optional: bool = False) -> dict[str, str]:
        node = self.obj(key, optional=optional)
        if node is None:
            return {}
        out: dict[str, str] = {}
        for name in node.payload:
            out[name] = node.str(name)
        return out


def _hello(node: _Obj) -> Hello:
    caps = node.obj("capabilities")
    assert caps is not None
    capabilities = Capabilities(
        live=caps.bool("live"), cancel=caps.bool("cancel"), max_inflight=caps.int("max_inflight")
    )
    if capabilities.max_inflight < 1:
        raise ProtocolError("message.capabilities.max_inflight: expected at least 1")
    environment = node.str_map("environment")
    if not environment:
        raise ProtocolError("message.environment: expected a non-empty map of strings")
    digest = environment.get("checkpoint_md5")
    if digest is None or not _MD5.fullmatch(digest):
        raise ProtocolError(
            "message.environment.checkpoint_md5: required, 32 lowercase hex characters"
        )
    return Hello(
        plugin=node.str("plugin"),
        version=node.str("version"),
        models=node.strs("models"),
        capabilities=capabilities,
        environment=environment,
        git_commit=node.opt_str("git_commit"),
    )


def _configure(node: _Obj) -> Configure:
    options = node.obj("options", optional=True)
    artifacts_node = node.obj("artifacts", optional=True)
    artifacts: dict[str, Mapping[str, str]] = {}
    if artifacts_node is not None:
        for model in artifacts_node.payload:
            artifacts[model] = artifacts_node.str_map(model)
    return Configure(
        model=node.str("model"),
        scene_id=node.str("scene_id"),
        agents=tuple(
            AgentRef(agent.str("agent_id"), agent.str("agent_type"))
            for agent in node.objs("agents")
        ),
        source=node.str("source"),
        options=dict(options.payload) if options is not None else {},
        work_dir=node.str("work_dir"),
        scene_dir=node.str("scene_dir"),
        raw_root=node.opt_str("raw_root"),
        artifacts=artifacts,
        device=node.str("device"),
        sensors=tuple(dict(sensor.payload) for sensor in node.objs("sensors")),
        samples=node.ints("samples"),
    )


def _configured(node: _Obj) -> Configured:
    timings: dict[str, float] = {}
    raw = node.obj("timings", optional=True)
    if raw is not None:
        for key in raw.payload:
            value = raw.num(key)
            if value < 0:
                raise ProtocolError(f"{raw.at(key)}: expected a non-negative number")
            timings[key] = value
    return Configured(scene_id=node.str("scene_id"), timings=timings)


def _frame(node: _Obj) -> Frame:
    poses_node = node.obj("poses")
    assert poses_node is not None
    poses: dict[str, PoseJson] = {}
    for agent_id in poses_node.payload:
        pose = poses_node.obj(agent_id)
        assert pose is not None
        for key in ("qw", "qx", "qy", "qz", "tx", "ty", "tz"):
            pose.num(key)
        poses[agent_id] = dict(pose.payload)
    images = tuple(
        ImageInput(image.str("agent_id"), image.str("sensor_id"), image.str("path"))
        for image in node.objs("images", optional=True)
    )
    points: list[PointsInput] = []
    for sweep in node.objs("points", optional=True):
        layout = sweep.str("layout")
        if layout not in POINT_LAYOUTS:
            raise ProtocolError(f"{sweep.at('layout')}: unknown layout {layout!r}")
        points.append(
            PointsInput(
                sweep.str("agent_id"),
                sweep.str("sensor_id"),
                sweep.str("path"),
                sweep.int("count"),
                layout,
            )
        )
    return Frame(
        id=node.int("id"),
        scene_id=node.str("scene_id"),
        time_us=node.int("time_us"),
        poses=poses,
        images=images,
        points=tuple(points),
    )


def _box(node: _Obj) -> BoxRow:
    frame = node.str("frame")
    if frame not in ("body", "world"):
        raise ProtocolError(f"{node.at('frame')}: expected body or world")
    center = node.nums("center", 3)
    size = node.nums("size_lwh", 3)
    quaternion = node.nums("quaternion", 4)
    return BoxRow(
        agent_id=node.str("agent_id"),
        source=node.str("source"),
        category=node.str("category"),
        score=node.num("score"),
        track_id=node.opt_str("track_id"),
        center=(center[0], center[1], center[2]),
        size_lwh=(size[0], size[1], size[2]),
        quaternion=(quaternion[0], quaternion[1], quaternion[2], quaternion[3]),
        frame=frame,
    )


def _result(node: _Obj) -> Result:
    return Result(
        id=node.int("id"),
        boxes=tuple(_box(box) for box in node.objs("boxes")),
        artifacts=node.strs("artifacts", optional=True),
    )


def _progress(node: _Obj) -> Progress:
    return Progress(id=node.opt_int("id"), fraction=node.opt_num("fraction"), text=node.str("text"))


def _error(node: _Obj) -> Error:
    code = node.str("code")
    if code not in ERROR_CODES:
        raise ProtocolError(f"{node.at('code')}: unknown error code {code!r}")
    return Error(id=node.opt_int("id"), code=code, message=node.str("message"))


def _cancel(node: _Obj) -> Cancel:
    return Cancel(id=node.opt_int("id"))


def _bye(node: _Obj) -> Bye:
    return Bye()


_DECODERS = {
    "hello": _hello,
    "configure": _configure,
    "configured": _configured,
    "frame": _frame,
    "result": _result,
    "progress": _progress,
    "error": _error,
    "cancel": _cancel,
    "bye": _bye,
}


# --- conversion -----------------------------------------------------------------------------


def to_prediction_box(row: BoxRow, *, scene_id: str, time_us: int) -> PredictionBox:
    """A `result` box as a format 1 row; :class:`PredictionError` names what is wrong."""
    qw, qx, qy, qz = row.quaternion
    try:
        rotation = SE3(qw, qx, qy, qz, 0.0, 0.0, 0.0)
    except ValueError as exc:
        raise PredictionError(f"box_from_rpy must be a unit quaternion: {exc}") from None
    return PredictionBox(
        scene_id=scene_id,
        agent_id=row.agent_id,
        source=row.source,
        time_us=time_us,
        category=row.category,
        score=row.score,
        track_id=row.track_id,
        center=row.center,
        size_lwh=row.size_lwh,
        box_from_rpy=rotation,
        frame=BoxFrame(row.frame),
    )


# --- line reader ----------------------------------------------------------------------------


class LineReader:
    """Reads ``\\n``-terminated lines from a binary stream, refusing lines over MAX_LINE."""

    def __init__(self, stream: IO[bytes]) -> None:
        self._stream = stream

    def readline(self) -> bytes | None:
        line = self._stream.readline(MAX_LINE + 1)
        if line == b"":
            return None
        if len(line) > MAX_LINE:
            raise ProtocolError(f"line exceeds {MAX_LINE} bytes (16 MiB)")
        return line


def rows_to_boxes(rows: Sequence[BoxRow], *, scene_id: str, time_us: int) -> list[PredictionBox]:
    return [to_prediction_box(row, scene_id=scene_id, time_us=time_us) for row in rows]


__all__ = [
    "ERROR_CODES",
    "MAX_LINE",
    "PROTOCOL",
    "AgentRef",
    "BoxRow",
    "Bye",
    "Cancel",
    "Capabilities",
    "Configure",
    "Configured",
    "Error",
    "Frame",
    "Hello",
    "ImageInput",
    "LineReader",
    "Message",
    "PointsInput",
    "Progress",
    "ProtocolError",
    "Result",
    "decode",
    "encode",
    "rows_to_boxes",
    "to_prediction_box",
]
