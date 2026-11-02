#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Fake ssh for the packaged-app cluster-dataset e2e
# (scripts/desktop/e2e_cluster_datasets_app.sh). The "cluster" is this machine:
#
# - `<coperceive argv> dataset detect|index|status ...` and the capability probe
#   run locally (the profile's coperceive argv is the bundled Python, or
#   fake_old_python.sh for the "too old" case).
# - `sbatch --parsable` runs the submitted script to completion with a fake
#   SLURM_JOB_ID, so the job really runs `coperceive dataset prepare`; squeue
#   then reports nothing and sacct reports COMPLETED/FAILED.
# - the viewer-page upload commands run with HOME=$FAKE_REMOTE_HOME.
# - `coperceive remote start|stop` and the `-N -L` tunnel are served by
#   fake_remote.py / tcp_forward.py (a real `coperceive serve` on 127.0.0.1).
#
# Everything else (ssh -G, probes) is answered by the shared HPC fixture.
# No network is used. Required env: FAKE_REMOTE_HOME, FAKE_CLUSTER_PYTHON.
set -u

here="$(cd "$(dirname "$0")" && pwd)"
base_fake="${here}/../hpc/fake_ssh.sh"
remote="${!#:-}"
home="${FAKE_REMOTE_HOME:?FAKE_REMOTE_HOME is required}"
python="${FAKE_CLUSTER_PYTHON:?FAKE_CLUSTER_PYTHON is required}"
slurm="${home}/.fake-slurm"
mkdir -p "${slurm}"

log_command() {
    if [ -n "${FAKE_SSH_LOG:-}" ]; then
        printf '%s\n' "$1" >> "${FAKE_SSH_LOG}"
    fi
}

# Tunnel: `ssh ... -N -L 127.0.0.1:<local>:<host>:<port> <alias>`.
tunnel=0
spec=""
previous=""
for arg in "$@"; do
    [ "${arg}" = "-N" ] && tunnel=1
    [ "${previous}" = "-L" ] && spec="${arg}"
    previous="${arg}"
done
if [ "${tunnel}" = "1" ] && [ -n "${spec}" ]; then
    log_command "tunnel ${spec}"
    IFS=: read -r local_host local_port remote_host remote_port <<< "${spec}"
    exec "${python}" -I "${here}/tcp_forward.py" \
        "${local_host}" "${local_port}" "${remote_host}" "${remote_port}"
fi

case "${remote}" in
    "sh -c '# coperceive-check:capabilities"*)
        # Answered by whatever coperceive the profile names (new or fake_old_python.sh).
        log_command "capabilities"
        cd "${home}" || exit 1  # a real ssh session starts in the remote home
        HOME="${home}" exec sh -c "${remote}"
        ;;
    *" dataset detect "*|*" dataset index "*|*" dataset status "*)
        verb="${remote#* dataset }"
        log_command "dataset-${verb%% *}"
        HOME="${home}" exec sh -c "${remote}"
        ;;
    "sbatch --parsable")
        log_command "sbatch"
        count_file="${slurm}/next-id"
        job_id=$(( $(cat "${count_file}" 2>/dev/null || echo 700000) + 1 ))
        printf '%s\n' "${job_id}" > "${count_file}"
        cat > "${slurm}/${job_id}.sh"
        status=0
        SLURM_JOB_ID="${job_id}" HOME="${home}" bash "${slurm}/${job_id}.sh" \
            > "${slurm}/${job_id}.out" 2> "${slurm}/${job_id}.err" || status=$?
        printf '%s\n' "${status}" > "${slurm}/${job_id}.exit"
        printf '%s;fake-cluster\n' "${job_id}"
        exit 0
        ;;
    "squeue -h -j 7"[0-9][0-9][0-9][0-9][0-9]" "*)
        # The fake job ran to completion inside sbatch: no longer queued.
        log_command "squeue"
        exit 0
        ;;
    "sacct -j 7"[0-9][0-9][0-9][0-9][0-9]" "*)
        log_command "sacct"
        job_id="${remote#sacct -j }"
        job_id="${job_id%% *}"
        if [ "$(cat "${slurm}/${job_id}.exit" 2>/dev/null)" = "0" ]; then
            echo COMPLETED
        else
            echo FAILED
        fi
        exit 0
        ;;
    "# coperceive-static-verify"*|"sh -c 'mkdir -p -- \"\$HOME/"*|"sh -c 'cat > \"\$HOME/"*)
        log_command "static"
        HOME="${home}" exec sh -c "${remote}"
        ;;
    *" remote start "*|*" remote stop "*|*" remote status "*|*" remote token "*)
        verb="${remote#* remote }"
        log_command "remote-${verb%% *}"
        exec "${python}" -I "${here}/fake_remote.py" "${remote}"
        ;;
esac

exec "${base_fake}" "$@"
