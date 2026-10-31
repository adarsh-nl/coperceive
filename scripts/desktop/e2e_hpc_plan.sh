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
cp -R "${ROOT_DIR}/examples/pipelines/smoke" "${PLAN_ROOT}/data/code/pipelines/smoke"
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
    local server_file="${4:-${FIXTURES_DIR}/cluster-b.json}"
    python3 - "${request_file}" "${server_file}" \
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
    local expected_partition="${3:-main-cpu}"
    local expected_gres="${4:-}"
    local result_file="${TMP_DIR}/${name}-result.json"
    local ssh_log="${TMP_DIR}/fake-ssh-${name}.log"
    local stdout_log="${TMP_DIR}/${name}.stdout.log"
    local stderr_log="${TMP_DIR}/${name}.stderr.log"
    rm -f "${result_file}" "${ssh_log}"

    echo "--- Run plan for ${name} ---"
    FAKE_REMOTE_ROOT="${PLAN_ROOT}" \
    FAKE_SSH_LOG="${ssh_log}" \
    COPERCEIVE_TEST_ENV=1 \
    COPERCEIVE_E2E_REQUIRE_PREFIX=1 \
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

    python3 - "${name}" "${result_file}" "${ssh_log}" "${PLAN_ROOT}" "${expected_partition}" "${expected_gres}" <<'PY'
import hashlib
import json
import os
import stat
import sys

name, result_file, ssh_log, plan_root, expected_partition, expected_gres = sys.argv[1:7]
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
coperceive_check = next(
    (check for check in plan["checks"] if check["name"] == "coperceive"), None
)
if not coperceive_check or not coperceive_check.get("ok"):
    print(
        f"E2E_PLAN_FAIL: {name}: saved-command coperceive check failed: {coperceive_check}",
        file=sys.stderr,
    )
    sys.exit(1)
if plan["checkpoint"]["sha256"] != checkpoint_sha:
    print(f"E2E_PLAN_FAIL: {name}: checkpoint sha256 mismatch", file=sys.stderr)
    sys.exit(1)
if plan["split"]["sha256"] != split_sha:
    print(f"E2E_PLAN_FAIL: {name}: split sha256 mismatch", file=sys.stderr)
    sys.exit(1)

argv = plan["argv"]
if name == "plan-smoke":
    for flag in ("--split", "--sleep-seconds"):
        if argv.count(flag) != 1:
            print(f"E2E_PLAN_FAIL: {name}: smoke flag {flag} appears {argv.count(flag)} times", file=sys.stderr)
            sys.exit(1)
else:
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
if f"#SBATCH --partition={expected_partition}" not in contract or "--max-frames 3" not in contract:
    print(f"E2E_PLAN_FAIL: {name}: contract-check script is incomplete", file=sys.stderr)
    sys.exit(1)
if expected_gres:
    if plan.get("gres") != expected_gres:
        print(f"E2E_PLAN_FAIL: {name}: plan gres {plan.get('gres')!r}", file=sys.stderr)
        sys.exit(1)
else:
    if plan.get("gres") is not None:
        print(f"E2E_PLAN_FAIL: {name}: CPU-only plan has gres {plan.get('gres')!r}", file=sys.stderr)
        sys.exit(1)
# Contract check is light: never a GPU line, even for a GPU eval plan.
if "--gres" in contract:
    print(f"E2E_PLAN_FAIL: {name}: contract check requested a GPU", file=sys.stderr)
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

make_submit_request() {
    local name="$1"
    local plan_id="$2"
    local confirmed="$3"
    local options_json="$4"
    local server_file="${5:-${FIXTURES_DIR}/cluster-b.json}"
    local request_file="${TMP_DIR}/${name}-submit-request.json"
    python3 - "${request_file}" "${server_file}" "${plan_id}" "${confirmed}" "${options_json}" <<'PY'
import json
import sys

request_file, server_path, plan_id, confirmed, options_json = sys.argv[1:6]
payload = {
    "server": server_path,
    "plan_id": plan_id,
    "options": json.loads(options_json),
    "confirmed": confirmed == "true",
}
with open(request_file, "w", encoding="utf-8") as handle:
    json.dump(payload, handle)
PY
    printf '%s' "${request_file}"
}

