# SPDX-License-Identifier: Apache-2.0
# pyright: basic
"""The hand-written Griffin reader and converter, frozen on 2026-10-17.

This is the code `coperceive convert griffin` ran before it became an alias
for the manifest adapter (ADR 0009). It stays here, unchanged, so
``tests/test_adapters_griffin.py`` compares the adapter against an independent
implementation rather than against itself. Do not fix or extend it; the
adapter and ``coperceive/adapters/builtin/griffin.yaml`` are the live code.

The integer ``timestamp`` in each pose file is the frame id. Microseconds are
``frame_id * 100_000`` because the paper states a 10 Hz capture.
"""

from __future__ import annotations

import json
import os
import shutil
from concurrent.futures import Future, ProcessPoolExecutor, as_completed
from dataclasses import dataclass, replace
from pathlib import Path

from coperceive.diagnostics import shared_objects
from coperceive.griffin.catalog import folder_for
from coperceive.model import (
    SCHEMA_VERSION,
    Agent,
    AgentType,
    Annotation,
    Clock,
    ImageRef,
    Modality,
    PointCloud,
    Scene,
    Sensor,
    StampedPose,
)
from coperceive.se3 import SE3
from coperceive.store import write_scene

_CLOCK_NOTE = (
    "Griffin pose 'timestamp' is the integer frame id, equal to the file stem. "
    "time_us = frame_id * 100000 assumes the 10 Hz rate stated for the lidar in "
    "the Griffin paper. The files do not contain a measured wall clock."
)
_SIDES = (("vehicle-side", "vehicle"), ("drone-side", "drone"))


def load_scene(release_root: Path, scene_name: str) -> Scene:
    """Load both agents of one scene.

    ``release_root`` contains ``vehicle-side`` and ``drone-side``.
    """
    vehicle_infos = release_root / "vehicle-side" / "scene_infos.json"
    drone_infos_path = release_root / "drone-side" / "scene_infos.json"
    infos = json.loads(vehicle_infos.read_text(encoding="utf-8"))
    drone_infos = json.loads(drone_infos_path.read_text(encoding="utf-8"))
    if infos != drone_infos:
        raise ValueError("vehicle-side and drone-side scene_infos.json differ")
    match = next((row for row in infos if row["name"] == scene_name), None)
    if match is None:
        raise KeyError(scene_name)
    frames = [int(frame) for frame in match["info"]["frames"]]
    agents: list[Agent] = []
    sensors: list[Sensor] = []
    annotations: list[Annotation] = []
    images: list[ImageRef] = []
    clouds: list[PointCloud] = []
    for side, agent_id in _SIDES:
        agents.append(_poses(release_root / side, agent_id, frames))
        side_sensors = _sensors(release_root / side, agent_id)
        sensors.extend(side_sensors)
        annotations.extend(_labels(release_root / side, agent_id, frames))
        images.extend(_image_refs(agent_id, frames, side_sensors))
        if agent_id == "vehicle":
            clouds.extend(_lidar(release_root / side, frames))
    return Scene(
        schema_version=SCHEMA_VERSION,
        scene_id=scene_name,
        world_frame="ENU",
        clock=Clock(unit="us", scale_note=_CLOCK_NOTE),
        agents=tuple(agents),
        sensors=tuple(sensors),
        annotations=tuple(annotations),
        objects=shared_objects(annotations),
        point_clouds=tuple(clouds),
        images=tuple(images),
    )


