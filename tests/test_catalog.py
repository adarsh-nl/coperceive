# SPDX-License-Identifier: Apache-2.0
"""Tests for the dataset catalog and runner."""

from __future__ import annotations

import io
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from coperceive.catalog import dry_run_preset, get_dataset, load_catalog
from coperceive.catalog.runner import run_dataset_job
from coperceive.cli import build_parser, main


def test_catalog_structure() -> None:
    catalog = load_catalog()
    assert len(catalog) >= 4
    by_id = {d["id"]: d for d in catalog}

    # Griffin
    griffin = by_id["griffin"]
    assert griffin["name"] == "Griffin"
    assert griffin["status"] == "ready"
    assert griffin["licence"] == "MIT"
    assert "https://" in griffin["licence_url"]
    assert "https://" in griffin["source_url"]
    assert "https://" in griffin["paper_url"]
    preset_ids = [p["id"] for p in griffin["presets"]]
    assert "dev" in preset_ids
    assert "one-scene" in preset_ids
    assert "full" in preset_ids

    # OPV2V
    opv2v = by_id["opv2v"]
    assert opv2v["name"] == "OPV2V"
    assert opv2v["status"] == "local-import"
    assert opv2v["licence"] == "unknown"

    # V2XSet
    v2xset = by_id["v2xset"]
    assert v2xset["name"] == "V2XSet"
    assert v2xset["status"] == "local-import"
    assert v2xset["licence"] == "unknown"

    # V2V4Real
    v2v4real = by_id["v2v4real"]
    assert v2v4real["name"] == "V2V4Real"
    assert v2v4real["status"] == "planned"


def test_dry_run_preset_headroom(tmp_path: Path) -> None:
    # Test headroom refusal when disk free space is low
    with patch("shutil.disk_usage") as mock_usage:
        # total: 100GB, free: 1GB, estimated needed: 500MB -> (1GB - 500MB) = 500MB < 10% of 1GB (100MB)?
        # Wait: free is 500MB, total 100GB, needed 500MB -> remaining 0 < 10% of 500MB -> has_headroom False
        mock_usage.return_value = MagicMock(total=100 * 1024**3, free=500 * 1024**2)
        res = dry_run_preset("griffin", "dev", tmp_path)
        assert res["has_headroom"] is False

    # Test headroom ok when plenty of free space
    with patch("shutil.disk_usage") as mock_usage:
        mock_usage.return_value = MagicMock(total=100 * 1024**3, free=50 * 1024**3)
        res = dry_run_preset("griffin", "dev", tmp_path)
        assert res["has_headroom"] is True
        assert res["licence"] == "MIT"
        assert res["download_bytes"] > 0


def test_cli_dataset_catalog(capsys: pytest.CaptureFixture[str]) -> None:
    main(["dataset", "catalog"])
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert any(d["id"] == "griffin" for d in data)


def test_cli_dataset_dry_run(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    main(["dataset", "dry-run", "griffin", "dev", "--dest", str(tmp_path)])
    captured = capsys.readouterr()
    # The output may have tqdm progress bars before the json output
    lines = [line.strip() for line in captured.out.strip().splitlines() if line.strip()]
    json_text = "\n".join(lines[lines.index("{"):])
    data = json.loads(json_text)
    assert data["event"] == "dry_run"
    assert data["dataset_id"] == "griffin"
    assert data["preset_id"] == "dev"
    assert data["has_headroom"] in (True, False)
