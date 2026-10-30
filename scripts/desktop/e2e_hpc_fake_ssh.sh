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

# --- Browse: real `coperceive remote scan` over the fake ssh ---------------
# Each launch asks the app's test seam to run one real scan (argv quoting,
# SSH call, JSON parsing, error mapping) for a committed profile and writes the
# parsed result. The listings, the truncated note and the refused path come from
# the fake cluster, so no network or cluster is involved.
run_scan() {
    local alias_name="$1"
    local root="$2"
    local path="$3"
    local fixture="${FIXTURES_DIR}/${alias_name}.json"
    local scan_path="${path:-.}"
    local slug
    slug="$(printf '%s_%s' "${root}" "${scan_path}" | tr -c 'A-Za-z0-9' '_')"
    local result_file="${TMP_DIR}/scan-${alias_name}-${slug}.json"
    local stdout_log="${TMP_DIR}/scan-${alias_name}-${slug}.stdout.log"
    local stderr_log="${TMP_DIR}/scan-${alias_name}-${slug}.stderr.log"

    [ -f "${fixture}" ] || fail "fixture missing: ${fixture}"
    rm -f "${result_file}"

    echo "--- HPC browse scan for ${alias_name}: ${root} ${scan_path} ---"
    COPERCEIVE_TEST_ENV=1 \
    COPERCEIVE_SSH_BIN="${FAKE_SSH}" \
    COPERCEIVE_TEST_HPC_SCAN_PROFILE="${fixture}" \
    COPERCEIVE_TEST_HPC_SCAN_RESULT="${result_file}" \
    COPERCEIVE_TEST_HPC_SCAN_ROOT="${root}" \
    COPERCEIVE_TEST_HPC_SCAN_PATH="${scan_path}" \
    "${APP_EXE}" >"${stdout_log}" 2>"${stderr_log}" &
    APP_PID=$!

    local waited=0
    while [ ! -s "${result_file}" ]; do
        if ! kill -0 "${APP_PID}" 2>/dev/null; then
            fail "app exited before writing the ${alias_name} scan result"
        fi
        if [ "${waited}" -ge 300 ]; then
            fail "timed out waiting for the ${alias_name} scan result"
        fi
        sleep 0.2
        waited=$((waited + 1))
    done
    kill -9 "${APP_PID}" 2>/dev/null || true
    wait "${APP_PID}" 2>/dev/null || true
    APP_PID=""

    printf '%s\n' "${result_file}"
}

scan_ok() {
    python3 - "$1" "$2" "$3" <<'PY'
import json
import sys

case, result_file, root = sys.argv[1], sys.argv[2], sys.argv[3]
with open(result_file, encoding="utf-8") as handle:
    data = json.load(handle)

if not data.get("ok"):
    print(f"E2E_HPC_FAIL: {case} scan failed: {data.get('error')}", file=sys.stderr)
    sys.exit(1)
scan = data["scan"]
if scan["status"] != "ok":
    print(f"E2E_HPC_FAIL: {case} scan status {scan['status']}", file=sys.stderr)
    sys.exit(1)
if scan["root"] != root:
    print(f"E2E_HPC_FAIL: {case} root {scan['root']} != {root}", file=sys.stderr)
    sys.exit(1)
for folder in scan["folders"]:
    path = folder["path"]
    if path.startswith("/") or ".." in path.split("/"):
        print(f"E2E_HPC_FAIL: {case} folder path is not relative: {path}", file=sys.stderr)
        sys.exit(1)
serialised = json.dumps(data).lower()
for forbidden in ("password", "private_key", "bearer", "secret"):
    if forbidden in serialised:
        print(f"E2E_HPC_FAIL: {case} scan result contains {forbidden}", file=sys.stderr)
        sys.exit(1)
print(
    f"E2E_HPC_MARK: scan {case} ok (root={scan['root']}, path={scan['relative_path']}, "
    f"folders={scan['folder_count']}, scenes={scan['scene_count']}, "
    f"datasets={scan['dataset_count']}, truncated={scan['truncated']})"
)
PY
}

scan_refused() {
    python3 - "$1" "$2" <<'PY'
import json
import sys

case, result_file = sys.argv[1], sys.argv[2]
with open(result_file, encoding="utf-8") as handle:
    data = json.load(handle)
if data.get("ok"):
    print(f"E2E_HPC_FAIL: {case} scan should have been refused", file=sys.stderr)
    sys.exit(1)
error = data.get("error") or ""
if "outside the data root" not in error:
    print(f"E2E_HPC_FAIL: {case} refusal lost the server message: {error}", file=sys.stderr)
    sys.exit(1)
print(f"E2E_HPC_MARK: scan {case} refused with the server's plain message ({error})")
PY
}

