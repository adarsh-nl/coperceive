# Where `dataset prepare` spends its time

This is measured with `scripts/bench/prepare_profile.py`. It builds one synthetic
OPV2V scene: 2 agents × 40 frames, ASCII `x y z intensity` PCD sweeps of 60 000
points, four 800×600 PNG cameras per agent and frame, numpy-tagged YAML
records, and 519 MB raw. The script then prepares it in one process, under
cProfile and without it. The run was on an Apple M-series laptop with a local
SSD, so the cluster's network filesystem is not in these numbers (see "Reading
the raw data" below).

```bash
python scripts/bench/prepare_profile.py               # cProfile, grouped
python scripts/bench/prepare_profile.py --no-profile  # wall and CPU only
```

## The profile (one scene, 80 sweeps, 320 images)

| phase | `--images originals` | `--images previews` |
| --- | --- | --- |
| lidar read/convert (ASCII PCD parse) | 3.25 s (65 %) | 3.5 s |
| records (YAML, numpy tags) | 0.78 s | 0.78 s |
| camera previews: PNG decode | – | 3.0 s |
| camera previews: resize + JPEG encode | – | 1.9 s |
| fingerprint of the raw split | 0.003 s (484 entries) | same |
| filesystem metadata, copy, write | < 0.1 s | < 0.2 s |

Times are cProfile's, which adds overhead. CPUs used versus requested: each
scene is single-threaded, so one scene uses one CPU. The old whole-dataset job
asked for `min(scenes, 8)` CPUs on one node and kept them all until the slowest
scene finished. For 33 scenes on 8 CPUs, that is five waves, and the last one
uses 1 of the 8 CPUs.

## What changed

1. **The ASCII sweep parse uses numpy's C parser.** It parses with the same
   rounding as Python `float`, so the bytes are identical
   (`tests/test_adapter_readers.py` covers `-0`, `nan`, `inf`, out-of-range
   values and 8 000 random spellings). A bad token falls back to the Python
   path and fails the same way as before.
2. **One 1-CPU Slurm array task per scene**, `--array=0-N%throttle`, with the
   throttle editable in the preview. A task starts as soon as one CPU is free
   anywhere; it no longer waits for an N-CPU allocation on one node, and no
   CPU sits idle in the last wave. Claims, locks, the preview script and its
   hash and the single confirm are unchanged. The lock also uses an atomic
   `mkdir`, because the tasks run on several nodes.

The fingerprint, metadata and copy costs are negligible and were left as
they are. PNG decoding is inherent to previews mode; originals mode does not
decode any images.

## Before and after (one scene, no profiler)

| | before | after |
| --- | --- | --- |
| `--images originals` | 4.87 s | 3.87 s (−21 %) |
| `--images previews` | 11.1 s | 9.4 s (−16 %) |
| fixed cost per task (Python start, scene listing, fingerprint) | – | 0.73 s |

## Expected wall time for 33 scenes

A real OPV2V/V2XSet scene has about 530 agent-frames: a 16-scene split held
about 34 000 camera previews, which is 2 140 images and 535 sweeps per scene.
Scaled from the table above, that is roughly 26 s of CPU per scene in
originals mode and 62 s in previews mode.

| | old: one job, 8 CPUs | new: array, `%8` | new: array, `%16` |
| --- | --- | --- | --- |
| originals | 5 waves × 32 s ≈ 2.7 min | 5 × 27 s ≈ 2.2 min | 3 × 27 s ≈ 1.3 min |
| previews | 5 × 74 s ≈ 6.2 min | 5 × 63 s ≈ 5.2 min | 3 × 63 s ≈ 3.2 min |

These estimates count CPU only; queue time comes on top. The array mainly
helps there: 1-CPU tasks fit into gaps that an 8-CPU request waits for.

### Reading the raw data

At these sizes a scene is about 3.4 GB of ASCII sweeps and PNGs, so 33 scenes
read about 110 GB. On a shared filesystem that delivers 1 GB/s in total, the
reading alone takes about 2 minutes, so it competes with the CPU time above.
More tasks at once help only until the filesystem is saturated. Start with
`%8`, and raise the throttle if the job's tasks show low CPU use in
`sacct -j <job> -o JobID,Elapsed,TotalCPU`.
