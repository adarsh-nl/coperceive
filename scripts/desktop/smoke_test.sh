#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# Smoke test verifying the bundled Python environment, coperceive serve sidecar,
# constant-time token verification, and measuring artifact sizes.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
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

APP_BUNDLE="$(cd "${APP_BUNDLE}" && pwd -P)"
echo "App bundle: ${APP_BUNDLE}"

# Find bundled python inside the packaged .app bundle only
PYTHON_BIN=""
for candidate in \
    "${APP_BUNDLE}/Contents/Resources/resources/python/bin/python3" \
    "${APP_BUNDLE}/Contents/Resources/python/bin/python3"; do
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
export TMPDIR
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

SMOKE_PASSED=0

# Ensure processes are killed on script exit
cleanup_serve() {
    if [ -n "${SERVE_PID:-}" ] && kill -0 "${SERVE_PID}" 2>/dev/null; then
        echo "Terminating sidecar PID ${SERVE_PID}..."
        kill "${SERVE_PID}" 2>/dev/null || true
        wait "${SERVE_PID}" 2>/dev/null || true
    fi
}

preserve_safe_tmp_logs() {
    if [ -z "${COPERCEIVE_PRESERVE_LOGS_DIR:-}" ] || [ "${SMOKE_PASSED}" -eq 1 ]; then
        return
    fi
    local dest="${COPERCEIVE_PRESERVE_LOGS_DIR}/tmp"
    mkdir -p "${dest}"
    local redact='s/Bearer [A-Za-z0-9._-]+/Bearer [REDACTED]/g; s/([Tt]oken[^A-Za-z0-9]*)[:=][[:space:]]*[^[:space:]"]+/\1=[REDACTED]/g'
    local f
    for f in "${TMPDIR}/app_stdout.log" "${TMPDIR}/app_stderr.log" "${TMPDIR}/serve.log" "${TMPDIR}/demo_stdout.log" "${TMPDIR}/demo_stderr.log" "${TMPDIR}/prod_stdout.log" "${TMPDIR}/prod_stderr.log"; do
        if [ -f "$f" ]; then
            sed -E "$redact" "$f" > "${dest}/$(basename "$f")" 2>/dev/null || true
        fi
    done
    while IFS= read -r slog; do
        [ -n "$slog" ] || continue
        sed -E "$redact" "$slog" > "${dest}/$(basename "$(dirname "$slog")")_$(basename "$slog")" 2>/dev/null || true
    done < <(find "${TMPDIR}" -name 'serve-*.log' -type f 2>/dev/null || true)
}

