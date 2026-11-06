# SPDX-License-Identifier: Apache-2.0
"""Replay playback through the real viewer server and streamer, over a fake link.

    python scripts/bench/stream_bench.py [--rtt 20] [--mbps 12] [--frames 40]
                                         [--prefetch 4] [--connections 6] [--cancel-count 8]
                                         [--smoke]

A synthetic 2-agent scene sized like OPV2V (56 000 points per sweep, 10 Hz) is
written to a temporary cache and served by the real streamer
(``coperceive dataset stream``) through ``link.py`` (half the round trip each
way and a bandwidth cap, like ``remote_view_bench.py``). The real ``RemoteScenes``
mirror and the real FastAPI viewer server sit in front of it; the player asks
for what the viewer asks for: each frame's document and its two point sweeps,
``PREFETCH`` frames ahead over a browser-like number of connections, holding
when the next frame is not there yet ("Buffering…").

Reported, as machine-readable JSON on stdout and a small table on stderr:

- playback: fps, frame-load p50/p95, stalls, buffered-ahead seconds over time,
  MB/s over the link;
- per-request timings from the viewer's own ``GET /api/stream-stats`` (bounded
  samples of the streamer's queue/read time and the client's transfer time);
- seek/cancel: prefetch reads submitted and cancelled like a seek, and the
  bytes the cancelled prefetch did not draw compared with fetching it all.

Everything here is synthetic and the "network" is ``scripts/bench/link.py`` on
a local machine: these are not cluster numbers. ``--smoke`` runs a tiny fixed
case for CI (no network, no SSH, a few seconds).
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
from coperceive.remote_view.stream import MAX_READ
from coperceive.se3 import SE3
from coperceive.store import write_scene
from fastapi.testclient import TestClient

SCHEMA = "coperceive-stream-bench-1"
SCENE_ID = "bench_0000"
POINTS = 56_000
# points.bin stores xyz + intensity (16 bytes); the viewer serves xyz (12 bytes).
SWEEP_BYTES_PER_POINT = 16
SERVED_BYTES_PER_POINT = 12
GAP_S = 0.1  # OPV2V records at 10 Hz
DEFAULT_PREFETCH = 4
DEFAULT_CONNECTIONS = 6  # a browser's connections per host
DEFAULT_CANCEL_COUNT = 8


def scene(frames: int) -> Scene:
    """A 2-agent OPV2V-sized scene: 56 000 points per sweep, 10 Hz."""
    sweep = struct.pack("<4f", 1.0, 2.0, 0.5, 0.3) * POINTS
    agents: list[Agent] = []
    clouds: list[PointCloud] = []
    sensors: list[Sensor] = []
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
        scene_id=SCENE_ID,
        world_frame="ENU",
        clock=Clock("us", "bench"),
        agents=tuple(agents),
        sensors=tuple(sensors),
        annotations=(),
        objects=(),
        point_clouds=tuple(clouds),
    )


def _fixture(root: Path, frames: int) -> Path:
    cache = root / "cache"
    write_scene(scene(frames), cache / SCENE_ID)
    (cache / SCENE_ID / "COMPLETE.json").write_text("{}\n")
    return cache


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(len(ordered) - 1, lower + 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def play(
    http: TestClient,
    times: list[int],
    *,
    prefetch: int,
    connections: int,
    gap_s: float = GAP_S,
) -> dict[str, object]:
    """A time-based player: fetch ahead, present at 10 Hz, hold when not ready."""
    pool = ThreadPoolExecutor(max_workers=connections)
    pending: dict[int, Future[dict[str, object]]] = {}
    frame_ms: list[float] = []
    buffered_ahead_s: list[float] = []
    bytes_served = 0

    def fetch(index: int) -> dict[str, object]:
        started = time.perf_counter()
        body = http.get(f"/api/scenes/{SCENE_ID}/frame", params={"time_us": times[index]})
        served = len(body.content)
        document = body.json()
        for sensor in document["point_sensors"]:
            params = {
                "time_us": times[index],
                "agent_id": sensor["agent_id"],
                "sensor_id": sensor["sensor_id"],
            }
            points = http.get(f"/api/scenes/{SCENE_ID}/points", params=params)
            assert len(points.content) == POINTS * SERVED_BYTES_PER_POINT, len(points.content)
            served += len(points.content)
        return {"ms": (time.perf_counter() - started) * 1000.0, "bytes": served}

    stalls = 0
    started = time.perf_counter()
    due = started
    for index in range(len(times)):
        for ahead in range(index, min(index + 1 + prefetch, len(times))):
            if ahead not in pending:
                pending[ahead] = pool.submit(fetch, ahead)
        before = time.perf_counter()
        loaded = pending.pop(index).result()
        ready = time.perf_counter()
        frame_ms.append(float(loaded["ms"]))
        bytes_served += int(loaded["bytes"])
        if ready > due + 0.005 and index > 0:
            stalls += 1  # the player held: "Buffering…"
        ahead_ready = sum(1 for future in pending.values() if future.done())
        buffered_ahead_s.append(ahead_ready * gap_s)
        # The next sample is due one gap after this one is on screen or was due.
        due = max(due, ready) + gap_s
        sleep = due - gap_s - time.perf_counter()
        if sleep > 0:
            time.sleep(sleep)
        del before
    elapsed = time.perf_counter() - started
    pool.shutdown()
    return {
        "fps": round(len(times) / elapsed, 2),
        "elapsed_s": round(elapsed, 2),
        "frame_ms_p50": round(_percentile(frame_ms, 0.5), 1),
        "frame_ms_p95": round(_percentile(frame_ms, 0.95), 1),
        "stalls": stalls,
        "buffered_ahead_s": {
            "min": round(min(buffered_ahead_s), 1),
            "median": round(statistics.median(buffered_ahead_s), 1),
            "final": round(buffered_ahead_s[-1], 1),
        },
        "http_bytes": bytes_served,
    }


def blocking_probe(client: StreamClient, frames: int) -> dict[str, object]:
    """Small stat replies submitted behind one large sweep read.

    The old streamer answered one request at a time, so a stat queued behind a
    large read waited for the read's payload to cross the link; the concurrent
    streamer answers the small ops on its read loop while the read runs. Both
    are timed from the same submit instant, so the difference is the point.
    """
    sweep_bytes = POINTS * SWEEP_BYTES_PER_POINT
    file_size = frames * 2 * sweep_bytes
    big = {
        "op": "read",
        "path": f"{SCENE_ID}/points.bin",
        "offset": 0,
        "length": min(MAX_READ, file_size),
    }
    started = time.perf_counter()
    big_future = client.submit(big)
    small_futures = [
        client.submit({"op": "stat", "path": f"{SCENE_ID}/manifest.json"}) for _ in range(3)
    ]
    small_ms: list[float] = []
    for future in small_futures:
        header, _ = future.result(timeout=120)
        assert header["ok"]
        small_ms.append((time.perf_counter() - started) * 1000.0)
    big_header, big_payload = big_future.result(timeout=120)
    assert big_header["ok"] and len(big_payload) == big["length"]
    return {
        "big_read_bytes": int(big["length"]),
        "big_read_ms": round((time.perf_counter() - started) * 1000.0, 1),
        "small_stat_ms_p50": round(_percentile(small_ms, 0.5), 1),
        "small_stat_ms": [round(value, 1) for value in small_ms],
    }


def seek_cancel(
    client: StreamClient, frames: int, count: int, *, settle_s: float = 0.4
) -> dict[str, object]:
    """Prefetch point sweeps, cancel them like a seek, measure bytes not drawn.

    Two runs over the same link: one fetches ``count`` sweeps in full (the old
    "no cancellation" seek), one submits the same reads and immediately cancels
    them (what a seek now does to prefetched point clouds). The difference in
    bytes crossing the link is what cancellation saved; it is measured, not
    assumed: an in-flight read may still complete and deliver before the
    cancel is seen.
    """
    sweep_bytes = POINTS * SWEEP_BYTES_PER_POINT
    reads = min(count, frames * 2)
    requests = [
        {
            "op": "read",
            "path": f"{SCENE_ID}/points.bin",
            "offset": index * sweep_bytes,
            "length": sweep_bytes,
        }
        for index in range(reads)
    ]

    def drained() -> int:
        time.sleep(settle_s)  # the fake link holds replies for half the RTT each way
        return client.bytes_read

    control_before = client.bytes_read
    started = time.perf_counter()
    full = [client.submit(request) for request in requests]
    for future in full:
        assert future.result(timeout=120)[0]["ok"]
    control_s = time.perf_counter() - started
    control_bytes = max(0, drained() - control_before)

    cancel_before = client.bytes_read
    started = time.perf_counter()
    cancelled = [client.submit(request) for request in requests]
    for future in cancelled:
        client.cancel(future.request_id)
    cancel_s = time.perf_counter() - started
    cancel_bytes = max(0, drained() - cancel_before)
    saved_bytes = max(0, control_bytes - cancel_bytes)
    return {
        "reads": reads,
        "sweep_bytes": sweep_bytes,
        "control_ms": round(control_s * 1000.0, 1),
        "control_bytes": control_bytes,
        "cancel_ms": round(cancel_s * 1000.0, 1),
        "cancel_bytes": cancel_bytes,
        "cancelled_futures": sum(1 for future in cancelled if future.cancelled()),
        "saved_bytes": saved_bytes,
        "saved_pct": round(100.0 * saved_bytes / max(1, control_bytes), 1),
    }


def measure(
    rtt_ms: float,
    mbps: float,
    *,
    frames: int,
    prefetch: int,
    connections: int,
    cancel_count: int,
    hol_only: bool = False,
) -> dict[str, object]:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        cache = _fixture(root, frames)
        link = Path(__file__).with_name("link.py")
        stream = [sys.executable, "-m", "coperceive", "dataset", "stream"]
        stream += ["--root", str(cache)]
        argv = [sys.executable, str(link), str(rtt_ms), str(mbps), "--", *stream]
        client = StreamClient(argv, timeout_s=120)
        try:
            remote = RemoteScenes(client, root / "mirror", DiskCache(root / "disk", 2 * 1024**3))
            http = TestClient(create_app(root=root / "mirror", token=None, remote=remote))
            times = [i * 100_000 for i in range(frames)]
            opened = time.perf_counter()
            http.get(f"/api/scenes/{SCENE_ID}").raise_for_status()
            open_scene_ms = round((time.perf_counter() - opened) * 1000.0, 1)
            result: dict[str, object] = {
                "schema": SCHEMA,
                "label": "synthetic 2-agent OPV2V-sized scene, fake link (link.py), local machine",
                "link": {"rtt_ms": rtt_ms, "mbps": mbps},
                "player": {
                    "frames": frames,
                    "gap_s": GAP_S,
                    "prefetch": prefetch,
                    "connections": connections,
                },
                "open_scene_ms": open_scene_ms,
            }
            if hol_only:
                result["hol"] = blocking_probe(client, frames)
                return result
            link_bytes_before = client.bytes_read
            playback = play(http, times, prefetch=prefetch, connections=connections)
            link_bytes = client.bytes_read - link_bytes_before
            playback["mb_over_link"] = round(link_bytes / 1024**2, 1)
            playback["mb_per_s"] = round(playback["mb_over_link"] / playback["elapsed_s"], 2)
            stream_stats = http.get("/api/stream-stats")
            seek = seek_cancel(
                client, frames, cancel_count, settle_s=max(0.25, 2 * rtt_ms / 1000.0 + 0.1)
            )
            result["playback"] = playback
            result["stream_stats"] = stream_stats.json()
            result["seek_cancel"] = seek
            result["hol"] = blocking_probe(client, frames)
        finally:
            client.close()
        return result


def _table(result: dict[str, object]) -> str:
    link = result["link"]
    hol = result.get("hol")
    if "playback" not in result:
        assert isinstance(hol, dict)
        return (
            f"stream_bench (head-of-line only): {link['rtt_ms']} ms RTT, {link['mbps']} MB/s, "
            f"{result['player']['frames']} frames\n"
            f"  hol        stat behind a {hol['big_read_bytes'] / 1024**2:.0f} MB read: "
            f"p50 {hol['small_stat_ms_p50']} ms (big read {hol['big_read_ms']} ms)"
        )
    playback = result["playback"]
    stream = result["stream_stats"]
    seek = result["seek_cancel"]
    assert isinstance(link, dict) and isinstance(playback, dict)
    assert isinstance(stream, dict) and isinstance(seek, dict)
    lines = [
        f"stream_bench: {link['rtt_ms']} ms RTT, {link['mbps']} MB/s, "
        f"{result['player']['frames']} frames, prefetch {result['player']['prefetch']}",
        f"  playback   {playback['fps']} fps, frame p50 {playback['frame_ms_p50']} ms, "
        f"p95 {playback['frame_ms_p95']} ms, stalls {playback['stalls']}, "
        f"{playback['mb_over_link']} MB ({playback['mb_per_s']} MB/s)",
        f"  buffered   {playback['buffered_ahead_s']}",
        f"  requests   queue p95 {stream['queue_ms']['p95']} ms, "
        f"read p95 {stream['read_ms']['p95']} ms, transfer p95 {stream['transfer_ms']['p95']} ms",
        f"  hol        stat behind a {hol['big_read_bytes'] / 1024**2:.0f} MB read: "
        f"p50 {hol['small_stat_ms_p50']} ms (big read {hol['big_read_ms']} ms)",
        f"  seek       cancelled {seek['cancelled_futures']}/{seek['reads']} prefetched reads, "
        f"{seek['cancel_bytes'] / 1024**2:.1f} MB drawn "
        f"vs {seek['control_bytes'] / 1024**2:.1f} MB without cancelling "
        f"({seek['saved_pct']}% saved)",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rtt", type=float, default=20.0, help="round trip, ms")
    parser.add_argument("--mbps", type=float, default=12.0, help="link bandwidth, MB/s")
    parser.add_argument("--frames", type=int, default=40)
    parser.add_argument("--prefetch", type=int, default=DEFAULT_PREFETCH)
    parser.add_argument("--connections", type=int, default=DEFAULT_CONNECTIONS)
    parser.add_argument("--cancel-count", type=int, default=DEFAULT_CANCEL_COUNT)
    parser.add_argument("--hol-only", action="store_true", help="only the head-of-line probe")
    parser.add_argument("--smoke", action="store_true", help="tiny fixed case for CI")
    args = parser.parse_args()
    if args.smoke:
        args.rtt = 20.0
        args.mbps = 50.0
        args.frames = 6
        args.prefetch = 2
        args.connections = 2
        args.cancel_count = 2
    result = measure(
        args.rtt,
        args.mbps,
        frames=args.frames,
        prefetch=args.prefetch,
        connections=args.connections,
        cancel_count=args.cancel_count,
        hol_only=args.hol_only,
    )
    result["smoke"] = bool(args.smoke)
    print(json.dumps(result), flush=True)
    print(_table(result), file=sys.stderr, flush=True)


if __name__ == "__main__":
    main()
