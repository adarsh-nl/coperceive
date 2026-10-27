---
tags: [tasks, coperceive]
created: 2026-10-21
updated: 2026-10-23
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
| Last updated | 2026-10-26 |
| `main` | `0ee039d` (landing page). The desktop app is not on `main`. |
| `feat/desktop` | `5d5c98b`. 79 commits ahead of `main`, 1 behind. Not merged. Do not rebase `feat/m2-import` onto `main` until Adarsh merges this. |
| `feat/m2-import` | `8056ef6` (merge of PR #24). Last pre-merge green run [40311840281](https://github.com/adarsh-nl/coperceive/actions/runs/40311840281) (9/9) at `a6d51ca`; the merge-tip run is CI 40324034567. |
| `feat/landing` | `d3e43bf`, **not merged**, open items below |
| Version | 0.1.0 (desktop), not released, no tag |
| Repo visibility | appears **public** (Adarsh to confirm this is intended) |
| Current milestone | **M2: data without the terminal** |
| Top blocker | **M1-6 merge `feat/desktop` → `main`** (Adarsh). Do not rebase `feat/m2-import` until that merge. |
| Open agent branch | `agent/deepseek-library-validation-badge` (cached background validation badge in Library). Draft PR #25; green run [40313946425](https://github.com/adarsh-nl/coperceive/actions/runs/40313946425) (9/9) at `2e3343c`; this branch now carries the `8056ef6` base (#24 merged). |

## Key docs in the repo
- `HANDOVER.md`: long engineering log (cleanup task below)
- `docs/spec/format.md`: Schema 1 scene format (the contract)
- `docs/adr/`: decisions 0001–0014. Desktop is `0014-desktop-app.md`
- `docs/design/griffin-verification.md`: verified Griffin facts
- `docs/design/viewer-directions-2026-10-18/`: Claude Design source for the Connect backdrop (1b Ledger)
- `scripts/desktop/`: `bundle_python.sh`, `smoke_test.sh`, `e2e_mac.sh` (real-app UI test)
- `src-tauri/src/`: `main.rs`, `sidecar.rs`, `controller.rs`, `state.rs`
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

## ✅ Done: desktop app (on `feat/desktop`)
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

---

## 🔴 NOW: M1 close-out (do in this order)
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
- [ ] **M1-6 · Merge `feat/desktop` → `main`** (Adarsh), after M1-5 passes.

## 🟡 NEXT: M2, data without the terminal
- [x] ~~P0 · **Storage & Library location:** prompt on first use ("Where should coperceive keep datasets?", default `~/coperceive-data`), save `library_root` in `state.json`, show volume free space & used space, Settings → Storage (relocate with progress, Show in Finder).~~
- [x] ~~P0 · **M2a · `coperceive scene pack`:** self-contained scene (every scene file, plus every `images.parquet` path materialised as a real file). Reject absolute paths and `..`. Symlink targets must stay inside scene dir, media root, or raw Griffin root. Dangling links are per-file errors. Write `<dest>/<scene>.partial/` with `IMPORT.json`, resume on size+sha256, write `COMPLETE.json` last, then rename. `--images full|preview|none`. Dry-run byte count. Free-space check (10% margin). Progress NDJSON.~~
- [x] ~~P0 · **M2b · Import from folder…** in desktop app: imported scene lands in `library_root`. Cancel leaves resumable `.partial` (hidden from scene list).~~
- [x] ~~P0 · **Dataset catalog ("Get data"):** prominent on Connect when library is empty, File → Get Data….~~
  - [x] ~~Versioned `coperceive/catalog/datasets.yaml` (Griffin, OPV2V, V2XSet, V2V4Real).~~
  - [x] ~~Cards: name, description, modalities, agents, linked licence/source/paper, preset sizes via dry-run, download button.~~
  - [x] ~~Griffin presets: Dev tier (~240 MB), One scene with images, Full 25 m (~167 GB). MIT licence acknowledgement checkbox (`--accept-griffin-terms`).~~
  - [x] ~~OPV2V / V2XSet: honest status, "Import a local copy" folder picker + convert.~~
  - [x] ~~Download flow: preset dry-run → 10% headroom check → licence terms → sidecar NDJSON progress (download → md5 verify → extract → convert → validate) with Pause / Resume / Cancel → auto-open first scene on completion. Quit-resumable.~~
- [x] ~~P0 · **Library screen:** scene list/grid in `library_root`: dataset, town, duration, agents, sensors, size, has-images, has-map, validate badge. Actions: Open, Show in Finder, Validate now, Delete (to Trash).~~
- [x] ~~P0 · **Brand / Icon ("4c Cursor"):** lowercase monospace wordmark `coperceive` + orange-red cursor token `#D9482B`. App icon `#141414` with white `c` and block cursor. Generate `.icns` and Tauri icons from SVG master in `assets/brand/`. Replace blue-dot everywhere. Cursor blinks (1 s steps) on Connect screen; static under reduced motion.~~
- [x] ~~P1 · **Auto-update:** Tauri v2 updater with minisign keypair. Prompted like Claude app: background download, non-blocking banner/toast ("Restart to update"), Later/Restart, Help → Check for Updates…, Settings toggle. `release.yml` for `v*` tags.~~
- [ ] P1 · **Maps in one click:** not started. See the correction below before writing a licence on the button.
- [ ] P1 · Image thumbnails at import (640 px JPEG/WebP), full resolution on demand.
- [x] ~~P0 · **Real-data import check on the local Griffin Town03** (`~/coperceive-data/Town03-000`: 6.9 GB, 149 samples, 2 agents, 9 cameras; MacBook Air M3, 16 GB, macOS 26.5.2). Before the fix `scene pack` planned 2,521 files / 7,379,734,227 bytes, including 1,170 PNGs (3,380,385,023 bytes) under `drone-side/camera/right` that no `images.parquet` row names: frames from other scenes. `coperceive scene pack` now copies only the image files a scene lists, inside a listed frame directory. Measured on the real scene: full import 1,351 files / 3,999,349,204 bytes in 8.85 s (2.00 s user, 1.78 s sys, 97 MB peak RSS); `--images none` 10 files / 7,578,865 bytes; dry-run reports the 1,170 skipped frames in a note. `coperceive validate` on the packed scene: 1 scene, 0 failed, 0 missing frames, 0 warnings, 0 hard failures, 0.70 s. The packed scene opens in the app: 149 samples, 2 agents, 9 frustums, 55 boxes and 2,911 points at 111.85 s, playback advances at 1× with loop on. e2e step 5c now imports a copy with one unlisted frame beside a listed frame directory and asserts it is not copied (`IMPORT.json` note). e2e step 5c printed `skipped 1 image files not listed in images.parquet`. (`a6d51ca` · [40311840281](https://github.com/adarsh-nl/coperceive/actions/runs/40311840281))~~
- [x] ~~P0 · **Cached background validation badge in Library:** the badge now comes from a stored `coperceive validate` answer, not only from the Validate now button. `validation_cache` in `state.json` maps a scene directory to `{valid, manifest_mtime_ms}`; listing the library fills `is_valid` from the cache and asks for a background check of the scenes that have no fresh answer, one check at a time. The answer is reused while `manifest.json` is unchanged. This also fixes the single-scene bug that made the old button always report valid: `coperceive validate <scene dir>` now reads that scene (it used to find zero scenes and exit 0). CI printed `Step 5g cache={'valid': True, 'manifest_mtime_ms': ...}` and `Step 5g verified: Library badge ran in the background and its answer is cached.` (`2e3343c` · [40313946425](https://github.com/adarsh-nl/coperceive/actions/runs/40313946425))~~

- [ ] P2 · **`scene pack` skips macOS and database cruft:** `.DS_Store`, `._*` AppleDouble files and `jobs.sqlite` plus `-wal`/`-shm` sidecars are skipped and reported in one pack note instead of becoming scene files. On the local Town03-000, full import drops from 1,351 files / 3,999,349,204 bytes to 1,348 files / 3,999,324,620 bytes, and `--images none` from 10 files / 7,578,865 bytes to 7 files / 7,554,281 bytes. The test copies `tests/fixtures/synthetic_scene/synth_001` and seeds the cruft. Branch `agent/deepseek-pack-cruft`; PR/CI not yet recorded.

- [ ] Acceptance: from an empty Mac, Get data → Griffin "One scene with images" → Town03-000 plays with images and map, **without Terminal**. Update prompt verified.

### Correction, 2026-10-24

The strikes above describe code that is in the tree. They were written before a green run of the new real-app steps. Do not read them as e2e-verified. These stay open until `desktop-macos` is green on this checkpoint:

- [x] ~~P0 · e2e step 5c: import, cancel, resume, open.~~ (`7404f0b` · [40137604227](https://github.com/adarsh-nl/coperceive/actions/runs/40137604227)) Notice: `Step 5c verified: import, cancel, resume, open.`
- [x] ~~P1 · e2e step 2d: one viewer window after relaunch.~~ (`7404f0b` · [40137604227](https://github.com/adarsh-nl/coperceive/actions/runs/40137604227)) Notice: `Step 2d verified: one viewer window after relaunch.`
- [x] ~~P1 · e2e step 5d: mock `latest.json`, banner, What's new, Later, Restart now.~~ (`7404f0b` · [40137604227](https://github.com/adarsh-nl/coperceive/actions/runs/40137604227)) Notices: `Step 5d notices: banner coperceive 0.2.0 is ready`, `What's new showed the mock notes`, `Restart now stayed in the test app`, `Later hid the banner`, `mock server received latest.json`.
- [ ] P1 · Tauri updater plugin is not a Cargo dependency. `src-tauri/tauri.conf.json` `plugins.updater.pubkey` is the placeholder `PASTE_THE_PUBLIC_KEY_FROM_A_NEW_TAURI_SIGNER_KEYPAIR`. `docs/updater-secrets.md` is removed. The keypair committed in `e7f6fef` is public. Adarsh generates a new pair and pastes it as `docs/desktop/updates.md` says. Do not reuse the old key.
- [ ] P1 · **Maps in one click** is not started. Checked 2026-10-25: the CARLA 0.9.15 README (`https://github.com/carla-simulator/carla/blob/0.9.15/README.md`) says code is MIT and assets are CC-BY, with no version. `docs/adr/0008-hd-map.md` and `coperceive maps ingest` now record CC-BY for the town files. Do not write "CC-BY 4.0" on the button: the README does not say 4.0. Do not download the CARLA tarball or commit a `.xodr`.
- [ ] P0 · Mac acceptance (Adarsh): empty library → Get data → Griffin "One scene with images" → Town03-000 with images and a map, no Terminal. A test release shows the banner and installs on Restart.

Griffin byte counts are not stored as UI text. A dry-run on 2026-10-24 of `https://huggingface.co/api/datasets/wjh-svm/Griffin/tree/main/datasets/griffin_50scenes_25m` listed 15 files summing to 167,190,016,122 bytes (same total as `docs/design/griffin-verification.md`, tree API, 2026-10-15). Dev tier (`md5.txt` + `drone_metadata.zip` + `vehicle_metadata.zip` + `vehicle_lidar.zip`) was 239,749,191 bytes. RGB cameras plus those four files, excluding the two instance-segmentation zips, was 162,515,011,954 bytes. The on-disk check uses `required_free_bytes` from the live dry-run, not these integers. Licence: toolkit `LICENSE.txt` fetched 2026-10-24 starts "MIT License" / "Copyright (c) [2025] [Jiahao Wang]"; the dataset card YAML says `license: mit`. Paper `https://arxiv.org/abs/2503.06983`.

Loop preference (M1 leftover): the desktop viewer already stores `last_loop` in Rust `state.json` (`web/src/viewer/Viewer.tsx` `savedLoopChoice` / `handleToggleLoop`, `src-tauri/src/controller.rs`, `src-tauri/src/state.rs`). e2e step 2b already quits and relaunches with loop off. The fixed-port web app still uses `localStorage`. No further change in this checkpoint.

## 🟡 LATER: M3, HPC mode in the app
- [ ] P0 · Connect screen HPC target: saved hosts, queue status ("Waiting for a CPU node: Priority, 0:42"), heartbeat and reattach, walltime warning, orphan cleanup on launch
- [ ] P0 · **Real `ssh -L` tunnel test from the Mac over eduVPN**, including a VPN drop and recovery (never tested; the login node can't self-SSH)
- [ ] P1 · Run validate and eval jobs on the cluster from the app; read-only `squeue` status
- [ ] Acceptance: open Griffin on `/datasets/...` from the Mac without copying; survive a VPN drop; no job left after Quit

## 🟡 LATER: M4, ship 1.0
- [ ] Decision-dependent: code signing and notarisation (see Decisions)
- [ ] Auto-update (Tauri updater, signed manifest on GitHub Releases)
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
- [ ] P2 · Fix flaky `test_cancel_is_answered` properly — root cause found and fixed in `tests/fake_model_plugin/worker.py`: the worker mixed a buffered `sys.stdin.readline()` with `select.select([sys.stdin], ...)`, so a cancel that arrived in the same pipe write as the frame was pulled into Python's stdin buffer, where `select()` on the descriptor cannot see it. The worker then slept the full 5 s and the parent's `receive(timeout_s=5.0)` hit the same boundary as the sleep, so the test failed with `RunnerTimeout`. The worker now reads stdin through its own unbuffered line reader and checks that buffer before waiting on the descriptor (event-driven on real state; no retries, no extra sleeps, no longer timeouts). `test_cancel_written_with_the_frame_is_read` pins the worst case deterministically. CI evidence (three consecutive green `test (3.12)` and `test (3.13)` jobs) is recorded in the PR; strike after Lead confirms.
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
- 2026-10-27: Fixed the `test_cancel_is_answered` flake at its source, not with a retry or a longer timeout. The fake plugin's worker read stdin with buffered `readline()` and then waited with `select.select([sys.stdin], ...)`; when the parent put the frame and its cancel in the pipe together (`tests/test_model_runner.py:214-215`), the cancel was already in Python's stdin buffer, `select()` saw no readable descriptor, the worker slept its full 5 s and the parent's 5 s `receive` timed out (`coperceive/models/runner.py:277`). The worker now owns an unbuffered line reader and checks buffered lines before `select()`, answering a back-to-back cancel immediately; a regression test writes frame + cancel in one `write()`. No production runner change was needed. Branch `agent/deepseek-cancel-flake`.
- 2026-10-26: `scene pack` skips `.DS_Store`, `._*` AppleDouble files and `jobs.sqlite`/`-wal`/`-shm`, and reports the count, bytes and names in one pack note. Town03-000 full dry-run drops 3 files / 24,584 bytes. Branch `agent/deepseek-pack-cruft`.
- 2026-10-26: Green run [40313946425](https://github.com/adarsh-nl/coperceive/actions/runs/40313946425) (9/9) at `2e3343c` on `agent/deepseek-library-validation-badge`. The desktop job printed `Step 5g verified: Library badge ran in the background and its answer is cached.` and `Step 5g cache={'valid': True, 'manifest_mtime_ms': ...}`, so the cached badge runs in the real app. The run built the app from the Rust changes.
- 2026-10-26: Cached background validation badge in Library. `coperceive validate` now accepts a scene directory as its root (it found zero scenes and exited 0 before). The desktop app stores `{valid, manifest_mtime_ms}` per scene in `state.json` and validates stale scenes in the background; the Library badge reads the cached answer and shows `Validating…` until it lands. e2e step 5g opens Library and asserts the badge becomes `Valid ✓` without clicking Validate now and that `state.json` holds the cached answer. Branch `agent/deepseek-library-validation-badge`.

- 2026-10-26: Green run [40311840281](https://github.com/adarsh-nl/coperceive/actions/runs/40311840281) (9/9) at `a6d51ca` on `agent/deepseek-realdata-import-check` (based on `feat/m2-import`), real-data import fix. The desktop job built the app and printed `Step 5c skipped the unlisted frame (['skipped 1 image files not listed in images.parquet (61 bytes) in: vehicle/cam_front'])`, so the new import guard runs in the real app. Artifact `coperceive-desktop-macos-arm64` uploaded.
- 2026-10-26: Real-data import check on the local Griffin Town03-000. `coperceive scene pack` no longer copies unlisted image files that sit in a listed frame directory: the local scene had 1,170 such PNGs (3,380,385,023 bytes) in `drone-side/camera/right`, frames 11472–28340 from other scenes. Full import fell from 7,379,734,227 to 3,999,349,204 bytes (1,351 files, 8.85 s). `coperceive validate` on the packed scene: 0 failed, 0 missing frames, 0 warnings in 0.70 s. Real-data note: `point_index.parquet` has only `vehicle/lidar_top` (149 samples, 2,918–2,929 points per frame); the three drone lidar sensors return HTTP 200 with an empty body, and the 9 cameras each have all 149 frames. e2e step 5c now imports a copy with one unlisted frame and asserts it is not copied. Branch `agent/deepseek-realdata-import-check`.

- 2026-10-25: CARLA town-file licence checked against the 0.9.15 README. Code is MIT. Assets are CC-BY, with no version in that README. `maps ingest` now records CC-BY. "CC-BY 4.0" stays off the maps button.
- 2026-10-24: Struck e2e steps 2d, 5c, and 5d against green run [40137604227](https://github.com/adarsh-nl/coperceive/actions/runs/40137604227) at `7404f0b` (9/9). Artifact `coperceive-desktop-macos-arm64` 84,127,425 bytes. DMG `Coperceive_0.1.0_aarch64.dmg` 84,439,707 bytes.
- 2026-10-24: Run [40136895416](https://github.com/adarsh-nl/coperceive/actions/runs/40136895416) at `1ee6a8c` verified step 5c in the log and failed step 5d with an eval HTTP 504. Lint, web, pytest, gitleaks, and browser e2e passed. No dmg uploaded.
- 2026-10-24: Run [40136197303](https://github.com/adarsh-nl/coperceive/actions/runs/40136197303) at `acaae7f` failed lint (`ruff format`) and desktop e2e step 5c (cancel before the first copied file). Step 2d passed in that log. Web, pytest, gitleaks, and browser e2e passed. No dmg uploaded.
- 2026-10-24: Snapshot corrected to `main` `0ee039d`, `feat/desktop` `5d5c98b` (not merged), `feat/m2-import` `db8aa3b` (parent `bc23b26`). Recorded that the M2 strikes were ahead of e2e. Griffin sizes cited from the Hugging Face tree API on 2026-10-24. Removed `docs/updater-secrets.md` and replaced the burned updater public key with a placeholder. New e2e steps 2d, 5c, and 5d are written and not yet green. Last green run remains [40100332994](https://github.com/adarsh-nl/coperceive/actions/runs/40100332994) at `c50f568`.
- 2026-10-24: Verified M2 on `feat/m2-import` with all 9 CI jobs green on run [40100332994](https://github.com/adarsh-nl/coperceive/actions/runs/40100332994) (`c50f568`). Synchronously initialized viewpoint, appearance, scene ID and hidden agents from initial state to prevent premature state overwrite; verified real-app macOS UI e2e and backdrop CPU measurement with dmg artifact `coperceive-desktop-macos-arm64` (80.19 MB).
- 2026-10-24: Struck M1-5 against green run [40045726680](https://github.com/adarsh-nl/coperceive/actions/runs/40045726680) at `5cadd1b`. Fixed window position/size Retina scale doubling bug by saving/restoring in logical points with monitor visible frame clamping, added round-trip/clamping unit tests, and verified window frame unchanged across 3 relaunches in e2e step 2c.
- 2026-10-23: Struck M1-2 and M1-4 against [39923652622](https://github.com/adarsh-nl/coperceive/actions/runs/39923652622) at `d35dddd`. Animated backdrop median 0.6%cpu, p95 0.8. Recorded in `docs/media/measurements/backdrop-cpu.md`.
- 2026-10-23: Un-struck M1-4. Run [39828222428](https://github.com/adarsh-nl/coperceive/actions/runs/39828222428) sampled the static frame (`reduceMotion=1`). Split Import scene into M2a (`scene pack`), M2b (Import from folder), and M2c (Import from HPC, plan only until M2b).
- 2026-10-22: Struck M1-0, M1-3, and M1-4 against green run [39828222428](https://github.com/adarsh-nl/coperceive/actions/runs/39828222428) at `6ec22ad`. M1-2, M1-5, and M1-6 stay open.
- 2026-10-22: Checked the 2026-10-21 list against `feat/desktop` `bf36d0d` and CI run 39688720618. Struck M1-0 sub-items and M1-1 that the green run already covers. Loop persistence, the Python `COMPLETE.json` check, and the 60 s CPU sampler are in `f3820a0` and stay open until that run is green. M1-2 stays open. Corrected the snapshot (`b99c732` / 55 commits / run 39650235620 were stale).
- 2026-10-21: Created from the full project history (py123d check → standalone coperceive → desktop Phases 1–2 → M1 → backdrop → b99c732 review).