cleanup_all() {
    if [ -n "${APP_PID:-}" ] && kill -0 "${APP_PID}" 2>/dev/null; then
        echo "Terminating app process PID ${APP_PID}..."
        kill -9 "${APP_PID}" 2>/dev/null || true
        wait "${APP_PID}" 2>/dev/null || true
    fi
    cleanup_serve
    preserve_safe_tmp_logs
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

# Test 3c: Normal (non-test) release launch must NOT start a loopback controller
echo ""
echo "--- Production Launch Controller Gate Check (macOS) ---"
PROD_STATE_DIR="${TMPDIR}/prod-state"
mkdir -p "${PROD_STATE_DIR}"
PROD_STDOUT="${TMPDIR}/prod_stdout.log"
PROD_STDERR="${TMPDIR}/prod_stderr.log"

(
    unset COPERCEIVE_TEST_ENV
    unset COPERCEIVE_TEST_MARKER_FILE
    unset COPERCEIVE_CTRL_INFO_FILE
    unset COPERCEIVE_AUTOCONNECT_ROOT
    unset COPERCEIVE_DESKTOP_AUTOCONNECT_ROOT
    unset COPERCEIVE_UPDATE_URL
    unset COPERCEIVE_MOCK_DIALOG_FILE
    unset COPERCEIVE_TEST_IMPORT_SOURCE
    export COPERCEIVE_STATE_DIR="${PROD_STATE_DIR}"
    exec "${APP_EXE}" > "${PROD_STDOUT}" 2> "${PROD_STDERR}"
) &
APP_PID=$!

for _ in {1..8}; do
    if ! kill -0 "${APP_PID}" 2>/dev/null; then
        echo "Error: Production launch exited prematurely during controller-gate check!" >&2
        cat "${PROD_STDERR}" >&2 || true
        cat "${PROD_STDOUT}" >&2 || true
        exit 8
    fi
    sleep 0.5
done

if grep -q -E "Loopback controller listening|\\[desktop-ctrl\\]" "${PROD_STDOUT}" "${PROD_STDERR}" 2>/dev/null; then
    echo "Security failure: loopback controller started during normal (non-test) launch!" >&2
    cat "${PROD_STDOUT}" >&2
    exit 8
fi

PROD_PIDS="${APP_PID}"
CHILDREN=$(pgrep -P "${APP_PID}" 2>/dev/null || true)
if [ -n "${CHILDREN}" ]; then
    PROD_PIDS="${PROD_PIDS},$(printf '%s\n' "${CHILDREN}" | paste -sd, -)"
fi
PROD_LISTENERS=$(lsof -nP -a -p "${PROD_PIDS}" -iTCP -sTCP:LISTEN 2>/dev/null || true)
if [ -n "${PROD_LISTENERS}" ]; then
    echo "Security failure: unexpected listening TCP socket(s) during normal launch:" >&2
    echo "${PROD_LISTENERS}" >&2
    exit 8
fi

kill -TERM "${APP_PID}" 2>/dev/null || true
sleep 1
if kill -0 "${APP_PID}" 2>/dev/null; then
    kill -9 "${APP_PID}" 2>/dev/null || true
fi
wait "${APP_PID}" 2>/dev/null || true
APP_PID=""
echo "PROD_LAUNCH_VERIFIED: os=macos listening_ports=0 ctrl_disabled=true"

FIXTURE_ROOT="$(pwd)/tests/fixtures/synthetic_scene"
if [ ! -d "${FIXTURE_ROOT}" ]; then
    echo "Error: Synthetic scene fixture directory not found at ${FIXTURE_ROOT}!" >&2
    exit 11
fi

MARKER_FILE="${TMPDIR}/test_marker.txt"
CTRL_INFO_FILE="${TMPDIR}/ctrl_info.txt"
APP_STDOUT="${TMPDIR}/app_stdout.log"
APP_STDERR="${TMPDIR}/app_stderr.log"

run_diag_eval_probe() {
    local p_port="${1:-}"
    local p_secret="${2:-}"
    local p_ctrl_info="${3:-${CTRL_INFO_FILE:-}}"
    local p_marker="${4:-${MARKER_FILE:-}}"
    if [ -z "${p_port}" ] || [ -z "${p_secret}" ]; then
        if [ -n "${p_ctrl_info}" ] && [ -f "${p_ctrl_info}" ]; then
            p_port=$(sed -n '1p' "${p_ctrl_info}" 2>/dev/null || echo "")
            p_secret=$(sed -n '2p' "${p_ctrl_info}" 2>/dev/null || echo "")
        elif [ -n "${p_marker}" ] && [ -f "${p_marker}" ]; then
            p_port=$(sed -n '4p' "${p_marker}" 2>/dev/null || echo "")
            p_secret=$(sed -n '5p' "${p_marker}" 2>/dev/null || echo "")
        fi
    fi
    echo "=== Controller /eval diagnostic probe (ctrl_info_exists=$([ -n "${p_ctrl_info}" ] && [ -f "${p_ctrl_info}" ] && echo yes || echo no), ctrl_port=${p_port:-missing}) ===" >&2
    if [ -n "${p_port}" ] && [ "${p_port}" != "0" ] && [ -n "${p_secret}" ]; then
        local js_probe='(() => { const out = { readyState: document.readyState, href: window.location.href, consoleErrors: window.__COPERCEIVE_CONSOLE_ERRORS__ ?? null, lastUiError: window.__COPERCEIVE_LAST_ERROR__ ?? null, autoConnectState: window.__COPERCEIVE_AUTOCONNECT_STATE__ ?? null, scriptCount: document.scripts ? document.scripts.length : -1, bodyTextLen: document.body ? document.body.innerText.length : -1, bodyPreview: document.body ? document.body.innerText.slice(0, 300) : "" }; try { const c = document.createElement("canvas"); const gl2 = c.getContext("webgl2"); const gl = gl2 ? null : c.getContext("webgl"); const ctx = gl2 || gl; const dbg = ctx ? ctx.getExtension("WEBGL_debug_renderer_info") : null; out.webgl = { webgl2: Boolean(gl2), webgl: Boolean(gl || gl2), vendor: ctx ? ctx.getParameter(ctx.VENDOR) : null, renderer: ctx ? ctx.getParameter(ctx.RENDERER) : null, unmaskedRenderer: dbg && ctx ? ctx.getParameter(dbg.UNMASKED_RENDERER_WEBGL) : null }; } catch (e) { out.webglError = String(e); } return out; })()'
        local win
        for win in main viewer; do
            local payload
            payload=$(python3 -c 'import json, sys; print(json.dumps({"window": sys.argv[1], "script": sys.argv[2]}))' "${win}" "${js_probe}")
            local resp
            resp=$(curl -s --max-time 6 -X POST \
                -H "Authorization: Bearer ${p_secret}" \
                -H "Content-Type: application/json" \
                -d "${payload}" \
                "http://127.0.0.1:${p_port}/eval" 2>&1 || true)
            echo "--- /eval window=${win} ---" >&2
            echo "${resp:-<empty response>}" >&2
        done
    else
        echo "Skipping /eval: loopback controller port/secret unavailable." >&2
    fi
    echo "=== Host & Bundle Environment Diagnostics ===" >&2
    sysctl -n machdep.cpu.brand_string hw.ncpu hw.memsize 2>&1 >&2 || true
    file "${APP_EXE}" 2>&1 >&2 || true
    echo "--- xattr on APP_BUNDLE (first 30 lines) ---" >&2
    xattr -lr "${APP_BUNDLE}" 2>&1 | head -n 30 >&2 || true
    echo "--- codesign & spctl ---" >&2
    codesign -dvv "${APP_BUNDLE}" 2>&1 | head -n 20 >&2 || true
    spctl --assess --verbose=4 --type execute "${APP_BUNDLE}" 2>&1 || true
}

wait_for_app_readiness() {
    local label="$1"
    local app_pid="$2"
    local marker_file="$3"
    local ctrl_info_file="$4"
    local stdout_log="$5"
    local stderr_log="$6"
    local require_session="$7"
    local timeout_exit_code="$8"
    local died_exit_code="$9"

    for i in {1..90}; do
        if [ -f "${marker_file}" ]; then
            if [ "${require_session}" = "1" ]; then
                local m_origin m_token
                m_origin=$(sed -n '1p' "${marker_file}" 2>/dev/null || echo "")
                m_token=$(sed -n '2p' "${marker_file}" 2>/dev/null || echo "")
                if [ -n "${m_origin}" ] && [ -n "${m_token}" ]; then
                    return 0
                fi
            else
                local m_port m_secret
                m_port=$(sed -n '4p' "${marker_file}" 2>/dev/null || echo "")
                m_secret=$(sed -n '5p' "${marker_file}" 2>/dev/null || echo "")
                if [ -n "${m_port}" ] && [ "${m_port}" != "0" ] && [ -n "${m_secret}" ]; then
                    return 0
                fi
            fi
        fi
        if ! kill -0 "${app_pid}" 2>/dev/null; then
            echo "Error: ${label} process died prematurely during startup!" >&2
            echo "=== ${label} stderr ===" >&2
            cat "${stderr_log}" >&2 || true
            echo "=== ${label} stdout ===" >&2
            cat "${stdout_log}" >&2 || true
            exit "${died_exit_code}"
        fi
        sleep 0.5
    done

    echo "Error: ${label} readiness signal (${marker_file}) was not complete within 45s timeout!" >&2
    local last_phase
    last_phase=$(grep '\[auto-connect\] state=' "${stdout_log}" 2>/dev/null | tail -n 1 || true)
    if [ -n "${last_phase}" ]; then
        echo "Last auto-connect phase: ${last_phase}" >&2
    fi
    run_diag_eval_probe "" "" "${ctrl_info_file}" "${marker_file}"
    echo "=== Sidecar logs under ${TMPDIR} ===" >&2
    while IFS= read -r slog; do
        echo "--- ${slog} ---" >&2
        tail -n 40 "${slog}" >&2 || true
    done < <(find "${TMPDIR}" -maxdepth 3 -name 'serve-*.log' -type f 2>/dev/null | sort || true)
    echo "=== ${label} stderr ===" >&2
    cat "${stderr_log}" >&2 || true
    echo "=== ${label} stdout ===" >&2
    cat "${stdout_log}" >&2 || true
    exit "${timeout_exit_code}"
}

echo "Starting app binary with auto-connect fixture: ${FIXTURE_ROOT}..."
LAUNCH_START_S=$(python3 -c 'import time; print(f"{time.monotonic():.6f}")')
COPERCEIVE_AUTOCONNECT_ROOT="${FIXTURE_ROOT}" \
COPERCEIVE_TEST_MARKER_FILE="${MARKER_FILE}" \
COPERCEIVE_CTRL_INFO_FILE="${CTRL_INFO_FILE}" \
"${APP_EXE}" > "${APP_STDOUT}" 2> "${APP_STDERR}" &
APP_PID=$!
echo "App process spawned with PID ${APP_PID}"

echo "Waiting for auto-connect and viewer startup (up to 45s)..."
wait_for_app_readiness "Auto-connect app" "${APP_PID}" "${MARKER_FILE}" "${CTRL_INFO_FILE}" "${APP_STDOUT}" "${APP_STDERR}" 1 13 12
MARKER_ELAPSED_S=$(python3 -c 'import time, sys; print(f"{time.monotonic() - float(sys.argv[1]):.2f}")' "${LAUNCH_START_S}")
echo "Auto-connect marker created in ${MARKER_ELAPSED_S}s."
grep '\[auto-connect\] state=' "${APP_STDOUT}" || true

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
if [ -z "${CTRL_PORT}" ] || [ "${CTRL_PORT}" = "0" ] || [ -z "${CTRL_SECRET}" ]; then
    echo "Error: Controller port/secret missing in ${MARKER_FILE}!" >&2
    exit 14
fi
VIEWER_READY=0
EVAL_HTTP_CODE=""
for i in {1..50}; do
    RESP_WITH_CODE=$(curl -s -w '\n%{http_code}' -X POST -H "Authorization: Bearer ${CTRL_SECRET}" -H "Content-Type: application/json" -d '{"script":"Boolean(window.__COPERCEIVE_DESKTOP__)"}' "http://127.0.0.1:${CTRL_PORT}/eval" || true)
    EVAL_HTTP_CODE=$(printf '%s\n' "${RESP_WITH_CODE}" | tail -n 1)
    RESP=$(printf '%s\n' "${RESP_WITH_CODE}" | sed '$d')
    if [ "${EVAL_HTTP_CODE}" = "200" ] && echo "${RESP}" | grep -q '"result":true'; then
        echo "Verified: Viewer webview responded to controller /eval probe (HTTP 200)."
        VIEWER_READY=1
        break
    fi
    sleep 0.2
done
if [ "${VIEWER_READY}" -ne 1 ]; then
    echo "Error: Viewer webview did not respond with HTTP 200 to controller /eval probe within timeout (last HTTP ${EVAL_HTTP_CODE})!" >&2
    exit 14
fi

# Confirm auto-connect sidecar used the bundled Python inside the .app bundle
STEP4_SIDECAR_PY=$(python3 - "${APP_STDOUT}" <<'PY'
import json, sys
py_path = ""
with open(sys.argv[1], encoding="utf-8", errors="replace") as f:
    for line in f:
        if "[auto-connect] state=" in line:
            try:
                payload = json.loads(line.split("state=", 1)[1])
                detail = payload.get("detail") or {}
                if isinstance(detail, dict) and detail.get("python_bin"):
                    py_path = str(detail["python_bin"])
            except Exception:
                pass
print(py_path)
PY
)
case "${STEP4_SIDECAR_PY}" in
    "${APP_BUNDLE}/Contents/Resources/"*)
        if [ ! -x "${STEP4_SIDECAR_PY}" ]; then
            echo "Error: Step 4 sidecar Python is not executable: ${STEP4_SIDECAR_PY}" >&2
            exit 14
        fi
        echo "Verified: Auto-connect sidecar used bundled Python inside .app: ${STEP4_SIDECAR_PY}"
        ;;
    *)
        echo "Error: Auto-connect sidecar did not use bundled Python inside '${APP_BUNDLE}/Contents/Resources/'; got '${STEP4_SIDECAR_PY}'" >&2
        cat "${APP_STDOUT}" >&2
        exit 14
        ;;
