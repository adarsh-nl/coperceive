# ADR 0002 — On-disk format

Status: proposed. The scaffold writes this layout. Adarsh can replace it before v0.1.

## Context

One Griffin-25m scene, `Town03-000`: 149 frames, 298 poses, 7,213 boxes, 1,341 image path refs, 437,760 points. ASCII PLY parse of that scene took 0.346 s in the format script. The question is how to store it so that “everyone at time t” and “one lidar across the scene” are both cheap, workers can read it, images stay as paths, and a new column does not break an old reader.

Scripts and numbers: `spikes/bench_formats.py`, `docs/design/measurements/format_results.json` (write time and size; some read timings in that file are not used), `docs/design/measurements/format_latency_v2.json` (the read timings below). The v1 read path could return without touching bytes for Arrow and the hybrid blob, and an MCAP query with `end_time == start_time` returned no messages because the end is exclusive. v2 forces a checksum of the values actually read, and sets MCAP `end_time = t + 1`. Checksums agree within float32.

Versions in that spike environment: PyArrow 26.0.0, zarr 3.4.1, h5py 3.16.0, mcap 1.5.0, NumPy 2.5.4. The library’s install range is older on Python 3.10. These timings are from 3.12.

## Options

| Format | Write | Bytes | All agents at t, median | One sensor across the scene, median |
| --- | ---: | ---: | ---: | ---: |
| Parquet, points as rows | 0.083 s | 5,833,195 | 7.65 ms | 3.13 ms |
| Arrow IPC, mmap | 0.0053 s | 9,813,120 | 1.36 ms | 0.49 ms |
| Zarr, padded to 3,083 points | 0.121 s | 5,426,572 | 1.96 ms | 43.3 ms |
| HDF5, gzip | 0.164 s | 5,280,602 | 0.66 ms | 36.4 ms |
| MCAP | 0.046 s | 5,223,873 | 1.62 ms | 11.0 ms |
| Hybrid Parquet + `points.bin` | 0.027 s | 7,489,011 | 2.29 ms | 0.55 ms |

Image path refs in the hybrid layout are 14,869 bytes. A full read of the hybrid blob was 0.67 ms median. Four spawn workers read the hybrid blob with 0 errors in 0.342 s (7,004,160 bytes in that worker check).

Schema check, same spike: a pose table grew a `quality` column. Reading a declared column subset still returned 298 rows. PyArrow does not fill a column the reader asks for if the file lacks it.

The full 25 m lidar zip is 214,487,013 bytes. Parsing all 7,000 ASCII PLY files into a float32 blob took 15.62 s and produced 19,922,961 points and 318,767,376 bytes. That blob was not committed.

## Criteria

- Random access to one timestamp across agents, and to one sensor across the scene.
- A worker process can open the file after `spawn`.
- Images are paths.
- An extra column is ignored.
- Something other than this library can read the tables.
- The schema version is explicit.

## Decision

Hybrid, as `pkg.store` writes it.

- `manifest.json` holds schema version, world frame, clock, agents, and sensors.
- `poses.parquet`, `annotations.parquet`, `objects.parquet`, `images.parquet`, `point_index.parquet`.
- `points.bin` is little-endian float32 `x, y, z, intensity`, addressed by offset, nbytes, and count.
- Readers that name a column list ignore extra columns. A different `schema_version` raises.

Arrow IPC is close on the sensor scan and faster to write, and larger. HDF5 wins the single-timestamp read and the byte size, and loses the full sensor scan by almost two orders of magnitude. MCAP is the smallest and is a reasonable export, not the working store. Zarr’s padded cube is slow to scan.

## Consequences

- Point random access is a seek plus a read, not a table scan.
- Parquet tooling can open the tables. The blob needs the index.
- mmap of the blob is available to callers. The library’s worker test uses a spawn process and `stat`, which is the DataLoader-shaped check that was actually run.
- A PyTorch `DataLoader` was not run. `python3 -c "import torch"` in the earlier environment printed that torch was absent, and torch was not installed for this scaffold. The command that was not run is a `DataLoader` with `num_workers > 0` over `read_points_at`.
- This choice is reversible before v0.1. Adarsh confirms or replaces it.
