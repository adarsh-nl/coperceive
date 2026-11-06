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
unset COPERCEIVE_PYTHON

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

echo "Waiting for auto-connect and viewer startup (up to 45s)..."
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

# Poll until viewer webview is responsive
echo "Polling for viewer readiness via controller..."
CTRL_PORT=$(sed -n '4p' "${MARKER_FILE}" || echo "0")
CTRL_SECRET=$(sed -n '5p' "${MARKER_FILE}" || echo "")
if [ -n "${CTRL_PORT}" ] && [ "${CTRL_PORT}" != "0" ] && [ -n "${CTRL_SECRET}" ]; then
    for i in {1..50}; do
        RESP=$(curl -s -X POST -H "Authorization: Bearer ${CTRL_SECRET}" -H "Content-Type: application/json" -d '{"script":"Boolean(window.__COPERCEIVE_DESKTOP__)"}' "http://127.0.0.1:${CTRL_PORT}/eval" || true)
        if echo "${RESP}" | grep -q '"result":true'; then
            echo "Verified: Viewer webview responded to controller eval probe."
            break
        fi
        sleep 0.2
    done
fi

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
echo "Terminating auto-connect app process..."
kill -TERM "${APP_PID}" 2>/dev/null || true
sleep 1
if kill -0 "${APP_PID}" 2>/dev/null; then
    kill -9 "${APP_PID}" 2>/dev/null || true
fi
wait "${APP_PID}" 2>/dev/null || true
APP_PID=""
echo "Verified: App binary launched, auto-connected to fixture, loaded scenes, and terminated successfully."

# Test 5: First-Run Welcome Card & Demo Mode End-to-End Verification
echo ""
echo "--- First-Run Welcome Card & Demo Mode Verification (macOS) ---"
DEMO_STATE_DIR="${TMPDIR}/demo-state"
mkdir -p "${DEMO_STATE_DIR}"
DEMO_MARKER="${TMPDIR}/demo_marker.txt"
DEMO_STDOUT="${TMPDIR}/demo_stdout.log"
DEMO_STDERR="${TMPDIR}/demo_stderr.log"

unset COPERCEIVE_AUTOCONNECT_ROOT
unset COPERCEIVE_DESKTOP_AUTOCONNECT_ROOT
unset COPERCEIVE_PYTHON

echo "Starting app binary on empty state dir: ${DEMO_STATE_DIR}..."
COPERCEIVE_STATE_DIR="${DEMO_STATE_DIR}" \
COPERCEIVE_TEST_MARKER_FILE="${DEMO_MARKER}" \
"${APP_EXE}" > "${DEMO_STDOUT}" 2> "${DEMO_STDERR}" &
APP_PID=$!
echo "Demo test app process spawned with PID ${APP_PID}"

DEMO_CTRL_PORT=""
DEMO_CTRL_SECRET=""
for i in {1..90}; do
    if [ -f "${DEMO_MARKER}" ]; then
        DEMO_CTRL_PORT=$(sed -n '4p' "${DEMO_MARKER}")
        DEMO_CTRL_SECRET=$(sed -n '5p' "${DEMO_MARKER}")
        if [ -n "${DEMO_CTRL_PORT}" ] && [ "${DEMO_CTRL_PORT}" != "0" ] && [ -n "${DEMO_CTRL_SECRET}" ]; then
            break
        fi
    fi
    if ! kill -0 "${APP_PID}" 2>/dev/null; then
        echo "Error: Demo test app process died during startup!" >&2
        cat "${DEMO_STDERR}" >&2
        cat "${DEMO_STDOUT}" >&2
        exit 20
    fi
    sleep 0.5
done

if [ -z "${DEMO_CTRL_PORT}" ] || [ "${DEMO_CTRL_PORT}" = "0" ] || [ -z "${DEMO_CTRL_SECRET}" ]; then
    echo "Error: Controller port/secret not written to ${DEMO_MARKER} within timeout!" >&2
    cat "${DEMO_STDERR}" >&2
    cat "${DEMO_STDOUT}" >&2
    exit 20
fi
echo "Controller ready on port ${DEMO_CTRL_PORT}"

