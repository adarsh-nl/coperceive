# Cluster datasets — backend contract

Status: **frozen for the UI** (2026-10-31). These JSON shapes are the contract
between the Coperceive CLI/desktop backend and the Dataset view. They are
versioned by the `schema` string; additive fields may appear, existing fields do
not change meaning.

A **cluster dataset** is a raw dataset tree on the server plus a per-dataset
cache under the profile's `results_root`. The raw tree is only read, never
written. Detection and indexing are metadata-only and bounded; preparation is a
CPU Slurm job that writes schema-1 scenes into the cache.

## Commands

```text
coperceive dataset detect --path P
coperceive dataset index  --adapter A --raw-root R [--split S]
coperceive dataset prepare --adapter A --raw-root R [--split S] --cache C [--scenes ids | --all] --workers N [--images originals|previews]
coperceive dataset status --cache C
coperceive dataset previews --cache-root <results_root>/coperceive-cache [--remove]
```

`detect` and `index` print one JSON document and write nothing. `prepare` and
`status` are PR 2; their schemas are declared here now so the UI can be built
against them.

### Choosing the split

OPV2V and V2XSet have splits (`train`, `validate`, `test`, and OPV2V's
`test_culver_city`). `index` and `prepare` work on exactly one split:

- `--split S` names it. `R` may be the dataset root (`…/opv2v`) or the split
  directory itself (`…/opv2v/test`); with the split directory, `S` must match
  its name.
- Without `--split`, `R` must be a split directory, or a dataset root with only
  one split present. A root with several splits is refused with a plain error
  that lists them, e.g. `/datasets/…/opv2v holds several splits (train,
  validate, test); choose one`. The command never picks one silently.
- `--split` with an adapter that has no splits (Griffin) is refused.

The desktop passes the `split` that `detect` returned. `index` returns the
split it used, and the Dataset view shows that one.

## Cache layout

```text
<results_root>/coperceive-cache/<adapter>/<sha1(realpath(raw_root))[:12]>/<split>/<scene_id>/
<results_root>/coperceive-cache/<adapter>/<sha1(realpath(raw_root))[:12]>/<split>/dataset.json
```

- `raw_root` is the **dataset root** (for OPV2V/V2XSet that is the parent of the
  split directory, e.g. `/datasets/.../opv2v`, with `split=test`; for Griffin it
  is the release root).
- `<split>` is the adapter split parameter, or `all` when the adapter has none.
- A scene directory is exactly what `coperceive adapters ingest` writes
  (`manifest.json`, `COMPLETE.json`, …), moved into place atomically after it is
  complete.
- The cache path must not resolve inside `raw_root`; preparation refuses that.

## Camera images — `--images originals|previews`

Camera frames are never copied into the cache at full size, and nothing is ever
written under `raw_root`. Every prepared scene gets a link `raw` → `raw_root`
inside its cache directory, and its `images.parquet` paths go through it. The
user chooses per prepare, before confirming:

| mode | what prepare writes | what the viewer reads |
| --- | --- | --- |
| `originals` (default) | nothing extra | `raw/<image path>`: the dataset's own PNG, through the server's allow-listed media root |
| `previews` | a 640 px wide JPEG per camera frame at `previews/<image path>.jpg` (about 40 KB each) | the preview; the full-resolution original from `raw/` when the user opens one camera |

```text
<cache scene>/raw -> <raw_root>
<cache scene>/previews/<split>/<scene>/<agent>/<frame>_<camera>.png.jpg   (previews mode only)
```

- `images.parquet` paths are `raw/<path>` (originals) or `previews/<path>.jpg`
  (previews). A preview that is missing (removed, or the original had none)
  falls back to `raw/<path>`; an original that is missing is reported as no
  image, as before.
- The viewer server for a cluster dataset is started with
  `--media-root <raw_root>`: exactly this dataset's root, nothing above it. A
  path is served only if it resolves inside the scene directory or that root;
  a recorded path with `..` or an absolute path is refused, and so is any link
  inside the raw tree that resolves outside it.
