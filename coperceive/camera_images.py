# SPDX-License-Identifier: Apache-2.0
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportAttributeAccessIssue=false
"""Camera images of a prepared cluster scene: read the originals, or keep previews.

A prepared scene never copies camera frames at full size and never writes under
the raw dataset root. Its cache directory holds a link ``raw`` to the dataset
root, and ``images.parquet`` paths go through it:

- ``originals``: ``raw/<path>``, the dataset's own file, served from the
  allow-listed media root;
- ``previews``: ``previews/<path>.jpg``, a 640 px JPEG written next to the
  scene; the original stays reachable as ``raw/<path>`` for full resolution.

See docs/datasets/cluster-datasets.md, "Camera images".
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Literal

import pyarrow as pa
import pyarrow.parquet as pq
from PIL import Image

from coperceive.adapters.prefetch import io_threads
from coperceive.images import open_image
from coperceive.store import IMAGE_SCHEMA

ImageMode = Literal["originals", "previews"]
IMAGE_MODES: tuple[ImageMode, ...] = ("originals", "previews")
RAW_LINK = "raw"
PREVIEWS_DIR = "previews"
PREVIEW_SUFFIX = ".jpg"
PREVIEW_WIDTH = 640
PREVIEW_QUALITY = 80
# Disk estimate per preview shown before confirming: a 640 px JPEG of a CARLA
# camera frame at quality 80 is 25-55 KB; 40 KB is the middle.
PREVIEW_BYTES_ESTIMATE = 40_000
PREVIEWS_SCHEMA = "coperceive-cluster-previews-1"


class CameraImageError(ValueError):
    """A recorded image path or mode that cannot be staged safely."""


def original_of(path: str) -> str:
    """The dataset-relative path behind a recorded image path, in either mode."""
    if path.startswith(f"{PREVIEWS_DIR}/") and path.endswith(PREVIEW_SUFFIX):
        return path[len(PREVIEWS_DIR) + 1 : -len(PREVIEW_SUFFIX)]
    if path.startswith(f"{RAW_LINK}/"):
        return path[len(RAW_LINK) + 1 :]
    return path


def served_candidates(path: str, *, full: bool) -> list[str]:
    """Scene-relative paths to try for one image, best first.

    A preview falls back to the original (previews removed, or none was written);
    ``full`` asks for the original only.
    """
    if path.startswith(f"{PREVIEWS_DIR}/") and path.endswith(PREVIEW_SUFFIX):
        original = f"{RAW_LINK}/{original_of(path)}"
        return [original] if full else [path, original]
    return [path]


def _plain_relative(path: str) -> str:
    parts = Path(path).parts
    if path == "" or Path(path).is_absolute() or ".." in parts:
        raise CameraImageError(f"image path escapes the dataset root: {path}")
    return path


def _link_raw(scene_dir: Path, raw_root: Path) -> None:
    link = scene_dir / RAW_LINK
    if link.is_symlink() and os.readlink(link) == str(raw_root):
        return
    if link.exists() and not link.is_symlink():
        raise CameraImageError(f"{link} exists and is not the dataset link")
    temporary = scene_dir / f".{RAW_LINK}.tmp"
    if temporary.is_symlink() or temporary.exists():
        temporary.unlink()
    os.symlink(str(raw_root), temporary)
    os.replace(temporary, link)


def _write_preview(source: Path, target: Path) -> bool:
    """A 640 px wide JPEG of ``source`` at ``target``; False when there is no source."""
    if not source.is_file():
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp")
    with open_image(source) as image:
        image.draft("RGB", (PREVIEW_WIDTH, PREVIEW_WIDTH))
        frame = image.convert("RGB")
        if frame.width > PREVIEW_WIDTH:
            height = max(1, round(frame.height * PREVIEW_WIDTH / frame.width))
            frame = frame.resize((PREVIEW_WIDTH, height), Image.Resampling.BILINEAR)
        frame.save(temporary, "JPEG", quality=PREVIEW_QUALITY, optimize=True)
    os.replace(temporary, target)
    return True


def stage_images(scene_dir: Path, raw_root: Path, mode: str) -> dict[str, int]:
    """Point a scene's images at the originals or at previews, writing what that needs.

    Writes only inside ``scene_dir``: the ``raw`` link, ``previews/`` (previews
    mode) and ``images.parquet``. Switching to originals removes the previews.
    Returns ``{"images": n, "previews": written}``.
    """
    if mode not in IMAGE_MODES:
        raise CameraImageError(f"camera images must be one of {', '.join(IMAGE_MODES)}")
    raw = raw_root.resolve()
    _link_raw(scene_dir, raw)
    table_path = scene_dir / "images.parquet"
    table = pq.read_table(table_path, columns=list(IMAGE_SCHEMA.names))
    originals = [_plain_relative(original_of(path)) for path in table.column("path").to_pylist()]
    written = 0
    if mode == "previews":
        paths: list[str] = []
        todo: list[tuple[str, Path]] = []
        for original in originals:
            target = scene_dir / PREVIEWS_DIR / f"{original}{PREVIEW_SUFFIX}"
            if not target.is_file():
                todo.append((original, target))
            paths.append(f"{PREVIEWS_DIR}/{original}{PREVIEW_SUFFIX}")

        def preview(item: tuple[str, Path]) -> bool:
            original, target = item
            source = (raw / original).resolve()
            # A link inside the dataset that leads out of it is not read.
            return source.is_relative_to(raw) and _write_preview(source, target)

        # Each original is a cold read on a shared filesystem (~150 ms before
        # the first byte): read and encode several at once.
        with ThreadPoolExecutor(max_workers=max(1, io_threads())) as pool:
            written = sum(pool.map(preview, todo))
    else:
        paths = [f"{RAW_LINK}/{original}" for original in originals]
        previews = scene_dir / PREVIEWS_DIR
        if previews.is_dir() and not previews.is_symlink():
            shutil.rmtree(previews)
    rewritten = table.set_column(
        table.schema.get_field_index("path"), "path", pa.array(paths, pa.string())
    )
    temporary = table_path.with_name(".images.parquet.tmp")
    pq.write_table(rewritten.cast(IMAGE_SCHEMA), temporary)
    os.replace(temporary, table_path)
    return {"images": len(paths), "previews": written}


def _real_dirs(parent: Path) -> Iterator[Path]:
    try:
        with os.scandir(parent) as entries:
            for entry in entries:
                if entry.is_dir(follow_symlinks=False) and not entry.name.startswith("."):
                    yield Path(entry.path)
    except OSError:
        return


def preview_dirs(cache_root: Path) -> Iterator[Path]:
    """``<adapter>/<hash>/<split>/<scene>/previews`` below the cache root; never via a link."""
    for adapter in _real_dirs(cache_root):
        for digest in _real_dirs(adapter):
            for split in _real_dirs(digest):
                for scene in _real_dirs(split):
                    previews = scene / PREVIEWS_DIR
                    if previews.is_dir() and not previews.is_symlink():
                        yield previews


def _tree_size(root: Path) -> tuple[int, int]:
    total = files = 0
    for folder, _dirs, names in os.walk(root, followlinks=False):
        for name in names:
            try:
                stat = os.lstat(os.path.join(folder, name))
            except OSError:
                continue
            total += stat.st_size
            files += 1
    return total, files


def previews_usage(cache_root: str | Path, *, remove: bool = False) -> dict[str, object]:
    """Disk used by previews below a profile's cache root; ``remove`` deletes only them.

    A removed scene's images point back at the originals, so it stays viewable.
    """
    root = Path(cache_root)
    total = files = scenes = 0
    removed: list[Path] = []
    for previews in preview_dirs(root) if root.is_dir() else ():
        size, count = _tree_size(previews)
        total += size
        files += count
        scenes += 1
        if remove:
            scene_dir = previews.parent
            shutil.rmtree(previews)
            if (scene_dir / "images.parquet").is_file() and (scene_dir / RAW_LINK).is_symlink():
                stage_images(scene_dir, Path(os.readlink(scene_dir / RAW_LINK)), "originals")
            removed.append(scene_dir)
    if remove:
        _mark_originals(removed)
        return {
            "schema": PREVIEWS_SCHEMA,
            "bytes": 0,
            "files": 0,
            "scenes": 0,
            "removed": {"bytes": total, "files": files, "scenes": scenes},
        }
    return {"schema": PREVIEWS_SCHEMA, "bytes": total, "files": files, "scenes": scenes}


def _mark_originals(scene_dirs: list[Path]) -> None:
    from coperceive.cluster_datasets import mark_scene_images

    for scene_dir in scene_dirs:
        mark_scene_images(scene_dir.parent, scene_dir.name, "originals")


__all__ = [
    "IMAGE_MODES",
    "PREVIEWS_SCHEMA",
    "PREVIEW_BYTES_ESTIMATE",
    "CameraImageError",
    "ImageMode",
    "original_of",
    "previews_usage",
    "served_candidates",
    "stage_images",
]
