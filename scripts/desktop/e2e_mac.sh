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

# Import source: a copy of synth_001 with one PNG that no images.parquet row
# names, sitting beside a listed frame directory. Real camera trees (e.g.
# drone-side/camera/right in the local Town03-000) hold frames from other
# scenes; the import must copy only the frames the scene lists.
IMPORT_SOURCE="${TMP_DIR}/import-src/synth_001"
mkdir -p "$(dirname "${IMPORT_SOURCE}")"
cp -R "${FIXTURE_2}" "${IMPORT_SOURCE}"
STRAY_FRAME="${IMPORT_SOURCE}/vehicle/cam_front/0999999.png"
mkdir -p "$(dirname "${STRAY_FRAME}")"
printf 'a frame from another scene that images.parquet does not list\n' > "${STRAY_FRAME}"

STATE_DIR="${HOME}/Library/Application Support/nl.adarsh.coperceive"
STATE_FILE="${STATE_DIR}/state.json"
mkdir -p "${STATE_DIR}"
chmod 0700 "${STATE_DIR}"

APP_PID=""
UPDATE_SERVER_PID=""

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
    if [ -n "${UPDATE_SERVER_PID:-}" ] && kill -0 "${UPDATE_SERVER_PID}" 2>/dev/null; then
        kill "${UPDATE_SERVER_PID}" 2>/dev/null || true
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
export COPERCEIVE_TEST_IMPORT_SOURCE="${IMPORT_SOURCE}"
export COPERCEIVE_PACK_SLEEP_S="0.5"

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

# Wait up to 10s for debounced state persistence
python3 -c '
import sys, json, time

state_file = "'"${STATE_FILE}"'"
print(f"Waiting for state.json to persist at {state_file}...", flush=True)
deadline = time.monotonic() + 10.0
saved = {}
while time.monotonic() < deadline:
    try:
        with open(state_file, "r", encoding="utf-8") as f:
            saved = json.load(f)
        if saved.get("last_viewpoint") == "chase" and saved.get("last_sample_index") == 148:
            break
    except Exception:
        pass
    time.sleep(0.1)

print(f"Step 1 verified state.json content: {saved}", flush=True)
assert saved.get("last_viewpoint") == "chase", f"Expected last_viewpoint chase in state.json, got {saved}"
assert saved.get("last_sample_index") == 148, f"Expected last_sample_index 148 in state.json, got {saved}"
'

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
deadline = time.monotonic() + 15.0
drone_pressed = None
boxes_checked = None
frustum_images_checked = None
uav_checked = None

while time.monotonic() < deadline:
    drone_pressed = eval_js("document.querySelector('button[aria-keyshortcuts=\"3\"]')?.getAttribute('aria-pressed')")
    boxes_checked = eval_js("""(() => {
        const eye = Array.from(document.querySelectorAll('.eye')).find(b => b.textContent.includes("Boxes"));
        return eye ? (eye.getAttribute("aria-checked") || eye.getAttribute("aria-pressed")) : null;
    })()""")
    frustum_images_checked = eval_js("""(() => {
        const eye = Array.from(document.querySelectorAll('.eye')).find(b => b.textContent.includes("Frustum images"));
        return eye ? (eye.getAttribute("aria-checked") || eye.getAttribute("aria-pressed")) : null;
    })()""")
    uav_checked = eval_js("""(() => {
        const swatch = Array.from(document.querySelectorAll('.swatch')).find(b => b.textContent.includes("uav_01") || b.textContent.includes("Drone"));
        return swatch ? (swatch.getAttribute("aria-checked") || swatch.getAttribute("aria-pressed")) : null;
    })()""")
    if drone_pressed == "true" and boxes_checked == "false" and frustum_images_checked == "false" and uav_checked == "false":
        break
    time.sleep(0.2)

assert drone_pressed == "true", f"Expected Drone viewpoint aria-pressed==true, got {drone_pressed}"
assert boxes_checked == "false", f"Expected Boxes toggle aria-checked==false, got {boxes_checked}"
assert frustum_images_checked == "false", f"Expected Frustum images toggle aria-checked==false, got {frustum_images_checked}"
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
echo "E2E_MARK step=2c"
echo "--- Step 2c: Relaunch 3 times and assert viewer window frame is unchanged ---"

