# SPDX-License-Identifier: Apache-2.0
"""Ensure all release-versioned manifests, lockfiles, and scripts agree."""

from __future__ import annotations

import json
import re
import shutil
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def collect_versions(root: Path) -> dict[str, str]:
    """Read the version string from every release-versioned file under ``root``."""
    versions: dict[str, str] = {}

    pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    versions["pyproject.toml"] = str(pyproject["project"]["version"])

    uv_lock = tomllib.loads((root / "uv.lock").read_text(encoding="utf-8"))
    uv_pkg = next(
        (pkg for pkg in uv_lock.get("package", []) if pkg.get("name") == "coperceive"),
        None,
    )
    assert uv_pkg is not None, "coperceive entry missing from uv.lock"
    versions["uv.lock:coperceive"] = str(uv_pkg["version"])

    cargo_toml = tomllib.loads((root / "src-tauri" / "Cargo.toml").read_text(encoding="utf-8"))
    versions["src-tauri/Cargo.toml"] = str(cargo_toml["package"]["version"])

    cargo_lock = tomllib.loads((root / "src-tauri" / "Cargo.lock").read_text(encoding="utf-8"))
    cargo_pkg = next(
        (pkg for pkg in cargo_lock.get("package", []) if pkg.get("name") == "coperceive-desktop"),
        None,
    )
    assert cargo_pkg is not None, "coperceive-desktop entry missing from src-tauri/Cargo.lock"
    versions["src-tauri/Cargo.lock:coperceive-desktop"] = str(cargo_pkg["version"])

    tauri_conf = json.loads((root / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8"))
    versions["src-tauri/tauri.conf.json"] = str(tauri_conf["version"])

    site_pkg = json.loads((root / "site" / "package.json").read_text(encoding="utf-8"))
    versions["site/package.json"] = str(site_pkg["version"])

    site_lock = json.loads((root / "site" / "package-lock.json").read_text(encoding="utf-8"))
    versions["site/package-lock.json"] = str(site_lock["version"])
    versions["site/package-lock.json:packages['']"] = str(site_lock["packages"][""]["version"])

    sync_sh = (root / "scripts" / "desktop" / "sync_version.sh").read_text(encoding="utf-8")
    m_echo = re.search(r'\|\|\s*echo\s+"([^"]+)"', sync_sh)
    m_fallback = re.search(r'if \[ -z "\$\{VERSION\}" \].*?\n\s*VERSION="([^"]+)"', sync_sh)
    assert m_echo is not None, "fallback echo missing in scripts/desktop/sync_version.sh"
    assert m_fallback is not None, "default VERSION missing in scripts/desktop/sync_version.sh"
    versions["scripts/desktop/sync_version.sh:echo"] = m_echo.group(1)
    versions["scripts/desktop/sync_version.sh:default"] = m_fallback.group(1)

    return versions


def assert_versions_agree(root: Path) -> dict[str, str]:
    """Assert that every collected version source matches ``pyproject.toml``."""
    versions = collect_versions(root)
    expected = versions["pyproject.toml"]
    mismatches = {k: v for k, v in versions.items() if v != expected}
    assert not mismatches, (
        f"Version sources disagree (expected {expected!r} from pyproject.toml): {mismatches}"
    )
    return versions


def test_all_version_sources_agree() -> None:
    versions = assert_versions_agree(ROOT)
    assert versions["pyproject.toml"] == "0.1.2"


def test_version_mismatch_is_detected(tmp_path: Path) -> None:
    """Mutating any single version source must cause assert_versions_agree to fail."""
    files = (
        "pyproject.toml",
        "uv.lock",
        "src-tauri/Cargo.toml",
        "src-tauri/Cargo.lock",
        "src-tauri/tauri.conf.json",
        "site/package.json",
        "site/package-lock.json",
        "scripts/desktop/sync_version.sh",
    )
    for rel in files:
        dest = tmp_path / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / rel, dest)

    assert_versions_agree(tmp_path)

    for rel in files:
        backup = (tmp_path / rel).read_text(encoding="utf-8")
        try:
            mutated = backup.replace("0.1.2", "0.1.1", 1)
            assert mutated != backup, f"expected 0.1.2 in {rel}"
            (tmp_path / rel).write_text(mutated, encoding="utf-8")
            with pytest.raises(AssertionError, match="Version sources disagree"):
                assert_versions_agree(tmp_path)
        finally:
            (tmp_path / rel).write_text(backup, encoding="utf-8")
