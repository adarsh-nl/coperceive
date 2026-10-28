#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
#
# Packaged-app end-to-end check for the "New evaluation" run plan.
#
# Uses the real built .app with the committed fake ssh against cluster-b, and
# builds two plans: one with the profile's module environment, one whose
# pipeline registration carries a container environment (the server re-resolves
# the environment from the registration; a client-side override is ignored). It asserts the plan JSON (argv separation,
# checkpoint/split sha256, environment, contract-check script), the saved 0600
# plan file under results_root, and that nothing was submitted (no sbatch /
# scancel in the fake-ssh command log).
set -euo pipefail

APP_EXE="${1:-}"
if [ -z "${APP_EXE}" ] || [ ! -x "${APP_EXE}" ]; then
    echo "E2E_PLAN_FAIL: pass the built app executable as the first argument" >&2
    exit 1
fi
ROOT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
FIXTURES_DIR="${ROOT_DIR}/src-tauri/tests/fixtures/hpc"
FAKE_SSH="${FIXTURES_DIR}/fake_ssh.sh"
chmod +x "${FAKE_SSH}"

TMP_DIR="$(mktemp -d /tmp/coperceive-hpc-plan-e2e-XXXXXX)"
PLAN_ROOT="${TMP_DIR}/remote"
APP_PID=""
cleanup() {
    local status=$?
    if [ -n "${APP_PID}" ] && kill -0 "${APP_PID}" 2>/dev/null; then
        kill -9 "${APP_PID}" 2>/dev/null || true
    fi
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
    echo "E2E_PLAN_FAIL: $*" >&2
    exit 1
}

mkdir -p "${PLAN_ROOT}/data/code/pipelines" \
         "${PLAN_ROOT}/data/code/checkpoints" \
         "${PLAN_ROOT}/data/code/splits" \
         "${PLAN_ROOT}/data/code/containers" \
         "${PLAN_ROOT}/data/scenes" \
         "${PLAN_ROOT}/data/results" \
         "${PLAN_ROOT}/work/alice"
printf 'checkpoint-bytes\n' > "${PLAN_ROOT}/data/code/checkpoints/model.pt"
printf 'scene-a\nscene-b\n' > "${PLAN_ROOT}/data/code/splits/test.txt"
printf 'fake container image for the contract-check render\n' > "${PLAN_ROOT}/data/code/containers/pipeline.sif"
printf '#!/usr/bin/env python3\n' > "${PLAN_ROOT}/data/code/pipelines/contract_pipeline.py"

cat > "${PLAN_ROOT}/data/code/pipelines/coperceive-pipeline.json" <<'JSON'
{
  "name": "contract-check",
  "interpreter": ["/usr/bin/python3"],
  "environment": {"kind": "none"},
  "arguments": [
    {"name": "checkpoint", "kind": "path", "role": "checkpoint", "required": true, "help": "weights"},
    {"name": "split", "kind": "path", "role": "test_data", "required": true, "help": "test split"},
    {"name": "extra", "kind": "str", "help": "free text"},
    {"name": "frame_stride", "kind": "int", "default": 1},
    {"name": "half", "kind": "bool", "default": false}
  ]
}
JSON

mkdir -p "${PLAN_ROOT}/data/code/pipelines/container"
printf '#!/usr/bin/env python3\n' > "${PLAN_ROOT}/data/code/pipelines/container/contract_pipeline.py"
cat > "${PLAN_ROOT}/data/code/pipelines/container/coperceive-pipeline.json" <<'JSON'
{
  "name": "contract-check",
  "script": "/data/code/pipelines/container/contract_pipeline.py",
  "interpreter": ["/usr/bin/python3"],
  "environment": {
    "kind": "container",
    "container_image": "/data/code/containers/pipeline.sif",
    "compute_has_internet": false
  },
  "arguments": [
    {"name": "checkpoint", "kind": "path", "role": "checkpoint", "required": true, "help": "weights"},
    {"name": "split", "kind": "path", "role": "test_data", "required": true, "help": "test split"},
    {"name": "extra", "kind": "str", "help": "free text"},
    {"name": "frame_stride", "kind": "int", "default": 1},
    {"name": "half", "kind": "bool", "default": false}
  ]
}
JSON

