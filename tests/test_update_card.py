# SPDX-License-Identifier: Apache-2.0
"""The "update available" card at the bottom of the sidebar, in both themes.

The desktop controller is faked with Playwright routes: ``/updater-check`` offers
a synthetic 0.2.0 and ``/updater-install`` records the restart. The card sits
above Settings in the sidebar, in the blue accent on a tinted panel; Install
restarts into the update; What's new opens the notes; ✕ ("Later") hides it for
the session (a background check does not bring it back); the collapsed sidebar
shows a dot that opens the sidebar on the card; the viewer has no banner, only a
small "Update ready" in its top bar.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import pytest
import uvicorn
from coperceive.app.server import create_app
from playwright.sync_api import Browser, Page, Route, sync_playwright
from playwright.sync_api import Error as PlaywrightError

from tests import test_design_system_visual as visual

CTRL_PORT = 47919
NOTES = "Synthetic release notes for the update card test."


@pytest.fixture(scope="module")
def app_url(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    bundle = visual._bundle_dir()  # pyright: ignore[reportPrivateUsage]
    if bundle is None:
        visual._unavailable("no web bundle built in coperceive/app/static")  # pyright: ignore[reportPrivateUsage]
    app = create_app(root=tmp_path_factory.mktemp("update_root"), token=None, static=bundle)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning"))
    thread = threading.Thread(target=server.run, name="update-card", daemon=True)
    thread.start()
    deadline = time.monotonic() + 20
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    assert server.started, "uvicorn did not start"
    port = server.servers[0].sockets[0].getsockname()[1]
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=10)


@pytest.fixture(scope="module")
def browser() -> Iterator[Browser]:
    with sync_playwright() as playwright:
        try:
            launched = playwright.chromium.launch(
                headless=True,
                args=visual._CHROMIUM_ARGS,  # pyright: ignore[reportPrivateUsage]
            )
        except PlaywrightError as exc:
            visual._unavailable(f"chromium cannot start: {exc}")  # pyright: ignore[reportPrivateUsage]
        yield launched
        launched.close()


def _controller(calls: list[str]) -> Any:
    def handle(route: Route) -> None:
        path = route.request.url.split(str(CTRL_PORT), 1)[-1]
        calls.append(path)
        body: object = {}
        if path.startswith("/updater-check"):
            body = {"status": "available", "version": "0.2.0", "notes": NOTES}
        elif path.startswith("/updater-install"):
            body = {"status": "ok"}
        route.fulfill(
            status=200,
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Headers": "Authorization, Content-Type",
                "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
            },
            content_type="application/json",
            body=json.dumps(body),
        )

    return handle


def _page(browser: Browser, theme: str, calls: list[str], route: str = "#/connect") -> Page:
    context = browser.new_context(
        viewport={"width": 1440, "height": 900}, color_scheme=cast(Any, theme)
    )
    context.route(f"http://127.0.0.1:{CTRL_PORT}/**", _controller(calls))
    context.add_init_script(visual.RICH_FIXTURE_INIT_SCRIPT)
    context.add_init_script(
        f"window.__COPERCEIVE_CTRL_PORT__ = {CTRL_PORT}; window.__COPERCEIVE_CTRL_SECRET__ = 's';"
        # Outside Tauri the updater runs only in the test env with an update URL.
        f"window.__COPERCEIVE_UPDATE_URL__ = 'http://127.0.0.1:{CTRL_PORT}/latest.json';"
        f"localStorage.setItem('coperceive-theme-override', '{theme}');"
    )
    page = context.new_page()
    page.goto(f"{page_base(page, route)}", wait_until="networkidle")
    return page


_BASE: dict[str, str] = {}


def page_base(page: Page, route: str) -> str:
    return f"{_BASE['url']}/{route}"


def _rgb(page: Page, selector: str, prop: str) -> str:
    return cast(str, page.eval_on_selector(selector, f"(el) => getComputedStyle(el).{prop}"))


def _token(page: Page, name: str) -> str:
    """A design token resolved to the browser's rgb() form."""
    return cast(
        str,
        page.evaluate(
            """(name) => {
              const probe = document.createElement('span');
              probe.style.color = `var(${name})`;
              document.body.appendChild(probe);
              const out = getComputedStyle(probe).color;
              probe.remove();
              return out;
            }""",
            name,
        ),
    )


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_the_update_card_sits_above_settings_and_installs(
    browser: Browser, app_url: str, theme: str
) -> None:
    _BASE["url"] = app_url
    calls: list[str] = []
    page = _page(browser, theme, calls)
    try:
        card = page.locator(".c-sidebar .update-card")
        card.wait_for(timeout=10000)
        assert card.locator(".update-card-title").inner_text() == "New update available"
        assert "coperceive 0.2.0 is ready" in card.inner_text()
        # No top banner any more.
        assert page.locator("[role=banner][aria-label='Update ready']").count() == 0
        # At the bottom of the sidebar, just above Settings.
        settings = page.locator(".c-sidebar button[aria-label='Settings (⌘,)']")
        card_box, settings_box = card.bounding_box(), settings.bounding_box()
        assert card_box and settings_box
        assert card_box["y"] + card_box["height"] <= settings_box["y"] + 0.5, (
            card_box,
            settings_box,
        )
        assert settings_box["y"] - (card_box["y"] + card_box["height"]) < 24
        # Design tokens in this theme: blue accent text on a tinted panel, an accent pill.
        assert _rgb(page, ".update-card-title", "color") == _token(page, "--primary-hover")
        assert _rgb(page, ".update-card .update-pill", "backgroundColor") == _token(
            page, "--accent"
        )
        assert _rgb(page, ".update-card", "backgroundColor") not in (
            _token(page, "--panel"),
            "rgba(0, 0, 0, 0)",
        )
        assert _rgb(page, ".update-card", "borderTopLeftRadius") == "8px"
        page.screenshot(path=str(_shot_dir() / f"update-card-{theme}.png"))

        # What's new: the release notes in a popover from the card; Escape closes it.
        card.get_by_role("button", name="What's new").click()
        notes = page.locator(".update-notes[role=dialog]")
        notes.wait_for(timeout=3000)
        assert NOTES in notes.inner_text()
        page.screenshot(path=str(_shot_dir() / f"update-card-notes-{theme}.png"))
        page.keyboard.press("Escape")
        assert notes.count() == 0

        # Install restarts into the downloaded update (the controller's install).
        card.get_by_role("button", name="Install").click()
        page.wait_for_function("() => window.__COPERCEIVE_UPDATE_RESTART__ === true", timeout=5000)
        assert any(call.startswith("/updater-install") for call in calls), calls
    finally:
        page.context.close()


