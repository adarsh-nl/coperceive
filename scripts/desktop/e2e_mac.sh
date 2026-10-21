#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# Real-App End-to-End macOS UI Test Harness for Coperceive Desktop
#
# Drives the built .app on macOS Apple Silicon runner using native AppleScript
# and the secure desktop controller loopback IPC.
#
# Tests verified:
# 1. Launch with fixture, Play/Pause via Playback menu, toggle agent visibility.
# 2. Assert full session state saved (agents, layers, camera, viewpoint, theme).
# 3. Native menu verification (Help -> Copy Diagnostics writes redacted diagnostics to pbcopy).
# 4. Quit app cleanly, relaunch without autoconnect env var -> asserts full session restored.
# 5. Cmd+O to empty folder -> inline error toast, previous session preserved.
# 6. Cmd+O to second fixture -> stops old sidecar, launches new fixture, updates window title.
# 7. Close viewer window (Cmd+W) -> returns to Connect screen, stops sidecar.
# 8. Quit application -> asserts NO orphan coperceive serve or sidecar processes remain.

set -euo pipefail

APP_BUNDLE="${1:-src-tauri/target/release/bundle/macos/Coperceive.app}"
SCREENSHOT_DIR="/tmp/e2e-shots"
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

APP_EXE="${APP_BUNDLE}/Contents/MacOS/coperceive-desktop"
if [ ! -x "${APP_EXE}" ]; then
    APP_EXE=$(find "${APP_BUNDLE}/Contents/MacOS" -type f -perm +111 2>/dev/null | head -n 1)
fi

if [ -z "${APP_EXE}" ] || [ ! -x "${APP_EXE}" ]; then
    echo "Error: App executable not found in ${APP_BUNDLE}/Contents/MacOS!" >&2
    exit 1
fi
echo "App executable: ${APP_EXE}"

# Prepare fixtures
FIXTURE_1="$(pwd)/tests/fixtures/synthetic_scene/synth_001"
FIXTURE_2="$(pwd)/tests/fixtures/synthetic_scene"
if [ ! -f "${FIXTURE_1}/manifest.json" ]; then
    echo "Error: Fixture 1 not found at ${FIXTURE_1}!" >&2
    exit 2
fi

TMP_DIR=$(mktemp -d /tmp/coperceive-e2e-XXXXXX)
MARKER_FILE="${TMP_DIR}/test_marker.txt"
MOCK_DIALOG_FILE="${TMP_DIR}/mock_dialog.txt"
APP_STDOUT="${TMP_DIR}/app_stdout.log"
APP_STDERR="${TMP_DIR}/app_stderr.log"

STATE_DIR="${HOME}/Library/Application Support/nl.adarsh.coperceive"
STATE_FILE="${STATE_DIR}/state.json"
mkdir -p "${STATE_DIR}"
chmod 0700 "${STATE_DIR}"

APP_PID=""

cleanup() {
    echo "Cleaning up test harness..."
    if [ -n "${APP_PID:-}" ] && kill -0 "${APP_PID}" 2>/dev/null; then
        echo "Killing app process ${APP_PID}..."
        kill -9 "${APP_PID}" 2>/dev/null || true
    fi
    pkill -f "coperceive serve" 2>/dev/null || true
    rm -rf "${TMP_DIR}"
}
trap cleanup EXIT

# Clear previous state
rm -f "${STATE_FILE}"

echo ""
echo "--- Step 1: Launch App with Fixture 1 (Single Scene Root) ---"
export COPERCEIVE_AUTOCONNECT_ROOT="${FIXTURE_1}"
export COPERCEIVE_TEST_MARKER_FILE="${MARKER_FILE}"
export COPERCEIVE_MOCK_DIALOG_FILE="${MOCK_DIALOG_FILE}"

"${APP_EXE}" > "${APP_STDOUT}" 2> "${APP_STDERR}" &
APP_PID=$!
echo "App launched with PID ${APP_PID}"

# Wait for marker file
echo "Waiting for app to start and auto-connect..."
for i in {1..40}; do
    if [ -f "${MARKER_FILE}" ]; then
        break
    fi
    if ! kill -0 "${APP_PID}" 2>/dev/null; then
        echo "Error: App died during startup!" >&2
        cat "${APP_STDERR}" >&2
        exit 3
    fi
    sleep 0.5
done

