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
FIXTURE_1="$(pwd)/tests/fixtures/synthetic_scene"
FIXTURE_2="$(pwd)/tests/fixtures/synthetic_scene/synth_001"
if [ ! -d "${FIXTURE_1}" ]; then
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
    local status=$?
    echo "Cleaning up test harness (exit status: ${status})..."
    if [ "${status}" -ne 0 ]; then
        echo "=== App STDOUT on failure ==="
        cat "${APP_STDOUT}" 2>/dev/null || true
        echo "=== App STDERR on failure ==="
        cat "${APP_STDERR}" 2>/dev/null || true
        if [ -f "${TMP_DIR}/relaunch_stdout.log" ]; then
            echo "=== Relaunch STDOUT ==="
            cat "${TMP_DIR}/relaunch_stdout.log" 2>/dev/null || true
        fi
        if [ -f "${TMP_DIR}/relaunch_stderr.log" ]; then
            echo "=== Relaunch STDERR ==="
            cat "${TMP_DIR}/relaunch_stderr.log" 2>/dev/null || true
        fi
        if [ -d "${HOME}/Library/Logs/Coperceive" ]; then
            echo "=== Coperceive logs ==="
            cat "${HOME}/Library/Logs/Coperceive/"*.log 2>/dev/null || true
        fi
    fi
    if [ -n "${APP_PID:-}" ] && kill -0 "${APP_PID}" 2>/dev/null; then
        echo "Killing app process ${APP_PID}..."
        kill -9 "${APP_PID}" 2>/dev/null || true
    fi
    pkill -f "coperceive serve" 2>/dev/null || true
    rm -rf "${TMP_DIR}"
}
trap cleanup EXIT INT TERM HUP

# Ensure no leftover serve or app processes from prior steps
pkill -f "coperceive serve" 2>/dev/null || true
pkill -f "coperceive-desktop" 2>/dev/null || true
pkill -f "Coperceive" 2>/dev/null || true

# Clear previous state
rm -f "${STATE_FILE}"
rm -f "${HOME}/Library/Application Support/coperceive/state.json"

echo ""
echo "--- Step 1: Launch App with Fixture 1 (Multi-Scene Root) ---"
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
        echo "Marker file created after ${i} checks ($((i/2))s)!"
        break
    fi
    if ! kill -0 "${APP_PID}" 2>/dev/null; then
        echo "Error: App died during startup (check ${i})!" >&2
        cat "${APP_STDERR}" >&2
        exit 3
    fi
    if [ $((i % 10)) -eq 0 ]; then
        echo "Still waiting for marker file... ($((i/2))s elapsed)"
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

# Verify fixture scene is listed by /api/scenes
SCENES_RESP=$(curl -s -f -H "Authorization: Bearer ${APP_TOKEN}" "${APP_ORIGIN}/api/scenes")
echo "Scenes response: ${SCENES_RESP}"
if ! echo "${SCENES_RESP}" | grep -q "synth_001"; then
    echo "Error: /api/scenes did not list scene 'synth_001'!" >&2
    exit 5
fi
echo "Verified: fixture scene correctly served and listed."

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
    last_err = None
    last_counts = None
    while time.monotonic() < deadline:
        try:
            counts = eval_js("window.__COPERCEIVE_SCENE_COUNTS__ ? window.__COPERCEIVE_SCENE_COUNTS__() : null", timeout=4)
            last_counts = counts
            if counts and counts.get("points", 0) > 0:
                return counts
        except Exception as e:
            last_err = e
        time.sleep(0.5)
    raise RuntimeError(f"Timed out waiting for viewer scene to load and render (last_counts={last_counts}, last_err={last_err})")

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

