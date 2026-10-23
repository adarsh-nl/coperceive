# SPDX-License-Identifier: Apache-2.0
"""``coperceive scene pack`` on a synthetic scene. No Griffin frames."""

from __future__ import annotations

import io
import json
import os
from pathlib import Path
from typing import cast

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from coperceive.cli import main
from coperceive.scene.pack import PackError, bytes_with_margin, exceeds_copy_cap, pack_scene
from coperceive.viewdata import dataset_root_error, scene_directories
from PIL import Image


def _png(path: Path, size: tuple[int, int] = (800, 400)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, (200, 20, 20)).save(path, format="PNG")


def _images(path: Path, rows: list[str]) -> None:
    count = len(rows)
    table = pa.table(
        {
            "agent_id": pa.array(["vehicle"] * count),
            "sensor_id": pa.array(["cam"] * count),
            "path": pa.array(rows),
            "time_us": pa.array([0] * count, type=pa.int64()),
        }
    )
    pq.write_table(table, path)


def _scene(tmp: Path) -> tuple[Path, Path, Path]:
    media = tmp / "media"
    outside = tmp / "outside"
    scene = tmp / "scene"
    scene.mkdir()
    (scene / "manifest.json").write_text('{"scene_id":"scene"}\n', encoding="utf-8")
    (scene / "points.bin").write_bytes(b"points-ok")
    _png(media / "cam" / "frame.png")
    _png(outside / "secret.png", (8, 8))
    os.symlink(media / "cam", scene / "cam")
    os.symlink(outside / "secret.png", scene / "leak")
    os.symlink(scene / "missing.png", scene / "gone")
    _images(
        scene / "images.parquet",
        ["cam/frame.png", "leak", "gone", "/etc/passwd", "../outside/secret.png"],
    )
    return scene, media, outside


def _events(buffer: io.StringIO) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for line in buffer.getvalue().splitlines():
        if not line.strip():
            continue
        loaded: object = json.loads(line)
        if not isinstance(loaded, dict):
            continue
        row: dict[str, object] = {}
        for key, value in cast(dict[object, object], loaded).items():
            if isinstance(key, str):
                row[key] = value
        rows.append(row)
    return rows


def _as_int(value: object) -> int:
    if type(value) is not int:
        raise AssertionError(value)
    return value


def test_pack_copies_media_symlink_and_refuses_the_rest(tmp_path: Path) -> None:
    scene, media, _outside = _scene(tmp_path)
    dest = tmp_path / "library"
    buffer = io.StringIO()
    final = pack_scene(
        scene,
        dest,
        images="full",
        media_roots=[media],
        out=buffer,
    )
    assert final == dest / "scene"
    assert final is not None
    copied = final / "cam" / "frame.png"
    assert copied.is_file()
    assert not copied.is_symlink()
    assert copied.read_bytes() == (media / "cam" / "frame.png").read_bytes()
    assert not (final / "leak").exists()
    assert not (dest / "scene.partial").exists()
    errors = [row for row in _events(buffer) if row["event"] == "error"]
    messages = " ".join(str(row["error"]) for row in errors)
    assert "refused" in messages
    assert "dangling symlink" in messages
    assert "/etc/passwd" in messages
    assert ".." in messages
    assert scene_directories(dest, require_complete=True) == [final]
    record = json.loads((final / "IMPORT.json").read_text(encoding="utf-8"))
    assert record["tool_version"]
    assert record["source"].endswith("scene")
    listed = {row["path"]: row for row in record["files"]}
    assert listed["cam/frame.png"]["size"] == copied.stat().st_size
    assert len(listed["points.bin"]["sha256"]) == 64


def test_resume_skips_verified_file_and_replaces_corruption(tmp_path: Path) -> None:
    scene = tmp_path / "scene"
    scene.mkdir()
    (scene / "manifest.json").write_text('{"scene_id":"scene"}\n', encoding="utf-8")
    (scene / "points.bin").write_bytes(b"points-ok")
    partial = tmp_path / "library" / "scene.partial"
    partial.mkdir(parents=True)
    manifest = partial / "manifest.json"
    manifest.write_text('{"scene_id":"scene"}\n', encoding="utf-8")
    os.utime(manifest, (1_000, 1_000))
    (partial / "points.bin").write_bytes(b"POINTS-NO")
    buffer = io.StringIO()
    final = pack_scene(scene, tmp_path / "library", images="none", out=buffer)
    assert final is not None
    assert (final / "manifest.json").stat().st_mtime == 1_000
    assert (final / "points.bin").read_bytes() == b"points-ok"
    skipped = [row for row in _events(buffer) if row["event"] == "file" and row["skipped"] is True]
    assert [row["path"] for row in skipped] == ["manifest.json"]
    assert not partial.exists()


