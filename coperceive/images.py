# SPDX-License-Identifier: Apache-2.0
"""Image loading restricted to the formats Coperceive actually decodes.

Pillow has had decoder advisories in TIFF/PDF/FPX/JPEG2000 and other formats we
never read. Restricting the decoder surface is defence in depth; Coperceive
opens only PNG and JPEG camera files and writes JPEG previews.
"""

from __future__ import annotations

from pathlib import Path
from typing import IO, Any

from PIL import Image

# Formats accepted by every dataset/preview image load. Keep this list minimal.
ALLOWED_IMAGE_FORMATS = ("PNG", "JPEG")


def open_image(source: str | Path | IO[bytes], **kwargs: Any) -> Image.Image:
    """Open a PNG or JPEG; refuse every other Pillow decoder."""
    return Image.open(source, formats=ALLOWED_IMAGE_FORMATS, **kwargs)