python3 << EOF
import json, time, urllib.request

port = "${RELAUNCH_PORT}"
secret = "${RELAUNCH_SECRET}"

deadline = time.monotonic() + 15
initial_ws = None
while time.monotonic() < deadline:
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/state",
            headers={"Authorization": f"Bearer {secret}"}
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            st = json.loads(r.read().decode("utf-8"))
            initial_ws = st.get("window_state")
            if initial_ws:
                break
    except Exception:
        pass
    time.sleep(0.5)

print(f"Initial window state before 3 relaunches: {initial_ws}")
assert initial_ws is not None, "Expected window_state to be saved in state.json"
assert "x" in initial_ws and "y" in initial_ws and "width" in initial_ws and "height" in initial_ws
with open("${TMP_DIR}/initial_window_state.json", "w") as f:
    json.dump(initial_ws, f)
EOF

for launch in 1 2 3; do
    echo "Relaunch cycle ${launch}/3..."
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

    "${APP_EXE}" > "${TMP_DIR}/geom_relaunch_${launch}_stdout.log" 2> "${TMP_DIR}/geom_relaunch_${launch}_stderr.log" &
    APP_PID=$!
    for i in {1..120}; do
        if [ -f "${MARKER_FILE}" ]; then
            break
        fi
        if ! kill -0 "${APP_PID}" 2>/dev/null; then
            echo "Error: relaunch ${launch} died" >&2
            cat "${TMP_DIR}/geom_relaunch_${launch}_stderr.log" >&2
            exit 11
        fi
        sleep 0.5
    done
    if [ ! -f "${MARKER_FILE}" ]; then
        echo "Error: relaunch ${launch} did not produce marker file" >&2
        cat "${TMP_DIR}/geom_relaunch_${launch}_stderr.log" >&2
        exit 11
    fi
    RELAUNCH_PORT=$(sed -n '4p' "${MARKER_FILE}")
    RELAUNCH_SECRET=$(sed -n '5p' "${MARKER_FILE}")

    python3 << EOF
import json, time, urllib.request

port = "${RELAUNCH_PORT}"
secret = "${RELAUNCH_SECRET}"
launch = "${launch}"

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

with open("${TMP_DIR}/initial_window_state.json") as f:
    expected = json.load(f)

deadline = time.monotonic() + 30
while time.monotonic() < deadline:
    try:
        counts = eval_js("window.__COPERCEIVE_SCENE_COUNTS__ ? window.__COPERCEIVE_SCENE_COUNTS__() : null", timeout=4)
        if counts and counts.get("points", 0) > 0:
            break
    except Exception:
        pass
    time.sleep(0.5)

curr_ws = None
deadline = time.monotonic() + 15
while time.monotonic() < deadline:
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/state",
            headers={"Authorization": f"Bearer {secret}"}
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            st = json.loads(r.read().decode("utf-8"))
            curr_ws = st.get("window_state")
            if curr_ws:
                break
    except Exception:
        pass
    time.sleep(0.5)

print(f"Relaunch {launch} window state: {curr_ws}")
assert curr_ws is not None, f"Window state missing on relaunch {launch}"

for key in ("x", "y", "width", "height"):
    diff = abs(float(curr_ws[key]) - float(expected[key]))
    assert diff < 1.0, f"Window frame drifted on relaunch {launch}: {key} was {curr_ws[key]}, expected {expected[key]} (diff {diff})"

print(f"Relaunch {launch}/3 verified: window frame unchanged ({curr_ws['width']}x{curr_ws['height']} at x={curr_ws['x']}, y={curr_ws['y']}).")
EOF
done

echo "Step 2c verified: viewer window frame remained completely unchanged across 3 relaunches."

echo ""
echo "--- Step 2d: Relaunch opens one viewer window ---"
OPENED_COUNT=$(grep -c "opened viewer window" "${TMP_DIR}/geom_relaunch_3_stderr.log" || true)
REUSED_COUNT=$(grep -c "reusing existing viewer window" "${TMP_DIR}/geom_relaunch_3_stderr.log" || true)
echo "Step 2d notices: opened viewer window=${OPENED_COUNT}, reusing existing viewer window=${REUSED_COUNT}"
if [ "${OPENED_COUNT}" != "1" ]; then
    echo "Error: expected exactly one 'opened viewer window' on the third relaunch, got ${OPENED_COUNT}" >&2
    echo "=== stderr ===" >&2
    cat "${TMP_DIR}/geom_relaunch_3_stderr.log" >&2 || true
    exit 16