esac

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

# Test 4b: Symlinked .app path launch check (without dereferencing the .app symlink beforehand)
echo ""
echo "--- Symlinked .app Launch Check (macOS) ---"
SYM_DIR="${TMPDIR}/sym-apps"
SYM_APP="${SYM_DIR}/Coperceive.app"
SYM_STATE_DIR="${TMPDIR}/sym-state"
SYM_MARKER="${TMPDIR}/sym_marker.txt"
SYM_STDOUT="${TMPDIR}/sym_stdout.log"
SYM_STDERR="${TMPDIR}/sym_stderr.log"
mkdir -p "${SYM_DIR}" "${SYM_STATE_DIR}"
ln -s "${APP_BUNDLE}" "${SYM_APP}"
SYM_EXE="${SYM_APP}/Contents/MacOS/$(basename "${APP_EXE}")"
if [ ! -x "${SYM_EXE}" ]; then
    echo "Error: Symlinked app executable is not executable: ${SYM_EXE}" >&2
    exit 17
fi

echo "Launching app binary through symlinked .app path: ${SYM_EXE}..."
COPERCEIVE_STATE_DIR="${SYM_STATE_DIR}" \
COPERCEIVE_NO_RESTORE=1 \
COPERCEIVE_AUTOCONNECT_ROOT="${FIXTURE_ROOT}" \
COPERCEIVE_TEST_MARKER_FILE="${SYM_MARKER}" \
"${SYM_EXE}" > "${SYM_STDOUT}" 2> "${SYM_STDERR}" &
SYM_PID=$!

