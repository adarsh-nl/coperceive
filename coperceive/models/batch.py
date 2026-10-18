# SPDX-License-Identifier: Apache-2.0
"""Batch mode: drive a plugin over scenes and write a predictions format 1 run (ADR 0011
decision 3, spec §CLI, §Provenance, §Partial runs).

One child per run, started at the first scene that the cache cannot answer, so a run served
entirely from the cache starts none. Scenes go in name order; frames in pose-time order;
every ``result`` row passes ``to_prediction_box`` and ``validate_boxes`` before anything is
written; a scene is written with ``write_run`` when complete. A protocol violation, a child
exit or the run timeout ends the run as ``partial``; a per-scene error (a frame the child
could not serve, a frame timeout) fails that scene and the run goes on, also ``partial``.
"""

from __future__ import annotations

import os
import socket
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol, cast

from coperceive.adapters.ingest import coperceive_version
from coperceive.errors import CoperceiveError
from coperceive.evaluation.predictions import (
    BOXES_FILE,
    PredictionBox,
    PredictionError,
    RunMeta,
    check_run_id,
    read_run_meta,
    validate_boxes,
    write_run,
)
from coperceive.model import Scene
from coperceive.models.cache import CacheError, CheckpointInfo, SceneCache, checkpoint_info
from coperceive.models.inputs import InputFile, build_frame, remove_inputs, sensor_json
from coperceive.models.protocol import (
    AgentRef,
    Configure,
    Configured,
    Error,
    Message,
    Progress,
    Result,
    to_prediction_box,
)
from coperceive.models.runner import PluginProcess, RunnerError, RunnerTimeout
from coperceive.models.spec import (
    Manifest,
    ModelSpec,
    ModelSpecError,
    check_options,
    manifest_sha256,
    select_model,
)
from coperceive.store import read_scene
from coperceive.viewdata import pose_times, scene_directories

EXIT_OK = 0
EXIT_USER = 1
EXIT_PROTOCOL = 3
EXIT_TIMEOUT = 4

TimedResult = tuple[int, Result]  # (time_us, the child's result for that frame)


class BatchError(CoperceiveError):
    """A user error of ``model run`` (exit 1)."""


@dataclass(frozen=True)
class RunOptions:
    model: str | None = None
    agent_ids: tuple[str, ...] | None = None
    source: str | None = None
    scene_ids: tuple[str, ...] | None = None
    raw_root: Path | None = None
    media_roots: tuple[Path, ...] = ()
    artifacts_dir: Path | None = None
    auto_dependencies: bool = True
    options: Mapping[str, str] = field(default_factory=lambda: {})
    frame_timeout_s: float | None = None
    run_timeout_s: float | None = None
    cache: bool = True
    hash_inputs: bool = False
    work_dir: Path | None = None
    force: bool = False


@dataclass(frozen=True)
class SceneOutcome:
    scene_id: str
    frames: int
    boxes: int
    cached: bool
    error: str | None = None


@dataclass(frozen=True)
class BatchReport:
    completed: tuple[str, ...]
    skipped: tuple[str, ...]
    failed: tuple[str, ...]
    frames_run: int
    cache_hits: int
    boxes: int
    status: str  # complete | partial
    reason: str | None
    scenes: tuple[SceneOutcome, ...] = ()

    @property
    def exit_code(self) -> int:
        """§Exit codes: 0 complete, 4 timeout or cancel, 3 anything else partial."""
        if self.status == "complete":
            return EXIT_OK
        if self.reason is not None and self.reason.startswith(("timeout", "cancelled")):
            return EXIT_TIMEOUT
        return EXIT_PROTOCOL


class SceneCacheLike(Protocol):
    """What batch mode asks of a cache (``coperceive.models.cache.SceneCache``)."""

    def lookup(
        self,
        scene: Scene,
        *,
        environment: Mapping[str, str],
        manifest_sha256: str,
        options: Mapping[str, object],
    ) -> tuple[TimedResult, ...] | None: ...

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
    ) -> Path: ...


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def run_meta_for(
    manifest: Manifest,
    spec: ModelSpec,
    *,
    run_id: str,
    created_utc: str,
    provenance: Mapping[str, object],
) -> RunMeta:
    """§Provenance's ``model`` and ``notes`` rules on top of the model's ``outputs``."""
    notes = [manifest.licence.notice]
    if manifest.licence.citation:
        notes.append(manifest.licence.citation)
    if manifest.notes:
        notes.append(manifest.notes)
    return RunMeta(
        run_id=run_id,
        model=f"{manifest.name} {spec.key} {manifest.version}",
        score_note=spec.outputs.score_note,
        categories=spec.outputs.categories,
        sources=spec.outputs.sources,
        created_utc=created_utc,
        notes=" | ".join(notes),
        provenance=dict(provenance),
    )


