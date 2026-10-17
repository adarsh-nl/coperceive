# SPDX-License-Identifier: Apache-2.0
"""Find adapters: built-in manifests, ``coperceive.adapters`` entry points, manifest files.

An entry point value is an adapter instance, a zero-argument factory that
returns one, or a path (``str`` or ``Path``) to a manifest file.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import metadata
from pathlib import Path

from coperceive.adapters.build import ManifestAdapter
from coperceive.adapters.ingest import Adapter
from coperceive.adapters.readers import registry as readers
from coperceive.adapters.spec import (
    SpecError,
    builtin_manifest_names,
    builtin_manifest_path,
    load_manifest,
)
from coperceive.errors import CoperceiveError

GROUP = "coperceive.adapters"


class RegistryError(CoperceiveError):
    """No adapter by that name, or an entry point that is not an adapter."""


@dataclass(frozen=True)
class AdapterInfo:
    name: str
    source: str  # builtin | entry-point
    location: str


_entries: dict[str, metadata.EntryPoint] | None = None


def clear_cache() -> None:
    global _entries
    _entries = None
    readers.clear_cache()


def list_adapters() -> tuple[AdapterInfo, ...]:
    rows = [
        AdapterInfo(name, "builtin", str(builtin_manifest_path(name)))
        for name in builtin_manifest_names()
    ]
    for name, entry in sorted(_entry_points().items()):
        rows.append(AdapterInfo(name, "entry-point", str(getattr(entry, "value", ""))))
    return tuple(rows)


def resolve_adapter(spec: str) -> Adapter:
    """A built-in name, an entry point name, or a path to a manifest file."""
    path = Path(spec)
    if spec.endswith((".yaml", ".yml")) or path.is_file():
        if not path.is_file():
            raise RegistryError(f"no manifest file at {spec}")
        return ManifestAdapter(load_manifest(path))
    if spec in builtin_manifest_names():
        return ManifestAdapter(load_manifest(builtin_manifest_path(spec)))
    entry = _entry_points().get(spec)
    if entry is None:
        known = ", ".join(info.name for info in list_adapters())
        raise RegistryError(f"no adapter named {spec!r}; known: {known or 'none'}")
    loaded: object = entry.load()
    if isinstance(loaded, (str, Path)):
        return ManifestAdapter(load_manifest(Path(loaded)))
    if callable(loaded) and not _looks_like_adapter(loaded):
        loaded = loaded()
    if not _looks_like_adapter(loaded):
        raise RegistryError(f"entry point {spec!r} did not produce an adapter")
    return loaded  # type: ignore[return-value]


def _looks_like_adapter(value: object) -> bool:
    return all(hasattr(value, attr) for attr in ("name", "scenes", "load", "check"))


def _entry_points() -> dict[str, metadata.EntryPoint]:
    global _entries
    if _entries is None:
        _entries = {entry.name: entry for entry in metadata.entry_points(group=GROUP)}
    return _entries


__all__ = [
    "AdapterInfo",
    "RegistryError",
    "SpecError",
    "clear_cache",
    "list_adapters",
    "resolve_adapter",
]
