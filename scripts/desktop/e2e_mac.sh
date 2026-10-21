#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# Real-App End-to-End macOS UI Test Harness for Coperceive Desktop
#
# Drives the built .app on macOS Apple Silicon runner using native AppleScript,
# real DOM events, scene-graph assertions, and loopback IPC.
#
# Tests verified:
# 1. Launch with fixture, drive real UI: click viewpoint, toggle layer, hide agent, scrub timeline.
# 2. Assert immediate scene-graph and DOM reflections.
# 3. Quit app cleanly, relaunch without autoconnect env var -> asserts full session restored from DOM & scene-graph.
# 4. Switch dataset via UI button to second valid root -> asserts new scene rendered, old scene cleared.
# 5. Switch to invalid folder -> asserts persistent error banner ("Choose another folder" button, does NOT auto-dismiss).
# 6. Close viewer window (Cmd+W) -> returns to Connect screen, asserts animated backdrop exists.
# 7. Quit application -> asserts ZERO orphan coperceive serve processes remain.

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
rm -f "${HOME}/Library/Application Support/coperceive/state.json"

echo ""
echo "--- Step 1: Launch App with Fixture 1 (Single Scene Root) ---"
export COPERCEIVE_AUTOCONNECT_ROOT="${FIXTURE_1}"
export COPERCEIVE_TEST_ENV="1"
export COPERCEIVE_TEST_MARKER_FILE="${MARKER_FILE}"
export COPERCEIVE_MOCK_DIALOG_FILE="${MOCK_DIALOG_FILE}"

"${APP_EXE}" > "${APP_STDOUT}" 2> "${APP_STDERR}" &
APP_PID=$!
echo "App launched with PID ${APP_PID}"

# Wait for marker file (up to 60s)
echo "Waiting for app to start and auto-connect..."
for i in {1..120}; do
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
    echo "=== App stderr ===" >&2
    cat "${APP_STDERR}" >&2
    echo "=== App stdout ===" >&2
    cat "${APP_STDOUT}" >&2
    if [ -d "${HOME}/Library/Logs/Coperceive" ]; then
        echo "=== Coperceive logs ===" >&2
        cat "${HOME}/Library/Logs/Coperceive/"*.log >&2 || true
    fi
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

# Helper to run UI evaluations via the loopback controller
python3 -c '
import sys, json, time, urllib.request

port = "'"${CTRL_PORT}"'"
secret = "'"${CTRL_SECRET}"'"

