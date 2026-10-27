# SPDX-License-Identifier: Apache-2.0
"""Landing page at phone, tablet and desktop widths: layout, contrast, axe, skip link, motion."""

from __future__ import annotations

import functools
import http.server
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import cast

import pytest

from tests.test_landing_page import AXE_PATH, CHROME_ARGS, DIST_DIR, PREFIX, REQUIRED, QuietHandler

try:
    from playwright.sync_api import Browser, Page, Playwright, sync_playwright
except ImportError:
    pytest.skip("playwright is not installed", allow_module_level=True)

VIEWPORTS = [(375, 812), (768, 1024), (1440, 900)]

# Records every getContext call, so reduced motion can prove no WebGL context was made.
CONTEXT_PROBE = """
(() => {
  window.__contexts = [];
  const original = HTMLCanvasElement.prototype.getContext;
  HTMLCanvasElement.prototype.getContext = function (kind, ...rest) {
    window.__contexts.push(String(kind));
    return original.call(this, kind, ...rest);
  };
})();
"""

# Adds agents to the legend chip the way a 4-agent clip would (three vehicles and a
# drone, then Lane). The chip is built at build time from the manifest; this copies
# its own entry markup so the test exercises the shipped CSS.
FOUR_AGENT_LEGEND = """() => {
  const chip = document.querySelector('[data-legend]');
  const entries = [...chip.querySelectorAll('[data-legend-entry]')];
  const template = entries[0];
  const labels = ['Vehicle 1', 'Vehicle 2', 'Vehicle 3', 'Drone', 'Lane'];
  const colours = ['#F58A1F', '#4CC9F0', '#8E6CEF', '#3C8DFF', '#7dcea0'];
  chip.replaceChildren(...labels.map((label, i) => {
    const node = template.cloneNode(true);
    node.lastChild.textContent = label;
    node.firstElementChild.style.backgroundColor = colours[i];
    return node;
  }));
  return chip.children.length;
}"""


def _unavailable(reason: str) -> None:
    if REQUIRED:
        pytest.fail(reason)
    pytest.skip(reason)


@pytest.fixture(scope="module")
def dist_url(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """``site/dist`` served under ``/coperceive/``, as GitHub Pages serves it."""
    if not (DIST_DIR / "index.html").is_file():
        _unavailable(f"site/dist missing at {DIST_DIR}. Run npm run build in site/.")
    root = tmp_path_factory.mktemp("pages-root")
    (root / PREFIX.strip("/")).symlink_to(DIST_DIR, target_is_directory=True)
    handler = functools.partial(QuietHandler, directory=str(root))
    httpd = http.server.HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}{PREFIX}/"
    httpd.shutdown()


def launch(playwright: Playwright) -> Browser:
    try:
        return playwright.chromium.launch(args=CHROME_ARGS)
    except Exception as exc:
        _unavailable(f"Failed to launch chromium: {exc}")
        raise


def _axe_serious(p: Page) -> list[tuple[object, object]]:
    p.evaluate(AXE_PATH.read_text())
    report = cast(
        dict[str, object],
        p.evaluate(
            """() => axe.run({
                runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'] }
            })"""
        ),
    )
    serious: list[tuple[object, object]] = []
    for item in cast(list[object], report.get("violations", [])):
        if isinstance(item, dict):
            row = cast(dict[str, object], item)
            if row.get("impact") in {"serious", "critical"}:
                serious.append((row.get("id"), row.get("impact")))
    return serious