make_request() {
    local name="$1"
    local local_registration="$2"
    local remote_script="$3"
    local request_file="${TMP_DIR}/${name}-request.json"
    python3 - "${request_file}" "${FIXTURES_DIR}/cluster-b.json" \
        "${local_registration}" "${remote_script}" <<'PY'
import json
import os
import sys

request_file, server_path, local_registration, remote_script = sys.argv[1:5]
# The registration file exists locally in the fake remote tree; the request
# carries its remote path, which is what the app re-reads on save.
spec = json.load(open(local_registration, encoding="utf-8"))
spec["script"] = remote_script
registration_path = os.path.join(os.path.dirname(remote_script), "coperceive-pipeline.json")
payload = {
    "server": server_path,
    "save": True,
    "request": {
        "profile_id": "fixture-cluster-b",
        "pipeline": spec,
        "pipeline_source": "custom",
        "registration_path": registration_path,
        "plugin_manifest": None,
        "values": {
            "checkpoint": "/data/code/checkpoints/model.pt",
            "split": "/data/code/splits/test.txt",
            "extra": "a b; touch /tmp/x",
            "frame_stride": 2,
            "half": True,
        },
        "checkpoint": "/data/code/checkpoints/model.pt",
        "split": "/data/code/splits/test.txt",
        "environment_override": None,
    },
}
with open(request_file, "w", encoding="utf-8") as handle:
    json.dump(payload, handle)
PY
    printf '%s' "${request_file}"
}

