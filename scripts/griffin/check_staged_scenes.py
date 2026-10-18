#!/usr/bin/env python
# SPDX-License-Identifier: Apache-2.0
"""Open staged scenes in headless Chromium and record what the page shows.

Runs inside the allocation that staged the scenes (``stage_viewer.py``): it starts
``coperceive serve``'s app in-process on a free loopback port with the staged roots,
visits each scene, waits for lidar points, the town map and a camera thumbnail, opens
the split view on the first camera, and writes ``report.json`` and PNGs to ``--out``.
Exit status 1 when a scene shows no points, no map or no camera image, so the job log
says so. Nothing here touches the dataset: images are read through the camera links
and ``--media-root``.
"""

from __future__ import annotations

import argparse
import json
import socket
import sys
import threading
import time
from pathlib import Path
from typing import Any

import uvicorn
from coperceive.app.server import create_app
from playwright.sync_api import Browser, Page, sync_playwright

CHROMIUM_ARGS = [
    "--use-gl=angle",
    "--use-angle=swiftshader",
    "--enable-unsafe-swiftshader",
    "--ignore-gpu-blocklist",
]


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait(page: Page, ready: Any, what: str, timeout_s: float = 60.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if ready():
            return
        page.wait_for_timeout(250)
    raise TimeoutError(f"waited {timeout_s}s for {what}")


def _status(page: Page) -> str:
    return page.locator(".status-line").inner_text()


def _points_in(status: str) -> int:
    # "Town03-000 at 1118500000 µs. 2929 points, 55 boxes, 9 frustums. map drawn."
    try:
        return int(status.split(" µs. ", 1)[1].split(" points", 1)[0])
    except (IndexError, ValueError):
        return -1


def _map_settled(page: Page) -> bool:
    """The map is drawn, or the page says the scene has none."""
    status = _status(page)
    if "map drawn." in status:
        return True
    pills = " ".join(page.locator(".empty-pill li").all_inner_texts())
    return "no map" in pills or "no map" in status


def check_scene(
    page: Page, scene_id: str, out: Path, first: bool, *, require_map: bool = True
) -> dict[str, object]:
    requests: list[str] = []
    bad: list[str] = []
    page.on("request", lambda req: requests.append(req.url) if "/api/" in req.url else None)
    page.on(
        "response",
        lambda res: (
            bad.append(f"{res.status} {res.url}")
            if res.status >= 400 and "/api/" in res.url
            else None
        ),
    )
    if not first:
        page.locator("#scene-id").click()
        page.locator("[role=option]", has_text=scene_id).click()
    started = time.monotonic()
    _wait(page, lambda: scene_id in _status(page), f"{scene_id} to load")
    if require_map:
        _wait(page, lambda: "map drawn." in _status(page), "the map")
    else:
        _wait(page, lambda: _map_settled(page), "the map or a no-map note")
    _wait(page, lambda: _points_in(_status(page)) > 0, "lidar points")
    _wait(page, lambda: page.locator(".cam-thumb canvas").count() >= 1, "a camera thumbnail")
    loaded_s = round(time.monotonic() - started, 2)
    page.wait_for_timeout(800)
    status = _status(page)
    pill = page.locator(".empty-pill li").all_inner_texts()
    page.screenshot(path=str(out / f"{scene_id}.png"))
    page.locator(".cam-thumb").first.click()
    _wait(page, lambda: page.locator(".camera-split canvas").count() == 1, "the split image")
    page.wait_for_timeout(500)
    page.screenshot(path=str(out / f"{scene_id}-split.png"))
    page.keyboard.press("Escape")
    report: dict[str, object] = {
        "scene_id": scene_id,
        "status": status,
        "points": _points_in(status),
        "map_drawn": "map drawn." in status,
        "thumbnails": page.locator(".cam-thumb canvas").count(),
        "missing_pill": pill,
        "image_requests": sum("/image?" in url for url in requests),
        "http_errors": bad[:10],
        "seconds_to_show": loaded_s,
    }
    print(json.dumps(report), flush=True)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--maps", required=True, type=Path)
    parser.add_argument("--predictions", type=Path, default=None)
    parser.add_argument("--media-root", action="append", default=[], type=Path)
    parser.add_argument("--static", type=Path, default=None)
    parser.add_argument("--scenes", nargs="+", required=True)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument(
        "--allow-no-map",
        action="store_true",
        help="A scene without a map passes when it shows the no-map note (OpenCOOD scenes "
        "whose town could not be identified).",
    )
    args = parser.parse_args(argv)
    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)
    app = create_app(
        root=args.root,
        token=None,
        maps=args.maps,
        predictions=args.predictions,
        static=args.static,
        media_roots=tuple(args.media_root),
    )
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 30
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    if not server.started:
        print("server did not start", file=sys.stderr)
        return 2
    reports: list[dict[str, object]] = []
    with sync_playwright() as playwright:
        browser: Browser = playwright.chromium.launch(headless=True, args=CHROMIUM_ARGS)
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        errors: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(f"http://127.0.0.1:{port}/#/viewer", wait_until="networkidle")
        for index, scene_id in enumerate(args.scenes):
            reports.append(
                check_scene(
                    page, scene_id, out, first=index == 0, require_map=not args.allow_no_map
                )
            )
        browser.close()
    server.should_exit = True
    thread.join(timeout=10)
    summary = {"scenes": reports, "page_errors": errors}
    (out / "report.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    failed = [
        r["scene_id"]
        for r in reports
        if not (
            isinstance(r["points"], int)
            and r["points"] > 0
            and (r["map_drawn"] is True or args.allow_no_map)
            and isinstance(r["thumbnails"], int)
            and r["thumbnails"] >= 1
        )
    ]
    if failed or errors:
        print(f"check failed: scenes {failed}, page errors {errors}", file=sys.stderr)
        return 1
    with_map = sum(1 for r in reports if r["map_drawn"] is True)
    print(
        f"ok: {len(reports)} scenes with points and camera images, {with_map} with a map; "
        f"report in {out}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
