#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Fake ssh used by the HPC portability tests (Rust unit tests and the CI
# packaged-app e2e step). It never touches a network: it answers from committed
# cluster fixtures so the same detection flow is exercised for several profile
# shapes, including a ProxyJump alias and a server without a scheduler.
#
# Usage is the real ssh shape: fake_ssh.sh <ssh options> <destination> <command>
set -u

all_args="$*"
remote="${!#:-}"

case "${all_args}" in
    *"-G cluster-a"*)
        printf 'host cluster-a\nuser alice\nhostname cluster-a.example.org\nport 22\nproxyjump none\nproxycommand none\nidentityfile ~/.ssh/id_ed25519\n'
        exit 0
        ;;
    *"-G cluster-b"*)
        printf 'host cluster-b\nuser bob\nhostname cluster-b.internal.example.org\nport 22\nproxyjump jump.example.org\nproxycommand none\n'
        exit 0
        ;;
    *"-G cluster-none"*)
        printf 'host cluster-none\nuser carol\nhostname cluster-none.example.org\nport 2222\nproxyjump none\nproxycommand none\n'
        exit 0
        ;;
esac

case "${remote}" in
    *unreachable*)
        # Failure injection: the alias "unreachable" is not reachable.
        echo "ssh: connect to host cluster-x.example.org port 22: No route to host" >&2
        exit 255
        ;;
    *"Permission denied"*)
        echo "alice@cluster-a.example.org: Permission denied (publickey)." >&2
        exit 255
        ;;
esac

case "$*" in
    *"unreachable"*)
        echo "ssh: connect to host cluster-x.example.org port 22: No route to host" >&2
        exit 255
        ;;
    *"-G locked"*)
        # ssh -G never fails for a locked host; detection reaches it, then auth fails.
        printf 'host locked\nuser alice\nhostname cluster-a.example.org\nport 22\nproxyjump none\nproxycommand none\n'
        exit 0
        ;;
    *"locked"*)
        echo "alice@cluster-a.example.org: Permission denied (publickey)." >&2
        exit 255
        ;;
    *"-G "*)
        printf 'host unknown\nuser alice\nhostname unknown.example.org\nport 22\nproxyjump none\nproxycommand none\n'
        exit 0
        ;;
esac

case "${remote}" in
    true)
        exit 0
        ;;
    *"coperceive-check:scheduler"*)
        case "${all_args}" in
            *cluster-b*|*cluster-a*) echo slurm ;;
            *) echo none ;;
        esac
        exit 0
        ;;
    *"coperceive-check:sinfo"*)
        case "${all_args}" in
            *cluster-a*)
                printf 'gpu-a100*|up|2-00:00:00|8|gpu:a100:4\nmain-cpu|up|4:00:00|32|(null)\n'
                ;;
            *cluster-b*)
                printf 'main-cpu*|up|2-00:00:00|64|(null)\ngpu-h100|up|1-00:00:00|32|gpu:h100:2\n'
                ;;
            *)
                printf ''
                ;;
        esac
        exit 0
        ;;
    *"coperceive-check:environment"*)
        case "${all_args}" in
            *cluster-a*)
                cat <<'EOF'
MODULE=
LMOD=
CONDA=
CONDA_ENV=
VIRTUAL_ENV=/home/alice/venvs/coperceive
PYTHON=/home/alice/venvs/coperceive/bin/python
APPTAINER=
SINGULARITY=
COPERCEIVE=/home/alice/venvs/coperceive/bin/coperceive
SCRATCH=/scratch/alice
WORK=
TMPDIR=/tmp
HOME=/home/alice
EOF
                ;;
            *cluster-b*)
                cat <<'EOF'
MODULE=/usr/share/lmod/lmod/libexec/lmod
LMOD=/usr/share/lmod/lmod/libexec/lmod
CONDA=
CONDA_ENV=
VIRTUAL_ENV=
PYTHON=/usr/bin/python3
APPTAINER=
SINGULARITY=
COPERCEIVE=/apps/coperceive/bin/coperceive
SCRATCH=
WORK=/work/alice
TMPDIR=/tmp
HOME=/home/bob
EOF
                ;;
            *)
                cat <<'EOF'
