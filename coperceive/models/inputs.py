# SPDX-License-Identifier: Apache-2.0
"""What the parent sends with a ``frame``: poses, image paths, extracted sweeps, raw files.

Every path the child receives is resolved through ``confine`` against the scene directory,
a media root or the raw root, as the image route does. Sweeps are written as little-endian
float32 ``x, y, z, intensity`` under ``<work_dir>/inputs/<frame id>/``.
"""

from __future__ import annotations

import fnmatch
import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from coperceive.adapters.paths import PathError, confine
from coperceive.errors import CoperceiveError, TimeQueryError
from coperceive.model import Modality, PointCloud, Scene, Sensor
from coperceive.models.protocol import Frame, ImageInput, PointsInput
from coperceive.models.spec import ModelSpec
from coperceive.robustness.apply import AgentDecision, decide
from coperceive.robustness.spec import PerturbationSpec
from coperceive.se3 import SE3
from coperceive.transform import lookup_pose
from coperceive.viewdata import image_path

POINT_LAYOUT = "xyzi_f32le"


class InputError(CoperceiveError):
    """A raw template could not be expanded, or a path leaves its root."""


@dataclass(frozen=True)
class InputFile:
    """One file a frame referenced; the cache records these (spec §Cache ``inputs``)."""

    relative: str
    absolute: Path
    size: int
    mtime_ns: int


def pose_json(pose: SE3) -> dict[str, float]:
    return {
        "qw": pose.qw,
        "qx": pose.qx,
        "qy": pose.qy,
        "qz": pose.qz,
        "tx": pose.tx,
        "ty": pose.ty,
        "tz": pose.tz,
    }


def sensor_json(sensor: Sensor) -> dict[str, object]:
    out: dict[str, object] = {
        "agent_id": sensor.agent_id,
        "sensor_id": sensor.sensor_id,
        "modality": sensor.modality.value,
        "body_from_sensor": pose_json(sensor.body_from_sensor),
    }
    for key in ("image_width_px", "image_height_px", "fx", "fy", "cx", "cy"):
        value = getattr(sensor, key)
        if value is not None:
            out[key] = value
    return out


def selected_sensors(scene: Scene, spec: ModelSpec, agent_ids: Sequence[str]) -> tuple[Sensor, ...]:
    """The requested agents' sensors of the model's modalities whose ids match §Inputs globs."""
    wanted = set(agent_ids)
    modalities = {Modality(name) for name in spec.inputs.modalities}
    out: list[Sensor] = []
    for sensor in scene.sensors:
        if sensor.agent_id not in wanted or sensor.modality not in modalities:
            continue
        if any(fnmatch.fnmatchcase(sensor.sensor_id, glob) for glob in spec.inputs.sensors):
            out.append(sensor)
    return tuple(out)


def input_file(root: Path, relative: str) -> InputFile | None:
    """The file ``relative`` under ``root`` when it exists; ``None`` when it does not."""
    try:
        absolute = confine(root, relative)
    except PathError as exc:
        raise InputError(str(exc)) from None
    if not absolute.is_file():
        return None
    stat = absolute.stat()
    return InputFile(
        relative=relative, absolute=absolute, size=stat.st_size, mtime_ns=stat.st_mtime_ns
    )


def resolve_image(scene_dir: Path, media_roots: Sequence[Path], relative: str) -> InputFile | None:
    """The scene's own file first, then each ``--media-root`` (the image route's rule)."""
    found = input_file(scene_dir, relative)
    if found is not None:
        return found
    for root in media_roots:
        found = input_file(root, relative)
        if found is not None:
            return found
    return None


