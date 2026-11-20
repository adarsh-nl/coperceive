#!/bin/bash
# Full evaluation run for plan plan-1790000000-aabbccddeeff.
# Pipeline: griffin.
# Rendered by Coperceive from the saved plan and the server profile.
# The user reviewed this exact script before confirming the submission.
#SBATCH --job-name=coperceive-run-plan-1790000000-aabbccddeeff
#SBATCH --partition=gpu-a40
#SBATCH --gres=gpu:1
#SBATCH --cpus-per-task=8
#SBATCH --mem=64G
#SBATCH --time=01:00:00
#SBATCH --constraint="a40"
#SBATCH --output=/home/tester/logs/%x-%j.out
#SBATCH --error=/home/tester/logs/%x-%j.err
#SBATCH --mail-type=END,FAIL

set -euo pipefail
SCRATCH_DIR="/scratch/$USER/$SLURM_JOB_ID"
mkdir -p -m 0700 -- "$SCRATCH_DIR"
trap 'rm -rf -- "$SCRATCH_DIR"' EXIT
export TMPDIR="$SCRATCH_DIR/tmp"
mkdir -p -m 0700 -- "$TMPDIR"
mkdir -p -m 0700 -- "$SCRATCH_DIR/work"
OUT_DIR="/home/tester/results/runs/plan-1790000000-aabbccddeeff"
mkdir -p -m 0700 -- "$OUT_DIR"

module purge
module load python/3.9.18
module load nvidia/cuda-11.8
module load nvidia/cuda-11.x_cudnn-8.6
export PYTHONPATH=/opt/coperceive  # the coperceive the run's python imports, as plan_sbatch exports its own
echo "Starting run for plan plan-1790000000-aabbccddeeff on $(hostname)"
# The run's exit status is kept: its logs and artefacts come home below either way.
set +e
/opt/venv/bin/python -m coperceive model run --manifest /opt/coperceive/plugins/griffin/coperceive_griffin_model/model.yaml --scenes /data/griffin/scenes --out /home/tester/results/runs/plan-1790000000-aabbccddeeff --run-id plan-1790000000-aabbccddeeff --model vehicle-side --raw-root /data/griffin/raw --option ckpt_root=/data/griffin/ckpts --work "$SCRATCH_DIR/work"
RC=$?
set -e
echo "model run exit $RC"
mkdir -p -m 0700 -- "$OUT_DIR/plan-1790000000-aabbccddeeff"
/opt/venv/bin/python - "$OUT_DIR/plan-1790000000-aabbccddeeff/environment.json" <<'PY' || true
import hashlib
import json
import os
import platform
import subprocess
import sys
from importlib import metadata

out = sys.argv[1]
wanted = ("numpy", "pyarrow", "torch", "pillow", "pyyaml")
packages = {}
for name in wanted:
    try:
        packages[name] = metadata.version(name)
    except metadata.PackageNotFoundError:
        continue
freeze = None
try:
    done = subprocess.run(
        [sys.executable, "-m", "pip", "freeze"],
        capture_output=True,
        timeout=20,
        check=False,
    )
    if done.returncode == 0:
        freeze = hashlib.sha256(done.stdout).hexdigest()
except (OSError, subprocess.TimeoutExpired):
    freeze = None
loaded = os.environ.get("LOADEDMODULES", "")
modules = [part for part in loaded.split(":") if part]
cuda = None
driver = None
try:
    smi = subprocess.run(
        ["nvidia-smi"],
        capture_output=True,
        text=True,
        timeout=8,
        check=False,
    )
    if smi.returncode == 0:
        marker = "CUDA Version:"
        for line in smi.stdout.splitlines():
            if marker in line:
                tail = line.split(marker, 1)[1].strip()
                cuda = tail.split()[0] if tail else None
                break
    query = subprocess.run(
        ["nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader"],
        capture_output=True,
        text=True,
        timeout=8,
        check=False,
    )
    if query.returncode == 0:
        rows = [row.strip() for row in query.stdout.splitlines() if row.strip()]
        if rows:
            driver = rows[0].split(",")[0].strip() or None
except (OSError, subprocess.TimeoutExpired):
    pass
digest = None
for key in ("APPTAINER_IMAGE", "SINGULARITY_IMAGE"):
    value = os.environ.get(key, "")
    if value.startswith("sha256:"):
        digest = value
        break
payload = {
    "schema": "coperceive-environment",
    "schema_version": 1,
    "python": platform.python_version(),
    "key_packages": packages,
    "pip_freeze_sha256": freeze,
    "modules": modules,
    "container_image_digest": digest,
    "cuda": cuda,
    "driver": driver,
}
with open(out, "w", encoding="utf-8") as handle:
    json.dump(payload, handle, sort_keys=True)
    handle.write("\n")
PY
# The work directory's logs and artefacts, not its inputs or any toolkit copy.
rsync -a --include='*/' --include='*.log' --include='artifacts/***' --exclude='*' --prune-empty-dirs "$SCRATCH_DIR/work/" "$OUT_DIR/work/"
if [ "$RC" -ne 0 ]; then exit "$RC"; fi

test -s "$OUT_DIR/plan-1790000000-aabbccddeeff/run.json" || { echo "coperceive model run wrote no run.json in $OUT_DIR/plan-1790000000-aabbccddeeff." >&2; exit 1; }
if grep -Eq '"boxes_written": *[1-9]' "$OUT_DIR/plan-1790000000-aabbccddeeff/run.json"; then compgen -G "$OUT_DIR/plan-1790000000-aabbccddeeff/*/boxes.parquet" >/dev/null || { echo "coperceive model run wrote boxes but no boxes.parquet for any scene in $OUT_DIR/plan-1790000000-aabbccddeeff." >&2; exit 1; }; fi
echo "Run finished for plan plan-1790000000-aabbccddeeff: $OUT_DIR"
