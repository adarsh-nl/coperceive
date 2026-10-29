# SPDX-License-Identifier: Apache-2.0
"""Preview thumbnails written at import time (library cards)."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from coperceive.scene.thumbnail import (
    PREVIEW_EDGE,
    THUMBNAIL_NAME,
    ThumbnailError,
    write_scene_thumbnail,
    write_scene_thumbnails,
)
from PIL import Image


def _scene(root: Path, *, rows: list[str] | None = None) -> Path:
    """A minimal scene: images.parquet listing frames that exist on disk."""
    scene = root / "Town03-000"
    scene.mkdir(parents=True)
    if rows is None:
        rows = ["vehicle-side/camera/front/000001.png", "vehicle-side/camera/back/000001.png"]
    for relative in rows:
        target = scene / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        image = Image.new("RGB", (800, 600), (30, 120, 200))
        image.save(target, format="PNG")
    table = pa.table({"path": pa.array(rows, type=pa.string())})
    pq.write_table(table, scene / "images.parquet")
    return scene


def test_writes_a_bounded_jpeg_preview(tmp_path: Path) -> None:
    scene = _scene(tmp_path)
    result = write_scene_thumbnail(scene)
    assert result is not None and result == scene / THUMBNAIL_NAME
    opened = cast(Image.Image, Image.open(result))
    try:
        assert opened.format == "JPEG"
        assert max(opened.size) <= PREVIEW_EDGE
        assert opened.size == (640, 480)  # same aspect ratio, never upscaled
    finally:
        opened.close()
    assert result.stat().st_size > 0


def test_existing_thumbnail_is_kept_unless_forced(tmp_path: Path) -> None:
    scene = _scene(tmp_path)
    first = write_scene_thumbnail(scene)
    assert first is not None
    mtime = first.stat().st_mtime_ns
    again = write_scene_thumbnail(scene)
    assert again is not None and again == first
    assert again.stat().st_mtime_ns == mtime
    forced = write_scene_thumbnail(scene, force=True)
    assert forced is not None
    assert forced.stat().st_mtime_ns >= mtime


def test_a_scene_without_images_has_no_thumbnail(tmp_path: Path) -> None:
    scene = tmp_path / "empty-scene"
    scene.mkdir()
    assert write_scene_thumbnail(scene) is None


def test_images_that_leave_the_scene_are_skipped(tmp_path: Path) -> None:
    outside = tmp_path / "outside.png"
    Image.new("RGB", (800, 600), (200, 30, 30)).save(outside, format="PNG")
    scene = tmp_path / "scene"
    (scene / "vehicle-side").mkdir(parents=True)
    # A symlink inside the scene pointing at a file outside it must not be read.
    (scene / "vehicle-side/escape.png").symlink_to(outside)
    pq.write_table(
        pa.table(
            {"path": pa.array(["vehicle-side/escape.png", "../outside.png"], type=pa.string())}
        ),
        scene / "images.parquet",
    )
    assert write_scene_thumbnail(scene) is None


def test_a_corrupt_image_is_reported_but_does_not_raise_from_batch(tmp_path: Path) -> None:
    good = _scene(tmp_path / "good", rows=["vehicle-side/camera/front/000001.png"])
    bad = tmp_path / "bad" / "corrupt-scene"
    bad.mkdir(parents=True)
    (bad / "broken.png").write_bytes(b"not an image")
    pq.write_table(
        pa.table({"path": pa.array(["broken.png"], type=pa.string())}),
        bad / "images.parquet",
    )
    written, errors = write_scene_thumbnails([good, bad])
    assert written == ["Town03-000"]
    assert len(errors) == 1 and errors[0].startswith("corrupt-scene:")
    with pytest.raises(ThumbnailError):
        write_scene_thumbnail(bad, force=True)
