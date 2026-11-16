# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: E501
# pyright: reportPrivateUsage=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportArgumentType=false, reportAttributeAccessIssue=false, reportIndexIssue=false, reportOptionalMemberAccess=false, reportGeneralTypeIssues=false, reportUnknownParameterType=false, reportMissingParameterType=false
"""The three browser views of a shared bundle: included, local copy, not included."""

from __future__ import annotations

import hashlib
import json
import os
import socket
import threading
import time
from collections.abc import Generator, Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
import uvicorn
from coperceive.app.server import create_app
from coperceive.bundle import BundleExportRequest, export_bundle, open_bundle
from coperceive.demo import create

from tests.test_viewer_browser import _bundle_dir, _unavailable

try:
    from playwright.sync_api import Browser, Page, sync_playwright
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


@pytest.fixture(scope="module")
def demo_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return create(tmp_path_factory.mktemp("bundle-browser"), seed=0).root


@pytest.fixture(scope="module")
def included_bundle(demo_root: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    return export_bundle(
        BundleExportRequest(
            run_id="demo",
            scenes_root=demo_root / "scenes",
            predictions_root=demo_root / "predictions",
            out=tmp_path_factory.mktemp("bundle-included") / "demo.cpbundle",
            dataset_id="demo",
            include_sensor_data=True,
        )
    )


@pytest.fixture(scope="module")
def omitted_bundle(demo_root: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    return export_bundle(
        BundleExportRequest(
            run_id="demo",
            scenes_root=demo_root / "scenes",
            predictions_root=demo_root / "predictions",
            out=tmp_path_factory.mktemp("bundle-omitted") / "demo.cpbundle",
            dataset_id="demo",
        )
    )


def _port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@contextmanager
def _serve(bundle_root: Path) -> Generator[tuple[str, list[str]], None, None]:
    bundle = _bundle_dir()
    if bundle is None:
        _unavailable("no web bundle")
    app = create_app(
        root=bundle_root / "scenes",
        token=None,
        predictions=bundle_root / "predictions",
        static=bundle,
        bundle_info=bundle_root / "open.json",
    )
    port = _port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 20
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    requests: list[str] = []
    yield f"http://127.0.0.1:{port}", requests
    server.should_exit = True


def _open_page(browser: Browser, origin: str) -> Page:
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    requests: list[str] = []
    page._bundle_requests = requests  # type: ignore[attr-defined]
    page.add_init_script("window.__COPERCEIVE_TEST_ENV__ = true;")
    page.on("request", lambda request: requests.append(request.url))
    page.goto(f"{origin}/#/viewer", wait_until="networkidle")
    page.wait_for_selector("#scene-id", timeout=30000)
    page.wait_for_function(
        "() => (window.__COPERCEIVE_RENDERED_TIMES__ || []).length > 0", timeout=30000
    )
    return page


def _latest(page: Page) -> dict[str, object]:
    stats = page.evaluate("() => window.__COPERCEIVE_RENDERED_TIMES__ || []")
    return stats[-1] if stats else {}


def _requests(page: Page) -> list[str]:
    return list(page._bundle_requests)  # type: ignore[attr-defined]


def _screenshot(page: Page, name: str) -> None:
    shots = os.environ.get("COPERCEIVE_E2E_SHOTS")
    if shots:
        Path(shots).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(shots) / name), full_page=True)


def _manifest(bundle_root: Path) -> dict[str, object]:
    return json.loads((bundle_root / "bundle.json").read_text(encoding="utf-8"))


def _tree(root: Path) -> dict[str, int]:
    return {
        p.relative_to(root).as_posix(): p.stat().st_mtime_ns for p in root.rglob("*") if p.is_file()
    }


