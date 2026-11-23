#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
#
# Backend end-to-end check for cluster datasets: synthetic OPV2V layout ->
# detect -> index -> prepare one -> status -> prepare all -> status -> serve.
# No real data and no network. Uses the same Python package the app bundles.
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
PYTHON="${ROOT_DIR}/.venv/bin/python"
if [ ! -x "${PYTHON}" ]; then
    APP="$(find "${ROOT_DIR}/src-tauri/target" -type d -name Coperceive.app 2>/dev/null | head -n 1)"
    for candidate in "${APP}/Contents/Resources/resources/python/bin/python3" "${ROOT_DIR}/src-tauri/resources/python/bin/python3"; do
        if [ -n "${candidate}" ] && [ -x "${candidate}" ]; then PYTHON="${candidate}"; break; fi
    done
fi
if [ ! -x "${PYTHON}" ]; then PYTHON="python3"; fi
TMP_DIR="$(mktemp -d /tmp/coperceive-dataset-e2e-XXXXXX)"
trap 'rm -rf "${TMP_DIR}"' EXIT INT TERM HUP
fail() { echo "E2E_DATASET_FAIL: $*" >&2; exit 1; }
step() { echo "Step CD-$1 verified: $2"; }

RAW="${TMP_DIR}/raw/opv2v"
"${PYTHON}" - "${RAW}" <<'PY'
import json, sys
from pathlib import Path
root = Path(sys.argv[1]); split = "test"
NUMPY_YAW = ("!!python/object/apply:numpy.core.multiarray.scalar [!!python/object/apply:numpy.dtype "
             "{args: [f8, false, true], state: !!python/tuple [3, '<', null, null, null, -1, -1, 0]}, "
             "!!binary 'AAAAAACAVkA=']")  # float64 90.0
extrinsic = [[1.0,0.0,0.0,0.0],[0.0,1.0,0.0,0.0],[0.0,0.0,1.0,0.0],[0.0,0.0,0.0,1.0]]
intrinsic = [[335.6,0.0,400.0],[0.0,335.6,300.0],[0.0,0.0,1.0]]
pcd_head = ("# .PCD v0.7 - Point Cloud Data file format\nVERSION 0.7\nFIELDS x y z rgb\n"
            "SIZE 4 4 4 4\nTYPE F F F F\nCOUNT 1 1 1 1\nWIDTH 1\nHEIGHT 1\n"
            "VIEWPOINT 0 0 0 1 0 0 0\nPOINTS 1\nDATA ascii\n0.000000 0.000000 0.000000 0\n")
for scene in range(3):
    base = root / split / f"scene_{scene:04d}"; base.mkdir(parents=True)
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
BEFORE="$(find "${RAW}" -type f | sort | xargs sha256sum)"
grep -q "numpy.core.multiarray.scalar" "${RAW}/test/scene_0000/641/000000.yaml" || { echo "fixture lost its numpy tag" >&2; exit 1; }
echo "Step CD-1 verified: synthetic OPV2V tree with 3 scenes (records carry a numpy-tagged yaw, as real OPV2V does)"

