# SPDX-License-Identifier: Apache-2.0
"""The streaming bench's CI case: it completes and prints complete JSON.

No timing assertion here: the numbers depend on the machine. CI only needs the
script to run end to end (synthetic scene, fake link, real viewer server and
streamer, no network or SSH) and to keep its machine-readable shape.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

REPO = Path(__file__).resolve().parents[1]
BENCH = REPO / "scripts" / "bench" / "stream_bench.py"


def test_stream_bench_smoke_prints_a_complete_json_document() -> None:
    completed = subprocess.run(
        [sys.executable, str(BENCH), "--smoke"],
        capture_output=True,
        text=True,
        timeout=180,
        cwd=REPO,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr[-4000:]
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    assert lines, f"no JSON on stdout; stderr:\n{completed.stderr[-4000:]}"
    result = cast(dict[str, Any], json.loads(lines[-1]))
    assert result["schema"] == "coperceive-stream-bench-1"
    assert result["smoke"] is True
    assert result["label"].startswith("synthetic")
    assert result["link"] == {"rtt_ms": 20.0, "mbps": 50.0}
    assert result["player"]["frames"] == 6
    playback = result["playback"]
    for key in ("fps", "frame_ms_p50", "frame_ms_p95", "stalls", "mb_over_link", "mb_per_s"):
        assert key in playback
    assert set(playback["buffered_ahead_s"]) == {"min", "median", "final"}
    stats = result["stream_stats"]
    assert stats["schema"] == "coperceive-stream-stats-1"
    for key in ("queue_ms", "read_ms", "transfer_ms"):
        assert {"count", "p50", "p95"} <= set(stats[key])
    seek = result["seek_cancel"]
    for key in ("reads", "cancelled_futures", "cancel_bytes", "control_bytes", "saved_bytes"):
        assert key in seek
    assert seek["reads"] == 2
