# SPDX-License-Identifier: Apache-2.0
"""Record a short scroll of the landing page and a frame-time sample for CI artifacts."""

from __future__ import annotations

import functools
import http.server
import json
import os
import shutil
import socket
import statistics
import sys
import threading
import time
from pathlib import Path

DIST_DIR = Path(__file__).resolve().parents[2] / "site" / "dist"
ARTIFACT_DIR = Path(os.environ.get("COPERCEIVE_ARTIFACT_DIR", "/tmp/landing-artifacts"))


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        pass


def main() -> None:
    if not (DIST_DIR / "index.html").is_file():
        sys.exit(f"site/dist does not exist at {DIST_DIR}. Run npm run build in site/.")

    root = Path("/tmp/coperceive-pages-root")
    if root.exists():
        shutil.rmtree(root)
    root.mkdir()
    (root / "coperceive").symlink_to(DIST_DIR, target_is_directory=True)

    port = find_free_port()
    handler = functools.partial(QuietHandler, directory=str(root))
    httpd = http.server.HTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base_url = f"http://127.0.0.1:{port}/coperceive/"
    time.sleep(0.3)

    from playwright.sync_api import sync_playwright

    video_dir = Path("/tmp/hero_recordings")
    if video_dir.exists():
        shutil.rmtree(video_dir)
    video_dir.mkdir()

    with sync_playwright() as p:
        browser = p.chromium.launch(
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--use-gl=angle",
                "--use-angle=swiftshader",
                "--enable-unsafe-swiftshader",
            ]
        )
        context = browser.new_context(
            viewport={"width": 1440, "height": 900},
            record_video_dir=str(video_dir),
            record_video_size={"width": 1440, "height": 900},
        )
        page = context.new_page()
        page.goto(base_url, wait_until="networkidle")
        page.wait_for_selector("canvas[data-bev=hero]", timeout=15000)
        deltas = page.evaluate(
            """() => new Promise((resolve) => {
                const samples = [];
                let last = performance.now();
                function step(now) {
                    samples.push(now - last);
                    last = now;
                    if (samples.length >= 105) resolve(samples.slice(5));
                    else requestAnimationFrame(step);
                }
                requestAnimationFrame(step);
            })"""
        )
        metrics = {
            "num_frames": len(deltas),
            "median_ms": round(statistics.median(deltas), 2),
            "p95_ms": round(sorted(deltas)[max(0, int(len(deltas) * 0.95) - 1)], 2),
        }
        print(json.dumps(metrics))
        ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
        shot = ARTIFACT_DIR / "landing_page.png"
        page.screenshot(path=str(shot))
        page.evaluate(
            """() => new Promise((resolve) => {
                const start = window.scrollY;
                const end = document.body.scrollHeight - window.innerHeight;
                const t0 = performance.now();
                function step(now) {
                    const p = Math.min(1, (now - t0) / 12000);
                    window.scrollTo(0, start + (end - start) * p);
                    if (p < 1) requestAnimationFrame(step);
                    else resolve(true);
                }
                requestAnimationFrame(step);
            })"""
        )
        video = page.video
        page.close()
        context.close()
        browser.close()

    httpd.shutdown()
    if video is None:
        sys.exit("no video recorded")
    src = Path(video.path())
    dest = ARTIFACT_DIR / "hero_recording.webm"
    shutil.copy2(src, dest)
    print(f"wrote {shot} and {dest} ({dest.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
