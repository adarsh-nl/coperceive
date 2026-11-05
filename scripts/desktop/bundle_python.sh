#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# Bundle reproducible python-build-standalone (macOS Apple Silicon arm64)
# for the Coperceive Tauri desktop application.

set -euo pipefail

PYTHON_VERSION="20250115"
PYTHON_ARCH="aarch64-apple-darwin"
PYTHON_TARBALL="cpython-3.13.1+${PYTHON_VERSION}-${PYTHON_ARCH}-install_only.tar.gz"
EXPECTED_SHA256="d00ac75b15a05da2f6bc0057fe36f6944f9d513239f1c7af12766e03f288fe65"
DOWNLOAD_URL="https://github.com/astral-sh/python-build-standalone/releases/download/${PYTHON_VERSION}/${PYTHON_TARBALL}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
RESOURCE_DIR="${REPO_ROOT}/src-tauri/resources"
PYTHON_DEST="${RESOURCE_DIR}/python"
CACHE_DIR="${HOME}/.cache/coperceive-desktop"

mkdir -p "${CACHE_DIR}"
ARCHIVE_PATH="${CACHE_DIR}/${PYTHON_TARBALL}"

if [ ! -f "${ARCHIVE_PATH}" ]; then
    echo "Downloading python-build-standalone (${PYTHON_TARBALL})..."
    curl -sSL -o "${ARCHIVE_PATH}" "${DOWNLOAD_URL}"
fi

echo "Verifying SHA-256 checksum..."
ACTUAL_SHA256=$(shasum -a 256 "${ARCHIVE_PATH}" | awk '{print $1}')
if [ "${ACTUAL_SHA256}" != "${EXPECTED_SHA256}" ]; then
    echo "SHA256 mismatch for ${PYTHON_TARBALL}!" >&2
    echo "Expected: ${EXPECTED_SHA256}" >&2
    echo "Actual:   ${ACTUAL_SHA256}" >&2
    exit 1
fi
echo "Checksum verified: ${ACTUAL_SHA256}"

echo "Extracting Python standalone runtime into ${PYTHON_DEST}..."
rm -rf "${PYTHON_DEST}"
mkdir -p "${RESOURCE_DIR}"
tar -xzf "${ARCHIVE_PATH}" -C "${RESOURCE_DIR}"

PYTHON_BIN="${PYTHON_DEST}/bin/python3"
if [ ! -x "${PYTHON_BIN}" ]; then
    echo "Error: Python binary not found at ${PYTHON_BIN}" >&2
    exit 2
fi

echo "Python runtime extracted: $("${PYTHON_BIN}" --version)"

# Build the viewer bundle into coperceive/app/static so the wheel install below
# contains it (the built files are not committed).
echo "Building the viewer bundle for the bundled Python environment..."
bash "${REPO_ROOT}/scripts/build_web_static.sh"

# Install coperceive and locked dependencies into bundled Python environment
echo "Installing locked dependencies into bundled Python environment..."
cd "${REPO_ROOT}"

LOCK_FILE="${SCRIPT_DIR}/requirements-darwin-arm64.lock"
if [ ! -f "${LOCK_FILE}" ]; then
    echo "Error: Lock file not found at ${LOCK_FILE}" >&2
    exit 3
fi

# Install dependencies inside the bundled runtime using hash-pinned lock
"${PYTHON_BIN}" -m pip install --no-warn-script-location --require-hashes -r "${LOCK_FILE}"

echo "Installing coperceive package into bundled Python environment..."
"${PYTHON_BIN}" -m pip install --no-deps --no-warn-script-location .

BUNDLED_STATIC="${PYTHON_DEST}/lib/python3.13/site-packages/coperceive/app/static"
"${PYTHON_BIN}" "${REPO_ROOT}/scripts/check_bundle_assets.py" --static "${BUNDLED_STATIC}"

# Clean up console scripts with host-specific shebangs from bin/
# The sidecar executes `python3 -m coperceive serve` directly.
echo "Cleaning up console scripts with host-specific shebangs from bin/..."
find "${PYTHON_DEST}/bin" -type f ! -name "python*" -delete
find "${PYTHON_DEST}/bin" -type l ! -name "python*" -delete
rm -f "${PYTHON_DEST}/bin"/python*-config || true
chmod +x "${PYTHON_DEST}/bin"/python*

# Strip pip, ensurepip, setuptools from the bundle after installation
echo "Stripping pip, ensurepip, and build tools from bundle..."
rm -rf "${PYTHON_DEST}/lib/python3.13/site-packages/pip"*
rm -rf "${PYTHON_DEST}/lib/python3.13/site-packages/setuptools"*
rm -rf "${PYTHON_DEST}/lib/python3.13/site-packages/wheel"*
rm -rf "${PYTHON_DEST}/lib/python3.13/ensurepip"

# Strip test suites from standard library and site-packages (do not delete *.testing packages)
echo "Stripping test suites from bundle..."
rm -rf "${PYTHON_DEST}/lib/python3.13/test"
rm -rf "${PYTHON_DEST}/lib/python3.13/unittest/test"
rm -rf "${PYTHON_DEST}/lib/python3.13/ctypes/test"
rm -rf "${PYTHON_DEST}/lib/python3.13/sqlite3/test"
find "${PYTHON_DEST}/lib/python3.13/site-packages" -type d -name "tests" -exec rm -rf {} + 2>/dev/null || true

# Strip C headers
echo "Stripping C/C++ header files from bundle..."
rm -rf "${PYTHON_DEST}/include"
find "${PYTHON_DEST}" -type d -name "include" -exec rm -rf {} + 2>/dev/null || true

# Strip unused standard library GUI/demo modules
echo "Stripping unused standard library modules..."
rm -rf "${PYTHON_DEST}/lib/python3.13/idlelib"
rm -rf "${PYTHON_DEST}/lib/python3.13/tkinter"
rm -rf "${PYTHON_DEST}/lib/python3.13/turtledemo"
rm -f "${PYTHON_DEST}/lib/python3.13/turtle.py"

# Strip caches and compiled bytecode to keep bundle compact
echo "Stripping caches and .pyc/.pyo files..."
find "${PYTHON_DEST}" -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
find "${PYTHON_DEST}" -name "*.pyc" -delete 2>/dev/null || true
find "${PYTHON_DEST}" -name "*.pyo" -delete 2>/dev/null || true

echo "Bundled Python environment ready at: ${PYTHON_DEST}"
