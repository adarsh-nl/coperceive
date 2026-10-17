# SPDX-License-Identifier: Apache-2.0
"""``coperceive convert griffin``: a permanent alias for the Griffin manifest adapter.

The scene layout, the parallel run, ``.partial/`` and ``COMPLETE.json`` are
the shared driver in ``coperceive.adapters.ingest``; the Griffin rules are
``coperceive/adapters/builtin/griffin.yaml``. The report and its printed form
are the ones this command always had. The pre-adapter implementation is
frozen in ``tests/griffin_reference.py`` for the regression test.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from coperceive.adapters.build import ManifestAdapter
from coperceive.adapters.ingest import IngestError, IngestReport, ingest
from coperceive.adapters.ingest import default_workers as default_workers
from coperceive.adapters.ingest import format_report as _format_ingest
from coperceive.adapters.spec import builtin_manifest_path, load_manifest
from coperceive.griffin.catalog import folder_for


class ConvertError(RuntimeError):
    """One or more scenes failed. Successful scenes stay on disk."""

    def __init__(self, report: ConvertReport) -> None:
        self.report = report
        super().__init__("\n".join(report.errors))


@dataclass(frozen=True)
class ConvertReport:
    written: tuple[str, ...]
    skipped: tuple[str, ...]
    resumed: tuple[str, ...]
    errors: tuple[str, ...]
    frame_counts: tuple[tuple[str, int], ...]
    label_fields: tuple[tuple[int, int], ...]


def griffin_adapter() -> ManifestAdapter:
    return ManifestAdapter(load_manifest(builtin_manifest_path("griffin")))


def convert_griffin(
    raw_root: Path,
    out: Path,
    *,
    subset: str,
    workers: int | None = None,
) -> ConvertReport:
    """Write one hybrid directory per scene under ``out``."""
    folder_for(subset)
    try:
        report = ingest(
            griffin_adapter(), raw_root, out, params={"subset": subset}, workers=workers
        )
    except IngestError as exc:
        raise ConvertError(_convert_report(exc.report)) from None
    return _convert_report(report)


def format_report(report: ConvertReport) -> str:
    return _format_ingest(
        IngestReport(
            written=report.written,
            skipped=report.skipped,
            resumed=report.resumed,
            errors=report.errors,
            sample_counts=report.frame_counts,
            variant_counts=report.label_fields,
        )
    )


def _convert_report(report: IngestReport) -> ConvertReport:
    return ConvertReport(
        written=report.written,
        skipped=report.skipped,
        resumed=report.resumed,
        errors=report.errors,
        frame_counts=report.sample_counts,
        label_fields=report.variant_counts,
    )
