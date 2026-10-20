# SPDX-License-Identifier: MIT
"""Measure frame-time (median/p95) and record a <=15s video of the hero animation."""

from __future__ import annotations

import functools
import http.server
import json
import os
import shutil
import socket
import sys
import threading
import time
from pathlib import Path

import numpy as np
from playwright.sync_api import sync_playwright

DIST_DIR = Path(__file__).resolve().parents[2] / "site" / "dist"
ARTIFACT_DIR = Path(
    os.environ.get(
        "COPERCEIVE_ARTIFACT_DIR",
        "/home/nanjaiyalathaa/.gemini/antigravity-cli/brain/a741175a-9819-4f1c-b434-bcaf7075b85d",
    )
)


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        pass


def main() -> None:
    if not (DIST_DIR / "index.html").is_file():
        sys.exit(f"site/dist does not exist at {DIST_DIR}. Run 'npm run build' first.")

    port = find_free_port()
    handler = functools.partial(QuietHandler, directory=str(DIST_DIR))
    httpd = http.server.HTTPServer(("127.0.0.1", port), handler)
    server_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    server_thread.start()
    base_url = f"http://127.0.0.1:{port}"
    time.sleep(0.3)

    temp_video_dir = Path("/tmp/hero_recordings")
    temp_video_dir.mkdir(parents=True, exist_ok=True)

    print(f"Server started at {base_url}")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            args=[
                "--use-gl=angle",
                "--use-angle=swiftshader",
                "--enable-unsafe-swiftshader",
                "--ignore-gpu-blocklist",
            ]
        )
        # Context with video recording enabled
        context = browser.new_context(
            viewport={"width": 1440, "height": 900},
            record_video_dir=str(temp_video_dir),
            record_video_size={"width": 1440, "height": 900},
        )
        page = context.new_page()

        print("Navigating to landing page...")
        page.goto(base_url, wait_until="networkidle")

        # Wait for hero canvas and telemetry to be active
        page.wait_for_selector('div[role="region"]', timeout=15000)
        page.wait_for_selector('text="CAV (Ground)"', timeout=15000)

        # 1. Measure rAF frame times over 100 frames
        print("Measuring requestAnimationFrame frame times over 100 frames...")
        deltas = page.evaluate(
            """
            () => new Promise((resolve) => {
                const samples = [];
                let lastTime = performance.now();
                function step(now) {
                    samples.push(now - lastTime);
                    lastTime = now;
                    if (samples.length >= 105) {
                        resolve(samples.slice(5)); // skip first 5 warm-up frames
                    } else {
                        requestAnimationFrame(step);
                    }
                }
                requestAnimationFrame(step);
            })
            """
        )

        deltas_np = np.array(deltas, dtype=float)
        median_ms = float(np.median(deltas_np))
        p95_ms = float(np.percentile(deltas_np, 95))
        min_ms = float(np.min(deltas_np))
        max_ms = float(np.max(deltas_np))

        metrics = {
            "num_frames": len(deltas),
            "median_ms": round(median_ms, 2),
            "p95_ms": round(p95_ms, 2),
            "min_ms": round(min_ms, 2),
            "max_ms": round(max_ms, 2),
        }
        print(f"Frame Time Metrics: {json.dumps(metrics, indent=2)}")

        # Save screenshot
        screenshot_local = DIST_DIR / "landing_page.png"
        page.screenshot(path=str(screenshot_local), full_page=True)
        print(f"Saved full-page screenshot to {screenshot_local}")

        if ARTIFACT_DIR.is_dir():
            shutil.copy2(screenshot_local, ARTIFACT_DIR / "landing_page.png")
            print(f"Copied screenshot to artifact dir: {ARTIFACT_DIR / 'landing_page.png'}")

        # Let the animation run for 10 seconds total video duration (<= 15s)
        print("Recording animation (10 seconds)...")
        page.wait_for_timeout(10000)

        # Close page and context to finalize video
        video_path = page.video.path() if page.video else None
        page.close()
        context.close()
        browser.close()

    httpd.shutdown()

    if video_path and os.path.exists(video_path):
        out_webm = DIST_DIR / "hero_recording.webm"
        shutil.copy2(video_path, out_webm)
        print(f"Saved video recording to {out_webm} (size: {os.path.getsize(out_webm)} bytes)")
        if ARTIFACT_DIR.is_dir():
            shutil.copy2(video_path, ARTIFACT_DIR / "hero_recording.webm")
            print(f"Copied video recording to {ARTIFACT_DIR / 'hero_recording.webm'}")

        # Also output summary json
        summary = {
            "metrics": metrics,
            "video_size_bytes": os.path.getsize(out_webm),
            "video_path": str(out_webm),
            "screenshot_path": str(screenshot_local),
        }
        with open(DIST_DIR / "perf_metrics.json", "w") as f:
            json.dump(summary, f, indent=2)
        print("Done!")
    else:
        print("Warning: No video recorded.")


if __name__ == "__main__":
    main()
