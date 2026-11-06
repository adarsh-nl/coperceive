# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownParameterType=false, reportMissingParameterType=false, reportArgumentType=false, reportPrivateUsage=false, reportIndexIssue=false, reportAttributeAccessIssue=false, reportOptionalMemberAccess=false, reportGeneralTypeIssues=false, reportUnusedFunction=false
"""Export one run as a shareable ``.cpbundle``."""

from __future__ import annotations

import json
import shutil
import tempfile
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import cast

from coperceive import __version__
from coperceive.bundle.format import Content, manifest_document, safe_member, sha256_file
from coperceive.evaluation.config import DEFAULT_CONFIG, load_config
from coperceive.evaluation.predictions import (
    BOXES_FILE,
    PredictionBox,
    read_boxes,
    read_run_meta,
    write_run,
)
from coperceive.plot import render_bev
from coperceive.repro.export import ExportRequest, check_card, export_card
from coperceive.repro.scrub import scrub_json, scrub_text
from coperceive.store import read_scene

MANIFEST = "bundle.json"


class BundleExportError(ValueError):
    """The selected run cannot be exported."""


@dataclass(frozen=True)
class BundleExportRequest:
    run_id: str
    scenes_root: Path
    predictions_root: Path
    out: Path
    title: str = ""
    compare_runs: tuple[str, ...] = ()
    frames: str = "worst:6"
    include_raw: bool = False
    include_full_predictions: bool = False
    include_figures: bool = False
    created_utc: str | None = None
    config_path: Path | None = None


