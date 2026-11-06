# SPDX-License-Identifier: Apache-2.0
"""Tests for scripts/desktop/write_latest_json.py updater manifest generator."""

from __future__ import annotations

import base64
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "write_latest_json", ROOT / "scripts" / "desktop" / "write_latest_json.py"
)
assert _spec is not None and _spec.loader is not None
wlj = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(wlj)


def make_mock_minisign_signature(filename: str, version: str = "0.1.0") -> str:
    """Produce a mock 4-line minisign signature encoded in base64."""
    raw = (
        "untrusted comment: signature from tauri secret key\n"
        "RURe/UQ5KO0ICYPzAZcQLEuQFmcnZYzmfBaXmR2Y9srGS6KtKCSbosfxeqvEO7Ntf0EXzNzR+XzfnQ+g/PCgnIOQSAMSHtoTagk=\n"
        f"trusted comment: timestamp:1793928796\tfile:{filename}\tversion:{version}\n"
        "l/+HFPkB6keJtLLq4IMwkJhCi0EKc5pq3sunUvTQFddC6IyA1TZa4R3vvSdmLecKa8IINOOhKHq5zwSZiMivAg==\n"
    )
    return base64.b64encode(raw.encode("utf-8")).decode("utf-8")


def create_mock_assets(assets_dir: Path, version: str = "0.1.0") -> dict[str, str]:
    """Create a complete set of mock updater assets for all 4 platforms."""
    files = {
        "Coperceive_aarch64.app.tar.gz": make_mock_minisign_signature(
            "Coperceive_aarch64.app.tar.gz", version
        ),
        "Coperceive_x64.app.tar.gz": make_mock_minisign_signature(
            "Coperceive_x64.app.tar.gz", version
        ),
        f"Coperceive_{version}_amd64.AppImage": make_mock_minisign_signature(
            f"Coperceive_{version}_amd64.AppImage", version
        ),
        f"Coperceive_{version}_x64-setup.exe": make_mock_minisign_signature(
            f"Coperceive_{version}_x64-setup.exe", version
        ),
    }
    for filename, sig in files.items():
        (assets_dir / filename).write_bytes(f"mock-archive-data-for-{filename}".encode())
        (assets_dir / f"{filename}.sig").write_text(sig + "\n")
    return files


def test_the_4_platform_urls_are_distinct_and_exist_in_assets(tmp_path: Path) -> None:
    """All 4 platform URLs must be distinct and point to existing asset files."""
    conf = tmp_path / "tauri.conf.json"
    conf.write_text(json.dumps({"version": "0.1.0"}))
    assets = tmp_path / "assets"
    assets.mkdir()
    expected_files = create_mock_assets(assets, "0.1.0")

    out = tmp_path / "latest.json"
    argv = [
        "--conf",
        str(conf),
        "--tag",
        "v0.1.0",
        "--assets",
        str(assets),
        "--repo",
        "adarsh-nl/coperceive",
        "--out",
        str(out),
        "--require-all",
    ]
    assert wlj.main(argv) == 0

    manifest = json.loads(out.read_text())
    assert manifest["version"] == "0.1.0"
    platforms = manifest["platforms"]
    assert set(platforms.keys()) == {
        "darwin-aarch64",
        "darwin-x86_64",
        "windows-x86_64",
        "linux-x86_64",
    }

    urls = [p["url"] for p in platforms.values()]
    assert len(urls) == len(set(urls)), f"URLs must be distinct: {urls}"

    for plat_name, plat_data in platforms.items():
        url = plat_data["url"]
        filename = url.split("/")[-1]
        assert (assets / filename).is_file(), f"Asset {filename} for {plat_name} must exist on disk"
        assert filename in expected_files

        # Verify signature trusted comment matches the filename
        sig_text = plat_data["signature"]
        trusted_file = wlj.extract_trusted_comment_file(sig_text)
        assert trusted_file == filename, (
            f"Trusted comment file '{trusted_file}' must match asset filename '{filename}'"
        )


def test_signature_trusted_comment_file_must_equal_filename(tmp_path: Path) -> None:
    """A signature whose trusted comment file: does not match the archive is rejected."""
    conf = tmp_path / "tauri.conf.json"
    conf.write_text(json.dumps({"version": "0.1.0"}))
    assets = tmp_path / "assets"
    assets.mkdir()
    create_mock_assets(assets, "0.1.0")

    # Tamper with Intel signature to claim it signs Coperceive.app.tar.gz instead
    # of Coperceive_x64.app.tar.gz
    bad_sig = make_mock_minisign_signature("Coperceive.app.tar.gz", "0.1.0")
    (assets / "Coperceive_x64.app.tar.gz.sig").write_text(bad_sig + "\n")

    out = tmp_path / "latest.json"
    argv = [
        "--conf",
        str(conf),
        "--tag",
        "v0.1.0",
        "--assets",
        str(assets),
        "--repo",
        "adarsh-nl/coperceive",
        "--out",
        str(out),
        "--require-all",
    ]
    with pytest.raises(SystemExit) as exc_info:
        wlj.main(argv)
    assert "does not match archive" in str(exc_info.value) or exc_info.value.code != 0


def test_missing_intel_archive_fails_without_falling_back(tmp_path: Path) -> None:
    """If Intel macOS archive is missing, it must not fall back to arm64; require-all must fail."""
    conf = tmp_path / "tauri.conf.json"
    conf.write_text(json.dumps({"version": "0.1.0"}))
    assets = tmp_path / "assets"
    assets.mkdir()
    create_mock_assets(assets, "0.1.0")

    # Remove the Intel archive
    (assets / "Coperceive_x64.app.tar.gz").unlink()
    (assets / "Coperceive_x64.app.tar.gz.sig").unlink()

    out = tmp_path / "latest.json"
    argv = [
        "--conf",
        str(conf),
        "--tag",
        "v0.1.0",
        "--assets",
        str(assets),
        "--repo",
        "adarsh-nl/coperceive",
        "--out",
        str(out),
        "--require-all",
    ]
    assert wlj.main(argv) == 1
    # Ensure no latest.json was written with arm64 falling back to intel
    if out.exists():
        manifest = json.loads(out.read_text())
        assert "darwin-x86_64" not in manifest.get("platforms", {})


def test_two_platforms_resolving_to_same_file_raises_error(tmp_path: Path) -> None:
    """If two platforms somehow resolve to the same file, it must be refused."""
    conf = tmp_path / "tauri.conf.json"
    conf.write_text(json.dumps({"version": "0.1.0"}))
    assets = tmp_path / "assets"
    assets.mkdir()

    # Symlink Intel archive to arm64 archive
    (assets / "Coperceive_aarch64.app.tar.gz").write_bytes(b"content")
    (assets / "Coperceive_aarch64.app.tar.gz.sig").write_text(
        make_mock_minisign_signature("Coperceive_aarch64.app.tar.gz") + "\n"
    )
    (assets / "Coperceive_x64.app.tar.gz").symlink_to(assets / "Coperceive_aarch64.app.tar.gz")
    (assets / "Coperceive_x64.app.tar.gz.sig").write_text(
        make_mock_minisign_signature("Coperceive_x64.app.tar.gz") + "\n"
    )

    with pytest.raises(SystemExit) as exc_info:
        wlj.find_platform_archives(assets, "0.1.0")
    assert "resolved to the same file" in str(exc_info.value)
