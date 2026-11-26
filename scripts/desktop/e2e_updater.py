#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Cross-platform automated updater E2E harness (macOS, Windows, Linux).

Launches an older installed build of Coperceive Desktop configured with a
test-only updater endpoint override (`COPERCEIVE_UPDATE_URL`) pointing at a
local loopback HTTP server, verifies:

1. Tampered update archives are rejected on cryptographic signature check and
   leave the installed version, ``state.json``, and library untouched.
2. Valid throwaway-signed update archives are detected, downloaded, signature-
   verified, installed, and restarted into the newer version.
3. ``state.json`` and library scene files survive the update + restart intact.
"""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, cast


def _empty_requests() -> list[dict[str, str]]:
    return []


@dataclass
class MockUpdaterState:
    """Mutable state shared with the loopback updater HTTP server."""

    new_version: str
    platform: str
    archive_name: str
    archive_bytes: bytes
    tampered_bytes: bytes
    signature: str
    port: int = 0
    phase: str = "tampered"
    requests: list[dict[str, str]] = field(default_factory=_empty_requests)
    lock: threading.Lock = field(default_factory=threading.Lock)


def make_tampered_bytes(archive_bytes: bytes) -> bytes:
    """Return archive bytes guaranteed to fail minisign signature verification."""
    if not archive_bytes:
        return b"tampered-empty-archive"
    buf = bytearray(archive_bytes)
    idx = len(buf) // 2
    buf[idx] ^= 0xFF
    if len(buf) > 1:
        buf[-1] ^= 0x5A
    return bytes(buf)


def build_latest_json(state: MockUpdaterState) -> bytes:
    """Build a Tauri updater ``latest.json`` payload for the local server."""
    payload = {
        "version": state.new_version,
        "notes": "Automated CI updater E2E",
        "pub_date": "2026-11-26T00:00:00Z",
        "platforms": {
            state.platform: {
                "signature": state.signature,
                "url": f"http://127.0.0.1:{state.port}/archives/{state.archive_name}",
            }
        },
    }
    return json.dumps(payload).encode("utf-8")


def start_mock_updater_server(state: MockUpdaterState) -> ThreadingHTTPServer:
    """Start a loopback HTTP server serving ``/latest.json`` and the archive."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            with state.lock:
                phase = state.phase
                state.requests.append({"method": "GET", "path": self.path, "phase": phase})

            if self.path == "/latest.json":
                body = build_latest_json(state)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

            expected_archive_path = f"/archives/{state.archive_name}"
            if self.path == expected_archive_path:
                body = state.tampered_bytes if phase == "tampered" else state.archive_bytes
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

            self.send_response(404)
            self.end_headers()

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    state.port = int(server.server_address[1])
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def snapshot_directory(root: Path) -> dict[str, str]:
    """Return ``{relative_posix_path: sha256_hex}`` for every file under ``root``."""
    digest_map: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            h = hashlib.sha256()
            with path.open("rb") as fh:
                for chunk in iter(lambda: fh.read(65536), b""):
                    h.update(chunk)
            digest_map[path.relative_to(root).as_posix()] = h.hexdigest()
    return digest_map


def seed_state_and_library(
    tmp_dir: Path, fixture_dir: Path
) -> tuple[Path, Path, dict[str, Any], dict[str, str]]:
    """Create isolated ``COPERCEIVE_STATE_DIR`` and library with a synthetic scene."""
    if not fixture_dir.is_dir():
        raise FileNotFoundError(f"Synthetic fixture directory not found: {fixture_dir}")

    library_dir = tmp_dir / "library"
    scene_dir = library_dir / "synth_001"
    shutil.copytree(
        fixture_dir,
        scene_dir,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )

    state_dir = tmp_dir / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        os.chmod(state_dir, 0o700)

    seeded_state: dict[str, Any] = {
        "library_root": str(library_dir),
        "last_data_root": str(scene_dir),
        "last_scene_id": "synth_001",
        "theme_override": "dark",
        "reopen_last_session": True,
        "recent_roots": [str(scene_dir)],
    }
    state_file = state_dir / "state.json"
    state_file.write_text(json.dumps(seeded_state, indent=2) + "\n", encoding="utf-8")
    if os.name == "posix":
        os.chmod(state_file, 0o600)

    return state_dir, library_dir, seeded_state, snapshot_directory(library_dir)