MODULE=
LMOD=
CONDA=
CONDA_ENV=
VIRTUAL_ENV=
PYTHON=/usr/bin/python3
APPTAINER=
SINGULARITY=
COPERCEIVE=
SCRATCH=
WORK=
TMPDIR=/tmp
HOME=/home/carol
EOF
                ;;
        esac
        exit 0
        ;;
    *"coperceive-check:coperceive-version"*)
        case "${all_args}" in
            *cluster-a*) echo "coperceive 0.4.0" ;;
            *cluster-b*) echo "coperceive 0.4.1" ;;
            *) exit 1 ;;
        esac
        exit 0
        ;;
    *"coperceive-check:scratch"*)
        case "${all_args}" in
            *"/scratch/alice"*)
                printf 'WRITABLE\n/dev/sdb1 1024 10 500000 1%% /scratch\nDisk quotas for user alice (uid 1001):\n     Filesystem  blocks   quota   limit   grace   files   quota   limit   grace\n'
                ;;
            *"/work/alice"*)
                printf 'WRITABLE\n/dev/sdc1 1024 20 250000 1%% /work\n'
                ;;
            *"/tmp"*)
                printf 'WRITABLE\n/dev/sda1 1024 100 100000 1%% /\n'
                ;;
            *)
                printf 'NOT_WRITABLE\n'
                ;;
        esac
        exit 0
        ;;
esac

