# SPDX-License-Identifier: Apache-2.0
"""A fake plugin package for the entry-point tests. Not installed; loaded by a
monkeypatched ``entry_points``."""

from __future__ import annotations

import struct
from collections.abc import Mapping
from pathlib import Path

from coperceive.adapters.build import ManifestAdapter
from coperceive.adapters.spec import builtin_manifest_path, load_manifest


def make_adapter() -> ManifestAdapter:
    return ManifestAdapter(load_manifest(builtin_manifest_path("griffin")))


def read_txt_points(
    path: Path, *, fields: Mapping[str, str], intensity_fill: float | None = None
) -> tuple[int, bytes]:
    """``x y z i`` per line. Ignores ``fields`` on purpose: a plugin owns its layout."""
    rows = [line.split() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    values = [float(v) for row in rows for v in row[:4]]
    return len(rows), struct.pack("<" + "f" * len(values), *values)


read_txt_points.kind = "lidar"  # type: ignore[attr-defined]