"${PYTHON}" -m coperceive.cli dataset detect --path "${RAW}/test" > "${TMP_DIR}/detect.json"
"${PYTHON}" - "${TMP_DIR}/detect.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
assert d["status"] == "ok" and d["adapter"] == "opv2v" and d["scene_count"] == 3, d
print(f"Step CD-2 verified: detect {d['adapter']} {d['scene_count']} scenes")
PY
"${PYTHON}" -m coperceive.cli dataset index --adapter opv2v --raw-root "${RAW}/test" > "${TMP_DIR}/index.json"
"${PYTHON}" - "${TMP_DIR}/index.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
assert d["scene_count"] == 3 and d["estimated_cache_bytes"] > 0, d["scene_count"]
assert d["cache"]["relative_path"].startswith("coperceive-cache/opv2v/")
print(f"Step CD-3 verified: index {d['scene_count']} scenes, estimate {d['estimated_cache_bytes']} bytes")
PY
CACHE="${TMP_DIR}/results/$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["cache"]["relative_path"])' "${TMP_DIR}/index.json")"
"${PYTHON}" -m coperceive.cli dataset prepare --adapter opv2v --raw-root "${RAW}/test" --cache "${CACHE}" --scenes scene_0000 --workers 1 > "${TMP_DIR}/prepare1.log"
# use status command instead of parsing progress
"${PYTHON}" -m coperceive.cli dataset status --cache "${CACHE}" > "${TMP_DIR}/status1.json"
"${PYTHON}" - "${TMP_DIR}/status1.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
states = {s["scene_id"]: s["state"] for s in d["scenes"]}
assert states == {"scene_0000": "ready", "scene_0001": "not_prepared", "scene_0002": "not_prepared"}, states
print("Step CD-4 verified: prepare one scene ready, others not prepared")
PY
FIRST_PREPARED="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["scenes"][0]["prepared_unix"])' "${TMP_DIR}/status1.json")"
"${PYTHON}" -m coperceive.cli dataset prepare --adapter opv2v --raw-root "${RAW}/test" --cache "${CACHE}" --all --workers 3 > "${TMP_DIR}/prepareall.log"
"${PYTHON}" -m coperceive.cli dataset status --cache "${CACHE}" > "${TMP_DIR}/statusall.json"
"${PYTHON}" - "${TMP_DIR}/statusall.json" "${FIRST_PREPARED}" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
assert all(s["state"] == "ready" for s in d["scenes"]), d["scenes"]
assert str(d["scenes"][0]["prepared_unix"]) == sys.argv[2], (d["scenes"][0], sys.argv[2])
print("Step CD-5 verified: prepare all ready, first scene not re-ingested")
PY
"${PYTHON}" - "${CACHE}" <<'PY'
import sys
from fastapi.testclient import TestClient
from coperceive.app.server import create_app
cache = sys.argv[1]
client = TestClient(create_app(root=cache, token="e2e-token"))
response = client.get("/api/scenes", headers={"Authorization": "Bearer e2e-token"})
assert response.status_code == 200, response.text
scenes = {item["scene_id"] for item in response.json()}
assert scenes == {"scene_0000", "scene_0001", "scene_0002"}, scenes
print("Step CD-6 verified: served scene list contains exactly the ready scenes")
PY
AFTER="$(find "${RAW}" -type f | sort | xargs sha256sum)"
[ "${BEFORE}" = "${AFTER}" ] || fail "raw tree changed"
echo "Step CD-7 verified: raw tree byte-identical"

# --- V2V4Real-shaped split: detect names its splits and index reads every scene ---
# The layout the Servers browser recognises: <root>/test/testoutput_CAV_data_*/<0|1>/,
# two vehicle agents, no camera images. Detect counted 9 scene directories; index
# must return the same 9 scenes (the HPC workspace showed 0 for this folder).
RAW_V2V4REAL="${TMP_DIR}/raw/v2v4real"
"${PYTHON}" - "${RAW_V2V4REAL}" <<'PY'
import sys
from pathlib import Path
root, split = Path(sys.argv[1]), "test"
pcd = ("# .PCD v0.7 - Point Cloud Data file format\nVERSION 0.7\nFIELDS x y z rgb\n"
       "SIZE 4 4 4 4\nTYPE F F F F\nCOUNT 1 1 1 1\nWIDTH 1\nHEIGHT 1\n"
       "VIEWPOINT 0 0 0 1 0 0 0\nPOINTS 1\nDATA ascii\n0.000000 0.000000 0.000000 0\n")
for index in range(9):
    scene = root / split / f"testoutput_CAV_data_2022-03-15-09-54-{40 + index:02d}_0"
    for agent in ("0", "1"):
        folder = scene / agent
        folder.mkdir(parents=True)
        for frame in (0, 1, 2):
            (folder / f"{frame:06d}.yaml").write_text(
                "ego_speed: 5.0\n"
                "gps: [39.9, -83.0, 226.1, 2.6, -0.0003, 0.0047]\n"
                f"lidar_pose: [{agent}.0, {frame}.0, 1.9, 0.0, 90.0, 0.0]\n"
                f"true_ego_pos: [{agent}.0, {frame}.0, 0.0, 0.0, 90.0, 0.0]\n"
                "vehicles:\n  650:\n    angle: [0.0, 90.0, 0.0]\n    ass_id: -1\n"
                "    center: [0.0, 0.0, 0.75]\n    extent: [2.4, 1.0, 0.75]\n"
                f"    location: [{agent}.0, {frame}.0, 0.0]\n    speed: 0.0\n"
            )
            (folder / f"{frame:06d}.pcd").write_text(pcd)