# --- Browse: `coperceive remote scan` -------------------------------------
# Metadata-only listings for the committed cluster fixtures. The scan bounds
# (depth/entries/time) are exercised by the Rust and Python unit tests; here the
# fake answers with the same JSON shape, including one truncated listing and one
# refused path so the UI's plain-language messages are covered end to end.
case "${remote}" in
    *"coperceive remote scan"*)
        case "${remote}" in
            *"coperceive remote scan"*"--root /data/files"*)
                cat <<'JSON'
{
  "status": "ok",
  "root": "/data/files",
  "path": "/data/files",
  "relative_path": ".",
  "depth": 2,
  "timeout_s": 10.0,
  "max_entries": 200,
  "max_total": 2000,
  "elapsed_ms": 3.5,
  "truncated": false,
  "truncated_reason": null,
  "folder_count": 2,
  "scene_count": 1,
  "dataset_count": 0,
  "folders": [
    {"path": ".", "depth": 0, "is_scene": false, "scene_id": null, "dataset": false,
     "scene_count": 0, "has_manifest": false, "has_complete": false,
     "subfolder_count": 1, "file_count": 0},
    {"path": "Files-001", "depth": 1, "is_scene": true, "scene_id": "Files-001",
     "dataset": false, "scene_count": 0, "has_manifest": true, "has_complete": true,
     "subfolder_count": 0, "file_count": 4}
  ],
  "scenes": [{"path": "Files-001", "scene_id": "Files-001", "folder_name": "Files-001"}],
  "datasets": [],
  "skipped": []
}
JSON
                exit 0
                ;;
            *"--root /data/scenes"*"--path datasets"*)
                cat <<'JSON'
{
  "status": "ok",
  "root": "/data/scenes",
  "path": "/data/scenes/datasets",
  "relative_path": "datasets",
  "depth": 2,
  "timeout_s": 10.0,
  "max_entries": 200,
  "max_total": 2000,
  "elapsed_ms": 9.0,
  "truncated": true,
  "truncated_reason": "entry limit",
  "folder_count": 4,
  "scene_count": 2,
  "dataset_count": 1,
  "folders": [
    {"path": "datasets", "depth": 0, "is_scene": false, "scene_id": null, "dataset": true,
     "scene_count": 2, "has_manifest": false, "has_complete": false,
     "subfolder_count": 3, "file_count": 1},
    {"path": "datasets/Town03-007", "depth": 1, "is_scene": true, "scene_id": "Town03-007",
     "dataset": false, "scene_count": 0, "has_manifest": true, "has_complete": true,
     "subfolder_count": 0, "file_count": 7},
    {"path": "datasets/Town06-002", "depth": 1, "is_scene": true, "scene_id": "Town06-002",
     "dataset": false, "scene_count": 0, "has_manifest": true, "has_complete": true,
     "subfolder_count": 0, "file_count": 7},
    {"path": "datasets/notes", "depth": 1, "is_scene": false, "scene_id": null,
     "dataset": false, "scene_count": 0, "has_manifest": false, "has_complete": false,
     "subfolder_count": 0, "file_count": 2}
  ],
  "scenes": [
    {"path": "datasets/Town03-007", "scene_id": "Town03-007", "folder_name": "Town03-007"},
    {"path": "datasets/Town06-002", "scene_id": "Town06-002", "folder_name": "Town06-002"}
  ],
  "datasets": [{"path": "datasets", "scene_count": 2}],
  "skipped": [{"path": "datasets/camera-link", "reason": "symlink target is outside the data root"}]
}
JSON
                exit 0
                ;;
            *"--root /data/scenes"*"--path escape-link"*)
                cat <<'JSON'
{"status": "error", "error": "the path is a symlink that points outside the data root: /data/scenes/escape-link -> /etc"}
JSON
                exit 1
                ;;
            *"--root /data/scenes"*"--path loose-folder"*)
                cat <<'JSON'
{
  "status": "ok",
  "root": "/data/scenes",
  "path": "/data/scenes/loose-folder",
  "relative_path": "loose-folder",
  "depth": 2,
  "timeout_s": 10.0,
  "max_entries": 200,
  "max_total": 2000,
  "elapsed_ms": 1.5,
  "truncated": false,
  "truncated_reason": null,
  "folder_count": 1,
  "scene_count": 0,
  "dataset_count": 0,
  "folders": [
    {"path": "loose-folder", "depth": 0, "is_scene": false, "scene_id": null,
     "dataset": false, "scene_count": 0, "has_manifest": false, "has_complete": false,
     "subfolder_count": 0, "file_count": 3}
  ],
  "scenes": [],
  "datasets": [],
  "skipped": []
}
JSON
                exit 0
                ;;
            *"--root /data/scenes"*)
                cat <<'JSON'
{
  "status": "ok",
  "root": "/data/scenes",
  "path": "/data/scenes",
  "relative_path": ".",
  "depth": 2,
  "timeout_s": 10.0,
  "max_entries": 200,
  "max_total": 2000,
  "elapsed_ms": 6.0,
  "truncated": false,
  "truncated_reason": null,
  "folder_count": 3,
  "scene_count": 1,
  "dataset_count": 1,
  "folders": [
    {"path": ".", "depth": 0, "is_scene": false, "scene_id": null, "dataset": true,
     "scene_count": 1, "has_manifest": false, "has_complete": false,
     "subfolder_count": 3, "file_count": 0},
    {"path": "Town03-000", "depth": 1, "is_scene": true, "scene_id": "Town03-000",
     "dataset": false, "scene_count": 0, "has_manifest": true, "has_complete": true,
     "subfolder_count": 0, "file_count": 7},
    {"path": "datasets", "depth": 1, "is_scene": false, "scene_id": null, "dataset": true,
     "scene_count": 1, "has_manifest": false, "has_complete": false,
     "subfolder_count": 3, "file_count": 0}
  ],
  "scenes": [{"path": "Town03-000", "scene_id": "Town03-000", "folder_name": "Town03-000"}],
  "datasets": [{"path": ".", "scene_count": 1}, {"path": "datasets", "scene_count": 1}],
  "skipped": []
}
JSON
                exit 0
                ;;
        esac
        echo "fake_ssh: unsupported scan target: ${remote}" >&2
        exit 127
        ;;
esac

echo "fake_ssh: unsupported command: ${remote}" >&2
exit 127
