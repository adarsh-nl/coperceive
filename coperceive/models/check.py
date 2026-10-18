# SPDX-License-Identifier: Apache-2.0
"""``coperceive model check``: the ten conformance rules of spec §Conformance over the
synthetic scene, one pass per model, pass or fail per rule."""

from __future__ import annotations

import os
import shutil
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from coperceive.evaluation.predictions import PredictionError, validate_boxes
from coperceive.model import Scene
from coperceive.models.batch import run_meta_for, select_agents
from coperceive.models.inputs import build_frame, remove_inputs, sensor_json
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
from coperceive.models.spec import Manifest, ModelSpec, check_options
from coperceive.models.synthetic import write_synthetic_scene
from coperceive.store import read_scene
from coperceive.viewdata import pose_times

RULES: tuple[str, ...] = (
    "hello first, protocol 1, name, version, every model, an environment",
    "configure answered with configured (or error unsupported: skipped)",
    "every frame answered once, in order, within frame_timeout_s",
    "every box passes PredictionBox and validate_boxes with a declared source and category",
    "the child writes only under the work directory",
    "the child does not read ground truth",
    "the child exits 0 within grace_s after bye",
    "two runs give identical results (or outputs.deterministic is false)",
    "cancel on an outstanding frame is answered with error cancelled",
    "nothing but protocol messages on stdout",
)
STATUSES = ("ok", "fail", "skipped", "not checked")


@dataclass(frozen=True)
class RuleResult:
    number: int
    name: str
    status: str
    reason: str | None = None


class _Rules:
    """Collects one verdict per rule; the first failure of a rule wins."""

    def __init__(self) -> None:
        self.results: dict[int, RuleResult] = {}

    def set(self, number: int, status: str, reason: str | None = None) -> None:
        current = self.results.get(number)
        if current is not None and current.status == "fail":
            return
        if current is not None and status == "ok" and current.status != "ok":
            return
        self.results[number] = RuleResult(number, RULES[number - 1], status, reason)

    def done(self) -> tuple[RuleResult, ...]:
        return tuple(
            self.results.get(n, RuleResult(n, RULES[n - 1], "not checked", "not reached"))
            for n in range(1, len(RULES) + 1)
        )


def check_plugin(
    manifest: Manifest,
    *,
    model: str | None,
    work_root: Path,
    keep: bool,
    raw_root: Path | None,
    parent_env: Mapping[str, str],
    options: Mapping[str, str] | None = None,
) -> tuple[RuleResult, ...]:
    """Run the rules over the synthetic scene for ``model`` or every model of the manifest."""
    rules = _Rules()
    work_root.mkdir(parents=True, exist_ok=True)
    models = [manifest.models[model]] if model is not None else list(manifest.models.values())
    try:
        for spec in models:
            _check_model(manifest, spec, rules, work_root, raw_root, parent_env, options or {})
    finally:
        if not keep:
            shutil.rmtree(work_root, ignore_errors=True)
    return rules.done()


def _listing(root: Path, exclude: Path) -> set[str]:
    out: set[str] = set()
    for path in root.rglob("*"):
        if path.is_relative_to(exclude):
            continue
        out.add(str(path.relative_to(root)))
    return out


