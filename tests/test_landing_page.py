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


def test_landing_scroll_canvas_links_and_axe(site_server: str, tmp_path: Path):
    with sync_playwright() as playwright:
        browser = _launch(playwright)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        errors: list[str] = []

        def on_error(err: BaseException) -> None:
            errors.append(str(err))

        def on_console(msg: ConsoleMessage) -> None:
            if msg.type == "error":
                errors.append(msg.text)

        page.on("pageerror", on_error)
        page.on("console", on_console)

        response = page.goto(site_server, wait_until="networkidle")
        assert response is not None and response.status == 200
        assert page.locator("h1").inner_text() == "Every agent. One view."
        assert page.locator("header").count() == 0
        assert page.locator("[data-logo]").is_visible()
        logo_size = page.evaluate(
            "() => parseFloat(getComputedStyle("
            "document.querySelector('[data-logo] span')).fontSize)"
        )
        assert isinstance(logo_size, (int, float)) and float(logo_size) >= 28
        cursor_h = page.evaluate(
            "() => document.querySelector('.cursor-blink').getBoundingClientRect().height"
        )
        assert isinstance(cursor_h, (int, float)) and float(cursor_h) >= 12
        assert page.locator("button", has_text="Pause").count() == 0
        body = page.locator("body").inner_text()
        for banned in (
            "GitHub",
            "Griffin",
            "CARLA",
            "Town03",
            "OPV2V",
            "V2XSet",
            "V2V4Real",
            "Adarsh",
        ):
            assert banned not in body, banned
        assert "Coming soon." in body
        assert "Recorded scene, illustrative second vehicle" in body
        assert "Apache-2.0" not in body
        assert "OFL" not in body

        page.wait_for_selector("canvas[data-bev=hero]", timeout=15000)
        page.wait_for_timeout(400)
        hero_a = _hash(page, "canvas[data-bev=hero]")
        assert _pixel_count(page, "canvas[data-bev=hero]") > 20
        page.wait_for_timeout(500)
        hero_b = _hash(page, "canvas[data-bev=hero]")
        assert hero_a != hero_b

        logo_top = page.evaluate(
            "() => Number(getComputedStyle(document.querySelector('[data-logo]')).opacity)"
        )
        assert isinstance(logo_top, (int, float)) and float(logo_top) > 0.9
        page.evaluate("() => window.scrollTo(0, window.innerHeight)")
        page.wait_for_timeout(200)
        logo_fade = page.evaluate(
            "() => Number(getComputedStyle(document.querySelector('[data-logo]')).opacity)"
        )
        assert isinstance(logo_fade, (int, float)) and float(logo_fade) < 0.05
        assert (
            page.evaluate("() => getComputedStyle(document.querySelector('[data-logo]')).position")
            != "fixed"
        )
        assert (
            page.evaluate("() => getComputedStyle(document.querySelector('[data-logo]')).position")
            != "sticky"
        )

        page.locator("#email").fill("not-an-email")
        page.get_by_role("button", name="Join the waitlist").click()
        assert page.get_by_text("Enter an email address.").is_visible()
        posts: list[str] = []
        page.on("request", lambda req: posts.append(req.url) if req.method == "POST" else None)
        page.locator("#email").fill("ada@example.com")
        page.get_by_role("button", name="Join the waitlist").click()
        assert page.get_by_text("Waitlist opens soon.").is_visible()
        assert posts == []

        page.locator("#email").focus()
        email_outline = page.evaluate("() => getComputedStyle(document.activeElement).outlineStyle")
        assert email_outline != "none"
        page.keyboard.press("Tab")
        outline = page.evaluate("() => getComputedStyle(document.activeElement).outlineStyle")
        assert outline != "none"
        focused = page.evaluate(
            "() => (document.activeElement?.innerText"
            " || document.activeElement?.getAttribute('aria-label') || '')"
        )
        assert isinstance(focused, str) and "Join the waitlist" in focused
        seen: set[str] = set()
        label_js = (
            "() => (document.activeElement?.innerText"
            " || document.activeElement?.getAttribute('aria-label') || '')"
        )
        for _ in range(16):
            name = page.evaluate(label_js)
            if not isinstance(name, str):
                raise AssertionError("focused control has no name")
            seen.add(name.strip())
            page.keyboard.press("Tab")
        assert any("Join the waitlist" in item or item == "Email" for item in seen)
        assert not any("Pause" in item or "GitHub" in item for item in seen)

        hrefs = cast(
            list[object],
            page.eval_on_selector_all(
                "a[href]",
                "els => els.map(el => el.href)",
            ),
        )
        for href in hrefs:
            if not isinstance(href, str):
                raise AssertionError("link href is not a string")
            assert "github.com" not in href, href
            if href.startswith(site_server) or "127.0.0.1" in href:
                status = page.request.get(href).status
                assert status == 200, href

        assert AXE_PATH.is_file()
        page.evaluate(AXE_PATH.read_text())
        axe = page.evaluate(
            """() => axe.run({
                runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] }
            })"""
        )
        report = cast(dict[str, object], axe)
        violations_obj = report.get("violations", [])
        if not isinstance(violations_obj, list):
            raise AssertionError("axe violations missing")
        serious: list[tuple[object, object]] = []
        for item in cast(list[object], violations_obj):
            if not isinstance(item, dict):
                continue
            row = cast(dict[str, object], item)
            if row.get("impact") in {"serious", "critical"}:
                serious.append((row.get("id"), row.get("impact")))
        assert serious == [], serious

        assert page.locator("[data-pin=true]").count() == 0
        page.screenshot(path=str(tmp_path / "landing-1440.png"), full_page=False)
        page.set_viewport_size({"width": 375, "height": 812})
        page.goto(site_server, wait_until="networkidle")
        assert page.locator("[data-pin=true]").count() == 0
        page.screenshot(path=str(tmp_path / "landing-375.png"), full_page=False)
        assert errors == [], errors
        browser.close()


def test_reduced_motion_stills_without_pinning(site_server: str):
    with sync_playwright() as playwright:
        browser = _launch(playwright)
        page = browser.new_page(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
        page.goto(site_server, wait_until="networkidle")
        assert page.locator("[data-pin=true]").count() == 0
        assert page.locator("button", has_text="Pause").count() == 0
        assert page.locator("canvas[data-bev=hero]").count() == 0
        assert page.locator("img[alt*='Recorded scene']").is_visible()
        assert (
            page.evaluate(
                "() => getComputedStyle(document.querySelector('.cursor-blink')).animationName"
            )
            == "none"
        )
        browser.close()
