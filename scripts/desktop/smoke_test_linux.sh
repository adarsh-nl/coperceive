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
export APPIMAGE_EXTRACT_AND_RUN=1

# Require bundled Python in installed package; never fall back to repo or system python
PYTHON_BIN=""
for candidate in \
    "/usr/lib/Coperceive/resources/python/bin/python3" \
    "/usr/lib/coperceive-desktop/resources/python/bin/python3" \
    "/usr/lib/coperceive/resources/python/bin/python3" \
    "$(dirname "${APP_EXE}")/../lib/Coperceive/resources/python/bin/python3" \
    "$(dirname "${APP_EXE}")/../lib/coperceive-desktop/resources/python/bin/python3"; do
    if [ -x "${candidate}" ]; then
        PYTHON_BIN="$(readlink -f "${candidate}")"
        break
    fi
done

if [ -z "${PYTHON_BIN}" ]; then
    echo "Error: Bundled Python interpreter not found in installed package or resources!" >&2
    exit 2
fi

echo "Found bundled Python: ${PYTHON_BIN}"
echo "Python version: $("${PYTHON_BIN}" --version)"
unset COPERCEIVE_PYTHON

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

echo "Checking GET ${APP_ORIGIN}/api/health..."
curl -s -f -H "Authorization: Bearer ${APP_TOKEN}" "${APP_ORIGIN}/api/health" >/dev/null

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

CTRL_PORT=$(sed -n '4p' "${MARKER_FILE}" || true)
CTRL_SECRET=$(sed -n '5p' "${MARKER_FILE}" || true)
if [ -z "${CTRL_PORT}" ] || [ "${CTRL_PORT}" = "0" ] || [ -z "${CTRL_SECRET}" ]; then
    echo "Error: Controller port/secret missing in ${MARKER_FILE}!" >&2
    exit 6
fi

echo "Capturing viewer screenshot..."
VIEWER_READY=0
for i in {1..50}; do
    RESP=$(curl -s -X POST -H "Authorization: Bearer ${CTRL_SECRET}" -H "Content-Type: application/json" -d '{"script":"Boolean(window.__COPERCEIVE_DESKTOP__)"}' "http://127.0.0.1:${CTRL_PORT}/eval" || true)
    if echo "${RESP}" | grep -q '"result":true'; then
        VIEWER_READY=1
        break
    fi
    sleep 0.2
done
if [ "${VIEWER_READY}" -ne 1 ]; then
    echo "Error: Viewer webview did not respond to controller eval probe within timeout!" >&2
    exit 6
fi
capture_screenshot "viewer"

echo "Navigating to Dataset View and capturing screenshot..."
curl -s -f -X POST -H "Authorization: Bearer ${CTRL_SECRET}" -H "Content-Type: application/json" -d "{}" "http://127.0.0.1:${CTRL_PORT}/show-library" >/dev/null
curl -s -f -X POST -H "Authorization: Bearer ${CTRL_SECRET}" -H "Content-Type: application/json" -d "{\"window\":\"main\",\"script\":\"window.location.hash='#/connect?tab=hpc'; true;\"}" "http://127.0.0.1:${CTRL_PORT}/eval" >/dev/null
HPC_READY=0
for i in {1..30}; do
    RESP=$(curl -s -X POST -H "Authorization: Bearer ${CTRL_SECRET}" -H "Content-Type: application/json" -d '{"window":"main","script":"Boolean(window.location.hash.includes(\"hpc\"))"}' "http://127.0.0.1:${CTRL_PORT}/eval" || true)
    if echo "${RESP}" | grep -q '"result":true'; then
        HPC_READY=1
        break
    fi
    sleep 0.2
done
if [ "${HPC_READY}" -ne 1 ]; then
    echo "Error: Main window did not navigate to HPC tab within timeout!" >&2
    exit 7
fi
capture_screenshot "dataset_view"

echo "Terminating auto-connect app process..."
kill -TERM "${APP_PID}" 2>/dev/null || true
wait "${APP_PID}" 2>/dev/null || true
APP_PID=""

