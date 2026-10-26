#!/bin/bash
#SBATCH --job-name=carla-record
#SBATCH --partition=ps
#SBATCH --nodelist=hpc-node11
#SBATCH --gres=gpu:lovelace:1
#SBATCH --cpus-per-task=4
#SBATCH --mem=32G
#SBATCH --time=00:30:00
#SBATCH --output=/home/nanjaiyalathaa/logs/%x-%j.out
#SBATCH --error=/home/nanjaiyalathaa/logs/%x-%j.err

# Not submitted. Runs scripts/sim/record_carla_scene.py.
# The drone is a lidar actor at a fixed 40 m, not a vehicle.

set -euo pipefail

ROOT="/local/nanjaiyalathaa/carla-0.9.15"
PY="$ROOT/py310/bin/python"
REPO="/home/nanjaiyalathaa/wt/site-hero-multi"
SCRATCH="/local/nanjaiyalathaa/rec-${SLURM_JOB_ID}"
CAPTURE="$SCRATCH/capture"
REPORT="/home/nanjaiyalathaa/results/carla-record-${SLURM_JOB_ID}"

mkdir -p "$CAPTURE" "$REPORT" /home/nanjaiyalathaa/logs
hostname -s | tee "$REPORT/node.txt"
test -x "$ROOT/CarlaUE4.sh"
test -x "$PY"
test -f "$REPO/scripts/sim/record_carla_scene.py"

source /etc/profile.d/modules.sh
module load python/3.10.7

PORT=$("$PY" - <<'PY'
import socket
import sys

def triple_is_free(port):
    held = []
    try:
        for offset in (0, 1, 2):
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.bind(("127.0.0.1", port + offset))
            held.append(sock)
        return True
    except OSError:
        return False
    finally:
        for sock in held:
            sock.close()

for port in range(30000, 40000):
    if triple_is_free(port):
        print(port)
        sys.exit(0)
sys.exit("no free port triple")
PY
)
{
  echo "port=$PORT"
  echo "port_plus_1=$((PORT + 1))"
  echo "port_plus_2=$((PORT + 2))"
} | tee "$REPORT/port.txt"

cd "$ROOT"
./CarlaUE4.sh -RenderOffScreen -carla-rpc-port="$PORT" -nosound \
  >"$REPORT/server.log" 2>&1 &
server=$!
cleanup() {
  if kill -0 "$server" 2>/dev/null; then
    kill "$server" 2>/dev/null || true
    wait "$server" 2>/dev/null || true
  fi
  rm -rf "$SCRATCH"
}
trap cleanup EXIT

ready=0
for _ in $(seq 1 90); do
  if "$PY" -c "import socket; s=socket.create_connection(('127.0.0.1', $PORT), 2); s.close()"; then
    ready=1
    break
  fi
  if ! kill -0 "$server" 2>/dev/null; then
    echo "server exited before the port opened" >&2
    exit 1
  fi
  sleep 5
done
test "$ready" = 1

"$PY" "$REPO/scripts/sim/record_carla_scene.py" \
  --port "$PORT" \
  --out "$CAPTURE" \
  --seed 20261026 \
  --map Town03 \
  --warmup-ticks 20 \
  --ticks 100 \
  --step 0.1
du -sb "$CAPTURE" | tee "$REPORT/du.txt"
bytes=$(awk '{print $1}' "$REPORT/du.txt")
limit=$((2 * 1024 * 1024 * 1024))
if [ "$bytes" -ge "$limit" ]; then
  echo "capture ${bytes} bytes is not under 2 GiB" >&2
  exit 1
fi
cp -a "$CAPTURE/." "$REPORT/"
