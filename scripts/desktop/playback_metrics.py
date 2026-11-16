# SPDX-License-Identifier: Apache-2.0
"""Playback frame counting without wraparound errors for smoke tests."""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PlaybackSample:
    """One poll of window.__COPERCEIVE_PLAYBACK_STATE__()."""

    frame_index: int
    samples: int
    wall_s: float


def count_frames_advanced(samples: Sequence[PlaybackSample]) -> tuple[int, float]:
    """Count total frames advanced across consecutive polls, handling wraparound.

    Accumulates per-step forward deltas modulo each sample's ``sample.samples``
    (read from ``window.__COPERCEIVE_PLAYBACK_STATE__().samples``) instead of
    comparing only the first and last frames modulo a hard-coded 100, which
    drops an entire loop whenever playback wraps within the window.
    """
    if len(samples) < 2:
        raise ValueError("Need at least two playback samples to measure rate")

    first = samples[0]
    if first.samples <= 0 or first.frame_index < 0 or first.frame_index >= first.samples:
        raise ValueError(f"Invalid initial playback sample: {first!r}")

    total_frames = 0
    prev = first
    for cur in samples[1:]:
        n = cur.samples
        if n <= 0 or cur.frame_index < 0 or cur.frame_index >= n:
            raise ValueError(f"Invalid playback sample: {cur!r}")
        if cur.frame_index < prev.frame_index and n != prev.samples:
            raise ValueError(f"Sample count changed across wrap ({prev.samples} -> {n})")
        delta = (cur.frame_index - prev.frame_index + n) % n
        total_frames += delta
        prev = cur

    elapsed_s = max(samples[-1].wall_s - samples[0].wall_s, 1e-6)
    return total_frames, elapsed_s


def evaluate_playback_samples(
    samples: Sequence[PlaybackSample],
    hz: float = 10.0,
    min_ratio: float = 0.9,
) -> tuple[int, float, float, float, bool]:
    """Return (frames, elapsed_s, ratio, min_required_frames, passed)."""
    frames, elapsed_s = count_frames_advanced(samples)
    expected = hz * elapsed_s
    ratio = frames / expected
    min_required = min_ratio * expected
    return frames, elapsed_s, ratio, min_required, frames >= min_required


def load_samples_jsonl(path: Path) -> list[PlaybackSample]:
    """Load recorded playback poll samples from a JSON-lines file."""
    out: list[PlaybackSample] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        row = json.loads(line)
        out.append(
            PlaybackSample(
                frame_index=int(row["frame_index"]),
                samples=int(row["samples"]),
                wall_s=float(row["wall_s"]),
            )
        )
    return out


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: playback_metrics.py <samples.jsonl>", file=sys.stderr)
        return 2
    samples = load_samples_jsonl(Path(argv[1]))
    frames, elapsed_s, ratio, min_required, passed = evaluate_playback_samples(samples)
    start_f = samples[0].frame_index
    end_f = samples[-1].frame_index
    n_samples = samples[-1].samples
    print(
        f"Demo 1x playback: elapsed={elapsed_s:.2f}s frames={frames} "
        f"({start_f} -> {end_f}, samples={n_samples}) "
        f"ratio={ratio:.2f}x (required >= 0.90x / {min_required:.1f} frames)"
    )
    if not passed:
        print(
            f"Error: Demo playback too slow: {frames} frames in {elapsed_s:.2f}s "
            f"({ratio:.2f}x < 0.90x)",
            file=sys.stderr,
        )
        return 26
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
