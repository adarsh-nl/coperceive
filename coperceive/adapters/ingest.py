# SPDX-License-Identifier: Apache-2.0
"""The one ingest driver every adapter shares.

Scenes run in parallel. A scene is skipped when ``COMPLETE.json`` matches its
sample list. A partial directory is removed and rebuilt, unless it already has a
matching marker, in which case it is renamed into place. One scene's failure
leaves the scenes that finished. ``COMPLETE.json`` is not part of schema 1.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from collections.abc import Mapping, Sequence
from concurrent.futures import Future, ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import Protocol, cast

from coperceive.adapters.build import CheckReport, SceneRef
from coperceive.errors import CoperceiveError
from coperceive.model import SCHEMA_VERSION, Scene
from coperceive.store import write_scene

COMPLETE = "COMPLETE.json"


class Adapter(Protocol):
    """What the driver needs from an adapter. ``ManifestAdapter`` is one."""

    name: str

    def scenes(self, raw_root: Path, params: Mapping[str, str]) -> Sequence[SceneRef]: ...

    def load(self, raw_root: Path, ref: SceneRef) -> Scene: ...

    def check(
        self,
        raw_root: Path,
        params: Mapping[str, str],
        *,
        scene_ids: Sequence[str] | None = None,
    ) -> CheckReport: ...


class IngestError(CoperceiveError):
    """One or more scenes failed. Successful scenes stay on disk."""

    def __init__(self, report: IngestReport) -> None:
        self.report = report
        super().__init__("\n".join(report.errors))


@dataclass(frozen=True)
class IngestReport:
    written: tuple[str, ...]
    skipped: tuple[str, ...]
    resumed: tuple[str, ...]
    errors: tuple[str, ...]
    sample_counts: tuple[tuple[str, int], ...]
    variant_counts: tuple[tuple[int, int], ...]


@dataclass(frozen=True)
class _Job:
    adapter: Adapter
    raw_root: str
    out: str
    ref: SceneRef
    provenance: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class _Result:
    scene_id: str
    status: str
    samples: int
    variants: tuple[tuple[int, int], ...]


def default_workers() -> int:
    count = os.cpu_count() or 1
    return max(1, min(8, count))


def ingest(
    adapter: Adapter,
    raw_root: Path,
    out: Path,
    *,
    params: Mapping[str, str],
    workers: int | None = None,
    scene_ids: Sequence[str] | None = None,
) -> IngestReport:
    """Write one schema 1 directory per scene under ``out``."""
    refs = list(adapter.scenes(raw_root, params))
    if scene_ids is not None:
        known = {ref.scene_id for ref in refs}
        missing = [s for s in scene_ids if s not in known]
        if missing:
            raise IngestError(
                IngestReport(
                    (),
                    (),
                    (),
                    tuple(f"{s}: not a scene of {adapter.name}" for s in missing),
                    (),
                    (),
                )
            )
        wanted = set(scene_ids)
        refs = [ref for ref in refs if ref.scene_id in wanted]
    provenance = _provenance(adapter)
    jobs = [_Job(adapter, str(raw_root), str(out), ref, provenance) for ref in refs]
    pool = workers if workers is not None else default_workers()
    if pool < 1:
        raise ValueError("workers must be >= 1")
    results: list[_Result] = []
    errors: list[str] = []
    if pool == 1 or len(jobs) <= 1:
        for job in jobs:
            _collect(job, results, errors)
    else:
        with ProcessPoolExecutor(max_workers=pool) as executor:
            futures = {executor.submit(ingest_one, job): job for job in jobs}
            for future in as_completed(futures):
                _collect_future(futures[future], future, results, errors)
    report = _report(results, errors)
    if errors:
        raise IngestError(report)
    return report


def ingest_one(job: _Job) -> _Result:
    """Ingest a single scene. Safe to run in a worker process."""
    raw_root = Path(job.raw_root)
    out = Path(job.out)
    final = out / job.ref.scene_id
    partial = out / ".partial" / job.ref.scene_id
    expected = _expected(job.ref)
    if _is_complete(final, expected):
        return _Result(job.ref.scene_id, "skipped", len(job.ref.samples), _stored_variants(final))
    if final.exists():
        shutil.rmtree(final)
    if _is_complete(partial, expected):
        partial.rename(final)
        return _Result(job.ref.scene_id, "resumed", len(job.ref.samples), _stored_variants(final))
    if partial.exists():
        shutil.rmtree(partial)
    partial.parent.mkdir(parents=True, exist_ok=True)
    partial.mkdir()
    try:
        variants = _variants(job.adapter, raw_root, job.ref)
        scene = job.adapter.load(raw_root, job.ref)
        write_scene(scene, partial)
        marker = dict(expected)
        if variants:
            marker["label_field_counts"] = {str(w): c for w, c in variants}
        marker.update(dict(job.provenance))
        (partial / COMPLETE).write_text(json.dumps(marker, indent=2) + "\n", encoding="utf-8")
        partial.rename(final)
    except Exception:
        shutil.rmtree(partial, ignore_errors=True)
        raise
    return _Result(job.ref.scene_id, "wrote", len(job.ref.samples), variants)


def format_report(report: IngestReport) -> str:
    lines = [
        f"wrote={len(report.written)} skipped={len(report.skipped)} resumed={len(report.resumed)}",
    ]
    histogram: dict[int, int] = {}
    for _scene_id, count in report.sample_counts:
        histogram[count] = histogram.get(count, 0) + 1
    for count in sorted(histogram):
        lines.append(f"frames {count}: {histogram[count]} scenes")
    if report.variant_counts:
        rendered = ", ".join(f"{width}:{total}" for width, total in report.variant_counts)
        lines.append(f"label_fields {rendered}")
    for scene_id, count in report.sample_counts:
        lines.append(f"{count}\t{scene_id}")
    for error in report.errors:
        lines.append(f"error\t{error}")
    return "\n".join(lines)


def manifest_sha256(adapter: Adapter) -> str | None:
    source = getattr(getattr(adapter, "spec", None), "source", None)
    if not isinstance(source, str):
        return None
    path = Path(source)
    if not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def coperceive_version() -> str:
    try:
        return metadata.version("coperceive")
    except metadata.PackageNotFoundError:
        return "unknown"


def _provenance(adapter: Adapter) -> tuple[tuple[str, str], ...]:
    rows = [("adapter", adapter.name), ("coperceive_version", coperceive_version())]
    digest = manifest_sha256(adapter)
    if digest is not None:
        rows.append(("manifest_sha256", digest))
    return tuple(rows)


def _variants(adapter: Adapter, raw_root: Path, ref: SceneRef) -> tuple[tuple[int, int], ...]:
    """Label-width counts for the marker, when the adapter reports them."""
    counter = getattr(adapter, "variant_counts", None)
    if counter is None:
        return ()
    counts: object = counter(raw_root, ref)
    if not isinstance(counts, tuple):
        return ()
    return counts  # type: ignore[return-value]


def _collect(job: _Job, results: list[_Result], errors: list[str]) -> None:
    try:
        results.append(ingest_one(job))
    except Exception as exc:
        errors.append(f"{job.ref.scene_id}: {type(exc).__name__}: {exc}")


def _collect_future(
    job: _Job,
    future: Future[_Result],
    results: list[_Result],
    errors: list[str],
) -> None:
    try:
        results.append(future.result())
    except Exception as exc:
        errors.append(f"{job.ref.scene_id}: {type(exc).__name__}: {exc}")


def _report(results: list[_Result], errors: list[str]) -> IngestReport:
    results.sort(key=lambda item: item.scene_id)
    widths: dict[int, int] = {}
    for result in results:
        for width, count in result.variants:
            widths[width] = widths.get(width, 0) + count
    return IngestReport(
        written=tuple(item.scene_id for item in results if item.status == "wrote"),
        skipped=tuple(item.scene_id for item in results if item.status == "skipped"),
        resumed=tuple(item.scene_id for item in results if item.status == "resumed"),
        errors=tuple(errors),
        sample_counts=tuple((item.scene_id, item.samples) for item in results),
        variant_counts=tuple(sorted(widths.items())),
    )


def _expected(ref: SceneRef) -> dict[str, object]:
    frames = ref.frames
    key = "frames" if len(frames) == len(ref.samples) else "samples"
    return {
        "scene_id": ref.scene_id,
        "schema_version": SCHEMA_VERSION,
        key: list(frames) if key == "frames" else [str(s) for s in ref.samples],
    }


def _is_complete(directory: Path, expected: dict[str, object]) -> bool:
    path = directory / COMPLETE
    if not path.is_file():
        return False
    try:
        got = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(got, dict):
        return False
    return all(got.get(key) == value for key, value in expected.items())  # type: ignore[union-attr]


def _stored_variants(directory: Path) -> tuple[tuple[int, int], ...]:
    try:
        payload = json.loads((directory / COMPLETE).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ()
    if not isinstance(payload, dict):
        return ()
    raw = cast(dict[str, object], payload).get("label_field_counts", {})
    if not isinstance(raw, dict):
        return ()
    rows: list[tuple[int, int]] = []
    for key, value in cast(dict[str, object], raw).items():
        rows.append((int(str(key)), int(str(value))))
    return tuple(sorted(rows))