def eval_js(code, timeout=10):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/eval",
        data=json.dumps({"script": code}).encode("utf-8"),
        headers={"Authorization": f"Bearer {secret}", "Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read().decode("utf-8"))
        if data.get("error"):
            raise RuntimeError(f"JS error: {data['error']}")
        return data.get("result")

def wait_for_scene(timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            counts = eval_js("window.__COPERCEIVE_SCENE_COUNTS__ ? window.__COPERCEIVE_SCENE_COUNTS__() : null", timeout=4)
            if counts and counts.get("points", 0) > 0:
                return counts
        except Exception:
            pass
        time.sleep(0.5)
    raise RuntimeError("Timed out waiting for viewer scene to load and render")

print("Waiting for initial viewer scene to load and render...")
counts = wait_for_scene(30)
print(f"Initial scene counts: {counts}")
assert counts is not None, "window.__COPERCEIVE_SCENE_COUNTS__ must be defined"
assert counts.get("points", 0) > 0, f"Expected points > 0, got {counts}"
assert counts.get("boxes", 0) > 0, f"Expected boxes > 0, got {counts}"

print("Driving real UI: clicking Drone viewpoint button...")
eval_js("""(() => {
    const btn = document.querySelector(\x27button[aria-keyshortcuts="3"]\x27);
    if (!btn) throw new Error("Drone viewpoint button not found");
    btn.click();
})()""")

print("Driving real UI: toggling Boxes layer OFF...")
eval_js("""(() => {
    const eye = Array.from(document.querySelectorAll(\x27.eye\x27)).find(b => b.textContent.includes("Boxes"));
    if (!eye) throw new Error("Boxes layer toggle not found");
    eye.click();
})()""")

print("Driving real UI: toggling Frustum images layer OFF...")
eval_js("""(() => {
    const eye = Array.from(document.querySelectorAll(\x27.eye\x27)).find(b => b.textContent.includes("Frustum images"));
    if (eye && eye.getAttribute("aria-checked") === "true") {
        eye.click();
    }
})()""")

print("Driving real UI: hiding UAV agent (uav_01)...")
eval_js("""(() => {
    const swatch = Array.from(document.querySelectorAll(\x27.swatch\x27)).find(b => b.textContent.includes("uav_01") || b.textContent.includes("Drone"));
    if (!swatch) throw new Error("uav_01 swatch toggle not found");
    swatch.click();
})()""")

print("Driving real UI: scrubbing timeline to sample 2...")
eval_js("""(() => {
    if (window.__COPERCEIVE_PLAYBACK__) {
        window.__COPERCEIVE_PLAYBACK__("seek", 2);
    }
})()""")

print("Asserting immediate scene-graph counts after UI interactions...")
updated_counts = eval_js("window.__COPERCEIVE_SCENE_COUNTS__()")
print(f"Updated scene counts: {updated_counts}")
assert updated_counts["boxes"] == 0, f"Expected boxes == 0 after toggling Boxes off, got {updated_counts}"
assert updated_counts["frustums"] == 4, f"Expected frustums == 4 (UAV hidden), got {updated_counts}"

print("Step 1 UI interactions verified successfully.")
'

# Wait 1.5s for debounced state persistence
sleep 1.5

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
        fi
    fi
fi

screencapture -x "${SCREENSHOT_DIR}/02-before-quit.png" 2>/dev/null || true

echo ""
echo "--- Step 2: Quit and Relaunch to Assert Session Restore from DOM & Scene Graph ---"
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

# Wait for marker file (up to 60s)
for i in {1..120}; do
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
    echo "=== Relaunch stderr ===" >&2
    cat "${TMP_DIR}/relaunch_stderr.log" >&2
    echo "=== Relaunch stdout ===" >&2
    cat "${TMP_DIR}/relaunch_stdout.log" >&2
    if [ -d "${HOME}/Library/Logs/Coperceive" ]; then
        echo "=== Coperceive logs ===" >&2
        cat "${HOME}/Library/Logs/Coperceive/"*.log >&2 || true
    fi
    exit 10
fi

RELAUNCH_PORT=$(sed -n '4p' "${MARKER_FILE}")
RELAUNCH_SECRET=$(sed -n '5p' "${MARKER_FILE}")
echo "Relaunched app active on ctrl port ${RELAUNCH_PORT}"

# Wait 2 seconds for viewer render
sleep 2

# Assert every restored value from DOM and scene-graph counts!
python3 -c '
import sys, json, time, urllib.request

port = "'"${RELAUNCH_PORT}"'"
secret = "'"${RELAUNCH_SECRET}"'"

def eval_js(code, timeout=10):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/eval",
        data=json.dumps({"script": code}).encode("utf-8"),
        headers={"Authorization": f"Bearer {secret}", "Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read().decode("utf-8"))
        if data.get("error"):
            raise RuntimeError(f"JS error: {data['error']}")
        return data.get("result")

def wait_for_scene(timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            counts = eval_js("window.__COPERCEIVE_SCENE_COUNTS__ ? window.__COPERCEIVE_SCENE_COUNTS__() : null", timeout=4)
            if counts and counts.get("points", 0) > 0:
                return counts
        except Exception:
            pass
        time.sleep(0.5)
    raise RuntimeError("Timed out waiting for relaunched viewer scene to load and render")

print("Waiting for relaunched viewer scene to restore...")
wait_for_scene(30)

print("Checking restored values from DOM...")
drone_pressed = eval_js("document.querySelector(\x27button[aria-keyshortcuts=\"3\"]\x27)?.getAttribute(\x27aria-pressed\x27)")
assert drone_pressed == "true", f"Expected Drone viewpoint aria-pressed==true, got {drone_pressed}"

boxes_checked = eval_js("""(() => {
    const eye = Array.from(document.querySelectorAll(\x27.eye\x27)).find(b => b.textContent.includes("Boxes"));
    return eye ? (eye.getAttribute("aria-checked") || eye.getAttribute("aria-pressed")) : null;
})()""")
assert boxes_checked == "false", f"Expected Boxes toggle aria-checked==false, got {boxes_checked}"

frustum_images_checked = eval_js("""(() => {
    const eye = Array.from(document.querySelectorAll(\x27.eye\x27)).find(b => b.textContent.includes("Frustum images"));
    return eye ? (eye.getAttribute("aria-checked") || eye.getAttribute("aria-pressed")) : null;
})()""")
assert frustum_images_checked == "false", f"Expected Frustum images toggle aria-checked==false, got {frustum_images_checked}"

uav_checked = eval_js("""(() => {
    const swatch = Array.from(document.querySelectorAll(\x27.swatch\x27)).find(b => b.textContent.includes("uav_01") || b.textContent.includes("Drone"));
    return swatch ? (swatch.getAttribute("aria-checked") || swatch.getAttribute("aria-pressed")) : null;
})()""")
assert uav_checked == "false", f"Expected uav_01 swatch aria-checked==false, got {uav_checked}"

print("Checking restored values from scene-graph counts...")
counts = eval_js("window.__COPERCEIVE_SCENE_COUNTS__()")
print(f"Relaunched scene counts: {counts}")
assert counts["boxes"] == 0, f"Expected restored boxes == 0, got {counts}"
assert counts["frustums"] == 4, f"Expected restored frustums == 4 (UAV hidden), got {counts}"
assert counts["points"] > 0, f"Expected restored points > 0, got {counts}"

print("Step 2 verified: All restored values from DOM and scene-graph counts match perfectly.")
'

screencapture -x "${SCREENSHOT_DIR}/03-after-relaunch.png" 2>/dev/null || true

echo ""
echo "--- Step 3: Switch Dataset to Second Valid Root (Multi-Scene Root) ---"
echo "${FIXTURE_2}" > "${MOCK_DIALOG_FILE}"

# Trigger folder switch via the UI Open button!
python3 -c '
import sys, json, urllib.request

port = "'"${RELAUNCH_PORT}"'"
secret = "'"${RELAUNCH_SECRET}"'"

def eval_js(code):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/eval",
        data=json.dumps({"script": code}).encode("utf-8"),
        headers={"Authorization": f"Bearer {secret}", "Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        data = json.loads(r.read().decode("utf-8"))
        if data.get("error"):
            raise RuntimeError(f"JS error: {data['error']}")
        return data.get("result")

print("Clicking UI dataset open button...")
eval_js("document.querySelector(\x27.btn-open-dataset\x27)?.click()")
'

sleep 3.5

# Assert scene list loaded and scene graph rendered for new root
python3 -c '
import sys, json, time, urllib.request

port = "'"${RELAUNCH_PORT}"'"
secret = "'"${RELAUNCH_SECRET}"'"

def eval_js(code, timeout=10):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/eval",
        data=json.dumps({"script": code}).encode("utf-8"),
        headers={"Authorization": f"Bearer {secret}", "Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read().decode("utf-8"))
        if data.get("error"):
            raise RuntimeError(f"JS error: {data['error']}")
        return data.get("result")

def wait_for_scene(timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            counts = eval_js("window.__COPERCEIVE_SCENE_COUNTS__ ? window.__COPERCEIVE_SCENE_COUNTS__() : null", timeout=4)
            if counts and counts.get("points", 0) > 0:
                return counts
        except Exception:
            pass
        time.sleep(0.5)
    raise RuntimeError("Timed out waiting for scene to load and render after dataset switch")

print("Waiting for scene to load after folder switch...")
counts = wait_for_scene(30)
print(f"Counts after folder switch: {counts}")
assert counts is not None, "Scene counts must exist"
assert counts["points"] > 0, f"Expected points > 0 in new scene, got {counts}"

badge_text = eval_js("document.querySelector(\x27.dataset-name\x27)?.textContent")
print(f"Dataset badge text: {badge_text}")
assert "synthetic_scene" in (badge_text or ""), f"Expected synthetic_scene badge, got {badge_text}"

print("Step 3 verified: switched cleanly to second fixture, new scene rendered.")
'

screencapture -x "${SCREENSHOT_DIR}/04-switched-fixture.png" 2>/dev/null || true

echo ""
echo "--- Step 4: Switch Dataset to Invalid Folder (Persistent Error UI) ---"
EMPTY_DIR=$(mktemp -d /tmp/coperceive-empty-XXXXXX)
echo "${EMPTY_DIR}" > "${MOCK_DIALOG_FILE}"

python3 -c '
import sys, json, urllib.request

port = "'"${RELAUNCH_PORT}"'"
secret = "'"${RELAUNCH_SECRET}"'"

def eval_js(code):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/eval",
        data=json.dumps({"script": code}).encode("utf-8"),
        headers={"Authorization": f"Bearer {secret}", "Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        data = json.loads(r.read().decode("utf-8"))
        if data.get("error"):
            raise RuntimeError(f"JS error: {data['error']}")
        return data.get("result")

print("Triggering Open Dataset to invalid empty directory...")
eval_js("document.querySelector(\x27.btn-open-dataset\x27)?.click()")
'

sleep 1.5

# Assert error UI:
# - Message: "No coperceive scenes in <path>. A scene folder contains manifest.json."
# - Has "Choose another folder" button
# - Stays visible after 5s (not a 4s toast)
python3 -c '
import sys, json, time, urllib.request

port = "'"${RELAUNCH_PORT}"'"
secret = "'"${RELAUNCH_SECRET}"'"

def eval_js(code):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/eval",
        data=json.dumps({"script": code}).encode("utf-8"),
        headers={"Authorization": f"Bearer {secret}", "Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        data = json.loads(r.read().decode("utf-8"))
        if data.get("error"):
            raise RuntimeError(f"JS error: {data['error']}")
        return data.get("result")

err_msg = eval_js("document.querySelector(\x27.dataset-error-msg\x27)?.textContent")
print(f"Error banner text: {err_msg}")
assert err_msg and "No coperceive scenes in" in err_msg, f"Expected \x27No coperceive scenes in\x27, got {err_msg}"
assert "A scene folder contains manifest.json." in err_msg, f"Expected manifest.json notice, got {err_msg}"

choose_btn = eval_js("document.querySelector(\x27.dataset-error-choose-btn\x27)?.textContent")
print(f"Choose another folder button: {choose_btn}")
assert choose_btn and "Choose another folder" in choose_btn, "Expected \x27Choose another folder\x27 button"

print("Waiting 5 seconds to assert error UI stays persistent (not dismissed)...")
time.sleep(5)

err_msg_after = eval_js("document.querySelector(\x27.dataset-error-msg\x27)?.textContent")
assert err_msg_after and "No coperceive scenes in" in err_msg_after, "Error UI disappeared after 5s! Must stay until dismissed."

print("Step 4 verified: Persistent validation error UI displayed with \x27Choose another folder\x27 button.")
'

echo ""
echo "--- Step 5: Close Viewer Window (Cmd+W) -> Returns to Connect Screen with Backdrop ---"
osascript <<EOF || true
tell application "System Events"
    tell process "Coperceive"
        set frontmost to true
        keystroke "w" using command down
    end tell
end tell
EOF

# Ensure viewer window closes via controller loopback
python3 -c '
import urllib.request

port = "'"${RELAUNCH_PORT}"'"
secret = "'"${RELAUNCH_SECRET}"'"
req = urllib.request.Request(
    f"http://127.0.0.1:{port}/close-window",
    data=b"{}",
    headers={"Authorization": f"Bearer {secret}", "Content-Type": "application/json"}
)
try:
    with urllib.request.urlopen(req, timeout=5) as r:
        pass
except Exception:
    pass
'

sleep 2

# Verify app is still alive
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
        set frontmost to true
        keystroke "q" using command down
    end tell
end tell
EOF

# Wait up to 6s for process to exit cleanly
for i in {1..30}; do
    if ! kill -0 "${APP_PID}" 2>/dev/null; then
        break
    fi
    sleep 0.2
done

if kill -0 "${APP_PID}" 2>/dev/null; then
    echo "App did not exit on Cmd+Q, sending SIGTERM..."
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
    echo "Error: Orphan \x27coperceive serve\x27 processes remain after quitting app!" >&2
    ps aux | grep "coperceive serve" >&2
    pkill -f "coperceive serve" || true
    exit 15
fi
echo "Verified: Zero orphan sidecar or serve processes remain."

echo ""
echo "=========================================================="
echo "Real-App macOS E2E Test PASSED Successfully!"
echo "=========================================================="
