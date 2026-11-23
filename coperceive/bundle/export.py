# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownParameterType=false, reportMissingParameterType=false, reportArgumentType=false, reportPrivateUsage=false, reportIndexIssue=false, reportAttributeAccessIssue=false, reportOptionalMemberAccess=false, reportGeneralTypeIssues=false, reportUnusedFunction=false
"""Export one run as a shareable ``.cpbundle``.

The bundle always carries the scores, the boxes (ground truth for the included
frames plus the run's predictions), the poses, the repro card and the rerun
command as text. LiDAR points and camera images are only carried when the
dataset is on the redistributable allow list (see :mod:`coperceive.bundle.sensor`),
the exporter opts in, and the selected frames fit the sensor-data cap.

Nothing is read through a link (FOLD-03). The run folder and each exported scene
folder are walked first with ``os.walk(followlinks=False)``; each entry is
``lstat``-ed, and a symbolic link (to anything, including a file in the same
scene), a hard link, a FIFO, a device or a socket refuses the **whole** export
with an error that names it. The walk records each entry's device and inode.
Every source file is then read only through :class:`_Tree.open`, which opens it
with ``O_NOFOLLOW | O_NONBLOCK`` and refuses it unless ``fstat`` shows the same
device and inode, one name, and a regular file: a file or a parent folder
swapped after the walk is refused, not read. Scene tables and the run's files
are copied that way into a private snapshot, and everything else (the scene
reader, the repro card) reads the snapshot. The staged files pass the walk
check again before they are scrubbed, hashed and zipped.

What this does not close: a path swapped after the walk is still *opened*
before ``fstat`` can refuse it. ``O_NONBLOCK`` keeps a FIFO from blocking that
open, but opening a device node can have side effects of its own. Nothing from
such an open is read.
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
from coperceive.camera_images import RAW_LINK
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
    if status.st_nlink > 1:
        raise BundleExportError(f"refusing to export: {shown} is a hard link (it has other names)")
    return status


#: What the walk saw for one entry: device, inode, and whether it is a folder.
_Entry = tuple[int, int, bool]


def check_source_tree(
    root: Path,
    top: Path,
    label: str,
    *,
    left_out: str | None = None,
    omitted: list[str] | None = None,
) -> dict[str, _Entry]:
    """Refuse ``top`` (``root`` itself or a path under it) unless every entry is plain.

    ``root`` is the folder the user chose and may itself be reached through a
    link; nothing below it may be. Each component from ``root`` down to ``top``,
    and every entry ``os.walk(followlinks=False)`` finds under ``top``, must be a
    regular file with one name or a real directory whose resolved path stays
    inside ``root``. Returns what was seen, keyed by the path relative to ``root``.

    ``left_out`` names the one entry (relative to ``root``) that must instead be a
    symbolic link: a prepared cluster scene's camera link. It is appended to
    ``omitted`` and never followed, resolved, recorded or descended into; a real
    file or folder there is refused.
    """
    seen: dict[str, _Entry] = {}

    def record(relative_path: str, status: os.stat_result) -> None:
        seen[relative_path] = (status.st_dev, status.st_ino, stat.S_ISDIR(status.st_mode))

    try:
        resolved_root = root.resolve(strict=True)
        relative = top.relative_to(root)
    except (OSError, ValueError):
        raise BundleExportError(f"refusing to export: {label} is not readable") from None
    current = root
    for part in relative.parts:
        current = current / part
        key = current.relative_to(root).as_posix()
        record(key, _plain_entry(current, _shown(label, key), directory=True))

    def contained(path: Path, shown: str) -> None:
        try:
            inside = path.resolve(strict=True).is_relative_to(resolved_root)
        except OSError:
            inside = False
        if not inside:
            raise BundleExportError(f"refusing to export: {shown} resolves outside {label}")

    contained(top, _shown(label, relative.as_posix()))
    if not top.is_dir():
        return seen

    def walk_error(exc: OSError) -> None:
        raise BundleExportError(
            f"refusing to export: cannot list a folder under {label} ({exc.strerror})"
        )

    for dirpath, dirnames, filenames in os.walk(top, onerror=walk_error, followlinks=False):
        here = Path(dirpath)
        for name in sorted(dirnames) + sorted(filenames):
            entry = here / name
            key = entry.relative_to(root).as_posix()
            shown = _shown(label, key)
            if key == left_out:
                _left_out_link(entry, shown)
                if omitted is not None:
                    omitted.append(key)
                continue
            record(key, _plain_entry(entry, shown, directory=True))
            contained(entry, shown)
    return seen


def _left_out_link(entry: Path, shown: str) -> None:
    """The raw camera link is left out only if ``lstat`` says it is a link."""
    try:
        status = os.lstat(entry)
    except OSError as exc:
        raise BundleExportError(
            f"refusing to export: cannot read {shown} ({exc.strerror})"
        ) from None
    if not stat.S_ISLNK(status.st_mode):
        raise BundleExportError(
            f"refusing to export: {shown} is not the link to the raw camera folder that "
            "a prepared cluster scene has there; only that link is left out of a bundle"
        )


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
    status = os.fstat(handle.fileno())
    if not stat.S_ISREG(status.st_mode):
        handle.close()
        raise BundleExportError(f"refusing to export: {shown} is not a regular file")
    if status.st_nlink > 1:
        handle.close()
        raise BundleExportError(f"refusing to export: {shown} is a hard link (it has other names)")
    return handle


@dataclass
class _Tree:
    """One source folder (the scenes root or the predictions root) and what its walk saw."""

    root: Path
    label: str
    seen: dict[str, _Entry] = field(default_factory=dict)
    #: Camera links left out of the bundle, relative to ``root``. Never followed.
    omitted: list[str] = field(default_factory=list)

    def walk(self, relative: str) -> None:
        self.seen.update(check_source_tree(self.root, self.root / relative, self.label))

    def walk_scene(self, scene_id: str) -> None:
        """Walk one exported scene; its ``camera_images.RAW_LINK`` link is left out."""
        self.seen.update(
            check_source_tree(
                self.root,
                self.root / scene_id,
                self.label,
                left_out=f"{scene_id}/{RAW_LINK}",
                omitted=self.omitted,
            )
        )

    def files_under(self, relative: str) -> list[str]:
        prefix = relative.rstrip("/") + "/"
        return sorted(
            key for key, entry in self.seen.items() if key.startswith(prefix) and not entry[2]
        )

    def open(self, relative: str) -> BinaryIO:
        """The walked file at ``relative``, refused unless it is still that file."""
        shown = _shown(self.label, relative)
        entry = self.seen.get(relative)
        if entry is None or entry[2]:
            raise BundleExportError(f"refusing to export: {shown} was not checked before reading")
        handle = _open_regular(self.root / relative, shown)
        status = os.fstat(handle.fileno())
        if (status.st_dev, status.st_ino) != entry[:2]:
            handle.close()
            raise BundleExportError(
                f"refusing to export: {shown} changed after it was checked "
                "(a file or folder on its path was replaced)"
            )
        return handle

    def copy(self, relative: str, destination: Path) -> None:
        with self.open(relative) as handle:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.unlink(missing_ok=True)
            with destination.open("wb") as sink:
                shutil.copyfileobj(handle, sink, 1024 * 1024)


@dataclass
class _Sources:
    """Checked reads of the folders an export uses, through a private snapshot.

    ``snapshot`` mirrors the two roots: ``snapshot/scenes/<scene>/`` holds the
    scene's top-level tables (``points.bin`` only when asked for) and
    ``snapshot/predictions/<run>/`` the whole run folder, each copied through
    :meth:`_Tree.open`. Readers that take a path (``read_scene``, the repro card)
    are given the snapshot, never the source.
    """

    scenes: _Tree
    predictions: _Tree
    snapshot: Path
    _copied: set[str] = field(default_factory=set)

    @property
    def scenes_root(self) -> Path:
        return self.snapshot / "scenes"

    @property
    def predictions_root(self) -> Path:
        return self.snapshot / "predictions"

    def _take(self, tree: _Tree, relative: str) -> None:
        key = f"{tree.label}/{relative}"
        if key not in self._copied:
            tree.copy(relative, self.snapshot / tree.label / relative)
            self._copied.add(key)

    def run(self, run_id: str) -> Path:
        self.predictions.walk(run_id)
        for relative in self.predictions.files_under(run_id):
            self._take(self.predictions, relative)
        (self.predictions_root / run_id).mkdir(parents=True, exist_ok=True)
        return self.predictions_root / run_id

    def scene(self, scene_id: str, *, points: bool = False) -> Path:
        """The snapshot of one walked scene's tables (and its points blob with ``points``)."""
        for relative in self.scenes.files_under(scene_id):
            name = relative[len(scene_id) + 1 :]
            if "/" in name or (name == "points.bin" and not points):
                continue
            self._take(self.scenes, relative)
        directory = self.scenes_root / scene_id
        if not (directory / "manifest.json").is_file():
            raise BundleExportError(f"no scene {scene_id!r} under {self.scenes.root}")
        return directory


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


