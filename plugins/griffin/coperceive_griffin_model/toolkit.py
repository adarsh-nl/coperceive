# SPDX-License-Identifier: Apache-2.0
"""The Griffin toolkit steps as argv lists (no shell), on Python 3.8: a scratch copy of the
toolkit, the release tree, the checkpoint, the nuScenes conversion, the info pickles and
``tools/test.py`` through ``torch.distributed.launch`` with one process, as
``tools/dist_eval.sh`` does. Every step logs to a file under the work directory.
"""

from __future__ import annotations

import glob
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Sequence

from convert import md5_of


class ToolkitError(RuntimeError):
    """A toolkit step failed; the message ends with the tail of its log."""


def copy_toolkit(source: str, dest: str) -> None:
    """The toolkit without its ``.git`` and ``work_dirs`` (the home checkout stays untouched)."""
    if os.path.isdir(dest):
        return
    shutil.copytree(
        source,
        dest,
        symlinks=True,
        ignore=shutil.ignore_patterns(".git", "work_dirs*", "__pycache__"),
    )


def toolkit_commit(source: str) -> str | None:
    """The checkout's HEAD from ``.git`` without running git; None when unknown."""
    head = os.path.join(source, ".git", "HEAD")
    try:
        with open(head, encoding="utf-8") as handle:
            text = handle.read().strip()
        if text.startswith("ref: "):
            ref = os.path.join(source, ".git", text[5:])
            with open(ref, encoding="utf-8") as handle:
                text = handle.read().strip()
        return text[:12] or None
    except OSError:
        return None


def place_checkpoint(
    source: str, toolkit: str, prefix: str, relative: str, expected_md5: str
) -> str:
    """Copy the checkpoint under ``<toolkit>/ckpts/<prefix>/<relative>`` and check its MD5."""
    target = os.path.join(toolkit, "ckpts", prefix, relative)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    if not os.path.isfile(target):
        shutil.copyfile(source, target)
    digest = md5_of(target)
    if digest != expected_md5:
        raise ToolkitError(f"checkpoint {source}: md5 {digest} is not the expected {expected_md5}")
    return target


def run_step(
    argv: Sequence[str], *, cwd: str, env: dict[str, str], log_path: str, what: str
) -> None:
    """Run one step with its output appended to ``log_path``; raise with the log's tail."""
    started = time.monotonic()
    with open(log_path, "a", encoding="utf-8") as log:
        log.write(f"== {what}: {' '.join(argv)}\n")
        log.flush()
        done = subprocess.run(  # noqa: S603 - argv list, no shell
            list(argv), cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT, shell=False
        )
        log.write(f"== {what}: exit {done.returncode} after {time.monotonic() - started:.0f} s\n")
    if done.returncode != 0:
        raise ToolkitError(
            f"{what} failed with exit {done.returncode}; log tail: {_tail(log_path)}"
        )


def _tail(path: str, lines: int = 12) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            return " | ".join(line.rstrip() for line in handle.readlines()[-lines:])
    except OSError:
        return "(no log)"


def convert_release(
    toolkit: str, prefix: str, split_file: str, *, env: dict[str, str], log_path: str
) -> str:
    """``trans_kitti2nuscenes.py`` then ``generate_nuscenes_pkl.py``; returns the nuScenes
    ``v1.0-trainval`` root of the vehicle side (sample.json, scene.json live there)."""
    python = sys.executable
    release = os.path.join("datasets", prefix, "griffin-release")
    nuscenes = os.path.join("datasets", prefix, "griffin-nuscenes")
    run_step(
        [
            python,
            "tools/griffin_data_converter/trans_kitti2nuscenes.py",
            "--source_dir",
            release,
            "--target_dir",
            nuscenes,
            "--split_file",
            split_file,
        ],
        cwd=toolkit,
        env=env,
        log_path=log_path,
        what="trans_kitti2nuscenes",
    )
    run_step(
        [
            python,
            "tools/griffin_data_converter/generate_nuscenes_pkl.py",
            "--root_path",
            nuscenes,
            "--out_path",
            os.path.join("data", "infos", prefix),
            "--split_file",
            split_file,
        ],
        cwd=toolkit,
        env=env,
        log_path=log_path,
        what="generate_nuscenes_pkl",
    )
    return os.path.join(toolkit, nuscenes, "vehicle-side", "v1.0-trainval")


def work_dir_for(config: str) -> str:
    """``tools/dist_eval.sh``: ``${CFG%.*}`` with ``configs`` replaced by ``work_dirs``."""
    stem = os.path.splitext(config)[0]
    return stem.replace("configs", "work_dirs") + "/"


def evaluate(
    toolkit: str,
    config: str,
    checkpoint: str,
    *,
    env: dict[str, str],
    log_path: str,
    master_port: int,
) -> str:
    """``tools/test.py`` through ``torch.distributed.launch`` with one process; returns the
    newest ``results_nusc_det.json`` the run wrote."""
    work_dir = work_dir_for(config)
    os.makedirs(os.path.join(toolkit, work_dir, "logs"), exist_ok=True)
    stamp = time.strftime("%m%d%H%M")
    step_env = dict(env)
    step_env["PYTHONPATH"] = toolkit + os.pathsep + env.get("PYTHONPATH", "")
    run_step(
        [
            sys.executable,
            "-m",
            "torch.distributed.launch",
            "--nproc_per_node=1",
            f"--master_port={master_port}",
            "tools/test.py",
            config,
            checkpoint,
            "--launcher",
            "pytorch",
            "--eval",
            "bbox",
            "--show-dir",
            work_dir,
            "--out",
            f"{work_dir}results-{stamp}.pkl",
        ],
        cwd=toolkit,
        env=step_env,
        log_path=log_path,
        what="tools/test.py",
    )
    candidates = sorted(
        glob.glob(os.path.join(toolkit, work_dir, "json_output", "*", "results_nusc_det.json")),
        key=os.path.getmtime,
    )
    if not candidates:
        raise ToolkitError(f"tools/test.py wrote no results_nusc_det.json under {work_dir}")
    return candidates[-1]


def collect_artifacts(
    toolkit: str, config: str, det_json: str, dest: str, prefix: str, side: str
) -> list[str]:
    """Copy the detection JSON, the results pickle and, when present, the drone track
    queries under ``dest``; returns the artefact names copied."""
    names: list[str] = []
    os.makedirs(dest, exist_ok=True)
    shutil.copyfile(det_json, os.path.join(dest, "det_json"))
    names.append("det_json")
    work_dir = os.path.join(toolkit, work_dir_for(config))
    pickles = sorted(glob.glob(os.path.join(work_dir, "results-*.pkl")), key=os.path.getmtime)
    if pickles:
        shutil.copyfile(pickles[-1], os.path.join(dest, "det_pkl"))
        names.append("det_pkl")
    track_query = os.path.join(toolkit, "data", "infos", prefix, side, "track_query")
    if os.path.isdir(track_query):
        target = os.path.join(dest, "track_query")
        if not os.path.isdir(target):
            shutil.copytree(track_query, target)
        names.append("track_query")
    return names
