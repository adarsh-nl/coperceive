# SPDX-License-Identifier: Apache-2.0
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
"""Build a repro card from a local evaluation run or a saved server-profile run."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import cast

from coperceive import __version__
from coperceive.adapters.ingest import coperceive_version
from coperceive.errors import CoperceiveError
from coperceive.evaluation.evaluate import METRICS_FORMAT, read_metrics
from coperceive.evaluation.predictions import check_scene_id, predictions_root, read_run_meta
from coperceive.hpc.profile import ProfileError, ServerProfile
from coperceive.repro.schema import SCHEMA_NAME, SCHEMA_VERSION, ReproError, validate_card
from coperceive.repro.scrub import file_name, leaks, relativise, scrub_card

_HEX64 = re.compile(r"^[a-f0-9]{64}$")
_HEX40 = re.compile(r"^[a-f0-9]{40}$")
_SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_MODULE = re.compile(r"^[A-Za-z0-9_./+][A-Za-z0-9_./+-]{0,127}$")
_READ_LIMIT = 8_000_000
_PIPELINE_LABEL = "Pipeline report. Not the coperceive score."
_ROLES = frozenset({"checkpoint", "test_data", "config", "output", "other"})


@dataclass(frozen=True)
class ExportRequest:
    """Where a run's files live. At least one of ``scenes_root`` and ``profile_path``."""

    run_id: str
    scenes_root: Path | None = None
    predictions_root: Path | None = None
    profile_path: Path | None = None
    created_utc: str | None = None
    time_limit: str | None = None
    gpus: str | None = None
    memory: str | None = None