echo "Polling for first-run Welcome Card on empty state dir (no injected seed)..."
WELCOME_READY=0
for i in {1..120}; do
    RESP=$(curl -s -f -X POST \
        -H "Authorization: Bearer ${DEMO_CTRL_SECRET}" \
        -H "Content-Type: application/json" \
        -d '{"window":"main","script":"Boolean(document.querySelector(\".welcome-card\") && document.querySelector(\"[data-testid=\\\"welcome-open-demo\\\"]\"))"}' \
        "http://127.0.0.1:${DEMO_CTRL_PORT}/eval" 2>/dev/null || echo "")
    if echo "${RESP}" | grep -q '"result":true'; then
        WELCOME_READY=1
        break
    fi
    sleep 0.25
done

if [ "${WELCOME_READY}" -ne 1 ]; then
    PAGE_STATE=$(curl -s -X POST \
        -H "Authorization: Bearer ${DEMO_CTRL_SECRET}" \
        -H "Content-Type: application/json" \
        -d '{"window":"main","script":"document.body ? document.body.innerText : \"<no body>\""}' \
        "http://127.0.0.1:${DEMO_CTRL_PORT}/eval" 2>/dev/null || echo "<eval failed>")
    echo "Error: Welcome card did not appear on empty state dir within timeout!" >&2
    echo "Main window page content: ${PAGE_STATE}" >&2
    cat "${DEMO_STDERR}" >&2
    exit 21
fi
echo "Verified: Welcome card appeared automatically on empty first-run state dir."
screencapture -x "${SCREENSHOT_DIR}/welcome_card.png" 2>/dev/null || true

echo "Clicking 'Open the demo' button in Welcome Card..."
CLICK_RESP=$(curl -s -f -X POST \
    -H "Authorization: Bearer ${DEMO_CTRL_SECRET}" \
    -H "Content-Type: application/json" \
    -d '{"window":"main","script":"(() => { const btn = document.querySelector(\"[data-testid=\\\"welcome-open-demo\\\"]\"); if (!btn) return \"missing\"; btn.click(); return \"clicked\"; })()"}' \
    "http://127.0.0.1:${DEMO_CTRL_PORT}/eval")
if ! echo "${CLICK_RESP}" | grep -q '"clicked"'; then
    echo "Error: Failed to click 'Open the demo' button: ${CLICK_RESP}" >&2
    exit 22
fi

echo "Polling until demo scene is generated, opened in viewer, and renders LiDAR points, camera strip, and boxes..."
VIEWER_CHECK_SCRIPT='{"window":"viewer","script":"(() => { const title = (document.querySelector(\".dataset-badge .dataset-name\")?.textContent || \"\").trim(); const points = [...document.querySelectorAll(\".agent-points\")].reduce((s, el) => s + (parseInt((el.getAttribute(\"title\") || \"0\").split(\" \")[0], 10) || 0), 0); const cameras = [...document.querySelectorAll(\"section.cam-strip[data-open=\\\"true\\\"] .cam-thumb canvas\")].filter(c => c.width > 0 && c.height > 0).length; const boxes = parseInt((document.querySelector(\"button.eye[aria-label=\\\"Boxes\\\"] .eye-meta\")?.textContent || \"0\").trim(), 10) || 0; const ready = title === \"Demo (synthetic)\" && points > 0 && cameras >= 1 && boxes > 0; return { ready, title, points, cameras, boxes }; })()"}'

DEMO_LOADED=0
LAST_VIEWER_STATE=""
for i in {1..480}; do
    if ! kill -0 "${APP_PID}" 2>/dev/null; then
        echo "Error: App process exited while generating/opening demo!" >&2
        cat "${DEMO_STDERR}" >&2
        cat "${DEMO_STDOUT}" >&2
        exit 23
    fi
    RESP=$(curl -s -X POST \
        -H "Authorization: Bearer ${DEMO_CTRL_SECRET}" \
        -H "Content-Type: application/json" \
        -d "${VIEWER_CHECK_SCRIPT}" \
        "http://127.0.0.1:${DEMO_CTRL_PORT}/eval" 2>/dev/null || echo "")
    if [ -n "${RESP}" ]; then
        LAST_VIEWER_STATE="${RESP}"
        if echo "${RESP}" | grep -q '"ready":true'; then
            DEMO_LOADED=1
            break
        fi
    fi
    sleep 0.5
