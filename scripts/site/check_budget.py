# SPDX-License-Identifier: Apache-2.0
"""Fail when the built landing page (``site/dist``) is over its size budget.

Sizes are gzip level 9 and kilobytes are 1000 bytes, as Vite reports them.

* first-load JS: every ``<script type="module" src>`` and ``<link rel="modulepreload">``
  in ``index.html``, gzip, at most 80 kB
* the ``RealClip`` chunk, gzip, at most 150 kB
* ``hero/clip.bin``, gzip, at most 1.5 MB
* ``hero/poster.webp``, as stored, at most 200 kB
* ``three`` and ``RealClip`` are not in first-load JS and not modulepreloaded

Usage: ``python scripts/site/check_budget.py [site/dist]``. Prints one line per budget.
"""

from __future__ import annotations

import gzip
import re
import sys
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path

DIST = Path(__file__).resolve().parents[2] / "site" / "dist"
BASE = "/coperceive/"

FIRST_LOAD_JS_GZIP = 80_000
REALCLIP_GZIP = 150_000
CLIP_BIN_GZIP = 1_500_000
POSTER_WEBP = 200_000

# three's own banner and a symbol only three defines; either in first-load JS means three is there.
THREE_MARKERS = (b"Copyright 2010-", b"WebGLRenderer")


@dataclass(frozen=True)
class Check:
    name: str
    value: int
    limit: int | None
    ok: bool

    def line(self) -> str:
        limit = f" (limit {self.limit:,})" if self.limit is not None else ""
        return f"{'ok  ' if self.ok else 'FAIL'} {self.name}: {self.value:,}{limit}"


class _Entries(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.scripts: list[str] = []
        self.preloads: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: v or "" for k, v in attrs}
        if tag == "script" and a.get("type") == "module" and a.get("src"):
            self.scripts.append(a["src"])
        if tag == "link" and a.get("rel") == "modulepreload" and a.get("href"):
            self.preloads.append(a["href"])


def _local(dist: Path, url: str) -> Path:
    if not url.startswith(BASE):
        raise ValueError(f"{url} is not under {BASE}")
    return dist / url[len(BASE) :]


def gz(data: bytes) -> int:
    return len(gzip.compress(data, compresslevel=9, mtime=0))


def check(dist: Path) -> list[Check]:
    parser = _Entries()
    parser.feed((dist / "index.html").read_text(encoding="utf-8"))
    first = [_local(dist, u) for u in [*parser.scripts, *parser.preloads]]
    first_bytes = [p.read_bytes() for p in first]
    first_gz = sum(gz(b) for b in first_bytes)

    chunks = sorted((dist / "assets").glob("RealClip-*.js"))
    if len(chunks) != 1:
        raise ValueError(f"expected one RealClip chunk, found {[c.name for c in chunks]}")
    realclip_gz = gz(chunks[0].read_bytes())

    preloaded = [Path(u).name for u in parser.preloads]
    bad_preload = [n for n in preloaded if re.search(r"three|RealClip", n, re.IGNORECASE)]
    three_in_first = [
        p.name
        for p, b in zip(first, first_bytes, strict=True)
        if any(m in b for m in THREE_MARKERS)
    ]

    clip_gz = gz((dist / "hero" / "clip.bin").read_bytes())
    poster = (dist / "hero" / "poster.webp").stat().st_size
    return [
        Check(
            "first-load JS gzip bytes", first_gz, FIRST_LOAD_JS_GZIP, first_gz <= FIRST_LOAD_JS_GZIP
        ),
        Check(
            "RealClip chunk gzip bytes", realclip_gz, REALCLIP_GZIP, realclip_gz <= REALCLIP_GZIP
        ),
        Check("clip.bin gzip bytes", clip_gz, CLIP_BIN_GZIP, clip_gz <= CLIP_BIN_GZIP),
        Check("poster.webp bytes", poster, POSTER_WEBP, poster <= POSTER_WEBP),
        Check("three or RealClip modulepreloaded (count)", len(bad_preload), 0, not bad_preload),
        Check(
            "first-load files containing three (count)", len(three_in_first), 0, not three_in_first
        ),
    ]


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    dist = Path(args[0]) if args else DIST
    results = check(dist)
    for result in results:
        print(result.line())
    return 0 if all(r.ok for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