def export_card(request: ExportRequest) -> dict[str, object]:
    """The card for ``request``. Raises :class:`ReproError` when the run cannot be read."""
    run_id = _check_id(request.run_id)
    if request.scenes_root is None and request.profile_path is None:
        raise ReproError(
            "Say where the run lives: --root for a local evaluation, or --profile for a "
            "saved run on a server profile."
        )
    created = request.created_utc or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", created) is None:
        raise ReproError("created_utc must look like 2026-01-02T03:04:05Z")

    unknown: list[str] = []
    roots: dict[str, tuple[str, ...]] = {
        "data_roots": (),
        "code_roots": (),
        "results_root": (),
    }
    paths: list[dict[str, str]] = []
    plan: dict[str, object] | None = None
    run_dir: Path | None = None
    profile: ServerProfile | None = None

    if request.profile_path is not None:
        profile = _load_profile(request.profile_path)
        roots = {
            "data_roots": profile.data_roots,
            "code_roots": profile.code_roots,
            "results_root": (profile.results_root,),
        }
        plan_path = Path(profile.results_root) / "plans" / f"{run_id}.json"
        run_dir = Path(profile.results_root) / "runs" / run_id
        if plan_path.is_file():
            plan = _read_json_object(plan_path)
        elif request.scenes_root is None:
            raise ReproError(
                f"No saved plan for {run_id} under the profile's results folder "
                f"({plan_path.parent})."
            )

    local_run: Path | None = None
    provenance: dict[str, object] | None = None
    scene_ids: list[str] = []
    by_scene: dict[str, object] = {}
    if request.scenes_root is not None:
        store = predictions_root(request.scenes_root, request.predictions_root)
        if store is None:
            raise ReproError(
                f"No predictions root under {request.scenes_root} "
                "(no predictions/<run>/run.json). Pass --predictions if the runs live elsewhere."
            )
        try:
            meta = read_run_meta(store, run_id)
        except CoperceiveError as exc:
            raise ReproError(str(exc)) from exc
        local_run = store / run_id
        if meta.provenance is not None:
            provenance = {str(key): value for key, value in meta.provenance.items()}
        by_scene, scene_ids = _local_scenes(store, run_id, unknown)

    if provenance is None and local_run is not None:
        provenance = None
    remote_provenance = _provenance_file(local_run)
    if provenance is None:
        provenance = remote_provenance

    predictions_sha, predictions_known = _predictions_hash(local_run, run_dir)
    if not predictions_known:
        unknown.append("predictions_sha256")

    dataset = _dataset(request.scenes_root, unknown)
    split_ids = list(scene_ids)
    split_name, split_sha, listed = _split(plan, roots, paths)
    for scene_id in listed:
        if scene_id not in split_ids:
            split_ids.append(scene_id)
    jsonl_ids = _scene_ids_in_jsonl(local_run, run_dir)
    for scene_id in jsonl_ids:
        if scene_id not in split_ids:
            split_ids.append(scene_id)
    split_ids = sorted(set(split_ids))
    if not split_ids:
        unknown.append("split.scene_ids")
    if split_name is None:
        unknown.append("split.name")
    if split_sha is None:
        unknown.append("split.split_file_sha256")

    pipeline, script = _pipeline(plan, provenance, run_dir, local_run, roots, paths, unknown)
    checkpoint = _checkpoint(plan, provenance, roots, paths, unknown)
    environment = _environment(plan, profile, run_dir, local_run, unknown)
    resources = _resources(plan, request, unknown)
    code_commit = _code_commit(provenance, unknown)
    version = coperceive_version()
    if version == "unknown":
        version = __version__
    git_commit = _repo_commit()
    if git_commit is None:
        unknown.append("coperceive.git_commit")

    metrics: dict[str, object] | None
    if by_scene:
        metrics = {
            "source": "coperceive eval run",
            "authoritative": True,
            "by_scene": by_scene,
        }
    else:
        metrics = None
        unknown.append("metrics")

    report = _pipeline_report(run_dir) or _pipeline_report(local_run)
    perturbation = _perturbation(provenance, run_dir, local_run)
    if script is not None:
        _add_path(paths, script, roots)

    card: dict[str, object] = {
        "schema": SCHEMA_NAME,
        "schema_version": SCHEMA_VERSION,
        "id": _card_id(run_id, created, predictions_sha),
        "created_utc": created,
        "coperceive": {"version": version, "git_commit": git_commit},
        "dataset": dataset,
        "split": {
            "name": split_name,
            "scene_ids": split_ids or None,
            "split_file_sha256": split_sha,
        },
        "pipeline": pipeline,
        "checkpoint": checkpoint,
        "code": {"git_commit": code_commit},
        "environment": environment,
        "resources": resources,
        "paths": _unique_paths(paths),
        "metrics": metrics,
        "predictions_sha256": predictions_sha,
        "pipeline_report": report,
        "perturbation": perturbation,
        "unknown": sorted(set(unknown)),
    }
    card = scrub_card(card)
    problems = validate_card(card)
    leaked = leaks(card)
    if problems or leaked:
        detail = "; ".join([*problems, *leaked][:8])
        raise ReproError(f"The repro card failed its own check: {detail}")
    return card


