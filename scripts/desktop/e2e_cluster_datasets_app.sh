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
# dataset_open starts the app's local `coperceive serve` reading the cache over
# the fake ssh (`coperceive dataset stream`), with no Slurm job. The raw tree is the synthetic OPV2V layout from
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
    # The app's viewer server (killed with the app) and its streamer over ssh.
    pkill -f "${TMP_DIR}/remote-view" 2>/dev/null || true
    pkill -f "dataset stream --root ${CLUSTER}" 2>/dev/null || true
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
from PIL import Image
camera_png = Image.new("RGB", (800, 600), (60, 120, 180))
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
            # One camera image per frame, as OPV2V's {frame:06d}_camera0.png.
            camera_png.save(folder / f"{frame:06d}_camera0.png")
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
    COPERCEIVE_REMOTE_VIEW_CACHE="${TMP_DIR}/remote-view" \
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
import json, sys, time, urllib.error, urllib.parse, urllib.request
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
# Camera images the flow asked for: status, type, size and time over the tunnel.
wanted = served.replace("-served.json", "-images.json")
try:
    queries = json.load(open(wanted, encoding="utf-8"))
except OSError:
    queries = []
fetched = []
for query in queries:
    url = f"{session['origin']}/api/scenes/{query.pop('scene')}/image?" + urllib.parse.urlencode(query)
    started = time.monotonic()
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"Authorization": "Bearer " + session["token"]}), timeout=20) as response:
            body = response.read()
            fetched.append({"url": url.split("/api/", 1)[1], "status": response.status,
                            "type": response.headers.get("content-type"), "bytes": len(body),
                            "ms": round((time.monotonic() - started) * 1000, 1)})
    except urllib.error.HTTPError as error:
        fetched.append({"url": url.split("/api/", 1)[1], "status": error.code, "type": None, "bytes": 0, "ms": 0})
