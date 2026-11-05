# SPDX-License-Identifier: Apache-2.0
"""Check that a built viewer bundle is complete and self-contained.

Used by CI and the packaging scripts in three places:

* ``--static coperceive/app/static`` after a local web build,
* ``--wheel dist/coperceive-*.whl`` to prove the wheel ships the viewer, and
* ``--app Coperceive.app`` to prove the packaged desktop app ships it too.

For each target it requires ``coperceive/app/static/index.html`` (or the static
directory itself), every local ``<script src>``/``<link href>`` asset that
index.html references, and at least one JS bundle. The release bundle must not
contain the Vite test-stage marker.
"""

from __future__ import annotations

import argparse
import sys
import zipfile
from html.parser import HTMLParser
from pathlib import Path

STAGE_MARKER = "__COPERCEIVE_STAGE__"
INDEX = "index.html"


class _AssetRefs(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.refs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key: value for key, value in attrs}
        if tag == "script" and values.get("src"):
            self.refs.append(values["src"] or "")
        elif tag == "link" and values.get("href"):
            self.refs.append(values["href"] or "")


def local_refs(html: str) -> list[str]:
    parser = _AssetRefs()
    parser.feed(html)
    refs: list[str] = []
    for ref in parser.refs:
        if ref.startswith(("http://", "https://", "data:", "#", "//")):
            continue
        refs.append(ref.lstrip("./"))
    return refs


def check_static(static_dir: Path, label: str) -> list[str]:
    index = static_dir / INDEX
    if not index.is_file():
        return [f"{label}: no {INDEX} in {static_dir}"]
    try:
        html = index.read_text(encoding="utf-8")
    except OSError as exc:
        return [f"{label}: cannot read {index}: {exc}"]
    refs = local_refs(html)
    problems: list[str] = []
    missing = [ref for ref in refs if not (static_dir / ref).is_file()]
    if missing:
        problems.append(f"{label}: index.html references missing files: {missing}")
    javascript = [ref for ref in refs if ref.endswith(".js")]
    if not javascript:
        problems.append(f"{label}: index.html references no JS bundle")
    for ref in javascript:
        path = static_dir / ref
        if path.is_file():
            try:
                if STAGE_MARKER in path.read_text(encoding="utf-8", errors="replace"):
                    problems.append(f"{label}: {ref} contains the test-stage marker")
            except OSError as exc:
                problems.append(f"{label}: cannot read {ref}: {exc}")
    return problems


def check_wheel(wheel: Path) -> list[str]:
    if not wheel.is_file():
        return [f"wheel not found: {wheel}"]
    problems: list[str] = []
    try:
        with zipfile.ZipFile(wheel) as archive:
            names = set(archive.namelist())
            index_member = next(
                (name for name in names if name.endswith("coperceive/app/static/index.html")),
                None,
            )
            if index_member is None:
                return [f"{wheel.name}: no coperceive/app/static/index.html"]
            prefix = index_member[: -len(INDEX)]
            html = archive.read(index_member).decode("utf-8")
            refs = local_refs(html)
            missing = [ref for ref in refs if prefix + ref not in names]
            if missing:
                problems.append(
                    f"{wheel.name}: index.html references files missing from the wheel: {missing}"
                )
            javascript = [ref for ref in refs if ref.endswith(".js")]
            if not javascript:
                problems.append(f"{wheel.name}: no JS bundle referenced by index.html")
            for ref in javascript:
                member = prefix + ref
                if member in names and STAGE_MARKER in archive.read(member).decode(
                    "utf-8", errors="replace"
                ):
                    problems.append(f"{wheel.name}: {ref} contains the test-stage marker")
    except (OSError, zipfile.BadZipFile) as exc:
        return [f"{wheel.name}: cannot read wheel: {exc}"]
    return problems


def check_app(app: Path) -> list[str]:
    if not app.is_dir():
        return [f"app bundle not found: {app}"]
    indices = sorted(app.rglob(f"coperceive/app/static/{INDEX}"))
    if not indices:
        return [f"{app.name}: no coperceive/app/static/{INDEX} inside the app"]
    problems: list[str] = []
    for index in indices:
        problems.extend(check_static(index.parent, f"{app.name}:{index.parent.name}"))
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--static", type=Path, help="built coperceive/app/static dir")
    target.add_argument("--wheel", type=Path, help="built wheel file")
    target.add_argument("--app", type=Path, help="packaged .app bundle")
    args = parser.parse_args(argv)

    if args.static is not None:
        problems = check_static(args.static, "static")
    elif args.wheel is not None:
        problems = check_wheel(args.wheel)
    else:
        assert args.app is not None
        problems = check_app(args.app)
    if problems:
        for problem in problems:
            print(f"bundle check failed: {problem}", file=sys.stderr)
        return 1
    print("viewer bundle check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