echo ""
echo "--- First-Run Welcome Card & Demo Mode Verification (Linux) ---"
DEMO_STATE_DIR="${SMOKE_TMPDIR}/demo-state"
mkdir -p "${DEMO_STATE_DIR}"
DEMO_MARKER="${SMOKE_TMPDIR}/demo_marker.txt"
DEMO_STDOUT="${SMOKE_TMPDIR}/demo_stdout.log"
DEMO_STDERR="${SMOKE_TMPDIR}/demo_stderr.log"

unset COPERCEIVE_AUTOCONNECT_ROOT
unset COPERCEIVE_DESKTOP_AUTOCONNECT_ROOT
unset COPERCEIVE_PYTHON

echo "Starting coperceive-desktop on empty state dir: ${DEMO_STATE_DIR}..."
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

echo "Polling for first-run library location prompt on empty state dir..."
PROMPT_READY=0
for i in {1..120}; do
    RESP=$(curl -s -f -X POST \
        -H "Authorization: Bearer ${DEMO_CTRL_SECRET}" \
        -H "Content-Type: application/json" \
        -d '{"window":"main","script":"Boolean(Array.from(document.querySelectorAll(\"button\")).find(b => (b.textContent || \"\").includes(\"Use default\")))"}' \
        "http://127.0.0.1:${DEMO_CTRL_PORT}/eval" 2>/dev/null || echo "")
    if echo "${RESP}" | grep -q '"result":true'; then
        PROMPT_READY=1
        break
    fi
    sleep 0.25
done

if [ "${PROMPT_READY}" -ne 1 ]; then
    PAGE_STATE=$(curl -s -X POST \
        -H "Authorization: Bearer ${DEMO_CTRL_SECRET}" \
        -H "Content-Type: application/json" \
        -d '{"window":"main","script":"document.body ? document.body.innerText : \"<no body>\""}' \
        "http://127.0.0.1:${DEMO_CTRL_PORT}/eval" 2>/dev/null || echo "<eval failed>")
    echo "Error: First-run library location prompt ('Use default') did not appear within timeout!" >&2
    echo "Main window page content: ${PAGE_STATE}" >&2
    cat "${DEMO_STDERR}" >&2
    exit 21
fi
echo "Verified: First-run library location prompt appeared. Clicking 'Use default'..."
USE_DEFAULT_RESP=$(curl -s -f -X POST \
    -H "Authorization: Bearer ${DEMO_CTRL_SECRET}" \
    -H "Content-Type: application/json" \
    -d '{"window":"main","script":"(() => { const btn = Array.from(document.querySelectorAll(\"button\")).find(b => (b.textContent || \"\").includes(\"Use default\")); if (!btn) return \"missing\"; btn.click(); return \"clicked\"; })()"}' \
    "http://127.0.0.1:${DEMO_CTRL_PORT}/eval")
if ! echo "${USE_DEFAULT_RESP}" | grep -q '"clicked"'; then
    echo "Error: Failed to click 'Use default' button: ${USE_DEFAULT_RESP}" >&2
    exit 21
fi

echo "Polling for first-run Welcome Card after confirming library location (no injected seed)..."
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
capture_screenshot "welcome_card"

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
VIEWER_CHECK_SCRIPT='{"window":"viewer","script":"(() => { document.querySelector(\"section.cam-strip[data-open=\\\"true\\\"] .cam-thumb\")?.scrollIntoView?.(); const title = (document.querySelector(\".dataset-badge .dataset-name\")?.textContent || \"\").trim(); const points = [...document.querySelectorAll(\".agent-points\")].reduce((s, el) => s + (parseInt((el.getAttribute(\"title\") || \"0\").split(\" \")[0], 10) || 0), 0); const cameras = [...document.querySelectorAll(\"section.cam-strip[data-open=\\\"true\\\"] .cam-thumb canvas\")].filter(c => c.width > 0 && c.height > 0).length; const boxes = parseInt((document.querySelector(\"button.eye[aria-label=\\\"Boxes\\\"] .eye-meta\")?.textContent || \"0\").trim(), 10) || 0; const ready = title === \"Demo (synthetic)\" && points > 0 && cameras >= 1 && boxes > 0; return { ready, title, points, cameras, boxes }; })()"}'

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

