#!/bin/bash
#SBATCH --job-name=carla-fetch
#SBATCH --partition=ps
#SBATCH --cpus-per-task=2
#SBATCH --mem=16G
#SBATCH --time=03:00:00
#SBATCH --output=/home/nanjaiyalathaa/logs/%x-%j.out
#SBATCH --error=/home/nanjaiyalathaa/logs/%x-%j.err

# Copy a staged CARLA 0.9.15 tarball onto this node's /local and unpack it.
# /local is not shared. docs/design/carla-scratch.md says to pin later jobs
# with --nodelist to the hostname written in node.txt.
# Does not use the network. Does not delete ~/carla-staging.

set -euo pipefail

EXPECT_BYTES=8386636048
# Measured earlier on a local copy and recorded in docs/adr/0008-hd-map.md.
# CARLA does not publish this digest.
EXPECT_SHA256=7b2f432ce74b251593f95dcc8dee6d99ad9625c2a71a2d6d48f24d879de19ef7
STAGED="/home/nanjaiyalathaa/carla-staging/CARLA_0.9.15.tar.gz"
ROOT="/local/nanjaiyalathaa/carla-0.9.15"
REPORT="/home/nanjaiyalathaa/results/carla-fetch-${SLURM_JOB_ID}"
MIN_FREE=$((40 * 1024 * 1024 * 1024))

mkdir -p "$ROOT" "$REPORT" /home/nanjaiyalathaa/logs
hostname -s | tee "$REPORT/node.txt"
df -B1 /local | tee "$REPORT/df-start.txt"
free_bytes=$(df -B1 --output=avail /local | tail -1 | tr -d ' ')
echo "free_bytes=$free_bytes" | tee "$REPORT/free.txt"
if [ "$free_bytes" -lt "$MIN_FREE" ]; then
  echo "free_bytes=$free_bytes below $MIN_FREE" >&2
  exit 1
fi

archive="$ROOT/CARLA_0.9.15.tar.gz"
cp -- "$STAGED" "$archive"
actual=$(stat -c %s "$archive")
echo "bytes=$actual" | tee "$REPORT/bytes.txt"
if [ "$actual" != "$EXPECT_BYTES" ]; then
  echo "size $actual != $EXPECT_BYTES" >&2
  exit 1
fi

printf '%s  %s\n' "$EXPECT_SHA256" "$archive" > "$REPORT/sha256.expected"
if sha256sum -c "$REPORT/sha256.expected" | tee "$REPORT/sha256.txt"; then
  :
else
  got=$(sha256sum "$archive" | awk '{print $1}')
  echo "digest mismatch: $got" | tee -a "$REPORT/sha256.txt" >&2
  exit 1
fi

tar -xzf "$archive" -C "$ROOT"
test -x "$ROOT/CarlaUE4.sh"
rm -f "$archive"
du -sb "$ROOT" | tee "$REPORT/du.txt"
df -B1 /local | tee "$REPORT/df-end.txt"
echo "done node=$(hostname -s)"