def test_preview_rewrites_paths_and_limits_the_long_edge(tmp_path: Path) -> None:
    scene, media, _outside = _scene(tmp_path)
    buffer = io.StringIO()
    final = pack_scene(
        scene,
        tmp_path / "library",
        images="preview",
        media_roots=[media],
        out=buffer,
    )
    assert final is not None
    preview = final / "cam" / "frame.jpg"
    assert preview.is_file()
    assert not (final / "cam" / "frame.png").exists()
    with Image.open(preview) as image:
        assert max(image.size) <= 640
        assert image.format == "JPEG"
    table = pq.read_table(final / "images.parquet")
    first = table.to_pylist()[0]
    assert isinstance(first, dict)
    assert first.get("path") == "cam/frame.jpg"
    plan = next(row for row in _events(buffer) if row["event"] == "plan")
    assert _as_int(plan["bytes"]) > 0


def test_dry_run_reports_bytes_and_writes_nothing(tmp_path: Path) -> None:
    scene, media, _outside = _scene(tmp_path)
    dest = tmp_path / "library"
    buffer = io.StringIO()
    assert (
        pack_scene(scene, dest, images="full", media_roots=[media], dry_run=True, out=buffer)
        is None
    )
    assert not dest.exists()
    plan = next(row for row in _events(buffer) if row["event"] == "plan")
    assert plan["dry_run"] is True
    assert _as_int(plan["bytes"]) > 0


def test_partial_scene_is_not_listed(tmp_path: Path) -> None:
    library = tmp_path / "library"
    done = library / "scene"
    done.mkdir(parents=True)
    (done / "manifest.json").write_text("{}", encoding="utf-8")
    (done / "COMPLETE.json").write_text("{}", encoding="utf-8")
    partial = library / "scene.partial"
    partial.mkdir()
    (partial / "manifest.json").write_text("{}", encoding="utf-8")
    (partial / "COMPLETE.json").write_text("{}", encoding="utf-8")
    assert scene_directories(library, require_complete=True) == [done]
    assert scene_directories(partial, require_complete=True) == []
    only = tmp_path / "only"
    hidden = only / "other.partial"
    hidden.mkdir(parents=True)
    (hidden / "manifest.json").write_text("{}", encoding="utf-8")
    (hidden / "COMPLETE.json").write_text("{}", encoding="utf-8")
    assert dataset_root_error(only) is not None


def test_griffin_root_materialises_a_relative_image(tmp_path: Path) -> None:
    scene = tmp_path / "scene"
    scene.mkdir()
    (scene / "manifest.json").write_text('{"scene_id":"scene"}\n', encoding="utf-8")
    griffin = tmp_path / "griffin"
    _png(griffin / "vehicle-side" / "camera" / "back" / "000.png", (4, 4))
    _images(scene / "images.parquet", ["vehicle-side/camera/back/000.png"])
    final = pack_scene(scene, tmp_path / "library", images="full", griffin_root=griffin)
    assert final is not None
    copied = final / "vehicle-side" / "camera" / "back" / "000.png"
    source = griffin / "vehicle-side" / "camera" / "back" / "000.png"
    assert copied.read_bytes() == source.read_bytes()


def test_free_space_keeps_a_ten_percent_margin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert bytes_with_margin(100) == 110
    assert bytes_with_margin(0) == 0
    scene = tmp_path / "scene"
    scene.mkdir()
    (scene / "manifest.json").write_text("{}\n", encoding="utf-8")
    (scene / "points.bin").write_bytes(b"12345")

    def tiny(_path: Path) -> object:
        return type("Usage", (), {"free": 1})()

    monkeypatch.setattr("coperceive.scene.pack.shutil.disk_usage", tiny)
    try:
        pack_scene(scene, tmp_path / "library", images="none")
    except PackError as exc:
        assert "10% margin" in str(exc)
    else:
        raise AssertionError("expected a free-space error")


def _payload_bytes(directory: Path) -> int:
    total = 0
    for path in directory.rglob("*"):
        if path.is_file() and path.name not in ("IMPORT.json", "COMPLETE.json"):
            total += path.stat().st_size
    return total


