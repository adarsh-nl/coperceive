# Settings inventory

Status: **draft for Adarsh and the PM**, updated 2026-11-22. No code is proposed
by this document; it lists what exists, what is missing, and where each choice
should live before anything is built.

**At a glance:** 72 settings. By the primary recommendation: **34 app Settings**
(global, per server or the viewer's remembered per-view state), **24 per run**
(New evaluation, Prepare, a viewer session or the run's own metadata), **2
CLI/config only**, and **12 fixed**. A row that offers a default plus an
override is counted by its first recommendation (for example "per server default
+ per run override" is app Settings), and a display-only per-view control is
counted under app Settings. The rows are listed in the tables below.

Scope: the desktop app (Settings, New evaluation, Prepare, viewer), the CLI and
the files both read. Evidence is the code on `release/v0.1.2` at
`d51e2be` (2026-11-22); three rows also cite open PRs #213 (plugin
interpreter) and #222 (viewer idle timeout), and #218's contributor docs on
`release/v0.1.3`. File and function names are given so a reviewer can check a
row.

## The rule this inventory applies

**Anything that changes a reported number is chosen per run and recorded with
the run, never as a hidden global default.** A number is only meaningful with
the rules that produced it. The evaluator already does this: `eval run` copies
its whole config into every `metrics.json`, and `eval report` prints the
protocol above the table. A display preference (colour, point size, units) may
be global because it does not change a stored result.

Where a row says **per run**, the choice should be visible in New evaluation
(or the Prepare preview for dataset preparation), stored in the run plan /
prepared-scene status / `run.json`, and shown wherever that run's results are
shown. **CLI/config only** means expert analysis that is not part of the run
being scored; it should still be recorded in the output it writes.

## Evaluation

These decide what is matched and scored. Most live in the evaluation config
(`coperceive/evaluation/config.py`) and reach `eval run` through `--config FILE`
or a few flags. **The AP protocol is the exception:** New evaluation now chooses
it per run (#211), the plan and `run.json` record it, and `eval run` uses the
recorded value by default. The app has no form for the other keys, and for a
built-in model the job runs `coperceive model run` only: `metrics.json` is
written later by `coperceive eval run`, not by the model job.

| Setting | Where it lives today | Default | Who would change it, and why | Recommendation |
| --- | --- | --- | --- | --- |
| AP protocol (`protocol`) | Eval YAML key `protocol`; CLI `coperceive eval run --protocol default\|legacy`; New evaluation's **AP protocol** choice (*Default* / *Legacy (v0.1.1)*, merged in #211) records it in the run plan; `model run --protocol` writes it to `run.json` provenance, and `eval run` uses the recorded value unless a flag/config says otherwise. `metrics.json` records the expanded `ranking_scope`/`gt_scope`. | `default` = per-frame ranking, connected-union ground truth within 70 m; `legacy` = the v0.1.1 rules. | Researchers comparing with v0.1.1 numbers, or with OpenCOOD-style per-frame AP. | **Per run** (New evaluation), recorded in the plan and run and passed to the scoring step (#211); CLI/config remains for headless use. |
| IoU thresholds (`thresholds`) | Eval YAML; no CLI flag (except `--by` bins on compare). | `[0.5, 0.7]` for `iou_bev`. | Matching a paper's AP@0.25/0.5/0.7 table. | **Per run**, advanced: default hidden, expandable, recorded. |
| Criterion (`criterion`) | Eval YAML. | `iou_bev`. | Centre-distance benchmarks (nuScenes/native centre AP); `centre` requires `thresholds` in metres. | **Per run**, advanced; record and label every AP with its criterion. |
| Display threshold (`display_threshold`) | Eval YAML. | `0.5`, one of `thresholds`. | Choosing the operating point at which `tp/fp/miss` statuses are stored for the viewer and paper export. | **Per run**, advanced; record. |
| Per-class thresholds (`class_thresholds`) | Eval YAML. | `{}` (global thresholds for every class). | Pedestrians/cyclists need different IoU than cars in some benchmarks. | **Per run**, advanced/config only; record. |
| Ignore ground truth below visibility (`ignore_gt_visibility_below`) | Eval YAML. | `null` (keep every visibility, including null). | Reproducing benchmarks that drop heavily occluded ground truth. | **Per run**, advanced; record. |
| Maximum range (`max_range_m`) | Eval YAML; viewer has its own display range, not the evaluator's. | `null` (no range limit). | Range-limited comparisons; must not be confused with the viewer's 80 m radar. | **Per run**, advanced; record. |
| Score threshold (`score_threshold`) | Eval YAML. | `0.3`. | The operating point for `precision`, `recall` and per-sample counts; papers publish different points. | **Per run**, exposed next to the protocol because it changes reported counts; record. |
| Communication range (`comm_range_m`) | Eval YAML; part of the `default` protocol. | `70.0` m. | Cooperative benchmarks with another connected range (e.g. 50/100 m); `eval compare` refuses different ranges. | **Per run**, advanced; record. |
| Ground-truth scope (`gt_scope`) | Eval YAML; set by the protocol preset. | `connected_union` under `default`, `ego_only` under `legacy`. | Comparing "what my agent saw" with "what the connected agents saw" for a fused output. | **Per run** through the protocol preset; expert override in advanced/config, recorded. |
| Ranking scope (`ranking_scope`) | Eval YAML; set by the protocol preset. | `per_frame` under `default`, `pooled` under `legacy`. | Reproducing OpenCOOD's per-frame AP or KITTI-style pooled AP. | **Per run** through the protocol preset; expert override only, recorded. |
| Categories the model can emit (`run.json categories`) | Model manifest `outputs.categories`; `predictions import --categories` for imported boxes. | The model's manifest list; for import, the classes seen in the file. | A model that emits a class outside the ground-truth vocabulary, or a custom detector whose class names differ. | **Run/model metadata**, not a global setting. Show it in Prepare/New evaluation; edit in the model manifest or at import, never silently at eval time. |
| Class-name mapping (`gt_class_map`) | Eval YAML. | `{}` (no mapping). | Dataset vocabulary differs from the model's (e.g. `truck` → `car`). | **Per run**, advanced/config only; record. |
| Score meaning (`run.json score_note`) | Required in `run.json`; model manifest `outputs.score_note`; `predictions import --score-note`. | Model manifest text. | A model whose score is a logit, sigmoid or calibrated probability; readers must know. | **Run/model metadata**; show it where results are shown. |
| Output sources (`run.json sources`) | Model manifest `outputs.sources`; `predictions import --source-note`. | `single`/`fused` from the manifest. | A model with more than one output or a custom source name. | **Run/model metadata**; not a global setting. |
| Which agents count | Scene agents and their annotations; `gt_scope`/`comm_range_m` for fused ground truth; `eval run --scenes` selects scenes; `eval compare --by agents` groups by annotating-agent count. Viewer agent visibility is display-only. | Every scene agent; fused GT from agents within 70 m. | A benchmark that scores one agent, a subset of scenes, or a different cooperative set. | **Per run** for `gt_scope`/range/scenes (recorded); **per view** for the viewer's agent visibility, never recorded as a score rule. |
| Scenes included (`eval run --scenes`) | CLI flag; `eval report` requires one config across the evaluated scenes. | All scenes with `boxes.parquet`. | A partial or split evaluation; a smoke run on a few scenes. | **Per run/CLI**, recorded in the run's metrics (or the run plan's provenance). |
| Compare breakdown bins (`eval compare --distance-bins`, `--visibility-bins`) | CLI flags. | Distance `0,30,50` m; visibility `0.2,0.6`. | Analysis of where errors occur; does not change the run's headline numbers. | **CLI/config only**, printed in the comparison output; not a Settings item. |
| Failure ranking (`eval failures --top`, `--weights`, `--latency-run`) | CLI flags; default weights in the failures code. | 50 frames, built-in weights. | Inspecting the worst frames or a custom badness weighting. | **CLI/config only**, analysis-only. |
| Eval config file (`eval.yaml`) | `--config FILE`; copied into `metrics.json`. | Spec defaults. | A lab that reuses one benchmark definition across many runs. | **Per run** can start from a saved/shared config; keep the config file as the reproducible artifact. |

## Model runs and plugins

| Setting | Where it lives today | Default | Who would change it, and why | Recommendation |
| --- | --- | --- | --- | --- |
| Plugin interpreter (`--plugin-python`) | Proposed in **open PR #213** (not merged on 2026-11-22): accepted by `model run` (local and `--slurm`), `model check` and `model inspect`. No app field. | Without the flag the plugin manifest's `environment` decides, exactly as before. | A plugin user whose cluster environment has another name/path than the installed manifest (for example a renamed conda env), or a contributor testing another interpreter. | **App Settings (per server)** as an optional default, with a per-run override and the CLI flag for headless use; until the app part exists, CLI/config only. The flag's own PR is the source of truth for validation and provenance. |

## Viewer

Display-only preferences. They may be global; none of them changes a stored
number. Today the app's Settings owns the state-backed preferences, while the
viewer toolbar owns the per-scene appearance in `localStorage` (`coperceive-view`).

| Setting | Where it lives today | Default | Who would change it, and why | Recommendation |
| --- | --- | --- | --- | --- |
| Streaming buffer ahead | Settings → Viewer; `state.json buffer_target_seconds` (`useStreamingSettings`, `web/src/viewer/streamingSettings.ts`). | 3 s; choices 1/3/5 s. | Slow links need more headroom; low-memory machines need less. | **App Settings (global)**, already. |
| Client memory cap | Settings → Viewer; `client_memory_limit_bytes`. | 512 MB; choices 256/512/1024 MB. | Dense point clouds on a small laptop vs a workstation. | **App Settings (global)**, already. |
| Streaming stats line | Settings → Viewer; `show_stream_stats`. | Off. | Diagnosing buffering on a link. | **App Settings (global)**, already. |
| Point size | Viewer toolbar; `localStorage coperceive-view` `pointSize` (`appearance.ts`). | 2 (choices 1–4). | LiDAR legibility on a projector or a high-DPI screen. | **App Settings (global)** as a default, with the toolbar keeping a per-view override. |
| Colour by agent / class / seen | Viewer toolbar; `colorBy`. | `agent`. | Explaining a scene by class, or by which agent sees a box. | **Per view** (it is contextual), with the last choice remembered; optionally a global default. |
| Class/agent colours | Hard-coded palette in `web/src/viewer/appearance.ts` and `seenBy.ts`; style tokens in `viewerStyle.ts`. | Fixed palette, style-specific "seen" colours. | Colour-blind users, print/paper figures, matching a paper's colours. | **Leave fixed for now**; if demand appears, add a global palette setting with accessible defaults. Not a run setting. |
| Theme (dark/light/system) | Viewer toolbar; `theme_override` localStorage + state; follows OS by default (`systemTheme`). | System theme; Classic supports both, other styles are dark-only. | Desktop preference; screen sharing/printing. | **App Settings (global)**, with the toolbar toggle as a shortcut. |
| Default camera view | `state.json last_viewpoint` (last used), initial `orbit`; `viewpoints.ts` lists orbit/bird/chase/drone/camera. | Orbit, or the last used view that the scene supports. | A reviewer who always starts in bird's-eye, or a demo that starts in chase. | **App Settings (global) default + per view last-used**. Keep the scene-capability fallback. |
| Viewer style | Settings → Viewer → Style; state.json; `ViewerStyleSettings`. | Classic. | Presentation (Cinema), dense telemetry (Instrument), quieter chrome (Studio). | **App Settings (global)**, already. |
| Cinema camera drift | Settings → Viewer, only for Cinema; state.json. | On, unless the OS asks for reduced motion. | Motion sensitivity; a still-frame demo. | **App Settings (global)**, already; respect reduced motion. |
| Camera strip mode | Settings → Viewer; `camera_strip`. | All cameras, scroll. | Small screens prefer one agent at a time. | **App Settings (global)**, already. |
| Frustum images, opacity, camera subset | Viewer toolbar / side panel; `appearance.ts`. | Images off; opacity 0.85; all cameras when enabled. | Seeing what a camera saw without leaving the 3D view. | **Per view**; a global default is optional, not needed now. |
| Split view ratio and camera | Viewer; `splitRatio`, `splitCamera`. | 0.58; last camera. | Reading a camera next to the point cloud. | **Per view**, remembered. |
| Radar card, axes, map, boxes, points, box style | Viewer toggles; `appearance.ts`. | Radar on; axes/map/boxes/points on; wire boxes. | Decluttering a scene or a screenshot. | **Per view**, remembered. |
| Hidden classes, prediction score floor | Viewer toolbar; `hiddenClasses`, `predictionScore` (null = the run's `score_threshold`). | No hidden classes; run threshold. | Focusing on one class or one confidence range. | **Per view**; the run threshold stays the recorded default. |
| Units | Fixed output: metres, m/s and km/h (`telemetry.ts`, `sceneStats.ts`); no setting. | Metric. | Users expecting imperial units; papers use metres. | **Leave fixed** for now; if added, a global display setting only (never changes stored values). |
| Continue to next scene; open first ready scene | Settings → Viewer/General; state.json. | Auto-next off; open-first-ready on. | Batch review vs deliberate scene picking. | **App Settings (global)**, already. |
| Reopen last session | Settings → General; state.json. | On. | Kiosk/demo setups that should start at Connect. | **App Settings (global)**, already. |
| Dataset grouping | Settings → Datasets; `dataset_grouping`. | None (one list by name). | Large cluster datasets: group by state or recording day. | **App Settings (global)**, already. |
| Viewer idle timeout | **Not implemented.** Proposed in the v0.1.3 feature-C design note (open PR #222): Settings → Cluster datasets → "Keep the viewer connected after closing"; the local server and SSH stream stay up for an idle timeout after the viewer window closes. | Proposed **10 minutes**, the old viewer-job lease; the default is **pending the phase-0 measurement (unverified)** and is a decision for Adarsh. | A user who closes and reopens a scene: a longer timeout avoids the reconnect wait; a shorter one releases a login-node process sooner. It never runs a Slurm job. | **App Settings (global)** — only if the measurement shows reopening is slow enough to matter; keep the clamp and "Disconnect now", and do not build it before the measurement. |

## Library and cache

| Setting | Where it lives today | Default | Who would change it, and why | Recommendation |
| --- | --- | --- | --- | --- |
| Local library location | Library screen (`setLibraryRoot`/`moveLibraryRoot`); `state.json library_root`; shown in Settings → General. | `~/coperceive-data` (or `$COPERCEIVE_STATE_DIR/coperceive-data`). | Move the library to an external/large disk; shared lab machine. | **App Settings (global)**, with the Library's existing move flow; keep the move confirmation. |
| Local library disk use | Not a setting; Library shows used/free (`get_storage_stats`). | No cap. | Keeping an eye on disk; no automatic eviction today. | **Leave fixed**; show free space, add a warning threshold only if asked. Do not silently delete scenes. |
| Cluster results/cache root | `results_root` in the server profile; the prepare cache is derived as `<results_root>/coperceive-cache/<adapter>/<digest>/<split>` (`cluster_datasets.py`). The UI shows `cache_root` in the Prepare preview; it is not editable. | `results_root` per server, no separate cache root. | Quotas, a scratch cache, or separating results from cache on the cluster. | **Per server profile** (advanced), with the derived path shown; do not add a second global root. A per-prepare override is optional later. |
| Camera-image mode when preparing | Settings → Cluster datasets (per server or every server) and each Prepare preview; `camera_images_default` / `camera_images_by_profile`. | Originals (no extra disk). | Slow links prefer previews; quota-limited clusters prefer originals. | **Per Prepare**, with the Settings default; already. |
| Previews disk estimate and removal | Prepare preview shows `estimated_preview_bytes`; Settings → Cluster datasets shows usage and "Remove previews". | No limit. | Reclaiming results-folder space. | **Per server / per Prepare**, already; a warning above a threshold is reasonable. |
| Viewing cache (SSH) size | Settings → Cluster datasets; `remoteViewCache` cap. | 2 GB; choices 1/2/5/10/20 GB. | Local disk budget for streamed sweeps/images. | **App Settings (global)**, already. |
| Model run cache | CLI `--no-cache`; cache under the run's output; no app checkbox. | Cache on. | Re-running a scene for timing or after a code change; saves GPU time otherwise. | **Per run**, as a checkbox ("reuse scene cache"), recorded in the run; not a global silent default. |
| Dataset downloads | Get Data; lands under the library root; no separate cache root. | Library root. | Disk layout. | **Follows library location**; no new setting. |
| Remote viewer page cache | Hard-coded `~/.cache/coperceive/static/<app-version>` on the login node (`remote_static.rs`). | Per app version. | Cluster home quota. | **Leave fixed** (implementation detail); document it. |

## Cluster

Cluster connection and scheduler fields. Most already live in the server
profile; the question is whether they are global, per server, per run or fixed.

| Setting | Where it lives today | Default | Who would change it, and why | Recommendation |
| --- | --- | --- | --- | --- |
| Server profile: SSH alias/host/user/port/proxy jump | Servers screen (`HpcServers.tsx`); `server_profiles.rs`; public keys come from the user's SSH config. | None; required to connect. | Any cluster user. | **App Settings (per server)**, already (Servers). |
| Server profile: scheduler kind | Servers screen; `scheduler: slurm\|none`. | None/slurm per profile. | A local-only or future scheduler. | **Per server**, already. |
| Server profile: data roots, code roots, results root, scratch template | Servers screen; profile fields. | None; results/scratch required by validation. | Every cluster; the layout differs per site. | **Per server**, already; keep validation (no spaces/#/quotes in results, scratch markers). |
| Server profile: environment (modules, conda/venv/container, coperceive command, wheelhouse, internet) | Servers screen; `EnvironmentSetup`; discovery can prefill. | As discovered; coperceive command defaults to `coperceive`. | Sites with modules, conda envs, containers or a wheelhouse. | **Per server**, with the per-plan environment override already in New evaluation. |
| Default partition | Profile `defaults.partition`; discovered partition list; per-plan Submit Advanced. | None. | Each cluster/partition; a user may switch per run. | **Per server default + per run override**, already. |
| Account, QoS | Profile `defaults.account`/`defaults.qos`; Submit Advanced. | None. | Site allocation accounting; different project accounts per run. | **Per server default + per run override**, already. |
| CPUs, memory, GRES/GPU | Profile defaults; Submit Advanced; model manifest resources fill built-in defaults. | Model manifest for built-in; unset otherwise. | Fitting a job to a partition, or a GPU type the manifest constraint allows. | **Per run** (Submit Advanced), with server/manifest defaults; already. |
| Walltime and time estimate | Plan `time_estimate` from manifest resources/measured timings; Submit Advanced walltime; profile `defaults.time_limit` and partition max. | Model estimate; custom pipeline 4 h fallback; partition limit enforced. | Long/expensive runs; a queue with short limits. | **Per run** (Submit Advanced), already; record the value used in the plan. |
| Viewer-job lifetime | App starts `coperceive remote start` with the profile's `defaults.time_limit` (`main.rs remote_connect`); CLI `--time`, omitted = partition default. | Profile time limit; otherwise partition default. | A viewing session may be much shorter or longer than a model job; queues may want a short walltime. | **Per viewer session** with a **per-server default**; show it when opening a dataset. |
| Viewer-job CPUs/memory | App uses profile `defaults.cpus_per_task`/`defaults.memory`; CLI `--cpus`/`--mem`. | Cluster default when unset. | Serving a large dataset or sharing a login allocation. | **Per viewer session (advanced)** with the server default; optional. |
| Dataset prepare workers/throttle | Prepare preview; clamped 1–16; default `min(scenes, 8)`. | 8 or fewer. | Cluster queue pressure and shared-filesystem load. | **Per Prepare**, already. |
| Dataset prepare walltime/resources | Derived by the Prepare backend; preview shows partition/walltime/CPUs; no editable time field in the app today. | Derived from the estimate. | Very large datasets or a short queue limit. | **Per Prepare (advanced)** if the estimate is wrong; record the value in the prepare plan. |
| Partition discovery / manual partition | Servers → discovery; Submit Advanced. | Discovered list, manual fallback. | A partition not visible to `sinfo` or a new partition. | **Per server**, already. |
| Viewer page upload | Fixed per app version (see Library and cache). | — | — | **Leave fixed**. |
| Remote wait timeout | CLI `remote start --wait-timeout`; app uses 5 s then polls up to 15 min. | 45 s CLI; 5 s app. | Very slow schedulers. | **Leave fixed/internal**; the app already polls. |

## Contributor workflow (not app settings)

| Setting | Where it lives today | Default | Who would change it, and why | Recommendation |
| --- | --- | --- | --- | --- |
| Login-node build limits | `docs/contributing/hpc-dev.md` (merged #218 into v0.1.3): the example `.hpc-env.sh` sets `CARGO_BUILD_JOBS=1`, cold rebuilds run at `nice -n 19`, and `cargo test` is not run on a login node. | One Cargo job; warm `cargo check`/`tsc -b` take seconds, a cold rebuild takes several minutes. Many sites limit login-node commands to about a minute on one core; check the site's rules. | Contributors building on a shared login node, not app users. | **Leave fixed** (contributor documentation only). It is intentionally not an app setting and not chosen per run; CI runs the Rust tests. |

## Updates and diagnostics

| Setting | Where it lives today | Default | Who would change it, and why | Recommendation |
| --- | --- | --- | --- | --- |
| Automatic update check/download | Settings → Updates; `state.json auto_download_updates`; Update banner checks on launch when enabled. | On. | Air-gapped machines, metered links, or a lab that pins a version. | **App Settings (global)**, already. |
| Manual update check | Update banner / app menu (`__COPERCEIVE_CHECK_UPDATES__`; Tauri updater). | Available any time. | Checking after a release. | **Keep as a manual action**, not a setting. |
| Update feed/signature policy | Hard-coded signed GitHub Releases feed; `COPERCEIVE_UPDATE_URL` is a test/enterprise override. | Official signed releases. | A mirror or an enterprise deployment. | **Leave fixed** by policy; an override should stay an environment/deployment decision, not a user setting. |
| Diagnostics content and redaction | `diagnostics.rs`: credential headers, tokens, SSH keys and home paths are redacted; the preview, copied text and zip are built in Rust from redacted content. | Always redacted; no toggle. | Users sharing a report; support. | **Leave fixed (policy)**. Do not offer a "raw diagnostics" switch. |
| Crash reports / logs kept | Crash records under the app state dir; diagnostics includes bounded tails. | Bounded. | Support vs disk. | **Leave fixed**; surface "open logs" rather than a retention setting. |
| Telemetry/analytics | None in this codebase. | — | — | **Leave absent** unless a product decision adds it, with explicit consent. |

## What is missing today (summary)

- The AP protocol is now per run in New evaluation (#211), recorded in the plan
  and `run.json`. The **other** evaluator config keys are still CLI/config-only,
  the app's built-in model job still does not score, and no app screen invokes
  `coperceive eval run`.
- The plugin interpreter override exists only as the CLI flag in open PR #213;
  there is no app field yet.
- No Settings entry for point size, colour-by, theme or default camera view;
  these live in the viewer toolbar/localStorage. They are display-only.
- No library disk cap or automatic eviction; only free/used space is shown.
- No viewer idle timeout yet: the v0.1.3 design (open PR #222) proposes one after
  a phase-0 measurement. The actual dataset view runs no Slurm job; the older
  viewer-job path would use the profile's time limit.
- No telemetry, and diagnostics are deliberately not configurable.

## Decisions this document asks for

1. Should the **other evaluator config keys** (thresholds, criterion, ranges,
   classes) become per-run fields in New evaluation with an "advanced" section,
   or stay a YAML file selected per run? The AP protocol is already per run.
2. Should the app **invoke `eval run`** after a built-in model job (making the
   remaining config reachable and the report visible in-app), or keep scoring as
   a separate CLI step and only record/display the protocol?
3. Should point size, colour-by, theme and default camera view move into
   Settings, or remain per-view viewer controls with remembered state?
4. Is the **viewer idle timeout** wanted (#222), and what default should it have
   after the phase-0 measurement? Is it global or per server?
5. Is a **per-viewer-session walltime** wanted for the older viewer-job path,
   separate from the server profile's model-run time limit?
6. Should the **plugin interpreter** (#213) get an app default per server, or
   stay a CLI/config flag only?
7. Is a library **disk warning/limit** wanted, and should it ever evict, or only
   warn?
