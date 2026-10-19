# SPDX-License-Identifier: Apache-2.0
"""The Griffin plugin's protocol-1 worker (Python 3.8, standard library only).

Started by coperceive as ``python worker.py --model <name>`` from the plugin directory, in
the Griffin conda environment. ``hello`` reports the checkpoint it will load (path and MD5,
hashed here and again by the parent). On the first ``configure`` of a run it copies the
toolkit into the work directory, trims ``scene_infos.json`` to the staged scenes, renumbers
the split, converts the release to nuScenes tables, builds the info pickles and runs
``tools/test.py`` once over every staged scene (the toolkit is a whole-dataset evaluator and
the Agile track head is stateful within a scene), then answers every ``frame`` from the
detection JSON. With the option ``det_json`` it answers from an existing JSON and needs no
GPU, which is what ``coperceive model check`` and the tests use. Ground truth is never read.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any

from convert import (
    DEFAULT_CATEGORIES,
    ConvertError,
    Index,
    index_detections,
    md5_of,
    read_samples,
)
from stage import (
    StageError,
    check_against_split,
    check_blocks,
    link_release,
    load_scene_infos,
    present_scenes,
    write_trimmed,
)
from toolkit import (
    ToolkitError,
    collect_artifacts,
    convert_release,
    copy_toolkit,
    evaluate,
    place_checkpoint,
    place_track_query,
    toolkit_commit,
)

PROTOCOL = 1
PLUGIN = "griffin"
VERSION = "0.1.0"
DEFAULT_CKPT_ROOT = "/datasets/eemcs/ps/cv/huggingface/griffin/ckpts"
DEFAULT_TOOLKIT = os.path.expanduser("~/src/griffin")
SUBSET = "25m"

MODELS: dict[str, dict[str, Any]] = {
    "vehicle-side": {
        "config": "projects/configs_griffin_50scenes_{subset}/vehicle-side/"
        "tiny_track_r50_stream_bs8_48epoch_3cls.py",
        "checkpoint": "vehicle-side/iter_33024.pth",
        "md5": "1201f5692e390a75e5b5ab0efa0cae19",
        "agent": "vehicle",
        "side": "vehicle-side",
        "nusc_side": "vehicle-side",
        "needs": {},
        "early_fusion": False,
    },
    "drone-side": {
        "config": "projects/configs_griffin_50scenes_{subset}/drone-side/"
        "tiny_track_r50_stream_bs8_24epoch_3cls_eval.py",
        "checkpoint": "drone-side/iter_33024.pth",
        "md5": "41734b8d764d1213935a231a3419f655",
        "agent": "drone",
        "side": "drone-side",
        "nusc_side": "drone-side",
        "needs": {},
        "early_fusion": False,
    },
    "instance-fusion": {
        "config": "projects/configs_griffin_50scenes_{subset}/cooperative/instance_fusion/"
        "tiny_track_r50_stream_bs8_48epoch_3cls.py",
        "checkpoint": "cooperative/instance_fusion/iter_33024.pth",
        "md5": "7e1448188b6e99ca6303575c3466b97f",
        "agent": "vehicle",
        "side": "cooperative",
        "nusc_side": "cooperative",
        # model -> artefact: the drone-side pass's track queries, by path from configure.
        "needs": {"drone-side": "track_query"},
        "early_fusion": False,
    },
}


def _emit(payload: dict[str, Any]) -> None:
    payload.setdefault("protocol", PROTOCOL)
    sys.stdout.write(json.dumps(payload, allow_nan=False) + "\n")
    sys.stdout.flush()


def _log(text: str) -> None:
    sys.stderr.write(text + "\n")
    sys.stderr.flush()


def _error(frame_id: int | None, code: str, message: str) -> None:
    _emit({"type": "error", "id": frame_id, "code": code, "message": message[:2000]})


def _progress(text: str, fraction: float | None = None) -> None:
    _emit({"type": "progress", "id": None, "fraction": fraction, "text": text})


def _torch_version() -> str:
    try:
        import torch  # type: ignore[import-not-found]  # noqa: PLC0415 - the env may lack it

        return str(torch.__version__)
    except Exception:  # noqa: BLE001 - absent or broken torch is a fact to report
        return "absent"


class Worker:
    def __init__(self, model: str, ckpt_root: str, toolkit_dir: str) -> None:
        self.model = model
        self.spec = MODELS[model]
        self.ckpt_root = ckpt_root
        self.toolkit_dir = toolkit_dir
        self.checkpoint = os.path.join(
            ckpt_root, f"griffin_50scenes_{SUBSET}", str(self.spec["checkpoint"])
        )
        self.index: Index | None = None
        self.artifacts: list[str] = []
        self.artifacts_reported = False
        self.config: dict[str, Any] = {}
        # Reported once, on the configure that ran the toolkit (Adarsh, 2026-10-19).
        self.timings: dict[str, float] = {}
        self.source = "single"

    # -- hello ---------------------------------------------------------------------------

    def hello(self) -> None:
        digest = md5_of(self.checkpoint)
        environment = {
            "python": sys.version.split()[0],
            "torch": _torch_version(),
            "checkpoint_md5": digest,
            "checkpoint_path": os.path.abspath(self.checkpoint),
            "toolkit": self.toolkit_dir,
        }
        commit = toolkit_commit(self.toolkit_dir)
        if commit:
            environment["toolkit_commit"] = commit
        _emit(
            {
                "type": "hello",
                "plugin": PLUGIN,
                "version": VERSION,
                "models": list(MODELS),
                "capabilities": {"live": False, "cancel": False, "max_inflight": 1},
                "environment": environment,
                "git_commit": commit,
            }
        )

    # -- loop ----------------------------------------------------------------------------

    def run(self) -> int:
        self.hello()
        for line in sys.stdin:
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                _error(None, "bad_message", "not JSON")
                continue
            kind = message.get("type")
            if kind == "configure":
                self._configure(message)
            elif kind == "frame":
                self._frame(message)
            elif kind == "cancel":
                continue  # nothing is ever outstanding: frames are answered at once
            elif kind == "bye":
                return 0
            else:
                _error(None, "bad_message", f"unknown type {kind!r}")
        return 0

    # -- configure -----------------------------------------------------------------------

    def _configure(self, message: dict[str, Any]) -> None:
        if message.get("model") != self.model:
            _error(None, "unsupported", f"this worker serves {self.model}")
            return
        agents = [str(a.get("agent_id")) for a in message.get("agents", [])]
        if self.spec["agent"] not in agents:
            _error(
                None,
                "unsupported",
                f"{self.model} needs agent {self.spec['agent']!r} in the request",
            )
            return
        options = message.get("options", {}) if isinstance(message.get("options"), dict) else {}
        if str(options.get("subset", SUBSET)) != SUBSET:
            _error(None, "unsupported", f"only subset {SUBSET} is pinned in this plugin")
            return
        self.config = message
        self.source = str(message.get("source", "single"))
        try:
            if self.index is None:
                min_score = float(options.get("min_score", 0.05))
                det_json = str(options.get("det_json", "") or "")
                if det_json:
                    nusc_root = str(options.get("nusc_root", "") or "")
                    if not nusc_root:
                        raise ConvertError(
                            "option det_json needs option nusc_root (sample.json, scene.json)"
                        )
                    self._index_from(det_json, nusc_root, min_score)
                else:
                    self._run_toolkit(message, min_score)
        except (ConvertError, StageError, ToolkitError, OSError) as exc:
            _error(None, "configure_failed", f"{type(exc).__name__}: {exc}")
            return
        configured: dict[str, Any] = {"type": "configured", "scene_id": message.get("scene_id")}
        if self.timings:
            configured["timings"] = self.timings
            self.timings = {}  # the toolkit ran once; later scenes report nothing
        _emit(configured)

    def _index_from(self, det_json: str, nusc_root: str, min_score: float) -> None:
        samples = read_samples(nusc_root)
        index, dropped = index_detections(
            det_json,
            samples,
            agent_id=str(self.spec["agent"]),
            source=self.source,
            min_score=min_score,
        )
        kept = sum(len(rows) for rows in index.values())
        _log(f"indexed {det_json}: {kept} boxes kept, {dropped} dropped below {min_score}")
        self.index = index

    def _run_toolkit(self, message: dict[str, Any], min_score: float) -> None:
        """The whole pipeline once per worker: every staged scene in one toolkit pass."""
        work_dir = str(message.get("work_dir", ""))
        raw_root = str(message.get("raw_root", "") or "")
        if not work_dir or not raw_root:
            raise ToolkitError("configure needs work_dir and raw_root for a toolkit run")
        prefix = f"griffin_50scenes_{SUBSET}"
        # One toolkit copy per model: no tree is shared with the dependency's pass.
        toolkit = os.path.join(work_dir, f"griffin-{self.model}")
        log_path = os.path.join(work_dir, "griffin-toolkit.log")
        env = dict(os.environ)
        env.setdefault("MASTER_PORT", str(20000 + os.getpid() % 20000))
        run_started = time.monotonic()
        _progress("copying the toolkit", 0.02)
        copy_toolkit(self.toolkit_dir, toolkit)
        for name in ("datasets", "ckpts", os.path.join("data", "infos")):
            os.makedirs(os.path.join(toolkit, name), exist_ok=True)
        for needed_model, needed_name in dict(self.spec["needs"]).items():
            given = message.get("artifacts", {}).get(needed_model, {}).get(needed_name)
            if not given:
                raise ToolkitError(
                    f"{self.model} needs the artefact {needed_model}/{needed_name} in "
                    "configure.artifacts"
                )
            place_track_query(str(given), toolkit, prefix)
        _progress("trimming scene_infos.json to the staged scenes", 0.05)
        infos = load_scene_infos(os.path.join(raw_root, "vehicle-side", "scene_infos.json"))
        other = load_scene_infos(os.path.join(raw_root, "drone-side", "scene_infos.json"))
        if infos != other:
            raise StageError("vehicle-side and drone-side scene_infos.json differ")
        check_blocks(infos)
        picked = present_scenes(infos, raw_root)
        split_source = os.path.join(toolkit, "data", "split_datas", f"{prefix}.json")
        check_against_split(picked, split_source)
        release_dir = os.path.join(toolkit, "datasets", prefix, "griffin-release")
        link_release(raw_root, release_dir)
        names = write_trimmed(release_dir, picked, split_source)
        _log(f"staged scenes ({len(names)}): {', '.join(names)}")
        _progress("checking the checkpoint", 0.08)
        checkpoint = place_checkpoint(
            self.checkpoint, toolkit, prefix, str(self.spec["checkpoint"]), str(self.spec["md5"])
        )
        _progress("converting the release to nuScenes tables", 0.1)
        nusc_root = convert_release(
            toolkit,
            prefix,
            os.path.join("data", "split_datas", f"{prefix}.json"),
            env=env,
            log_path=log_path,
            nusc_side=str(self.spec["nusc_side"]),
            early_fusion=bool(self.spec["early_fusion"]),
        )
        config = str(self.spec["config"]).format(subset=SUBSET)
        conversion_s = time.monotonic() - run_started  # copy, staging, conversion, info pickles
        _progress("running tools/test.py over every staged scene", 0.2)
        started = time.monotonic()
        det_json = evaluate(
            toolkit,
            config,
            os.path.relpath(checkpoint, toolkit),
            env=env,
            log_path=log_path,
            master_port=int(env["MASTER_PORT"]),
        )
        inference_s = time.monotonic() - started
        self.timings = {
            "conversion_s": round(conversion_s, 3),
            "inference_s": round(inference_s, 3),
        }
        _log(f"tools/test.py finished in {inference_s:.0f} s: {det_json}")
        _progress("indexing the detections", 0.95)
        self._index_from(det_json, nusc_root, min_score)
        dest = os.path.join(work_dir, "artifacts", self.model)
        self.artifacts = collect_artifacts(
            toolkit, config, det_json, dest, prefix, str(self.spec["side"])
        )

    # -- frames --------------------------------------------------------------------------

    def _frame(self, message: dict[str, Any]) -> None:
        frame_id = message.get("id")
        if self.index is None:
            _error(frame_id, "frame_failed", "frame before a successful configure")
            return
        scene_id = str(message.get("scene_id"))
        time_us = int(message.get("time_us", -1))
        boxes = [dict(box) for box in self.index.get((scene_id, time_us), [])]
        for box in boxes:
            box["source"] = self.source
            if box["category"] not in DEFAULT_CATEGORIES:
                _log(f"category {box['category']!r} is outside the manifest's list")
        artifacts: list[str] = []
        if self.artifacts and not self.artifacts_reported:
            artifacts = list(self.artifacts)
            self.artifacts_reported = True
        _emit({"type": "result", "id": frame_id, "boxes": boxes, "artifacts": artifacts})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True, choices=sorted(MODELS))
    parser.add_argument("--ckpt-root", default=DEFAULT_CKPT_ROOT, help="released checkpoints root")
    parser.add_argument("--toolkit", default=DEFAULT_TOOLKIT, help="Griffin toolkit checkout")
    args = parser.parse_args(argv)
    try:
        return Worker(str(args.model), str(args.ckpt_root), str(args.toolkit)).run()
    except BrokenPipeError:
        return 0


if __name__ == "__main__":
    sys.exit(main())
