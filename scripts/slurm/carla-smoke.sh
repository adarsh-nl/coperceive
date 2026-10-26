#!/bin/bash
#SBATCH --job-name=carla-smoke
#SBATCH --partition=ps
#SBATCH --nodelist=hpc-node11
#SBATCH --gres=gpu:lovelace:1
#SBATCH --cpus-per-task=4
#SBATCH --mem=32G
#SBATCH --time=00:15:00
#SBATCH --output=/home/nanjaiyalathaa/logs/%x-%j.out
#SBATCH --error=/home/nanjaiyalathaa/logs/%x-%j.err

# One Town03 lidar vehicle on the node from job 641301.
# The venv python imports carla after `module load python/3.10.7`
# (job 641370). Without that module it starts as system Python 3.10.12.

set -euo pipefail

ROOT="/local/nanjaiyalathaa/carla-0.9.15"
PY="$ROOT/py310/bin/python"
REPORT="/home/nanjaiyalathaa/results/carla-smoke-${SLURM_JOB_ID}"

mkdir -p "$REPORT" /home/nanjaiyalathaa/logs
hostname -s | tee "$REPORT/node.txt"
test -x "$ROOT/CarlaUE4.sh"
test -x "$PY"

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
server_start_ns=$(date +%s%N)
echo "server_start_unix_ns=$server_start_ns" | tee "$REPORT/server-start.txt"
./CarlaUE4.sh -RenderOffScreen -carla-rpc-port="$PORT" -nosound \
  >"$REPORT/server.log" 2>&1 &
server=$!
cleanup() {
  if kill -0 "$server" 2>/dev/null; then
    kill "$server" 2>/dev/null || true
    wait "$server" 2>/dev/null || true
  fi
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
port_open_ns=$(date +%s%N)
echo "port_open_unix_ns=$port_open_ns" | tee "$REPORT/port-open.txt"
"$PY" -c "print('port_wait_s=' + format(($port_open_ns - $server_start_ns) / 1e9, '.3f'))" | tee "$REPORT/port-wait.txt"

cat >"$REPORT/client.py" <<'PY'
import sys
import queue
import carla

port = int(sys.argv[1])
out_path = sys.argv[2]
client = carla.Client("127.0.0.1", port)
client.set_timeout(60.0)
print("server_version=" + client.get_server_version())
world = client.get_world()
print("map_before=" + world.get_map().name)
world = client.load_world("Town03")
print("map_after=" + world.get_map().name)

settings = world.get_settings()
settings.synchronous_mode = True
settings.fixed_delta_seconds = 0.1
world.apply_settings(settings)

library = world.get_blueprint_library()
vehicle_bp = sorted(library.filter("vehicle.*"), key=lambda bp: bp.id)[0]
spawn = world.get_map().get_spawn_points()[0]
vehicle = world.spawn_actor(vehicle_bp, spawn)
print("vehicle=" + vehicle.type_id)

lidar_bp = library.find("sensor.lidar.ray_cast")
lidar_bp.set_attribute("channels", "32")
lidar_bp.set_attribute("range", "50")
lidar_bp.set_attribute("points_per_second", "56000")
lidar_bp.set_attribute("rotation_frequency", "10")
lidar_bp.set_attribute("upper_fov", "10")
lidar_bp.set_attribute("lower_fov", "-30")
lidar_bp.set_attribute("sensor_tick", "0.0")
lidar_bp.set_attribute("dropoff_general_rate", "0.0")
lidar = world.spawn_actor(
    lidar_bp,
    carla.Transform(carla.Location(z=2.4)),
    attach_to=vehicle,
)

pending = queue.Queue()
lidar.listen(pending.put)
rows = []
try:
    for tick in range(50):
        frame = world.tick()
        measurement = pending.get(timeout=30.0)
        rows.append((tick, frame, measurement.frame, len(measurement)))
finally:
    lidar.stop()
    if lidar.is_alive:
        lidar.destroy()
    if vehicle.is_alive:
        vehicle.destroy()
    settings.synchronous_mode = False
    world.apply_settings(settings)

with open(out_path, "w", encoding="utf-8") as handle:
    handle.write("tick world_frame lidar_frame points\n")
    for tick, frame, lidar_frame, points in rows:
        handle.write(f"{tick} {frame} {lidar_frame} {points}\n")
print(f"lidar_rows={len(rows)}")
PY

"$PY" "$REPORT/client.py" "$PORT" "$REPORT/lidar-counts.txt" | tee "$REPORT/client.txt"