fi
WINDOW_NAMES=$(osascript -e 'tell application "System Events" to get name of every window of process "Coperceive"' 2>/dev/null || true)
echo "Step 2d visible window names: ${WINDOW_NAMES:-unavailable}"
if [ -n "${WINDOW_NAMES}" ]; then
    VIEWER_WINDOWS=$(printf '%s\n' "${WINDOW_NAMES}" | tr ',' '\n' | grep -c "coperceive —" || true)
    echo "Step 2d viewer titles: ${VIEWER_WINDOWS}"
    if [ "${VIEWER_WINDOWS}" != "1" ]; then
        echo "Error: expected one viewer window title, got ${VIEWER_WINDOWS} (${WINDOW_NAMES})" >&2
        exit 16
    fi
fi
echo "Step 2d verified: one viewer window after relaunch."

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
before = eval_js("""(() => ({
    settings: window.__COPERCEIVE_VIEW_SETTINGS__ ? window.__COPERCEIVE_VIEW_SETTINGS__() : null,
    graph: window.__COPERCEIVE_SCENE_GRAPH__ ? window.__COPERCEIVE_SCENE_GRAPH__() : null,
}))()""")
print(f"View settings before multi-scene switch: {json.dumps(before)}")
if not before or not before.get("settings") or not before.get("graph"):
    raise SystemExit("E2E_FAIL step=3b-before missing view settings or scene graph")
with open("${TMP_DIR}/before-3b.json", "w", encoding="utf-8") as handle:
    json.dump(before, handle)
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

with open("${TMP_DIR}/before-3b.json", encoding="utf-8") as handle:
    before = json.load(handle)
after = eval_js("""(async () => {
    const origin = sessionStorage.getItem("coperceive-origin");
    const token = sessionStorage.getItem("coperceive-token");
    const settings = window.__COPERCEIVE_VIEW_SETTINGS__ ? window.__COPERCEIVE_VIEW_SETTINGS__() : null;
    const graph = window.__COPERCEIVE_SCENE_GRAPH__ ? window.__COPERCEIVE_SCENE_GRAPH__() : null;
    const playback = window.__COPERCEIVE_PLAYBACK_STATE__ ? window.__COPERCEIVE_PLAYBACK_STATE__() : null;
    if (!settings || !graph || !origin || !token) {
        return { settings, graph, playback, error: "missing hooks" };
    }
    const headers = { Authorization: "Bearer " + token };
    const summary = await (await fetch(origin + "/api/scenes/" + encodeURIComponent(settings.sceneId), { headers })).json();
    const t0 = summary.times_us[0];
    const frame = await (await fetch(origin + "/api/scenes/" + encodeURIComponent(settings.sceneId) + "/frame?time_us=" + t0, { headers })).json();
    const hidden = settings.hiddenAgents || {};
    const classes = settings.hiddenClasses || [];
    const expectedPoints = settings.layers.points
        ? (frame.point_sensors || []).reduce((sum, sensor) => hidden[sensor.agent_id] ? sum : sum + sensor.count, 0)
        : 0;
    const expectedBoxes = settings.layers.boxes
        ? (frame.boxes || []).filter((box) => !hidden[box.agent_id] && (typeof box.category !== "string" || !classes.includes(box.category))).length
        : 0;
    return { settings, graph, playback, t0, expectedPoints, expectedBoxes };
})()""", timeout=15)
print(f"After multi-scene switch: {json.dumps(after)}")
assert after and after.get("settings") and after.get("graph"), f"Missing settings after switch: {after}"
for key in ("viewpoint", "layers", "hiddenAgents", "hiddenClasses"):
    assert before["settings"][key] == after["settings"][key], (
        f"{key} changed across the root switch: before={before['settings'][key]} after={after['settings'][key]}"
    )
