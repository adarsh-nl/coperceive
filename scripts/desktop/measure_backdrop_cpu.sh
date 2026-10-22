#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# Sample CPU while the Connect backdrop runs at 1280x800.
#
# Launches the built macOS app with resume turned off so the Connect window
# stays up, sets that window to 1280x800, and prints `ps -o %cpu` once a
# second for 60 seconds. The raw lines are the measurement. This script does
# not decide whether the number is under the 5% target.
#
# Usage: scripts/desktop/measure_backdrop_cpu.sh [path-to-Coperceive.app]

set -euo pipefail

if [ "$(uname -s)" != "Darwin" ]; then
    echo "measure_backdrop_cpu.sh only runs on macOS (uname=$(uname -s))." >&2
    exit 2
fi

SAMPLES=60
WIDTH=1280
HEIGHT=800
APP_BUNDLE="${1:-src-tauri/target/release/bundle/macos/Coperceive.app}"

if [ ! -d "${APP_BUNDLE}" ]; then
    for candidate in $(find . -iname "Coperceive.app" -type d 2>/dev/null); do
        if [ -d "${candidate}" ]; then
            APP_BUNDLE="${candidate}"
            break
        fi
    done
fi
APP_BUNDLE="$(cd "${APP_BUNDLE}" && pwd)"
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
    echo "Error: app executable not found under ${APP_BUNDLE}/Contents/MacOS" >&2
    exit 2
fi

TMP_DIR=$(mktemp -d /tmp/coperceive-backdrop-cpu-XXXXXX)
STATE_DIR="${HOME}/Library/Application Support/nl.adarsh.coperceive"
STATE_FILE="${STATE_DIR}/state.json"
STATE_BACKUP="${TMP_DIR}/state.json.bak"
HAD_STATE=0
APP_PID=""

restore_state() {
    if [ "${HAD_STATE}" -eq 1 ]; then
        cp "${STATE_BACKUP}" "${STATE_FILE}"
    else
        rm -f "${STATE_FILE}"
    fi
}

cleanup() {
    local status=$?
    if [ -n "${APP_PID}" ] && kill -0 "${APP_PID}" 2>/dev/null; then
        kill -TERM "${APP_PID}" 2>/dev/null || true
        wait "${APP_PID}" 2>/dev/null || true
    fi
    restore_state
    rm -rf "${TMP_DIR}"
    exit "${status}"
}
trap cleanup EXIT INT TERM HUP

mkdir -p "${STATE_DIR}"
chmod 0700 "${STATE_DIR}"
if [ -f "${STATE_FILE}" ]; then
    cp "${STATE_FILE}" "${STATE_BACKUP}"
    HAD_STATE=1
fi
printf '%s\n' '{"reopen_last_session":false}' > "${STATE_FILE}"
chmod 0600 "${STATE_FILE}"

echo "app=${APP_EXE}"
echo "window_target=${WIDTH}x${HEIGHT}"
echo "samples=${SAMPLES}"
reduce_motion="$(defaults read com.apple.universalaccess reduceMotion 2>/dev/null || echo "unset")"
echo "reduceMotion=${reduce_motion}"

env -u COPERCEIVE_AUTOCONNECT_ROOT -u COPERCEIVE_DESKTOP_AUTOCONNECT_ROOT \
    "${APP_EXE}" > "${TMP_DIR}/stdout.log" 2> "${TMP_DIR}/stderr.log" &
APP_PID=$!
echo "pid=${APP_PID}"

ready=0
for _ in $(seq 1 60); do
    if ! kill -0 "${APP_PID}" 2>/dev/null; then
        echo "Error: app exited before the Connect window appeared" >&2
        cat "${TMP_DIR}/stderr.log" >&2 || true
        exit 3
    fi
    if osascript -e 'tell application "System Events" to tell process "Coperceive" to get name of window 1' >/dev/null 2>&1; then
        ready=1
        break
    fi
    sleep 0.5
done
if [ "${ready}" -ne 1 ]; then
    echo "Error: Connect window did not appear within 30s" >&2
    cat "${TMP_DIR}/stderr.log" >&2 || true
    exit 4
fi

osascript -e "tell application \"System Events\" to tell process \"Coperceive\" to set size of window 1 to {${WIDTH}, ${HEIGHT}}"
sleep 0.5
size="$(osascript -e 'tell application "System Events" to tell process "Coperceive" to get size of window 1')"
echo "window_size=${size}"
case "${size}" in
    *"${WIDTH}"*"${HEIGHT}"*) ;;
    *)
        echo "Error: window size is ${size}, expected ${WIDTH}x${HEIGHT}" >&2
        exit 5
        ;;
esac

descendant_pids() {
    local root="$1"
    local -a queue=("${root}")
    local -a all=()
    local pid child
    while [ "${#queue[@]}" -gt 0 ]; do
        pid="${queue[0]}"
        if [ "${#queue[@]}" -eq 1 ]; then
            queue=()
        else
            queue=("${queue[@]:1}")
        fi
        all+=("${pid}")
        while IFS= read -r child; do
            if [ -n "${child}" ]; then
                queue+=("${child}")
            fi
        done < <(pgrep -P "${pid}" || true)
    done
    printf '%s\n' "${all[@]}"
}

echo "----- raw ps -o pid,ppid,%cpu,comm (one block per second) -----"
for i in $(seq 0 $((SAMPLES - 1))); do
    if ! kill -0 "${APP_PID}" 2>/dev/null; then
        echo "Error: app died during sample ${i}" >&2
        exit 6
    fi
    pids="$(descendant_pids "${APP_PID}" | paste -sd, -)"
    echo "sample=${i} epoch=$(date -u +%Y-%m-%dT%H:%M:%SZ) pids=${pids}"
    ps -p "${pids}" -o pid,ppid,%cpu,comm
    if [ "${i}" -lt $((SAMPLES - 1)) ]; then
        sleep 1
    fi
done

echo "----- end raw samples -----"
