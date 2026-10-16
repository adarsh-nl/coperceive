# Handover

Main is `0c36928`, the merge of pull request #12 (UI redesign, landing page, frustum images off by default, Pillow 13 pyright fix). Phase 6 playback, cameras, sequence export, view settings, and the bird's-eye and overlay figures are on main. The page is a light control shell around a dark viewport, with a dark theme on the same layout.

## Where things stand

Merged into main and deleted from the remote, because each tip was an ancestor of main and none had an open pull request: `cursor/carla-hd-maps-0452`, `cursor/phase6-birds-eye-plot-0452`, `cursor/phase6-camera-images-0452`, `cursor/phase6-customisation-0452`, `cursor/phase6-playback-viewpoints-0452`, `cursor/phase6-sequence-export-0452`, `cursor/ui-redesign-0452`, `feat/phase1-core`, `feat/phase2-griffin`, `feat/phase3-query`, `feat/phase5-viewer`. `main` was not deleted.

Remote branches other than `main`:

- `feat/phase4-validate`. Its tip is not an ancestor of main. The commit that is not in main's history is `7af9239` ("Merge pull request #5 from adarsh-nl/feat/phase5-viewer"). The branch is behind main. It was not deleted.
- `cursor/phase7-release-0452`. Draft pull request #13, "feat: package 0.1.0 with a Tauri desktop shell". Not merged. No tag was pushed, nothing was published to PyPI, and no GitHub Release was created. Do not continue that branch.
- `cursor/handover-roadmap-0452`. Draft pull request #14. This file.

## Roadmap

1. The repository goes public after a review.
2. Dataset-agnostic adapters.
3. The eval engine. Prediction files first, then a live model plugin.
4. PyPI and the Tauri desktop app.

Do not start a second dataset or training code.

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
- The desktop plan on main is still `docs/adr/0007-desktop.md`. Tauri is the planned shell. Electron is the fallback. A draft implementation is pull request #13 and stays unmerged. PyPI and that shell come after the eval engine.

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
