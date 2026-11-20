# Convert once: HPC datasets

Proposed on 2026-11-20 for v0.1.3, for PM review and Adarsh's approval. No product code
until it is approved. Code references take the form `file` › `function`. Anything the
code does not settle is marked **unverified**.

## Today, and corrections to the brief

- **Reuse:** scenes are prepared once into
  `<results_root>/coperceive-cache/<adapter>/<hash>/<split>/` and reused while they stay
  ready (`coperceive/cluster_datasets.py` › `_claim_and_prepare_scene`).
- **`<hash>` is not a content digest:** it is the sha1 of the raw root's *path string*
  (`cache_relative_path`). The content fingerprint (path, size and mtime) is kept in
  `dataset.json` (`_fingerprint`).
- **"Prepare whole dataset" and "Prepare remaining" both submit every scene:** they send
  `scenes: "all"`, which becomes one array task per indexed scene, ready or not
  (`src-tauri/src/main.rs` › `dataset_prepare`). A ready scene is skipped inside its task,
  but the task still costs a Slurm slot and two walks of the raw tree.
- **First-time offer already written:** `web/src/desktop/clusterDatasets.ts` ›
  `wholeDatasetOffer` exists, but nothing calls it.

## Why scenes look "prepared every time"

1. **Per-scene jobs:** each scene needs its own job until the whole split is done
   (`DatasetView.tsx` › `handleOpenScene`).
2. **Index on every open:** every open runs `dataset index` on the login node again. The
   CHANGELOG measures this at 45–60 s on OPV2V train.
3. **Spurious staleness (inference):**
   - `_fingerprint` stops at 30 s or 200 000 entries and sets `truncated`.
   - Both comparisons ignore `truncated`: `prepare_cluster_dataset` and
     `dataset_status_document`. The status check repeats this walk on every 5 s poll.
   - A walk cut by the clock covers different entries on each run, so the digest
     changes and *every* ready scene of the split turns stale.
   - **Question for Adarsh:** do your rows read *Stale · raw dataset fingerprint changed*
     (this cause), or *Not prepared* (causes 1–2)?
   - **Read-only check:** `coperceive dataset index --adapter opv2v --raw-root /data/opv2v --split train`,
     then look at `fingerprint.truncated`.
4. **Two cache-path computations (latent):**
   - Python hashes `realpath(raw_root)`.
   - Rust's `dataset_jobs.rs` › `cache_slug` hashes the string as passed, and status and
     open use that path.
   - They agree for detected datasets, because detect returns a realpath. Only the
     `dataset.raw_root || dataset.path` fallback could split them.

## P0 fixes, before the UI

- **Raw check:**
  - A truncated digest never marks a scene stale; status says "raw check incomplete".
  - The check runs once per open, in the background. Polls during a job skip it.
  - **DECISION (Adarsh):** that, or a per-scene fingerprint recorded at prepare time.
- **One cache path:** Rust uses the index's `cache.relative_path`.
- **Prepare exit code:** `dataset prepare` exits 2 only for *its own* failed scenes
  (`cli.py` › `_dataset_prepare`; today it exits 2 for any failed scene in the cache).
- **Job name:** one per dataset, `coperceive-dataset-<hash>-<split>`. Today every `test`
  split shares a name, which weakens `job_watch.rs` › `check_owner`.
- **Open without an index:**
  - The conversion job writes the index document into the cache (`index.json`).
  - A converted split opens from it plus `dataset status`, and runs the full index only
    when the raw check reports a change.
  - **DECISION (Adarsh):** this, or keep the background re-index on every open.

## 1. First-open flow

- **Card:** a split with no ready scenes, whose offer was not dismissed, shows a card
  above the scene list. It is not a modal.
- **Default button:** **Review conversion job…**, which opens the existing whole-split
  panel.
- ***Not now*:** remembered per (profile, adapter, raw root, split).

| Figure | Source | First open on a cluster |
|---|---|---|
| Scenes | index `scene_count` | counted |
| Raw size | index `estimated_cache_bytes`, which is a raw-byte sum; expose it as `raw_bytes` | "at least" if truncated; unavailable for DAIR-V2X-C |
| Cache size | new per-scene `bytes_written` (timing log, `dataset.json`) | unavailable |
| CPU time | logged `cpu_s` (unread today) per raw file × files | unavailable |
| Wall time | `dataset_jobs.rs` › `estimate_seconds` | model constants today |