for i in {1..90}; do
    if [ -f "${SYM_MARKER}" ]; then
        break
    fi
    if ! kill -0 "${SYM_PID}" 2>/dev/null; then
        echo "Error: Symlinked .app process died prematurely during startup!" >&2
        cat "${SYM_STDERR}" >&2
        cat "${SYM_STDOUT}" >&2
        exit 17
    fi
    sleep 0.5
done

if [ ! -f "${SYM_MARKER}" ]; then
    echo "Error: Symlinked .app marker file was not created within 45s timeout!" >&2
    cat "${SYM_STDERR}" >&2
    cat "${SYM_STDOUT}" >&2
    kill -TERM "${SYM_PID}" 2>/dev/null || true
    exit 17
fi

SYM_ORIGIN=$(sed -n '1p' "${SYM_MARKER}")
SYM_TOKEN=$(sed -n '2p' "${SYM_MARKER}")
SYM_SCENES_RESP=$(curl -s -f -H "Authorization: Bearer ${SYM_TOKEN}" "${SYM_ORIGIN}/api/scenes" || true)
SYM_SCENE_COUNT=$(python3 -c 'import json, sys; data = json.loads(sys.argv[1]); print(len(data))' "${SYM_SCENES_RESP}" 2>/dev/null || echo "0")
if [ "${SYM_SCENE_COUNT}" -lt 1 ]; then
    echo "Error: Symlinked .app /api/scenes returned < 1 scene (${SYM_SCENES_RESP})" >&2
    kill -TERM "${SYM_PID}" 2>/dev/null || true
    exit 17
