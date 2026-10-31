# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import pytest
from coperceive.cli import build_parser, main


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


def test_convert_griffin_reports_a_missing_raw_root_without_a_traceback(
    capsys: pytest.CaptureFixture[str],
) -> None:
    missing = "/nonexistent/griffin-release-for-test"
    with pytest.raises(SystemExit) as caught:
        main(
            [
                "convert",
                "griffin",
                "--subset",
                "25m",
                "--raw-root",
                missing,
                "--out",
                "/tmp/unused-griffin-out",
            ]
        )
    assert caught.value.code == 1
    err = capsys.readouterr().err
    assert f"Griffin raw root does not exist: {missing}" in err
    assert "Traceback" not in err
