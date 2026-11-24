# Viewer lifetime on the HPC

Proposed on 2026-11-20 for v0.1.3 (feature C); decisions recorded on 2026-11-22 and
2026-11-23. Code is cited from `release/v0.1.3` at `17b4ea0`. Anything the code does not
settle is marked **unverified**.

## Decisions

- **DECIDED (Adarsh, 2026-11-20): remove the app-side `remote_connect` viewer-job path,
  and keep the `coperceive remote` CLI.**
  - The removal is its own PR against `release/v0.1.3`, after this note merges.
  - The inventory below lists everything that reaches the path.
- **DECIDED (Adarsh, 2026-11-20): measure reopen time before building any idle timeout.**
  - "Stay connected for 10 minutes after the window closes" is built only if the real
    number justifies it.
  - If it is built, its default is a Settings value.
- **DECIDED (Adarsh, 2026-11-20): the Re-check action for scenes wrongly marked stale is
  a v0.1.2 P0 fix**, not part of this feature.
- **DECIDED (routed to v0.1.2, PM 2026-11-23): Open predictions** is broken today (see
  below). The v0.1.2 session fixes it on the stream path, as a v0.1.2 P0 fix. This feature
  does not fix it, and the removal leaves `open_predictions` as that fix leaves it.

## Does viewing run a Slurm job? No.

- **The app's only viewing path is `dataset_open`** (`src-tauri/src/main.rs`):
  - It starts a local `coperceive serve` (`SidecarSession`).
  - That server reads the cache over one SSH connection to `coperceive dataset stream`
    on the login node.
  - It logs `reading … over SSH, no Slurm job`.
  - Contract: `docs/datasets/cluster-datasets.md`, "Viewing: over SSH, no job"; and
    CHANGELOG [0.1.0]: "Viewing a prepared cluster dataset runs no Slurm job."
- **The old viewer-job path:**
  - The chain: `remote_connect` › `RemoteSessionManager` (`remote.rs`) ›
    `coperceive remote start` › `sbatch scripts/desktop/remote_serve.sbatch`.
  - `state.remote` is set only by `remote_connect` (`main.rs:4365`).
  - No TypeScript file in the history has ever contained `remote_connect` (`git log -S`).
    Until `803386a` (2026-11-03), `dataset_open` itself called it
    (`803386a^:src-tauri/src/main.rs:2694`).
- **Its lease is never renewed:**
  - `remote_serve.sbatch` stops the server once its heartbeat file is 600 s old
    (`COPERCEIVE_HEARTBEAT_TIMEOUT`, watchdog at lines 212–221).
  - The app never calls `RemoteClient::heartbeat`.

## Removal inventory: everything that reaches the path

| Caller | Where it stays covered, or why it is no longer needed |
|---|---|
| Test controller command `"remote_connect"` (`main.rs:4088`) | Test-only. Its only users are the two E2E steps below. Removed with them. |
| E2E Flow I, step CD-15: a slow-starting viewer job is attached, not refused | The app no longer attaches to jobs. The status rule stays tested for the CLI: `test_a_running_job_without_its_session_file_yet_is_starting_not_stopped` (`tests/test_remote.py`). |
| E2E Flow F step 3, CD-19: `remote_connect`, then a local scene sends `remote stop` and clears the header | Steps 1–2 of the same flow stay: `dataset_open` streaming, then `start_local` clears "Streaming over SSH". With no remote session there is nothing to stop. (The script also labels the camera-preview flow, CD-11–13, as "Flow F".) |
| `remote.rs` tests of `RemoteSessionManager`: queued/attach, cancel, reattach, `disconnect_with_timeout` | Removed with the manager. The command quoting, injection and token-redaction tests stay while anything still uses `RemoteClient`. Whether `tests/test_remote.py` covers the same rules for the CLI is **unverified**; the removal PR checks it before deleting any of them. |
| Static upload `remote_static::prepare_remote_static` (`main.rs:4269`, inside `remote_connect`) and the E2E `FAKE_SCP` | Removed with the path. `set_resource_dir` and `OsLayout` in the same module stay: Python resolution uses them (`main.rs:160`, `platform/linux.rs:172`). |
| `controller.rs` test `…allowed_origin_for_remote_connect` | Kept and renamed. It checks `open_viewer_window`'s origin sync, which `dataset_open` also uses. |
| Disconnect on window close and quit (`main.rs:5608`, `5641`, `5682`); `disconnect_remote_in_background` (`829`, `1863`); the inline `disconnect()` in `dataset_open` (`3890`) | Removed with `state.remote`. The sidecar stop next to each one stays. |
| `state.json` `viewer_sessions` (`state.rs:429`) | Removed. `AppPersistentState` does not deny unknown fields, so an old file still loads; add a test. |
| Viewer job badge: `Viewer.tsx` › `getViewerJob` › `viewer_get_job` and `/viewer-session` › `viewer_session_status` (`main.rs:1050`) | Always empty in the app today, because nothing sets `state.remote`. Remove it with the path, together with the tests that reach it: `controller.rs:1543–1555` (`/viewer-session`), `web/src/desktop/ctrlGate.test.ts` (`viewer_get_job`), and the mock in `tests/test_viewer_layout.py:146`. |
| Commands `remote_status`, `remote_disconnect` | No web caller. Remove them. |
| **`open_predictions` (`main.rs:3324`), reached from Job watch and Run history** | **Not dead: it is reachable in the UI.** It needs `state.remote`. That worked while `dataset_open` started the viewer job. Since `803386a` (2026-11-03; in the v0.1.0 and v0.1.1 tags) nothing sets it, so the call always fails with "Open predictions needs an active remote session". This is a regression from the switch to the stream path, and no test covers it. Nothing hides the button: `is_remote_enabled` is never called (only defined, `local.ts:175`); Run history is an HPC workspace tab and Job watch is part of New evaluation. **DECIDED (routed to v0.1.2, PM 2026-11-23):** the v0.1.2 session re-points it to the `dataset_open` stream path. The removal PR waits until that fix is synced into `release/v0.1.3`, and does not change `open_predictions` beyond what the fix leaves. |
| CLI `coperceive remote …`, `remote_serve.sbatch`, `tests/test_remote.py` (21 tests) | Kept, as decided. |

