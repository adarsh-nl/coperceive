# SPDX-License-Identifier: Apache-2.0
"""Reader names a manifest may use: the built-ins plus ``coperceive.readers`` entry points.

A plugin reader is a callable with a ``kind`` attribute: ``record``
(``(path) -> dict``), ``table`` (``(path) -> list[dict]``) or ``lidar``
(``(path, *, fields, intensity_fill=None) -> (count, bytes)``). Plugins run in
the CLI process, like any installed package.
"""

from __future__ import annotations

from collections.abc import Callable
from importlib import metadata

GROUP = "coperceive.readers"
KINDS = ("record", "table", "lidar")

BUILTIN_RECORD = ("json_record", "yaml_record")
BUILTIN_TABLE = ("json_table", "csv_rows")
BUILTIN_TEXT = ("text_rows",)
BUILTIN_LIDAR = ("ply", "pcd", "bin_f32")

_cache: dict[str, dict[str, Callable[..., object]]] | None = None


def plugin_readers() -> dict[str, dict[str, Callable[..., object]]]:
    """``{kind: {name: callable}}`` from installed entry points. Cached."""
    global _cache
    if _cache is not None:
        return _cache
    found: dict[str, dict[str, Callable[..., object]]] = {kind: {} for kind in KINDS}
    for entry in metadata.entry_points(group=GROUP):
        loaded = entry.load()
        kind = getattr(loaded, "kind", None)
        if kind not in KINDS or not callable(loaded):
            raise ValueError(
                f"reader entry point {entry.name!r} must be callable with kind in {KINDS}"
            )
        found[kind][entry.name] = loaded
    _cache = found
    return found


def clear_cache() -> None:
    global _cache
    _cache = None


def names(kind: str) -> tuple[str, ...]:
    builtin = {"record": BUILTIN_RECORD, "table": BUILTIN_TABLE, "lidar": BUILTIN_LIDAR}[kind]
    return builtin + tuple(sorted(plugin_readers()[kind]))


def plugin(kind: str, name: str) -> Callable[..., object] | None:
    return plugin_readers()[kind].get(name)
