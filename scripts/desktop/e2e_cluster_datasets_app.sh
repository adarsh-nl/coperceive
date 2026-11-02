#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
#
# Packaged-app end-to-end check for cluster datasets over a fake SSH cluster.
#
# Launches the real built app with COPERCEIVE_TEST_DATASET_FLOW_REQUEST; its test
# seam calls the same Tauri commands the Dataset view invokes (dataset_detect,
# dataset_index, dataset_prepare, dataset_prepare_submit, dataset_status,
# dataset_open). The fake cluster is this machine: dataset commands run the
# bundled coperceive, a confirmed sbatch runs the rendered prepare script, and
# dataset_open starts a real `coperceive serve` on the cache behind a loopback
# "tunnel". The raw tree is the synthetic OPV2V layout from
# e2e_cluster_datasets.sh (3 test scenes) plus a bigger train split, as on the
# real cluster, so index and prepare must use the split detect returned; no real
# data, no network.
#
# Usage: e2e_cluster_datasets_app.sh [app-executable]
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
HPC_FIXTURES="${ROOT_DIR}/src-tauri/tests/fixtures/hpc"
FIXTURES_DIR="${ROOT_DIR}/src-tauri/tests/fixtures/datasets"
FAKE_SSH="${FIXTURES_DIR}/fake_cluster_ssh.sh"
FAKE_SCP="${FIXTURES_DIR}/fake_scp.sh"
PROFILE_ID="fixture-datasets"

fail() {
    echo "E2E_DATASET_APP_FAIL: $*" >&2
    exit 1
}

APP_EXE="${1:-}"
APP_BUNDLE=""
if [ -z "${APP_EXE}" ]; then
    APP_BUNDLE="$(find "${ROOT_DIR}/src-tauri/target" -iname Coperceive.app -type d 2>/dev/null | head -n 1)"
    [ -n "${APP_BUNDLE}" ] || fail "built .app not found"
    for candidate in "${APP_BUNDLE}/Contents/MacOS/"*; do
        if [ -f "${candidate}" ] && [ -x "${candidate}" ]; then
            APP_EXE="${candidate}"
            break
        fi
    done
fi
[ -n "${APP_EXE}" ] && [ -x "${APP_EXE}" ] || fail "no app executable (pass it as the first argument)"

# The fake cluster runs the same Python the app bundles.
PYTHON="${COPERCEIVE_E2E_PYTHON:-}"
if [ -z "${PYTHON}" ]; then
    for candidate in \
        "${APP_BUNDLE:+${APP_BUNDLE}/Contents/Resources/resources/python/bin/python3}" \
        "${ROOT_DIR}/src-tauri/resources/python/bin/python3" \
        "${ROOT_DIR}/.venv/bin/python"; do
        if [ -n "${candidate}" ] && [ -x "${candidate}" ]; then
            PYTHON="${candidate}"
            break
        fi
    done
fi
[ -n "${PYTHON}" ] && [ -x "${PYTHON}" ] || fail "no Python with coperceive found"
chmod +x "${FAKE_SSH}" "${FAKE_SCP}" "${HPC_FIXTURES}/fake_ssh.sh"

# Resolve symlinks (/tmp -> /private/tmp) so the paths the app sends are the
# real paths the cluster reports back.
TMP_DIR="$(cd "$(mktemp -d /tmp/coperceive-dataset-app-e2e-XXXXXX)" && pwd -P)"
CLUSTER="${TMP_DIR}/cluster"
RAW="${CLUSTER}/datasets/opv2v"
REMOTE_HOME="${CLUSTER}/home"
APP_PID=""

