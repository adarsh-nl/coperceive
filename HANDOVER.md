# Handover

Phase 4 is in the tree. Stop here. Do not start a viewer, a second dataset, or training code.

## Goal

An independent cooperative perception data platform Adarsh owns. Phase 4 checks a directory of schema 1 scenes and writes a JSON report and a Markdown report. The on-disk contract is still schema 1 in `docs/spec/format.md`. The distribution, import, and command name are `coperceive`.

Clean-room rule: do not install, import, copy, paraphrase, or model naming or schema on kesai-labs/py123d. It was not opened for this phase. The Griffin toolkit was not re-opened. Altitude bands and official scene counts are the figures already recorded in `docs/design/griffin-verification.md` from `Dataset_Preparation.md`.

## State

- `coperceive validate <dataset-root>` writes JSON and Markdown. Defaults are `coperceive-validate.json` and `coperceive-validate.md` in the current directory. A root that is not a directory exits 2. A scene that fails to read is one error row. The process exits 0 after both files exist, including when checks flag samples.
- The validator does not import `coperceive.griffin`. The subset name is the `platform` value `subset`.
- Time sync reports per-agent `dt`, the modal gap, the rate, and missing frames. A gap above `gap_factor` (1.5) times the modal gap counts `max(0, round(dt / nominal) - 1)` missing frames. Cross-agent match uses `sync_tolerance_us` 0. Nearest absolute `dt` uses `nearest_tolerance_us` of 1 s.
- Altitude is drone `z` minus vehicle `z` at shared timestamps. The expected band is 25±2, 40±2, 55±2, or 20–60 m for subsets `25m`, `40m`, `55m`, and `random`. That band is the toolkit cruise figure. `cruise_altitude_m` is the per-scene field. A climb or descent is reported only when the phase rule fires.
- Pose flags use 40 m/s and 20 m/s². Acceleration is the change in speed divided by the mean of the two neighbouring `dt` values. Thresholds are written into the report.
- Shared track ids report centre, yaw, and size disagreement. Nearest-centre pairs inside 5 m are `computed` and `ground_truth` false. They are not written into `scene.objects`.
- Coverage counts tracks seen by the vehicle only, the drone only, or both.
- Official scene counts are 47, 54, 50, and 104. “About 150” frames is recorded, not treated as a hard failure.
- Schema version stays `1`. Griffin-25m conversion from Phase 2 was reused from `/tmp/griffin-scenes` (47 scene directories). It was not re-downloaded.

## Decisions already in the code

- The report is Markdown, not HTML.
- Exit 0 means the report was written. It does not mean every check passed.
- A phase is the longest prefix (climb) or suffix (descent) whose median is at least 2 m below the rest, with at least 80% of steps moving by at least 0.05 m that way, at least 5 frames, and at most 40% of the scene. Both ends matching is `none`. On the synthetic step series this labels 7 frames, because the longest mostly-descending suffix includes the last cruise samples.
- The 25±2 m band is checked against `cruise_altitude_m` and against per-frame height, and the report says which is which. On Griffin-25m the cruise field is inside the band and the per-frame height is not.
- Official frame text “about 150” is not a failure. All 47 Griffin-25m scenes are 147, 148, or 149.
- Computed pairs use a 5 m centre gate and stay labelled computed.
- Speed 40 m/s flags nothing on this tier. Acceleration 20 m/s² flags 22 steps. Those flags are the threshold, not a claim that the pose file is corrupt.
- Phase 3 choices stand: discrete `Scene.at`, no pixel rounding, `z <= 0` behind, computed pairs are not ground truth, one reference agent, `identity_collate`, CPU torch in CI, `timezone.utc` for the licence stamp.
- Phase 2 Griffin choices stand: schema `1`, null visibility, drone lidar calibs without clouds, `time_us = frame_id * 100000`, east taken from the toolkit pose comment. Source: https://github.com/adarsh-nl/coperceive
- More DataLoader workers were slower than `num_workers=0` on the Phase 3 machine. That was not changed.

## Open questions for Adarsh

1. Are 40 m/s and 20 m/s² the pose limits to keep? On Griffin-25m the speed limit flags 0 steps and the acceleration limit flags 22.
2. Is 5 m the right gate for a computed nearest-centre pair? This tier produced 46 such pairs, median centre error 4.2270493908856235 m.
3. Is the 2 m / 5-frame / 40% phase rule the split you want? It labels only Town06-001 (descent, 38 frames). Town06-011 climbs by less than 2 m and stays `none`.
4. Should “about 150” frames stay informational? Every Griffin-25m scene fails an exact-150 check.
5. Should `coperceive validate` exit non-zero when a check flags samples, or keep exit 0 once the report exists?
6. `Scene.at` still returns the discrete sample. Say if training should use an interpolated pose instead.
7. Is +Z depth, with no pixel rounding, the camera contract to keep?
8. `CooperativeDataset` indexes one reference agent. Say if the index should be the union of agent times.
9. Identity collate versus a padded cloud batch. Training code was not added.
10. On this 4-vCPU VM, more DataLoader workers were slower than `num_workers=0`. A different shard was not built.
11. Visibility. Keep null when the field is absent, or match the toolkit and store `1.0`?
12. Drone lidar calibs with no clouds. Keep them as sensors, or drop stems that have no point files?
13. East. The toolkit comment is the only evidence. A map check was not done.
14. Image paths are written for every camera calib even when the dev tier did not download images.
15. Schema number. Still `1`.
16. Viewer and a second dataset. Not this phase.

## Griffin-25m validation

