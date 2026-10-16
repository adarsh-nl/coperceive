# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from coperceive.cli import build_parser


def test_griffin_commands_parse() -> None:
    parser = build_parser()
    download = parser.parse_args(
        [
            "download",
            "griffin",
            "--subset",
            "25m",
            "--preset",
            "dev",
            "--dest",
            "/tmp/griffin",
            "--dry-run",
            "--accept-griffin-terms",
        ]
    )
    assert download.subset == "25m"
    assert download.preset == "dev"
    assert download.dry_run is True
    convert = parser.parse_args(
        [
            "convert",
            "griffin",
            "--subset",
            "40m",
            "--raw-root",
            "/tmp/raw",
            "--out",
            "/tmp/out",
            "--workers",
            "2",
        ]
    )
    assert convert.subset == "40m"
    assert convert.workers == 2
