# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownParameterType=false, reportMissingParameterType=false, reportArgumentType=false, reportPrivateUsage=false, reportIndexIssue=false, reportAttributeAccessIssue=false, reportOptionalMemberAccess=false, reportGeneralTypeIssues=false, reportUnusedFunction=false
"""Export one run as a shareable ``.cpbundle``.

The bundle always carries the scores, the boxes (ground truth for the included
frames plus the run's predictions), the poses, the repro card and the rerun
command as text. LiDAR points and camera images are only carried when the
dataset is on the redistributable allow list (see :mod:`coperceive.bundle.sensor`),
the exporter opts in, and the selected frames fit the sensor-data cap.

Nothing is read through a link (FOLD-03). Every scene and run folder the export
reads is walked first with ``os.walk(followlinks=False)``; each entry is
``lstat``-ed, and a symbolic link (to anything, including a file in the same
scene), a FIFO, a device or a socket refuses the **whole** export with an error
that names it. The staged files pass the same check before they are scrubbed,
hashed and zipped, and every copy opens its source with ``O_NOFOLLOW``.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import tempfile
import zipfile
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO, cast

from coperceive import __version__
from coperceive.bundle.format import Content, manifest_document, safe_member, sha256_file
from coperceive.bundle.sensor import SENSOR_MAX_BYTES, SensorPolicy, sensor_data_policy
from coperceive.evaluation.evaluate import MATCHES_FILE
from coperceive.evaluation.predictions import (
    BOXES_FILE,
    PredictionBox,
    read_boxes,
    read_run_meta,
    write_run,
)
from coperceive.model import Scene
from coperceive.plot import render_bev
from coperceive.repro.export import ExportRequest, check_card, export_card
from coperceive.repro.scrub import scrub_json, scrub_text
from coperceive.store import read_scene, write_scene

MANIFEST = "bundle.json"
SENSOR_POINTS_KIND = "sensor-points"
SENSOR_IMAGE_KIND = "sensor-image"


class BundleExportError(ValueError):
    """The selected run cannot be exported."""


@dataclass(frozen=True)
class BundleExportRequest:
    run_id: str
    scenes_root: Path
    predictions_root: Path
    out: Path
    title: str = ""
    frames: str = "worst:6"
    include_full_predictions: bool = False
    #: The dataset the run was evaluated on. It is never inferred from a name:
    #: the allow list is looked up by this id, and a missing id means no sensor
    #: data can be included.
    dataset_id: str | None = None
    #: Off by default. When true and the dataset is allow-listed, the included
    #: frames' LiDAR points and camera images travel in the bundle.
    include_sensor_data: bool = False
    #: Uncompressed LiDAR + camera bytes allowed for the included frames.
    sensor_max_bytes: int = SENSOR_MAX_BYTES
    created_utc: str | None = None
    extra_markers: tuple[str, ...] = ()


@dataclass
class _Staging:
    files: dict[str, str] = field(default_factory=dict)  # relative -> kind
    licences: list[dict[str, str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    redactions: int = 0
    redaction_details: list[dict[str, object]] = field(default_factory=list)
    sensor_bytes: int = 0


@dataclass(frozen=True)
class _SensorPlan:
    status: str
    reason: str | None
    policy: SensorPolicy
    adapter: str | None
    bytes: int
    scenes: tuple[dict[str, object], ...]
    full_scenes: dict[str, Scene]

    def document(self, max_bytes: int) -> dict[str, object]:
        return {
            "status": self.status,
            "reason": self.reason,
            "licence": self.policy.licence,
            "licence_url": self.policy.licence_url,
            "dataset_id": self.policy.dataset_id,
            "dataset_label": self.policy.dataset_label,
            "adapter": self.adapter,
            "bytes": self.bytes,
            "max_bytes": max_bytes,
            "scenes": [dict(row) for row in self.scenes],
        }


def _safe_scene_id(value: object) -> str:
    text = str(value)
    if not text or "/" in text or "\\" in text or text in {".", ".."}:
        raise BundleExportError(f"unsafe scene id {text!r}")
    return text


def _shown(label: str, relative: str) -> str:
    return f"{label}/{relative}" if label and relative not in {"", "."} else (relative or label)


def _plain_entry(path: Path, shown: str, *, directory: bool) -> os.stat_result:
    """``lstat`` one entry: a regular file, or a real directory when ``directory``."""
    try:
        status = os.lstat(path)
    except OSError as exc:
        raise BundleExportError(
            f"refusing to export: cannot read {shown} ({exc.strerror})"
        ) from None
    if stat.S_ISLNK(status.st_mode):
        raise BundleExportError(
            f"refusing to export: {shown} is a symbolic link. A bundle carries only "
            "regular files; replace the link with a copy of the file it points to."
        )
    if directory and stat.S_ISDIR(status.st_mode):
        return status
    if not stat.S_ISREG(status.st_mode):
        raise BundleExportError(f"refusing to export: {shown} is not a regular file")
    return status


def check_source_tree(root: Path, top: Path, label: str) -> None:
    """Refuse ``top`` (``root`` itself or a path under it) unless every entry is plain.

    ``root`` is the folder the user chose and may itself be reached through a
    link; nothing below it may be. Each component from ``root`` down to ``top``,
    and every entry ``os.walk(followlinks=False)`` finds under ``top``, must be a
    regular file or a real directory whose resolved path stays inside ``root``.
    """
    try:
        resolved_root = root.resolve(strict=True)
        relative = top.relative_to(root)
    except (OSError, ValueError):
        raise BundleExportError(f"refusing to export: {label} is not readable") from None
    current = root
    for part in relative.parts:
        current = current / part
        _plain_entry(current, _shown(label, current.relative_to(root).as_posix()), directory=True)

    def contained(path: Path, shown: str) -> None:
        try:
            inside = path.resolve(strict=True).is_relative_to(resolved_root)
        except OSError:
            inside = False
        if not inside:
            raise BundleExportError(f"refusing to export: {shown} resolves outside {label}")

    contained(top, _shown(label, relative.as_posix()))
    if not top.is_dir():
        return

    def walk_error(exc: OSError) -> None:
        raise BundleExportError(
            f"refusing to export: cannot list a folder under {label} ({exc.strerror})"
        )

    for dirpath, dirnames, filenames in os.walk(top, onerror=walk_error, followlinks=False):
        here = Path(dirpath)
        for name in sorted(dirnames) + sorted(filenames):
            entry = here / name
            shown = _shown(label, entry.relative_to(root).as_posix())
            _plain_entry(entry, shown, directory=True)
            contained(entry, shown)


def _open_regular(source: Path, shown: str) -> BinaryIO:
    """Open one checked file without following a link swapped in after the walk."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        descriptor = os.open(source, flags)
    except OSError as exc:
        raise BundleExportError(
            f"refusing to export: cannot open {shown} ({exc.strerror})"
        ) from None
    handle = os.fdopen(descriptor, "rb")
    if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
        handle.close()
        raise BundleExportError(f"refusing to export: {shown} is not a regular file")
    return handle