- `dataset.json` records each scene's mode as `"images": "originals" |
  "previews"`. Preparing a ready scene again with the other mode only rewrites
  its image paths (and writes or keeps its previews); it is not re-ingested.
- `dataset previews --cache-root C` prints `{"schema":
  "coperceive-cluster-previews-1", "bytes": N, "files": N, "scenes": N}` for
  every `previews/` directory below `C` (the profile's whole cache).
  `--remove` deletes exactly those directories, never a scene file, never the
  `raw` link and never anything it points to, and marks those scenes
  `originals`. The scenes stay viewable.
- The estimate shown before confirming is `camera_samples` of the selected
  scenes (from the index) × 40 KB.

## `dataset detect` — `coperceive-cluster-detect-1`

```json
{
  "schema": "coperceive-cluster-detect-1",
  "path": "/datasets/eemcs/ps/cv/opencood/opv2v/test",
  "adapter": "opv2v",
  "raw_root": "/datasets/eemcs/ps/cv/opencood/opv2v",
  "split": "test",
  "params": {"split": "test"},
  "licence": {
    "status": "unknown",
    "spdx": null,
    "notice": "No licence is published. …",
    "links": ["https://mobility-lab.seas.ucla.edu/opv2v/"]
  },
  "scene_count": 16,
  "status": "ok",
  "reason": null
}
```

- `adapter` is `"griffin"`, `"opv2v"`, `"v2xset"`, or `null` (`status:"none"`).
- `split` is `null` for Griffin.
- `licence` is `null` when the adapter has no licence block. Its fields are the
  adapter manifest's licence: `status` (`known`/`unknown`), `spdx`, `notice`,
  `links`.
- The CLI exits 0 for `status:"ok"` and `"none"`. An unreadable/nonexistent path
  returns `status:"error"` and exits 1.
- Detection reads directory entries and the adapter's small scene index only;
  it never reads images, point clouds, parquet, or pose contents. For
  OPV2V/V2XSet it lists the split directory and one scene's agent folders; it
  does not list frames. `scene_count` is the number of scene directories in the
  split (at most `--max-scenes`); `index` may later list fewer if a scene turns
  out to be unreadable (it is then in `errors`).
- When `path` is a dataset root with several splits, `split` is the first
  present one in the adapter's order; the UI should let the user choose, and
  `index`/`prepare` must be given it explicitly.

## `dataset index` — `coperceive-cluster-index-1`

```json
{
  "schema": "coperceive-cluster-index-1",
  "adapter": "opv2v",
  "raw_root": "/datasets/eemcs/ps/cv/opencood/opv2v",
  "split": "test",
  "params": {"split": "test"},
  "licence": {"status": "unknown", "spdx": null, "notice": "…", "links": ["…"]},
  "fingerprint": {
    "algorithm": "sha1-mtime-size-v1",
    "digest": "9f1c…",
    "entries": 18432,
    "truncated": false
  },
  "cache": {"relative_path": "coperceive-cache/opv2v/3b1e2f0a9c77/test"},
  "estimated_cache_bytes": 2147483648,
  "estimated_cache_bytes_method": "sum of file sizes under each scene directory, metadata only",
  "scene_count": 16,
  "scenes": [
    {
      "scene_id": "2021_09_09_20_09_24",
      "sample_count": 60,
      "duration_s": 3.0,
      "agent_count": 3,
      "sensors": {
        "sensors": 2,
        "camera_samples": 240,
        "lidar_samples": 60
      }
    }
  ],
  "errors": [],
  "truncated": false,
  "elapsed_ms": 321.4
}
```

- `fingerprint` is a bounded digest over file/directory **metadata only** below
  the split directory (or the raw root when there is no split): relative path,
  type, size and `mtime_ns`, sorted, SHA-1. `entries` counts metadata entries
  visited; `truncated` is true when `--max-entries`/`--timeout` stopped it.
- `split` is the split that was indexed: the `--split` given, or the one the
  raw root names.
- `estimated_cache_bytes` is additive: cached scene files are approximately the
  raw scene files, so it is the sum of file sizes under each scene directory
  (metadata only, no file contents). `estimated_cache_bytes_method` records that
  rule. It is an estimate, not a promise. When `fingerprint.truncated` is true,
  scenes the walk did not finish are sized by a further walk that stops at the
  same `--timeout`; the estimate is then a lower bound.
- `cache.relative_path` is relative to `results_root` and is exactly the layout
  above; the full cache is `<results_root>/<relative_path>`.
- `duration_s` is cheap: `sample_count × adapter clock scale`, not measured from
  media. It is `null` when the adapter has no scale.
- `agent_count`, `sample_count` and `sensors` are read cheaply: agents and
  their samples from directory listings, the sensors from each agent's first
  calibration record (one file per agent), `lidar_samples` from the listing of
  the point-cloud folders. `sensors` = declared sensors, `camera_samples` =
  image records (cameras × samples, for every sample), `lidar_samples` =
  point-cloud files present. No pose, box or point data is read.
- `errors` are per-scene errors, one line each, e.g.
  `"scene_0001: vehicle-side/pose/000000.json: missing"`; they do not abort the
  index, and a scene with an error is not listed in `scenes`.
- OPV2V/V2XSet records may hold numpy-tagged scalars
  (`!!python/object/apply:numpy.core.multiarray.scalar`, `numpy.dtype`,
  `numpy.core.multiarray._reconstruct`); they are read as plain numbers by a
  `SafeLoader` subclass that rebuilds them from the dtype descriptor and bytes.
  Every other `python/*` tag is refused.
- `truncated` means `--max-scenes` was hit; `scene_count` is the returned count.

## `dataset.json` cache index — `coperceive-cluster-dataset-cache-1`

Written inside every cache split directory. Atomically replaced (temp + rename).

```json
{
  "schema": "coperceive-cluster-dataset-cache-1",
  "adapter": "opv2v",
  "raw_root": "/datasets/eemcs/ps/cv/opencood/opv2v",
  "split": "test",
  "params": {"split": "test"},
  "cache_root": "/scratch/alice/.../coperceive-cache/opv2v/3b1e2f0a9c77/test",
  "raw_fingerprint": {
    "algorithm": "sha1-mtime-size-v1",
    "digest": "9f1c…",
    "entries": 18432,
    "truncated": false
  },
  "created_unix": 1793461000,
  "updated_unix": 1793462000,
  "scenes": {
    "2021_09_09_20_09_24": {
      "state": "ready",
      "reason": null,
      "job_id": "651911",
      "prepared_unix": 1793461500,
      "scene_dir": "2021_09_09_20_09_24"
    },
    "2021_09_09_20_09_40": {
      "state": "failed",
      "reason": "vehicle-side/pose/000000.json: missing",
      "job_id": "651911",
      "prepared_unix": null,
      "scene_dir": "2021_09_09_20_09_40"
    }
  }
}
```

`state` is one of:

| state | meaning |
| --- | --- |
| `not_prepared` | index knows the scene; no completed cache directory. |
| `preparing` | a prepare job is running (or was started) for it. |
| `ready` | complete schema-1 scene is in the cache. |
| `failed` | preparation failed; `reason` is the plain error. |
| `stale` | raw fingerprint changed since `ready`; it must be re-prepared. |

A scene is `ready` only when the cache directory contains both `manifest.json`
and `COMPLETE.json`. A scene whose `raw_fingerprint` no longer matches the
dataset's current fingerprint is `stale` even if it was ready. `job_id` is the
Slurm id that last prepared it; `null` otherwise.

## Per-scene status — `coperceive-cluster-scene-status-1`

`dataset status --cache C` returns one document with a list of these records,
plus the cache-level fields:

```json
{
  "schema": "coperceive-cluster-scene-status-1",
  "cache_root": "/scratch/alice/.../coperceive-cache/opv2v/3b1e2f0a9c77/test",
  "adapter": "opv2v",
  "raw_root": "/datasets/.../opv2v",
  "split": "test",
  "updated_unix": 1793462000,
  "scenes": [
    {
      "scene_id": "2021_09_09_20_09_24",
      "state": "ready",
      "reason": null,
      "job_id": "651911",
      "prepared_unix": 1793461500,
      "scene_dir": "2021_09_09_20_09_24"
    }
  ]
}
```

The same record is used inside `dataset.json.scenes`; `scene_id` is implied by
the map key there. The UI polls this command; `state`/`reason` are the only
fields it needs to render progress and failures, and `job_id` links a preparing
scene to its Slurm job.

## Bounds and safety

- Detection/index accept `--max-scenes` (default 5000), `--max-entries`
  (200000) and `--timeout` (30 s) for index; they never write.
- Preparation is scheduled: CPU only (no GRES), `--cpus-per-task` equal to
  `--workers`, a per-job scratch subdirectory, the existing preview/confirm
  hash, and a per-scene temporary directory moved into the cache atomically.
- One failed scene does not stop the rest; its `reason` is kept. The claim takeover threshold is a fixed four hours, so the prepare
  job walltime must stay at or below four hours (the default is four hours).
- Nothing derived from licensed/unknown raw data is written to this repository;
  fixtures are synthetic.
- A failed `dataset detect|index|prepare|status` prints its JSON document with a
  one-line `error`/`reason` (e.g. `could not read <file>: <reason>`) and the same
  line on stderr; `--verbose` adds the traceback.

## Server support check — `coperceive capabilities`

`coperceive capabilities` prints
`{"schema": "coperceive-capabilities-1", "version": "0.1.0", "capabilities": ["cluster-datasets-2", …, "cluster-datasets-5"]}`.
`cluster-datasets-2` means: these commands, numpy-tagged records and
per-scene index errors; `cluster-datasets-3` adds `--split` on `index` and
`prepare`; `cluster-datasets-4` adds `prepare --images` and `dataset
previews`; `cluster-datasets-5` adds `dataset stream` (viewing over SSH). The
desktop needs `cluster-datasets-5`. When a desktop dataset command fails, and on Test
connection (row `cluster-datasets`), the app runs this command through the
saved argv. A build without it, or without `cluster-datasets-5`, gets one
message: `coperceive on this server is too old for cluster datasets (found X,
need a build with cluster-datasets-5). Update it with: <command>`, where the
command is `git -C <checkout> pull --ff-only && <python> -m pip install -e
<checkout> --no-deps` for a git checkout, else a `pip install --upgrade` from
the repository.

## Desktop command names

The Rust commands call the saved server command over SSH and return the JSON
documents above unchanged (they do not re-shape them):

| Tauri command | arguments | returns |
| --- | --- | --- |
| `dataset_detect` | `{ profileId: string, path: string }` | detect document |
| `dataset_index` | `{ profileId: string, adapter: string, rawRoot: string }` | index document |

Both are read-only and use the profile's saved `coperceive_command` argv. A
failed remote command returns a plain error string; a successful `detect` with
no match returns `status:"none"` (not an error).


## Viewing: over SSH, no job

Preparing is once per scene: a prepared scene stays in the cache and is reused
until the raw data changes. Viewing runs **no Slurm job**. The app starts its
own viewer server on this Mac (`coperceive serve --remote-stream-argv …`), and
that server reads the prepared files over one long-lived SSH connection to a
read-only streamer on the login node:

    ssh <the profile's options and host> <coperceive argv> dataset stream \
        --root <cache split directory> --root <dataset raw root>

- **One connection, pipelined.** Requests are JSON lines on the streamer's
  stdin, each with an id; replies are a JSON header line and the bytes. Several
  reads are in flight at once, so round trips overlap. The SSH options are the
  app's usual ones (`BatchMode`, `ControlMaster=auto`, `ControlPersist`). Every
  remote argument is shell-quoted; file paths only travel on stdin, never
  through a shell.
- **Read-only and confined.** The streamer reads only below its `--root`s:
  the dataset's cache split and its raw root (for "Read originals" camera
  images). It refuses absolute paths, `..`, NUL, and anything whose `realpath`
  leaves the roots, symlinks included. It never writes. Requires
  `cluster-datasets-5`.
- **What crosses the link.** Each scene's small files (manifest and tables) are
  mirrored on first open; point sweeps and camera images are read as byte
  ranges on demand, prefetched ahead of playback (as in "Buffering…"), and kept
  in a local cache capped at 2 GB (Settings → Cluster datasets → "Viewing
  cache", with "Clear cache"). The cache lives in
  `~/Library/Caches/nl.adarsh.coperceive/remote-view/`.
- **While a prepare job runs**, scenes with `COMPLETE.json` are listed and
  viewable; the others join the viewer's list as they finish. Viewing still
  works when no job can be submitted (no free nodes).
- Every further **Open** of a scene of the same dataset reuses the viewer
  server and its connection, and points the open viewer at the scene
  (`#/viewer?scene=…`). Opening another dataset or a local folder stops it.

Measured with `scripts/bench/remote_view_bench.py` (a 2-agent OPV2V-sized
scene, 56 000 points per sweep, 10 Hz; `scripts/bench/link.py` adds half the
round trip each way and caps the bandwidth):

| link | playback (first time) | median frame | "Buffering…" frames | from the local cache |
| --- | --- | --- | --- | --- |
| 20 ms, 25 MB/s | 9.7 fps | 160 ms | 0 of 60 | 10 fps, ~18 ms |
| 20 ms, 50 MB/s | 9.9 fps | 112 ms | 0 of 60 | 10 fps, ~18 ms |
| 40 ms, 25 MB/s | 9.7 fps | 223 ms | 0 of 60 | 10 fps, ~18 ms |
| 40 ms, 50 MB/s | 9.8 fps | 156 ms | 0 of 60 | 10 fps, ~18 ms |
| 40 ms, 10 MB/s | 5.7 fps | 841 ms | 35 of 60 | 10 fps, ~21 ms |

Opening the scene took 0.6–1.9 s; 60 frames moved about 102 MB (1.7 MB per
frame), so below about 18 MB/s the first pass is bandwidth-bound and buffers;
once cached it plays at full rate.

## Camera-image settings and previews (desktop)

| Tauri command | arguments | returns |
| --- | --- | --- |
| `get_camera_images_setting` | `{ "profileId"?: string }` | `{ "default": "originals"\|"previews", "profile": "originals"\|"previews" \| null, "effective": "originals"\|"previews" }` |
| `set_camera_images_setting` | `{ "profileId"?: string, "mode": "originals"\|"previews"\|null }` | the same document |
| `dataset_previews` | `{ "profileId": string, "remove": boolean }` | the `coperceive-cluster-previews-1` document (after removal when `remove`) |

The setting is stored in `state.json` (`camera_images_default`, and
`camera_images_by_profile` keyed by profile id). Changing it never touches
prepared scenes; it only sets what the next prepare preview starts with.
`dataset_previews` with `remove: true` is called only after the user confirms.

## Desktop prepare/status/open commands (frozen)

These names and argument shapes are the second half of the UI contract. All
return JSON and use the saved server profile and `coperceive_command`.

| Tauri command | arguments | returns |
| --- | --- | --- |
| `dataset_prepare` | `{ "profileId": string, "adapter": "griffin"\|"opv2v"\|"v2xset", "rawRoot": string, "split": string \| null, "scenes"?: string[] \| "all", "workers"?: number, "images"?: "originals"\|"previews" }` | **submission preview**: `{ "plan_id": string, "job_name": string, "partition": string, "walltime": string, "cpus_per_task": number, "gres": null, "estimated_cache_bytes": number, "images": "originals"\|"previews", "estimated_preview_bytes": number, "cache_root": string, "script": string, "preview_hash": string }` |
| `dataset_prepare_submit` | `{ "profileId": string, "previewHash": string, "confirmed": boolean }` | `{ "job_id": string, "state": "pending"\|"running"\|"completed"\|"failed"\|"cancelled"\|"unknown", "queue_reason": string \| null, "plan_id": string }` |
| `dataset_status` | `{ "profileId": string, "adapter": string, "rawRoot": string, "split": string \| null }` | `coperceive-cluster-scene-status-1` document |
| `dataset_open` | `{ "profileId": string, "adapter": string, "rawRoot": string, "split": string \| null, "sceneId"?: string }` | `{ "origin": string, "token": string, "server_version": string, "data_root": string, "target_type": "remote" }` |

`images` omitted means the user's default for this server profile (Settings →
Cluster datasets → "Camera images when preparing"; else the app-wide default;
else `originals`). The preview echoes the mode it used, and the script passes
`--images` explicitly, so a different mode is a different script and a
different `preview_hash`. `estimated_preview_bytes` is the extra disk the
previews mode would use for the selected scenes, whichever mode is chosen, so
the UI can show the cost next to that option.

`scenes` omitted means all scenes, same as `"all"`. `workers` is optional: when omitted the backend uses `min(selected_scene_count, 8)`; it is clamped to 1–16 either way. The preview's `cpus_per_task` is exactly the workers actually used, and the script passes the same `--workers`. `cache_root` is the absolute cache split directory (`results_root` + `cache.relative_path`) so the UI never computes it. `split` is `null` only for
Griffin. `dataset_prepare` renders the full script but submits nothing.
`dataset_prepare_submit` requires the exact `preview_hash`; a changed preview or
an unconfirmed call submits nothing and returns a plain error. The preview is
the existing job-submit preview shape plus the additive
`estimated_cache_bytes`, and its script contains a `# No GPU requested
(CPU-only job).` line, the job-owned scratch subdirectory trap, and
`--cpus-per-task` equal to `--workers`.

`dataset_open` serves the **cache split directory**, not the raw root, so the
viewer's scene list is exactly the ready cache scenes. It passes
`--media-root <raw_root>` so camera images resolve through each scene's `raw`
link (see Camera images). It reuses an existing
remote session for the same cache directory when one is running; `sceneId` is
the requested first scene and does not prepare anything by itself (the UI calls
`dataset_prepare` for a priority scene first).