def parse_label_line(line: str, *, agent_id: str, time_us: int) -> Annotation:
    """Parse one Griffin label line into a body-frame box.

    Twelve fields are ``type x y z l w h roll pitch yaw id visibility``.
    Eleven fields omit visibility; it is stored as null, not as a filled-in
    1.0. Nine fields omit roll, pitch, and visibility; roll and pitch are 0
    and visibility is null. Lengths, widths, heights, and translations are
    metres. Angles are degrees. See ``docs/datasets/griffin.md``.
    """
    parts = line.split()
    if len(parts) == 12:
        roll, pitch, yaw = float(parts[7]), float(parts[8]), float(parts[9])
        track = str(int(float(parts[10])))
        visibility: float | None = float(parts[11])
    elif len(parts) == 11:
        roll, pitch, yaw = float(parts[7]), float(parts[8]), float(parts[9])
        track = str(int(float(parts[10])))
        visibility = None
    elif len(parts) == 9:
        roll, pitch = 0.0, 0.0
        yaw = float(parts[7])
        track = str(int(float(parts[8])))
        visibility = None
    else:
        raise ValueError(f"expected 12, 11, or 9 label fields, got {len(parts)}")
    return Annotation(
        agent_id=agent_id,
        time_us=time_us,
        track_id=track,
        category=parts[0],
        center=(float(parts[1]), float(parts[2]), float(parts[3])),
        size_lwh=(float(parts[4]), float(parts[5]), float(parts[6])),
        box_from_rpy=SE3.from_rpy_deg(roll, pitch, yaw),
        visibility=visibility,
    )


def parse_pose(payload: dict[str, object]) -> tuple[int, SE3]:
    frame = int(payload["timestamp"])  # type: ignore[arg-type]
    rotation = SE3.from_rpy_deg(
        float(payload["roll"]),  # type: ignore[arg-type]
        float(payload["pitch"]),  # type: ignore[arg-type]
        float(payload["yaw"]),  # type: ignore[arg-type]
    )
    pose = rotation.translation(
        float(payload["x"]),  # type: ignore[arg-type]
        float(payload["y"]),  # type: ignore[arg-type]
        float(payload["z"]),  # type: ignore[arg-type]
    )
    return frame, pose


def read_ascii_ply(path: Path) -> tuple[int, bytes]:
    """ASCII PLY with float properties x, y, z, I.

    Returns ``(count, little-endian x, y, z, intensity)``. The caller attaches
    the agent, sensor, and time.
    """
    text = path.read_text(encoding="utf-8")
    header, _, body = text.partition("end_header\n")
    count = 0
    for line in header.splitlines():
        if line.startswith("element vertex"):
            count = int(line.split()[-1])
    values: list[float] = []
    for line in body.splitlines():
        if not line.strip():
            continue
        values.extend(float(part) for part in line.split())
        if len(values) >= count * 4:
            break
    return count, _f32_bytes(values[: count * 4])


def _f32_bytes(values: list[float]) -> bytes:
    import struct

    return struct.pack("<" + "f" * len(values), *values)


def _poses(side: Path, agent_id: str, frames: list[int]) -> Agent:
    stamped: list[StampedPose] = []
    for frame in frames:
        payload = json.loads((side / "pose" / f"{frame:06d}.json").read_text(encoding="utf-8"))
        file_frame, pose = parse_pose(payload)
        if file_frame != frame:
            raise ValueError(f"{side} pose {frame} has timestamp {file_frame}")
        stamped.append(StampedPose(time_us=frame * 100_000, world_from_body=pose))
    agent_type = AgentType.UAV if agent_id == "drone" else AgentType.VEHICLE
    return Agent(agent_id=agent_id, agent_type=agent_type, poses=tuple(stamped))


