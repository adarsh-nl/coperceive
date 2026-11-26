# SPDX-License-Identifier: Apache-2.0
"""Unit tests for the cross-platform updater E2E driver (scripts/desktop/e2e_updater.py)."""

from __future__ import annotations

import importlib.util
import json
import sys
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "desktop" / "e2e_updater.py"

_spec = importlib.util.spec_from_file_location("e2e_updater", SCRIPT_PATH)
assert _spec is not None and _spec.loader is not None
eu = importlib.util.module_from_spec(_spec)
sys.modules["e2e_updater"] = eu
_spec.loader.exec_module(eu)


def test_make_tampered_bytes_always_differs() -> None:
    original = b"signed-tauri-archive-payload"
    tampered = eu.make_tampered_bytes(original)
    assert tampered != original
    assert len(tampered) == len(original)
    assert eu.make_tampered_bytes(b"") != b""


def test_mock_updater_server_serves_tampered_then_valid_archive(tmp_path: Path) -> None:
    archive_bytes = b"valid-signed-archive-bytes"
    state = eu.MockUpdaterState(
        new_version="0.1.2",
        platform="linux-x86_64",
        archive_name="Coperceive_0.1.2_amd64.AppImage",
        archive_bytes=archive_bytes,
        tampered_bytes=eu.make_tampered_bytes(archive_bytes),
        signature="valid-base64-sig",
    )
    server = eu.start_mock_updater_server(state)
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{state.port}/latest.json") as resp:
            manifest = json.loads(resp.read().decode("utf-8"))
        assert manifest["version"] == "0.1.2"
        entry = manifest["platforms"]["linux-x86_64"]
        assert entry["signature"] == "valid-base64-sig"

        with urllib.request.urlopen(entry["url"]) as resp:
            assert resp.read() == state.tampered_bytes

        with state.lock:
            state.phase = "valid"

        with urllib.request.urlopen(entry["url"]) as resp:
            assert resp.read() == archive_bytes
    finally:
        server.shutdown()
        server.server_close()


def test_seed_state_and_library_and_snapshot_preserve(tmp_path: Path) -> None:
    fixture = ROOT / "tests" / "fixtures" / "synthetic_scene"
    state_dir, library_dir, seeded, snapshot = eu.seed_state_and_library(tmp_path, fixture)
    assert (state_dir / "state.json").is_file()
    assert (library_dir / "synth_001").is_dir()
    assert seeded["library_root"] == str(library_dir)
    assert seeded["last_data_root"] == str(library_dir / "synth_001")
    assert seeded["theme_override"] == "dark"
    assert snapshot
    assert eu.snapshot_directory(library_dir) == snapshot


def test_build_launch_env_sets_real_install_and_clears_conflicts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("COPERCEIVE_PYTHON", "/stale/python")
    monkeypatch.setenv("COPERCEIVE_AUTOCONNECT_ROOT", "/stale/root")
    env = eu.build_launch_env(
        state_dir=tmp_path / "state",
        marker_file=tmp_path / "marker.txt",
        ctrl_info_file=tmp_path / "ctrl.txt",
        update_url="http://127.0.0.1:9999/latest.json",
    )
    assert "COPERCEIVE_PYTHON" not in env
    assert "COPERCEIVE_AUTOCONNECT_ROOT" not in env
    assert env["COPERCEIVE_TEST_ENV"] == "1"
    assert env["COPERCEIVE_TEST_REAL_UPDATE_INSTALL"] == "1"
    assert env["COPERCEIVE_UPDATE_URL"] == "http://127.0.0.1:9999/latest.json"


