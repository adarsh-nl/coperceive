#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# Linux Desktop smoke test: launches app binary under xvfb,
# starts local sidecar with synthetic scene fixture, and queries /api/scenes health endpoint.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
APP_EXE="${1:-${ROOT_DIR}/src-tauri/target/release/coperceive-desktop}"

if [ ! -x "${APP_EXE}" ]; then
    if [ -x "${ROOT_DIR}/src-tauri/target/debug/coperceive-desktop" ]; then
        APP_EXE="${ROOT_DIR}/src-tauri/target/debug/coperceive-desktop"
    else
        echo "Error: coperceive-desktop binary not found at ${APP_EXE}" >&2
        exit 1
    fi
fi

echo "Using coperceive-desktop binary: ${APP_EXE}"

FIXTURE_ROOT="${ROOT_DIR}/tests/fixtures/synthetic_scene"
if [ ! -d "${FIXTURE_ROOT}" ]; then
    echo "Error: Synthetic scene fixture directory not found at ${FIXTURE_ROOT}!" >&2
    exit 2
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

SMOKE_TMPDIR="$(mktemp -d /tmp/coperceive-linux-smoke-XXXXXX)"
APP_PID=""

cleanup() {
    if [ -n "${APP_PID}" ]; then
        kill -TERM "${APP_PID}" 2>/dev/null || true
        wait "${APP_PID}" 2>/dev/null || true
    fi
    if [ "${SMOKE_SUCCESS:-0}" -eq 1 ]; then
        rm -rf "${SMOKE_TMPDIR}"
    fi
}
trap cleanup EXIT

MARKER_FILE="${SMOKE_TMPDIR}/test_marker.txt"
APP_STDOUT="${SMOKE_TMPDIR}/app_stdout.log"
APP_STDERR="${SMOKE_TMPDIR}/app_stderr.log"

export PYTHONUNBUFFERED=1
export RUST_BACKTRACE=1
export NO_AT_BRIDGE=1
export WEBKIT_DISABLE_COMPOSITING_MODE=1
export WEBKIT_DISABLE_DMABUF_RENDERER=1
export WEBKIT_FORCE_SANDBOX=0
export LIBGL_ALWAYS_SOFTWARE=1

if [ -z "${COPERCEIVE_PYTHON:-}" ] && [ ! -d "${ROOT_DIR}/src-tauri/resources/python" ]; then
    if command -v python3 >/dev/null 2>&1; then
        export COPERCEIVE_PYTHON="$(command -v python3)"
    fi
fi

echo "Starting coperceive-desktop with auto-connect fixture..."
COPERCEIVE_AUTOCONNECT_ROOT="${FIXTURE_ROOT}" \
COPERCEIVE_TEST_MARKER_FILE="${MARKER_FILE}" \
COPERCEIVE_STATE_DIR="${SMOKE_TMPDIR}/state" \
"${APP_EXE}" > "${APP_STDOUT}" 2> "${APP_STDERR}" &
APP_PID=$!
echo "App process spawned with PID ${APP_PID}"

echo "Waiting for auto-connect and sidecar startup (up to 45s)..."
for i in {1..90}; do
    if [ -f "${MARKER_FILE}" ]; then
        break
    fi
    if ! kill -0 "${APP_PID}" 2>/dev/null; then
        echo "Error: App process died prematurely during startup!" >&2
        echo "=== App stderr ===" >&2
        cat "${APP_STDERR}" >&2
        echo "=== App stdout ===" >&2
        cat "${APP_STDOUT}" >&2
        exit 3
    fi
    sleep 0.5
done

