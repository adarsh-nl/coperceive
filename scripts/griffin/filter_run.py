#!/usr/bin/env python
# SPDX-License-Identifier: Apache-2.0
"""Cut one output (agent and source) out of a merged predictions run.

Job 628386's runs hold several outputs in one ``boxes.parquet`` per scene (vehicle single,
drone single, vehicle fused). A plugin run holds one. To compare them byte for byte, this
writes the chosen output's rows, in file order, through ``write_run`` into a run of its own;
the original is never touched. A scene with no matching row is not written, and the count is
printed.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

from coperceive.evaluation.predictions import (
    BOXES_FILE,
    PredictionBox,
    read_boxes,
    read_run_meta,
    write_run,
)


def filter_run(
    src_root: Path,
    src_run: str,
    agent: str,
    source: str,
    dst_root: Path,
    dst_run: str,
    *,
    force: bool = False,
) -> tuple[int, int]:
    """``(scenes written, rows written)``."""
    meta = read_run_meta(src_root, src_run)
    if source not in meta.source_names:
        raise SystemExit(f"{src_run}: no source {source!r}; has {', '.join(meta.source_names)}")
    kept_sources = tuple((name, note) for name, note in meta.sources if name == source)
    note = f"filtered: agent {agent}, source {source} of run {src_run}"
    new_meta = replace(
        meta,
        run_id=dst_run,
        sources=kept_sources,
        notes=f"{meta.notes} | {note}" if meta.notes else note,
        provenance=None,
    )
    rows: list[PredictionBox] = []
    scenes = 0
    for scene_dir in sorted((src_root / src_run).iterdir()):
        if not (scene_dir / BOXES_FILE).is_file():
            continue
        picked = [
            box
            for box in read_boxes(src_root, src_run, scene_dir.name)
            if box.agent_id == agent and box.source == source
        ]
        if picked:
            scenes += 1
            rows.extend(picked)
    write_run(dst_root, dst_run, new_meta, rows, force=force)
    return scenes, len(rows)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--src-root", required=True, type=Path)
    parser.add_argument("--src-run", required=True)
    parser.add_argument("--agent", required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--dst-root", required=True, type=Path)
    parser.add_argument("--dst-run", required=True)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    scenes, rows = filter_run(
        args.src_root,
        args.src_run,
        args.agent,
        args.source,
        args.dst_root,
        args.dst_run,
        force=args.force,
    )
    print(f"wrote {args.dst_root / args.dst_run}: {scenes} scenes, {rows} rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
