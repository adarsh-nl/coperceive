# Convert once: HPC datasets

Proposed on 2026-11-20 for v0.1.3. The PM reviewed it and Adarsh decided the open points
on 2026-11-20 (marked **DECIDED**). The P0 fixes ship in v0.1.2; the UI waits for v0.1.3.
Anything the code does not settle is marked **unverified**.

## Today, and corrections to the brief

- **Scene cache:** scenes are prepared once into
  `<results_root>/coperceive-cache/<adapter>/<hash>/<split>/` and reused while they stay
  ready (`coperceive/cluster_datasets.py` › `_claim_and_prepare_scene`).
- **`<hash>`:** it hashes the raw root's *path string* (`cache_relative_path`), not its
  content. The content fingerprint lives in `dataset.json`.
- **"Prepare remaining":** it submits one array task per indexed scene, ready or not
  (`src-tauri/src/main.rs` › `dataset_prepare`).
- **First-time offer:** one is already written (`clusterDatasets.ts` ›
  `wholeDatasetOffer`), but nothing calls it.

## Why scenes look "prepared every time"

1. **One job per scene:** each scene needs its own job until the split is done
   (`DatasetView.tsx` › `handleOpenScene`).
2. **Index on every open:** each open runs `dataset index` on the login node again,
   45–60 s on OPV2V train (CHANGELOG).
3. **Spurious staleness (inference):**
   - `_fingerprint` stops at 30 s or 200 000 entries and sets `truncated`.
   - Both digest comparisons ignore that flag (`prepare_cluster_dataset`,
     `dataset_status_document`).
   - A walk cut off by the clock covers different entries each run, so the digest changes
     and *every* ready scene goes stale.
   - Status repeats the walk on each 5 s poll.
   - **Question for Adarsh:** do rows read *Stale · raw dataset fingerprint changed*
     (this cause), or *Not prepared* (causes 1 and 2)?
   - **Read-only check:** `fingerprint.truncated` in the output of `coperceive dataset index`.
4. **Two cache-path computations (latent):** Python hashes `realpath(raw_root)`, while
   Rust's `cache_slug` hashes the string as passed. They agree for detected datasets.

## P0 fixes, before the UI (v0.1.2)

- **Raw check:**
  - A truncated digest never marks a scene stale. Status says "raw check incomplete".
  - The check runs once per open, in the background, and not while a job is running.
  - **DECIDED (Adarsh, 2026-11-20):** a truncated fingerprint never marks a scene stale.
    A per-scene fingerprint recorded at prepare time is listed under *Later*.
- **One cache path:** Rust uses the index's `cache.relative_path`.
- **Exit code:** `dataset prepare` exits 2 only for its own failed scenes. Today it does so
  for any failed scene in the cache (`cli.py` › `_dataset_prepare`).
- **Job name:** one name per dataset, `coperceive-dataset-<hash>-<split>`. Today every
  `test` split shares a name, which weakens `job_watch.rs` › `check_owner`.
- **Open without an index:**
  - The conversion job writes `index.json` into the cache.
  - A converted split opens from that file plus `dataset status`.
  - It re-indexes only when the raw check reports a change.
  - **DECIDED (Adarsh, 2026-11-20):** open from the saved `index.json` plus
    `dataset status`, without re-indexing.

## 1. First-open flow

- **When:** a split has no ready scenes and the offer has not been dismissed.
- **What:** a card above the scene list, not a modal.
- **Default button:** **Review conversion job…** opens the existing whole-split panel.
- ***Not now*:** remembered per (profile, adapter, raw root, split).

| Figure | Source | First open on a cluster |
|---|---|---|
| Scenes | index `scene_count` | counted |
| Raw size | index `estimated_cache_bytes`, a raw-byte sum, renamed `raw_bytes` | "at least" if truncated; none for DAIR-V2X-C |
| Cache size | new per-scene `bytes_written` | unavailable |
| CPU time | logged `cpu_s` (unread today) per raw file × files | unavailable |
| Wall time | `dataset_jobs.rs` › `estimate_seconds` | model constants today |

