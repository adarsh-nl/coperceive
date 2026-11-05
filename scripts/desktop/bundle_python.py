#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Bundle reproducible python-build-standalone runtime for Coperceive Desktop.

Supports four targets:
  - aarch64-apple-darwin (macOS Apple Silicon)
  - x86_64-apple-darwin (macOS Intel)
  - x86_64-pc-windows-msvc (Windows x86_64)
  - x86_64-unknown-linux-gnu (Linux x86_64)

Downloads the official standalone archive, verifies its published SHA-256
checksum, unpacks it into src-tauri/resources/python, installs locked
dependencies and the coperceive package, validates bundled assets, and
strips development/test files to produce a minimal, self-contained distribution.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import urllib.request
from pathlib import Path

PYTHON_RELEASE = "20250115"
PYTHON_VERSION_CPYTHON = "cpython-3.13.1"

TARGETS: dict[str, dict[str, str | bool]] = {
    "aarch64-apple-darwin": {
        "tarball": (
            f"{PYTHON_VERSION_CPYTHON}+{PYTHON_RELEASE}-aarch64-apple-darwin-install_only.tar.gz"
        ),
        "sha256": "d00ac75b15a05da2f6bc0057fe36f6944f9d513239f1c7af12766e03f288fe65",
        "lockfile": "requirements-darwin-arm64.lock",
        "is_windows": False,
        "bin_rel": "bin/python3",
        "site_packages_rel": "lib/python3.13/site-packages",
    },
    "x86_64-apple-darwin": {
        "tarball": (
            f"{PYTHON_VERSION_CPYTHON}+{PYTHON_RELEASE}-x86_64-apple-darwin-install_only.tar.gz"
        ),
        "sha256": "994c233cf86487b61283db63363ee969cf69dc551fba389c6d6d4e8534d4735f",
        "lockfile": "requirements-darwin-x86_64.lock",
        "is_windows": False,
        "bin_rel": "bin/python3",
        "site_packages_rel": "lib/python3.13/site-packages",
    },
    "x86_64-pc-windows-msvc": {
        "tarball": (
            f"{PYTHON_VERSION_CPYTHON}+{PYTHON_RELEASE}-x86_64-pc-windows-msvc-install_only.tar.gz"
        ),
        "sha256": "92021e1151d118db94689a38407248de96d8a2b8ffa9c4ae31b990441fda0aa0",
        "lockfile": "requirements-windows-x86_64.lock",
        "is_windows": True,
        "bin_rel": "python.exe",
        "site_packages_rel": "Lib/site-packages",
    },
    "x86_64-unknown-linux-gnu": {
        "tarball": (
            f"{PYTHON_VERSION_CPYTHON}+{PYTHON_RELEASE}-x86_64-unknown-linux-gnu-install_only.tar.gz"
        ),
        "sha256": "5e7d73b3144d51f15fca492c0f8afd9f690fd3e7bfa61e259beb3650d0336e1b",
        "lockfile": "requirements-linux-x86_64.lock",
        "is_windows": False,
        "bin_rel": "bin/python3",
        "site_packages_rel": "lib/python3.13/site-packages",
    },
}


def detect_host_target() -> str:
    if sys.platform == "darwin":
        machine = platform.machine().lower()
        if "arm" in machine or "aarch64" in machine:
            return "aarch64-apple-darwin"
        return "x86_64-apple-darwin"
    if sys.platform.startswith("win"):
        return "x86_64-pc-windows-msvc"
    if sys.platform.startswith("linux"):
        return "x86_64-unknown-linux-gnu"
    raise RuntimeError(f"Unsupported host platform: {sys.platform}")


