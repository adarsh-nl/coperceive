# SPDX-License-Identifier: Apache-2.0
"""``scripts/site/render_poster.py``: og.png and the touch icon from the committed clip."""

from __future__ import annotations

import os
from pathlib import Path
from typing import cast

import numpy as np
import pytest
from PIL import Image
from scripts.site import render_poster as rp


def _open(path: Path) -> Image.Image:
    image = cast(Image.Image, Image.open(path))
    image.load()
    return image


REQUIRED = os.environ.get("COPERCEIVE_E2E_REQUIRED", "") not in {"", "0"}


@pytest.fixture(autouse=True)
def _fonts() -> None:
    if not (rp.GEIST.is_file() and rp.GEIST_MONO.is_file()):
        if REQUIRED:
            pytest.fail("Geist fonts missing: run npm ci in site/")
        pytest.skip("Geist fonts missing: run npm ci in site/")


def test_og_is_deterministic_and_within_budget(tmp_path: Path) -> None:
    a = rp.render_og(tmp_path / "a.png")
    b = rp.render_og(tmp_path / "b.png")
    assert a.read_bytes() == b.read_bytes()
    assert a.stat().st_size <= 300_000
    im = _open(a)
    assert im.size == (1200, 630)
    assert im.mode == "RGB"


def test_committed_og_matches_a_fresh_render(tmp_path: Path) -> None:
    fresh = rp.render_og(tmp_path / "og.png")
    a, b = _open(fresh), _open(rp.PUBLIC_DIR / "og.png").convert("RGB")
    assert a.size == b.size
    diff = np.abs(np.asarray(a, dtype=np.int16) - np.asarray(b, dtype=np.int16))
    # Font rasterising can differ by a level between FreeType builds; the scene must not.
    assert float(diff.mean()) < 0.5, float(diff.mean())


def test_touch_icon(tmp_path: Path) -> None:
    out = rp.render_touch_icon(tmp_path / "icon.png")
    im = _open(out)
    assert im.size == (180, 180)
    assert im.mode == "RGB"  # opaque: iOS fills transparency with black or white
    assert im.getpixel((0, 0)) == (0, 0, 0)
    assert _open(rp.PUBLIC_DIR / "apple-touch-icon.png").size == (180, 180)


def test_index_links_the_images() -> None:
    html = (rp.SITE_DIR / "index.html").read_text(encoding="utf-8")
    assert '<link rel="apple-touch-icon" href="%BASE_URL%apple-touch-icon.png" />' in html
    assert 'content="https://adarsh-nl.github.io/coperceive/og.png"' in html
    assert '<meta property="og:image:width" content="1200" />' in html
    assert '<meta property="og:image:height" content="630" />' in html
