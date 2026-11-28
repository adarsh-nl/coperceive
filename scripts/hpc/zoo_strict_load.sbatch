#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
#
# Strict-load every OpenCOOD checkpoint under a root, on one compute node, running no code
# from the checkpoint files (see zoo_strict_load.py). Submit from the coperceive checkout:
#
#   sbatch scripts/hpc/zoo_strict_load.sbatch <checkpoint_root> <path> [<path> ...]
#
# Each <path> is a checkpoint folder or file relative to <checkpoint_root>; nothing else under
# the root is read, so dataset trees and zips beside the checkpoints are never walked or
# opened. Reads those paths only; copies the checkpoint files to /local/$SLURM_JOB_ID, writes
# one JSON report to $HOME/results/coperceive-zoo/<jobid>/ (ZOO_OUT overrides the directory).
# No dataset is opened and no forward pass runs; the GPU is requested only so the job runs on
# the hardware the models will run on, and its name is recorded.
#
# torch 1.12.1+cu113 has no kernels for sm_89 (L40/L40S, "lovelace"), so the job asks for
# an Ampere A40 or A100; the ps partition has only Lovelace GPUs, so main-gpu is used.
#
#SBATCH --job-name=coperceive-zoo-strict-load
#SBATCH --partition=main-gpu
#SBATCH --gres=gpu:ampere:1
#SBATCH --constraint=a40|a100
#SBATCH --cpus-per-task=4
#SBATCH --mem=32G
#SBATCH --time=00:30:00
#SBATCH --output=%x-%j.out
#SBATCH --error=%x-%j.err

set -euo pipefail
USAGE="usage: sbatch scripts/hpc/zoo_strict_load.sbatch <checkpoint_root> <path> [<path> ...]"
CKPT_ROOT="${1:?${USAGE}}"
shift
if [ "$#" -lt 1 ]; then
    echo "zoo_strict_load.sbatch: name at least one checkpoint path under the root" >&2
    echo "${USAGE}" >&2
    exit 2
fi
INCLUDES=()
for path in "$@"; do
    INCLUDES+=(--include "${path}")
done
ROOT_DIR="${COPERCEIVE_ROOT:-${SLURM_SUBMIT_DIR:-$PWD}}"
DRIVER="${ROOT_DIR}/scripts/hpc/zoo_strict_load.py"
if [ ! -f "${DRIVER}" ]; then
    echo "zoo_strict_load.sbatch: cannot find ${DRIVER}" >&2
    echo "Set COPERCEIVE_ROOT to the checkout, or submit from the checkout directory." >&2
    exit 2
fi
PYTHON="${ZOO_PYTHON:-$HOME/.conda/envs/opencood-official/bin/python}"
OPENCOOD_SRC="${ZOO_OPENCOOD_SRC:-$HOME/opencood-official}"
OPENCOOD_COMMIT="31ba16025da27ffe4e336f011290dfbc66f9a1f1"
V2XVIT_SRC="${ZOO_V2XVIT_SRC:-$HOME/v2xvit-official}"
COSDH_SRC="${ZOO_COSDH_SRC:-$HOME/cosdh-official}"
# CoSDH imports icecream, which only the cosdh env has.
COSDH_PYTHON="${ZOO_COSDH_PYTHON:-$HOME/.conda/envs/cosdh/bin/python}"
if [ ! -x "${PYTHON}" ]; then
    echo "zoo_strict_load.sbatch: interpreter not found: ${PYTHON} (set ZOO_PYTHON)" >&2
    exit 2
fi
HEAD="$(git --no-optional-locks -C "${OPENCOOD_SRC}" rev-parse HEAD)"
if [ "${HEAD}" != "${OPENCOOD_COMMIT}" ]; then
    echo "zoo_strict_load.sbatch: ${OPENCOOD_SRC} is at ${HEAD}, not ${OPENCOOD_COMMIT}" >&2
    exit 2
fi
if [ -n "$(git --no-optional-locks -C "${OPENCOOD_SRC}" status --porcelain --untracked-files=no)" ]; then
    echo "zoo_strict_load.sbatch: ${OPENCOOD_SRC} has tracked changes; the check needs ${OPENCOOD_COMMIT} as committed" >&2
    exit 2
fi

SCRATCH="/local/${SLURM_JOB_ID:?run this through sbatch}"
mkdir -p "${SCRATCH}"
trap 'rm -rf "${SCRATCH}"' EXIT
OUT_DIR="${ZOO_OUT:-$HOME/results/coperceive-zoo/${SLURM_JOB_ID}}"
mkdir -p "${OUT_DIR}"

# No modules: the torch and spconv wheels in the env carry their own CUDA runtime.
# No bytecode is written into the checkouts; no user site-packages.
export PYTHONDONTWRITEBYTECODE=1
export PYTHONNOUSERSITE=1
export CUDA_MODULE_LOADING=LAZY

echo "host=$(hostname) job=${SLURM_JOB_ID}"
echo "root=${ROOT_DIR} ($(git --no-optional-locks -C "${ROOT_DIR}" rev-parse HEAD))"
echo "python=${PYTHON}"
echo "opencood=${OPENCOOD_SRC} (${HEAD})"
echo "checkpoints=${CKPT_ROOT}"
echo "out=${OUT_DIR}"
nvidia-smi --query-gpu=name,driver_version --format=csv,noheader || true

cd "${SCRATCH}"
"${PYTHON}" -B "${DRIVER}" \
    --root "${CKPT_ROOT}" \
    "${INCLUDES[@]}" \
    --stage "${SCRATCH}/stage" \
    --out "${OUT_DIR}/zoo_strict_load.json" \
    --repo "${ROOT_DIR}" \
    --primary-src "${OPENCOOD_SRC}" \
    --package opencood \
    --expect-count 24 \
    --codebase "v2x-vit=${V2XVIT_SRC}:v2xvit" \
    --codebase "cosdh=${COSDH_SRC}:opencood:${COSDH_PYTHON}" \
    --relaxed-ordered-dict
echo "done: ${OUT_DIR}/zoo_strict_load.json"