def _ranked_frames(run_dir: Path, scenes_root: Path, run_id: str) -> list[tuple[str, int]]:
    """The run's frames, worst first, ranked as ``coperceive eval failures`` ranks them.

    Choosing frames is not exporting: this reads the run's matches from its
    checked snapshot and, for distance, only the scenes the run has predictions
    for, with ``read_scene`` and no walk. A scene that cannot be read is left
    out of the ranking; the scenes finally chosen are walked and read through
    the checked snapshot like any other export.
    """
    from coperceive.errors import CoperceiveError
    from coperceive.evaluation.evaluate import METRICS_FILE
    from coperceive.evaluation.failures import mine_failures
    from coperceive.evaluation.outcomes import OutcomeRecord, match_rows, records_from_rows

    records: list[OutcomeRecord] = []
    for scene_dir in sorted(p for p in run_dir.iterdir() if p.is_dir()):
        scene_id = _safe_scene_id(scene_dir.name)
        if not (scene_dir / METRICS_FILE).is_file() or not (scene_dir / MATCHES_FILE).is_file():
            continue
        try:
            scene = read_scene(scenes_root / scene_id, points=False)
        except (OSError, CoperceiveError, ValueError):
            continue
        records.extend(
            records_from_rows(scene, match_rows(scene_dir), run_id=run_id, scene_id=scene_id)
        )
    if not records:
        return []
    document = mine_failures(records, run_id=run_id, top=len(records))
    ranked: list[tuple[str, int]] = []
    for frame in cast(list[dict[str, object]], document["frames"]):
        key = (_safe_scene_id(frame["scene_id"]), int(cast(int, frame["time_us"])))
        if key not in ranked:
            ranked.append(key)
    return ranked