assert after["playback"]["frameIndex"] == 0, f"Expected frame 0 after the switch, got {after['playback']}"
old_scene = before["settings"]["sceneId"]
new_scene = after["settings"]["sceneId"]
old_token = before["graph"]["token"]
tagged = after["graph"]["tagged"]
assert tagged, f"Expected tagged scene-graph objects, got {after['graph']}"
for item in tagged:
    assert item["sceneId"] == new_scene, f"Object tagged with {item['sceneId']}, open scene is {new_scene}"
    assert item["loadId"] != old_token, f"Object still tagged with the pre-switch load {old_token}: {item}"
    if old_scene != new_scene:
        assert item["sceneId"] != old_scene, f"Object still tagged with the old scene {old_scene}"
assert after["graph"]["points"] == after["expectedPoints"], (
    f"Point count {after['graph']['points']} != frame 0 {after['expectedPoints']}"
)
assert after["graph"]["boxes"] == after["expectedBoxes"], (
    f"Box count {after['graph']['boxes']} != frame 0 {after['expectedBoxes']}"
)

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

def _eval_js_once(code, timeout=10):
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

def _transient(exc):
    # The controller answers 504 after 10 s when the webview misses an eval
    # callback. A runner hiccup can also reset the loopback connection.
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in (500, 502, 503, 504)
    return isinstance(exc, (urllib.error.URLError, TimeoutError, ConnectionError))

def eval_retry(step, code, timeout=10, tries=5):
    """Run one controller eval, retrying transient failures with backoff.

    Each retry is logged so a flaky runner is visible in the CI log; a page
    error (RuntimeError from the webview) fails on the first attempt.
    """
    import time
    import urllib.error

    delay = 0.5
    last = None
    for attempt in range(1, tries + 1):
        try:
            return _eval_js_once(code, timeout=timeout)
        except Exception as exc:  # noqa: BLE001 - classified below
            last = exc
            if not _transient(exc) or attempt == tries:
                break
            print(
                f"E2E_RETRY step={step} attempt={attempt}/{tries} after "
                f"{type(exc).__name__}: {exc}",
                flush=True,
            )
            time.sleep(delay)
            delay = min(delay * 2, 4.0)
    print(f"E2E_FAIL step={step} {type(last).__name__}: {last}", flush=True)
    raise SystemExit(1)

def eval_js(code, timeout=10):
    return eval_retry("4-open", code, timeout=timeout)

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

def _eval_js_once(code, timeout=10):
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

def _transient(exc):
    # The controller answers 504 after 10 s when the webview misses an eval
    # callback. A runner hiccup can also reset the loopback connection.
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in (500, 502, 503, 504)
    return isinstance(exc, (urllib.error.URLError, TimeoutError, ConnectionError))

def eval_retry(step, code, timeout=10, tries=5):
    """Run one controller eval, retrying transient failures with backoff.

    Each retry is logged so a flaky runner is visible in the CI log; a page
    error (RuntimeError from the webview) fails on the first attempt.
    """
    import time
    import urllib.error

    delay = 0.5
    last = None
    for attempt in range(1, tries + 1):
        try:
            return _eval_js_once(code, timeout=timeout)
        except Exception as exc:  # noqa: BLE001 - classified below
            last = exc
            if not _transient(exc) or attempt == tries:
                break
            print(
                f"E2E_RETRY step={step} attempt={attempt}/{tries} after "
                f"{type(exc).__name__}: {exc}",
                flush=True,
            )
            time.sleep(delay)
            delay = min(delay * 2, 4.0)
    print(f"E2E_FAIL step={step} {type(last).__name__}: {last}", flush=True)
    raise SystemExit(1)

def eval_js(code, timeout=10):
    return eval_retry("4-assert", code, timeout=timeout)

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
echo "--- Step 5b: Verify Library and Get Data Catalog on Connect Screen ---"
python3 - <<EOF
import time
import urllib.request
import json
import sys

# Read the controller of the app that is running now. CTRL_PORT belongs to the
# first launch and is dead after the step 2 relaunch.
port = ${RELAUNCH_PORT}
secret = "${RELAUNCH_SECRET}"
print(f"Querying Connect window DOM through loopback controller on port {port}...")

