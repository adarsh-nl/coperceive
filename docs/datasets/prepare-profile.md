# Where `dataset prepare` spends its time

## On the cluster: reading is latency-bound

DeepSeek's timing script (#111) ran as job 670409 on partition `ps`, with
1 CPU. It timed one V2XSet train scene, the first in the listing (probably
`2021_08_18_19_11_02`: 4 agents, 852 lidar samples, 3,408 camera frames).

| stage | size | time | rate |
| --- | --- | --- | --- |
| raw read, **cold** (PCD + PNG) | 3.80 GB, ~4,260 files | **665 s** | 6.4 files/s, 5.4 MiB/s |
| raw read, warm (same node) | 3.80 GB | 4.5 s | 937 files/s, 797 MiB/s |
| parse PCD | 852 files, 1.79 GB | 21.8 s | 23.5 ms/file |
| parse YAML | 852 files, 15.5 MB | 25.8 s | 23 ms/file (~18 KB each) |
| preview JPEG | 200 sampled frames | 4.6 s | 20 ms/frame (≈ 69 s for 3,408) |
| write scene | 531 MB | 1.6 s | |

- **Cold reads dominate.** They take about 11 minutes for the scene's files,
  against about 2 minutes of CPU. The limit is not bandwidth: the same files
  read warm at 800 MiB/s. It is about 150 ms of per-file latency on the shared
  filesystem, and a single sequential reader spends almost all its time
  waiting.
- **The old code read sequentially.** It read every record and sweep in turn,
  so each scene paid that latency file by file. Previews mode then read every
  PNG the same way. This is where the real 1 h 40 min for 33 scenes went.
- **The synthetic profile could not show this.** It ran on a laptop's local
  SSD.

### YAML

- **Each record was parsed twice.** Prepare parsed every per-frame record
  once for the poses and once again for the boxes, so it paid 2 × 23 ms per
  record (the timing script parses once).
- **libyaml was most likely in use.** On an M-series Mac an 18 KB OPV2V-sized
  record takes 8.4 ms with libyaml (`yaml.CSafeLoader`) and 73 ms with
  PyYAML's pure-Python parser. 23 ms on a slower cluster CPU fits libyaml;
  pure Python would be several times slower.
- **What remains is Python-side.** PyYAML builds the objects in Python even
  with libyaml.
- **The loader now says which it uses.** It logs a warning when libyaml is
  missing, and every scene's timing line records `yaml_loader`, so the next
  run confirms it.

## What changed

| change | why | where |
| --- | --- | --- |
| Raw files are read ahead of the parser, 16 at a time per task (`COPERCEIVE_IO_THREADS`, set in the prepare script), in the order the build consumes them, at most 256 MB ahead | per-file latency overlaps instead of adding up | `coperceive/adapters/prefetch.py`, `build.py` |
| Each per-frame record is parsed once per build (poses, sensors and boxes share it) | half the YAML time | `ManifestAdapter._record` |
| Camera previews are read and encoded 16 at a time | previews mode reads every PNG | `coperceive/camera_images.py` |
| "Read originals" reads no PNG at all: it records paths and links the raw root (checked: no open or stat of any PNG during prepare) | PNGs are most of the files and half the bytes | unchanged, verified |
| The ASCII PCD parse uses numpy's C parser, byte-identical | 65 % of CPU in the synthetic profile | `readers/lidar.py` |
| One 1-CPU Slurm array task per scene; the default `%N` is a filesystem budget (128 reads in flight = 8 tasks × 16), not a CPU count | tasks run across nodes; the filesystem is the shared limit | `dataset_jobs.rs` |
| The prepare preview estimates the wall time: files × 150 ms ÷ 16 reads at once versus CPU (46.5 ms per sweep, 20 ms per preview), plus 2 s to write per scene. Once the cache has a timing log, the median seconds per raw file of the last run's scenes in that mode is used instead | an honest number before Submit | `estimate_seconds` |
| Every scene writes a timing line (read wait, build, write, previews, total, CPU, files, MB, I/O threads, YAML loader) to the job log (`coperceive-timing {...}`) and to `<cache>/.timing.jsonl` | the next run explains itself and feeds the estimate | `cluster_datasets.py` |

Simulating the cluster's cold reads locally (`scripts/bench/prepare_profile.py
--cold-ms 150`, which delays the first read of each raw file by 150 ms) on a
2-agent, 40-frame synthetic scene:

| | sequential (`--io-threads 0`) | 16 at a time |
| --- | --- | --- |
| originals | 32.6 s | 5.7 s (CPU-bound) |
| previews | 67.3 s | 9.0 s |

## Expected wall time for the 33-scene split, new defaults

This assumes scenes like the measured one (852 sweeps, 3,408 camera frames)
and that 128 concurrent reads still see about 150 ms each, which is not yet
measured.

| | per scene | 33 scenes at `%8` |
| --- | --- | --- |
| before (one job, 8 processes, sequential reads), originals | 1,704 files × 150 ms + 73 s CPU ≈ 5.5 min | ≈ 25–30 min |
| before, previews | + 3,408 PNGs × 150 ms + 69 s ≈ 15 min | ≈ 1 h 15 min plus queueing (the real run took 1 h 40 min) |
| **now, originals** | reads 16 s, overlapped with 40 s CPU → ≈ 42 s | **≈ 3 min** |
| **now, previews** | reads 48 s, overlapped with 108 s CPU → ≈ 110 s | **≈ 8 min** |

These are estimates until a real run confirms them. The next run's timing
lines and DeepSeek's script show whether reads keep scaling at 128 in flight.
If they don't, `read_wait_s` grows and the preview's estimate, which learns
from that log, grows with it. In that case, lower "Scenes at once" or
`COPERCEIVE_IO_THREADS`.

## The synthetic CPU profile (local SSD)

`scripts/bench/prepare_profile.py` without `--cold-ms`: one synthetic OPV2V
scene (2 agents × 40 frames, 60 000-point ASCII PCDs, four 800×600 PNG cameras
per frame, 519 MB).

| phase | originals | previews |
| --- | --- | --- |
| lidar read/convert (ASCII parse) | 3.25 s (65 %) | 3.5 s |
| YAML records | 0.78 s | 0.78 s |
| PNG decode | – | 3.0 s |
| resize + JPEG encode | – | 1.9 s |
| fingerprint | 0.003 s | same |

The ASCII parse change took the scene from 4.87 to 3.87 s (originals) and
from 11.1 to 9.4 s (previews). The fixed cost per array task (Python start,
scene listing, fingerprint) is 0.73 s.