def _sensors(side: Path, agent_id: str) -> list[Sensor]:
    sensors: list[Sensor] = []
    for path in sorted((side / "calib").glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        extrinsic = payload["extrinsic"]
        rotation = _rotation_from_rows(extrinsic)
        translation = SE3.identity().translation(
            float(extrinsic[0][3]), float(extrinsic[1][3]), float(extrinsic[2][3])
        )
        body_from_sensor = SE3(
            rotation.qw,
            rotation.qx,
            rotation.qy,
            rotation.qz,
            translation.tx,
            translation.ty,
            translation.tz,
        )
        intrinsic = payload.get("intrinsic")
        width = height = fx = fy = cx = cy = None
        modality = Modality.LIDAR if path.stem.startswith("lidar") else Modality.CAMERA
        if intrinsic is not None:
            fx = float(intrinsic[0][0])
            fy = float(intrinsic[1][1])
            cx = float(intrinsic[0][2])
            cy = float(intrinsic[1][2])
            width = int(round(cx * 2))
            height = int(round(cy * 2))
            modality = Modality.CAMERA
        sensors.append(
            Sensor(
                sensor_id=path.stem,
                agent_id=agent_id,
                modality=modality,
                body_from_sensor=body_from_sensor,
                image_width_px=width,
                image_height_px=height,
                fx=fx,
                fy=fy,
                cx=cx,
                cy=cy,
            )
        )
    return sensors


def _rotation_from_rows(matrix: list[list[float]]) -> SE3:
    rows = (
        (float(matrix[0][0]), float(matrix[0][1]), float(matrix[0][2])),
        (float(matrix[1][0]), float(matrix[1][1]), float(matrix[1][2])),
        (float(matrix[2][0]), float(matrix[2][1]), float(matrix[2][2])),
    )
    return SE3.from_matrix(rows)


def _labels(side: Path, agent_id: str, frames: list[int]) -> list[Annotation]:
    rows: list[Annotation] = []
    for frame in frames:
        text = (side / "label" / f"{frame:06d}.txt").read_text(encoding="utf-8")
        for line in text.splitlines():
            if line.strip():
                rows.append(parse_label_line(line, agent_id=agent_id, time_us=frame * 100_000))
    return rows


def _image_refs(agent_id: str, frames: list[int], sensors: list[Sensor]) -> list[ImageRef]:
    """Path refs for camera calibs that were actually read. Bytes stay outside the store."""
    side = "drone-side" if agent_id == "drone" else "vehicle-side"
    refs: list[ImageRef] = []
    for sensor in sensors:
        if sensor.modality is not Modality.CAMERA:
            continue
        for frame in frames:
            refs.append(
                ImageRef(
                    agent_id=agent_id,
                    sensor_id=sensor.sensor_id,
                    time_us=frame * 100_000,
                    path=f"{side}/camera/{sensor.sensor_id}/{frame:06d}.png",
                )
            )
    return refs


def _lidar(side: Path, frames: list[int]) -> list[PointCloud]:
    clouds: list[PointCloud] = []
    lidar = side / "lidar" / "lidar_top"
    if not lidar.is_dir():
        return clouds
    for frame in frames:
        path = lidar / f"{frame:06d}.ply"
        if not path.exists():
            continue
        count, blob = read_ascii_ply(path)
        clouds.append(
            PointCloud(
                agent_id="vehicle",
                sensor_id="lidar_top",
                time_us=frame * 100_000,
                count=count,
                xyz_i_f32=blob,
            )
        )
    return clouds


# --- the frozen converter -----------------------------------------------------

_COMPLETE = "COMPLETE.json"


class ReferenceConvertError(RuntimeError):
    """One or more scenes failed. Successful scenes stay on disk."""

    def __init__(self, report: ReferenceReport) -> None:
        self.report = report
        super().__init__("\n".join(report.errors))


@dataclass(frozen=True)
class ReferenceReport:
    written: tuple[str, ...]
    skipped: tuple[str, ...]
    resumed: tuple[str, ...]
    errors: tuple[str, ...]
    frame_counts: tuple[tuple[str, int], ...]
    label_fields: tuple[tuple[int, int], ...]


@dataclass(frozen=True)
class _Job:
    raw_root: str
    out: str
    scene_id: str
    frames: tuple[int, ...]
    subset: str
    map_name: str
    cruise_altitude_m: str


@dataclass(frozen=True)
class _SceneResult:
    scene_id: str
    status: str
    frames: int
    label_fields: tuple[tuple[int, int], ...]


def default_workers() -> int:
    count = os.cpu_count() or 1
    return max(1, min(8, count))


def convert_reference(
    raw_root: Path,
    out: Path,
    *,
    subset: str,
    workers: int | None = None,
) -> ReferenceReport:
    """The pre-adapter converter, frozen. Writes one directory per scene under ``out``."""
    folder_for(subset)
    jobs = _jobs(raw_root, out, subset)
    pool = workers if workers is not None else default_workers()
    if pool < 1:
        raise ValueError("workers must be >= 1")
    results: list[_SceneResult] = []
    errors: list[str] = []
    if pool == 1 or len(jobs) <= 1:
        for job in jobs:
            _collect(job, results, errors)
    else:
        with ProcessPoolExecutor(max_workers=pool) as executor:
            futures = {executor.submit(convert_one, job): job for job in jobs}
            for future in as_completed(futures):
                _collect_future(futures[future], future, results, errors)
    report = _report(results, errors)
    if errors:
        raise ReferenceConvertError(report)
    return report


def convert_one(job: _Job) -> _SceneResult:
    """Convert a single scene. Safe to run in a worker process."""
    raw_root = Path(job.raw_root)
    out = Path(job.out)
    final = out / job.scene_id
    partial = out / ".partial" / job.scene_id
    expected = _expected(job)
    if _is_complete(final, expected):
        return _SceneResult(job.scene_id, "skipped", len(job.frames), _stored_fields(final))
    if final.exists():
        shutil.rmtree(final)
    if _is_complete(partial, expected):
        partial.rename(final)
        return _SceneResult(job.scene_id, "resumed", len(job.frames), _stored_fields(final))
    if partial.exists():
        shutil.rmtree(partial)
    partial.parent.mkdir(parents=True, exist_ok=True)
    partial.mkdir()
    try:
        counts = _label_counts(raw_root, job.frames)
        _require_frames(raw_root, job.frames)
        scene = _with_platform(
            load_scene(raw_root, job.scene_id),
            subset=job.subset,
            map_name=job.map_name,
            cruise=job.cruise_altitude_m,
        )
        _require_lidar(raw_root, job.frames, scene)
        write_scene(scene, partial)
        (partial / _COMPLETE).write_text(_complete_text(expected, counts), encoding="utf-8")
        partial.rename(final)
    except Exception:
        shutil.rmtree(partial, ignore_errors=True)
        raise
    return _SceneResult(
        job.scene_id,
        "wrote",
        len(job.frames),
        tuple(sorted(counts.items())),
    )


def format_report(report: ReferenceReport) -> str:
    lines = [
        f"wrote={len(report.written)} skipped={len(report.skipped)} resumed={len(report.resumed)}",
    ]
    histogram: dict[int, int] = {}
    for _scene_id, count in report.frame_counts:
        histogram[count] = histogram.get(count, 0) + 1
    for count in sorted(histogram):
        lines.append(f"frames {count}: {histogram[count]} scenes")
    if report.label_fields:
        rendered = ", ".join(f"{width}:{total}" for width, total in report.label_fields)
        lines.append(f"label_fields {rendered}")
    for scene_id, count in report.frame_counts:
        lines.append(f"{count}\t{scene_id}")
    for error in report.errors:
        lines.append(f"error\t{error}")
    return "\n".join(lines)


def _jobs(raw_root: Path, out: Path, subset: str) -> list[_Job]:
    infos = json.loads((raw_root / "vehicle-side" / "scene_infos.json").read_text(encoding="utf-8"))
    drone = json.loads((raw_root / "drone-side" / "scene_infos.json").read_text(encoding="utf-8"))
    if infos != drone:
        raise ValueError("vehicle-side and drone-side scene_infos.json differ")
    jobs: list[_Job] = []
    for row in infos:
        scene_id = str(row["name"])
        _check_scene_id(scene_id)
        info = row["info"]
        frames = tuple(int(frame) for frame in info["frames"])
        if not frames:
            raise ValueError(f"{scene_id} lists no frames")
        cruise = info["cruise_altitude"]
        if isinstance(cruise, bool) or not isinstance(cruise, (int, float)):
            raise ValueError(f"{scene_id} cruise_altitude is not a number")
        jobs.append(
            _Job(
                raw_root=str(raw_root),
                out=str(out),
                scene_id=scene_id,
                frames=frames,
                subset=subset,
                map_name=str(info["map_name"]),
                cruise_altitude_m=format(float(cruise), ".10g"),
            )
        )
    return jobs


def _check_scene_id(scene_id: str) -> None:
    if not scene_id or scene_id in {".", ".."} or scene_id.startswith("."):
        raise ValueError(f"refusing scene id {scene_id!r}")
    if "/" in scene_id or "\\" in scene_id:
        raise ValueError(f"refusing scene id {scene_id!r}")


def _collect(job: _Job, results: list[_SceneResult], errors: list[str]) -> None:
    try:
        results.append(convert_one(job))
    except Exception as exc:
        errors.append(f"{job.scene_id}: {type(exc).__name__}: {exc}")


def _collect_future(
    job: _Job,
    future: Future[_SceneResult],
    results: list[_SceneResult],
    errors: list[str],
) -> None:
    try:
        results.append(future.result())
    except Exception as exc:
        errors.append(f"{job.scene_id}: {type(exc).__name__}: {exc}")


def _report(results: list[_SceneResult], errors: list[str]) -> ReferenceReport:
    results.sort(key=lambda item: item.scene_id)
    fields: dict[int, int] = {}
    for result in results:
        for width, count in result.label_fields:
            fields[width] = fields.get(width, 0) + count
    return ReferenceReport(
        written=tuple(item.scene_id for item in results if item.status == "wrote"),
        skipped=tuple(item.scene_id for item in results if item.status == "skipped"),
        resumed=tuple(item.scene_id for item in results if item.status == "resumed"),
        errors=tuple(errors),
        frame_counts=tuple((item.scene_id, item.frames) for item in results),
        label_fields=tuple(sorted(fields.items())),
    )


def _expected(job: _Job) -> dict[str, object]:
    return {
        "scene_id": job.scene_id,
        "schema_version": SCHEMA_VERSION,
        "frames": list(job.frames),
    }


def _complete_text(expected: dict[str, object], counts: dict[int, int]) -> str:
    payload = dict(expected)
    payload["label_field_counts"] = {str(width): count for width, count in sorted(counts.items())}
    return json.dumps(payload, indent=2) + "\n"


def _is_complete(directory: Path, expected: dict[str, object]) -> bool:
    path = directory / _COMPLETE
    if not path.is_file():
        return False
    try:
        got = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return (
        got.get("scene_id") == expected["scene_id"]
        and got.get("schema_version") == expected["schema_version"]
        and got.get("frames") == expected["frames"]
    )


def _stored_fields(directory: Path) -> tuple[tuple[int, int], ...]:
    try:
        payload = json.loads((directory / _COMPLETE).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ()
    raw = payload.get("label_field_counts", {})
    if not isinstance(raw, dict):
        return ()
    rows: list[tuple[int, int]] = []
    for key, value in raw.items():
        rows.append((int(key), int(value)))
    return tuple(sorted(rows))


def _label_counts(raw_root: Path, frames: tuple[int, ...]) -> dict[int, int]:
    counts: dict[int, int] = {}
    for side in ("vehicle-side", "drone-side"):
        for frame in frames:
            text = (raw_root / side / "label" / f"{frame:06d}.txt").read_text(encoding="utf-8")
            for line in text.splitlines():
                if not line.strip():
                    continue
                width = len(line.split())
                counts[width] = counts.get(width, 0) + 1
    return counts


def _require_frames(raw_root: Path, frames: tuple[int, ...]) -> None:
    for side in ("vehicle-side", "drone-side"):
        for frame in frames:
            pose = raw_root / side / "pose" / f"{frame:06d}.json"
            label = raw_root / side / "label" / f"{frame:06d}.txt"
            if not pose.is_file():
                raise FileNotFoundError(pose)
            if not label.is_file():
                raise FileNotFoundError(label)


def _require_lidar(raw_root: Path, frames: tuple[int, ...], scene: Scene) -> None:
    lidar = raw_root / "vehicle-side" / "lidar" / "lidar_top"
    if not lidar.is_dir():
        return
    expected = {frame * 100_000 for frame in frames}
    found = {cloud.time_us for cloud in scene.point_clouds}
    missing = [frame for frame in frames if frame * 100_000 not in found]
    if missing or found != expected:
        first = missing[0] if missing else "count"
        raise ValueError(f"vehicle lidar is missing {len(missing)} frames; first {first}")


def _with_platform(scene: Scene, *, subset: str, map_name: str, cruise: str) -> Scene:
    platform = (
        ("cruise_altitude_m", cruise),
        ("map_name", map_name),
        ("subset", subset),
    )
    agents = tuple(
        Agent(
            agent_id=agent.agent_id,
            agent_type=agent.agent_type,
            poses=agent.poses,
            platform=platform,
        )
        for agent in scene.agents
    )
    return replace(scene, agents=agents)
