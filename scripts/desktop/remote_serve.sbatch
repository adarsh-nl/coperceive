#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# Partition, account, QoS, time, CPU and memory are supplied by the caller's
# server profile as sbatch flags; this script has no site defaults. Omitted flags
# fall back to the cluster's own partition defaults.
#SBATCH --signal=B:SIGTERM@30

# Remote coperceive serve session on Slurm compute node
# Runs coperceive serve bound to the node's cluster IP with token authentication.

set -euo pipefail

UUID="${COPERCEIVE_REMOTE_UUID:-$(python3 -c 'import uuid; print(uuid.uuid4())')}"
DATA_ROOT="${COPERCEIVE_REMOTE_ROOT:-}"
TOKEN_FILE="${COPERCEIVE_REMOTE_TOKEN_FILE:-}"

if [ -z "${DATA_ROOT}" ]; then
    echo "Error: COPERCEIVE_REMOTE_ROOT is required" >&2
    exit 1
fi

if [ -z "${TOKEN_FILE}" ] || [ ! -f "${TOKEN_FILE}" ]; then
    echo "Error: Valid COPERCEIVE_REMOTE_TOKEN_FILE is required" >&2
    exit 1
fi

SESSIONS_DIR="${HOME}/.coperceive/sessions"
LOGS_DIR="${HOME}/.coperceive/logs"
mkdir -p -m 0700 "${SESSIONS_DIR}" "${LOGS_DIR}"

SESSION_FILE="${SESSIONS_DIR}/${UUID}.json"

# Resolve compute node cluster IP (first non-loopback IP)
NODE_IP=$(hostname -I | awk '{print $1}')
if [ -z "${NODE_IP}" ]; then
    NODE_IP="127.0.0.1"
fi

# Allocate a free loopback/cluster port
PORT=$(python3 -c 'import socket; s=socket.socket(); s.bind(("", 0)); print(s.getsockname()[1]); s.close()')

# Resolve Python interpreter: the server profile's chosen interpreter first,
# then whatever python3 the compute node provides. Never install anything here.
if [ -n "${COPERCEIVE_REMOTE_PYTHON:-}" ] && [ -x "${COPERCEIVE_REMOTE_PYTHON}" ]; then
    PYTHON_BIN="${COPERCEIVE_REMOTE_PYTHON}"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v python3)"
else
    echo "Error: no Python 3 interpreter was found on this compute node. Set the interpreter in the server profile, or ask your cluster administrators." >&2
    exit 1
fi

if [ -n "${COPERCEIVE_REPO_ROOT:-}" ] && [ -d "${COPERCEIVE_REPO_ROOT}/coperceive" ]; then
    export PYTHONPATH="${COPERCEIVE_REPO_ROOT}:${PYTHONPATH:-}"
fi

echo "Starting coperceive remote serve on ${NODE_IP}:${PORT} (job ${SLURM_JOB_ID:-none}, uuid ${UUID})..."

# Heartbeat lease configuration (default: 600s / 10 min, 0 to disable)
HEARTBEAT_TIMEOUT="${COPERCEIVE_HEARTBEAT_TIMEOUT:-600}"
HEARTBEAT_FILE="${SESSIONS_DIR}/${UUID}.heartbeat"
touch "${HEARTBEAT_FILE}"
chmod 0600 "${HEARTBEAT_FILE}"

cleanup() {
    trap - SIGTERM SIGINT EXIT
    echo "Caught signal, stopping remote coperceive serve (PID ${SERVE_PID:-none})..."
    if [ -n "${WATCHDOG_PID:-}" ] && kill -0 "${WATCHDOG_PID}" 2>/dev/null; then
        kill "${WATCHDOG_PID}" 2>/dev/null || true
    fi
    if [ -n "${SERVE_PID:-}" ] && kill -0 "${SERVE_PID}" 2>/dev/null; then
        kill -TERM "${SERVE_PID}" 2>/dev/null || true
        for _ in {1..25}; do
            if ! kill -0 "${SERVE_PID}" 2>/dev/null; then
                break
            fi
            sleep 0.2
        done
        kill -KILL "${SERVE_PID}" 2>/dev/null || true
    fi
    if [ -f "${SESSION_FILE}" ]; then
        rm -f "${SESSION_FILE}"
    fi
    if [ -f "${TOKEN_FILE}" ]; then
        rm -f "${TOKEN_FILE}"
    fi
    if [ -f "${HEARTBEAT_FILE}" ]; then
        rm -f "${HEARTBEAT_FILE}"
    fi
    echo "Remote session cleaned up."
    exit 0
}
trap cleanup SIGTERM SIGINT EXIT

# The desktop app uploads its built viewer bundle to a per-version cache dir
# under the login home (shared with compute nodes on this cluster) and passes it
# as COPERCEIVE_REMOTE_STATIC; serve it so Remote mode has a page without Node
# or a build on the login node.
STATIC_ARGS=()
if [ -n "${COPERCEIVE_REMOTE_STATIC:-}" ]; then
    if [ ! -f "${COPERCEIVE_REMOTE_STATIC}/index.html" ]; then
        echo "Remote viewer page missing at ${COPERCEIVE_REMOTE_STATIC}" >&2
        exit 1
    fi
    STATIC_ARGS=(--static "${COPERCEIVE_REMOTE_STATIC}")
fi

# Launch server binding to cluster IP with token-file
# Note: Token is in a 0600 file and NEVER on argv
"${PYTHON_BIN}" -m coperceive serve \
    --host "${NODE_IP}" \
    --port "${PORT}" \
    --token-file "${TOKEN_FILE}" \
    --root "${DATA_ROOT}" \
    ${STATIC_ARGS[@]+"${STATIC_ARGS[@]}"} \
    ${COPERCEIVE_EXTRA_ARGS:-} &
SERVE_PID=$!

# Write 0600 session file
touch "${SESSION_FILE}"
chmod 0600 "${SESSION_FILE}"
NOW_TS=$(date +%s)
cat <<EOF > "${SESSION_FILE}"
{
  "uuid": "${UUID}",
  "job_id": ${SLURM_JOB_ID:-0},
  "node": "${SLURMD_NODENAME:-$(hostname)}",
  "ip": "${NODE_IP}",
  "port": ${PORT},
  "pid": ${SERVE_PID},
  "started_at": ${NOW_TS},
  "last_heartbeat": ${NOW_TS},
  "data_root": "${DATA_ROOT}",
  "token_file": "${TOKEN_FILE}"
}
EOF

echo "Session file created: ${SESSION_FILE} (mode 0600)"

# Background watchdog for heartbeat lease timeout
if [ "${HEARTBEAT_TIMEOUT}" -gt 0 ] 2>/dev/null; then
    (
        while true; do
            sleep 5
            if [ -f "${HEARTBEAT_FILE}" ]; then
                LAST_HB=$(stat -c %Y "${HEARTBEAT_FILE}" 2>/dev/null || stat -f %m "${HEARTBEAT_FILE}" 2>/dev/null || date +%s)
                NOW=$(date +%s)
                AGE=$((NOW - LAST_HB))
                if [ "${AGE}" -ge "${HEARTBEAT_TIMEOUT}" ]; then
                    echo "Heartbeat lease expired (${AGE}s >= ${HEARTBEAT_TIMEOUT}s), stopping serve..."
                    cleanup
                    exit 0
                fi
            else
                echo "Heartbeat file removed, stopping serve..."
                cleanup
                exit 0
            fi
        done
    ) &
    WATCHDOG_PID=$!
fi

# Wait for server process
wait "${SERVE_PID}"