def _assert_extraction_intact(bundle_root: Path) -> None:
    manifest = _manifest(bundle_root)
    listed = {item["path"]: item for item in manifest["contents"]}
    extracted = {
        p.relative_to(bundle_root).as_posix() for p in bundle_root.rglob("*") if p.is_file()
    }
    assert extracted == set(listed) | {"bundle.json", "open.json"}
    for relative, item in listed.items():
        data = (bundle_root / relative).read_bytes()
        assert hashlib.sha256(data).hexdigest() == item["sha256"]
        assert len(data) == item["size"]


def test_bundle_view_included(browser: Browser, included_bundle: Path, tmp_path: Path) -> None:
    opened = open_bundle(included_bundle, data_root=tmp_path / "included")
    with _serve(opened.root) as (origin, _):
        page = _open_page(browser, origin)
        try:
            page.wait_for_function(
                "() => (window.__COPERCEIVE_RENDERED_TIMES__ || []).some((row) => (row.points || 0) > 0)",
                timeout=30000,
            )
            latest = _latest(page)
            assert int(latest.get("boxes") or 0) > 0
            body = page.locator("body").inner_text()
            manifest = _manifest(opened.root)
            assert "From a shared bundle" in body
            assert "Apache-2.0" in body
            assert str(manifest["rerun_command"]) in body
            assert page.get_by_role("button", name="Copy rerun").count() == 1
            assert "local copy" not in body.lower()
            assert "not included" not in body.lower()
            image_requests = [url for url in _requests(page) if "/image" in url]
            assert image_requests
            page.wait_for_function(
                "() => [...document.images].some((img) => img.naturalWidth > 0)", timeout=30000
            )
            for editable in ("Submit", "Re-run", "Save plan"):
                assert page.get_by_role("button", name=editable, exact=True).count() == 0
            _screenshot(page, "bundle-included.png")
        finally:
            page.close()


def test_bundle_view_local_copy(
    browser: Browser, demo_root: Path, omitted_bundle: Path, tmp_path: Path
) -> None:
    library = demo_root / "scenes"
    library_before = _tree(library)
    opened = open_bundle(omitted_bundle, data_root=tmp_path / "local", library_roots=[library])
    assert opened.report["sensor_data"] == "local-copy"
    with _serve(opened.root) as (origin, _):
        page = _open_page(browser, origin)
        try:
            page.wait_for_function(
                "() => (window.__COPERCEIVE_RENDERED_TIMES__ || []).some((row) => (row.points || 0) > 0)",
                timeout=30000,
            )
            body = page.locator("body").inner_text()
            label = opened.report["scenes"][0]["label"]
            assert label in body
            assert "local copy" in body.lower()
            assert page.get_by_role("button", name="Copy rerun").count() == 1
            assert "mAP" in body
            image_requests = [url for url in _requests(page) if "/image" in url]
            assert image_requests
            page.wait_for_function(
                "() => [...document.images].some((img) => img.naturalWidth > 0)", timeout=30000
            )
            # The bundle itself never gained local sensor files.
            assert not list((opened.root / "scenes/demo-junction/agents").rglob("*.png"))
            _assert_extraction_intact(opened.root)
            assert _tree(library) == library_before
            _screenshot(page, "bundle-local-copy.png")
        finally:
            page.close()


def test_bundle_view_not_included(browser: Browser, omitted_bundle: Path, tmp_path: Path) -> None:
    opened = open_bundle(omitted_bundle, data_root=tmp_path / "none")
    assert opened.report["sensor_data"] == "omitted"
    with _serve(opened.root) as (origin, _):
        page = _open_page(browser, origin)
        try:
            body = page.locator("body").inner_text()
            manifest = _manifest(opened.root)
            reason = str(manifest["sensor_data"]["reason"])
            assert reason in body
            assert "not included" in body.lower()
            latest = _latest(page)
            assert int(latest.get("points") or 0) == 0
            assert int(latest.get("boxes") or 0) > 0
            assert not [url for url in _requests(page) if "/image" in url]
            assert "mAP" in body
            assert str(manifest["rerun_command"]) in body
            assert page.get_by_role("button", name="Copy rerun").count() == 1
            _screenshot(page, "bundle-not-included.png")
        finally:
            page.close()
