"""Open, close and reopen a cluster dataset in one running app, and time each step.

Step CD-20 of ``e2e_cluster_datasets_app.sh`` (feature C, phase 0 in
``docs/design/viewer-job-lifetime.md``). It drives an app that the shell script
started in test mode against the fake cluster, through the loopback test
controller, and asserts what reopening does today:

- each Open starts its own viewer server (a new token), and each one opens its
  own ssh stream: two ``dataset-stream`` lines after the reopen;
- closing the viewer window stops that server;
- no Slurm job, viewer job or tunnel is involved.

The timings it prints measure the mechanism on this machine with a local fake
ssh and a warm cache. They are not the cluster's reopen time.

It never prints the viewer token, the controller secret or an ``/eval`` body:
the token is read from the ``dataset_open`` reply and only compared.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import time
import urllib.error
import urllib.request
from typing import Any, cast

POLL_S = 0.05
FRAME_POLL_SCRIPT = (
    "(() => { const stats = window.__COPERCEIVE_FRAME_STATS__ || []; "
    "const applied = stats.filter((s) => s.result === 'applied'); "
    "const first = applied.length ? applied[0] : null; "
    "const body = document.body ? document.body.innerText : ''; "
    "return { applied: applied.length, at: first ? first.at : null, "
    "time_origin: performance.timeOrigin, "
    "streaming: body.includes('Streaming over SSH'), "
    "webgl_missing: Boolean(document.querySelector('.webgl-missing')) }; })()"
)
FORBIDDEN_LOG_PREFIXES = ("remote-", "sbatch", "tunnel")


class FlowError(AssertionError):
    pass


def as_obj(value: object) -> dict[str, Any] | None:
    """``value`` as a JSON object, or None."""
    return cast(dict[str, Any], value) if isinstance(value, dict) else None


class Controller:
    """The app's loopback test controller (``src-tauri/src/controller.rs``)."""

    def __init__(self, port: int, secret: str) -> None:
        self.base = f"http://127.0.0.1:{port}"
        self._secret = secret

    def post(self, path: str, payload: dict[str, Any], timeout: float) -> tuple[int, Any]:
        request = urllib.request.Request(
            f"{self.base}{path}",
            data=json.dumps(payload).encode(),
            method="POST",
            headers={"Authorization": f"Bearer {self._secret}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.status, json.loads(response.read() or b"null")
        except urllib.error.HTTPError as error:
            return error.code, None

    def eval(self, window: str, script: str, timeout_s: int = 10) -> tuple[int, Any]:
        """Run ``script`` in ``window``. Returns (HTTP status, the script's result)."""
        status, body = self.post(
            "/eval",
            {"window": window, "script": script, "timeout": timeout_s},
            timeout=timeout_s + 15,
        )
        reply = as_obj(body)
        if status != 200 or reply is None:
            return status, None
        if reply.get("error"):
            raise FlowError(f"{window} script failed: {str(reply['error'])[:200]}")
        return status, reply.get("result")


def read_marker(path: str, deadline_s: float) -> tuple[int, str]:
    end = time.monotonic() + deadline_s
    while time.monotonic() < end:
        if os.path.exists(path):
            lines = open(path, encoding="utf-8").read().splitlines()
            if len(lines) >= 5 and lines[3].strip() not in ("", "0") and lines[4].strip():
                return int(lines[3]), lines[4].strip()
        time.sleep(0.2)
    raise FlowError("the app wrote no controller port and secret to its marker file")


def wait_for_main(ctrl: Controller, deadline_s: float) -> None:
    """Wait until the main window's page runs scripts (the controller is up before it loads)."""
    end = time.monotonic() + deadline_s
    while time.monotonic() < end:
        status, value = ctrl.eval("main", "(() => 1)()", timeout_s=5)
        if status == 200 and value == 1:
            return
        time.sleep(0.2)
    raise FlowError("the main window never answered the test controller")


def stream_lines(ssh_log: str) -> list[str]:
    if not os.path.exists(ssh_log):
        return []
    return open(ssh_log, encoding="utf-8").read().splitlines()


def open_dataset(ctrl: Controller, args: dict[str, Any], scenes: list[str]) -> dict[str, Any]:
    """dataset_open from the main window, as DatasetView.tsx does, then time the first frame."""
    meta = {
        "dataset": {
            "scenes": [{"scene_id": scene, "state": "ready"} for scene in scenes],
            "label": "OPV2V test",
            "server": "Dataset e2e fake cluster",
        }
    }
    script = (
        "(async () => { "
        f"const r = await window.__TAURI_INTERNALS__.invoke('dataset_open', {json.dumps(args)}); "
        "await window.__TAURI_INTERNALS__.invoke('set_viewer_dataset_scenes', "
        f"{json.dumps(meta)}); "
        "return r; })()"
    )
    wall0 = time.time()
    t0 = time.monotonic()
    status, value = ctrl.eval("main", script, timeout_s=60)
    returned = time.monotonic()
    session = as_obj(value)
    if status != 200 or session is None:
        raise FlowError(f"dataset_open did not answer (HTTP {status})")
    for key in ("origin", "token", "target_type"):
        if not session.get(key):
            raise FlowError(f"dataset_open returned no {key}; keys {sorted(session)}")

    last: dict[str, Any] | None = None
    deadline = t0 + 60
    while time.monotonic() < deadline:
        status, value = ctrl.eval("viewer", FRAME_POLL_SCRIPT)
        last = as_obj(value)
        if status == 200 and last is not None and last.get("applied"):
            seen = time.monotonic()
            page_ms = None
            if last.get("at") is not None and last.get("time_origin"):
                page_ms = (float(last["time_origin"]) + float(last["at"])) / 1000.0 - wall0
                page_ms = round(page_ms * 1000)
            return {
                "origin": session["origin"],
                "token": session["token"],
                "target_type": session["target_type"],
                "returned_ms": round((returned - t0) * 1000),
                "first_frame_ms": round((seen - t0) * 1000),
                "first_frame_page_ms": page_ms,
                "streaming": bool(last.get("streaming")),
                "webgl_missing": bool(last.get("webgl_missing")),
            }
        time.sleep(POLL_S)
    raise FlowError(f"no frame was applied in the viewer within 60 s; last poll {last}")


def close_viewer(ctrl: Controller, origin: str) -> dict[str, int]:
    """Close the viewer window, then wait until it is gone and its server refuses connections."""
    t0 = time.monotonic()
    status, _ = ctrl.post("/close-window", {}, timeout=10)
    if status != 200:
        raise FlowError(f"/close-window answered HTTP {status}")
    deadline = t0 + 20
    gone = None
    while time.monotonic() < deadline:
        status, _ = ctrl.eval("viewer", "(() => 1)()", timeout_s=2)
        if status == 502:  # the dispatcher found no "viewer" window
            gone = time.monotonic()
            break
        time.sleep(POLL_S)
    if gone is None:
        raise FlowError("the viewer window was still open 20 s after /close-window")
    refused = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{origin}/api/health", timeout=2):
                pass
        except urllib.error.HTTPError:
            pass  # still answering
        except urllib.error.URLError as error:
            if not isinstance(error.reason, TimeoutError):  # a slow answer is not a stopped server
                refused = time.monotonic()
                break
        except TimeoutError:
            pass
        except OSError:  # connection refused or reset
            refused = time.monotonic()
            break
        time.sleep(POLL_S)
    if refused is None:
        raise FlowError("the closed viewer's server still answered 20 s after /close-window")
    return {
        "window_gone_ms": round((gone - t0) * 1000),
        "server_stopped_ms": round((refused - t0) * 1000),
    }


def machine() -> str:
    parts = [platform.platform(), platform.machine(), f"{os.cpu_count()} logical CPUs"]
    if sys.platform == "darwin":
        for key, fmt in (("machdep.cpu.brand_string", "{}"), ("hw.memsize", "{} bytes RAM")):
            try:
                value = subprocess.run(
                    ["sysctl", "-n", key], capture_output=True, text=True, timeout=5, check=True
                ).stdout.strip()
            except (OSError, subprocess.SubprocessError):
                continue
            if value:
                parts.append(fmt.format(value))
    return ", ".join(parts)


def run(
    marker: str, dataset_args: dict[str, Any], ssh_log: str, scenes: list[str]
) -> dict[str, Any]:
    port, secret = read_marker(marker, deadline_s=45)
    ctrl = Controller(port, secret)
    wait_for_main(ctrl, deadline_s=45)
    args = {**dataset_args, "sceneId": scenes[0]}

    first = open_dataset(ctrl, args, scenes)
    after_first = stream_lines(ssh_log).count("dataset-stream")
    if after_first != 1:
        raise FlowError(f"the first Open made {after_first} ssh streams, expected 1")
    if first["target_type"] != "stream":
        raise FlowError(f"the first Open is {first['target_type']!r}, not a stream")

    closed = close_viewer(ctrl, first["origin"])

    second = open_dataset(ctrl, args, scenes)
    log = stream_lines(ssh_log)
    streams = log.count("dataset-stream")
    if streams != 2:
        raise FlowError(
            f"after the reopen there are {streams} ssh streams, expected 2 (one per Open)"
        )
    if second["token"] == first["token"]:
        raise FlowError("the reopen reused the closed viewer server's token")
    jobs = [line for line in log if line.startswith(FORBIDDEN_LOG_PREFIXES)]
    if jobs:
        raise FlowError(f"opening or reopening ran a job or tunnel: {jobs}")

    return {
        "open": {k: first[k] for k in ("returned_ms", "first_frame_ms", "first_frame_page_ms")},
        "close": closed,
        "reopen": {k: second[k] for k in ("returned_ms", "first_frame_ms", "first_frame_page_ms")},
        "ssh_streams": streams,
        "new_token": True,
        "origin_changed": second["origin"] != first["origin"],
        "webgl_missing": first["webgl_missing"] or second["webgl_missing"],
        "poll_ms": round(POLL_S * 1000),
        "machine": machine(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--marker", required=True, help="the app's COPERCEIVE_TEST_MARKER_FILE")
    parser.add_argument(
        "--dataset-args", required=True, help="dataset_open arguments as JSON object members"
    )
    parser.add_argument("--ssh-log", required=True, help="this app's FAKE_SSH_LOG")
    parser.add_argument(
        "--scene", action="append", required=True, help="a ready scene id; the first is opened"
    )
    opts = parser.parse_args()
    try:
        result = run(
            opts.marker, json.loads("{" + opts.dataset_args + "}"), opts.ssh_log, opts.scene
        )
    except FlowError as error:
        print(f"E2E_DATASET_APP_FAIL: CD-20 {error}", file=sys.stderr)
        return 1
    print("E2E_REOPEN_MEASURE " + json.dumps(result, sort_keys=True))
    o, c, r = result["open"], result["close"], result["reopen"]
    print(
        "Step CD-20 verified: open -> close -> reopen in one app. "
        f"Open: dataset_open {o['returned_ms']} ms, first frame {o['first_frame_ms']} ms. "
        f"Close: window gone {c['window_gone_ms']} ms, server stopped {c['server_stopped_ms']} ms. "
        f"Reopen: dataset_open {r['returned_ms']} ms, first frame {r['first_frame_ms']} ms. "
        f"A new viewer server each time (new token; origin changed: {result['origin_changed']}), "
        f"{result['ssh_streams']} ssh streams, no Slurm job. "
        f"Local fake ssh, warm cache, {result['poll_ms']} ms polling"
        + ("; no WebGL, so frame = frame document" if result["webgl_missing"] else "")
        + "."
    )
    print(f"Step CD-20 machine: {result['machine']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