def read_marker(marker_file: Path) -> tuple[str, str, str, int, str]:
    """Parse ``COPERCEIVE_TEST_MARKER_FILE`` lines ``(origin, token, log, port, secret)``."""
    lines = marker_file.read_text(encoding="utf-8").splitlines()
    if len(lines) < 5:
        raise ValueError(f"Expected 5 lines in marker file {marker_file}, got {len(lines)}")
    return lines[0], lines[1], lines[2], int(lines[3]), lines[4]


def wait_for_marker(
    marker_file: Path,
    timeout_s: float = 90.0,
    proc: subprocess.Popen[bytes] | None = None,
) -> tuple[str, str, str, int, str]:
    """Wait until ``marker_file`` exists and parses cleanly."""
    deadline = time.monotonic() + timeout_s
    last_err: Exception | None = None
    while time.monotonic() < deadline:
        if marker_file.is_file():
            try:
                return read_marker(marker_file)
            except (OSError, ValueError) as exc:
                last_err = exc
        if proc is not None and proc.poll() is not None:
            # Wait briefly for a child/restarted process only when proc wasn't supposed to hand off;
            # callers handling restarts pass proc=None.
            raise RuntimeError(f"App exited prematurely with code {proc.returncode}")
        time.sleep(0.25)
    raise TimeoutError(f"Timed out waiting for marker file {marker_file}: {last_err}")


def controller_request(
    port: int,
    secret: str,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    timeout_s: float = 30.0,
) -> tuple[int, dict[str, Any]]:
    """Send an authenticated JSON request to the loopback desktop controller."""
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Authorization": f"Bearer {secret}"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=data,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        body: dict[str, Any]
        try:
            parsed: Any = json.loads(raw) if raw else {}
            body = cast("dict[str, Any]", parsed) if isinstance(parsed, dict) else {"raw": raw}
        except json.JSONDecodeError:
            body = {"raw": raw}
        return exc.code, body


def trigger_real_install(port: int, secret: str, timeout_s: float = 60.0) -> None:
    """Invoke ``POST /updater-install``, tolerating process-restart connection drops.

    On macOS/Linux ``handle.restart()`` calls ``request_exit`` and exits without
    sending a response; on Windows NSIS ``update.install`` calls ``exit(0)``.
    Any explicit HTTP 4xx/5xx error is raised immediately.
    """
    try:
        status, body = controller_request(
            port, secret, "POST", "/updater-install", {}, timeout_s=timeout_s
        )
    except (
        http.client.RemoteDisconnected,
        ConnectionResetError,
        ConnectionAbortedError,
        BrokenPipeError,
    ):
        return
    except urllib.error.URLError as exc:
        reason = exc.reason
        if isinstance(
            reason,
            (
                http.client.RemoteDisconnected,
                ConnectionResetError,
                ConnectionAbortedError,
                BrokenPipeError,
                EOFError,
            ),
        ):
            return
        raise
    if status >= 400 or body.get("status") == "error":
        raise RuntimeError(f"/updater-install failed with HTTP {status}: {body}")