def provenance_json(
    manifest: Manifest,
    spec: ModelSpec,
    environment: Mapping[str, str],
    git_commit: str | None,
    *,
    started_utc: str,
    ended_utc: str,
    status: str,
    completed: Sequence[str],
    reason: str | None,
    options: Mapping[str, object],
    cache_hits: int,
    frames_run: int,
    dependencies: Mapping[str, Mapping[str, str]],
    timings: Mapping[str, float],
    parent_env: Mapping[str, str],
    checkpoint: CheckpointInfo | None = None,
) -> dict[str, object]:
    """Every key of spec §Provenance."""
    return {
        "checkpoint": checkpoint.as_json() if checkpoint is not None else None,
        "plugin": manifest.name,
        "plugin_version": manifest.version,
        "model": spec.key,
        "manifest_sha256": manifest_sha256(manifest),
        "environment": dict(environment),
        "git_commit": git_commit,
        "coperceive_version": coperceive_version(),
        "host": socket.gethostname(),
        "started_utc": started_utc,
        "ended_utc": ended_utc,
        "status": status,
        "completed_scenes": list(completed),
        "reason": reason,
        "options": dict(options),
        "cache_hits": cache_hits,
        "frames_run": frames_run,
        "timings": dict(timings),
        "dependencies": {k: dict(v) for k, v in dependencies.items()},
        "slurm_job_id": parent_env.get("SLURM_JOB_ID"),
    }


def select_agents(
    scene: Scene, spec: ModelSpec, requested: Sequence[str] | None
) -> tuple[str, ...]:
    """The requested agents, or every agent whose type the model runs on, in scene order."""
    if requested is not None:
        known = {agent.agent_id for agent in scene.agents}
        missing = [a for a in requested if a not in known]
        if missing:
            raise BatchError(f"scene {scene.scene_id} has no agent {missing[0]!r}")
        wanted = set(requested)
        return tuple(a.agent_id for a in scene.agents if a.agent_id in wanted)
    kinds = spec.inputs.agents
    return tuple(a.agent_id for a in scene.agents if kinds is None or a.agent_type.value in kinds)


def _source(spec: ModelSpec, requested: str | None) -> str:
    names = [name for name, _ in spec.outputs.sources]
    if requested is None:
        return names[0]
    if requested not in names:
        raise BatchError(f"source {requested!r} is not one of {', '.join(names)}")
    return requested


class _Stop(Exception):
    """Ends the run: the child is gone, misbehaved, or the run timed out."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class _SceneFailed(Exception):
    """Fails this scene; the run goes on with the next one."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class _SceneRun:
    """What one scene produced before it was written."""

    def __init__(self) -> None:
        self.rows: list[PredictionBox] = []
        self.results: list[TimedResult] = []
        self.inputs: dict[str, InputFile] = {}
        self.frames = 0