def test_later_hides_the_card_for_the_session_and_the_collapsed_sidebar_shows_a_dot(
    browser: Browser, app_url: str
) -> None:
    _BASE["url"] = app_url
    calls: list[str] = []
    page = _page(browser, "dark", calls)
    try:
        card = page.locator(".c-sidebar .update-card")
        card.wait_for(timeout=10000)

        # The collapsed sidebar: a blue dot on its toggle, "Update ready"; it opens the card.
        page.locator(".c-sidebar button[aria-label='Toggle sidebar (⌘\\\\)']").click()
        dot = page.locator("button.update-dot[aria-label='Update ready']")
        dot.wait_for(timeout=3000)
        page.wait_for_timeout(400)  # the sidebar's collapse transition
        assert dot.get_attribute("title") == "Update ready"
        assert _rgb(page, "button.update-dot span", "backgroundColor") == _token(page, "--accent")
        page.screenshot(path=str(_shot_dir() / "update-dot-collapsed.png"))
        dot.click()
        card.wait_for(timeout=3000)
        page.wait_for_function("() => document.activeElement?.id === 'update-card'", timeout=3000)
        assert dot.count() == 0

        # ✕ is "Later" (its accessible name, and its text for the e2e's text lookup).
        later = card.get_by_role("button", name="Later")
        assert "Later" in (later.text_content() or "")
        later.click()
        assert page.locator(".update-card").count() == 0
        # The background check finds the same version: it stays hidden this session.
        page.evaluate("() => window.__COPERCEIVE_CHECK_UPDATES__(false)")
        page.wait_for_timeout(500)
        assert page.locator(".update-card").count() == 0
        # Asking again (Help → Check for Updates) shows it.
        page.evaluate("() => window.__COPERCEIVE_CHECK_UPDATES__(true)")
        page.locator(".c-sidebar .update-card").wait_for(timeout=5000)
    finally:
        page.context.close()


def test_the_viewer_has_no_banner_only_update_ready(browser: Browser, app_url: str) -> None:
    _BASE["url"] = app_url
    calls: list[str] = []
    page = _page(browser, "light", calls, "#/viewer")
    try:
        ready = page.locator(".topbar .viewer-update-dot")
        ready.wait_for(timeout=10000)
        assert ready.inner_text() == "Update ready"
        assert page.locator(".update-card").count() == 0
        assert page.locator("[role=banner][aria-label='Update ready']").count() == 0
        # It shows the Library, where the card is (here: the same window's route).
        ready.click()
        page.locator(".c-sidebar .update-card").wait_for(timeout=10000)
    finally:
        page.context.close()


def _shot_dir() -> Path:
    """Screenshots go to ``COPERCEIVE_SHOTS`` when set (to look at them), else a temp folder."""
    wanted = os.environ.get("COPERCEIVE_SHOTS")
    out = Path(wanted) if wanted else Path(tempfile.gettempdir()) / "coperceive-update-card"
    out.mkdir(parents=True, exist_ok=True)
    return out
