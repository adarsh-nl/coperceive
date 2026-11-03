#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
#
# One-scene prepare timing on a compute node. Submit with the partition as an
# sbatch argument; Slurm parses #SBATCH before this script runs, so a partition
# cannot be taken from a positional argument inside the file.
#
#   sbatch --partition=<PARTITION> scripts/hpc/prepare_timing.sbatch \
#       <raw_root> <scene_id>
#
# The checkout and interpreter are resolved independently of $0: sbatch runs a
# spooled copy, so dirname $0 does not point at the repository.
#
#SBATCH --job-name=coperceive-prepare-timing
#SBATCH --cpus-per-task=1
#SBATCH --time=00:30:00
#SBATCH --output=%x-%j.out
#SBATCH --error=%x-%j.err
# No GPU requested (CPU-only diagnostic job).

set -euo pipefail
RAW_ROOT="${1:?usage: sbatch --partition=<partition> prepare_timing.sbatch <raw_root> <scene_id>}"
SCENE="${2:?scene id is required}"
ROOT_DIR="${COPERCEIVE_ROOT:-${SLURM_SUBMIT_DIR:-$PWD}}"
if [ ! -f "${ROOT_DIR}/scripts/hpc/prepare_timing.py" ]; then
    echo "prepare_timing.sbatch: cannot find scripts/hpc/prepare_timing.py under ${ROOT_DIR}" >&2
    echo "Set COPERCEIVE_ROOT to the checkout, or submit from the checkout directory." >&2
    exit 2
fi
PYTHON="${COPERCEIVE_PYTHON:-$HOME/coperceive-app/.venv/bin/python}"
if [ ! -x "${PYTHON}" ]; then
    echo "prepare_timing.sbatch: interpreter not found: ${PYTHON}" >&2
    echo "Set COPERCEIVE_PYTHON to the saved server profile's coperceive interpreter." >&2
    exit 2
fi
OUT="${HOME}/results/coperceive-timing/${SLURM_JOB_ID:-manual}-${SCENE}"
mkdir -p "${OUT}"
echo "submit_dir=${SLURM_SUBMIT_DIR:-unset}"
echo "root=${ROOT_DIR}"
echo "python=${PYTHON}"
echo "raw_root=${RAW_ROOT}"
echo "scene=${SCENE}"
echo "out=${OUT}"
exec "${PYTHON}" "${ROOT_DIR}/scripts/hpc/prepare_timing.py" \
    --adapter v2xset \
    --raw-root "${RAW_ROOT}" \
    --split train \
    --scene "${SCENE}" \
    --out "${OUT}"
