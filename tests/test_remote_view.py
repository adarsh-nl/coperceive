# SPDX-License-Identifier: Apache-2.0
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportPrivateUsage=false
"""Viewing a prepared cluster dataset over SSH, with no Slurm job.

The streamer runs here as a local process (``python -m coperceive dataset
stream``) where the app runs it through one ``ssh``; everything else is the real
code path: the pipelined client, the local mirror and disk cache, and the viewer
server. Fixtures are synthetic.
"""

from __future__ import annotations

import io
import json
import os
import sys
from pathlib import Path

import pyarrow.parquet as pq
import pytest
from coperceive.app.server import create_app
from coperceive.cluster_datasets import prepare_cluster_dataset
from coperceive.remote_view import scenes as remote_scenes
from coperceive.remote_view.client import StreamClient, StreamRefused
from coperceive.remote_view.scenes import DiskCache, RemoteScenes
from coperceive.remote_view.stream import Roots, StreamError, handle
from fastapi.testclient import TestClient
from PIL import Image

from tests.opencood_release import two_cav_scene
from tests.opencood_release import write_release as write_opencood

SCENE = "scene_0001"


@pytest.fixture
def prepared(tmp_path: Path) -> tuple[Path, Path]:
    """A raw OPV2V tree and its prepared cache (originals mode), like the cluster's."""
    raw = tmp_path / "datasets" / "opv2v"
    write_opencood(raw, "test", [two_cav_scene(SCENE), two_cav_scene("scene_0002")])
    Image.new("RGB", (64, 48), (10, 120, 200)).save(
        raw / "test" / SCENE / "641" / "000069_camera0.png"
    )
    cache = tmp_path / "results" / "coperceive-cache" / "opv2v" / "abc" / "test"
    prepare_cluster_dataset("opv2v", raw, cache, split="test")
    return raw, cache


def _client(cache: Path, raw: Path) -> StreamClient:
    argv = [
        sys.executable,
        "-m",
        "coperceive",
        "dataset",
        "stream",
        "--root",
        str(cache),
        "--root",
        str(raw),
    ]
    return StreamClient(argv, timeout_s=30)


def _app(tmp_path: Path, cache: Path, raw: Path) -> tuple[TestClient, RemoteScenes, StreamClient]:
    client = _client(cache, raw)
    remote = RemoteScenes(
        client, tmp_path / "mirror", DiskCache(tmp_path / "disk-cache", 64 * 1024**2)
    )
    app = create_app(root=tmp_path / "mirror", token=None, remote=remote)
    return TestClient(app), remote, client


