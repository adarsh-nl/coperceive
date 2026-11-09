# SPDX-License-Identifier: Apache-2.0
# pyright: reportPrivateUsage=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
"""Open a demo bundle read-only in the browser: frames, boxes and scores."""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
import uvicorn
from coperceive.app.server import create_app
from coperceive.bundle import BundleExportRequest, export_bundle, open_bundle
from coperceive.demo import create

from tests.test_viewer_browser import _bundle_dir, _unavailable

try:
    from playwright.sync_api import Browser, sync_playwright
except ImportError:  # pragma: no cover
    pytest.skip("playwright is not installed", allow_module_level=True)


@pytest.fixture(scope="module")
def browser() -> Iterator[Browser]:
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(headless=True)
        except Exception as exc:  # browser binary absent in unit-test jobs
            _unavailable(f"Chromium cannot start: {exc}")
        yield browser
        browser.close()


def _port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture(scope="module")
def bundle_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = create(tmp_path_factory.mktemp("bundle-browser"), seed=0).root
    archive = root / "demo.cpbundle"
    export_bundle(
        BundleExportRequest(
            run_id="demo",
            scenes_root=root / "scenes",
            predictions_root=root / "predictions",
            out=archive,
        )
    )
    return open_bundle(archive, data_root=tmp_path_factory.mktemp("bundle-open")).root


def test_demo_bundle_opens_read_only_and_shows_results(browser: Browser, bundle_root: Path) -> None:
    bundle = _bundle_dir()
    if bundle is None:
        _unavailable("no web bundle")
    app = create_app(
        root=bundle_root / "scenes",
        token=None,
        predictions=bundle_root / "predictions",
        static=bundle,
    )
    port = _port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 20
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    page.add_init_script("window.__COPERCEIVE_TEST_ENV__ = true;")
    try:
        page.goto(f"http://127.0.0.1:{port}/#/viewer", wait_until="networkidle")
        page.wait_for_selector("#scene-id", timeout=30000)
        body = page.locator("body").inner_text()
        assert "demo-junction" in body
        assert "mAP" in body or "Predictions" in body
        for editable in ("Submit", "Re-run", "Save plan"):
            assert page.get_by_role("button", name=editable, exact=True).count() == 0
    finally:
        page.close()
        server.should_exit = True