def _check_model(
    manifest: Manifest,
    spec: ModelSpec,
    rules: _Rules,
    work_root: Path,
    raw_root: Path | None,
    parent_env: Mapping[str, str],
    options: Mapping[str, str],
) -> None:
    assert manifest.source is not None
    effective = check_options(spec, options)
    scenes_root = work_root / f"scenes-{spec.key}"
    scene_dir = write_synthetic_scene(scenes_root, spec)
    scene = read_scene(scene_dir)
    work_dir = work_root / f"work-{spec.key}"
    meta = run_meta_for(
        manifest, spec, run_id="check", created_utc="1970-01-01T00:00:00Z", provenance={}
    )
    agent_ids = select_agents(scene, spec, None)
    gt = scene_dir / "annotations.parquet"
    enforced = _hide(gt)
    before = _listing(work_root, work_dir)
    timeout = spec.resources.frame_timeout_s
    try:
        try:
            child = PluginProcess.start(
                manifest,
                spec,
                plugin_dir=manifest.source.parent,
                work_dir=work_dir,
                parent_env=parent_env,
                hello_timeout_s=timeout,
            )
        except RunnerTimeout as exc:
            rules.set(1, "fail", f"no hello within {timeout:g} s: {exc}")
            return
        except RunnerError as exc:
            rules.set(1, "fail", str(exc))
            if "not JSON" in str(exc) or "protocol violation" in str(exc):
                rules.set(10, "fail", str(exc))
            return
        hello = child.hello
        missing = [m for m in manifest.models if m not in hello.models]
        if missing:
            rules.set(1, "fail", f"hello.models lacks {missing}")
        else:
            rules.set(1, "ok")
        try:
            first = _pass(
                child,
                spec,
                scene,
                scene_dir,
                meta,
                agent_ids,
                effective,
                work_dir,
                raw_root,
                rules,
                timeout,
                cancel=hello.capabilities.cancel,
            )
            if first is None:
                return
            if spec.outputs.deterministic:
                second = _pass(
                    child,
                    spec,
                    scene,
                    scene_dir,
                    meta,
                    agent_ids,
                    effective,
                    work_dir,
                    raw_root,
                    rules,
                    timeout,
                    cancel=False,
                )
                if second is not None:
                    if [r.boxes for r in first] == [r.boxes for r in second]:
                        rules.set(8, "ok")
                    else:
                        rules.set(8, "fail", "the two passes differ box for box")
            else:
                rules.set(8, "skipped", "outputs.deterministic is false")
        except _Violation as exc:
            if exc.stdout:
                rules.set(10, "fail", exc.reason)
            rules.set(3, "fail", exc.reason)
            return
        finally:
            started = time.monotonic()
            code = child.close(grace_s=spec.resources.grace_s)
            elapsed = time.monotonic() - started
            if code == 0 and elapsed <= spec.resources.grace_s + 1.0:
                rules.set(7, "ok")
            else:
                rules.set(7, "fail", f"exit code {code} after {elapsed:.1f} s")
    finally:
        _restore(gt)
        after = _listing(work_root, work_dir)
        new = sorted(after - before)
        if new:
            rules.set(5, "fail", f"new files outside the work directory: {new[:3]}")
        else:
            rules.set(5, "ok")
        if not enforced:
            rules.set(
                6, "not checked", "annotations.parquet stayed readable (permissions not enforced)"
            )


class _Violation(Exception):
    def __init__(self, reason: str, *, stdout: bool) -> None:
        super().__init__(reason)
        self.reason = reason
        self.stdout = stdout


def _hide(path: Path) -> bool:
    """chmod 000 the ground truth; True when that makes it unreadable."""
    try:
        os.chmod(path, 0)
        with path.open("rb"):
            return False
    except PermissionError:
        return True
    except OSError:
        return False


def _restore(path: Path) -> None:
    try:
        os.chmod(path, 0o644)
    except OSError:
        pass


def _receive(child: PluginProcess, timeout: float) -> Message:
    try:
        return child.receive(timeout)
    except RunnerError as exc:
        text = str(exc)
        raise _Violation(text, stdout="not JSON" in text or "line:" in text) from None


