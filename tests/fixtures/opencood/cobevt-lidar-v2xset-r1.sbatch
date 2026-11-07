#!/bin/bash
# model-opencood-r1: emitted by coperceive model run in Slurm mode; --time from the manifest's resources (no earlier run with a timing split under --out):
#   180 s staging + 60 s conversion + 0.08 s/sample x 7 samples, rounded up to 10 min, doubled.
# Compute nodes install nothing and reach no index; the plugin is already in the .venv.
#SBATCH --job-name=model-opencood-r1
#SBATCH --partition=test-partition
#SBATCH --gres=gpu:1
#SBATCH --cpus-per-task=8
#SBATCH --mem=16G
#SBATCH --time=00:20:00
#SBATCH --output=<tmp>/logs/%x-%j.out
#SBATCH --error=<tmp>/logs/%x-%j.err
#SBATCH --mail-type=END,FAIL

set -euo pipefail
SCRATCH="/scratch/$USER/$SLURM_JOB_ID"
RUN_ID=r1
mkdir -p "$SCRATCH"
trap 'rm -rf "$SCRATCH"' EXIT
export TMPDIR="$SCRATCH/tmp"
mkdir -p "$TMPDIR" "$SCRATCH/scenes" "$SCRATCH/out" "$SCRATCH/work"

module purge

export PYTHONPATH=<repo>  # the coperceive that emitted this job, not the venv's editable target

STAGE_START=$SECONDS
rsync -a --files-from=<tmp>/predictions/.slurm/r1.scenes.txt <tmp>/scenes/ "$SCRATCH/scenes/"
# The checkpoint archive is staged with the inputs; the run below reads it
# from scratch through the rewritten --option, never from the shared path.
mkdir -p "$SCRATCH/checkpoints/checkpoint"
rsync -a <tmp>/cobevt_lidar.zip "$SCRATCH/checkpoints/checkpoint/"
STAGING=$((SECONDS - STAGE_START))  # rsync wall time -> timings.staging_s

# The run's exit status is kept: a partial run (3 or 4) still comes home below.
set +e
<python> -m coperceive model run --manifest <plugin>/model.yaml --scenes "$SCRATCH/scenes" --out "$SCRATCH/out" --run-id r1 --work "$SCRATCH/work" --staging-seconds "$STAGING" --model cobevt-lidar-v2xset --option "checkpoint=$SCRATCH/checkpoints/checkpoint/cobevt_lidar.zip"
RC=$?
set -e
echo "model run exit $RC"

mkdir -p "$SCRATCH/out/$RUN_ID"
<python> - "$SCRATCH/out/$RUN_ID/environment.json" <<'PY' || true
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

mkdir -p <tmp>/results/"$SLURM_JOB_ID"
rsync -a "$SCRATCH/out/" <tmp>/results/"$SLURM_JOB_ID"/
# The work directory's logs and artefacts, not its inputs or any toolkit copy.
rsync -a --include='*/' --include='*.log' --include='artifacts/***' --exclude='*' --prune-empty-dirs "$SCRATCH/work/" <tmp>/results/"$SLURM_JOB_ID"/work/
mkdir -p <tmp>/predictions/r1
rsync -a "$SCRATCH/out/$RUN_ID/" <tmp>/predictions/r1/
echo "done: <tmp>/predictions/r1 (exit $RC)"
exit "$RC"