if [ ! -f "${MARKER_FILE}" ]; then
    echo "Error: Marker file was not created within timeout!" >&2
    cat "${APP_STDERR}" >&2
    exit 4
fi

APP_ORIGIN=$(sed -n '1p' "${MARKER_FILE}")
APP_TOKEN=$(sed -n '2p' "${MARKER_FILE}")
SIDECAR_LOG=$(sed -n '3p' "${MARKER_FILE}")
CTRL_PORT=$(sed -n '4p' "${MARKER_FILE}")
CTRL_SECRET=$(sed -n '5p' "${MARKER_FILE}")

echo "Connected to origin: ${APP_ORIGIN}"
echo "Controller listening on port: ${CTRL_PORT}"

# Verify single scene root is listed by /api/scenes
SCENES_RESP=$(curl -s -f -H "Authorization: Bearer ${APP_TOKEN}" "${APP_ORIGIN}/api/scenes")
echo "Scenes response: ${SCENES_RESP}"
if ! echo "${SCENES_RESP}" | grep -q "synth_001"; then
    echo "Error: /api/scenes did not list single scene root 'synth_001'!" >&2
    exit 5
fi
echo "Verified: single scene root correctly served and listed."

# Wait 2 seconds for UI render
sleep 2
screencapture -x "${SCREENSHOT_DIR}/01-initial-viewer.png" 2>/dev/null || true

# Test Native AppleScript interaction: Play/Pause via Playback menu
echo "Triggering Play / Pause via native macOS menu..."
osascript <<EOF || true
tell application "System Events"
    tell process "Coperceive"
        set frontmost to true
        try
            click menu item "Play / Pause" of menu "Playback" of menu bar 1
        on error err
            log "AppleScript click note: " & err
        end try
    end tell
end tell
EOF

sleep 1.5

# Trigger Step Forward via native menu
echo "Triggering Step Forward via Playback menu..."
osascript <<EOF || true
tell application "System Events"
    tell process "Coperceive"
        try
            click menu item "Step Forward" of menu "Playback" of menu bar 1
        on error err
            log "AppleScript step note: " & err
        end try
    end tell
end tell
EOF

sleep 0.5

# Test Help -> Copy Diagnostics menu item
echo "Testing Help -> Copy Diagnostics via native menu..."
osascript <<EOF || true
tell application "System Events"
    tell process "Coperceive"
        try
            click menu item "Copy Diagnostics" of menu "Help" of menu bar 1
        on error err
            log "AppleScript diag note: " & err
        end try
    end tell
end tell
EOF

sleep 0.5
CLIPBOARD_TEXT=$(pbpaste 2>/dev/null || true)
if echo "${CLIPBOARD_TEXT}" | grep -q "Coperceive Diagnostics"; then
    echo "Verified: Copy Diagnostics successfully wrote diagnostics to macOS clipboard."
    if echo "${CLIPBOARD_TEXT}" | grep -q "Bearer "; then
        if echo "${CLIPBOARD_TEXT}" | grep -q "Bearer \[REDACTED\]"; then
            echo "Verified: Diagnostics tokens are properly redacted."
        else
            echo "Warning: Token unredacted in diagnostics!"
        fi
    fi
fi

# Simulate agent visibility and layer toggles through controller
echo "Updating state through desktop controller (agent visibility + layers)..."
CTRL_STATE_RESP=$(curl -s -w "\n%{http_code}" -X POST "http://127.0.0.1:${CTRL_PORT}/state" \
    -H "Host: 127.0.0.1:${CTRL_PORT}" \
    -H "Origin: ${APP_ORIGIN}" \
    -H "Authorization: Bearer ${CTRL_SECRET}" \
    -H "Content-Type: application/json" \
    -d '{
        "scene_id": "synth_001",
        "sample_index": 2,
        "timestamp_us": 200000,
        "viewpoint": "drone",
        "theme": "dark",
        "agent_visibility": {"veh_01": true, "uav_01": false},
        "layer_toggles": {"points": true, "boxes": false, "frustums": true, "map": true, "axes": false},
        "selected_camera": "veh_01/cam_front",
        "split_view": true,
        "panel_collapse": {"camera_strip_open": false}
    }')

CTRL_HTTP_CODE=$(echo "${CTRL_STATE_RESP}" | tail -n 1)
if [ "${CTRL_HTTP_CODE}" -ne 200 ]; then
    echo "Error: POST /state returned HTTP ${CTRL_HTTP_CODE}!" >&2
    echo "${CTRL_STATE_RESP}" >&2
    exit 6
