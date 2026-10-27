# SPDX-License-Identifier: Apache-2.0
"""Browser checks for the public landing page."""

from __future__ import annotations

import functools
import http.server
import os
import socket
import threading
import time
from pathlib import Path
from typing import NoReturn, cast

import pytest

try:
    from playwright.sync_api import Browser, ConsoleMessage, Page, Playwright, sync_playwright
except ImportError:
    if os.environ.get("COPERCEIVE_E2E_REQUIRED", "") not in {"", "0"}:
        raise
    pytest.skip("playwright is not installed", allow_module_level=True)

ROOT = Path(__file__).resolve().parent.parent
DIST_DIR = ROOT / "site" / "dist"
AXE_PATH = ROOT / "site" / "node_modules" / "axe-core" / "axe.min.js"
PREFIX = "/coperceive"

REQUIRED = os.environ.get("COPERCEIVE_E2E_REQUIRED", "") not in {"", "0"}

CHROME_ARGS = [
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--use-gl=angle",
    "--use-angle=swiftshader",
    "--enable-unsafe-swiftshader",
    "--ignore-gpu-blocklist",
    # The landing canvases are 2D. On SwiftShader (software GL) an accelerated 2D canvas
    # is slow enough that screenshots never get a frame; raster them on the CPU instead.
    "--disable-accelerated-2d-canvas",
]


def _unavailable(reason: str) -> NoReturn:
    if REQUIRED:
        pytest.fail(reason)
    pytest.skip(reason)


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        pass


@pytest.fixture(scope="module")
def site_server(tmp_path_factory: pytest.TempPathFactory):
    if not (DIST_DIR / "index.html").is_file():
        _unavailable(f"site/dist missing at {DIST_DIR}. Run npm run build in site/.")
    root = tmp_path_factory.mktemp("pages-root")
    (root / "coperceive").symlink_to(DIST_DIR, target_is_directory=True)
    port = find_free_port()
    handler = functools.partial(QuietHandler, directory=str(root))
    httpd = http.server.HTTPServer(("127.0.0.1", port), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    time.sleep(0.2)
    yield f"http://127.0.0.1:{port}{PREFIX}/"
    httpd.shutdown()


def _launch(playwright: Playwright) -> Browser:
    try:
        return playwright.chromium.launch(args=CHROME_ARGS)
    except Exception:
        chrome = os.environ.get("CHROME_PATH", "/usr/local/bin/google-chrome")
        try:
            return playwright.chromium.launch(executable_path=chrome, args=CHROME_ARGS)
        except Exception as exc:
            _unavailable(f"Failed to launch chromium: {exc}")


_CANVAS_JS = """(selector) => {
    const canvas = document.querySelector(selector);
    if (!canvas || canvas.width < 2 || canvas.height < 2) return null;
    const probe = document.createElement('canvas');
    probe.width = canvas.width;
    probe.height = canvas.height;
    const ctx = probe.getContext('2d');
    if (!ctx) return null;
    ctx.drawImage(canvas, 0, 0);
    return ctx.getImageData(0, 0, probe.width, probe.height).data;
}"""


def _with_pixels(body: str) -> str:
    return "(selector) => {\n  const data = (" + _CANVAS_JS + ")(selector);\n" + body + "\n}"


def _as_int(value: object, selector: str) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    raise AssertionError(f"{selector} did not return an int")


def _pixel_count(page: Page, selector: str) -> int:
    script = _with_pixels(
        """
            if (!data) return -1;
            let n = 0;
            for (let i = 0; i < data.length; i += 16) {
                if (data[i] + data[i + 1] + data[i + 2] > 18) n += 1;
            }
            return n;
        """
    )
    return _as_int(page.evaluate(script, selector), selector)


def _hash(page: Page, selector: str) -> int:
    script = _with_pixels(
        """
            if (!data) return 0;
            let h = 2166136261;
            for (let i = 0; i < data.length; i += 64) {
                h ^= data[i] + data[i + 1] * 3 + data[i + 2] * 7;
                h = Math.imul(h, 16777619);
            }
            return h >>> 0;
        """
    )
    return _as_int(page.evaluate(script, selector), selector)


REPO = "https://github.com/adarsh-nl/coperceive"
INSTALL = (
    '$ pip install -e ".[app,griffin]"\n'
    "$ coperceive convert griffin --subset 25m --raw-root ./griffin-release --out ./scenes\n"
    "$ coperceive serve --root ./scenes\n"
    "→ http://127.0.0.1:8731"
)


SHOTS = os.environ.get("COPERCEIVE_E2E_SHOTS", "")


def _shot(page: Page, tmp_path: Path, name: str) -> None:
    """Viewport screenshot to tmp_path, and to COPERCEIVE_E2E_SHOTS when that is set."""
    page.screenshot(path=str(tmp_path / f"{name}.png"), full_page=False)
    if SHOTS:
        Path(SHOTS).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(SHOTS) / f"{name}.png"), full_page=False)