scan_root_one() {
    python3 - "$1" "$2" <<'PY'
import json
import sys

case, result_file = sys.argv[1], sys.argv[2]
with open(result_file, encoding="utf-8") as handle:
    data = json.load(handle)
if not data.get("ok"):
    print(f"E2E_HPC_FAIL: {case} scan failed: {data.get('error')}", file=sys.stderr)
    sys.exit(1)
scan = data["scan"]
scenes = {scene["scene_id"] for scene in scan["scenes"]}
folders = {folder["path"]: folder for folder in scan["folders"]}
if scenes != {"Town03-000"}:
    print(f"E2E_HPC_FAIL: {case} scenes {scenes}", file=sys.stderr)
    sys.exit(1)
if not folders.get("Town03-000", {}).get("is_scene"):
    print(f"E2E_HPC_FAIL: {case} Town03-000 is not flagged as a scene", file=sys.stderr)
    sys.exit(1)
datasets = {dataset["path"] for dataset in scan["datasets"]}
if datasets != {".", "datasets"}:
    print(f"E2E_HPC_FAIL: {case} datasets {datasets}", file=sys.stderr)
    sys.exit(1)
print(
    f"E2E_HPC_MARK: scan {case} lists Town03-000 as a scene and datasets "
    f"{sorted(datasets)} (folders={scan['folder_count']})"
)
PY
}

scan_truncated() {
    python3 - "$1" "$2" <<'PY'
import json
import sys

case, result_file = sys.argv[1], sys.argv[2]
with open(result_file, encoding="utf-8") as handle:
    data = json.load(handle)
if not data.get("ok"):
    print(f"E2E_HPC_FAIL: {case} scan failed: {data.get('error')}", file=sys.stderr)
    sys.exit(1)
scan = data["scan"]
if not scan["truncated"]:
    print(f"E2E_HPC_FAIL: {case} scan was not marked truncated", file=sys.stderr)
    sys.exit(1)
if scan["truncated_reason"] != "entry limit":
    print(f"E2E_HPC_FAIL: {case} reason {scan['truncated_reason']}", file=sys.stderr)
    sys.exit(1)
if scan["max_entries"] != 200:
    print(f"E2E_HPC_FAIL: {case} max_entries {scan['max_entries']}", file=sys.stderr)
    sys.exit(1)
reasons = [entry["reason"] for entry in scan["skipped"]]
if not any("outside the data root" in reason for reason in reasons):
    print(f"E2E_HPC_FAIL: {case} skipped reasons {reasons}", file=sys.stderr)
    sys.exit(1)
scenes = {scene["scene_id"] for scene in scan["scenes"]}
if scenes != {"Town03-007", "Town06-002"}:
    print(f"E2E_HPC_FAIL: {case} scenes {scenes}", file=sys.stderr)
    sys.exit(1)
print(
    f"E2E_HPC_MARK: scan {case} truncated at {scan['max_entries']} entries "
    f"({scan['truncated_reason']}), skipped {len(scan['skipped'])} entry with a plain reason"
)
PY
}

scan_none() {
    python3 - "$1" "$2" <<'PY'
import json
import sys

case, result_file = sys.argv[1], sys.argv[2]
with open(result_file, encoding="utf-8") as handle:
    data = json.load(handle)
if not data.get("ok"):
    print(f"E2E_HPC_FAIL: {case} scan failed: {data.get('error')}", file=sys.stderr)
    sys.exit(1)
scan = data["scan"]
scenes = {scene["scene_id"] for scene in scan["scenes"]}
if scenes != {"Files-001"}:
    print(f"E2E_HPC_FAIL: {case} scenes {scenes}", file=sys.stderr)
    sys.exit(1)
if scan["dataset_count"] != 0:
    print(f"E2E_HPC_FAIL: {case} dataset_count {scan['dataset_count']}", file=sys.stderr)
    sys.exit(1)
print(f"E2E_HPC_MARK: scan {case} lists Files-001 as a scene (no datasets)")
PY
}

