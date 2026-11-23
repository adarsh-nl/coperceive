# SPDX-License-Identifier: Apache-2.0
"""``coperceive --version`` and ``coperceive.__version__`` are the package metadata's
version (one source: ``[project] version`` in pyproject.toml, coperceive/_version.py)."""

from __future__ import annotations

import subprocess
import sys
from importlib import metadata
from pathlib import Path

import coperceive
import pytest
from coperceive import _version


def test_the_version_is_the_package_metadata() -> None:
    assert coperceive.__version__ == metadata.version("coperceive")


def test_coperceive_dash_dash_version_prints_the_package_metadata() -> None:
    done = subprocess.run(
        [sys.executable, "-m", "coperceive", "--version"],
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    )
    assert done.stdout.strip() == f"coperceive {metadata.version('coperceive')}"


def test_an_uninstalled_checkout_reads_its_pyproject(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def missing(_name: str) -> str:
        raise metadata.PackageNotFoundError("coperceive")

    monkeypatch.setattr(metadata, "version", missing)
    # This checkout's pyproject.toml, the file a release bumps.
    root = Path(_version.__file__).resolve().parent.parent
    expected = _version._from_pyproject(root)  # pyright: ignore[reportPrivateUsage]
    assert expected is not None and expected != _version.UNKNOWN
    assert _version.version() == expected
    # Another project's pyproject.toml, or none, is not taken for coperceive's.
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "other"\nversion = "9.9.9"\n', encoding="utf-8"
    )
    assert _version._from_pyproject(tmp_path) is None  # pyright: ignore[reportPrivateUsage]
    assert _version._from_pyproject(tmp_path / "none") is None  # pyright: ignore[reportPrivateUsage]