STATS_HAS = "(t) => document.querySelector('[data-eval-stats]').textContent.includes(t)"


def _axe_serious(page: Page) -> list[tuple[object, object]]:
    assert AXE_PATH.is_file()
    page.evaluate(AXE_PATH.read_text())
    report = cast(
        dict[str, object],
        page.evaluate(
            """() => axe.run({
                runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] }
            })"""
        ),
    )
    violations_obj = report.get("violations", [])
    if not isinstance(violations_obj, list):
        raise AssertionError("axe violations missing")
    serious: list[tuple[object, object]] = []
    for item in cast(list[object], violations_obj):
        if isinstance(item, dict):
            row = cast(dict[str, object], item)
            if row.get("impact") in {"serious", "critical"}:
                serious.append((row.get("id"), row.get("impact")))
    return serious


def _scroll_story(page: Page, step: float) -> None:
    """Scroll so the pinned story's progress is ``step`` of 5 (0 = hero, 4.5 = last caption)."""
    page.evaluate(
        """(step) => {
            const el = document.querySelector('[data-screen-label=Story]');
            const span = el.offsetHeight - window.innerHeight;
            window.scrollTo(0, el.offsetTop + span * step / 5);
        }""",
        step,
    )
    page.wait_for_timeout(250)


def _opacity(page: Page, selector: str) -> float:
    value = page.evaluate(
        "(s) => Number(getComputedStyle(document.querySelector(s)).opacity)", selector
    )
    assert isinstance(value, (int, float))
    return float(value)


