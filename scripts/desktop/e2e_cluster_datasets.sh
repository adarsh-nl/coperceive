#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
#
# Backend end-to-end check for cluster datasets: synthetic OPV2V layout ->
# detect -> index -> prepare one -> status -> prepare all -> status -> serve.
# No real data and no network. Uses the same Python package the app bundles.
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
PYTHON="${ROOT_DIR}/.venv/bin/python"
if [ ! -x "${PYTHON}" ]; then PYTHON="python3"; fi
if ! "${PYTHON}" -c "import yaml" >/dev/null 2>&1; then
    "${PYTHON}" -m pip install --quiet pyyaml || { echo "E2E_DATASET_FAIL: PyYAML is required for the synthetic tree" >&2; exit 1; }
fi
TMP_DIR="$(mktemp -d /tmp/coperceive-dataset-e2e-XXXXXX)"
trap 'rm -rf "${TMP_DIR}"' EXIT INT TERM HUP
fail() { echo "E2E_DATASET_FAIL: $*" >&2; exit 1; }
step() { echo "Step CD-$1 verified: $2"; }

RAW="${TMP_DIR}/raw/opv2v"
"${PYTHON}" - "${RAW}" <<'PY'
import sys
from pathlib import Path
from tests.opencood_release import AgentTrack, Box, SceneSpec, write_release
root = Path(sys.argv[1])
scenes = [
    SceneSpec(f"scene_{i:04d}", [AgentTrack(str(a), poses=[((float(a), float(f), 1.9), (0.0, 90.0, 0.0)) for f in range(4)], boxes=[Box(1, location=(1.0, 2.0, 0.0))]) for a in (641, 650, 651)], stems=(0, 2, 4, 6))
    for i in range(3)
]
write_release(root, "test", scenes)
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
