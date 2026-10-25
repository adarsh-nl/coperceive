#!/bin/bash
# Real Get data path: coperceive dataset dry-run, then dataset download
# griffin one-scene, then validate and an open-scene HTTP check.
# CPU only. Scratch stays on the node disk. The report copied home is text.
#SBATCH --job-name=getdata-one-scene
#SBATCH --partition=ps
#SBATCH --cpus-per-task=8
#SBATCH --time=08:00:00
#SBATCH --output=/home/nanjaiyalathaa/logs/%x-%j.out
#SBATCH --error=/home/nanjaiyalathaa/logs/%x-%j.err

set -euo pipefail

SCRATCH="/local/${SLURM_JOB_ID}"
REPORT="/home/nanjaiyalathaa/results/getdata-${SLURM_JOB_ID}"
MAPS="/home/nanjaiyalathaa/results/628385/maps"
mkdir -p "$SCRATCH" "$REPORT" /home/nanjaiyalathaa/logs
trap 'rm -rf "$SCRATCH"' EXIT

cd /home/nanjaiyalathaa/wt/m2-import
# shellcheck disable=SC1091
source /home/nanjaiyalathaa/coperceive/.venv/bin/activate
export PYTHONPATH=/home/nanjaiyalathaa/wt/m2-import
export PYTHONUNBUFFERED=1

echo "start_epoch=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
df -B1 "$SCRATCH" | tee "$REPORT/df-start.txt"

python - <<'PY' "$SCRATCH" "$REPORT/df-samples.txt" &
import sys, time
from pathlib import Path
scratch = Path(sys.argv[1])
out = Path(sys.argv[2])
while True:
    usage = __import__("shutil").disk_usage(scratch)
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with out.open("a", encoding="utf-8") as handle:
        handle.write(f"{stamp} total={usage.total} used={usage.used} free={usage.free}\n")
    time.sleep(60)
PY
SAMPLER=$!

python -m coperceive dataset dry-run griffin one-scene --dest "$SCRATCH/library" \
  | tee "$REPORT/dry-run.json"
python - "$REPORT/dry-run.json" <<'PY'
import json, sys
from pathlib import Path
text = Path(sys.argv[1]).read_text(encoding="utf-8")
start = text.find("{")
payload = json.loads(text[start:])
if not payload.get("has_headroom"):
    raise SystemExit("dry-run refused: not enough free space")
print(
    f"dry_run_download_bytes={payload['download_bytes']} "
    f"disk_bytes={payload['disk_bytes']} free_bytes={payload['free_bytes']}"
)
PY

echo "download_start=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
set +e
python -m coperceive dataset download griffin one-scene --dest "$SCRATCH/library" --accept-terms \
  | python -u -c 'import sys,datetime
for line in sys.stdin:
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    sys.stdout.write(f"{stamp} {line}")
    sys.stdout.flush()' \
  | tee "$REPORT/progress.ndjson"
DOWNLOAD_STATUS=${PIPESTATUS[0]}
set -e
echo "download_end=$(date -u +%Y-%m-%dT%H:%M:%SZ) status=${DOWNLOAD_STATUS}"
if [[ "$DOWNLOAD_STATUS" -ne 0 ]]; then
  exit "$DOWNLOAD_STATUS"
fi

RAW="$SCRATCH/library/.raw/griffin"
if [[ -f "$RAW/griffin_50scenes_25m/DOWNLOAD.txt" ]]; then
  cp "$RAW/griffin_50scenes_25m/DOWNLOAD.txt" "$REPORT/DOWNLOAD.txt"
fi
du -sb "$SCRATCH/library" "$RAW" > "$REPORT/du-after-download.txt" || true

echo "validate_start=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
python -m coperceive validate "$SCRATCH/library" --maps "$MAPS" --strict \
  --json "$REPORT/validate.json" --markdown "$REPORT/validate.md"
echo "validate_end=$(date -u +%Y-%m-%dT%H:%M:%SZ)"

RELEASE="$RAW/griffin_50scenes_25m/griffin-release"
PORT=$((20000 + SLURM_JOB_ID % 20000))
echo "open_start=$(date -u +%Y-%m-%dT%H:%M:%SZ) release=${RELEASE} port=${PORT}"
python -m coperceive serve --host 127.0.0.1 --port "$PORT" --root "$SCRATCH/library" \
  --maps "$MAPS" --media-root "$RELEASE" > "$REPORT/serve.out" 2>&1 &
SERVE=$!
for _ in 1 2 3 4 5 6 7 8 9 10; do
  if curl -fsS "http://127.0.0.1:${PORT}/api/scenes" > "$REPORT/scenes.json"; then
    break
  fi
  sleep 1
done
curl -fsS "http://127.0.0.1:${PORT}/api/scenes/Town03-000" > "$REPORT/town03.json" || true
curl -sS -D "$REPORT/image.headers" -o "$REPORT/image.bin" \
  "http://127.0.0.1:${PORT}/api/scenes/Town03-000/image?time_us=1118500000&agent_id=vehicle&sensor_id=front" || true
curl -sS -D "$REPORT/map.headers" -o "$REPORT/map.json" \
  "http://127.0.0.1:${PORT}/api/scenes/Town03-000/map" || true
curl -sS -D "$REPORT/map-lines.headers" -o "$REPORT/map-lines.bin" \
  "http://127.0.0.1:${PORT}/api/scenes/Town03-000/map/lines" || true
kill "$SERVE" || true
wait "$SERVE" || true
echo "open_end=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
wc -c "$REPORT/image.bin" "$REPORT/map-lines.bin" | tee "$REPORT/open-bytes.txt"

kill "$SAMPLER" || true
wait "$SAMPLER" || true
df -B1 "$SCRATCH" | tee "$REPORT/df-end.txt"
echo "end_epoch=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
