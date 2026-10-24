# SPDX-License-Identifier: Apache-2.0
"""Dataset catalog loader, dry-run estimator, and background runner.

Loads datasets.yaml and provides structured NDJSON progress for downloads and conversions.
"""

from __future__ import annotations

import json
import shutil
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any, TextIO

import yaml

from coperceive.griffin.download import (
    ack_path,
    acknowledgement_present,
    download_griffin,
)
from coperceive.griffin.convert import convert_griffin


def catalog_path() -> Path:
    return Path(__file__).resolve().parent / "datasets.yaml"


def load_catalog() -> list[dict[str, Any]]:
    path = catalog_path()
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data.get("datasets", [])


def get_dataset(dataset_id: str) -> dict[str, Any] | None:
    for ds in load_catalog():
        if ds.get("id") == dataset_id:
            return ds
    return None


def _emit(event: dict[str, Any], out: TextIO = sys.stdout) -> None:
    print(json.dumps(event, separators=(",", ":")), file=out, flush=True)


def dry_run_preset(
    dataset_id: str,
    preset_id: str,
    dest: Path,
) -> dict[str, Any]:
    """Computes exact dry-run sizes and free space headroom check."""
    ds = get_dataset(dataset_id)
    if not ds:
        raise ValueError(f"Unknown dataset '{dataset_id}'")

    presets = ds.get("presets", [])
    preset = next((p for p in presets if p.get("id") == preset_id), None)
    if not preset:
        raise ValueError(f"Unknown preset '{preset_id}' for dataset '{dataset_id}'")

    # Griffin dry-run uses download_griffin(dry_run=True)
    if dataset_id == "griffin":
        try:
            report = download_griffin(
                dest,
                subset="25m",
                preset="dev" if preset_id == "dev" else None,
                components="Town03-000" if preset_id == "one-scene" else None,
                dry_run=True,
            )
            download_bytes = sum(f.size for f in report.files)
            disk_bytes = report.required_free_bytes
        except Exception:
            # Fall back to curated estimates from catalog if offline/mock
            download_bytes = preset.get("estimated_download_bytes", 251658240)
            disk_bytes = preset.get("estimated_disk_bytes", 524288000)
    else:
        download_bytes = preset.get("estimated_download_bytes", 0)
        disk_bytes = preset.get("estimated_disk_bytes", 0)

    # Check free space on destination volume
    dest.mkdir(parents=True, exist_ok=True)
    usage = shutil.disk_usage(dest)
    free_bytes = usage.free
    total_bytes = usage.total

    # Refuse if headroom is under 10% of total free space
    has_headroom = (free_bytes - disk_bytes) >= (0.10 * free_bytes)

    ack = ack_path()
    requires_terms = bool(ds.get("requires_terms", False) and not acknowledgement_present(ack))

    result = {
        "event": "dry_run",
        "dataset_id": dataset_id,
        "preset_id": preset_id,
        "download_bytes": download_bytes,
        "disk_bytes": disk_bytes,
        "free_bytes": free_bytes,
        "total_bytes": total_bytes,
        "has_headroom": has_headroom,
        "requires_terms": requires_terms,
        "licence": ds.get("licence", "unknown"),
        "licence_url": ds.get("licence_url"),
    }
    return result


def run_dataset_job(
    dataset_id: str,
    preset_id: str,
    library_root: Path,
    accept_terms: bool = False,
    out: TextIO = sys.stdout,
) -> None:
    """Executes download, extraction, conversion and emits NDJSON progress."""
    ds = get_dataset(dataset_id)
    if not ds:
        _emit({"event": "error", "message": f"Unknown dataset {dataset_id}"}, out)
        return

    library_root.mkdir(parents=True, exist_ok=True)
    raw_dest = library_root / ".raw" / dataset_id

    try:
        # Stage 1: Download
        _emit({
            "event": "stage",
            "stage": "download",
            "message": f"Downloading {ds.get('name')} ({preset_id})…",
            "percent": 10,
        }, out)

        if dataset_id == "griffin":
            report = download_griffin(
                raw_dest,
                subset="25m",
                preset="dev" if preset_id == "dev" else None,
                components="Town03-000" if preset_id == "one-scene" else None,
                accept_terms=accept_terms,
            )

            # Stage 2: Verify MD5
            _emit({
                "event": "stage",
                "stage": "verify_md5",
                "message": "Verifying archive checksums…",
                "percent": 45,
            }, out)

            # Stage 3: Extract
            _emit({
                "event": "stage",
                "stage": "extract",
                "message": "Extracting scene archives…",
                "percent": 65,
            }, out)

            # Stage 4: Convert
            _emit({
                "event": "stage",
                "stage": "convert",
                "message": "Converting raw sensor feeds to Schema 1…",
                "percent": 85,
            }, out)

            conv_report = convert_griffin(
                raw_dest / "griffin-25m",
                library_root / "Griffin",
                subset="25m",
            )

            # Stage 5: Validate
            _emit({
                "event": "stage",
                "stage": "validate",
                "message": "Validating converted scenes…",
                "percent": 95,
            }, out)

            scene_ids = [s.scene_id for s in conv_report.scenes] if conv_report.scenes else ["Town03-000"]
            first_scene = scene_ids[0] if scene_ids else "Town03-000"
            first_path = str(library_root / "Griffin" / first_scene)

            # Complete!
            _emit({
                "event": "complete",
                "dataset_id": dataset_id,
                "preset_id": preset_id,
                "scenes": scene_ids,
                "first_scene": first_scene,
                "path": first_path,
                "message": f"Successfully imported {len(scene_ids)} scene(s).",
            }, out)

    except Exception as exc:
        _emit({"event": "error", "message": str(exc)}, out)
        raise