json.dump(fetched, open(served.replace("-served.json", "-served-images.json"), "w", encoding="utf-8"))
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
assert "--workers 1" in script and "#SBATCH --array=0-0%1" in script, script
assert "SCENES=(scene_0000)" in script and '--scenes "$SCENE"' in script, script
assert "export COPERCEIVE_IO_THREADS=16" in script, script
assert preview["estimated_seconds"] > 0 and "raw files" in preview["estimate_note"], preview["estimate_note"]
assert preview["gres"] is None, preview["gres"]
cache_root = preview.get("cache_root") or ""
assert cache_root.startswith(f"{tmp}/cluster/results/coperceive-cache/opv2v/"), cache_root
assert os.path.commonpath([cache_root, raw]) != raw, (cache_root, raw)
assert preview["preview_hash"], preview
# No camera-image setting yet: the preview reads originals and says what previews would cost.
assert preview["images"] == "originals" and " --images originals " in script, (preview["images"], script)
assert preview["estimated_preview_bytes"] == 3 * 4 * 40_000, preview["estimated_preview_bytes"]
print(f"Step CD-3 verified: dataset_prepare preview is CPU-only, --split test, a 1-task array of 1 CPU, --workers 1, "
      f"--images originals (previews would add {preview['estimated_preview_bytes']} bytes), "
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
camera_query() {  # camera_query SCENE [extra json fields]: frame 0 of agent 641's camera0
    printf '{"scene": "%s", "time_us": 0, "agent_id": "641", "sensor_id": "camera0"%s}' "$1" "${2:-}"
}
printf '[%s, %s]' "$(camera_query scene_0000)" "$(camera_query scene_0000 ', "width": 160')" > "${TMP_DIR}/all-images.json"
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
assert (preview["cpus_per_task"], preview["array_tasks"], preview["throttle"]) == (1, 3, 3), preview
assert "#SBATCH --array=0-2%3" in preview["script"], preview["script"]
assert "SCENES=(scene_0000 scene_0001 scene_0002)" in preview["script"], preview["script"]
sbatch = [line for line in open(ssh_log, encoding="utf-8").read().splitlines() if line.startswith("sbatch")]
assert len(sbatch) == 1, sbatch
scenes = {scene["scene_id"]: scene for scene in steps[2]["value"]["scenes"]}
assert {key: value["state"] for key, value in scenes.items()} == {
    "scene_0000": "ready", "scene_0001": "ready", "scene_0002": "ready"}, scenes
before = {s["scene_id"]: s for s in json.load(open(f"{tmp}/one-result.json"))["steps"][2]["value"]["scenes"]}
assert scenes["scene_0000"]["prepared_unix"] == before["scene_0000"]["prepared_unix"], (
    scenes["scene_0000"], before["scene_0000"])
job = steps[1]["value"]["job_id"]
assert {scenes[s]["job_id"] for s in ("scene_0001", "scene_0002")} == {f"{job}_1", f"{job}_2"}, scenes
timing = [json.loads(line) for line in open(f"{preview['cache_root']}/.timing.jsonl", encoding="utf-8")]
timed = {row["scene_id"]: row for row in timing if row.get("job_id") in (f"{job}_1", f"{job}_2")}
assert set(timed) == {"scene_0001", "scene_0002"}, timing
assert all(row["files_read"] > 0 and row["io_threads"] == 16 for row in timed.values()), timed
timed_text = ", ".join("%s: %d files, %s s" % (k, v["files_read"], v["total_s"]) for k, v in sorted(timed.items()))
print(f"Step CD-7 verified: prepare all (workers omitted) -> one sbatch, an array of 3 one-CPU tasks "
      f"(%3), each task claimed its scene ({job}_1, {job}_2) and logged its timing "
      f"({timed_text}); "
      f"status all ready; "
      f"scene_0000 prepared_unix unchanged ({before['scene_0000']['prepared_unix']})")
session = steps[3]["value"]
assert session["origin"].startswith("http://127.0.0.1:") and session["token"], {k: v for k, v in session.items() if k != "token"}
assert session["target_type"] == "stream", session["target_type"]
log = open(ssh_log, encoding="utf-8").read().splitlines()
assert "remote-start" not in log and log.count("dataset-stream") == 1, log
served = json.load(open(f"{tmp}/all-served.json", encoding="utf-8"))
ready = sorted(key for key, value in scenes.items() if value["state"] == "ready")
assert served == ready, (served, ready)
partial = json.load(open(f"{tmp}/one-served.json", encoding="utf-8"))
images = json.load(open(f"{tmp}/all-served-images.json", encoding="utf-8"))
original, strip = images
assert (original["status"], original["type"]) == (200, "image/png"), original
assert (strip["status"], strip["type"]) == (200, "image/jpeg"), strip
print(f"Step CD-8 verified: dataset_open -> origin {session['origin']} with a token, no viewer job "
      f"(no remote start; one ssh stream); served scenes "
      f"{served} == ready scenes (after one-scene prepare: {partial}); camera0 served from the "
      f"dataset's original PNG through the media root ({original['bytes']} bytes)")
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
# --- Flow F: camera previews, chosen in Settings --------------------------------
# The setting for this server changes what the next preview starts with (and so
# its hash); preparing the ready scene_0001 again with previews only re-stages
# its images. The opened scene serves the preview, and the original on `full`.
printf '[%s, %s, %s, %s, %s]' \
    "$(camera_query scene_0001)" "$(camera_query scene_0001 ', "full": "true"')" \
    "$(camera_query scene_0001 ', "width": 160')" "$(camera_query scene_0000)" \
    "$(camera_query scene_0000 ', "width": 160')" > "${TMP_DIR}/previews-images.json"
run_flow previews "[
  {\"command\": \"get_camera_images_setting\", \"args\": {$(profile_args)}},
  {\"command\": \"dataset_prepare\", \"args\": {${DATASET_ARGS}, \"scenes\": [\"scene_0001\"], \"workers\": 1}},
  {\"command\": \"set_camera_images_setting\", \"args\": {$(profile_args), \"mode\": \"previews\"}},
  {\"command\": \"dataset_prepare\", \"args\": {${DATASET_ARGS}, \"scenes\": [\"scene_0001\"], \"workers\": 1}},
  {\"command\": \"dataset_prepare_submit\", \"args\": {$(profile_args), \"previewHash\": \"@last_preview_hash\", \"confirmed\": true}},
  {\"command\": \"dataset_status\", \"args\": {${DATASET_ARGS}}},
  {\"command\": \"dataset_previews\", \"args\": {$(profile_args), \"remove\": false}},
  {\"command\": \"dataset_open\", \"args\": {${DATASET_ARGS}}}
]"
check previews <<'PY'
import json, os, sys
result, ssh_log, tmp, raw = sys.argv[1:5]
steps = json.load(open(result, encoding="utf-8"))["steps"]
for step in steps:
    assert step["ok"], step
before, originals, setting, previews = (steps[i]["value"] for i in range(4))
assert before["effective"] == "originals" and setting["effective"] == "previews", (before, setting)
assert originals["images"] == "originals" and previews["images"] == "previews", (originals["images"], previews["images"])
assert " --images previews " in previews["script"], previews["script"]
assert originals["preview_hash"] != previews["preview_hash"], (originals["preview_hash"], previews["preview_hash"])
state = json.load(open(f"{tmp}/state/state.json", encoding="utf-8"))
assert list(state["camera_images_by_profile"].values()) == ["previews"], state
print(f"Step CD-11 verified: Settings -> previews for this server: the next preview starts with "
      f"--images previews and its hash changed ({originals['preview_hash']} -> {previews['preview_hash']})")
modes = {scene["scene_id"]: scene.get("images") for scene in steps[5]["value"]["scenes"]}
assert modes == {"scene_0000": "originals", "scene_0001": "previews", "scene_0002": "originals"}, modes
usage = steps[6]["value"]
assert usage["scenes"] == 1 and usage["bytes"] > 0, usage
preview, full, strip, original, original_strip = json.load(open(f"{tmp}/previews-served-images.json", encoding="utf-8"))
assert (preview["status"], preview["type"]) == (200, "image/jpeg"), preview
assert (full["status"], full["type"]) == (200, "image/png"), full
assert (original["status"], original["type"]) == (200, "image/png"), original
assert preview["bytes"] < original["bytes"], (preview, original)
print(f"Step CD-12 verified: scene_0001 upgraded to previews without re-ingesting; status modes {modes}; "
      f"previews use {usage['bytes']} bytes; opened scene serves the preview (image/jpeg, {preview['bytes']} B), "
      f"the original on full (image/png, {full['bytes']} B), and scene_0000's original (image/png)")
print("E2E_CAMERA_MEASURE " + json.dumps({"preview": preview, "full": full, "strip_preview": strip,
                                          "original": original, "strip_original": original_strip}))
PY

# --- Flow G: remove previews -----------------------------------------------------
printf '[%s]' "$(camera_query scene_0001)" > "${TMP_DIR}/removal-images.json"
run_flow removal "[
  {\"command\": \"dataset_previews\", \"args\": {$(profile_args), \"remove\": true}},
  {\"command\": \"dataset_previews\", \"args\": {$(profile_args), \"remove\": false}},
  {\"command\": \"dataset_status\", \"args\": {${DATASET_ARGS}}},
  {\"command\": \"dataset_open\", \"args\": {${DATASET_ARGS}}}
]"
raw_digest > "${TMP_DIR}/raw-after-previews.txt"
cmp -s "${TMP_DIR}/raw-before.txt" "${TMP_DIR}/raw-after-previews.txt" || fail "CD-13 raw tree changed"
check removal <<'PY'
import json, sys
result, ssh_log, tmp, raw = sys.argv[1:5]
steps = json.load(open(result, encoding="utf-8"))["steps"]
for step in steps:
    assert step["ok"], step
removed, after, status = (steps[i]["value"] for i in range(3))
assert removed["removed"]["scenes"] == 1 and after["bytes"] == 0, (removed, after)
modes = {scene["scene_id"]: scene.get("images") for scene in status["scenes"]}
assert set(modes.values()) == {"originals"}, modes
(image,) = json.load(open(f"{tmp}/removal-served-images.json", encoding="utf-8"))
assert (image["status"], image["type"]) == (200, "image/png"), image
print(f"Step CD-13 verified: Remove previews deleted {removed['removed']['bytes']} bytes in "
      f"{removed['removed']['scenes']} scene; all scenes back on originals and scene_0001 still "
      "serves its image; raw tree byte-identical")
PY

# --- Flow H: a second Open reuses the viewer and its one ssh stream ---------------
# Viewing a prepared dataset runs no Slurm job: both Opens go to the app's own
# viewer server, which reads the cache over a single ssh connection.
run_flow reuse "[
  {\"command\": \"dataset_open\", \"args\": {${DATASET_ARGS}, \"sceneId\": \"scene_0000\"}},
  {\"command\": \"dataset_open\", \"args\": {${DATASET_ARGS}, \"sceneId\": \"scene_0002\"}}
]"
check reuse <<'PY'
import json, sys
result, ssh_log, tmp, raw = sys.argv[1:5]
steps = json.load(open(result, encoding="utf-8"))["steps"]
for step in steps:
    assert step["ok"], step
first, second = steps[0]["value"], steps[1]["value"]
assert (first["origin"], first["token"]) == (second["origin"], second["token"]), "the second Open started another server"
log = open(ssh_log, encoding="utf-8").read().splitlines()
assert not [line for line in log if line.startswith(("remote-", "sbatch", "tunnel"))], log
assert log.count("dataset-stream") == 1, log
served = json.load(open(f"{tmp}/reuse-served.json", encoding="utf-8"))
assert served == ["scene_0000", "scene_0001", "scene_0002"], served
print(f"Step CD-14 verified: two Opens of the same dataset -> one viewer server ({first['origin']}), "
      f"no Slurm job (0 sbatch, 0 remote start), one ssh stream serving {len(served)} scenes")
PY

# --- Flow I: a viewer job that starts slowly (the real cluster's reply) ------------
# On the cluster the job queued, ran, and only then wrote its session file; a
# lookup by session id alone answered {"status": "stopped", "state": "RUNNING"}
# and the app refused it ("Cannot reattach: … not running (state: RUNNING)").
export FAKE_REMOTE_SLOW_SESSION=1
run_flow slowsession "[
  {\"command\": \"remote_connect\", \"args\": {$(profile_args), \"dataRoot\": \"${CLUSTER}/results\"}}
]"
unset FAKE_REMOTE_SLOW_SESSION
check slowsession <<'PY'
import json, sys
result, ssh_log, tmp, raw = sys.argv[1:5]
(step,) = json.load(open(result, encoding="utf-8"))["steps"]
assert step["ok"], step
assert step["value"]["origin"].startswith("http://127.0.0.1:"), step["value"]
log = open(ssh_log, encoding="utf-8").read().splitlines()
assert log.count("remote-start") == 1 and "remote-status" in log, log
print(f"Step CD-15 verified: a viewer job that ran before writing its session (status by id "
      f"alone said stopped/RUNNING) is attached, not refused: origin {step['value']['origin']}, "
      f"{log.count('remote-status')} status check(s), 1 remote start")
PY

# --- Flow J: view prepared scenes while the prepare job runs ---------------------
# train_0000 is prepared first; then a job for the rest is submitted and held
# running (FAKE_SBATCH_HOLD). The ready scene is viewable meanwhile, over ssh,
# with no viewer job; the scenes still preparing are not served.
TRAIN_ARGS="${DATASET_ARGS/\"split\": \"test\"/\"split\": \"train\"}"
[ "${TRAIN_ARGS}" != "${DATASET_ARGS}" ] || fail "could not derive the train split arguments"
run_flow trainone "[
  {\"command\": \"dataset_prepare\", \"args\": {${TRAIN_ARGS}, \"scenes\": [\"train_0000\"], \"workers\": 1}},
  {\"command\": \"dataset_prepare_submit\", \"args\": {$(profile_args), \"previewHash\": \"@last_preview_hash\", \"confirmed\": true}}
]"
# --- Flow K: no nodes for jobs, still viewable; the streamer refuses escapes --------
# Only train_0000 is prepared; the job for the rest cannot be submitted.
export FAKE_SBATCH_FAIL=1
run_flow nonodes "[
  {\"command\": \"dataset_prepare\", \"args\": {${TRAIN_ARGS}, \"scenes\": \"all\", \"workers\": 1}},
  {\"command\": \"dataset_prepare_submit\", \"args\": {$(profile_args), \"previewHash\": \"@last_preview_hash\", \"confirmed\": true}},
  {\"command\": \"dataset_open\", \"args\": {${TRAIN_ARGS}, \"sceneId\": \"train_0000\"}}
]"
unset FAKE_SBATCH_FAIL
check nonodes "${FAKE_SSH}" "${REMOTE_HOME}" "${PYTHON}" <<'PY'
import json, os, subprocess, sys
result, ssh_log, tmp, raw, fake_ssh, home, python = sys.argv[1:8]
preview, submit, opened = json.load(open(result, encoding="utf-8"))["steps"]
assert preview["ok"] and opened["ok"], (preview, opened)
assert not submit["ok"] and "node configuration is not available" in submit["error"], submit
served = json.load(open(f"{tmp}/nonodes-served.json", encoding="utf-8"))
assert served == ["train_0000"], served
log = open(ssh_log, encoding="utf-8").read().splitlines()
assert "remote-start" not in log and log.count("dataset-stream") == 1, log
print(f"Step CD-16 verified: sbatch failed (no nodes: {submit['error'].splitlines()[0][:80]!r}); "
      f"dataset_open still served the prepared {served} over ssh, no job")

# The exact streamer command the app ran, over the same fake ssh: reads inside
# the dataset work; a climb out, an absolute path and a symlink escape are refused.
remote = open(ssh_log + ".stream-command", encoding="utf-8").read()
cache_root = remote.split("--root ", 1)[1].split(" --root", 1)[0].strip("'")
escape = os.path.join(cache_root, "train_0000", "escape-link")
os.symlink(f"{home}/.fake-slurm/next-id", escape)
try:
    requests = [
        {"op": "read", "path": "train_0000/manifest.json"},
        {"op": "read", "path": "../../../home/.fake-slurm/next-id"},
        {"op": "read", "path": f"{home}/.fake-slurm/next-id"},
        {"op": "read", "path": "train_0000/escape-link"},
        {"op": "stat", "path": "train_0000/../../.."},
    ]
    stdin = "".join(json.dumps({**r, "id": i + 1}) + "\n" for i, r in enumerate(requests))
    env = {**os.environ, "FAKE_REMOTE_HOME": home, "FAKE_CLUSTER_PYTHON": python}
    env.pop("FAKE_SSH_LOG", None)
    out = subprocess.run([fake_ssh, "fake-cluster", remote], input=stdin.encode(), env=env,
                         capture_output=True, timeout=60).stdout
finally:
    os.unlink(escape)
replies = []
while out:
    line, out = out.split(b"\n", 1)
    header = json.loads(line)
    out = out[int(header.get("size") or 0):]
    replies.append(header)
assert replies[0]["ok"], replies[0]
assert all(not reply["ok"] for reply in replies[1:]), replies
print(f"Step CD-17 verified: the app's streamer command reads inside the dataset and refuses "
      f"{len(replies) - 1} escapes (.., absolute, symlink, stat ..): "
      f"{sorted({reply['error'][:40] for reply in replies[1:]})}")
PY

# Then the job for the rest is submitted and held running.
HOLD="${TMP_DIR}/release-prepare"
export FAKE_SBATCH_HOLD="${HOLD}"
run_flow during "[
  {\"command\": \"dataset_prepare\", \"args\": {${TRAIN_ARGS}, \"scenes\": \"all\", \"workers\": 1}},
  {\"command\": \"dataset_prepare_submit\", \"args\": {$(profile_args), \"previewHash\": \"@last_preview_hash\", \"confirmed\": true}},
  {\"command\": \"dataset_status\", \"args\": {${TRAIN_ARGS}}},
  {\"command\": \"dataset_open\", \"args\": {${TRAIN_ARGS}, \"sceneId\": \"train_0000\"}}
]"
unset FAKE_SBATCH_HOLD
check during "${HOLD}" "${REMOTE_HOME}" <<'PY'
import json, os, sys, time
result, ssh_log, tmp, raw, hold, home = sys.argv[1:7]
steps = json.load(open(result, encoding="utf-8"))["steps"]
for step in steps:
    assert step["ok"], step
job_id = steps[1]["value"]["job_id"]
slurm = f"{home}/.fake-slurm"
assert os.path.exists(f"{slurm}/{job_id}.running") and not os.path.exists(f"{slurm}/{job_id}.exit"), \
    "the prepare job was not still running while viewing"
states = {scene["scene_id"]: scene["state"] for scene in steps[2]["value"]["scenes"]}
assert states["train_0000"] == "ready" and all(
    state != "ready" for scene, state in states.items() if scene != "train_0000"), states
served = json.load(open(f"{tmp}/during-served.json", encoding="utf-8"))
assert served == ["train_0000"], served
log = open(ssh_log, encoding="utf-8").read().splitlines()
assert log.count("sbatch") == 1 and "remote-start" not in log and log.count("dataset-stream") == 1, log
open(hold, "w").close()
deadline = time.monotonic() + 120
while not os.path.exists(f"{slurm}/{job_id}.exit"):
    assert time.monotonic() < deadline, "the released prepare job did not finish"
    time.sleep(0.2)
assert open(f"{slurm}/{job_id}.exit").read().strip() == "0"
print(f"Step CD-18 verified: while prepare job {job_id} ran (others {sorted(set(states.values()) - {'ready'})}), "
      f"dataset_open served the ready scene {served} over one ssh stream with no viewer job; "
      "the job then finished")
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
            "has no capabilities command, need a build with cluster-datasets-5). Update it with: " + update)
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