def _copy_regular(source: Path, destination: Path, shown: str) -> None:
    with _open_regular(source, shown) as handle:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.unlink(missing_ok=True)
        with destination.open("wb") as sink:
            shutil.copyfileobj(handle, sink, 1024 * 1024)


def _scene_directory(scenes_root: Path, scene_id: str) -> Path:
    candidate = scenes_root / scene_id
    try:
        candidate.resolve().relative_to(scenes_root.resolve())
    except (OSError, ValueError):
        raise BundleExportError(f"scene {scene_id!r} is outside the scenes root") from None
    if not (candidate / "manifest.json").is_file():
        raise BundleExportError(f"no scene {scene_id!r} under {scenes_root}")
    return candidate


def _safe_image_path(value: str) -> str:
    """A recorded image path that is also a safe file path inside the bundle."""
    try:
        return safe_member(value)
    except ValueError as exc:
        raise BundleExportError(f"scene image path cannot travel in a bundle: {exc}") from None


def _read_complete(scene_dir: Path) -> dict[str, object]:
    try:
        payload: object = json.loads((scene_dir / "COMPLETE.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return cast(dict[str, object], payload) if isinstance(payload, dict) else {}


def _scene_adapter(scene_dir: Path) -> str | None:
    value = _read_complete(scene_dir).get("adapter")
    return value if isinstance(value, str) and value else None


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
        for scene_dir in sorted(scenes_root.iterdir()):
            if scene_dir.name.startswith(".") or not (scene_dir / "manifest.json").is_file():
                continue
            check_source_tree(scenes_root, scene_dir, "scenes")
            scene = read_scene(scene_dir, points=False)
            times = sorted({a.time_us for a in scene.annotations})
            candidates.extend((scene.scene_id, time) for time in times)
    if spec.startswith("worst:"):
        return candidates[:count]
    out: list[tuple[str, int]] = []
    for part in spec.split(","):
        scene_id, _, time_text = part.partition("@")
        out.append((_safe_scene_id(scene_id), int(time_text or 0)))
    return out


def _frame_count(scene: Scene) -> int:
    return len({pose.time_us for agent in scene.agents for pose in agent.poses})


def _included_times(frames: list[tuple[str, int]], scene_id: str) -> list[int]:
    return sorted({time_us for candidate, time_us in frames if candidate == scene_id})


def _sensor_bytes(scene_dir: Path, scene: Scene, times: set[int]) -> int:
    total = 0
    for cloud in scene.point_clouds:
        if cloud.time_us in times:
            total += len(cloud.xyz_i_f32)
    for image in scene.images:
        if image.time_us not in times:
            continue
        relative = _safe_image_path(image.path)
        shown = f"scenes/{scene.scene_id}/{relative}"
        total += _plain_entry(scene_dir / relative, shown, directory=False).st_size
    return total


def _plan_sensor(request: BundleExportRequest, frames: list[tuple[str, int]]) -> _SensorPlan:
    """Decide what sensor data may travel, before anything is written."""
    if request.sensor_max_bytes < 0:
        raise BundleExportError("sensor_max_bytes must be zero or more")
    scene_ids: list[str] = []
    for scene_id, _time_us in frames:
        if scene_id not in scene_ids:
            scene_ids.append(scene_id)
    # Each scene proves itself: one demo scene must not carry another scene's
    # sensor bytes into the bundle. The first refusal is the recorded decision.
    policy: SensorPolicy | None = None
    for scene_id in scene_ids:
        complete = _read_complete(_scene_directory(request.scenes_root, scene_id))
        recorded_adapter = complete.get("adapter")
        generator = complete.get("generator")
        scene_policy = sensor_data_policy(
            request.dataset_id,
            recorded_adapter=recorded_adapter if isinstance(recorded_adapter, str) else None,
            generator=generator if isinstance(generator, str) else None,
        )
        if policy is None or (policy.allowed and not scene_policy.allowed):
            policy = scene_policy
    assert policy is not None
    adapter: str | None = None
    rows: list[dict[str, object]] = []
    full_scenes: dict[str, Scene] = {}
    total = 0
    want_sensor = policy.allowed and request.include_sensor_data
    for scene_id in scene_ids:
        scene_dir = _scene_directory(request.scenes_root, scene_id)
        if adapter is None:
            adapter = _scene_adapter(scene_dir) or policy.adapter
        times = set(_included_times(frames, scene_id))
        structure = read_scene(scene_dir, points=False)
        if want_sensor:
            scene = read_scene(scene_dir)
            full_scenes[scene_id] = scene
            total += _sensor_bytes(scene_dir, scene, times)
        else:
            scene = structure
        rows.append(
            {
                "scene_id": scene_id,
                "frame_count": _frame_count(scene),
                "frames_included": sorted(times),
            }
        )
    if want_sensor:
        if total > request.sensor_max_bytes:
            raise BundleExportError(
                f"the selected frames' sensor data is {total} bytes "
                f"({total / (1024 * 1024):.1f} MiB), over the {request.sensor_max_bytes}-byte "
                f"sensor-data cap ({request.sensor_max_bytes / (1024 * 1024):.1f} MiB). "
                "Choose fewer frames, or raise sensor_max_bytes from Python."
            )
        if total == 0:
            status = "omitted"
            reason = "the selected frames carry no LiDAR or camera bytes"
        else:
            status = "included"
            reason = "the dataset licence allows redistribution and the exporter opted in"
    elif not policy.allowed:
        status = "omitted"
        reason = policy.reason
    else:
        status = "omitted"
        reason = "the exporter did not opt in to sensor data"
    return _SensorPlan(
        status=status,
        reason=reason,
        policy=policy,
        adapter=adapter,
        bytes=total if status == "included" else 0,
        scenes=tuple(rows),
        full_scenes=full_scenes,
    )


def _write_scene(
    staging: _Staging,
    work: Path,
    scene_dir: Path,
    scene: Scene,
    times: set[int],
    *,
    include_sensor: bool,
) -> None:
    """Write the scene with only the included frames and only the allowed sensors."""
    filtered = replace(
        scene,
        agents=tuple(
            replace(agent, poses=tuple(pose for pose in agent.poses if pose.time_us in times))
            for agent in scene.agents
        ),
        annotations=tuple(ann for ann in scene.annotations if ann.time_us in times),
        point_clouds=(
            tuple(cloud for cloud in scene.point_clouds if cloud.time_us in times)
            if include_sensor
            else ()
        ),
        images=tuple(image for image in scene.images if image.time_us in times)
        if include_sensor
        else (),
    )
    target = work / "scenes" / scene.scene_id
    write_scene(filtered, target)
    complete = scene_dir / "COMPLETE.json"
    if os.path.lexists(complete):
        (target / "COMPLETE.json").unlink(missing_ok=True)
        _copy_regular(complete, target / "COMPLETE.json", f"scenes/{scene.scene_id}/COMPLETE.json")
    sensor_paths: set[str] = set()
    if include_sensor:
        for image in filtered.images:
            if image.time_us not in times:
                continue
            relative = _safe_image_path(image.path)
            shown = f"scenes/{scene.scene_id}/{relative}"
            # A recorded image that is missing refuses the export instead of
            # leaving a silent gap; _sensor_bytes has already lstat-ed it.
            _copy_regular(scene_dir / relative, target / relative, shown)
            sensor_paths.add(f"scenes/{scene.scene_id}/{relative}")
        sensor_paths.add(f"scenes/{scene.scene_id}/points.bin")
        sensor_paths.add(f"scenes/{scene.scene_id}/point_index.parquet")
    else:
        # A bundle without sensor data carries no points blob at all. The empty
        # point index that write_scene wrote would otherwise point at nothing.
        (target / "points.bin").unlink(missing_ok=True)
        (target / "point_index.parquet").unlink(missing_ok=True)
    for path in sorted(target.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(work).as_posix()
        if relative in sensor_paths:
            staging.files[relative] = (
                SENSOR_POINTS_KIND
                if relative.endswith(("points.bin", "point_index.parquet"))
                else SENSOR_IMAGE_KIND
            )
        else:
            staging.files[relative] = "scene-structure"


def _write_frame(
    staging: _Staging, work: Path, scene: Scene, scene_id: str, time_us: int, index: int
) -> None:
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


def _scrub_work(
    work: Path, markers: tuple[str, ...], roots: tuple[tuple[str, str], ...] = ()
) -> tuple[int, list[dict[str, object]]]:
    redactions = 0
    details: list[dict[str, object]] = []
    for path in work.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() == ".json":
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                before = path.read_text(encoding="utf-8", errors="replace")
                after_text = scrub_text(before, markers, roots)
                if after_text != before:
                    redactions += 1
                    details.append({"file": path.relative_to(work).as_posix(), "kind": "text"})
                path.write_text(after_text, encoding="utf-8")
                continue
            before = json.dumps(payload, sort_keys=True)
            after_doc = scrub_json(payload, markers, roots)
            after = json.dumps(after_doc, sort_keys=True)
            if after != before:
                redactions += 1
                details.append({"file": path.relative_to(work).as_posix(), "kind": "json"})
            path.write_text(json.dumps(after_doc, indent=2) + "\n", encoding="utf-8")
        elif path.suffix.lower() in {".txt", ".log", ".md", ".yaml", ".yml", ".csv"}:
            before = path.read_text(encoding="utf-8", errors="replace")
            after_text = scrub_text(before, markers, roots)
            if after_text != before:
                redactions += 1
                details.append({"file": path.relative_to(work).as_posix(), "kind": "text"})
            path.write_text(after_text, encoding="utf-8")
    import pyarrow as pa
    import pyarrow.parquet as pq

    for path in work.rglob("*.parquet"):
        table = pq.read_table(path)
        columns = []
        changed = False
        for name in table.column_names:
            column = table.column(name)
            if pa.types.is_string(column.type) or pa.types.is_large_string(column.type):
                values = [
                    scrub_text(value, markers, roots) if isinstance(value, str) else value
                    for value in column.to_pylist()
                ]
                if values != column.to_pylist():
                    changed = True
                columns.append(pa.array(values, type=column.type))
            else:
                columns.append(column)
        if changed:
            redactions += 1
            details.append({"file": path.relative_to(work).as_posix(), "kind": "parquet"})
            pq.write_table(pa.table(dict(zip(table.column_names, columns, strict=True))), path)
    return redactions, details


def _write_matches(source: Path, destination: Path, times: set[int], shown: str) -> None:
    """The run's ``matches.parquet`` rows at the included frames only."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    _plain_entry(source, shown, directory=False)
    with _open_regular(source, shown) as handle:
        table = pq.read_table(handle)
    if "time_us" not in table.column_names:
        raise BundleExportError(f"{shown} has no time_us column")
    keep = pa.array([value in times for value in table.column("time_us").to_pylist()])
    destination.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table.filter(keep), destination)


def _check_staged(work: Path, staging: _Staging) -> None:
    """Staging holds only regular files and real folders, and lists each file."""
    check_source_tree(work, work, "bundle")
    for relative in staging.files:
        _plain_entry(work / safe_member(relative), relative, directory=False)


def rerun_command(run_id: str) -> str:
    """The text the app shows with a copy button. It is never executed here."""
    return f"coperceive eval run {run_id} --root scenes --predictions predictions"


def export_bundle(request: BundleExportRequest) -> Path:
    """Write ``request.out`` and return its path. Every text file is scrubbed."""
    run_id = request.run_id
    if not run_id or "/" in run_id or "\\" in run_id or run_id in {".", ".."}:
        raise BundleExportError(f"unsafe run id {run_id!r}")
    run_dir = request.predictions_root / run_id
    if not os.path.lexists(run_dir):
        raise BundleExportError(f"no prediction run {run_id!r} under {request.predictions_root}")
    # FOLD-03: walk everything the export reads before reading any of it.
    check_source_tree(request.predictions_root, run_dir, "predictions")
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
    for scene_id in dict.fromkeys(scene_id for scene_id, _time_us in frames):
        candidate = request.scenes_root / scene_id
        if not os.path.lexists(candidate):
            raise BundleExportError(f"no scene {scene_id!r} under {request.scenes_root}")
        check_source_tree(request.scenes_root, candidate, "scenes")
    # The sensor decision (allow list, opt-in, cap) is made before any file is
    # written; an over-cap selection refuses the export rather than truncating it.
    sensor = _plan_sensor(request, frames)
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
        command = rerun_command(run_id)
        (work / "repro/rerun.txt").write_text(
            "Rerun the bundled evaluation (text only; Coperceive never runs it):\n"
            + command
            + "\n",
            encoding="utf-8",
        )
        staging.files["repro/rerun.txt"] = "rerun-command"
        # The config the run actually used, from its own report; never a guess.
        (work / "eval").mkdir()
        config_doc: object = "unknown"
        for scene_id, _time_us in frames:
            candidate = request.predictions_root / run_id / scene_id / "metrics.json"
            if candidate.is_file():
                try:
                    payload = json.loads(candidate.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                if isinstance(payload, dict) and isinstance(payload.get("config"), dict):
                    config_doc = payload["config"]
                    break
        (work / "eval/config.json").write_text(
            json.dumps(config_doc, indent=2) + "\n", encoding="utf-8"
        )
        staging.files["eval/config.json"] = "eval-config"
        for scene_id, _time_us in frames:
            metrics = request.predictions_root / run_id / scene_id / "metrics.json"
            rel = f"eval/{scene_id}/metrics.json"
            if os.path.lexists(metrics) and rel not in staging.files:
                shown = f"predictions/{run_id}/{scene_id}/metrics.json"
                _copy_regular(metrics, work / rel, shown)
                staging.files[rel] = "eval-metrics"
                rel_pred = f"predictions/{run_id}/{scene_id}/metrics.json"
                _copy_regular(metrics, work / rel_pred, shown)
                staging.files[rel_pred] = "predictions"
        # filtered predictions
        write_run(work / "predictions", run_id, meta, filtered, force=True)
        # The viewer draws a run's boxes from its matches, so the included
        # frames' rows travel too (a bundle without them shows no predictions).
        for scene_id in dict.fromkeys(scene_id for scene_id, _time_us in frames):
            source = run_dir / scene_id / MATCHES_FILE
            if os.path.lexists(source):
                _write_matches(
                    source,
                    work / "predictions" / run_id / scene_id / MATCHES_FILE,
                    set(_included_times(frames, scene_id)),
                    f"predictions/{run_id}/{scene_id}/{MATCHES_FILE}",
                )
        for path in (work / "predictions").rglob("*"):
            if path.is_file():
                staging.files[path.relative_to(work).as_posix()] = "predictions"
        if request.include_full_predictions:
            for name in ("run.json", BOXES_FILE):
                source = run_dir / name
                if os.path.lexists(source):
                    target = f"predictions-full/{name}"
                    _copy_regular(source, work / target, f"predictions/{run_id}/{name}")
                    staging.files[target] = "full-predictions"
        # scenes: structure, GT boxes and poses always; sensors only if allowed
        for index, (scene_id, time_us) in enumerate(frames):
            scene_dir = _scene_directory(request.scenes_root, scene_id)
            scene = sensor.full_scenes.get(scene_id) or read_scene(scene_dir, points=False)
            _write_scene(
                staging,
                work,
                scene_dir,
                scene,
                set(_included_times(frames, scene_id)),
                include_sensor=sensor.status == "included",
            )
            _write_frame(staging, work, scene, scene_id, time_us, index)
            if sensor.status == "included":
                staging.licences.append(
                    {
                        "dataset": str(sensor.policy.dataset_id or scene_id),
                        "licence": str(sensor.policy.licence or ""),
                        "note": "sensor data included under the redistributable-dataset allow list",
                    }
                )
        # Every staged file is checked like a source file before the scrubber
        # rewrites it (a link here would be written through) and before hashing.
        _check_staged(work, staging)
        staging.redactions, staging.redaction_details = _scrub_work(work, request.extra_markers)
        _check_staged(work, staging)
        # manifest over every file except itself
        contents: list[Content] = []
        for rel in sorted(staging.files):
            path = work / safe_member(rel)
            contents.append(
                Content(rel, sha256_file(path), path.stat().st_size, staging.files[rel])
            )
        staging.sensor_bytes = sum(
            item.size for item in contents if item.kind in {SENSOR_POINTS_KIND, SENSOR_IMAGE_KIND}
        )
        if sensor.status == "included" and staging.sensor_bytes > request.sensor_max_bytes:
            raise BundleExportError(
                f"sensor data is {staging.sensor_bytes} bytes after scrubbing, over the "
                f"{request.sensor_max_bytes}-byte sensor-data cap"
            )
        if sensor.status == "omitted" and staging.sensor_bytes:
            raise BundleExportError("sensor data was omitted but sensor files were staged")
        created = request.created_utc or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        title = scrub_text(request.title or f"{meta.run_id} results")
        licence_rows = [
            {key: scrub_text(value) for key, value in row.items()} for row in staging.licences
        ]
        sensor_document = sensor.document(request.sensor_max_bytes)
        sensor_document["bytes"] = staging.sensor_bytes
        document = manifest_document(
            title=title,
            app_version=__version__,
            created_utc=created,
            run_id=run_id,
            contents=contents,
            licences=licence_rows,
            privacy={
                "raw_sensor_data": sensor.status == "included",
                "sensor_data": sensor.status,
                "scrubbed": True,
                "redacted_files": staging.redactions,
                "redactions": staging.redaction_details,
            },
            sensor_data=sensor_document,
            rerun_command=command,
        )
        document["rerun_command"] = scrub_text(
            str(document.get("rerun_command", "")), request.extra_markers
        )
        sensor_doc = document.get("sensor_data")
        if isinstance(sensor_doc, dict):
            document["sensor_data"] = scrub_json(sensor_doc, request.extra_markers)
        (work / MANIFEST).write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
        _plain_entry(work / MANIFEST, MANIFEST, directory=False)
        # Exactly the manifest and the files it lists, nothing else in staging.
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            archive.write(work / MANIFEST, MANIFEST)
            for item in contents:
                archive.write(work / item.path, item.path)
    return out


def preview_bundle(request: BundleExportRequest) -> dict[str, object]:
    """The review manifest (files, sizes, total, scrub count) without keeping an archive."""
    temporary = request.out.parent / f".{request.out.name}.preview"
    request.out.parent.mkdir(parents=True, exist_ok=True)
    try:
        path = export_bundle(replace(request, out=temporary))
        with zipfile.ZipFile(path) as archive:
            manifest = json.loads(archive.read(MANIFEST))
        manifest["total_bytes"] = sum(int(item["size"]) for item in manifest["contents"])
        return manifest
    finally:
        temporary.unlink(missing_ok=True)