Command, from `/workspace`, on 2026-10-15. `validate_root(Path("/tmp/griffin-scenes"))` then `write_report` to `/tmp/coperceive-validate-25m.json` and `/tmp/coperceive-validate-25m.md`. Elapsed 5.584 s. The full tables are in `docs/validate.md`. Those report files are not in git.

- Scenes read 47, failed 0. Official 25 m count matches (47). Frames 7,000. Missing frames 0. Every pose gap is 100,000 µs, rate 10 Hz. Cross-agent misses at tolerance 0: 0 of 7,000. Nearest absolute `dt` is 0.
- Per-frame drone `z` minus vehicle `z`: 7,000 samples, min 16.811032561585307 m, p05 24.353514404036105 m, median 26.410151720046997 m, p95 28.216206969693303 m, max 29.66659187246114 m. Inside [23, 27] m: 4,520. Outside: 2,480.
- `cruise_altitude_m` over 47 scenes, same report, validator percentile: min 23.08411411 m, p05 23.29079752 m, median 25.23606834 m, p95 26.69679135 m, max 26.91940837 m. All inside 25±2 m.
- Scene median height minus `cruise_altitude_m`: min −0.9936694535794466 m, median 1.243236409414063 m, max 2.0082581264744377 m.
- Phases: descent 1, none 46. Town06-001 is the descent, 38 frames, `cruise_altitude_m` 24.51687333. Phase samples: min 16.811032561585307 m, median 23.507026061415672 m, max 26.08574439957738 m. Cruise-only samples (6,962): min 21.820821456611156 m, median 26.431367035023868 m, max 29.66659187246114 m.
- The Phase 2 range 16.8–29.7 m, median 26.4 m, is this per-frame difference. The toolkit 25±2 m figure is the cruise field. The flown height sits about 1.2 m above that field. The low tail is the Town06-001 descent, not a dataset-wide takeoff.
- Speed max 24.579697456127064 m/s (Town03-002), flags at 40 m/s: 0. Acceleration max 26.239874369602163 m/s² (Town10HD-011), flags at 20 m/s²: 22 on 10 scenes.
- Shared-track pairs 61,302. Centre error median 2.929642751054232e-14 m, max 4.576455661277233e-13 m. Yaw median 0 deg, max 1.0231815394945443e-12 deg. Length, width, and height error 0.
- Computed pairs 46, `ground_truth` false. Centre median 4.2270493908856235 m, max 4.968817988334346 m.
- Coverage: vehicle only 40 tracks, drone only 406, both 834.
- Frame counts are not exactly 150: 45 scenes of 149, Town03-010 of 148, Town10HD-009 of 147.

The 40 m, 55 m, and random subsets were not validated.

## Gates on this machine

Python 3.12.3. Commands and results from 2026-10-15:

- `ruff check .` — all checks passed (ruff 0.17.0)
- `ruff format --check .` — 67 files already formatted (ruff 0.17.0)
- `pyright` — 0 errors, 0 warnings, 0 informations (pyright 1.1.414, strict on the core, including `coperceive/dataset.py`, `coperceive/validate.py`, `tests/test_dataset.py`, and `tests/test_validate.py`). Excluded: `coperceive/app`, `coperceive/griffin`, `tests/test_app.py`, `tests/test_griffin.py`, `tests/test_griffin_phase2.py`
- `pytest -o addopts= -q` — 70 passed, 1 skipped, 6 warnings in 2.50s (pytest 9.1.1). The skip is `test_real_griffin_release_loads` without `COPERCEIVE_GRIFFIN_RAW`. The warnings are `DeprecationWarning` from `fork()` in the dataset worker test and the parallel convert test.
- `pytest --cov=coperceive --cov-report=term-missing -q` — exit 0, coverage 88% (2817 statements, 254 missed, branch coverage on)
- `python -m build --wheel` — built `dist/coperceive-0.0.0-py3-none-any.whl` (build 1.6.1, hatchling 1.32.4)
- Fresh venv `/tmp/coperceive-phase4-fresh`, run from `/tmp` so the source tree was not on `sys.path`: `pip install dist/coperceive-0.0.0-py3-none-any.whl`. `coperceive --help` printed “Cooperative multi-agent perception data tools.” and listed `schema`, `serve`, `download`, `convert`, and `validate`. `coperceive schema` printed `1`. `coperceive validate --help` listed `root`, `--json`, and `--markdown`. `import coperceive` loaded `/tmp/coperceive-phase4-fresh/lib/python3.12/site-packages/coperceive/__init__.py`. `import coperceive.dataset` raised `ModuleNotFoundError: No module named 'torch'`.
- `mkdocs build` — succeeded (MkDocs 1.6.1, Material 9.7.7). Material printed its MkDocs 2.0 warning. The site was written to `site/`.
- `gitleaks detect --source . --redact --no-banner --exit-code 1` (8.28.0) — no leaks, 13 commits scanned (the branch before this handover’s commit)
- `gitleaks detect --no-git --source . --redact --no-banner --exit-code 1` — no leaks in the working tree (4.42 MB scanned)

Python 3.10 is not installed on this machine. The 3.10 result is the GitHub Actions job.

## Commands

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[app,griffin,dev,docs]"
.venv/bin/pip install torch --index-url https://download.pytorch.org/whl/cpu
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/pyright
.venv/bin/pytest
.venv/bin/pytest --cov=coperceive --cov-report=term-missing
.venv/bin/mkdocs build
coperceive validate /path/scenes --json report.json --markdown report.md
```

The Griffin-25m scenes used for the report were already at `/tmp/griffin-scenes`. They are not in git.

## Next

Stop. Phase 4 is the validation report. Do not start Phase 5 from this note.
