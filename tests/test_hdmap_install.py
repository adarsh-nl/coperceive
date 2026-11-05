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
from coperceive.hdmap.install import (
    LICENCE,
    MAP_SOURCE_UNAVAILABLE,
    TOWN03_MIRROR_COMMIT,
    TOWN03_URL,
    install_town03,
)
from coperceive.hdmap.store import load_map

from tests.test_hdmap import _scene_on_lane, _straight


class _MockMapServer:
    def __init__(self, payload: bytes, status: int = 200) -> None:
        self.payload = payload
        self.status = status
        self.requests: list[str] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                outer.requests.append(self.path)
                self.send_response(outer.status)
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
        with pytest.raises(ValueError, match="Map source unavailable"):
            install_town03(
                tmp_path / "maps",
                tmp_path / "scenes",
                url=server.url,
                expected_sha256="0" * 64,
            )
    assert not (tmp_path / "maps" / "Town03").exists()


def test_install_reports_a_missing_source_without_jargon(tmp_path: Path) -> None:
    payload = _fixture_xodr(tmp_path)
    with _MockMapServer(payload, status=404) as server:
        with pytest.raises(ValueError) as caught:
            install_town03(
                tmp_path / "maps",
                tmp_path / "scenes",
                url=server.url,
                expected_sha256=hashlib.sha256(payload).hexdigest(),
            )
    assert str(caught.value) == MAP_SOURCE_UNAVAILABLE
    assert not (tmp_path / "maps" / "Town03").exists()


def test_the_mirror_is_pinned_to_a_commit_and_not_master() -> None:
    assert TOWN03_MIRROR_COMMIT in TOWN03_URL
    assert "/master/" not in TOWN03_URL
    assert len(TOWN03_MIRROR_COMMIT) == 40


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
    # Ingest and alignment were staged; failure leaves no unaligned maps/Town03.
    assert not (scenes / "maps" / "Town03").exists()


def test_the_verified_town_table_matches_adr_0008_and_is_pinned() -> None:
    from coperceive.hdmap.install import TOWN_SHA256, town_url

    expected = {
        "Town01": "97a7f6ac67812567e5c8ee0599cd823b23f80f30f3f97c502212e38b72e2b709",
        "Town02": "953c05f17def231239ffcadba3307628a82d0d335b67b4e29c3098f7aed1dd7d",
        "Town03": "abf177bcd9b66cfefb7ac4919f6456815f1384f4229f1343c8a6a90bbb7713c9",
        "Town04": "4dddd1560ab7812b0e89cd4a523d8b6f2b93947eafb496d9eeb83cc772116c85",
        "Town05": "7edc53b0be840e177598da7a4ed9571146d7b2595ce280d154c90f92e8a48851",
        "Town06": "a90b613175fa837fdc128ddfaf5ddad25797b128fdbd8cd945974bc21a60496e",
        "Town07": "be730652769e993e27f748ba5793dba9e49d299205146a7a8db0bcd17af877bf",
        "Town10HD": "5d883b799f634030af92be1e9d79d107845540ba04338e8c60e095be1aef7be7",
    }
    assert TOWN_SHA256 == expected
    from coperceive.hdmap.install import TOWN_VERIFICATION

    assert {town for town, level in TOWN_VERIFICATION.items() if level == "carla-0.9.15-package"} == {
        "Town03", "Town06", "Town07", "Town10HD",
    }
    assert {town for town, level in TOWN_VERIFICATION.items() if level == "public-mirrors-not-package"} == {
        "Town01", "Town02", "Town04", "Town05",
    }
    for town in TOWN_SHA256:
        url = town_url(town)
        assert TOWN03_MIRROR_COMMIT in url
        assert "/master/" not in url
        assert url.endswith(f"/{town}.xodr")


def test_generic_install_of_town10hd_from_a_mock_source(tmp_path: Path) -> None:
    from coperceive.hdmap.install import install_verified_town

    payload = _fixture_xodr(tmp_path)
    digest = hashlib.sha256(payload).hexdigest()
    maps = tmp_path / "library" / "maps"
    scenes = tmp_path / "library"
    scenes.mkdir(parents=True)
    from coperceive.model import Agent, AgentType, Clock, Scene, StampedPose
    from coperceive.se3 import SE3
    from coperceive.store import SCHEMA_VERSION, write_scene

    pose = StampedPose(0, SE3.identity().translation(40.0, -1.75, 0.4))
    agent = Agent(
        "vehicle",
        AgentType.VEHICLE,
        (pose,),
        platform=(("map_name", "Carla/Maps/Town10HD"),),
    )
    scene = Scene(
        schema_version=SCHEMA_VERSION,
        scene_id="Town10HD-000",
        world_frame="ENU",
        clock=Clock("us", "install fixture"),
        agents=(agent,),
        sensors=(),
        annotations=(),
        objects=(),
    )
    write_scene(scene, scenes / "Town10HD-000")
    with _MockMapServer(payload) as server:
        report = install_verified_town(
            "Town10HD",
            maps,
            scenes,
            url=server.url,
            expected_sha256=digest,
        )
    assert report["map_name"] == "Carla/Maps/Town10HD"
    assert report["town"] == "Town10HD"
    assert report["alignment"]["measured"] is True
    assert (maps / "Town10HD" / "manifest.json").is_file()
