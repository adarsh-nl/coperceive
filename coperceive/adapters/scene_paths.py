# SPDX-License-Identifier: Apache-2.0
"""Scene ids are one safe path part, and their paths stay inside a library root."""

from __future__ import annotations

import re
from pathlib import Path

from coperceive.errors import CoperceiveError

_WINDOWS_RESERVED = frozenset(
    {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
)
_SAFE = re.compile(r"^[^/\\:\x00-\x1f]+$")


class ScenePathError(CoperceiveError):
    """A scene id or its resolved path would leave the library root."""


def safe_scene_id(value: object) -> str:
    """A scene id that is exactly one path component on every supported OS."""
    if not isinstance(value, str) or not value:
        raise ScenePathError("unsafe scene id: empty")
    if value in {".", ".."}:
        raise ScenePathError(f"unsafe scene id: {value!r}")
    if not _SAFE.fullmatch(value):
        raise ScenePathError(f"unsafe scene id: {value!r} contains a path separator or NUL")
    if value != value.strip() or value.endswith("."):
        raise ScenePathError(f"unsafe scene id: {value!r} has a trailing dot or space")
    if value.split(".", 1)[0].upper() in _WINDOWS_RESERVED:
        raise ScenePathError(f"unsafe scene id: {value!r} is a reserved Windows name")
    return value


def scene_path(root: Path, scene_id: str) -> Path:
    """``root / safe_scene_id(scene_id)`` with a resolved containment check.

    A pre-existing symlink at the destination is refused: a scene directory is a
    real directory under the library root, never a link to somewhere else.
    """
    safe = safe_scene_id(scene_id)
    root_resolved = root.resolve()
    candidate = root / safe
    try:
        if candidate.is_symlink():
            raise ScenePathError(f"unsafe scene path: {scene_id!r} is a symlink")
        parent = candidate.parent.resolve()
    except OSError as exc:
        raise ScenePathError(f"unsafe scene path for {scene_id!r}: {exc}") from None
    if parent != root_resolved and root_resolved not in parent.parents:
        raise ScenePathError(f"unsafe scene path for {scene_id!r}: outside {root_resolved}")
    return candidate


def remove_scene_tree(path: Path, root: Path, scene_id: str) -> None:
    """Delete a scene directory only after proving it is a real child of ``root``."""
    target = scene_path(root, scene_id)
    if target.is_symlink():
        raise ScenePathError(f"unsafe scene path: {scene_id!r} is a symlink")
    if path != target:
        raise ScenePathError(f"unsafe scene path for {scene_id!r}")
    if path.exists():
        import shutil

        shutil.rmtree(path)