def check_sidecar_scenes(origin: str, token: str, timeout_s: float = 20.0) -> int:
    """Verify the sidecar serves the synthetic library scene."""
    req = urllib.request.Request(
        f"{origin}/api/scenes",
        headers={"Authorization": f"Bearer {token}"},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        assert resp.status == 200, f"Unexpected /api/scenes HTTP {resp.status}"
        scenes_raw: Any = json.loads(resp.read().decode("utf-8"))
    if not isinstance(scenes_raw, list):
        raise AssertionError(f"Expected list from /api/scenes, got {scenes_raw!r}")
    scenes = cast("list[Any]", scenes_raw)
    if len(scenes) < 1:
        raise AssertionError(f"Expected >= 1 scene from /api/scenes, got {scenes!r}")
    return len(scenes)


def assert_state_preserved(
    ctrl_port: int,
    ctrl_secret: str,
    state_dir: Path,
    library_dir: Path,
    expected_state: dict[str, Any],
    expected_library_snapshot: dict[str, str],
) -> None:
    """Assert ``state.json`` (on disk and via ``GET /state``) and library files are intact."""
    status, controller_state = controller_request(ctrl_port, ctrl_secret, "GET", "/state")
    assert status == 200, f"GET /state returned HTTP {status}: {controller_state}"
    disk_state = json.loads((state_dir / "state.json").read_text(encoding="utf-8"))

    for source_name, state_obj in (
        ("controller /state", controller_state),
        ("disk state.json", disk_state),
    ):
        for key in ("library_root", "last_data_root", "last_scene_id", "theme_override"):
            assert state_obj.get(key) == expected_state[key], (
                f"{source_name} mismatch for {key}: "
                f"{state_obj.get(key)!r} != {expected_state[key]!r}"
            )
        assert expected_state["last_data_root"] in (state_obj.get("recent_roots") or []), (
            f"{source_name} missing seeded recent_roots entry: {state_obj.get('recent_roots')!r}"
        )

    actual_snapshot = snapshot_directory(library_dir)
    assert actual_snapshot == expected_library_snapshot, (
        f"Library files changed during update: {actual_snapshot} != {expected_library_snapshot}"
    )


def build_launch_env(
    state_dir: Path,
    marker_file: Path,
    ctrl_info_file: Path,
    update_url: str,
) -> dict[str, str]:
    """Construct the clean test environment for launching the older packaged app."""
    env = dict(os.environ)
    for key in (
        "COPERCEIVE_PYTHON",
        "COPERCEIVE_AUTOCONNECT_ROOT",
        "COPERCEIVE_DESKTOP_AUTOCONNECT_ROOT",
    ):
        env.pop(key, None)

    env["COPERCEIVE_STATE_DIR"] = str(state_dir)
    env["COPERCEIVE_TEST_ENV"] = "1"
    env["COPERCEIVE_TEST_MARKER_FILE"] = str(marker_file)
    env["COPERCEIVE_CTRL_INFO_FILE"] = str(ctrl_info_file)
    env["COPERCEIVE_UPDATE_URL"] = update_url
    env["COPERCEIVE_TEST_REAL_UPDATE_INSTALL"] = "1"
    env["PYTHONUNBUFFERED"] = "1"
    env["RUST_BACKTRACE"] = "1"

    if sys.platform.startswith("linux"):
        env.setdefault("NO_AT_BRIDGE", "1")
        env.setdefault("WEBKIT_DISABLE_COMPOSITING_MODE", "1")
        env.setdefault("WEBKIT_DISABLE_DMABUF_RENDERER", "1")
        env.setdefault("WEBKIT_FORCE_SANDBOX", "0")
        env.setdefault("LIBGL_ALWAYS_SOFTWARE", "1")
        env.setdefault("APPIMAGE_EXTRACT_AND_RUN", "1")

    return env


def _argv_matches_app(argv: list[str], targets: set[str]) -> bool:
    """Match the app binary itself (or ``python <mock_app>``), never wrapper CLIs."""
    if not argv:
        return False
    if argv[0] in targets:
        return True
    exe_name = Path(argv[0]).name
    if exe_name.startswith("python") and len(argv) >= 2 and argv[1] in targets:
        return True
    return False


def find_posix_matching_pids(
    app_path: Path,
    self_pid: int | None = None,
    parent_pid: int | None = None,
    ps_output: str | None = None,
    proc_root: Path = Path("/proc"),
) -> list[int]:
    """Return PIDs for the desktop app (or AppImage child), excluding wrappers."""
    cur_pid = os.getpid() if self_pid is None else self_pid
    cur_ppid = os.getppid() if parent_pid is None else parent_pid
    targets = {str(app_path), str(app_path.resolve())}
    matched: set[int] = set()

    if ps_output is None:
        try:
            res = subprocess.run(
                ["ps", "-ax", "-o", "pid=,args="],
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
            ps_output = res.stdout if res.returncode == 0 else ""
        except (OSError, subprocess.SubprocessError):
            ps_output = ""

    for line in ps_output.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        parts = stripped.split(None, 1)
        if len(parts) < 2 or not parts[0].isdigit():
            continue
        pid = int(parts[0])
        if pid in (cur_pid, cur_ppid):
            continue
        if _argv_matches_app(parts[1].split(), targets):
            matched.add(pid)

    if proc_root.is_dir():
        env_markers = {f"APPIMAGE={t}".encode() for t in targets}
        for proc_dir in proc_root.iterdir():
            if not proc_dir.name.isdigit():
                continue
            pid = int(proc_dir.name)
            if pid in (cur_pid, cur_ppid):
                continue
            try:
                cmdline_raw = (proc_dir / "cmdline").read_bytes()
                argv = [
                    arg.decode("utf-8", errors="ignore") for arg in cmdline_raw.split(b"\0") if arg
                ]
                if _argv_matches_app(argv, targets):
                    matched.add(pid)
                    continue
                environ_entries = set((proc_dir / "environ").read_bytes().split(b"\0"))
                if environ_entries & env_markers:
                    matched.add(pid)
            except OSError:
                continue

    return sorted(matched)


def terminate_matching_processes(app_path: Path) -> None:
    """Best-effort cleanup of the launched or restarted desktop app process."""
    if os.name == "nt":
        install_dir = str(app_path.resolve().parent)
        ps_cmd = (
            "$dir = [regex]::Escape($env:COPERCEIVE_CLEANUP_DIR); "
            "Get-CimInstance Win32_Process | "
            "Where-Object { $_.ExecutablePath -and $_.ExecutablePath -match $dir } | "
            "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
        )
        env = dict(os.environ)
        env["COPERCEIVE_CLEANUP_DIR"] = install_dir
        subprocess.run(["powershell", "-NoProfile", "-Command", ps_cmd], env=env, check=False)
        return

    pids = find_posix_matching_pids(app_path)
    if not pids:
        return
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    time.sleep(0.5)
    for pid in find_posix_matching_pids(app_path):
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass


def run_e2e_updater(
    app_path: Path,
    platform: str,
    old_version: str,
    new_version: str,
    archive_path: Path,
    sig_path: Path,
    fixture_dir: Path,
    timeout_s: float = 120.0,
) -> None:
    """Execute the full tampered-rejection + valid-update + restart + state E2E."""
    archive_bytes = archive_path.read_bytes()
    signature = sig_path.read_text(encoding="utf-8").strip()
    if not signature:
        raise ValueError(f"Empty signature file: {sig_path}")

    state = MockUpdaterState(
        new_version=new_version,
        platform=platform,
        archive_name=archive_path.name,
        archive_bytes=archive_bytes,
        tampered_bytes=make_tampered_bytes(archive_bytes),
        signature=signature,
    )
    server = start_mock_updater_server(state)
    update_url = f"http://127.0.0.1:{state.port}/latest.json"
    print(
        f"[e2e-updater] mock server at {update_url} "
        f"for {platform} ({old_version} -> {new_version})",
        flush=True,
    )

    tmp_dir = Path(tempfile.mkdtemp(prefix="coperceive-e2e-updater-"))
    marker_file = tmp_dir / "test_marker.txt"
    ctrl_info_file = tmp_dir / "ctrl_info.txt"
    proc: subprocess.Popen[bytes] | None = None
    try:
        state_dir, library_dir, seeded_state, lib_snapshot = seed_state_and_library(
            tmp_dir, fixture_dir
        )
        stdout_log = tmp_dir / "app_stdout.log"
        stderr_log = tmp_dir / "app_stderr.log"

        env = build_launch_env(state_dir, marker_file, ctrl_info_file, update_url)
        launch_args = [str(app_path)]
        if sys.platform == "darwin" or os.name == "nt":
            # macOS (`open -n <app> --args ...`) and Windows NSIS (`/UPDATE /R /ARGS ...`)
            # forward CLI args across restart without reliably preserving parent env vars.
            env_json_file = tmp_dir / "e2e_updater_env.json"
            coperceive_env = {k: v for k, v in env.items() if k.startswith("COPERCEIVE_")}
            env_json_file.write_text(json.dumps(coperceive_env), encoding="utf-8")
            launch_args.append(f"--e2e-updater-env={env_json_file}")

        with stdout_log.open("wb") as out_fh, stderr_log.open("wb") as err_fh:
            proc = subprocess.Popen(launch_args, env=env, stdout=out_fh, stderr=err_fh)
            origin, token, _, ctrl_port, ctrl_secret = wait_for_marker(
                marker_file, timeout_s=timeout_s, proc=proc
            )
        print(
            f"[e2e-updater] older app ready on ctrl_port={ctrl_port}, origin={origin}", flush=True
        )
        check_sidecar_scenes(origin, token)

        # 1. Verify old version + tampered archive rejection
        status, check_old = controller_request(
            ctrl_port, ctrl_secret, "POST", "/updater-check", {"is_manual": True}
        )
        assert status == 200, f"Initial /updater-check failed: HTTP {status} {check_old}"
        assert check_old.get("status") == "available", f"Expected available update, got {check_old}"
        assert check_old.get("current_version") == old_version, (
            f"Expected old version {old_version}, got {check_old}"
        )
        assert check_old.get("version") == new_version, (
            f"Expected target version {new_version}, got {check_old}"
        )

        status, tamper_resp = controller_request(
            ctrl_port, ctrl_secret, "POST", "/updater-install", {}
        )
        assert status == 400 and tamper_resp.get("status") == "error", (
            f"Expected tampered archive rejection (HTTP 400), got HTTP {status}: {tamper_resp}"
        )
        print(
            f"[e2e-updater] tampered archive rejected as expected: {tamper_resp.get('error')}",
            flush=True,
        )

        with state.lock:
            tampered_downloads = [
                r
                for r in state.requests
                if r["phase"] == "tampered" and r["path"].startswith("/archives/")
            ]
        assert tampered_downloads, (
            "App did not download tampered archive before rejecting signature"
        )

        status, after_tamper = controller_request(
            ctrl_port, ctrl_secret, "POST", "/updater-check", {"is_manual": True}
        )
        assert status == 200 and after_tamper.get("current_version") == old_version, (
            f"Expected version to stay {old_version} after tampered update, got {after_tamper}"
        )
        assert_state_preserved(
            ctrl_port, ctrl_secret, state_dir, library_dir, seeded_state, lib_snapshot
        )

        # 2. Switch to valid signed archive, install, and wait for restart into new_version
        with state.lock:
            state.phase = "valid"

        status, check_valid = controller_request(
            ctrl_port, ctrl_secret, "POST", "/updater-check", {"is_manual": True}
        )
        assert status == 200 and check_valid.get("status") == "available", check_valid

        marker_file.unlink(missing_ok=True)
        ctrl_info_file.unlink(missing_ok=True)

        print("[e2e-updater] triggering valid signed update install + restart...", flush=True)
        trigger_real_install(ctrl_port, ctrl_secret, timeout_s=timeout_s)

        new_origin, new_token, _, new_ctrl_port, new_ctrl_secret = wait_for_marker(
            marker_file, timeout_s=timeout_s, proc=None
        )
        print(
            f"[e2e-updater] restarted app ready on ctrl_port={new_ctrl_port}, origin={new_origin}",
            flush=True,
        )

        status, check_new = controller_request(
            new_ctrl_port, new_ctrl_secret, "POST", "/updater-check", {"is_manual": True}
        )
        assert status == 200, f"Post-restart /updater-check failed: HTTP {status} {check_new}"
        assert check_new.get("current_version") == new_version, (
            f"Expected restarted app version {new_version}, got {check_new}"
        )
        assert check_new.get("status") == "uptodate", (
            f"Expected restarted app to report uptodate for {new_version}, got {check_new}"
        )

        assert_state_preserved(
            new_ctrl_port, new_ctrl_secret, state_dir, library_dir, seeded_state, lib_snapshot
        )
        scene_count = check_sidecar_scenes(new_origin, new_token)

        with state.lock:
            valid_downloads = [
                r
                for r in state.requests
                if r["phase"] == "valid" and r["path"].startswith("/archives/")
            ]
            post_restart_checks = [
                r for r in state.requests if r["phase"] == "valid" and r["path"] == "/latest.json"
            ]
        assert valid_downloads, "App did not download valid signed archive"
        assert len(post_restart_checks) >= 2, (
            f"Expected /latest.json checks before and after restart, saw {post_restart_checks}"
        )

        print(
            f"E2E_UPDATER_VERIFIED: platform={platform} "
            f"old={old_version} new={new_version} scenes={scene_count}",
            flush=True,
        )
    except Exception:
        print(
            f"[e2e-updater] failure diagnostics: marker_exists={marker_file.is_file()} "
            f"ctrl_info_exists={ctrl_info_file.is_file()}",
            file=sys.stderr,
        )
        if os.name == "nt":
            diag = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    "Get-Item $env:COPERCEIVE_DIAG_APP | "
                    "Select-Object FullName,Length,LastWriteTimeUtc,"
                    "@{N='ProductVersion';E={$_.VersionInfo.ProductVersion}} | Format-List; "
                    "Get-CimInstance Win32_Process | "
                    "Where-Object { $_.Name -match 'coperceive|setup|nsis' } | "
                    "Select-Object ProcessId,Name,CommandLine | Format-Table -AutoSize",
                ],
                env={**os.environ, "COPERCEIVE_DIAG_APP": str(app_path)},
                capture_output=True,
                text=True,
                check=False,
            )
            if diag.stdout:
                print(diag.stdout, file=sys.stderr)
        for log_name in ("app_stdout.log", "app_stderr.log"):
            log_path = tmp_dir / log_name
            if log_path.is_file():
                print(f"=== {log_name} ===", file=sys.stderr)
                print(
                    log_path.read_text(encoding="utf-8", errors="replace")[-4000:], file=sys.stderr
                )
        raise
    finally:
        server.shutdown()
        server.server_close()
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        terminate_matching_processes(app_path)
        shutil.rmtree(tmp_dir, ignore_errors=True)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    repo_root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--app", required=True, type=Path, help="Path to installed older app executable/AppImage"
    )
    parser.add_argument(
        "--platform",
        required=True,
        choices=("darwin-aarch64", "windows-x86_64", "linux-x86_64"),
        help="Tauri updater platform key",
    )
    parser.add_argument("--old-version", required=True, help="Expected older version string")
    parser.add_argument("--new-version", required=True, help="Expected newer version string")
    parser.add_argument(
        "--archive", required=True, type=Path, help="Path to signed newer update archive"
    )
    parser.add_argument("--sig", required=True, type=Path, help="Path to .sig file for --archive")
    parser.add_argument(
        "--fixture",
        type=Path,
        default=repo_root / "tests" / "fixtures" / "synthetic_scene",
        help="Synthetic scene fixture directory to seed in library",
    )
    parser.add_argument(
        "--timeout", type=float, default=120.0, help="Per-phase startup/restart timeout in seconds"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    run_e2e_updater(
        app_path=args.app,
        platform=args.platform,
        old_version=args.old_version,
        new_version=args.new_version,
        archive_path=args.archive,
        sig_path=args.sig,
        fixture_dir=args.fixture,
        timeout_s=args.timeout,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
