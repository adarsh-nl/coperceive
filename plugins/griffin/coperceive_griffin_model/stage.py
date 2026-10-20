# SPDX-License-Identifier: Apache-2.0
"""Trim a staged Griffin release to the scenes it holds and renumber the toolkit split
(the rules of ``scripts/griffin/scene_infos_val.py``, on Python 3.8).

``tools/griffin_data_converter/trans_kitti2nuscenes.py`` derives scenes from the sorted
``pose/`` listing, not from ``scene_infos.json``, and names them ``scene-{k:04}-{name}``
with ``k`` counting contiguous frame blocks; a tree that holds only some scenes therefore
needs ``scene_infos.json`` trimmed to those scenes, in dataset order, and a split file whose
val names are renumbered to match. Sample tokens are ``sample_<timestamp>`` and do not
depend on the numbering, so the detection JSON is unaffected.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Sequence
from typing import Any

SCENE_NAME = re.compile(r"^scene-(\d{4})-(.+)$")
SIDES = ("vehicle-side", "drone-side")


class StageError(RuntimeError):
    """The release or the split does not have the shape the toolkit's converter assumes."""


def load_scene_infos(path: str) -> list[dict[str, Any]]:
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, list):
        raise StageError(f"{path}: expected a JSON list")
    return data


def frames_of(scene: dict[str, Any]) -> list[str]:
    frames = scene["info"]["frames"]
    for frame in frames:
        if not (isinstance(frame, str) and len(frame) == 6 and frame.isdigit()):
            raise StageError(f"scene {scene['name']}: frame id {frame!r} is not a 6-digit string")
    return list(frames)


def check_blocks(scenes: Sequence[dict[str, Any]]) -> None:
    """Every scene is one contiguous block; blocks are sorted and separated."""
    prev_last = -10
    seen: set = set()
    for scene in scenes:
        ints = [int(f) for f in frames_of(scene)]
        if ints != list(range(ints[0], ints[0] + len(ints))):
            raise StageError(f"scene {scene['name']}: frames are not contiguous")
        if ints[0] - prev_last < 2:
            raise StageError(
                f"scene {scene['name']}: touches the previous block; renumbering unsafe"
            )
        prev_last = ints[-1]
        dup = seen.intersection(ints)
        if dup:
            raise StageError(f"scene {scene['name']}: frames {sorted(dup)[:3]} appear twice")
        seen.update(ints)


def split_val_names(split_path: str) -> list[str]:
    with open(split_path, encoding="utf-8") as handle:
        split = json.load(handle)
    names = split["batch_split"]["val"]
    if not isinstance(names, list) or not names:
        raise StageError(f"{split_path}: batch_split.val is empty")
    return [str(n) for n in names]


def present_scenes(
    scenes: Sequence[dict[str, Any]], raw_root: str, side: str = "vehicle-side"
) -> list[tuple[int, dict[str, Any]]]:
    """``(index, scene)`` of every scene whose pose files are all staged under ``raw_root``,
    in dataset order."""
    picked: list[tuple[int, dict[str, Any]]] = []
    pose_dir = os.path.join(raw_root, side, "pose")
    for index, scene in enumerate(scenes):
        frames = frames_of(scene)
        if all(os.path.isfile(os.path.join(pose_dir, f"{frame}.json")) for frame in frames):
            picked.append((index, scene))
    if not picked:
        raise StageError(f"no scene of scene_infos.json has all its pose files under {pose_dir}")
    return picked


def check_against_split(picked: Sequence[tuple[int, dict[str, Any]]], split_path: str) -> None:
    """The published index ``NNNN`` of every val scene equals its position in scene_infos."""
    by_name: dict[str, int] = {}
    for full_name in split_val_names(split_path):
        match = SCENE_NAME.match(full_name)
        if match is None:
            raise StageError(f"split name {full_name!r} is not scene-NNNN-<name>")
        by_name[match.group(2)] = int(match.group(1))
    for index, scene in picked:
        published = by_name.get(scene["name"])
        if published is not None and published != index:
            raise StageError(
                f"{scene['name']}: published index {published:04d} differs from scene_infos "
                f"position {index:04d}"
            )


def write_trimmed(
    release_dir: str, picked: Sequence[tuple[int, dict[str, Any]]], split_out: str
) -> list[str]:
    """Trimmed ``scene_infos.json`` for both sides under ``release_dir`` and the renumbered
    split (``train: []``, ``val`` = block k is the k-th picked scene); returns the new names."""
    trimmed = [scene for _, scene in picked]
    check_blocks(trimmed)
    for side in SIDES:
        target_dir = os.path.join(release_dir, side)
        os.makedirs(target_dir, exist_ok=True)
        with open(os.path.join(target_dir, "scene_infos.json"), "w", encoding="utf-8") as handle:
            json.dump(trimmed, handle, indent=4)
    new_names = [f"scene-{k:04d}-{scene['name']}" for k, scene in enumerate(trimmed)]
    os.makedirs(os.path.dirname(os.path.abspath(split_out)), exist_ok=True)
    with open(split_out, "w", encoding="utf-8") as handle:
        json.dump({"batch_split": {"train": [], "val": new_names}}, handle, indent=4)
    return new_names


def link_release(raw_root: str, release_dir: str) -> None:
    """``release_dir/<side>/{calib,pose,label,camera,lidar}`` as symlinks into the staged
    raw root (never written); ``scene_infos.json`` is written by :func:`write_trimmed`."""
    for side in SIDES:
        source = os.path.join(raw_root, side)
        target = os.path.join(release_dir, side)
        os.makedirs(target, exist_ok=True)
        for name in ("calib", "pose", "label", "camera", "lidar"):
            src = os.path.join(source, name)
            dst = os.path.join(target, name)
            if os.path.isdir(src) and not os.path.lexists(dst):
                os.symlink(src, dst)
