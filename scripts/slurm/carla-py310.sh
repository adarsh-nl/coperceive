#!/bin/bash
#SBATCH --job-name=carla-py310
#SBATCH --partition=ps
#SBATCH --nodelist=hpc-node11
#SBATCH --cpus-per-task=2
#SBATCH --mem=8G
#SBATCH --time=00:30:00
#SBATCH --output=/home/nanjaiyalathaa/logs/%x-%j.out
#SBATCH --error=/home/nanjaiyalathaa/logs/%x-%j.err

# Venv on the node from results/carla-fetch-641301/node.txt.
# /local is not shared. This job does not use the network.

set -euo pipefail

ROOT="/local/nanjaiyalathaa/carla-0.9.15"
VENV="$ROOT/py310"
REPORT="/home/nanjaiyalathaa/results/carla-py310-${SLURM_JOB_ID}"

mkdir -p "$REPORT" /home/nanjaiyalathaa/logs
hostname -s | tee "$REPORT/node.txt"
test -x "$ROOT/CarlaUE4.sh"

source /etc/profile.d/modules.sh
module load python/3.10.7
python3 -m venv "$VENV"
"$VENV/bin/python" -m pip install --no-index \
  --find-links /home/nanjaiyalathaa/wheelhouse \
  'carla==0.9.15' 'numpy==1.26.4'
"$VENV/bin/python" -c "import carla; print(carla.__file__)" | tee "$REPORT/carla-file.txt"
"$VENV/bin/python" -c "import numpy, sys; print(sys.version); print(numpy.__version__)" | tee "$REPORT/versions.txt"
"$VENV/bin/python" -m pip freeze | tee "$REPORT/freeze.txt"

# The two lines above are the approved checks. These probes record whether
# the venv still starts after the module environment is removed.
set +e
env -u PYTHONHOME -u PYTHONPATH -u LD_LIBRARY_PATH \
  "$VENV/bin/python" -c 'import sys; print(sys.version)' \
  >"$REPORT/bare.txt" 2>"$REPORT/bare.err"
echo "bare_exit=$?" | tee "$REPORT/bare-status.txt"
env -u PYTHONHOME -u PYTHONPATH \
  LD_LIBRARY_PATH=/deepstore/software/python/3.10.7/lib \
  "$VENV/bin/python" -c 'import sys; print(sys.version)' \
  >"$REPORT/ldpath.txt" 2>"$REPORT/ldpath.err"
echo "ldpath_exit=$?" | tee "$REPORT/ldpath-status.txt"
set -e
