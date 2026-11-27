# SPDX-License-Identifier: Apache-2.0
"""Play a real-size scene in the real viewer and measure it (ADR 0020, phase 0).

    python scripts/bench/viewer_bench.py [--fixture ROOT] [--quick]
                                         [--agents 4] [--points 160000]
                                         [--cameras 16] [--samples 100]
                                         [--warmup-s 2] [--measure-s 7]

Starts ``coperceive serve`` (a bearer token, a free loopback port, ``--root`` on
the fixture and ``--static`` on the built web bundle) and drives the viewer page
in headless Chromium with Playwright, as ``tests/test_viewer_browser.py`` does.
Without ``--fixture`` a scene is generated with ``scale_fixture.py`` into a
temporary folder outside the repository and removed afterwards.

The page opens the scene, waits for the first drawn sample, presses Play at
1x, plays through a warm-up and then measures a fixed window:

- frame time p50/p95/max, from a small ``requestAnimationFrame`` loop injected
  into the page (the app has no per-rAF metric yet);
- samples drawn per second (applied ``__COPERCEIVE_FRAME_STATS__`` entries)
  against the scene's own rate, and the playhead's pace;
- per-phase p50/p95 from ``__COPERCEIVE_FRAME_STATS__``: load (frame
  document), points (waiting for the sweeps), draw (the points wait, colour
  fill and CPU buffer copy) and render (the GPU upload and both composer
  passes). Points, draw and render count applied samples only; dropped ones
  are counted, not timed;
- I/O and decode per request kind, from a bench-only wrapper around the page's
  ``fetch`` and ``Response.json``/``arrayBuffer``/``blob``: the wait for the
  response, the body read and the JSON parse of frame documents, in the
  measured window (``io_ms``) and over the whole run (``io_all_ms``). Points
  need no decode (raw float32 views). Camera images are decoded in a Web
  Worker, out of the wrapper's reach, so ``image_decode`` is null;
- buffering after the warm-up: the timeline's "Buffering..." label and the
  stream buffer's stalled flag;
- JS heap (``performance.memory``), the server's peak resident memory (Linux;
  the resident size at the end elsewhere), an estimate of GPU bytes from the
  visible point count x 24 (float32 position and colour), and the bytes the
  page received over the network;
- the machine: CPU model, logical CPUs, RAM, OS, and the WebGL renderer.

When the page could not start WebGL (the viewer shows its ``.webgl-missing``
notice) the bench prints "rendering skipped: no WebGL" and reports the fetch
side only: no frame-time, draw, render or GPU numbers.

JSON on stdout (last line), a table on stderr. ``--quick`` is the CI shape:
4 x 160 000 points x 16 cameras x 30 samples, a short window. Exit code 3 means
the environment cannot run it (no Playwright, no Chromium, no web bundle).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, cast

from bench_machine import machine_info, machine_line

REPO = Path(__file__).resolve().parents[2]
FIXTURE = Path(__file__).with_name("scale_fixture.py")
SCHEMA = "coperceive-viewer-bench-1"
UNAVAILABLE = 3
BYTES_PER_GPU_POINT = 24  # float32 xyz position + float32 rgb colour
SWIFTSHADER_ARGS = [
    "--use-gl=angle",
    "--use-angle=swiftshader",
    "--enable-unsafe-swiftshader",
    "--ignore-gpu-blocklist",
]
FULL = {
    "agents": 4,
    "points": 160_000,
    "cameras": 16,
    "samples": 100,
    "warmup": 2.0,
    "measure": 7.0,
}
QUICK = {
    "agents": 4,
    "points": 160_000,
    "cameras": 16,
    "samples": 30,
    "warmup": 1.0,
    "measure": 1.5,
}

# Injected after the first draw: a rAF timestamp log and a 50 ms poller for the
# buffering label, the stream buffer, the playhead, the heap and the point count.
MONITOR_JS = """
() => {
  const bench = { raf: [], polls: [], running: true };
  window.__COPERCEIVE_BENCH__ = bench;
  const tick = (ts) => {
    bench.raf.push(ts);
    if (bench.running) requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
  bench.timer = setInterval(() => {
    const playback = window.__COPERCEIVE_PLAYBACK_STATE__;
    const state = playback ? playback() : null;
    const buffer = window.__COPERCEIVE_BUFFER_STATS__ || null;
    const sceneCounts = window.__COPERCEIVE_SCENE_COUNTS__;
    const counts = sceneCounts ? sceneCounts() : null;
    const memory = performance.memory || null;
    bench.polls.push({
      at: performance.now(),
      buffering: document.querySelector('.clock .buffering') !== null,
      stalled: buffer ? Boolean(buffer.stalled) : null,
      buffered_s: buffer ? buffer.buffered_seconds : null,
      playing: state ? state.playing : null,
      index: state ? state.frameIndex : null,
      points: counts ? counts.points : null,
      heap_used: memory ? memory.usedJSHeapSize : null,
      heap_total: memory ? memory.totalJSHeapSize : null,
    });
  }, 50);
  return performance.now();
}
"""

VISIBLE_POINTS_JS = """
() => {
  const counts = window.__COPERCEIVE_SCENE_COUNTS__;
  const now = counts ? counts() : null;
  return now ? now.points : 0;
}
"""

COLLECT_JS = """
() => {
  const bench = window.__COPERCEIVE_BENCH__;
  bench.running = false;
  clearInterval(bench.timer);
  const perf = window.__COPERCEIVE_PLAYBACK_PERF__ ? window.__COPERCEIVE_PLAYBACK_PERF__() : null;
  return {
    now: performance.now(),
    raf: bench.raf,
    polls: bench.polls,
    frame_stats: window.__COPERCEIVE_FRAME_STATS__ || [],
    io: window.__COPERCEIVE_BENCH_IO__ || [],
    playback_perf: perf,
    cache: window.__COPERCEIVE_CACHE_STATS__ || null,
    buffer: window.__COPERCEIVE_BUFFER_STATS__ || null,
  };
}
"""

RENDERER_JS = """
() => {
  const canvas = document.createElement('canvas');
  const gl = canvas.getContext('webgl2') || canvas.getContext('webgl');
  if (!gl) return { webgl: false, renderer: null, vendor: null, version: null };
  const info = gl.getExtension('WEBGL_debug_renderer_info');
  const out = {
    webgl: true,
    version: gl.getParameter(gl.VERSION),
    renderer: info ? gl.getParameter(info.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER),
    vendor: info ? gl.getParameter(info.UNMASKED_VENDOR_WEBGL) : gl.getParameter(gl.VENDOR),
  };
  const lose = gl.getExtension('WEBGL_lose_context');
  if (lose) lose.loseContext();
  return out;
}
"""


# Installed before the app's scripts: times the page's own requests without
# changing what they return. Bench-only; the app has no such hook.
IO_HOOK_JS = """
(() => {
  const io = [];
  window.__COPERCEIVE_BENCH_IO__ = io;
  // The scene routes only: /api/scenes/<id>/<kind>, not prediction frames.
  const route = new RegExp('/api/scenes/[^/]+/(points|frame|image)$');
  const kindOf = (url) => {
    const match = route.exec(String(url || '').split('?')[0]);
    return match ? match[1] : 'other';
  };
  const push = (row) => { if (io.length < 100000) io.push(row); };
  const fetch0 = window.fetch.bind(window);
  window.fetch = async (input, init) => {
    const t0 = performance.now();
    const response = await fetch0(input, init);
    const t1 = performance.now();
    push({ kind: kindOf(response.url), op: 'wait', at: t1, ms: t1 - t0 });
    return response;
  };
  const proto = Response.prototype;
  const text0 = proto.text, buffer0 = proto.arrayBuffer, blob0 = proto.blob;
  proto.json = async function () {
    const t0 = performance.now();
    const text = await text0.call(this);
    const t1 = performance.now();
    const value = JSON.parse(text);
    const t2 = performance.now();
    const kind = kindOf(this.url);
    push({ kind, op: 'read', at: t1, ms: t1 - t0 });
    push({ kind, op: 'parse', at: t2, ms: t2 - t1 });
    return value;
  };
  proto.arrayBuffer = async function () {
    const t0 = performance.now();
    const buffer = await buffer0.call(this);
    const t1 = performance.now();
    push({ kind: kindOf(this.url), op: 'read', at: t1, ms: t1 - t0, bytes: buffer.byteLength });
    return buffer;
  };
  proto.blob = async function () {
    const t0 = performance.now();
    const blob = await blob0.call(this);
    const t1 = performance.now();
    push({ kind: kindOf(this.url), op: 'read', at: t1, ms: t1 - t0, bytes: blob.size });
    return blob;
  };
  // The viewer decodes camera images in a worker today; this sees main-thread decodes only.
  if (typeof window.createImageBitmap === 'function') {
    const decode0 = window.createImageBitmap.bind(window);
    window.createImageBitmap = async (...args) => {
      const t0 = performance.now();
      const bitmap = await decode0(...args);
      const t1 = performance.now();
      push({ kind: 'image', op: 'decode', at: t1, ms: t1 - t0 });
      return bitmap;
    };
  }
})();
"""


class Unavailable(RuntimeError):
    """This machine cannot run the bench (no Playwright, Chromium or web bundle)."""


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(len(ordered) - 1, lower + 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _spread(values: list[float]) -> dict[str, float | int]:
    return {
        "count": len(values),
        "p50": round(percentile(values, 0.5), 1),
        "p95": round(percentile(values, 0.95), 1),
        "max": round(max(values), 1) if values else 0.0,
    }


def bundle_dir() -> Path:
    """``COPERCEIVE_WEB_DIST`` when set, else the packaged build (the browser tests' rule)."""
    wanted = os.environ.get("COPERCEIVE_WEB_DIST")
    chosen = Path(wanted).expanduser() if wanted else REPO / "coperceive" / "app" / "static"
    if not (chosen / "index.html").is_file():
        raise Unavailable(
            f"no web bundle at {chosen}: run `bash scripts/build_web_static.sh` "
            "or set COPERCEIVE_WEB_DIST to a vite build directory"
        )
    return chosen


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _get(url: str, token: str, timeout: float = 600.0) -> Any:
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())


def make_fixture(root: Path, args: argparse.Namespace) -> dict[str, object]:
    argv = [sys.executable, str(FIXTURE), str(root)]
    argv += ["--agents", str(args.agents), "--points", str(args.points)]
    argv += ["--cameras", str(args.cameras), "--samples", str(args.samples)]
    argv += ["--rate-hz", str(args.rate_hz), "--seed", str(args.seed)]
    if args.image_size:
        argv += ["--image-size", args.image_size]
    completed = subprocess.run(argv, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise RuntimeError(f"scale_fixture.py failed:\n{completed.stderr[-4000:]}")
    sys.stderr.write(completed.stderr)
    return cast(dict[str, object], json.loads(completed.stdout.strip().splitlines()[-1]))


class Server:
    """``coperceive serve`` on a free loopback port with a fresh bearer token."""

    def __init__(self, root: Path, static: Path, log: Path) -> None:
        self.token = secrets.token_urlsafe(24)
        self.port = _free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self.log = log
        env = {**os.environ, "COPERCEIVE_TOKEN": self.token}
        argv = [sys.executable, "-m", "coperceive", "serve", "--host", "127.0.0.1"]
        argv += ["--port", str(self.port), "--root", str(root), "--static", str(static)]
        self._log_handle = log.open("wb")
        self.process = subprocess.Popen(
            argv, env=env, stdout=self._log_handle, stderr=subprocess.STDOUT, cwd=REPO
        )
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError(f"coperceive serve exited:\n{self.tail()}")
            try:
                _get(f"{self.base}/api/health", self.token, timeout=2)
                return
            except (urllib.error.URLError, ConnectionError, TimeoutError):
                time.sleep(0.1)
        self.close()
        raise RuntimeError(f"coperceive serve did not answer in 60 s:\n{self.tail()}")

    def memory(self) -> dict[str, int | None]:
        """The server's resident memory: the peak on Linux, the current size elsewhere."""
        pid = self.process.pid
        status = Path(f"/proc/{pid}/status")
        if status.is_file():
            for line in status.read_text(encoding="utf-8").splitlines():
                if line.startswith("VmHWM:"):
                    return {"server_rss_peak_bytes": int(line.split()[1]) * 1024}
        try:
            rss = subprocess.run(
                ["ps", "-o", "rss=", "-p", str(pid)],
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            rss = ""
        return {"server_rss_end_bytes": int(rss) * 1024 if rss.isdigit() else None}

    def tail(self) -> str:
        self._log_handle.flush()
        return self.log.read_text(encoding="utf-8", errors="replace")[-4000:]

    def close(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
        self._log_handle.close()


def _gl_args(mode: str) -> list[str]:
    if mode == "swiftshader" or (mode == "auto" and sys.platform.startswith("linux")):
        return list(SWIFTSHADER_ARGS)
    return []


_SCENE_ROUTE = re.compile(r"/api/scenes/[^/]+/(points|frame|image)$")


def url_kind(url: str) -> str:
    """The scene route a URL is for; prediction frames and the rest are "other"."""
    match = _SCENE_ROUTE.search(url.split("?", 1)[0])
    return match.group(1) if match else "other"


def drive(server: Server, scene_id: str, args: argparse.Namespace) -> dict[str, object]:
    """Open the scene, play at 1x, return the raw measurements."""
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise Unavailable(
            "playwright is not installed: `pip install -e '.[app,dev]'` then "
            "`python -m playwright install chromium`"
        ) from exc

    with sync_playwright() as playwright:
        chromium_args = [*_gl_args(args.gl), "--enable-precise-memory-info"]
        try:
            browser = playwright.chromium.launch(
                headless=not args.headed,
                args=chromium_args,
                executable_path=args.executable_path,
            )
        except PlaywrightError as exc:
            raise Unavailable(
                f"chromium cannot start here: {str(exc).splitlines()[0]} "
                "(try `python -m playwright install chromium`)"
            ) from exc
        try:
            page = browser.new_page(viewport={"width": args.width, "height": args.height})
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            initial: dict[str, object] = {}
            if args.frustum_images:
                initial["layer_toggles"] = {"frustums": True, "frustum_images": True}
            page.add_init_script(IO_HOOK_JS)
            page.add_init_script(
                "try { sessionStorage.setItem('coperceive-token', "
                + json.dumps(server.token)
                + "); } catch (e) {}\n"
                + "window.__COPERCEIVE_TEST_ENV__ = true;\n"
                + (
                    f"window.__COPERCEIVE_INITIAL_STATE__ = {json.dumps(initial)};"
                    if initial
                    else ""
                )
            )
            # Bytes received, from the DevTools protocol (no body copies).
            cdp = page.context.new_cdp_session(page)
            cdp.send("Network.enable")  # pyright: ignore[reportUnknownMemberType]
            urls: dict[str, str] = {}
            received: list[tuple[float, str, int]] = []

            def sent(event: dict[str, Any]) -> None:
                urls[str(event["requestId"])] = str(event["request"]["url"])

            def finished(event: dict[str, Any]) -> None:
                kind = url_kind(urls.get(str(event["requestId"]), ""))
                size = int(event.get("encodedDataLength", 0))
                received.append((time.monotonic(), kind, size))

            cdp.on("Network.requestWillBeSent", sent)
            cdp.on("Network.loadingFinished", finished)

            opened = time.monotonic()
            page.goto(f"{server.base}/#/viewer", wait_until="domcontentloaded")
            renderer = cast(dict[str, object], page.evaluate(RENDERER_JS))
            status = page.locator(".status-line")
            deadline = time.monotonic() + args.timeout_s
            while time.monotonic() < deadline:
                if page.locator(".webgl-missing").count() > 0:
                    break
                if int(page.evaluate(VISIBLE_POINTS_JS)) > 0:
                    break
                page.wait_for_timeout(100)
            else:
                text = status.inner_text() if status.count() else "(no status line)"
                raise RuntimeError(f"no sample drawn in {args.timeout_s} s; status: {text!r}")
            first_draw_s = time.monotonic() - opened
            no_webgl = page.locator(".webgl-missing").count() > 0
            if no_webgl:
                # The notice replaces the stage; give the frame loader a moment to start.
                page.wait_for_timeout(500)
            page.evaluate(
                "window.__COPERCEIVE_PLAYBACK__ && window.__COPERCEIVE_PLAYBACK__('speed', 1)"
            )
            page.evaluate(MONITOR_JS)
            play = page.locator("button[aria-label=Play]")
            if play.count() > 0:
                play.first.click()
            else:
                page.evaluate(
                    "window.__COPERCEIVE_PLAYBACK__ && window.__COPERCEIVE_PLAYBACK__('toggle')"
                )
            play_at = cast(float, page.evaluate("performance.now()"))
            page.wait_for_timeout(args.warmup_s * 1000)
            measure_from = cast(float, page.evaluate("performance.now()"))
            measure_from_mono = time.monotonic()
            page.wait_for_timeout(args.measure_s * 1000)
            raw = cast(dict[str, Any], page.evaluate(COLLECT_JS))
            measure_to_mono = time.monotonic()
            raw.update(
                {
                    "renderer": renderer,
                    "no_webgl": no_webgl,
                    "first_draw_s": round(first_draw_s, 2),
                    "play_at": play_at,
                    "measure_from": measure_from,
                    "measure_to": raw["now"],
                    "errors": errors,
                    "browser_version": browser.version,
                    "chromium_args": chromium_args,
                    "network": [
                        (kind, size)
                        for at, kind, size in received
                        if measure_from_mono <= at <= measure_to_mono
                    ],
                }
            )
            page.close()
            return raw
        finally:
            browser.close()


def summarize(raw: dict[str, Any], *, scene_rate_hz: float, samples: int) -> dict[str, object]:
    """Turn the page's raw logs into the report's numbers."""
    start, end = float(raw["measure_from"]), float(raw["measure_to"])
    window_s = max(1e-6, (end - start) / 1000.0)
    raf = [float(t) for t in raw["raf"] if start <= float(t) <= end]
    frame_ms = [b - a for a, b in zip(raf, raf[1:], strict=False)]
    stats = [row for row in raw["frame_stats"] if start <= float(row["at"]) <= end]
    applied = [row for row in stats if row.get("result") == "applied"]
    polls = [row for row in raw["polls"] if start <= float(row["at"]) <= end]

    def rising(key: str) -> int:
        events = 0
        previous = bool(polls[0][key]) if polls else False
        for row in polls[1:]:
            now = bool(row[key])
            events += int(now and not previous)
            previous = now
        return events

    buffering_ms = 0.0
    for before, after in zip(polls, polls[1:], strict=False):
        if before["buffering"]:
            buffering_ms += float(after["at"]) - float(before["at"])
    advanced = 0
    wrapped = False
    indices = [int(row["index"]) for row in polls if row["index"] is not None]
    for before, after in zip(indices, indices[1:], strict=False):
        if after >= before:
            advanced += after - before
        else:  # looped back to the start
            wrapped = True
            advanced += (samples - 1 - before) + after + 1
    heap_used = [int(row["heap_used"]) for row in polls if row["heap_used"] is not None]
    heap_total = [int(row["heap_total"]) for row in polls if row["heap_total"] is not None]
    visible = [int(row["points"]) for row in polls if row["points"] is not None]
    by_kind: dict[str, int] = {"points": 0, "frame": 0, "image": 0, "other": 0}
    for kind, size in raw["network"]:
        by_kind[kind] = by_kind.get(kind, 0) + int(size)
    network_bytes = sum(by_kind.values())

    def phase(key: str, rows: list[dict[str, Any]]) -> dict[str, float | int]:
        values = [float(row[key]) for row in rows if row.get(key) is not None]
        return _spread(values)

    def io_spread(rows: list[dict[str, Any]]) -> dict[str, dict[str, dict[str, float | int]]]:
        grouped: dict[str, dict[str, list[float]]] = {}
        for row in rows:
            grouped.setdefault(str(row["kind"]), {}).setdefault(str(row["op"]), []).append(
                float(row["ms"])
            )
        return {kind: {op: _spread(v) for op, v in ops.items()} for kind, ops in grouped.items()}

    io_rows = cast(list[dict[str, Any]], raw.get("io", []))
    io_ms = io_spread([row for row in io_rows if start <= float(row["at"]) <= end])
    io_all_ms = io_spread(io_rows)

    no_webgl = bool(raw["no_webgl"])
    report: dict[str, object] = {
        "window_s": round(window_s, 2),
        "rendering_skipped": no_webgl,
        "first_draw_s": raw["first_draw_s"],
        "phases_ms": {"load": phase("load_ms", stats), "points": phase("points_ms", applied)},
        "io_ms": io_ms,
        "io_all_ms": io_all_ms,
        "decode_ms": {
            "frame_json_parse": io_ms.get("frame", {}).get("parse", _spread([])),
            "image_decode": io_ms.get("image", {}).get("decode"),  # None: decoded in a worker
            "points": None,
        },
        "buffering": {
            "events": rising("buffering"),
            "ms": round(buffering_ms, 1),
            "at_window_start": bool(polls[0]["buffering"]) if polls else False,
            "stalled_events": rising("stalled"),
            "buffered_s_min": round(
                min(
                    (float(r["buffered_s"]) for r in polls if r["buffered_s"] is not None),
                    default=0.0,
                ),
                2,
            ),
        },
        "playhead": {
            "samples_advanced": advanced,
            "per_s": round(advanced / window_s, 2),
            "scene_rate_hz": scene_rate_hz,
            "wrapped": wrapped,
        },
        "memory": {
            "js_heap_used_max_bytes": max(heap_used) if heap_used else None,
            "js_heap_used_end_bytes": heap_used[-1] if heap_used else None,
            "js_heap_total_end_bytes": heap_total[-1] if heap_total else None,
        },
        "network": {
            "bytes": network_bytes,
            "mb_per_s": round(network_bytes / 1024**2 / window_s, 2),
            "by_kind_bytes": by_kind,
        },
        "cache": raw.get("cache"),
        "buffer": raw.get("buffer"),
        "page_errors": raw.get("errors", []),
    }
    if not no_webgl:
        phases = cast(dict[str, object], report["phases_ms"])
        phases["draw"] = phase("draw_ms", applied)
        phases["render"] = phase("render_ms", applied)
        report["frame_time_ms"] = _spread(frame_ms)
        report["samples"] = {
            "drawn": len(applied),
            "dropped": len(stats) - len(applied),
            "drawn_per_s": round(len(applied) / window_s, 2),
            "scene_rate_hz": scene_rate_hz,
            "ratio": round(len(applied) / window_s / scene_rate_hz, 3) if scene_rate_hz else None,
        }
        memory = cast(dict[str, object], report["memory"])
        memory["visible_points_max"] = max(visible) if visible else 0
        memory["gpu_points_estimate_bytes"] = (max(visible) if visible else 0) * BYTES_PER_GPU_POINT
    return report


def _mb(value: object) -> str:
    return f"{value / 1024**2:.0f} MB" if isinstance(value, int) else "n/a"


def table(result: dict[str, Any]) -> str:
    fixture = result["fixture"]
    m = result["measured"]
    browser = result["browser"]
    size = cast(list[int] | None, fixture.get("image_size"))
    images = f", images {size[0]}x{size[1]}" if size and len(size) == 2 else ""
    lines = [
        f"viewer_bench{' (quick)' if result['quick'] else ''}: {fixture['agents']} agents x "
        f"{fixture['points']} points x {fixture['cameras']} cameras x {fixture['samples']} "
        f"samples at {fixture['rate_hz']} Hz{images} ({_mb(fixture.get('bytes'))} on disk)",
        f"  machine    {machine_line(result['machine'])}",
        f"  browser    {browser['name']} {browser['version']}, WebGL renderer: "
        f"{browser['renderer'] or 'none'}",
        f"  server     open scene {result['server']['open_scene_ms']} ms; "
        f"first draw {m['first_draw_s']} s; warm-up {result['config']['warmup_s']} s, "
        f"window {m['window_s']} s",
    ]
    if m["rendering_skipped"]:
        lines.append("  rendering skipped: no WebGL (fetch side only below)")
    else:
        ft = m["frame_time_ms"]
        s = m["samples"]
        lines.append(
            f"  frame time p50 {ft['p50']} ms, p95 {ft['p95']} ms, max {ft['max']} ms "
            f"({ft['count']} rAF frames)"
        )
        lines.append(
            f"  samples    {s['drawn_per_s']}/s drawn vs {s['scene_rate_hz']} Hz "
            f"(ratio {s['ratio']}), {s['dropped']} dropped"
        )
    for name, spread in m["phases_ms"].items():
        lines.append(
            f"  {name:<10} p50 {spread['p50']} ms, p95 {spread['p95']} ms ({spread['count']})"
        )
    for kind, ops in sorted(m["io_ms"].items()):
        if kind == "other":
            continue
        parts = [
            f"{op} p50 {spread['p50']} / p95 {spread['p95']} ms ({spread['count']})"
            for op, spread in ops.items()
        ]
        lines.append(f"  io {kind:<7} " + "; ".join(parts))
    b = m["buffering"]
    p = m["playhead"]
    lines.append(
        f"  buffering  {b['events']} events, {b['ms']} ms after warm-up "
        f"(stalled {b['stalled_events']}); playhead {p['per_s']}/s"
        + (" (looped)" if p["wrapped"] else "")
    )
    mem = m["memory"]
    gpu = (
        f", GPU points ~{_mb(mem.get('gpu_points_estimate_bytes'))} "
        f"({mem.get('visible_points_max')} visible)"
        if "gpu_points_estimate_bytes" in mem
        else ""
    )
    server_rss = (
        f", server RSS peak {_mb(mem['server_rss_peak_bytes'])}"
        if "server_rss_peak_bytes" in mem
        else f", server RSS at end {_mb(mem.get('server_rss_end_bytes'))}"
    )
    lines.append(f"  memory     JS heap max {_mb(mem['js_heap_used_max_bytes'])}{server_rss}{gpu}")
    n = m["network"]
    lines.append(f"  network    {n['mb_per_s']} MB/s received ({_mb(n['bytes'])} in the window)")
    if m["page_errors"]:
        lines.append(f"  page errors: {len(m['page_errors'])} (see JSON)")
    return "\n".join(lines)


def _scene_rate(times_us: list[int]) -> float:
    gaps = sorted(b - a for a, b in zip(times_us, times_us[1:], strict=False) if b > a)
    if not gaps:
        return 0.0
    return round(1e6 / gaps[len(gaps) // 2], 3)


def run(args: argparse.Namespace) -> dict[str, Any]:
    static = bundle_dir()
    workdir = Path(tempfile.mkdtemp(prefix="coperceive-viewer-bench-"))
    try:
        if args.fixture is not None:
            root = Path(args.fixture).expanduser().resolve()
            fixture: dict[str, object] = {"root": str(root), "generated": False}
        else:
            root = workdir / "scenes"
            fixture = {**make_fixture(root, args), "generated": True}
        server = Server(root, static, workdir / "serve.log")
        try:
            listed = cast(list[dict[str, str]], _get(f"{server.base}/api/scenes", server.token))
            if not listed:
                raise RuntimeError(f"no complete scene under {root}")
            scene_id = args.scene_id or listed[0]["scene_id"]
            opened = time.monotonic()
            summary = cast(
                dict[str, Any], _get(f"{server.base}/api/scenes/{scene_id}", server.token)
            )
            open_scene_ms = round((time.monotonic() - opened) * 1000.0, 1)
            times_us = [int(t) for t in summary["times_us"]]
            sensors = cast(list[dict[str, Any]], summary.get("sensors", []))
            if not fixture["generated"]:
                fixture.update(
                    {
                        "scene_id": scene_id,
                        "agents": len(summary.get("agents", [])),
                        "points": "n/a",
                        "cameras": sum(1 for s in sensors if s.get("modality") == "camera"),
                        "samples": len(times_us),
                        "rate_hz": _scene_rate(times_us),
                        "bytes": sum(
                            p.stat().st_size for p in (root / scene_id).rglob("*") if p.is_file()
                        )
                        if (root / scene_id).is_dir()
                        else None,
                    }
                )
            rate = _scene_rate(times_us)
            raw = drive(server, scene_id, args)
            server_memory = server.memory()
        except BaseException:
            if args.verbose:
                sys.stderr.write(server.tail())
            raise
        finally:
            server.close()
        measured = summarize(raw, scene_rate_hz=rate, samples=len(times_us))
        cast(dict[str, object], measured["memory"]).update(server_memory)
        renderer = cast(dict[str, Any], raw["renderer"])
        return {
            "schema": SCHEMA,
            "quick": bool(args.quick),
            "label": "synthetic scale fixture, local coperceive serve, headless Chromium",
            "machine": machine_info(),
            "browser": {
                "name": "chromium",
                "version": raw["browser_version"],
                "headless": not args.headed,
                "args": raw["chromium_args"],
                "gl": args.gl,
                "webgl": bool(renderer.get("webgl")),
                "renderer": renderer.get("renderer"),
                "vendor": renderer.get("vendor"),
                "gl_version": renderer.get("version"),
            },
            "fixture": fixture,
            "server": {"open_scene_ms": open_scene_ms},
            "config": {
                "warmup_s": args.warmup_s,
                "measure_s": args.measure_s,
                "viewport": [args.width, args.height],
                "frustum_images": bool(args.frustum_images),
            },
            "measured": measured,
            "playback_perf": raw.get("playback_perf"),
        }
    finally:
        if args.keep_fixture and args.fixture is None:
            print(f"viewer_bench: kept {workdir}", file=sys.stderr)
        else:
            shutil.rmtree(workdir, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--fixture", help="a --root with a scale_fixture.py scene (else generate)")
    parser.add_argument("--scene-id", help="scene to open (default: the first one)")
    parser.add_argument("--quick", action="store_true", help="CI shape: 30 samples, short window")
    parser.add_argument("--agents", type=int)
    parser.add_argument("--points", type=int)
    parser.add_argument("--cameras", type=int)
    parser.add_argument("--samples", type=int)
    parser.add_argument("--rate-hz", type=float, default=10.0)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument(
        "--image-size", metavar="WxH", help="camera image size for a generated fixture"
    )
    parser.add_argument("--warmup-s", type=float, help="play this long before measuring")
    parser.add_argument("--measure-s", type=float, help="the measured window")
    parser.add_argument(
        "--gl",
        choices=("auto", "swiftshader", "default"),
        default="auto",
        help="auto: SwiftShader on Linux (as CI), the browser's default elsewhere",
    )
    parser.add_argument("--frustum-images", action="store_true", help="camera images in frustums")
    parser.add_argument("--width", type=int, default=1400)
    parser.add_argument("--height", type=int, default=900)
    parser.add_argument("--timeout-s", type=float, default=180.0, help="wait for the first draw")
    parser.add_argument("--headed", action="store_true")
    parser.add_argument(
        "--executable-path", help="a Chromium or Chrome binary instead of Playwright's own build"
    )
    parser.add_argument("--keep-fixture", action="store_true")
    parser.add_argument("--json-out", type=Path, help="also write the JSON report here")
    parser.add_argument("--verbose", action="store_true", help="print the server log on failure")
    args = parser.parse_args(argv)
    shape = QUICK if args.quick else FULL
    for key in ("agents", "points", "cameras", "samples"):
        if getattr(args, key) is None:
            setattr(args, key, shape[key])
    if args.warmup_s is None:
        args.warmup_s = shape["warmup"]
    if args.measure_s is None:
        args.measure_s = shape["measure"]
    try:
        result = run(args)
    except Unavailable as exc:
        print(f"viewer_bench: {exc}", file=sys.stderr)
        return UNAVAILABLE
    result["measured"]["rendering_note"] = (
        "rendering skipped: no WebGL" if result["measured"]["rendering_skipped"] else None
    )
    if result["measured"]["rendering_skipped"]:
        print("rendering skipped: no WebGL", file=sys.stderr)
    text = json.dumps(result)
    if args.json_out is not None:
        args.json_out.write_text(text + "\n", encoding="utf-8")
    print(text, flush=True)
    print(table(result), file=sys.stderr, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
