#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""A dependency-free smoke pipeline for the HPC "New evaluation" flow.

It declares one path argument (`--split`) and one int (`--sleep-seconds`), then
writes the small output contract every pipeline must produce:
`predictions.jsonl` (one progress line here; no model, so no boxes) and
`metrics.json` with the metrics schema below. It is intentionally cluster-neutral:
the split path is supplied by the caller and no path is baked in.

The contract check calls the script with `--max-frames 3`; in that mode the sleep
is capped at one second so the check is quick. A normal run keeps the full delay
(default 30 s).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path


def _first_scene(split: Path) -> str:
    try:
        for line in split.read_text(encoding="utf-8").splitlines():
            text = line.strip()
            if text:
                return text
    except OSError as exc:
        raise SystemExit(f"smoke: cannot read the split file {split}: {exc}") from None
    raise SystemExit(f"smoke: the split file {split} is empty")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", type=Path, required=True, help="scene or split file")
    parser.add_argument(
        "--sleep-seconds",
        type=int,
        default=30,
        help="seconds to sleep before writing metrics (default: 30)",
    )
    parser.add_argument("--output-dir", type=Path, required=True, help="contract output folder")
    parser.add_argument(
        "--max-frames",
        type=int,
        default=None,
        help="set by the contract check; caps the smoke sleep at one second",
    )
    args = parser.parse_args(argv)

    if args.sleep_seconds < 0:
        parser.error("--sleep-seconds must not be negative")
    scene_id = _first_scene(args.split)
    sleep_seconds = (
        min(args.sleep_seconds, 1) if args.max_frames is not None else args.sleep_seconds
    )
    output = args.output_dir
    output.mkdir(parents=True, exist_ok=True)

    # A progress line is valid JSONL under the open contract and makes the file
    # non-empty for the contract check. No model is installed, so no boxes.
    predictions = output / "predictions.jsonl"
    predictions.write_text(
        json.dumps(
            {
                "type": "progress",
                "scenes_done": 1,
                "scenes_total": 1,
                "frames_done": 0,
                "frames_total": 0,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    if sleep_seconds:
        time.sleep(sleep_seconds)
    metrics = {
        "schema": "coperceive-pipeline-metrics",
        "version": 1,
        "run": {"model": "smoke", "checkpoint": "", "scenes": [scene_id]},
        "outputs": [],
        "metrics": {"smoke": "ok", "sleep_seconds": args.sleep_seconds},
        "notes": "dependency-free smoke pipeline; write your own predictions.jsonl here",
    }
    (output / "metrics.json").write_text(json.dumps(metrics, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
