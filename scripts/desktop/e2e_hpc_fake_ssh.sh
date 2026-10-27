#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
#
# Packaged-app end-to-end check for the Remote HPC workspace profile flow.
#
# Launches the real built .app with a fake `ssh` binary (committed fixture) and
# asks the app's test seam to run its real "Test connection" code against three
# committed server profiles: Slurm + module/venv (cluster-a), Slurm behind a
# ProxyJump alias with modules (cluster-b), and a server without a scheduler
# (cluster-none). Asserts the report, the 1-minute login-node budget, and that
# no secret-shaped value leaks into the report. No cluster or network is used.
set -euo pipefail

APP_BUNDLE="${1:-}"
ROOT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
FIXTURES_DIR="${ROOT_DIR}/src-tauri/tests/fixtures/hpc"
FAKE_SSH="${FIXTURES_DIR}/fake_ssh.sh"

if [ -z "${APP_BUNDLE}" ]; then
    APP_BUNDLE="src-tauri/target/release/bundle/macos/Coperceive.app"
fi
if [ ! -d "${APP_BUNDLE}" ]; then
    APP_BUNDLE=""
    while IFS= read -r candidate; do
        APP_BUNDLE="${candidate}"
        break
    done < <(find "${ROOT_DIR}" -iname "Coperceive.app" -type d 2>/dev/null)
fi
if [ -z "${APP_BUNDLE}" ] || [ ! -d "${APP_BUNDLE}" ]; then
    echo "E2E_HPC_FAIL: built .app not found" >&2
    exit 1
fi
APP_BUNDLE="$(cd "${APP_BUNDLE}" && pwd)"
APP_EXE=""
for candidate in "${APP_BUNDLE}/Contents/MacOS/"*; do
    if [ -f "${candidate}" ] && [ -x "${candidate}" ]; then
        APP_EXE="${candidate}"
        break
    fi
done
if [ -z "${APP_EXE}" ]; then
    echo "E2E_HPC_FAIL: no executable in ${APP_BUNDLE}/Contents/MacOS" >&2
    exit 1
fi
chmod +x "${FAKE_SSH}"

TMP_DIR="$(mktemp -d /tmp/coperceive-hpc-e2e-XXXXXX)"
APP_PID=""
cleanup() {
    local status=$?
    if [ -n "${APP_PID}" ] && kill -0 "${APP_PID}" 2>/dev/null; then
        kill -9 "${APP_PID}" 2>/dev/null || true
    fi
    pkill -f "Coperceive.app/Contents/MacOS" 2>/dev/null || true
    if [ "${status}" -ne 0 ]; then
        for log in "${TMP_DIR}"/*.stdout.log "${TMP_DIR}"/*.stderr.log; do
            [ -f "${log}" ] || continue
            echo "=== ${log} ==="
            tail -n 60 "${log}"
        done
    fi
    rm -rf "${TMP_DIR}"
}
trap cleanup EXIT INT TERM HUP

fail() {
    echo "E2E_HPC_FAIL: $*" >&2
    exit 1
}

run_profile() {
    local alias_name="$1"
    local fixture="${FIXTURES_DIR}/${alias_name}.json"
    local result_file="${TMP_DIR}/${alias_name}.json"
    local stdout_log="${TMP_DIR}/${alias_name}.stdout.log"
    local stderr_log="${TMP_DIR}/${alias_name}.stderr.log"

    [ -f "${fixture}" ] || fail "fixture missing: ${fixture}"
    rm -f "${result_file}"

    echo "--- HPC connection test for ${alias_name} ---"
    COPERCEIVE_TEST_ENV=1 \
    COPERCEIVE_SSH_BIN="${FAKE_SSH}" \
    COPERCEIVE_TEST_HPC_SERVER="${fixture}" \
    COPERCEIVE_TEST_HPC_RESULT="${result_file}" \
    "${APP_EXE}" >"${stdout_log}" 2>"${stderr_log}" &
    APP_PID=$!

    local waited=0
    while [ ! -s "${result_file}" ]; do
        if ! kill -0 "${APP_PID}" 2>/dev/null; then
            fail "app exited before writing the ${alias_name} result"
        fi
        if [ "${waited}" -ge 300 ]; then
            fail "timed out waiting for the ${alias_name} result"
        fi
        sleep 0.2
        waited=$((waited + 1))
    done
    kill -9 "${APP_PID}" 2>/dev/null || true
    wait "${APP_PID}" 2>/dev/null || true
    APP_PID=""

    python3 - "${alias_name}" "${result_file}" <<'PY'
import json
import sys

alias_name, result_file = sys.argv[1], sys.argv[2]
with open(result_file, encoding="utf-8") as handle:
    data = json.load(handle)

if not data.get("ok"):
    print(f"E2E_HPC_FAIL: {alias_name} detection failed: {data.get('error')}", file=sys.stderr)
    sys.exit(1)

report = data["report"]
serialised = json.dumps(data).lower()
for forbidden in ("password", "private_key", "secret", "bearer"):
    if forbidden in serialised:
        print(f"E2E_HPC_FAIL: {alias_name} report contains {forbidden}", file=sys.stderr)
        sys.exit(1)

if report["total_ms"] >= 60_000:
    print(f"E2E_HPC_FAIL: {alias_name} took {report['total_ms']} ms (must be < 60 s)", file=sys.stderr)
    sys.exit(1)

if not report["reachable"] or not report["signed_in"]:
    print(f"E2E_HPC_FAIL: {alias_name} was not reachable/signed in", file=sys.stderr)
    sys.exit(1)

names = {partition["name"] for partition in report["partitions"]}
gpu_types = {gpu for partition in report["partitions"] for gpu in partition["gpu_types"]}

if alias_name == "cluster-a":
    assert report["scheduler"] == "slurm", report["scheduler"]
    assert {"gpu-a100", "main-cpu"} <= names, names
    assert gpu_types == {"a100"}, gpu_types
    assert report["environment"]["kind"] == "venv", report["environment"]["kind"]
    assert report["scratch"]["writable"] and report["scratch"]["path"] == "/scratch/alice", report["scratch"]
elif alias_name == "cluster-b":
    assert report["scheduler"] == "slurm", report["scheduler"]
    assert {"main-cpu", "gpu-h100"} <= names, names
    assert gpu_types == {"h100"}, gpu_types
    assert report["environment"]["kind"] == "modules", report["environment"]["kind"]
    assert report["ssh"]["proxy_jump"] == "jump.example.org", report["ssh"]
elif alias_name == "cluster-none":
    assert report["scheduler"] == "none", report["scheduler"]
    assert report["partitions"] == [], report["partitions"]
    assert report["scheduler_note"], "a missing scheduler must be explained"
    assert report["scratch"]["writable"], report["scratch"]
else:
    print(f"E2E_HPC_FAIL: unknown profile {alias_name}", file=sys.stderr)
    sys.exit(1)

print(
    f"E2E_HPC_MARK: {alias_name} verified (scheduler={report['scheduler']}, "
    f"partitions={sorted(names)}, gpus={sorted(gpu_types)}, scratch={report['scratch']['path']}, "
    f"total_ms={report['total_ms']})"
)
PY
    echo "Step verified: HPC connection test for ${alias_name}."
}

# Repo-relative app path is easier to find when CI builds from a subdirectory;
# run from the repository root so relative fixtures resolve the same way.
run_profile cluster-a
run_profile cluster-b
run_profile cluster-none

echo "HPC e2e verified: profiles, detection, ProxyJump alias and no-scheduler path."