def compute_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def download_file(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    temp_dest = dest.with_suffix(".tmp")
    req = urllib.request.Request(url, headers={"User-Agent": "coperceive-desktop-builder"})
    with urllib.request.urlopen(req) as resp, temp_dest.open("wb") as out:
        while chunk := resp.read(1024 * 1024):
            out.write(chunk)
    temp_dest.replace(dest)


def strip_bundle(python_dest: Path, is_windows: bool) -> None:
    print("Stripping test suites, build tools, and unused files from bundle...")
    # Clean up non-python binaries/scripts
    if not is_windows:
        bin_dir = python_dest / "bin"
        if bin_dir.is_dir():
            for item in bin_dir.iterdir():
                if not item.name.startswith("python"):
                    if item.is_dir():
                        shutil.rmtree(item, ignore_errors=True)
                    else:
                        item.unlink(missing_ok=True)
                elif item.name.endswith("-config"):
                    item.unlink(missing_ok=True)
                else:
                    try:
                        os.chmod(item, 0o755)
                    except OSError:
                        pass
    else:
        scripts_dir = python_dest / "Scripts"
        if scripts_dir.is_dir():
            for item in scripts_dir.iterdir():
                if not item.name.lower().startswith("python"):
                    if item.is_dir():
                        shutil.rmtree(item, ignore_errors=True)
                    else:
                        item.unlink(missing_ok=True)

    # Site packages & stdlib paths
    if is_windows:
        site_packages = python_dest / "Lib" / "site-packages"
        lib_dir = python_dest / "Lib"
    else:
        site_packages = python_dest / "lib" / "python3.13" / "site-packages"
        lib_dir = python_dest / "lib" / "python3.13"

    # Strip pip, ensurepip, setuptools, wheel
    if site_packages.is_dir():
        for prefix in ("pip", "setuptools", "wheel", "_distutils_hack"):
            for match in site_packages.glob(f"{prefix}*"):
                if match.is_dir():
                    shutil.rmtree(match, ignore_errors=True)
                else:
                    match.unlink(missing_ok=True)

    ensurepip = lib_dir / "ensurepip"
    if ensurepip.is_dir():
        shutil.rmtree(ensurepip, ignore_errors=True)

    # Strip standard library test suites
    for test_dir in (
        lib_dir / "test",
        lib_dir / "unittest" / "test",
        lib_dir / "ctypes" / "test",
        lib_dir / "sqlite3" / "test",
        lib_dir / "idlelib",
        lib_dir / "tkinter",
        lib_dir / "turtledemo",
    ):
        if test_dir.is_dir():
            shutil.rmtree(test_dir, ignore_errors=True)

    turtle_py = lib_dir / "turtle.py"
    turtle_py.unlink(missing_ok=True)

    # Strip packages test suites (preserve *.testing packages like numpy.testing)
    if site_packages.is_dir():
        for root, dirs, _files in os.walk(site_packages, topdown=False):
            for d in dirs:
                if d == "tests":
                    shutil.rmtree(Path(root) / d, ignore_errors=True)

    # Strip C headers and static libs
    include_dir = python_dest / "include"
    if include_dir.is_dir():
        shutil.rmtree(include_dir, ignore_errors=True)
    for root, dirs, files in os.walk(python_dest, topdown=False):
        for d in dirs:
            if d == "include":
                shutil.rmtree(Path(root) / d, ignore_errors=True)
        for f in files:
            if f.endswith(".a") or f.endswith(".pdb"):
                (Path(root) / f).unlink(missing_ok=True)

    # Strip bytecode caches
    for root, dirs, files in os.walk(python_dest, topdown=False):
        for d in dirs:
            if d == "__pycache__":
                shutil.rmtree(Path(root) / d, ignore_errors=True)
        for f in files:
            if f.endswith(".pyc") or f.endswith(".pyo"):
                (Path(root) / f).unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--target",
        default="auto",
        choices=["auto"] + list(TARGETS.keys()),
        help="Target architecture triple (default: auto)",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=None,
        help="Directory to cache downloaded Python archives",
    )
    parser.add_argument(
        "--dest-dir",
        type=Path,
        default=None,
        help="Destination directory for bundled python (default: src-tauri/resources/python)",
    )
    parser.add_argument(
        "--skip-web-build",
        action="store_true",
        help="Skip rebuilding the viewer bundle if already present",
    )
    args = parser.parse_args(argv)

    target = detect_host_target() if args.target == "auto" else args.target
    cfg = TARGETS[target]
    tarball = str(cfg["tarball"])
    expected_sha256 = str(cfg["sha256"])
    lockfile_name = str(cfg["lockfile"])
    is_windows = bool(cfg["is_windows"])

    repo_root = Path(__file__).resolve().parents[2]
    script_dir = Path(__file__).resolve().parent

    cache_dir = args.cache_dir
    if cache_dir is None:
        if sys.platform.startswith("win"):
            base = os.environ.get("LOCALAPPDATA", str(Path.home() / ".cache"))
            cache_dir = Path(base) / "coperceive-desktop"
        else:
            cache_dir = Path.home() / ".cache" / "coperceive-desktop"
    cache_dir.mkdir(parents=True, exist_ok=True)

    dest_dir = args.dest_dir or (repo_root / "src-tauri" / "resources" / "python")
    resources_dir = dest_dir.parent
    resources_dir.mkdir(parents=True, exist_ok=True)

    archive_path = cache_dir / tarball
    download_url = f"https://github.com/astral-sh/python-build-standalone/releases/download/{PYTHON_RELEASE}/{tarball}"

    if not archive_path.is_file():
        print(f"Downloading python-build-standalone ({tarball})...")
        download_file(download_url, archive_path)

    print("Verifying SHA-256 checksum...")
    actual_sha256 = compute_sha256(archive_path)
    if actual_sha256 != expected_sha256:
        print(f"SHA-256 mismatch for {tarball}!", file=sys.stderr)
        print(f"Expected: {expected_sha256}", file=sys.stderr)
        print(f"Actual:   {actual_sha256}", file=sys.stderr)
        return 1
    print(f"Checksum verified: {actual_sha256}")

    print(f"Extracting Python standalone runtime into {dest_dir}...")
    if dest_dir.is_dir():
        shutil.rmtree(dest_dir)

    import tempfile

    with tempfile.TemporaryDirectory() as extract_tmp:
        with tarfile.open(archive_path, "r:gz") as tar:
            tar.extractall(path=extract_tmp)
        extracted_python = Path(extract_tmp) / "python"
        if not extracted_python.is_dir():
            # If tarball does not have top-level python dir, use extract_tmp
            extracted_python = Path(extract_tmp)
        shutil.move(str(extracted_python), str(dest_dir))

    python_bin = dest_dir / str(cfg["bin_rel"])
    if not is_windows and python_bin.is_file():
        os.chmod(python_bin, 0o755)

    if not python_bin.is_file():
        print(f"Error: Python binary not found at {python_bin}", file=sys.stderr)
        return 2

    # Verify binary can execute (under Rosetta if x86_64 on macOS Apple Silicon)
    version_proc = subprocess.run(
        [str(python_bin), "--version"],
        capture_output=True,
        text=True,
    )
    if version_proc.returncode != 0:
        print(
            f"Error: Failed to execute bundled Python {python_bin}: {version_proc.stderr}",
            file=sys.stderr,
        )
        return 2
    print(f"Python runtime extracted: {version_proc.stdout.strip()}")

    # Ensure web static is built
    static_index = repo_root / "coperceive" / "app" / "static" / "index.html"
    if not args.skip_web_build or not static_index.is_file():
        print("Building viewer bundle into coperceive/app/static...")
        build_script = repo_root / "scripts" / "build_web_static.sh"
        if sys.platform.startswith("win"):
            # Run bash if available, else run npm directly
            if shutil.which("bash"):
                subprocess.run(["bash", str(build_script)], cwd=repo_root, check=True)
            else:
                subprocess.run(["npm", "run", "build"], cwd=repo_root / "web", check=True)
        else:
            subprocess.run(["bash", str(build_script)], cwd=repo_root, check=True)

    # Check lock file
    lockfile_path = script_dir / lockfile_name
    if not lockfile_path.is_file():
        print(f"Error: Lock file not found at {lockfile_path}", file=sys.stderr)
        return 3

    print(f"Installing locked dependencies from {lockfile_name} into bundled Python...")
    subprocess.run(
        [
            str(python_bin),
            "-m",
            "pip",
            "install",
            "--no-warn-script-location",
            "--require-hashes",
            "-r",
            str(lockfile_path),
        ],
        cwd=repo_root,
        check=True,
    )

    print("Installing coperceive package into bundled Python environment...")
    subprocess.run(
        [
            str(python_bin),
            "-m",
            "pip",
            "install",
            "--no-deps",
            "--no-warn-script-location",
            str(repo_root),
        ],
        cwd=repo_root,
        check=True,
    )

    bundled_static = dest_dir / str(cfg["site_packages_rel"]) / "coperceive" / "app" / "static"
    print(f"Validating bundled static assets at {bundled_static}...")
    subprocess.run(
        [
            str(python_bin),
            str(repo_root / "scripts" / "check_bundle_assets.py"),
            "--static",
            str(bundled_static),
        ],
        check=True,
    )

    strip_bundle(dest_dir, is_windows)

    print(f"Bundled Python environment ready at: {dest_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
