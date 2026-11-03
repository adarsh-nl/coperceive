# SPDX-License-Identifier: Apache-2.0
# pyright: reportPrivateUsage=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
"""Where `coperceive dataset prepare` spends its time, on a synthetic OPV2V scene.

    python scripts/bench/prepare_profile.py [--agents 2] [--frames 40] [--points 60000]
        [--images originals previews] [--top 25]

The scene is sized like a real OPV2V one (ASCII ``x y z intensity`` PCD sweeps,
four 800x600 PNG cameras per agent and frame, numpy-tagged YAML records); no
dataset bytes. Prints one JSON line per phase (wall and CPU seconds) and the
heaviest functions from cProfile, grouped into: lidar read/convert, records,
image staging/preview encoding, filesystem metadata, fingerprint, copying and
writing.
"""

from __future__ import annotations

import argparse
import cProfile
import json
import os
import pstats
import resource
import tempfile
import time
from concurrent.futures import Future
from pathlib import Path
from typing import Any

import numpy as np
from coperceive import cluster_datasets
from coperceive.cluster_datasets import _fingerprint, prepare_cluster_dataset
from PIL import Image

NUMPY_YAW = (
    "!!python/object/apply:numpy.core.multiarray.scalar [!!python/object/apply:numpy.dtype "
    "{args: [f8, false, true], state: !!python/tuple [3, '<', null, null, null, -1, -1, 0]}, "
    "!!binary 'AAAAAACAVkA=']"
)
EXTRINSIC = [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0], [0.0, 0.0, 1.0, 0.0], [0.0, 0.0, 0.0, 1.0]]
INTRINSIC = [[335.6, 0.0, 400.0], [0.0, 335.6, 300.0], [0.0, 0.0, 1.0]]

GROUPS = {
    "lidar read/convert": ("lidar.py", "_ascii_columns", "read_pcd", "_pack"),
    "records (yaml)": ("yaml", "numpy_yaml", "records.py"),
    "images (stage/encode)": ("camera_images", "PIL", "Image", "png", "jpeg"),
    "filesystem metadata": ("scandir", "stat", "listdir", "glob", "is_file", "is_dir", "exists"),
    "fingerprint": ("_fingerprint",),
    "copy/write": ("write_scene", "parquet", "write_bytes", "write_text", "copy", "rename"),
}


