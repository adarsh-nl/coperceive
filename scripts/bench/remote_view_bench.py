# SPDX-License-Identifier: Apache-2.0
"""Playback over the SSH stream, on a link with a given round trip and bandwidth.

    python scripts/bench/remote_view_bench.py [--rtt 20 40] [--mbps 25 50] [--frames 60]

A synthetic 2-agent scene sized like OPV2V (56 000 points per sweep, 10 Hz) is
served by the real remote-view server through ``link.py`` (half the RTT each
way, a bandwidth cap). The player asks for what the viewer asks for: each
frame's document and its two sweeps, four frames ahead, six requests at a time
(a browser's connections per host), holding when a frame is not there yet.
"""

from __future__ import annotations

import argparse
import json
import statistics
import struct
import sys
import tempfile
import time
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

from coperceive.app.server import create_app
from coperceive.model import (
    SCHEMA_VERSION,
    Agent,
    AgentType,
    Clock,
    Modality,
    PointCloud,
    Scene,
    Sensor,
    StampedPose,
)
from coperceive.remote_view.client import StreamClient
from coperceive.remote_view.scenes import DiskCache, RemoteScenes
from coperceive.se3 import SE3
from coperceive.store import write_scene
from fastapi.testclient import TestClient

POINTS = 56_000
PREFETCH = 4
CONNECTIONS = 6
GAP_S = 0.1


def scene(frames: int) -> Scene:
    sweep = struct.pack("<4f", 1.0, 2.0, 0.5, 0.3) * POINTS
    agents = []
    clouds = []
    sensors = []
    for agent_id in ("641", "650"):
        poses = tuple(
            StampedPose(i * 100_000, SE3.identity().translation(float(i), 0.0, 0.0))
            for i in range(frames)
        )
        agents.append(Agent(agent_id, AgentType.VEHICLE, poses))
        sensors.append(Sensor("lidar", agent_id, Modality.LIDAR, SE3.identity()))
        clouds.extend(
            PointCloud(agent_id, "lidar", i * 100_000, POINTS, sweep) for i in range(frames)
        )
    return Scene(
        schema_version=SCHEMA_VERSION,
        scene_id="bench_0000",
        world_frame="ENU",
        clock=Clock("us", "bench"),
        agents=tuple(agents),
        sensors=tuple(sensors),
        annotations=(),
        objects=(),
        point_clouds=tuple(clouds),
    )


def play(http: TestClient, times: list[int]) -> dict[str, float]:
    pool = ThreadPoolExecutor(CONNECTIONS)
    pending: dict[int, Future[float]] = {}

    def fetch(index: int) -> float:
        started = time.perf_counter()
        doc = http.get("/api/scenes/bench_0000/frame", params={"time_us": times[index]}).json()
        for sensor in doc["point_sensors"]:
            params = {
                "time_us": times[index],
                "agent_id": sensor["agent_id"],
                "sensor_id": sensor["sensor_id"],
            }
            body = http.get("/api/scenes/bench_0000/points", params=params).content
            assert len(body) == POINTS * 12, len(body)
        return time.perf_counter() - started

    latencies: list[float] = []
    waits = 0
    started = time.perf_counter()
    due = started
    for index in range(len(times)):
        for ahead in range(index, min(index + 1 + PREFETCH, len(times))):
            if ahead not in pending:
                pending[ahead] = pool.submit(fetch, ahead)
        before = time.perf_counter()
        latencies.append(pending.pop(index).result())
        ready = time.perf_counter()
        if ready > due + 0.005 and index > 0:
            waits += 1  # the player held: "Buffering..."
        # The next sample is due one gap after this one is on screen or was due.
        due = max(due, ready) + GAP_S
        sleep = due - GAP_S - time.perf_counter()
        if sleep > 0:
            time.sleep(sleep)
        del before
    total = time.perf_counter() - started
    pool.shutdown()
    return {
        "fps": round(len(times) / total, 2),
        "frame_ms_median": round(statistics.median(latencies) * 1000, 1),
        "frame_ms_p95": round(sorted(latencies)[int(len(latencies) * 0.95) - 1] * 1000, 1),
        "buffering_frames": waits,
        "frames": len(times),
    }


def measure(rtt_ms: float, mbps: float, frames: int) -> dict[str, object]:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        cache_root = root / "cache"
        write_scene(scene(frames), cache_root / "bench_0000")
        (cache_root / "bench_0000" / "COMPLETE.json").write_text("{}\n")
        link = Path(__file__).with_name("link.py")
        stream = [sys.executable, "-m", "coperceive", "dataset", "stream"]
        stream += ["--root", str(cache_root)]
        argv = [sys.executable, str(link), str(rtt_ms), str(mbps), "--", *stream]
        client = StreamClient(argv, timeout_s=120)
        remote = RemoteScenes(client, root / "mirror", DiskCache(root / "disk", 2 * 1024**3))
        http = TestClient(create_app(root=root / "mirror", token=None, remote=remote))
        times = [i * 100_000 for i in range(frames)]
        opened = time.perf_counter()
        http.get("/api/scenes/bench_0000").raise_for_status()
        open_ms = round((time.perf_counter() - opened) * 1000, 1)
        cold = play(http, times)
        warm = play(http, times)
        client.close()
        return {
            "rtt_ms": rtt_ms,
            "mbps": mbps,
            "open_scene_ms": open_ms,
            "cold": cold,
            "warm_from_disk_cache": warm,
            "stream_requests": client.requests,
            "mb_over_link": round(client.bytes_read / 1024**2, 1),
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rtt", type=float, nargs="+", default=[20.0, 40.0])
    parser.add_argument("--mbps", type=float, nargs="+", default=[25.0, 50.0])
    parser.add_argument("--frames", type=int, default=60)
    args = parser.parse_args()
    for rtt in args.rtt:
        for mbps in args.mbps:
            print(json.dumps(measure(rtt, mbps, args.frames)), flush=True)


if __name__ == "__main__":
    main()
