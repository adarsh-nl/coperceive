# Handover

Phase 5 is in the tree. Stop here. Do not start a desktop shell, a second dataset, or training code.

## Goal

An independent cooperative perception data platform Adarsh owns. Phase 5 draws a schema 1 scene in the local page with three.js and can write the same discrete samples to a Rerun `.rrd`. The on-disk contract is still schema 1 in `docs/spec/format.md`. The distribution, import, and command name are `coperceive`.

Clean-room rule: do not install, import, copy, paraphrase, or model naming or schema on kesai-labs/py123d. It was not opened for this phase. The Griffin toolkit was not re-opened. Altitude bands and official scene counts are the figures already recorded in `docs/design/griffin-verification.md` from `Dataset_Preparation.md`.

## State

- The Viewer tab is the default page. It lists direct child directories of the server root that contain `manifest.json`. The scrubber steps through recorded pose times. `max_gap_us` is 0, so a time that is not a sample is omitted. Camera images are not drawn. Frustums use `2 * atan(cy / fy)` and +Z forward. Lidar points are 2 CSS pixels and do not shrink with distance.
- `GET /api/scenes`, `GET /api/scenes/{id}`, `GET /api/scenes/{id}/frame`, `GET /api/scenes/{id}/points`, and `POST /api/scenes/{id}/export/rerun` are the viewer API. A scene id that contains `/`, `\`, or `..` is 400. Auth is checked before the CSRF header, so a POST with no bearer token is 401 and a token without `X-Coperceive-Request: 1` is 403.
- `coperceive export rerun SCENE --out FILE.rrd` writes the same recording. A missing manifest exits 2. A missing `viewer` extra exits 2. The timeline name is `coperceive`. Rerun quaternions are xyzw. The HTTP pose stays `(qw, qx, qy, qz)`. `rerun analytics disable` runs before `rr.init` when the binary is next to the interpreter. `spawn` is false.
- `coperceive.viewdata` does not import FastAPI or Rerun. `export_rerun` imports `rerun` inside the function. The React page calls the server only over HTTP.
- The desktop plan is `docs/adr/0007-desktop.md`. Tauri is the planned shell. Electron is the fallback. Neither is built. There is no `src-tauri/` and no Electron project.
- Phase 4 validation is unchanged. `coperceive validate` still exits 0 by default. `--strict` exits 1 on an unreadable scene, a missing frame, or a cross-agent pose miss. Speed 40 m/s, instantaneous acceleration 20 m/s², and smoothed acceleration over 0.5 s are warnings. The computed gate is `max(2 m, 0.5 * the longer box length)` and stays labelled computed.
- Schema version stays `1`. Griffin-25m conversion from Phase 2 was not re-downloaded. The validator was not re-run in this phase.

## Decisions already in the code

- three.js is inside this app. Rerun is an `.rrd` export, not the hub page. viser stays in the `[viewer]` extra and is not the page.
- The server keeps one decoded scene in memory.
- The page and the server stay separate processes so a later shell can spawn `coperceive serve --host 127.0.0.1`.
- Phase 4 choices stand: warnings rather than hard failures for the speed and acceleration thresholds, the size-scaled computed gate, and exit 0 unless `--strict`.
- Phase 3 choices stand: discrete `Scene.at`, no pixel rounding, `z <= 0` behind, computed pairs are not ground truth, one reference agent, `identity_collate`, CPU torch in CI, `timezone.utc` for the licence stamp.
- Phase 2 Griffin choices stand: schema `1`, null visibility, drone lidar calibs without clouds, `time_us = frame_id * 100000`, east taken from the toolkit pose comment. Source: https://github.com/adarsh-nl/coperceive
- More DataLoader workers were slower than `num_workers=0` on the Phase 3 machine. That was not changed.

## Open questions for Adarsh

1. 40 m/s and 20 m/s² stay warnings. Smoothed acceleration uses the same 20 m/s² and a 0.5 s trailing window, and is also a warning. Say if the smoothed check should become a hard failure. On Griffin-25m it warns on 0 steps.
2. The computed gate is `max(2 m, 0.5 * the longer box length)`. Griffin-25m has 11 computed pairs, centre median 4.071134798976429 m, max 4.518411314835206 m. Say if the floor or the 0.5 length fraction should change.
3. Is the 2 m / 5-frame / 40% phase rule the split you want? It labels only Town06-001 (descent, 38 frames). Town06-011 climbs by less than 2 m and stays `none`.
4. “About 150” frames stays informational. Every Griffin-25m scene is 147, 148, or 149.
5. The default exit is 0. `--strict` exits 1 on an unreadable scene, a missing frame, or a cross-agent pose miss.
6. `Scene.at` still returns the discrete sample. Say if training should use an interpolated pose instead.
7. Is +Z depth, with no pixel rounding, the camera contract to keep?
8. `CooperativeDataset` indexes one reference agent. Say if the index should be the union of agent times.
9. Identity collate versus a padded cloud batch. Training code was not added.
10. On the Phase 3 machine, more DataLoader workers were slower than `num_workers=0`. A different shard was not built.
11. Visibility. Keep null when the field is absent, or match the toolkit and store `1.0`?
12. Drone lidar calibs with no clouds. Keep them as sensors, or drop stems that have no point files?
13. East. The toolkit comment is the only evidence. A map check was not done.
14. Image paths are written for every camera calib even when the dev tier did not download images.
15. Schema number. Still `1`.
16. Desktop shell. Tauri is the plan and Electron is the fallback. Say if that order should be reversed before any shell work. The shell was not built.
17. Lidar points are 2 CSS pixels. On Town06-001, 6 CSS pixels filled about half of the densest 80×80 patch of the default view. Say if 2 is still too large or too small.

## Griffin-25m validation

This phase did not re-run the validator. The numbers below are the 2026-10-15 run already recorded in `docs/validate.md`, from `validate_root(Path("/tmp/griffin-scenes"))` then `write_report` to `/tmp/coperceive-validate-25m.json` and `/tmp/coperceive-validate-25m.md`. Elapsed 5.737 s. Those report files are not in git.

- Scenes read 47, failed 0. Official 25 m count matches (47). Frames 7,000. Missing frames 0. Every pose gap is 100,000 µs, rate 10 Hz. Cross-agent misses at tolerance 0: 0 of 7,000. Nearest absolute `dt` is 0.
- Per-frame drone `z` minus vehicle `z`: 7,000 samples, min 16.811032561585307 m, p05 24.353514404036105 m, median 26.410151720046997 m, p95 28.216206969693303 m, max 29.66659187246114 m. Inside [23, 27] m: 4,520. Outside: 2,480.
- `cruise_altitude_m` over 47 scenes, same report, validator percentile: min 23.08411411 m, p05 23.29079752 m, median 25.23606834 m, p95 26.69679135 m, max 26.91940837 m. All inside 25±2 m.
- Scene median height minus `cruise_altitude_m`: min −0.9936694535794466 m, median 1.243236409414063 m, max 2.0082581264744377 m.
- Phases: descent 1, none 46. Town06-001 is the descent, 38 frames, `cruise_altitude_m` 24.51687333. Phase samples: min 16.811032561585307 m, median 23.507026061415672 m, max 26.08574439957738 m. Cruise-only samples (6,962): min 21.820821456611156 m, median 26.431367035023868 m, max 29.66659187246114 m.
- The Phase 2 range 16.8–29.7 m, median 26.4 m, is this per-frame difference. The toolkit 25±2 m figure is the cruise field. The flown height sits about 1.2 m above that field. The low tail is the Town06-001 descent, not a dataset-wide takeoff.
- Speed max 24.579697456127064 m/s (Town03-002), warnings at 40 m/s: 0. Instantaneous acceleration max 26.239874369602163 m/s² (Town10HD-011), warnings at 20 m/s²: 22. Smoothed acceleration max 15.712867500264949 m/s², warnings: 0. Hard failures: 0.
- Shared-track pairs 61,302. Centre error median 2.929642751054232e-14 m, max 4.576455661277233e-13 m. Yaw median 0 deg, max 1.0231815394945443e-12 deg. Length, width, and height error 0.
- Computed pairs 11, `ground_truth` false. Centre min 1.092018395040389 m, median 4.071134798976429 m, max 4.518411314835206 m.
- Coverage: vehicle only 40 tracks, drone only 406, both 834.
- Frame counts are not exactly 150: 45 scenes of 149, Town03-010 of 148, Town10HD-009 of 147.

The 40 m, 55 m, and random subsets were not validated.

## Gates on this machine

Python 3.12.3. Commands and results from 2026-10-15, on this phase’s tree:

- `ruff check .` — all checks passed (ruff 0.17.0)
- `ruff format --check .` — 72 files already formatted (ruff 0.17.0)
- `pyright` — 0 errors, 0 warnings, 0 informations (pyright 1.1.414, strict on the core, including `coperceive/viewdata.py`, `coperceive/rerun_export.py`, `coperceive/dataset.py`, `coperceive/validate.py`, and their tests). Excluded: `coperceive/app`, `coperceive/griffin`, `tests/test_app.py`, `tests/test_griffin.py`, `tests/test_griffin_phase2.py`
- `pytest -o addopts= -q` — 77 passed, 1 skipped, 6 warnings in 2.56s (pytest 9.1.1). The skip is `test_real_griffin_release_loads` without `COPERCEIVE_GRIFFIN_RAW`. The warnings are `DeprecationWarning` from `fork()` in the dataset worker test and the parallel convert test.
- `pytest --cov=coperceive --cov-report=term-missing -q` — exit 0, coverage 87% (3216 statements, 310 missed, branch coverage on)
- `python -m build --wheel` — built `dist/coperceive-0.0.0-py3-none-any.whl` (build 1.6.1, hatchling 1.32.4). The wheel contains `coperceive/viewdata.py`, `coperceive/rerun_export.py`, and `coperceive/app/static/index.html`. The page in that gate build was `index-BsGpDfev.js`. The 2 px point-size change ships `index-BAPWDmG4.js`.
- Fresh venv `/tmp/coperceive-phase5-fresh`, run from `/tmp` so the source tree was not on `sys.path`: `pip install dist/coperceive-0.0.0-py3-none-any.whl`. `coperceive --help` printed “Cooperative multi-agent perception data tools.” and listed `schema`, `serve`, `download`, `convert`, `validate`, and `export`. `coperceive schema` printed `1`. `coperceive export rerun --help` listed `scene` and `--out`. `coperceive validate --help` listed `--strict`. `import coperceive` loaded `/tmp/coperceive-phase5-fresh/lib/python3.12/site-packages/coperceive/__init__.py`. `import coperceive.dataset` raised `ModuleNotFoundError: No module named 'torch'`.
- `mkdocs build` — succeeded (MkDocs 1.6.1, Material 9.7.7). Material printed its MkDocs 2.0 warning. The site was written to `site/`.
- `gitleaks detect --source . --redact --no-banner --exit-code 1` (8.28.0) — no leaks, 15 commits scanned (the branch before this handover’s commit)
- `gitleaks detect --no-git --source . --redact --no-banner --exit-code 1` — no leaks in the working tree (5.20 MB scanned), including this file before the commit. The git scan above does not include this commit.

Python 3.10 is not installed on this machine. The 3.10 result is the GitHub Actions job.

The browser check used `coperceive serve --host 127.0.0.1 --port 8731 --root /tmp/coperceive-viewer-demo` and a synthetic scene `desk` (vehicle, drone, two pose times, one box, one frustum). The page listed `desk`, the scrubber moved from `0` µs to `100000` µs, hiding the vehicle dropped the reported point count, the Desk tab still showed the jobs page, and Export Rerun wrote `/tmp/coperceive-viewer-demo/exports/desk.rrd` (17,466 bytes, 2 samples). Orbit moved the box, the frustum, and the axis triads. A blue drone point was visible with the vehicle hidden. The two orange vehicle points sit on the orange box and were hard to separate from the wireframe in that fixture. A Griffin scene was not opened in the browser.

## Commands

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[app,griffin,dev,docs,viewer]"
.venv/bin/pip install torch --index-url https://download.pytorch.org/whl/cpu
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/pyright
.venv/bin/pytest
.venv/bin/pytest --cov=coperceive --cov-report=term-missing
.venv/bin/mkdocs build
cd web && npm install --legacy-peer-deps && npm run build
coperceive serve --root /path/to/scene-parent
coperceive export rerun /path/scene --out /path/scene.rrd
```

The Griffin-25m scenes used for the Phase 4 report were already at `/tmp/griffin-scenes`. They are not in git.

## Next

Stop. Phase 5 is the in-app viewer and the Rerun export. Do not start the desktop shell from this note.
