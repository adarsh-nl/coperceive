# SPDX-License-Identifier: Apache-2.0
"""Tests for scripts/desktop/write_latest_json.py (the updater manifest)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "desktop" / "write_latest_json.py"
_spec = importlib.util.spec_from_file_location("write_latest_json", _SCRIPT)
assert _spec is not None and _spec.loader is not None
wlj = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wlj)


def _setup(tmp_path: Path, version: str = "0.1.1", sig: str | None = "c2lnbmF0dXJl") -> Path:
    conf = tmp_path / "tauri.conf.json"
    conf.write_text(json.dumps({"version": version}))
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "Coperceive.app.tar.gz").write_bytes(b"archive")
    if sig is not None:
        (assets / "Coperceive.app.tar.gz.sig").write_text(sig + "\n")
    (assets / "Coperceive_0.1.1_aarch64.dmg").write_bytes(b"dmg")
    return conf


def _run(tmp_path: Path, conf: Path, tag: str) -> int:
    return wlj.main(
        [
            "--conf",
            str(conf),
            "--tag",
            tag,
            "--assets",
            str(tmp_path / "assets"),
            "--repo",
            "adarsh-nl/coperceive",
            "--out",
            str(tmp_path / "latest.json"),
        ]
    )


def test_writes_a_manifest_for_the_matching_tag(tmp_path: Path) -> None:
    conf = _setup(tmp_path)
    assert _run(tmp_path, conf, "v0.1.1") == 0
    manifest = json.loads((tmp_path / "latest.json").read_text())
    assert manifest["version"] == "0.1.1"
    platform = manifest["platforms"]["darwin-aarch64"]
    assert platform["signature"] == "c2lnbmF0dXJl"
    assert platform["url"] == (
        "https://github.com/adarsh-nl/coperceive/releases/download/v0.1.1/Coperceive.app.tar.gz"
    )
    assert manifest["pub_date"].endswith("Z")


def test_refuses_a_tag_that_does_not_match_the_bundle_version(tmp_path: Path) -> None:
    conf = _setup(tmp_path, version="0.1.0")
    assert _run(tmp_path, conf, "v0.1.1") == 1
    assert not (tmp_path / "latest.json").exists()


def test_refuses_a_missing_signature(tmp_path: Path) -> None:
    conf = _setup(tmp_path, sig=None)
    with pytest.raises(SystemExit, match="did not sign"):
        _run(tmp_path, conf, "v0.1.1")


def test_refuses_an_empty_signature(tmp_path: Path) -> None:
    conf = _setup(tmp_path, sig="")
    assert _run(tmp_path, conf, "v0.1.1") == 1