- **During the run:** once the first scenes finish, the rest is projected from this run's
  own timings.
- **DECIDED (Adarsh, 2026-11-20):** a rate from another split of the same adapter may be
  used, labelled with its source.
- **DECIDED (Adarsh, 2026-11-20):** the wall-time constants (`estimate_seconds`) and the
  150 MB-per-scene header fallback (`formatStorageEstimate`) go. Each figure shows
  "unavailable" until measured.

## 2. Job preview and confirmation

- **Unchanged:** the full script, the confirm checkbox and the `preview_hash` check
  (`submit_prepared`).
- **The card:** it only opens that panel.
- **Never silent:** nothing is submitted or resubmitted without that confirmation.

## 3. Partial, resumed, failed, cancelled

- **Remaining scenes only:** `scenes: "remaining"` is resolved from status at preview time
  to the not_prepared, failed and stale scenes, minus live claims.
- **Resume:** job ids are kept per dataset in `state.rs`, so polling resumes after a
  restart. Today they live only in React state.
- **Retry N failed:** the same flow, limited to the failed scenes.
- **Cancel:**
  - A new `dataset_prepare_cancel` asks for confirmation, checks the owner by the unique
    name, then runs `scancel`.
  - `cancel_run_job` accepts only `plan-…` ids.
  - The next run takes over dead claims (`_claim_still_live`).
  - **Unverified:** whether a killed task leaves a `.partial/<scene>` that resumes cleanly.
- **Large splits (unverified):** when a split has more scenes than `MaxArraySize`, each
  task takes several scenes.

## 4. Staleness (#209)

- **Counts:** status gains counts per state and per stale reason.
- **Re-convert N stale scenes:** previews one array over exactly those scenes.
- **Reasons:** the manifest and raw reasons stay separate (`staleExplanation`).

## 5. Disk

- **Dataset info:** shows the measured cache size (the sum of `bytes_written`) and the
  cache path.
- **Remove converted split…:**
  - The app asks for confirmation, then calls a new `dataset remove --cache <split dir>`.
  - It refuses:
    - any path other than `<results_root>/coperceive-cache/<adapter>/<hash>/<split>`;
    - a split with no cache-schema `dataset.json`;
    - symlinks, `..` and paths inside the raw root;
    - a split with a live prepare job.
  - It first renames the split to `.removing-<split>-<unix>`.
  - **DECIDED (Adarsh, 2026-11-20):** deletion runs in a confirmed one-CPU job, because
    it can outlast the login-node minute.
- **Never automatic:** nothing is deleted without confirmation.

## 6. Per-scene prepare

- **DECIDED (Adarsh, 2026-11-20):** per-scene prepare stays as the one-scene fallback
  ("Prepare & open"), behind the card.

## 7. Shared caches (described only, not built)

- **Paths already match:** two users with one group results root already share the cache
  path.
- **Sharing would need:**
  - group-writable locks;
  - a read-only `shared_cache_roots` profile list;
  - trust in other users' scene files (security review, MISSED-1).

## 8. Later

- **Per-scene raw fingerprint:** record each scene's raw fingerprint at prepare time, so a
  raw change marks only the scenes it touches stale, and a truncated split-wide walk no
  longer matters.

## 9. Tests and acceptance

- **CI, unit tests:**
  - Python: remaining selection, truncated digest, exit code, `bytes_written`, remove guards.
  - Rust: job name, cancel, chunking.
  - Web: the card.
- **CI, app E2E** (`e2e_cluster_datasets_app.sh`, macOS CI only):
  - first open, then convert;
  - reopen with no `sbatch`;
  - *Re-convert 1*;
  - cancel (the fake gains `scancel`);
  - remove.
- **Adarsh, on the real cluster:**
  - Convert a split, then reopen it the next day: no job runs.
  - Compare the size shown with `du`.
  - Cancel a run, then resume it.
  - Remove the split.
