# SPDX-License-Identifier: Apache-2.0
"""The crash notice in the browser build, with the startup crash injected.

After a crash, the desktop app relaunches with no session token, so the main
window shows the Library/HPC shell (ConnectScreen). The notice used to be
mounted only in the viewer shell, so it never appeared there (the Step 5i e2e
failure). These tests stub the Tauri bridge so ``diagnostics_startup_crash``
returns a synthetic record, then check the notice on every screen the main
window shows, that Share diagnostics opens the dialog there, that Dismiss is
remembered, and that the viewer window does not repeat the notice.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterator

import pytest
import uvicorn
from coperceive.app.server import create_app
from playwright.sync_api import Browser, Page, sync_playwright
from playwright.sync_api import Error as PlaywrightError

from tests import test_dataset_view_v2 as dataset_view
from tests import test_design_system_visual as visual

CRASH = {
    "schema": "coperceive-crash-1",
    "kind": "unclean-exit",
    "timestamp_unix": 1_700_000_123,
    "version": "0.1.0",
    "commit": "abc1234",
    "message": "Coperceive closed unexpectedly last time (the running marker was still present).",
    "location": None,
}

# The diagnostics commands of the Tauri bridge: the startup crash and a report. Every
# other command goes to the design-system fixture's own stub, so the shell renders.
TAURI_STUB = """(crash) => {
  window.__INVOKES__ = [];
  window.__TAURI_EVENT_PLUGIN_INTERNALS__ = window.__TAURI_EVENT_PLUGIN_INTERNALS__ || {
    unregisterListener: () => undefined,
  };
  const fixture = window.__TAURI_INTERNALS__ || {};
  const passOn = fixture.invoke ? fixture.invoke.bind(fixture) : async () => null;
  window.__TAURI_INTERNALS__ = {
    ...fixture,
    transformCallback: fixture.transformCallback || (() => 0),
    metadata: fixture.metadata || {
      currentWindow: { label: "main" }, currentWebview: { label: "main" },
    },
    invoke: async (cmd, args, options) => {
      window.__INVOKES__.push(cmd);
      if (cmd === "diagnostics_startup_crash") return crash;
      if (cmd === "diagnostics_build_report") {
        return { preview: "Coperceive Diagnostics\\ncrash-1700000123.json", crashes: 1, bytes: 40 };
      }
      // A fresh install: empty lists, no saved state.
      const lists = ["get_library_scenes", "list_server_profiles", "dataset_catalog"];
      if (lists.includes(cmd)) return [];
      return passOn(cmd, args, options);
    },
  };
}"""

NOTICE = ".crash-notice"


@pytest.fixture(scope="module")
def app_url(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    bundle = visual._bundle_dir()  # pyright: ignore[reportPrivateUsage]
    if bundle is None:
        visual._unavailable("no web bundle built in coperceive/app/static")  # pyright: ignore[reportPrivateUsage]
    app = create_app(root=tmp_path_factory.mktemp("crash_root"), token=None, static=bundle)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning"))
    thread = threading.Thread(target=server.run, name="crash-notice", daemon=True)
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


def _page(browser: Browser, extra_init: str = "") -> Page:
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    context.add_init_script(visual.RICH_FIXTURE_INIT_SCRIPT)
    context.add_init_script(f"({TAURI_STUB})({json.dumps(CRASH)});{extra_init}")
    return context.new_page()


def _notice(page: Page) -> str:
    page.wait_for_selector(NOTICE, timeout=10000)
    return page.locator(NOTICE).inner_text()


def test_the_notice_shows_on_the_library_after_a_crash(browser: Browser, app_url: str) -> None:
    """A relaunch with no session opens on the Library: the notice is there, and
    stays while the main window moves to the HPC workspace and a dataset view."""
    page = _page(browser)
    # The packaged app's relaunch has no session token, so App renders ConnectScreen
    # (the branch `#/connect` takes). Over plain HTTP the browser build always counts
    # as connected, so the test names that branch by its route.
    page.goto(f"{app_url}/#/connect", wait_until="networkidle")
    page.wait_for_selector(".c-sidebar", timeout=15000)
    assert "closed unexpectedly" in _notice(page)
    assert page.evaluate("window.__INVOKES__").count("diagnostics_startup_crash") == 1

    page.evaluate(visual.SEED_FIXTURES_EVAL)
    page.locator(".c-sidebar button", has_text="Servers").first.click()
    page.wait_for_timeout(300)
    assert "closed unexpectedly" in _notice(page)

    page.evaluate(dataset_view.DATASET_SEED)
    page.wait_for_selector("section[aria-label='Dataset view'] [role=grid]", timeout=5000)
    assert "closed unexpectedly" in _notice(page)
    page.context.close()


def test_share_diagnostics_opens_the_dialog_on_the_library(browser: Browser, app_url: str) -> None:
    page = _page(browser)
    page.goto(f"{app_url}/#/connect", wait_until="networkidle")
    _notice(page)
    page.locator(NOTICE).get_by_role("button", name="Share diagnostics…").click()
    page.wait_for_selector(".diagnostics-dialog[role=dialog]", timeout=5000)
    preview = page.locator(".diagnostics-preview")
    page.wait_for_function(
        "() => (document.querySelector('.diagnostics-preview') || {}).textContent"
        "?.includes('Coperceive Diagnostics')"
    )
    assert "crash-1700000123.json" in preview.inner_text()
    page.context.close()


def test_the_notice_shows_after_a_restored_session(browser: Browser, app_url: str) -> None:
    """With a session restored, the main window opens on the viewer shell."""
    page = _page(browser, "sessionStorage.setItem('coperceive-token', 'restored');")
    page.goto(f"{app_url}/", wait_until="networkidle")
    page.wait_for_selector("header.topbar", timeout=15000)
    assert "closed unexpectedly" in _notice(page)
    page.context.close()


def test_dismiss_is_remembered_for_that_crash(browser: Browser, app_url: str) -> None:
    page = _page(browser)
    page.goto(f"{app_url}/#/connect", wait_until="networkidle")
    _notice(page)
    page.locator(NOTICE).get_by_role("button", name="Dismiss").click()
    assert page.locator(NOTICE).count() == 0
    stored = page.evaluate(
        f"localStorage.getItem('coperceive-dismissed-crash-{CRASH['timestamp_unix']}')"
    )
    assert stored == "1"
    page.reload(wait_until="networkidle")
    page.wait_for_selector(".c-sidebar", timeout=15000)
    page.wait_for_timeout(500)
    assert page.locator(NOTICE).count() == 0
    page.context.close()


def test_the_viewer_window_does_not_repeat_the_notice(browser: Browser, app_url: str) -> None:
    page = _page(browser, "window.__COPERCEIVE_VIEWER_WINDOW__ = true;")
    page.goto(f"{app_url}/#/viewer", wait_until="networkidle")
    page.wait_for_selector("header.topbar", timeout=15000)
    page.wait_for_timeout(500)
    assert page.locator(NOTICE).count() == 0
    assert "diagnostics_startup_crash" not in page.evaluate("window.__INVOKES__")
    page.context.close()
