# SPDX-License-Identifier: Apache-2.0
"""Find model plugins: ``coperceive.models`` entry points and manifest files (spec §Discovery).

An entry point's value, once loaded, is a ``str`` or ``Path`` to the plugin's manifest. The
plugin is never imported into coperceive's interpreter beyond that attribute.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import metadata
from pathlib import Path

from coperceive.errors import CoperceiveError
from coperceive.models.spec import GROUP, Manifest, load_manifest


class RegistryError(CoperceiveError):
    """No plugin by that name, or an entry point that does not name a manifest."""


@dataclass(frozen=True)
class PluginInfo:
    name: str
    source: str  # entry-point
    location: str


_entries: dict[str, metadata.EntryPoint] | None = None


def clear_cache() -> None:
    global _entries
    _entries = None


def list_plugins() -> tuple[PluginInfo, ...]:
    return tuple(
        PluginInfo(name, "entry-point", str(getattr(entry, "value", "")))
        for name, entry in sorted(_entry_points().items())
    )


def resolve_manifest(name: str | None, *, manifest: Path | None) -> Manifest:
    """``--manifest FILE`` wins; else the entry point ``name``; else an error naming the known."""
    if manifest is not None:
        if not manifest.is_file():
            raise RegistryError(f"no manifest file at {manifest}")
        return load_manifest(manifest)
    if name is None:
        raise RegistryError("a plugin name or --manifest FILE is required")
    entry = _entry_points().get(name)
    if entry is None:
        known = ", ".join(info.name for info in list_plugins())
        raise RegistryError(f"no model plugin named {name!r}; known: {known or 'none'}")
    loaded: object = entry.load()
    if not isinstance(loaded, (str, Path)):
        raise RegistryError(f"entry point {name!r} did not name a manifest file")
    path = Path(loaded)
    if not path.is_file():
        raise RegistryError(f"entry point {name!r} names a missing manifest {path}")
    loaded_manifest = load_manifest(path)
    if loaded_manifest.name != name:
        raise RegistryError(
            f"entry point {name!r} names a manifest called {loaded_manifest.name!r}"
        )
    return loaded_manifest


def _entry_points() -> dict[str, metadata.EntryPoint]:
    global _entries
    if _entries is None:
        _entries = {entry.name: entry for entry in metadata.entry_points(group=GROUP)}
    return _entries


__all__ = ["PluginInfo", "RegistryError", "clear_cache", "list_plugins", "resolve_manifest"]
