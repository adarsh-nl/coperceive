# SPDX-License-Identifier: Apache-2.0
"""Write a CARLA capture and reject a sequence that is missing a frame.

The recorder imports this module. The tests do too. Nothing here imports CARLA.
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import TypedDict, cast

LICENCE_LINE = "CARLA 0.9.15: code MIT, assets CC-BY"
FRAME_COUNT = 100
TIME_STEP_US = 100_000
DRONE_HEIGHT_M = 40.0
ROOF_LIDAR_Z_M = 2.4
COLLABORATORS = ("car-0", "car-1", "car-2", "drone")

LIDAR_ATTRIBUTES: dict[str, str] = {
    "channels": "32",
    "range": "50",
    "points_per_second": "56000",
    "rotation_frequency": "10",
    "upper_fov": "10",
    "lower_fov": "-30",
    "sensor_tick": "0.0",
    "dropoff_general_rate": "0.0",
}


class CaptureError(Exception):
    """The capture is not 100 complete frames for every collaborator."""


class Location(TypedDict):
    x: float
    y: float
    z: float


class Rotation(TypedDict):
    pitch: float
    yaw: float
    roll: float


class Pose(TypedDict):
    location: Location
    rotation: Rotation


class Box(TypedDict):
    actor_id: int
    type_id: str
    center: Location
    half_extent: Location
    rotation: Rotation


def choose_spawn_indices(count: int, k: int, seed: int) -> list[int]:
    """Pick ``k`` spawn-point indexes with ``random.Random(seed)``."""
    if k < 1 or count < k:
        raise CaptureError(f"need {k} spawn points, found {count}")
    return random.Random(seed).sample(range(count), k)


def four_wheel_ids(blueprints: list[tuple[str, int | None]]) -> list[str]:
    """Blueprint ids whose ``number_of_wheels`` attribute is 4, in id order."""
    return sorted(blueprint_id for blueprint_id, wheels in blueprints if wheels == 4)


def choose_blueprint_ids(
    blueprint_ids: list[str], k: int, seed: int, spawn_count: int
) -> list[str]:
    """Continue the spawn-point RNG and pick ``k`` distinct blueprint ids.

    ``choose_spawn_indices(spawn_count, k, seed)`` consumes the first sample.
    This call consumes that same sample, then draws the blueprints.
    """
    if spawn_count < k:
        raise CaptureError(f"need {k} spawn points, found {spawn_count}")
    if len(blueprint_ids) < k:
        raise CaptureError(f"need {k} four-wheel blueprints, found {len(blueprint_ids)}")
    rng = random.Random(seed)
    rng.sample(range(spawn_count), k)
    return rng.sample(blueprint_ids, k)


def time_us_for(frame_id: int, step_s: float) -> int:
    return frame_id * int(round(step_s * 1_000_000))


def write_agent_tick(
    out: Path,
    agent_id: str,
    frame_id: int,
    time_us: int,
    pose: Pose,
    points: bytes,
    carla_frame: int,
) -> None:
    """Append one pose and write that frame's sensor-frame lidar bytes."""
    if len(points) % 16 != 0:
        raise CaptureError(f"{agent_id} frame {frame_id} lidar is not xyz+intensity float32")
    folder = out / "agents" / agent_id
    (folder / "lidar").mkdir(parents=True, exist_ok=True)
    _append_jsonl(
        folder / "poses.jsonl",
        {
            "frame_id": frame_id,
            "time_us": time_us,
            "carla_frame": carla_frame,
            "location": pose["location"],
            "rotation": pose["rotation"],
            "point_count": len(points) // 16,
        },
    )
    (folder / "lidar" / f"{frame_id:06d}.bin").write_bytes(points)


def write_boxes(out: Path, frame_id: int, boxes: list[Box]) -> None:
    _append_jsonl(out / "boxes.jsonl", {"frame_id": frame_id, "boxes": boxes})


def write_manifest(out: Path, manifest: dict[str, object]) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def build_manifest(
    *,
    seed: int,
    map_name: str,
    step_s: float,
    frames: int,
    carla_version: str,
    agents: list[dict[str, object]],
    sensors: list[dict[str, object]],
) -> dict[str, object]:
    return {
        "seed": seed,
        "map": map_name,
        "tick_rate_hz": int(round(1.0 / step_s)),
        "fixed_delta_seconds": step_s,
        "frames": frames,
        "carla_version": carla_version,
        "licence": LICENCE_LINE,
        "agents": agents,
        "sensors": sensors,
    }


def require_complete(out: Path, agent_ids: tuple[str, ...] | list[str], n_frames: int) -> None:
    """Exit path for a capture whose frame ids are not ``0 .. n_frames-1``."""
    expected = list(range(n_frames))
    errors: list[str] = []
    for agent_id in agent_ids:
        pose_path = out / "agents" / agent_id / "poses.jsonl"
        frame_ids = [_frame_id(row, pose_path) for row in _read_jsonl(pose_path)]
        if frame_ids != expected:
            errors.append(f"{agent_id} pose frames are {frame_ids}, expected {expected}")
        for frame_id in expected:
            blob = out / "agents" / agent_id / "lidar" / f"{frame_id:06d}.bin"
            if not blob.is_file():
                errors.append(f"{agent_id} is missing lidar frame {frame_id}")
            elif blob.stat().st_size % 16 != 0:
                errors.append(f"{agent_id} lidar frame {frame_id} is not xyz+intensity float32")
    box_path = out / "boxes.jsonl"
    box_ids = [_frame_id(row, box_path) for row in _read_jsonl(box_path)]
    if box_ids != expected:
        errors.append(f"box frames are {box_ids}, expected {expected}")
    manifest_path = out / "manifest.json"
    if not manifest_path.is_file():
        errors.append("manifest.json is missing")
    else:
        loaded: object = json.loads(manifest_path.read_text(encoding="utf-8"))
        if _as_dict(loaded, manifest_path).get("licence") != LICENCE_LINE:
            errors.append(f"licence must be {LICENCE_LINE!r}")
    if errors:
        raise CaptureError("\n".join(errors))


def _append_jsonl(path: Path, row: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row) + "\n")


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    if not path.is_file():
        return []
    rows: list[dict[str, object]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line:
            continue
        loaded: object = json.loads(line)
        rows.append(_as_dict(loaded, path))
    return rows


def _as_dict(value: object, path: Path) -> dict[str, object]:
    if not isinstance(value, dict):
        raise CaptureError(f"{path} row is not an object")
    row: dict[str, object] = {}
    for key, item in cast(dict[object, object], value).items():
        if not isinstance(key, str):
            raise CaptureError(f"{path} has a non-string key")
        row[key] = item
    return row


def _frame_id(row: dict[str, object], path: Path) -> int:
    value = row.get("frame_id")
    if type(value) is not int:
        raise CaptureError(f"{path} frame_id is not an int")
    return value
