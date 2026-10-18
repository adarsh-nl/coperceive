# SPDX-License-Identifier: Apache-2.0
"""The fake plugin's worker: a conforming protocol-1 child with faults to inject.

Standard library only (PyArrow for the ``known-answer`` model, which is the one model
allowed to read a scene's ground truth, and only in tests). Model ``default`` invents one
car five metres ahead of every requested agent; ``misbehave`` (a ``configure`` option) picks
a fault for the runner and conformance tests. Run as ``python worker.py --model NAME``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import select
import sys
import time
from pathlib import Path
from typing import Any, cast

PROTOCOL = 1
PLUGIN = "fake"
VERSION = "0.0.1"
MODELS = ("default", "known-answer")


def _emit(payload: dict[str, Any]) -> None:
    payload.setdefault("protocol", PROTOCOL)
    sys.stdout.write(json.dumps(payload, allow_nan=False) + "\n")
    sys.stdout.flush()


def _log(text: str) -> None:
    sys.stderr.write(text + "\n")
    sys.stderr.flush()


def _own_md5() -> str:
    return hashlib.md5(Path(__file__).read_bytes()).hexdigest()  # noqa: S324 - a fingerprint


def _hello() -> None:
    _emit(
        {
            "type": "hello",
            "plugin": PLUGIN,
            "version": VERSION,
            "models": list(MODELS),
            "capabilities": {"live": True, "cancel": True, "max_inflight": 1},
            "environment": {
                "python": sys.version.split()[0],
                "checkpoint_md5": _own_md5(),
                "checkpoint_path": str(Path(__file__).resolve()),
            },
            "git_commit": None,
        }
    )


def _box(agent_id: str, category: str, center: list[float], score: float) -> dict[str, Any]:
    return {
        "agent_id": agent_id,
        "source": "single",
        "category": category,
        "score": score,
        "track_id": None,
        "center": center,
        "size_lwh": [4.5, 1.9, 1.6] if category == "car" else [0.6, 0.6, 1.8],
        "quaternion": [1.0, 0.0, 0.0, 0.0],
        "frame": "world",
    }


class Worker:
    def __init__(self, model: str, count_file: str | None) -> None:
        self.model = model
        self.count_file = count_file
        self.config: dict[str, Any] = {}
        self.options: dict[str, Any] = {}
        self.no_exit = False

    @property
    def misbehave(self) -> str:
        value = self.options.get("misbehave", "")
        return value if isinstance(value, str) else ""

    def run(self) -> int:
        _hello()
        stdin = sys.stdin
        while True:
            line = stdin.readline()
            if line == "":
                if self.no_exit:
                    while True:
                        time.sleep(1.0)
                return 0
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                _emit({"type": "error", "id": None, "code": "bad_message", "message": "not JSON"})
                continue
            kind = message.get("type")
            if kind == "configure":
                self._configure(message)
            elif kind == "frame":
                self._frame(message)
            elif kind == "cancel":
                pass  # nothing outstanding outside _frame
            elif kind == "bye":
                if self.no_exit:
                    continue
                return 0
            else:
                _emit({"type": "error", "id": None, "code": "bad_message", "message": str(kind)})

    def _configure(self, message: dict[str, Any]) -> None:
        self.config = message
        options = message.get("options", {})
        self.options = options if isinstance(options, dict) else {}
        if message.get("model") not in MODELS:
            _emit(
                {
                    "type": "error",
                    "id": None,
                    "code": "unsupported",
                    "message": f"unknown model {message.get('model')!r}",
                }
            )
            return
        if self.misbehave == "no-exit":
            self.no_exit = True
        if self.misbehave == "read-gt":
            path = Path(str(message.get("scene_dir", ""))) / "annotations.parquet"
            try:
                path.read_bytes()
            except OSError as exc:
                _emit(
                    {
                        "type": "error",
                        "id": None,
                        "code": "configure_failed",
                        "message": f"cannot read ground truth: {exc}",
                    }
                )
                return
        _emit({"type": "configured", "scene_id": message.get("scene_id")})

    def _count(self) -> None:
        if self.count_file:
            with open(self.count_file, "a", encoding="utf-8") as handle:
                handle.write("frame\n")

    def _frame(self, message: dict[str, Any]) -> None:
        frame_id = message.get("id")
        self._count()
        fault = self.misbehave
        if fault == "sleep":
            if self._sleep_cancellable(5.0, frame_id):
                return
        if fault == "secret":
            secret = os.environ.get("FAKE_SECRET", "")
            _log(f"the secret is {secret}")
            _emit(
                {
                    "type": "error",
                    "id": frame_id,
                    "code": "frame_failed",
                    "message": f"failed with token {secret}",
                }
            )
            sys.stdout.write(f"stray line with {secret}\n")
            sys.stdout.flush()
            return
        if fault == "stray-stdout":
            sys.stdout.write("hello world\n")
            sys.stdout.flush()
        if fault == "write-outside":
            outside = Path(str(self.config.get("work_dir", "."))).parent / "escaped.txt"
            outside.write_text("oops", encoding="utf-8")
        boxes = self._boxes(message)
        artifacts: list[str] = []
        if fault == "marker":
            work = Path(str(self.config.get("work_dir", ".")))
            marker = work / "artifacts" / self.model / "marker"
            marker.mkdir(parents=True, exist_ok=True)
            (marker / "done.txt").write_text("done\n", encoding="utf-8")
            artifacts.append("marker")
        answer_id = frame_id + 1 if fault == "wrong-id" and isinstance(frame_id, int) else frame_id
        _emit({"type": "result", "id": answer_id, "boxes": boxes, "artifacts": artifacts})

    def _sleep_cancellable(self, seconds: float, frame_id: Any) -> bool:
        """Sleep, but answer a cancel that arrives meanwhile. True when cancelled."""
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            ready, _, _ = select.select([sys.stdin], [], [], 0.05)
            if ready:
                line = sys.stdin.readline()
                if line == "":
                    return True
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if message.get("type") == "cancel" and message.get("id") in (None, frame_id):
                    _emit(
                        {
                            "type": "error",
                            "id": frame_id,
                            "code": "cancelled",
                            "message": "cancelled by the parent",
                        }
                    )
                    return True
                if message.get("type") == "bye":
                    sys.exit(0)
        return False

    def _boxes(self, message: dict[str, Any]) -> list[dict[str, Any]]:
        if self.model == "known-answer":
            return self._known_answer(message)
        agents = [str(a.get("agent_id")) for a in self.config.get("agents", [])]
        poses = message.get("poses", {})
        score = (time.time() % 1.0) if self.misbehave == "nondeterministic" else 0.9
        boxes: list[dict[str, Any]] = []
        for agent_id in agents:
            pose = poses.get(agent_id)
            if not isinstance(pose, dict):
                continue
            values = cast(dict[str, Any], pose)
            tx, ty, tz = float(values["tx"]), float(values["ty"]), float(values["tz"])
            # bad-agent: the box is placed at a real pose but attributed to an agent the
            # frame did not request (and that the scene does not have).
            owner = "ghost" if self.misbehave == "bad-agent" else agent_id
            boxes.append(_box(owner, "car", [tx + 5.0, ty, tz], score))
            if self.options.get("extra") is True:
                boxes.append(_box(agent_id, "pedestrian", [tx + 8.0, ty + 2.0, tz], score))
        return boxes

    def _known_answer(self, message: dict[str, Any]) -> list[dict[str, Any]]:
        """Each requested agent's ground-truth boxes at this time, shifted by ``offset_m`` along x.

        Boxes are stored in the agent's body frame or the world frame; both are reported in
        the frame they were stored in, so the parent places them exactly as the ground truth.
        """
        import pyarrow.parquet as pq

        scene_dir = Path(str(self.config.get("scene_dir", "")))
        time_us = int(message.get("time_us", 0))
        offset = float(self.options.get("offset_m", 0.0))
        agents = {str(a.get("agent_id")) for a in self.config.get("agents", [])}
        table = pq.read_table(scene_dir / "annotations.parquet").to_pylist()
        boxes: list[dict[str, Any]] = []
        for row in table:
            if row["agent_id"] not in agents or int(row["time_us"]) != time_us:
                continue
            boxes.append(
                {
                    "agent_id": row["agent_id"],
                    "source": "single",
                    "category": row["category"],
                    "score": 0.9,
                    "track_id": row["track_id"],
                    "center": [float(row["cx"]) + offset, float(row["cy"]), float(row["cz"])],
                    "size_lwh": [float(row["length"]), float(row["width"]), float(row["height"])],
                    "quaternion": [
                        float(row["qw"]),
                        float(row["qx"]),
                        float(row["qy"]),
                        float(row["qz"]),
                    ],
                    "frame": row["frame"],
                }
            )
        return boxes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    parser.add_argument("--count-file", default=None)
    args = parser.parse_args(argv)
    try:
        return Worker(str(args.model), args.count_file).run()
    except BrokenPipeError:
        return 0


if __name__ == "__main__":
    sys.exit(main())