def _snapshot(root: Path) -> dict[str, tuple[int, int]]:
    return {
        str(path.relative_to(root)): (path.stat().st_size, path.stat().st_mtime_ns)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_the_streamer_reads_only_below_its_roots(
    prepared: tuple[Path, Path], tmp_path: Path
) -> None:
    raw, cache = prepared
    roots = Roots([cache, raw])
    header, data = handle(roots, {"op": "read", "path": f"{SCENE}/manifest.json"})
    assert header["ok"] and json.loads(data)["scene_id"] == SCENE
    # The scene's raw link leads into the allowed raw root.
    image = f"{SCENE}/raw/test/{SCENE}/641/000069_camera0.png"
    assert handle(roots, {"op": "read", "path": image})[1][:4] == b"\x89PNG"
    secret = tmp_path / "secret.txt"
    secret.write_text("no")
    (cache / SCENE / "escape").symlink_to(secret)
    for bad in (
        "../secret.txt",
        "/etc/passwd",
        f"{SCENE}/../../secret.txt",
        f"{SCENE}/escape",
        "",
        "a\x00b",
    ):
        with pytest.raises(StreamError, match="escapes|relative path"):
            handle(roots, {"op": "read", "path": bad})
    # Only the cache root alone: the raw link now leads outside and is refused.
    with pytest.raises(StreamError, match="escapes"):
        handle(Roots([cache]), {"op": "read", "path": image})


def test_viewing_over_the_stream_matches_reading_the_scene_locally(
    prepared: tuple[Path, Path], tmp_path: Path
) -> None:
    raw, cache = prepared
    before = _snapshot(raw)
    cache_before = _snapshot(cache)
    http, _, client = _app(tmp_path, cache, raw)
    local = TestClient(create_app(root=cache, token=None, media_roots=(raw,)))
    try:
        assert http.get("/api/scenes").json() == local.get("/api/scenes").json()
        remote_summary = http.get(f"/api/scenes/{SCENE}").json()
        local_summary = local.get(f"/api/scenes/{SCENE}").json()
        assert remote_summary == local_summary
        time_us = remote_summary["times_us"][1]
        remote_frame = http.get(f"/api/scenes/{SCENE}/frame", params={"time_us": time_us}).json()
        assert (
            remote_frame
            == local.get(f"/api/scenes/{SCENE}/frame", params={"time_us": time_us}).json()
        )
        assert remote_frame["point_sensors"], remote_frame
        for sensor in remote_frame["point_sensors"]:
            params = {
                "time_us": time_us,
                "agent_id": sensor["agent_id"],
                "sensor_id": sensor["sensor_id"],
            }
            remote_points = http.get(f"/api/scenes/{SCENE}/points", params=params)
            assert remote_points.status_code == 200
            assert (
                remote_points.content
                == local.get(f"/api/scenes/{SCENE}/points", params=params).content
            )
        image = {"time_us": 69 * 50_000, "agent_id": "641", "sensor_id": "camera0"}
        response = http.get(f"/api/scenes/{SCENE}/image", params=image)
        assert (response.status_code, response.headers["content-type"]) == (200, "image/png")
        with Image.open(io.BytesIO(response.content)) as decoded:
            assert decoded.size == (64, 48)
        thumb = http.get(f"/api/scenes/{SCENE}/image", params={**image, "width": 32})
        assert thumb.headers["content-type"] == "image/jpeg"
    finally:
        client.close()
    # Nothing was written on the "cluster": raw tree and cache unchanged.
    assert _snapshot(raw) == before
    assert _snapshot(cache) == cache_before


def test_a_path_escape_through_the_viewer_is_refused(
    prepared: tuple[Path, Path], tmp_path: Path
) -> None:
    raw, cache = prepared
    http, _, client = _app(tmp_path, cache, raw)
    try:
        assert http.get("/api/scenes/..%2F..%2Fetc").status_code in (400, 404)
        assert http.get("/api/scenes/scene_0001.partial").status_code in (400, 404)
        # A recorded image path that climbs out of the dataset (tampered table).
        table = pq.read_table(cache / SCENE / "images.parquet")
        paths = table.column("path").to_pylist()
        paths[0] = "raw/../../../../secret"
        pq.write_table(table.set_column(3, "path", [paths]), cache / SCENE / "images.parquet")
        (cache / SCENE / "COMPLETE.json").touch()  # a new version: the mirror refreshes
        remote_scenes._LIST_TTL_S = 0.0
        row = table.slice(0, 1).to_pylist()[0]
        response = http.get(
            f"/api/scenes/{SCENE}/image",
            params={
                "time_us": row["time_us"],
                "agent_id": row["agent_id"],
                "sensor_id": row["sensor_id"],
            },
        )
        assert response.status_code == 400, response.text
    finally:
        remote_scenes._LIST_TTL_S = 3.0
        client.close()


def test_a_scene_that_finishes_while_viewing_joins_the_list(
    prepared: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Viewing while the prepare job still runs: only complete scenes are served,
    and one that completes later appears without a new connection."""
    raw, cache = prepared
    monkeypatch.setattr(remote_scenes, "_LIST_TTL_S", 0.0)
    pending = cache / "scene_0003"
    pending.mkdir()
    (pending / "manifest.json").write_text((cache / SCENE / "manifest.json").read_text())
    (cache / ".partial").mkdir(exist_ok=True)
    http, _, client = _app(tmp_path, cache, raw)
    try:
        assert [row["scene_id"] for row in http.get("/api/scenes").json()] == [SCENE, "scene_0002"]
        assert http.get("/api/scenes/scene_0003").status_code == 404
        for name in ("poses.parquet", "annotations.parquet", "objects.parquet", "images.parquet",
                     "point_index.parquet", "points.bin", "COMPLETE.json"):  # fmt: skip
            (pending / name).write_bytes((cache / SCENE / name).read_bytes())
        assert "scene_0003" in [row["scene_id"] for row in http.get("/api/scenes").json()]
        assert http.get("/api/scenes/scene_0003").status_code == 200
        assert client.requests > 0
    finally:
        client.close()


def test_the_disk_cache_stays_under_its_cap_and_clears(tmp_path: Path) -> None:
    cache = DiskCache(tmp_path / "c", cap_bytes=10_000)
    for index in range(30):
        cache.put(f"k{index}", bytes([index]) * 1_000)
        os.utime(tmp_path / "c", None)
    assert cache.size <= 10_000
    assert cache.get("k29") == bytes([29]) * 1_000
    assert cache.get("k0") is None
    cache.clear()
    assert cache.size == 0 and cache.get("k29") is None


def test_one_connection_serves_many_reads_in_flight(prepared: tuple[Path, Path]) -> None:
    raw, cache = prepared
    client = _client(cache, raw)
    try:
        futures = [
            client.submit({"op": "read", "path": f"{SCENE}/manifest.json"}) for _ in range(20)
        ]
        assert all(future.result(timeout=30)[0]["ok"] for future in futures)
        with pytest.raises(StreamRefused):
            client.read("../outside")
        assert client.read(f"{SCENE}/manifest.json")[:1] == b"{"
    finally:
        client.close()