def _choose_frames(
    run_dir: Path, spec: str, boxes: list[PredictionBox], scenes_root: Path, run_id: str
) -> list[tuple[str, int]]:
    """The frames to export.

    ``worst:N`` takes the first N of the run's ``failures.json`` ranking or, when
    the run has none, of the same ranking computed here (see :func:`_ranked_frames`),
    then the run's other predicted frames. Otherwise ``spec`` is an explicit
    ``scene@time_us,...`` list, parsed without reading anything.
    """
    if not spec.startswith("worst:"):
        out: list[tuple[str, int]] = []
        for part in spec.split(","):
            scene_id, _, time_text = part.partition("@")
            try:
                time_us = int(time_text or 0)
            except ValueError:
                raise BundleExportError("frames must be worst:N or scene@time_us,...") from None
            out.append((_safe_scene_id(scene_id), time_us))
        return out
    try:
        count = int(spec.split(":", 1)[1])
    except ValueError:
        raise BundleExportError("frames must be worst:N or a comma-separated list") from None
    candidates: list[tuple[str, int]] = []
    failures = run_dir / "failures.json"
    if failures.is_file():
        payload = json.loads(failures.read_text(encoding="utf-8"))
        for item in cast(list[dict[str, object]], payload.get("frames", [])):
            candidates.append((_safe_scene_id(item.get("scene_id")), int(item.get("time_us", 0))))
    if not candidates:
        candidates = _ranked_frames(run_dir, scenes_root, run_id)
        # Frames the ranking scores as clean still count, after the bad ones.
        for key in sorted({(_safe_scene_id(box.scene_id), box.time_us) for box in boxes}):
            if key not in candidates:
                candidates.append(key)
    return candidates[:count]


def _frame_count(scene: Scene) -> int:
    return len({pose.time_us for agent in scene.agents for pose in agent.poses})


def _included_times(frames: list[tuple[str, int]], scene_id: str) -> list[int]:
    return sorted({time_us for candidate, time_us in frames if candidate == scene_id})


def _sensor_bytes(scene_dir: Path, scene: Scene, times: set[int]) -> int:
    """LiDAR and camera bytes at ``times``; image sizes come from an ``lstat`` of each source."""
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


