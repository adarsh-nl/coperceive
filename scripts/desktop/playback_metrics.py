# SPDX-License-Identifier: Apache-2.0
"""Playback frame counting without wraparound errors for smoke tests."""

from __future__ import annotations

import argparse
import http.client
import json
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast

PLAYBACK_POLL_SCRIPT = (
    "(() => { const st = window.__COPERCEIVE_PLAYBACK_STATE__?.() ?? {}; "
    'const txt = document.body ? document.body.innerText : ""; '
    "return { frameIndex: st.frameIndex ?? -1, samples: st.samples ?? 0, "
    'playing: Boolean(st.playing), buffering: txt.includes("Buffering") }; })()'
)


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


def eval_controller(
    port: int,
    secret: str,
    script: str = PLAYBACK_POLL_SCRIPT,
    window: str = "viewer",
    timeout_s: float = 10.0,
) -> dict[str, object] | None:
    """Send one `/eval` POST over loopback without spawning external processes."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout_s)
    try:
        body = json.dumps({"window": window, "script": script}).encode("utf-8")
        conn.request(
            "POST",
            "/eval",
            body=body,
            headers={
                "Host": f"127.0.0.1:{port}",
                "Authorization": f"Bearer {secret}",
                "Content-Type": "application/json",
            },
        )
        resp = conn.getresponse()
        raw = resp.read().decode("utf-8", errors="replace")
        if resp.status != 200:
            return None
        parsed: object = json.loads(raw)
        if not isinstance(parsed, dict):
            return None
        return cast(dict[str, object], parsed)
    except OSError:
        return None
    except json.JSONDecodeError:
        return None
    finally:
        conn.close()


def poll_playback_samples(
    fetch_fn: Callable[[], dict[str, object] | None],
    polls: int = 25,
    interval_s: float = 0.2,
    clock_fn: Callable[[], float] = time.monotonic,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> tuple[list[PlaybackSample], bool]:
    """Collect playback samples in-process so polling does not contend for CPU."""
    collected: list[PlaybackSample] = []
    buffering_seen = False
    for _ in range(polls):
        sleep_fn(interval_s)
        payload = fetch_fn()
        wall_s = clock_fn()
        if not isinstance(payload, dict):
            continue
        raw_res: object = payload.get("result")
        if not isinstance(raw_res, dict):
            continue
        res = cast(dict[str, object], raw_res)
        if res.get("buffering") is True:
            buffering_seen = True
        idx = res.get("frameIndex", -1)
        count = res.get("samples", 0)
        if (
            isinstance(idx, int)
            and not isinstance(idx, bool)
            and isinstance(count, int)
            and not isinstance(count, bool)
            and count > 0
            and 0 <= idx < count
        ):
            collected.append(PlaybackSample(frame_index=idx, samples=count, wall_s=wall_s))
    return collected, buffering_seen


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


def _report_samples(samples: Sequence[PlaybackSample]) -> int:
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


def main(argv: list[str]) -> int:
    if len(argv) >= 2 and argv[1] == "poll":
        parser = argparse.ArgumentParser(prog="playback_metrics.py poll")
        parser.add_argument("--port", type=int, required=True)
        parser.add_argument("--secret", required=True)
        parser.add_argument("--window", default="viewer")
        parser.add_argument("--polls", type=int, default=25)
        parser.add_argument("--interval", type=float, default=0.2)
        ns = parser.parse_args(argv[2:])
        samples, buffering_seen = poll_playback_samples(
            lambda: eval_controller(ns.port, ns.secret, window=ns.window),
            polls=ns.polls,
            interval_s=ns.interval,
        )
        perf = eval_controller(
            ns.port,
            ns.secret,
            script=(
                "(() => { const perf = window.__COPERCEIVE_PLAYBACK_PERF__?.() ?? null; "
                "const st = window.__COPERCEIVE_PLAYBACK_STATE__?.(); "
                'if (st?.playing) window.__COPERCEIVE_PLAYBACK__?.("toggle"); return perf; })()'
            ),
            window=ns.window,
        )
        print(f"Demo playback perf breakdown: {json.dumps(perf)}")
        if buffering_seen:
            print(
                "Error: Demo playback showed 'Buffering' during 1x local playback!",
                file=sys.stderr,
            )
            return 26
        if len(samples) < 2:
            print(
                "Error: Could not read at least 2 playback samples during demo playback!",
                file=sys.stderr,
            )
            return 26
        return _report_samples(samples)

    if len(argv) != 2:
        print(
            "usage: playback_metrics.py <samples.jsonl> | poll --port PORT --secret SECRET",
            file=sys.stderr,
        )
        return 2
    samples = load_samples_jsonl(Path(argv[1]))
    return _report_samples(samples)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
