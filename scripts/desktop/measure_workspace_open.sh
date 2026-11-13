#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
#
# Measures opening an OPV2V train dataset in the HPC workspace before and after
# the cached scene index, on a fake cluster.
#
# The "cluster" is this machine: the app's login-node command
# (`coperceive dataset index`, the same argv the desktop runs over SSH) is
# executed locally against a synthetic OPV2V train tree, exactly as
# scripts/desktop/e2e_cluster_datasets.sh does. No SSH, Slurm, dataset bytes or
# network. The web measurement (`web/scripts/measure-opening-a-dataset.mts`)
# imports the real client modules: the cold open waits for the index command,
# then a second open shows the cached document and re-indexes in the background.
#
# Usage: scripts/desktop/measure_workspace_open.sh [--scenes N]
# Prints a JSON document; the real-cluster time is not measured here.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
PYTHON="${ROOT_DIR}/.venv/bin/python"
SCENES=500
while [ $# -gt 0 ]; do
    case "$1" in
        --scenes) SCENES="${2:?--scenes needs a number}"; shift 2 ;;
        -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
done
[ -x "${PYTHON}" ] || { echo "missing ${PYTHON}" >&2; exit 1; }

TMP_DIR="$(mktemp -d /tmp/coperceive-open-time-XXXXXX)"
trap 'rm -rf "${TMP_DIR}"' EXIT INT TERM HUP
RAW="${TMP_DIR}/opv2v"

PYTHONPATH="${ROOT_DIR}" "${PYTHON}" - "${RAW}" "${SCENES}" <<'PY'
"""A synthetic OPV2V train split: scene dirs with two agents' frames."""
import sys
from pathlib import Path

from tests.opencood_release import two_cav_scene, write_release

raw, scenes = Path(sys.argv[1]), int(sys.argv[2])
write_release(raw, "train", [two_cav_scene(f"train_{index:04d}") for index in range(scenes)])
print(f"wrote {scenes} synthetic train scenes to {raw}", file=sys.stderr)
PY

cd "${ROOT_DIR}/web"
COPERCEIVE_MEASURE_RAW_ROOT="${RAW}" \
COPERCEIVE_MEASURE_SPLIT="train" \
COPERCEIVE_MEASURE_PYTHON="${PYTHON}" \
COPERCEIVE_MEASURE_SCENES="${SCENES}" \
    npx tsx scripts/measure-opening-a-dataset.mts