stop_fake_cluster() {
    local pid_file
    for pid_file in "${REMOTE_HOME}"/.fake-slurm/sessions/*.pid; do
        [ -f "${pid_file}" ] || continue
        kill -TERM -- "-$(cat "${pid_file}")" 2>/dev/null || true
        rm -f "${pid_file}"
    done
    pkill -f "${FIXTURES_DIR}/tcp_forward.py" 2>/dev/null || true
}

cleanup() {
    local status=$?
    if [ -n "${APP_PID}" ] && kill -0 "${APP_PID}" 2>/dev/null; then
        kill -9 "${APP_PID}" 2>/dev/null || true
    fi
    stop_fake_cluster
    if [ "${status}" -ne 0 ]; then
        for log in "${TMP_DIR}"/*.log "${REMOTE_HOME}"/.fake-slurm/*.err \
            "${REMOTE_HOME}"/.fake-slurm/sessions/*.log; do
            [ -f "${log}" ] || continue
            echo "=== ${log} ==="
            tail -n 60 "${log}"
        done
    fi
    rm -rf "${TMP_DIR}"
}
trap cleanup EXIT INT TERM HUP

mkdir -p "${CLUSTER}/results" "${CLUSTER}/scratch" "${REMOTE_HOME}" "${TMP_DIR}/state"

# Same synthetic tree as e2e_cluster_datasets.sh: test has 3 scenes x 3 agents x
# 4 frames. train has 5 scenes, listed first, like the real opencood/opv2v root.
"${PYTHON}" - "${RAW}" <<'PY'
import json, sys
from pathlib import Path
root = Path(sys.argv[1])
NUMPY_YAW = ("!!python/object/apply:numpy.core.multiarray.scalar [!!python/object/apply:numpy.dtype "
             "{args: [f8, false, true], state: !!python/tuple [3, '<', null, null, null, -1, -1, 0]}, "
             "!!binary 'AAAAAACAVkA=']")  # float64 90.0
extrinsic = [[1.0,0.0,0.0,0.0],[0.0,1.0,0.0,0.0],[0.0,0.0,1.0,0.0],[0.0,0.0,0.0,1.0]]
intrinsic = [[335.6,0.0,400.0],[0.0,335.6,300.0],[0.0,0.0,1.0]]
pcd_head = ("# .PCD v0.7 - Point Cloud Data file format\nVERSION 0.7\nFIELDS x y z rgb\n"
            "SIZE 4 4 4 4\nTYPE F F F F\nCOUNT 1 1 1 1\nWIDTH 1\nHEIGHT 1\n"
            "VIEWPOINT 0 0 0 1 0 0 0\nPOINTS 1\nDATA ascii\n0.000000 0.000000 0.000000 0\n")
scenes = [("train", f"train_{i:04d}") for i in range(5)] + [("test", f"scene_{i:04d}") for i in range(3)]
for split, scene_id in scenes:
    base = root / split / scene_id; base.mkdir(parents=True)
    (base / "data_protocol.yaml").write_text('{"world": {"fixed_delta_seconds": 0.05, "sync_mode": true}}')
    for agent in ("641", "650", "651"):
        folder = base / agent; folder.mkdir()
        for frame in (0, 2, 4, 6):
            record = {
                "ego_speed": 5.0,
                "lidar_pose": [float(agent), float(frame), 1.9, 0.0, 90.0, 0.0],
                "true_ego_pos": [float(agent), float(frame), 0.0, 0.0, 90.0, 0.0],
                "vehicles": {"650": {"angle": [0.0, 90.0, 0.0], "center": [0.0, 0.0, 0.75],
                                      "extent": [2.4, 1.0, 0.75], "location": [float(agent), float(frame), 0.0],
                                      "speed": 0.0}},
                "camera0": {"cords": [float(agent), float(frame), 1.9, 0.0, 90.0, 0.0],
                            "extrinsic": extrinsic, "intrinsic": intrinsic},
            }
            # Real OPV2V records carry numpy-tagged scalars (PyYAML's full dumper):
            # write the yaw that way so the numpy-safe loader is on this path.
            text = json.dumps(record).replace(", 90.0, 0.0]", ", " + NUMPY_YAW + ", 0.0]", 1)
            assert NUMPY_YAW in text
            (folder / f"{frame:06d}.yaml").write_text(text)
            (folder / f"{frame:06d}.pcd").write_text(pcd_head)
PY

raw_digest() {
    python3 - "${RAW}" <<'PY'
import hashlib, sys
from pathlib import Path
root = Path(sys.argv[1])
for path in sorted(p for p in root.rglob("*") if p.is_file()):
    print(hashlib.sha256(path.read_bytes()).hexdigest(), path.relative_to(root))
PY
}
raw_digest > "${TMP_DIR}/raw-before.txt"

# The fake cluster's server profile: cluster-b's shape (ssh alias answered by
# the shared fake ssh) with this machine's paths and the bundled coperceive.
SERVER="${TMP_DIR}/server.json"
python3 - "${HPC_FIXTURES}/cluster-b.json" "${SERVER}" "${PROFILE_ID}" "${CLUSTER}" "${PYTHON}" <<'PY'
import json, sys
source, target, profile_id, cluster, python = sys.argv[1:6]
entry = json.load(open(source, encoding="utf-8"))
entry["id"] = profile_id
profile = entry["profile"]
profile["name"] = "Dataset e2e fake cluster"
profile["data_roots"] = [f"{cluster}/datasets"]
profile["scratch_template"] = f"{cluster}/scratch"
profile["results_root"] = f"{cluster}/results"
profile["code_roots"] = []
profile["environment"]["interpreter"] = python
profile["environment"]["coperceive_command"] = [python, "-m", "coperceive"]
json.dump(entry, open(target, "w", encoding="utf-8"), indent=2)
PY

# run_flow NAME STEPS_JSON: one app launch running the listed commands. When the
# flow ends with dataset_open, the served scene list is read through the app's
# origin and token while the app (and its tunnel) are still alive.
run_flow() {
    local name="$1"
    local steps="$2"
    local request="${TMP_DIR}/${name}-request.json"
    local result="${TMP_DIR}/${name}-result.json"
    printf '{"server": "%s", "steps": %s}\n' "${SERVER}" "${steps}" > "${request}"
    rm -f "${result}"

    echo "--- Dataset flow ${name} ---"
    COPERCEIVE_TEST_ENV=1 \
    COPERCEIVE_E2E_REQUIRE_PREFIX=1 \
    COPERCEIVE_STATE_DIR="${TMP_DIR}/state" \
    COPERCEIVE_SSH_BIN="${FAKE_SSH}" \
    COPERCEIVE_SCP_BIN="${FAKE_SCP}" \
    FAKE_REMOTE_HOME="${REMOTE_HOME}" \
    FAKE_CLUSTER_PYTHON="${PYTHON}" \
    FAKE_SSH_LOG="${TMP_DIR}/${name}-ssh.txt" \
    COPERCEIVE_TEST_DATASET_FLOW_REQUEST="${request}" \
    COPERCEIVE_TEST_DATASET_FLOW_RESULT="${result}" \
    "${APP_EXE}" >"${TMP_DIR}/${name}.stdout.log" 2>"${TMP_DIR}/${name}.stderr.log" &
    APP_PID=$!

    local waited=0
    while [ ! -s "${result}" ]; do
        kill -0 "${APP_PID}" 2>/dev/null || fail "app exited before writing the ${name} result"
        [ "${waited}" -lt 900 ] || fail "timed out waiting for the ${name} result"
        sleep 0.2
        waited=$((waited + 1))
    done
    python3 - "${result}" "${TMP_DIR}/${name}-served.json" <<'PY'
import json, sys, time, urllib.request
result, served = sys.argv[1:3]
steps = json.load(open(result, encoding="utf-8")).get("steps") or []
if not steps or steps[-1]["command"] != "dataset_open" or not steps[-1]["ok"]:
    sys.exit(0)
session = steps[-1]["value"]
request = urllib.request.Request(
    f"{session['origin']}/api/scenes",
    headers={"Authorization": "Bearer " + session["token"]},
)
deadline = time.monotonic() + 30
while True:
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            scenes = sorted(item["scene_id"] for item in json.load(response))
        break
    except OSError as error:
        if time.monotonic() > deadline:
            print(f"E2E_DATASET_APP_FAIL: served scene list unreachable: {error}", file=sys.stderr)
            sys.exit(1)
        time.sleep(0.3)
json.dump(scenes, open(served, "w", encoding="utf-8"))
PY
    kill -9 "${APP_PID}" 2>/dev/null || true
    wait "${APP_PID}" 2>/dev/null || true
    APP_PID=""
    stop_fake_cluster
}

# check NAME: run the Python assertions on stdin with the flow's files.
check() {
    local name="$1"
    shift
    python3 - "${TMP_DIR}/${name}-result.json" "${TMP_DIR}/${name}-ssh.txt" "${TMP_DIR}" "${RAW}" "$@" \
        || fail "the ${name} flow assertions failed (see the traceback above)"
}

profile_args() {
    printf '"profileId": "%s"' "${PROFILE_ID}"
}

# --- Flow A: detect, index, preview, two refused submits ----------------------
run_flow detect "[
  {\"command\": \"test_server_connection\", \"args\": {}},
  {\"command\": \"dataset_detect\", \"args\": {$(profile_args), \"path\": \"${RAW}/test\"}}
]"
DATASET="$(python3 - "${TMP_DIR}/detect-result.json" <<'PY'
import json, sys
connection, step = json.load(open(sys.argv[1], encoding="utf-8"))["steps"]
assert connection["ok"], connection
rows = {check["name"]: check for check in connection["value"]["checks"]}
assert rows.get("cluster-datasets", {}).get("ok") is True, rows.get("cluster-datasets")
if not step["ok"]:
    print(f"E2E_DATASET_APP_FAIL: CD-1 detect failed: {step['error']}", file=sys.stderr)
    sys.exit(1)
d = step["value"]
assert d["status"] == "ok" and d["adapter"] == "opv2v" and d["scene_count"] == 3, d
assert d["split"] == "test" and d["raw_root"] == str(__import__("pathlib").Path(d["path"]).parent), d
print(json.dumps({"adapter": d["adapter"], "rawRoot": d["raw_root"], "split": d["split"]}))
PY
)"
echo "Step CD-1 verified: Test connection reports cluster datasets supported; dataset_detect on opv2v/test -> ok, opv2v, split test, 3 scenes, raw_root the parent that also holds train (numpy-tagged records)"
DATASET_ARGS="$(profile_args), $(python3 -c 'import json,sys; print(json.dumps(json.loads(sys.argv[1]))[1:-1])' "${DATASET}")"
RAW_ROOT="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["rawRoot"])' "${DATASET}")"

run_flow preview "[
  {\"command\": \"dataset_index\", \"args\": {${DATASET_ARGS}}},
  {\"command\": \"dataset_index\", \"args\": {$(profile_args), \"adapter\": \"opv2v\", \"rawRoot\": \"${RAW_ROOT}\"}},
  {\"command\": \"dataset_prepare\", \"args\": {${DATASET_ARGS}, \"scenes\": [\"scene_0000\"], \"workers\": 1}},
  {\"command\": \"dataset_prepare_submit\", \"args\": {$(profile_args), \"previewHash\": \"0000000000000000\", \"confirmed\": true}},
  {\"command\": \"dataset_prepare\", \"args\": {${DATASET_ARGS}, \"scenes\": [\"scene_0000\"], \"workers\": 1}},
  {\"command\": \"dataset_prepare_submit\", \"args\": {$(profile_args), \"previewHash\": \"@last_preview_hash\", \"confirmed\": false}}
]"
check preview <<'PY'
import json, os, sys
result, ssh_log, tmp, raw = sys.argv[1:5]
steps = json.load(open(result, encoding="utf-8"))["steps"]
index, unsplit = steps[0], steps[1]
steps = steps[:1] + steps[2:]
for step in steps[:2]:
    assert step["ok"], step
index = index["value"]
ids = [scene["scene_id"] for scene in index["scenes"]]
assert index["split"] == "test" and ids == ["scene_0000", "scene_0001", "scene_0002"], (index["split"], ids)
assert index["scene_count"] == 3 and index["estimated_cache_bytes"] > 0, index
assert index["cache"]["relative_path"].endswith("/test"), index["cache"]
assert not unsplit["ok"], unsplit
assert unsplit["error"] == f"{raw} holds several splits (train, test); choose one", unsplit["error"]
print(f"Step CD-2 verified: dataset_index with detect's split -> split test, 3 scenes {ids} (train has 5), "
      f"estimated_cache_bytes={index['estimated_cache_bytes']}; without a split -> {unsplit['error']!r}")
preview = steps[1]["value"]
script = preview["script"]
assert " --split test " in script, script
assert "# No GPU requested" in script, script
assert preview["cpus_per_task"] == 1, preview["cpus_per_task"]
assert "--workers 1" in script, script
assert preview["gres"] is None, preview["gres"]
cache_root = preview.get("cache_root") or ""
assert cache_root.startswith(f"{tmp}/cluster/results/coperceive-cache/opv2v/"), cache_root
assert os.path.commonpath([cache_root, raw]) != raw, (cache_root, raw)
assert preview["preview_hash"], preview
print(f"Step CD-3 verified: dataset_prepare preview is CPU-only, --split test, cpus_per_task=1, --workers 1, "
      f"cache_root outside the raw root, preview_hash={preview['preview_hash']}")
wrong, unconfirmed = steps[2], steps[4]
assert steps[3]["ok"], steps[3]
assert not wrong["ok"] and "preview changed" in wrong["error"], wrong
assert not unconfirmed["ok"] and "Nothing was submitted" in unconfirmed["error"], unconfirmed
commands = open(ssh_log, encoding="utf-8").read().splitlines() if os.path.exists(ssh_log) else []
assert not any(line.startswith("sbatch") for line in commands), commands
print(f"Step CD-4 verified: wrong hash ({wrong['error']!r}) and confirmed:false "
      f"({unconfirmed['error']!r}) submit nothing; no sbatch in the fake-ssh log")
PY

# --- Flow B: confirmed submit of one scene, status, partial open --------------
run_flow one "[
  {\"command\": \"dataset_prepare\", \"args\": {${DATASET_ARGS}, \"scenes\": [\"scene_0000\"], \"workers\": 1}},
  {\"command\": \"dataset_prepare_submit\", \"args\": {$(profile_args), \"previewHash\": \"@last_preview_hash\", \"confirmed\": true}},
  {\"command\": \"dataset_status\", \"args\": {${DATASET_ARGS}}},
  {\"command\": \"dataset_open\", \"args\": {${DATASET_ARGS}, \"sceneId\": \"scene_0000\"}}
]"
check one <<'PY'
import json, sys
result, ssh_log, tmp, raw = sys.argv[1:5]
steps = json.load(open(result, encoding="utf-8"))["steps"]
for step in steps:
    assert step["ok"], step
submitted = steps[1]["value"]
sbatch = [line for line in open(ssh_log, encoding="utf-8").read().splitlines() if line.startswith("sbatch")]
assert len(sbatch) == 1, sbatch
exit_code = open(f"{tmp}/cluster/home/.fake-slurm/{submitted['job_id']}.exit").read().strip()
assert exit_code == "0", f"prepare job exited {exit_code}"
print(f"Step CD-5 verified: confirmed submit -> exactly one sbatch (job {submitted['job_id']}, "
      f"state {submitted['state']}); the fake job ran the prepare")
states = {scene["scene_id"]: scene["state"] for scene in steps[2]["value"]["scenes"]}
assert states == {"scene_0000": "ready", "scene_0001": "not_prepared", "scene_0002": "not_prepared"}, states
print(f"Step CD-6 verified: dataset_status -> {states}")
served = json.load(open(f"{tmp}/one-served.json", encoding="utf-8"))
assert served == ["scene_0000"], served
PY

# --- Flow C: prepare all with default workers, status, open -------------------
run_flow all "[
  {\"command\": \"dataset_prepare\", \"args\": {${DATASET_ARGS}, \"scenes\": \"all\"}},
  {\"command\": \"dataset_prepare_submit\", \"args\": {$(profile_args), \"previewHash\": \"@last_preview_hash\", \"confirmed\": true}},
  {\"command\": \"dataset_status\", \"args\": {${DATASET_ARGS}}},
  {\"command\": \"dataset_open\", \"args\": {${DATASET_ARGS}}}
]"
check all <<'PY'
import json, sys
result, ssh_log, tmp, raw = sys.argv[1:5]
steps = json.load(open(result, encoding="utf-8"))["steps"]
for step in steps:
    assert step["ok"], step
preview = steps[0]["value"]
assert preview["cpus_per_task"] == 3, preview["cpus_per_task"]
assert "--workers 3" in preview["script"] and "--all" in preview["script"], preview["script"]
sbatch = [line for line in open(ssh_log, encoding="utf-8").read().splitlines() if line.startswith("sbatch")]
assert len(sbatch) == 1, sbatch
scenes = {scene["scene_id"]: scene for scene in steps[2]["value"]["scenes"]}
assert {key: value["state"] for key, value in scenes.items()} == {
    "scene_0000": "ready", "scene_0001": "ready", "scene_0002": "ready"}, scenes
before = {s["scene_id"]: s for s in json.load(open(f"{tmp}/one-result.json"))["steps"][2]["value"]["scenes"]}
assert scenes["scene_0000"]["prepared_unix"] == before["scene_0000"]["prepared_unix"], (
    scenes["scene_0000"], before["scene_0000"])
print(f"Step CD-7 verified: prepare all (workers omitted) -> cpus_per_task=3; status all ready; "
      f"scene_0000 prepared_unix unchanged ({before['scene_0000']['prepared_unix']})")
session = steps[3]["value"]
assert session["origin"].startswith("http://127.0.0.1:") and session["token"], {k: v for k, v in session.items() if k != "token"}
assert session["target_type"] == "remote", session["target_type"]
served = json.load(open(f"{tmp}/all-served.json", encoding="utf-8"))
ready = sorted(key for key, value in scenes.items() if value["state"] == "ready")
assert served == ready, (served, ready)
partial = json.load(open(f"{tmp}/one-served.json", encoding="utf-8"))
print(f"Step CD-8 verified: dataset_open -> origin {session['origin']} with a token; served scenes "
      f"{served} == ready scenes (after one-scene prepare: {partial})")
PY

raw_digest > "${TMP_DIR}/raw-after.txt"
cmp -s "${TMP_DIR}/raw-before.txt" "${TMP_DIR}/raw-after.txt" || fail "CD-9 raw tree changed"
python3 - "${TMP_DIR}" <<'PY'
import glob, sys
tmp = sys.argv[1]
files = sorted(glob.glob(f"{tmp}/*-request.json") + glob.glob(f"{tmp}/*-result.json"))
assert len(files) == 8, files
for path in files:
    text = open(path, encoding="utf-8").read().lower()
    for forbidden in ("password", "private_key", "secret", "bearer"):
        assert forbidden not in text, f"{path} contains {forbidden}"
print(f"Step CD-9 verified: raw tree byte-identical ({sum(1 for _ in open(tmp + '/raw-before.txt'))} files, "
      "sorted sha256); no password/private_key/secret/bearer in the request/result JSON")
PY
# --- Flow E: an older coperceive on the cluster --------------------------------
# The profile's interpreter is fake_old_python.sh: no `capabilities`, no
# `dataset detect`. Test connection and dataset_detect must both say "too old"
# with the exact update command, never raw argparse output.
OLD_CHECKOUT="${TMP_DIR}/old-checkout"
mkdir -p "${OLD_CHECKOUT}/coperceive" "${OLD_CHECKOUT}/.git"
printf '' > "${OLD_CHECKOUT}/coperceive/__init__.py"
export FAKE_OLD_CHECKOUT="${OLD_CHECKOUT}"
OLD_PYTHON="${ROOT_DIR}/src-tauri/tests/fixtures/datasets/fake_old_python.sh"
NEW_SERVER="${SERVER}"
SERVER="${TMP_DIR}/server-old.json"
python3 - "${NEW_SERVER}" "${SERVER}" "${OLD_PYTHON}" <<'PY'
import json, sys
source, target, python = sys.argv[1:4]
entry = json.load(open(source, encoding="utf-8"))
entry["profile"]["environment"]["interpreter"] = python
entry["profile"]["environment"]["coperceive_command"] = [python, "-m", "coperceive"]
json.dump(entry, open(target, "w", encoding="utf-8"), indent=2)
PY
run_flow old "[
  {\"command\": \"test_server_connection\", \"args\": {}},
  {\"command\": \"dataset_detect\", \"args\": {$(profile_args), \"path\": \"${RAW}/test\"}}
]"
SERVER="${NEW_SERVER}"
check old "${OLD_CHECKOUT}" "${OLD_PYTHON}" <<'PY'
import json, sys
result, ssh_log, tmp, raw, checkout, python = sys.argv[1:7]
connection, detect = json.load(open(result, encoding="utf-8"))["steps"]
update = f"git -C {checkout} pull --ff-only && {python} -m pip install -e {checkout} --no-deps"
expected = ("coperceive on this server is too old for cluster datasets (found coperceive 0.0.0, which "
            "has no capabilities command, need a build with cluster-datasets-3). Update it with: " + update)
assert not detect["ok"], detect
assert detect["error"] == expected, detect["error"]
assert "invalid choice" not in detect["error"] and "usage:" not in detect["error"], detect["error"]
rows = {check["name"]: check for check in connection["value"]["checks"]}
row = rows.get("cluster-datasets")
assert row is not None and row["ok"] is False and row["detail"] == expected, row
log = open(ssh_log, encoding="utf-8").read().splitlines()
assert "dataset-detect" in log and log.count("capabilities") == 2 and "sbatch" not in log, log
for path in (result, f"{tmp}/old-request.json"):
    text = open(path, encoding="utf-8").read().lower()
    for forbidden in ("password", "private_key", "secret", "bearer"):
        assert forbidden not in text, f"{path} contains {forbidden}"
print(f"Step CD-10 verified: old coperceive (no capabilities, 'invalid choice: detect') -> Test connection "
      f"row and dataset_detect both say: {expected}")
PY

echo "E2E_DATASET_APP_MARK: cluster datasets verified through the packaged app over fake SSH"