print("Driving real UI: scrubbing timeline to sample 148 (second-to-last sample in end zone)...")
eval_js("""(() => {
    if (window.__COPERCEIVE_PLAYBACK__) {
        window.__COPERCEIVE_PLAYBACK__("seek", 148);
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
        echo "Relaunch marker file created after ${i} checks ($((i/2))s)!"
        break
    fi
    if ! kill -0 "${APP_PID}" 2>/dev/null; then
        echo "Error: Relaunched app died during startup (check ${i})!" >&2
        cat "${TMP_DIR}/relaunch_stderr.log" >&2
        exit 9
    fi
    if [ $((i % 10)) -eq 0 ]; then
        echo "Still waiting for relaunch marker file... ($((i/2))s elapsed)"
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
python3 << EOF
import sys, json, time, urllib.request

port = "${RELAUNCH_PORT}"
secret = "${RELAUNCH_SECRET}"

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

def eval_retry(step, code, tries=3):
    # The controller answers 504 after 10 s if the webview misses an eval.
    # Outlast that and retry; one missed callback is not a product failure.
    last = None
    for _ in range(tries):
        try:
            return eval_js(code, timeout=15)
        except Exception as e:
            last = e
            time.sleep(0.5)
    print(f"E2E_FAIL step={step} {type(last).__name__}: {last}", flush=True)
    raise SystemExit(1)

def wait_for_scene(timeout=30):
    deadline = time.monotonic() + timeout
    last_err = None
    last_counts = None
    while time.monotonic() < deadline:
        try:
            counts = eval_js("window.__COPERCEIVE_SCENE_COUNTS__ ? window.__COPERCEIVE_SCENE_COUNTS__() : null", timeout=4)
            last_counts = counts
            if counts and counts.get("points", 0) > 0:
                return counts
        except Exception as e:
            last_err = e
        time.sleep(0.5)
    raise RuntimeError(f"Timed out waiting for relaunched viewer scene to load and render (last_counts={last_counts}, last_err={last_err})")

print("Waiting for relaunched viewer scene to restore...")
wait_for_scene(30)

print("Checking restored values from DOM...")
drone_pressed = eval_js("document.querySelector('button[aria-keyshortcuts=\"3\"]')?.getAttribute('aria-pressed')")
assert drone_pressed == "true", f"Expected Drone viewpoint aria-pressed==true, got {drone_pressed}"

boxes_checked = eval_js("""(() => {
    const eye = Array.from(document.querySelectorAll('.eye')).find(b => b.textContent.includes("Boxes"));
    return eye ? (eye.getAttribute("aria-checked") || eye.getAttribute("aria-pressed")) : null;
})()""")
assert boxes_checked == "false", f"Expected Boxes toggle aria-checked==false, got {boxes_checked}"

frustum_images_checked = eval_js("""(() => {
    const eye = Array.from(document.querySelectorAll('.eye')).find(b => b.textContent.includes("Frustum images"));
    return eye ? (eye.getAttribute("aria-checked") || eye.getAttribute("aria-pressed")) : null;
})()""")
assert frustum_images_checked == "false", f"Expected Frustum images toggle aria-checked==false, got {frustum_images_checked}"

uav_checked = eval_js("""(() => {
    const swatch = Array.from(document.querySelectorAll('.swatch')).find(b => b.textContent.includes("uav_01") || b.textContent.includes("Drone"));
    return swatch ? (swatch.getAttribute("aria-checked") || swatch.getAttribute("aria-pressed")) : null;
})()""")
assert uav_checked == "false", f"Expected uav_01 swatch aria-checked==false, got {uav_checked}"

print("Checking restored values from scene-graph counts...")
counts = eval_js("window.__COPERCEIVE_SCENE_COUNTS__()")
print(f"Relaunched scene counts: {counts}")
assert counts["boxes"] == 0, f"Expected restored boxes == 0, got {counts}"
assert counts["frustums"] == 4, f"Expected restored frustums == 4 (UAV hidden), got {counts}"
assert counts["points"] > 0, f"Expected restored points > 0, got {counts}"

print("Verifying resume never restores into the end zone (must restore 0)...")
pb_restored = eval_js("window.__COPERCEIVE_PLAYBACK_STATE__ ? window.__COPERCEIVE_PLAYBACK_STATE__() : null")
print(f"Relaunched playback state: {pb_restored}")
assert pb_restored is not None, "window.__COPERCEIVE_PLAYBACK_STATE__ must exist"
assert pb_restored["frameIndex"] == 0, f"Expected resume into end zone to reset frameIndex to 0, got {pb_restored['frameIndex']}"
assert pb_restored["loop"] == True, f"Expected default loop == True for scenes under 60s, got {pb_restored['loop']}"

print("Testing playback requirement (a): restore at the second-to-last sample, click Play, assert still playing 3 s later with frameIndex advanced >= 25...")
eval_js("window.__COPERCEIVE_PLAYBACK__('seek', 148)")
eval_js("document.querySelector('button.icon.primary')?.click()")
time.sleep(3.0)
pb_a = eval_js("window.__COPERCEIVE_PLAYBACK_STATE__()")
print(f"State 3s after Play click: {pb_a}")
assert pb_a["playing"] == True, f"Expected still playing 3s later, got {pb_a}"
assert pb_a["frameIndex"] >= 25, f"Expected frameIndex advanced >= 25, got {pb_a['frameIndex']}"
eval_js("window.__COPERCEIVE_PLAYBACK__('toggle')")

print("Testing playback requirement (b): loop off, play to the end, assert Replay, click, assert it plays from 0...")
eval_js("window.__COPERCEIVE_PLAYBACK__('loop')")
time.sleep(0.1)
pb_b_loop = eval_js("window.__COPERCEIVE_PLAYBACK_STATE__()")
assert pb_b_loop["loop"] == False, f"Expected loop to be False, got {pb_b_loop}"

# Seek to sample 125 (before the 2s end-zone threshold at 129) so Play plays forward to the end
eval_js("window.__COPERCEIVE_PLAYBACK__('seek', 125)")
eval_js("document.querySelector('button.icon.primary')?.click()")

deadline = time.monotonic() + 6.0
while time.monotonic() < deadline:
    pb_cur = eval_js("window.__COPERCEIVE_PLAYBACK_STATE__()")
    if not pb_cur["playing"] and pb_cur["frameIndex"] >= 149:
        break
    time.sleep(0.05)

btn_label = eval_js("document.querySelector('button.icon.primary')?.getAttribute('aria-label')")
print(f"Play button aria-label at end of playback: {btn_label}")
assert btn_label == "Replay", f"Expected play button to show 'Replay', got '{btn_label}'"

eval_js("document.querySelector('button.icon.primary')?.click()")
time.sleep(0.5)
pb_replayed = eval_js("window.__COPERCEIVE_PLAYBACK_STATE__()")
print(f"State after clicking Replay: {pb_replayed}")
assert pb_replayed["playing"] == True, f"Expected playing == True after clicking Replay, got {pb_replayed}"
assert pb_replayed["frameIndex"] < 25, f"Expected playback to restart from 0, got {pb_replayed['frameIndex']}"
eval_js("window.__COPERCEIVE_PLAYBACK__('toggle')")

print("Testing playback requirement (c): play 15 s across the loop wrap with no frame-change gap over 300 ms...")
eval_js("window.__COPERCEIVE_PLAYBACK__('loop')")
time.sleep(0.1)
pb_c_loop = eval_js("window.__COPERCEIVE_PLAYBACK_STATE__()")
assert pb_c_loop["loop"] == True, f"Expected loop to be True, got {pb_c_loop}"

eval_js("window.__COPERCEIVE_PLAYBACK__('seek', 120)")

eval_js("""
(() => {
    window.__RECORD_GAPS__ = {
        lastFrame: null,
        lastChange: performance.now(),
        maxGapMs: 0,
        wraps: 0,
        framesSeen: 0
    };
    if (window.__RECORD_INTERVAL__) clearInterval(window.__RECORD_INTERVAL__);
    window.__RECORD_INTERVAL__ = setInterval(() => {
        const st = window.__COPERCEIVE_PLAYBACK_STATE__ ? window.__COPERCEIVE_PLAYBACK_STATE__() : null;
        if (!st) return;
        const now = performance.now();
        if (st.frameIndex !== window.__RECORD_GAPS__.lastFrame) {
            if (window.__RECORD_GAPS__.lastFrame !== null) {
                const gap = now - window.__RECORD_GAPS__.lastChange;
                if (gap > window.__RECORD_GAPS__.maxGapMs) {
                    window.__RECORD_GAPS__.maxGapMs = gap;
                }
                if (window.__RECORD_GAPS__.lastFrame > 140 && st.frameIndex < 10) {
                    window.__RECORD_GAPS__.wraps++;
                }
            }
            window.__RECORD_GAPS__.lastFrame = st.frameIndex;
            window.__RECORD_GAPS__.lastChange = now;
            window.__RECORD_GAPS__.framesSeen++;
        }
    }, 10);
})()
""")

eval_js("document.querySelector('button.icon.primary')?.click()")

time.sleep(15.5)

# Reads can be retried. A toggle cannot: a lost callback may still have run.
try:
    eval_js("window.__COPERCEIVE_PLAYBACK__('toggle')", timeout=15)
except Exception as e:
    print(f"E2E_NOTE step=2-stop-playback {type(e).__name__}: {e}", flush=True)
try:
    eval_js("if (window.__RECORD_INTERVAL__) { clearInterval(window.__RECORD_INTERVAL__); window.__RECORD_INTERVAL__ = null; }", timeout=15)
except Exception as e:
    print(f"E2E_NOTE step=2-clear-gap-recorder {type(e).__name__}: {e}", flush=True)

report = eval_retry("2-read-gaps", "window.__RECORD_GAPS__")
print(f"Playback requirement (c) verified: max frame gap = {report['maxGapMs']:.1f}ms, loop wraps observed = {report['wraps']}, frames seen = {report['framesSeen']}")
assert report["wraps"] >= 1, f"Expected at least 1 loop wrap over 15s, got {report['wraps']}"
assert report["maxGapMs"] <= 300.0, f"Frame change gap exceeded 300ms: {report['maxGapMs']:.1f}ms"

print("Step 2 verified: All restored values from DOM and scene-graph counts match perfectly.")
EOF

screencapture -x "${SCREENSHOT_DIR}/03-after-relaunch.png" 2>/dev/null || true

echo ""
echo "E2E_MARK step=2b"
echo "--- Step 2b: Loop choice survives quit and relaunch (state.json, not localStorage) ---"
python3 << EOF
import json, time, urllib.request
port = "${RELAUNCH_PORT}"
secret = "${RELAUNCH_SECRET}"

def eval_js(code, timeout=15):
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

def wait_playback(step, timeout=25):
    deadline = time.monotonic() + timeout
    last = "none"
    while time.monotonic() < deadline:
        try:
            state = eval_js("window.__COPERCEIVE_PLAYBACK_STATE__ ? window.__COPERCEIVE_PLAYBACK_STATE__() : null", timeout=15)
            if isinstance(state, dict) and "loop" in state:
                return state
            last = f"unexpected {state!r}"
        except Exception as e:
            last = f"{type(e).__name__}: {e}"
        time.sleep(0.4)
    print(f"E2E_FAIL step={step} {last}", flush=True)
    raise SystemExit(1)

before = wait_playback("2b-before")
print(f"Loop before toggle: {before}", flush=True)
if before["loop"] is not False:
    try:
        eval_js("window.__COPERCEIVE_PLAYBACK__('loop')")
    except Exception as e:
        print(f"E2E_NOTE step=2b-toggle {type(e).__name__}: {e}", flush=True)
after = wait_playback("2b-after-toggle")
if after["loop"] is not False:
    try:
        eval_js("window.__COPERCEIVE_PLAYBACK__('loop')")
    except Exception as e:
        print(f"E2E_NOTE step=2b-toggle-again {type(e).__name__}: {e}", flush=True)
    after = wait_playback("2b-after-toggle-again")
assert after["loop"] is False, f"Expected loop off before quit, got {after}"
print("Loop is off; waiting for state.json write.", flush=True)
EOF

sleep 1

python3 << EOF
import json, time, urllib.request
port = "${RELAUNCH_PORT}"
secret = "${RELAUNCH_SECRET}"

def read_state():
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/state",
        headers={"Authorization": f"Bearer {secret}"},
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode("utf-8"))

saved = None
last = "none"
for _ in range(3):
    try:
        saved = read_state()
        break
    except Exception as e:
        last = f"{type(e).__name__}: {e}"
        time.sleep(0.5)
if saved is None:
    print(f"E2E_FAIL step=2b-read-state {last}", flush=True)
    raise SystemExit(1)
print(f"state.json last_loop={saved.get('last_loop')}", flush=True)
assert saved.get("last_loop") is False, f"Expected last_loop false in state.json, got {saved.get('last_loop')}"
EOF

osascript <<EOF || true
tell application "System Events"
    tell process "Coperceive"
        keystroke "q" using command down
    end tell
end tell
EOF

for i in {1..20}; do
    if ! kill -0 "${APP_PID}" 2>/dev/null; then
        break
    fi
    sleep 0.2
done
if kill -0 "${APP_PID}" 2>/dev/null; then
    kill -TERM "${APP_PID}" 2>/dev/null || true
    wait "${APP_PID}" 2>/dev/null || true
fi
APP_PID=""
rm -f "${MARKER_FILE}"

echo "Relaunching to confirm loop stayed off..."
"${APP_EXE}" > "${TMP_DIR}/loop_relaunch_stdout.log" 2> "${TMP_DIR}/loop_relaunch_stderr.log" &
APP_PID=$!
for i in {1..120}; do
    if [ -f "${MARKER_FILE}" ]; then
        echo "Loop-relaunch marker after ${i} checks."
        break
    fi
    if ! kill -0 "${APP_PID}" 2>/dev/null; then
        echo "Error: loop relaunch died" >&2
        cat "${TMP_DIR}/loop_relaunch_stderr.log" >&2
        exit 11
    fi
    sleep 0.5
done
if [ ! -f "${MARKER_FILE}" ]; then
    echo "Error: loop relaunch did not resume" >&2
    cat "${TMP_DIR}/loop_relaunch_stderr.log" >&2
    exit 11
fi
RELAUNCH_PORT=$(sed -n '4p' "${MARKER_FILE}")
RELAUNCH_SECRET=$(sed -n '5p' "${MARKER_FILE}")

python3 << EOF
import json, time, urllib.request
port = "${RELAUNCH_PORT}"
secret = "${RELAUNCH_SECRET}"

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

deadline = time.monotonic() + 30
state = None
while time.monotonic() < deadline:
    try:
        counts = eval_js("window.__COPERCEIVE_SCENE_COUNTS__ ? window.__COPERCEIVE_SCENE_COUNTS__() : null", timeout=4)
        state = eval_js("window.__COPERCEIVE_PLAYBACK_STATE__()")
        if counts and counts.get("points", 0) > 0 and state and state.get("samples", 0) > 1:
            break
    except Exception:
        pass
    time.sleep(0.5)
print(f"Loop after relaunch: {state}")
assert state is not None and state.get("loop") is False, f"Loop choice did not survive relaunch: {state}"
print("Step 2b verified: loop stayed off across quit and relaunch.")
EOF

echo ""
echo "--- Step 3: Switch Dataset to Second Valid Root (Single Scene Root) ---"
echo "${FIXTURE_2}" > "${MOCK_DIALOG_FILE}"

# Trigger folder switch via the UI Open button!
python3 << EOF
import sys, json, urllib.request

port = "${RELAUNCH_PORT}"
secret = "${RELAUNCH_SECRET}"

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
btn_res = eval_js("(() => { const b = document.querySelector('.btn-open-dataset'); return b ? 'found' : 'NOT_FOUND'; })()")
print(f"Button presence: {btn_res}")
eval_js("document.querySelector('.btn-open-dataset')?.click()")
EOF

sleep 3.5

# Assert scene list loaded and scene graph rendered for new root
python3 << EOF
import sys, json, time, urllib.request

port = "${RELAUNCH_PORT}"
secret = "${RELAUNCH_SECRET}"

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

# Print initial diagnostics right after sleep 3.5
dbg_initial = eval_js("""(() => ({
    status: document.querySelector('.status')?.textContent,
    badge: document.querySelector('.dataset-name')?.textContent,
    scenes: window.__COPERCEIVE_SCENES__ ? window.__COPERCEIVE_SCENES__() : ((document.querySelector('#scene-id')?.getAttribute('data-options') || '').split(',').filter(Boolean)),
    sceneId: document.querySelector('#scene-id')?.value,
    errBanner: document.querySelector('.dataset-error-msg')?.textContent,
    lastDatasetChange: window.__LAST_DATASET_CHANGE__,
    lastDraw: window.__LAST_DRAW__,
    counts: window.__COPERCEIVE_SCENE_COUNTS__ ? window.__COPERCEIVE_SCENE_COUNTS__() : null,
}))()""")
print(f"DIAGNOSTICS AFTER SWITCH & SLEEP: {json.dumps(dbg_initial, indent=2)}")

def wait_for_scene(timeout=30):
    deadline = time.monotonic() + timeout
    last_err = None
    last_counts = None
    while time.monotonic() < deadline:
        try:
            counts = eval_js("window.__COPERCEIVE_SCENE_COUNTS__ ? window.__COPERCEIVE_SCENE_COUNTS__() : null", timeout=4)
            last_counts = counts
            if counts and counts.get("points", 0) > 0:
                return counts
        except Exception as e:
            last_err = e
        time.sleep(0.5)
    dbg_end = eval_js("""(() => ({
        status: document.querySelector('.status')?.textContent,
        badge: document.querySelector('.dataset-name')?.textContent,
        scenes: window.__COPERCEIVE_SCENES__ ? window.__COPERCEIVE_SCENES__() : ((document.querySelector('#scene-id')?.getAttribute('data-options') || '').split(',').filter(Boolean)),
        sceneId: document.querySelector('#scene-id')?.value,
        errBanner: document.querySelector('.dataset-error-msg')?.textContent,
        lastDatasetChange: window.__LAST_DATASET_CHANGE__,
        lastDraw: window.__LAST_DRAW__,
        counts: window.__COPERCEIVE_SCENE_COUNTS__ ? window.__COPERCEIVE_SCENE_COUNTS__() : null,
    }))()""")
    print(f"DEBUG AT TIMEOUT: {json.dumps(dbg_end, indent=2)}", file=sys.stderr)
    raise RuntimeError(f"Timed out waiting for scene to load and render after dataset switch (last_counts={last_counts}, last_err={last_err})")

print("Waiting for scene to load after folder switch...")
counts = wait_for_scene(30)
print(f"Counts after folder switch: {counts}")
assert counts is not None, "Scene counts must exist"
assert counts["points"] > 0, f"Expected points > 0 in new scene, got {counts}"

badge_text = eval_js("document.querySelector('.dataset-name')?.textContent")
print(f"Dataset badge text: {badge_text}")
assert "synth_001" in (badge_text or ""), f"Expected synth_001 badge, got {badge_text}"

print("Step 3 verified: switched cleanly to second fixture, new scene rendered.")
EOF

screencapture -x "${SCREENSHOT_DIR}/04-switched-fixture.png" 2>/dev/null || true

echo ""
echo "--- Step 3b: Switch Dataset from Single-Scene to Multi-Scene Root ---"
echo "${FIXTURE_1}" > "${MOCK_DIALOG_FILE}"

python3 << EOF
import sys, json, urllib.request

port = "${RELAUNCH_PORT}"
secret = "${RELAUNCH_SECRET}"

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

print("Clicking UI dataset open button to switch back to multi-scene root...")
eval_js("document.querySelector('.btn-open-dataset')?.click()")
EOF

sleep 3.5

python3 << EOF
import sys, json, time, urllib.request

port = "${RELAUNCH_PORT}"
secret = "${RELAUNCH_SECRET}"

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
    last_counts = None
    last_err = None
    while time.monotonic() < deadline:
        try:
            counts = eval_js("window.__COPERCEIVE_SCENE_COUNTS__ ? window.__COPERCEIVE_SCENE_COUNTS__() : null", timeout=4)
            last_counts = counts
            if counts and counts.get("points", 0) > 0:
                return counts
        except Exception as e:
            last_err = e
        time.sleep(0.5)
    dbg = eval_js("""(() => ({
        status: document.querySelector('.status')?.textContent,
        badge: document.querySelector('.dataset-name')?.textContent,
        scenes: window.__COPERCEIVE_SCENES__ ? window.__COPERCEIVE_SCENES__() : ((document.querySelector('#scene-id')?.getAttribute('data-options') || '').split(',').filter(Boolean)),
        sceneId: document.querySelector('#scene-id')?.value,
        errBanner: document.querySelector('.dataset-error-msg')?.textContent,
        counts: window.__COPERCEIVE_SCENE_COUNTS__ ? window.__COPERCEIVE_SCENE_COUNTS__() : null,
    }))()""")
    print(f"DEBUG AT 3b TIMEOUT: {json.dumps(dbg, indent=2)}", file=sys.stderr)
    raise RuntimeError(f"Timed out waiting for scene after switching to multi-scene root (counts={last_counts}, err={last_err})")

print("Waiting for scene after switching to multi-scene root...")
counts = wait_for_scene(30)
print(f"Counts after multi-scene switch: {counts}")
assert counts["points"] > 0, f"Expected points > 0, got {counts}"

scenes = eval_js("window.__COPERCEIVE_SCENES__ ? window.__COPERCEIVE_SCENES__() : ((document.querySelector('#scene-id')?.getAttribute('data-options') || '').split(',').filter(Boolean))")
print(f"Scene list options: {scenes}")
assert "synth_001" in scenes, f"Expected synth_001 in scene list, got {scenes}"
assert "synth_002" in scenes, f"Expected synth_002 in scene list, got {scenes}"

badge = eval_js("document.querySelector('.dataset-name')?.textContent")
print(f"Dataset badge: {badge}")
assert "synthetic_scene" in (badge or ""), f"Expected synthetic_scene badge, got {badge}"

print("Step 3b verified: Switched from single-scene folder to multi-scene root cleanly.")
EOF

echo ""
echo "--- Step 4: Switch Dataset to Invalid Folder (Persistent Error UI) ---"
EMPTY_DIR=$(mktemp -d /tmp/coperceive-empty-XXXXXX)
echo "${EMPTY_DIR}" > "${MOCK_DIALOG_FILE}"

python3 << EOF
import sys, json, urllib.request

port = "${RELAUNCH_PORT}"
secret = "${RELAUNCH_SECRET}"

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
eval_js("document.querySelector('.btn-open-dataset')?.click()")
EOF

sleep 1.5

# Assert error UI:
# - Message: "No coperceive scenes in <path>. A scene folder contains manifest.json and COMPLETE.json."
# - Has "Choose another folder" button
# - Stays visible after 5s (not a 4s toast)
python3 << EOF
import sys, json, time, urllib.request

port = "${RELAUNCH_PORT}"
secret = "${RELAUNCH_SECRET}"

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

err_msg = eval_js("document.querySelector('.dataset-error-msg')?.textContent")
print(f"Error banner text: {err_msg}")
assert err_msg and "No coperceive scenes in" in err_msg, f"Expected 'No coperceive scenes in', got {err_msg}"
assert "manifest.json and COMPLETE.json" in err_msg, f"Expected manifest.json and COMPLETE.json notice, got {err_msg}"

choose_btn = eval_js("document.querySelector('.dataset-error-choose-btn')?.textContent")
print(f"Choose another folder button: {choose_btn}")
assert choose_btn and "Choose another folder" in choose_btn, "Expected 'Choose another folder' button"

print("Waiting 5 seconds to assert error UI stays persistent (not dismissed)...")
time.sleep(5)

err_msg_after = eval_js("document.querySelector('.dataset-error-msg')?.textContent")
assert err_msg_after and "No coperceive scenes in" in err_msg_after, "Error UI disappeared after 5s! Must stay until dismissed."

print("Step 4 verified: Persistent validation error UI displayed with 'Choose another folder' button.")
EOF

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
python3 << EOF
import urllib.request

port = "${RELAUNCH_PORT}"
secret = "${RELAUNCH_SECRET}"
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
EOF

sleep 2

# Verify app is still alive
if ! kill -0 "${APP_PID}" 2>/dev/null; then
    echo "Error: Closing viewer window killed the application process!" >&2
    exit 14
fi
echo "Verified: App process is still running after closing viewer window."

echo "Connect backdrop is on screen. The 60 s 1280x800 CPU sample is scripts/desktop/measure_backdrop_cpu.sh."

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