@pytest.mark.parametrize(("width", "height"), VIEWPORTS)
def test_layout_contrast_and_axe(dist_url: str, tmp_path: Path, width: int, height: int):
    with sync_playwright() as playwright:
        browser = launch(playwright)
        page = browser.new_page(viewport={"width": width, "height": height})
        page.goto(dist_url, wait_until="networkidle")

        # No horizontal scroll, at the top and after scrolling through every section.
        overflow_js = "() => document.documentElement.scrollWidth - window.innerWidth"
        assert page.evaluate(overflow_js) <= 0
        page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(900)
        assert page.evaluate(overflow_js) <= 0
        page.evaluate("() => window.scrollTo(0, 0)")
        page.wait_for_timeout(200)

        # The logo sits inside the safe-area padding: at least 20 px from the top-left
        # edge, and its offsets are computed from env(safe-area-inset-*).
        box = page.locator("[data-logo]").bounding_box()
        assert box is not None and box["x"] >= 19.5 and box["y"] >= 19.5, box
        logo_class = page.locator("[data-logo]").get_attribute("class") or ""
        assert "safe-area-inset-left" in logo_class and "safe-area-inset-top" in logo_class
        viewport_meta = page.locator("meta[name=viewport]").get_attribute("content") or ""
        assert "viewport-fit=cover" in viewport_meta

        # The legend wraps inside the viewport with five entries (four agents and Lane).
        assert page.evaluate(FOUR_AGENT_LEGEND) == 5
        chip = page.locator("[data-legend]").bounding_box()
        assert chip is not None
        assert chip["x"] >= 0 and chip["x"] + chip["width"] <= width, chip
        clipped = page.evaluate(
            "() => [...document.querySelectorAll('[data-legend-entry]')].filter(e => {"
            " const r = e.getBoundingClientRect(), c = e.parentElement.getBoundingClientRect();"
            " return r.left < c.left - 0.5 || r.right > c.right + 0.5"
            " || e.scrollWidth > e.clientWidth + 0.5; }).length"
        )
        assert clipped == 0
        assert page.evaluate(overflow_js) <= 0
        page.locator("[data-legend]").screenshot(path=str(tmp_path / f"legend-{width}.png"))

        # AA contrast and the rest of axe's WCAG 2.1 A/AA rules: nothing serious or critical,
        # with every section revealed.
        page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(900)
        page.evaluate(
            "() => document.querySelectorAll('.reveal').forEach(e => e.classList.add('is-in'))"
        )
        page.wait_for_timeout(800)
        assert _axe_serious(page) == []
        browser.close()


def test_skip_link_and_waitlist_focus(dist_url: str):
    with sync_playwright() as playwright:
        browser = launch(playwright)
        page = browser.new_page(viewport={"width": 375, "height": 812})
        page.goto(dist_url, wait_until="networkidle")
        page.keyboard.press("Tab")
        skip = page.evaluate("() => document.activeElement?.textContent?.trim()")
        assert skip == "Skip to main content"
        outline = page.evaluate("() => getComputedStyle(document.activeElement).outlineStyle")
        assert outline != "none"
        page.keyboard.press("Enter")
        page.wait_for_timeout(200)
        assert page.evaluate("() => document.activeElement?.id") == "main-content"

        page.locator("#email").focus()
        assert page.evaluate("() => document.activeElement?.id") == "email"
        assert (
            page.evaluate("() => getComputedStyle(document.activeElement).outlineStyle") != "none"
        )
        page.keyboard.press("Tab")
        assert page.evaluate("() => document.activeElement?.textContent?.trim()") == (
            "Join the waitlist"
        )
        assert (
            page.evaluate("() => getComputedStyle(document.activeElement).outlineStyle") != "none"
        )
        browser.close()


@pytest.mark.parametrize(("width", "height"), VIEWPORTS)
def test_reduced_motion_poster_only(dist_url: str, width: int, height: int):
    with sync_playwright() as playwright:
        browser = launch(playwright)
        context = browser.new_context(
            viewport={"width": width, "height": height}, reduced_motion="reduce"
        )
        context.add_init_script(CONTEXT_PROBE)
        page = context.new_page()
        page.goto(dist_url, wait_until="networkidle")
        # Longer than the player's idle start (3.5 s plus idle), so a late start would show.
        page.wait_for_timeout(6000)
        assert page.locator("img[alt*='Recorded scene']").is_visible()
        assert page.locator("canvas").count() == 0
        contexts = page.evaluate("() => window.__contexts")
        assert not any("webgl" in str(kind) for kind in cast(list[object], contexts)), contexts
        browser.close()
