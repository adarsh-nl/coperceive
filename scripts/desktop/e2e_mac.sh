#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# Real-App End-to-End macOS UI Test Harness for Coperceive Desktop
#
# Resolves the built .app bundle and delegates the shared desktop E2E flow
# to scripts/desktop/e2e_desktop.py (--os macos).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
APP_BUNDLE="${1:-src-tauri/target/release/bundle/macos/Coperceive.app}"
SCREENSHOT_DIR="${COPERCEIVE_E2E_SHOTS:-/tmp/e2e-shots}"
mkdir -p "${SCREENSHOT_DIR}"

echo "=========================================================="
echo "Coperceive Desktop Real-App E2E macOS UI Test"
echo "=========================================================="

if [ ! -d "${APP_BUNDLE}" ]; then
    echo "Locating built .app bundle..."
    for candidate in $(find . -iname "Coperceive.app" -type d 2>/dev/null); do
        if [ -d "${candidate}" ]; then
            APP_BUNDLE="${candidate}"
            break
        fi
    done
fi

APP_BUNDLE="$(cd "${APP_BUNDLE}" && pwd)"
echo "App bundle: ${APP_BUNDLE}"

APP_EXE="${APP_BUNDLE}/Contents/MacOS/Coperceive"
if [ ! -x "${APP_EXE}" ]; then
    if [ -x "${APP_BUNDLE}/Contents/MacOS/coperceive-desktop" ]; then
        APP_EXE="${APP_BUNDLE}/Contents/MacOS/coperceive-desktop"
    else
        for cand in "${APP_BUNDLE}/Contents/MacOS/"*; do
            if [ -f "${cand}" ] && [ -x "${cand}" ]; then
                APP_EXE="${cand}"
                break
            fi
        done
    fi
fi

if [ -z "${APP_EXE}" ] || [ ! -x "${APP_EXE}" ]; then
    echo "Error: App executable not found in ${APP_BUNDLE}/Contents/MacOS! Listing contents:" >&2
    ls -la "${APP_BUNDLE}/Contents/MacOS" || true
    exit 1
fi
echo "App executable: ${APP_EXE}"

exec python3 "${SCRIPT_DIR}/e2e_desktop.py" \
    --os macos \
    --app-exe "${APP_EXE}" \
    --repo-root "${ROOT_DIR}" \
    --screenshot-dir "${SCREENSHOT_DIR}"
