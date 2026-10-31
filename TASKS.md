---
tags: [tasks, coperceive]
created: 2026-10-21
updated: 2026-10-31
repo: https://github.com/adarsh-nl/coperceive
---
# coperceive: master task list

**Repo:** https://github.com/adarsh-nl/coperceive · **Actions:** https://github.com/adarsh-nl/coperceive/actions · **Owner:** Adarsh (adarsh-nl)

> [!info] How to use this file (agents read this first)
> 1. This is the single source of truth for **what is done, what is next, and what is left**. Keep it at the repo root as `TASKS.md` and in Adarsh's Obsidian vault (`01 Tasks/Coperceive Task List.md`).
> 2. Finished item: change `- [ ]` to `- [x]`, wrap the text in `~~ ~~`, and append `(commit sha · CI run link)`. Don't strike anything that hasn't been verified (see Rules).
> 3. New work goes under the right milestone, or under **Backlog**, with a priority tag: `P0` (breaks the core job), `P1` (feels like a prototype), `P2` (polish).
> 4. Update the **Snapshot** block on every PR. Never delete history: struck-through items stay.
> 5. Work top-down: **NOW** before **NEXT**, P0 before P1.

---

## Snapshot (update every PR)
| | |
|---|---|
| Last updated | 2026-10-31 |
| `main` | `0ee039d` (landing page). The desktop app is not on `main`. |
| `feat/desktop` | Merged into `feat/m2-import`. |
| `feat/m2-import` | `0d0865e` (merge of PR #86). Last pre-merge green run [41229196435](https://github.com/adarsh-nl/coperceive/actions/runs/41229196435) (11/11) at `a1f5d35`. |
| `feat/landing` | `d3e43bf`, **not merged**, open items below |
| Version | 0.1.0 (desktop), not released, no tag |
| Repo visibility | appears **public** (Adarsh to confirm this is intended) |
| Current milestone | **M2 completion & cluster dataset visualization** |
| Top blocker | Cluster datasets merge (PR #87 backend + PR #88 UI), then `feat/m2-import` → `main` merge (Adarsh). |
| Open agent branches / PRs | #87 (`agent/deepseek-cluster-datasets-2`), #88 (`agent/agy-cluster-datasets-ui`), this docs PR (`agent/agy-tasks-refresh`). |
| Last merged agent branch | `agent/agy-ci-speed` (CI acceleration, warm cache 5m55s, path filtering, PR-scoped concurrency). PR #86, merge `0d0865e`. |

## Key docs in the repo
- `HANDOVER.md`: long engineering log
- `docs/ci.md`: CI pipeline architecture, caching hit rates, and empirical wall-clock metrics
- `docs/datasets/cluster-datasets.md`: cluster datasets JSON schemas and frozen desktop command contract
- `docs/spec/format.md`: Schema 1 scene format (the contract)
- `docs/adr/`: decisions 0001–0014. Desktop is `0014-desktop-app.md`
- `docs/design/griffin-verification.md`: verified Griffin facts
- `docs/design/viewer-directions-2026-10-18/`: Claude Design source for the Connect backdrop (1b Ledger)
- `scripts/desktop/`: `bundle_python.sh`, `smoke_test.sh`, `e2e_mac.sh` (real-app UI test), `build_dmg.sh`
- `src-tauri/src/`: `main.rs`, `sidecar.rs`, `controller.rs`, `state.rs`, `cluster_datasets.rs`, `job_submit.rs`, `updater.rs`
- `web/src/`: `App.tsx`, `desktop/`, `viewer/Viewer.tsx`, `viewer/playback.ts`, `desktop/backdrop/`

## Rules (non-negotiable)
- **Clean-room:** no code, naming or schema from kesai-labs/py123d or Adarsh's fork, not even his own merged Griffin parser.
- **Data licences:**
  - Griffin is MIT (credit it).
  - CARLA town files are CC-BY assets (0.9.15 README, checked 2026-10-25; version not stated). The simulator code is MIT. Don't commit `.xodr`.
  - **OPV2V and V2XSet frames and images never go in the repo** (licence unknown; see `docs/datasets/opencood.md`).
- **Never invent facts** in docs, the landing page or the UI: numbers, dataset details, claims. Cite a file:line or a measurement.
- **Verification:**
  - "Done" means the real-app e2e test covers it and CI is green with the artifact checked.
  - Reports link the CI run and the artifact.
  - Adarsh's Mac test closes each milestone.
- **Security:** never write tokens to argv, state files, logs or diagnostics. Loopback only. Exact-origin CORS.
- **HPC:** don't compile Tauri on the login node (policy: under 1 minute of work). Slurm partition `ps`.
- **Agents never** push to `main`, merge, tag, publish releases, enable Pages or change repo visibility. Adarsh does those.
- No CLI jargon in the GUI. Every error says what happened and what to do next.

---

## ✅ Done: platform (on `main`)
- [x] ~~Phase 0: repo bootstrap, ADRs 0001–0006, clean-room rule, CI (lint, pyright, pytest 3.10–3.13, gitleaks)~~
- [x] ~~Phase 1: Schema 1 on-disk format (`docs/spec/format.md`): manifest, poses, objects, annotations, point_index, points.bin, images.parquet; `time_us = frame_id × 100000`~~
- [x] ~~Phase 2: Griffin download and convert (`coperceive download/convert`, `--preset dev`, `--accept-griffin-terms`, `--raw-root`)~~
- [x] ~~Phase 3: query API plus PyTorch `CooperativeDataset`~~
- [x] ~~Phase 4: `coperceive validate` (time sync, altitude, cross-agent, `--strict`, `--maps` centreline block)~~
- [x] ~~Phase 5: three.js viewer and Rerun `.rrd` export~~
- [x] ~~Phase 6: web app with playback 0.25–4×, viewpoints (orbit, bird's-eye, chase, drone, camera), camera strip, frustum images, sequence export (MP4/GIF/PNG), figures, view settings~~
- [x] ~~HD maps: `coperceive maps fetch/ingest/align` for CARLA 0.9.15 Town03/06/07/10HD (identity alignment on Griffin-25m)~~
- [x] ~~Dataset adapters: OPV2V and V2XSet (ADR 0009)~~
- [x] ~~Eval engine step A and step B (ADR 0010), live model plugins over NDJSON on stdio (ADR 0011), Griffin with 4 models~~
- [x] ~~Real Griffin eval run (Task 12). Drone and fusion match the paper within 0.001~~

## ✅ Done: desktop app (on `feat/desktop` & merged to `feat/m2-import`)
- [x] ~~ADR 0014: Tauri v2; local mode plus HPC-over-SSH mode; python-build-standalone; unsigned dmg for now~~
- [x] ~~Real Tauri v2 app (not a console stub): window, folder picker, invoke commands, `tauri build` in CI~~
- [x] ~~Bundled Python 3.13.1 (pinned SHA-256) with hash-locked dependencies (`requirements-darwin-arm64.lock`, `--require-hashes`)~~
- [x] ~~CI honesty: `shell: bash` with pipefail, `if-no-files-found: error`, smoke test launches the real binary~~
- [x] ~~Fix: startup panic from `plugins.dialog` config~~
- [x] ~~Fix: viewer stuck on `tauri://` origin. Viewer window now opens at the sidecar origin with the token injected by an init script, never in the URL~~
- [x] ~~Fix: intermittent health-probe timeout (60 s budget, robust HTTP read); logs kept in `~/Library/Logs/Coperceive/`~~
- [x] ~~Fix: hiding an agent left points and frustums drawn (`renderNow` plus scene-graph test)~~
- [x] ~~Fix: status line lagged during playback~~
- [x] ~~Test hooks gated to `MODE === "test"`, absent from the production bundle~~
- [x] ~~`docs/media`: Town03-000 PNGs, hero mp4, README with Griffin MIT / CARLA CC-BY attribution~~
- [x] ~~HPC CLI: `coperceive remote start|status|stop|list|heartbeat` on Slurm, heartbeat lease watchdog, TIMEOUT mapping, token not on argv~~
- [x] ~~Rust `RemoteClient` / `SshTunnel` / `RemoteSessionManager` (ControlMaster, BatchMode), 19 Rust tests~~
- [x] ~~**Mac-verified:** local mode plays Town03-000 with vehicle and drone, 9 cameras, scrub and quit (7ce950e, a42cb9b)~~

## ✅ Done: M1 (verified in code at b99c732, CI 39650235620)
- [x] ~~Rust-side `state.json` (in the app config dir, since localStorage is per-origin with a random port) with auto-resume of folder, scene, sample, viewpoint, layers and agent visibility~~
- [x] ~~Change folder without restarting: ⌘O, Open Recent, drag-drop, "Open…" in the viewer~~
- [x] ~~Folder validation: a scene folder (manifest.json) or a root with ≥1 scene; persistent error with a "Choose another folder" button~~
- [x] ~~Hardened loopback controller: bearer auth, exact-origin CORS, Host check, 64 KB body cap, rate limit~~
- [x] ~~Native menus: File / View / Playback / Help (Show Logs, Copy Diagnostics, Docs, Repo, Report Issue), About~~
- [x] ~~Closing the viewer returns to Connect; Recent datasets list~~
- [x] ~~Developer UI removed (token field, echo job, "Phase 3", `--predictions` text)~~
- [x] ~~Toggles expose `aria-pressed` / `aria-checked`~~
- [x] ~~Frustum images restore correctly (e2e asserts it after relaunch)~~
- [x] ~~`e2e_mac.sh` drives the real UI: layer and agent toggles, viewpoint, quit and relaunch, folder switch, invalid folder, no orphan sidecar after Quit~~
- [x] ~~CI uploads the dmg only (80.1 MB, down from 353 MB)~~
- [x] ~~Animated Connect backdrop from design 1b "Ledger": 60 s, 3-chapter seamless loop, reduced-motion still frame, 30 fps cap, DPR ≤ 2, pause when hidden, media committed~~

## ✅ Done: M2 & Desktop Polish (merged through PR #86 on `feat/m2-import`)
- [x] ~~Storage & Library location: prompt on first use, save `library_root` in `state.json`, show volume free/used space, Settings → Storage (relocate with progress, Show in Finder).~~
- [x] ~~M2a `coperceive scene pack`: self-contained scene, reject path traversal, `.partial` staging, resume on size+sha256, write `COMPLETE.json` last then rename. `--images full|preview|none`. Headroom check.~~
- [x] ~~M2b Import from folder in desktop app: imported scene lands in `library_root`. Cancel leaves resumable `.partial`.~~
- [x] ~~Dataset catalog ("Get data"): versioned `catalog/datasets.yaml`, preset dry-run, headroom check, license checkbox, Pause/Resume/Cancel, auto-open on completion.~~
- [x] ~~Library screen: scene table/grid, metadata, cached background validation badge (`validation_cache` in `state.json`), Open, Finder, Validate now, Delete.~~
- [x] ~~Brand & Icon: lowercase monospace wordmark `coperceive` + orange-red cursor token `#D9482B`. App icon redrawn on standard macOS grid with rounded-rect body and margin (#78 `3c47b36`). Site favicon aligned.~~
- [x] ~~Native Edit menu: Undo, Redo, Cut, Copy, Paste, Select All with keyboard shortcuts (#74 `1c713b3`).~~
- [x] ~~Auto-update: Tauri v2 updater plugin with minisign verification, quiet 404, prompt banner/toast ("Restart to update"), Help → Check for Updates… (#77 `7212bff`). Stripped insecure transport protocol, gated endpoint overrides behind test seams.~~
- [x] ~~Viewer layout & resizable panels: drag handles with clamping and reset, `state.json` persistence, full-screen 3D view (`F` / Esc), wordmark navigation to Library, playback stall bounded to ≤ 350 ms (#80 `eb93acd`).~~
- [x] ~~Town03 map: button layout formatted as full-width secondary button above row actions (#80 `eb93acd`).~~
- [x] ~~Input field hygiene: autocapitalize/autocorrect disabled on all identifier/server inputs (#72 `1c713b3`).~~
- [x] ~~CI pipeline acceleration: Rust cache (`Swatinem/rust-cache@v2`), bundled Python standalone caching, pip wheels cache, path filtering for docs/python-only PRs, PR-scoped concurrency cancellation, warm-cache `desktop-macos` down to 5m 55s (#86 `0d0865e`).~~
- [x] ~~Pre-push hook: `scripts/install_git_hooks.sh` catches ruff/pyright/lint errors locally prior to push (#86 `0d0865e`).~~

## ✅ Done: M3 HPC Foundation (merged through PR #86 on `feat/m2-import`)
- [x] ~~HPC Server Profiles: remote server profiles, read-only bounded detection, path normalization, safe containment, secret-free persistence in `state.json` (#40, #41, #70, #76, #79).~~
- [x] ~~Remote Browse: non-blocking remote folder traversal with symlink handling and directory listing (#39, #83).~~
- [x] ~~Run-Plan Builder & Job Submit: CPU-only and GPU selection per job type, `# No GPU requested`, preview hash + confirm required, sbatch stdin (#42, #52, #81, #82).~~
- [x] ~~Job Monitor & Watch: squeue polling, queue reason in plain language, log streaming, auto-stop on job completion (#52, #84).~~
- [x] ~~Results View & Cache Synchronization: partial-cache serving, remote static artifact serving over SSH tunnel (#60, #84).~~
- [x] ~~Cluster Datasets Specification & Contract: `docs/datasets/cluster-datasets.md` defining JSON schemas and frozen desktop command contract; read-only `dataset_detect` and `dataset_index` Tauri commands (#85 `30d2a82`).~~

---

## 🔴 NOW: Cluster Datasets & Milestone Close-Out (In Order)

1. **Cluster datasets backend (PR 2, DeepSeek):**
   - [ ] Cache staging under `<cache>/.partial/<scene>`, atomic rename, dataset.json index, idempotent, never writes under raw root.
   - [ ] Scheduled CPU prepare job with Slurm preview hash + confirm, status polling document (`coperceive-cluster-scene-status-1`), partial cache serving.
   - PR #87 open on `agent/deepseek-cluster-datasets-2`.

2. **Cluster datasets UI (Antigravity):**
   - [ ] Browse banner (`OPV2V · test · 16 scenes`, licence status notice and links, "Open dataset").
   - [ ] Dataset view: scene table, status chips (`ready`, `preparing`, `not_prepared`, `stale`, `failed`), status polling every ~5s.
   - [ ] Slurm CPU prepare script preview with confirm checkbox, single-scene prepare & auto-open.
   - [ ] Viewer scene dropdown disabling non-ready scenes with `(preparing…)`.
   - PR #88 open on `agent/agy-cluster-datasets-ui` (rebased on `0d0865e`, all Tauri stubs removed, tests green).

3. **End-to-End Verification:**
   - [ ] Real-app e2e against fake-SSH cluster fixtures covering detect → index → prepare single scene → ready → viewer open.
   - [ ] Real `ssh -L` tunnel verification from the Mac over eduVPN on `hpc-head1`.

4. **Milestone Close-Out & Release:**
   - [ ] Merge `feat/m2-import` → `main` (Adarsh).
   - [ ] Tag `v0.1.0` and publish desktop release with DMG on GitHub Releases (Adarsh).

---

## 🟡 LATER: M4, ship 1.0
- [ ] Decision-dependent: code signing and notarisation (see Decisions)
- [ ] P2 · App size: 271 MB installed. Drop matplotlib if only export uses it, strip tests and `__pycache__`
- [ ] P2 · Follow system light/dark; desktop-shell accessibility (keyboard flow, VoiceOver labels, axe on Connect)
- [ ] P2 · Designed empty, loading and error states for every panel
- [ ] Release notes, `v1.0.0` tag, dmg on GitHub Releases (Adarsh tags and publishes)
- [ ] Docs with screenshots; landing download button points at the release

## 🟡 Landing page (`feat/landing`, d3e43bf)
- [x] ~~Vite + React + TS site in `site/`; scripted three.js hero from a real Town03-000 clip~~
- [x] ~~Facts corrected (Griffin = CARLA-AirSim simulation, 9 cameras, real CLI flags, licences); design tokens (black, glass, Geist, #e67828 / #2878dc)~~
- [x] ~~Real occlusion moment (Town03-000 pedestrian track 265 at 1,123,700,000 µs)~~
- [ ] P1 · Lighthouse performance ≥90 (now 59 desktop / 32 mobile): lazy-load three.js with a dynamic import
- [ ] P1 · Remove or source the hardcoded "visibility 1.0"
- [ ] P2 · A real Vercel web-interface-guidelines review with file:line findings
- [ ] P2 · One token source (tokens.ts and tailwind.config.js are duplicated)
- [ ] P2 · Confirm the landing CI job is green; add a real app screenshot from M1
- [ ] Merge plus hosting decision (Adarsh)

## 🟡 Platform and research follow-ups
- [ ] P1 · **v0.1 release of the Python package/CLI** (Phase 7 never completed; check branch `cursor/phase7-release-0452`). CHANGELOG, version bump, README quick start (not on PyPI; name-squat check before publishing)
- [ ] P1 · Vehicle-side mAP gap: ours 0.1985 vs paper 0.1682 (drone and fusion match). Time-boxed: tables over all 47 scenes, then check the root `vehicle-side.pth`
- [x] ~~P2 · Fix flaky `test_cancel_is_answered` properly~~ (Merged in PR #30 `0b44bd9`)
- [x] ~~P1 · `desktop-macos`: `hdiutil: create failed - Resource busy` retry fix~~ (Merged in PR #31 / #86)
- [ ] P2 · `HANDOVER.md` cleanup: 500+ lines of logs. Move the logs to `docs/log/`, keep a 1-page state; point to this file for tasks
- [ ] P2 · Triage stale branches: `chore/post-step-b`, `cursor/handover-roadmap-0452`, `cursor/phase7-release-0452`, `docs/*`, `feat/eval-step-*`, `feat/phase4-validate`, `feat/adapter-opencood` (merge, close or delete; Adarsh approves)
- [ ] P2 · Answer the 25 open questions in `HANDOVER.md` § "Open questions for Adarsh" (thresholds, visibility null vs 1.0, drone lidar calibs, map sample step, …)
- [ ] Deferred · Second real cooperative dataset end-to-end (V2V4Real) after M2

## ❓ Decisions only Adarsh can make
- [x] ~~Code signing: not now. Unsigned dmg; docs explain right-click → Open.~~ (Decided 2026-10-24)
- [x] ~~Auto-update: yes, prompted like the Claude desktop app (GitHub Releases + Tauri v2 updater with minisign keypair).~~ (Decided 2026-10-24)
- [x] ~~Demo data: no built-in demo data. Removed "Sample scene"; replaced by Dataset catalog ("Get data" screen).~~ (Decided 2026-10-24)
- [x] ~~HPC jobs: partition `ps`, CPU only, default walltime 4 h (in Advanced disclosure in M3, never required).~~ (Decided 2026-10-24)
- [x] ~~Name, icon and wordmark: design "4c Cursor" (lowercase monospace `coperceive` + orange-red block cursor `#D9482B`; dark `#141414` icon with white monospace `c`).~~ (Decided 2026-10-24)
- [ ] Repo public: intended? Timing of the public announcement
- [ ] Landing hosting (Vercel / GitHub Pages / university page)

## 🧺 Backlog (new ideas: add here with a priority)
- [ ] P2 · Export from the desktop app (clip, still, BEV figure, `.rrd`) through a native Save dialog
- [ ] P2 · Shareable scene bundle a colleague can open in one double-click
- [ ] P2 · Optional ledger strip on the Connect backdrop (from design 1b)
- [ ] P2 · Viewer redesign directions 1a Stage / 1b Ledger / 1c Two Perspectives (`docs/design/viewer-directions-2026-10-18/`). Pick one for the evaluation view
- [ ] P2 · Windows/Linux builds kept buildable (not shipped for 1.0)

---

## Change log of this file
- 2026-10-31: Refreshed master task list through PR #86 (`0d0865e`). Struck merged HPC workspace milestones (profiles, browsing, run-plan builder, Slurm submit/watch, results sync, cluster datasets detect/index contract PR #85), desktop updater (#77), icon polish (#78), viewer layout with panel resize and full screen (#80), and CI acceleration (#86). Recorded open PRs #87 (cluster datasets backend PR 2) and #88 (cluster datasets UI), and updated milestone roadmap towards M2→main merge and v0.1.0 release.
- 2026-10-27: The `desktop-macos` DMG retry is now capped and tested. `COPERCEIVE_DMG_ATTEMPTS` takes a whole number only (non-integers are rejected before any build with `exit 64`), is clamped to 1..3 with the clamp logged, and defaults to 2; the exact-signature gating is unchanged. Branch `agent/deepseek-hdiutil-flake`.
- 2026-10-27: `desktop-macos` DMG flake fixed in CI only, branch `agent/deepseek-hdiutil-flake`. `scripts/desktop/build_dmg.sh` pre-detaches stale `Coperceive` volumes, clears stale `rw.*.dmg` images, logs `hdiutil info`/`XProtect`/disk, and retries the build once only on that exact signature; `ci.yml` calls it instead of the bare `tauri build`. No product code changed.
- 2026-10-27: Fixed the `test_cancel_is_answered` flake at its source, not with a retry or a longer timeout. The fake plugin's worker owns an unbuffered line reader and checks buffered lines before `select()`, answering a back-to-back cancel immediately. Branch `agent/deepseek-cancel-flake`.
- 2026-10-26: Added real-app e2e steps 5e (Get data Pause / Resume / Cancel, test-mode local job, no network) and 5f (Settings → Change location moves a scene between two temp libraries and updates `state.json`). Struck against the green `desktop-macos` job in run [40318211323](https://github.com/adarsh-nl/coperceive/actions/runs/40318211323) at `737794e` (9/9 jobs green).
- 2026-10-26: Desktop e2e eval hardening. Steps 4 and 5c retry transient controller failures (504/timeout/reset) up to four times with backoff. Branch `agent/deepseek-e2e-eval-retry`.
- 2026-10-26: `scene pack` skips `.DS_Store`, `._*` AppleDouble files and `jobs.sqlite`/`-wal`/`-shm`, and reports the count, bytes and names in one pack note. Branch `agent/deepseek-pack-cruft`.
- 2026-10-26: Green run [40313946425](https://github.com/adarsh-nl/coperceive/actions/runs/40313946425) (9/9) at `2e3343c` on `agent/deepseek-library-validation-badge`.
- 2026-10-26: Cached background validation badge in Library. `coperceive validate` now accepts a scene directory as its root. Branch `agent/deepseek-library-validation-badge`.
- 2026-10-26: Green run [40311840281](https://github.com/adarsh-nl/coperceive/actions/runs/40311840281) (9/9) at `a6d51ca` on `agent/deepseek-realdata-import-check`.
- 2026-10-26: Real-data import check on the local Griffin Town03-000. `coperceive scene pack` no longer copies unlisted image files that sit in a listed frame directory.
- 2026-10-25: CARLA town-file licence checked against the 0.9.15 README. Code is MIT. Assets are CC-BY, with no version in that README. `maps ingest` now records CC-BY. "CC-BY 4.0" stays off the maps button.
- 2026-10-24: Struck e2e steps 2d, 5c, and 5d against green run [40137604227](https://github.com/adarsh-nl/coperceive/actions/runs/40137604227) at `7404f0b` (9/9).
- 2026-10-24: Verified M2 on `feat/m2-import` with all 9 CI jobs green on run [40100332994](https://github.com/adarsh-nl/coperceive/actions/runs/40100332994) (`c50f568`).
- 2026-10-24: Struck M1-5 against green run [40045726680](https://github.com/adarsh-nl/coperceive/actions/runs/40045726680) at `5cadd1b`.
- 2026-10-23: Struck M1-2 and M1-4 against [39923652622](https://github.com/adarsh-nl/coperceive/actions/runs/39923652622) at `d35dddd`.
- 2026-10-22: Struck M1-0, M1-3, and M1-4 against green run [39828222428](https://github.com/adarsh-nl/coperceive/actions/runs/39828222428) at `6ec22ad`.
- 2026-10-21: Created from the full project history.
