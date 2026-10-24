# SPDX-License-Identifier: Apache-2.0
"""Tests for the dataset catalog and runner."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from coperceive.catalog import dry_run_preset, load_catalog
from coperceive.cli import main
from coperceive.griffin.download import (
    DownloadReport,
    Hub,
    PlannedFile,
    RemoteFile,
    download_griffin,
)


def _report(size: int) -> DownloadReport:
    planned = PlannedFile(
        path="datasets/griffin_50scenes_25m/vehicle_lidar.zip",
        size=size,
        archive="vehicle_lidar",
        role="zip",
    )
    return DownloadReport(
        subset="25m",
        folder="griffin_50scenes_25m",
        dest=Path("/tmp/unused"),
        files=(planned,),
        required_free_bytes=size * 2,
        notice="mit",
        acknowledgement="not acknowledged",
        dry_run=True,
        self_md5_note="",
        downloaded=(),
        skipped=(),
        release_root=None,
    )


def test_catalog_structure() -> None:
    catalog = load_catalog()
    assert len(catalog) >= 4
    by_id = {d["id"]: d for d in catalog}

    griffin = by_id["griffin"]
    assert griffin["name"] == "Griffin"
    assert griffin["status"] == "ready"
    assert griffin["licence"] == "MIT"
    assert griffin["licence_url"] == (
        "https://raw.githubusercontent.com/wang-jh18-SVM/Griffin/main/LICENSE.txt"
    )
    assert griffin["source_url"] == "https://huggingface.co/datasets/wjh-svm/Griffin"
    assert griffin["paper_url"] == "https://arxiv.org/abs/2503.06983"
    preset_ids = [p["id"] for p in griffin["presets"]]
    assert preset_ids == ["dev", "one-scene", "full"]
    for preset in griffin["presets"]:
        assert "MB" not in preset["name"]
        assert "GB" not in preset["name"]
        assert "estimated_download_bytes" not in preset

    opv2v = by_id["opv2v"]
    assert opv2v["status"] == "local-import"
    assert opv2v["licence"] == "unknown"
    assert "licence_url" not in opv2v

    v2xset = by_id["v2xset"]
    assert v2xset["status"] == "local-import"
    assert v2xset["licence"] == "unknown"

    v2v4real = by_id["v2v4real"]
    assert v2v4real["status"] == "planned"
    assert v2v4real["licence"] == "unknown"
    assert v2v4real["paper_url"] == "https://arxiv.org/abs/2303.07601"


def test_dry_run_preset_headroom(tmp_path: Path) -> None:
    # required_free_bytes is twice the listed size. Headroom remains when
    # free - required is at least 10% of free.
    with (
        patch("coperceive.catalog.runner.download_griffin", return_value=_report(475)),
        patch("shutil.disk_usage") as mock_usage,
    ):
        mock_usage.return_value = MagicMock(total=10_000, free=1000)
        res = dry_run_preset("griffin", "dev", tmp_path)
        assert res["disk_bytes"] == 950
        assert res["has_headroom"] is False

    with (
        patch("coperceive.catalog.runner.download_griffin", return_value=_report(400)),
        patch("shutil.disk_usage") as mock_usage,
    ):
        mock_usage.return_value = MagicMock(total=10_000, free=1000)
        res = dry_run_preset("griffin", "dev", tmp_path)
        assert res["has_headroom"] is True
        assert res["licence"] == "MIT"
        assert res["size_source"].startswith("Hugging Face dataset wjh-svm/Griffin")
        assert res["download_bytes"] == 400


def test_dry_run_does_not_invent_a_size_when_the_listing_fails(tmp_path: Path) -> None:
    with patch(
        "coperceive.catalog.runner.download_griffin",
        side_effect=RuntimeError("listing failed"),
    ):
        with pytest.raises(RuntimeError, match="dataset host"):
            dry_run_preset("griffin", "dev", tmp_path)


def test_cli_dataset_catalog(capsys: pytest.CaptureFixture[str]) -> None:
    main(["dataset", "catalog"])
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert any(d["id"] == "griffin" for d in data)


def test_cli_dataset_dry_run(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    with patch("coperceive.catalog.runner.download_griffin", return_value=_report(42)):
        main(["dataset", "dry-run", "griffin", "dev", "--dest", str(tmp_path)])
    captured = capsys.readouterr()
    lines = [line.strip() for line in captured.out.strip().splitlines() if line.strip()]
    json_text = "\n".join(lines[lines.index("{") :])
    data = json.loads(json_text)
    assert data["event"] == "dry_run"
    assert data["dataset_id"] == "griffin"
    assert data["preset_id"] == "dev"
    assert data["download_bytes"] == 42


class _Hub(Hub):
    def __init__(self) -> None:
        self.files = [
            RemoteFile("datasets/griffin_50scenes_25m/md5.txt", 10),
            RemoteFile("datasets/griffin_50scenes_25m/vehicle_lidar.zip", 100),
            RemoteFile("datasets/griffin_50scenes_25m/drone_camera_front.zip", 50),
        ]

    def list_files(self, folder: str) -> list[RemoteFile]:
        assert folder == "griffin_50scenes_25m"
        return list(self.files)

    def read_dataset_card(self) -> str:
        return "---\nlicense: mit\n---\n"

    def read_toolkit_license(self) -> str:
        return "MIT License\n\nCopyright (c) [2025] [Jiahao Wang]\n"


def test_star_components_sums_every_archive(tmp_path: Path) -> None:
    report = download_griffin(
        tmp_path,
        subset="25m",
        components="*",
        dry_run=True,
        hub=_Hub(),
    )
    assert report.dry_run is True
    assert sum(item.size for item in report.files) == 160
