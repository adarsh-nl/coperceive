# SPDX-License-Identifier: Apache-2.0
"""Per-scene result cache under the run directory (ADR 0011 decision 7, spec §Cache).

``<predictions root>/<run_id>/cache/<plugin>/<model>/<checkpoint_md5>/<scene_id>/`` holds
``result.json`` (the child's results for every frame of the scene, the manifest digest,
the effective options, the child's environment and the inputs the scene used) and the
artefacts the model declared. A lookup is a hit only when the recorded manifest digest
and options equal the current ones; an entry that fails to decode is deleted.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import cast

from coperceive.adapters.paths import PathError, confine
from coperceive.errors import CoperceiveError
from coperceive.evaluation.predictions import (
    PredictionError,
    check_run_id,
    check_scene_id,
    list_runs,
)
from coperceive.model import Scene
from coperceive.models.inputs import InputFile
from coperceive.models.protocol import ProtocolError, Result, decode, encode, to_prediction_box

CACHE_DIR = "cache"
RESULT_FILE = "result.json"
TimedResult = tuple[int, Result]


class CacheError(CoperceiveError):
    """A cache path is malformed."""


@dataclass(frozen=True)
class CacheKey:
    """The four values Adarsh named (2026-10-18)."""

    plugin: str
    model: str
    checkpoint_md5: str
    scene_id: str


class SceneCache:
    """The cache of one run directory."""

    def __init__(self, run_dir: Path, *, plugin: str, model: str) -> None:
        self.run_dir = run_dir
        self.plugin = plugin
        self.model = model

    def key_for(self, scene: Scene, environment: Mapping[str, str]) -> CacheKey:
        digest = environment.get("checkpoint_md5")
        if not digest:
            raise CacheError("hello.environment.checkpoint_md5 is required for the cache key")
        return CacheKey(self.plugin, self.model, digest, scene.scene_id)

    def path_for(self, key: CacheKey) -> Path:
        check_scene_id(key.scene_id)
        try:
            return confine(
                self.run_dir,
                f"{CACHE_DIR}/{key.plugin}/{key.model}/{key.checkpoint_md5}/{key.scene_id}",
            )
        except PathError as exc:
            raise CacheError(str(exc)) from None

    def artifact_dir(self, key: CacheKey) -> Path | None:
        directory = self.path_for(key) / "artifacts"
        return directory if directory.is_dir() else None

    # -- the batch driver's interface ----------------------------------------------------

    def lookup(
        self,
        scene: Scene,
        *,
        environment: Mapping[str, str],
        manifest_sha256: str,
        options: Mapping[str, object],
    ) -> tuple[TimedResult, ...] | None:
        return self.get(
            self.key_for(scene, environment),
            scene=scene,
            manifest_sha256=manifest_sha256,
            options=options,
        )

    def store(
        self,
        scene: Scene,
        *,
        environment: Mapping[str, str],
        manifest_sha256: str,
        options: Mapping[str, object],
        inputs: Sequence[InputFile],
        results: Sequence[TimedResult],
        artifacts: Path | None,
    ) -> Path:
        return self.put(
            self.key_for(scene, environment),
            manifest_sha256=manifest_sha256,
            options=options,
            environment=environment,
            inputs=inputs,
            results=results,
            artifacts=artifacts,
        )

    # -- entries -------------------------------------------------------------------------

    def get(
        self,
        key: CacheKey,
        *,
        scene: Scene | None,
        manifest_sha256: str,
        options: Mapping[str, object],
    ) -> tuple[TimedResult, ...] | None:
        """The stored results, or None when absent, stale (digest or options differ) or
        corrupt (then the entry is deleted)."""
        directory = self.path_for(key)
        path = directory / RESULT_FILE
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("result.json is not an object")
            entry = cast(dict[str, object], payload)
            if entry.get("manifest_sha256") != manifest_sha256:
                return None
            if entry.get("options") != dict(options):
                return None
            frames = entry.get("frames")
            if not isinstance(frames, list):
                raise ValueError("frames is not a list")
            results: list[TimedResult] = []
            agents = {agent.agent_id for agent in scene.agents} if scene is not None else None
            for item in cast(list[object], frames):
                if not isinstance(item, dict):
                    raise ValueError("frame entry is not an object")
                frame = cast(dict[str, object], item)
                time_us = frame.get("time_us")
                line = frame.get("line")
                if not isinstance(time_us, int) or not isinstance(line, str):
                    raise ValueError("frame entry needs time_us and line")
                message = decode(line.encode("utf-8") + b"\n")
                if not isinstance(message, Result):
                    raise ValueError("frame entry is not a result")
                for box in message.boxes:
                    to_prediction_box(box, scene_id=key.scene_id, time_us=time_us)
                    if agents is not None and box.agent_id not in agents:
                        raise ValueError(f"box for unknown agent {box.agent_id!r}")
                results.append((time_us, message))
            return tuple(results)
        except (ValueError, ProtocolError, PredictionError, OSError):
            shutil.rmtree(directory, ignore_errors=True)
            return None

    def put(
        self,
        key: CacheKey,
        *,
        manifest_sha256: str,
        options: Mapping[str, object],
        environment: Mapping[str, str],
        inputs: Sequence[InputFile],
        results: Sequence[TimedResult],
        artifacts: Path | None,
    ) -> Path:
        directory = self.path_for(key)
        if directory.exists():
            shutil.rmtree(directory)
        directory.mkdir(parents=True)
        payload: dict[str, object] = {
            "manifest_sha256": manifest_sha256,
            "options": dict(options),
            "environment": dict(environment),
            "inputs": [
                {"path": f.relative, "size": f.size, "mtime_ns": f.mtime_ns}
                for f in sorted(inputs, key=lambda f: f.relative)
            ],
            "frames": [
                {"time_us": time_us, "line": encode(result).decode("utf-8").rstrip("\n")}
                for time_us, result in results
            ],
            "written_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        (directory / RESULT_FILE).write_text(
            json.dumps(payload, indent=2, allow_nan=False) + "\n", encoding="utf-8"
        )
        if artifacts is not None and artifacts.is_dir():
            shutil.copytree(artifacts, directory / "artifacts")
        return directory


def prune(root: Path, *, run_id: str | None, plugin: str | None, model: str | None) -> int:
    """Delete the matching cache directories under ``root``; returns how many scenes went."""
    count = 0
    runs = (run_id,) if run_id is not None else list_runs(root)
    for name in runs:
        check_run_id(name)
        cache_root = root / name / CACHE_DIR
        if not cache_root.is_dir():
            continue
        for plugin_dir in sorted(cache_root.iterdir()):
            if plugin is not None and plugin_dir.name != plugin:
                continue
            for model_dir in sorted(p for p in plugin_dir.iterdir() if p.is_dir()):
                if model is not None and model_dir.name != model:
                    continue
                for digest_dir in sorted(p for p in model_dir.iterdir() if p.is_dir()):
                    scenes = [p for p in digest_dir.iterdir() if p.is_dir()]
                    count += len(scenes)
                    shutil.rmtree(digest_dir)
                if not any(model_dir.iterdir()):
                    model_dir.rmdir()
            if not any(plugin_dir.iterdir()):
                plugin_dir.rmdir()
        if not any(cache_root.iterdir()):
            cache_root.rmdir()
    return count


__all__ = ["CACHE_DIR", "RESULT_FILE", "CacheError", "CacheKey", "SceneCache", "prune"]
