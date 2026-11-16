# SPDX-License-Identifier: Apache-2.0
"""Unit tests for wraparound-safe playback counting in scripts/desktop/smoke_test.sh."""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from scripts.desktop.playback_metrics import (
    PlaybackSample,
    count_frames_advanced,
    eval_controller,
    evaluate_playback_samples,
    poll_playback_samples,
)
from scripts.desktop.playback_metrics import (
    main as playback_main,
)


def test_counts_linear_playback_without_wrap() -> None:
    samples = [
        PlaybackSample(frame_index=10, samples=100, wall_s=0.0),
        PlaybackSample(frame_index=15, samples=100, wall_s=0.5),
        PlaybackSample(frame_index=20, samples=100, wall_s=1.0),
    ]
    frames, elapsed_s = count_frames_advanced(samples)
    assert frames == 10
    assert elapsed_s == pytest.approx(1.0)


def test_counts_single_loop_boundary_wrap() -> None:
    samples = [
        PlaybackSample(frame_index=94, samples=100, wall_s=0.0),
        PlaybackSample(frame_index=98, samples=100, wall_s=0.4),
        PlaybackSample(frame_index=2, samples=100, wall_s=0.8),
        PlaybackSample(frame_index=6, samples=100, wall_s=1.2),
    ]
    frames, elapsed_s = count_frames_advanced(samples)
    assert frames == 12
    assert elapsed_s == pytest.approx(1.2)


def test_counts_full_loop_wrap_inside_window_without_losing_a_loop() -> None:
    # Mirrors Rosetta CI runs (~9.91s..11.40s) where 100-frame demo wrapped once
    # inside the polling window and end_frame passed or matched start_frame.
    rosetta_same_frame = [
        PlaybackSample(frame_index=(5 + i * 10) % 100, samples=100, wall_s=i * 0.991)
        for i in range(11)  # 10 steps * 10 frames = 100 frames; ends back at frame 5
    ]
    assert rosetta_same_frame[0].frame_index == 5
    assert rosetta_same_frame[-1].frame_index == 5
    frames, elapsed_s, ratio, _, passed = evaluate_playback_samples(rosetta_same_frame)
    assert frames == 100
    assert elapsed_s == pytest.approx(9.91)
    assert ratio == pytest.approx(1.009, abs=0.01)
    assert passed is True

    # Start 0 -> wrap past 99 -> end 15 in 11.40s = 115 frames (1.01x, previously 15 / 0.13x)
    indices = [0, 8, 16, 24, 32, 40, 48, 56, 64, 72, 80, 88, 96, 4, 15]
    rosetta_past_start = [
        PlaybackSample(
            frame_index=idx,
            samples=100,
            wall_s=(i / (len(indices) - 1)) * 11.40,
        )
        for i, idx in enumerate(indices)
    ]
    frames2, elapsed2, ratio2, _, passed2 = evaluate_playback_samples(rosetta_past_start)
    assert frames2 == 115
    assert elapsed2 == pytest.approx(11.40)
    assert ratio2 == pytest.approx(1.01, abs=0.01)
    assert passed2 is True


def test_uses_sample_count_from_playback_state_not_hardcoded_100() -> None:
    samples = [
        PlaybackSample(frame_index=30, samples=33, wall_s=0.0),
        PlaybackSample(frame_index=32, samples=33, wall_s=0.2),
        PlaybackSample(frame_index=1, samples=33, wall_s=0.4),
        PlaybackSample(frame_index=4, samples=33, wall_s=0.6),
    ]
    frames, _ = count_frames_advanced(samples)
    assert frames == 7


def test_cli_exit_codes_for_slow_and_fast_runs(tmp_path: Path) -> None:
    fast = tmp_path / "fast.jsonl"
    fast.write_text(
        "\n".join(
            json.dumps({"frame_index": idx, "samples": 100, "wall_s": t})
            for idx, t in [(95, 0.0), (99, 0.4), (3, 0.8), (7, 1.2)]
        ),
        encoding="utf-8",
    )
    assert playback_main(["playback_metrics.py", str(fast)]) == 0

    slow = tmp_path / "slow.jsonl"
    slow.write_text(
        "\n".join(
            json.dumps({"frame_index": idx, "samples": 100, "wall_s": t})
            for idx, t in [(0, 0.0), (1, 1.0), (2, 2.0)]
        ),
        encoding="utf-8",
    )
    assert playback_main(["playback_metrics.py", str(slow)]) == 26


def test_in_process_poll_handles_wrap_and_buffering() -> None:
    frames = iter([96, 98, 0, 2, 4])
    clock_t = iter([0.2, 0.4, 0.6, 0.8, 1.0])

    samples, buffering = poll_playback_samples(
        lambda: {
            "result": {
                "frameIndex": next(frames),
                "samples": 100,
                "playing": True,
                "buffering": False,
            }
        },
        polls=5,
        interval_s=0.2,
        clock_fn=lambda: next(clock_t),
        sleep_fn=lambda _: None,
    )
    assert buffering is False
    total, elapsed, ratio, _, passed = evaluate_playback_samples(samples)
    assert total == 8
    assert elapsed == pytest.approx(0.8)
    assert ratio == pytest.approx(1.0)
    assert passed is True

    _, buffering_hit = poll_playback_samples(
        lambda: {"result": {"frameIndex": 0, "samples": 100, "playing": True, "buffering": True}},
        polls=2,
        interval_s=0.0,
        sleep_fn=lambda _: None,
    )
    assert buffering_hit is True


def test_poll_subcommand_against_real_http_controller() -> None:
    calls = 0
    started_at: float | None = None

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            nonlocal calls, started_at
            if self.headers.get("Authorization") != "Bearer test-secret":
                self.send_response(401)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            calls += 1
            if "__COPERCEIVE_PLAYBACK_PERF__" in body["script"]:
                payload = {"result": {"applied": calls}}
            else:
                now = time.monotonic()
                if started_at is None:
                    started_at = now
                elapsed = max(0.0, now - started_at)
                idx = (96 + round(elapsed * 10.0)) % 100
                payload = {
                    "result": {
                        "frameIndex": idx,
                        "samples": 100,
                        "playing": True,
                        "buffering": False,
                    }
                }
            raw = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        rc = playback_main(
            [
                "playback_metrics.py",
                "poll",
                "--port",
                str(server.server_address[1]),
                "--secret",
                "test-secret",
                "--polls",
                "10",
                "--interval",
                "0.15",
            ]
        )
        assert rc == 0
        assert eval_controller(server.server_address[1], "bad-secret") is None
    finally:
        server.shutdown()
        server.server_close()
