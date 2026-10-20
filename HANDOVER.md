# Handover

The web viewer is a light control shell around a dark viewport, with a dark theme on the same layout. Phase 6 (playback, cameras, sequence export, view settings, and the bird's-eye and overlay figures) is on main. The desktop shell stays out. Do not start a shell, a second dataset, or training code.

## Goal

An independent cooperative perception data platform Adarsh owns. Schema 1 scenes stay the contract in `docs/spec/format.md`. A CARLA town is a sidecar map, drawn under the lidar and used by `coperceive validate`. The page plays those samples and can sit in orbit, bird's-eye, chase, drone, or a camera frustum. The distribution, import, and command name are `coperceive`.

Clean-room rule: do not install, import, copy, paraphrase, or model naming or schema on kesai-labs/py123d or the adarsh-nl fork. Neither was opened. The only outside idea used is a shared HD-map layer per town, drawn with the sensors. The Griffin toolkit was not re-opened. Altitude bands and official scene counts are the figures already recorded in `docs/design/griffin-verification.md`.

## State

- `coperceive maps fetch` extracts `Town03.xodr`, `Town06.xodr`, `Town07.xodr`, and `Town10HD.xodr` from CARLA 0.9.15 (`https://downloads.carlasim.com/Linux/CARLA_0.9.15.tar.gz`). `maps ingest` writes `maps/<town>/manifest.json`, `polylines.parquet`, and `vertices.bin`. `maps align` stores `world_from_opendrive`. The `.xodr` bytes and the map directories are not in git.
- The map format is `coperceive-hdmap` version 1. Vertices stay in the OpenDRIVE frame. The transform is a 3×3 matrix plus a translation, because a reflection is allowed and a quaternion cannot store one. Schema version stays `1`.
- The viewer draws lane centres and road edges under the lidar. `GET /api/scenes/{id}/map/lines` returns the world-frame blob. A missing map is 404 and the status ends in `no map.`
- The status line counts boxes and frustums for agents that are still shown. Orbit frames the vehicle pose and lidar points within 80 m horizontally once per scene. Later scrubs do not move an orbit camera. `GET /favicon.ico` returns `favicon.svg`.
- Playback uses the recorded gap between pose times at 0.25×, 0.5×, 1×, 2×, and 4×. The page caches 16 frame documents and 48 point sweeps and requests the next four samples. Viewpoints ease over 180 ms. Look-through uses sensor +Z as forward and sensor −Y as up. The checklist is `docs/design/viewer-features.md`.
- `GET /api/scenes/{id}/image` returns the file named in `images.parquet` for that sample. The path is confined to the scene directory. A path that leaves the scene is 400. A missing row or a missing file is 404. The page shows one panel per camera. Boxes start on and Lidar starts off. The strip keeps the last complete sample until the next images arrive. Image bytes are not in git.
- Sequence export writes every recorded sample from the start time through the end time as one frame. `fps` is the file's playback rate. `coperceive export sequence` reads one camera. The page can also post bitmaps of the live 3D view, at most 60 frames. A camera export accepts 600. MP4 needs `ffmpeg` on `PATH` with `shell` false. GIF and PNG use Pillow. Files land in `exports/` under the server root. Rerun export is unchanged.
- View settings live in `localStorage` under `coperceive-view`. The object is layers, color, point size, box style, hidden classes, and theme. It has no token. The bearer token stays in `sessionStorage` under `coperceive-token`. Defaults are every layer on, color by agent, 2 px points, wire boxes, and light. Class color applies to boxes. Points and frustums stay colored by agent. The photo strip keeps its own Boxes and Lidar checkboxes.
- `coperceive validate --maps` adds a centreline block. It is a measurement. It is not a hard failure. With no maps directory the block says `not measured`.
- Format 1 predictions (`run.json` + `boxes.parquet`), matching geometry (IoU & centre distance), greedy matching, all-points pooled AP, and `coperceive eval run|report|compare` are on `main` (PR #16, PR #18; [ADR 0010](docs/adr/0010-eval-predictions.md)).
- Dataset adapters for OPV2V and V2XSet with left-handed y-reflection, RSU typing, and automated OpenDRIVE town identification are on `main` (PR #20; [ADR 0009](docs/adr/0009-dataset-adapters.md), [ADR 0012](docs/adr/0012-left-handed-sources.md)).
- Model plugin system (`coperceive.models`, `coperceive model list|inspect|check|run|cache prune`, `--slurm`) and Griffin plugin (`plugins/griffin/`, `coperceive-griffin-model` 0.1.0) with vehicle, drone, instance-fusion, and early-fusion models are on `main` (PR #19, PR #21; [ADR 0011](docs/adr/0011-live-model-plugin.md)).
- Canonical read-only Step B acceptance runs are preserved at `/home/nanjaiyalathaa/results/reference-stepb-20261020/predictions/` (replacing filtered 628386).
- Merged worktrees (`eval-step-b`, `adapter-opencood`, `step-a-complete`) are removed and their local branches deleted. The shared `.venv` editable install points at `~/coperceive` on `main`.
- The desktop plan is [ADR 0014](docs/adr/0014-desktop-app.md) (Tauri shell, `ExecutionTarget` local and SSH-HPC, connect window, remote runner). Electron is the fallback. Neither is built.

## Decisions already in the code

- The map is a sidecar keyed by `platform.map_name`. Geometry is not copied into each scene.
- Driving-lane centre is the primary centreline. The OpenDRIVE reference line is reported beside it.
- Alignment candidates keep z up. A horizontal translation is stored only when it cuts the median residual by at least 1 m. `tz` stays 0.
- On Griffin-25m against CARLA 0.9.15, every town selected `identity`. Griffin `+X` equals OpenDRIVE `+X`. The header CRS is transverse Mercator, so in PROJ that axis is easting. This is the file frame, not a compass survey.
- Positive OpenDRIVE lane ids are driven against `+s`. The nearest-segment heading for those lanes is reversed by π.
- Centreline distance does not fail validate. `lane_centre_median_minus_claim_m` subtracts 0.24 m from the measured median.
- Phase 5 choices stand: three.js in the page, Rerun as an `.rrd` export, lidar points at 2 CSS pixels, loopback bind unless `--token` is set.
- Phase 4 choices stand: warnings for speed and acceleration, the size-scaled computed gate, exit 0 unless `--strict`.
- Phase 3 and Phase 2 choices stand, including schema `1`, null visibility, and `time_us = frame_id * 100000`.

## Open questions for Adarsh

1. 40 m/s and 20 m/s² stay warnings. Smoothed acceleration uses the same 20 m/s² and a 0.5 s trailing window. On Griffin-25m the smoothed check warns on 0 steps.
2. The computed gate is `max(2 m, 0.5 * the longer box length)`. Griffin-25m has 11 computed pairs, centre median 4.071134798976429 m, max 4.518411314835206 m.
3. Is the 2 m / 5-frame / 40% phase rule the split you want? It labels only Town06-001.
4. “About 150” frames stays informational. Every Griffin-25m scene is 147, 148, or 149.
5. The default exit is 0. `--strict` exits 1 on an unreadable scene, a missing frame, or a cross-agent pose miss. The centreline block does not count.
6. `Scene.at` still returns the discrete sample.
7. Is +Z depth, with no pixel rounding, the camera contract to keep?
8. `CooperativeDataset` indexes one reference agent.
9. Identity collate versus a padded cloud batch. Training code was not added.
10. On the Phase 3 machine, more DataLoader workers were slower than `num_workers=0`.
11. Visibility. Keep null when the field is absent, or store `1.0`?
12. Drone lidar calibs with no clouds. Keep them as sensors, or drop stems that have no point files?
13. East is Griffin `+X` = OpenDRIVE `+X` = PROJ easting on these four files. Say if you want a compass check on top of that.
14. The 0.24 m claim is not the measured median. Lane-centre median is 0.013712643507518118 m. Reference-line median is 1.7860191236754484 m. Say which quantity the 0.24 m figure was meant to be.
15. Town07's lane-centre median is 0.1647962688524098 m, with `identity` still ahead of the next candidate by metres. Say if another CARLA package should be ingested.
16. Sample step is 1.0 m. Say if the sidecar should be finer.
17. Image paths are written for every camera even when the file is absent. On this machine only Town03-000 has the PNG bytes, under the scene directory, outside git. Other scenes return 404.
18. Schema number. Still `1`. The map is `coperceive-hdmap` version 1, separate from the scene schema.
19. Desktop shell. Tauri is the plan and Electron is the fallback. The shell was not built.
20. Lidar points are 2 CSS pixels. Say if 2 is still too large or too small.
21. Town03-000's nine cameras were unpacked one archive at a time. A full extract of all nine archives for every scene was not done: the dry-run `required_free_bytes` for that was 324550526424. Say if more scenes should be unpacked.
22. Light is the default. Dark is stored with the other view settings. The viewport stays `#141414` in both. Say if the light palette should change.
23. Look-through uses sensor −Y as up because the Town03-000 front camera's +Y points down. Say if another dataset uses the opposite axis.
24. Sequence export keeps one frame per recorded sample. 10 fps is realtime on Griffin. Say if a chosen fps should resample the timeline instead.
25. The live 3D view export stops at 60 frames because those bitmaps are uploaded. A camera export reads files on the server and stops at 600. Say if the view cap should be higher.

## Griffin-25m map check

Downloaded and converted on this machine on 2026-10-15. `coperceive download griffin --subset 25m --preset dev --dest /tmp/griffin-raw --accept-griffin-terms`, then `coperceive convert griffin --subset 25m --raw-root /tmp/griffin-raw/griffin_50scenes_25m/griffin-release --out /tmp/griffin-scenes`. 47 scenes. Recordings are not in git.

CARLA package `/tmp/CARLA_0.9.15.tar.gz`: 8,386,636,048 bytes, SHA-256 `7b2f432ce74b251593f95dcc8dee6d99ad9625c2a71a2d6d48f24d879de19ef7`. OpenDRIVE SHA-256 values are in `docs/adr/0008-hd-map.md`.

`coperceive maps align --maps /tmp/coperceive-maps --scenes /tmp/griffin-scenes` selected `identity` for all four towns and stored no translation.

| Map | Poses | Median m | Heading median deg | Next candidate |
| --- | ---: | ---: | ---: | --- |
| Town03 | 1936 | 0.013712643507518118 | 0.24974331271204164 | rot180, 0.9566292293761771 m |
| Town06 | 1788 | 0.0019174357069388736 | 0.027407758135083782 | reflect_x, 23.651517233061078 m |
| Town07 | 1490 | 0.1647962688524098 | 2.4803696188618147 | swap_neg, 6.108792644120575 m |
| Town10HD | 1786 | 0.03035693727363501 | 0.23833585438623572 | reflect_x, 0.8693663665846356 m |

`coperceive validate /tmp/griffin-scenes --maps /tmp/coperceive-maps` wrote `/tmp/coperceive-validate-25m.json` and `/tmp/coperceive-validate-25m.md`. Those files are not in git. Stdout: `scenes 47 failed 0 missing_frames 0 warnings 22 hard 0`.

Centreline, 7,000 vehicle poses: lane-centre median 0.013712643507518118 m (p95 0.662319574473314 m, max 1.7440808533399281 m). Median minus 0.24 m is −0.22628735649248188 m. Reference-line median 1.7860191236754484 m. Heading-error median 0.20308361215973036 deg (p95 8.561008872962589 deg, max 161.4437770562339 deg). Vehicle boxes to the lane centre: 66,647 boxes, median 0.05214550975125978 m, max 46.31949753942487 m.

Vehicle pose `z` from `poses.parquet`: 7,000 values, median −0.01233192440122366 m, max 7.992929458618164 m. On Town03-012, 15 poses (every 10th frame) sit near 7.99 m and the gap to a nearby lane-centre sample has median −0.012375831604003906 m. `tz` stays 0.

The earlier Phase 4 totals (speed, altitude, shared tracks, 11 computed pairs, 22 instantaneous acceleration warnings) are still the same run's non-map rows. They are copied in `docs/validate.md`. The 40 m, 55 m, and random subsets were not validated.

## Phase 6 playback check

`coperceive serve --host 127.0.0.1 --port 8731 --root /tmp/griffin-scenes --maps /tmp/coperceive-maps` on 2026-10-16. Town03-000, sample 1, vehicle shown: `Town03-000 at 1118500000 µs. 2929 points, 55 boxes, 9 frustums. map drawn.` Vehicle hidden at 1122500000 µs: `0 points, 33 boxes, 5 frustums. map drawn.`

The pose gap is 100000 µs. In the playback recording the clock moved from 1118500000 µs to 1133300000 µs, which is 148 steps, while the video clock ran about 15 s. That is the 10 Hz recording at 1×.

Vehicle front camera at 1118500000 µs: sensor +Y in world is (0, −0.005, −1), so it points down. The page uses sensor −Y as `camera.up`. The front-camera screenshot has the road in the lower half of the view. Drone bottom +Z in world is about (0, 0, −1), so that view looks down.

Loop, 4×, and a scene with no UAV pose were not exercised on Griffin. The delay function is the same one 1× uses.

## Browser check

`coperceive serve --host 127.0.0.1 --port 8731 --root /tmp/griffin-scenes --maps /tmp/coperceive-maps`.

Town03-000 at 1118500000 µs, vehicle shown: `2929 points, 55 boxes, 9 frustums. map drawn.` Vehicle hidden: `0 points, 35 boxes, 5 frustums. map drawn.` The green lane centres and grey road edges stayed on screen. The camera was a close intersection, not the whole town. `GET /favicon.ico` returned 200 `image/svg+xml`, and the icon rendered. `GET /favicon.svg` returned 200. `GET /api/scenes/Town03-000/map/lines` returned 200 and 360,812 bytes.

## Gates on this machine

Python 3.12.3. Commands on 2026-10-15 after the map code was in the tree. Pyright and pytest below were run after `pip install -e ".[viewer]"` and the CPU torch wheel. The first pyright, before those extras, reported 27 errors, all `import rerun` / `import torch` missing. With the extras installed it is clean. CI installs both.

- `ruff check .` — all checks passed (ruff 0.17.1)
- `ruff format --check .` — passed (ruff 0.17.1)
- `pyright` — 0 errors, 0 warnings, 0 informations (pyright 1.1.414, strict). Excluded: `coperceive/app`, `coperceive/griffin`, `tests/test_app.py`, `tests/test_griffin.py`, `tests/test_griffin_phase2.py`
- `pytest --override-ini addopts= --tb=no -q` — 89 passed, 1 skipped, 6 warnings in 2.61 s (pytest 9.1.1). The skip is `test_real_griffin_release_loads` without `COPERCEIVE_GRIFFIN_RAW`. The warnings are `DeprecationWarning` from `fork()` in the dataset worker test and the parallel convert test.
- `pytest --cov=coperceive --cov-report=term-missing -q` — exit 0, coverage 85% (4483 statements, 506 missed, branch coverage on)
- `python -m build --wheel` — built `dist/coperceive-0.0.0-py3-none-any.whl` (build installed for the gate, hatchling 1.32.4). The wheel contains `coperceive/hdmap/` and `coperceive/app/static/assets/index-DRF1eOHR.js`.
- Fresh venv `/tmp/coperceive-hdmap-fresh`, commands run from `/tmp`: `pip install dist/coperceive-0.0.0-py3-none-any.whl`. `coperceive --help` listed `schema`, `serve`, `maps`, `download`, `convert`, `validate`, and `export`. `coperceive schema` printed `1`. `coperceive maps --help` listed `fetch`, `ingest`, and `align`. `import coperceive.hdmap` loaded `/tmp/coperceive-hdmap-fresh/lib/python3.12/site-packages/coperceive/hdmap/__init__.py`. `import coperceive.dataset` raised `ModuleNotFoundError: No module named 'torch'`.
- `mkdocs build` — succeeded (MkDocs 1.6.1; the Material plugin printed its MkDocs 2.0 warning). The site was written to `site/`.
- `gitleaks detect --no-git --source . --redact --no-banner --exit-code 1` (8.28.0) — no leaks, 5.54 MB scanned.
- `gitleaks detect --source . --redact --no-banner --exit-code 1` (8.28.0) — no leaks, 19 commits scanned, through `134c816`. This paragraph’s commit is not in that scan.
- Python 3.10, 3.11, and 3.13 were not run on this machine. Those versions are the GitHub Actions matrix.

## Phase 6 camera check

`coperceive serve --host 127.0.0.1 --port 8731 --root /tmp/griffin-scenes --maps /tmp/coperceive-maps` on 2026-10-16. Town03-000 at 1118500000 µs, all nine cameras present as files: `GET /api/scenes/Town03-000/image` returned 200 `image/png` for vehicle front (2,926,270 bytes). The PNG is 1920×1080. The stored pinhole is `fx = fy = 687.2924957435579`, `cx = 960`, `cy = 540`, `image_width_px = 1920`, `image_height_px = 1080`.

The page showed a photo in every panel: vehicle back, front, left, right, and drone back, bottom, front, left, right. Vehicle front has the road in the lower half, and the wireframes sit on the cars. Drone bottom looks down at the road. Boxes is a photo overlay; the 3D boxes stay when it is unchecked. Lidar draws the sweep on the photo and starts off. Town03-001 shows `No image file for this sample.` The Desk tab still shows Jobs. A 390 px wide window can scroll to the strip.

The extract log ended `done files=1341`. `HF_TOKEN` was unset. The download used an anonymous Hub request. The zips were deleted after the Town03-000 members were written. No image file is in git.

The pose gap is 100000 µs, so 1× is 10 samples per second. `town03_camera_playback.mp4` shows sample 1 at file time 10.5 s and sample 94 at file time 13.0 s, then the clock sits on sample 97. That file-time rate is not the page timer. A wall-clock trace of the same page, with the speed control on 1×, stepped once per about 250 ms (about 4 samples per second). Each timeout was armed for 100 ms and fired late. Image loads did not schedule extra steps. The timeout lived in an effect that depended on the sample index, so it started only after React committed the new sample, and PNG decode plus the 3D draw sat on the main thread in front of that wait.

The clock now follows elapsed time. A draw that runs long skips to the sample that elapsed time has reached, and the index does not run ahead of that clock. Photos are decoded on a worker to a 640 px bitmap. The 3D view redraws when the sample, the map, or the camera changes. On the Griffin display, a 20 fps grab of the transport went from sample 2 at 0.45 s to sample 39 at 4.15 s, 37 steps in 3.70 s, 10.0 per second. In `town03_1x_ten_steps.mp4` the status line read 1120400000 µs at file time 13.0 s and 1124300000 µs at file time 17.0 s, which is 39 steps in 4.0 s, while the hint said `Playing at 1×`. A headless Chrome run went from sample 1 to sample 40 in 4.084 s, 9.55 per second, and no sample was ahead of elapsed time.

## Gates for the playback slice

Python 3.12.3, on 2026-10-16, after the playback page was in the tree. The map-phase gate list above is the earlier run. That slice did not rebuild the wheel.

- `ruff check .` — all checks passed (ruff 0.17.1)
- `ruff format --check .` — passed
- `pyright` — 0 errors, 0 warnings, 0 informations
- `pytest --override-ini addopts= --tb=line -q` — 89 passed, 1 skipped, 6 warnings in 2.64 s. The skip is `test_real_griffin_release_loads` without `COPERCEIVE_GRIFFIN_RAW`.
- `mkdocs build` — succeeded (MkDocs 1.6.1, Material MkDocs 2.0 warning)
- `npm run build` in `web/` — wrote `coperceive/app/static/assets/index-DoaPsDlj.js` and `index-DrTviwvX.css`
- Python 3.10, 3.11, and 3.13 were not run on this machine.

## Gates for the camera slice

Python 3.12.3, on 2026-10-16, after the image route and the camera strip were in the tree. This slice did not rebuild the wheel and did not run gitleaks.

- `ruff check .` — all checks passed (ruff 0.17.1)
- `ruff format --check .` — passed
- `pyright` — 0 errors, 0 warnings, 0 informations
- `pytest --override-ini addopts= --tb=short -q` — 90 passed, 1 skipped, 6 warnings in 2.97 s. The new test is `test_image_route_serves_the_recorded_file`. The skip is `test_real_griffin_release_loads` without `COPERCEIVE_GRIFFIN_RAW`.
- `mkdocs build` — succeeded (MkDocs 1.6.1, Material MkDocs 2.0 warning)
- `npm run build` in `web/` — wrote `coperceive/app/static/assets/index-KV2yBqvj.js` and `index-BLVkEqEi.css`
- Python 3.10, 3.11, and 3.13 were not run on this machine.

## Gates for the playback-rate fix

Python 3.12.3, on 2026-10-16, after the elapsed-time clock. This fix did not rebuild the wheel and did not run gitleaks.

- `ruff check .` — all checks passed (ruff 0.17.1)
- `ruff format --check .` — 83 files already formatted
- `pyright` — 0 errors, 0 warnings, 0 informations
- `pytest --override-ini addopts= --tb=line -q` — 90 passed, 1 skipped, 6 warnings in 4.47 s. The skip is `test_real_griffin_release_loads` without `COPERCEIVE_GRIFFIN_RAW`.
- `mkdocs build` — succeeded (MkDocs 1.6.1, Material MkDocs 2.0 warning)
- `npm run build` in `web/` — wrote `coperceive/app/static/assets/index-D0TRq-iP.js` and `index-BWFAAdbI.css`
- Python 3.10, 3.11, and 3.13 were not run on this machine.

## Commands

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[app,griffin,dev,docs,viewer]"
.venv/bin/pip install torch --index-url https://download.pytorch.org/whl/cpu
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/pyright
.venv/bin/pytest
cd web && npm install --legacy-peer-deps && npm run build
coperceive maps fetch --dest /path/outside/the/repo
coperceive maps ingest --xodr /path/Town03.xodr --map-name Carla/Maps/Town03 --out /path/maps
coperceive maps align --maps /path/maps --scenes /path/scenes
coperceive validate /path/scenes --maps /path/maps
coperceive serve --host 127.0.0.1 --root /path/scenes --maps /path/maps
coperceive export bev /path/scene --out /path/bev.svg --maps /path/maps --time-us 1118500000
coperceive export overlay /path/scene --out /path/front.png --agent vehicle --sensor front --time-us 1118500000
```

## Phase 6 sequence export check

`coperceive export sequence /tmp/griffin-scenes/Town03-000 --out /tmp/coperceive-export/town03-front.mp4 --agent vehicle --sensor front --start-us 1118500000 --end-us 1119400000 --fps 10 --width 960 --height 540` wrote 10 frames, 960×540, `r_frame_rate=10/1`, duration 1.000000 s. The same range through 1119000000 µs wrote a 6-frame GIF at 480×270 with a 100 ms frame delay. One sample wrote `town03-front.png` at 640×360. The still is the vehicle front camera: a road, cars, and a truck.

The page, after a hard reload of `index-CNMnmym3.js`, exported samples 1–3 of `front · vehicle` as `exports/Town03-000-vehicle-front-1118500000-1118700000.mp4`: 3 frames, 960×540, 10 fps, duration 0.300000 s. Save still of This view wrote `exports/Town03-000-view-1118500000-1118500000.png`, 960×540, the orbit lidar and boxes. The note in the export bar read those paths. Camera photos stayed on screen.

## Gates for the sequence slice

Python 3.12.3, on 2026-10-16, after sequence export was in the tree. This slice did not rebuild the wheel and did not run gitleaks.

- `ruff check .` — all checks passed (ruff 0.17.1)
- `ruff format --check .` — 85 files already formatted
- `pyright` — 0 errors, 0 warnings, 0 informations
- `pytest --override-ini addopts= --tb=line -q` — 99 passed, 1 skipped, 6 warnings in 2.55 s. The skip is `test_real_griffin_release_loads` without `COPERCEIVE_GRIFFIN_RAW`. The new tests are in `tests/test_sequence_export.py` and `test_sequence_export_route_writes_gif_and_view`.
- `mkdocs build` — succeeded (MkDocs 1.6.1, Material MkDocs 2.0 warning)
- `npm run build` in `web/` — wrote `coperceive/app/static/assets/index-CNMnmym3.js` and `index-DGphZgbx.css`
- Python 3.10, 3.11, and 3.13 were not run on this machine. A machine without `ffmpeg` was not run. GIF and PNG do not call it. The MP4 unit test skips when `ffmpeg` is absent, and another test checks the missing-binary error.

## Phase 6 view-settings check

On Town03-000 at 1118500000 µs the stored row was points off, color by class, solid boxes, light theme, and `car` hidden. The status line read `0 points, 40 boxes, 9 frustums. map drawn.` A reload kept that row. Reset view restored `2929 points, 55 boxes, 9 frustums. map drawn.` with color by agent, wire boxes, and the dark theme. Hiding `car` is the difference between 55 and 40 boxes on that sample.

## Gates for the view-settings slice

Python 3.12.3, on 2026-10-16. This slice did not change Python modules. It did not rebuild the wheel and did not run gitleaks.

- `ruff check .` — all checks passed (ruff 0.17.1)
- `ruff format --check .` — 85 files already formatted
- `pyright` — 0 errors, 0 warnings, 0 informations
- `pytest --override-ini addopts= --tb=line -q` — 99 passed, 1 skipped, 6 warnings in 2.88 s. The skip is `test_real_griffin_release_loads` without `COPERCEIVE_GRIFFIN_RAW`.
- `mkdocs build` — succeeded (MkDocs 1.6.1, Material MkDocs 2.0 warning)
- `npm run build` in `web/` — wrote `coperceive/app/static/assets/index-CRVRY35I.js` and `index-Dsi7k7Px.css`
- Python 3.10, 3.11, and 3.13 were not run on this machine.

## Phase 6 figures

Town03-000 at 1118500000 µs. `coperceive export bev` with `--maps /tmp/coperceive-maps` wrote a 960×960 PNG and SVG: 2889 points, 55 boxes, map drawn. The page status for that sample is 2929 points; the other 40 are farther than 120 m from the agents and are left out of the figure. `coperceive export overlay --agent vehicle --sensor front` wrote a 1920×1080 PNG, 854 points inside the image, 55 boxes. The saved PNG's mean luminance is 169.5, the same as `vehicle-side/camera/front/011185.png`. The overlay limits follow imshow pixel centres, from `-0.5` to `width - 0.5`. Limits of `(0, width)` left half a pixel of the figure colour, and matplotlib 3.10 (the wheel Python 3.10 installs) saved that as an empty bottom row.

The page, on `index-DLr5_I71.js`, wrote `exports/Town03-000-bev-1118500000.png` (2889 points, 55 boxes, map drawn) and `exports/Town03-000-vehicle-front-overlay-1118500000.png` (854 points, 55 boxes). The preview showed the road photograph with the boxes and the lidar.

The lint job kept failing in `tests/test_hdmap.py` after the job created `.venv` and installed FastAPI. On that runner, strict pyright still reported `TestClient.get` and the response members as unknown (17 errors, lines 327–338). Local pyright on the same file was clean, so another workflow-only change was not a reliable next step. `test_map_http_and_favicon` now lives in `tests/test_app.py` with the other FastAPI tests. That file is already excluded from strict pyright. `tests/test_hdmap.py` no longer imports FastAPI. The lint job still creates `.venv` and runs pyright from it.

## Gates for the figures slice

Python 3.12.3, on 2026-10-16. This slice did not rebuild the wheel and did not run gitleaks. Python 3.10, 3.11, and 3.13 were not run on this machine.

- `ruff check .` — all checks passed (ruff 0.17.1)
- `ruff format --check .` — 87 files already formatted
- `pyright` — 0 errors, 0 warnings, 0 informations
- `pytest --override-ini addopts= --tb=line -q` — 104 passed, 1 skipped, 6 warnings in 3.18 s. The skip is `test_real_griffin_release_loads` without `COPERCEIVE_GRIFFIN_RAW`. The warnings are `DeprecationWarning` from `fork()`. Re-run `tests/test_plot.py` after the overlay extent fix: 4 passed.
- `mkdocs build` — succeeded (MkDocs 1.6.1, Material MkDocs 2.0 warning)
- `npm run build` in `web/` — wrote `coperceive/app/static/assets/index-DLr5_I71.js` and `index-DUb5mwdX.css`

## Next

The viewer checklist in `docs/design/viewer-features.md` is marked. Radar, segmentation, depth color, and exposure stay not done: the Griffin-25m dev tier does not carry them. The desktop shell is still out of scope.

## UI redesign

The web shell is light by default (`#F4F4ED` page, `#F7F7F1` panels, `#D8D8C7` borders, `#1C1C1C` text) with a dark theme on the same layout. The viewport stays `#141414` in both. View settings, including the theme, stay in `localStorage` under `coperceive-view`. The bearer token stays in `sessionStorage`. Playback still follows `(t[i+1] − t[i]) / speed` and does not depend on `frameIndex` inside the play effect.

Bloom is `UnrealBloomPass` on layer 1 (lidar, wire boxes, trails). The map and the ground grid are not on that layer. Lane centres are `#C6F3DC`. The grid fades with distance from the camera. A vignette sits over the viewport. A UAV label is a glass `CSS2DObject` with the id and altitude, clamped inside the viewport. Viewpoint easing is a 0.48 s time constant; reduced motion still snaps. Chase pressed again returns to orbit. `#DE3B21` is the playhead and the Play button. The selected camera is a draggable, resizable picture-in-picture panel, 420 px wide, 16:9. Speed on a card is the pose delta, in m/s and km/h. Sequence, still, bird's-eye, camera overlay, and Rerun share one export dialog.

## UI redesign check

`coperceive serve --host 127.0.0.1 --port 8731 --root /tmp/griffin-scenes --maps /tmp/coperceive-maps` on 2026-10-16. Town03-000, light theme, orbit, sample 1: the status pill read `Town03-000 · Orbit` and the clock read `1118.5 s · sample 1 / 149`. The drone label read `drone  28.3 m`. Dark theme sets the panel colour to `#1A1A1A`. The export dialog title is `Export`.

With loop on, play at 1× moved the clock from sample 1 to sample 37 in about 4 s, then through bird's-eye, chase, drone, and camera while the clock kept the recorded pace. The clock wrapped from sample 132 to sample 6. Pressing Chase while chase was already selected returned the status to Orbit.

On sample 1 the vehicle pose delta is 0.0 m/s. The drone is 1.0 m/s, 4 km/h, heading 326°, altitude 28.3 m. Dragging the camera panel moved it, and the resize handle changed its width from 420 px to 490 px. In the drone viewpoint the label is clamped inside the viewport. With `prefers-reduced-motion: reduce`, the viewpoint highlight transition is 0 s and two frames 700 ms apart after a viewpoint change were identical. Every Griffin-25m scene on this machine has a UAV. With the drone removed from the scene and frame documents, Town03-000 in the drone viewpoint read `No UAV pose at this sample, so the drone view is holding.` and drew no drone label.

## Landing, frustum images, and the side column

The page at `/` is a landing view. `#/viewer` opens the viewer. The top bar is one 32 px row: brand, the current map and scene id, Viewer/Desk in the centre, then Export, a theme icon, and Settings. Speed stays on one line. Camera chips wrap inside the picture-in-picture panel. The timecode flag sits under the tick labels.

The right column keeps the agent cards, then a scene summary (duration, samples, agents, boxes by class), a selected-object inspector, and an 80 m radar. Clicking a box reports class, track id, pose-delta speed, distance to each agent, and which agents have a nearby box.

Frustum images start off. The far plane, 12 m out, uses that camera’s frame for the playhead when the camera is enabled. The layer toggle, an expandable camera list with All and None, the picture-in-picture chip, and a click on the frustum all write the same choice into `coperceive-view`. A saved view from before per-camera choices loads with images off. Only enabled cameras are decoded, at 360 px on the existing worker, and the texture is reused. Opacity is a slider. The picture-in-picture panel stays. Looking through a camera hides that camera’s plane. The landing poster still draws every frustum image and does not store that choice.

Bencho’s free blocks are MIT. The landing interactions are written here and do not copy that source. On Town03-000 the landing default is light, and dark sets the page background to `#101010`. With `prefers-reduced-motion: reduce`, the hero, shimmer, and tilt animations are `none`.

## Gates for the UI redesign

Python 3.12.3, on 2026-10-16. This slice did not change Python modules. It did not rebuild the wheel and did not run gitleaks or `mkdocs build`. Python 3.10, 3.11, and 3.13 were not run on this machine. There is no web test suite.

- `ruff check .` — all checks passed (ruff 0.17.1)
- `ruff format --check .` — 87 files already formatted
- `pyright` — 0 errors, 0 warnings, 0 informations
- `pytest --override-ini addopts= --tb=line -q` — 104 passed, 1 skipped, 6 warnings in 6.77 s. The skip is `test_real_griffin_release_loads` without `COPERCEIVE_GRIFFIN_RAW`. The warnings are `DeprecationWarning` from `fork()`.
- `npm run build` in `web/` — wrote `coperceive/app/static/assets/index-BBhIjMFy.js` and `index-CCdy_1eI.css`. `npm install` needs `--legacy-peer-deps` because `openapi-typescript` 7.13.0 declares a TypeScript 5 peer.

## Dataset adapters (2026-10-17)

ADR 0009 is accepted and implemented on branch `docs/adr-0009-adapters` (draft PR to open from https://github.com/adarsh-nl/coperceive/compare/main...docs/adr-0009-adapters?expand=1; `gh` is not on the HPC). Adarsh took the eight decisions recorded at the end of the ADR; the "As built" section lists where the code departs from the proposal.

- `coperceive/adapters/`: `paths` (templates, confinement), `rotation` (Euler/quaternion/matrix, `AxisRemap`), `readers/` (records, lidar, calib, plugin registry), `spec` (manifest dataclasses and validator, `spec_version` 1), `build` (`ManifestAdapter`, `SceneRef`, `check`), `ingest` (the shared driver, `Adapter` protocol), `registry` (built-ins, `coperceive.adapters` entry points, manifest files), `commands` (CLI), `builtin/griffin.yaml`.
- `coperceive adapters list|check|ingest`. `check` writes nothing. `ingest` is `convert_griffin`'s driver generalised; `COMPLETE.json` gains `adapter`, `manifest_sha256`, `coperceive_version`, and old markers still skip.
- `coperceive convert griffin` is a permanent alias for the adapter (`griffin/convert.py` wraps `ingest` with the Griffin manifest; `ConvertReport`, `format_report`, the CLI flags and the printed lines are unchanged). `coperceive/griffin/raw.py` is removed. The pre-adapter reader and converter are frozen, verbatim, in `tests/griffin_reference.py` (`# pyright: basic`) so the regression compares two implementations; do not fix or extend that file.
- Regression: `tests/test_adapters_griffin.py`. Synthetic releases (12/11/9-field label lines, a drone lidar calib with no clouds, a metadata-only tree) run in CI. **The real Griffin-25m comparison passed** on 2026-10-17: Slurm job 626732 on `ps,main-cpu`, raw tree `/datasets/eemcs/ps/cv/huggingface/griffin/datasets/griffin_50scenes_25m/griffin_50scenes_25m/griffin-release` staged to `/local` without `camera/` (642,289,560 bytes, rsync 12 min 16 s), both conversions with 4 workers, `pytest -m slow` 1 passed in 25.09 s. 47 scenes each side; old tree 333,298,663 bytes, new 333,305,525 bytes (the difference is the three provenance keys in 47 `COMPLETE.json` files, excluded from the comparison by name). Script: `~/scripts/slurm/coperceive-griffin-regression.sbatch`.
- The first real run (job 626730) **failed** on `poses` of `Town03-000`. Measured on that scene: 145/149 vehicle and 147/149 drone pose rows differed by at most 3.3e-16 per quaternion component (translations exact), and 6906/7213 box rows stored the opposite sign of the same rotation (max component difference 1.72). Cause: `euler_to_se3` composed three single-axis quaternions where the old reader called `SE3.from_rpy_deg`. Fix in `c787a9d`: intrinsic `zyx` returns the `from_rpy_deg` closed form; `test_intrinsic_zyx_is_bit_identical_to_from_rpy_deg` pins it. The synthetic fixtures have zero angles and did not catch it.
- CI on PR #15 (draft, https://github.com/adarsh-nl/coperceive/pull/15): 12 of 12 checks green at `24785c9` (lint, gitleaks, pytest 3.10–3.13, web build). The Euler fix and the alias switch were pushed after that run; re-check the PR page.
- The toy KITTI-flavoured dataset (`tests/toy_dataset.py`, `docs/datasets/add-a-dataset.md`) exercises what Griffin does not: `scenes.glob`, `agents.discover`, a CSV pose table with an `xyzw` quaternion, `clock.from: field` in seconds, YAML calibs with translation plus Euler and explicit image sizes, bottom-centred boxes with `yaw_only` and a `class_map`, `bin_f32` lidar, and an `RFU` body remap. A test checks the doc quotes the manifest verbatim.
- Not exercised on any real data: `pcd`, `json_table` joins, `yaw_only` on a real label file, non-identity camera remaps, `sensor:<id>` box frames, `centre_origin: bottom` beyond the toy. Nothing claims OPV2V, V2XSet, DAIR-V2X or nuScenes read correctly.
- Schema 1 is unchanged. `docs/spec/format.md` now states the `FLU` body and `RDF` camera convention the code already assumed. `pyyaml` is a core dependency and `types-PyYAML` a dev one.
- The web app's job allow-list is unchanged; `adapters ingest` is not reachable from the page.

Pip on this HPC: the user-level pip config points `index-url` at a UTwente GitLab registry. The `.venv` install used `PIP_INDEX_URL=https://pypi.org/simple` to bypass it. Compute nodes cannot reach PyPI (job 626711 timed out), so `.venv` was installed on the login node and the gates job only runs the checks. `coperceive/app/static` is never rebuilt or committed from the HPC; CI builds the web app.

## Gates for the adapters slice (before the alias switch)

Slurm job 626728 on `ps,main-cpu` (hpc-node, Python 3.13.14 from `module load python/3.13.14`, the repo `.venv`), 2026-10-17, at commit `48b7129`. `~/scripts/slurm/coperceive-gates.sbatch`. Elapsed 40 s. The wheel was built separately on the login node.

- `ruff check .` — all checks passed (ruff 0.17.1)
- `ruff format --check .` — 114 files already formatted
- `pyright` — 0 errors, 0 warnings, 0 informations (pyright 1.1.414, strict). `coperceive/adapters` and its tests are inside the strict set; the exclusions are unchanged.
- `pytest --override-ini addopts= --tb=short -q` — 174 passed, 2 skipped, 8 warnings in 11.00 s. The skips are `test_real_griffin_release_loads` and `test_adapter_matches_converter_on_the_real_release`, both without `COPERCEIVE_GRIFFIN_RAW`. The warnings are the `fork()` `DeprecationWarning` from the parallel tests; the two new ones come from `test_ingest_keeps_finished_scenes_when_one_fails`, which runs the driver with two workers.
- `mkdocs build` — succeeded (MkDocs Material, with its MkDocs 2.0 notice)
- `pip wheel . --no-build-isolation --no-deps` on the login node — `coperceive-0.0.0-py3-none-any.whl` contains `coperceive/adapters/builtin/griffin.yaml` and the committed `app/static` assets.
- `npm run build` was not run on the HPC; `coperceive/app/static` was not touched. Python 3.10, 3.11 and 3.12 were not run here; they are the GitHub Actions matrix.
- Not run: the slow real-release regression (needs the raw path), gitleaks (not installed on the HPC; CI runs it).

## Gates after the alias switch

Slurm job 626744 on `ps,main-cpu`, Python 3.13.14, the repo `.venv`, 2026-10-17, at commit `a30ad70` (the alias switch). Elapsed 22 s.

- `ruff check .` — all checks passed (ruff 0.17.1)
- `ruff format --check .` — 114 files already formatted
- `pyright` — 0 errors, 0 warnings, 0 informations (strict; `tests/griffin_reference.py` is `# pyright: basic` by a file comment, the `pyproject.toml` exclusions are unchanged)
- `pytest --override-ini addopts= --tb=short -q` — 175 passed, 2 skipped, 8 warnings in 8.22 s. The skips are the two `COPERCEIVE_GRIFFIN_RAW` tests (the real-release regression ran separately as job 626732 and passed). The warnings are the `fork()` `DeprecationWarning` from the parallel tests. The new test is `test_intrinsic_zyx_is_bit_identical_to_from_rpy_deg`.
- `mkdocs build` — succeeded
- Not run on the HPC: `npm run build` (CI), gitleaks (CI), Python 3.10–3.12 (CI). CI was green at `24785c9`; the two later commits (`c787a9d`, `a30ad70`) need the PR page checked again.

## Done: Evaluation Step A & Step B, and Adapters (PRs #16 to #21)

All milestones for Step A evaluation, left-handed dataset adapters (OPV2V, V2XSet), and Step B batch model plugins are complete and merged into `main` (commit `fffda8a`):

- **Step A: Evaluation Engine & Viewer Integration** ([PR #16](https://github.com/adarsh-nl/coperceive/pull/16), [PR #17](https://github.com/adarsh-nl/coperceive/pull/17), [PR #18](https://github.com/adarsh-nl/coperceive/pull/18); [ADR 0010](docs/adr/0010-eval-predictions.md), [docs/eval.md](docs/eval.md), [docs/spec/predictions.md](docs/spec/predictions.md)):
  - Format 1 predictions (`run.json`, `boxes.parquet`, `write_run`), BEV IoU & centre-distance greedy matching, all-points AP, pooled AP metrics across scenes, and CLI commands `coperceive eval run|report|compare`.
  - Viewer integration (`coperceive serve --predictions`): Predictions layer with status-styled wireframes (TP in teal `#1FA3A3`, FP dashed, Miss dotted, Ignored 40%), status chips, timeline recall sparklines, docked camera strip, split view with depth-coloured point cloud, and WebGL context loss handling.
  - Scene staging and verification scripts: `scripts/griffin/stage_viewer.py` and `scripts/griffin/check_staged_scenes.py`.

- **Dataset Adapters: OPV2V and V2XSet** ([PR #20](https://github.com/adarsh-nl/coperceive/pull/20); [ADR 0009](docs/adr/0009-dataset-adapters.md), [ADR 0012](docs/adr/0012-left-handed-sources.md), [docs/datasets/opencood.md](docs/datasets/opencood.md)):
  - Ingestion for left-handed CARLA-derived datasets via explicit y-reflection, box offsets `location + R · offset` with `2 · extent`, typed agent discovery (RSU as `-1`), and agent display names (`Vehicle 1`, `RSU 1`, `Drone 1`).
  - Automated town identification from OpenDRIVE road networks (`coperceive maps identify` with relative ratio ≥ 10× and absolute gap ≥ 0.5 m). Verified on OPV2V test and V2XSet train scenes (both Town06).

- **Evaluation Step B: Batch Model Plugins** ([PR #19](https://github.com/adarsh-nl/coperceive/pull/19), [PR #21](https://github.com/adarsh-nl/coperceive/pull/21); [ADR 0011](docs/adr/0011-live-model-plugin.md), [docs/spec/model-plugin.md](docs/spec/model-plugin.md), [docs/models.md](docs/models.md)):
  - Model plugin specification v1, entry-point and manifest registry, newline-delimited JSON protocol, and subprocess runner with secret redaction and checkpoint MD5 hashing.
  - Batch driver (`coperceive model run`) with content-addressed cache under `<run>/cache/`, timing split (`staging_s`, `conversion_s`, `inference_s`), GPU model recording in `provenance.gpu`, and 10-rule synthetic conformance check (`coperceive model check`).
  - Model dependency execution (`depends_on`), `outputs.transient_artifacts` (excluding 11 GB drone track queries from cache and transfer), and Slurm batch emitter (`model run --slurm`).
  - Griffin plugin (`plugins/griffin/`, `coperceive-griffin-model` 0.1.0) with four models: `vehicle-side`, `drone-side`, `instance-fusion`, and `early-fusion`.
  - Scrambled-label check verifying AP collapses to 0.0 under class permutation.

### Canonical reference runs

The dated, read-only Step B acceptance runs on NVIDIA A40 GPUs are preserved as the canonical reference (replacing the temporary filtered 628386 runs):
`/home/nanjaiyalathaa/results/reference-stepb-20261020/predictions/`

- `griffin25m-vehicle-plugin/` (Slurm job 629288 on `ctit092`, 7,930 boxes, wall 23m44s, per-sample 0.394s)
- `griffin25m-instance-plugin-drone-side/` (Slurm job 630818 on `ctit094`, 12,093 boxes, wall 13m46s, per-sample 0.438s)
- `griffin25m-instance-plugin/` (Slurm job 630818 on `ctit094`, 8,294 boxes, wall 25m46s, per-sample 0.380s)
- `griffin25m-early-plugin/` (Slurm job 630821 on `ctit094`, 8,971 boxes, wall 15m48s, per-sample 0.509s)

All four runs pair 100% of rows in exact file order against filtered 628386 with `|size| 0 m`, `|quaternion| 0`, `|score| 0`, and `|centre| <= 5.68e-14 m` (1-4 float64 ULPs), yielding `+0` / `+0.000` deltas across all four evaluation configs.

### Pooled 10-val-scene metrics (50 m range, score threshold 0.40, min score 0.05)

| Output | Config | GT | Pred | TP | FP | Miss | Precision | Recall | Headline mAP (`all` / `model`) |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `vehicle · single` | IoU unmapped (0.5 / 0.7) | 8,827 | 7,930 | 2,416 | 2,985 | 6,411 | 0.447 | 0.274 | `0.088 / 0.074` · `0.175 / 0.149` |
| `vehicle · single` | IoU mapped (0.5 / 0.7) | 8,827 | 7,930 | 3,003 | 2,398 | 5,824 | 0.556 | 0.340 | `0.150 / 0.106` |
| `vehicle · single` | Centre unmapped (4/2/1/0.5 m) | 8,827 | 7,930 | 2,884 | 2,517 | 5,943 | 0.534 | 0.327 | `0.127 / 0.117 / 0.100 / 0.081` (`0.255 / 0.234 / 0.200 / 0.163`) |
| `vehicle · single` | Centre mapped (4/2/1/0.5 m) | 8,827 | 7,930 | 3,968 | 1,433 | 4,859 | 0.735 | 0.450 | `0.345 / 0.296 / 0.228 / 0.148` |
| `drone · single` | IoU unmapped (0.5 / 0.7) | 11,456 | 12,093 | 1,107 | 5,343 | 10,349 | 0.172 | 0.097 | `0.010 / 0.001` · `0.020 / 0.002` |
| `drone · single` | IoU mapped (0.5 / 0.7) | 11,456 | 12,093 | 2,193 | 4,256 | 9,263 | 0.340 | 0.191 | `0.040 / 0.003` |
| `drone · single` | Centre unmapped (4/2/1/0.5 m) | 11,456 | 12,093 | 2,880 | 3,570 | 8,576 | 0.447 | 0.251 | `0.097 / 0.069 / 0.028 / 0.006` (`0.193 / 0.138 / 0.055 / 0.011`) |
| `drone · single` | Centre mapped (4/2/1/0.5 m) | 11,456 | 12,093 | 4,628 | 1,822 | 6,828 | 0.718 | 0.404 | `0.252 / 0.176 / 0.068 / 0.012` |
| `vehicle · fused` (instance) | IoU unmapped (0.5 / 0.7) | 8,827 | 8,294 | 2,475 | 4,123 | 6,352 | 0.375 | 0.280 | `0.071 / 0.057` · `0.141 / 0.114` |
| `vehicle · fused` (instance) | IoU mapped (0.5 / 0.7) | 8,827 | 8,294 | 3,206 | 3,391 | 5,621 | 0.486 | 0.363 | `0.132 / 0.088` |
| `vehicle · fused` (instance) | Centre unmapped (4/2/1/0.5 m) | 8,827 | 8,294 | 3,194 | 3,403 | 5,633 | 0.484 | 0.362 | `0.114 / 0.102 / 0.081 / 0.062` (`0.229 / 0.203 / 0.162 / 0.123`) |
| `vehicle · fused` (instance) | Centre mapped (4/2/1/0.5 m) | 8,827 | 8,294 | 4,550 | 2,046 | 4,277 | 0.690 | 0.515 | `0.309 / 0.247 / 0.159 / 0.099` |
| `vehicle · fused` (early) | IoU unmapped (0.5 / 0.7) | 8,827 | 8,971 | 2,852 | 4,089 | 5,975 | 0.411 | 0.323 | `0.081 / 0.062` · `0.163 / 0.124` |
| `vehicle · fused` (early) | IoU mapped (0.5 / 0.7) | 8,827 | 8,971 | 4,101 | 2,839 | 4,726 | 0.591 | 0.465 | `0.180 / 0.108` |
| `vehicle · fused` (early) | Centre unmapped (4/2/1/0.5 m) | 8,827 | 8,971 | 3,577 | 3,360 | 5,250 | 0.516 | 0.405 | `0.118 / 0.113 / 0.101 / 0.077` (`0.236 / 0.226 / 0.203 / 0.153`) |
| `vehicle · fused` (early) | Centre mapped (4/2/1/0.5 m) | 8,827 | 8,971 | 5,630 | 1,300 | 3,197 | 0.812 | 0.638 | `0.378 / 0.365 / 0.302 / 0.146` |

## Open items

1. **Griffin vehicle-side gap:** our vehicle toolkit mAP is 0.1985 vs authors' 0.1682, while drone and instance fusion match within 0.001 (see detailed investigation note below).
2. **V2V4Real adapter:** real-world cooperative dataset, left-handed/GPS conversion, NumPy-safe YAML loader, ADR 0013 plan (Task 2).
3. **Live mode refresh:** SSE streaming without second code path, reusing batch protocol (Task 3 design).
4. **Desktop app & landing page:** ADR 0014 (Tauri shell, `ExecutionTarget` local / SSH-HPC), static landing page in `site/` (Phases 1-3).
5. **Town07's 0.395 m alignment residual:** ego starts off-road in a plaza and waits at a junction; unverified which car in the drone image is ego, whether junction/parking lanes should join. Identity remains the best alignment candidate.

### Griffin vehicle-side evaluation gap (investigation note, Task 1)

Our vehicle-side toolkit mAP on the 10 val scenes is **0.1985** (NDS 0.2566) against the authors' log of **0.1682** (NDS 0.1991), while drone-side (0.1119 vs 0.1127, delta −0.0008) and instance-fusion (0.1447 vs 0.1442, delta +0.0005) match the authors' logs within 0.001.

#### 1. Checkpoint inventory (`ckpts/griffin_50scenes_25m/`)

All checkpoints were downloaded from the official Hugging Face repository `wjh-svm/Griffin` (release date July 2026):

| Model | Checkpoint file | MD5 hash | Size (bytes) | File modify time (HPC) | Internal checkpoint timestamp / metadata |
| --- | --- | --- | ---: | --- | --- |
| **vehicle-side** | `vehicle-side/iter_33024.pth` | `1201f5692e390a75e5b5ab0efa0cae19` | 219,799,203 | 2026-07-21 01:22:14 | `Thu Feb 20 02:54:09 2025`, iter 33023, 48 epochs |
| *vehicle-side (root)* | `vehicle-side.pth` | `256d80f0a2791402ac7fa217d13f733b` | 219,799,075 | 2026-07-21 01:22:13 | `Thu Feb 20 02:54:09 2025`, iter 33023 (differs in 324/686 keys) |
| **drone-side** | `drone-side/iter_33024.pth` | `41734b8d764d1213935a231a3419f655` | 219,802,531 | 2026-07-21 01:22:10 | `Thu Feb 20 12:26:06 2025`, iter 33023, 48 epochs |
| *drone-side (root)* | `drone-side.pth` | `d6cb7b6e5914bf807a1faf8c5b69600b` | 219,802,339 | 2026-07-21 01:22:17 | `Thu Feb 20 12:26:06 2025`, iter 33023 (differs in 324/686 keys) |
| **cooperative** | `cooperative/instance_fusion/iter_33024.pth` | `7e1448188b6e99ca6303575c3466b97f` | 222,769,163 | 2026-07-21 01:22:09 | `Fri Feb 21 04:48:32 2025`, iter 33023, 48 epochs |
| *cooperative (root)* | `cooperative.pth` | `03479d564e1b4a2cf3a58ae08899ff72` | 222,768,971 | 2026-07-21 01:22:08 | `Fri Feb 21 04:48:32 2025`, iter 33023 (differs in 332/694 keys) |
| **early-fusion** | `early-fusion/iter_33024.pth` | `ccf39651d3e4381ec06e4d0709821949` | 219,814,563 | 2026-07-21 01:22:10 | `Wed Feb 19 14:41:59 2025`, iter 33023, 48 epochs |

#### 2. Documentation and authors' log cross-check

- **Release Documentation (`Training_and_Evaluation.md:118-124`):** The Griffin toolkit documentation explicitly names `iter_33024.pth` for all four 25m models, with the exact MD5 hashes matching our files (`1201f569...` for vehicle, `41734b8d...` for drone, `7e144818...` for instance-fusion, `ccf39651...` for early-fusion). Neither the repository README, documentation, nor the Hugging Face model card names any other iteration or weights for the 25m split.
- **Authors' Log Files (`ckpts/griffin_50scenes_25m/logs/`):**
  - `vehicle-side-eval.log` (Fri Feb 21 10:36:10 2025) explicitly loaded `projects/work_dirs_agile_50scenes_25m/vehicle-side/tiny_track_r50_stream_bs8_48epoch_3cls/iter_33024.pth`.
  - `vehicle-side-train.log` confirms that iteration 33024 was the 48th and final epoch checkpoint (688 iters/epoch × 48 = 33,024 iters, saved Thu Feb 20 02:54:09 2025). The evaluation immediately following iter 33024 in that training log scored `mAP: 0.1689` / `NDS: 0.1998`.
- Both the authors' evaluation and ours evaluated detections for **1,490 val samples**, loaded annotations for **1,490 val samples**, and filtered from 13,459 GT boxes to identical **8,827 boxes** within the 50 m class range.
- The difference lies in the predictions emitted: the authors' evaluation kept 402,945 predictions after range filtering, whereas our evaluation produced 402,106 predictions (839 fewer).

#### 3. Remaining candidate causes (all unverified)

- **(a) Streaming tracker state on differing GPU architecture and CuDNN (unverified):**
  The authors' evaluation ran on 4× NVIDIA GeForce RTX 4090 GPUs (`sm_89`, CuDNN 8.0.5) in their local `miniforge3/envs/v2xtrack` environment. Our runs execute on an NVIDIA A40 (`sm_86`, CuDNN 8.6.0 via module `nvidia/cuda-11.x_cudnn-8.6`). Because the Agile model runs with sequential video-test streaming (`MemoryBank` carrying recurrent track queries across 149 consecutive frames per scene), small kernel-level floating-point differences in MSDeformableAttention3D or TemporalSelfAttention compound over the scene timeline.
- **(b) nuScenes table construction: full 50 scenes vs 10 val scenes (unverified):**
  In the authors' log, nuScenes tables were generated over all 47 scenes of the release (7,000 samples, 66,647 annotations) and the val split was filtered during evaluation. In our pipeline, `scene_infos.json` was trimmed to the 10 val scenes (1,490 samples, 13,459 annotations) before generating nuScenes pickle tables to economize node scratch space. While sample timestamps and ground truth annotations are identical, differences in sample token UUID hashing or initial memory-bank state at scene boundaries could affect tracking memory.
- **(c) Upstream weight export divergence (unverified):**
  The top-level `vehicle-side.pth` (219,799,075 bytes) and `vehicle-side/iter_33024.pth` (219,799,203 bytes) have identical metadata timestamps (`Thu Feb 20 02:54:09 2025`, `iter: 33023`) but differ across 324 of 686 tensor weights (max delta 0.9389). While `iter_33024.pth` is the one documented in `Training_and_Evaluation.md`, it remains unverified whether the authors' evaluation script ran on a local checkpoint file that was re-saved, stripped of optimizer state, or modified before uploading to Hugging Face.
- **(d) Image decoding and resize interpolation (unverified):**
  The input pipeline applies `NormalizeMultiviewImage` and `MultiScaleFlipAug3D(scales=[0.5])`. Differences in underlying JPEG/PNG decode or bilinear interpolation implementations between the authors' OpenCV 4.11.0 / TorchVision 0.10.1 stack and cluster system libraries could lead to minor differences in input feature maps.

