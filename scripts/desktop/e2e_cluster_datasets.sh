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
            (folder / f"{frame:06d}.yaml").write_text(json.dumps(record))
            (folder / f"{frame:06d}.pcd").write_text(pcd_head)
PY
BEFORE="$(find "${RAW}" -type f | sort | xargs sha256sum)"
echo "Step CD-1 verified: synthetic OPV2V tree with 3 scenes"

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
echo "E2E_DATASET_MARK: cluster datasets backend verified"