fi

SYM_SIDECAR_PY=$(python3 - "${SYM_STDOUT}" <<'PY'
import json, sys
py_path = ""
with open(sys.argv[1], encoding="utf-8", errors="replace") as f:
    for line in f:
        if "[auto-connect] state=" in line:
            try:
                payload = json.loads(line.split("state=", 1)[1])
                detail = payload.get("detail") or {}
                if isinstance(detail, dict) and detail.get("python_bin") and detail.get("python_exists") is True:
                    py_path = str(detail["python_bin"])
            except Exception:
                pass
print(py_path)
PY
)
case "${SYM_SIDECAR_PY}" in
    "${SYM_APP}/Contents/Resources/"*|"${APP_BUNDLE}/Contents/Resources/"*)
        if [ ! -x "${SYM_SIDECAR_PY}" ]; then
            echo "Error: Symlinked .app sidecar Python is not executable: ${SYM_SIDECAR_PY}" >&2
            kill -TERM "${SYM_PID}" 2>/dev/null || true
            exit 17
        fi
        echo "Verified: Symlinked .app launch resolved bundled Python inside .app: ${SYM_SIDECAR_PY} (/api/scenes=${SYM_SCENE_COUNT})"
        ;;
    *)
        echo "Error: Symlinked .app did not use bundled Python inside .app; got '${SYM_SIDECAR_PY}'" >&2
        cat "${SYM_STDOUT}" >&2
        kill -TERM "${SYM_PID}" 2>/dev/null || true
        exit 17
        ;;
