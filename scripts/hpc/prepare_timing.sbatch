#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
#
# One-scene prepare timing on a compute node. Submit with the partition as an
# sbatch argument; Slurm parses #SBATCH before this script runs, so a
# partition cannot be taken from a positional argument inside the file.
#
#   sbatch --partition=<PARTITION> scripts/hpc/prepare_timing.sbatch \
#       <raw_root> <scene_id>
#
#SBATCH --job-name=coperceive-prepare-timing
#SBATCH --cpus-per-task=1
#SBATCH --time=00:30:00
#SBATCH --output=%x-%j.out
#SBATCH --error=%x-%j.err
# No GPU requested (CPU-only diagnostic job).

set -euo pipefail
PARTITION="${1:?usage: sbatch --partition=<partition> prepare_timing.sbatch <raw_root> <scene_id>}"
RAW_ROOT="${2:?raw root is required}"
SCENE="${3:?scene id is required}"
ROOT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
OUT="${HOME}/results/coperceive-timing/${SLURM_JOB_ID:-manual}-${SCENE}"
mkdir -p "${OUT}"
echo "partition=${PARTITION} (allocation comes from sbatch --partition)"
echo "raw_root=${RAW_ROOT}"
echo "scene=${SCENE}"
echo "out=${OUT}"
exec "${ROOT_DIR}/.venv/bin/python" "${ROOT_DIR}/scripts/hpc/prepare_timing.py" \
    --adapter v2xset \
    --raw-root "${RAW_ROOT}" \
    --split train \
    --scene "${SCENE}" \
    --out "${OUT}"