def render_markdown(card: Mapping[str, object]) -> str:
    """A short companion a person can read without the schema."""
    lines = [
        "# Repro card",
        "",
        f"Id: {_text(card.get('id'))}",
        f"Written: {_text(card.get('created_utc'))}",
        "",
        "## What was run",
        "",
    ]
    coperceive = _object(card.get("coperceive"))
    version = _text(coperceive.get("version"))
    commit = _maybe(coperceive.get("git_commit"))
    lines.append(f"coperceive {version} ({commit})")
    dataset = _object(card.get("dataset"))
    lines.append(
        "Dataset: "
        + ", ".join(
            f"{label} {_maybe(dataset.get(key))}"
            for label, key in (
                ("id", "id"),
                ("version", "version"),
                ("manifest", "manifest_sha256"),
            )
        )
    )
    split = _object(card.get("split"))
    scenes = split.get("scene_ids")
    count = len(scenes) if isinstance(scenes, list) else 0
    lines.append(f"Split: {_maybe(split.get('name'))} ({count} scenes)")
    pipeline = _object(card.get("pipeline"))
    lines.append(f"Pipeline: {_maybe(pipeline.get('name'))}")
    checkpoint = _object(card.get("checkpoint"))
    lines.append(
        "Checkpoint: "
        f"{_maybe(checkpoint.get('file_name'))} sha256 {_maybe(checkpoint.get('sha256'))}"
    )
    code = _object(card.get("code"))
    lines.append(f"Code commit: {_maybe(code.get('git_commit'))}")
    lines.extend(["", "## Environment", ""])
    environment = _object(card.get("environment"))
    lines.append(f"Python {_maybe(environment.get('python'))}")
    modules = environment.get("modules")
    if isinstance(modules, list):
        module_text = ", ".join(item for item in modules if isinstance(item, str))
    else:
        module_text = ""
    lines.append(f"Modules: {module_text or 'not recorded'}")
    digest = _maybe(environment.get("container_image_digest"))
    lines.append(f"Container digest: {digest}")
    cuda = _maybe(environment.get("cuda"))
    driver = _maybe(environment.get("driver"))
    lines.append(f"CUDA {cuda}, driver {driver}")
    resources = _object(card.get("resources"))
    lines.append(
        "Scheduler request: "
        f"GPUs {_maybe(resources.get('gpus'))}, time {_maybe(resources.get('time'))}, "
        f"memory {_maybe(resources.get('memory'))}"
    )
    lines.extend(["", "## Scores", ""])
    metrics = card.get("metrics")
    if isinstance(metrics, dict):
        scenes_obj = metrics.get("by_scene")
        scene_count = len(scenes_obj) if isinstance(scenes_obj, dict) else 0
        lines.append(
            f"Authoritative scores from coperceive eval run, {scene_count} scene"
            f"{'' if scene_count == 1 else 's'}."
        )
    else:
        lines.append("coperceive has not scored this run, so this card has no score.")
    lines.append(f"predictions.jsonl sha256: {_maybe(card.get('predictions_sha256'))}")
    if card.get("pipeline_report") is not None:
        lines.append("A pipeline report is attached and is not the coperceive score.")
    if card.get("perturbation") is not None:
        lines.append("A perturbation spec is attached.")
    unknown = card.get("unknown")
    lines.extend(["", "## Not recorded", ""])
    if isinstance(unknown, list) and unknown:
        for item in unknown:
            if isinstance(item, str):
                lines.append(f"- {item}")
    else:
        lines.append("Nothing was left unknown.")
    lines.append("")
    return "\n".join(lines)


