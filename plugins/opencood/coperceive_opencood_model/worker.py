# SPDX-License-Identifier: Apache-2.0
"""The OpenCOOD plugin's protocol-1 worker (standard library + numpy/torch via the checkpoint).

Started by coperceive as ``python worker.py --model <name> --checkpoint FILE`` from the
plugin directory, in the user's OpenCOOD conda environment. ``hello`` reports the MD5 of the
checkpoint archive (hashed here and again by the parent) and the OpenCOOD/torch versions. The
first ``configure`` extracts the archive into the work directory, reads the checkpoint's own
``config.yaml`` and loads the model through the seam in ``engine``; every later ``frame`` is
built from the protocol's poses and lidar sweeps (``batch``), run through OpenCOOD's own
inference pipeline, and converted back to schema 1 world boxes (``convert``).

The worker imports neither coperceive nor any vendored OpenCOOD code: OpenCOOD is imported
from the conda environment at runtime, behind ``engine``. Ground truth is never read; the
scene directory is never touched. ``--option checkpoint=FILE`` is required and the checkpoint
archive's contents are unverified until Adarsh runs the read-only cluster checks in
``docs/models/opencood.md``.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
import traceback

import opencood_batch as batch
import opencood_checkpoint as checkpoint
import opencood_convert as convert
import opencood_engine as engine

PROTOCOL = 1
PLUGIN = "opencood"
VERSION = "0.1.0"
MODELS = ("cobevt-lidar-v2xset",)
DEFAULT_CATEGORY = "vehicle"  # the V2XSet/OPV2V releases annotate vehicles only


def _emit(payload: dict) -> None:
    payload.setdefault("protocol", PROTOCOL)
    sys.stdout.write(json.dumps(payload, allow_nan=False) + "\n")
    sys.stdout.flush()


def _log(text: str) -> None:
    sys.stderr.write(str(text) + "\n")
    sys.stderr.flush()


def _error(frame_id: int | None, code: str, message: str) -> None:
    _emit({"type": "error", "id": frame_id, "code": code, "message": str(message)[:2000]})


def _progress(text: str, fraction: float | None = None) -> None:
    _emit({"type": "progress", "id": None, "fraction": fraction, "text": text})


def _md5_of(path: str) -> str:
    return checkpoint.md5_of(path)


def _option(message: dict, key: str, default: object = "") -> object:
    options = message.get("options")
    if not isinstance(options, dict):
        return default
    value = options.get(key, default)
    return default if value is None else value


def _score_threshold(message: dict) -> float:
    """``score_threshold`` from configure: a negative value (or absent) means the config's own.

    ``0`` is a real override (drop boxes below score 0, like the checkpoint config can), so
    it must not be treated as falsy and replaced by -1.
    """
    value = _option(message, "score_threshold", -1.0)
    if isinstance(value, bool):
        return -1.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return -1.0


def _as_str_list(value: object) -> list[dict[str, str]]:
    """The ``agents`` list of a configure message as ``{agent_id, agent_type}`` dicts."""
    if not isinstance(value, list):
        return []
    out: list[dict[str, str]] = []
    for item in value:
        if isinstance(item, dict):
            agent_id = item.get("agent_id")
            agent_type = item.get("agent_type")
            if isinstance(agent_id, str) and agent_id:
                out.append({"agent_id": agent_id, "agent_type": str(agent_type or "other")})
    return out


class Worker:
    def __init__(self, model: str, checkpoint_path: str) -> None:
        self.model = model
        self.checkpoint_path = os.path.abspath(checkpoint_path) if checkpoint_path else ""
        self.agents: list[dict[str, str]] = []
        self.sensors: object = []
        self.ego: str | None = None
        self.engine: engine.OpenCOODEngine | None = None
        self.scene_id: str | None = None
        self.source = "fused"
        self._pending_conversion_s = 0.0
        self._pending_inference_s = 0.0
        self._config_rel = ""
        self._weights_rel = ""

    # -- hello ---------------------------------------------------------------------------

    def hello(self) -> None:
        environment = {
            "python": sys.version.split()[0],
            "checkpoint_md5": self._own_digest(),
        }
        environment.update(engine.torch_environment())
        environment.update(engine.opencood_environment())
        if self.checkpoint_path:
            try:
                environment["checkpoint_md5"] = _md5_of(self.checkpoint_path)
                environment["checkpoint_path"] = self.checkpoint_path
            except OSError as exc:
                environment["checkpoint_error"] = str(exc)[:300]
        _emit(
            {
                "type": "hello",
                "plugin": PLUGIN,
                "version": VERSION,
                "models": list(MODELS),
                "capabilities": {"live": False, "cancel": False, "max_inflight": 1},
                "environment": environment,
                "git_commit": None,
            }
        )

    def _own_digest(self) -> str:
        return _md5_of(os.path.abspath(__file__))

    # -- loop ----------------------------------------------------------------------------

    def run(self) -> int:
        self.hello()
        for line in sys.stdin:
            try:
                message = json.loads(line)
            except ValueError:
                _error(None, "bad_message", "not JSON")
                continue
            if not isinstance(message, dict):
                _error(None, "bad_message", "a protocol line is a JSON object")
                continue
            kind = message.get("type")
            if kind == "configure":
                self._configure(message)
            elif kind == "frame":
                self._frame(message)
            elif kind == "cancel":
                continue  # frames are answered synchronously, so nothing is outstanding
            elif kind == "bye":
                return 0
            else:
                _error(None, "bad_message", f"unknown type {kind!r}")
        return 0

    # -- configure -----------------------------------------------------------------------

    def _configure(self, message: dict) -> None:
        if message.get("model") != self.model:
            _error(None, "unsupported", f"this worker serves {self.model}")
            return
        self.agents = _as_str_list(message.get("agents"))
        if not self.agents:
            _error(None, "unsupported", "this scene has no agents to fuse")
            return
        self.ego = batch.ego_agent_id(self.agents)
        if self.ego is None:
            _error(
                None,
                "unsupported",
                "this plugin needs a vehicle ego: the scene has no vehicle agent "
                "(fused output is attributed to the ego vehicle)",
            )
            return
        sensors = message.get("sensors")
        if not any(
            isinstance(sensor, dict) and sensor.get("modality") == "lidar"
            for sensor in (sensors if isinstance(sensors, list) else [])
        ):
            _error(None, "unsupported", "this scene has no lidar sensor")
            return
        self.sensors = sensors
        option_path = str(_option(message, "checkpoint", "") or "")
        if not option_path:
            _error(
                None,
                "unsupported",
                "--option checkpoint=FILE is required: the path of the OpenCOOD checkpoint "
                "archive (.zip). Model check and the first cluster run need it too.",
            )
            return
        if self.checkpoint_path and os.path.abspath(option_path) != self.checkpoint_path:
            _error(
                None,
                "configure_failed",
                f"the worker was started with checkpoint {self.checkpoint_path!r} but "
                f"configure names {os.path.abspath(option_path)!r}; one worker runs one checkpoint",
            )
            return
        self.scene_id = str(message.get("scene_id") or "")
        self.source = str(message.get("source") or "fused")
        try:
            if self.engine is None:
                self._load_engine(message, option_path)
        except (
            checkpoint.CheckpointError,
            engine.EngineError,
            convert.ConvertError,
            OSError,
        ) as exc:
            _error(None, "configure_failed", f"{type(exc).__name__}: {exc}")
            return
        configured: dict = {"type": "configured", "scene_id": message.get("scene_id")}
        timings: dict[str, float] = {}
        if self._pending_conversion_s > 0.0:
            timings["conversion_s"] = round(self._pending_conversion_s, 3)
            self._pending_conversion_s = 0.0
        if self._pending_inference_s > 0.0:
            timings["inference_s"] = round(self._pending_inference_s, 3)
            self._pending_inference_s = 0.0
        if timings:
            configured["timings"] = timings
        _emit(configured)

    def _load_engine(self, message: dict, option_path: str) -> None:
        work_dir = str(message.get("work_dir") or os.environ.get("COPERCEIVE_MODEL_WORK") or ".")
        config_hint = str(_option(message, "checkpoint_config", "") or "")
        weights_hint = str(_option(message, "checkpoint_weights", "") or "")
        _progress("extracting the checkpoint archive", 0.1)
        # Extract under the work directory (the Slurm job's scratch), and remove the
        # extraction as soon as the config is parsed and the weights are in memory: a
        # multi-GB checkpoint must not stay under <out>/.work on a local run.
        destination = tempfile.mkdtemp(prefix="opencood-checkpoint-", dir=work_dir)
        started = time.monotonic()
        try:
            config_path, weights_path, written = checkpoint.prepare(
                option_path,
                destination,
                config_hint=config_hint,
                weights_hint=weights_hint,
            )
            self._config_rel = os.path.relpath(config_path, destination)
            self._weights_rel = os.path.relpath(weights_path, destination)
            _log(
                f"checkpoint {option_path}: {len(written)} files extracted; config "
                f"{self._config_rel}, weights {self._weights_rel}"
            )
            device = str(_option(message, "device", "") or "") or str(message.get("device") or "")
            score_threshold = _score_threshold(message)
            _progress("loading the OpenCOOD model from the checkpoint config", 0.5)
            self.engine = engine.OpenCOODEngine(
                config_path,
                weights_path,
                work_dir=work_dir,
                device=device,
                score_threshold=score_threshold,
                log=_log,
            )
        finally:
            shutil.rmtree(destination, ignore_errors=True)
        self._pending_conversion_s = time.monotonic() - started
        description = self.engine.describe()
        _log("loaded: " + ", ".join(f"{key}={value}" for key, value in description.items()))

    # -- frames --------------------------------------------------------------------------

    def _frame(self, message: dict) -> None:
        frame_id = message.get("id")
        try:
            boxes = self._answer(message)
        except (
            engine.EngineError,
            convert.ConvertError,
            checkpoint.CheckpointError,
            OSError,
            ValueError,
            KeyError,
        ) as exc:
            _error(frame_id, "frame_failed", f"{type(exc).__name__}: {exc}")
            return
        _emit({"type": "result", "id": frame_id, "boxes": boxes})

    def _answer(self, message: dict) -> list[dict]:
        if self.engine is None or self.ego is None:
            raise engine.EngineError("frame before a successful configure")
        poses = message.get("poses")
        if not isinstance(poses, dict):
            poses = {}
        points_by_agent: dict[str, tuple[str, str, int, str]] = {}
        points = message.get("points")
        for item in points if isinstance(points, list) else []:
            if not isinstance(item, dict):
                continue
            agent_id = item.get("agent_id")
            path = item.get("path")
            sensor_id = item.get("sensor_id")
            count = item.get("count")
            layout = item.get("layout")
            if not isinstance(agent_id, str) or not isinstance(path, str):
                continue
            if agent_id in points_by_agent:  # more than one lidar: the first is used
                continue
            points_by_agent[agent_id] = (
                path,
                str(sensor_id or "lidar"),
                int(count) if isinstance(count, int) else 0,
                str(layout or ""),
            )
        base_data = batch.build_base_data(
            agents=self.agents,
            sensors=self.sensors,
            poses=poses,
            points_by_agent=points_by_agent,
            com_range=self.engine.com_range,
            max_cav=self.engine.max_cav,
            preferred_ego=self.ego,
        )
        if base_data is None:
            return []
        time_us = message.get("time_us")
        started = time.monotonic()
        predictions = self.engine.infer(
            base_data,
            scene_id=self.scene_id or "",
            time_us=int(time_us) if isinstance(time_us, int) else 0,
        )
        self._pending_inference_s += time.monotonic() - started
        ego_pose = poses.get(self.ego)
        if ego_pose is None:
            raise engine.EngineError(f"frame has no pose for the ego agent {self.ego!r}")
        source = self.source
        boxes: list[dict] = []
        for prediction in predictions:
            score = prediction.get("score")
            if not isinstance(score, (int, float)):
                continue
            boxes.append(
                convert.box_to_protocol(
                    center=list(prediction["center"]),  # type: ignore[arg-type]
                    size_lwh=list(prediction["size_lwh"]),  # type: ignore[arg-type]
                    heading_deg=float(prediction["heading"]),  # type: ignore[arg-type]
                    ego_pose=ego_pose,
                    agent_id=self.ego,
                    source=source,
                    category=DEFAULT_CATEGORY,
                    score=float(score),
                )
            )
        return boxes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True, choices=sorted(MODELS))
    parser.add_argument(
        "--checkpoint",
        default="",
        help="the checkpoint archive the manifest's checkpoint.option names",
    )
    args = parser.parse_args(argv)
    try:
        return Worker(str(args.model), str(args.checkpoint)).run()
    except BrokenPipeError:
        return 0
    except Exception:  # noqa: BLE001 - a worker that dies still says why on stderr
        _log(traceback.format_exc())
        return 1


if __name__ == "__main__":
    sys.exit(main())
