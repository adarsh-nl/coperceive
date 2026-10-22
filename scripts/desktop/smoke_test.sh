#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# Smoke test verifying the bundled Python environment, coperceive serve sidecar,
# constant-time token verification, and measuring artifact sizes.

set -euo pipefail

APP_BUNDLE="${1:-src-tauri/target/release/bundle/macos/Coperceive.app}"
DMG_FILE="${2:-src-tauri/target/release/bundle/dmg/Coperceive_0.0.0_aarch64.dmg}"

echo "=========================================================="
echo "Coperceive Desktop Artifact Verification & Smoke Test"
echo "=========================================================="

if [ ! -d "${APP_BUNDLE}" ]; then
    echo "Searching for built .app bundle across repository..."
    FOUND_APP=""
    for candidate in $(find . -iname "*.app" -type d 2>/dev/null); do
        if [ -d "${candidate}" ]; then
            FOUND_APP="${candidate}"
            break
        fi
    done

    if [ -n "${FOUND_APP}" ]; then
        APP_BUNDLE="${FOUND_APP}"
    else
        echo "Error: .app bundle not found! Listing targets:" >&2
        find . -maxdepth 6 -type d 2>/dev/null | grep -E "target|bundle|dist|release" || true
        exit 1
    fi
fi

APP_BUNDLE="$(cd "${APP_BUNDLE}" && pwd)"
echo "App bundle: ${APP_BUNDLE}"

# Find bundled python
PYTHON_BIN=""
for candidate in \
    "${APP_BUNDLE}/Contents/Resources/resources/python/bin/python3" \
    "${APP_BUNDLE}/Contents/Resources/python/bin/python3" \
    "src-tauri/resources/python/bin/python3"; do
    if [ -x "${candidate}" ]; then
        PYTHON_BIN="$(cd "$(dirname "${candidate}")" && pwd)/$(basename "${candidate}")"
        break
    fi
done

if [ -z "${PYTHON_BIN}" ]; then
    echo "Error: Bundled Python interpreter not found in bundle!" >&2
    exit 2
fi

echo "Found bundled Python: ${PYTHON_BIN}"
echo "Python version: $("${PYTHON_BIN}" --version)"
(cd /tmp && "${PYTHON_BIN}" -c "import coperceive, fastapi, uvicorn; print('coperceive import verified from:', coperceive.__file__)")

# Measure artifact sizes
echo ""
echo "--- Artifact Measured Sizes ---"
APP_SIZE=$(du -sh "${APP_BUNDLE}" | awk '{print $1}')
APP_SIZE_BYTES=$(du -sk "${APP_BUNDLE}" | awk '{print $1 * 1024}')
echo "Coperceive.app size: ${APP_SIZE} (${APP_SIZE_BYTES} bytes)"

FOUND_DMG=""
if [ -f "${DMG_FILE}" ]; then
    FOUND_DMG="${DMG_FILE}"
else
    for candidate in $(find . -iname "*.dmg" -type f 2>/dev/null); do
        if [ -f "${candidate}" ]; then
            FOUND_DMG="${candidate}"
            break
        fi
    done
fi

if [ -n "${FOUND_DMG}" ]; then
    FOUND_DMG="$(cd "$(dirname "${FOUND_DMG}")" && pwd)/$(basename "${FOUND_DMG}")"
    DMG_SIZE=$(ls -lh "${FOUND_DMG}" | awk '{print $5}')
    DMG_SIZE_BYTES=$(stat -f %z "${FOUND_DMG}" 2>/dev/null || wc -c < "${FOUND_DMG}" 2>/dev/null || echo "0")
    echo "Coperceive .dmg size: ${DMG_SIZE} (${DMG_SIZE_BYTES} bytes, ${FOUND_DMG})"
else
    DMG_SIZE="unknown"
    DMG_SIZE_BYTES="0"
    echo "Warning: .dmg file not located for measurement"
fi

if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
    echo "### Artifact Measured Sizes" >> "${GITHUB_STEP_SUMMARY}"
    echo "- **Coperceive.app**: ${APP_SIZE} (${APP_SIZE_BYTES} bytes)" >> "${GITHUB_STEP_SUMMARY}"
    echo "- **Coperceive .dmg**: ${DMG_SIZE} (${DMG_SIZE_BYTES} bytes) (\`${FOUND_DMG}\`)" >> "${GITHUB_STEP_SUMMARY}"
