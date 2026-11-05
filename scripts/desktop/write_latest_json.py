#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Write the Tauri updater manifest (latest.json) for a GitHub Release.

The Tauri bundler produces update archives and corresponding ``.sig`` files when
``bundle.createUpdaterArtifacts`` is on, but it does not write ``latest.json``.
The app reads that file from
``https://github.com/<repo>/releases/latest/download/latest.json``.

The tag must match the bundle version: an app that still reports the old version
after updating would be offered the same update again on every check.

Platforms supported in the updater manifest:
- darwin-aarch64 (macOS Apple Silicon)
- darwin-x86_64  (macOS Intel)
- windows-x86_64 (Windows x86_64)
- linux-x86_64   (Linux x86_64)
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ALL_PLATFORMS = (
    "darwin-aarch64",
    "darwin-x86_64",
    "windows-x86_64",
    "linux-x86_64",
)


def classify_archive(path: Path) -> str | None:
    """Classify an update archive by target platform."""
    name = path.name
    if name.endswith(".sig") or name.endswith(".dmg") or name.endswith(".deb"):
        return None
    name_lower = name.lower()

    # Windows: .nsis.zip or .exe installer
    if (
        name.endswith(".nsis.zip")
        or name.endswith(".exe")
        or ("windows" in name_lower and name.endswith(".zip"))
    ):
        if (
            "x64" in name_lower
            or "x86_64" in name_lower
            or "setup" in name_lower
            or "windows" in name_lower
        ):
            return "windows-x86_64"

    # Linux: .AppImage or .AppImage.tar.gz
    if ".appimage" in name_lower or ("linux" in name_lower and name.endswith(".tar.gz")):
        if "amd64" in name_lower or "x86_64" in name_lower or "linux" in name_lower:
            return "linux-x86_64"

    # macOS: .app.tar.gz
    if name.endswith(".app.tar.gz") or (name.endswith(".tar.gz") and "darwin" in name_lower):
        if "x86_64" in name_lower or "x64" in name_lower or "intel" in name_lower:
            return "darwin-x86_64"
        if "aarch64" in name_lower or "arm64" in name_lower:
            return "darwin-aarch64"
        # If default Coperceive.app.tar.gz without arch qualifier, default to darwin-aarch64
        if name == "Coperceive.app.tar.gz":
            return "darwin-aarch64"

    return None


def find_platform_archives(assets: Path) -> dict[str, tuple[Path, Path]]:
    """Scan the assets directory for updater archives and signatures for each platform."""
    platforms: dict[str, tuple[Path, Path]] = {}
    for p in sorted(assets.iterdir()):
        if not p.is_file():
            continue
        platform = classify_archive(p)
        if platform is None:
            continue
        sig = p.with_name(p.name + ".sig")
        if not sig.is_file():
            raise SystemExit(
                f"Missing {sig.name}: the build did not sign updater archive {p.name}. "
                "Check TAURI_SIGNING_PRIVATE_KEY and TAURI_SIGNING_PRIVATE_KEY_PASSWORD."
            )
        platforms[platform] = (p, sig)
    return platforms


def build_manifest(
    *,
    version: str,
    platforms: dict[str, dict[str, str]],
    notes: str,
    pub_date: str,
) -> dict[str, object]:
    return {
        "version": version,
        "notes": notes,
        "pub_date": pub_date,
        "platforms": platforms,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--conf", type=Path, required=True, help="src-tauri/tauri.conf.json")
    parser.add_argument("--tag", required=True, help="release tag, e.g. v0.1.1")
    parser.add_argument(
        "--assets", type=Path, required=True, help="directory with updater archives and signatures"
    )
    parser.add_argument("--repo", required=True, help="owner/name")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--notes", default=None)
    parser.add_argument(
        "--require-all",
        action="store_true",
        help=(
            "require all 4 platforms (darwin-aarch64, darwin-x86_64, windows-x86_64, linux-x86_64)"
        ),
    )
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

    archives = find_platform_archives(args.assets)
    if not archives:
        print(f"No valid update archives found in {args.assets}", file=sys.stderr)
        return 1

    if args.require_all:
        missing = [plat for plat in ALL_PLATFORMS if plat not in archives]
        if missing:
            print(
                f"Missing updater archives for platform(s): {', '.join(missing)}",
                file=sys.stderr,
            )
            return 1

    platforms_map: dict[str, dict[str, str]] = {}
    for platform, (archive, sig) in sorted(archives.items()):
        signature = sig.read_text().strip()
        if not signature:
            print(f"{sig.name} is empty.", file=sys.stderr)
            return 1
        url = f"https://github.com/{args.repo}/releases/download/{args.tag}/{archive.name}"
        platforms_map[platform] = {"signature": signature, "url": url}

    notes = args.notes or f"See https://github.com/{args.repo}/releases/tag/{args.tag}"
    pub_date = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    manifest = build_manifest(
        version=version,
        platforms=platforms_map,
        notes=notes,
        pub_date=pub_date,
    )
    args.out.write_text(json.dumps(manifest, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
