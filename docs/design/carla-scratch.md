# CARLA scratch on this cluster

`/local` is a separate disk on each node. It is not visible from the login node or from any other compute node. The cluster wiki says files there are removed after 90 days without a change (https://hpc.wiki.utwente.nl/, checked 2026-10-26).

`scripts/slurm/carla-fetch.sh` writes the node name to `~/results/carla-fetch-<jobid>/node.txt`. Every later CARLA job has to set `#SBATCH --nodelist=` to that hostname. A job on another node will not see the unpacked tree.

The staged tarball stays at `~/carla-staging/CARLA_0.9.15.tar.gz` until the GPU smoke test has passed. The job deletes only the copy under `/local/nanjaiyalathaa/carla-0.9.15/`.
