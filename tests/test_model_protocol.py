# SPDX-License-Identifier: Apache-2.0
"""The newline-delimited JSON protocol between coperceive and a plugin (spec §Protocol)."""

from __future__ import annotations

import io
import json

import pytest
from coperceive.evaluation.predictions import PredictionError
from coperceive.model import BoxFrame
from coperceive.models.protocol import (
    MAX_LINE,
    PROTOCOL,
    AgentRef,
    BoxRow,
    Bye,
    Cancel,
    Capabilities,
    Configure,
    Configured,
    Error,
    Frame,
    Hello,
    ImageInput,
    LineReader,
    Message,
    PointsInput,
    Progress,
    ProtocolError,
    Result,
    decode,
    encode,
    to_prediction_box,
)

POSE = {"qw": 1.0, "qx": 0.0, "qy": 0.0, "qz": 0.0, "tx": 12.5, "ty": -3.0, "tz": 0.2}
BOX = BoxRow(
    agent_id="vehicle",
    source="single",
    category="car",
    score=0.81,
    track_id=None,
    center=(20.1, -4.2, 0.9),
    size_lwh=(4.5, 1.9, 1.6),
    quaternion=(1.0, 0.0, 0.0, 0.0),
    frame="world",
)
MESSAGES: list[Message] = [
    Hello(
        plugin="fake",
        version="0.0.1",
        models=("default",),
        capabilities=Capabilities(live=True, cancel=True, max_inflight=1),
        environment={"python": "3.13.14", "checkpoint_md5": "d41d8cd98f00b204e9800998ecf8427e"},
        git_commit=None,
    ),
    Configure(
        model="default",
        scene_id="coop",
        agents=(AgentRef("vehicle", "vehicle"), AgentRef("uav", "uav")),
        source="single",
        options={"extra": False, "misbehave": ""},
        work_dir="/tmp/work",
        scene_dir="/tmp/scenes/coop",
        raw_root=None,
        artifacts={},
        device="cpu",
        sensors=({"agent_id": "vehicle", "sensor_id": "cam_front", "modality": "camera"},),
        samples=(0, 100_000),
    ),
    Configured(scene_id="coop"),
    Frame(
        id=1,
        scene_id="coop",
        time_us=100_000,
        poses={"vehicle": POSE},
        images=(ImageInput("vehicle", "cam_front", "/tmp/scenes/coop/v/cam_front/0100000.png"),),
        points=(
            PointsInput("vehicle", "lidar_top", "/tmp/work/inputs/1/v.bin", 2929, "xyzi_f32le"),
        ),
    ),
    Result(id=1, boxes=(BOX,), artifacts=("det_pkl",)),
    Progress(id=1, fraction=0.4, text="loading checkpoint"),
    Error(id=1, code="frame_failed", message="CUDA out of memory"),
    Cancel(id=None),
    Bye(),
]


@pytest.mark.parametrize("message", MESSAGES, ids=lambda m: type(m).__name__)
def test_every_message_round_trips(message: Message) -> None:
    assert decode(encode(message)) == message


def test_encode_has_one_trailing_newline() -> None:
    line = encode(Bye())
    assert line.endswith(b"\n") and line.count(b"\n") == 1
    payload = json.loads(line)
    assert payload == {"type": "bye", "protocol": PROTOCOL}


def test_missing_protocol_is_bad_message() -> None:
    with pytest.raises(ProtocolError, match="protocol") as caught:
        decode(b'{"type": "frame"}\n')
    assert caught.value.code == "bad_message"


def test_unknown_type_rejected() -> None:
    with pytest.raises(ProtocolError, match="type"):
        decode(b'{"type": "blob", "protocol": 1}\n')
    with pytest.raises(ProtocolError, match="object"):
        decode(b"[1, 2]\n")
    with pytest.raises(ProtocolError, match="JSON"):
        decode(b"hello world\n")


def test_wrong_field_type_rejected() -> None:
    with pytest.raises(ProtocolError, match="id"):
        decode(b'{"type": "result", "protocol": 1, "id": "one", "boxes": []}\n')
    with pytest.raises(ProtocolError, match="boxes"):
        decode(b'{"type": "result", "protocol": 1, "id": 1, "boxes": [{"agent_id": 3}]}\n')


def test_unknown_error_code_rejected() -> None:
    with pytest.raises(ProtocolError, match="boom"):
        decode(b'{"type": "error", "protocol": 1, "id": null, "code": "boom", "message": "x"}\n')


def test_long_line_rejected() -> None:
    with pytest.raises(ProtocolError, match="16"):
        decode(b"{" + b" " * MAX_LINE + b"}\n")


def test_box_converts() -> None:
    box = to_prediction_box(BOX, scene_id="coop", time_us=100_000)
    assert box.frame is BoxFrame.WORLD
    assert box.size_lwh == (4.5, 1.9, 1.6)
    assert box.scene_id == "coop" and box.time_us == 100_000
    assert box.box_from_rpy.apply((0.0, 0.0, 0.0)) == (0.0, 0.0, 0.0)


def test_box_with_non_unit_quaternion_fails() -> None:
    bad = BoxRow(
        agent_id="vehicle",
        source="single",
        category="car",
        score=0.5,
        track_id=None,
        center=(0.0, 0.0, 0.0),
        size_lwh=(1.0, 1.0, 1.0),
        quaternion=(1.0, 1.0, 0.0, 0.0),
        frame="world",
    )
    with pytest.raises(PredictionError, match="unit quaternion"):
        to_prediction_box(bad, scene_id="coop", time_us=0)


def test_line_reader() -> None:
    reader = LineReader(io.BytesIO(b"a\nb\n"))
    assert reader.readline() == b"a\n"
    assert reader.readline() == b"b\n"
    assert reader.readline() is None
    with pytest.raises(ProtocolError, match="16"):
        LineReader(io.BytesIO(b"x" * (MAX_LINE + 1) + b"\n")).readline()


@pytest.mark.parametrize(
    "digest", [None, "", "none", "NONE", "D41D8CD98F00B204E9800998ECF8427E", "abc"]
)
def test_hello_requires_a_32_hex_checkpoint_md5(digest: str | None) -> None:
    environment: dict[str, str] = {"python": "3.13.14"}
    if digest is not None:
        environment["checkpoint_md5"] = digest
    payload = {
        "type": "hello",
        "protocol": 1,
        "plugin": "fake",
        "version": "0.0.1",
        "models": ["default"],
        "capabilities": {"live": True, "cancel": True, "max_inflight": 1},
        "environment": environment,
    }
    with pytest.raises(ProtocolError, match="environment.checkpoint_md5") as caught:
        decode(json.dumps(payload).encode("utf-8") + b"\n")
    assert caught.value.code == "bad_message"