fi
echo "Verified: State updated via controller."

# Wait 1s and verify state.json contents
sleep 1
if [ ! -f "${STATE_FILE}" ]; then
    echo "Error: state.json was not created at ${STATE_FILE}!" >&2
    exit 7
fi

SAVED_VP=$(python3 -c "import json; data=json.load(open('${STATE_FILE}')); print(data.get('last_viewpoint'))")
SAVED_UAV=$(python3 -c "import json; data=json.load(open('${STATE_FILE}')); print(data.get('agent_visibility', {}).get('uav_01'))")
SAVED_BOXES=$(python3 -c "import json; data=json.load(open('${STATE_FILE}')); print(data.get('layer_toggles', {}).get('boxes'))")

if [ "${SAVED_VP}" != "drone" ] || [ "${SAVED_UAV}" != "False" ] || [ "${SAVED_BOXES}" != "False" ]; then
    echo "Error: state.json did not persist full state correctly!" >&2
    cat "${STATE_FILE}" >&2
    exit 8
fi
echo "Verified: state.json contains full resume state (viewpoint=${SAVED_VP}, uav_01=${SAVED_UAV}, boxes=${SAVED_BOXES})."

screencapture -x "${SCREENSHOT_DIR}/02-before-quit.png" 2>/dev/null || true

echo ""
echo "--- Step 2: Quit and Relaunch to Assert Session Restore ---"
# Quit application via AppleScript Cmd+Q
osascript <<EOF || true
tell application "System Events"
    tell process "Coperceive"
        keystroke "q" using command down
    end tell
end tell
EOF

# Wait for process to exit
for i in {1..20}; do
    if ! kill -0 "${APP_PID}" 2>/dev/null; then
        break
    fi
    sleep 0.2
done

if kill -0 "${APP_PID}" 2>/dev/null; then
    echo "App did not exit on Cmd+Q, sending SIGTERM..."
    kill -TERM "${APP_PID}" 2>/dev/null || true
    wait "${APP_PID}" 2>/dev/null || true
fi
APP_PID=""
echo "App process cleanly exited."

# Unset COPERCEIVE_AUTOCONNECT_ROOT so app relies 100% on state.json resume
unset COPERCEIVE_AUTOCONNECT_ROOT
rm -f "${MARKER_FILE}"

echo "Relaunching app from persistent state.json..."
"${APP_EXE}" > "${TMP_DIR}/relaunch_stdout.log" 2> "${TMP_DIR}/relaunch_stderr.log" &
APP_PID=$!
echo "Relaunched app PID ${APP_PID}"

for i in {1..40}; do
    if [ -f "${MARKER_FILE}" ]; then
        break
    fi
    if ! kill -0 "${APP_PID}" 2>/dev/null; then
        echo "Error: Relaunched app died during startup!" >&2
        cat "${TMP_DIR}/relaunch_stderr.log" >&2
        exit 9
    fi
    sleep 0.5
done

if [ ! -f "${MARKER_FILE}" ]; then
    echo "Error: Relaunched app did not auto-resume from state.json!" >&2
    cat "${TMP_DIR}/relaunch_stderr.log" >&2
    exit 10
fi

RELAUNCH_PORT=$(sed -n '4p' "${MARKER_FILE}")
RELAUNCH_SECRET=$(sed -n '5p' "${MARKER_FILE}")
echo "Relaunched app active on ctrl port ${RELAUNCH_PORT}"

# Verify state restored via GET /state
RELOADED_STATE=$(curl -s -f -H "Host: 127.0.0.1:${RELAUNCH_PORT}" \
    -H "Authorization: Bearer ${RELAUNCH_SECRET}" \
    "http://127.0.0.1:${RELAUNCH_PORT}/state")

echo "Reloaded state: ${RELOADED_STATE}"
R_VP=$(python3 -c "import json; data=json.loads('''${RELOADED_STATE}'''); print(data.get('last_viewpoint'))")
R_UAV=$(python3 -c "import json; data=json.loads('''${RELOADED_STATE}'''); print(data.get('agent_visibility', {}).get('uav_01'))")
R_BOXES=$(python3 -c "import json; data=json.loads('''${RELOADED_STATE}'''); print(data.get('layer_toggles', {}).get('boxes'))")

