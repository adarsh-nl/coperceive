#!/usr/bin/env python
# SPDX-License-Identifier: Apache-2.0
"""Compare two predictions runs scene by scene: bytes first, then what differs.

For every scene of run A: ``boxes.parquet`` byte-identical in B, else the Arrow tables, else
the rows as a multiset (order ignored), else the maximum centre, size, rotation and score
delta over rows paired in order. Exit 0 only when every scene of A is byte-identical.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

import pyarrow.parquet as pq
from coperceive.evaluation.predictions import BOXES_FILE, PredictionBox, read_boxes


def _key(box: PredictionBox) -> tuple[object, ...]:
    rotation = box.box_from_rpy
    return (
        box.agent_id,
        box.source,
        box.time_us,
        box.category,
        box.score,
        box.track_id,
        box.center,
        box.size_lwh,
        (rotation.qw, rotation.qx, rotation.qy, rotation.qz),
        box.frame.value,
    )


def _deltas(a: Sequence[PredictionBox], b: Sequence[PredictionBox]) -> str:
    n = min(len(a), len(b))
    centre = size = quat = score = 0.0
    keys = 0
    for x, y in zip(a[:n], b[:n], strict=True):
        if (x.time_us, x.agent_id, x.category) != (y.time_us, y.agent_id, y.category):
            keys += 1
        centre = max(centre, max(abs(p - q) for p, q in zip(x.center, y.center, strict=True)))
        size = max(size, max(abs(p - q) for p, q in zip(x.size_lwh, y.size_lwh, strict=True)))
        qa = (x.box_from_rpy.qw, x.box_from_rpy.qx, x.box_from_rpy.qy, x.box_from_rpy.qz)
        qb = (y.box_from_rpy.qw, y.box_from_rpy.qx, y.box_from_rpy.qy, y.box_from_rpy.qz)
        quat = max(quat, max(abs(p - q) for p, q in zip(qa, qb, strict=True)))
        score = max(score, abs(x.score - y.score))
    return (
        f"paired in order {n} of {len(a)} vs {len(b)} rows, {keys} with another "
        f"(time, agent, category); max |centre| {centre:.6g} m, |size| {size:.6g} m, "
        f"|quaternion| {quat:.6g}, |score| {score:.6g}"
    )


def compare_runs(a_root: Path, a_run: str, b_root: Path, b_run: str) -> tuple[list[str], int, int]:
    """``(report lines, scenes byte-identical, scenes in A)``."""
    lines: list[str] = []
    identical = 0
    scenes = sorted(p.name for p in (a_root / a_run).iterdir() if (p / BOXES_FILE).is_file())
    for scene in scenes:
        a = a_root / a_run / scene / BOXES_FILE
        b = b_root / b_run / scene / BOXES_FILE
        if not b.is_file():
            lines.append(f"{scene}: MISSING in {b_run}")
            continue
        if a.read_bytes() == b.read_bytes():
            identical += 1
            lines.append(f"{scene}: byte-identical ({a.stat().st_size} B)")
            continue
        table_equal = pq.read_table(a).equals(pq.read_table(b), check_metadata=False)
        ra = list(read_boxes(a_root, a_run, scene))
        rb = list(read_boxes(b_root, b_run, scene))
        same_set = Counter(map(_key, ra)) == Counter(map(_key, rb))
        lines.append(
            f"{scene}: bytes differ; Arrow tables equal {table_equal}; "
            f"same rows in another order {same_set and not table_equal}; " + _deltas(ra, rb)
        )
    lines.append(f"scenes byte-identical: {identical} of {len(scenes)}")
    return lines, identical, len(scenes)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--a-root", required=True, type=Path)
    parser.add_argument("--a-run", required=True)
    parser.add_argument("--b-root", required=True, type=Path)
    parser.add_argument("--b-run", required=True)
    args = parser.parse_args(argv)
    lines, identical, total = compare_runs(args.a_root, args.a_run, args.b_root, args.b_run)
    print("\n".join(lines))
    return 0 if identical == total else 1


if __name__ == "__main__":
    sys.exit(main())