run_save_roundtrip() {
    local fixture="${FIXTURES_DIR}/cluster-b.json"
    local result_file="${TMP_DIR}/server-save.json"
    local state_dir="${TMP_DIR}/server-state"
    local stdout_log="${TMP_DIR}/server-save.stdout.log"
    local stderr_log="${TMP_DIR}/server-save.stderr.log"
    [ -f "${fixture}" ] || fail "fixture missing: ${fixture}"
    rm -rf "${state_dir}" "${result_file}"

    echo "--- HPC server save/reload/edit round trip ---"
    COPERCEIVE_TEST_ENV=1 \
    COPERCEIVE_STATE_DIR="${state_dir}" \
    COPERCEIVE_SSH_BIN="${FAKE_SSH}" \
    COPERCEIVE_TEST_HPC_SERVER="${fixture}" \
    COPERCEIVE_TEST_HPC_SAVE_RESULT="${result_file}" \
    "${APP_EXE}" >"${stdout_log}" 2>"${stderr_log}" &
    APP_PID=$!

    local waited=0
    while [ ! -s "${result_file}" ]; do
        if ! kill -0 "${APP_PID}" 2>/dev/null; then
            fail "app exited before writing the server save result"
        fi
        if [ "${waited}" -ge 300 ]; then
            fail "timed out waiting for the server save result"
        fi
        sleep 0.2
        waited=$((waited + 1))
    done
    kill -9 "${APP_PID}" 2>/dev/null || true
    wait "${APP_PID}" 2>/dev/null || true
    APP_PID=""

    python3 - "${result_file}" "${state_dir}" <<'PY'
import json
import sys
from pathlib import Path

result_file, state_dir = sys.argv[1], sys.argv[2]
with open(result_file, encoding="utf-8") as handle:
    data = json.load(handle)
if not data.get("ok"):
    print(f"E2E_SAVE_FAIL: {data.get('error')}", file=sys.stderr)
    sys.exit(1)
saved_id = data.get("saved_id") or ""
if not saved_id or saved_id not in (data.get("listed_ids") or []):
    print(f"E2E_SAVE_FAIL: saved server missing from reload: {data}", file=sys.stderr)
    sys.exit(1)
if not data.get("listed_before_edit"):
    print(f"E2E_SAVE_FAIL: server not listed before edit: {data}", file=sys.stderr)
    sys.exit(1)
if not str(data.get("edited_name", "")).endswith("(edited)"):
    print(f"E2E_SAVE_FAIL: edit not saved: {data}", file=sys.stderr)
    sys.exit(1)
if data.get("persisted_name") != data.get("edited_name"):
    print(f"E2E_SAVE_FAIL: edited name not on disk: {data}", file=sys.stderr)
    sys.exit(1)
if data.get("persisted_cpus") != 8:
    print(f"E2E_SAVE_FAIL: edited resources not on disk: {data}", file=sys.stderr)
    sys.exit(1)
store = Path(state_dir) / "servers.json"
if not store.is_file():
    print(f"E2E_SAVE_FAIL: server store missing at {store}", file=sys.stderr)
    sys.exit(1)
text = store.read_text(encoding="utf-8")
if saved_id not in text or "(edited)" not in text:
    print("E2E_SAVE_FAIL: server store does not contain the saved/edited entry", file=sys.stderr)
    sys.exit(1)
print(
    f"E2E_SAVE_MARK: server save/reload/edit verified (id={saved_id}, "
    f"name={data['persisted_name']}, cpus={data['persisted_cpus']})"
)
PY
    echo "Step verified: HPC server save, reload, list and edit."
}

# Repo-relative app path is easier to find when CI builds from a subdirectory;
# run from the repository root so relative fixtures resolve the same way.
run_profile cluster-a
run_profile cluster-b
run_profile cluster-none

# The "New evaluation" run plan against the same fake cluster: builds, checks
# and saves a plan (module and container environments); never submits.
bash "$(dirname "$0")/e2e_hpc_plan.sh" "${APP_EXE}"

SCAN_FILE="$(run_scan cluster-a /data/scenes "" | tail -n 1)"
scan_ok "cluster-a root" "${SCAN_FILE}" /data/scenes
scan_root_one "cluster-a root" "${SCAN_FILE}"
echo "Step verified: HPC browse listed data folders on cluster-a."

SCAN_FILE="$(run_scan cluster-a /data/scenes datasets | tail -n 1)"
scan_ok "cluster-a datasets" "${SCAN_FILE}" /data/scenes
scan_truncated "cluster-a datasets" "${SCAN_FILE}"
echo "Step verified: HPC browse showed the truncated and skipped notices for cluster-a."

SCAN_FILE="$(run_scan cluster-a /data/scenes escape-link | tail -n 1)"
scan_refused "cluster-a escape-link" "${SCAN_FILE}"
echo "Step verified: HPC browse passed the server's refused-path message through."

SCAN_FILE="$(run_scan cluster-none /data/files "" | tail -n 1)"
scan_ok "cluster-none root" "${SCAN_FILE}" /data/files
scan_none "cluster-none root" "${SCAN_FILE}"
echo "Step verified: HPC browse read the no-scheduler server's folders."

run_save_roundtrip

echo "HPC e2e verified: profiles, detection, ProxyJump alias, no-scheduler path, run plans, Browse, and server save/reload/edit."