def build(raw: Path, agents: int, frames: int, points: int) -> None:
    rng = np.random.default_rng(3)
    scene = raw / "test" / "2021_08_23_21_47_19"
    scene.mkdir(parents=True)
    (scene / "data_protocol.yaml").write_text(
        '{"world": {"fixed_delta_seconds": 0.05, "sync_mode": true}}'
    )
    header = (
        "# .PCD v0.7 - Point Cloud Data file format\nVERSION 0.7\nFIELDS x y z intensity\n"
        "SIZE 4 4 4 4\nTYPE F F F F\nCOUNT 1 1 1 1\nWIDTH {n}\nHEIGHT 1\n"
        "VIEWPOINT 0 0 0 1 0 0 0\nPOINTS {n}\nDATA ascii\n"
    )
    noise = rng.integers(0, 255, size=(600, 800, 3), dtype=np.uint8)
    camera = Image.fromarray((noise // 4 + 96).astype(np.uint8))  # PNG-sized like a render
    png = scene / "camera.png"
    camera.save(png)
    for index in range(agents):
        agent = scene / str(641 + index)
        agent.mkdir()
        for frame in range(frames):
            stem = f"{69 + 2 * frame:06d}"
            record = {
                "ego_speed": 5.0,
                "lidar_pose": [float(index), float(frame), 1.9, 0.0, 90.0, 0.0],
                "true_ego_pos": [float(index), float(frame), 0.0, 0.0, 90.0, 0.0],
                "vehicles": {
                    str(900 + v): {
                        "angle": [0.0, 90.0, 0.0],
                        "center": [0.0, 0.0, 0.75],
                        "extent": [2.4, 1.0, 0.75],
                        "location": [float(v * 5), float(frame), 0.0],
                        "speed": 3.0,
                    }
                    for v in range(25)
                },
                **{
                    f"camera{c}": {
                        "cords": [float(index), float(frame), 1.9, 0.0, 90.0, 0.0],
                        "extrinsic": EXTRINSIC,
                        "intrinsic": INTRINSIC,
                    }
                    for c in range(4)
                },
            }
            text = json.dumps(record).replace(", 90.0, 0.0]", ", " + NUMPY_YAW + ", 0.0]", 1)
            (agent / f"{stem}.yaml").write_text(text)
            xyz = rng.uniform(-80, 80, size=(points, 4)).astype(np.float32)
            xyz[:, 3] = rng.random(points, dtype=np.float32)
            with open(agent / f"{stem}.pcd", "w", encoding="ascii") as handle:
                handle.write(header.format(n=points))
                np.savetxt(handle, xyz, fmt="%.4f")
            for c in range(4):
                os.link(png, agent / f"{stem}_camera{c}.png")
    png.unlink()


class InlineExecutor:
    """Runs the per-scene work in this process, so cProfile sees it."""

    def __init__(self, max_workers: int = 1) -> None:
        del max_workers

    def __enter__(self) -> InlineExecutor:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def submit(self, fn: Any, *args: Any) -> Future[Any]:
        future: Future[Any] = Future()
        future.set_result(fn(*args))
        return future


def simulate_cold_reads(raw: Path, cold_ms: float) -> None:
    """The first read of each raw file waits ``cold_ms`` (a shared filesystem's
    per-file latency; ~150 ms measured on the cluster), like a cold page cache."""
    import threading

    import coperceive.camera_images as camera_images

    root = str(raw.resolve())
    seen: set[str] = set()
    lock = threading.Lock()

    def cold(path: object) -> None:
        name = os.path.realpath(str(path))
        if not name.startswith(root):
            return
        with lock:
            first = name not in seen
            seen.add(name)
        if first:
            time.sleep(cold_ms / 1000)

    read_bytes = Path.read_bytes

    def slow_read_bytes(self: Path) -> bytes:
        cold(self)
        return read_bytes(self)

    open_image = camera_images.Image.open

    def slow_open(fp: Any, *args: Any, **kwargs: Any) -> Any:
        cold(fp)
        return open_image(fp, *args, **kwargs)

    Path.read_bytes = slow_read_bytes  # type: ignore[method-assign]
    camera_images.Image.open = slow_open  # type: ignore[assignment]


def cpu() -> float:
    usage = resource.getrusage(resource.RUSAGE_SELF)
    children = resource.getrusage(resource.RUSAGE_CHILDREN)
    return usage.ru_utime + usage.ru_stime + children.ru_utime + children.ru_stime


def grouped(stats: pstats.Stats, total: float) -> dict[str, float]:
    sums = dict.fromkeys(GROUPS, 0.0)
    for (filename, _line, name), row in stats.stats.items():  # type: ignore[attr-defined]
        tottime = float(row[2])
        where = f"{filename}:{name}"
        for group, needles in GROUPS.items():
            if any(needle in where for needle in needles):
                sums[group] += tottime
                break
    sums["other"] = max(0.0, total - sum(sums.values()))
    return {key: round(value, 2) for key, value in sums.items()}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agents", type=int, default=2)
    parser.add_argument("--frames", type=int, default=40)
    parser.add_argument("--points", type=int, default=60_000)
    parser.add_argument("--images", nargs="+", default=["originals", "previews"])
    parser.add_argument("--top", type=int, default=25)
    parser.add_argument("--no-profile", action="store_true", help="time only, in a worker process")
    parser.add_argument(
        "--cold-ms", type=float, default=0.0, help="first-read latency per raw file (cluster: 150)"
    )
    parser.add_argument(
        "--io-threads", type=int, default=None, help="COPERCEIVE_IO_THREADS for the prepare"
    )
    args = parser.parse_args()
    if not args.no_profile:
        cluster_datasets.ProcessPoolExecutor = InlineExecutor  # type: ignore[misc,assignment]
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        raw = root / "datasets" / "opv2v"
        started = time.perf_counter()
        build(raw, args.agents, args.frames, args.points)
        size = sum(p.stat().st_size for p in raw.rglob("*") if p.is_file())
        print(json.dumps({"phase": "build fixture", "s": round(time.perf_counter() - started, 2),
                          "raw_mb": round(size / 1024**2, 1)}), flush=True)  # fmt: skip
        if args.cold_ms:
            if args.no_profile:
                parser.error("--cold-ms needs the in-process run (drop --no-profile)")
            simulate_cold_reads(raw, args.cold_ms)
        if args.io_threads is not None:
            os.environ["COPERCEIVE_IO_THREADS"] = str(args.io_threads)
        started = time.perf_counter()
        fingerprint = _fingerprint(raw / "test", deadline=time.monotonic() + 60)
        print(json.dumps({"phase": "fingerprint", "s": round(time.perf_counter() - started, 3),
                          "entries": fingerprint["entries"]}), flush=True)  # fmt: skip
        for images in args.images:
            cache = root / "results" / images
            profile = cProfile.Profile()
            wall, before = time.perf_counter(), cpu()
            if not args.no_profile:
                profile.enable()
            prepare_cluster_dataset("opv2v", raw, cache, split="test", images=images, workers=1)
            profile.disable()
            wall, used = time.perf_counter() - wall, cpu() - before
            line: dict[str, object] = {"phase": f"prepare --images {images}"}
            line.update(wall_s=round(wall, 2), cpu_s=round(used, 2))
            if args.no_profile:
                print(json.dumps(line), flush=True)
                continue
            stats = pstats.Stats(profile)
            line["groups_s"] = grouped(stats, wall)
            print(json.dumps(line), flush=True)
            stats.sort_stats("tottime").print_stats(args.top)


if __name__ == "__main__":
    main()