run_submit() {
    local name="$1"
    local request_file="$2"
    local confirmed="$3"
    local expected_partition="${4:-main-cpu}"
    local expected_gres="${5:-}"
    local result_file="${TMP_DIR}/${name}-submit-result.json"
    local ssh_log="${TMP_DIR}/fake-ssh-${name}.log"
    local stdout_log="${TMP_DIR}/${name}.stdout.log"
    local stderr_log="${TMP_DIR}/${name}.stderr.log"
    rm -f "${result_file}" "${ssh_log}"

    echo "--- Submission for ${name} (confirmed=${confirmed}) ---"
    FAKE_REMOTE_ROOT="${PLAN_ROOT}" \
    FAKE_SSH_LOG="${ssh_log}" \
    COPERCEIVE_TEST_ENV=1 \
    COPERCEIVE_E2E_REQUIRE_PREFIX=1 \
    COPERCEIVE_SSH_BIN="${FAKE_SSH}" \
    COPERCEIVE_TEST_HPC_SUBMIT_REQUEST="${request_file}" \
    COPERCEIVE_TEST_HPC_SUBMIT_RESULT="${result_file}" \
    "${APP_EXE}" >"${stdout_log}" 2>"${stderr_log}" &
    APP_PID=$!

    local waited=0
    while [ ! -s "${result_file}" ]; do
        if ! kill -0 "${APP_PID}" 2>/dev/null; then
            fail "app exited before writing the ${name} submit result"
        fi
        if [ "${waited}" -ge 450 ]; then
            fail "timed out waiting for the ${name} submit result"
        fi
        sleep 0.2
        waited=$((waited + 1))
    done
    kill -9 "${APP_PID}" 2>/dev/null || true
    wait "${APP_PID}" 2>/dev/null || true
    APP_PID=""

    python3 - "${name}" "${result_file}" "${ssh_log}" "${confirmed}" "${expected_partition}" "${expected_gres}" <<'PY'
import json
import sys

name, result_file, ssh_log, confirmed, expected_partition, expected_gres = sys.argv[1:7]
with open(result_file, encoding="utf-8") as handle:
    data = json.load(handle)
script = data.get("script", "")
for needle in (f"#SBATCH --partition={expected_partition}", "--output-dir"):
    if needle not in script:
        print(f"E2E_SUBMIT_FAIL: {name}: script lacks {needle!r}", file=sys.stderr)
        sys.exit(1)
log = ""
try:
    log = open(ssh_log, encoding="utf-8").read()
except OSError:
    pass
if confirmed == "true":
    if not data.get("ok"):
        print(f"E2E_SUBMIT_FAIL: {name}: {data.get('error')}", file=sys.stderr)
        sys.exit(1)
    outcome = data.get("outcome") or {}
    if outcome.get("job_id") != "424242":
        print(f"E2E_SUBMIT_FAIL: {name}: job id {outcome.get('job_id')!r}", file=sys.stderr)
        sys.exit(1)
    if outcome.get("state") != "pending" or outcome.get("queue_reason") != "Resources":
        print(f"E2E_SUBMIT_FAIL: {name}: state/reason {outcome.get('state')!r}/{outcome.get('queue_reason')!r}", file=sys.stderr)
        sys.exit(1)
    if expected_gres:
        if f"#SBATCH --gres={expected_gres}" not in script:
            print(f"E2E_SUBMIT_FAIL: {name}: script lacks gres {expected_gres!r}", file=sys.stderr)
            sys.exit(1)
    elif "--gres=" in script:
        print(f"E2E_SUBMIT_FAIL: {name}: CPU-only preview contains --gres", file=sys.stderr)
        sys.exit(1)
    if log.count("sbatch") != 1:
        print(f"E2E_SUBMIT_FAIL: {name}: expected exactly one sbatch, log={log!r}", file=sys.stderr)
        sys.exit(1)
    if "scancel" in log:
        print(f"E2E_SUBMIT_FAIL: {name}: submitted job was cancelled", file=sys.stderr)
        sys.exit(1)
    print(
        f"E2E_SUBMIT_MARK: {name} verified (confirmed=True, job_id={outcome['job_id']}, "
        f"state={outcome['state']}, queue_reason={outcome['queue_reason']}, "
        f"script_partition={expected_partition}, gres={expected_gres or 'none'})"
    )
else:
    if data.get("ok"):
        print(f"E2E_SUBMIT_FAIL: {name}: unconfirmed submit was accepted", file=sys.stderr)
        sys.exit(1)
    if "confirmation" not in (data.get("error") or "").lower():
        print(f"E2E_SUBMIT_FAIL: {name}: {data.get('error')}", file=sys.stderr)
        sys.exit(1)
    if "sbatch" in log:
        print(f"E2E_SUBMIT_FAIL: {name}: unconfirmed submit called sbatch", file=sys.stderr)
        sys.exit(1)
    if expected_gres:
        if f"#SBATCH --gres={expected_gres}" not in script:
            print(f"E2E_SUBMIT_FAIL: {name}: preview lacks gres {expected_gres!r}", file=sys.stderr)
            sys.exit(1)
    elif "--gres=" in script:
        print(f"E2E_SUBMIT_FAIL: {name}: CPU-only preview contains --gres", file=sys.stderr)
        sys.exit(1)
    print(f"E2E_SUBMIT_MARK: {name} refused without confirmation (no sbatch)")
PY
    echo "Step verified: submission for ${name}."
}

