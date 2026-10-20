# SPDX-License-Identifier: MIT
"""Run Lighthouse audits (desktop and mobile) against site/dist and report scores."""

from __future__ import annotations

import functools
import http.server
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

SITE_DIR = Path(__file__).resolve().parents[2] / "site"
DIST_DIR = SITE_DIR / "dist"
ARTIFACT_DIR = Path(
    os.environ.get(
        "COPERCEIVE_ARTIFACT_DIR",
        "/home/nanjaiyalathaa/.gemini/antigravity-cli/brain/a741175a-9819-4f1c-b434-bcaf7075b85d",
    )
)
CHROME_PATH = "/home/nanjaiyalathaa/.cache/ms-playwright/chromium-1248/chrome-linux64/chrome"


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        pass


def run_audit(url: str, mode: str) -> dict:
    print(f"\n--- Running Lighthouse Audit ({mode}) ---")
    output_base = DIST_DIR / f"lighthouse-{mode}"
    cmd = [
        str(SITE_DIR / "node_modules" / ".bin" / "lighthouse"),
        url,
        "--output=json,html",
        f"--output-path={output_base}",
        (
            "--chrome-flags=--headless=new --no-sandbox --disable-dev-shm-usage "
            "--use-gl=angle --use-angle=swiftshader --enable-unsafe-swiftshader"
        ),
        "--quiet",
    ]
    if mode == "desktop":
        cmd.append("--preset=desktop")

    env = os.environ.copy()
    env["CHROME_PATH"] = CHROME_PATH

    res = subprocess.run(cmd, env=env, cwd=str(SITE_DIR), capture_output=True, text=True)
    if res.returncode != 0:
        print(f"Lighthouse failed with code {res.returncode}:")
        print(res.stderr)
        sys.exit(res.returncode)

    json_path = DIST_DIR / f"lighthouse-{mode}.report.json"
    with open(json_path) as f:
        data = json.load(f)

    categories = data.get("categories", {})
    scores = {
        cat: round(categories[cat]["score"] * 100)
        for cat in ["performance", "accessibility", "best-practices", "seo"]
        if cat in categories and categories[cat].get("score") is not None
    }

    print(f"Results for {mode.upper()}:")
    for cat, score in scores.items():
        print(f"  {cat:15s}: {score}/100")

    if ARTIFACT_DIR.is_dir():
        shutil.copy2(json_path, ARTIFACT_DIR / f"lighthouse-{mode}.report.json")
        html_path = DIST_DIR / f"lighthouse-{mode}.report.html"
        if html_path.exists():
            shutil.copy2(html_path, ARTIFACT_DIR / f"lighthouse-{mode}.report.html")

    return scores


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

    try:
        desktop_scores = run_audit(base_url, "desktop")
        mobile_scores = run_audit(base_url, "mobile")

        summary = {
            "desktop": desktop_scores,
            "mobile": mobile_scores,
        }
        with open(DIST_DIR / "lighthouse_summary.json", "w") as f:
            json.dump(summary, f, indent=2)

        if ARTIFACT_DIR.is_dir():
            with open(ARTIFACT_DIR / "lighthouse_summary.json", "w") as f:
                json.dump(summary, f, indent=2)

        print("\n=== Lighthouse Audit Complete ===")
        print(json.dumps(summary, indent=2))
    finally:
        httpd.shutdown()


if __name__ == "__main__":
    main()