PY
"${PYTHON}" -m coperceive.cli dataset detect --path "${RAW_V2V4REAL}" > "${TMP_DIR}/detect-v2v4real.json"
"${PYTHON}" -m coperceive.cli dataset index --adapter v2v4real --raw-root "${RAW_V2V4REAL}" --split test > "${TMP_DIR}/index-v2v4real.json"
"${PYTHON}" - "${TMP_DIR}/detect-v2v4real.json" "${TMP_DIR}/index-v2v4real.json" <<'PY'
import json, sys
detect = json.load(open(sys.argv[1]))
index = json.load(open(sys.argv[2]))
assert detect["status"] == "ok" and detect["adapter"] == "v2v4real", detect
assert detect["split"] == "test" and detect["scene_count"] == 9, detect
assert detect["splits"] == [{"name": "test", "scene_count": 9, "adapter": "v2v4real"}], detect["splits"]
assert index["scene_count"] == 9 and index["errors"] == [], index
assert index["split"] == "test" and len(index["scenes"]) == 9, index["scenes"]
assert all(scene["agent_count"] == 2 for scene in index["scenes"]), index["scenes"]
print("Step CD-8 verified: V2V4Real-shaped split -> detect v2v4real/test/9 scenes, "
      "splits lists the recognised split, index reads the same 9 scenes with no errors")
PY
# The reported symptom without inventing a cluster fault: the same
# V2V4Real-shaped tree with its agent folders present but no frame files. Detect
# counts the 9 scene directories; index can read none of them and returns 0
# scenes plus 9 per-scene errors. On the old app this rendered exactly
# "V2V4REAL test | 0 scenes / No scenes in this split" while the sidebar kept
# detect's 9. The view now names the errors and offers a retry; the cluster-side
# reason the real tree's scenes were unreadable is still unverified.
RAW_V2V4REAL_EMPTY="${TMP_DIR}/raw/v2v4real-empty"
"${PYTHON}" - "${RAW_V2V4REAL_EMPTY}" <<'PY'
import sys
from pathlib import Path
root, split = Path(sys.argv[1]), "test"
for index in range(9):
    scene = root / split / f"testoutput_CAV_data_2022-03-15-09-54-{40 + index:02d}_0"
    for agent in ("0", "1"):
        (scene / agent).mkdir(parents=True)
PY
"${PYTHON}" -m coperceive.cli dataset detect --path "${RAW_V2V4REAL_EMPTY}" > "${TMP_DIR}/detect-v2v4real-empty.json"
"${PYTHON}" -m coperceive.cli dataset index --adapter v2v4real --raw-root "${RAW_V2V4REAL_EMPTY}" --split test > "${TMP_DIR}/index-v2v4real-empty.json"
"${PYTHON}" - "${TMP_DIR}/detect-v2v4real-empty.json" "${TMP_DIR}/index-v2v4real-empty.json" <<'PY'
import json, sys
detect = json.load(open(sys.argv[1]))
index = json.load(open(sys.argv[2]))
assert detect["status"] == "ok" and detect["scene_count"] == 9, detect
assert index["scene_count"] == 0 and len(index["errors"]) == 9, index
assert all("no samples found with streams.poses" in line for line in index["errors"]), index["errors"]
print("Step CD-9 verified: V2V4Real-shaped tree with no frame files -> detect 9 scenes / "
      "index 0 scenes + 9 per-scene errors, the detect/index disagreement the view now names "
      "(the old view showed only 'No scenes in this split.')")
PY
echo "E2E_DATASET_MARK: cluster datasets backend verified"