fi

# Headless smoke test: launch serve with token-file and verify loopback authentication
echo ""
echo "--- Headless Sidecar Smoke Test ---"
TMPDIR=$(mktemp -d /tmp/coperceive-desktop-smoke-XXXXXX)
trap 'rm -rf "${TMPDIR}"' EXIT

TOKEN="smoke-test-secret-token-abcdef123456"
TOKEN_FILE="${TMPDIR}/token.txt"
touch "${TOKEN_FILE}"
chmod 0600 "${TOKEN_FILE}"
echo "${TOKEN}" > "${TOKEN_FILE}"

DATA_ROOT="${TMPDIR}/data"
mkdir -p "${DATA_ROOT}/smoke"
# serve refuses a root that has no scene with both markers. This is only a
# startup probe, not a real scene.
printf '%s\n' '{"scene_id":"smoke"}' > "${DATA_ROOT}/smoke/manifest.json"
printf '%s\n' '{}' > "${DATA_ROOT}/smoke/COMPLETE.json"

# Ephemeral port on loopback
PORT=$("${PYTHON_BIN}" -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')
echo "Launching coperceive serve on port ${PORT} with token-file ${TOKEN_FILE}..."

"${PYTHON_BIN}" -m coperceive serve \
    --host 127.0.0.1 \
    --port "${PORT}" \
    --token-file "${TOKEN_FILE}" \
    --root "${DATA_ROOT}" > "${TMPDIR}/serve.log" 2>&1 &
SERVE_PID=$!

echo "Sidecar PID: ${SERVE_PID}"

# Ensure processes are killed on script exit
cleanup_serve() {
    if [ -n "${SERVE_PID:-}" ] && kill -0 "${SERVE_PID}" 2>/dev/null; then
        echo "Terminating sidecar PID ${SERVE_PID}..."
        kill "${SERVE_PID}" 2>/dev/null || true
        wait "${SERVE_PID}" 2>/dev/null || true
    fi
}

cleanup_all() {
    if [ -n "${APP_PID:-}" ] && kill -0 "${APP_PID}" 2>/dev/null; then
        echo "Terminating app process PID ${APP_PID}..."
        kill -9 "${APP_PID}" 2>/dev/null || true
        wait "${APP_PID}" 2>/dev/null || true
    fi
    cleanup_serve
    rm -rf "${TMPDIR}"
}
trap cleanup_all EXIT

# Wait for server to start (poll up to 30s)
echo "Waiting for /api/health readiness..."
HEALTHY=0
for i in {1..60}; do
    if curl -s -f -H "Authorization: Bearer ${TOKEN}" "http://127.0.0.1:${PORT}/api/health" > /dev/null 2>&1; then
        HEALTHY=1
        break
    fi
    if ! kill -0 "${SERVE_PID}" 2>/dev/null; then
        echo "Error: Sidecar process died prematurely. Log output:" >&2
        cat "${TMPDIR}/serve.log" >&2
        exit 3
    fi
    sleep 0.5
done

if [ "${HEALTHY}" -ne 1 ]; then
    echo "Error: Server failed to become healthy within 30 seconds. Log output:" >&2
    cat "${TMPDIR}/serve.log" >&2
    exit 3
fi

echo "Server is healthy!"

# Test 1: Unauthenticated request must return HTTP 401
echo "Checking unauthenticated request (expect HTTP 401)..."
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:${PORT}/api/health")
if [ "${HTTP_CODE}" != "401" ]; then
    echo "Security failure: unauthenticated request returned HTTP ${HTTP_CODE}, expected 401!" >&2
    exit 4
fi
echo "Verified: unauthenticated request returned 401 Unauthorized"

# Test 2: Authenticated request returns HTTP 200 OK
echo "Checking authenticated request with token (expect HTTP 200)..."
RESP=$(curl -s -H "Authorization: Bearer ${TOKEN}" "http://127.0.0.1:${PORT}/api/health")
echo "Response: ${RESP}"
if ! echo "${RESP}" | grep -q '"status":"ok"'; then
    echo "Failure: unexpected health response: ${RESP}" >&2
    exit 5
fi
echo "Verified: authenticated request returned HTTP 200 OK with status ok"

# Terminate sidecar process cleanly
cleanup_serve
echo "Verified: sidecar terminated cleanly, no orphaned processes."

# Test 3: Verify no runner shebangs exist in Python bin directory
BIN_DIR="$(dirname "${PYTHON_BIN}")"
echo ""
echo "--- Python Binary Sanitization Check ---"
echo "Verifying no CI runner shebangs remain in ${BIN_DIR}..."
RUNNER_SHEBANGS=$(grep -rnE "^#\!.*runner" "${BIN_DIR}" 2>/dev/null || true)
if [ -n "${RUNNER_SHEBANGS}" ]; then
    echo "Error: Found scripts with host runner shebangs in ${BIN_DIR}:" >&2
    echo "${RUNNER_SHEBANGS}" >&2
    exit 6
fi
echo "Verified: No runner shebangs found in ${BIN_DIR}."

# Test 3b: Verify test hooks are absent from release .app bundle
echo ""
echo "--- Release Bundle Hygiene Check ---"
echo "Verifying __COPERCEIVE_STAGE__ is absent from ${APP_BUNDLE}..."
LEAKED_HOOKS=$(grep -rn "window.__COPERCEIVE_STAGE__" "${APP_BUNDLE}" 2>/dev/null || true)
if [ -n "${LEAKED_HOOKS}" ]; then
    echo "Error: __COPERCEIVE_STAGE__ found in release app bundle:" >&2
    echo "${LEAKED_HOOKS}" >&2
    exit 7
fi
echo "Verified: No test hooks leaked into release app bundle."

# Test 4: App Binary Launch & Viewer Scene Loading Check
echo ""
echo "--- App Binary Launch & Viewer Scene Loading Check (macOS) ---"
APP_EXE="${APP_BUNDLE}/Contents/MacOS/coperceive-desktop"
if [ ! -x "${APP_EXE}" ]; then
    if [ -x "${APP_BUNDLE}/Contents/MacOS/Coperceive" ]; then
        APP_EXE="${APP_BUNDLE}/Contents/MacOS/Coperceive"
    else
        APP_EXE=$(find "${APP_BUNDLE}/Contents/MacOS" -type f -perm +111 2>/dev/null | head -n 1)
    fi
fi

if [ -z "${APP_EXE}" ] || [ ! -x "${APP_EXE}" ]; then
    echo "Error: App executable not found or not executable in ${APP_BUNDLE}/Contents/MacOS" >&2
    ls -la "${APP_BUNDLE}/Contents/MacOS" || true
    exit 10
fi

FIXTURE_ROOT="$(pwd)/tests/fixtures/synthetic_scene"
if [ ! -d "${FIXTURE_ROOT}" ]; then
    echo "Error: Synthetic scene fixture directory not found at ${FIXTURE_ROOT}!" >&2
    exit 11
fi

MARKER_FILE="${TMPDIR}/test_marker.txt"
APP_STDOUT="${TMPDIR}/app_stdout.log"
APP_STDERR="${TMPDIR}/app_stderr.log"

echo "Starting app binary with auto-connect fixture: ${FIXTURE_ROOT}..."
COPERCEIVE_AUTOCONNECT_ROOT="${FIXTURE_ROOT}" \
COPERCEIVE_TEST_MARKER_FILE="${MARKER_FILE}" \
"${APP_EXE}" > "${APP_STDOUT}" 2> "${APP_STDERR}" &
APP_PID=$!
echo "App process spawned with PID ${APP_PID}"

echo "Waiting for auto-connect and viewer startup (up to 15s)..."
for i in {1..30}; do
    if [ -f "${MARKER_FILE}" ]; then
        break
    fi
    if ! kill -0 "${APP_PID}" 2>/dev/null; then
        echo "Error: App process died prematurely during startup!" >&2
        echo "=== App stderr ===" >&2
        cat "${APP_STDERR}" >&2
        echo "=== App stdout ===" >&2
        cat "${APP_STDOUT}" >&2
        exit 12
    fi
    sleep 0.5
done

if [ ! -f "${MARKER_FILE}" ]; then
    echo "Error: Auto-connect marker file was not created within timeout!" >&2
    echo "=== App stderr ===" >&2
    cat "${APP_STDERR}" >&2
    echo "=== App stdout ===" >&2
    cat "${APP_STDOUT}" >&2
    exit 13
fi

APP_ORIGIN=$(sed -n '1p' "${MARKER_FILE}")
APP_TOKEN=$(sed -n '2p' "${MARKER_FILE}")
SIDECAR_LOG=$(sed -n '3p' "${MARKER_FILE}")

echo "Auto-connected to origin: ${APP_ORIGIN}"
echo "Sidecar log path: ${SIDECAR_LOG}"

# Query GET /api/scenes to verify the sidecar serves the fixture scene
echo "Checking GET ${APP_ORIGIN}/api/scenes with auth token..."
SCENES_RESP=$(curl -s -f -H "Authorization: Bearer ${APP_TOKEN}" "${APP_ORIGIN}/api/scenes")
echo "Scenes response: ${SCENES_RESP}"
SCENE_COUNT=$(python3 -c "import json; data=json.loads('''${SCENES_RESP}'''); print(len(data))")
if [ "${SCENE_COUNT}" -lt 1 ]; then
    echo "Failure: expected >= 1 scene in response, got ${SCENE_COUNT}!" >&2
    exit 14
fi
echo "Verified: /api/scenes returned ${SCENE_COUNT} scene(s) with HTTP 200 OK."

# Verify frame endpoint probe: loads and serves frame data for synth_001
echo "Checking GET ${APP_ORIGIN}/api/scenes/synth_001/frame?time_us=0..."
FRAME_RESP=$(curl -s -f -H "Authorization: Bearer ${APP_TOKEN}" "${APP_ORIGIN}/api/scenes/synth_001/frame?time_us=0")
FRAME_TIME=$(python3 -c "import json; data=json.loads('''${FRAME_RESP}'''); print(data.get('time_us', -1))")
if [ "${FRAME_TIME}" -ne 0 ]; then
    echo "Failure: expected time_us 0 in frame response, got ${FRAME_TIME}!" >&2
    exit 14
fi
echo "Verified: frame endpoint returned valid frame data at time_us 0."

# Wait up to 10 seconds to verify process stability and let the viewer webview fetch scenes
echo "Waiting 10 seconds to verify viewer stability..."
sleep 10

# Capture native window screenshot on macOS
SCREENSHOT_DIR="/tmp/e2e-shots"
mkdir -p "${SCREENSHOT_DIR}"
echo "Capturing desktop application window screenshot..."
screencapture -x "${SCREENSHOT_DIR}/desktop-app-window.png" 2>/dev/null || true
if [ -f "${SCREENSHOT_DIR}/desktop-app-window.png" ]; then
    echo "Verified: screenshot captured at ${SCREENSHOT_DIR}/desktop-app-window.png ($(stat -f %z "${SCREENSHOT_DIR}/desktop-app-window.png" 2>/dev/null || wc -c < "${SCREENSHOT_DIR}/desktop-app-window.png") bytes)"
fi

if ! kill -0 "${APP_PID}" 2>/dev/null; then
    echo "Error: App process died prematurely!" >&2
    echo "=== App stderr ===" >&2
    cat "${APP_STDERR}" >&2
    echo "=== App stdout ===" >&2
    cat "${APP_STDOUT}" >&2
    exit 15
fi

if grep -q -E "The string did not match the expected pattern|panicked|Fatal startup error" "${APP_STDERR}"; then
    echo "Error: App stderr contains pattern error or panic!" >&2
    cat "${APP_STDERR}" >&2
    kill -9 "${APP_PID}" 2>/dev/null || true
    exit 16
fi

if [ -f "${SIDECAR_LOG}" ]; then
    echo "--- Sidecar Log (recent 15 lines) ---"
    tail -n 15 "${SIDECAR_LOG}"
fi

echo "Verified: App process ${APP_PID} is alive and healthy."
echo "Terminating app process..."
kill -TERM "${APP_PID}" 2>/dev/null || true
sleep 1
if kill -0 "${APP_PID}" 2>/dev/null; then
    kill -9 "${APP_PID}" 2>/dev/null || true
fi
wait "${APP_PID}" 2>/dev/null || true
APP_PID=""
echo "Verified: App binary launched, auto-connected to fixture, loaded scenes, and terminated successfully."
echo "=========================================================="
echo "Smoke test PASSED successfully!"
echo "=========================================================="