- **During the run:** once the first scenes finish, the card and the jobs indicator
  project the rest from this run's own timings.
- **DECISION (Adarsh):** does a rate from another split of the same adapter count, if it
  is labelled with its source?
- **DECISION (Adarsh):** the wall-time constants and the 150 MB-per-scene header fallback
  (`formatStorageEstimate`) break "measured or unavailable". I recommend "unavailable".

## 2. Job preview and confirmation

- **Unchanged:** the full script, the confirm checkbox and the `preview_hash` check
  (`submit_prepared`).
- **What the card does:** it only opens that panel.
- **No silent submission:** no automatic submit, and no automatic resubmit.

## 3. Partial, resumed, failed, cancelled

- **Remaining scenes only:** `scenes: "remaining"` is resolved from status at preview
  time. It covers not_prepared, failed and stale scenes, minus live claims, and the array
  covers only those scenes.
- **Resume after restart:** job ids are kept per dataset in `state.rs`, so polling resumes.
  Today they are kept only in React state (`DatasetView` › `submittedJobs`).
- **Retry N failed:** the same flow, limited to the failed scenes.
- **Cancel:**
  - A new `dataset_prepare_cancel` asks for confirmation, checks the owner by the unique
    job name, then runs `scancel`. It is needed because `cancel_run_job` accepts only
    `plan-…` ids.
  - The next run takes over dead claims (`_claim_still_live`).
  - **Unverified:** whether a killed task leaves a `.partial/<scene>` that resumes
    cleanly.
- **Array size (unverified):** the cluster's `MaxArraySize` is unknown. The preview reads
  it from `scontrol show config` and puts several scenes in one task when needed.

## 4. Staleness (#209)

- **Counts:** status gains counts per state and per stale reason.
- **One action:** **Re-convert N stale scenes** previews one array over exactly those
  scenes.
- **Separate reasons:**
  - The manifest reason ("Coperceive's conversion changed") and the raw reason stay
    separate (`grouping.ts` › `staleExplanation`).
  - The manifest check is cheap, since it reads `COMPLETE.json`, so it runs first.

## 5. Disk

- **What the user sees:** Dataset info shows the measured cache size (the sum of
  `bytes_written`) and the cache path.
- **Remove converted split…:**
  - The app asks for confirmation, then calls a new `dataset remove --cache <split dir>`.
  - It refuses:
    - a path other than `<results_root>/coperceive-cache/<adapter>/<hash>/<split>`;
    - a split without a cache-schema `dataset.json`;
    - symlinks, `..` and paths inside the raw root;
    - a split with a live prepare job.
  - It first renames the split atomically to `.removing-<split>-<unix>`.
  - **DECISION (Adarsh):** the deletion runs in a confirmed one-CPU job (recommended,
    because it can outlast the login-node minute), or bounded on the login node and
    resumed later.
- **Nothing is ever deleted without the confirmation.**

## 6. Per-scene prepare

- **Recommendation:** keep it as the one-scene fallback ("Prepare & open"), behind the
  card.
- **DECISION (Adarsh):** keep it or remove it.

## 7. Shared caches

**Description only; not to be built.**

- **Same path for everyone:** the path depends only on adapter, raw realpath and split,
  so two users with one group results root share it.
- **What sharing needs:**
  - group-writable locks;
  - a read-only `shared_cache_roots` list in the profile;
  - trust in other users' scene files (security review, MISSED-1).

## 8. Tests and acceptance

- **CI:** unit tests:
  - Python: remaining selection, truncated digest, per-task exit code, `bytes_written`,
    and remove guards (traversal, symlink, raw root, live job).
  - Rust: job name, cancel owner check, chunking.
  - Web: the card and the "unavailable" text.
- **CI: `scripts/desktop/e2e_cluster_datasets_app.sh` gains these flows** (it runs on
  macOS CI only):
  - first open, then convert;
  - a new launch opens with no `sbatch` and no index;
  - a stale digest leads to *Re-convert 1*;
  - cancel under `FAKE_SBATCH_HOLD` (the fake gains `scancel`);
  - remove.
- **Adarsh, on the real cluster:**
  - Convert one split, then reopen it the next day: there should be no offer and no job.
  - Compare the size shown against `du`.
  - Cancel a conversion, then convert the remaining scenes.
  - Remove the split.
