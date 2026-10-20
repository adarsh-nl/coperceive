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

# Build and install coperceive wheel with core and app dependencies (excluding torch and viewer extras)
echo "Installing coperceive and locked dependencies into bundled Python environment..."
cd "${REPO_ROOT}"

# Install pip dependencies inside the bundled runtime
"${PYTHON_BIN}" -m pip install --upgrade pip
"${PYTHON_BIN}" -m pip install --no-warn-script-location \
    "numpy>=1.26.4" \
    "pyarrow>=18" \
    "pillow>=10.4" \
    "matplotlib>=3.8" \
    "pyyaml>=6" \
    "fastapi>=0.115" \
    "uvicorn>=0.30" \
    "pydantic>=2"

"${PYTHON_BIN}" -m pip install --no-deps --no-warn-script-location .

# Strip caches to keep bundle compact
find "${PYTHON_DEST}" -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
find "${PYTHON_DEST}" -name "*.pyc" -delete 2>/dev/null || true

echo "Bundled Python environment ready at: ${PYTHON_DEST}"
