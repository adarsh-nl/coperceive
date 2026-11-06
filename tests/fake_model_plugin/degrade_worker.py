# SPDX-License-Identifier: Apache-2.0
"""The robustness-sweep fixture worker: ground truth in the pose the frame received.

For every requested agent it reads the scene's own annotations at the frame time and
reports each box in the body frame of the pose that frame carried. With no
perturbation that body box is the exact ground truth once the parent places it with
the scene pose, so mAP is 1. A delayed, dropped or moved (pose-noise) message moves
the reported box by exactly the pose error, so the score falls deterministically as
``pose_noise_xy`` grows. World-frame annotations are converted; a body-frame
annotation is passed through, so the degradation is only meaningful for world-frame
scenes (the demo scene is one).

This is a test fixture, like ``known-answer``: reading the ground truth is what makes
the degradation exact, and no plugin outside the tests does it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

PROTOCOL = 1
PLUGIN = "fake"
VERSION = "0.0.1"
MODEL = "degrade"

Quaternion = tuple[float, float, float, float]
Point = tuple[float, float, float]


def _emit(payload: dict[str, Any]) -> None:
    payload.setdefault("protocol", PROTOCOL)
    sys.stdout.write(json.dumps(payload, allow_nan=False) + "\n")
    sys.stdout.flush()


def _own_md5() -> str:
    return hashlib.md5(Path(__file__).read_bytes()).hexdigest()  # noqa: S324 - a fingerprint


def _hello() -> None:
    _emit(
        {
            "type": "hello",
            "plugin": PLUGIN,
            "version": VERSION,
            "models": [MODEL],
            "capabilities": {"live": True, "cancel": True, "max_inflight": 1},
            "environment": {
                "python": sys.version.split()[0],
                "checkpoint_md5": _own_md5(),
                "checkpoint_path": str(Path(__file__).resolve()),
            },
            "git_commit": None,
        }
    )


def _qmul(a: Quaternion, b: Quaternion) -> Quaternion:
    aw, ax, ay, az = a
    bw, bx, by, bz = b
    return (
        aw * bw - ax * bx - ay * by - az * bz,
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
    )


def _conj(q: Quaternion) -> Quaternion:
    return (q[0], -q[1], -q[2], -q[3])


def _rotate(q: Quaternion, point: Point) -> Point:
    """Rotate ``point`` by the unit quaternion ``q`` (``q * p * q^-1``)."""
    w, x, y, z = q
    px, py, pz = point
    tx = 2.0 * (y * pz - z * py)
    ty = 2.0 * (z * px - x * pz)
    tz = 2.0 * (x * py - y * px)
    return (
        px + w * tx + (y * tz - z * ty),
        py + w * ty + (z * tx - x * tz),
        pz + w * tz + (x * ty - y * tx),
    )


def _pose(pose: Mapping[str, object]) -> tuple[Quaternion, Point]:
    quaternion: Quaternion = (
        _number(pose["qw"]),
        _number(pose["qx"]),
        _number(pose["qy"]),
        _number(pose["qz"]),
    )
    translation: Point = (_number(pose["tx"]), _number(pose["ty"]), _number(pose["tz"]))
    return quaternion, translation


def _number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"expected a number, got {value!r}")
    return float(value)


class Worker:
    def __init__(self, model: str) -> None:
        if model != MODEL:
            raise SystemExit(f"unknown model {model!r}")
        self.agents: list[str] = []
        self.source = "single"
        self.rows: dict[tuple[str, int], list[Mapping[str, object]]] = {}

    def run(self) -> int:
        _hello()
        for line in sys.stdin:
            try:
                message: dict[str, Any] = json.loads(line)
            except json.JSONDecodeError:
                _emit({"type": "error", "id": None, "code": "bad_message", "message": "not JSON"})
                continue
            kind = message.get("type")
            if kind == "configure":
                self._configure(message)
            elif kind == "frame":
                self._frame(message)
            elif kind == "cancel":
                pass
            elif kind == "bye":
                return 0
            else:
                _emit({"type": "error", "id": None, "code": "bad_message", "message": str(kind)})
        return 0

    def _configure(self, message: dict[str, Any]) -> None:
        import pyarrow.parquet as pq

        scene_dir = Path(str(message.get("scene_dir", "")))
        try:
            table = pq.read_table(scene_dir / "annotations.parquet").to_pylist()
        except OSError as error:
            _emit(
                {
                    "type": "error",
                    "id": None,
                    "code": "configure_failed",
                    "message": f"cannot read annotations: {error}",
                }
            )
            return
        self.rows = {}
        for row in table:
            record = cast(Mapping[str, object], row)
            key = (str(record["agent_id"]), int(_number(record["time_us"])))
            self.rows.setdefault(key, []).append(record)
        raw_agents: object = message.get("agents", [])
        self.agents = []
        if isinstance(raw_agents, list):
            for agent in cast(list[object], raw_agents):
                if isinstance(agent, dict):
                    payload = cast(Mapping[str, object], agent)
                    self.agents.append(str(payload.get("agent_id")))
        source = message.get("source")
        self.source = source if isinstance(source, str) else "single"
        _emit({"type": "configured", "scene_id": message.get("scene_id")})

    def _frame(self, message: dict[str, Any]) -> None:
        time_us = int(_number(message.get("time_us", 0)))
        raw_poses: object = message.get("poses", {})
        poses: Mapping[str, object] = (
            cast(Mapping[str, object], raw_poses) if isinstance(raw_poses, dict) else {}
        )
        boxes: list[dict[str, Any]] = []
        for agent_id in self.agents:
            sent = poses.get(agent_id)
            if not isinstance(sent, dict):
                continue
            rotation, translation = _pose(cast(Mapping[str, object], sent))
            body_from_world = _conj(rotation)
            for row in self.rows.get((agent_id, time_us), []):
                category = str(row["category"])
                stored = str(row["frame"])
                length, width, height = (
                    _number(row["length"]),
                    _number(row["width"]),
                    _number(row["height"]),
                )
                quaternion: Quaternion = (
                    _number(row["qw"]),
                    _number(row["qx"]),
                    _number(row["qy"]),
                    _number(row["qz"]),
                )
                center: Point = (
                    _number(row["cx"]) - translation[0],
                    _number(row["cy"]) - translation[1],
                    _number(row["cz"]) - translation[2],
                )
                if stored == "world":
                    center = _rotate(body_from_world, center)
                    quaternion = _qmul(body_from_world, quaternion)
                boxes.append(
                    {
                        "agent_id": agent_id,
                        "source": self.source,
                        "category": category,
                        "score": 0.9,
                        "track_id": row["track_id"],
                        "center": list(center),
                        "size_lwh": [length, width, height],
                        "quaternion": list(quaternion),
                        "frame": "body",
                    }
                )
        _emit({"type": "result", "id": message.get("id"), "boxes": boxes, "artifacts": []})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    args = parser.parse_args(argv)
    try:
        return Worker(str(args.model)).run()
    except BrokenPipeError:
        return 0


if __name__ == "__main__":
    sys.exit(main())