## The CLI's own lease is never renewed either (proposed v0.1.2 fix, not built)

- **No CLI command renews it automatically:**
  - `coperceive remote start` submits the job and returns.
  - `record_heartbeat` (`coperceive/remote/manager.py:197`) runs only from
    `coperceive remote heartbeat`.
  - The served viewer does not touch the heartbeat file. `remote status` only reads its
    age (`manager.py:652–658`), and `remote stop` deletes it (`manager.py:813`).
- **The docs never say to renew it:** `docs/reference/cli.md` has only the usage line.
- **So, inferred from the code and unverified on the cluster:** a CLI viewer job stops
  about 600 s after it starts, whatever `--time` says, unless its user runs
  `remote heartbeat` by hand.
- **Unverified on the cluster:** whether `COPERCEIVE_HEARTBEAT_TIMEOUT=0` set in the
  user's shell reaches the job. `start_session` passes its environment to `sbatch`, but
  the cluster's export setting is unknown.
- **Proposed fix:** have the server renew the lease while it serves requests, or document
  the renewal. This is for the PM to route.

## What reopening costs today (the stream path)

| Event | What happens | Code |
|---|---|---|
| Another scene, same dataset | Server and connection are reused | `dataset_open`, `stream_key` |
| Viewer window closed | The local server stops, which ends its SSH stream | `on_window_event` › `sidecar.stop()` |
| Reopen after closing | A new server, a new SSH connection, up to 60 s health wait | `wait_until_healthy(60 s)` |
| SSH or VPN drop | The next request runs the SSH command again | `StreamClient.submit` |

## Phase 0: measure reopen time (before anything is built)

- **Mechanism, on the fake-SSH E2E:** a new flow opens a scene, closes the viewer window,
  and opens it again. It asserts what happens today:
  - a second `dataset-stream` line in the SSH log;
  - a new origin.

  It prints Open → first frame for each open. Fake times measure the mechanism, not the
  cluster.
- **Real number: `scripts/bench/reopen_time.py`, which Adarsh runs once from his laptop.**
  - It starts the same local server `dataset_open` starts, against a saved server profile
    and a prepared split, five times.
  - Each run records the time to healthy and the time to the first frame of one scene.
  - It prints median, minimum and maximum.
  - It submits no job, writes nothing on the cluster, and prints no token.
- **Rule:** if the median reopen is short enough that waiting doesn't matter, the idle
  timeout is not built. The threshold is Adarsh's call once the number is in.

## If the number justifies it: stay connected after closing

- **Lifetime:**
  - After the window closes, the server and its SSH stream stay up for T.
  - T is a Settings value under Cluster datasets; proposed default 10 min.
  - Quitting always disconnects.
- **Reattach:** within one app run, reuse via `stream_key`. Across restarts there is
  nothing to find, because the server is the app's child.
- **Security:**
  - The token stays in memory; nothing new is written.
  - Once stopped, the port closes and the 0600 token file is removed, so an old page is
    refused.
  - The #182 token and Host checks apply to every `/api` route.
  - **Unverified:** the mode of the temp directory.
- **Visibility:** "Viewer connected to <profile> · idle 4 min · disconnects at 14:32",
  with **Disconnect now**, in the jobs indicator and the dataset view.
- **Failure modes:**
  - SSH or VPN drop: reconnect on the next request (**unverified**; no test, add one).
  - App crash: the server can outlive the app, because there is no parent-death signal
    (**unverified**; propose a check).
- **Cost:**
  - 0 CPU-hours.
  - One login-node SSH session and one streamer per connected dataset, ≤ N × T
    process-hours per day.
  - The streamer's idle CPU and memory are **unavailable**; phase 0 measures them with
    `ps`.
- **Tests:**
  - Rust: kept for T, then stopped; Disconnect now stops it; quit stops it; the setting
    is clamped.
  - E2E: reopen within T opens no new `dataset-stream`; after T it opens a new one.