def extract_sweep(cloud: PointCloud, dest: Path) -> int:
    """Write the sweep as little-endian float32 ``x, y, z, intensity``; returns the count."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(cloud.xyz_i_f32)
    return cloud.count


def frame_index(spec: ModelSpec, time_us: int) -> int | None:
    clock = spec.inputs.raw_clock
    if clock is None:
        return None
    scale_us, offset_us = clock
    quotient, remainder = divmod(time_us - offset_us, scale_us)
    if remainder != 0:
        raise InputError(
            f"time_us {time_us} is not a whole frame of raw_clock "
            f"(scale {scale_us}, offset {offset_us})"
        )
    return quotient


def raw_files(
    spec: ModelSpec,
    *,
    scene_id: str,
    agent_id: str,
    agent_type: str,
    sensor_ids: Sequence[str],
    time_us: int,
    raw_root: Path,
) -> list[InputFile]:
    """The §Inputs ``raw`` templates expanded for one agent at one time, confined to the root.

    A template with ``{sensor}`` expands once per sensor id; the others once. Files that do
    not exist are omitted, not invented.
    """
    roots = spec.inputs.raw_agent_roots
    agent_root = roots.get(agent_id, roots.get(agent_type))
    frame = frame_index(spec, time_us)
    out: list[InputFile] = []
    for template in spec.inputs.raw:
        names = list(sensor_ids) if "sensor" in template.placeholders else [None]
        for sensor_id in names:
            values: dict[str, object] = {"scene": scene_id, "agent": agent_id, "time_us": time_us}
            if agent_root is not None:
                values["agent_root"] = agent_root
            if frame is not None:
                values["frame"] = frame
            if sensor_id is not None:
                values["sensor"] = sensor_id
            try:
                relative = template.expand(values)
            except PathError as exc:
                raise InputError(str(exc)) from None
            found = input_file(raw_root, relative)
            if found is not None:
                out.append(found)
    return out


def build_frame(
    scene: Scene,
    scene_dir: Path,
    spec: ModelSpec,
    *,
    frame_id: int,
    time_us: int,
    agent_ids: Sequence[str],
    work_dir: Path,
    media_roots: Sequence[Path],
    raw_root: Path | None,
    perturbation: PerturbationSpec | None = None,
    ego_agent_id: str | None = None,
) -> tuple[Frame, tuple[InputFile, ...]]:
    """One ``frame`` message and the files it referenced.

    ``poses`` holds every agent of the scene with a pose at ``time_us`` (the child may need
    the others' positions); images and sweeps are the requested agents' selected sensors.
    ``perturbation`` replaces non-ego poses and sensor times. Ground truth is not read.
    """
    decisions = _decisions(scene, time_us, agent_ids, perturbation, ego_agent_id)
    poses: dict[str, dict[str, float]] = {}
    for agent in scene.agents:
        decision = decisions.get(agent.agent_id) if decisions is not None else None
        if decision is not None:
            if decision.pose is None:
                continue
            poses[agent.agent_id] = pose_json(decision.pose)
            continue
        try:
            pose = lookup_pose(agent, time_us, max_gap_us=0)
        except TimeQueryError:
            continue
        poses[agent.agent_id] = pose_json(pose)
    sensors = selected_sensors(scene, spec, agent_ids)
    files: list[InputFile] = []
    images: list[ImageInput] = []
    points: list[PointsInput] = []
    for sensor in sensors:
        sensor_time = _sensor_time(sensor.agent_id, time_us, decisions)
        if sensor_time is None:
            continue
        if sensor.modality is Modality.CAMERA:
            relative = image_path(scene, sensor_time, sensor.agent_id, sensor.sensor_id)
            if relative is None:
                continue
            found = resolve_image(scene_dir, media_roots, relative)
            if found is None:
                continue
            files.append(found)
            images.append(ImageInput(sensor.agent_id, sensor.sensor_id, str(found.absolute)))
        elif sensor.modality is Modality.LIDAR:
            cloud = next(
                (
                    c
                    for c in scene.point_clouds
                    if c.agent_id == sensor.agent_id
                    and c.sensor_id == sensor.sensor_id
                    and c.time_us == sensor_time
                ),
                None,
            )
            if cloud is None:
                continue
            dest = work_dir / "inputs" / str(frame_id) / f"{sensor.agent_id}-{sensor.sensor_id}.bin"
            count = extract_sweep(cloud, dest)
            points.append(
                PointsInput(sensor.agent_id, sensor.sensor_id, str(dest), count, POINT_LAYOUT)
            )
            source = input_file(scene_dir, "points.bin")
            if source is not None and source not in files:
                files.append(source)
    if raw_root is not None and spec.inputs.raw:
        by_id = {agent.agent_id: agent for agent in scene.agents}
        for agent_id in agent_ids:
            raw_time = _sensor_time(agent_id, time_us, decisions)
            if raw_time is None:
                continue
            agent = by_id[agent_id]
            sensor_ids = [s.sensor_id for s in sensors if s.agent_id == agent_id]
            files.extend(
                raw_files(
                    spec,
                    scene_id=scene.scene_id,
                    agent_id=agent_id,
                    agent_type=agent.agent_type.value,
                    sensor_ids=sensor_ids,
                    time_us=raw_time,
                    raw_root=raw_root,
                )
            )
    frame = Frame(
        id=frame_id,
        scene_id=scene.scene_id,
        time_us=time_us,
        poses=poses,
        images=tuple(images),
        points=tuple(points),
    )
    unique = {f.relative: f for f in files}
    return frame, tuple(unique[k] for k in sorted(unique))


def _decisions(
    scene: Scene,
    time_us: int,
    agent_ids: Sequence[str],
    perturbation: PerturbationSpec | None,
    ego_agent_id: str | None,
) -> dict[str, AgentDecision] | None:
    if perturbation is None:
        return None
    ego = ego_agent_id or (agent_ids[0] if agent_ids else scene.agents[0].agent_id)
    try:
        lookup_pose(scene.agent(ego), time_us, max_gap_us=0)
    except (KeyError, TimeQueryError):
        return None
    return {item.agent_id: item for item in decide(scene, time_us, perturbation, ego)}


def _sensor_time(
    agent_id: str, time_us: int, decisions: dict[str, AgentDecision] | None
) -> int | None:
    if decisions is None:
        return time_us
    decision = decisions.get(agent_id)
    if decision is None or not decision.message or decision.source_time_us is None:
        return None
    return decision.source_time_us


def remove_inputs(work_dir: Path, frame_id: int) -> None:
    """Delete ``inputs/<frame id>/`` after the result (spec §Working directory)."""
    directory = work_dir / "inputs" / str(frame_id)
    if not directory.is_dir():
        return
    for child in directory.iterdir():
        try:
            os.unlink(child)
        except OSError:
            pass
    try:
        directory.rmdir()
    except OSError:
        pass


__all__ = [
    "POINT_LAYOUT",
    "InputError",
    "InputFile",
    "build_frame",
    "extract_sweep",
    "frame_index",
    "input_file",
    "pose_json",
    "raw_files",
    "remove_inputs",
    "resolve_image",
    "selected_sensors",
    "sensor_json",
]
