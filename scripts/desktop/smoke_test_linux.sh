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

TMPDIR="$(mktemp -d /tmp/coperceive-linux-smoke-XXXXXX)"
trap 'rm -rf "${TMPDIR}"' EXIT

MARKER_FILE="${TMPDIR}/test_marker.txt"
APP_STDOUT="${TMPDIR}/app_stdout.log"
APP_STDERR="${TMPDIR}/app_stderr.log"

export WEBKIT_DISABLE_COMPOSITING_MODE=1
export LIBGL_ALWAYS_SOFTWARE=1

if [ -z "${COPERCEIVE_PYTHON:-}" ] && command -v python3 >/dev/null 2>&1; then
    export COPERCEIVE_PYTHON="$(command -v python3)"
fi

echo "Starting coperceive-desktop with auto-connect fixture under xvfb..."
if command -v xvfb-run >/dev/null 2>&1 && [ -z "${DISPLAY:-}" ]; then
    COPERCEIVE_AUTOCONNECT_ROOT="${FIXTURE_ROOT}" \
    COPERCEIVE_TEST_MARKER_FILE="${MARKER_FILE}" \
    COPERCEIVE_STATE_DIR="${TMPDIR}/state" \
    xvfb-run -a -s "-screen 0 1280x800x24" "${APP_EXE}" > "${APP_STDOUT}" 2> "${APP_STDERR}" &
    APP_PID=$!
else
    COPERCEIVE_AUTOCONNECT_ROOT="${FIXTURE_ROOT}" \
    COPERCEIVE_TEST_MARKER_FILE="${MARKER_FILE}" \
    COPERCEIVE_STATE_DIR="${TMPDIR}/state" \
    "${APP_EXE}" > "${APP_STDOUT}" 2> "${APP_STDERR}" &
    APP_PID=$!
fi
echo "App process spawned with PID ${APP_PID}"

echo "Waiting for auto-connect and sidecar startup (up to 30s)..."
for i in {1..60}; do
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
    echo "=== App stderr ===" >&2
    cat "${APP_STDERR}" >&2
    echo "=== App stdout ===" >&2
    cat "${APP_STDOUT}" >&2
    kill "${APP_PID}" 2>/dev/null || true
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
    kill "${APP_PID}" 2>/dev/null || true
    exit 5
fi

echo "Verified: /api/scenes returned ${SCENE_COUNT} scene(s) with HTTP 200 OK."

# Clean up app process gracefully
kill -TERM "${APP_PID}" 2>/dev/null || true
wait "${APP_PID}" 2>/dev/null || true
echo "Linux Desktop smoke test PASSED successfully."