if [ ! -f "${MARKER_FILE}" ]; then
    echo "Error: Auto-connect marker file was not created within timeout!" >&2
    if [ -n "${APP_PID}" ] && kill -0 "${APP_PID}" 2>/dev/null; then
        kill -TERM "${APP_PID}" 2>/dev/null || true
        wait "${APP_PID}" 2>/dev/null || true
    fi
    echo "=== App stderr ===" >&2
    cat "${APP_STDERR}" >&2
    echo "=== App stdout ===" >&2
    cat "${APP_STDOUT}" >&2
    echo "=== Sidecar logs ===" >&2
    for logfile in /tmp/coperceive-local-*/serve-*.log "${SMOKE_TMPDIR}"/coperceive-local-*/serve-*.log; do
        if [ -f "${logfile}" ]; then
            echo "--- ${logfile} ---" >&2
            cat "${logfile}" >&2
        fi
    done
    exit 4
fi

APP_ORIGIN=$(sed -n '1p' "${MARKER_FILE}")
APP_TOKEN=$(sed -n '2p' "${MARKER_FILE}")
SIDECAR_LOG=$(sed -n '3p' "${MARKER_FILE}")

echo "Auto-connected to origin: ${APP_ORIGIN}"
echo "Sidecar log path: ${SIDECAR_LOG}"

# Query GET /api/scenes with auth token
echo "Checking GET ${APP_ORIGIN}/api/scenes with auth token..."
SCENES_RESP=$(curl -s -f -H "Authorization: Bearer ${APP_TOKEN}" "${APP_ORIGIN}/api/scenes")
echo "Scenes response: ${SCENES_RESP}"
SCENE_COUNT=$(printf '%s\n' "${SCENES_RESP}" | python3 -c "import sys, json; data=json.load(sys.stdin); print(len(data))")
if [ "${SCENE_COUNT}" -lt 1 ]; then
    echo "Failure: expected >= 1 scene in response, got ${SCENE_COUNT}!" >&2
    exit 5
fi

echo "Checking GET ${APP_ORIGIN}/healthz..."
curl -s -f -H "Authorization: Bearer ${APP_TOKEN}" "${APP_ORIGIN}/healthz" >/dev/null || true

echo "Verified: /api/scenes returned ${SCENE_COUNT} scene(s) with HTTP 200 OK."

SCREENSHOT_DIR="/tmp/e2e-shots"
mkdir -p "${SCREENSHOT_DIR}"

capture_screenshot() {
    local name="$1"
    if command -v import >/dev/null 2>&1; then
        import -window root "${SCREENSHOT_DIR}/${name}.png" 2>/dev/null || true
    elif command -v xwd >/dev/null 2>&1 && command -v convert >/dev/null 2>&1; then
        xwd -root -silent | convert xwd:- "${SCREENSHOT_DIR}/${name}.png" 2>/dev/null || true
    fi
    if [ -f "${SCREENSHOT_DIR}/${name}.png" ]; then
        echo "Captured screenshot: ${SCREENSHOT_DIR}/${name}.png ($(wc -c < "${SCREENSHOT_DIR}/${name}.png") bytes)"
    fi
}

echo "Capturing viewer screenshot..."
sleep 2
capture_screenshot "viewer"

CTRL_PORT=$(sed -n '4p' "${MARKER_FILE}" || true)
CTRL_SECRET=$(sed -n '5p' "${MARKER_FILE}" || true)

if [ -n "${CTRL_PORT}" ] && [ "${CTRL_PORT}" != "0" ] && [ -n "${CTRL_SECRET}" ]; then
    echo "Showing Library and capturing screenshot..."
    curl -s -X POST -H "Authorization: Bearer ${CTRL_SECRET}" -H "Content-Type: application/json" -d "{}" "http://127.0.0.1:${CTRL_PORT}/show-library" >/dev/null || true
    sleep 2
    capture_screenshot "library"

    echo "Navigating to Dataset View and capturing screenshot..."
    curl -s -X POST -H "Authorization: Bearer ${CTRL_SECRET}" -H "Content-Type: application/json" -d "{\"script\":\"window.location.hash='#/connect?tab=hpc';\"}" "http://127.0.0.1:${CTRL_PORT}/eval" >/dev/null || true
    sleep 2
    capture_screenshot "dataset_view"
fi

SMOKE_SUCCESS=1
echo "Linux Desktop smoke test PASSED successfully."