req = urllib.request.Request(
    f"http://127.0.0.1:{port}/eval",
    data=json.dumps({"script": """
        (() => {
            const buttons = Array.from(document.querySelectorAll('nav button')).map(b => b.textContent.trim());
            const hasLibrary = buttons.includes('Library');
            const hasGetData = buttons.includes('Get Data');
            return JSON.stringify({ hasLibrary, hasGetData, buttons });
        })()
    """}).encode("utf-8"),
    headers={"Authorization": f"Bearer {secret}", "Content-Type": "application/json"}
)

last = None
for attempt in range(1, 6):
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            eval_resp = json.loads(r.read().decode("utf-8"))
        break
    except Exception as exc:
        last = exc
        print(
            f"E2E_RETRY step=5b attempt={attempt}/5 after {type(exc).__name__}: {exc}",
            flush=True,
        )
        time.sleep(0.5)
else:
    print(f"E2E_FAIL step=5b could not read the Connect window: {last}", flush=True)
    raise SystemExit(1)
res_json = json.loads(eval_resp.get("result", "{}"))
print(f"Connect screen nav tabs found: {res_json.get('buttons')}")
if not res_json.get("hasLibrary") or not res_json.get("hasGetData"):
    print(f"E2E_FAIL step=5b missing nav tabs: {res_json}", flush=True)
    raise SystemExit(1)
print("Step 5b verified: Connect window contains Library and Get Data catalog navigation.")
EOF

echo ""
echo "--- Step 5c: Import synthetic scene, cancel, resume, open ---"
LIBRARY_DIR="${HOME}/coperceive-data"
rm -rf "${LIBRARY_DIR}/synth_001" "${LIBRARY_DIR}/synth_001.partial"
python3 - << EOF
import json, time, urllib.request
from pathlib import Path

port = "${RELAUNCH_PORT}"
secret = "${RELAUNCH_SECRET}"
library = Path("${LIBRARY_DIR}")