def run_batch(
    manifest: Manifest,
    scenes_root: Path,
    out_root: Path,
    run_id: str,
    options: RunOptions,
    *,
    created_utc: str,
    cache: SceneCacheLike | None = None,
    parent_env: Mapping[str, str] | None = None,
) -> BatchReport:
    """Run ``manifest``'s model over the scenes and write ``<out_root>/<run_id>``."""
    env: dict[str, str] = dict(os.environ if parent_env is None else parent_env)
    check_run_id(run_id)
    spec = select_model(manifest, options.model)
    effective = check_options(spec, options.options)
    source = _source(spec, options.source)
    if manifest.environment.kind == "container":
        raise BatchError("environment.container is not implemented in this version")
    if spec.inputs.raw and options.raw_root is None:
        raise BatchError(f"model {spec.key} needs --raw-root for its raw inputs")
    if manifest.source is None:
        raise BatchError("the manifest must come from a file")
    digest = manifest_sha256(manifest)
    plugin_dir = manifest.source.parent
    work_dir = options.work_dir or (out_root / ".work" / run_id)
    frame_timeout = options.frame_timeout_s or spec.resources.frame_timeout_s
    run_timeout = options.run_timeout_s or spec.resources.run_timeout_s
    deadline = time.monotonic() + run_timeout if run_timeout else None
    stored = _stored_provenance(out_root, run_id, digest, force=options.force)
    if cache is None and options.cache:
        cache = SceneCache(out_root / run_id, plugin=manifest.name, model=spec.key)
    directories = {path.name: path for path in scene_directories(scenes_root)}
    if options.scene_ids is None:
        selected = list(directories)
    else:
        selected = list(dict.fromkeys(options.scene_ids))
    for scene_id in selected:
        if scene_id not in directories:
            raise BatchError(f"no scene directory {scene_id!r} under {scenes_root}")

    started = time.monotonic()
    completed: list[str] = []
    skipped: list[str] = []
    failed: list[str] = []
    outcomes: list[SceneOutcome] = []
    counters = {"frames": 0, "hits": 0, "boxes": 0}
    status = "complete"
    reason: str | None = None
    child: PluginProcess | None = None
    # The environment of the run that filled this run directory lets the cache answer a
    # scene before any child exists, but its checkpoint digest is recomputed from the file
    # on disk (re-hashed only when size or mtime changed), never trusted from run.json.
    checkpoint: CheckpointInfo | None = None
    environment: Mapping[str, str] = {}
    if cache is not None and options.cache:
        checkpoint, environment = _verified_environment(stored)
    git_commit: str | None = None
    next_frame_id = 1
    device = "cuda:0" if env.get("CUDA_VISIBLE_DEVICES") else "cpu"

    def meta_now(final_status: str, final_reason: str | None) -> RunMeta:
        wall = time.monotonic() - started
        frames = counters["frames"]
        provenance = provenance_json(
            manifest,
            spec,
            environment,
            git_commit,
            started_utc=created_utc,
            ended_utc=utc_now(),
            status=final_status,
            completed=completed,
            reason=final_reason,
            options=effective,
            cache_hits=counters["hits"],
            frames_run=frames,
            dependencies={},
            checkpoint=checkpoint,
            timings={
                "staging_s": 0.0,
                "per_sample_s": (wall / frames) if frames else 0.0,
                "wall_s": wall,
            },
            parent_env=env,
        )
        return run_meta_for(
            manifest, spec, run_id=run_id, created_utc=created_utc, provenance=provenance
        )

    def lookup(scene: Scene) -> tuple[TimedResult, ...] | None:
        if cache is None or not options.cache or "checkpoint_md5" not in environment:
            return None
        return cache.lookup(
            scene, environment=environment, manifest_sha256=digest, options=effective
        )

    try:
        for scene_id in selected:
            target = out_root / run_id / scene_id / BOXES_FILE
            if target.is_file() and not options.force:
                skipped.append(scene_id)
                outcomes.append(SceneOutcome(scene_id, 0, 0, False))
                continue
            scene = read_scene(directories[scene_id])
            agent_ids = select_agents(scene, spec, options.agent_ids)
            hit = lookup(scene)
            if hit is None and child is None:
                child = PluginProcess.start(
                    manifest,
                    spec,
                    plugin_dir=plugin_dir,
                    work_dir=work_dir,
                    parent_env=env,
                    hello_timeout_s=frame_timeout,
                )
                environment = child.hello.environment
                git_commit = child.hello.git_commit
                checkpoint = _check_hello_checkpoint(child, checkpoint)
                hit = lookup(scene)
            meta = meta_now("complete", None)
            if hit is not None:
                rows = _rows_of(scene, hit)
                validate_boxes(scene, meta, rows)
                write_run(out_root, run_id, meta, rows, force=options.force)
                completed.append(scene_id)
                counters["hits"] += 1
                counters["boxes"] += len(rows)
                outcomes.append(SceneOutcome(scene_id, 0, len(rows), True))
                continue
            assert child is not None
            run = _SceneRun()
            try:
                _run_scene(
                    child,
                    scene,
                    directories[scene_id],
                    spec,
                    agent_ids=agent_ids,
                    source=source,
                    effective=effective,
                    work_dir=work_dir,
                    options=options,
                    frame_timeout=frame_timeout,
                    deadline=deadline,
                    device=device,
                    first_id=next_frame_id,
                    out=run,
                )
            except _SceneFailed as exc:
                counters["frames"] += run.frames
                next_frame_id += run.frames
                failed.append(scene_id)
                outcomes.append(SceneOutcome(scene_id, run.frames, 0, False, exc.reason))
                status = "partial"
                if reason is None:
                    prefix = "" if exc.reason.startswith("timeout") else "plugin_error: "
                    reason = prefix + exc.reason
                continue
            counters["frames"] += run.frames
            next_frame_id += run.frames
            validate_boxes(scene, meta, run.rows)
            write_run(out_root, run_id, meta, run.rows, force=options.force)
            if cache is not None and options.cache:
                cache.store(
                    scene,
                    environment=environment,
                    manifest_sha256=digest,
                    options=effective,
                    inputs=tuple(run.inputs.values()),
                    results=tuple(run.results),
                    artifacts=_artifact_dir(work_dir, spec),
                )
            completed.append(scene_id)
            counters["boxes"] += len(run.rows)
            outcomes.append(SceneOutcome(scene_id, run.frames, len(run.rows), False))
    except _Stop as exc:
        status, reason = "partial", exc.reason
        if selected and len(outcomes) < len(selected):
            failed.append(selected[len(outcomes)])
            outcomes.append(SceneOutcome(selected[len(outcomes)], 0, 0, False, exc.reason))
    except (RunnerError, PredictionError, ModelSpecError) as exc:
        status, reason = "partial", f"plugin_error: {exc}"
        if selected and len(outcomes) < len(selected):
            failed.append(selected[len(outcomes)])
            outcomes.append(SceneOutcome(selected[len(outcomes)], 0, 0, False, str(exc)))
    finally:
        if child is not None:
            child.close(grace_s=spec.resources.grace_s)
    write_run(out_root, run_id, meta_now(status, reason), [], force=True)
    return BatchReport(
        completed=tuple(completed),
        skipped=tuple(skipped),
        failed=tuple(failed),
        frames_run=counters["frames"],
        cache_hits=counters["hits"],
        boxes=counters["boxes"],
        status=status,
        reason=reason,
        scenes=tuple(outcomes),
    )


