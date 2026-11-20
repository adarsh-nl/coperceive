# Convert once: HPC datasets

Proposed 2026-11-20 for v0.1.3, for PM review and Adarsh's approval. No product code until
approved. Code is cited as `file` › `function`. Anything the code does not settle is
**unverified**.

## Today, and three corrections to the brief

- **Prepared once:** scenes are prepared once into
  `<results_root>/coperceive-cache/<adapter>/<hash>/<split>/` and reused while they are
  ready (`coperceive/cluster_datasets.py` › `_claim_and_prepare_scene`).
- **Correction 1:** `<hash>` is the sha1 of the raw root's *path string*
  (`cache_relative_path`), not a content digest. The content fingerprint (path, size,
  mtime) is stored in `dataset.json` (`_fingerprint`).
- **Correction 2:** "Prepare whole dataset" and "Prepare remaining" both send
  `scenes: "all"`. The app then submits **one array task per indexed scene, ready or not**
  (`src-tauri/src/main.rs` › `dataset_prepare`). Each task skips its scene if it is
  already ready, but still costs a Slurm task and two raw-tree walks.
- **Correction 3 (for feature C):** the contract says viewing runs no Slurm job
  (`docs/datasets/cluster-datasets.md`; `main.rs` › `dataset_open`). The C note settles this.
- **An offer already exists:** a first-time offer is written but never called
  (`web/src/desktop/clusterDatasets.ts` › `wholeDatasetOffer`; only its test uses it).

## Why scenes still look "prepared every time"

1. **One job per scene:** every scene needs its own job until the split is done. Open on
   a scene that is not ready starts the single-scene prepare (`DatasetView.tsx` ›
   `handleOpenScene`).
2. **Index on every open:** each open runs `dataset index` on the login node again,
   45–60 s on OPV2V train (CHANGELOG).
3. **Spurious staleness (inference, unverified on real data):**
   - `_fingerprint` stops at 30 s or 200 000 entries and sets `truncated`.
   - Both digest comparisons ignore that flag (`prepare_cluster_dataset`,
     `dataset_status_document`).
   - A walk cut off by the clock covers different entries each run, so the digest changes
     and **every** ready scene turns stale.
   - Status repeats this walk on every 5 s poll.
   - Read-only check for Adarsh, about 1 min:
     `coperceive dataset index --adapter opv2v --raw-root /data/opv2v --split train`.
     Read `fingerprint.truncated` and `elapsed_ms`.
4. **Two cache-path computations (latent):**
   - Python hashes `realpath(raw_root)`; Rust's `cache_slug` hashes the string as passed,
     and status and open use the Rust path.
   - Detect returns a realpath, so both agree in the normal flow.
   - Only the `dataset.raw_root || dataset.path` fallback in `DatasetView.tsx` could split
     them.

## P0 fixes, before the UI

- **Raw check:**
  - A truncated digest never makes a scene stale. Status says "raw check incomplete".
  - **DECISION (Adarsh):** that, or a per-scene fingerprint recorded at prepare time.
  - The check runs once per open, in the background. Polls during a job use
    `refresh_stale=False`.
- **One cache path:** Rust uses the index's `cache.relative_path`; `cache_slug` goes.
- **Exit code:** `dataset prepare` exits 2 only for *its own* failed scenes. Today it
  exits 2 for any failed scene in the cache (`cli.py` › `_dataset_prepare`).
- **Job name:** one name per dataset, `coperceive-dataset-<hash>-<split>`. Today every
  `test` split shares a name, which weakens the owner check (`job_watch.rs` ›
  `check_owner`).

## 1. First-open flow

- **When:** a split with no ready scenes, whose offer has not been dismissed for this
  (profile, adapter, raw root, split).
- **What:** a card above the scene list, not a modal.
- **Default button:** **Review conversion job…** opens the existing whole-split panel.
- ***Not now*:** the dismissal is remembered in app state, and the header button stays.

The card shows these figures, measured or marked unavailable:

| Figure | Source | First open on a cluster |
|---|---|---|
| Scenes | index `scene_count` | always (counted) |
| Raw size | index `estimated_cache_bytes`, which is a raw-byte sum; add `raw_bytes`, keep the old key | "at least X" when truncated; unavailable for DAIR-V2X-C |
| Cache size | new per-scene `bytes_written` in `.timing.jsonl` and `dataset.json` | unavailable |
| CPU time | logged `cpu_s` (unread today) per raw file × files | unavailable |
| Wall time | `dataset_jobs.rs` › `estimate_seconds` | model constants today |

