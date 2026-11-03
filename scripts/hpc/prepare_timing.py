# SPDX-License-Identifier: Apache-2.0
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownLambdaType=false, reportUnusedVariable=false, reportPrivateUsage=false, reportAttributeAccessIssue=false
"""Read-only timing diagnostic for one raw cluster dataset scene.

Run this on a compute node (or briefly on the login node for the 5-frame read
test) before changing the prepare array. It never writes under the raw root:
the only writes are JPEG preview bytes and the JSON/summary under ``--out``.

It uses the same parsers as ``coperceive adapters ingest`` (``read_pcd``,
``read_yaml_record``, ``adapter.load``, ``write_scene``) and the same preview
size/quality as the scene packer, so the numbers describe the real pipeline.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import socket
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

PREVIEW_LIMIT = 200  # preview timing is sampled for very large camera sets


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * fraction))))
    return ordered[index]


def _files(scene_dir: Path, suffix: str) -> list[Path]:
    return sorted(scene_dir.rglob(f"*{suffix}"))


def _read_pass(paths: list[Path]) -> dict[str, Any]:
    """Read each file once; report bytes, wall time, throughput and per-file latency."""
    latencies: list[float] = []
    total_bytes = 0
    start = time.perf_counter()
    for path in paths:
        one_start = time.perf_counter()
        data = path.read_bytes()
        latencies.append(time.perf_counter() - one_start)
        total_bytes += len(data)
    elapsed = time.perf_counter() - start
    return {
        "files": len(paths),
        "bytes": total_bytes,
        "seconds": round(elapsed, 6),
        "files_per_second": round(len(paths) / elapsed, 3) if elapsed else 0.0,
        "mib_per_second": round(total_bytes / 1_048_576 / elapsed, 3) if elapsed else 0.0,
        "median_file_ms": round(statistics.median(latencies) * 1000, 3) if latencies else 0.0,
        "p95_file_ms": round(_percentile(latencies, 0.95) * 1000, 3),
    }


def _parse_files(paths: list[Path], reader: Any) -> dict[str, Any]:
    latencies: list[float] = []
    errors: list[str] = []
    total_bytes = 0
    start = time.perf_counter()
    for path in paths:
        one_start = time.perf_counter()
        try:
            reader(path)
            total_bytes += path.stat().st_size
        except Exception as exc:  # a diagnostic must not abort on one bad file
            errors.append(f"{path.name}: {type(exc).__name__}: {exc}")
        latencies.append(time.perf_counter() - one_start)
    elapsed = time.perf_counter() - start
    return {
        "files": len(paths),
        "bytes": total_bytes,
        "seconds": round(elapsed, 6),
        "files_per_second": round(len(paths) / elapsed, 3) if elapsed else 0.0,
        "median_file_ms": round(statistics.median(latencies) * 1000, 3) if latencies else 0.0,
        "p95_file_ms": round(_percentile(latencies, 0.95) * 1000, 3),
        "errors": errors[:20],
    }


def _filesystem_type(path: Path) -> str:
    try:
        result = subprocess.run(
            ["df", "-T", str(path)], capture_output=True, text=True, timeout=5, check=False
        )
        lines = result.stdout.splitlines()
        if len(lines) >= 2:
            parts = lines[1].split()
            if len(parts) >= 2:
                return parts[1]
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        result = subprocess.run(
            ["stat", "-f", "-c", "%T", str(path)],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        return result.stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def _environment(raw_root: Path, out: Path) -> dict[str, Any]:
    return {
        "node": socket.gethostname(),
        "nproc": os.cpu_count(),
        "slurm_cpus_per_task": os.environ.get("SLURM_CPUS_PER_TASK"),
        "slurm_job_id": os.environ.get("SLURM_JOB_ID"),
        "filesystem_raw": _filesystem_type(raw_root),
        "filesystem_out": _filesystem_type(out),
        "python": platform.python_version(),
        "platform": platform.platform(),
    }


def _preview_frame(source: Path, destination: Path) -> int:
    from coperceive.scene.pack import _preview_jpeg

    data = _preview_jpeg(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    return len(data)


def _human(summary: dict[str, Any]) -> str:
    rows = [
        (
            "raw read first",
            f"{summary['read_first']['bytes']} B",
            f"{summary['read_first']['seconds']} s",
            f"{summary['read_first']['files_per_second']} files/s",
            f"{summary['read_first']['mib_per_second']} MiB/s",
        ),
        (
            "raw read warm",
            f"{summary['read_warm']['bytes']} B",
            f"{summary['read_warm']['seconds']} s",
            f"{summary['read_warm']['files_per_second']} files/s",
            f"{summary['read_warm']['mib_per_second']} MiB/s",
        ),
        (
            "parse pcd",
            f"{summary['parse_pcd']['files']} files",
            f"{summary['parse_pcd']['seconds']} s",
            "",
            "",
        ),
        (
            "parse yaml",
            f"{summary['parse_yaml']['files']} files",
            f"{summary['parse_yaml']['seconds']} s",
            "",
            "",
        ),
        (
            "preview jpeg",
            f"{summary['preview']['frames']} frames",
            f"{summary['preview']['seconds']} s",
            "",
            f"{summary['preview']['jpeg_bytes']} B",
        ),
        (
            "write scene",
            f"{summary['write']['bytes']} B",
            f"{summary['write']['seconds']} s",
            "",
            "",
        ),
    ]
    width = max(len(row[0]) for row in rows)
    lines = [f"{'stage'.ljust(width)}  {'size':>12}  {'time':>10}  {'rate':>12}  {'note':>12}"]
    for name, size, seconds, rate, note in rows:
        lines.append(f"{name.ljust(width)}  {size:>12}  {seconds:>10}  {rate:>12}  {note:>12}")
    return "\n".join(lines)


def run(args: argparse.Namespace) -> dict[str, Any]:
    from coperceive.adapters.readers.lidar import read_pcd
    from coperceive.adapters.readers.records import read_yaml_record
    from coperceive.adapters.registry import resolve_adapter
    from coperceive.store import write_scene

    raw_root = Path(args.raw_root).resolve()
    out = Path(args.out).resolve()
    if not raw_root.is_dir():
        raise SystemExit(f"raw root is not a directory: {raw_root}")
    if out == raw_root or raw_root in out.parents:
        raise SystemExit("--out must not be inside the raw root (read-only raw data)")
    out.mkdir(parents=True, exist_ok=True)
    adapter = resolve_adapter(args.adapter)
    refs = adapter.scenes(raw_root, {"split": args.split} if args.split else {})
    match = next((ref for ref in refs if ref.scene_id == args.scene), None)
    if match is None:
        raise SystemExit(f"scene {args.scene!r} is not in this raw root/split")
    scene_dir = raw_root / args.split / args.scene if args.split else raw_root / args.scene
    if not scene_dir.is_dir():
        raise SystemExit(f"scene directory not found: {scene_dir}")
    pcds = _files(scene_dir, ".pcd")
    yamls = [path for path in _files(scene_dir, ".yaml") if path.name != "data_protocol.yaml"]
    pngs = _files(scene_dir, ".png")

    read_first = _read_pass(pcds + pngs)
    read_warm = _read_pass(pcds + pngs)
    lidar = adapter.spec.streams.lidar
    fields = {"x": "x", "y": "y", "z": "z"}
    intensity = 0.0
    if lidar is not None:
        fields = dict(getattr(lidar, "fields", fields))
        intensity = getattr(getattr(lidar, "intensity", None), "fill", 0.0) or 0.0
    parse_pcd = _parse_files(
        pcds, lambda path: read_pcd(path, fields=fields, intensity_fill=intensity)
    )
    parse_yaml = _parse_files(yamls, read_yaml_record)

    preview_latencies: list[float] = []
    jpeg_bytes = 0
    for source in pngs[:PREVIEW_LIMIT]:
        started = time.perf_counter()
        jpeg_bytes += _preview_frame(source, out / "preview" / f"{source.stem}.jpg")
        preview_latencies.append(time.perf_counter() - started)
    preview = {
        "frames": len(preview_latencies),
        "sampled": len(pngs) > PREVIEW_LIMIT,
        "seconds": round(sum(preview_latencies), 6),
        "median_frame_ms": round(statistics.median(preview_latencies) * 1000, 3)
        if preview_latencies
        else 0.0,
        "p95_frame_ms": round(_percentile(preview_latencies, 0.95) * 1000, 3),
        "jpeg_bytes": jpeg_bytes,
    }

    load_start = time.perf_counter()
    scene = adapter.load(raw_root, match)
    load_seconds = time.perf_counter() - load_start
    write_dir = out / "scene"
    if write_dir.exists():
        shutil.rmtree(write_dir)
    write_start = time.perf_counter()
    write_scene(scene, write_dir)
    write_seconds = time.perf_counter() - write_start
    write_bytes = sum(path.stat().st_size for path in write_dir.rglob("*") if path.is_file())

    summary = {
        "schema": "coperceive-prepare-timing-1",
        "adapter": args.adapter,
        "raw_root": str(raw_root),
        "split": args.split,
        "scene_id": args.scene,
        "counts": {"pcd": len(pcds), "yaml": len(yamls), "png": len(pngs)},
        "environment": _environment(raw_root, out),
        "read_first": read_first,
        "read_warm": read_warm,
        "parse_pcd": parse_pcd,
        "parse_yaml": parse_yaml,
        "preview": preview,
        "load_seconds": round(load_seconds, 6),
        "write": {"bytes": write_bytes, "seconds": round(write_seconds, 6)},
        "out": str(out),
    }
    (out / "prepare_timing.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))
    print(_human(summary))
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adapter", required=True, choices=("griffin", "opv2v", "v2xset"))
    parser.add_argument("--raw-root", required=True)
    parser.add_argument("--split", default="")
    parser.add_argument("--scene", required=True)
    parser.add_argument("--out", required=True, help="A directory under results, not the raw root.")
    args = parser.parse_args(argv)
    run(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
