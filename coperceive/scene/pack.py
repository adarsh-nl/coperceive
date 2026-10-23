# SPDX-License-Identifier: Apache-2.0
"""Pack one scene into a self-contained directory.

The output is ``<dest>/<scene>.partial`` until ``COMPLETE.json`` is in place.
The directory is then renamed to ``<dest>/<scene>``. A later run skips a file
whose size and sha256 already match.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

import pyarrow as pa
import pyarrow.parquet as pq

from coperceive import __version__

IMAGE_TABLE = "images.parquet"
IMPORT_NAME = "IMPORT.json"
COMPLETE_NAME = "COMPLETE.json"
PREVIEW_EDGE = 640
PREVIEW_QUALITY = 85


class PackError(Exception):
    """The pack cannot start, or the destination volume is too small."""


@dataclass(frozen=True)
class PlannedFile:
    """One file that will land in the packed scene."""

    dest_rel: str
    source: Path | None
    kind: str  # "copy", "preview", or "table"
    table_paths: tuple[str, ...] | None = None


@dataclass(frozen=True)
class Plan:
    scene_name: str
    files: tuple[PlannedFile, ...]
    errors: tuple[dict[str, str], ...]
    image_paths: tuple[str, ...]


def bytes_with_margin(planned: int) -> int:
    """Free space required: the planned bytes plus a 10% margin, rounded up."""
    if planned < 0:
        raise ValueError("planned bytes must be >= 0")
    return planned + (planned + 9) // 10


def _emit(out: TextIO, event: dict[str, object]) -> None:
    print(json.dumps(event, separators=(",", ":")), file=out, flush=True)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _inside(real: Path, roots: list[Path]) -> bool:
    for root in roots:
        try:
            if real.is_relative_to(root):
                return True
        except ValueError:
            continue
    return False


def _reject_relative(text: str) -> str | None:
    if text == "" or Path(text).is_absolute() or ".." in Path(text).parts:
        return f"refused: {text!r} is absolute or contains '..'"
    return None


def _missing_reason(scene: Path, relative: str) -> str:
    current = scene
    for part in Path(relative).parts:
        current = current / part
        if current.is_symlink() and not current.exists():
            return f"dangling symlink: {relative}"
    return f"file not found: {relative}"


def _candidates(
    scene: Path,
    relative: str,
    media_roots: list[Path],
    griffin_root: Path | None,
) -> list[Path]:
    found = [scene / relative]
    found.extend(root / relative for root in media_roots)
    if griffin_root is not None:
        found.append(griffin_root / relative)
    return found


def _resolve(
    scene: Path,
    relative: str,
    roots: list[Path],
    media_roots: list[Path],
    griffin_root: Path | None,
) -> Path:
    refused = _reject_relative(relative)
    if refused is not None:
        raise PackError(refused)
    for candidate in _candidates(scene, relative, media_roots, griffin_root):
        if candidate.is_symlink() and not candidate.exists():
            raise PackError(f"dangling symlink: {relative}")
        if not candidate.exists():
            continue
        real = Path(os.path.realpath(candidate))
        if not real.is_file():
            continue
        if not _inside(real, roots):
            raise PackError(
                f"refused: {relative} resolves outside the scene, media root, and Griffin root"
            )
        return real
    raise PackError(_missing_reason(scene, relative))


def _preview_jpeg(source: Path) -> bytes:
    from PIL import Image

    with Image.open(source) as image:
        converted = image.convert("RGB")
        converted.thumbnail((PREVIEW_EDGE, PREVIEW_EDGE), Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        converted.save(buffer, format="JPEG", quality=PREVIEW_QUALITY)
        return buffer.getvalue()


def _rows(table: pa.Table) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for item in table.to_pylist():
        row: dict[str, object] = {}
        for key, value in item.items():
            row[key] = value
        rows.append(row)
    return rows


def _jpg_rel(relative: str) -> str:
    return Path(relative).with_suffix(".jpg").as_posix()


def _image_column(scene: Path) -> tuple[str, ...] | None:
    table_path = scene / IMAGE_TABLE
    if not table_path.is_file():
        return None
    table = pq.read_table(table_path)
    if "path" not in table.schema.names:
        raise PackError(f"{IMAGE_TABLE} has no path column")
    paths: list[str] = []
    for row in _rows(table):
        value = row.get("path")
        if not isinstance(value, str):
            raise PackError(f"{IMAGE_TABLE} path column has a non-string value")
        paths.append(value)
    return tuple(paths)


def _walk_files(scene: Path, roots: list[Path]) -> tuple[dict[str, Path], list[dict[str, str]]]:
    found: dict[str, Path] = {}
    errors: list[dict[str, str]] = []

    def walk(directory: Path) -> None:
        try:
            children = sorted(directory.iterdir(), key=lambda path: path.name)
        except OSError as exc:
            errors.append({"path": directory.name, "error": f"cannot list directory: {exc}"})
            return
        for child in children:
            relative = child.relative_to(scene).as_posix()
            if child.name in (IMPORT_NAME, COMPLETE_NAME) or child.name.endswith(".writing"):
                continue
            if child.is_symlink():
                if not child.exists():
                    errors.append({"path": relative, "error": f"dangling symlink: {relative}"})
                    continue
                real = Path(os.path.realpath(child))
                if not _inside(real, roots):
                    errors.append(
                        {
                            "path": relative,
                            "error": (
                                f"refused: {relative} resolves outside the scene, "
                                "media root, and Griffin root"
                            ),
                        }
                    )
                    continue
                if child.is_dir():
                    walk(child)
                    continue
                if not real.is_file():
                    errors.append({"path": relative, "error": f"refused: {relative} is not a file"})
                    continue
                found[relative] = real
                continue
            if child.is_dir():
                walk(child)
                continue
            if child.is_file():
                found[relative] = child

    walk(scene)
    return found, errors


def build_plan(
    scene: Path,
    *,
    images: str,
    media_roots: list[Path],
    griffin_root: Path | None,
) -> Plan:
    if images not in ("full", "preview", "none"):
        raise PackError("images must be full, preview, or none")
    scene = scene.resolve()
    if not scene.is_dir():
        raise PackError(f"not a scene directory: {scene}")
    if scene.name.startswith(".") or scene.name.endswith(".partial"):
        raise PackError(f"refused scene name: {scene.name}")
    if not (scene / "manifest.json").is_file():
        raise PackError(f"scene has no manifest.json: {scene}")
    roots = [scene, *[root.resolve() for root in media_roots]]
    if griffin_root is not None:
        roots.append(griffin_root.resolve())
    walked, errors = _walk_files(scene, roots)
    image_paths = _image_column(scene)
    listed = set(image_paths or ())
    seen_errors = {item["path"] for item in errors}
    files: dict[str, PlannedFile] = {}
    for relative, source in walked.items():
        if relative in listed:
            continue
        if images == "preview" and relative == IMAGE_TABLE:
            continue
        files[relative] = PlannedFile(relative, source, "copy")
    column: list[str] = []
    for relative in image_paths or ():
        refused = _reject_relative(relative)
        if refused is not None:
            if relative not in seen_errors:
                errors.append({"path": relative, "error": refused})
                seen_errors.add(relative)
            column.append(relative)
            continue
        try:
            real = _resolve(scene, relative, roots, media_roots, griffin_root)
        except PackError as exc:
            if relative not in seen_errors:
                errors.append({"path": relative, "error": str(exc)})
                seen_errors.add(relative)
            column.append(relative)
            continue
        if images == "none":
            column.append(relative)
            continue
        if images == "preview":
            dest_rel = _jpg_rel(relative)
            files[dest_rel] = PlannedFile(dest_rel, real, "preview")
            column.append(dest_rel)
            continue
        files[relative] = PlannedFile(relative, real, "copy")
        column.append(relative)
    if images == "preview" and image_paths is not None:
        files[IMAGE_TABLE] = PlannedFile(
            IMAGE_TABLE,
            scene / IMAGE_TABLE,
            "table",
            tuple(column),
        )
    ordered = tuple(files[key] for key in sorted(files))
    return Plan(scene.name, ordered, tuple(errors), tuple(column))


def _payload_size(item: PlannedFile) -> int:
    if item.kind == "preview":
        if item.source is None:
            return 0
        return len(_preview_jpeg(item.source))
    if item.kind == "table":
        if item.source is None or item.table_paths is None:
            return 0
        return len(_rewrite_table(item.source, list(item.table_paths)))
    if item.source is None:
        return 0
    return item.source.stat().st_size


def _rewrite_table(table_path: Path, paths: list[str]) -> bytes:
    table = pq.read_table(table_path)
    rows = _rows(table)
    if len(rows) != len(paths):
        raise PackError(f"{IMAGE_TABLE} row count does not match the path list")
    for row, path in zip(rows, paths, strict=True):
        row["path"] = path
    rebuilt = pa.Table.from_pylist(rows, schema=table.schema)
    buffer = io.BytesIO()
    pq.write_table(rebuilt, buffer)
    return buffer.getvalue()


def _write_bytes(dest: Path, data: bytes) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    temporary = dest.with_name(dest.name + ".writing")
    temporary.write_bytes(data)
    os.replace(temporary, dest)


def _copy_file(source: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    temporary = dest.with_name(dest.name + ".writing")
    shutil.copyfile(source, temporary)
    os.replace(temporary, dest)


def _same_payload(dest: Path, size: int, digest: str) -> bool:
    if not dest.is_file():
        return False
    if dest.stat().st_size != size:
        return False
    return _sha256(dest) == digest


def _materialise(item: PlannedFile, dest: Path) -> tuple[int, str, bool]:
    """Write one file. Returns size, sha256, and whether the write was skipped."""
    if item.kind == "preview":
        if item.source is None:
            raise PackError(f"preview has no source: {item.dest_rel}")
        data = _preview_jpeg(item.source)
        digest = hashlib.sha256(data).hexdigest()
        if _same_payload(dest, len(data), digest):
            return len(data), digest, True
        _write_bytes(dest, data)
        return len(data), digest, False
    if item.kind == "table":
        if item.source is None or item.table_paths is None:
            raise PackError(f"table has no source: {item.dest_rel}")
        data = _rewrite_table(item.source, list(item.table_paths))
        digest = hashlib.sha256(data).hexdigest()
        if _same_payload(dest, len(data), digest):
            return len(data), digest, True
        _write_bytes(dest, data)
        return len(data), digest, False
    if item.source is None:
        raise PackError(f"file has no source: {item.dest_rel}")
    size = item.source.stat().st_size
    digest = _sha256(item.source)
    if _same_payload(dest, size, digest):
        return size, digest, True
    _copy_file(item.source, dest)
    return size, digest, False


def _free_bytes(path: Path) -> int:
    probe = path if path.exists() else path.parent
    while not probe.exists():
        parent = probe.parent
        if parent == probe:
            break
        probe = parent
    return int(shutil.disk_usage(probe).free)


def pack_scene(
    scene: Path,
    dest_root: Path,
    *,
    images: str,
    media_roots: list[Path] | None = None,
    griffin_root: Path | None = None,
    dry_run: bool = False,
    out: TextIO | None = None,
) -> Path | None:
    """Pack ``scene`` under ``dest_root``. Returns the final directory, or None for a dry run."""
    import sys

    sink = sys.stdout if out is None else out
    plan = build_plan(
        scene,
        images=images,
        media_roots=[] if media_roots is None else media_roots,
        griffin_root=griffin_root,
    )
    for error in plan.errors:
        _emit(sink, {"event": "error", "path": error["path"], "error": error["error"]})
    sizes = {item.dest_rel: _payload_size(item) for item in plan.files}
    planned = sum(sizes.values())
    final = dest_root / plan.scene_name
    partial = dest_root / f"{plan.scene_name}.partial"
    remaining = planned
    if partial.is_dir():
        remaining = 0
        for item in plan.files:
            target = partial / item.dest_rel
            if target.is_file() and target.stat().st_size == sizes[item.dest_rel]:
                continue
            remaining += sizes[item.dest_rel]
    required = bytes_with_margin(remaining)
    free = _free_bytes(dest_root if dest_root.exists() else dest_root.parent)
    _emit(
        sink,
        {
            "event": "plan",
            "files": len(plan.files),
            "bytes": planned,
            "remaining_bytes": remaining,
            "required_bytes": required,
            "free_bytes": free,
            "images": images,
            "dry_run": dry_run,
        },
    )
    if free < required:
        raise PackError(
            f"not enough free space: {free} bytes free, {required} bytes required "
            f"({remaining} bytes plus a 10% margin)"
        )
    if dry_run:
        return None
    if final.exists():
        raise PackError(f"destination already exists: {final}")
    partial.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, object]] = []
    done = 0
    copied = 0
    total_bytes = 0
    for item in plan.files:
        target = partial / item.dest_rel
        size, digest, skipped = _materialise(item, target)
        done += 1
        if not skipped:
            copied += size
        records.append({"path": item.dest_rel, "size": size, "sha256": digest})
        total_bytes += size
        _emit(
            sink,
            {
                "event": "file",
                "path": item.dest_rel,
                "bytes": size,
                "done": done,
                "total": len(plan.files),
                "skipped": skipped,
            },
        )
    source = scene.resolve()
    manifest = {
        "source": str(source),
        "tool_version": __version__,
        "images": images,
        "files": records,
        "errors": [dict(error) for error in plan.errors],
    }
    _write_bytes(
        partial / IMPORT_NAME,
        (json.dumps(manifest, indent=2) + "\n").encode("utf-8"),
    )
    marker = {
        "tool": "coperceive scene pack",
        "tool_version": __version__,
        "images": images,
        "source": str(source),
    }
    _write_bytes(
        partial / COMPLETE_NAME,
        (json.dumps(marker, indent=2) + "\n").encode("utf-8"),
    )
    os.rename(partial, final)
    _emit(
        sink,
        {
            "event": "done",
            "directory": str(final),
            "files": len(records),
            "bytes": total_bytes,
            "copied_bytes": copied,
            "errors": len(plan.errors),
        },
    )
    return final
