# SPDX-License-Identifier: Apache-2.0
"""Coperceive's version, from one place: ``[project] version`` in ``pyproject.toml``.

Installing the package (``pip``/``uv``, editable installs included) writes that value into
the distribution's metadata, and :func:`version` reads it from there. A source checkout that
was never installed (its root on ``PYTHONPATH``, no ``coperceive`` distribution) has no
metadata: the value is then read from the ``pyproject.toml`` beside the package. With
neither, the version is ``0+unknown``, a valid PEP 440 local version that sorts below every
release, so nothing can mistake it for one.

An editable install records the version at install time: after a bump in
``pyproject.toml``, reinstall (``uv sync`` or ``pip install -e .``) to refresh it.
"""

from __future__ import annotations

import tomllib
from importlib import metadata
from pathlib import Path

DISTRIBUTION = "coperceive"
UNKNOWN = "0+unknown"


def _from_pyproject(root: Path) -> str | None:
    """``[project] version`` of ``root/pyproject.toml`` when that project is coperceive."""
    try:
        data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return None
    project = data.get("project")
    if not isinstance(project, dict):
        return None
    name, value = project.get("name"), project.get("version")  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    if name != DISTRIBUTION or not isinstance(value, str) or not value:
        return None
    return value


def version() -> str:
    """The installed distribution's version, else the checkout's ``pyproject.toml``, else
    ``0+unknown``."""
    try:
        return metadata.version(DISTRIBUTION)
    except metadata.PackageNotFoundError:
        pass
    return _from_pyproject(Path(__file__).resolve().parent.parent) or UNKNOWN
