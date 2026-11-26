#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""OS-neutral real-app desktop E2E driver for Coperceive Desktop.

Drives the built desktop app (macOS .app or Linux binary) via the loopback
controller (/eval, /state, /close-window, /quit) and runtime-gated test seams.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
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
import zipfile
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import cast


def _as_dict(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise RuntimeError(f"Expected dict, got {type(value).__name__}: {value!r}")
    return cast(dict[str, object], value)


def _as_int(value: object, name: str = "value") -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RuntimeError(f"Expected numeric {name}, got {value!r}")
    return int(value)


def _as_float(value: object, name: str = "value") -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RuntimeError(f"Expected numeric {name}, got {value!r}")
    return float(value)


def _as_str(value: object, name: str = "value") -> str:
    if not isinstance(value, str):
        raise RuntimeError(f"Expected string {name}, got {value!r}")
    return value


def _as_str_list(value: object, name: str = "value") -> list[str]:
    if not isinstance(value, list):
        raise RuntimeError(f"Expected list {name}, got {value!r}")
    items = cast(list[object], value)
    out: list[str] = []
    for item in items:
        if not isinstance(item, str):
            raise RuntimeError(f"Expected string element in {name}, got {item!r}")
        out.append(item)
    return out


def find_stale_pids(
    ps_lines: list[str],
    *,
    self_pid: int,
    match_serve: bool,
    match_app: bool,
    include_macos_app: bool = False,
) -> list[int]:
    """Return PIDs for actual desktop app or `coperceive serve` processes.

    Matches on `argv[0]` rather than `pkill -f` substring matching so wrapper
    commands (`xvfb-run`, `dbus-run-session`, `e2e_desktop.py --app-exe ...`)
    are never killed.
    """
    matched: list[int] = []
    for raw in ps_lines:
        stripped = raw.strip()
        if not stripped:
            continue
        parts = stripped.split(None, 1)
        if len(parts) < 2 or not parts[0].isdigit():
            continue
        pid = int(parts[0])
        if pid == self_pid:
            continue
        tokens = parts[1].split()
        if not tokens:
            continue
        exe_base = os.path.basename(tokens[0])
        rest = tokens[1:]
        is_app = exe_base == "coperceive-desktop" or (
            include_macos_app and exe_base == "Coperceive"
        )
        is_serve = "serve" in rest and (
            exe_base == "coperceive"
            or (
                exe_base.startswith("python")
                and any(tok == "coperceive" or tok.endswith("/coperceive") for tok in rest)
            )
        )
        if (match_app and is_app) or (match_serve and is_serve):
            matched.append(pid)
    return matched


def _list_stale_pids(
    *,
    match_serve: bool,
    match_app: bool,
    include_macos_app: bool = False,
) -> list[int]:
    res = subprocess.run(
        ["ps", "-eo", "pid=,args="],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if res.returncode != 0:
        return []
    return find_stale_pids(
        res.stdout.splitlines(),
        self_pid=os.getpid(),
        match_serve=match_serve,
        match_app=match_app,
        include_macos_app=include_macos_app,
    )


def _kill_stale_processes(
    *,
    match_serve: bool,
    match_app: bool,
    include_macos_app: bool = False,
) -> None:
    for pid in _list_stale_pids(
        match_serve=match_serve,
        match_app=match_app,
        include_macos_app=include_macos_app,
    ):
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass


@dataclass(frozen=True)
class MarkerInfo:
    origin: str
    token: str
    sidecar_log: str
    ctrl_port: int
    ctrl_secret: str


def read_marker_file(marker_path: Path) -> MarkerInfo | None:
    if not marker_path.is_file():
        return None
    lines = marker_path.read_text(encoding="utf-8", errors="replace").splitlines()
    if len(lines) < 5:
        return None
    port_str = lines[3].strip()
    secret = lines[4].strip()
    if not port_str or port_str == "0" or not secret:
        return None
    try:
        port = int(port_str)
    except ValueError:
        return None
    return MarkerInfo(
        origin=lines[0].strip(),
        token=lines[1].strip(),
        sidecar_log=lines[2].strip(),
        ctrl_port=port,
        ctrl_secret=secret,
    )


def _is_transient_http_error(exc: Exception) -> bool:
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in (500, 502, 503, 504)
    return isinstance(exc, (urllib.error.URLError, TimeoutError, ConnectionError, OSError))


class ControllerClient:
    """Loopback HTTP client for the desktop test controller."""

    def __init__(self, port: int, secret: str) -> None:
        self.port = port
        self.secret = secret

    def _post_json(
        self,
        path: str,
        payload: dict[str, object],
        timeout_s: float = 15.0,
    ) -> dict[str, object]:
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=body,
            headers={
                "Authorization": f"Bearer {self.secret}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            raw = resp.read().decode("utf-8")
        parsed: object = json.loads(raw)
        return _as_dict(parsed)

    def eval_js(
        self,
        script: str,
        *,
        timeout: int = 10,
        window: str | None = None,
    ) -> object:
        payload: dict[str, object] = {"script": script, "timeout": timeout}
        if window is not None:
            payload["window"] = window
        data = self._post_json("/eval", payload, timeout_s=float(timeout + 5))
        err = data.get("error")
        if err:
            raise RuntimeError(f"JS error: {err}")
        return data.get("result")

    def eval_retry(
        self,
        step: str,
        script: str,
        *,
        timeout: int = 10,
        tries: int = 5,
        window: str | None = None,
    ) -> object:
        delay = 0.5
        last: Exception | None = None
        for attempt in range(1, tries + 1):
            try:
                return self.eval_js(script, timeout=timeout, window=window)
            except Exception as exc:
                last = exc
                if not _is_transient_http_error(exc) or attempt == tries:
                    break
                print(
                    f"E2E_RETRY step={step} attempt={attempt}/{tries} after "
                    f"{type(exc).__name__}: {exc}",
                    flush=True,
                )
                time.sleep(delay)
                delay = min(delay * 2.0, 4.0)
        print(f"E2E_FAIL step={step} {type(last).__name__}: {last}", flush=True)
        raise SystemExit(1)

    def eval_once_note(
        self,
        step: str,
        script: str,
        *,
        timeout: int = 15,
        window: str | None = None,
    ) -> object:
        try:
            return self.eval_js(script, timeout=timeout, window=window)
        except Exception as exc:
            print(f"E2E_NOTE step={step} {type(exc).__name__}: {exc}", flush=True)
            return None

    def get_state(self, timeout_s: float = 15.0) -> dict[str, object]:
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/state",
            headers={"Authorization": f"Bearer {self.secret}"},
        )
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            parsed: object = json.loads(resp.read().decode("utf-8"))
        return _as_dict(parsed)

    def close_viewer_window(self) -> None:
        try:
            self._post_json("/close-window", {}, timeout_s=5.0)
        except Exception:
            pass

    def quit_app(self) -> bool:
        try:
            self._post_json("/quit", {}, timeout_s=5.0)
            return True
        except Exception:
            return False

    def wait_for(
        self,
        description: str,
        script: str,
        seconds: float = 40.0,
        interval: float = 0.25,
        window: str | None = None,
        retry_errors: bool = False,
    ) -> object:
        deadline = time.monotonic() + seconds
        last: object = None
        while time.monotonic() < deadline:
            try:
                last = self.eval_js(script, timeout=15, window=window)
                if last:
                    return last
            except Exception as exc:
                if not retry_errors:
                    raise
                last = f"{type(exc).__name__}: {exc}"
            time.sleep(interval)
        raise RuntimeError(f"timed out waiting for {description}: {last}")

    def wait_for_scene(self, timeout: float = 30.0) -> dict[str, object]:
        deadline = time.monotonic() + timeout
        last_err: Exception | None = None
        last_counts: object = None
        while time.monotonic() < deadline:
            try:
                counts = self.eval_js(
                    "window.__COPERCEIVE_SCENE_COUNTS__ ? "
                    "window.__COPERCEIVE_SCENE_COUNTS__() : null",
                    timeout=4,
                )
                last_counts = counts
                if isinstance(counts, dict):
                    counts_dict = cast(dict[str, object], counts)
                    pts = counts_dict.get("points", 0)
                    if isinstance(pts, (int, float)) and not isinstance(pts, bool) and pts > 0:
                        return counts_dict
            except Exception as exc:
                last_err = exc
            time.sleep(0.5)
        raise RuntimeError(
            "Timed out waiting for viewer scene to load and render "
            f"(last_counts={last_counts}, last_err={last_err})"
        )

    def click_button(
        self,
        label: str,
        *,
        exact: bool = False,
        window: str | None = None,
        use_eval_once_step: str | None = None,
    ) -> None:
        comparator = "===" if exact else "includes"
        if exact:
            expr = f"(item.textContent || '').trim() === {json.dumps(label)}"
        else:
            expr = f"(item.textContent || '').includes({json.dumps(label)})"
        del comparator
        script = f"""
            (() => {{
                const button = Array.from(document.querySelectorAll('button')).find(
                    (item) => {expr}
                );
                if (!button) return 'missing';
                button.click();
                return 'clicked';
            }})()
        """
        if use_eval_once_step is not None:
            result = self.eval_once_note(use_eval_once_step, script, timeout=10, window=window)
        else:
            result = self.eval_js(script, timeout=15, window=window)
        if result != "clicked":
            raise RuntimeError(f"button not found: {label}")


class PlatformAdapter(ABC):
    """OS-specific operations required by the shared desktop E2E flow."""

    os_name: str
    passed_banner: str

    @abstractmethod
    def prepare_environment(self, tmp_dir: Path) -> tuple[Path, Path, dict[str, str]]:
        """Return (state_dir, default_library_dir, platform_env)."""

    @abstractmethod
    def prepare_appearance_light(self) -> None:
        """Ensure system appearance is light before Step 1 when supported."""

    @abstractmethod
    def restore_appearance(self) -> None:
        """Restore pre-test system appearance during cleanup."""

    @abstractmethod
    def capture_screenshot(self, path: Path) -> None:
        """Capture a desktop screenshot if tooling exists."""

    @abstractmethod
    def trigger_quit(self, proc: subprocess.Popen[bytes], ctrl: ControllerClient | None) -> None:
        """Request clean quit via native shortcut or controller."""

    @abstractmethod
    def pre_close_viewer_gesture(self) -> None:
        """Send OS close-window shortcut if applicable."""

    @abstractmethod
    def step1_copy_diagnostics(self) -> None:
        """Verify Help -> Copy Diagnostics via native menu when supported."""

    @abstractmethod
    def step2d_verify_window_titles(self) -> None:
        """Verify OS window manager shows one viewer window when supported."""

    @abstractmethod
    def step5h_os_input_and_edit_shortcuts(
        self,
        ctrl: ControllerClient,
        focus_and_clear: Callable[[str], None],
        wait_for_field_value: Callable[[str, str, str], str],
        poll_until: Callable[
            [Callable[[], str], str, Callable[[str], bool], str],
            str,
        ],
    ) -> None:
        """Verify native keystrokes/Edit menu shortcuts or skip with reason on Linux."""

    @abstractmethod
    def reveal_button_label(self) -> str:
        """Expected reveal button text in RunHistory after saving a repro card."""

    @abstractmethod
    def extra_failure_log_dirs(self) -> list[Path]:
        """Extra OS log directories to dump on failure."""


class MacOSAdapter(PlatformAdapter):
    os_name = "macos"
    passed_banner = "Real-App macOS E2E Test PASSED Successfully!"

    def __init__(self) -> None:
        self._initial_dark_mode = "false"

    def prepare_environment(self, tmp_dir: Path) -> tuple[Path, Path, dict[str, str]]:
        del tmp_dir
        state_dir = Path.home() / "Library" / "Application Support" / "nl.adarsh.coperceive"
        state_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(state_dir, 0o700)
        legacy_state = Path.home() / "Library" / "Application Support" / "coperceive" / "state.json"
        if legacy_state.exists():
            legacy_state.unlink()
        library_dir = Path.home() / "coperceive-data"
        return state_dir, library_dir, {}

    def prepare_appearance_light(self) -> None:
        res = subprocess.run(
            ["defaults", "read", "-g", "AppleInterfaceStyle"],
            capture_output=True,
            text=True,
            check=False,
        )
        self._initial_dark_mode = "true" if res.stdout.strip() == "Dark" else "false"
        osa = subprocess.run(
            [
                "osascript",
                "-e",
                'tell application "System Events" to tell appearance '
                "preferences to set dark mode to false",
            ],
            capture_output=True,
            check=False,
        )
        if osa.returncode != 0:
            subprocess.run(
                ["defaults", "delete", "-g", "AppleInterfaceStyle"],
                capture_output=True,
                check=False,
            )

    def restore_appearance(self) -> None:
        if self._initial_dark_mode == "true":
            osa = subprocess.run(
                [
                    "osascript",
                    "-e",
                    'tell application "System Events" to tell appearance '
                    "preferences to set dark mode to true",
                ],
                capture_output=True,
                check=False,
            )
            if osa.returncode != 0:
                subprocess.run(
                    ["defaults", "write", "-g", "AppleInterfaceStyle", "Dark"],
                    capture_output=True,
                    check=False,
                )
        else:
            osa = subprocess.run(
                [
                    "osascript",
                    "-e",
                    'tell application "System Events" to tell appearance '
                    "preferences to set dark mode to false",
                ],
                capture_output=True,
                check=False,
            )
            if osa.returncode != 0:
                subprocess.run(
                    ["defaults", "delete", "-g", "AppleInterfaceStyle"],
                    capture_output=True,
                    check=False,
                )

    def capture_screenshot(self, path: Path) -> None:
        subprocess.run(["screencapture", "-x", str(path)], check=False)

    def trigger_quit(self, proc: subprocess.Popen[bytes], ctrl: ControllerClient | None) -> None:
        del proc, ctrl
        subprocess.run(
            [
                "osascript",
                "-e",
                'tell application "System Events"\n'
                '    tell process "Coperceive"\n'
                "        set frontmost to true\n"
                '        keystroke "q" using command down\n'
                "    end tell\n"
                "end tell",
            ],
            capture_output=True,
            check=False,
        )

    def pre_close_viewer_gesture(self) -> None:
        subprocess.run(
            [
                "osascript",
                "-e",
                'tell application "System Events"\n'
                '    tell process "Coperceive"\n'
                "        set frontmost to true\n"
                '        keystroke "w" using command down\n'
                "    end tell\n"
                "end tell",
            ],
            capture_output=True,
            check=False,
        )

    def step1_copy_diagnostics(self) -> None:
        print("Testing Help -> Copy Diagnostics via native menu...")
        subprocess.run(
            [
                "osascript",
                "-e",
                'tell application "System Events"\n'
                '    tell process "Coperceive"\n'
                "        try\n"
                '            click menu item "Copy Diagnostics" of menu "Help" of menu bar 1\n'
                "        on error err\n"
                '            log "AppleScript diag note: " & err\n'
                "        end try\n"
                "    end tell\n"
                "end tell",
            ],
            check=False,
        )
        time.sleep(0.5)
        clip = subprocess.run(["pbpaste"], capture_output=True, text=True, check=False).stdout
        if "Coperceive Diagnostics" in clip:
            print("Verified: Copy Diagnostics successfully wrote diagnostics to macOS clipboard.")
            if "Bearer " in clip and "Bearer [REDACTED]" in clip:
                print("Verified: Diagnostics tokens are properly redacted.")

    def step2d_verify_window_titles(self) -> None:
        res = subprocess.run(
            [
                "osascript",
                "-e",
                'tell application "System Events" to get name of '
                'every window of process "Coperceive"',
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        window_names = res.stdout.strip()
        print(f"Step 2d visible window names: {window_names or 'unavailable'}")
        if window_names:
            viewer_windows = sum(1 for part in window_names.split(",") if "coperceive —" in part)
            print(f"Step 2d viewer titles: {viewer_windows}")
            if viewer_windows != 1:
                raise RuntimeError(
                    f"expected one viewer window title, got {viewer_windows} ({window_names})"
                )

    def step5h_os_input_and_edit_shortcuts(
        self,
        ctrl: ControllerClient,
        focus_and_clear: Callable[[str], None],
        wait_for_field_value: Callable[[str, str, str], str],
        poll_until: Callable[
            [Callable[[], str], str, Callable[[str], bool], str],
            str,
        ],
    ) -> None:
        del ctrl
        for field_id, text in [
            ("hpc-user", "cluster-user"),
            ("hpc-partition", "ps"),
            ("hpc-host", "cluster-login"),
        ]:
            focus_and_clear(field_id)
            as_script = (
                'tell application "System Events"\n'
                '    tell process "Coperceive"\n'
                "        set frontmost to true\n"
                f'        keystroke "{text}"\n'
                "    end tell\n"
                "end tell"
            )
            subprocess.run(["osascript", "-e", as_script], check=True)
            val = wait_for_field_value(
                field_id,
                text,
                f"Typed #{field_id} lowercase check (auto-capitalisation bug!)",
            )
            print(f"Step 5h typed #{field_id}: '{val}' (matched expected lowercase)")

        print("Testing macOS Edit menu shortcuts in real app...", flush=True)
        focus_and_clear("hpc-user")
        subprocess.run(
            [
                "osascript",
                "-e",
                'tell application "System Events"\n'
                '    tell process "Coperceive"\n'
                "        set frontmost to true\n"
                '        keystroke "original-text"\n'
                "    end tell\n"
                "end tell",
            ],
            check=True,
        )
        wait_for_field_value("hpc-user", "original-text", "Step 5h Cmd+A initial setup")

        subprocess.run(
            [
                "osascript",
                "-e",
                'tell application "System Events"\n'
                '    tell process "Coperceive"\n'
                "        set frontmost to true\n"
                '        keystroke "a" using command down\n'
                "        delay 0.1\n"
                '        keystroke "replaced"\n'
                "    end tell\n"
                "end tell",
            ],
            check=True,
        )
        val_replaced = wait_for_field_value(
            "hpc-user", "replaced", "Cmd+A select-all and replace failed"
        )
        print(f"Step 5h Cmd+A select-all and replace verified: '{val_replaced}'")

        subprocess.run(["pbcopy"], input="", text=True, check=False)
        subprocess.run(
            [
                "osascript",
                "-e",
                'tell application "System Events"\n'
                '    tell process "Coperceive"\n'
                "        set frontmost to true\n"
                '        keystroke "a" using command down\n'
                "        delay 0.1\n"
                '        keystroke "c" using command down\n'
                "    end tell\n"
                "end tell",
            ],
            check=True,
        )
        poll_until(
            lambda: subprocess.run(["pbpaste"], check=False, capture_output=True, text=True).stdout,
            repr("replaced"),
            lambda v: v == "replaced",
            "Cmd+C copy clipboard",
        )

        focus_and_clear("hpc-host")
        as_paste = (
            'tell application "System Events"\n'
            '    tell process "Coperceive"\n'
            "        set frontmost to true\n"
            '        keystroke "v" using command down\n'
            "    end tell\n"
            "end tell"
        )
        subprocess.run(["osascript", "-e", as_paste], check=True)
        val_pasted = wait_for_field_value("hpc-host", "replaced", "Cmd+C / Cmd+V round trip failed")
        print(f"Step 5h Cmd+C copy and Cmd+V paste verified: '{val_pasted}'")

        subprocess.run(
            [
                "osascript",
                "-e",
                'tell application "System Events"\n'
                '    tell process "Coperceive"\n'
                "        set frontmost to true\n"
                '        keystroke "a" using command down\n'
                "        delay 0.1\n"
                '        keystroke "x" using command down\n'
                "    end tell\n"
                "end tell",
            ],
            check=True,
        )
        wait_for_field_value("hpc-host", "", "Cmd+X cut failed")

        subprocess.run(["osascript", "-e", as_paste], check=True)
        val_recut = wait_for_field_value("hpc-host", "replaced", "Cmd+V paste after cut failed")
        print(f"Step 5h Cmd+X cut and Cmd+V paste verified: '{val_recut}'")
        print(
            "Step 5h verified: text inputs disable macOS auto-capitalisation, "
            "typed lowercase values stay unchanged."
        )

    def reveal_button_label(self) -> str:
        return "Show in Finder"

    def extra_failure_log_dirs(self) -> list[Path]:
        return [Path.home() / "Library" / "Logs" / "Coperceive"]


class LinuxAdapter(PlatformAdapter):
    os_name = "linux"
    passed_banner = "Real-App Linux E2E Test PASSED Successfully!"

    def prepare_environment(self, tmp_dir: Path) -> tuple[Path, Path, dict[str, str]]:
        state_dir = tmp_dir / "state"
        log_dir = tmp_dir / "logs"
        state_dir.mkdir(parents=True, exist_ok=True)
        log_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(state_dir, 0o700)
        library_dir = state_dir / "coperceive-data"
        env = {
            "COPERCEIVE_STATE_DIR": str(state_dir),
            "COPERCEIVE_TEST_LOG_DIR": str(log_dir),
            "PYTHONUNBUFFERED": "1",
            "RUST_BACKTRACE": "1",
            "NO_AT_BRIDGE": "1",
            "WEBKIT_DISABLE_COMPOSITING_MODE": "1",
            "WEBKIT_DISABLE_DMABUF_RENDERER": "1",
            "WEBKIT_FORCE_SANDBOX": "0",
            "LIBGL_ALWAYS_SOFTWARE": "1",
            "APPIMAGE_EXTRACT_AND_RUN": "1",
        }
        return state_dir, library_dir, env

    def prepare_appearance_light(self) -> None:
        return

    def restore_appearance(self) -> None:
        return

    def capture_screenshot(self, path: Path) -> None:
        if shutil.which("import"):
            try:
                subprocess.run(
                    ["import", "-window", "root", str(path)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=15,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                print(f"E2E_NOTE screenshot timeout capturing {path}", flush=True)
            return
        if shutil.which("xwd") and shutil.which("convert"):
            try:
                xwd = subprocess.run(
                    ["xwd", "-root", "-silent"],
                    capture_output=True,
                    timeout=15,
                    check=False,
                )
                if xwd.returncode == 0 and xwd.stdout:
                    subprocess.run(
                        ["convert", "xwd:-", str(path)],
                        input=xwd.stdout,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=15,
                        check=False,
                    )
            except subprocess.TimeoutExpired:
                print(f"E2E_NOTE screenshot timeout capturing {path}", flush=True)

    def trigger_quit(self, proc: subprocess.Popen[bytes], ctrl: ControllerClient | None) -> None:
        if ctrl is not None and ctrl.quit_app():
            return
        if proc.poll() is None:
            proc.terminate()

    def pre_close_viewer_gesture(self) -> None:
        return

    def step1_copy_diagnostics(self) -> None:
        print(
            "Step 1 skipped-with-reason (Linux): macOS-only osascript/pbpaste native Help -> "
            "Copy Diagnostics menu automation does not apply under Xvfb; diagnostics redaction "
            "is verified in Step 5i.",
            flush=True,
        )

    def step2d_verify_window_titles(self) -> None:
        print(
            "Step 2d skipped-with-reason (Linux): macOS-only osascript window-title enumeration "
            "skipped under bare Xvfb; stderr single-viewer-window check enforced.",
            flush=True,
        )

    def step5h_os_input_and_edit_shortcuts(
        self,
        ctrl: ControllerClient,
        focus_and_clear: Callable[[str], None],
        wait_for_field_value: Callable[[str, str, str], str],
        poll_until: Callable[
            [Callable[[], str], str, Callable[[str], bool], str],
            str,
        ],
    ) -> None:
        del ctrl, focus_and_clear, wait_for_field_value, poll_until
        print(
            "Step 5h skipped-with-reason (Linux): macOS-only osascript/pbpaste/Cmd+ Edit-menu "
            "and auto-capitalization checks do not apply on Linux; DOM input attributes "
            "(autocapitalize/autocorrect/spellcheck/autocomplete) were verified.",
            flush=True,
        )

    def reveal_button_label(self) -> str:
        return "Show in folder"

    def extra_failure_log_dirs(self) -> list[Path]:
        return []


def build_platform_adapter(os_name: str) -> PlatformAdapter:
    if os_name == "macos":
        return MacOSAdapter()
    if os_name == "linux":
        return LinuxAdapter()
    raise ValueError(f"Unsupported OS: {os_name}")


class MockUpdateServer:
    """Local mock update manifest server for Step 5d."""

    def __init__(self, log_file: Path) -> None:
        self.log_file = log_file
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.port = 0

    def start(self) -> int:
        owner = self
        log_file = self.log_file
        static_manifest = json.dumps(
            {
                "version": "0.2.0",
                "notes": "Mock release notes for the test.",
                "pub_date": "2026-10-24T00:00:00Z",
                "platforms": {
                    "darwin-aarch64": {
                        "signature": "",
                        "url": "http://127.0.0.1:9/Coperceive.app.tar.gz",
                    },
                    "darwin-x86_64": {
                        "signature": "",
                        "url": "http://127.0.0.1:9/Coperceive.app.tar.gz",
                    },
                    "linux-x86_64": {
                        "signature": "",
                        "url": "http://127.0.0.1:9/Coperceive.AppImage",
                    },
                    "windows-x86_64": {
                        "signature": "",
                        "url": "http://127.0.0.1:9/Coperceive-setup.exe",
                    },
                },
            }
        ).encode("utf-8")

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                if self.path == "/healthz":
                    self.send_response(200)
                    self.send_header("Content-Type", "text/plain")
                    self.send_header("Content-Length", "2")
                    self.end_headers()
                    self.wfile.write(b"ok")
                    return
                with open(log_file, "a", encoding="utf-8") as handle:
                    handle.write(self.path + "\n")
                if any(ext in self.path for ext in (".tar.gz", ".app", ".AppImage", ".exe")):
                    pkg = b"mock update archive payload content for minisign verification failure"
                    self.send_response(200)
                    self.send_header("Content-Type", "application/gzip")
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Content-Length", str(len(pkg)))
                    self.end_headers()
                    self.wfile.write(pkg)
                    return
                if "404" in self.path:
                    self.send_response(404)
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.end_headers()
                    return
                if "bad-sig" in self.path:
                    raw_minisign = (
                        "untrusted comment: signature from minisign secret key\n"
                        "RUTX5qZvdPSepPF2zsjvP/L/TQtBLRnWRvaq+4omK1UEugtQp/USrGiI1v762sDy"
                        "3id3aor7bRC9ETyeot6lMHHrWoZ5hjUM1Qc=\n"
                        "trusted comment: timestamp:1791649051\tfile:payload\tversion:0.2.0\n"
                        "DDq2CLOEl5cQmC2eJRHlnZPVViOZsu1CjpPKQoei4A/XBfkDqGO0B+QMrcrHgEQ5"
                        "qV3mne75tdFWhJpqUXh9AA=="
                    )
                    sig_b64 = base64.b64encode(raw_minisign.encode("utf-8")).decode("ascii")
                    my_port = owner.port
                    bad_body = json.dumps(
                        {
                            "version": "0.2.0",
                            "notes": "Bad signature notes.",
                            "pub_date": "2026-10-24T00:00:00Z",
                            "platforms": {
                                "darwin-aarch64": {
                                    "signature": sig_b64,
                                    "url": f"http://127.0.0.1:{my_port}/bad-sig-package.tar.gz",
                                },
                                "darwin-x86_64": {
                                    "signature": sig_b64,
                                    "url": f"http://127.0.0.1:{my_port}/bad-sig-package.tar.gz",
                                },
                                "linux-x86_64": {
                                    "signature": sig_b64,
                                    "url": f"http://127.0.0.1:{my_port}/bad-sig-package.AppImage",
                                },
                                "windows-x86_64": {
                                    "signature": sig_b64,
                                    "url": f"http://127.0.0.1:{my_port}/bad-sig-package.exe",
                                },
                            },
                        }
                    ).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Content-Length", str(len(bad_body)))
                    self.end_headers()
                    self.wfile.write(bad_body)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(static_manifest)))
                self.end_headers()
                self.wfile.write(static_manifest)

            def log_message(self, format: str, *args: object) -> None:
                del format, args
                return

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = int(self._server.server_address[1])
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

        deadline = time.monotonic() + 30.0
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{self.port}/healthz", timeout=1.0
                ) as resp:
                    if resp.status == 200:
                        return self.port
            except Exception:
                pass
            time.sleep(0.1)
        raise RuntimeError("mock update server did not become ready within 30s")

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None


@dataclass
class E2EContext:
    adapter: PlatformAdapter
    app_exe: Path
    repo_root: Path
    fixture_1: Path
    fixture_2: Path
    tmp_dir: Path
    screenshot_dir: Path
    state_dir: Path
    state_file: Path
    library_dir: Path
    marker_file: Path
    mock_dialog_file: Path
    import_source: Path
    repro_dir: Path
    fake_job_pid_file: Path
    base_env: dict[str, str]
    extra_env: dict[str, str] = field(default_factory=dict[str, str])
    proc: subprocess.Popen[bytes] | None = None
    current_stdout_log: Path | None = None
    current_stderr_log: Path | None = None
    current_ctrl: ControllerClient | None = None
    current_marker: MarkerInfo | None = None
    update_server: MockUpdateServer | None = None
    state_backup_5f: Path | None = None
    _stdout_fh: object | None = None
    _stderr_fh: object | None = None

    def close_log_handles(self) -> None:
        for attr in ("_stdout_fh", "_stderr_fh"):
            fh = getattr(self, attr)
            if fh is not None:
                try:
                    fh.close()
                except Exception:
                    pass
                setattr(self, attr, None)

    def launch(
        self,
        log_prefix: str,
        *,
        autoconnect_root: Path | None = None,
        env_overrides: dict[str, str] | None = None,
        wait_marker: bool = True,
        failure_exit_code: int = 1,
    ) -> MarkerInfo | None:
        if self.marker_file.exists():
            self.marker_file.unlink()
        stdout_log = self.tmp_dir / f"{log_prefix}_stdout.log"
        stderr_log = self.tmp_dir / f"{log_prefix}_stderr.log"
        self.current_stdout_log = stdout_log
        self.current_stderr_log = stderr_log

        env = os.environ.copy()
        env.pop("COPERCEIVE_AUTOCONNECT_ROOT", None)
        env.pop("COPERCEIVE_DESKTOP_AUTOCONNECT_ROOT", None)
        env.pop("COPERCEIVE_UPDATE_URL", None)
        env.update(self.base_env)
        env.update(self.extra_env)
        if autoconnect_root is not None:
            env["COPERCEIVE_AUTOCONNECT_ROOT"] = str(autoconnect_root)
        if env_overrides:
            env.update(env_overrides)

        stdout_fh = open(stdout_log, "wb")
        stderr_fh = open(stderr_log, "wb")
        self._stdout_fh = stdout_fh
        self._stderr_fh = stderr_fh
        self.proc = subprocess.Popen(
            [str(self.app_exe)],
            env=env,
            stdout=stdout_fh,
            stderr=stderr_fh,
        )
        print(f"App launched ({log_prefix}) with PID {self.proc.pid}", flush=True)

        if not wait_marker:
            return None

        info: MarkerInfo | None = None
        for i in range(1, 121):
            info = read_marker_file(self.marker_file)
            if info is not None:
                print(f"Marker file created after {i} checks ({i // 2}s)!", flush=True)
                break
            if self.proc.poll() is not None:
                print(
                    f"Error: App ({log_prefix}) died during startup (check {i})!",
                    file=sys.stderr,
                )
                print(stderr_log.read_text(encoding="utf-8", errors="replace"), file=sys.stderr)
                raise SystemExit(failure_exit_code)
            if i % 10 == 0:
                print(f"Still waiting for marker file... ({i // 2}s elapsed)", flush=True)
            time.sleep(0.5)

        if info is None:
            print(
                f"Error: Marker file was not created within timeout ({log_prefix})!",
                file=sys.stderr,
            )
            print("=== App stderr ===", file=sys.stderr)
            print(stderr_log.read_text(encoding="utf-8", errors="replace"), file=sys.stderr)
            print("=== App stdout ===", file=sys.stderr)
            print(stdout_log.read_text(encoding="utf-8", errors="replace"), file=sys.stderr)
            raise SystemExit(failure_exit_code)

        self.current_marker = info
        self.current_ctrl = ControllerClient(info.ctrl_port, info.ctrl_secret)
        return info

    def quit_current(self, wait_timeout_s: float = 6.0) -> None:
        proc = self.proc
        if proc is None:
            return
        if proc.poll() is None:
            self.adapter.trigger_quit(proc, self.current_ctrl)
            deadline = time.monotonic() + wait_timeout_s
            while time.monotonic() < deadline:
                if proc.poll() is not None:
                    break
                time.sleep(0.2)
            if proc.poll() is None:
                print("App did not exit cleanly on quit, sending SIGTERM...", flush=True)
                proc.terminate()
                try:
                    proc.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=2.0)
        self.close_log_handles()
        self.proc = None
        self.current_ctrl = None
        self.current_marker = None
        if self.marker_file.exists():
            self.marker_file.unlink()


def create_repro_fixture(repro_dir: Path) -> None:
    data = repro_dir / "data"
    code = repro_dir / "code" / "pipelines"
    results = repro_dir / "results"
    run_id = "plan-1790000000-aabbccddeeff"
    for path in (data / "weights", code, results / "plans", results / "runs" / run_id):
        path.mkdir(parents=True, exist_ok=True)
    weights = data / "weights" / "model.pt"
    weights.write_bytes(b"weights")
    (results / "runs" / run_id / "predictions.jsonl").write_text(
        '{"scene_id":"scene-a","score":0.2}\n',
        encoding="utf-8",
    )
    plan = {
        "id": run_id,
        "pipeline": {"name": "synthetic", "source": "custom"},
        "argv": ["python3", str(code / "run.py"), "--checkpoint", str(weights)],
        "environment": {"kind": "none"},
        "checkpoint": {
            "path": str(weights),
            "sha256": hashlib.sha256(weights.read_bytes()).hexdigest(),
            "kind": "file",
        },
        "split": {"path": "", "kind": ""},
        "output_dir": str(results / "runs" / run_id),
        "gres": None,
        "nothing_submitted": True,
    }
    (results / "plans" / f"{run_id}.json").write_text(json.dumps(plan), encoding="utf-8")
    profile = {
        "name": "Repro fixture",
        "ssh_alias": "repro-fixture",
        "host": None,
        "user": None,
        "data_roots": [str(data)],
        "scratch_template": str(repro_dir / "scratch") + "/$USER/$SLURM_JOB_ID",
        "results_root": str(results),
        "code_roots": [str(repro_dir / "code")],
        "scheduler": "slurm",
        "defaults": {},
        "environment": {
            "kind": "none",
            "modules": [],
            "interpreter": None,
            "container_image": None,
            "coperceive_command": ["coperceive"],
            "wheelhouse": None,
            "compute_has_internet": False,
        },
        "notes": None,
    }
    (repro_dir / "profile.json").write_text(json.dumps(profile), encoding="utf-8")
    (code / "run.py").write_text("print('synthetic')\n", encoding="utf-8")


def run_playback_requirement_c(ctrl: ControllerClient) -> None:
    """Measure Step 2 playback requirement (c): 15s loop wrap with <= 300ms gap.

    Kept as a dedicated function so Item 1 PR B can adjust the measurement
    window cleanly without touching other E2E steps.
    """
    print(
        "Testing playback requirement (c): play 15 s across the loop wrap with no "
        "frame-change gap over 300 ms...",
        flush=True,
    )
    pb_c_loop: dict[str, object] | None = None
    for _ in range(3):
        pb_c_loop = _as_dict(
            ctrl.eval_retry("2-loop-state", "window.__COPERCEIVE_PLAYBACK_STATE__()")
        )
        if pb_c_loop.get("loop") is True:
            break
        ctrl.eval_once_note("2-loop-on", "window.__COPERCEIVE_PLAYBACK__('loop')")
        time.sleep(0.1)
        pb_c_loop = _as_dict(
            ctrl.eval_retry("2-loop-state", "window.__COPERCEIVE_PLAYBACK_STATE__()")
        )
        if pb_c_loop.get("loop") is True:
            break
    assert pb_c_loop is not None and pb_c_loop.get("loop") is True, (
        f"Expected loop to be True, got {pb_c_loop}"
    )

    ctrl.eval_js("window.__COPERCEIVE_PLAYBACK__('seek', 120)")
    ctrl.eval_js(
        """
(() => {
    window.__RECORD_GAPS__ = {
        lastFrame: null,
        lastChange: performance.now(),
        maxGapMs: 0,
        wraps: 0,
        framesSeen: 0
    };
    if (window.__RECORD_INTERVAL__) clearInterval(window.__RECORD_INTERVAL__);
    window.__RECORD_INTERVAL__ = setInterval(() => {
        const st = window.__COPERCEIVE_PLAYBACK_STATE__
            ? window.__COPERCEIVE_PLAYBACK_STATE__() : null;
        if (!st) return;
        const now = performance.now();
        if (st.frameIndex !== window.__RECORD_GAPS__.lastFrame) {
            if (window.__RECORD_GAPS__.lastFrame !== null) {
                const gap = now - window.__RECORD_GAPS__.lastChange;
                if (gap > window.__RECORD_GAPS__.maxGapMs) {
                    window.__RECORD_GAPS__.maxGapMs = gap;
                }
                if (window.__RECORD_GAPS__.lastFrame > 140 && st.frameIndex < 10) {
                    window.__RECORD_GAPS__.wraps++;
                }
            }
            window.__RECORD_GAPS__.lastFrame = st.frameIndex;
            window.__RECORD_GAPS__.lastChange = now;
            window.__RECORD_GAPS__.framesSeen++;
        }
    }, 10);
})()
"""
    )

    ctrl.eval_js("document.querySelector('button.icon.primary')?.click()")
    time.sleep(15.5)

    try:
        ctrl.eval_js("window.__COPERCEIVE_PLAYBACK__('toggle')", timeout=15)
    except Exception as exc:
        print(f"E2E_NOTE step=2-stop-playback {type(exc).__name__}: {exc}", flush=True)
    try:
        ctrl.eval_js(
            "if (window.__RECORD_INTERVAL__) { "
            "clearInterval(window.__RECORD_INTERVAL__); "
            "window.__RECORD_INTERVAL__ = null; }",
            timeout=15,
        )
    except Exception as exc:
        print(f"E2E_NOTE step=2-clear-gap-recorder {type(exc).__name__}: {exc}", flush=True)

    report = _as_dict(ctrl.eval_retry("2-read-gaps", "window.__RECORD_GAPS__"))
    max_gap_ms = _as_float(report["maxGapMs"], "maxGapMs")
    wraps = _as_int(report["wraps"], "wraps")
    frames_seen = _as_int(report["framesSeen"], "framesSeen")
    print(
        f"Playback requirement (c) verified: max frame gap = {max_gap_ms:.1f}ms, "
        f"loop wraps observed = {wraps}, frames seen = {frames_seen}",
        flush=True,
    )
    assert wraps >= 1, f"Expected at least 1 loop wrap over 15s, got {wraps}"
    assert max_gap_ms <= 300.0, f"Frame change gap exceeded 300ms: {max_gap_ms:.1f}ms"


def step_1_initial_viewer(ctx: E2EContext) -> None:
    print("", flush=True)
    print("--- Step 1: Launch App with Fixture 1 (Multi-Scene Root) ---", flush=True)
    info = ctx.launch("app", autoconnect_root=ctx.fixture_1, failure_exit_code=4)
    assert info is not None and ctx.current_ctrl is not None
    ctrl = ctx.current_ctrl

    print(f"Connected to origin: {info.origin}", flush=True)
    print(f"Controller listening on port: {info.ctrl_port}", flush=True)

    req = urllib.request.Request(
        f"{info.origin}/api/scenes",
        headers={"Authorization": f"Bearer {info.token}"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        scenes_text = resp.read().decode("utf-8")
    print(f"Scenes response: {scenes_text}", flush=True)
    if "synth_001" not in scenes_text:
        print("Error: /api/scenes did not list scene 'synth_001'!", file=sys.stderr)
        raise SystemExit(5)
    print("Verified: fixture scene correctly served and listed.", flush=True)

    time.sleep(2.0)
    ctx.adapter.capture_screenshot(ctx.screenshot_dir / "01-initial-viewer.png")

    print("Waiting for initial viewer scene to load and render...", flush=True)
    counts = ctrl.wait_for_scene(30.0)
    print(f"Initial scene counts: {counts}", flush=True)
    assert _as_int(counts.get("points", 0), "points") > 0, f"Expected points > 0, got {counts}"
    assert _as_int(counts.get("boxes", 0), "boxes") > 0, f"Expected boxes > 0, got {counts}"

    print("Asserting Inspector empty state in UI...", flush=True)
    inspector_empty = _as_dict(
        ctrl.eval_js(
            """(() => {
        const el = document.querySelector(".side-card .panel-state-empty");
        if (!el) return null;
        return {
            title: el.querySelector(".panel-state-title")?.textContent?.trim() || "",
            desc: el.querySelector(".panel-state-description")?.textContent?.trim() || "",
            action: el.querySelector(".panel-state-action")?.textContent?.trim() || ""
        };
    })()"""
        )
    )
    empty_title = _as_str(inspector_empty["title"], "title")
    empty_desc = _as_str(inspector_empty["desc"], "desc")
    empty_action = _as_str(inspector_empty["action"], "action")
    assert empty_title == "No object selected", f"Unexpected title: {empty_title}"
    assert empty_desc.endswith("."), f"Description must end with period: {empty_desc}"
    assert empty_action == "", f"Empty inspector should have no action button: {empty_action}"
    print(f"Verified: Inspector empty state rendered correctly ({empty_title})", flush=True)

    print(
        "Asserting legacy last_theme migration: system appearance is light, "
        "app must follow system...",
        flush=True,
    )
    theme_val = ctrl.eval_js("document.documentElement.dataset.theme")
    print(f"Current document theme: {theme_val}", flush=True)
    assert theme_val == "light", (
        f"Expected theme to follow light system appearance despite legacy "
        f"last_theme: 'dark', got '{theme_val}'"
    )

    print("Driving real UI: clicking Drone viewpoint button...", flush=True)
    ctrl.eval_js(
        """(() => {
        const btn = document.querySelector('button[aria-keyshortcuts="3"]');
        if (!btn) throw new Error("Drone viewpoint button not found");
        btn.click();
    })()"""
    )

    print("Driving real UI: toggling Boxes layer OFF...", flush=True)
    ctrl.eval_js(
        """(() => {
        const eye = Array.from(document.querySelectorAll('.eye'))
            .find(b => b.textContent.includes("Boxes"));
        if (!eye) throw new Error("Boxes layer toggle not found");
        eye.click();
    })()"""
    )

    print("Driving real UI: toggling Frustum images layer OFF...", flush=True)
    ctrl.eval_js(
        """(() => {
        const eye = Array.from(document.querySelectorAll('.eye'))
            .find(b => b.textContent.includes("Frustum images"));
        if (eye && eye.getAttribute("aria-checked") === "true") {
            eye.click();
        }
    })()"""
    )

    print("Driving real UI: hiding UAV agent (uav_01)...", flush=True)
    ctrl.eval_js(
        """(() => {
        const swatch = Array.from(document.querySelectorAll('.swatch'))
            .find(b => b.textContent.includes("uav_01") || b.textContent.includes("Drone"));
        if (!swatch) throw new Error("uav_01 swatch toggle not found");
        swatch.click();
    })()"""
    )

    print(
        "Driving real UI: scrubbing timeline to sample 148 (second-to-last sample in end zone)...",
        flush=True,
    )
    ctrl.eval_js(
        """(() => {
        if (window.__COPERCEIVE_PLAYBACK__) {
            window.__COPERCEIVE_PLAYBACK__("seek", 148);
        }
    })()"""
    )

    print("Driving real UI: resizing left panel to 320px...", flush=True)
    ctrl.eval_js(
        """(() => {
        if (window.__COPERCEIVE_RESIZE_PANEL__) {
            window.__COPERCEIVE_RESIZE_PANEL__("left", 320);
        }
    })()"""
    )

    print("Asserting immediate scene-graph counts after UI interactions...", flush=True)
    updated_counts = _as_dict(ctrl.eval_js("window.__COPERCEIVE_SCENE_COUNTS__()"))
    print(f"Updated scene counts: {updated_counts}", flush=True)
    assert _as_int(updated_counts["boxes"], "boxes") == 0, (
        f"Expected boxes == 0 after toggling Boxes off, got {updated_counts}"
    )
    assert _as_int(updated_counts["frustums"], "frustums") == 4, (
        f"Expected frustums == 4 (UAV hidden), got {updated_counts}"
    )

    print("Flushing debounced state to controller...", flush=True)
    ctrl.eval_js("window.__COPERCEIVE_FLUSH_STATE__ ? window.__COPERCEIVE_FLUSH_STATE__() : null")
    print("Step 1 UI interactions verified successfully.", flush=True)

    print(f"Waiting for state.json to persist at {ctx.state_file}...", flush=True)
    deadline = time.monotonic() + 30.0
    saved: dict[str, object] = {}
    while time.monotonic() < deadline:
        try:
            loaded = json.loads(ctx.state_file.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                saved = cast(dict[str, object], loaded)
                layout = saved.get("layout")
                if (
                    saved.get("last_viewpoint") == "chase"
                    and saved.get("last_sample_index") == 148
                    and isinstance(layout, dict)
                    and cast(dict[str, object], layout).get("left_px") == 320
                ):
                    break
        except Exception:
            pass
        time.sleep(0.1)

    print(f"Step 1 verified state.json content: {saved}", flush=True)
    assert saved.get("last_viewpoint") == "chase", (
        f"Expected last_viewpoint chase in state.json, got {saved}"
    )
    assert saved.get("last_sample_index") == 148, (
        f"Expected last_sample_index 148 in state.json, got {saved}"
    )
    saved_layout = saved.get("layout")
    assert (
        isinstance(saved_layout, dict)
        and cast(dict[str, object], saved_layout).get("left_px") == 320
    ), f"Expected layout.left_px 320 in state.json, got {saved}"
    assert saved.get("theme_override") is None, (
        f"Expected theme_override to be None, got {saved.get('theme_override')}"
    )
    assert "last_theme" not in saved, (
        f"Expected legacy last_theme to be pruned from state.json, got {saved}"
    )

    ctx.adapter.step1_copy_diagnostics()


def step_1b_1c_1d_exports(ctx: E2EContext) -> None:
    print("", flush=True)
    print("--- Step 1b: Export Still and BEV in Real App and Assert Non-Empty PNGs ---", flush=True)
    ctrl = ctx.current_ctrl
    assert ctrl is not None

    print("Driving real UI: opening Export dialog...", flush=True)
    ctrl.eval_js(
        """(() => {
        const exportBtn = Array.from(document.querySelectorAll('button'))
            .find(b => b.textContent && b.textContent.trim() === "Export");
        if (!exportBtn) throw new Error("Export button not found");
        exportBtn.click();
    })()""",
        timeout=15,
    )
    time.sleep(0.5)

    still_dest = ctx.tmp_dir / f"e2e_still_{int(time.time())}.png"
    ctx.mock_dialog_file.write_text(f"{still_dest}\n", encoding="utf-8")
    print(f"Seeded mock dialog for still export: {still_dest}", flush=True)

    print("Driving real UI: clicking Save still button...", flush=True)
    ctrl.eval_js(
        """(() => {
        const dialog = document.querySelector('.dialog');
        if (!dialog) throw new Error("Export dialog not found in DOM");
        const stillBtn = Array.from(dialog.querySelectorAll('button'))
            .find(b => b.textContent && b.textContent.includes("Save still"));
        if (!stillBtn) throw new Error("Save still button not found in export dialog");
        stillBtn.click();
    })()""",
        timeout=15,
    )

    print("Waiting for still export to complete in real UI...", flush=True)
    deadline = time.monotonic() + 30.0
    still_res: dict[str, object] = {}
    still_exported = False
    while time.monotonic() < deadline:
        res_raw = ctrl.eval_js(
            """(() => {
            const dialog = document.querySelector('.dialog');
            const note = dialog ? dialog.querySelector('p.muted') : null;
            const noteText = note ? note.textContent : "";
            if (noteText.includes("Saved to ") && noteText.includes("still")) {
                return { done: true, note: noteText };
            }
            if (noteText.includes("Failed") || noteText.includes("Error")
                || noteText.includes("error")) {
                return { error: noteText };
            }
            return { done: false, note: noteText };
        })()""",
            timeout=15,
        )
        still_res = _as_dict(res_raw)
        if still_res.get("error"):
            raise RuntimeError(f"Still export failed in UI: {still_res['error']}")
        if still_res.get("done"):
            print(f"Still export completed in UI: {still_res}", flush=True)
            still_exported = True
            break
        time.sleep(0.5)

    assert still_exported, "Still export timed out in UI"
    assert still_dest.is_file(), f"Exported still file does not exist at {still_dest}"
    still_size = still_dest.stat().st_size
    print(f"Asserting non-empty still PNG: {still_dest} is {still_size} bytes", flush=True)
    assert still_size > 0, f"Exported still PNG {still_dest} is empty (0 bytes)"
    still_note = _as_str(still_res.get("note"), "still note")
    assert "Saved to " in still_note, f"Expected Saved to in note: {still_note}"
    assert still_dest.name in still_note, f"Expected filename in note: {still_note}"
    print("Step 1b verified: Still exported successfully and non-empty PNG asserted.", flush=True)

    bev_dest = ctx.tmp_dir / f"e2e_bev_{int(time.time())}.png"
    ctx.mock_dialog_file.write_text(f"{bev_dest}\n", encoding="utf-8")
    print(f"Seeded mock dialog for BEV export: {bev_dest}", flush=True)

    print("Driving real UI: clicking Bird's-eye export button...", flush=True)
    ctrl.eval_js(
        """(() => {
        const dialog = document.querySelector('.dialog');
        if (!dialog) throw new Error("Export dialog not found in DOM");
        const bevBtn = Array.from(dialog.querySelectorAll('button'))
            .find(b => b.textContent && b.textContent.includes("Bird's-eye"));
        if (!bevBtn) throw new Error("Bird's-eye button not found in export dialog");
        bevBtn.click();
    })()""",
        timeout=15,
    )

    print("Waiting for BEV export to complete and render preview in real UI...", flush=True)
    deadline = time.monotonic() + 30.0
    bev_res: dict[str, object] = {}
    bev_exported = False
    while time.monotonic() < deadline:
        res_raw = ctrl.eval_js(
            """(() => {
            const dialog = document.querySelector('.dialog');
            const img = dialog ? dialog.querySelector('img.plot-preview') : null;
            const note = dialog ? dialog.querySelector('p.muted') : null;
            const noteText = note ? note.textContent : "";
            if (img && img.src && noteText.includes("Saved to ") && noteText.includes("bev")) {
                return { done: true, src: img.src, note: noteText };
            }
            if (noteText.includes("Failed") || noteText.includes("Error")
                || noteText.includes("error")) {
                return { error: noteText };
            }
            return { done: false, note: noteText };
        })()""",
            timeout=15,
        )
        bev_res = _as_dict(res_raw)
        if bev_res.get("error"):
            raise RuntimeError(f"BEV export failed in UI: {bev_res['error']}")
        if bev_res.get("done"):
            print(f"BEV export completed in UI: {bev_res}", flush=True)
            bev_exported = True
            break
        time.sleep(0.5)

    assert bev_exported, "BEV export timed out in UI"
    print("Closing Export dialog...", flush=True)
    ctrl.eval_js(
        """(() => {
        const dialog = document.querySelector('.dialog');
        const closeBtn = dialog ? Array.from(dialog.querySelectorAll('button'))
            .find(b => b.textContent && b.textContent.trim() === "Close") : null;
        if (closeBtn) closeBtn.click();
    })()""",
        timeout=15,
    )

    assert bev_dest.is_file(), f"Exported BEV file does not exist at {bev_dest}"
    bev_size = bev_dest.stat().st_size
    print(f"Asserting non-empty BEV PNG: {bev_dest} is {bev_size} bytes", flush=True)
    assert bev_size > 0, f"Exported BEV PNG {bev_dest} is empty (0 bytes)"
    bev_note = _as_str(bev_res.get("note"), "bev note")
    assert "Saved to " in bev_note, f"Expected Saved to in note: {bev_note}"
    assert bev_dest.name in bev_note, f"Expected filename in note: {bev_note}"
    print(
        "Step 1c verified: BEV figure exported successfully and non-empty PNG asserted.", flush=True
    )

    print("Driving real UI: opening Export dialog for the paper zip...", flush=True)
    ctrl.eval_js(
        """(() => {
        const exportBtn = Array.from(document.querySelectorAll('button'))
            .find(b => b.textContent && b.textContent.trim() === "Export");
        if (!exportBtn) throw new Error("Export button not found");
        exportBtn.click();
    })()""",
        timeout=15,
    )
    time.sleep(0.5)

    paper_dest = ctx.tmp_dir / f"e2e_paper_{int(time.time())}.zip"
    ctx.mock_dialog_file.write_text(f"{paper_dest}\n", encoding="utf-8")
    print(f"Seeded mock dialog for paper export: {paper_dest}", flush=True)
    ctrl.eval_js(
        """(() => {
        const dialog = document.querySelector('.dialog');
        if (!dialog) throw new Error("Export dialog not found in DOM");
        const paperBtn = dialog.querySelector('#export-paper');
        if (!paperBtn) throw new Error("Export for paper button not found");
        paperBtn.click();
    })()""",
        timeout=15,
    )
    print("Waiting for paper export to finish...", flush=True)
    deadline = time.monotonic() + 60.0
    paper_exported = False
    while time.monotonic() < deadline:
        paper_res = _as_dict(
            ctrl.eval_js(
                """(() => {
                const dialog = document.querySelector('.dialog');
                const note = dialog ? dialog.querySelector('p.muted') : null;
                const noteText = note ? note.textContent : "";
                if (noteText.includes("Ready for Overleaf")) {
                    return { done: true, note: noteText };
                }
                if (noteText.includes("Failed") || noteText.includes("Error")
                || noteText.includes("error")) {
                    return { error: noteText };
                }
                return { done: false, note: noteText };
            })()""",
                timeout=15,
            )
        )
        if paper_res.get("error"):
            raise RuntimeError(f"Paper export failed in UI: {paper_res['error']}")
        if paper_res.get("done"):
            paper_exported = True
            break
        time.sleep(0.5)

    assert paper_exported, "Paper export timed out in UI"
    assert paper_dest.is_file(), f"Paper zip does not exist at {paper_dest}"
    paper_size = paper_dest.stat().st_size
    print(f"Asserting non-empty paper zip: {paper_dest} is {paper_size} bytes", flush=True)
    assert paper_size > 0, f"Paper zip {paper_dest} is empty"
    print(
        "Step 1d verified: Paper export wrote a non-empty zip and said Ready for Overleaf.",
        flush=True,
    )
    ctx.adapter.capture_screenshot(ctx.screenshot_dir / "02-before-quit.png")


def step_2_relaunch_and_playback(ctx: E2EContext) -> None:
    print("", flush=True)
    print(
        "--- Step 2: Quit and Relaunch to Assert Session Restore from DOM & Scene Graph ---",
        flush=True,
    )
    ctx.quit_current()
    print("App process cleanly exited.", flush=True)

    print("Relaunching app from persistent state.json...", flush=True)
    info = ctx.launch("relaunch", autoconnect_root=None, failure_exit_code=10)
    assert info is not None and ctx.current_ctrl is not None
    ctrl = ctx.current_ctrl
    print(f"Relaunched app active on ctrl port {info.ctrl_port}", flush=True)
    time.sleep(2.0)

    print("Waiting for relaunched viewer scene to restore...", flush=True)
    ctrl.wait_for_scene(30.0)

    print("Checking restored values from DOM...", flush=True)
    deadline = time.monotonic() + 15.0
    drone_pressed: object = None
    boxes_checked: object = None
    frustum_images_checked: object = None
    uav_checked: object = None
    while time.monotonic() < deadline:
        drone_pressed = ctrl.eval_js(
            "document.querySelector('button[aria-keyshortcuts=\"3\"]')"
            "?.getAttribute('aria-pressed')"
        )
        boxes_checked = ctrl.eval_js(
            """(() => {
            const eye = Array.from(document.querySelectorAll('.eye'))
            .find(b => b.textContent.includes("Boxes"));
            return eye ? (eye.getAttribute("aria-checked")
                || eye.getAttribute("aria-pressed")) : null;
        })()"""
        )
        frustum_images_checked = ctrl.eval_js(
            """(() => {
            const eye = Array.from(document.querySelectorAll('.eye'))
            .find(b => b.textContent.includes("Frustum images"));
            return eye ? (eye.getAttribute("aria-checked")
                || eye.getAttribute("aria-pressed")) : null;
        })()"""
        )
        uav_checked = ctrl.eval_js(
            """(() => {
            const swatch = Array.from(document.querySelectorAll('.swatch'))
            .find(b => b.textContent.includes("uav_01") || b.textContent.includes("Drone"));
            return swatch ? (swatch.getAttribute("aria-checked")
                || swatch.getAttribute("aria-pressed")) : null;
        })()"""
        )
        if (
            drone_pressed == "true"
            and boxes_checked == "false"
            and frustum_images_checked == "false"
            and uav_checked == "false"
        ):
            break
        time.sleep(0.2)

    assert drone_pressed == "true", (
        f"Expected Drone viewpoint aria-pressed==true, got {drone_pressed}"
    )
    assert boxes_checked == "false", (
        f"Expected Boxes toggle aria-checked==false, got {boxes_checked}"
    )
    assert frustum_images_checked == "false", (
        f"Expected Frustum images toggle aria-checked==false, got {frustum_images_checked}"
    )
    assert uav_checked == "false", f"Expected uav_01 swatch aria-checked==false, got {uav_checked}"

    print("Checking restored values from scene-graph counts...", flush=True)
    counts = _as_dict(ctrl.eval_js("window.__COPERCEIVE_SCENE_COUNTS__()"))
    print(f"Relaunched scene counts: {counts}", flush=True)
    assert _as_int(counts["boxes"], "boxes") == 0, f"Expected restored boxes == 0, got {counts}"
    assert _as_int(counts["frustums"], "frustums") == 4, (
        f"Expected restored frustums == 4 (UAV hidden), got {counts}"
    )
    assert _as_int(counts["points"], "points") > 0, f"Expected restored points > 0, got {counts}"

    print("Checking restored layout sizes from state.json...", flush=True)
    layout = _as_dict(
        ctrl.eval_js(
            "window.__COPERCEIVE_LAYOUT_SIZES__ ? window.__COPERCEIVE_LAYOUT_SIZES__() : null"
        )
    )
    print(f"Relaunched layout: {layout}", flush=True)
    assert layout.get("left_px") == 320, f"Expected restored left_px == 320, got {layout}"
    left_dom_w = _as_float(
        ctrl.eval_js("document.querySelector('aside.panel')?.getBoundingClientRect().width"),
        "left_dom_w",
    )
    print(f"Relaunched left panel DOM width: {left_dom_w}", flush=True)
    assert abs(left_dom_w - 320.0) < 2.0, f"Expected DOM width ~320, got {left_dom_w}"

    print("Verifying resume never restores into the end zone (must restore 0)...", flush=True)
    pb_restored = _as_dict(
        ctrl.eval_js(
            "window.__COPERCEIVE_PLAYBACK_STATE__ ? window.__COPERCEIVE_PLAYBACK_STATE__() : null"
        )
    )
    print(f"Relaunched playback state: {pb_restored}", flush=True)
    assert pb_restored["frameIndex"] == 0, (
        f"Expected resume into end zone to reset frameIndex to 0, got {pb_restored['frameIndex']}"
    )
    assert pb_restored["loop"] is True, (
        f"Expected default loop == True for scenes under 60s, got {pb_restored['loop']}"
    )

    print(
        "Testing playback requirement (a): restore at the second-to-last sample, click Play, "
        "assert still playing 3 s later with frameIndex advanced >= 25...",
        flush=True,
    )
    ctrl.eval_js("window.__COPERCEIVE_PLAYBACK__('seek', 148)")
    time.sleep(0.5)
    graph_before = _as_dict(ctrl.eval_js("window.__COPERCEIVE_SCENE_GRAPH__()"))
    frustums_before = {
        _as_str(_as_dict(f)["key"]): _as_dict(f)["position"]
        for f in cast(list[object], graph_before["frustums"])
    }
    ctrl.eval_js("document.querySelector('button.icon.primary')?.click()")
    time.sleep(3.0)
    pb_a = _as_dict(ctrl.eval_js("window.__COPERCEIVE_PLAYBACK_STATE__()"))
    print(f"State 3s after Play click: {pb_a}", flush=True)
    assert pb_a["playing"] is True, f"Expected still playing 3s later, got {pb_a}"
    frame_a = _as_int(pb_a["frameIndex"], "frameIndex")
    assert frame_a >= 25, f"Expected frameIndex advanced >= 25, got {frame_a}"
    graph_a = _as_dict(ctrl.eval_js("window.__COPERCEIVE_SCENE_GRAPH__()"))
    frustums_after = {
        _as_str(_as_dict(f)["key"]): _as_dict(f)["position"]
        for f in cast(list[object], graph_a["frustums"])
    }
    front_before = cast(list[object] | None, frustums_before.get("vehicle/cam_front"))
    front_now = cast(list[object] | None, frustums_after.get("vehicle/cam_front"))
    assert front_before and abs(_as_float(front_before[0]) - 149.5) < 1e-6, (
        f"cam_front at seek 148: {front_before}"
    )
    assert front_now and abs(_as_float(front_now[0]) - 1.5 - frame_a) <= 3.0, (
        f"cam_front {front_now} at frame {frame_a}"
    )
    for plane_obj in cast(list[object], graph_a["imagePlanes"]):
        plane = _as_dict(plane_obj)
        if plane.get("visible"):
            assert frustums_after.get(_as_str(plane["key"])) == plane["position"], (
                f"image plane off its frustum: {plane}"
            )
    print(
        f"Step 2 notices: cam_front frustum followed the vehicle from x={front_before[0]} "
        f"to x={front_now[0]} during playback",
        flush=True,
    )
    ctrl.eval_js("window.__COPERCEIVE_PLAYBACK__('toggle')")

    print(
        "Testing playback requirement (b): loop off, play to the end, assert Replay, "
        "click, assert it plays from 0...",
        flush=True,
    )
    ctrl.eval_js("window.__COPERCEIVE_PLAYBACK__('loop')")
    time.sleep(0.1)
    pb_b_loop = _as_dict(ctrl.eval_js("window.__COPERCEIVE_PLAYBACK_STATE__()"))
    assert pb_b_loop["loop"] is False, f"Expected loop to be False, got {pb_b_loop}"

    ctrl.eval_js("window.__COPERCEIVE_PLAYBACK__('seek', 125)")
    ctrl.eval_js("document.querySelector('button.icon.primary')?.click()")
    ctrl.eval_js(
        """
new Promise((resolve) => {
    const start = Date.now();
    const check = () => {
        const pb = window.__COPERCEIVE_PLAYBACK_STATE__
            ? window.__COPERCEIVE_PLAYBACK_STATE__() : null;
        if ((pb && !pb.playing && pb.frameIndex >= 149) || (Date.now() - start > 8000)) {
            resolve(pb);
        } else {
            setTimeout(check, 50);
        }
    };
    check();
})
""",
        timeout=15,
    )
    btn_label = ctrl.eval_js(
        "document.querySelector('button.icon.primary')?.getAttribute('aria-label')"
    )
    print(f"Play button aria-label at end of playback: {btn_label}", flush=True)
    assert btn_label == "Replay", f"Expected play button to show 'Replay', got '{btn_label}'"

    ctrl.eval_js("document.querySelector('button.icon.primary')?.click()")
    time.sleep(0.5)
    pb_replayed = _as_dict(ctrl.eval_js("window.__COPERCEIVE_PLAYBACK_STATE__()"))
    print(f"State after clicking Replay: {pb_replayed}", flush=True)
    assert pb_replayed["playing"] is True, (
        f"Expected playing == True after clicking Replay, got {pb_replayed}"
    )
    assert _as_int(pb_replayed["frameIndex"], "frameIndex") < 25, (
        f"Expected playback to restart from 0, got {pb_replayed['frameIndex']}"
    )
    ctrl.eval_js("window.__COPERCEIVE_PLAYBACK__('toggle')")

    run_playback_requirement_c(ctrl)

    print("Testing double-click reset of left panel resizer...", flush=True)
    ctrl.eval_js(
        """(() => {
        const resizer = document.querySelector('.panel-resizer-left');
        if (!resizer) throw new Error("panel-resizer-left not found");
        resizer.dispatchEvent(new MouseEvent('dblclick', { bubbles: true }));
    })()"""
    )
    layout_after_reset = _as_dict(ctrl.eval_js("window.__COPERCEIVE_LAYOUT_SIZES__()"))
    assert layout_after_reset.get("left_px") == 248, (
        f"Expected left_px reset to 248, got {layout_after_reset}"
    )

    print("Testing keyboard resize on focused handle...", flush=True)
    ctrl.eval_js(
        """(() => {
        const resizer = document.querySelector('.panel-resizer-left');
        if (!resizer) throw new Error("panel-resizer-left not found");
        resizer.focus();
        resizer.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
    })()"""
    )
    layout_after_kb = _as_dict(ctrl.eval_js("window.__COPERCEIVE_LAYOUT_SIZES__()"))
    assert layout_after_kb.get("left_px") == 258, (
        f"Expected left_px incremented by 10 to 258, got {layout_after_kb}"
    )

    print("Testing full-screen 3D view toggle with F and Esc...", flush=True)
    ctrl.eval_js(
        """(() => {
        window.dispatchEvent(new KeyboardEvent('keydown', { key: 'F', bubbles: true }));
    })()"""
    )
    time.sleep(0.3)
    is_fs = ctrl.eval_js(
        "Boolean(window.__COPERCEIVE_IS_FULLSCREEN__ && window.__COPERCEIVE_IS_FULLSCREEN__())"
    )
    ws_fs = ctrl.eval_js("Boolean(document.querySelector('.workspace.is-fullscreen'))")
    left_hidden = ctrl.eval_js(
        "(() => { const el = document.querySelector('aside.panel:not(.panel-right)'); "
        "return el === null || getComputedStyle(el).display === 'none'; })()"
    )
    right_hidden = ctrl.eval_js(
        "(() => { const el = document.querySelector('aside.panel-right'); "
        "return el === null || getComputedStyle(el).display === 'none'; })()"
    )
    timeline_vis = ctrl.eval_js("document.querySelector('.timeline') !== null")
    print(
        f"Full screen active: is_fs={is_fs}, ws_fs={ws_fs}, left_hidden={left_hidden}, "
        f"right_hidden={right_hidden}, timeline_vis={timeline_vis}",
        flush=True,
    )
    assert is_fs and ws_fs and left_hidden and right_hidden and timeline_vis, (
        "Full screen must hide panels and keep timeline"
    )
    ctx.adapter.capture_screenshot(ctx.screenshot_dir / "03b-fullscreen.png")

    ctrl.eval_js(
        """(() => {
        window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    })()"""
    )
    time.sleep(0.3)
    is_fs_after = ctrl.eval_js(
        "Boolean(window.__COPERCEIVE_IS_FULLSCREEN__ && window.__COPERCEIVE_IS_FULLSCREEN__())"
    )
    layout_restored = _as_dict(ctrl.eval_js("window.__COPERCEIVE_LAYOUT_SIZES__()"))
    print(
        f"Exited full screen: is_fs_after={is_fs_after}, layout_restored={layout_restored}",
        flush=True,
    )
    assert not is_fs_after, "Escape must exit full screen"
    assert layout_restored.get("left_px") == 258, (
        f"Previous sizes must be preserved, got {layout_restored}"
    )
    ctx.adapter.capture_screenshot(ctx.screenshot_dir / "03c-resized-panels.png")
    print(
        "Step 2 verified: All restored values from DOM and scene-graph counts match perfectly.",
        flush=True,
    )
    ctx.adapter.capture_screenshot(ctx.screenshot_dir / "03-after-relaunch.png")


def step_2b_loop_persistence(ctx: E2EContext) -> None:
    print("", flush=True)
    print("E2E_MARK step=2b", flush=True)
    print(
        "--- Step 2b: Loop choice survives quit and relaunch (state.json, not localStorage) ---",
        flush=True,
    )
    ctrl = ctx.current_ctrl
    assert ctrl is not None

    def wait_playback(
        active_ctrl: ControllerClient, step: str, timeout: float = 25.0
    ) -> dict[str, object]:
        deadline = time.monotonic() + timeout
        last = "none"
        while time.monotonic() < deadline:
            try:
                state = active_ctrl.eval_js(
                    "window.__COPERCEIVE_PLAYBACK_STATE__ ? "
                    "window.__COPERCEIVE_PLAYBACK_STATE__() : null",
                    timeout=15,
                )
                if isinstance(state, dict) and "loop" in state:
                    return cast(dict[str, object], state)
                last = f"unexpected {state!r}"
            except Exception as exc:
                last = f"{type(exc).__name__}: {exc}"
            time.sleep(0.4)
        print(f"E2E_FAIL step={step} {last}", flush=True)
        raise SystemExit(1)

    before = wait_playback(ctrl, "2b-before")
    print(f"Loop before toggle: {before}", flush=True)
    if before["loop"] is not False:
        ctrl.eval_once_note("2b-toggle", "window.__COPERCEIVE_PLAYBACK__('loop')")
    after = wait_playback(ctrl, "2b-after-toggle")
    if after["loop"] is not False:
        ctrl.eval_once_note("2b-toggle-again", "window.__COPERCEIVE_PLAYBACK__('loop')")
        after = wait_playback(ctrl, "2b-after-toggle-again")
    assert after["loop"] is False, f"Expected loop off before quit, got {after}"
    print("Loop is off; waiting for state.json write.", flush=True)

    time.sleep(1.0)
    saved: dict[str, object] | None = None
    last = "none"
    for _ in range(3):
        try:
            saved = ctrl.get_state()
            break
        except Exception as exc:
            last = f"{type(exc).__name__}: {exc}"
            time.sleep(0.5)
    if saved is None:
        print(f"E2E_FAIL step=2b-read-state {last}", flush=True)
        raise SystemExit(1)
    print(f"state.json last_loop={saved.get('last_loop')}", flush=True)
    assert saved.get("last_loop") is False, (
        f"Expected last_loop false in state.json, got {saved.get('last_loop')}"
    )

    ctx.quit_current()
    print("Relaunching to confirm loop stayed off...", flush=True)
    ctx.launch("loop_relaunch", failure_exit_code=11)
    assert ctx.current_ctrl is not None
    ctrl = ctx.current_ctrl

    deadline = time.monotonic() + 30.0
    state: dict[str, object] | None = None
    while time.monotonic() < deadline:
        try:
            counts = ctrl.eval_js(
                "window.__COPERCEIVE_SCENE_COUNTS__ ? window.__COPERCEIVE_SCENE_COUNTS__() : null",
                timeout=4,
            )
            st_raw = ctrl.eval_js("window.__COPERCEIVE_PLAYBACK_STATE__()")
            if (
                isinstance(counts, dict)
                and _as_int(cast(dict[str, object], counts).get("points", 0)) > 0
                and isinstance(st_raw, dict)
            ):
                state = cast(dict[str, object], st_raw)
                if _as_int(state.get("samples", 0)) > 1:
                    break
        except Exception:
            pass
        time.sleep(0.5)

    print(f"Loop after relaunch: {state}", flush=True)
    assert state is not None and state.get("loop") is False, (
        f"Loop choice did not survive relaunch: {state}"
    )
    print("Step 2b verified: loop stayed off across quit and relaunch.", flush=True)


def step_2c_window_geometry(ctx: E2EContext) -> None:
    print("", flush=True)
    print("E2E_MARK step=2c", flush=True)
    print(
        "--- Step 2c: Relaunch 3 times and assert viewer window frame is unchanged ---", flush=True
    )
    ctrl = ctx.current_ctrl
    assert ctrl is not None

    deadline = time.monotonic() + 15.0
    initial_ws: dict[str, object] | None = None
    while time.monotonic() < deadline:
        try:
            st = ctrl.get_state(timeout_s=5.0)
            ws = st.get("window_state")
            if isinstance(ws, dict):
                initial_ws = cast(dict[str, object], ws)
                break
        except Exception:
            pass
        time.sleep(0.5)

    print(f"Initial window state before 3 relaunches: {initial_ws}", flush=True)
    assert initial_ws is not None, "Expected window_state to be saved in state.json"
    for key in ("x", "y", "width", "height"):
        assert key in initial_ws, f"Missing {key} in window_state: {initial_ws}"

    for launch in (1, 2, 3):
        print(f"Relaunch cycle {launch}/3...", flush=True)
        ctx.quit_current()
        ctx.launch(f"geom_relaunch_{launch}", failure_exit_code=11)
        assert ctx.current_ctrl is not None
        ctrl = ctx.current_ctrl

        deadline = time.monotonic() + 30.0
        while time.monotonic() < deadline:
            try:
                counts = ctrl.eval_js(
                    "window.__COPERCEIVE_SCENE_COUNTS__ ? "
                    "window.__COPERCEIVE_SCENE_COUNTS__() : null",
                    timeout=4,
                )
                if (
                    isinstance(counts, dict)
                    and _as_int(cast(dict[str, object], counts).get("points", 0)) > 0
                ):
                    break
            except Exception:
                pass
            time.sleep(0.5)

        curr_ws: dict[str, object] | None = None
        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline:
            try:
                st = ctrl.get_state(timeout_s=5.0)
                ws = st.get("window_state")
                if isinstance(ws, dict):
                    curr_ws = cast(dict[str, object], ws)
                    break
            except Exception:
                pass
            time.sleep(0.5)

        print(f"Relaunch {launch} window state: {curr_ws}", flush=True)
        assert curr_ws is not None, f"Window state missing on relaunch {launch}"
        for key in ("x", "y", "width", "height"):
            diff = abs(_as_float(curr_ws[key], key) - _as_float(initial_ws[key], key))
            assert diff < 1.0, (
                f"Window frame drifted on relaunch {launch}: {key} was {curr_ws[key]}, "
                f"expected {initial_ws[key]} (diff {diff})"
            )
        print(
            f"Relaunch {launch}/3 verified: window frame unchanged "
            f"({curr_ws['width']}x{curr_ws['height']} at x={curr_ws['x']}, y={curr_ws['y']}).",
            flush=True,
        )

    print(
        "Step 2c verified: viewer window frame remained completely unchanged across 3 relaunches.",
        flush=True,
    )


def step_2d_single_viewer_window(ctx: E2EContext) -> None:
    print("", flush=True)
    print("--- Step 2d: Relaunch opens one viewer window ---", flush=True)
    assert ctx.current_stderr_log is not None
    stderr_text = ctx.current_stderr_log.read_text(encoding="utf-8", errors="replace")
    opened_count = sum(1 for line in stderr_text.splitlines() if "opened viewer window" in line)
    reused_count = sum(
        1 for line in stderr_text.splitlines() if "reusing existing viewer window" in line
    )
    print(
        f"Step 2d notices: opened viewer window={opened_count}, "
        f"reusing existing viewer window={reused_count}",
        flush=True,
    )
    if opened_count != 1:
        print(
            f"Error: expected exactly one 'opened viewer window' on the third relaunch, "
            f"got {opened_count}",
            file=sys.stderr,
        )
        print("=== stderr ===", file=sys.stderr)
        print(stderr_text, file=sys.stderr)
        raise SystemExit(16)
    ctx.adapter.step2d_verify_window_titles()
    print("Step 2d verified: one viewer window after relaunch.", flush=True)


def step_3_switch_single_scene(ctx: E2EContext) -> None:
    print("", flush=True)
    print("--- Step 3: Switch Dataset to Second Valid Root (Single Scene Root) ---", flush=True)
    ctrl = ctx.current_ctrl
    assert ctrl is not None

    ctx.mock_dialog_file.write_text(f"{ctx.fixture_2}\n", encoding="utf-8")
    print("Clicking UI dataset open button...", flush=True)
    btn_res = ctrl.eval_js(
        "(() => { const b = document.querySelector('.btn-open-dataset'); "
        "return b ? 'found' : 'NOT_FOUND'; })()"
    )
    print(f"Button presence: {btn_res}", flush=True)
    ctrl.eval_once_note("3-open-btn", "document.querySelector('.btn-open-dataset')?.click()")

    time.sleep(3.5)
    ctrl.wait_for(
        "dataset switch to the single-scene root",
        """
        (document.querySelector('.dataset-name')?.textContent || '').includes('synth_001') &&
        (window.__COPERCEIVE_SCENES__ ? window.__COPERCEIVE_SCENES__() : []).length === 1
        """,
        seconds=40.0,
    )

    dbg_initial = ctrl.eval_js(
        """(() => ({
        status: document.querySelector('.status')?.textContent,
        badge: document.querySelector('.dataset-name')?.textContent,
        scenes: window.__COPERCEIVE_SCENES__ ? window.__COPERCEIVE_SCENES__() :
            ((document.querySelector('#scene-id')?.getAttribute('data-options') || '')
                .split(',').filter(Boolean)),
        sceneId: document.querySelector('#scene-id')?.value,
        errBanner: document.querySelector('.dataset-error-msg')?.textContent,
        lastDatasetChange: window.__LAST_DATASET_CHANGE__,
        lastDraw: window.__LAST_DRAW__,
        counts: window.__COPERCEIVE_SCENE_COUNTS__ ? window.__COPERCEIVE_SCENE_COUNTS__() : null,
    }))()"""
    )
    print(f"DIAGNOSTICS AFTER SWITCH & SLEEP: {json.dumps(dbg_initial, indent=2)}", flush=True)

    print("Waiting for scene to load after folder switch...", flush=True)
    counts = ctrl.wait_for_scene(30.0)
    print(f"Counts after folder switch: {counts}", flush=True)
    assert _as_int(counts["points"], "points") > 0, (
        f"Expected points > 0 in new scene, got {counts}"
    )

    badge_text = ctrl.eval_js("document.querySelector('.dataset-name')?.textContent")
    print(f"Dataset badge text: {badge_text}", flush=True)
    assert "synth_001" in str(badge_text or ""), f"Expected synth_001 badge, got {badge_text}"
    print("Step 3 verified: switched cleanly to second fixture, new scene rendered.", flush=True)
    ctx.adapter.capture_screenshot(ctx.screenshot_dir / "04-switched-fixture.png")


def step_3b_switch_multi_scene(ctx: E2EContext) -> None:
    print("", flush=True)
    print("--- Step 3b: Switch Dataset from Single-Scene to Multi-Scene Root ---", flush=True)
    ctrl = ctx.current_ctrl
    assert ctrl is not None
    ctx.mock_dialog_file.write_text(f"{ctx.fixture_1}\n", encoding="utf-8")

    print("Clicking UI dataset open button to switch back to multi-scene root...", flush=True)
    before_raw = ctrl.eval_js(
        """(() => ({
        settings: window.__COPERCEIVE_VIEW_SETTINGS__
            ? window.__COPERCEIVE_VIEW_SETTINGS__() : null,
        graph: window.__COPERCEIVE_SCENE_GRAPH__ ? window.__COPERCEIVE_SCENE_GRAPH__() : null,
    }))()"""
    )
    before = _as_dict(before_raw)
    print(f"View settings before multi-scene switch: {json.dumps(before)}", flush=True)
    if not before.get("settings") or not before.get("graph"):
        raise SystemExit("E2E_FAIL step=3b-before missing view settings or scene graph")

    ctrl.eval_once_note("3b-open-btn", "document.querySelector('.btn-open-dataset')?.click()")
    time.sleep(3.5)

    ctrl.wait_for(
        "dataset switch back to the multi-scene root",
        """
        (document.querySelector('.dataset-name')?.textContent || '').includes('synthetic_scene') &&
        (window.__COPERCEIVE_SCENES__ ? window.__COPERCEIVE_SCENES__() : []).length === 2
        """,
        seconds=40.0,
    )

    print("Waiting for scene after switching to multi-scene root...", flush=True)
    counts = ctrl.wait_for_scene(30.0)
    print(f"Counts after multi-scene switch: {counts}", flush=True)
    assert _as_int(counts["points"], "points") > 0, f"Expected points > 0, got {counts}"

    scenes = _as_str_list(
        ctrl.eval_js(
            "window.__COPERCEIVE_SCENES__ ? window.__COPERCEIVE_SCENES__() : "
            "((document.querySelector('#scene-id')?.getAttribute('data-options') || '')"
            ".split(',').filter(Boolean))"
        ),
        "scenes",
    )
    print(f"Scene list options: {scenes}", flush=True)
    assert "synth_001" in scenes, f"Expected synth_001 in scene list, got {scenes}"
    assert "synth_002" in scenes, f"Expected synth_002 in scene list, got {scenes}"

    badge = ctrl.eval_js("document.querySelector('.dataset-name')?.textContent")
    print(f"Dataset badge: {badge}", flush=True)
    assert "synthetic_scene" in str(badge or ""), f"Expected synthetic_scene badge, got {badge}"

    after = _as_dict(
        ctrl.eval_js(
            """(async () => {
        const origin = sessionStorage.getItem("coperceive-origin");
        const token = sessionStorage.getItem("coperceive-token");
        const settings = window.__COPERCEIVE_VIEW_SETTINGS__
            ? window.__COPERCEIVE_VIEW_SETTINGS__() : null;
        const graph = window.__COPERCEIVE_SCENE_GRAPH__
            ? window.__COPERCEIVE_SCENE_GRAPH__() : null;
        const playback = window.__COPERCEIVE_PLAYBACK_STATE__
            ? window.__COPERCEIVE_PLAYBACK_STATE__() : null;
        if (!settings || !graph || !origin || !token) {
            return { settings, graph, playback, error: "missing hooks" };
        }
        const headers = { Authorization: "Bearer " + token };
        const summary = await (await fetch(
            origin + "/api/scenes/" + encodeURIComponent(settings.sceneId), { headers }
        )).json();
        const t0 = summary.times_us[0];
        const frame = await (await fetch(
            origin + "/api/scenes/" + encodeURIComponent(settings.sceneId)
            + "/frame?time_us=" + t0, { headers }
        )).json();
        const hidden = settings.hiddenAgents || {};
        const classes = settings.hiddenClasses || [];
        const expectedPoints = settings.layers.points
            ? (frame.point_sensors || []).reduce(
                (sum, sensor) => hidden[sensor.agent_id] ? sum : sum + sensor.count, 0)
            : 0;
        const expectedBoxes = settings.layers.boxes
            ? (frame.boxes || []).filter((box) => !hidden[box.agent_id]
                && (typeof box.category !== "string" || !classes.includes(box.category))).length
            : 0;
        return { settings, graph, playback, t0, expectedPoints, expectedBoxes };
    })()""",
            timeout=15,
        )
    )
    print(f"After multi-scene switch: {json.dumps(after)}", flush=True)
    before_settings = _as_dict(before["settings"])
    before_graph = _as_dict(before["graph"])
    after_settings = _as_dict(after["settings"])
    after_graph = _as_dict(after["graph"])
    after_playback = _as_dict(after["playback"])
    for key in ("viewpoint", "layers", "hiddenAgents", "hiddenClasses"):
        assert before_settings[key] == after_settings[key], (
            f"{key} changed across the root switch: before={before_settings[key]} "
            f"after={after_settings[key]}"
        )
    assert after_playback["frameIndex"] == 0, (
        f"Expected frame 0 after the switch, got {after_playback}"
    )
    old_scene = _as_str(before_settings["sceneId"], "old_scene")
    new_scene = _as_str(after_settings["sceneId"], "new_scene")
    old_token = before_graph["token"]
    tagged = cast(list[object], after_graph["tagged"])
    assert tagged, f"Expected tagged scene-graph objects, got {after_graph}"
    for item_obj in tagged:
        item = _as_dict(item_obj)
        assert item["sceneId"] == new_scene, (
            f"Object tagged with {item['sceneId']}, open scene is {new_scene}"
        )
        assert item["loadId"] != old_token, (
            f"Object still tagged with the pre-switch load {old_token}: {item}"
        )
        if old_scene != new_scene:
            assert item["sceneId"] != old_scene, (
                f"Object still tagged with the old scene {old_scene}"
            )
    assert after_graph["points"] == after["expectedPoints"], (
        f"Point count {after_graph['points']} != frame 0 {after['expectedPoints']}"
    )
    assert after_graph["boxes"] == after["expectedBoxes"], (
        f"Box count {after_graph['boxes']} != frame 0 {after['expectedBoxes']}"
    )
    print(
        "Step 3b verified: Switched from single-scene folder to multi-scene root cleanly.",
        flush=True,
    )


def step_4_invalid_folder_error(ctx: E2EContext) -> None:
    print("", flush=True)
    print("--- Step 4: Switch Dataset to Invalid Folder (Persistent Error UI) ---", flush=True)
    ctrl = ctx.current_ctrl
    assert ctrl is not None
    empty_dir = ctx.tmp_dir / "empty_invalid"
    empty_dir.mkdir(parents=True, exist_ok=True)
    ctx.mock_dialog_file.write_text(f"{empty_dir}\n", encoding="utf-8")

    def open_dataset() -> bool:
        for attempt in range(1, 4):
            deadline = time.monotonic() + 20.0
            while time.monotonic() < deadline:
                if ctrl.eval_retry("4-open", "!!document.querySelector('.btn-open-dataset')"):
                    break
                time.sleep(0.25)
            else:
                return False
            ctrl.eval_once_note(
                f"4-open-picker-{attempt}",
                "document.querySelector('.btn-open-dataset')?.click()",
                timeout=10,
            )
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                if not ctx.mock_dialog_file.exists():
                    return True
                time.sleep(0.1)
            print(
                f"E2E_NOTE step=4-open-picker attempt={attempt}: the click did not consume the "
                "picker answer; retrying",
                flush=True,
            )
        return False

    print("Triggering Open Dataset to invalid empty directory...", flush=True)
    assert open_dataset(), (
        "the viewer's Open button never appeared or never consumed the picker answer"
    )
    time.sleep(1.5)

    deadline = time.monotonic() + 20.0
    err_msg: object = None
    while time.monotonic() < deadline:
        err_msg = ctrl.eval_retry(
            "4-assert", "document.querySelector('.dataset-error-msg')?.textContent"
        )
        if isinstance(err_msg, str) and "No coperceive scenes in" in err_msg:
            break
        time.sleep(0.5)
    print(f"Error banner text: {err_msg}", flush=True)
    assert isinstance(err_msg, str) and "No coperceive scenes in" in err_msg, (
        f"Expected 'No coperceive scenes in', got {err_msg}"
    )
    assert "manifest.json and COMPLETE.json" in err_msg, (
        f"Expected manifest.json and COMPLETE.json notice, got {err_msg}"
    )

    choose_btn = ctrl.eval_retry(
        "4-assert", "document.querySelector('.dataset-error-choose-btn')?.textContent"
    )
    print(f"Choose another folder button: {choose_btn}", flush=True)
    assert isinstance(choose_btn, str) and "Choose another folder" in choose_btn, (
        "Expected 'Choose another folder' button"
    )

    print("Waiting 5 seconds to assert error UI stays persistent (not dismissed)...", flush=True)
    time.sleep(5.0)

    err_msg_after = ctrl.eval_retry(
        "4-assert", "document.querySelector('.dataset-error-msg')?.textContent"
    )
    assert isinstance(err_msg_after, str) and "No coperceive scenes in" in err_msg_after, (
        "Error UI disappeared after 5s! Must stay until dismissed."
    )
    print(
        "Step 4 verified: Persistent validation error UI displayed with "
        "'Choose another folder' button.",
        flush=True,
    )


def step_5_close_viewer(ctx: E2EContext) -> None:
    print("", flush=True)
    print(
        "--- Step 5: Close Viewer Window (Cmd+W) -> Returns to Connect Screen with Backdrop ---",
        flush=True,
    )
    assert ctx.current_ctrl is not None and ctx.proc is not None
    ctx.adapter.pre_close_viewer_gesture()
    ctx.current_ctrl.close_viewer_window()
    time.sleep(2.0)
    if ctx.proc.poll() is not None:
        print("Error: Closing viewer window killed the application process!", file=sys.stderr)
        raise SystemExit(14)
    print("Verified: App process is still running after closing viewer window.", flush=True)
    print("Connect screen is on screen.", flush=True)
    ctx.adapter.capture_screenshot(ctx.screenshot_dir / "05-connect-window.png")


def step_5b_connect_nav_and_fonts(ctx: E2EContext) -> None:
    print("", flush=True)
    print("--- Step 5b: Verify Library and Get Data Catalog on Connect Screen ---", flush=True)
    ctrl = ctx.current_ctrl
    assert ctrl is not None
    print(
        f"Querying Connect window DOM through loopback controller on port {ctrl.port}...",
        flush=True,
    )

    raw_tabs = ctrl.eval_retry(
        "5b",
        """
        (() => {
            const buttons = Array.from(document.querySelectorAll('nav button'))
                .map(b => b.textContent.trim());
            const hasLibrary = buttons.includes('Library');
            const hasGetData = buttons.includes('Get Data');
            return JSON.stringify({ hasLibrary, hasGetData, buttons });
        })()
        """,
    )
    res_json = _as_dict(json.loads(_as_str(raw_tabs, "tabs")))
    print(f"Connect screen nav tabs found: {res_json.get('buttons')}", flush=True)
    if not res_json.get("hasLibrary") or not res_json.get("hasGetData"):
        print(f"E2E_FAIL step=5b missing nav tabs: {res_json}", flush=True)
        raise SystemExit(1)
    print(
        "Step 5b verified: Connect window contains Library and Get Data catalog navigation.",
        flush=True,
    )

    raw_fonts = ctrl.eval_js(
        """
        (async () => {
            await document.fonts.ready;
            const geist = document.fonts.check('13px "Geist"');
            const geistMono = document.fonts.check('13px "Geist Mono"');
            const loaded = [...document.fonts]
                .filter(f => f.status === 'loaded')
                .map(f => f.family.replace(/['"]/g, ''));
            const loadedGeist = loaded.some(f => f.includes('Geist') && !f.includes('Mono'));
            const loadedGeistMono = loaded.some(f => f.includes('Geist Mono'));
            return JSON.stringify({ geist, geistMono, loadedGeist, loadedGeistMono });
        })()
        """
    )
    font_json = _as_dict(json.loads(_as_str(raw_fonts, "fonts")))
    assert font_json.get("geist") and font_json.get("loadedGeist"), (
        f"Geist font not loaded in packaged app: {font_json}"
    )
    assert font_json.get("geistMono") and font_json.get("loadedGeistMono"), (
        f"Geist Mono font not loaded in packaged app: {font_json}"
    )
    print(
        f"Step 5b verified: bundled Geist and Geist Mono loaded in packaged app: {font_json}",
        flush=True,
    )


def step_5c_import_cancel_resume_open(ctx: E2EContext) -> None:
    print("", flush=True)
    print("--- Step 5c: Import synthetic scene, cancel, resume, open ---", flush=True)
    ctrl = ctx.current_ctrl
    assert ctrl is not None and ctx.current_stderr_log is not None

    for old in (ctx.library_dir / "synth_001", ctx.library_dir / "synth_001.partial"):
        if old.exists():
            shutil.rmtree(old)

    def wait_text(label: str, seconds: float = 20.0) -> None:
        deadline = time.monotonic() + seconds
        last = ""
        while time.monotonic() < deadline:
            last = str(ctrl.eval_retry("5c", "document.body.innerText", timeout=15) or "")
            if label in last:
                return
            time.sleep(0.3)
        raise RuntimeError(f"did not see {label}: {last[-400:]}")

    wait_text("Use default")
    ctrl.click_button("Use default", use_eval_once_step="5c-click-Use default")
    time.sleep(0.5)
    state = _as_dict(json.loads(ctx.state_file.read_text(encoding="utf-8")))
    library_root_str = _as_str(state.get("library_root"), "library_root")
    assert library_root_str, f"library_root was not saved: {state}"
    library = Path(library_root_str)
    print(f"Step 5c library_root={library}", flush=True)

    ctrl.click_button("Open Folder", use_eval_once_step="5c-click-Open Folder")
    time.sleep(0.3)
    ctrl.click_button("Import from folder", use_eval_once_step="5c-click-Import from folder")
    time.sleep(0.3)
    source = ctrl.eval_retry("5c", "document.body.innerText.includes('synth_001') ? 'yes' : 'no'")
    assert source == "yes", "import source was not the synthetic scene"
    ctrl.click_button("Start import", use_eval_once_step="5c-click-Start import")

    deadline = time.monotonic() + 30.0
    seen = ""
    while time.monotonic() < deadline:
        seen = str(ctrl.eval_retry("5c", "document.body.innerText", timeout=15) or "")
        if " / " in seen:
            break
        time.sleep(0.2)
    else:
        raise RuntimeError(f"import did not copy a file: {seen[-400:]}")

    ctrl.click_button("Cancel", use_eval_once_step="5c-click-Cancel")
    time.sleep(0.8)
    partials = list(library.glob("*.partial"))
    finished = library / "synth_001"
    assert partials, f"cancel left no resumable partial in {library}"
    assert not finished.is_dir(), f"cancel published the scene: {finished}"
    print(f"Step 5c cancel left {[p.name for p in partials]}", flush=True)

    ctrl.click_button("Start import", use_eval_once_step="5c-click-Start import-resume")
    deadline = time.monotonic() + 40.0
    started = False
    while time.monotonic() < deadline:
        text = str(ctrl.eval_retry("5c", "document.body.innerText", timeout=15) or "")
        if "Starting…" in text or "Importing…" in text:
            started = True
        if "Import finished." in text:
            break
        if (
            started
            and "Import stopped" in text
            and "Importing" not in text
            and "Starting" not in text
        ):
            raise RuntimeError(text[-400:])
        time.sleep(0.4)
    else:
        raise RuntimeError("resume did not finish")
    assert (finished / "COMPLETE.json").is_file()
    assert not list(library.glob("*.partial")), "partial directory still listed after resume"
    print("Step 5c resume wrote synth_001/COMPLETE.json", flush=True)

    stray = finished / "vehicle" / "cam_front" / "0999999.png"
    assert not stray.exists(), f"import copied an image the scene does not list: {stray}"
    import_record = _as_dict(json.loads((finished / "IMPORT.json").read_text(encoding="utf-8")))
    notes = _as_str_list(import_record.get("notes") or [], "IMPORT.json notes")
    assert any("not listed in images.parquet" in note for note in notes), notes
    print(f"Step 5c skipped the unlisted frame ({notes})", flush=True)

    app_log = ctx.current_stderr_log
    log_mark = len(app_log.read_bytes())

    def new_log_lines() -> list[str]:
        return app_log.read_bytes()[log_mark:].decode("utf-8", "replace").splitlines()

    ctrl.click_button("Open Local Viewer", use_eval_once_step="5c-click-Open Local Viewer")
    deadline = time.monotonic() + 40.0
    scenes: object = None
    while time.monotonic() < deadline:
        try:
            scenes = ctrl.eval_js(
                "window.__COPERCEIVE_VIEWER_WINDOW__ && window.__COPERCEIVE_SCENES__ ? "
                "window.__COPERCEIVE_SCENES__() : null",
                timeout=15,
                window="viewer",
            )
        except Exception:
            scenes = None
        if isinstance(scenes, list) and "synth_001" in scenes:
            print(f"Step 5c opened scenes={scenes}", flush=True)
            break
        time.sleep(0.5)
    else:
        raise RuntimeError(f"viewer did not open synth_001, last={scenes}")

    lines = new_log_lines()
    opened = sum(1 for line in lines if "[viewer] opened viewer window" in line)
    reused = sum(1 for line in lines if "[viewer] reusing existing viewer window" in line)
    print(
        f"Step 5c viewer notices since Open Local Viewer: opened={opened}, reused={reused}",
        flush=True,
    )
    assert opened == 1 and reused == 0, (opened, reused)

    print("Testing wordmark link in viewer brings the main window's Library forward...", flush=True)
    clicked_raw = ctrl.eval_js(
        """(() => {
        const brand = document.querySelector('header.topbar a.brand');
        if (!brand) return JSON.stringify({clicked: false});
        const label = brand.getAttribute('aria-label');
        const href = brand.getAttribute('href');
        brand.click();
        return JSON.stringify({
            clicked: true, viewerWindow: Boolean(window.__COPERCEIVE_VIEWER_WINDOW__),
            label, href
        });
    })()""",
        timeout=15,
        window="viewer",
    )
    clicked = _as_dict(json.loads(_as_str(clicked_raw or "null")))
    print(f"Step 5c wordmark click in the viewer window: {clicked}", flush=True)
    assert clicked == {
        "clicked": True,
        "viewerWindow": True,
        "label": "Return to Library",
        "href": "#/viewer",
    }, clicked

    lib_state: dict[str, object] | None = None
    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline:
        try:
            raw_lib = ctrl.eval_js(
                """JSON.stringify({
                ipc: Boolean(window.__TAURI_INTERNALS__),
                tab: document.querySelector(
                    'nav[aria-label="Connect navigation"] button[aria-selected="true"]'
                )?.innerText?.trim() ?? null,
                listsScene: document.body.innerText.includes('synth_001'),
                failed: /Failed to load|(^|[^0-9])0 scenes/.test(document.body.innerText),
            })""",
                timeout=15,
                window="main",
            )
            lib_state = _as_dict(json.loads(_as_str(raw_lib or "null")))
        except Exception as exc:
            lib_state = {"error": str(exc)}
        if lib_state and lib_state.get("tab") == "Library" and lib_state.get("listsScene"):
            break
        time.sleep(0.5)
    print(f"Step 5c main window after the click: {lib_state}", flush=True)
    assert lib_state and lib_state.get("ipc") is True, lib_state
    assert lib_state.get("tab") == "Library" and lib_state.get("listsScene") is True, lib_state
    assert lib_state.get("failed") is False, lib_state

    viewer_state = _as_dict(
        json.loads(
            _as_str(
                ctrl.eval_js(
                    """JSON.stringify({
                hash: window.location.hash,
                connectShell: Boolean(
                    document.querySelector('nav[aria-label="Connect navigation"]')),
                landing: Boolean(document.querySelector('.hero')),
            })""",
                    timeout=15,
                    window="viewer",
                )
            )
        )
    )
    print(f"Step 5c viewer window after the click: {viewer_state}", flush=True)
    assert viewer_state == {"hash": "#/viewer", "connectShell": False, "landing": False}, (
        viewer_state
    )

    shown_lines: list[str] = []
    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline:
        shown_lines = [line for line in new_log_lines() if "[viewer] show-library -> " in line]
        if shown_lines:
            break
        time.sleep(0.3)
    print(f"Step 5c controller log: {shown_lines}", flush=True)
    assert len(shown_lines) == 1 and "shown=true" in shown_lines[0], shown_lines

    print(
        "Step 5c verified: wordmark in the viewer brings the main window's Library forward "
        "and it lists synth_001.",
        flush=True,
    )
    print("Step 5c verified: import, cancel, resume, open.", flush=True)


def step_5d_update_banner(ctx: E2EContext) -> None:
    print("", flush=True)
    print("--- Step 5d: Update banner from a mock latest.json ---", flush=True)
    update_log = ctx.tmp_dir / "update-hits.log"
    if update_log.exists():
        update_log.unlink()
    server = MockUpdateServer(update_log)
    update_port = server.start()
    ctx.update_server = server
    update_url = f"http://127.0.0.1:{update_port}/latest.json"
    ctx.extra_env["COPERCEIVE_UPDATE_URL"] = update_url
    print(f"Step 5d mock latest.json at {update_url}", flush=True)

    ctx.quit_current(wait_timeout_s=6.0)
    ctx.launch("update", failure_exit_code=17)
    assert ctx.current_ctrl is not None and ctx.proc is not None
    ctrl = ctx.current_ctrl
    time.sleep(2.0)

    deadline = time.monotonic() + 40.0
    text = ""
    while time.monotonic() < deadline:
        try:
            text = str(
                ctrl.eval_js(
                    "document.body ? document.body.innerText : ''",
                    timeout=15,
                    window="main",
                )
                or ""
            )
            if "coperceive 0.2.0 is ready" in text:
                break
        except Exception:
            pass
        time.sleep(0.4)
    else:
        raise RuntimeError(f"update banner did not appear: {text[-500:]}")
    print("Step 5d notices: sidebar card coperceive 0.2.0 is ready", flush=True)

    ctrl.click_button("What's new", window="main")
    time.sleep(0.3)
    notes = str(ctrl.eval_js("document.body.innerText", timeout=15, window="main") or "")
    assert "Mock release notes for the test." in notes, notes[-500:]
    print("Step 5d notices: What's new showed the mock notes", flush=True)

    ctrl.click_button("Install", window="main")
    time.sleep(0.4)
    restarted = ctrl.eval_js(
        "window.__COPERCEIVE_UPDATE_RESTART__ === true ? 'yes' : 'no'",
        timeout=15,
        window="main",
    )
    assert restarted == "yes", "Install did not record the test-env flag"
    print("Step 5d notices: Install (restart into the update) stayed in the test app", flush=True)

    ctrl.click_button("Later", window="main")
    time.sleep(0.4)
    after = str(ctrl.eval_js("document.body.innerText", timeout=15, window="main") or "")
    assert "is ready" not in after, after[-500:]
    print("Step 5d notices: Later hid the card", flush=True)

    ctrl.eval_js(
        f"window.__COPERCEIVE_UPDATE_URL__ = 'http://127.0.0.1:{update_port}/404.json'",
        timeout=15,
        window="main",
    )
    ctrl.eval_js("window.__COPERCEIVE_CHECK_UPDATES__(false)", timeout=15, window="main")
    time.sleep(0.5)
    txt_404_quiet = str(
        ctrl.eval_js("document.body ? document.body.innerText : ''", timeout=15, window="main")
        or ""
    )
    assert "Could not check for updates" not in txt_404_quiet, (
        f"404 background check must be quiet: {txt_404_quiet}"
    )
    assert "You're up to date" not in txt_404_quiet, (
        f"404 background check must not toast: {txt_404_quiet}"
    )
    print("Step 5d notices: 404 background check is quiet", flush=True)

    ctrl.eval_js("window.__COPERCEIVE_CHECK_UPDATES__(true)", timeout=15, window="main")
    time.sleep(0.5)
    txt_404_manual = str(
        ctrl.eval_js("document.body ? document.body.innerText : ''", timeout=15, window="main")
        or ""
    )
    assert "You're up to date" in txt_404_manual, (
        f"404 manual check must show You're up to date, got: {txt_404_manual}"
    )
    print("Step 5d notices: 404 manual check showed You're up to date", flush=True)

    ctrl.eval_js("window.__COPERCEIVE_UPDATE_RESTART__ = false", timeout=15, window="main")
    ctrl.eval_js(
        f"window.__COPERCEIVE_UPDATE_URL__ = 'http://127.0.0.1:{update_port}/bad-sig.json'",
        timeout=15,
        window="main",
    )
    ctrl.eval_js("window.__COPERCEIVE_CHECK_UPDATES__(true)", timeout=15, window="main")
    time.sleep(0.8)
    txt_bad_banner = str(
        ctrl.eval_js("document.body ? document.body.innerText : ''", timeout=15, window="main")
        or ""
    )
    assert "coperceive 0.2.0 is ready" in txt_bad_banner, (
        f"Expected the update card before the Install click, got: {txt_bad_banner}"
    )
    ctrl.click_button("Install", window="main")
    time.sleep(1.0)
    txt_bad_err = str(
        ctrl.eval_js("document.body ? document.body.innerText : ''", timeout=15, window="main")
        or ""
    )
    assert "Retry" in txt_bad_err, (
        f"Bad signature must show error with Retry button, got: {txt_bad_err}"
    )
    assert any(
        term in txt_bad_err.lower()
        for term in ("different key than the one provided", "signature verification failed")
    ), f"Expected signature verification error, got: {txt_bad_err}"
    assert "could not check for updates" not in txt_bad_err.lower(), (
        f"Must not be generic network failure: {txt_bad_err}"
    )
    restarted_bad = ctrl.eval_js(
        "window.__COPERCEIVE_UPDATE_RESTART__ === true ? 'yes' : 'no'",
        timeout=15,
        window="main",
    )
    assert restarted_bad == "no", "Manifest with bad signature must NOT be installed"
    print(
        "Step 5d notices: manifest with bad signature is rejected with error and not installed",
        flush=True,
    )
    print("Step 5d verified: banner, Later, and Restart.", flush=True)

    hits = update_log.read_text(encoding="utf-8", errors="replace") if update_log.is_file() else ""
    if "latest.json" not in hits:
        print("Error: the app did not request latest.json from the mock server", file=sys.stderr)
        print(hits, file=sys.stderr)
        raise SystemExit(17)
    print("Step 5d verified: mock server received latest.json.", flush=True)
    if ctx.proc.poll() is not None:
        print("Error: Install quit the app", file=sys.stderr)
        raise SystemExit(17)


def step_5e_download_pause_resume_cancel(ctx: E2EContext) -> None:
    print("", flush=True)
    print("--- Step 5e: Pause, Resume and Cancel a dataset download job (Get data) ---", flush=True)
    ctrl = ctx.current_ctrl
    assert ctrl is not None
    ctrl.close_viewer_window()
    time.sleep(1.5)

    def body_text() -> str:
        return str(ctrl.eval_js("document.body.innerText", timeout=15) or "")

    def progress_percent() -> int | None:
        val = ctrl.eval_js(
            "document.querySelector('[role=progressbar]') ? "
            "document.querySelector('[role=progressbar]').getAttribute('aria-valuenow') : null",
            timeout=15,
        )
        return int(str(val)) if val not in (None, "") else None

    ctrl.click_button("Get Data", exact=True)
    ctrl.wait_for(
        "Get data catalog",
        "document.body.innerText.includes('Dev tier') && "
        "document.body.innerText.includes('Griffin')",
        seconds=45.0,
    )
    catalog_text = body_text()
    assert "Could not load the dataset list" not in catalog_text, catalog_text[-500:]

    ctrl.click_button("Dev tier")
    ctrl.wait_for(
        "Griffin dry-run panel",
        "document.body.innerText.includes('Download Griffin (dev)') && "
        "document.body.innerText.includes('Start Download')",
        seconds=30.0,
    )

    checkbox = ctrl.eval_js(
        """
        (() => {
            const box = document.querySelector('input[type=checkbox]');
            if (!box) return 'missing';
            if (!box.checked) box.click();
            return box.checked ? 'checked' : 'not-checked';
        })()
        """,
        timeout=15,
    )
    assert checkbox == "checked", f"licence checkbox state: {checkbox}"

    start_state = ctrl.eval_js(
        """
        (() => {
            const button = Array.from(document.querySelectorAll('button')).find(
                (item) => (item.textContent || '').trim() === 'Start Download'
            );
            if (!button) return 'missing';
            return button.disabled ? 'disabled' : 'enabled';
        })()
        """,
        timeout=15,
    )
    assert start_state == "enabled", f"Start Download is {start_state}"

    ctrl.click_button("Start Download", exact=True)
    ctrl.wait_for(
        "download progress panel",
        """
        !!document.querySelector('[role=progressbar]') &&
        Array.from(document.querySelectorAll('button'))
            .some((item) => (item.textContent || '').trim() === 'Pause')
        """,
        seconds=20.0,
    )
    assert "Stage: download" in body_text(), body_text()[-500:]

    ctrl.wait_for(
        "download progress to flow",
        "parseInt(document.querySelector('[role=progressbar]')"
        "?.getAttribute('aria-valuenow') || '0', 10) >= 7",
        seconds=25.0,
    )
    print(f"Step 5e notices: download progress started at {progress_percent()}%", flush=True)

    pause_note = ctrl.eval_js(
        """
        (async () => {
            const button = Array.from(document.querySelectorAll('button')).find(
                (item) => (item.textContent || '').trim() === 'Pause'
            );
            if (!button) return 'missing-pause-button';
            button.click();
            const deadline = Date.now() + 4000;
            while (Date.now() < deadline) {
                if (document.body.innerText.includes('Download paused.')) return 'seen';
                await new Promise((resolve) => setTimeout(resolve, 10));
            }
            return 'not-seen';
        })()
        """,
        timeout=15,
    )
    assert pause_note == "seen", f"Pause did not show 'Download paused.': {pause_note}"
    ctrl.wait_for(
        "Resume button after Pause",
        "Array.from(document.querySelectorAll('button'))"
        ".some((item) => (item.textContent || '').trim() === 'Resume')",
        seconds=5.0,
    )

    time.sleep(0.6)
    paused_percent = progress_percent()
    time.sleep(0.6)
    assert progress_percent() == paused_percent, (
        f"progress moved while paused: {paused_percent} -> {progress_percent()}"
    )
    assert paused_percent is not None
    print(
        f"Step 5e notices: Pause -> 'Download paused.', progress frozen at {paused_percent}%",
        flush=True,
    )

    if (
        not ctx.fake_job_pid_file.is_file()
        or not ctx.fake_job_pid_file.read_text(encoding="utf-8").strip()
    ):
        print("Error: the fake dataset job did not report its pid", file=sys.stderr)
        raise SystemExit(18)
    fake_job_pid = ctx.fake_job_pid_file.read_text(encoding="utf-8").strip()
    ps_res = subprocess.run(
        ["ps", "-o", "stat=", "-p", fake_job_pid],
        capture_output=True,
        text=True,
        check=False,
    )
    fake_job_stat = ps_res.stdout.strip()
    print(
        f"Step 5e notices: fake job pid {fake_job_pid} state after Pause: "
        f"{fake_job_stat or 'gone'}",
        flush=True,
    )
    if not fake_job_stat.startswith("T"):
        print(
            f"Error: paused dataset job is not stopped (ps stat '{fake_job_stat}')", file=sys.stderr
        )
        raise SystemExit(18)

    resume_note = ctrl.eval_js(
        """
        (async () => {
            const seen = [];
            const capture = () => {
                if (seen.length === 0
                    && (document.body.innerText || '').includes('Download resumed.')) {
                    seen.push('Download resumed.');
                }
            };
            const observer = new MutationObserver(capture);
            observer.observe(
                document.body, { childList: true, subtree: true, characterData: true });
            const button = Array.from(document.querySelectorAll('button')).find(
                (item) => (item.textContent || '').trim() === 'Resume'
            );
            if (!button) {
                observer.disconnect();
                return 'missing-resume-button';
            }
            await new Promise((resolve) => requestAnimationFrame(resolve));
            button.click();
            const deadline = Date.now() + 6000;
            while (Date.now() < deadline && seen.length === 0) {
                capture();
                await new Promise((resolve) => setTimeout(resolve, 10));
            }
            observer.disconnect();
            return seen.length > 0 ? 'seen' : 'not-seen';
        })()
        """,
        timeout=20,
    )
    if resume_note == "seen":
        print("Step 5e notices: Resume -> 'Download resumed.'", flush=True)
    else:
        print(
            f"E2E_NOTE step=5e-resume-note {resume_note}: the resumed progress event replaced "
            "the note before it painted; asserting button and progress instead",
            flush=True,
        )
    ctrl.wait_for(
        "Pause button after Resume",
        "Array.from(document.querySelectorAll('button'))"
        ".some((item) => (item.textContent || '').trim() === 'Pause')",
        seconds=10.0,
    )

    deadline = time.monotonic() + 15.0
    resumed_percent: int | None = None
    while time.monotonic() < deadline:
        resumed_percent = progress_percent()
        if resumed_percent is not None and resumed_percent > paused_percent:
            break
        time.sleep(0.2)
    else:
        raise RuntimeError(
            f"progress did not resume: still {resumed_percent}% after {paused_percent}%"
        )
    print(
        f"Step 5e notices: resume restored the Pause button, progress advanced "
        f"{paused_percent}% -> {resumed_percent}%",
        flush=True,
    )

    ctrl.click_button("Cancel", exact=True)
    ctrl.wait_for(
        "progress panel to stop after Cancel",
        """
        !document.querySelector('[role=progressbar]') &&
        Array.from(document.querySelectorAll('button'))
            .some((item) => (item.textContent || '').trim() === 'Start Download')
        """,
        seconds=15.0,
    )
    assert (
        ctrl.eval_js(
            "Array.from(document.querySelectorAll('button'))"
            ".some((item) => (item.textContent || '').trim() === 'Pause')",
            timeout=15,
        )
        is False
    ), "Pause button still visible after Cancel"
    print(
        "Step 5e notices: Cancel removed the progress panel and returned to Start Download",
        flush=True,
    )

    pid_int = int(fake_job_pid)
    for _ in range(20):
        try:
            os.kill(pid_int, 0)
        except OSError:
            break
        time.sleep(0.2)
    else:
        print(f"Error: Cancel left the dataset job running (pid {fake_job_pid})", file=sys.stderr)
        raise SystemExit(19)
    print(
        "Step 5e verified: Pause, Resume and Cancel of a dataset download job in Get data.",
        flush=True,
    )


def step_5f_move_library(ctx: E2EContext) -> None:
    print("", flush=True)
    print("--- Step 5f: Move library to a new folder (Settings -> Change location) ---", flush=True)
    ctx.quit_current(wait_timeout_s=6.0)

    lib_a = ctx.tmp_dir / "e2e-library-a"
    lib_b = ctx.tmp_dir / "e2e-library-b"
    state_backup = ctx.tmp_dir / "state-before-5f.json"
    for p in (lib_a, lib_b):
        if p.exists():
            shutil.rmtree(p)
    lib_a.mkdir(parents=True)
    shutil.copytree(ctx.fixture_1 / "synth_001", lib_a / "synth_001")
    if (
        not (lib_a / "synth_001" / "manifest.json").is_file()
        or not (lib_a / "synth_001" / "COMPLETE.json").is_file()
    ):
        print(
            f"Error: could not seed the temp library from {ctx.fixture_1}/synth_001",
            file=sys.stderr,
        )
        raise SystemExit(20)
    shutil.copy2(ctx.state_file, state_backup)
    ctx.state_backup_5f = state_backup

    state_data = _as_dict(json.loads(ctx.state_file.read_text(encoding="utf-8")))
    state_data["library_root"] = str(lib_a)
    ctx.state_file.write_text(json.dumps(state_data, indent=2) + "\n", encoding="utf-8")
    print(
        f"Step 5f notices: temp library seeded at {lib_a}; state.json will be restored "
        "from the pre-step copy.",
        flush=True,
    )

    ctx.launch("move", failure_exit_code=20)
    assert ctx.current_ctrl is not None
    ctrl = ctx.current_ctrl
    time.sleep(1.0)
    ctrl.close_viewer_window()
    time.sleep(1.5)

    ctx.mock_dialog_file.write_text(f"{lib_b}\n", encoding="utf-8")
    ctrl.click_button("Library")
    ctrl.wait_for(
        "Library screen with the seeded temp library",
        f"document.body.innerText.includes({json.dumps(str(lib_a))}) && "
        "document.body.innerText.includes('synth_001')",
        seconds=30.0,
    )
    library_text = str(ctrl.eval_js("document.body.innerText", timeout=15) or "")
    assert "synth_001" in library_text, library_text[-500:]
    assert "1 scene" in library_text, library_text[-500:]

    ctrl.click_button("Change location")
    ctrl.wait_for(
        "Change Library Location dialog",
        "document.body.innerText.includes('Change Library Location')",
        seconds=10.0,
    )
    dialog_text = str(ctrl.eval_js("document.body.innerText", timeout=15) or "")
    assert f"Current: {lib_a}" in dialog_text, dialog_text[-500:]

    ctrl.click_button("Select New Folder")
    ctrl.wait_for(
        "library relocated to the new folder",
        f"document.body.innerText.includes({json.dumps(str(lib_b))}) && "
        "!document.body.innerText.includes('Change Library Location')",
        seconds=30.0,
    )
    moved_text = str(ctrl.eval_js("document.body.innerText", timeout=15) or "")
    assert str(lib_b) in moved_text, moved_text[-500:]
    assert str(lib_a) not in moved_text, (
        f"old library path still shown after the move: {moved_text[-500:]}"
    )
    assert "synth_001" in moved_text, moved_text[-500:]
    assert "1 scene" in moved_text, moved_text[-500:]
    print(f"Step 5f notices: Library now lists synth_001 under {lib_b}", flush=True)

    if not (lib_b / "synth_001").is_dir() or not (lib_b / "synth_001" / "COMPLETE.json").is_file():
        print(f"Error: synth_001 was not moved to the new library {lib_b}", file=sys.stderr)
        raise SystemExit(21)
    if (lib_a / "synth_001").exists():
        print(f"Error: synth_001 is still in the old library {lib_a}", file=sys.stderr)
        raise SystemExit(21)
    if list(lib_a.iterdir()):
        print(
            f"Error: the old library was not left empty: {list(lib_a.iterdir())}", file=sys.stderr
        )
        raise SystemExit(21)
    if list(lib_a.glob("*.partial")) or list(lib_b.glob("*.partial")):
        print("Error: a .partial directory was left behind by the move", file=sys.stderr)
        raise SystemExit(21)

    deadline = time.monotonic() + 5.0
    root: object = None
    while time.monotonic() < deadline:
        try:
            root = json.loads(ctx.state_file.read_text(encoding="utf-8")).get("library_root")
        except Exception:
            root = None
        if root == str(lib_b):
            break
        time.sleep(0.1)
    assert root == str(lib_b), f"state.json library_root is {root!r}, expected {str(lib_b)!r}"
    print(f"Step 5f notices: state.json library_root={root}", flush=True)

    shutil.copy2(state_backup, ctx.state_file)
    ctx.state_backup_5f = None
    print("Step 5f restored the pre-step state.json (real libraries were never moved).", flush=True)
    print(
        "Step 5f verified: Move library relocated synth_001 to the new root, left the old root "
        "empty, and updated state.json.",
        flush=True,
    )

    ctx.extra_env.pop("COPERCEIVE_UPDATE_URL", None)
    if ctx.update_server is not None:
        ctx.update_server.stop()
        ctx.update_server = None
    ctx.quit_current(wait_timeout_s=6.0)

    info = ctx.launch("settle", failure_exit_code=22)
    assert info is not None and ctx.current_ctrl is not None
    ctx.current_ctrl.close_viewer_window()
    time.sleep(1.5)
    print(
        f"Step 5f settle: relaunched from the restored state.json without COPERCEIVE_UPDATE_URL; "
        f"/eval port={info.ctrl_port}.",
        flush=True,
    )


def step_5g_library_validation_cache(ctx: E2EContext) -> None:
    print("", flush=True)
    print(
        "--- Step 5g: Library validation badge runs in the background and is cached ---", flush=True
    )
    ctrl = ctx.current_ctrl
    assert ctrl is not None
    ctrl.close_viewer_window()
    time.sleep(1.5)

    deadline = time.monotonic() + 20.0
    while time.monotonic() < deadline:
        found = ctrl.eval_retry(
            "5g",
            "!!Array.from(document.querySelectorAll('nav button'))"
            ".find((item) => (item.textContent || '').trim() === 'Library')",
            timeout=15,
        )
        if found:
            break
        time.sleep(0.25)
    else:
        raise RuntimeError("Library tab not found on the Connect window")

    res = ctrl.eval_once_note(
        "5g-click-library",
        """
        (() => {
            const button = Array.from(document.querySelectorAll('nav button')).find(
                (item) => (item.textContent || '').trim() === 'Library'
            );
            if (!button) return 'missing';
            button.click();
            return 'clicked';
        })()
        """,
    )
    if res != "clicked":
        raise RuntimeError("Library tab could not be clicked")

    deadline = time.monotonic() + 60.0
    seen = ""
    while time.monotonic() < deadline:
        seen = str(ctrl.eval_retry("5g", "document.body.innerText", timeout=15) or "")
        if "Valid ✓" in seen:
            break
        time.sleep(0.5)
    else:
        diag = ctrl.eval_retry(
            "5g",
            """(() => ({
            body: (document.body.innerText || '').slice(-800),
            nav: Array.from(document.querySelectorAll('nav button'))
                .map((item) => (item.textContent || '').trim()),
            libraryBadges: Array.from(document.querySelectorAll(
                '.library-badge, .badge, [data-validate]'
            )).map((item) => (item.textContent || '').trim()).slice(0, 8),
            scenes: window.__COPERCEIVE_SCENES__ ? window.__COPERCEIVE_SCENES__() : null,
            updateBanner: !!document.body.innerText.match(/is ready[.] Restart to update/),
        }))()""",
            timeout=15,
        )
        raise RuntimeError(
            f"Library badge never became valid without clicking Validate now: "
            f"{seen[-500:]} diag={diag}"
        )

    state = _as_dict(json.loads(ctx.state_file.read_text(encoding="utf-8")))
    cache = _as_dict(state.get("validation_cache") or {})
    synth = [key for key in cache if key.endswith("/synth_001")]
    assert synth, f"background validation did not cache synth_001: {cache}"
    entry = _as_dict(cache[synth[0]])
    assert entry.get("valid") is True, entry
    print(f"Step 5g cache={entry}", flush=True)
    print(
        "Step 5g verified: Library badge ran in the background and its answer is cached.",
        flush=True,
    )


def step_5h_hpc_input_autocap(ctx: E2EContext) -> None:
    print("", flush=True)
    print("--- Step 5h: macOS Auto-capitalization Disabled in Real App ---", flush=True)
    ctrl = ctx.current_ctrl
    assert ctrl is not None

    def poll_until(
        read_fn: Callable[[], str],
        expected_desc: str,
        match_fn: Callable[[str], bool],
        context: str,
        timeout_s: float = 5.0,
        interval_s: float = 0.1,
    ) -> str:
        deadline = time.monotonic() + timeout_s
        last_val = ""
        while True:
            last_val = read_fn()
            if match_fn(last_val):
                return last_val
            if time.monotonic() >= deadline:
                raise AssertionError(
                    f"{context}: holds {last_val!r}, expected {expected_desc} "
                    f"after {timeout_s:.1f}s"
                )
            time.sleep(interval_s)

    def wait_for_field_value(field_id: str, expected: str, context: str) -> str:
        script = f"(() => (document.getElementById({json.dumps(field_id)}) || {{}}).value)()"
        return poll_until(
            lambda: str(ctrl.eval_js(script, timeout=15) or ""),
            repr(expected),
            lambda v: v == expected,
            f"{context} (#{field_id})",
        )

    def wait_for_dom(predicate_js: str, context: str) -> str:
        return poll_until(
            lambda: str(ctrl.eval_js(predicate_js, timeout=15) or ""),
            "truthy",
            bool,
            context,
        )

    nav_res = ctrl.eval_js(
        """(() => {
        const tab = Array.from(document.querySelectorAll('nav button')).find(
            b => (b.textContent || '').trim() === 'HPC Workspace'
        );
        if (!tab) return 'missing_hpc_tab';
        tab.click();
        return 'clicked';
    })()""",
        timeout=15,
    )
    print(f"Step 5h HPC tab navigation: {nav_res}", flush=True)
    wait_for_dom(
        "(() => document.getElementById('hpc-user') ? 'already_open' : "
        "(Array.from(document.querySelectorAll('button'))"
        ".find(b => (b.textContent || '').trim() === 'Add server') ? 'ready' : ''))()",
        "Step 5h HPC Workspace tab",
    )

    add_res = ctrl.eval_js(
        """(() => {
        if (document.getElementById('hpc-user')) return 'already_open';
        const btn = Array.from(document.querySelectorAll('button')).find(
            b => (b.textContent || '').trim() === 'Add server'
        );
        if (!btn) return 'missing_add_server';
        btn.click();
        return 'clicked';
    })()""",
        timeout=15,
    )
    print(f"Step 5h Add server button: {add_res}", flush=True)
    wait_for_dom(
        "(() => document.getElementById('hpc-user') ? 'open' : '')()",
        "Step 5h Add server editor",
    )

    attrs_raw = ctrl.eval_js(
        """(() => {
        const ids = [
            'hpc-name', 'hpc-host', 'hpc-user', 'hpc-data-root', 'hpc-scratch',
            'hpc-results-root', 'hpc-code-roots', 'hpc-partition', 'hpc-time',
            'hpc-cpus', 'hpc-mem', 'hpc-interpreter', 'hpc-wheelhouse'
        ];
        const out = {};
        for (const id of ids) {
            const el = document.getElementById(id);
            if (!el) {
                out[id] = null;
                continue;
            }
            out[id] = {
                autocapitalize: el.getAttribute('autocapitalize'),
                autocorrect: el.getAttribute('autocorrect'),
                spellcheck: el.getAttribute('spellcheck'),
                autocomplete: el.getAttribute('autocomplete'),
            };
        }
        return JSON.stringify(out);
    })()""",
        timeout=15,
    )
    attrs = _as_dict(json.loads(_as_str(attrs_raw, "attrs")))
    for field_id, field_attrs_obj in attrs.items():
        assert field_attrs_obj is not None, f"Input #{field_id} not found in DOM"
        field_attrs = _as_dict(field_attrs_obj)
        assert field_attrs["autocapitalize"] == "off", (
            f"#{field_id} autocapitalize != off: {field_attrs}"
        )
        assert field_attrs["autocorrect"] == "off", f"#{field_id} autocorrect != off: {field_attrs}"
        assert field_attrs["spellcheck"] == "false", (
            f"#{field_id} spellcheck != false: {field_attrs}"
        )
        assert field_attrs["autocomplete"] == "off", (
            f"#{field_id} autocomplete != off: {field_attrs}"
        )
    print(
        "Step 5h verified: all HPC form inputs specify autocapitalize=off, autocorrect=off, "
        "spellcheck=false, autocomplete=off.",
        flush=True,
    )

    ctrl.eval_js(
        """(() => {
        const sel = document.getElementById('hpc-scheduler');
        if (sel) {
            sel.value = 'slurm';
            sel.dispatchEvent(new Event('change', { bubbles: true }));
        }
    })()""",
        timeout=15,
    )
    wait_for_dom(
        "(() => { const p = document.getElementById('hpc-partition'); "
        "return p && !p.disabled ? 'enabled' : ''; })()",
        "Step 5h Slurm partition field enable",
    )

    def focus_and_clear(field_id: str) -> None:
        res = ctrl.eval_js(
            f"""(() => {{
            const el = document.getElementById({json.dumps(field_id)});
            if (!el) return 'missing';
            el.focus();
            el.value = '';
            el.dispatchEvent(new Event('input', {{ bubbles: true }}));
            return 'focused';
        }})()""",
            timeout=15,
        )
        assert res == "focused", f"Input #{field_id} missing when focusing: {res}"
        wait_for_field_value(field_id, "", f"Clearing #{field_id}")

    ctx.adapter.step5h_os_input_and_edit_shortcuts(
        ctrl, focus_and_clear, wait_for_field_value, poll_until
    )

    ctrl.eval_js(
        """(() => {
        const btn = Array.from(document.querySelectorAll('button')).find(
            b => (b.textContent || '').trim() === 'Close'
        );
        if (btn) btn.click();
    })()""",
        timeout=15,
    )


def step_5i_crash_diagnostics_redaction(ctx: E2EContext) -> None:
    print("", flush=True)
    print(
        "--- Step 5i: Crash -> notice -> Share diagnostics -> saved zip has no secrets ---",
        flush=True,
    )
    diag_logs = ctx.tmp_dir / "diag-logs"
    diag_zip = ctx.tmp_dir / "diag.zip"
    for p in (diag_logs, diag_zip):
        if p.is_dir():
            shutil.rmtree(p)
        elif p.exists():
            p.unlink()
    diag_logs.mkdir(parents=True, exist_ok=True)
    (diag_logs / "desktop.log").write_text(
        "\n".join(
            [
                "-----BEGIN OPENSSH PRIVATE KEY-----",
                "e2e-secret-key-body",
                "-----END OPENSSH PRIVATE KEY-----",
                "http://127.0.0.1:5000/viewer?token=e2e-secret-token",
                '{"password": "e2e secret pass"}',
                "Authorization: Bearer e2e-secret-bearer",
                "/Users/e2e-user/project",
                "ssh e2e-alias.example.org",
                "",
            ]
        ),
        encoding="utf-8",
    )

    ctx.quit_current(wait_timeout_s=6.0)
    ctx.launch(
        "diag_crash",
        env_overrides={
            "COPERCEIVE_TEST_CRASH": "1",
            "COPERCEIVE_TEST_LOG_DIR": str(diag_logs),
        },
        wait_marker=False,
    )
    assert ctx.proc is not None
    deadline = time.monotonic() + 20.0
    while time.monotonic() < deadline:
        if ctx.proc.poll() is not None:
            break
        time.sleep(0.2)
    if ctx.proc.poll() is None:
        ctx.proc.terminate()
    crash_status = ctx.proc.wait(timeout=5.0)
    ctx.close_log_handles()
    ctx.proc = None
    if crash_status == 0:
        print("E2E_FAIL step=5i crash launch exited 0", file=sys.stderr)
        raise SystemExit(1)

    crash_files = sorted(diag_logs.glob("crash-*.json"))
    if not crash_files:
        print("E2E_FAIL step=5i no crash-*.json written", file=sys.stderr)
        if ctx.current_stderr_log and ctx.current_stderr_log.is_file():
            print(
                ctx.current_stderr_log.read_text(encoding="utf-8", errors="replace"),
                file=sys.stderr,
            )
        raise SystemExit(1)
    crash_file = crash_files[0]

    ctx.launch(
        "diag_relaunch",
        env_overrides={
            "COPERCEIVE_TEST_LOG_DIR": str(diag_logs),
            "COPERCEIVE_TEST_DIAGNOSTICS_SAVE_PATH": str(diag_zip),
        },
        failure_exit_code=1,
    )
    assert ctx.current_ctrl is not None
    ctrl = ctx.current_ctrl

    secrets = [
        "e2e-secret-key-body",
        "e2e-secret-token",
        "e2e secret pass",
        "e2e-secret-bearer",
        "e2e-user",
        "e2e-alias.example.org",
        "BEGIN OPENSSH",
    ]
    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline:
        if ctrl.eval_js(
            "(() => { const n = document.querySelector('.crash-notice'); "
            "return n ? n.textContent : ''; })()",
            window="main",
        ):
            break
        time.sleep(0.3)
    notice = str(
        ctrl.eval_js(
            "(() => (document.querySelector('.crash-notice') || {}).textContent || '')()",
            window="main",
        )
        or ""
    )
    assert "closed unexpectedly" in notice, notice
    ctrl.eval_js(
        """(() => {
        const button = Array.from(document.querySelectorAll('.crash-notice button'))
            .find(b => (b.textContent || '').includes('Share diagnostics'));
        if (!button) throw new Error('no Share diagnostics button');
        button.click();
    })()""",
        window="main",
    )

    deadline = time.monotonic() + 15.0
    preview = ""
    while time.monotonic() < deadline:
        preview = str(
            ctrl.eval_js(
                "(() => (document.querySelector('.diagnostics-preview') || {})"
                ".textContent || '')()",
                window="main",
            )
            or ""
        )
        if "Coperceive Diagnostics" in preview and "Collecting diagnostics" not in preview:
            break
        time.sleep(0.3)
    assert "Coperceive Diagnostics" in preview and "Collecting diagnostics" not in preview, preview[
        :500
    ]
    assert "COPERCEIVE_TEST_CRASH" in preview or "crash-" in preview, preview[:500]
    for planted in secrets:
        assert planted not in preview, f"{planted} leaked into preview:\n{preview[:2000]}"

    if diag_zip.exists():
        diag_zip.unlink()
    ctrl.eval_js(
        """(() => {
        const button = Array.from(document.querySelectorAll('.diagnostics-actions button'))
            .find(b => (b.textContent || '').includes('Save as file'));
        if (!button) throw new Error('no Save as file button');
        button.click();
    })()""",
        window="main",
    )
    deadline = time.monotonic() + 20.0
    while time.monotonic() < deadline and not diag_zip.exists():
        time.sleep(0.2)
    assert diag_zip.is_file(), f"zip not written at {diag_zip}"

    entries = 0
    with zipfile.ZipFile(diag_zip) as archive:
        names = archive.namelist()
        assert "report.txt" in names, names
        for name in names:
            entries += 1
            text = archive.read(name).decode("utf-8", errors="replace")
            for planted in secrets:
                assert planted not in text, f"{planted} leaked into zip entry {name}"
    print(f"Step 5i preview and {entries} zip entries contain no planted secrets", flush=True)

    try:
        ctrl.eval_js(
            "(() => { const b = Array.from(document.querySelectorAll('button'))"
            ".find(x => (x.textContent || '').trim() === 'Close'); if (b) b.click(); })()",
            window="main",
        )
    except Exception:
        pass

    ctx.quit_current(wait_timeout_s=6.0)
    shutil.rmtree(diag_logs, ignore_errors=True)
    if diag_zip.exists():
        diag_zip.unlink()

    ctx.launch("diag_clean", failure_exit_code=1)
    print(
        f"Step 5i verified: crash {crash_file.name} -> notice -> Share diagnostics -> "
        "saved zip scanned with no planted secrets.",
        flush=True,
    )


def step_5j_export_repro_card(ctx: E2EContext) -> None:
    print("--- Step 5j: Export a repro card from run history ---", flush=True)
    ctrl = ctx.current_ctrl
    assert ctrl is not None
    repro_card = ctx.tmp_dir / "repro-card.json"
    if repro_card.exists():
        repro_card.unlink()
    ctx.mock_dialog_file.write_text(f"{repro_card}\n", encoding="utf-8")

    opened = ctrl.eval_js(
        """(() => {
        const back = Array.from(document.querySelectorAll('a, button')).find(
            (item) => (item.textContent || '').trim() === 'Return to Library'
        );
        if (back) back.click();
        const tab = Array.from(document.querySelectorAll('nav button, [role="tab"]')).find(
            (button) => (button.textContent || '').trim() === 'HPC Workspace'
        );
        if (tab) tab.click();
        return 'clicked';
    })()""",
        timeout=20,
        window="main",
    )
    print(f"Step 5j history tab: {opened}", flush=True)

    def wait_click(script: str, label: str, timeout: float = 10.0) -> None:
        deadline = time.monotonic() + timeout
        last: object = None
        while time.monotonic() < deadline:
            last = ctrl.eval_js(script, timeout=20, window="main")
            if last == "clicked":
                return
            time.sleep(0.1)
        diag = ctrl.eval_js(
            """(() => ({
            hash: location.hash,
            tabs: Array.from(document.querySelectorAll('[role="tab"], nav button')).map(
                (item) => (item.textContent || '').trim()
            ).filter(Boolean).slice(0, 20),
            buttons: Array.from(document.querySelectorAll('button')).map(
                (item) => (item.textContent || '').trim()
            ).filter(Boolean).slice(0, 30),
        }))()""",
            timeout=20,
            window="main",
        )
        raise SystemExit(f"{label} did not appear: {last}; page={json.dumps(diag)}")

    wait_click(
        """(() => {
        const history = Array.from(document.querySelectorAll('[role="tab"], button')).find(
            (button) => (button.textContent || '').trim() === 'Run history'
        );
        if (!history) return 'missing_history_tab';
        history.click();
        return 'clicked';
    })()""",
        "Run history tab",
    )
    print("Step 5j history tab: clicked", flush=True)

    wait_click(
        """(() => {
        const button = Array.from(document.querySelectorAll('button')).find(
            (item) => (item.textContent || '').trim() === 'Export repro card'
        );
        if (!button) return 'missing_export_button';
        button.click();
        return 'clicked';
    })()""",
        "Export repro card button",
    )
    print("Step 5j export button: clicked", flush=True)

    preview = ""
    for _ in range(40):
        time.sleep(0.25)
        preview = str(ctrl.eval_js("document.body.innerText", timeout=20, window="main") or "")
        if "Not recorded" in preview or "was not" in preview or "has not scored" in preview:
            break
    if (
        "Not recorded" not in preview
        and "was not" not in preview
        and "has not scored" not in preview
    ):
        raise SystemExit("repro card preview did not say what is missing:\n" + preview[-1500:])

    saved = ctrl.eval_js(
        """(() => {
        const button = Array.from(document.querySelectorAll('button')).find(
            (item) => (item.textContent || '').trim() === 'Save repro card'
        );
        if (!button) return 'missing_save_button';
        button.click();
        return 'clicked';
    })()""",
        timeout=20,
        window="main",
    )
    print(f"Step 5j save button: {saved}", flush=True)

    expected_reveal = ctx.adapter.reveal_button_label()
    reveal_seen = ""
    for _ in range(40):
        time.sleep(0.25)
        reveal_seen = str(ctrl.eval_js("document.body.innerText", timeout=20, window="main") or "")
        if expected_reveal in reveal_seen:
            break
    if expected_reveal not in reveal_seen:
        raise SystemExit(
            f"repro card save did not offer {expected_reveal}:\n" + reveal_seen[-1500:]
        )
    print(f"Step 5j preview listed the missing fields and offered {expected_reveal}.", flush=True)

    if not repro_card.is_file() or repro_card.stat().st_size == 0:
        raise SystemExit(f"repro card was not written to {repro_card}")
    text = repro_card.read_text(encoding="utf-8")
    card = _as_dict(json.loads(text))
    if card.get("schema") != "coperceive-repro-card":
        raise SystemExit(f"saved file is not a repro card: {card.get('schema')!r}")
    for forbidden in ("ghp_", "/home/", "example.edu"):
        if forbidden in text:
            raise SystemExit(f"repro card contains {forbidden}")
    print(f"Step 5j card bytes: {repro_card.stat().st_size}", flush=True)
    print(
        "Step 5j verified: Export repro card showed missing fields and saved the card.", flush=True
    )


def step_6_quit_and_verify_no_orphans(ctx: E2EContext) -> None:
    print("", flush=True)
    print("--- Step 6: Quit Application and Verify Clean Process Exit ---", flush=True)
    ctx.quit_current(wait_timeout_s=6.0)

    time.sleep(1.0)
    orphan_pids = _list_stale_pids(match_serve=True, match_app=False)
    if orphan_pids:
        print(
            "Error: Orphan 'coperceive serve' processes remain after quitting app!", file=sys.stderr
        )
        print(orphan_pids, file=sys.stderr)
        _kill_stale_processes(match_serve=True, match_app=False)
        raise SystemExit(15)
    print("Verified: Zero orphan sidecar or serve processes remain.", flush=True)
    print("", flush=True)
    print("==========================================================", flush=True)
    print(ctx.adapter.passed_banner, flush=True)
    print("==========================================================", flush=True)


def cleanup_context(ctx: E2EContext, exit_status: int) -> None:
    print(f"Cleaning up test harness (exit status: {exit_status})...", flush=True)
    ctx.adapter.restore_appearance()
    if exit_status != 0:
        for label, path in (
            ("App STDOUT on failure", ctx.tmp_dir / "app_stdout.log"),
            ("App STDERR on failure", ctx.tmp_dir / "app_stderr.log"),
            ("Relaunch STDOUT", ctx.tmp_dir / "relaunch_stdout.log"),
            ("Relaunch STDERR", ctx.tmp_dir / "relaunch_stderr.log"),
        ):
            if path.is_file():
                print(f"=== {label} ===", flush=True)
                print(path.read_text(encoding="utf-8", errors="replace"), flush=True)
        for log in sorted(ctx.tmp_dir.glob("geom_relaunch_*_stderr.log")):
            print(f"=== {log.name} ===", flush=True)
            print(log.read_text(encoding="utf-8", errors="replace"), flush=True)
        for log_dir in ctx.adapter.extra_failure_log_dirs():
            if log_dir.is_dir():
                print(f"=== {log_dir} logs ===", flush=True)
                for log in sorted(log_dir.glob("*.log")):
                    print(log.read_text(encoding="utf-8", errors="replace"), flush=True)

    if ctx.proc is not None and ctx.proc.poll() is None:
        print(f"Killing app process {ctx.proc.pid}...", flush=True)
        ctx.proc.kill()
        try:
            ctx.proc.wait(timeout=2.0)
        except Exception:
            pass
    ctx.close_log_handles()

    if ctx.update_server is not None:
        ctx.update_server.stop()

    if ctx.fake_job_pid_file.is_file():
        pid_txt = ctx.fake_job_pid_file.read_text(encoding="utf-8", errors="replace").strip()
        if pid_txt.isdigit():
            try:
                os.kill(int(pid_txt), signal.SIGKILL)
            except OSError:
                pass

    if (
        ctx.state_backup_5f is not None
        and ctx.state_backup_5f.is_file()
        and ctx.state_file.exists()
    ):
        try:
            shutil.copy2(ctx.state_backup_5f, ctx.state_file)
        except OSError:
            pass

    _kill_stale_processes(match_serve=True, match_app=False)
    shutil.rmtree(ctx.tmp_dir, ignore_errors=True)


def run_e2e(
    *,
    os_name: str,
    app_exe: Path,
    repo_root: Path,
    screenshot_dir: Path,
) -> int:
    if not app_exe.is_file() or not os.access(app_exe, os.X_OK):
        print(f"Error: App executable not found or not executable: {app_exe}", file=sys.stderr)
        return 1

    fixture_1 = repo_root / "tests" / "fixtures" / "synthetic_scene"
    fixture_2 = fixture_1 / "synth_001"
    if not fixture_1.is_dir():
        print(f"Error: Fixture 1 not found at {fixture_1}!", file=sys.stderr)
        return 2

    screenshot_dir.mkdir(parents=True, exist_ok=True)
    tmp_dir = Path(tempfile.mkdtemp(prefix="coperceive-e2e-"))
    adapter = build_platform_adapter(os_name)
    state_dir, library_dir, platform_env = adapter.prepare_environment(tmp_dir)

    import_source = tmp_dir / "import-src" / "synth_001"
    import_source.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(fixture_2, import_source)
    stray_frame = import_source / "vehicle" / "cam_front" / "0999999.png"
    stray_frame.parent.mkdir(parents=True, exist_ok=True)
    stray_frame.write_text(
        "a frame from another scene that images.parquet does not list\n", encoding="utf-8"
    )

    repro_dir = tmp_dir / "repro"
    create_repro_fixture(repro_dir)

    marker_file = tmp_dir / "test_marker.txt"
    mock_dialog_file = tmp_dir / "mock_dialog.txt"
    fake_job_pid_file = tmp_dir / "fake-dataset-job.pid"
    state_file = state_dir / "state.json"

    base_env = {
        **platform_env,
        "COPERCEIVE_TEST_ENV": "1",
        "COPERCEIVE_TEST_MARKER_FILE": str(marker_file),
        "COPERCEIVE_MOCK_DIALOG_FILE": str(mock_dialog_file),
        "COPERCEIVE_TEST_IMPORT_SOURCE": str(import_source),
        "COPERCEIVE_PACK_SLEEP_S": "0.5",
        "COPERCEIVE_TEST_FAKE_DATASET_JOB": "1",
        "COPERCEIVE_TEST_FAKE_DATASET_PID_FILE": str(fake_job_pid_file),
        "COPERCEIVE_TEST_REPRO_DIR": str(repro_dir),
    }

    ctx = E2EContext(
        adapter=adapter,
        app_exe=app_exe,
        repo_root=repo_root,
        fixture_1=fixture_1,
        fixture_2=fixture_2,
        tmp_dir=tmp_dir,
        screenshot_dir=screenshot_dir,
        state_dir=state_dir,
        state_file=state_file,
        library_dir=library_dir,
        marker_file=marker_file,
        mock_dialog_file=mock_dialog_file,
        import_source=import_source,
        repro_dir=repro_dir,
        fake_job_pid_file=fake_job_pid_file,
        base_env=base_env,
    )

    exit_status = 0
    try:
        _kill_stale_processes(
            match_serve=True,
            match_app=True,
            include_macos_app=(os_name == "macos"),
        )

        adapter.prepare_appearance_light()
        if state_file.exists():
            state_file.unlink()
        state_dir.mkdir(parents=True, exist_ok=True)
        state_file.write_text(
            '{"last_theme": "dark", "reopen_last_session": true}\n', encoding="utf-8"
        )

        step_1_initial_viewer(ctx)
        step_1b_1c_1d_exports(ctx)
        step_2_relaunch_and_playback(ctx)
        step_2b_loop_persistence(ctx)
        step_2c_window_geometry(ctx)
        step_2d_single_viewer_window(ctx)
        step_3_switch_single_scene(ctx)
        step_3b_switch_multi_scene(ctx)
        step_4_invalid_folder_error(ctx)
        step_5_close_viewer(ctx)
        step_5b_connect_nav_and_fonts(ctx)
        step_5c_import_cancel_resume_open(ctx)
        step_5d_update_banner(ctx)
        step_5e_download_pause_resume_cancel(ctx)
        step_5f_move_library(ctx)
        step_5g_library_validation_cache(ctx)
        step_5h_hpc_input_autocap(ctx)
        step_5i_crash_diagnostics_redaction(ctx)
        step_5j_export_repro_card(ctx)
        step_6_quit_and_verify_no_orphans(ctx)
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1
        exit_status = code
        return exit_status
    except Exception:
        exit_status = 1
        raise
    finally:
        cleanup_context(ctx, exit_status)

    return 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Real-App Desktop E2E Harness")
    parser.add_argument("--os", dest="os_name", choices=("macos", "linux"), required=True)
    parser.add_argument("--app-exe", type=Path, required=True)
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
    )
    parser.add_argument(
        "--screenshot-dir",
        type=Path,
        default=Path("/tmp/e2e-shots"),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    ns = parse_args(sys.argv[1:] if argv is None else argv)
    return run_e2e(
        os_name=ns.os_name,
        app_exe=ns.app_exe.resolve(),
        repo_root=ns.repo_root.resolve(),
        screenshot_dir=ns.screenshot_dir,
    )


if __name__ == "__main__":
    raise SystemExit(main())
