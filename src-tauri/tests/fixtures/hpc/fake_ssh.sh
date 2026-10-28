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

echo "fake_ssh: unsupported command: ${remote}" >&2
exit 127