run_plan() {
    local name="$1"
    local request_file="$2"
    local result_file="${TMP_DIR}/${name}-result.json"
    local ssh_log="${TMP_DIR}/fake-ssh-${name}.log"
    local stdout_log="${TMP_DIR}/${name}.stdout.log"
    local stderr_log="${TMP_DIR}/${name}.stderr.log"
    rm -f "${result_file}" "${ssh_log}"

    echo "--- Run plan for ${name} ---"
    FAKE_REMOTE_ROOT="${PLAN_ROOT}" \
    FAKE_SSH_LOG="${ssh_log}" \
    COPERCEIVE_TEST_ENV=1 \
    COPERCEIVE_SSH_BIN="${FAKE_SSH}" \
    COPERCEIVE_TEST_HPC_PLAN_REQUEST="${request_file}" \
    COPERCEIVE_TEST_HPC_PLAN_RESULT="${result_file}" \
    "${APP_EXE}" >"${stdout_log}" 2>"${stderr_log}" &
    APP_PID=$!

    local waited=0
    while [ ! -s "${result_file}" ]; do
        if ! kill -0 "${APP_PID}" 2>/dev/null; then
            fail "app exited before writing the ${name} plan result"
        fi
        if [ "${waited}" -ge 450 ]; then
            fail "timed out waiting for the ${name} plan result"
        fi
        sleep 0.2
        waited=$((waited + 1))
    done
    kill -9 "${APP_PID}" 2>/dev/null || true
    wait "${APP_PID}" 2>/dev/null || true
    APP_PID=""

    python3 - "${name}" "${result_file}" "${ssh_log}" "${PLAN_ROOT}" <<'PY'
import hashlib
import json
import os
import stat
import sys

name, result_file, ssh_log, plan_root = sys.argv[1:5]
with open(result_file, encoding="utf-8") as handle:
    data = json.load(handle)
if not data.get("ok"):
    print(f"E2E_PLAN_FAIL: {name}: {data.get('error')}", file=sys.stderr)
    sys.exit(1)
plan = data["plan"]
serialised = json.dumps(plan).lower()
for forbidden in ("password", "private_key", "secret", "bearer", "token"):
    if forbidden in serialised:
        print(f"E2E_PLAN_FAIL: {name}: plan contains {forbidden}", file=sys.stderr)
        sys.exit(1)
if not plan["nothing_submitted"]:
    print(f"E2E_PLAN_FAIL: {name}: plan claims submission", file=sys.stderr)
    sys.exit(1)
if not plan["saved_path"] or not plan["saved_path"].startswith("/data/results/plans/"):
    print(f"E2E_PLAN_FAIL: {name}: plan was not saved under results_root", file=sys.stderr)
    sys.exit(1)
local_plan = os.path.join(plan_root, plan["saved_path"].lstrip("/"))
if not os.path.isfile(local_plan):
    print(f"E2E_PLAN_FAIL: {name}: saved plan file missing at {local_plan}", file=sys.stderr)
    sys.exit(1)
mode = stat.S_IMODE(os.stat(local_plan).st_mode)
if mode != 0o600:
    print(f"E2E_PLAN_FAIL: {name}: saved plan mode {oct(mode)} is not 0600", file=sys.stderr)
    sys.exit(1)
with open(local_plan, encoding="utf-8") as handle:
    saved = json.load(handle)
if saved.get("schema") != "coperceive-run-plan-1" or saved.get("nothing_submitted") is not True:
    print(f"E2E_PLAN_FAIL: {name}: saved plan JSON is not a run plan", file=sys.stderr)
    sys.exit(1)

checkpoint_sha = hashlib.sha256(
    open(os.path.join(plan_root, "data/code/checkpoints/model.pt"), "rb").read()
).hexdigest()
split_sha = hashlib.sha256(
    open(os.path.join(plan_root, "data/code/splits/test.txt"), "rb").read()
).hexdigest()
if plan["checkpoint"]["sha256"] != checkpoint_sha:
    print(f"E2E_PLAN_FAIL: {name}: checkpoint sha256 mismatch", file=sys.stderr)
    sys.exit(1)
if plan["split"]["sha256"] != split_sha:
    print(f"E2E_PLAN_FAIL: {name}: split sha256 mismatch", file=sys.stderr)
    sys.exit(1)

argv = plan["argv"]
if "a b; touch /tmp/x" not in argv:
    print(f"E2E_PLAN_FAIL: {name}: hostile value is not one argv element", file=sys.stderr)
    sys.exit(1)
for flag in ("--extra", "--checkpoint", "--split", "--frame_stride", "--half"):
    if argv.count(flag) != 1:
        print(f"E2E_PLAN_FAIL: {name}: typed argv flag {flag} appears {argv.count(flag)} times", file=sys.stderr)
        sys.exit(1)
if argv[len(argv) - 2] != "--output-dir":
    print(f"E2E_PLAN_FAIL: {name}: argv does not end with --output-dir", file=sys.stderr)
    sys.exit(1)

contract = plan["contract_check_script"]
if "#SBATCH --partition=main-cpu" not in contract or "--max-frames 3" not in contract:
    print(f"E2E_PLAN_FAIL: {name}: contract-check script is incomplete", file=sys.stderr)
    sys.exit(1)
expected = "apptainer exec" if name.endswith("container") else "module load python/3.12"
if expected not in contract:
    print(f"E2E_PLAN_FAIL: {name}: contract-check script lacks {expected!r}", file=sys.stderr)
    sys.exit(1)
for forbidden in ("pip install", "conda install", "git clone", "curl ", "wget "):
    if forbidden in contract:
        print(f"E2E_PLAN_FAIL: {name}: contract-check script installs/downloads: {forbidden}", file=sys.stderr)
        sys.exit(1)

if os.path.exists(ssh_log):
    log_text = open(ssh_log, encoding="utf-8").read()
    for forbidden in ("sbatch", "scancel"):
        if forbidden in log_text:
            print(f"E2E_PLAN_FAIL: {name}: the plan called {forbidden}", file=sys.stderr)
            sys.exit(1)

print(
    f"E2E_PLAN_MARK: {name} verified (env={plan['environment']['kind']}, "
    f"argv_elements={len(argv)}, checkpoint_sha256={plan['checkpoint']['sha256'][:12]}..., "
    f"saved={plan['saved_path']}, nothing_submitted={plan['nothing_submitted']})"
)
PY
    echo "Step verified: run plan for ${name} (nothing submitted)."
}

REQUEST_MODULE="$(make_request plan-module \
    "${PLAN_ROOT}/data/code/pipelines/coperceive-pipeline.json" \
    "/data/code/pipelines/contract_pipeline.py")"
run_plan plan-module "${REQUEST_MODULE}"
REQUEST_CONTAINER="$(make_request plan-container \
    "${PLAN_ROOT}/data/code/pipelines/container/coperceive-pipeline.json" \
    "/data/code/pipelines/container/contract_pipeline.py")"
run_plan plan-container "${REQUEST_CONTAINER}"

echo "Plan e2e verified: module and container plans built, checked and saved; nothing submitted."