make_watch_request() {
    local name="$1"
    local action="$2"
    local stream="$3"
    local confirmed="$4"
    local extra_json="${5:-}"
    if [ -z "${extra_json}" ]; then
        extra_json='{}'
    fi
    local request_file="${TMP_DIR}/${name}-watch-request.json"
    python3 - "${request_file}" "${FIXTURES_DIR}/cluster-b.json" "${PLAN_ID}" "${action}" "${stream}" "${confirmed}" "${extra_json}" <<'PY'
import json
import sys

request_file, server_path, plan_id, action, stream, confirmed, extra_json = sys.argv[1:8]
payload = {
    "server": server_path,
    "plan_id": plan_id,
    "job_id": "424242",
    "action": action,
    "stream": stream,
    "lines": 50,
    "confirmed": confirmed == "true",
}
payload.update(json.loads(extra_json))
with open(request_file, "w", encoding="utf-8") as handle:
    json.dump(payload, handle)
PY
    printf '%s' "${request_file}"
}

run_watch() {
    local name="$1"
    local request_file="$2"
    local action="$3"
    local job_state="$4"
    local result_file="${TMP_DIR}/${name}-watch-result.json"
    local ssh_log="${TMP_DIR}/fake-ssh-${name}.log"
    local stdout_log="${TMP_DIR}/${name}.stdout.log"
    local stderr_log="${TMP_DIR}/${name}.stderr.log"
    rm -f "${result_file}" "${ssh_log}"

    echo "--- Watch ${name} (action=${action}, job_state=${job_state}) ---"
    FAKE_REMOTE_ROOT="${PLAN_ROOT}" \
    FAKE_SSH_LOG="${ssh_log}" \
    FAKE_JOB_NAME="coperceive-run-${PLAN_ID}" \
    FAKE_JOB_STATE="${job_state}" \
    FAKE_PLAN_ID="${PLAN_ID}" \
    FAKE_PREDICTIONS_PATH="${PREDICTIONS_FILE}" \
    FAKE_METRICS_PATH="${METRICS_FILE}" \
    COPERCEIVE_TEST_ENV=1 \
    COPERCEIVE_E2E_REQUIRE_PREFIX=1 \
    COPERCEIVE_SSH_BIN="${FAKE_SSH}" \
    COPERCEIVE_TEST_HPC_WATCH_REQUEST="${request_file}" \
    COPERCEIVE_TEST_HPC_WATCH_RESULT="${result_file}" \
    "${APP_EXE}" >"${stdout_log}" 2>"${stderr_log}" &
    APP_PID=$!

    local waited=0
    while [ ! -s "${result_file}" ]; do
        if ! kill -0 "${APP_PID}" 2>/dev/null; then
            fail "app exited before writing the ${name} watch result"
        fi
        if [ "${waited}" -ge 450 ]; then
            fail "timed out waiting for the ${name} watch result"
        fi
        sleep 0.2
        waited=$((waited + 1))
    done
    kill -9 "${APP_PID}" 2>/dev/null || true
    wait "${APP_PID}" 2>/dev/null || true
    APP_PID=""

    python3 - "${name}" "${action}" "${job_state}" "${PLAN_ID}" "${result_file}" "${ssh_log}" <<'PY'
import json
import sys

name, action, job_state, plan_id, result_file, ssh_log = sys.argv[1:7]
with open(result_file, encoding="utf-8") as handle:
    data = json.load(handle)
if not data.get("ok"):
    print(f"E2E_WATCH_FAIL: {name}: {data.get('error')}", file=sys.stderr)
    sys.exit(1)
log = ""
try:
    log = open(ssh_log, encoding="utf-8").read()
except OSError:
    pass
if action == "status":
    status = data.get("status") or {}
    expected_state = job_state.lower()
    if status.get("state") != expected_state:
        print(f"E2E_WATCH_FAIL: {name}: state {status.get('state')!r}", file=sys.stderr)
        sys.exit(1)
    if job_state == "PENDING" and status.get("queue_reason") != "Resources":
        print(f"E2E_WATCH_FAIL: {name}: reason {status.get('queue_reason')!r}", file=sys.stderr)
        sys.exit(1)
    if not status.get("time_left"):
        print(f"E2E_WATCH_FAIL: {name}: no wall time left", file=sys.stderr)
        sys.exit(1)
    print(f"E2E_WATCH_MARK: {name} verified (state={status['state']}, queue_reason={status.get('queue_reason')}, time_left={status.get('time_left')})")
elif action == "logs":
    tail = data.get("logs") or {}
    if "wrote predictions" not in (tail.get("text") or ""):
        print(f"E2E_WATCH_FAIL: {name}: log text {tail.get('text')!r}", file=sys.stderr)
        sys.exit(1)
    if "tail" not in log:
        print(f"E2E_WATCH_FAIL: {name}: no tail command was issued", file=sys.stderr)
        sys.exit(1)
    print(f"E2E_WATCH_MARK: {name} verified (stream={tail.get('stream')}, lines={tail.get('lines')}, tail_calls={log.count('tail')})")
elif action == "cancel":
    cancel = data.get("cancel") or {}
    if cancel.get("state") != "cancelled":
        print(f"E2E_WATCH_FAIL: {name}: cancel state {cancel.get('state')!r}", file=sys.stderr)
        sys.exit(1)
    if log.count("scancel") != 1:
        print(f"E2E_WATCH_FAIL: {name}: expected exactly one scancel, log={log!r}", file=sys.stderr)
        sys.exit(1)
    print(f"E2E_WATCH_MARK: {name} verified (cancelled, scancel_calls=1)")
elif action == "tail":
    tail = data.get("tail") or {}
    progress = tail.get("progress") or {}
    if tail.get("boxes") != 3:
        print(f"E2E_REPORT_FAIL: {name}: boxes {tail.get('boxes')!r}", file=sys.stderr)
        sys.exit(1)
    if progress.get("frames_done") != 3:
        print(f"E2E_REPORT_FAIL: {name}: progress {progress!r}", file=sys.stderr)
        sys.exit(1)
    if "predictions-tail" not in log:
        print(f"E2E_REPORT_FAIL: {name}: no bounded tail command", file=sys.stderr)
        sys.exit(1)
    print(f"E2E_REPORT_MARK: {name} verified (boxes={tail['boxes']}, frames_done=3, offset={tail.get('offset')})")
elif action == "metrics":
    report = data.get("metrics") or {}
    metrics = report.get("metrics") or {}
    if metrics.get("latency_ms_per_frame") != 41.2:
        print(f"E2E_REPORT_FAIL: {name}: metrics {metrics!r}", file=sys.stderr)
        sys.exit(1)
    print(f"E2E_REPORT_MARK: {name} verified (latency_ms_per_frame=41.2, notes={report.get('notes')!r})")
elif action == "history":
    history = data.get("history") or []
    if len(history) != 2:
        print(f"E2E_REPORT_FAIL: {name}: history length {len(history)}", file=sys.stderr)
        sys.exit(1)
    running = history[0]
    completed = history[1]
    if not running.get("active") or running.get("state") != "running":
        print(f"E2E_REPORT_FAIL: {name}: running entry {running!r}", file=sys.stderr)
        sys.exit(1)
    if completed.get("state") != "completed" or not completed.get("plan_available"):
        print(f"E2E_REPORT_FAIL: {name}: completed entry {completed!r}", file=sys.stderr)
        sys.exit(1)
    if "history" not in log:
        print(f"E2E_REPORT_FAIL: {name}: no sacct history command", file=sys.stderr)
        sys.exit(1)
    print(f"E2E_REPORT_MARK: {name} verified (entries=2, running={running['job_id']}, completed={completed['job_id']}, plan_available=True)")
elif action == "import":
    imported = data.get("imported") or {}
    if imported.get("run_id") != plan_id:
        print(f"E2E_REPORT_FAIL: {name}: run id {imported.get('run_id')!r}", file=sys.stderr)
        sys.exit(1)
    if "Town03-000" not in (imported.get("scenes") or []):
        print(f"E2E_REPORT_FAIL: {name}: scenes {imported.get('scenes')!r}", file=sys.stderr)
        sys.exit(1)
    if "predictions-import" not in log:
        print(f"E2E_REPORT_FAIL: {name}: no predictions import command", file=sys.stderr)
        sys.exit(1)
    print(f"E2E_REPORT_MARK: {name} verified (run_id={imported['run_id']}, predictions_root={imported.get('predictions_root')})")
PY
    echo "Step verified: watch ${name}."
}