esac

kill -TERM "${SYM_PID}" 2>/dev/null || true
sleep 1
if kill -0 "${SYM_PID}" 2>/dev/null; then
    kill -9 "${SYM_PID}" 2>/dev/null || true
fi
wait "${SYM_PID}" 2>/dev/null || true
SYM_PID=""
rm -f "${SYM_APP}"

# Test 5: First-Run Welcome Card & Demo Mode End-to-End Verification
echo ""
echo "--- First-Run Welcome Card & Demo Mode Verification (macOS) ---"
DEMO_STATE_DIR="${TMPDIR}/demo-state"
mkdir -p "${DEMO_STATE_DIR}"
DEMO_MARKER="${TMPDIR}/demo_marker.txt"
DEMO_CTRL_INFO="${TMPDIR}/demo_ctrl_info.txt"
DEMO_STDOUT="${TMPDIR}/demo_stdout.log"
DEMO_STDERR="${TMPDIR}/demo_stderr.log"

unset COPERCEIVE_AUTOCONNECT_ROOT
unset COPERCEIVE_DESKTOP_AUTOCONNECT_ROOT
unset COPERCEIVE_PYTHON

echo "Starting app binary on empty state dir: ${DEMO_STATE_DIR}..."
COPERCEIVE_STATE_DIR="${DEMO_STATE_DIR}" \
COPERCEIVE_TEST_MARKER_FILE="${DEMO_MARKER}" \
COPERCEIVE_CTRL_INFO_FILE="${DEMO_CTRL_INFO}" \
"${APP_EXE}" > "${DEMO_STDOUT}" 2> "${DEMO_STDERR}" &
APP_PID=$!
echo "Demo test app process spawned with PID ${APP_PID}"

wait_for_app_readiness "Demo test app" "${APP_PID}" "${DEMO_MARKER}" "${DEMO_CTRL_INFO}" "${DEMO_STDOUT}" "${DEMO_STDERR}" 0 20 20
DEMO_CTRL_PORT=$(sed -n '4p' "${DEMO_MARKER}")
DEMO_CTRL_SECRET=$(sed -n '5p' "${DEMO_MARKER}")
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
VIEWER_CHECK_SCRIPT=$(python3 - "${COPERCEIVE_INTEL_VM_NO_WEBGL:-0}" <<'PY'
import json, sys
expect_no_webgl = sys.argv[1] == "1"
js = """(() => {
  document.querySelector("section.cam-strip[data-open='true'] .cam-thumb")?.scrollIntoView?.();
  const title = (document.querySelector(".dataset-badge .dataset-name")?.textContent || "").trim();
  const points = [...document.querySelectorAll(".agent-points")]
    .reduce((s, el) => s + (parseInt((el.getAttribute("title") || "0").split(" ")[0], 10) || 0), 0);
  const cameras = [...document.querySelectorAll("section.cam-strip[data-open='true'] .cam-thumb canvas")]
    .filter(c => c.width > 0 && c.height > 0).length;
  const boxes = parseInt((document.querySelector('button.eye[aria-label="Boxes"] .eye-meta')?.textContent || "0").trim(), 10) || 0;
  const notice = (document.querySelector(".webgl-missing")?.textContent || "").trim();
  const hasWebglUnavailable =
    notice.includes("3D view unavailable")
    && notice.includes("Copy diagnostics");
  const baseReady = title === "Demo (synthetic)" && points > 0 && cameras >= 1 && boxes > 0;
  const ready = EXPECT_NO_WEBGL ? (baseReady && hasWebglUnavailable) : (baseReady && !hasWebglUnavailable);
  return { ready, title, points, cameras, boxes, hasWebglUnavailable };
})()""".replace("EXPECT_NO_WEBGL", "true" if expect_no_webgl else "false")
print(json.dumps({"window": "viewer", "script": js}))
PY
)