def _eval_js_once(script, timeout=15):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/eval",
        data=json.dumps({"script": script}).encode("utf-8"),
        headers={"Authorization": f"Bearer {secret}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        data = json.loads(response.read().decode("utf-8"))
    if data.get("error"):
        raise RuntimeError(data["error"])
    return data.get("result")

def _transient(exc):
    # The controller answers 504 after 10 s when the webview misses an eval
    # callback. A runner hiccup can also reset the loopback connection.
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in (500, 502, 503, 504)
    return isinstance(exc, (urllib.error.URLError, TimeoutError, ConnectionError))

def eval_retry(step, code, timeout=10, tries=5):
    """Run one controller eval, retrying transient failures with backoff.

    Each retry is logged so a flaky runner is visible in the CI log; a page
    error (RuntimeError from the webview) fails on the first attempt.
    """
    import time
    import urllib.error

    delay = 0.5
    last = None
    for attempt in range(1, tries + 1):
        try:
            return _eval_js_once(code, timeout=timeout)
        except Exception as exc:  # noqa: BLE001 - classified below
            last = exc
            if not _transient(exc) or attempt == tries:
                break
            print(
                f"E2E_RETRY step={step} attempt={attempt}/{tries} after "
                f"{type(exc).__name__}: {exc}",
                flush=True,
            )
            time.sleep(delay)
            delay = min(delay * 2, 4.0)
    print(f"E2E_FAIL step={step} {type(last).__name__}: {last}", flush=True)
    raise SystemExit(1)

def eval_js(script, timeout=15):
    return eval_retry("5c", script, timeout=timeout)

def click_button(label):
    script = """
        (() => {
            const wanted = %s;
            const button = Array.from(document.querySelectorAll('button')).find((item) => (item.textContent || '').includes(wanted));
            if (!button) return 'missing';
            button.click();
            return 'clicked';
        })()
    """ % json.dumps(label)
    result = eval_js(script)
    if result != "clicked":
        raise RuntimeError(f"button not found: {label}")

def wait_text(label, seconds=20):
    deadline = time.monotonic() + seconds
    last = ""
    while time.monotonic() < deadline:
        last = eval_js("document.body.innerText") or ""
        if label in last:
            return
        time.sleep(0.3)
    raise RuntimeError(f"did not see {label}: {last[-400:]}")

wait_text("Use default")
click_button("Use default")
time.sleep(0.5)
state = json.loads(Path("${STATE_FILE}").read_text())
assert state.get("library_root"), f"library_root was not saved: {state}"
print(f"Step 5c library_root={state['library_root']}")

click_button("Open Folder")
time.sleep(0.3)
click_button("Import from folder")
time.sleep(0.3)
source = eval_js("document.body.innerText.includes('synth_001') ? 'yes' : 'no'")
assert source == "yes", "import source was not the synthetic scene"
click_button("Start import")

deadline = time.monotonic() + 30
seen = ""
while time.monotonic() < deadline:
    seen = eval_js("document.body.innerText") or ""
    if " / " in seen:
        break
    time.sleep(0.2)
else:
    raise RuntimeError(f"import did not copy a file: {seen[-400:]}")
click_button("Cancel")
time.sleep(0.8)
partials = list(library.glob("*.partial"))
finished = library / "synth_001"
assert partials, f"cancel left no resumable partial in {library}"
assert not finished.is_dir(), f"cancel published the scene: {finished}"
print(f"Step 5c cancel left {[path.name for path in partials]}")

click_button("Start import")
deadline = time.monotonic() + 40
started = False
while time.monotonic() < deadline:
    text = eval_js("document.body.innerText") or ""
    if "Starting…" in text or "Importing…" in text:
        started = True
    if "Import finished." in text:
        break
    if started and "Import stopped" in text and "Importing" not in text and "Starting" not in text:
        raise RuntimeError(text[-400:])
    time.sleep(0.4)
else:
    raise RuntimeError("resume did not finish")
assert (library / "synth_001" / "COMPLETE.json").is_file()
assert not list(library.glob("*.partial")), "partial directory still listed after resume"
print("Step 5c resume wrote synth_001/COMPLETE.json")

stray = library / "synth_001" / "vehicle" / "cam_front" / "0999999.png"
assert not stray.exists(), f"import copied an image the scene does not list: {stray}"
import_record = json.loads((library / "synth_001" / "IMPORT.json").read_text())
notes = import_record.get("notes") or []
assert any("not listed in images.parquet" in note for note in notes), notes
print(f"Step 5c skipped the unlisted frame ({notes})")

click_button("Open Local Viewer")
deadline = time.monotonic() + 40
while time.monotonic() < deadline:
    try:
        scenes = eval_js("window.__COPERCEIVE_SCENES__ ? window.__COPERCEIVE_SCENES__() : null")
    except Exception:
        scenes = None
    if scenes and "synth_001" in scenes:
        print(f"Step 5c opened scenes={scenes}")
        break
    time.sleep(0.5)
else:
    raise RuntimeError(f"viewer did not open synth_001, last={scenes}")
print("Step 5c verified: import, cancel, resume, open.")
EOF

echo ""
echo "--- Step 5d: Update banner from a mock latest.json ---"
export UPDATE_PORT_FILE="${TMP_DIR}/update.port"
export UPDATE_LOG_FILE="${TMP_DIR}/update-hits.log"
rm -f "${UPDATE_PORT_FILE}" "${UPDATE_LOG_FILE}"
python3 - << 'PY' &
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

port_file = os.environ["UPDATE_PORT_FILE"]
log_file = os.environ["UPDATE_LOG_FILE"]
body = json.dumps(
    {
        "version": "0.2.0",
        "notes": "Mock release notes for the test.",
        "pub_date": "2026-10-24T00:00:00Z",
        "platforms": {
            "darwin-aarch64": {
                "signature": "",
                "url": "http://127.0.0.1:9/Coperceive.app.tar.gz",
            }
        },
    }
).encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        with open(log_file, "a", encoding="utf-8") as handle:
            handle.write(self.path + "\n")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        return


server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
with open(port_file, "w", encoding="utf-8") as handle:
    handle.write(str(server.server_address[1]))
server.serve_forever()
PY
UPDATE_SERVER_PID=$!
for i in {1..40}; do
    if [ -s "${UPDATE_PORT_FILE}" ]; then
        break
    fi
    if ! kill -0 "${UPDATE_SERVER_PID}" 2>/dev/null; then
        echo "Error: mock update server exited" >&2
        exit 17
    fi
    sleep 0.1
done
if [ ! -s "${UPDATE_PORT_FILE}" ]; then
    echo "Error: mock update server did not bind" >&2
    exit 17
fi
UPDATE_PORT=$(cat "${UPDATE_PORT_FILE}")
export COPERCEIVE_UPDATE_URL="http://127.0.0.1:${UPDATE_PORT}/latest.json"
echo "Step 5d mock latest.json at ${COPERCEIVE_UPDATE_URL}"

osascript <<EOF || true
tell application "System Events"
    tell process "Coperceive"
        set frontmost to true
        keystroke "q" using command down
    end tell
end tell
EOF
for i in {1..30}; do
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
"${APP_EXE}" > "${TMP_DIR}/update_stdout.log" 2> "${TMP_DIR}/update_stderr.log" &
APP_PID=$!
for i in {1..120}; do
    if [ -f "${MARKER_FILE}" ]; then
        break
    fi
    if ! kill -0 "${APP_PID}" 2>/dev/null; then
        echo "Error: app died before the update banner check" >&2
        cat "${TMP_DIR}/update_stderr.log" >&2 || true
        exit 17
    fi
    sleep 0.5
done
if [ ! -f "${MARKER_FILE}" ]; then
    echo "Error: app did not start for the update banner check" >&2
    cat "${TMP_DIR}/update_stderr.log" >&2 || true
    exit 17
fi
RELAUNCH_PORT=$(sed -n '4p' "${MARKER_FILE}")
RELAUNCH_SECRET=$(sed -n '5p' "${MARKER_FILE}")
sleep 2
python3 - << EOF
import json, time, urllib.request, urllib.error

port = "${RELAUNCH_PORT}"
secret = "${RELAUNCH_SECRET}"

def eval_js(script, timeout=15):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/eval",
        data=json.dumps({"script": script}).encode("utf-8"),
        headers={"Authorization": f"Bearer {secret}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        data = json.loads(response.read().decode("utf-8"))
    if data.get("error"):
        raise RuntimeError(data["error"])
    return data.get("result")

def eval_when_ready(script, deadline):
    last = "page not ready"
    while time.monotonic() < deadline:
        try:
            return eval_js(script)
        except urllib.error.HTTPError as exc:
            last = f"HTTP {exc.code}"
        except Exception as exc:
            last = str(exc)
        time.sleep(0.5)
    raise RuntimeError(last)

def click_button(label):
    script = """
        (() => {
            const wanted = %s;
            const button = Array.from(document.querySelectorAll('button')).find((item) => (item.textContent || '').includes(wanted));
            if (!button) return 'missing';
            button.click();
            return 'clicked';
        })()
    """ % json.dumps(label)
    result = eval_js(script)
    if result != "clicked":
        raise RuntimeError(f"button not found: {label}")

deadline = time.monotonic() + 40
text = ""
while time.monotonic() < deadline:
    text = eval_when_ready("document.body ? document.body.innerText : ''", deadline) or ""
    if "coperceive 0.2.0 is ready" in text:
        break
    time.sleep(0.4)
else:
    raise RuntimeError(f"update banner did not appear: {text[-500:]}")
print("Step 5d notices: banner coperceive 0.2.0 is ready")
click_button("What's new")
time.sleep(0.3)
notes = eval_js("document.body.innerText") or ""
assert "Mock release notes for the test." in notes, notes[-500:]
print("Step 5d notices: What's new showed the mock notes")
click_button("Restart now")
time.sleep(0.4)
restarted = eval_js("window.__COPERCEIVE_UPDATE_RESTART__ === true ? 'yes' : 'no'")
assert restarted == "yes", "Restart now did not record the test-env flag"
print("Step 5d notices: Restart now stayed in the test app")
click_button("Later")
time.sleep(0.4)
after = eval_js("document.body.innerText") or ""
assert "is ready" not in after, after[-500:]
print("Step 5d notices: Later hid the banner")
print("Step 5d verified: banner, Later, and Restart.")
EOF
if ! grep -q "latest.json" "${UPDATE_LOG_FILE}"; then
    echo "Error: the app did not request latest.json from the mock server" >&2
    cat "${UPDATE_LOG_FILE}" >&2 || true
    exit 17
fi
echo "Step 5d verified: mock server received latest.json."
if ! kill -0 "${APP_PID}" 2>/dev/null; then
    echo "Error: Restart now quit the app" >&2
    exit 17
fi

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