REQUEST_MODULE="$(make_request plan-module \
    "${PLAN_ROOT}/data/code/pipelines/coperceive-pipeline.json" \
    "/data/code/pipelines/contract_pipeline.py")"
run_plan plan-module "${REQUEST_MODULE}"
REQUEST_CONTAINER="$(make_request plan-container \
    "${PLAN_ROOT}/data/code/pipelines/container/coperceive-pipeline.json" \
    "/data/code/pipelines/container/contract_pipeline.py")"
run_plan plan-container "${REQUEST_CONTAINER}"

# GPU decision (Adarsh 2026-10-31): a model-evaluation plan on a GPU partition
# starts with one GPU of the partition's detected type; the user can clear it.
GPU_SERVER="${TMP_DIR}/cluster-gpu.json"
python3 - "${FIXTURES_DIR}/cluster-b.json" "${GPU_SERVER}" <<'GPUPY'
import json
import sys

src, dest = sys.argv[1:3]
entry = json.load(open(src, encoding="utf-8"))
entry["profile"]["defaults"]["partition"] = "gpu-h100"
entry["discovered"] = {
    "partitions": [
        {"name": "main-cpu", "nodes": 64, "state": "up", "gpu_types": []},
        {"name": "gpu-h100", "nodes": 32, "state": "up", "gres": "gpu:h100:2", "gpu_types": ["h100"]},
    ],
    "gpu_types": ["h100"],
    "environment": {},
    "checked_unix": 1,
}
with open(dest, "w", encoding="utf-8") as handle:
    json.dump(entry, handle)