def _artifact_dir(work_dir: Path, spec: ModelSpec) -> Path | None:
    directory = work_dir / "artifacts" / spec.key
    return directory if directory.is_dir() else None


def _stored_provenance(
    out_root: Path, run_id: str, digest: str, *, force: bool
) -> Mapping[str, object] | None:
    """The provenance of an existing run directory; refuses another manifest without force."""
    if not (out_root / run_id / "run.json").is_file():
        return None
    meta = read_run_meta(out_root, run_id)
    provenance = meta.provenance
    if provenance is None:
        return None
    stored = provenance.get("manifest_sha256")
    if stored is not None and stored != digest and not force:
        raise ModelSpecError(
            f"run {run_id!r} was written with manifest_sha256 {stored}, the current manifest "
            f"is {digest}; use --force to mix them"
        )
    return provenance


def _stored_environment(provenance: Mapping[str, object] | None) -> dict[str, str]:
    if provenance is None:
        return {}
    raw = provenance.get("environment")
    if not isinstance(raw, Mapping):
        return {}
    out: dict[str, str] = {}
    for key, value in raw.items():  # pyright: ignore[reportUnknownVariableType]
        if isinstance(key, str) and isinstance(value, str):
            out[key] = value
    return out


def _verified_environment(
    provenance: Mapping[str, object] | None,
) -> tuple[CheckpointInfo | None, Mapping[str, str]]:
    """The previous run's environment with ``checkpoint_md5`` recomputed from the file on
    disk; empty (so the child is started) when there is no usable checkpoint record."""
    environment = _stored_environment(provenance)
    previous = provenance.get("checkpoint") if provenance is not None else None
    if not isinstance(previous, Mapping):
        return None, {}
    record = cast(Mapping[str, object], previous)
    path = record.get("path")
    if not isinstance(path, str) or not path:
        return None, {}
    try:
        info = checkpoint_info(Path(path), record)
    except CacheError:
        return None, {}
    environment["checkpoint_md5"] = info.md5
    environment["checkpoint_path"] = info.path
    return info, environment


def _check_hello_checkpoint(
    child: PluginProcess, previous: CheckpointInfo | None
) -> CheckpointInfo | None:
    """The parent hashes the file the child names and refuses a digest that disagrees."""
    path = child.hello.environment.get("checkpoint_path")
    if not path:
        return None
    try:
        info = checkpoint_info(Path(path), previous.as_json() if previous else None)
    except CacheError as exc:
        child.close(grace_s=1.0)
        raise RunnerError(f"protocol violation: {exc}") from None
    reported = child.hello.environment.get("checkpoint_md5")
    if info.md5 != reported:
        child.close(grace_s=1.0)
        raise RunnerError(
            f"protocol violation: hello reports checkpoint_md5 {reported} but {path} hashes "
            f"to {info.md5}"
        )
    return info


def _rows_of(scene: Scene, results: Sequence[TimedResult]) -> list[PredictionBox]:
    rows: list[PredictionBox] = []
    for time_us, result in results:
        for box in result.boxes:
            rows.append(to_prediction_box(box, scene_id=scene.scene_id, time_us=time_us))
    return rows


