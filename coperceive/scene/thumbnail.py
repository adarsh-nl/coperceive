# SPDX-License-Identifier: Apache-2.0
"""One small preview image per scene, written at import time.

The library card shows a 640 px JPEG preview; the full-resolution camera frames
stay untouched and are read on demand (the preview tier of
``docs/spec/predictions.md``/pack). The thumbnail is derived only from an image
listed in the scene's ``images.parquet``, must resolve inside the scene, and is
best effort: a scene without images still imports, it simply has no thumbnail.
"""

from __future__ import annotations

import io
import os
from collections.abc import Iterable
from pathlib import Path
from typing import cast

import pyarrow as pa
import pyarrow.parquet as pq

THUMBNAIL_NAME = "thumbnail.jpg"
PREVIEW_EDGE = 640
PREVIEW_QUALITY = 85
MAX_THUMBNAIL_BYTES = 2 * 1024 * 1024


class ThumbnailError(RuntimeError):
    """A thumbnail could not be produced. The import itself is unaffected."""


def thumbnail_path(scene: Path) -> Path:
    return scene / THUMBNAIL_NAME


def _rows(table: pa.Table) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for item in table.to_pylist():
        row: dict[str, object] = {}
        for key, value in item.items():
            row[key] = value
        rows.append(row)
    return rows


def _image_paths(scene: Path) -> list[str]:
    table_path = scene / "images.parquet"
    if not table_path.is_file():
        return []
    table: pa.Table = pq.read_table(table_path)
    if "path" not in table.schema.names:
        return []
    return [
        cast(str, row["path"])
        for row in _rows(table)
        if isinstance(row.get("path"), str) and row["path"]
    ]


def _first_frame(scene: Path) -> Path | None:
    """The first listed frame that exists inside the scene.

    Vehicle/front frames read best on a card, so they are preferred; otherwise
    the first listed frame is used. Symlinks that leave the scene are skipped.
    """
    root = scene.resolve()
    rows = _image_paths(scene)
    if not rows:
        return None

    def rank(relative: str) -> tuple[int, int]:
        parts = relative.lower()
        vehicle = 0 if "vehicle" in parts else 1
        front = 0 if "front" in parts else 1
        return (vehicle, front)

    for relative in sorted(rows, key=rank):
        candidate = (scene / relative).resolve()
        try:
            if not candidate.is_relative_to(root) or not candidate.is_file():
                continue
        except ValueError:
            continue
        return candidate
    return None


def write_scene_thumbnail(scene: Path, *, force: bool = False) -> Path | None:
    """Write ``scene/thumbnail.jpg`` at preview tier; return it, or None.

    Idempotent: an existing thumbnail is kept unless ``force``. The image is
    downscaled to at most 640 px on its long edge as JPEG, never upscaled.
    """
    from PIL import Image

    target = thumbnail_path(scene)
    if target.exists() and not force:
        return target
    source = _first_frame(scene)
    if source is None:
        return None
    try:
        opened = cast(Image.Image, Image.open(source))
    except (OSError, ValueError) as exc:
        raise ThumbnailError(f"could not read an image for the thumbnail: {exc}") from exc
    try:
        converted: Image.Image = opened.convert("RGB")
        converted.thumbnail((PREVIEW_EDGE, PREVIEW_EDGE), Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        converted.save(buffer, format="JPEG", quality=PREVIEW_QUALITY, optimize=True)
        payload = buffer.getvalue()
    except (OSError, ValueError) as exc:
        raise ThumbnailError(f"could not read an image for the thumbnail: {exc}") from exc
    finally:
        opened.close()
    if not payload or len(payload) > MAX_THUMBNAIL_BYTES:
        raise ThumbnailError("the thumbnail came out empty or too large")
    tmp = target.with_suffix(".jpg.tmp")
    try:
        tmp.write_bytes(payload)
        os.replace(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)
    return target


def write_scene_thumbnails(scenes: Iterable[Path]) -> tuple[list[str], list[str]]:
    """Best-effort thumbnails for imported scenes.

    Returns ``(written_scene_names, error_strings)``. Errors are reported, never
    raised: a thumbnail failure must not fail the import.
    """
    written: list[str] = []
    errors: list[str] = []
    for scene in scenes:
        try:
            result = write_scene_thumbnail(scene)
        except (ThumbnailError, OSError) as exc:
            errors.append(f"{scene.name}: {exc}")
            continue
        if result is not None:
            written.append(scene.name)
    return written, errors
