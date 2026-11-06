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
- darwin-aarch64 (macOS Apple Silicon): Coperceive_aarch64.app.tar.gz
- darwin-x86_64  (macOS Intel):         Coperceive_x64.app.tar.gz
- linux-x86_64   (Linux x86_64):        Coperceive_{version}_amd64.AppImage
- windows-x86_64 (Windows x86_64):      Coperceive_{version}_x64-setup.exe
"""

from __future__ import annotations

import argparse
import base64
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


def extract_trusted_comment_file(sig_content: str) -> str | None:
    """Parse trusted comment from a minisign signature and extract the file: parameter."""
    text = sig_content.strip()
    if not text.startswith("untrusted comment:"):
        try:
            decoded = base64.b64decode(text).decode("utf-8", errors="replace")
            if "trusted comment:" in decoded:
                text = decoded
        except Exception:
            pass
    for line in text.splitlines():
        if line.startswith("trusted comment:"):
            # Tokens separated by tabs or spaces
            for token in line.split("\t"):
                token = token.strip()
                if token.startswith("file:"):
                    return token[len("file:") :].strip()
            for token in line.split():
                token = token.strip()
                if token.startswith("file:"):
                    return token[len("file:") :].strip()
    return None


def get_expected_filenames(version: str) -> dict[str, list[str]]:
    """Return explicit expected archive filenames for each platform."""
    return {
        "darwin-aarch64": ["Coperceive_aarch64.app.tar.gz"],
        "darwin-x86_64": ["Coperceive_x64.app.tar.gz"],
        "linux-x86_64": [
            f"Coperceive_{version}_amd64.AppImage",
            f"coperceive_{version}_amd64.AppImage",
            f"Coperceive_{version}_amd64.AppImage.tar.gz",
            f"coperceive_{version}_amd64.AppImage.tar.gz",
        ],
        "windows-x86_64": [
            f"Coperceive_{version}_x64-setup.exe",
            f"Coperceive_{version}_x64-setup.nsis.zip",
        ],
    }


def find_platform_archives(assets: Path, version: str) -> dict[str, tuple[Path, Path]]:
    """Scan the assets directory for updater archives and signatures for each platform.

    Enforces strict mapping from explicit filenames and ensures no two platforms
    resolve to the same archive file.
    """
    expected = get_expected_filenames(version)
    platforms: dict[str, tuple[Path, Path]] = {}

    for platform, candidates in expected.items():
        for candidate_name in candidates:
            archive_path = assets / candidate_name
            if archive_path.is_file():
                sig_path = archive_path.with_name(archive_path.name + ".sig")
                if not sig_path.is_file():
                    raise SystemExit(
                        f"Missing {sig_path.name}: the build did not sign updater archive "
                        f"{archive_path.name}. Check TAURI_SIGNING_PRIVATE_KEY."
                    )
                platforms[platform] = (archive_path, sig_path)
                break

    # Guard: ensure no two platforms resolved to the same archive file
    resolved_paths: dict[Path, str] = {}
    for platform, (archive, _) in platforms.items():
        canonical = archive.resolve()
        if canonical in resolved_paths:
            raise SystemExit(
                f"Conflict: platforms '{resolved_paths[canonical]}' and '{platform}' "
                f"both resolved to the same file '{archive.name}'."
            )
        resolved_paths[canonical] = platform

    # Guard: verify signature trusted comment file: matches archive filename
    for platform, (archive, sig) in platforms.items():
        sig_text = sig.read_text().strip()
        if not sig_text:
            raise SystemExit(f"Signature file {sig.name} for platform {platform} is empty.")
        trusted_file = extract_trusted_comment_file(sig_text)
        if trusted_file is not None and trusted_file != archive.name:
            raise SystemExit(
                f"Signature verification mismatch for {platform}: signature {sig.name} "
                f"trusted comment specifies file '{trusted_file}', "
                f"but the archive is '{archive.name}'."
            )

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

    archives = find_platform_archives(args.assets, version)
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
