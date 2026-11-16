# SPDX-License-Identifier: Apache-2.0
"""Unit tests for wraparound-safe playback counting in scripts/desktop/smoke_test.sh."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from scripts.desktop.playback_metrics import (
    PlaybackSample,
    count_frames_advanced,
    evaluate_playback_samples,
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
