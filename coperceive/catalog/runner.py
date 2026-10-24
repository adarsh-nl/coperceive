# SPDX-License-Identifier: Apache-2.0
"""Dataset catalog loader, dry-run estimator, and background runner.

Loads datasets.yaml and provides structured NDJSON progress for downloads and conversions.
"""

from __future__ import annotations

import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TextIO, cast

import yaml

from coperceive.griffin.convert import convert_griffin
from coperceive.griffin.download import (
    Hub,
    ack_path,
    acknowledgement_present,
    download_griffin,
)


def catalog_path() -> Path:
    return Path(__file__).resolve().parent / "datasets.yaml"


def load_catalog() -> list[dict[str, Any]]:
    path = catalog_path()
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as f:
        loaded = yaml.safe_load(f)
    if not isinstance(loaded, dict):
        return []
    raw_dict = cast(dict[str, Any], loaded)
    raw_datasets: Any = raw_dict.get("datasets", [])
    if isinstance(raw_datasets, list):
        result: list[dict[str, Any]] = []
        for item in cast(list[object], raw_datasets):
            if isinstance(item, dict):
                result.append(cast(dict[str, Any], item))
        return result
    return []


def get_dataset(dataset_id: str) -> dict[str, Any] | None:
    for ds in load_catalog():
        if ds.get("id") == dataset_id:
            return ds
    return None


def _emit(event: dict[str, Any], out: TextIO = sys.stdout) -> None:
    print(json.dumps(event, separators=(",", ":")), file=out, flush=True)


def _find_preset(dataset: dict[str, Any], preset_id: str) -> dict[str, Any]:
    raw: Any = dataset.get("presets", [])
    items = cast(list[object], raw) if isinstance(raw, list) else []
    for item in items:
        if not isinstance(item, dict):
            continue
        found = cast(dict[str, Any], item)
        if found.get("id") == preset_id:
            return found
    raise ValueError(f"Unknown preset '{preset_id}'")


def _griffin_selection(preset: dict[str, Any]) -> tuple[str, str | None, str | None]:
    """Return subset, griffin preset name, and components for one catalog preset."""
    subset = preset.get("subset")
    if not isinstance(subset, str) or not subset:
        raise ValueError("Griffin preset is missing subset")
    named = preset.get("griffin_preset")
    components = preset.get("components")
    preset_name = named if isinstance(named, str) and named else None
    component_list = components if isinstance(components, str) and components else None
    if preset_name is None and component_list is None:
        raise ValueError("Griffin preset needs griffin_preset or components")
    if preset_name is not None and component_list is not None:
        raise ValueError("Griffin preset sets both griffin_preset and components")
    return subset, preset_name, component_list


def dry_run_preset(
    dataset_id: str,
    preset_id: str,
    dest: Path,
    hub: Hub | None = None,
) -> dict[str, Any]:
    """Byte counts from the dataset host, plus a free-space check.

    Griffin sizes are the Hub listing for the preset's archives. A failure is
    returned to the caller. This function does not fill in a stored estimate.
    """
    ds = get_dataset(dataset_id)
    if not ds:
        raise ValueError(f"Unknown dataset '{dataset_id}'")

    try:
        preset = _find_preset(ds, preset_id)
    except ValueError as exc:
        raise ValueError(f"Unknown preset '{preset_id}' for dataset '{dataset_id}'") from exc

    measured_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if dataset_id == "griffin":
        subset, preset_name, components = _griffin_selection(preset)
        try:
            report = download_griffin(
                dest,
                subset=subset,
                preset=preset_name,
                components=components,
                dry_run=True,
                hub=hub,
            )
        except Exception as exc:
            raise RuntimeError(
                "Could not read the Griffin file sizes from the dataset host. "
                "Check the network connection and try again."
            ) from exc
        download_bytes = sum(item.size for item in report.files)
        disk_bytes = report.required_free_bytes
        size_source = "Hugging Face dataset wjh-svm/Griffin tree listing"
    else:
        raise ValueError(f"{dataset_id} has no remote download to measure")

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
        "size_source": size_source,
        "measured_at": measured_at,
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
        _emit(
            {
                "event": "stage",
                "stage": "download",
                "message": f"Downloading {ds.get('name')} ({preset_id})…",
                "percent": 10,
            },
            out,
        )

        if dataset_id != "griffin":
            raise ValueError(f"{dataset_id} cannot be downloaded from here")
        preset = _find_preset(ds, preset_id)
        subset, preset_name, components = _griffin_selection(preset)
        report = download_griffin(
            raw_dest,
            subset=subset,
            preset=preset_name,
            components=components,
            accept_terms=accept_terms,
        )
        if report.release_root is None:
            raise RuntimeError(
                "The Griffin download finished without an extracted release. "
                "Try the download again."
            )

        _emit(
            {
                "event": "stage",
                "stage": "convert",
                "message": "Converting the release into scenes…",
                "percent": 70,
            },
            out,
        )
        conv_report = convert_griffin(report.release_root, library_root, subset=subset)

        _emit(
            {
                "event": "stage",
                "stage": "validate",
                "message": "Checking the converted scenes…",
                "percent": 90,
            },
            out,
        )

        scene_ids = list(conv_report.written)
        prefer = preset.get("open_scene")
        preferred = prefer if isinstance(prefer, str) else ""
        if preferred and preferred in scene_ids:
            first_scene = preferred
        elif scene_ids:
            first_scene = scene_ids[0]
        else:
            raise RuntimeError(
                "The download finished but no scene was written. "
                "Try the download again."
            )
        first_path = str(library_root / first_scene)
        _emit(
            {
                "event": "complete",
                "dataset_id": dataset_id,
                "preset_id": preset_id,
                "scenes": scene_ids,
                "first_scene": first_scene,
                "path": first_path,
                "message": f"Imported {len(scene_ids)} scene(s).",
            },
            out,
        )

    except Exception as exc:
        text = str(exc).strip() or "The download stopped."
        if "accept-griffin-terms" in text or "acknowledgement" in text:
            text = "Tick the Griffin licence acknowledgement, then try the download again."
        elif "dataset host" not in text and "network" not in text:
            text = f"The download did not finish. Try again. {text}"
        _emit({"event": "error", "message": text}, out)
        raise
