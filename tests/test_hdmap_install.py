# SPDX-License-Identifier: Apache-2.0
"""One-click Town03 map install against a mock server and a tiny .xodr fixture."""

# pyright: reportPrivateUsage=false
from __future__ import annotations

import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from coperceive.hdmap.install import LICENCE, install_town03
from coperceive.hdmap.store import load_map

from tests.test_hdmap import _scene_on_lane, _straight


class _MockMapServer:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.requests: list[str] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                outer.requests.append(self.path)
                self.send_response(200)
                self.send_header("Content-Type", "application/xml")
                self.send_header("Content-Length", str(len(outer.payload)))
                self.end_headers()
                self.wfile.write(outer.payload)

            def log_message(self, format: str, *args: object) -> None:
                return None

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self) -> _MockMapServer:
        self.thread.start()
        return self

    def __exit__(self, *args: object) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)

    @property
    def url(self) -> str:
        host, port = self.httpd.server_address[:2]
        return f"http://{host}:{port}/Town03.xodr"


def _fixture_xodr(tmp_path: Path) -> bytes:
    path = tmp_path / "fixture-Town03.xodr"
    _straight(path)
    return path.read_bytes()


def test_install_downloads_verifies_ingests_and_aligns_town03(tmp_path: Path) -> None:
    payload = _fixture_xodr(tmp_path)
    digest = hashlib.sha256(payload).hexdigest()
    maps = tmp_path / "library" / "maps"
    scenes = tmp_path / "library"
    _scene_on_lane(scenes)
    stages: list[tuple[str, int, str]] = []

    with _MockMapServer(payload) as server:
        report = install_town03(
            maps,
            scenes,
            url=server.url,
            expected_sha256=digest,
            progress=lambda stage, percent, message: stages.append((stage, percent, message)),
        )
        assert server.requests == ["/Town03.xodr"]

    assert [stage for stage, _percent, _message in stages][0] == "download"
    assert stages[-1][0] == "done" and stages[-1][1] == 100
    assert report["map_name"] == "Carla/Maps/Town03"
    assert report["licence"] == LICENCE
    assert report["sha256"] == digest
    assert report["alignment"]["measured"] is True
    assert report["alignment"]["map_name"] == "Carla/Maps/Town03"
    assert report["alignment"]["chosen"] == "identity"
    directory = maps / "Town03"
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["map_name"] == "Carla/Maps/Town03"
    assert manifest["source"]["licence"] == "CC-BY"
    assert manifest["source"]["sha256"] == digest
    assert manifest["source"]["retrieved_from"] == server.url
    assert "README" in manifest["source"]["licence_url"]
    assert manifest["world_from_opendrive"]["measured"] is True
    loaded = load_map(directory)
    assert loaded.polylines
    # The install keeps the map as parsed polylines rather than committing a .xodr.
    assert not (directory / "Town03.xodr").exists()


def test_install_refuses_a_tampered_download(tmp_path: Path) -> None:
    payload = _fixture_xodr(tmp_path)
    with _MockMapServer(payload) as server:
        with pytest.raises(ValueError, match="does not match"):
            install_town03(
                tmp_path / "maps",
                tmp_path / "scenes",
                url=server.url,
                expected_sha256="0" * 64,
            )
    assert not (tmp_path / "maps").exists() or not any((tmp_path / "maps").iterdir())


def test_install_requires_a_scene_that_names_the_map(tmp_path: Path) -> None:
    payload = _fixture_xodr(tmp_path)
    digest = hashlib.sha256(payload).hexdigest()
    scenes = tmp_path / "library"
    scenes.mkdir()
    with _MockMapServer(payload) as server:
        with pytest.raises(ValueError, match="no scene in this library names it"):
            install_town03(
                scenes / "maps",
                scenes,
                url=server.url,
                expected_sha256=digest,
            )