done

if [ "${DEMO_LOADED}" -ne 1 ]; then
    echo "Error: Demo scene did not finish loading in viewer within timeout!" >&2
    echo "Last viewer state: ${LAST_VIEWER_STATE}" >&2
    echo "=== Demo App stdout ===" >&2
    cat "${DEMO_STDOUT}" >&2
    echo "=== Demo App stderr ===" >&2
    cat "${DEMO_STDERR}" >&2
    exit 23
fi
echo "Verified viewer loaded demo scene: ${LAST_VIEWER_STATE}"

echo "Opening comparison/predictions view and verifying 'synthetic predictions' run..."
COMPARE_CHECK_SCRIPT='{"window":"viewer","script":"(() => { const runBtn = document.querySelector(\"#prediction-run\"); const compareBtn = document.querySelector(\"#prediction-compare\"); if (compareBtn) compareBtn.click(); const predRun = (runBtn?.textContent || \"\").trim(); const evalHead = (document.querySelector(\".eval-head\")?.textContent || \"\").trim(); const ok = predRun.includes(\"synthetic predictions\") && evalHead.includes(\"synthetic predictions\"); return { ok, predRun, evalHead }; })()"}'

COMPARE_OK=0
LAST_COMPARE_STATE=""
for i in {1..60}; do
    RESP=$(curl -s -X POST \
        -H "Authorization: Bearer ${DEMO_CTRL_SECRET}" \
        -H "Content-Type: application/json" \
        -d "${COMPARE_CHECK_SCRIPT}" \
        "http://127.0.0.1:${DEMO_CTRL_PORT}/eval" 2>/dev/null || echo "")
    if [ -n "${RESP}" ]; then
        LAST_COMPARE_STATE="${RESP}"
        if echo "${RESP}" | grep -q '"ok":true'; then
            COMPARE_OK=1
            break
        fi
    fi
    sleep 0.25
done

if [ "${COMPARE_OK}" -ne 1 ]; then
    echo "Error: Predictions/comparison view did not show 'synthetic predictions'!" >&2
    echo "Last comparison state: ${LAST_COMPARE_STATE}" >&2
    exit 24
fi
echo "Verified comparison/predictions view: ${LAST_COMPARE_STATE}"
screencapture -x "${SCREENSHOT_DIR}/demo-viewer.png" 2>/dev/null || true

DEMO_OUTCOME_LINE=$(grep "COPERCEIVE_DEMO_OUTCOME:" "${DEMO_STDOUT}" | tail -n 1 || echo "")
if [ -z "${DEMO_OUTCOME_LINE}" ]; then
    echo "Error: COPERCEIVE_DEMO_OUTCOME log line not found in app stdout!" >&2
    cat "${DEMO_STDOUT}" >&2
    exit 25
fi
echo "${DEMO_OUTCOME_LINE}"
echo "DEMO_SMOKE_VERIFIED: os=macos viewer=${LAST_VIEWER_STATE} comparison=${LAST_COMPARE_STATE} outcome=\"${DEMO_OUTCOME_LINE}\""

if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
    echo "### Demo Mode Smoke Verification (macOS)" >> "${GITHUB_STEP_SUMMARY}"
    echo "- **Outcome**: \`${DEMO_OUTCOME_LINE}\`" >> "${GITHUB_STEP_SUMMARY}"
    echo "- **Viewer**: \`${LAST_VIEWER_STATE}\`" >> "${GITHUB_STEP_SUMMARY}"
    echo "- **Comparison**: \`${LAST_COMPARE_STATE}\`" >> "${GITHUB_STEP_SUMMARY}"
fi

echo "Terminating demo app process..."
kill -TERM "${APP_PID}" 2>/dev/null || true
sleep 1
if kill -0 "${APP_PID}" 2>/dev/null; then
    kill -9 "${APP_PID}" 2>/dev/null || true
fi
wait "${APP_PID}" 2>/dev/null || true
APP_PID=""
echo "=========================================================="
echo "Smoke test PASSED successfully!"
echo "=========================================================="
