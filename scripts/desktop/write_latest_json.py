#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Write the Tauri updater manifest (latest.json) for a GitHub Release.

The Tauri bundler produces ``Coperceive.app.tar.gz`` and its ``.sig`` when
``bundle.createUpdaterArtifacts`` is on, but it does not write ``latest.json``.
The app reads that file from
``https://github.com/<repo>/releases/latest/download/latest.json``.

The tag must match the bundle version: an app that still reports the old version
after updating would be offered the same update again on every check.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

PLATFORM = "darwin-aarch64"
ARCHIVE_SUFFIX = ".app.tar.gz"


def build_manifest(
    *, version: str, signature: str, url: str, notes: str, pub_date: str
) -> dict[str, object]:
    return {
        "version": version,
        "notes": notes,
        "pub_date": pub_date,
        "platforms": {PLATFORM: {"signature": signature, "url": url}},
    }


def find_archive(assets: Path) -> tuple[Path, Path]:
    archives = sorted(p for p in assets.iterdir() if p.name.endswith(ARCHIVE_SUFFIX))
    if len(archives) != 1:
        names = [p.name for p in archives]
        raise SystemExit(f"Expected exactly one *{ARCHIVE_SUFFIX} in {assets}, found {names}")
    archive = archives[0]
    sig = archive.with_name(archive.name + ".sig")
    if not sig.is_file():
        raise SystemExit(
            f"Missing {sig.name}: the build did not sign the updater archive. "
            "Check TAURI_SIGNING_PRIVATE_KEY and TAURI_SIGNING_PRIVATE_KEY_PASSWORD."
        )
    return archive, sig


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--conf", type=Path, required=True, help="src-tauri/tauri.conf.json")
    parser.add_argument("--tag", required=True, help="release tag, e.g. v0.1.1")
    parser.add_argument(
        "--assets", type=Path, required=True, help="directory with the .tar.gz and .sig"
    )
    parser.add_argument("--repo", required=True, help="owner/name")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--notes", default=None)
    args = parser.parse_args(argv)

    version = str(json.loads(args.conf.read_text())["version"])
    tag_version = args.tag[1:] if args.tag.startswith("v") else args.tag
    if tag_version != version:
        print(
            f"Tag {args.tag} does not match the bundle version {version}. "
            "Set the version (pyproject.toml / tauri.conf.json) before tagging.",
            file=sys.stderr,
        )
        return 1

    archive, sig = find_archive(args.assets)
    signature = sig.read_text().strip()
    if not signature:
        print(f"{sig.name} is empty.", file=sys.stderr)
        return 1

    url = f"https://github.com/{args.repo}/releases/download/{args.tag}/{archive.name}"
    notes = args.notes or f"See https://github.com/{args.repo}/releases/tag/{args.tag}"
    pub_date = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    manifest = build_manifest(
        version=version, signature=signature, url=url, notes=notes, pub_date=pub_date
    )
    args.out.write_text(json.dumps(manifest, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
