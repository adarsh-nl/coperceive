# Viewer lifetime on the HPC

Proposed on 2026-11-20 for v0.1.3 (feature C), for PM review and Adarsh's approval. No
product code until it is approved. Code is cited from `release/v0.1.3`. Anything the code
does not settle is marked **unverified**.

## Does viewing run a Slurm job? No.

The brief says every HPC viewing session starts a CPU job. That has not been true since
v0.1.0.

- **The app's only viewing path is `dataset_open`** (`src-tauri/src/main.rs`):
  - It starts a local `coperceive serve` on the laptop (`SidecarSession`), which reads
    the cache over one SSH connection to `coperceive dataset stream` on the login node.
  - It logs `reading … over SSH, no Slurm job`.
  - The dataset view calls it (`DatasetView.tsx` › `openSceneInViewer`).
- **The contract says so:**
  - `docs/datasets/cluster-datasets.md`, "Viewing: over SSH, no job".
  - CHANGELOG [0.1.0] (2026-11-05): "Viewing a prepared cluster dataset runs no Slurm job."
- **The older viewer-job path is still in the tree:**
  - The chain: `remote_connect` › `RemoteSessionManager` (`remote.rs`) ›
    `coperceive remote start` (`coperceive/remote/manager.py` › `start_session`) ›
    `sbatch scripts/desktop/remote_serve.sbatch`.
  - **No web code calls `remote_connect`.** The web invokes only `remote_scan` and
    `remote_view_cache` of the `remote_*` commands.
  - Only the test-mode controller (#171) and the fake-SSH E2E reach it
    (`e2e_cluster_datasets_app.sh`, Flow F and step CD-15).

### What the old path does, if anything calls it

- **Name and walltime:**
  - The job name is `coperceive-desktop-<uuid>`.
  - Walltime is the profile's `time_limit`, else the partition default.
- **Stop:** the app stops the job on quit. On `RunEvent::Exit` and when the main window
  closes, `disconnect` runs `remote stop`, which cancels the job.
- **Reattach after a crash:**
  - The app keeps the job's uuid in `state.json` (`viewer_sessions`).
  - It fetches the token over SSH from `~/.coperceive/tokens/<uuid>.token` (mode 0600).
- **Heartbeat lease, inferred and unverified on the cluster:**
  - `remote_serve.sbatch` stops the server once its heartbeat file is 600 s old
    (`COPERCEIVE_HEARTBEAT_TIMEOUT`).
  - Nothing in the app calls `RemoteClient::heartbeat`; only a unit test does. It has
    been that way since the lease was added (d823c58, 2026-10-20).
  - So a job started by `remote_connect` stops itself about 10 minutes after it
    starts, however much it is used.
- **DECISION (Adarsh):**
  - **(a) Recommended:** remove the app-side `remote_connect` path and its two E2E
    flows. Keep the `coperceive remote` CLI, which `docs/reference/cli.md` documents and
    whose lease a CLI user renews with `remote heartbeat`.
  - **(b)** Keep the path and make the app renew the lease.
  - Either way, the rest of this note is about the stream path.

## What reopening costs today (the stream path)

| Event | What happens | Code |
|---|---|---|
| Another scene, same dataset | The server and the connection are reused | `dataset_open`, `stream_key` = profile, cache, raw root |
| Viewer window closed | The local server stops, which ends its SSH stream | `on_window_event` › `sidecar.stop()` |
| Reopen after closing | A new server starts, a new SSH connection opens, up to 60 s health wait | `wait_until_healthy(60 s)` |
| App quit | The server stops | `RunEvent::Exit` |
| SSH or VPN drop | The next request runs the SSH command again | `remote_view/client.py` › `StreamClient.submit` |
| Disk cache | Kept, 2 GB cap (Settings → Cluster datasets) | `remote_view_cache_dir` |

**Reopen time is unavailable:** nobody has measured how long it takes from Open to the
first frame after a close. Phase 0 of the implementation measures it on the fake harness
and on Adarsh's cluster before anything is built.

## Proposal: keep the viewer connected for a while after its window closes

The rest applies only if that measurement shows reopening is slow enough to matter.

### 1. Lifetime

- **Idle timeout:** after the viewer window closes, the local server and its SSH stream
  stay up for an idle timeout.
  - **Setting:** Settings → Cluster datasets → "Keep the viewer connected after closing".
  - **Proposed default: 10 minutes.** It is the lease the old viewer job already uses
    (600 s), and it covers closing and reopening a scene while costing no compute.
  - **DECISION (Adarsh):** the default.
- **Maximum lifetime:**
  - There is no partition walltime to stay under, because there is no job.
  - The bound is the app: quitting always disconnects.

### 2. Reattach

- **Within one app run:** the existing `stream_key` reuse covers it.
- **Across app restarts:** not proposed. The server is a child of the app, and there is
  nothing on the cluster to find.
- **No orphan on the login node:** the streamer exits when its stdin closes.
  `tests/test_stream_protocol.py` asserts "the streamer did not stop when stdin closed".
  So when the local server stops, its SSH session ends and the streamer follows. This is
  inferred from the pipe chain, not tested end to end.

### 3. Security

- **Token at rest:** the local server's token is a mode 0600 file in a per-process temp
  directory, removed when the server stops (`sidecar.rs`).
- **Reopening:** a kept server keeps its token in memory, and the reopened viewer window
  gets it from the app. Nothing new is written.
- **Fail closed:** once the server stops, its port closes and the token file is gone, so
  a page holding an old token gets a refused connection.
- **Every route:** the #182 checks still apply to every `/api` route: the token, and the
  Host check on loopback.
- **Unverified:** the temp directory's own mode. It is created with the default umask.

### 4. Visibility

- **Where:** the jobs indicator and the dataset view.
- **What it shows:** "Viewer connected to <profile> · idle for 4 min · disconnects at
  14:32", with a **Disconnect now** button.
- **No job id:** there is no job. Prepare jobs keep their ids in the same indicator.

### 5. Failure modes

| Failure | Behaviour | Test coverage |
|---|---|---|
| VPN or SSH drops | Next request reconnects (`StreamClient.submit`) | **unverified**: no test found for this; add one |
| Streamer or login node restarts | Same reconnect | as above |
| Scheduler kills a job, node fails | Not applicable: no job | none |
| Two app instances | Each has its own server and stream; the cluster side is read-only | **unverified**: both share `remote_view_cache_dir`; add a test |
| App crash | `Drop` does not run, and `sidecar.rs` sets no parent-death signal, so the local server and its SSH stream can outlive the app | **unverified**: none; propose a parent-death check |

### 6. Cost

- **Compute:** 0 CPU-hours. Viewing submits no job.
- **Login node:** one SSH session and one `coperceive dataset stream` process per
  connected dataset.
  - Extra process-hours per day ≤ N × T, where N is the number of closes per day that
    are not followed by a quit, and T is the idle timeout.
  - The streamer's idle CPU and memory are **unavailable**. Measure them with `ps` on the
    login node during phase 0.
- **The old viewer job, if kept (option b):**
  - CPU-hours per day = `cpus_per_task` × Σ over jobs of min(run time, walltime).
  - Today the run time ends at the 600 s lease.

## Tests and acceptance, after approval

- **Rust:**
  - the server is kept for T after the window closes and stops at T;
  - Disconnect now stops it;
  - quitting stops it;
  - the setting is clamped to its range.
- **Python:** the stream reconnects after its process is killed.
- **Fake-SSH E2E:** open, close, reopen within T (no new `dataset-stream` line in the
  SSH log), then reopen after T (a new one).
- **Adarsh, on the real cluster:**
  - Time a reopen before and after.
  - Check that `ps` shows no streamer after Disconnect now, or after a quit.
