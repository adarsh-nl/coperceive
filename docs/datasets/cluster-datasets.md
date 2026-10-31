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
coperceive dataset index  --adapter A --raw-root R
coperceive dataset prepare --adapter A --raw-root R --cache C [--scenes ids | --all] --workers N
coperceive dataset status --cache C
```

`detect` and `index` print one JSON document and write nothing. `prepare` and
`status` are PR 2; their schemas are declared here now so the UI can be built
against them.

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
  it never reads images, point clouds, parquet, or pose contents.

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
- `estimated_cache_bytes` is additive: cached scene files are approximately the
  raw scene files, so it is the sum of file sizes under each scene directory
  (metadata only, no file contents). `estimated_cache_bytes_method` records that
  rule. It is an estimate, not a promise.
- `cache.relative_path` is relative to `results_root` and is exactly the layout
  above; the full cache is `<results_root>/<relative_path>`.
- `duration_s` is cheap: `sample_count × adapter clock scale`, not measured from
  media. It is `null` when the adapter has no scale.
- `sensors` is a coarse summary (`sensors` = declared sensors, `camera_samples`
  = image records, `lidar_samples` = point-cloud records) from the adapter
  check.
- `errors` are per-scene check errors, e.g.
  `"scene_0001: vehicle-side/pose/000000.json: missing"`; they do not abort the
  index.
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


## Desktop prepare/status/open commands (frozen)

These names and argument shapes are the second half of the UI contract. All
return JSON and use the saved server profile and `coperceive_command`.

| Tauri command | arguments | returns |
| --- | --- | --- |
| `dataset_prepare` | `{ "profileId": string, "adapter": "griffin"\|"opv2v"\|"v2xset", "rawRoot": string, "split": string \| null, "scenes"?: string[] \| "all", "workers"?: number }` | **submission preview**: `{ "plan_id": string, "job_name": string, "partition": string, "walltime": string, "cpus_per_task": number, "gres": null, "estimated_cache_bytes": number, "cache_root": string, "script": string, "preview_hash": string }` |
| `dataset_prepare_submit` | `{ "profileId": string, "previewHash": string, "confirmed": boolean }` | `{ "job_id": string, "state": "pending"\|"running"\|"completed"\|"failed"\|"cancelled"\|"unknown", "queue_reason": string \| null, "plan_id": string }` |
| `dataset_status` | `{ "profileId": string, "adapter": string, "rawRoot": string, "split": string \| null }` | `coperceive-cluster-scene-status-1` document |
| `dataset_open` | `{ "profileId": string, "adapter": string, "rawRoot": string, "split": string \| null, "sceneId"?: string }` | `{ "origin": string, "token": string, "server_version": string, "data_root": string, "target_type": "remote" }` |

`scenes` omitted means all scenes, same as `"all"`. `workers` is optional: when omitted the backend uses `min(selected_scene_count, 8)`; it is clamped to 1–16 either way. The preview's `cpus_per_task` is exactly the workers actually used, and the script passes the same `--workers`. `cache_root` is the absolute cache split directory (`results_root` + `cache.relative_path`) so the UI never computes it. `split` is `null` only for
Griffin. `dataset_prepare` renders the full script but submits nothing.
`dataset_prepare_submit` requires the exact `preview_hash`; a changed preview or
an unconfirmed call submits nothing and returns a plain error. The preview is
the existing job-submit preview shape plus the additive
`estimated_cache_bytes`, and its script contains a `# No GPU requested
(CPU-only job).` line, the job-owned scratch subdirectory trap, and
`--cpus-per-task` equal to `--workers`.

`dataset_open` serves the **cache split directory**, not the raw root, so the
viewer's scene list is exactly the ready cache scenes. It reuses an existing
remote session for the same cache directory when one is running; `sceneId` is
the requested first scene and does not prepare anything by itself (the UI calls
`dataset_prepare` for a priority scene first).
