# SPDX-License-Identifier: Apache-2.0
"""Unit tests for the shared OS-neutral desktop E2E driver."""

from __future__ import annotations

import json
import os
import subprocess
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from scripts.desktop.e2e_desktop import (
    ControllerClient,
    LinuxAdapter,
    MacOSAdapter,
    MockUpdateServer,
    build_platform_adapter,
    create_repro_fixture,
    find_stale_pids,
    read_marker_file,
)


def test_build_platform_adapter_returns_os_specific_adapters() -> None:
    mac = build_platform_adapter("macos")
    linux = build_platform_adapter("linux")
    assert isinstance(mac, MacOSAdapter)
    assert isinstance(linux, LinuxAdapter)
    assert mac.reveal_button_label() == "Show in Finder"
    assert linux.reveal_button_label() == "Show in folder"
    assert mac.passed_banner == "Real-App macOS E2E Test PASSED Successfully!"
    assert linux.passed_banner == "Real-App Linux E2E Test PASSED Successfully!"
    with pytest.raises(ValueError, match="Unsupported OS"):
        build_platform_adapter("solaris")


def test_linux_adapter_isolates_state_and_logs(tmp_path: Path) -> None:
    adapter = LinuxAdapter()
    state_dir, library_dir, env = adapter.prepare_environment(tmp_path)
    assert state_dir == tmp_path / "state"
    assert library_dir == state_dir / "coperceive-data"
    assert env["COPERCEIVE_STATE_DIR"] == str(state_dir)
    assert env["COPERCEIVE_TEST_LOG_DIR"] == str(tmp_path / "logs")
    assert env["LIBGL_ALWAYS_SOFTWARE"] == "1"
    assert env["APPIMAGE_EXTRACT_AND_RUN"] == "1"


def test_read_marker_file_requires_valid_five_lines(tmp_path: Path) -> None:
    marker = tmp_path / "marker.txt"
    assert read_marker_file(marker) is None
    marker.write_text("http://127.0.0.1:1000\ntok\n/tmp/s.log\n0\nsec\n", encoding="utf-8")
    assert read_marker_file(marker) is None
    marker.write_text("http://127.0.0.1:1000\ntok\n/tmp/s.log\n45123\nsec\n", encoding="utf-8")
    parsed = read_marker_file(marker)
    assert parsed is not None
    assert parsed.origin == "http://127.0.0.1:1000"
    assert parsed.ctrl_port == 45123
    assert parsed.ctrl_secret == "sec"


def test_create_repro_fixture_creates_expected_files(tmp_path: Path) -> None:
    repro = tmp_path / "repro"
    create_repro_fixture(repro)
    plan = json.loads(
        (repro / "results" / "plans" / "plan-1790000000-aabbccddeeff.json").read_text(
            encoding="utf-8"
        )
    )
    assert plan["nothing_submitted"] is True
    assert (repro / "profile.json").is_file()
    assert (repro / "code" / "pipelines" / "run.py").is_file()


def test_mock_update_server_serves_all_platforms_and_bad_sig(tmp_path: Path) -> None:
    hit_log = tmp_path / "hits.log"
    server = MockUpdateServer(hit_log)
    port = server.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/latest.json", timeout=5) as resp:
            manifest = json.loads(resp.read().decode("utf-8"))
        assert manifest["version"] == "0.2.0"
        assert {
            "darwin-aarch64",
            "darwin-x86_64",
            "linux-x86_64",
            "windows-x86_64",
        } <= set(manifest["platforms"].keys())

        with urllib.request.urlopen(f"http://127.0.0.1:{port}/bad-sig.json", timeout=5) as resp:
            bad = json.loads(resp.read().decode("utf-8"))
        assert "linux-x86_64" in bad["platforms"]
        assert "darwin-aarch64" in bad["platforms"]
        assert bad["platforms"]["linux-x86_64"]["signature"]

        assert "latest.json" in hit_log.read_text(encoding="utf-8")
    finally:
        server.stop()


def test_controller_client_eval_retry_and_quit() -> None:
    calls = {"eval": 0, "quit": 0}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            _ = self.rfile.read(length)
            assert self.headers.get("Authorization") == "Bearer test-secret"
            if self.path == "/eval":
                calls["eval"] += 1
                if calls["eval"] == 1:
                    self.send_response(504)
                    self.end_headers()
                    return
                body = json.dumps({"result": {"points": 12}, "error": None}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path == "/quit":
                calls["quit"] += 1
                body = json.dumps({"quitting": True}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_response(404)
            self.end_headers()

        def log_message(self, format: str, *args: object) -> None:
            del format, args
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = ControllerClient(int(server.server_address[1]), "test-secret")
        res = client.eval_retry("test", "window.__COPERCEIVE_SCENE_COUNTS__()", tries=3)
        assert res == {"points": 12}
        assert calls["eval"] == 2
        assert client.quit_app() is True
        assert calls["quit"] == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5.0)


def test_find_stale_pids_never_matches_wrapper_invocations() -> None:
    lines = [
        (
            "101 xvfb-run -a dbus-run-session -- bash "
            "scripts/desktop/e2e_linux.sh /usr/bin/coperceive-desktop"
        ),
        (
            "102 python3 scripts/desktop/e2e_desktop.py --os linux "
            "--app-exe /usr/bin/coperceive-desktop"
        ),
        "103 /usr/bin/coperceive-desktop",
        "104 /Applications/Coperceive.app/Contents/MacOS/Coperceive",
        "105 /usr/lib/Coperceive/resources/python/bin/python3 -m coperceive serve --port 8000",
    ]
    assert find_stale_pids(lines, self_pid=102, match_serve=True, match_app=True) == [103, 105]
    assert find_stale_pids(
        lines, self_pid=102, match_serve=True, match_app=True, include_macos_app=True
    ) == [103, 104, 105]
    assert find_stale_pids(lines, self_pid=102, match_serve=True, match_app=False) == [105]
    assert find_stale_pids(lines, self_pid=103, match_serve=True, match_app=True) == [105]


def test_fake_scp_copies_static_contents_not_parent_dir(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parent.parent
    fake_scp = repo_root / "src-tauri" / "tests" / "fixtures" / "datasets" / "fake_scp.sh"
    src_static = tmp_path / "static"
    (src_static / "assets").mkdir(parents=True)
    (src_static / "index.html").write_text("<html>ok</html>\n", encoding="utf-8")
    (src_static / "assets" / "app.js").write_text("console.log(1)\n", encoding="utf-8")
    remote_home = tmp_path / "remote-home"
    remote_home.mkdir()

    res = subprocess.run(
        ["bash", str(fake_scp), "-r", "-q", "-p", "--", f"{src_static}/.", "cluster:.cache/s"],
        env={**os.environ, "FAKE_REMOTE_HOME": str(remote_home)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0, res.stderr
    assert (remote_home / ".cache" / "s" / "index.html").is_file()
    assert (remote_home / ".cache" / "s" / "assets" / "app.js").is_file()
    assert not (remote_home / ".cache" / "s" / "static").exists()