def _run_scene(
    child: PluginProcess,
    scene: Scene,
    scene_dir: Path,
    spec: ModelSpec,
    *,
    agent_ids: Sequence[str],
    source: str,
    effective: Mapping[str, object],
    work_dir: Path,
    options: RunOptions,
    frame_timeout: float,
    deadline: float | None,
    device: str,
    first_id: int,
    out: _SceneRun,
) -> None:
    by_id = {agent.agent_id: agent for agent in scene.agents}
    requested = set(agent_ids)
    samples = pose_times(scene)
    configure = Configure(
        model=spec.key,
        scene_id=scene.scene_id,
        agents=tuple(AgentRef(a, by_id[a].agent_type.value) for a in agent_ids),
        source=source,
        options=dict(effective),
        work_dir=str(work_dir),
        scene_dir=str(scene_dir.resolve()),
        raw_root=str(options.raw_root.resolve()) if options.raw_root else None,
        artifacts={},
        device=device,
        sensors=tuple(sensor_json(s) for s in scene.sensors if s.agent_id in requested),
        samples=tuple(samples),
    )
    child.send(configure)
    answer = _receive(child, frame_timeout, deadline)
    if isinstance(answer, Error):
        raise _SceneFailed(f"{answer.code}: {answer.message}")
    if not isinstance(answer, Configured):
        raise _Stop(f"plugin_error: expected configured, got {type(answer).__name__}")
    frame_id = first_id
    for time_us in samples:
        frame, files = build_frame(
            scene,
            scene_dir,
            spec,
            frame_id=frame_id,
            time_us=time_us,
            agent_ids=agent_ids,
            work_dir=work_dir,
            media_roots=options.media_roots,
            raw_root=options.raw_root,
        )
        if not any(agent in frame.poses for agent in requested):
            remove_inputs(work_dir, frame_id)
            continue
        if deadline is not None and time.monotonic() >= deadline:
            remove_inputs(work_dir, frame_id)
            raise _Stop(f"timeout: run_timeout_s reached before frame {frame_id}")
        child.send(frame)
        out.frames += 1
        try:
            result = _await_result(child, frame_id, frame_timeout, deadline)
        except RunnerTimeout:
            child.cancel(frame_id)
            try:
                _receive(child, spec.resources.grace_s, None)
            except RunnerError:
                pass
            raise _SceneFailed(
                f"timeout: frame {frame_id} at {time_us} µs exceeded {frame_timeout:g} s"
            ) from None
        finally:
            remove_inputs(work_dir, frame_id)
        if isinstance(result, Error):
            raise _SceneFailed(f"{result.code}: {result.message}")
        for box in result.boxes:
            if box.agent_id not in requested:
                raise _Stop(
                    f"plugin_error: box for agent {box.agent_id!r}, which frame {frame_id} "
                    "did not request"
                )
            out.rows.append(to_prediction_box(box, scene_id=scene.scene_id, time_us=time_us))
        out.results.append((time_us, result))
        for file in files:
            out.inputs[file.relative] = file
        frame_id += 1


def _receive(child: PluginProcess, timeout: float, deadline: float | None) -> Message:
    if deadline is not None:
        timeout = max(0.01, min(timeout, deadline - time.monotonic()))
    try:
        return child.receive(timeout)
    except RunnerTimeout:
        if deadline is not None and time.monotonic() >= deadline:
            raise _Stop("timeout: run_timeout_s reached") from None
        raise


def _await_result(
    child: PluginProcess, frame_id: int, timeout: float, deadline: float | None
) -> Result | Error:
    """The result or error for ``frame_id``; progress lines are skipped."""
    end = time.monotonic() + timeout
    while True:
        message = _receive(child, max(0.01, end - time.monotonic()), deadline)
        if isinstance(message, Progress):
            continue
        if isinstance(message, Error) and message.id is None:
            raise _Stop(f"plugin_error: {message.code}: {message.message}")
        if isinstance(message, (Result, Error)):
            if message.id != frame_id:
                raise _Stop(f"plugin_error: answer for frame {message.id}, expected {frame_id}")
            return message
        raise _Stop(
            f"plugin_error: unexpected {type(message).__name__} while waiting for frame {frame_id}"
        )


__all__ = [
    "EXIT_OK",
    "EXIT_PROTOCOL",
    "EXIT_TIMEOUT",
    "EXIT_USER",
    "BatchError",
    "BatchReport",
    "RunOptions",
    "SceneCacheLike",
    "SceneOutcome",
    "TimedResult",
    "provenance_json",
    "run_batch",
    "run_meta_for",
    "select_agents",
    "utc_now",
]
