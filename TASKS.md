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
> 2. Finished item: change `- [ ]` to `- [x]`, wrap the text in `~~ ~~`, and append `(commit sha · CI run link)` or PR details. Don't strike anything that hasn't been verified (see Rules).
> 3. New work goes under the right milestone, or under **Backlog**, with a priority tag: `P0` (breaks the core job), `P1` (feels like a prototype), `P2` (polish).
> 4. Update the **Snapshot** block on every PR. Never delete history: struck-through items stay.
> 5. Work top-down: **NOW** before **NEXT**, P0 before P1.

---

## Snapshot (update every PR)
| | |
|---|---|
| Last updated | 2026-10-31 |
| `main` | `0ee039d` (landing page). The desktop app is not on `main`. |
| `feat/desktop` | Merged into `feat/m2-import` (PR #51). |
| `feat/m2-import` | `0d0865e` (merge of PR #86). Last pre-merge green run [41229196435](https://github.com/adarsh-nl/coperceive/actions/runs/41229196435) (11/11) at `a1f5d35`. |
| `feat/landing` | `d3e43bf`, **not merged**, open items below |
| Version | 0.1.0 (desktop), not released, no tag |
| Repo visibility | appears **public** (Adarsh to confirm this is intended) |
| Current milestone | **M2 completion & cluster dataset visualization** |
| Top blocker | Cluster datasets merge (PR #87 backend + PR #88 UI), then `feat/m2-import` → `main` merge (Adarsh). |
| Open agent branches / PRs | #87 (`agent/deepseek-cluster-datasets-2`), #88 (`agent/agy-cluster-datasets-ui`), this docs PR #89 (`agent/agy-tasks-refresh`). |
| Last merged agent branch | `agent/agy-ci-speed` (PR #86, merge `0d0865e`). |

## Key docs in the repo
- `HANDOVER.md`: long engineering log
- `docs/ci.md`: CI pipeline architecture, caching hit rates, and empirical wall-clock metrics
- `docs/datasets/cluster-datasets.md`: cluster datasets JSON schemas and frozen desktop command contract
- `docs/spec/format.md`: Schema 1 scene format (the contract)
- `docs/adr/`: decisions 0001–0015. Desktop is `0014-desktop-app.md`
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
- [x] ~~Phase 4: `coperceive validate` (Schema 1, timestamp alignment, point/box ranges, missing files, frame rate)~~
- [x] ~~Phase 5: headless renderer (`coperceive render` -> BEV image, range image, camera projection, camera-overlay figure)~~
- [x] ~~Phase 6: fast browser-based viewer (`coperceive serve`), three.js, WebSocket clock sync, multi-agent dropdown, layer toggles, frustums~~
- [x] ~~ADR 0008: CARLA HD maps ingest, rasterizer, alignment against scene poses~~
- [x] ~~Eval engine step A and step B (ADR 0010), live model plugins over NDJSON on stdio (ADR 0011), Griffin with 4 models~~
- [x] ~~Real Griffin eval run (Task 12). Drone and fusion match the paper within 0.001~~

## ✅ Done: desktop app (on `feat/desktop` & merged to `feat/m2-import`)
- [x] ~~ADR 0014: Tauri v2; local mode plus HPC-over-SSH mode; python-build-standalone; unsigned dmg for now~~
- [x] ~~Real Tauri v2 app (not a console stub): window, folder picker, invoke commands, `tauri build` in CI~~
- [x] ~~Bundled Python 3.13.1 (pinned SHA-256) with hash-locked dependencies (`requirements-darwin-arm64.lock`, `--require-hashes`)~~
- [x] ~~Sidecar lifecycle: start `coperceive serve` on random loopback port, wait for health, write PID, kill on app exit (SIGTERM then SIGKILL), no zombie processes~~
- [x] ~~Dev vs packaged split: `tauri dev` uses system Python or venv; packaged app uses bundled Python runtime~~
- [x] ~~CI: build and test on macOS arm64 runner, real-app smoke test, upload unsigned `.dmg`~~
- [x] ~~Connect screen: local folder picker, server host/port/token, "Connect" transitions to viewer~~
- [x] ~~Viewer integration: bundled viewer assets inside Tauri app (no separate web server needed in packaged mode)~~
- [x] ~~**Mac-verified:** local mode plays Town03-000 with vehicle and drone, 9 cameras, scrub and quit (7ce950e, a42cb9b)~~
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

## ✅ Done: M2 & Desktop Polish (merged on `feat/m2-import`)
- [x] ~~Storage & Library location: prompt on first use, save `library_root` in `state.json`, show volume free/used space, Settings → Storage (relocate with progress, Show in Finder).~~
- [x] ~~M2a `coperceive scene pack`: self-contained scene, reject path traversal, `.partial` staging, resume on size+sha256, write `COMPLETE.json` last then rename. `--images full|preview|none`. Headroom check.~~
- [x] ~~`scene pack` skips macOS and database cruft: `.DS_Store`, `._*` AppleDouble files and `jobs.sqlite` plus `-wal`/`-shm` sidecars skipped and reported in one pack note (PR #28 `6eb08f6`).~~
- [x] ~~M2b Import from folder in desktop app: imported scene lands in `library_root`. Cancel leaves resumable `.partial`.~~
- [x] ~~Dataset catalog ("Get data"): versioned `catalog/datasets.yaml`, preset dry-run, headroom check, license checkbox, Pause/Resume/Cancel, auto-open on completion.~~
- [x] ~~Library screen: scene table/grid, metadata, cached background validation badge (`validation_cache` in `state.json`), Open, Finder, Validate now, Delete (PR #25 `2e3343c`).~~
- [x] ~~Maps in one click: one-click Town03 map download, ingest and align (PR #73 `aa8ed98`).~~
- [x] ~~Brand & Icon: lowercase monospace wordmark `coperceive` + orange-red cursor token `#D9482B`. App icon redrawn on standard macOS grid with rounded-rect body and margin (PR #78 `3c47b36`). Site favicon aligned.~~
- [x] ~~Native Edit menu: Undo, Redo, Cut, Copy, Paste, Select All with keyboard shortcuts (PR #74 `1c713b3`).~~
- [x] ~~Input field hygiene: autocapitalize/autocorrect disabled on all identifier/server inputs (PR #72 `1c713b3`).~~
- [x] ~~Auto-update: Tauri v2 updater plugin with minisign verification, quiet 404, prompt banner/toast ("Restart to update"), Help → Check for Updates… (PR #77 `7212bff`).~~
- [x] ~~Viewer layout & resizable panels: drag handles with clamping and reset, `state.json` persistence, full-screen 3D view (`F` / Esc), wordmark navigation to Library, playback stall bounded to ≤ 350 ms (PR #80 `eb93acd`).~~
- [x] ~~Town03 map button layout: formatted as full-width secondary button above row actions (PR #80 `eb93acd`).~~
- [x] ~~Flaky desktop e2e controller evals: retry transient controller evals with backoff and step 5b port fix (PR #27 `8a39b41`).~~
- [x] ~~CI pipeline acceleration: Rust cache (`Swatinem/rust-cache@v2`), bundled Python standalone caching, pip wheels cache, path filtering for docs/python-only PRs, PR-scoped concurrency cancellation, warm-cache `desktop-macos` down to 5m 55s (PR #86 `0d0865e`).~~

## ✅ Done: M3 HPC Foundation (merged through PR #86 on `feat/m2-import`)
- [x] ~~Remote Server Profiles: cluster profiles, scheduler adapter, SSH hardening, secret-free persistence in `state.json` (PR #40, #41, #70, #76, #79).~~
- [x] ~~Remote Browse: non-blocking remote folder traversal over SSH with symlinks and directory listing (PR #47, #83).~~
- [x] ~~Run-Plan Builder & Job Submit: evaluation run plan for saved HPC servers (nothing submitted), explicit confirmation, GPU default by job type, saved argv propagation (PR #50, #53, #81, #82).~~
- [x] ~~Job Monitor & Watch: watch submitted HPC run (state, logs, cancel), report, open in viewer, run history, full job names for watch (PR #61, #63, #65, #84).~~
- [x] ~~Cluster Datasets Contract: `docs/datasets/cluster-datasets.md` JSON schemas and frozen desktop command contract; read-only `dataset_detect` and `dataset_index` Tauri commands (PR #85 `30d2a82`).~~

---

## 🔴 NOW: Open Items & Next Milestones (In Order)

1. **Cluster datasets backend (PR 2, DeepSeek):**
   - [ ] Cache staging under `<cache>/.partial/<scene>`, atomic rename, `dataset.json` index, idempotent, never writes under raw root.
   - [ ] Scheduled CPU prepare job with Slurm preview hash + confirm, status polling document (`coperceive-cluster-scene-status-1`), partial-cache serving.
   - PR #87 open on `agent/deepseek-cluster-datasets-2`.

2. **Cluster datasets UI (Antigravity):**
   - [ ] Browse banner (`OPV2V · test · 16 scenes`, licence status notice and links, "Open dataset").
   - [ ] Dataset view: scene table, status chips (`ready`, `preparing`, `not_prepared`, `stale`, `failed`), status polling every ~5s.
   - [ ] Slurm CPU prepare script preview with confirm checkbox, single-scene prepare & auto-open.
   - [ ] Viewer scene dropdown disabling non-ready scenes with `(preparing…)`.
   - PR #88 open on `agent/agy-cluster-datasets-ui` (rebased on `0d0865e`, all Tauri stubs removed, tests green, CI run [41235531408]).

3. **M2 Acceptance & Outstanding Items:**
   - [ ] **Mac acceptance (Adarsh):** empty library → Get data → Griffin "One scene with images" (~2.5 GB) → progress with cancel → auto-opens Town03-000 with images and a map, without touching the Terminal.
   - [ ] **Updater acceptance:** install a test release and confirm the banner and Restart (Adarsh tags).
   - [ ] P1 · **Image thumbnails at import:** 640 px JPEG/WebP preview thumbnail at import, full resolution on demand.

4. **HPC Verification & Acceptance:**
   - [ ] **Fake-SSH cluster fixtures e2e:** real-app e2e against fake-SSH cluster fixtures covering detect → index → prepare single scene → ready → viewer open.
   - [ ] **Remote SSH tunnel test:** real `ssh -L` tunnel verification from the Mac over eduVPN on `hpc-head1`.
   - [ ] Acceptance: open Griffin on `/datasets/...` from the Mac without copying; survive a VPN drop; no job left after Quit.

5. **Milestone Close-Out & Release:**
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
- 2026-10-31: Refreshed master task list through PR #86 (`0d0865e`). Struck merged HPC workspace milestones with matching PR citations (server profiles #40, #41, #70, #76, #79; browsing #47, #83; run-plan builder and submit #50, #53, #81, #82; Slurm watch/monitor #61, #63, #65, #84; cluster datasets detect/index contract PR #85), desktop updater (#77), icon polish (#78), viewer layout with panel resize and full screen (#80), maps in one click (#73), e2e eval-retry (#27), scene pack cruft skipping (#28), and CI acceleration (#86). Archived historical M1 close-out and dated corrections under collapsed Archive. Recorded open PRs #87 (cluster datasets backend PR 2) and #88 (cluster datasets UI), open Mac and updater acceptance items, image thumbnails, and roadmap towards M2→main merge and v0.1.0 release.
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

---

## Archive

<details>
<summary>Click to expand historical evidence trail, M1 close-out log, and dated corrections</summary>

### M1 Close-out Log (Historical)
- [x] ~~**M1-0 · P0 · Play "starts then stops".**~~ (`6ec22ad` · [39828222428](https://github.com/adarsh-nl/coperceive/actions/runs/39828222428))
  - [x] ~~Play within the end zone (last 5% or 2 s, whichever is larger) rewinds to 0. One helper shared by the button and Space.~~ (`bf36d0d` · [39688720618](https://github.com/adarsh-nl/coperceive/actions/runs/39688720618))
  - [x] ~~Resume never restores into the end zone.~~ (`bf36d0d` · [39688720618](https://github.com/adarsh-nl/coperceive/actions/runs/39688720618))
  - [x] ~~Loop defaults ON for scenes under 60 s; the user's choice persists in `state.json` `last_loop`. e2e step 2b: loop off, quit, relaunch, still off.~~ (`f3820a0` · [39828222428](https://github.com/adarsh-nl/coperceive/actions/runs/39828222428))
  - [x] ~~Loop off at the end shows a Replay state on the play button.~~ (`bf36d0d` · [39688720618](https://github.com/adarsh-nl/coperceive/actions/runs/39688720618))
  - [x] ~~Playback never pauses while images load.~~ The clock is `setTimeout` from `indexAfter` (`Viewer.tsx` playback effect). Image decode is a separate effect. `paintWait` is only used by export. (`bf36d0d` · [39688720618](https://github.com/adarsh-nl/coperceive/actions/runs/39688720618))
  - [x] ~~e2e: resume at 148/149 → Play → still playing 3 s later (≥25 samples); Replay flow; 15 s across the wrap with no gap over 300 ms.~~ (`bf36d0d` · [39688720618](https://github.com/adarsh-nl/coperceive/actions/runs/39688720618))
- [x] ~~**M1-1 · P1 · Tighten serve CORS** (c4358b4/2c90678 allow any localhost port plus credentials). Use the exact viewer origin via `--allow-origin`, `allow_credentials=False`, minimal methods and headers. Or navigate the viewer to the new sidecar origin on switch and drop CORS. Add tests for a foreign origin and the real origin.~~ (`bf36d0d` · [39688720618](https://github.com/adarsh-nl/coperceive/actions/runs/39688720618))
- [x] ~~**M1-2 · P1 · e2e for scene → multi-scene root switch** (the case that failed on the Mac).~~ Step 3b asserts the layer flags, agent visibility, and viewpoint from before the switch, and that every tagged mesh or point cloud belongs to the new scene's load and matches that scene's frame 0 counts. The desktop job is green, so those asserts passed. (`d35dddd` · [39923652622](https://github.com/adarsh-nl/coperceive/actions/runs/39923652622))
- [x] ~~**M1-3 · P2 · Validation requires `COMPLETE.json`** next to `manifest.json`.~~ Rust `validate_dataset_root` and Python `serve` / `GET /api/scenes` both require the pair. (`f3820a0` · [39828222428](https://github.com/adarsh-nl/coperceive/actions/runs/39828222428))
- [x] ~~**M1-4 · P2 · Report the backdrop's measured CPU** (target: median under 5% of one core at 1280×800, animated).~~ `COPERCEIVE_FORCE_MOTION=1` with `COPERCEIVE_TEST_ENV`, first 5 s dropped, 60 samples. Median 0.6, p95 0.8, motion `animated`. Numbers in `docs/media/measurements/backdrop-cpu.md`. The [39828222428](https://github.com/adarsh-nl/coperceive/actions/runs/39828222428) samples do not count (`reduceMotion=1`). (`d35dddd` · [39923652622](https://github.com/adarsh-nl/coperceive/actions/runs/39923652622))
- [x] ~~**M1-5 · Mac acceptance (Adarsh, or Claude via computer use):**~~ (`5cadd1b` · [40045726680](https://github.com/adarsh-nl/coperceive/actions/runs/40045726680))
  - [x] ~~Demo flow: reopen → Play → keeps playing and looping~~
  - [x] ~~10 consecutive cold starts with no timeout~~
  - [x] ~~Resume across quit and relaunch (all settings)~~
  - [x] ~~Wrong folder → recover without restarting; switch scene ↔ root~~
  - [x] ~~Help → Copy Diagnostics: version, commit, token redacted~~
  - [x] ~~`ps aux | grep -i "coperceive serve" | grep -v grep` is empty after Quit~~
  - [x] ~~Backdrop looks right and loops seamlessly~~
  - [x] ~~Window frame invariance across 3 relaunches without physical/logical coordinate doubling~~

### Historical Dated Corrections & Notes

#### Correction, 2026-10-24
The strikes above describe code that is in the tree. They were written before a green run of the new real-app steps. Do not read them as e2e-verified. These stay open until `desktop-macos` is green on this checkpoint:

- [x] ~~P0 · e2e step 5c: import, cancel, resume, open.~~ (`7404f0b` · [40137604227](https://github.com/adarsh-nl/coperceive/actions/runs/40137604227)) Notice: `Step 5c verified: import, cancel, resume, open.`
- [x] ~~P1 · e2e step 2d: one viewer window after relaunch.~~ (`7404f0b` · [40137604227](https://github.com/adarsh-nl/coperceive/actions/runs/40137604227)) Notice: `Step 2d verified: one viewer window after relaunch.`
- [x] ~~P1 · e2e step 5d: mock `latest.json`, banner, What's new, Later, Restart now.~~ (`7404f0b` · [40137604227](https://github.com/adarsh-nl/coperceive/actions/runs/40137604227)) Notices: `Step 5d notices: banner coperceive 0.2.0 is ready`, `What's new showed the mock notes`, `Restart now stayed in the test app`, `Later hid the banner`, `mock server received latest.json`.
- [ ] P1 · Tauri updater plugin is not a Cargo dependency. `src-tauri/tauri.conf.json` `plugins.updater.pubkey` is the placeholder `PASTE_THE_PUBLIC_KEY_FROM_A_NEW_TAURI_SIGNER_KEYPAIR`. `docs/updater-secrets.md` is removed. The keypair committed in `e7f6fef` is public. Adarsh generates a new pair and pastes it as `docs/desktop/updates.md` says. Do not reuse the old key.
- [ ] P1 · **Maps in one click** is not started. Checked 2026-10-25: the CARLA 0.9.15 README (`https://github.com/carla-simulator/carla/blob/0.9.15/README.md`) says code is MIT and assets are CC-BY, with no version. `docs/adr/0008-hd-map.md` and `coperceive maps ingest` now record CC-BY for the town files. Do not write "CC-BY 4.0" on the button: the README does not say 4.0. Do not download the CARLA tarball or commit a `.xodr`.
- [ ] P0 · Mac acceptance (Adarsh): empty library → Get data → Griffin "One scene with images" → Town03-000 with images and a map, no Terminal. A test release shows the banner and installs on Restart.

Griffin byte counts are not stored as UI text. A dry-run on 2026-10-24 of `https://huggingface.co/api/datasets/wjh-svm/Griffin/tree/main/datasets/griffin_50scenes_25m` listed 15 files summing to 167,190,016,122 bytes (same total as `docs/design/griffin-verification.md`, tree API, 2026-10-15). Dev tier (`md5.txt` + `drone_metadata.zip` + `vehicle_metadata.zip` + `vehicle_lidar.zip`) was 239,749,191 bytes. RGB cameras plus those four files, excluding the two instance-segmentation zips, was 162,515,011,954 bytes. The on-disk check uses `required_free_bytes` from the live dry-run, not these integers. Licence: toolkit `LICENSE.txt` fetched 2026-10-24 starts "MIT License" / "Copyright (c) [2025] [Jiahao Wang]"; the dataset card YAML says `license: mit`. Paper `https://arxiv.org/abs/2503.06983`.

Loop preference (M1 leftover): the desktop viewer already stores `last_loop` in Rust `state.json` (`web/src/viewer/Viewer.tsx` `savedLoopChoice` / `handleToggleLoop`, `src-tauri/src/controller.rs`, `src-tauri/src/state.rs`). e2e step 2b already quits and relaunches with loop off. The fixed-port web app still uses `localStorage`. No further change in this checkpoint.

#### Correction, 2026-10-26
Two M2 flows have code and UI but no real-app coverage. Steps 5e and 5f below drive them through the real UI. Struck against the green `desktop-macos` job in run [40318211323](https://github.com/adarsh-nl/coperceive/actions/runs/40318211323) at `737794e` (all 9 jobs green; log: `Step 5e verified` / `Step 5f verified` / `Real-App macOS E2E Test PASSED`). Both use runtime-gated Rust seams (`src-tauri/src/test_seams.rs`) that are inert unless `COPERCEIVE_TEST_ENV` / `COPERCEIVE_TEST_MARKER_FILE` is set; the app is never driven with network data in these steps.

- [x] ~~P1 · e2e step 5e: Get data Pause / Resume / Cancel of a dataset download job. Asserts the real labels and notes from `GetDataScreen.tsx` (`Pause`, `Resume`, `Cancel`, `Download paused.`, `Download resumed.`), that progress freezes while paused and advances after resume, and that the job child is stopped (SIGSTOP) and gone after Cancel (SIGTERM). Dry-run and the job are local in test mode; no Hugging Face request.~~ (`737794e` · [40318211323](https://github.com/adarsh-nl/coperceive/actions/runs/40318211323)) Notices: `Step 5e notices: Pause -> 'Download paused.', progress frozen at 7%`, `fake job pid 13384 state after Pause: T<`, `Resume -> 'Download resumed.'`, `resume restored the Pause button, progress advanced 7% -> 8%`, `Cancel removed the progress panel and returned to Start Download`, `Step 5e verified: Pause, Resume and Cancel of a dataset download job in Get data.`
- [x] ~~P1 · e2e step 5f: Settings → Change location (Move library). Seeds a temp library from `tests/fixtures/synthetic_scene/synth_001`, moves it to a second temp root through the real "Select New Folder…" picker seam, then asserts the scene moved, the old root is empty with no `.partial` left behind, `state.json` `library_root` updated, and the Library screen still lists the scene. `state.json` is restored afterwards; a real `~/coperceive-data` library is never the source.~~ (`737794e` · [40318211323](https://github.com/adarsh-nl/coperceive/actions/runs/40318211323)) Notices: `Step 5f notices: Library now lists synth_001 under /tmp/coperceive-e2e-*/e2e-library-b`, `Step 5f notices: state.json library_root=/tmp/coperceive-e2e-*/e2e-library-b`, `Step 5f verified: Move library relocated synth_001 to the new root, left the old root empty, and updated state.json.`

</details>