def test_landing_motion_story_sections_and_axe(site_server: str, tmp_path: Path):
    with sync_playwright() as playwright:
        browser = _launch(playwright)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        errors: list[str] = []
        requests: list[str] = []

        def on_error(err: BaseException) -> None:
            errors.append(str(err))

        def on_console(msg: ConsoleMessage) -> None:
            if msg.type == "error":
                errors.append(msg.text)

        page.on("pageerror", on_error)
        page.on("console", on_console)
        page.on("request", lambda req: requests.append(req.url))

        response = page.goto(site_server, wait_until="networkidle")
        assert response is not None and response.status == 200
        # Fonts and scripts are self-hosted: nothing is fetched from another origin.
        origin = site_server.split("/coperceive/")[0]
        assert [u for u in requests if not u.startswith(origin)] == []

        assert page.locator("h1").inner_text() == "Many agents.\nOne scene."
        nav = page.locator("header nav a")
        assert nav.all_inner_texts() == ["Pipeline", "Install", "GitHub"]
        assert nav.nth(2).get_attribute("href") == REPO
        assert page.get_by_role("link", name="View on GitHub").get_attribute("href") == REPO
        docs = page.get_by_role("link", name="Read the docs").get_attribute("href")
        assert docs == f"{REPO}/tree/main/docs"

        # The story canvas draws and moves.
        page.wait_for_selector("canvas[data-bev=hero]", timeout=15000)
        page.wait_for_timeout(600)
        assert _pixel_count(page, "canvas[data-bev=hero]") > 20
        hero_a = _hash(page, "canvas[data-bev=hero]")
        page.wait_for_timeout(500)
        assert hero_a != _hash(page, "canvas[data-bev=hero]")
        _shot(page, tmp_path, "landing-1440-hero")

        # Scrolling the pinned story swaps the captions and fills the progress bar.
        _scroll_story(page, 2.5)
        assert _opacity(page, "[data-step='2']") > 0.9
        assert _opacity(page, "[data-step='1']") < 0.1
        assert page.locator("[data-step='2']").get_attribute("aria-hidden") == "false"
        bar = page.evaluate(
            "() => parseFloat(document.querySelector('[data-progress]').style.height)"
        )
        assert isinstance(bar, (int, float)) and 40 < float(bar) < 60
        _shot(page, tmp_path, "landing-1440-story-2")
        _scroll_story(page, 4.6)
        page.wait_for_timeout(1500)
        _shot(page, tmp_path, "landing-1440-story-4")

        # Pipeline cards and the install block, which types itself out.
        assert page.locator("#pipeline h3").all_inner_texts() == [
            "Any dataset, one manifest",
            "Check before you train",
            "Every agent in 3D",
            "Score in the world frame",
        ]
        page.locator("#pipeline").scroll_into_view_if_needed()
        page.wait_for_timeout(1200)
        _shot(page, tmp_path, "landing-1440-pipeline")
        page.locator("#install").scroll_into_view_if_needed()
        # It starts holding the full text; typing begins once it is in view.
        page.wait_for_function(
            "() => document.querySelector('[data-term]').textContent.endsWith('▍')", timeout=10000
        )
        page.wait_for_function(
            "(t) => document.querySelector('[data-term]').textContent === t",
            arg=INSTALL,
            timeout=10000,
        )
        _shot(page, tmp_path, "landing-1440-install")

        # The evaluate demo answers its toggles.
        page.locator("#evaluate").scroll_into_view_if_needed()
        page.wait_for_function(
            STATS_HAS,
            arg="det-v1 · IoU ≥ 0.5",
            timeout=10000,
        )
        page.get_by_role("button", name="IoU ≥ 0.7").click()
        page.get_by_role("button", name="det-v2").click()
        page.wait_for_function(
            STATS_HAS,
            arg="det-v2 · IoU ≥ 0.7",
            timeout=5000,
        )
        assert page.get_by_role("button", name="det-v2").get_attribute("aria-pressed") == "true"
        assert _pixel_count(page, "canvas[data-eval]") > 20
        page.evaluate("() => window.scrollBy(0, window.innerHeight * 0.8)")
        page.wait_for_timeout(1500)
        _shot(page, tmp_path, "landing-1440-evaluate")

        for anchor in ("#pipeline", "#install"):
            assert page.locator(anchor).count() == 1

        # A fresh load: the skip link is the first tab stop.
        page.goto(site_server, wait_until="networkidle")
        page.keyboard.press("Tab")
        assert (
            page.evaluate("() => document.activeElement?.textContent?.trim()") == "Skip to content"
        )
        assert (
            page.evaluate("() => getComputedStyle(document.activeElement).outlineStyle") != "none"
        )

        assert _axe_serious(page) == []

        page.set_viewport_size({"width": 375, "height": 812})
        page.goto(site_server, wait_until="networkidle")
        page.wait_for_timeout(400)
        overflow = page.evaluate("() => document.documentElement.scrollWidth - window.innerWidth")
        assert isinstance(overflow, (int, float)) and overflow <= 0
        page.wait_for_selector("canvas[data-bev=hero]", timeout=15000)
        page.wait_for_timeout(800)
        _shot(page, tmp_path, "landing-375-hero")
        assert errors == [], errors
        browser.close()


def test_reduced_motion_holds_still(site_server: str):
    with sync_playwright() as playwright:
        browser = _launch(playwright)
        page = browser.new_page(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
        page.goto(site_server, wait_until="networkidle")
        page.wait_for_selector("canvas[data-bev=hero]", timeout=15000)
        page.wait_for_timeout(600)
        assert _pixel_count(page, "canvas[data-bev=hero]") > 20
        # Sections are shown at once and the install block is not typed out.
        assert page.locator("[data-reveal][data-hidden]").count() == 0
        assert page.locator("[data-term]").text_content() == INSTALL
        assert (
            page.evaluate(
                "() => getComputedStyle(document.querySelector('.lm-cursor')).animationName"
            )
            == "none"
        )
        browser.close()