def _pass(
    child: PluginProcess,
    spec: ModelSpec,
    scene: Scene,
    scene_dir: Path,
    meta: object,
    agent_ids: Sequence[str],
    effective: Mapping[str, object],
    work_dir: Path,
    raw_root: Path | None,
    rules: _Rules,
    timeout: float,
    *,
    cancel: bool,
) -> list[Result] | None:
    """One configure and every frame; returns the results, or None when the model is skipped
    or a rule failed hard enough to stop."""
    from coperceive.evaluation.predictions import RunMeta

    assert isinstance(meta, RunMeta)
    by_id = {a.agent_id: a for a in scene.agents}
    child.send(
        Configure(
            model=spec.key,
            scene_id=scene.scene_id,
            agents=tuple(AgentRef(a, by_id[a].agent_type.value) for a in agent_ids),
            source=spec.outputs.sources[0][0],
            options=dict(effective),
            work_dir=str(work_dir),
            scene_dir=str(scene_dir.resolve()),
            raw_root=str(raw_root.resolve()) if raw_root else None,
            artifacts={},
            device="cpu",
            sensors=tuple(sensor_json(s) for s in scene.sensors if s.agent_id in set(agent_ids)),
            samples=tuple(pose_times(scene)),
        )
    )
    answer = _receive(child, timeout)
    while isinstance(answer, Progress):
        answer = _receive(child, timeout)
    if isinstance(answer, Error) and answer.code == "unsupported":
        rules.set(2, "skipped", f"{spec.key}: {answer.message}")
        return None
    if isinstance(answer, Error):
        if answer.code == "configure_failed" and "ground truth" in answer.message:
            rules.set(6, "fail", f"{spec.key}: {answer.message}")
        rules.set(2, "fail", f"{spec.key}: {answer.code}: {answer.message}")
        return None
    if not isinstance(answer, Configured):
        rules.set(2, "fail", f"{spec.key}: expected configured, got {type(answer).__name__}")
        return None
    rules.set(2, "ok")
    rules.set(6, "ok")
    results: list[Result] = []
    frame_id = 1
    categories = set(spec.outputs.categories)
    sources = {name for name, _ in spec.outputs.sources}
    times = pose_times(scene)
    for time_us in times:
        frame, _ = build_frame(
            scene,
            scene_dir,
            spec,
            frame_id=frame_id,
            time_us=time_us,
            agent_ids=agent_ids,
            work_dir=work_dir,
            media_roots=(),
            raw_root=raw_root,
        )
        child.send(frame)
        started = time.monotonic()
        message = _receive(child, timeout)
        while isinstance(message, Progress):
            message = _receive(child, timeout)
        remove_inputs(work_dir, frame_id)
        if time.monotonic() - started > timeout:
            rules.set(3, "fail", f"frame {frame_id} took longer than {timeout:g} s")
        if not isinstance(message, (Result, Error)) or message.id != frame_id:
            got = getattr(message, "id", None)
            rules.set(
                3, "fail", f"frame {frame_id} answered with {type(message).__name__} id {got}"
            )
            raise _Violation(f"answer out of order for frame {frame_id}", stdout=False)
        if isinstance(message, Error):
            rules.set(3, "fail", f"frame {frame_id}: {message.code}: {message.message}")
            frame_id += 1
            continue
        try:
            rows = [
                to_prediction_box(b, scene_id=scene.scene_id, time_us=time_us)
                for b in message.boxes
            ]
            validate_boxes(scene, meta, rows)
            for box in message.boxes:
                if box.source not in sources:
                    raise PredictionError(f"source {box.source!r} is not declared")
                if box.category not in categories:
                    raise PredictionError(f"category {box.category!r} is not declared")
        except PredictionError as exc:
            rules.set(4, "fail", f"frame {frame_id}: {exc}")
        results.append(message)
        frame_id += 1
    rules.set(3, "ok")
    rules.set(4, "ok")
    if cancel:
        # One extra frame, cancelled at once: the child answers it with error cancelled, or
        # with its result when the result left before the cancel arrived.
        frame, _ = build_frame(
            scene,
            scene_dir,
            spec,
            frame_id=frame_id,
            time_us=times[-1],
            agent_ids=agent_ids,
            work_dir=work_dir,
            media_roots=(),
            raw_root=raw_root,
        )
        child.send(frame)
        child.cancel(frame_id)
        message = _receive(child, timeout)
        while isinstance(message, Progress):
            message = _receive(child, timeout)
        remove_inputs(work_dir, frame_id)
        if isinstance(message, Error) and message.code == "cancelled" and message.id == frame_id:
            rules.set(9, "ok")
        elif isinstance(message, Result) and message.id == frame_id:
            rules.set(9, "ok", "answered before the cancel arrived")
        else:
            rules.set(9, "fail", f"cancel answered with {type(message).__name__}")
    elif 9 not in rules.results:
        rules.set(9, "skipped", "capabilities.cancel is false")
    rules.set(10, "ok")
    return results


def render_check(name: str, results: Sequence[RuleResult]) -> str:
    lines: list[str] = []
    counts = {status: 0 for status in STATUSES}
    for result in results:
        counts[result.status] += 1
        suffix = f" {result.reason}" if result.reason and result.status != "ok" else ""
        lines.append(f"{result.number:>2}. {result.name}: {result.status}{suffix}")
    lines.append(
        f"check {name}: {counts['ok']} ok, {counts['fail']} fail, {counts['skipped']} skipped"
        + (f", {counts['not checked']} not checked" if counts["not checked"] else "")
    )
    return "\n".join(lines) + "\n"


__all__ = ["RULES", "RuleResult", "check_plugin", "render_check"]