- **During the run:** once the first scenes finish, the card and the jobs indicator
  project the remaining time from this run's timings.
- **DECISION (Adarsh):** may a rate measured on another split of the same adapter count,
  labelled with its source?
- **DECISION (Adarsh):** the wall-time constants and the header's 150 MB-per-scene fallback
  (`formatStorageEstimate`) break "measured or unavailable". I recommend "unavailable"
  until measured.

## 2. Job preview and confirmation

- **Unchanged:** the full script, the confirm checkbox and the `preview_hash` check
  (`submit_prepared`).
- **The card:** only opens the panel.
- **Never automatic:** no submission without the confirm, and no resubmit on error
  (`recoverFromRefusedPrepareSubmit`).

## 3. Partial, resumed, failed, cancelled

- **Remaining only:** `dataset_prepare` accepts `scenes: "remaining"`, chosen from status
  at preview time: not_prepared, failed and stale scenes, without live `preparing` claims.
  The array and the panel title count only those.
- **Resume:**
  - Job ids are stored per dataset in `state.rs`, so polling resumes after a restart.
  - Today they live only in React state (`DatasetView` › `submittedJobs`).
- **Retry N failed:** the same flow, limited to failed scenes, with their reasons.
- **Cancel:**
  - A new `dataset_prepare_cancel`: confirm, owner check by the unique name, then `scancel`.
  - It is needed because `cancel_run_job` accepts only `plan-…` ids.
  - The next run takes dead claims over (`_claim_still_live`).
  - Unverified: whether a killed task leaves a `.partial/<scene>` that resumes cleanly.
- **Array size (unverified):**
  - The cluster's `MaxArraySize` is unknown.
  - The preview reads it from `scontrol show config` and puts several scenes in one task
    if needed.

## 4. Staleness (#209)

- **One action:** **Re-convert N stale scenes** previews one array over exactly those scenes.
- **Counts:** status gains counts per state and per stale reason.
- **Reasons kept apart:** "Coperceive's conversion changed" (manifest) and "raw files
  changed" (`grouping.ts` › `staleExplanation`).
- **First check on open:** the manifest check is cheap (it reads `COMPLETE.json`), so it
  runs first.

## 5. Disk

- **Shown:** Dataset info shows the measured cache size (sum of `bytes_written`) and the
  cache path.
- **Remove converted split…:**
  - The app asks for confirmation, then a new `dataset remove --cache <split dir>` runs.
  - It refuses:
    - a path that is not `<results_root>/coperceive-cache/<adapter>/<hash>/<split>`;
    - a path with no cache-schema `dataset.json`;
    - symlinks, `..`, and anything inside the raw root;
    - a split whose prepare job is still live.
  - It renames the split atomically to `.removing-<split>-<unix>` first, so the split
    leaves the app at once.
  - **DECISION (Adarsh):**
    - Recommended: the deletion runs as a confirmed one-CPU job, since it can take longer
      than a minute.
    - Alternative: bounded deletion on the login node, resumed on the next call.
- **Never automatic:** nothing is deleted without the confirm.

## 6. Per-scene prepare

- **Recommendation:** keep it as the fallback for one scene ("Prepare & open"), behind the
  card.
- **DECISION (Adarsh):** keep it or remove it.

## 7. Shared caches (described only, not built)

- **Paths already line up:** the cache path depends only on adapter, raw realpath and
  split. Two users whose results roots point at one group folder get the same path.
- **What sharing would need:**
  - group-writable locks and `dataset.json`;
  - a read-only `shared_cache_roots` profile list, which open checks first.
- **Trust:** trusting other people's scene files is the risk in security-review item
  MISSED-1.

## 8. Tests and acceptance

- **CI, Python:** remaining selection; a truncated digest is not stale; per-task exit code;
  `bytes_written`; remove guards (traversal, symlink, raw root, live job).
- **CI, Rust:** unique job name, cancel owner check, array chunking.
- **CI, web:** card shown or dismissed; "unavailable" text.
- **CI, E2E:**
  - `scripts/desktop/e2e_cluster_datasets_app.sh` gains these flows:
    - first open, then convert;
    - a new launch opens with no `sbatch`;
    - a stale digest gives *Re-convert 1*;
    - cancel under `FAKE_SBATCH_HOLD` (the fake needs an `scancel` case);
    - remove.
  - That script runs only on the macOS CI job.
- **Adarsh, on the real cluster:**
  - Convert one split, then reopen it the next day: no offer, no job.
  - Compare the shown size with `du`.
  - Cancel mid-run, then convert the remaining scenes.
  - Remove the split.