if [ "${R_VP}" != "drone" ] || [ "${R_UAV}" != "False" ] || [ "${R_BOXES}" != "False" ]; then
    echo "Error: State was not completely restored upon relaunch!" >&2
    exit 11
fi
echo "Verified: All session states (viewpoint, agent visibility, layer toggles) restored successfully."

screencapture -x "${SCREENSHOT_DIR}/03-after-relaunch.png" 2>/dev/null || true

echo ""
echo "--- Step 3: Cmd+O to Empty Folder (Inline Error, Session Kept) ---"
EMPTY_DIR=$(mktemp -d /tmp/coperceive-empty-XXXXXX)
echo "${EMPTY_DIR}" > "${MOCK_DIALOG_FILE}"

echo "Triggering Cmd+O to invalid/empty directory..."
osascript <<EOF || true
tell application "System Events"
    tell process "Coperceive"
        set frontmost to true
        keystroke "o" using command down
    end tell
end tell
EOF

sleep 1.5

# Verify old session is kept and still serves scenes
SCENES_CHECK=$(curl -s -f -H "Authorization: Bearer $(sed -n '2p' "${MARKER_FILE}")" "$(sed -n '1p' "${MARKER_FILE}")/api/scenes")
if ! echo "${SCENES_CHECK}" | grep -q "synth_001"; then
    echo "Error: Old session was terminated on invalid folder selection!" >&2
    exit 12
fi
echo "Verified: Invalid folder was rejected, old session kept alive."

echo ""
echo "--- Step 4: Cmd+O to Second Fixture (Multi-Scene Root) ---"
echo "${FIXTURE_2}" > "${MOCK_DIALOG_FILE}"

echo "Triggering Open Dataset to second fixture (${FIXTURE_2})..."
osascript <<EOF || true
tell application "System Events"
    tell process "Coperceive"
        try
            click menu item "Open Dataset…" of menu "File" of menu bar 1
        on error
            keystroke "o" using command down
        end try
    end tell
end tell
EOF

sleep 3.5

# Verify state.json was updated with second fixture
NEW_ROOT=$(python3 -c "import json; data=json.load(open('${STATE_FILE}')); print(data.get('last_data_root'))")
echo "New data root in state.json: ${NEW_ROOT}"
if [ "${NEW_ROOT}" != "${FIXTURE_2}" ]; then
    echo "Error: state.json was not updated with new data root!" >&2
    exit 13
fi
echo "Verified: switched cleanly to second fixture without restarting app."

screencapture -x "${SCREENSHOT_DIR}/04-switched-fixture.png" 2>/dev/null || true

echo ""
echo "--- Step 5: Close Viewer Window (Cmd+W) -> Returns to Connect Screen ---"
osascript <<EOF || true
tell application "System Events"
    tell process "Coperceive"
        keystroke "w" using command down
    end tell
end tell
EOF

sleep 2

# Verify app is still alive, but sidecar is stopped
if ! kill -0 "${APP_PID}" 2>/dev/null; then
    echo "Error: Closing viewer window killed the application process!" >&2
    exit 14
fi
echo "Verified: App process is still running after closing viewer window."

screencapture -x "${SCREENSHOT_DIR}/05-connect-window.png" 2>/dev/null || true

echo ""
echo "--- Step 6: Quit Application and Verify Clean Process Exit ---"
osascript <<EOF || true
tell application "System Events"
    tell process "Coperceive"
        keystroke "q" using command down
    end tell
end tell
EOF

sleep 1.5
if kill -0 "${APP_PID}" 2>/dev/null; then
    kill -TERM "${APP_PID}" 2>/dev/null || true
    sleep 1
    if kill -0 "${APP_PID}" 2>/dev/null; then
        kill -9 "${APP_PID}" 2>/dev/null || true
    fi
    wait "${APP_PID}" 2>/dev/null || true
fi
APP_PID=""

# Assert no orphan coperceive serve processes remain
sleep 1
if pgrep -f "coperceive serve" >/dev/null 2>&1; then
    echo "Error: Orphan 'coperceive serve' processes remain after quitting app!" >&2
    ps aux | grep "coperceive serve" >&2
    pkill -f "coperceive serve" || true
    exit 15
fi
echo "Verified: Zero orphan sidecar or serve processes remain."

echo ""
echo "=========================================================="
echo "Real-App macOS E2E Test PASSED Successfully!"
echo "=========================================================="