GPUPY
REQUEST_GPU="$(make_request plan-gpu \
    "${PLAN_ROOT}/data/code/pipelines/coperceive-pipeline.json" \
    "/data/code/pipelines/contract_pipeline.py" \
    "${GPU_SERVER}")"
run_plan plan-gpu "${REQUEST_GPU}" gpu-h100 gpu:h100:1
GPU_PLAN_ID="$(python3 -c "import json; print(json.load(open('${TMP_DIR}/plan-gpu-result.json'))['plan']['id'])")"
SUBMIT_GPU="$(make_submit_request plan-gpu-preview "${GPU_PLAN_ID}" false '{}' "${GPU_SERVER}")"
run_submit plan-gpu-preview "${SUBMIT_GPU}" false gpu-h100 gpu:h100:1
SUBMIT_NO_GPU="$(make_submit_request plan-gpu-no-gpu "${GPU_PLAN_ID}" false '{"gres":""}' "${GPU_SERVER}")"
run_submit plan-gpu-no-gpu "${SUBMIT_NO_GPU}" false gpu-h100 ""

# Smoke pipeline: dependency-free example that can go through the full flow on a
# cluster with no installed model plugin.
REQUEST_SMOKE="$(make_request plan-smoke \
    "${PLAN_ROOT}/data/code/pipelines/smoke/coperceive-pipeline.json" \
    "/data/code/pipelines/smoke/smoke_pipeline.py")"