@pytest.mark.parametrize("images", ["full", "preview", "none"])
def test_linked_camera_dir_does_not_copy_unlisted_frames(tmp_path: Path, images: str) -> None:
    camera = tmp_path / "media" / "vehicle-side" / "camera"
    _png(camera / "listed.png", (32, 32))
    for index in range(50):
        (camera / f"other-{index:02d}.png").write_bytes(f"other-scene-{index:02d}".encode())
    scene = tmp_path / "Town03-000"
    (scene / "vehicle-side").mkdir(parents=True)
    (scene / "manifest.json").write_text('{"scene_id":"Town03-000"}\n', encoding="utf-8")
    (scene / "points.bin").write_bytes(b"points-ok")
    os.symlink(camera, scene / "vehicle-side" / "camera")
    _images(scene / "images.parquet", ["vehicle-side/camera/listed.png"])
    buffer = io.StringIO()
    final = pack_scene(
        scene,
        tmp_path / "library",
        images=images,
        media_roots=[tmp_path / "media"],
        out=buffer,
    )
    assert final is not None
    events = _events(buffer)
    notes = [str(row["message"]) for row in events if row.get("event") == "note"]
    assert len(notes) == 1
    assert notes[0].startswith("linked directory not copied: vehicle-side/camera → ")
    assert str(camera) in notes[0]
    copied = [path.name for path in (final / "vehicle-side").rglob("*") if path.is_file()]
    if images == "full":
        assert copied == ["listed.png"]
    elif images == "preview":
        assert copied == ["listed.jpg"]
    else:
        assert copied == []
        table = pq.read_table(final / "images.parquet")
        assert table.num_rows == 0
        assert "path" in table.schema.names
    plan = next(row for row in events if row["event"] == "plan")
    assert plan["bytes_estimated"] is False
    assert _as_int(plan["bytes"]) == _payload_bytes(final)
    assert not any(name.startswith("other-") for name in copied)


def test_preview_dry_run_estimates_more_than_twenty_frames(tmp_path: Path) -> None:
    scene = tmp_path / "scene"
    scene.mkdir()
    (scene / "manifest.json").write_text("{}\n", encoding="utf-8")
    camera = tmp_path / "media" / "cam"
    rows: list[str] = []
    for index in range(21):
        name = f"frame-{index:02d}.png"
        _png(camera / name, (8, 8))
        rows.append(f"cam/{name}")
    os.symlink(camera, scene / "cam")
    _images(scene / "images.parquet", rows)
    buffer = io.StringIO()
    assert (
        pack_scene(
            scene,
            tmp_path / "library",
            images="preview",
            media_roots=[tmp_path / "media"],
            dry_run=True,
            out=buffer,
        )
        is None
    )
    plan = next(row for row in _events(buffer) if row["event"] == "plan")
    assert plan["bytes_estimated"] is True
    assert _as_int(plan["files"]) == 23  # manifest, rewritten table, 21 previews


def test_copy_cap_refuses_a_plan_over_three_times_the_scene(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert exceeds_copy_cap(9, 3) is False
    assert exceeds_copy_cap(10, 3) is True
    scene = tmp_path / "scene"
    scene.mkdir()
    (scene / "manifest.json").write_text("{}\n", encoding="utf-8")
    from dataclasses import replace

    import coperceive.scene.pack as pack

    original = pack.build_plan

    def collapsed(
        scene: Path,
        *,
        images: str,
        media_roots: list[Path],
        griffin_root: Path | None,
    ) -> pack.Plan:
        return replace(
            original(
                scene,
                images=images,
                media_roots=media_roots,
                griffin_root=griffin_root,
            ),
            baseline_bytes=0,
        )

    monkeypatch.setattr(pack, "build_plan", collapsed)
    with pytest.raises(PackError, match="3×"):
        pack_scene(scene, tmp_path / "out", images="none")
    final = pack_scene(scene, tmp_path / "forced", images="none", force=True)
    assert final is not None
    assert (final / "manifest.json").is_file()


def test_cli_pack_dry_run(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    scene = tmp_path / "scene"
    scene.mkdir()
    (scene / "manifest.json").write_text("{}\n", encoding="utf-8")
    main(["scene", "pack", str(scene), str(tmp_path / "out"), "--images", "none", "--dry-run"])
    captured = capsys.readouterr()
    event = json.loads(captured.out.strip().splitlines()[-1])
    assert event["event"] == "plan"
    assert event["dry_run"] is True
    assert not (tmp_path / "out").exists()