@dataclass
class _Staging:
    files: dict[str, str] = field(default_factory=dict)  # relative -> kind
    licences: list[dict[str, str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _safe_scene_id(value: object) -> str:
    text = str(value)
    if not text or "/" in text or "\\" in text or text in {".", ".."}:
        raise BundleExportError(f"unsafe scene id {text!r}")
    return text


def _choose_frames(scenes_root: Path, run_dir: Path, spec: str) -> list[tuple[str, int]]:
    if spec.startswith("worst:"):
        try:
            count = int(spec.split(":", 1)[1])
        except ValueError:
            raise BundleExportError("frames must be worst:N or a comma-separated list") from None
    else:
        count = 0
    candidates: list[tuple[str, int]] = []
    failures = run_dir / "failures.json"
    if failures.is_file():
        payload = json.loads(failures.read_text(encoding="utf-8"))
        for item in cast(list[dict[str, object]], payload.get("frames", [])):
            candidates.append((_safe_scene_id(item.get("scene_id")), int(item.get("time_us", 0))))
    if not candidates:
        for scene_dir in sorted(
            p for p in scenes_root.iterdir() if (p / "manifest.json").is_file()
        ):
            scene = read_scene(scene_dir)
            times = sorted({a.time_us for a in scene.annotations})
            candidates.extend((scene.scene_id, time) for time in times)
    if spec.startswith("worst:"):
        return candidates[:count]
    out: list[tuple[str, int]] = []
    for part in spec.split(","):
        scene_id, _, time_text = part.partition("@")
        out.append((_safe_scene_id(scene_id), int(time_text or 0)))
    return out


def _copy_scene_structure(staging: _Staging, scene_dir: Path, work: Path) -> None:
    for name in (
        "manifest.json",
        "poses.parquet",
        "annotations.parquet",
        "objects.parquet",
        "point_index.parquet",
    ):
        source = scene_dir / name
        if source.is_file():
            target = f"scenes/{scene_dir.name}/{name}"
            (work / target).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, work / target)
            staging.files[target] = "scene-structure"


def _write_frame(
    staging: _Staging, work: Path, scene_dir: Path, scene_id: str, time_us: int, index: int
) -> None:
    scene = read_scene(scene_dir)
    preview = f"frames/frame-{index + 1:02d}.png"
    (work / preview).parent.mkdir(parents=True, exist_ok=True)
    try:
        render_bev(scene, work / preview, time_us=time_us, theme="light", points=False)
        staging.files[preview] = "frame-preview"
    except Exception:
        staging.notes.append(f"no preview for {scene_id} at {time_us}")
    annotations = [
        {
            "track_id": a.track_id,
            "category": a.category,
            "center": list(a.center),
            "size_lwh": list(a.size_lwh),
        }
        for a in scene.annotations
        if a.time_us == time_us and a.agent_id == scene.agents[0].agent_id
    ]
    payload = {"scene_id": scene_id, "time_us": time_us, "boxes": annotations}
    target = f"frames/frame-{index + 1:02d}.json"
    (work / target).parent.mkdir(parents=True, exist_ok=True)
    (work / target).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    staging.files[target] = "frame-json"


def _scrub_work(work: Path) -> None:
    for path in work.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() == ".json":
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            path.write_text(json.dumps(scrub_json(payload), indent=2) + "\n", encoding="utf-8")
        elif path.suffix.lower() in {".txt", ".log", ".md", ".yaml", ".yml"}:
            path.write_text(
                scrub_text(path.read_text(encoding="utf-8", errors="replace")), encoding="utf-8"
            )


def export_bundle(request: BundleExportRequest) -> Path:
    """Write ``request.out`` and return its path. Every text file is scrubbed."""
    run_id = request.run_id
    run_dir = request.predictions_root / run_id
    if not run_dir.is_dir():
        raise BundleExportError(f"no prediction run {run_id!r} under {request.predictions_root}")
    meta = read_run_meta(request.predictions_root, run_id)
    box_rows: list[PredictionBox] = []
    for scene_dir in sorted(p for p in run_dir.iterdir() if p.is_dir()):
        box_rows.extend(read_boxes(request.predictions_root, run_id, scene_dir.name))
    frames = _choose_frames(request.scenes_root, run_dir, request.frames)
    if not frames:
        raise BundleExportError("no frames selected")
    wanted = set(frames)
    filtered = [box for box in box_rows if (box.scene_id, box.time_us) in wanted]
    if not filtered:
        raise BundleExportError("the selected frames have no predictions")
    out = request.out
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="coperceive-bundle-") as raw_work:
        work = Path(raw_work)
        staging = _Staging()
        # repro card
        card = export_card(
            ExportRequest(
                run_id=run_id,
                scenes_root=request.scenes_root,
                predictions_root=request.predictions_root,
                created_utc=request.created_utc,
            )
        )
        problems = check_card(card)
        if problems:
            raise BundleExportError("repro card failed its own check: " + "; ".join(problems))
        (work / "repro").mkdir()
        (work / "repro/card.json").write_text(json.dumps(card, indent=2) + "\n", encoding="utf-8")
        staging.files["repro/card.json"] = "repro-card"
        # eval config + metrics
        config = load_config(request.config_path) if request.config_path else DEFAULT_CONFIG
        (work / "eval").mkdir()
        (work / "eval/config.json").write_text(
            json.dumps(config_to_json(config), indent=2) + "\n", encoding="utf-8"
        )
        staging.files["eval/config.json"] = "eval-config"
        for scene_id, _time_us in frames:
            scene_dir = request.scenes_root / scene_id
            metrics = request.predictions_root / run_id / scene_id / "metrics.json"
            if metrics.is_file():
                rel = f"eval/{scene_id}/metrics.json"
                (work / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(metrics, work / rel)
                staging.files[rel] = "eval-metrics"
        # filtered predictions
        meta2 = meta
        write_run(work / "predictions", run_id, meta2, filtered, force=True)
        for path in (work / "predictions").rglob("*"):
            if path.is_file():
                staging.files[path.relative_to(work).as_posix()] = "predictions"
        if request.include_full_predictions:
            for name in ("run.json", BOXES_FILE):
                source = run_dir / name
                if source.is_file():
                    target = f"predictions-full/{name}"
                    (work / target).parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, work / target)
                    staging.files[target] = "full-predictions"
        # scenes structure + frames
        for index, (scene_id, time_us) in enumerate(frames):
            scene_dir = request.scenes_root / scene_id
            _copy_scene_structure(staging, scene_dir, work)
            _write_frame(staging, work, scene_dir, scene_id, time_us, index)
            if request.include_raw:
                scene = read_scene(scene_dir)
                for image in scene.images:
                    if image.time_us != time_us:
                        continue
                    source = scene_dir / image.path
                    if source.is_file():
                        target = f"raw/{scene_id}/{image.path}"
                        (work / target).parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(source, work / target)
                        staging.files[target] = "raw"
                staging.licences.append(
                    {
                        "dataset": scene_id,
                        "note": "raw files included; check the dataset licence before sharing",
                    }
                )
        _scrub_work(work)
        # manifest over every file except itself
        contents: list[Content] = []
        for rel in sorted(staging.files):
            path = work / safe_member(rel)
            contents.append(
                Content(rel, sha256_file(path), path.stat().st_size, staging.files[rel])
            )
        created = request.created_utc or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        title = scrub_text(request.title or f"{meta.run_id} results")
        licence_rows = [
            {key: scrub_text(value) for key, value in row.items()} for row in staging.licences
        ]
        document = manifest_document(
            title=title,
            app_version=__version__,
            created_utc=created,
            run_id=run_id,
            contents=contents,
            licences=licence_rows,
            privacy={"raw_sensor_data": request.include_raw, "scrubbed": True},
        )
        (work / MANIFEST).write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for path in sorted(work.rglob("*")):
                if path.is_file():
                    archive.write(path, path.relative_to(work).as_posix())
    return out


def config_to_json(config: object) -> dict[str, object]:
    from coperceive.evaluation.config import config_json as convert

    return convert(config)  # type: ignore[arg-type]
