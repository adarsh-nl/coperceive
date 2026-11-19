# SPDX-License-Identifier: Apache-2.0
"""Pillow decoder surface: only PNG/JPEG are accepted."""

from __future__ import annotations

import io

import pytest
from coperceive.images import open_image
from PIL import Image, UnidentifiedImageError


def _encoded(fmt: str) -> io.BytesIO:
    buffer = io.BytesIO()
    Image.new("RGB", (2, 2), (10, 20, 30)).save(buffer, format=fmt)
    buffer.seek(0)
    return buffer


def test_png_and_jpeg_are_accepted() -> None:
    for fmt in ("PNG", "JPEG"):
        with open_image(_encoded(fmt)) as image:
            assert image.size == (2, 2)


def test_tiff_is_refused() -> None:
    with pytest.raises(UnidentifiedImageError):
        open_image(_encoded("TIFF"))