def write_card(card: Mapping[str, object], dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(card, indent=2, sort_keys=True, allow_nan=False) + "\n"
    dest.write_text(text, encoding="utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def check_card(
    card: object,
    *,
    run_dir: Path | None = None,
    predictions: Path | None = None,
    run_id: str | None = None,
) -> list[str]:
    """Schema, secret and hash problems. Empty means the card checks out."""
    problems = validate_card(card)
    problems.extend(leaks(card))
    if not isinstance(card, dict):
        return problems or ["the card is not a JSON object"]
    report = card.get("pipeline_report")
    if isinstance(report, dict) and report.get("authoritative") is not False:
        problems.append("pipeline_report.authoritative must be false")
    metrics = card.get("metrics")
    if isinstance(metrics, dict) and metrics.get("authoritative") is not True:
        problems.append("metrics.authoritative must be true")
    if run_dir is not None:
        problems.extend(_check_predictions(card, run_dir))
    if predictions is not None and run_id is not None and isinstance(metrics, dict):
        problems.extend(_check_metrics(metrics, predictions, run_id))
    return problems


def _check_predictions(card: Mapping[str, object], run_dir: Path) -> list[str]:
    expected = card.get("predictions_sha256")
    path = _child(run_dir, "predictions.jsonl")
    if path is None:
        if isinstance(expected, str):
            return [
                "predictions.jsonl is not in the run directory, so the recorded hash "
                "cannot be checked"
            ]
        return []
    actual = sha256_file(path)
    if expected != actual:
        return [
            "predictions.jsonl does not match predictions_sha256; the predictions "
            "changed after the card was written"
        ]
    return []


def _check_metrics(metrics: Mapping[str, object], predictions: Path, run_id: str) -> list[str]:
    by_scene = metrics.get("by_scene")
    if not isinstance(by_scene, dict):
        return ["metrics.by_scene is missing"]
    problems: list[str] = []
    for scene_id, recorded in by_scene.items():
        if not isinstance(scene_id, str):
            continue
        directory = predictions / run_id / scene_id
        if not (directory / "metrics.json").is_file():
            problems.append(f"metrics for {scene_id} are not in {predictions}")
            continue
        try:
            on_disk = read_metrics(directory)
        except CoperceiveError as exc:
            problems.append(str(exc))
            continue
        if recorded != on_disk:
            problems.append(f"metrics for {scene_id} do not match coperceive's metrics.json")
    return problems


def _local_scenes(
    store: Path, run_id: str, unknown: list[str]
) -> tuple[dict[str, object], list[str]]:
    run_dir = store / run_id
    by_scene: dict[str, object] = {}
    scene_ids: list[str] = []
    if not run_dir.is_dir():
        return by_scene, scene_ids
    for child in sorted(run_dir.iterdir()):
        if not child.is_dir() or child.name.startswith(".") or child.name in {"cache"}:
            continue
        try:
            check_scene_id(child.name)
        except CoperceiveError:
            continue
        scene_ids.append(child.name)
        metrics_path = child / "metrics.json"
        if not metrics_path.is_file():
            unknown.append(f"metrics.{child.name}")
            continue
        try:
            by_scene[child.name] = read_metrics(child)
        except CoperceiveError:
            unknown.append(f"metrics.{child.name}")
    return by_scene, scene_ids


def _dataset(scenes_root: Path | None, unknown: list[str]) -> dict[str, object]:
    result: dict[str, object] = {"id": None, "version": None, "manifest_sha256": None}
    if scenes_root is None:
        unknown.extend(["dataset.id", "dataset.version", "dataset.manifest_sha256"])
        return result
    path = scenes_root / "dataset.json"
    if not path.is_file():
        unknown.extend(["dataset.id", "dataset.version", "dataset.manifest_sha256"])
        return result
    try:
        payload = _read_json_object(path)
    except ReproError:
        unknown.extend(["dataset.id", "dataset.version", "dataset.manifest_sha256"])
        return result
    result["manifest_sha256"] = sha256_file(path)
    dataset_id = payload.get("id")
    version = payload.get("version")
    if isinstance(dataset_id, str) and _SAFE_NAME.fullmatch(dataset_id):
        result["id"] = dataset_id
    else:
        unknown.append("dataset.id")
    if isinstance(version, str) and _SAFE_NAME.fullmatch(version):
        result["version"] = version
    else:
        unknown.append("dataset.version")
    return result


def _split(
    plan: Mapping[str, object] | None,
    roots: Mapping[str, tuple[str, ...]],
    paths: list[dict[str, str]],
) -> tuple[str | None, str | None, list[str]]:
    if plan is None:
        return None, None, []
    split = plan.get("split")
    if not isinstance(split, dict):
        return None, None, []
    raw_path = split.get("path")
    name = file_name(raw_path) if isinstance(raw_path, str) else None
    sha = split.get("sha256")
    digest = sha if isinstance(sha, str) and _HEX64.fullmatch(sha) else None
    listed: list[str] = []
    if isinstance(raw_path, str):
        _add_path(paths, raw_path, roots)
        file_path = Path(raw_path)
        if file_path.is_file() and _under_any_root(file_path, roots):
            if digest is None:
                digest = sha256_file(file_path)
            listed = _scene_lines(file_path)
    return name, digest, listed


def _pipeline(
    plan: Mapping[str, object] | None,
    provenance: Mapping[str, object] | None,
    run_dir: Path | None,
    local_run: Path | None,
    roots: Mapping[str, tuple[str, ...]],
    paths: list[dict[str, str]],
    unknown: list[str],
) -> tuple[dict[str, object], str | None]:
    name: str | None = None
    argv: list[str] = []
    if plan is not None:
        pipeline = plan.get("pipeline")
        if isinstance(pipeline, dict):
            raw_name = pipeline.get("name")
            if isinstance(raw_name, str) and raw_name.strip():
                name = raw_name.strip()
        raw_argv = plan.get("argv")
        if isinstance(raw_argv, list):
            argv = [item for item in raw_argv if isinstance(item, str)]
    if name is None and provenance is not None:
        plugin = provenance.get("plugin")
        model = provenance.get("model")
        if isinstance(plugin, str) and isinstance(model, str):
            name = f"{plugin}/{model}"
        elif isinstance(model, str):
            name = model
    if name is None:
        unknown.append("pipeline.name")
    script, values = _parse_argv(argv)
    roles = _roles_for(script, run_dir, local_run, roots)
    registration = _registration_sha(script, run_dir, local_run, roots)
    if registration is None:
        unknown.append("pipeline.registration_sha256")
    arguments: list[dict[str, object]] = []
    for arg_name in sorted(values):
        role = roles.get(arg_name)
        if role not in _ROLES:
            role = None
            unknown.append(f"pipeline.arguments.{arg_name}.role")
        value = _argument_value(arg_name, values[arg_name], roots, paths, unknown)
        arguments.append({"name": arg_name, "role": role, "value": value})
    return (
        {"name": name, "registration_sha256": registration, "arguments": arguments},
        script,
    )


def _argument_value(
    name: str,
    value: object,
    roots: Mapping[str, tuple[str, ...]],
    paths: list[dict[str, str]],
    unknown: list[str],
) -> object:
    if isinstance(value, bool) or value is None:
        return value
    if not isinstance(value, str):
        unknown.append(f"pipeline.arguments.{name}")
        return None
    if "\n" in value or "\x00" in value:
        unknown.append(f"pipeline.arguments.{name}")
        return None
    if value.startswith("/") or value.startswith("~"):
        recorded = _add_path(paths, value, roots)
        if recorded is not None:
            return recorded
        unknown.append(f"pipeline.arguments.{name}")
        return file_name(value)
    return value


def _checkpoint(
    plan: Mapping[str, object] | None,
    provenance: Mapping[str, object] | None,
    roots: Mapping[str, tuple[str, ...]],
    paths: list[dict[str, str]],
    unknown: list[str],
) -> dict[str, object]:
    raw_path = ""
    sha: str | None = None
    size: int | None = None
    if plan is not None and isinstance(plan.get("checkpoint"), dict):
        checkpoint = cast(dict[str, object], plan["checkpoint"])
        if isinstance(checkpoint.get("path"), str):
            raw_path = cast(str, checkpoint["path"])
        recorded = checkpoint.get("sha256")
        if isinstance(recorded, str) and _HEX64.fullmatch(recorded):
            sha = recorded
    if provenance is not None and isinstance(provenance.get("checkpoint"), dict):
        previous = cast(dict[str, object], provenance["checkpoint"])
        if not raw_path and isinstance(previous.get("path"), str):
            raw_path = cast(str, previous["path"])
        if size is None and type(previous.get("size")) is int and cast(int, previous["size"]) >= 0:
            size = cast(int, previous["size"])
    name = file_name(raw_path) if raw_path else None
    if raw_path:
        _add_path(paths, raw_path, roots)
        file_path = Path(raw_path)
        if file_path.is_file() and _under_any_root(file_path, roots):
            sha = sha256_file(file_path)
            size = file_path.stat().st_size
    if name is None:
        unknown.append("checkpoint.file_name")
    if sha is None:
        unknown.append("checkpoint.sha256")
    if size is None:
        unknown.append("checkpoint.size_bytes")
    return {"file_name": name, "sha256": sha, "size_bytes": size}


def _environment(
    plan: Mapping[str, object] | None,
    profile: ServerProfile | None,
    run_dir: Path | None,
    local_run: Path | None,
    unknown: list[str],
) -> dict[str, object]:
    captured = _environment_file(run_dir) or _environment_file(local_run)
    python = _opt_str(captured, "python")
    packages = captured.get("key_packages") if captured else None
    key_packages: dict[str, str] = {}
    if isinstance(packages, dict):
        for key, value in packages.items():
            if isinstance(key, str) and isinstance(value, str) and _SAFE_NAME.fullmatch(key):
                key_packages[key] = value
    freeze = _opt_sha(captured, "pip_freeze_sha256")
    modules = _modules(captured)
    digest = _opt_str(captured, "container_image_digest")
    if digest is not None and not digest.startswith("sha256:"):
        digest = None
    cuda = _opt_str(captured, "cuda")
    driver = _opt_str(captured, "driver")
    declared_modules, declared_digest = _declared_environment(plan, profile)
    if not modules and declared_modules:
        modules = declared_modules
    if digest is None and declared_digest is not None:
        digest = declared_digest
    seen = captured is not None or bool(declared_modules) or declared_digest is not None
    if python is None:
        unknown.append("environment.python")
    if freeze is None:
        unknown.append("environment.pip_freeze_sha256")
    if digest is None:
        unknown.append("environment.container_image_digest")
    if not modules and not seen:
        unknown.append("environment.modules")
    if cuda is None:
        unknown.append("environment.cuda")
    if driver is None:
        unknown.append("environment.driver")
    return {
        "python": python,
        "key_packages": key_packages,
        "pip_freeze_sha256": freeze,
        "container_image_digest": digest,
        "modules": modules,
        "cuda": cuda,
        "driver": driver,
    }


def _declared_environment(
    plan: Mapping[str, object] | None, profile: ServerProfile | None
) -> tuple[list[str], str | None]:
    modules: list[str] = []
    digest: str | None = None
    sources: list[Mapping[str, object]] = []
    if plan is not None and isinstance(plan.get("environment"), dict):
        sources.append(cast(dict[str, object], plan["environment"]))
    if profile is not None:
        sources.append(profile.environment.to_json())
    for source in sources:
        raw_modules = source.get("modules")
        if isinstance(raw_modules, list):
            for item in raw_modules:
                if isinstance(item, str) and _MODULE.fullmatch(item) and not item.startswith("/"):
                    if item not in modules:
                        modules.append(item)
        image = source.get("container_image")
        if isinstance(image, str) and image.startswith("sha256:"):
            digest = image
    return modules, digest


def _resources(
    plan: Mapping[str, object] | None, request: ExportRequest, unknown: list[str]
) -> dict[str, object]:
    gpus = _plain(request.gpus)
    if gpus is None and plan is not None and isinstance(plan.get("gres"), str):
        gpus = _plain(cast(str, plan["gres"]))
    time_limit = _plain(request.time_limit)
    memory = _plain(request.memory)
    if gpus is None:
        unknown.append("resources.gpus")
    if time_limit is None:
        unknown.append("resources.time")
    if memory is None:
        unknown.append("resources.memory")
    return {"gpus": gpus, "time": time_limit, "memory": memory}


def _pipeline_report(run_dir: Path | None) -> dict[str, object] | None:
    if run_dir is None:
        return None
    path = _child(run_dir, "metrics.json")
    if path is None:
        return None
    try:
        payload = _read_json_object(path)
    except ReproError:
        return None
    if payload.get("format") == METRICS_FORMAT:
        return None
    return {
        "authoritative": False,
        "label": _PIPELINE_LABEL,
        "content": payload,
    }


def _perturbation(
    provenance: Mapping[str, object] | None,
    run_dir: Path | None,
    local_run: Path | None,
) -> dict[str, object] | None:
    if provenance is not None and isinstance(provenance.get("perturbation"), dict):
        raw = cast(dict[str, object], provenance["perturbation"])
        return {str(key): value for key, value in raw.items()}
    for directory in (local_run, run_dir):
        if directory is None:
            continue
        path = _child(directory, "perturbation.json")
        if path is None:
            continue
        try:
            return _read_json_object(path)
        except ReproError:
            continue
    return None


def _predictions_hash(local_run: Path | None, run_dir: Path | None) -> tuple[str | None, bool]:
    for directory in (local_run, run_dir):
        if directory is None:
            continue
        jsonl = _child(directory, "predictions.jsonl")
        if jsonl is not None:
            return sha256_file(jsonl), True
    for directory in (local_run, run_dir):
        if directory is None:
            continue
        sidecar = _child(directory, "predictions.sha256")
        if sidecar is None:
            continue
        text = sidecar.read_text(encoding="utf-8").strip().split()
        if text and _HEX64.fullmatch(text[0]):
            return text[0], True
    return None, False


def _scene_ids_in_jsonl(local_run: Path | None, run_dir: Path | None) -> list[str]:
    found: list[str] = []
    for directory in (local_run, run_dir):
        if directory is None:
            continue
        path = _child(directory, "predictions.jsonl")
        if path is None:
            continue
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if len(line) > 1_000_000:
                    continue
                try:
                    payload: object = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(payload, dict):
                    continue
                scene_id = payload.get("scene_id")
                if not isinstance(scene_id, str):
                    continue
                try:
                    check_scene_id(scene_id)
                except CoperceiveError:
                    continue
                found.append(scene_id)
    return found


def _environment_file(directory: Path | None) -> dict[str, object] | None:
    if directory is None:
        return None
    path = _child(directory, "environment.json")
    if path is None:
        return None
    try:
        payload = _read_json_object(path)
    except ReproError:
        return None
    if payload.get("schema") != "coperceive-environment":
        return None
    return payload


def _provenance_file(directory: Path | None) -> dict[str, object] | None:
    if directory is None:
        return None
    path = _child(directory, "run.json")
    if path is None:
        return None
    try:
        payload = _read_json_object(path)
    except ReproError:
        return None
    provenance = payload.get("provenance")
    if isinstance(provenance, dict):
        return {str(key): value for key, value in provenance.items()}
    return None


def _registration_sha(
    script: str | None,
    run_dir: Path | None,
    local_run: Path | None,
    roots: Mapping[str, tuple[str, ...]],
) -> str | None:
    for directory in (run_dir, local_run):
        if directory is None:
            continue
        path = _child(directory, "coperceive-pipeline.json")
        if path is not None:
            return sha256_file(path)
    if script is None:
        return None
    sibling = Path(script).parent / "coperceive-pipeline.json"
    if sibling.is_file() and _under_any_root(sibling, roots):
        return sha256_file(sibling)
    return None


def _roles_for(
    script: str | None,
    run_dir: Path | None,
    local_run: Path | None,
    roots: Mapping[str, tuple[str, ...]],
) -> dict[str, str]:
    payload: dict[str, object] | None = None
    for directory in (run_dir, local_run):
        if directory is None:
            continue
        path = _child(directory, "coperceive-pipeline.json")
        if path is None:
            continue
        try:
            payload = _read_json_object(path)
        except ReproError:
            payload = None
        break
    if payload is None and script is not None:
        sibling = Path(script).parent / "coperceive-pipeline.json"
        if sibling.is_file() and _under_any_root(sibling, roots):
            try:
                payload = _read_json_object(sibling)
            except ReproError:
                payload = None
    roles: dict[str, str] = {}
    if payload is None:
        return roles
    arguments = payload.get("arguments")
    if not isinstance(arguments, list):
        return roles
    for item in arguments:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        role = item.get("role")
        if isinstance(name, str) and isinstance(role, str) and role in _ROLES:
            roles[name] = role
    return roles


def _parse_argv(argv: list[str]) -> tuple[str | None, dict[str, object]]:
    script: str | None = None
    values: dict[str, object] = {}
    index = 0
    while index < len(argv):
        item = argv[index]
        if item.startswith("--") and len(item) > 2:
            name = item[2:]
            if index + 1 < len(argv) and not argv[index + 1].startswith("--"):
                values[name] = argv[index + 1]
                index += 2
            else:
                values[name] = True
                index += 1
            continue
        if script is None and (item.endswith(".py") or item.endswith(".sh")):
            script = item
        index += 1
    return script, values


def _add_path(
    paths: list[dict[str, str]], raw: str, roots: Mapping[str, tuple[str, ...]]
) -> str | None:
    recorded = relativise(raw, roots)
    if recorded is None:
        return None
    paths.append(recorded)
    return recorded["relative"]


def _unique_paths(paths: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: list[dict[str, str]] = []
    for item in sorted(paths, key=lambda entry: (entry["role"], entry["relative"])):
        if item not in seen:
            seen.append(item)
    return seen


def _code_commit(provenance: Mapping[str, object] | None, unknown: list[str]) -> str | None:
    if provenance is None:
        unknown.append("code.git_commit")
        return None
    commit = provenance.get("git_commit")
    if isinstance(commit, str) and _HEX40.fullmatch(commit):
        return commit
    unknown.append("code.git_commit")
    return None


def _repo_commit() -> str | None:
    root = Path(__file__).resolve().parents[2]
    if not (root / ".git").exists():
        return None
    try:
        done = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    text = done.stdout.strip()
    if _HEX40.fullmatch(text):
        return text
    return None


def _card_id(run_id: str, created: str, predictions: str | None) -> str:
    material = f"{run_id}\n{created}\n{predictions or ''}".encode()
    return "card-" + hashlib.sha256(material).hexdigest()[:12]


def _load_profile(path: Path) -> ServerProfile:
    try:
        loaded: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ReproError(f"Could not read the server profile: {exc}") from exc
    if isinstance(loaded, dict) and isinstance(loaded.get("profile"), dict):
        loaded = loaded["profile"]
    if not isinstance(loaded, dict):
        raise ReproError("The server profile is not a JSON object.")
    try:
        return ServerProfile.from_json({str(key): value for key, value in loaded.items()})
    except ProfileError as exc:
        raise ReproError(str(exc)) from exc


def _read_json_object(path: Path) -> dict[str, object]:
    try:
        loaded: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ReproError(f"Could not read {path.name}: {exc}") from exc
    if not isinstance(loaded, dict):
        raise ReproError(f"{path.name} is not a JSON object.")
    return {str(key): value for key, value in loaded.items()}


def _child(directory: Path, name: str) -> Path | None:
    if "/" in name or name in {"", ".", ".."}:
        return None
    path = directory / name
    try:
        resolved = path.resolve()
        root = directory.resolve()
    except OSError:
        return None
    if resolved != root and root not in resolved.parents:
        return None
    if not resolved.is_file() or resolved.stat().st_size > _READ_LIMIT:
        return None
    return resolved


def _under_any_root(path: Path, roots: Mapping[str, tuple[str, ...]]) -> bool:
    try:
        resolved = str(path.resolve())
    except OSError:
        return False
    return relativise(resolved, roots) is not None or relativise(str(path), roots) is not None


def _scene_lines(path: Path) -> list[str]:
    found: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        scene_id = line.strip()
        if not scene_id or scene_id.startswith("#"):
            continue
        try:
            check_scene_id(scene_id)
        except CoperceiveError:
            return []
        found.append(scene_id)
    return found


def _modules(captured: Mapping[str, object] | None) -> list[str]:
    if captured is None:
        return []
    raw = captured.get("modules")
    if not isinstance(raw, list):
        return []
    modules: list[str] = []
    for item in raw:
        if isinstance(item, str) and _MODULE.fullmatch(item) and not item.startswith("/"):
            modules.append(item)
    return modules


def _opt_str(payload: Mapping[str, object] | None, key: str) -> str | None:
    if payload is None:
        return None
    value = payload.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _opt_sha(payload: Mapping[str, object] | None, key: str) -> str | None:
    value = _opt_str(payload, key)
    if value is not None and _HEX64.fullmatch(value):
        return value
    return None


def _plain(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    if not text or "\n" in text or "\x00" in text or len(text) > 128:
        return None
    return text


def _check_id(run_id: str) -> str:
    if (
        not run_id
        or run_id in {".", ".."}
        or "/" in run_id
        or "\\" in run_id
        or run_id.startswith(".")
        or "\x00" in run_id
    ):
        raise ReproError(f"run id {run_id!r} is not a directory name")
    return run_id


def _object(value: object) -> Mapping[str, object]:
    if isinstance(value, dict):
        return cast(dict[str, object], value)
    return {}


def _text(value: object) -> str:
    return value if isinstance(value, str) else "not recorded"


def _maybe(value: object) -> str:
    if isinstance(value, str) and value:
        return value
    return "not recorded"
