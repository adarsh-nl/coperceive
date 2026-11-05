#!/bin/bash
# Pack Town03-000 from the Griffin release into ~/results/import-test/.
# CPU only. Approved 2026-10-23: partition ps, walltime at most 1 hour.
#SBATCH --job-name=import-town03
#SBATCH --partition=ps
#SBATCH --cpus-per-task=4
#SBATCH --time=01:00:00
#SBATCH --output=/home/nanjaiyalathaa/logs/%x-%j.out
#SBATCH --error=/home/nanjaiyalathaa/logs/%x-%j.err

set -euo pipefail

SCENE="/home/nanjaiyalathaa/results/628385/scenes/Town03-000"
GRIFFIN="/datasets/eemcs/ps/cv/huggingface/griffin/datasets/griffin_50scenes_25m/griffin_50scenes_25m/griffin-release"
OUT="/home/nanjaiyalathaa/results/import-test/${SLURM_JOB_ID}"

mkdir -p "$OUT" /home/nanjaiyalathaa/logs
cd /home/nanjaiyalathaa/wt/m2-import
# shellcheck disable=SC1091
source /home/nanjaiyalathaa/coperceive/.venv/bin/activate
export PYTHONPATH=/home/nanjaiyalathaa/wt/m2-import
export PYTHONUNBUFFERED=1

echo "start_epoch=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
python -m coperceive scene pack "$SCENE" "$OUT/full" --images full --griffin-root "$GRIFFIN"
python -m coperceive scene pack "$SCENE" "$OUT/preview" --images preview --griffin-root "$GRIFFIN"

python - "$OUT" <<'PY'
import hashlib, json, sys
from pathlib import Path

out = Path(sys.argv[1])
for mode in ("full", "preview"):
    scene = out / mode / "Town03-000"
    record = json.loads((scene / "IMPORT.json").read_text(encoding="utf-8"))
    total = 0
    for row in record["files"]:
        path = scene / row["path"]
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if digest != row["sha256"] or len(data) != row["size"]:
            raise SystemExit(f"IMPORT.json mismatch: {path}")
        total += len(data)
    print(f"mode={mode} files={len(record['files'])} bytes={total} errors={len(record['errors'])} import_ok=1")
print("complete_ok=1")
PY

echo "end_epoch=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
du -sb "$OUT/full" "$OUT/preview"
