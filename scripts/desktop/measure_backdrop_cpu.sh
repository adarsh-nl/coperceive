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
echo "warmup_s=5"
reduce_motion="$(defaults read com.apple.universalaccess reduceMotion 2>/dev/null || echo "unset")"
echo "reduceMotion=${reduce_motion}"

CTRL_INFO="${TMP_DIR}/ctrl-info"
# Test hooks. FORCE_MOTION is ignored unless TEST_ENV is also set.
env -u COPERCEIVE_AUTOCONNECT_ROOT -u COPERCEIVE_DESKTOP_AUTOCONNECT_ROOT \
    COPERCEIVE_TEST_ENV=1 \
    COPERCEIVE_FORCE_MOTION=1 \
    COPERCEIVE_CTRL_INFO_FILE="${CTRL_INFO}" \
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

motion="missing"
for _ in $(seq 1 20); do
    if [ -f "${CTRL_INFO}" ]; then
        break
    fi
    sleep 0.5
done
if [ -f "${CTRL_INFO}" ]; then
    CTRL_PORT="$(sed -n '1p' "${CTRL_INFO}")"
    CTRL_SECRET="$(sed -n '2p' "${CTRL_INFO}")"
    for _ in $(seq 1 40); do
        motion="$(COPERCEIVE_EVAL_PORT="${CTRL_PORT}" COPERCEIVE_EVAL_SECRET="${CTRL_SECRET}" python3 - <<'PY'
import json, os, sys, urllib.request
port = os.environ["COPERCEIVE_EVAL_PORT"]
secret = os.environ["COPERCEIVE_EVAL_SECRET"]
req = urllib.request.Request(
    f"http://127.0.0.1:{port}/eval",
    data=json.dumps({"script": "document.querySelector('[data-backdrop-motion]')?.getAttribute('data-backdrop-motion') || ''"}).encode(),
    headers={"Authorization": f"Bearer {secret}", "Content-Type": "application/json"},
)
try:
    with urllib.request.urlopen(req, timeout=8) as response:
        body = json.loads(response.read().decode())
    print(body.get("result") or "")
except Exception as exc:
    print(f"error:{type(exc).__name__}", file=sys.stderr)
    print("")
PY
)"
        if [ "${motion}" = "animated" ]; then
            break
        fi
        sleep 0.5
    done
fi
echo "backdrop_motion=${motion}"
if [ "${motion}" != "animated" ]; then
    echo "Error: backdrop is '${motion}', expected animated. Refusing a static-frame measurement." >&2
    exit 7
fi

# 5 s warm-up, then 60 s of samples. The warm-up lines are logged and dropped
# from median and p95.
TOTAL=$((SAMPLES + 5))
SAMPLES_FILE="${TMP_DIR}/cpu-samples.txt"
: > "${SAMPLES_FILE}"
echo "----- raw ps -o pid,%cpu (one block per second; comm omitted) -----"
for i in $(seq 0 $((TOTAL - 1))); do
    if ! kill -0 "${APP_PID}" 2>/dev/null; then
        echo "Error: app died during sample ${i}" >&2
        exit 6
    fi
    pids="$(descendant_pids "${APP_PID}" | paste -sd, -)"
    if [ "${i}" -lt 5 ]; then
        phase="warmup"
    else
        phase="sample"
    fi
    line="cpu_block phase=${phase} i=${i} epoch=$(date -u +%Y-%m-%dT%H:%M:%SZ) pids=${pids}"
    echo "${line}" | tee -a "${SAMPLES_FILE}"
    ps -p "${pids}" -o pid=,%cpu= | tee -a "${SAMPLES_FILE}" || echo "ps_exit=$?"
    if [ "${i}" -lt $((TOTAL - 1)) ]; then
        sleep 1
    fi
done
echo "----- end raw samples -----"

COPERCEIVE_CPU_SAMPLES_FILE="${SAMPLES_FILE}" \
COPERCEIVE_CPU_OS_REDUCE="${reduce_motion}" \
COPERCEIVE_CPU_MD="${COPERCEIVE_CPU_MD:-docs/media/measurements/backdrop-cpu.md}" \
python3 - "${APP_PID}" <<'PY'
import math, os, sys
from pathlib import Path

app_pid = sys.argv[1]
sample_file = os.environ.get("COPERCEIVE_CPU_SAMPLES_FILE")
if not sample_file:
    print("Error: CPU sample file was not passed", file=sys.stderr)
    sys.exit(8)
text = Path(sample_file).read_text()
blocks = []
current = None
for line in text.splitlines():
    if line.startswith("cpu_block "):
        phase = "warmup" if "phase=warmup" in line else "sample"
        current = {"phase": phase, "cpus": []}
        blocks.append(current)
        continue
    if current is None or not line.strip() or line.startswith("ps_exit="):
        continue
    parts = line.split()
    if len(parts) < 2:
        continue
    try:
        current["cpus"].append(float(parts[1]))
    except ValueError:
        continue
used = [sum(block["cpus"]) for block in blocks if block["phase"] == "sample" and block["cpus"]]
if len(used) != 60:
    print(f"Error: expected 60 measurement samples, got {len(used)}", file=sys.stderr)
    sys.exit(8)
ordered = sorted(used)
mid = len(ordered) // 2
median = (ordered[mid - 1] + ordered[mid]) / 2
rank = math.ceil(0.95 * len(ordered)) - 1
p95 = ordered[rank]
def fmt(value: float) -> str:
    return f"{value:.1f}"
series = ", ".join(fmt(value) for value in used)
print(f"backdrop_cpu_median={fmt(median)}")
print(f"backdrop_cpu_p95={fmt(p95)}")
print(f"backdrop_cpu_samples={series}")
print(f"backdrop_cpu_n={len(used)}")
out = os.environ.get("COPERCEIVE_CPU_MD")
if out:
    body = f"""# Connect backdrop CPU

Measured by `scripts/desktop/measure_backdrop_cpu.sh` on the macOS runner while the Connect backdrop animated at 1280×800.

- Motion path: animated. `COPERCEIVE_FORCE_MOTION=1` is honoured only when `COPERCEIVE_TEST_ENV` is also set, and it ignores `prefers-reduced-motion`.
- OS `reduceMotion` at launch: {os.environ.get("COPERCEIVE_CPU_OS_REDUCE", "unknown")}
- Warm-up dropped: first 5 s (not included below)
- Samples: 60 × 1 s. Each value is the sum of `ps` `%cpu` for the app process tree.
- Median: {fmt(median)}
- p95: {fmt(p95)} (nearest rank, index ceil(0.95 × n) − 1)
- App pid: {app_pid}
- Target: median under 5% of one core. {"Met." if median < 5 else "Not met."}

## Samples (%cpu)

`{series}`
"""
    path = Path(out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    print(f"backdrop_cpu_md={path}")
if median >= 5:
    print(f"Error: median %cpu {fmt(median)} is not under 5", file=sys.stderr)
    sys.exit(9)
PY
