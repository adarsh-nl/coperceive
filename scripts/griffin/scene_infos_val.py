#!/usr/bin/env python
# SPDX-License-Identifier: Apache-2.0
"""Trim a Griffin release to the ``val`` scenes of a toolkit split file.

Runs inside the Slurm job (Python >= 3.8, stdlib only). It reads the dataset
copy under ``/datasets`` and writes ONLY into the staging tree on ``/local``:

* ``<out>/vehicle-side/scene_infos.json`` and ``<out>/drone-side/scene_infos.json``
  with the val scenes only, in dataset order (the two inputs must be equal; they
  are, see ``vehicle==drone scene_infos: True`` in the plan);
* ``<split-out>``: a split file with ``train: []`` and the val scenes RENUMBERED;
* ``<files-from>``: an ``rsync --files-from`` list (paths relative to the raw root)
  with the pose/label/calib files of both sides and the four vehicle RGB cameras
  of exactly the val frames.

Why the split file is renumbered. ``tools/griffin_data_converter/trans_kitti2nuscenes.py``
derives scenes from the sorted ``pose/`` listing, not from ``scene_infos.json``:
``generate_sample_metadata`` starts a new scene whenever
``int(self.frame_list[idx + 1]) - int(frame) != 1`` and ``generate_scene_metadata``
names it ``f'scene-{scene_num:04}-{scene_info["name"]}'`` with ``scene_num``
counting those contiguous blocks. In a tree that holds only the val frames, the
k-th val scene (in frame order) becomes block ``k``, so the published names
(``scene-0032-Town07-011`` ...) would fail ``verify_split_scenes`` and
``generate_nuscenes_pkl.py`` (``scene_names.index(s)``). Sample tokens are
``sample_<timestamp>`` and do not depend on the numbering, so the detection JSON
is unaffected. The script asserts what makes the renumbering exact: every scene
is one contiguous frame block, blocks do not touch (gap >= 2 frames), frames are
unique across scenes, and the published index ``NNNN`` equals the scene's
position in ``scene_infos.json``.

Frame id -> file name: the frame ids in ``scene_infos.json`` are six-digit
strings (``"011185"``) and the files are ``011185.png`` / ``.json`` / ``.txt``
(``ls vehicle-side/camera/front | head -3`` -> ``011185.png 011186.png 011187.png``).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Sequence
from typing import Any

SCENE_NAME = re.compile(r"^scene-(\d{4})-(.+)$")
SIDES = ("vehicle-side", "drone-side")
DEFAULT_CAMS = ("front", "back", "left", "right")


def load_scene_infos(path: str) -> list[dict[str, Any]]:
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, list):
        raise SystemExit(f"{path}: expected a JSON list")
    return data


def val_scene_names(split_path: str) -> list[str]:
    with open(split_path, encoding="utf-8") as handle:
        split = json.load(handle)
    names = split["batch_split"]["val"]
    if not isinstance(names, list) or not names:
        raise SystemExit(f"{split_path}: batch_split.val is empty")
    return [str(n) for n in names]


def frames_of(scene: dict[str, Any]) -> list[str]:
    frames = scene["info"]["frames"]
    for frame in frames:
        if not (isinstance(frame, str) and len(frame) == 6 and frame.isdigit()):
            raise SystemExit(f"scene {scene['name']}: frame id {frame!r} is not a 6-digit string")
    return list(frames)


def check_blocks(scenes: Sequence[dict[str, Any]]) -> None:
    """Every scene is one contiguous block; blocks are sorted and separated."""
    prev_last = -10
    seen: set = set()
    for scene in scenes:
        ints = [int(f) for f in frames_of(scene)]
        if ints != list(range(ints[0], ints[0] + len(ints))):
            raise SystemExit(f"scene {scene['name']}: frames are not contiguous")
        if ints[0] - prev_last < 2:
            raise SystemExit(
                f"scene {scene['name']}: touches the previous block; renumbering unsafe"
            )
        prev_last = ints[-1]
        dup = seen.intersection(ints)
        if dup:
            raise SystemExit(f"scene {scene['name']}: frames {sorted(dup)[:3]} appear twice")
        seen.update(ints)


def select_val(
    scenes: Sequence[dict[str, Any]], names: Sequence[str]
) -> list[tuple[int, dict[str, Any]]]:
    picked: list[tuple[int, dict[str, Any]]] = []
    for full_name in names:
        match = SCENE_NAME.match(full_name)
        if match is None:
            raise SystemExit(f"split name {full_name!r} is not scene-NNNN-<name>")
        index, name = int(match.group(1)), match.group(2)
        if index >= len(scenes) or scenes[index]["name"] != name:
            raise SystemExit(
                f"{full_name}: scene_infos[{index}] is "
                f"{scenes[index]['name'] if index < len(scenes) else 'absent'!r}; "
                "published numbering != scene_infos order"
            )
        picked.append((index, scenes[index]))
    picked.sort(key=lambda item: item[0])
    return picked


def main(argv: Sequence[str] = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--raw", required=True, help="griffin-release root on /datasets (read only)"
    )
    parser.add_argument(
        "--split", required=True, help="toolkit data/split_datas/griffin_50scenes_25m.json"
    )
    parser.add_argument(
        "--out",
        required=True,
        help="staging griffin-release root (trimmed scene_infos.json are written here)",
    )
    parser.add_argument(
        "--split-out", required=True, help="where to write the renumbered split file"
    )
    parser.add_argument(
        "--files-from", required=True, help="where to write the rsync --files-from list"
    )
    parser.add_argument("--cams", default=",".join(DEFAULT_CAMS), help="vehicle cameras to stage")
    parser.add_argument("--no-check", action="store_true", help="skip the per-file existence check")
    args = parser.parse_args(argv)

    cams = [c for c in args.cams.split(",") if c]
    infos = {
        side: load_scene_infos(os.path.join(args.raw, side, "scene_infos.json")) for side in SIDES
    }
    if infos["vehicle-side"] != infos["drone-side"]:
        raise SystemExit("vehicle-side and drone-side scene_infos.json differ")
    scenes = infos["vehicle-side"]
    check_blocks(scenes)

    picked = select_val(scenes, val_scene_names(args.split))
    trimmed = [scene for _, scene in picked]
    check_blocks(trimmed)

    # 1. trimmed scene_infos.json for both sides (same content, as in the release)
    for side in SIDES:
        target_dir = os.path.join(args.out, side)
        os.makedirs(target_dir, exist_ok=True)
        with open(os.path.join(target_dir, "scene_infos.json"), "w", encoding="utf-8") as handle:
            json.dump(trimmed, handle, indent=4)

    # 2. renumbered split file: block k of the trimmed tree is the k-th val scene
    new_names = [f"scene-{k:04d}-{scene['name']}" for k, scene in enumerate(trimmed)]
    os.makedirs(os.path.dirname(os.path.abspath(args.split_out)), exist_ok=True)
    with open(args.split_out, "w", encoding="utf-8") as handle:
        json.dump({"batch_split": {"train": [], "val": new_names}}, handle, indent=4)

    # 3. rsync file list: calib of both sides, pose+label of both sides, vehicle RGB cameras
    files: list[str] = []
    for side in SIDES:
        calib_dir = os.path.join(args.raw, side, "calib")
        files.extend(f"{side}/calib/{name}" for name in sorted(os.listdir(calib_dir)))
    frames: list[str] = []
    for scene in trimmed:
        frames.extend(frames_of(scene))
    for frame in frames:
        for side in SIDES:
            files.append(f"{side}/pose/{frame}.json")
            files.append(f"{side}/label/{frame}.txt")
        for cam in cams:
            files.append(f"vehicle-side/camera/{cam}/{frame}.png")
    if not args.no_check:
        missing = [f for f in files if not os.path.exists(os.path.join(args.raw, f))]
        if missing:
            raise SystemExit(f"{len(missing)} listed files are missing, e.g. {missing[:3]}")
    with open(args.files_from, "w", encoding="utf-8") as handle:
        handle.write("\n".join(files) + "\n")

    sample = [os.path.getsize(os.path.join(args.raw, f)) for f in files if f.endswith(".png")][:20]
    per_png = sum(sample) / len(sample) if sample else 0.0
    n_png = sum(1 for f in files if f.endswith(".png"))
    print(
        f"val scenes ({len(trimmed)}): "
        + ", ".join(f"{old:04d}->{new}" for (old, _), new in zip(picked, new_names))
    )
    print(f"frames: {len(frames)}  files listed: {len(files)}  pngs: {n_png}")
    print(
        f"estimated camera bytes: {n_png * per_png:.0f} ({n_png * per_png / 1e9:.1f} GB at {per_png / 1e6:.2f} MB/png, 20-file sample)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