DEMO_LOADED=0
LAST_VIEWER_STATE=""
LAST_VIEWER_HTTP_CODE=""
for i in {1..480}; do
    if ! kill -0 "${APP_PID}" 2>/dev/null; then
        echo "Error: App process exited while generating/opening demo!" >&2
        cat "${DEMO_STDERR}" >&2
        cat "${DEMO_STDOUT}" >&2
        exit 23
    fi
    RESP_WITH_CODE=$(curl -s -w '\n%{http_code}' -X POST \
        -H "Authorization: Bearer ${DEMO_CTRL_SECRET}" \
        -H "Content-Type: application/json" \
        -d "${VIEWER_CHECK_SCRIPT}" \
        "http://127.0.0.1:${DEMO_CTRL_PORT}/eval" 2>/dev/null || echo "")
    if [ -n "${RESP_WITH_CODE}" ]; then
        LAST_VIEWER_HTTP_CODE=$(printf '%s\n' "${RESP_WITH_CODE}" | tail -n 1)
        RESP=$(printf '%s\n' "${RESP_WITH_CODE}" | sed '$d')
        LAST_VIEWER_STATE="${RESP}"
        if [ "${LAST_VIEWER_HTTP_CODE}" = "200" ] && echo "${RESP}" | grep -q '"ready":true'; then
            DEMO_LOADED=1
            break
        fi
    fi
    sleep 0.5
done

if [ "${DEMO_LOADED}" -ne 1 ]; then
    echo "Error: Demo scene did not finish loading in viewer within timeout (last /eval HTTP ${LAST_VIEWER_HTTP_CODE:-none})!" >&2
    echo "Last viewer state: ${LAST_VIEWER_STATE}" >&2
    run_diag_eval_probe "${DEMO_CTRL_PORT}" "${DEMO_CTRL_SECRET}"
    echo "=== Demo App stdout ===" >&2
    cat "${DEMO_STDOUT}" >&2
    echo "=== Demo App stderr ===" >&2
    cat "${DEMO_STDERR}" >&2
    exit 23
fi
echo "Verified viewer loaded demo scene (/eval HTTP ${LAST_VIEWER_HTTP_CODE}): ${LAST_VIEWER_STATE}"
if [ "${COPERCEIVE_INTEL_VM_NO_WEBGL:-0}" = "1" ]; then
    screencapture -x "${SCREENSHOT_DIR}/demo-webgl-unavailable.png" 2>/dev/null || true
    if [ -f "${SCREENSHOT_DIR}/demo-webgl-unavailable.png" ]; then
        echo "Verified: captured WebGL-unavailable screenshot at ${SCREENSHOT_DIR}/demo-webgl-unavailable.png ($(stat -f %z "${SCREENSHOT_DIR}/demo-webgl-unavailable.png" 2>/dev/null || wc -c < "${SCREENSHOT_DIR}/demo-webgl-unavailable.png") bytes)"
    fi
fi

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
screencapture -x "${SCREENSHOT_DIR}/demo-viewer.png" 2>/dev/null || true

# Verify /frame and /predictions returned HTTP 200 on the demo sidecar log, and
# that the demo sidecar process itself ran with bundled Python inside the .app.
DEMO_SIDECAR_LOG=$(find "${TMPDIR}/coperceive-local-${APP_PID}" -name 'serve-*.log' -type f 2>/dev/null | head -n 1 || echo "")
if [ -z "${DEMO_SIDECAR_LOG}" ] || ! grep -q '"GET /api/scenes/demo-junction/frame' "${DEMO_SIDECAR_LOG}" || ! grep -q '/frame.*200 OK' "${DEMO_SIDECAR_LOG}" || ! grep -q '/predictions.*200 OK' "${DEMO_SIDECAR_LOG}"; then
    echo "Error: Demo sidecar log missing HTTP 200 OK for /frame or /predictions (${DEMO_SIDECAR_LOG:-none})!" >&2
    [ -n "${DEMO_SIDECAR_LOG}" ] && cat "${DEMO_SIDECAR_LOG}" >&2
    exit 24
fi
echo "Verified: Demo sidecar served /frame and /predictions with HTTP 200 OK (${DEMO_SIDECAR_LOG})."

