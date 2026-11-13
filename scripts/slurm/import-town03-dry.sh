#!/bin/bash
# Dry-run only. Does not write a scene. CPU, partition ps.
#SBATCH --job-name=import-dry
#SBATCH --partition=ps
#SBATCH --cpus-per-task=2
#SBATCH --time=00:20:00
#SBATCH --output=/home/cluster-user/logs/%x-%j.out
#SBATCH --error=/home/cluster-user/logs/%x-%j.err

set -euo pipefail

SCENE="/home/cluster-user/results/628385/scenes/Town03-000"
GRIFFIN="/datasets/eemcs/ps/cv/huggingface/griffin/datasets/griffin_50scenes_25m/griffin_50scenes_25m/griffin-release"
OUT="/home/cluster-user/results/import-test/dry-${SLURM_JOB_ID}"

mkdir -p /home/cluster-user/logs
cd /home/cluster-user/wt/m2-import
# shellcheck disable=SC1091
source /home/cluster-user/coperceive/.venv/bin/activate
export PYTHONPATH=/home/cluster-user/wt/m2-import
export PYTHONUNBUFFERED=1

echo "start_epoch=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
python -m coperceive scene pack "$SCENE" "$OUT" --images full --griffin-root "$GRIFFIN" --dry-run
if [ -e "$OUT" ]; then
  echo "dry_run_wrote=$OUT"
  exit 4
fi
echo "dry_run_wrote=nothing"
echo "end_epoch=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
