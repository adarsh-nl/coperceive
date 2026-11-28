#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# Real-App End-to-End Linux UI Test Harness for Coperceive Desktop
#
# Resolves the installed .deb binary (default /usr/bin/coperceive-desktop) or
# explicit executable path, ensures Xvfb + D-Bus session, and delegates the
# shared desktop E2E flow to scripts/desktop/e2e_desktop.py (--os linux).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
APP_EXE="${1:-/usr/bin/coperceive-desktop}"
SCREENSHOT_DIR="${COPERCEIVE_E2E_SHOTS:-/tmp/e2e-shots}"
mkdir -p "${SCREENSHOT_DIR}"

if [ ! -x "${APP_EXE}" ]; then
    if [ -x "${ROOT_DIR}/src-tauri/target/release/coperceive-desktop" ]; then
        APP_EXE="${ROOT_DIR}/src-tauri/target/release/coperceive-desktop"
    elif [ -x "${ROOT_DIR}/src-tauri/target/debug/coperceive-desktop" ]; then
        APP_EXE="${ROOT_DIR}/src-tauri/target/debug/coperceive-desktop"
    else
        echo "Error: coperceive-desktop executable not found at ${APP_EXE}" >&2
        exit 1
    fi
fi

# Ensure an active X display is running with 24-bit color depth
if [ -z "${DISPLAY:-}" ]; then
    if command -v xvfb-run >/dev/null 2>&1; then
        echo "No DISPLAY set; re-executing under xvfb-run..."
        exec xvfb-run -a -s "-screen 0 1920x1080x24" bash "$0" "$@"
    else
        echo "Warning: DISPLAY not set and xvfb-run not available" >&2
    fi
fi

# Ensure D-Bus session bus is available for GTK/WebKit
if [ -z "${DBUS_SESSION_BUS_ADDRESS:-}" ] && command -v dbus-run-session >/dev/null 2>&1; then
    echo "No DBUS_SESSION_BUS_ADDRESS set; re-executing under dbus-run-session..."
    exec dbus-run-session -- bash "$0" "$@"
fi

echo "=========================================================="
echo "Coperceive Desktop Real-App E2E Linux UI Test"
echo "=========================================================="
echo "App executable: ${APP_EXE}"

exec python3 "${SCRIPT_DIR}/e2e_desktop.py" \
    --os linux \
    --app-exe "${APP_EXE}" \
    --repo-root "${ROOT_DIR}" \
    --screenshot-dir "${SCREENSHOT_DIR}"
