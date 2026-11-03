# SPDX-License-Identifier: Apache-2.0
"""Visual regression and traffic-lights layout tests for the design system revamp."""

from __future__ import annotations

import os
import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import NoReturn

import pytest
import uvicorn
from coperceive.app.server import create_app

REQUIRED = os.environ.get("COPERCEIVE_E2E_REQUIRED", "") not in {"", "0"}


def _unavailable(reason: str) -> NoReturn:
    if REQUIRED:
        pytest.fail(reason)
    pytest.skip(reason)


try:
    from playwright.sync_api import Browser, Playwright, sync_playwright
    from playwright.sync_api import Error as PlaywrightError
except ImportError:  # pragma: no cover
    if REQUIRED:
        raise
    pytest.skip("playwright is not installed", allow_module_level=True)

_CHROMIUM_ARGS = [
    "--use-gl=angle",
    "--use-angle=swiftshader",
    "--enable-unsafe-swiftshader",
    "--ignore-gpu-blocklist",
]

VIEWPORTS = [
    {"width": 1280, "height": 800},
    {"width": 1600, "height": 1000},
]

THEMES = ["dark", "light"]

SCREENS = ["library", "get_data", "local", "remote", "settings"]


def _bundle_dir() -> Path | None:
    wanted = os.environ.get("COPERCEIVE_WEB_DIST")
    if wanted:
        chosen = Path(wanted).expanduser()
        if (chosen / "index.html").is_file():
            return chosen
    packaged = Path(__file__).resolve().parents[1] / "coperceive" / "app" / "static"
    return packaged if (packaged / "index.html").is_file() else None


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture(scope="module")
def app_url(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    bundle = _bundle_dir()
    if bundle is None:
        _unavailable("no web bundle built in coperceive/app/static")
    root = tmp_path_factory.mktemp("visual_root")
    app = create_app(root=root, token=None, static=bundle)
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, name="visual-server", daemon=True)
    thread.start()
    deadline = time.monotonic() + 20
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    assert server.started, "uvicorn did not start"
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=10)


@pytest.fixture(scope="module")
def playwright_instance() -> Iterator[Playwright]:
    with sync_playwright() as instance:
        yield instance


@pytest.fixture(scope="module")
def visual_browser(playwright_instance: Playwright) -> Iterator[Browser]:
    try:
        launched = playwright_instance.chromium.launch(headless=True, args=_CHROMIUM_ARGS)
    except PlaywrightError as exc:
        _unavailable(f"chromium cannot start: {exc}")
    yield launched
    launched.close()


@pytest.fixture(scope="module")
def screenshots_dir() -> Path:
    shots = Path(__file__).resolve().parents[1] / "docs" / "design" / "screenshots"
    shots.mkdir(parents=True, exist_ok=True)
    return shots


def test_visual_revamp_screens_and_traffic_lights_no_overlap(
    visual_browser: Browser, app_url: str, screenshots_dir: Path
) -> None:
    """Verifies each screen in dark and light themes at 1280x800 and 1600x1000,

    taking screenshots and asserting zero overlap with macOS traffic lights (x: 0..72px).
    """
    for vp in VIEWPORTS:
        w, h = vp["width"], vp["height"]
        context = visual_browser.new_context(viewport={"width": w, "height": h})
        page = context.new_page()

        try:
            # Enable test environment flags
            page.add_init_script("window.__COPERCEIVE_TEST_ENV__ = true;")
            page.add_init_script("window.__COPERCEIVE_DESKTOP__ = true;")
            page.goto(f"{app_url}/#/connect", wait_until="networkidle")
            page.wait_for_selector(".c-sidebar", timeout=15000)

            # Check traffic light safe zone: traffic light spacer exists and spans at least 68px
            spacer = page.locator(".traffic-lights-spacer")
            assert spacer.count() > 0, "Traffic lights spacer must exist"
            box = spacer.first.bounding_box()
            assert box is not None, "Spacer must be rendered"
            assert box["width"] >= 68, (
                f"Traffic lights spacer width is {box['width']}, expected >= 68"
            )
            assert box["x"] <= 1, f"Traffic lights spacer must start at left edge, got x={box['x']}"

            # Check wordmark position: brand-wordmark must be located to the right of traffic lights
            wordmark = page.locator(".c-sidebar .brand-wordmark")
            assert wordmark.count() > 0, "Brand wordmark must exist in sidebar"
            wm_box = wordmark.first.bounding_box()
            assert wm_box is not None, "Wordmark must have a bounding box"
            assert wm_box["x"] >= 74, (
                f"Brand wordmark x={wm_box['x']} overlaps with traffic lights safe zone (0..72px)!"
            )

            # Test screens in both themes
            for theme in THEMES:
                page.evaluate(f"document.documentElement.dataset.theme = '{theme}';")
                page.wait_for_timeout(100)

                for screen in SCREENS:
                    if screen == "library":
                        page.locator("button[role=tab]", has_text="Library").click()
                        page.wait_for_timeout(150)
                    elif screen == "get_data":
                        page.locator("button[role=tab]", has_text="Get Data").click()
                        page.wait_for_timeout(150)
                    elif screen == "local":
                        page.locator("button[role=tab]", has_text="Open Folder").click()
                        page.wait_for_timeout(150)
                    elif screen == "remote":
                        page.locator("button[role=tab]", has_text="HPC Workspace").click()
                        page.wait_for_timeout(150)
                    elif screen == "settings":
                        # Open settings modal
                        page.locator("button[aria-label='Settings (⌘,)']").click()
                        page.wait_for_selector(
                            "div[role=dialog][aria-label='Settings']", timeout=5000
                        )
                        page.wait_for_timeout(150)

                    # Capture screenshot
                    shot_path = screenshots_dir / f"{screen}_{theme}_{w}x{h}.png"
                    page.screenshot(path=str(shot_path), full_page=False)
                    assert shot_path.is_file() and shot_path.stat().st_size > 0

                    if screen == "settings":
                        # Close settings modal
                        page.locator("button[aria-label='Close settings (Esc)']").click()
                        page.wait_for_timeout(150)

            # Test collapsing sidebar:
            # When collapsed, toggle button and wordmark appear in top bar right of traffic lights
            toggle_btn = page.locator("button[aria-label*='Toggle sidebar']")
            toggle_btn.first.click()
            page.wait_for_timeout(250)

            # Assert sidebar is collapsed
            sidebar = page.locator(".c-sidebar")
            s_box = sidebar.bounding_box()
            assert s_box is not None
            assert s_box["width"] <= 1, f"Sidebar should be collapsed, width={s_box['width']}"

            # Wordmark and expand button in top bar must not overlap traffic lights safe zone
            collapsed_wm = page.locator("header .brand-wordmark")
            assert collapsed_wm.count() > 0
            c_box = collapsed_wm.first.bounding_box()
            assert c_box is not None
            assert c_box["x"] >= 74, (
                f"Collapsed header wordmark x={c_box['x']} overlaps with traffic lights (0..72px)!"
            )

            # Capture collapsed screenshot
            shot_collapsed = screenshots_dir / f"sidebar_collapsed_dark_{w}x{h}.png"
            page.screenshot(path=str(shot_collapsed))
            assert shot_collapsed.is_file()

        finally:
            context.close()