# The smoke pipeline declares the split path and the int only; the generic
# request helper adds a checkpoint value for the model pipelines.
python3 -c "import json,sys; p=sys.argv[1]; d=json.load(open(p)); d['request']['values']={k:v for k,v in d['request']['values'].items() if k in {'split','sleep_seconds'}}; json.dump(d, open(p, 'w'))" "${REQUEST_SMOKE}"
run_plan plan-smoke "${REQUEST_SMOKE}"
if ! grep -q "smoke_pipeline.py" "${TMP_DIR}/plan-smoke-result.json"; then
    fail "smoke plan does not contain the smoke pipeline argv"
fi
SMOKE_OUT="${TMP_DIR}/smoke-out"
python3 "${PLAN_ROOT}/data/code/pipelines/smoke/smoke_pipeline.py" \
    --split "${PLAN_ROOT}/data/code/splits/test.txt" \
    --sleep-seconds 0 --max-frames 3 --output-dir "${SMOKE_OUT}"
test -s "${SMOKE_OUT}/predictions.jsonl" ||
    fail "smoke pipeline wrote no predictions.jsonl"
test -s "${SMOKE_OUT}/metrics.json" ||
    fail "smoke pipeline wrote no metrics.json"
grep -q '"schema"[[:space:]]*:[[:space:]]*"coperceive-pipeline-metrics"' "${SMOKE_OUT}/metrics.json" ||
    fail "smoke metrics.json has no metrics schema"
echo "Step verified: smoke pipeline loaded, planned and produced both contract files."

PLAN_ID="$(python3 -c "import json; print(json.load(open('${TMP_DIR}/plan-smoke-result.json'))['plan']['id'])")"
UNCONFIRMED="$(make_submit_request plan-submit-unconfirmed "${PLAN_ID}" false '{}')"
run_submit plan-submit-unconfirmed "${UNCONFIRMED}" false
CONFIRMED="$(make_submit_request plan-submit \
    "${PLAN_ID}" true \
    '{"walltime":"12:00:00","cpus_per_task":16,"account":null,"qos":null}')"
run_submit plan-submit "${CONFIRMED}" true