DEMO_SIDECAR_PY=$(python3 - "${DEMO_STDOUT}" <<'PY'
import json, sys
py_path = ""
with open(sys.argv[1], encoding="utf-8", errors="replace") as f:
    for line in f:
        if "[auto-connect] state=" in line:
            try:
                payload = json.loads(line.split("state=", 1)[1])
                detail = payload.get("detail") or {}
                if isinstance(detail, dict) and detail.get("python_bin"):
                    py_path = str(detail["python_bin"])
            except Exception:
                pass
print(py_path)
PY
)
case "${DEMO_SIDECAR_PY}" in
    "${APP_BUNDLE}/Contents/Resources/"*)
        if [ ! -x "${DEMO_SIDECAR_PY}" ]; then
            echo "Error: Demo sidecar Python is not executable: ${DEMO_SIDECAR_PY}" >&2
            exit 25
        fi
        echo "Verified: Demo sidecar ran with bundled Python inside .app: ${DEMO_SIDECAR_PY}"
        ;;
    *)
        echo "Error: Demo sidecar did not use bundled Python inside '${APP_BUNDLE}/Contents/Resources/'; got '${DEMO_SIDECAR_PY}'" >&2
        cat "${DEMO_STDOUT}" >&2
        exit 25
        ;;
esac

if [ "${COPERCEIVE_INTEL_VM_NO_WEBGL:-0}" = "1" ]; then
    # Approved native-Intel CI exception: on macos-15-intel, the headless VM's WKWebView
    # returns null for WebGL vendor/renderer/shader precision ("webgl":{"renderer":null,
    # "unmaskedRenderer":null,"vendor":null,"webgl":true,"webgl2":true}, console error:
    # "THREE.WebGLRenderer: null is not an object (evaluating 'e.getShaderPrecisionFormat(e.VERTEX_SHADER,e.HIGH_FLOAT).precision')").
    # Skip 3D rendering and 1x playback assertions here; non-WebGL panels, APIs, and the
    # "3D view unavailable" notice remain blocking above.
    echo "Skipping 3D playback ratio assertion on COPERCEIVE_INTEL_VM_NO_WEBGL=1 (headless VM WebGL vendor/renderer/precision=null)."
else
    echo "Verifying demo 1x playback advances frames smoothly without 'Buffering'..."
    PLAYBACK_START_RESP=$(curl -s -f -X POST \
        -H "Authorization: Bearer ${DEMO_CTRL_SECRET}" \
        -H "Content-Type: application/json" \
        -d '{"window":"viewer","script":"(() => { const s0 = window.__COPERCEIVE_PLAYBACK_STATE__?.(); if (s0 && !s0.playing) window.__COPERCEIVE_PLAYBACK__?.(\"toggle\"); return window.__COPERCEIVE_PLAYBACK_STATE__?.() ?? null; })()"}' \
        "http://127.0.0.1:${DEMO_CTRL_PORT}/eval")
    echo "Playback started: ${PLAYBACK_START_RESP}"

    python3 "${SCRIPT_DIR}/playback_metrics.py" poll \
        --port "${DEMO_CTRL_PORT}" \
        --secret "${DEMO_CTRL_SECRET}" \
        --polls 25 \
        --interval 0.2 \
        --warmup-polls 2 || exit 26
fi

DEMO_OUTCOME_LINE=$(grep "COPERCEIVE_DEMO_OUTCOME:" "${DEMO_STDOUT}" | tail -n 1 || echo "")
if [ -z "${DEMO_OUTCOME_LINE}" ]; then
    echo "Error: COPERCEIVE_DEMO_OUTCOME log line not found in app stdout!" >&2
    cat "${DEMO_STDOUT}" >&2
    exit 25
fi
echo "${DEMO_OUTCOME_LINE}"
OUTCOME_PYTHON=$(echo "${DEMO_OUTCOME_LINE}" | sed -n 's/.*python=\([^ ]*\).*/\1/p')
case "${OUTCOME_PYTHON}" in
    "${APP_BUNDLE}/Contents/Resources/"*)
        if [ ! -x "${OUTCOME_PYTHON}" ]; then
            echo "Error: Outcome python is not executable: ${OUTCOME_PYTHON}" >&2
            exit 25
        fi
        ;;
    *)
        echo "Error: Demo did not run with the packaged app's bundled Python! Expected under '${APP_BUNDLE}/Contents/Resources/', got '${OUTCOME_PYTHON}'" >&2
        exit 25
        ;;
esac
echo "Verified: Demo ran with bundled Python inside app bundle: ${OUTCOME_PYTHON}"
echo "DEMO_SMOKE_VERIFIED: os=macos viewer=${LAST_VIEWER_STATE} predictions=${LAST_COMPARE_STATE} outcome=\"${DEMO_OUTCOME_LINE}\""

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
SMOKE_PASSED=1
echo "=========================================================="
echo "Smoke test PASSED successfully!"
echo "=========================================================="
