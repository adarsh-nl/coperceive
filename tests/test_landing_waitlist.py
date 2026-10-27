# SPDX-License-Identifier: Apache-2.0
"""Waitlist states in the built page, with ``VITE_WAITLIST_ENDPOINT`` unset (as in CI)."""

from __future__ import annotations

import functools
import http.server
import threading
from collections.abc import Iterator
from typing import cast

import pytest

from tests.test_landing_page import CHROME_ARGS, DIST_DIR, PREFIX, REQUIRED, QuietHandler

try:
    from playwright.sync_api import Page, sync_playwright
except ImportError:
    pytest.skip("playwright is not installed", allow_module_level=True)


def _unavailable(reason: str) -> None:
    if REQUIRED:
        pytest.fail(reason)
    pytest.skip(reason)


@pytest.fixture(scope="module")
def dist_url(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    if not (DIST_DIR / "index.html").is_file():
        _unavailable(f"site/dist missing at {DIST_DIR}. Run npm run build in site/.")
    root = tmp_path_factory.mktemp("pages-root")
    (root / PREFIX.strip("/")).symlink_to(DIST_DIR, target_is_directory=True)
    handler = functools.partial(QuietHandler, directory=str(root))
    httpd = http.server.HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}{PREFIX}/"
    httpd.shutdown()


def _status(page: Page) -> str:
    return page.locator("#waitlist-status").inner_text().strip()


def test_waitlist_without_an_endpoint_sends_and_stores_nothing(dist_url: str) -> None:
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(args=CHROME_ARGS)
        except Exception as exc:
            _unavailable(f"Failed to launch chromium: {exc}")
            raise
        # Reduced motion keeps the hero player (and its clip request) out of the window.
        context = browser.new_context(
            viewport={"width": 1440, "height": 900}, reduced_motion="reduce"
        )
        page = context.new_page()
        page.goto(dist_url, wait_until="networkidle")
        page.wait_for_timeout(500)

        status = page.locator("#waitlist-status")
        assert status.get_attribute("role") == "status"
        assert status.get_attribute("aria-live") == "polite"

        # Invalid: inline message, the field is marked and keeps focus.
        page.get_by_role("button", name="Join the waitlist").click()
        assert _status(page) == "Enter an email address, like name@example.com."
        assert page.locator("#email").get_attribute("aria-invalid") == "true"
        assert page.evaluate("() => document.activeElement?.id") == "email"
        assert page.locator("form[data-status=invalid]").count() == 1

        # Typing clears it.
        page.locator("#email").press_sequentially("ada")
        assert _status(page) == ""
        assert page.locator("#email").get_attribute("aria-invalid") == "false"

        # A valid address with no endpoint configured: opens soon, no request of any kind.
        requests: list[str] = []
        page.on("request", lambda req: requests.append(f"{req.method} {req.url}"))
        page.locator("#email").fill("ada@example.com")
        page.get_by_role("button", name="Join the waitlist").click()
        page.wait_for_timeout(1500)
        assert _status(page) == "Waitlist opens soon. Nothing was sent."
        assert page.locator("form[data-status=soon]").count() == 1
        assert requests == [], requests

        # Nothing stored: web storage, cookies and IndexedDB stay empty.
        stored = cast(
            dict[str, object],
            page.evaluate(
                """async () => ({
                local: localStorage.length,
                session: sessionStorage.length,
                cookie: document.cookie,
                idb: indexedDB.databases ? (await indexedDB.databases()).length : 0,
            })"""
            ),
        )
        assert stored == {"local": 0, "session": 0, "cookie": "", "idb": 0}, stored
        assert context.cookies() == []
        browser.close()