PREDICTIONS_FILE="${TMP_DIR}/predictions.jsonl"
METRICS_FILE="${TMP_DIR}/metrics.json"
WATCH_JOB_NAME="coperceive-run-${PLAN_ID}"
mkdir -p "${PLAN_ROOT}/data/results"
printf 'starting run\nwrote predictions for Town03\n' > "${PLAN_ROOT}/data/results/${WATCH_JOB_NAME}-424242.out"
printf 'warning: one frame was skipped\n' > "${PLAN_ROOT}/data/results/${WATCH_JOB_NAME}-424242.err"
WATCH_PENDING="$(make_watch_request watch-pending status "" false)"
run_watch watch-pending "${WATCH_PENDING}" status PENDING
WATCH_LOGS="$(make_watch_request watch-logs logs stdout false)"
run_watch watch-logs "${WATCH_LOGS}" logs RUNNING
WATCH_CANCEL="$(make_watch_request watch-cancel cancel "" true)"
run_watch watch-cancel "${WATCH_CANCEL}" cancel RUNNING

PREDICTIONS_FILE="${TMP_DIR}/predictions.jsonl"
cat > "${PREDICTIONS_FILE}" <<'JSON'
{"agent_id":"vehicle","source":"bev-fusion","time_us":0,"category":"car","score":0.9,"track_id":null,"cx":1,"cy":0,"cz":0,"length":4,"width":2,"height":1.6,"qw":1,"qx":0,"qy":0,"qz":0,"frame":"world"}
{"agent_id":"vehicle","source":"bev-fusion","time_us":100000,"category":"car","score":0.8,"track_id":null,"cx":2,"cy":0,"cz":0,"length":4,"width":2,"height":1.6,"qw":1,"qx":0,"qy":0,"qz":0,"frame":"world"}
{"agent_id":"vehicle","source":"bev-fusion","time_us":200000,"category":"car","score":0.7,"track_id":null,"cx":3,"cy":0,"cz":0,"length":4,"width":2,"height":1.6,"qw":1,"qx":0,"qy":0,"qz":0,"frame":"world"}
{"type":"progress","scenes_done":1,"scenes_total":1,"frames_done":3,"frames_total":10}
JSON
METRICS_FILE="${TMP_DIR}/metrics.json"
cat > "${METRICS_FILE}" <<'JSON'
{"schema":"coperceive-pipeline-metrics","version":1,"run":{"model":"bev-fusion","checkpoint":"model.pt","scenes":["Town03-000"]},"outputs":[{"agent_id":"vehicle","source":"bev-fusion","boxes":3,"scenes":1}],"metrics":{"latency_ms_per_frame":41.2},"notes":"fake pipeline report"}
JSON
WATCH_TAIL="$(make_watch_request watch-output-tail tail stdout false '{}')"
run_watch watch-output-tail "${WATCH_TAIL}" tail RUNNING
WATCH_METRICS="$(make_watch_request watch-metrics metrics "" false '{}')"
run_watch watch-metrics "${WATCH_METRICS}" metrics COMPLETED
WATCH_IMPORT="$(make_watch_request watch-import import "" false '{}')"
run_watch watch-import "${WATCH_IMPORT}" import COMPLETED
WATCH_HISTORY="$(make_watch_request watch-history history "" false '{}')"
run_watch watch-history "${WATCH_HISTORY}" history COMPLETED

# Re-run from history reuses the same saved plan and the reviewed-script confirm
# path as a first submission; this proves that path a second time.
RERUN_REQUEST="$(make_submit_request plan-rerun "${PLAN_ID}" true '{}')"
run_submit plan-rerun "${RERUN_REQUEST}" true

echo "Plan e2e verified: module and container plans built, checked and saved; nothing submitted until an explicit confirmation; the submitted job was watched, its log and predictions were read, its metrics report was read, its predictions were imported, the history listed it and a re-run of the saved plan was submitted."
