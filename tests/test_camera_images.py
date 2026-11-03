# SPDX-License-Identifier: Apache-2.0
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportAttributeAccessIssue=false, reportArgumentType=false, reportOperatorIssue=false
"""Camera images of prepared cluster scenes: originals or previews, never under the raw root."""

from __future__ import annotations

import io
import json
import os
from pathlib import Path

import pyarrow.parquet as pq
import pytest
from coperceive.app.server import create_app
from coperceive.camera_images import (
    PREVIEW_WIDTH,
    CameraImageError,
    previews_usage,
    stage_images,
)
from coperceive.cluster_datasets import DatasetError, prepare_cluster_dataset
from fastapi.testclient import TestClient
from PIL import Image

from tests.opencood_release import two_cav_scene
from tests.opencood_release import write_release as write_opencood

SCENE = "scene_0001"
BIG = "test/scene_0001/641/000069_camera0.png"


def _raw(root: Path) -> Path:
    write_opencood(root, "test", [two_cav_scene(SCENE), two_cav_scene("scene_0002")])
    big = root / BIG
    assert big.is_file(), sorted(p.name for p in big.parent.iterdir())[:8]
    Image.new("RGB", (1280, 720), (40, 90, 160)).save(big)
    return root


def _snapshot(root: Path) -> dict[str, tuple[int, int]]:
    return {
        str(path.relative_to(root)): (path.stat().st_size, path.stat().st_mtime_ns)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _paths(scene_dir: Path) -> list[str]:
    return pq.read_table(scene_dir / "images.parquet").column("path").to_pylist()


def _client(cache: Path, raw: Path | None) -> TestClient:
    media = () if raw is None else (raw,)
    return TestClient(create_app(root=cache, token=None, media_roots=media))


def _image(client: TestClient, path: str, **extra: object) -> tuple[int, str]:
    agent, rest = path.split("/")[2], path.rsplit("/", 1)[1]
    frame, sensor = rest.removesuffix(".png").split("_", 1)
    params = {"time_us": int(frame) * 50_000, "agent_id": agent, "sensor_id": sensor, **extra}
    response = client.get(f"/api/scenes/{SCENE}/image", params=params)
    return response.status_code, response.headers.get("content-type", "")


def test_originals_link_the_raw_root_and_serve_the_dataset_png(tmp_path: Path) -> None:
    raw = _raw(tmp_path / "opv2v")
    before = _snapshot(raw)
    cache = tmp_path / "results" / "cache" / "test"
    prepare_cluster_dataset("opv2v", raw, cache, scenes=[SCENE], split="test")
    scene = cache / SCENE
    assert (scene / "raw").is_symlink() and os.readlink(scene / "raw") == str(raw.resolve())
    paths = _paths(scene)
    assert paths and all(path.startswith("raw/test/scene_0001/") for path in paths), paths[:3]
    assert not (scene / "previews").exists()
    dataset = json.loads((cache / "dataset.json").read_text())
    assert dataset["scenes"][SCENE]["images"] == "originals"
    assert _image(_client(cache, raw), BIG) == (200, "image/png")
    # Without the dataset as a media root the link leads nowhere servable.
    assert _image(_client(cache, None), BIG)[0] == 400
    assert _snapshot(raw) == before


def test_previews_are_640px_jpegs_in_the_cache_and_full_reads_the_original(
    tmp_path: Path,
) -> None:
    raw = _raw(tmp_path / "opv2v")
    before = _snapshot(raw)
    cache = tmp_path / "results" / "cache" / "test"
    prepare_cluster_dataset("opv2v", raw, cache, scenes=[SCENE], split="test", images="previews")
    scene = cache / SCENE
    paths = _paths(scene)
    assert all(p.startswith("previews/test/") and p.endswith(".png.jpg") for p in paths), paths[:3]
    preview = scene / "previews" / f"{BIG}.jpg"
    with Image.open(preview) as image:
        assert image.format == "JPEG" and image.width == PREVIEW_WIDTH and image.height == 360
    client = _client(cache, raw)
    assert _image(client, BIG) == (200, "image/jpeg")
    assert _image(client, BIG, full="true") == (200, "image/png")
    dataset = json.loads((cache / "dataset.json").read_text())
    assert dataset["scenes"][SCENE]["images"] == "previews"
    assert _snapshot(raw) == before


def test_switching_modes_restages_without_ingesting_again(tmp_path: Path) -> None:
    raw = _raw(tmp_path / "opv2v")
    cache = tmp_path / "results" / "cache" / "test"
    prepare_cluster_dataset("opv2v", raw, cache, scenes=[SCENE], split="test")
    manifest = cache / SCENE / "manifest.json"
    ingested = manifest.stat().st_mtime_ns
    prepare_cluster_dataset("opv2v", raw, cache, scenes=[SCENE], split="test", images="previews")
    assert manifest.stat().st_mtime_ns == ingested
    assert (cache / SCENE / "previews" / f"{BIG}.jpg").is_file()
    prepare_cluster_dataset("opv2v", raw, cache, scenes=[SCENE], split="test")
    assert manifest.stat().st_mtime_ns == ingested
    assert not (cache / SCENE / "previews").exists()
    assert _paths(cache / SCENE)[0].startswith("raw/")
    with pytest.raises(DatasetError, match="camera images must be one of"):
        prepare_cluster_dataset("opv2v", raw, cache, scenes=[SCENE], split="test", images="all")


def test_removing_previews_keeps_scenes_viewable_and_touches_nothing_else(
    tmp_path: Path,
) -> None:
    raw = _raw(tmp_path / "opv2v")
    before = _snapshot(raw)
    cache_root = tmp_path / "results" / "coperceive-cache"
    cache = cache_root / "opv2v" / "abc123" / "test"
    prepare_cluster_dataset("opv2v", raw, cache, split="test", images="previews")
    usage = previews_usage(cache_root)
    assert usage["scenes"] == 2 and usage["files"] > 0 and int(str(usage["bytes"])) > 0
    scene_files = {
        str(p.relative_to(cache))
        for p in cache.rglob("*")
        if p.is_file() and "previews" not in p.parts and not p.is_symlink()
    }
    removed = previews_usage(cache_root, remove=True)
    assert removed["removed"] == {k: usage[k] for k in ("bytes", "files", "scenes")}
    assert previews_usage(cache_root)["bytes"] == 0
    assert not list(cache.glob("*/previews"))
    after_files = {
        str(p.relative_to(cache))
        for p in cache.rglob("*")
        if p.is_file() and "previews" not in p.parts and not p.is_symlink()
    }
    assert after_files == scene_files
    dataset = json.loads((cache / "dataset.json").read_text())
    assert {r["images"] for r in dataset["scenes"].values()} == {"originals"}
    assert _image(_client(cache, raw), BIG) == (200, "image/png")
    assert _snapshot(raw) == before


def test_a_recorded_path_that_climbs_out_is_refused(tmp_path: Path) -> None:
    raw = _raw(tmp_path / "opv2v")
    cache = tmp_path / "results" / "cache" / "test"
    prepare_cluster_dataset("opv2v", raw, cache, scenes=[SCENE], split="test")
    scene = cache / SCENE
    table = pq.read_table(scene / "images.parquet")
    climbing = ["raw/../../../etc/passwd", *table.column("path").to_pylist()[1:]]
    pq.write_table(table.set_column(3, "path", [climbing]), scene / "images.parquet")
    with pytest.raises(CameraImageError, match="escapes the dataset root"):
        stage_images(scene, raw, "previews")
    client = _client(cache, raw)
    first = table.slice(0, 1).to_pylist()[0]
    response = client.get(
        f"/api/scenes/{SCENE}/image",
        params={
            "time_us": first["time_us"],
            "agent_id": first["agent_id"],
            "sensor_id": first["sensor_id"],
        },
    )
    assert response.status_code == 400, response.text


def test_a_link_inside_the_dataset_that_leads_out_of_it_is_not_served(tmp_path: Path) -> None:
    raw = _raw(tmp_path / "opv2v")
    outside = tmp_path / "secret.png"
    Image.new("RGB", (8, 8)).save(outside)
    target = raw / BIG
    target.unlink()
    target.symlink_to(outside)
    cache = tmp_path / "results" / "cache" / "test"
    prepare_cluster_dataset("opv2v", raw, cache, scenes=[SCENE], split="test", images="previews")
    # No preview is made from it, and neither the preview nor the original is served.
    assert not (cache / SCENE / "previews" / f"{BIG}.jpg").exists()
    client = _client(cache, raw)
    assert _image(client, BIG)[0] in (400, 404)
    assert _image(client, BIG, full="true")[0] == 400
    assert outside.read_bytes()[:4] == b"\x89PNG"


def test_previews_usage_never_follows_the_raw_link(tmp_path: Path) -> None:
    raw = _raw(tmp_path / "opv2v")
    # A directory named previews inside the raw tree must not be counted or removed.
    decoy = raw / "test" / SCENE / "previews"
    decoy.mkdir()
    (decoy / "keep.jpg").write_bytes(b"x" * 10)
    cache_root = tmp_path / "results" / "coperceive-cache"
    cache = cache_root / "opv2v" / "abc123" / "test"
    prepare_cluster_dataset("opv2v", raw, cache, scenes=[SCENE], split="test")
    assert previews_usage(cache_root)["scenes"] == 0
    previews_usage(cache_root, remove=True)
    assert (decoy / "keep.jpg").read_bytes() == b"x" * 10


def test_cli_prepare_images_flag_and_previews_command(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from coperceive import cli

    raw = _raw(tmp_path / "opv2v")
    cache_root = tmp_path / "results" / "coperceive-cache"
    cache = cache_root / "opv2v" / "abc123" / "test"
    cli.main(
        [
            "dataset", "prepare", "--adapter", "opv2v", "--raw-root", str(raw), "--split", "test",
            "--cache", str(cache), "--scenes", SCENE, "--images", "previews",
        ]
    )  # fmt: skip
    capsys.readouterr()
    cli.main(["dataset", "previews", "--cache-root", str(cache_root)])
    usage = json.loads(capsys.readouterr().out)
    assert usage["schema"] == "coperceive-cluster-previews-1" and usage["scenes"] == 1
    cli.main(["dataset", "previews", "--cache-root", str(cache_root), "--remove"])
    assert json.loads(capsys.readouterr().out)["removed"]["scenes"] == 1
    status = json.loads((cache / "dataset.json").read_text())["scenes"][SCENE]
    assert status["images"] == "originals"


def test_jpeg_preview_bytes_are_small(tmp_path: Path) -> None:
    raw = _raw(tmp_path / "opv2v")
    cache = tmp_path / "results" / "cache" / "test"
    prepare_cluster_dataset("opv2v", raw, cache, scenes=[SCENE], split="test", images="previews")
    data = (cache / SCENE / "previews" / f"{BIG}.jpg").read_bytes()
    assert len(data) < 40_000
    with Image.open(io.BytesIO(data)) as image:
        assert image.size == (640, 360)
