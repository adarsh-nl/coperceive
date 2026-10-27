# SPDX-License-Identifier: Apache-2.0
"""``scripts/site/check_budget.py`` on synthetic builds, and the clip-after-poster order."""

from __future__ import annotations

import functools
import http.server
import os
import threading
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import cast

import pytest
from scripts.site import check_budget as budget

from tests.test_landing_page import CHROME_ARGS, DIST_DIR, REQUIRED, QuietHandler

INDEX = """<!doctype html><html><head>
<script type="module" crossorigin src="/coperceive/assets/index-a.js"></script>
{preload}
</head><body></body></html>"""


def _dist(
    root: Path,
    *,
    entry: bytes = b"console.log('page')",
    preload: str = "",
    poster: int = 1000,
) -> Path:
    dist = root / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "hero").mkdir()
    (dist / "index.html").write_text(INDEX.format(preload=preload), encoding="utf-8")
    (dist / "assets" / "index-a.js").write_bytes(entry)
    (dist / "assets" / "RealClip-b.js").write_bytes(b"/* Copyright 2010-2025 */ WebGLRenderer")
    (dist / "assets" / "three-c.js").write_bytes(b"WebGLRenderer")
    (dist / "hero" / "clip.bin").write_bytes(b"CPCVCLIP" + bytes(64))
    (dist / "hero" / "poster.webp").write_bytes(bytes(poster))
    return dist


def _failed(dist: Path) -> list[str]:
    return [c.name for c in budget.check(dist) if not c.ok]


def test_small_build_passes(tmp_path: Path) -> None:
    dist = _dist(tmp_path)
    assert _failed(dist) == []
    assert budget.main([str(dist)]) == 0


def test_preloading_three_fails(tmp_path: Path) -> None:
    link = '<link rel="modulepreload" crossorigin href="/coperceive/assets/three-c.js">'
    dist = _dist(tmp_path, preload=link)
    assert _failed(dist) == [
        "three or RealClip modulepreloaded (count)",
        "first-load files containing three (count)",
    ]
    assert budget.main([str(dist)]) == 1


def test_three_in_the_entry_fails(tmp_path: Path) -> None:
    dist = _dist(tmp_path, entry=b"/* Copyright 2010-2025 Three.js Authors */")
    assert _failed(dist) == ["first-load files containing three (count)"]


def test_large_entry_and_poster_fail(tmp_path: Path) -> None:
    dist = _dist(tmp_path, entry=os.urandom(90_000), poster=200_001)
    assert _failed(dist) == ["first-load JS gzip bytes", "poster.webp bytes"]


def test_real_build_is_within_budget() -> None:
    """The built site, when present (CI's landing job builds it before pytest)."""
    if not (budget.DIST / "index.html").is_file():
        pytest.skip("site/dist is not built")
    failed = _failed(budget.DIST)
    assert failed == [], failed


@contextmanager
def _serve(dist: Path, tmp: Path) -> Generator[str, None, None]:
    """``dist`` under ``/coperceive/``, as GitHub Pages serves it."""
    (tmp / "coperceive").symlink_to(dist, target_is_directory=True)
    handler = functools.partial(QuietHandler, directory=str(tmp))
    httpd = http.server.HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}/coperceive/"
    finally:
        httpd.shutdown()


def test_clip_is_fetched_after_the_poster_paints(tmp_path: Path) -> None:
    if not (DIST_DIR / "index.html").is_file():
        pytest.skip("site/dist is not built")
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        pytest.skip("playwright is not installed")

    with _serve(DIST_DIR, tmp_path) as url, sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(args=CHROME_ARGS)
        except Exception as exc:
            if REQUIRED:
                raise
            pytest.skip(f"Failed to launch chromium: {exc}")
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.add_init_script(
            "window.__lcp = 0; new PerformanceObserver(l => { for (const e of l.getEntries())"
            " window.__lcp = Math.max(window.__lcp, e.startTime); })"
            ".observe({ type: 'largest-contentful-paint', buffered: true });"
        )
        page.goto(url, wait_until="load")
        page.wait_for_selector("canvas[data-bev=hero]", timeout=20000)
        timing = cast(
            dict[str, float],
            page.evaluate(
                """() => {
                const r = performance.getEntriesByType('resource');
                const find = (s) => r.find(e => e.name.includes(s));
                const fcp = performance.getEntriesByName('first-contentful-paint')[0];
                return {
                  posterEnd: find('hero/poster.webp')?.responseEnd ?? -1,
                  clipStart: find('hero/clip.bin')?.startTime ?? -1,
                  fcp: fcp ? fcp.startTime : -1,
                  lcp: window.__lcp,
                };
            }"""
            ),
        )
        browser.close()
    assert timing["posterEnd"] > 0 and timing["clipStart"] > 0, timing
    assert timing["clipStart"] > timing["posterEnd"], timing
    assert timing["clipStart"] > timing["fcp"] > 0, timing
    assert timing["clipStart"] > timing["lcp"] > 0, timing