echo "Opening #prediction-output menu, selecting 'cav-b · noisy', and verifying 'synthetic predictions' evaluation view..."
COMPARE_CHECK_SCRIPT='{"window":"viewer","script":"(() => { const runBtn = document.querySelector(\"#prediction-run\"); const outBtn = document.querySelector(\"#prediction-output\"); const list = document.querySelector(\"ul.menu-list[aria-labelledby=\\\"prediction-output\\\"]\"); const curOut = (outBtn?.textContent || \"\").trim(); if (!curOut.includes(\"cav-b\")) { if (!list && outBtn) { outBtn.click(); } else if (list) { const opt = [...list.querySelectorAll(\"button[role=\\\"option\\\"]\")].find(b => (b.textContent || \"\").includes(\"cav-b\")); if (opt) opt.click(); } } const predRun = (runBtn?.textContent || \"\").trim(); const evalHead = (document.querySelector(\".eval-head\")?.textContent || \"\").trim(); const ok = predRun.includes(\"synthetic predictions\") && evalHead.includes(\"cav-b\") && evalHead.includes(\"synthetic predictions\"); return { ok, predRun, output: (outBtn?.textContent || \"\").trim(), evalHead }; })()"}'

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
    echo "Error: Predictions/evaluation view did not show 'synthetic predictions' for selected output!" >&2
    echo "Last predictions state: ${LAST_COMPARE_STATE}" >&2
    exit 24
fi
echo "Verified predictions/evaluation view: ${LAST_COMPARE_STATE}"
capture_screenshot "demo_viewer"

DEMO_OUTCOME_LINE=$(grep "COPERCEIVE_DEMO_OUTCOME:" "${DEMO_STDOUT}" | tail -n 1 || echo "")
if [ -z "${DEMO_OUTCOME_LINE}" ]; then
    echo "Error: COPERCEIVE_DEMO_OUTCOME log line not found in app stdout!" >&2
    cat "${DEMO_STDOUT}" >&2
    exit 25
fi
echo "${DEMO_OUTCOME_LINE}"
OUTCOME_PYTHON=$(echo "${DEMO_OUTCOME_LINE}" | sed -n 's/.*python=\([^ ]*\).*/\1/p')
case "${OUTCOME_PYTHON}" in
    /usr/lib/Coperceive/resources/python/*|/usr/lib/coperceive-desktop/resources/python/*|/usr/lib/coperceive/resources/python/*)
        if [ ! -x "${OUTCOME_PYTHON}" ]; then
            echo "Error: Outcome python is not executable: ${OUTCOME_PYTHON}" >&2
            exit 25
        fi
        ;;
    *)
        echo "Error: Demo did not run with the installed package's bundled Python! Expected under /usr/lib/Coperceive/resources/python/, got '${OUTCOME_PYTHON}'" >&2
        exit 25
        ;;
esac
echo "Verified: Demo ran with bundled Python inside installed package: ${OUTCOME_PYTHON}"
echo "DEMO_SMOKE_VERIFIED: os=linux viewer=${LAST_VIEWER_STATE} predictions=${LAST_COMPARE_STATE} outcome=\"${DEMO_OUTCOME_LINE}\""

if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
    echo "### Demo Mode Smoke Verification (Linux)" >> "${GITHUB_STEP_SUMMARY}"
    echo "- **Outcome**: \`${DEMO_OUTCOME_LINE}\`" >> "${GITHUB_STEP_SUMMARY}"
    echo "- **Viewer**: \`${LAST_VIEWER_STATE}\`" >> "${GITHUB_STEP_SUMMARY}"
    echo "- **Comparison**: \`${LAST_COMPARE_STATE}\`" >> "${GITHUB_STEP_SUMMARY}"
fi

SMOKE_SUCCESS=1
echo "Linux Desktop smoke test PASSED successfully."
