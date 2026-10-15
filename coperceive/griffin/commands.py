# SPDX-License-Identifier: Apache-2.0
"""CLI actions for Griffin download and convert.

Imported from ``coperceive.cli`` through ``importlib`` so the strict core does
not depend on this package's types.
"""

from __future__ import annotations

import sys
from pathlib import Path

from coperceive.griffin.convert import ConvertError, convert_griffin, format_report
from coperceive.griffin.download import (
    ChecksumError,
    DiskSpaceError,
    download_griffin,
)
from coperceive.griffin.download import (
    format_report as format_download,
)
from coperceive.griffin.licence import LicenceError


def download_command(args: object) -> None:
    try:
        report = download_griffin(
            Path(args.dest),  # type: ignore[attr-defined]
            subset=args.subset,  # type: ignore[attr-defined]
            components=args.components,  # type: ignore[attr-defined]
            preset=args.preset,  # type: ignore[attr-defined]
            dry_run=args.dry_run,  # type: ignore[attr-defined]
            accept_terms=args.accept_griffin_terms,  # type: ignore[attr-defined]
        )
    except (LicenceError, DiskSpaceError, ChecksumError, ValueError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from None
    print(format_download(report))


def convert_command(args: object) -> None:
    workers = args.workers  # type: ignore[attr-defined]
    try:
        report = convert_griffin(
            Path(args.raw_root),  # type: ignore[attr-defined]
            Path(args.out),  # type: ignore[attr-defined]
            subset=args.subset,  # type: ignore[attr-defined]
            workers=None if workers == 0 else workers,
        )
    except ConvertError as exc:
        print(format_report(exc.report), file=sys.stderr)
        raise SystemExit(1) from None
    except (ValueError, RuntimeError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from None
    print(format_report(report))