def _plan_sensor(
    request: BundleExportRequest, frames: list[tuple[str, int]], sources: _Sources
) -> _SensorPlan:
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
        complete = _read_complete(sources.scene(scene_id))
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
    if want_sensor and sources.scenes.omitted:
        # The camera link's images would be sensor data read through the link.
        raise BundleExportError(
            f"refusing to export: scenes/{sources.scenes.omitted[0]} is the link to the raw "
            "camera folder; it is left out only when the bundle carries no sensor data. "
            "Export without sensor data."
        )
    for scene_id in scene_ids:
        snapshot = sources.scene(scene_id, points=want_sensor)
        if adapter is None:
            adapter = _scene_adapter(snapshot) or policy.adapter
        times = set(_included_times(frames, scene_id))
        structure = read_scene(snapshot, points=False)
        if want_sensor:
            scene = read_scene(snapshot)
            full_scenes[scene_id] = scene
            total += _sensor_bytes(request.scenes_root / scene_id, scene, times)
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
    sources: _Sources,
    scene_id: str,
    scene: Scene,
    times: set[int],
    *,
    include_sensor: bool,
) -> None:
    """Write the scene with only the included frames and only the allowed sensors."""
    # The staged folder is named from the manifest's scene id, which comes from
    # someone else's folder: it must be the plain folder name the run refers to.
    if scene.scene_id != _safe_scene_id(scene_id):
        raise BundleExportError(
            f"refusing to export: scenes/{scene_id}/manifest.json records scene id "
            f"{scene.scene_id!r}, not {scene_id!r}"
        )
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
    complete = sources.scene(scene_id) / "COMPLETE.json"
    if complete.is_file():
        (target / "COMPLETE.json").unlink(missing_ok=True)
        shutil.copyfile(complete, target / "COMPLETE.json")
    sensor_paths: set[str] = set()
    if include_sensor:
        for image in filtered.images:
            if image.time_us not in times:
                continue
            relative = _safe_image_path(image.path)
            # A recorded image that is missing refuses the export instead of
            # leaving a silent gap: it was never walked, so it cannot be opened.
            sources.scenes.copy(f"{scene_id}/{relative}", target / relative)
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
    if not os.path.lexists(request.predictions_root / run_id):
        raise BundleExportError(f"no prediction run {run_id!r} under {request.predictions_root}")
    with tempfile.TemporaryDirectory(prefix="coperceive-bundle-src-") as raw_sources:
        sources = _Sources(
            scenes=_Tree(request.scenes_root, "scenes"),
            predictions=_Tree(request.predictions_root, "predictions"),
            snapshot=Path(raw_sources),
        )
        return _export(request, sources)


def _export(request: BundleExportRequest, sources: _Sources) -> Path:
    run_id = request.run_id
    # FOLD-03: the run folder is walked, then read only from its checked snapshot.
    run_dir = sources.run(run_id)
    meta = read_run_meta(sources.predictions_root, run_id)
    box_rows: list[PredictionBox] = []
    for scene_dir in sorted(p for p in run_dir.iterdir() if p.is_dir()):
        box_rows.extend(read_boxes(sources.predictions_root, run_id, scene_dir.name))
    frames = _choose_frames(run_dir, request.frames, box_rows, request.scenes_root, run_id)
    if not frames:
        raise BundleExportError("no frames selected")
    wanted = set(frames)
    filtered = [box for box in box_rows if (box.scene_id, box.time_us) in wanted]
    if not filtered:
        raise BundleExportError("the selected frames have no predictions")
    # Only the exported scenes are walked; a link in another scene is not our business.
    for scene_id in dict.fromkeys(scene_id for scene_id, _time_us in frames):
        if not os.path.lexists(request.scenes_root / scene_id):
            raise BundleExportError(f"no scene {scene_id!r} under {request.scenes_root}")
        sources.scenes.walk_scene(scene_id)
    if os.path.lexists(request.scenes_root / "dataset.json"):
        # The repro card records the dataset manifest's hash.
        sources.scenes.walk("dataset.json")
        sources.scenes.copy("dataset.json", sources.scenes_root / "dataset.json")
    # The sensor decision (allow list, opt-in, cap) is made before any file is
    # written; an over-cap selection refuses the export rather than truncating it.
    sensor = _plan_sensor(request, frames, sources)
    out = request.out
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="coperceive-bundle-") as raw_work:
        work = Path(raw_work)
        staging = _Staging()
        # repro card
        card = export_card(
            ExportRequest(
                run_id=run_id,
                scenes_root=sources.scenes_root,
                predictions_root=sources.predictions_root,
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
            candidate = run_dir / scene_id / "metrics.json"
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
            metrics = run_dir / scene_id / "metrics.json"
            rel = f"eval/{scene_id}/metrics.json"
            if metrics.is_file() and rel not in staging.files:
                (work / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(metrics, work / rel)
                staging.files[rel] = "eval-metrics"
                rel_pred = f"predictions/{run_id}/{scene_id}/metrics.json"
                (work / rel_pred).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(metrics, work / rel_pred)
                staging.files[rel_pred] = "predictions"
        # filtered predictions
        write_run(work / "predictions", run_id, meta, filtered, force=True)
        # The viewer draws a run's boxes from its matches, so the included
        # frames' rows travel too (a bundle without them shows no predictions).
        for scene_id in dict.fromkeys(scene_id for scene_id, _time_us in frames):
            source = run_dir / scene_id / MATCHES_FILE
            if source.is_file():
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
                if source.is_file():
                    target = f"predictions-full/{name}"
                    (work / target).parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, work / target)
                    staging.files[target] = "full-predictions"
        # scenes: structure, GT boxes and poses always; sensors only if allowed
        for index, (scene_id, time_us) in enumerate(frames):
            scene = sensor.full_scenes.get(scene_id) or read_scene(
                sources.scene(scene_id), points=False
            )
            _write_scene(
                staging,
                work,
                sources,
                scene_id,
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
            # Left out, never followed: the path in the bundle, never the target.
            omitted_links=[f"scenes/{key}" for key in sorted(sources.scenes.omitted)],
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