def test_find_posix_matching_pids_ignores_wrappers_and_matches_appimage(tmp_path: Path) -> None:
    app = tmp_path / "Coperceive-old.AppImage"
    app.write_bytes(b"")
    resolved = str(app.resolve())

    fake_proc = tmp_path / "proc"
    # 101: xvfb-run wrapper passing --app <resolved> -> must NOT match
    p101 = fake_proc / "101"
    p101.mkdir(parents=True)
    (p101 / "cmdline").write_bytes(
        f"xvfb-run\0-a\0python3\0scripts/desktop/e2e_updater.py\0--app\0{resolved}\0".encode()
    )
    (p101 / "environ").write_bytes(b"PATH=/usr/bin\0")
    # 202: mounted AppImage runtime child -> MUST match via APPIMAGE env
    p202 = fake_proc / "202"
    p202.mkdir(parents=True)
    (p202 / "cmdline").write_bytes(b"/tmp/.mount_Cop123/usr/bin/coperceive-desktop\0")
    (p202 / "environ").write_bytes(f"HOME=/tmp\0APPIMAGE={resolved}\0".encode())

    fake_ps = "\n".join(
        [
            f"101 xvfb-run -a python3 scripts/desktop/e2e_updater.py --app {resolved}",
            f"303 {resolved} --e2e-updater-env=/tmp/env.json",
            f"404 python3 {resolved}",
            f"505 {resolved}",
        ]
    )
    pids = eu.find_posix_matching_pids(
        app,
        self_pid=505,
        parent_pid=101,
        ps_output=fake_ps,
        proc_root=fake_proc,
    )
    assert pids == [202, 303, 404]


@pytest.mark.skipif(sys.platform.startswith("win"), reason="POSIX shell mock executable")
def test_run_e2e_updater_full_flow_against_mock_app(tmp_path: Path) -> None:
    """Exercise tampered rejection, valid install, restart, and state preservation."""
    app_script = tmp_path / "mock-coperceive"
    version_file = tmp_path / "installed_version.txt"
    version_file.write_text("0.0.1", encoding="utf-8")

    # A lightweight fake desktop controller mimicking old -> reject tampered -> restart -> new.
    mock_source = """#!/usr/bin/env python3
import http.server, json, os, pathlib, subprocess, sys, threading, urllib.request

STATE_DIR = pathlib.Path(os.environ["COPERCEIVE_STATE_DIR"])
MARKER = pathlib.Path(os.environ["COPERCEIVE_TEST_MARKER_FILE"])
UPDATE_URL = os.environ["COPERCEIVE_UPDATE_URL"]
VER_FILE = pathlib.Path(__VER_FILE__)
CUR_VER = VER_FILE.read_text(encoding="utf-8").strip()

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/state":
            data = (STATE_DIR / "state.json").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(data)
            return
        if self.path == "/api/scenes":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps([{"id": "synth_001"}]).encode("utf-8"))
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        with urllib.request.urlopen(UPDATE_URL) as r:
            manifest = json.loads(r.read().decode("utf-8"))
        plat = manifest["platforms"]["linux-x86_64"]
        if self.path == "/updater-check":
            if CUR_VER != manifest["version"]:
                body = {
                    "status": "available",
                    "version": manifest["version"],
                    "current_version": CUR_VER,
                }
            else:
                body = {"status": "uptodate", "current_version": CUR_VER}
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(body).encode("utf-8"))
            return
        if self.path == "/updater-install":
            with urllib.request.urlopen(plat["url"]) as r:
                payload = r.read()
            if payload != b"SIGNED-NEW-ARCHIVE":
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                err = json.dumps({"status": "error", "error": "Update rejected: bad sig"})
                self.wfile.write(err.encode("utf-8"))
                return
            VER_FILE.write_text(manifest["version"], encoding="utf-8")
            self.connection.close()
            def _restart():
                subprocess.Popen([sys.executable, __file__], env=os.environ)
                os._exit(0)
            threading.Thread(target=_restart, daemon=True).start()
            return
        self.send_response(404)
        self.end_headers()

    def log_message(self, *args):
        pass

srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
port = srv.server_address[1]
origin = f"http://127.0.0.1:{port}"
MARKER.write_text(f"{origin}\\ntok\\n/tmp/log\\n{port}\\nsecret\\n", encoding="utf-8")
srv.serve_forever()
""".replace("__VER_FILE__", json.dumps(str(version_file)))
    app_script.write_text(mock_source, encoding="utf-8")
    app_script.chmod(0o755)

    archive = tmp_path / "Coperceive_0.1.2_amd64.AppImage"
    archive.write_bytes(b"SIGNED-NEW-ARCHIVE")
    sig = tmp_path / "Coperceive_0.1.2_amd64.AppImage.sig"
    sig.write_text("dGhpc19pc19hX3NpZwo=", encoding="utf-8")

    eu.run_e2e_updater(
        app_path=app_script,
        platform="linux-x86_64",
        old_version="0.0.1",
        new_version="0.1.2",
        archive_path=archive,
        sig_path=sig,
        fixture_dir=ROOT / "tests" / "fixtures" / "synthetic_scene",
        timeout_s=20.0,
    )
