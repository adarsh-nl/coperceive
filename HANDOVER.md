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
- The desktop plan is still `docs/adr/0007-desktop.md`. Tauri is the planned shell. Electron is the fallback. Neither is built.

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

## Next

ADR 0010, evaluation engine step A (predictions format, import, matching, viewer overlay, timeline metrics), is drafted on branch `docs/adr-0010-eval-step-a` as `docs/adr/0010-eval-predictions.md`, status proposed, no PR, nothing implemented. Adarsh reviews it before any code.

## Evaluation engine, step A (2026-10-17)

ADR 0010 is accepted and implemented on branch `feat/eval-step-a` (based on `main` at `d3cd349`, the #15 merge). Compare link: https://github.com/adarsh-nl/coperceive/compare/main...feat/eval-step-a?expand=1. Adarsh opens the draft PR against `main`. Nothing is merged. The normative contract is `docs/spec/predictions.md`; the plan the work followed is `docs/superpowers/plans/2026-10-17-eval-step-a.md`; the user doc is `docs/eval.md`.

- `coperceive/evaluation/`: `predictions` (format 1: `run.json`, `boxes.parquet`, `write_run`, `load_rows`, `validate_boxes`, `predictions_root`), `geometry` (`WorldBox`, BEV IoU by Sutherland–Hodgman clipping, centre distance, yaw error), `config` (`EvalConfig`, YAML loader, `threshold_for`), `matching` (`match_sample`, greedy per class by score with ignore rules), `metrics` (all-points AP, `Counts`, TP errors, cooperative counts), `evaluate` (`evaluate_scene`, `matches.parquet`, `metrics.json`, `evaluate_run`), `report` (`eval report|compare` tables), `commands` (CLI).
- `coperceive predictions import`, `coperceive eval run|report|compare`; `coperceive serve --predictions`; `coperceive export bev --run --predictions`.
- `coperceive/evalview.py` (no FastAPI) and three GET routes: `/api/scenes/{id}/predictions`, `.../predictions/{run}/frame?time_us=&compare=`, `.../predictions/{run}/series?compare=`. `POST /export/bev` takes `run_id`. `web/openapi.json` regenerated; `web/src/api/schema.ts` was not (needs npm; CI or Adarsh).
- Page: Predictions block (run, compare run, output, source/status/cooperative chips, score slider, legend), prediction wires per status with dashed/dotted styles, matched GT at 40%, two eval sparklines and a recall-drop marker on the timeline, inspector and scene-card fields, `run_id` on the bird's-eye export. `tsc -b` and `oxlint` clean; **not seen in a browser** (no routes existed when it was written and no server runs on the HPC). `coperceive/app/static` was not rebuilt or committed; CI builds it.
- Defaults as decided: BEV IoU at 0.5 and 0.7, display at 0.5, `score_threshold` 0.3, every GT kept, centre distance as `criterion: centre`.

Reviews caught and fixed, on the branch: AP tie order used pose-time order instead of `boxes.parquet` row order (`849d46d`); `predictions import` joined an unchecked `--scene`/`scene_id` to the scenes root before validation (`c3c5533`). Euler closed form from the adapters slice is unrelated.

Known departures from the spec text, recorded in ADR 0010 "As built": AP categories are the union of `run.json` categories, GT categories and predicted categories; the aggregate AP in `eval report` is a mean over scene × output rows; the range check runs before the score-threshold check; `export bev` also takes `--predictions`; the page has an Output selector.

### Adversarial review (2026-10-17)

Three read-only finders over the branch at `28ee59c`, each finding then attacked by two independent refuters; a finding counted only when both refuters failed to refute it.

- Numerics (BEV IoU closed forms incl. 45° rotations, containment, degenerate sizes, 1e4 m coordinates; all-points AP on eight scenarios with ties; `tp_errors`; null-not-NaN; AP at 0.7 from its own matching pass; mAP skipping null): **no finding**, 20 checks recorded.
- Matching (ties within and across times; body vs world placement; wrong-agent timestamps at import and eval; ignored-GT rule 3; `max_range_m` from the body origin; category never crosses; `below_threshold` never matches; `matches.parquet` row order; cooperative columns; frame rows equal table rows with 72-number lines): **no finding**, 18 checks recorded.
- Paths: four findings reproduced. One confirmed and fixed: `read_metrics`, `read_matches` and `evalview._match_rows` opened `metrics.json`/`matches.parquet` under a confined directory without resolving the file, so a symlink planted inside the run directory was followed outside the predictions root (`17a6d7c`: `evaluate.scene_file` resolves and confines the file; test added; final review approved). Three were judged not required by the spec and left as open items below: a NUL byte or a 300-character `run_id` or `scene_id` reaches `Path.resolve`/`is_file` and the routes answer HTTP 500 instead of 400 (`server._scene_dir`, `evalview`), and `coperceive export bev --run` lets the same `ValueError`/`OSError` and an `EvaluationError` from a malformed `metrics.json` escape as a traceback. No file outside a root was opened in any of these.

Earlier per-task reviews caught two more defects before integration: AP tie order (pose-time order, not `boxes.parquet` row order; `849d46d`) and `predictions import` joining an unchecked `--scene` to the scenes root (`c3c5533`).

### Open questions for Adarsh (step A)

0. Robustness, not spec: map `ValueError`/`OSError` from path resolution to 400 in `server._scene_dir` and `server._eval_view`, and catch them in `export bev --run`, so a NUL byte or an over-long id never produces a 500 or a traceback? Small change; say yes and it goes in before the PR leaves draft.
1. Aggregate AP: keep the mean over scene × output rows, or change to per-scene means as the spec sentence says (then a scene with `single` and `fused` outputs weighs once)?
2. AP category set: keep the union (so a GT-only class the model never emits shows AP 0.0), or restrict to `run.json` `categories` as the spec says?
3. `render_compare` drops rows present in only one run without a marker. Print them with `n/a` deltas instead?
4. The fixture's 0.3 m cone shifted 0.1 m sits exactly on BEV IoU 0.5 at the display threshold; tests avoid it, but nudging the fixture would make it robust.
5. `web/src/api/schema.ts` regeneration: in CI, or by you after `npm ci`.
6. Colour-blindness: dashed/dotted styles are the primary cue; nothing was checked in a simulator. Say if you want the green moved towards teal.

### Task 12 (not started): the real run

Candidate source: the released Griffin checkpoints under `/datasets/eemcs/ps/cv/huggingface/griffin/ckpts/griffin_50scenes_25m/` (`vehicle-side.pth`, `drone-side.pth`, `cooperative.pth`, `early-fusion/`, `cooperative/instance_fusion/`). They need the Griffin toolkit's inference code and an mmdetection3d/CUDA environment, which this repository does not contain and which has not been installed. The plan: a GPU sbatch job that runs the toolkit's test script for the vehicle-side model on Griffin-25m, converts its output to `boxes.parquet` with `write_run`, then `eval run`. Stop and show Adarsh the environment setup and the sbatch before anything is installed.

## Gates for step A

Slurm job 627022 on `ps,main-cpu`, Python 3.13.14, the shared `.venv` linked into the worktree with `PYTHONPATH=$REPO` (the job prints `coperceive imported from /home/nanjaiyalathaa/coperceive-adr0010/coperceive/__init__.py` and exits 3 if the import resolves elsewhere), 2026-10-17, at commit `369ffa1`. Elapsed 29 s. `~/scripts/slurm/coperceive-gates.sbatch` with `REPO=`.

- `ruff check .` — all checks passed (ruff 0.17.1)
- `ruff format --check .` — 138 files already formatted
- `pyright` — 0 errors, 0 warnings, 0 informations (strict; `coperceive/evaluation` and `coperceive/evalview.py` are inside the strict set)
- `pytest --override-ini addopts= --tb=short -q` — 340 passed, 2 skipped, 8 warnings in 10.77 s. The skips are the two `COPERCEIVE_GRIFFIN_RAW` tests. The warnings are the `fork()` `DeprecationWarning` from the three parallel-run tests.
- `mkdocs build` — succeeded (`docs/eval.md` and `docs/spec/predictions.md` are in the nav)
- Earlier gates in this slice: job 627009 at `28ee59c` (337 passed, 2 skipped, pyright 0).
- Login node: `cd web && ./node_modules/.bin/tsc -b` exit 0 and `oxlint src` 0 errors (13 pre-existing warnings) at the page commit `92fafe0`; the page was not rebuilt and `coperceive/app/static` is unchanged.
- Not run on the HPC: `npm run build`, gitleaks, Python 3.10–3.12 (all CI). CI has not run yet on this branch: no PR is open. `web/src/api/schema.ts` is stale (needs `npx openapi-typescript`).
- Not verified anywhere: the page in a browser (dash patterns, bloom, chips, legend, reduced motion, the 404 path), the bird's-eye figure by a human eye (only its SVG content was asserted), a real model's predictions (synthetic fixtures only).

## Step A follow-ups after Adarsh's review of PR #16 (2026-10-17)

PR #16 is a draft against `main`. Decisions and what landed on `feat/eval-step-a`:

0. Robustness: a NUL byte or a 300-character `run_id`/`scene_id` now answers 400 on every scene and prediction route and on `POST /export/bev`; `export bev --run` prints one line and exits 2. Tests `test_prediction_routes_answer_400_for_nul_and_overlong_ids` and `test_cli_bev_run_with_nul_or_overlong_id_prints_one_line` (`33a5913`).
1. Headline AP is pooled: `matches.parquet` gains `status_at_<i>` per threshold index (AP-pass status, score threshold ignored); `eval report`/`compare` pool every prediction of a run across scenes per `(agent_id, source)` and per class, with a grand `all` row pooled over outputs; per-scene AP stays in `metrics.json` and the per-scene rows. Spec §Metrics "Pooled AP", §`matches.parquet` and the report paragraph updated; ADR "As built" updated. Normative test: per-scene APs 1.0 and 0.5 pool to 5/6, not 0.75 (`e3572c7`).
2. AP categories stay the union of `run.json`, GT and predicted classes; a class with no GT is `null`/`n/a`, never 0; `eval run` prints `warning	<scene>: category 'x' is not in run.json categories` (`RunReport.warnings`) (`ed08f7a`).
3. `eval compare` lists every row of either run, `n/a` where one side is missing, footer `rows: N shared, N only in <a>, N only in <b>` (`5857cec`).
4. Fixture cone moved off the IoU 0.5 boundary (0.41 now); `test_iou_exactly_at_the_threshold_is_a_match` pins `>=` with a 3×2 box shifted 1 m (IoU exactly 0.5 → tp at 0.5, fp at 0.7) (`bf894f2`).
5. `web/src/api/schema.ts` regenerated (additive); new CI job `web`: regenerates `web/openapi.json` from `create_app` and `schema.ts` from it, fails on drift in either, then `tsc -b`. Caveat: the Python step installs the newest FastAPI/pydantic on the runner, so a dependency bump that changes `openapi()` output surfaces as drift (`338525a`).
6. TP colour green → teal `#1FA3A3` in the page, the figure, the spec, the docs and the tests; line styles unchanged. Contrast 5.99:1 on `#141414`, 2.86:1 on `#F7F7F1` (the outgoing green was 2.73, the vehicle orange is 2.76). Caveat from the implementer: `#1FA3A3` is ~13° in hue from the pedestrian class colour `#3D9A86`; a more cyan teal is the alternative if that matters (`ee580f4`).

Task 12 plan: `docs/superpowers/plans/2026-10-17-griffin-acceptance-run.md` (`39bd1e5`) and the sbatch draft `~/scripts/slurm/griffin-infer.sbatch` on the HPC. Nothing installed, cloned or submitted. Headline facts: the Griffin baselines are camera-only UniAD/BEVFormer-tiny (the four vehicle RGB cameras, ~69 GB, must be staged; lidar is not read); pins are Python 3.8, torch 1.9.1+cu111, mmcv-full 1.4.0, mmdet 2.14.0, mmseg 0.14.1, mmdet3d v0.17.1 built from source; L40/L40S (CC 8.9) are outside the wheels' sm_86 SASS, so the plan proposes `--constraint=a40`; driver 595.58.03 on L40S is new enough for any CUDA 11.x; no existing env has the mm* stack; a new `~/.conda/envs/griffin` (~6–8 GB) is proposed; the nuScenes-style conversion the toolkit needs has never been run here and must run in the job.

CI on PR #16 for these commits has not been seen from the HPC (private repo, no credentials); Adarsh pastes the check list.

## Gates for the step A follow-ups

Slurm job 627044 on `ps,main-cpu`, Python 3.13.14, the shared `.venv` linked into the worktree with `PYTHONPATH=$REPO`, 2026-10-17, at commit `cbf9984`. Elapsed 30 s.

- `ruff check .` — all checks passed (ruff 0.17.1)
- `ruff format --check .` — 139 files already formatted
- `pyright` — 0 errors, 0 warnings, 0 informations (strict)
- `pytest --override-ini addopts= --tb=short -q` — 348 passed, 2 skipped, 8 warnings in 11.21 s (skips: the two `COPERCEIVE_GRIFFIN_RAW` tests; warnings: the `fork()` deprecation from the parallel tests)
- `mkdocs build` — succeeded
- Login node: `cd web && ./node_modules/.bin/tsc -b` exit 0 after the `schema.ts` regeneration and the teal change; `oxlint src` 0 errors.
- Not run on the HPC: `npm run build`, gitleaks, Python 3.10–3.12, the new `web` CI job — all CI. Not seen anywhere: the page in a browser, a real model's output.

### CI fix after PR #16's first run

The lint job reported 16 strict-pyright errors in `tests/test_eval_end_to_end.py` (lines 110–150) that the HPC gate, on the same pyright 1.1.414, did not. Cause found while typing the responses: starlette 1.8 returns `httpx2.Response`, not `httpx.Response`; on a runner where that base does not resolve, every `TestClient` call reads as unknown (the same symptom `tests/test_hdmap.py` had last phase). Fix `388b518`: `client: TestClient`, and the four calls go through `_get`/`_post` helpers returning a `_Response` protocol (`status_code`, `json()`), one marked unknown-member access each. Gate job 627046 at `388b518`: whole-repo pyright 0 errors, 348 passed / 2 skipped; the gate now prints its pyright, fastapi, starlette and httpx versions. The HPC venv has a superset of CI's extras, so it cannot reproduce a package missing on the runner; CI on PR #16 at `388b518`: all green (lint, web, gitleaks, tests 3.10–3.13), confirmed by Adarsh. The PR stays a draft.

**Rule for strict-typed tests that use `TestClient` (third occurrence):** do not annotate responses as `httpx.Response` and do not call `client.get`/`client.post` inline in a strict file. starlette's `TestClient` returns `httpx2.Response`; where `httpx2` does not resolve on the runner the whole client is unknown. Either keep such tests in `tests/test_app.py` (excluded from strict pyright) or go through typed helpers returning a protocol, as `tests/test_eval_end_to_end.py` does.

## Task 12: the real Griffin run (2026-10-17)

Adarsh approved the drafts with changes (two-step install with login-node downloads and an offline GPU build; A40 then A100, no L40S; val scenes only; `source=single`; operating point 0.05 with 0.4 as the viewer default; 50 m unless the paper states a range; vehicle side only; MIT + arXiv attribution in `run.json`). Files: `scripts/griffin/griffin_to_predictions.py` and `scripts/griffin/scene_infos_val.py` in this repository; on the HPC `~/scripts/griffin/download.sh`, `~/scripts/griffin/download-list.md`, `~/scripts/slurm/griffin-build.sbatch`, `~/scripts/slurm/griffin-build-verify.sbatch`, `~/scripts/slurm/griffin-infer.sbatch`. Plan: `docs/superpowers/plans/2026-10-17-griffin-acceptance-run.md`.

Downloads (login node, each step under a minute; measured 115.5 MB/s): 2.4 GB into `~/wheelhouse` (torch 1.9.1+cu111 2,041,339,783 B, torchvision, mmcv-full 1.4.0, 187 PyPI wheels/sdists), conda packages for `python=3.8.20`, `~/src/mmdetection3d` at `f1107977` (v0.17.1), `~/src/griffin` at `9c02ba4a`. Three corrections on the way: `PIP_INDEX_URL=https://pypi.org/simple` (the user-level pip config points at a GitLab registry); `--python-version 3.8.20` (pip read `3.8` as 3.8.0 and refused `flake8`'s `>=3.8.1`); the hand-computed dependency closure missed transitive packages (`cycler`, `cffi`, …) and three wheels landed under manylinux2014/2010 names, so the closure was completed with a Python 3.8 `pip download` resolve (16 files added) and `SHA256SUMS` rewritten from PyPI's JSON digests. An offline `pip install --dry-run` with the Python 3.8 interpreter of `dhd_env` now resolves the whole set from the wheelhouse.

Build (`~/.conda/envs/griffin`, 4.3 GB, offline from the wheelhouse): job 627052 stopped at the SHA check (renamed wheels); 627053 stopped at `cycler` (closure gap); 627056 refused because the partial env existed; 627057 (A40 node ctit092, gcc 11.4, driver 595.58.03, nvcc 11.8) installed everything and compiled mmdet3d v0.17.1's CUDA ops in 8 min 1 s, then `pip check` failed on "ninja 1.11.1.1 is not supported on this platform" (a wheel-tag quirk; ninja was only the build parallelizer). The build script now uninstalls ninja before `pip check`; `griffin-build-verify.sbatch` is the script's assertion tail run against the env 627057 left. Verify job 627059 failed on the probe's own GPU matmul assertion (`atol=1e-2` on values near 256 under torch 1.9's default TF32 matmuls); made relative. Verify job 627062 passed: `pip check` clean, torch 1.9.1+cu111 on an A40 (capability 8.6, `sm_86` in the arch list), mmcv `nms` and mmdet3d `iou3d` ops OK, Griffin plugin import OK (model type `Agile`, `score_thresh 0.4`, `filter_score_thresh 0.35`, classes car/bicycle/pedestrian). Logs: `~/logs/griffin-build-*.{out,err}`, `~/results/627062/{pip-freeze.txt,conda-explicit.txt}`.

Staging check on the login node: `scene_infos_val.py` against the real release maps the 10 val scenes to `scene-0000…0009`, 1,490 frames, 11,933 files, ~17.0 GB of camera PNGs, both sides' trimmed `scene_infos.json` equal, in 3.7 s.

Inference jobs (`griffin-infer.sbatch`, `--constraint="a40|a100"`, all landed on A40 node ctit092, 1,805 GB free on `/local`): 627063 failed in the trimmer (`zip(strict=True)` under the griffin env's Python 3.8 — the trimmer now runs with coperceive's 3.13 interpreter); 627064 failed on a broken line continuation I introduced; 627065 ran the whole GPU part — staging 17 GB in 11 min 51 s, toolkit conversion 1 min 08 s + 24 s, `dist_eval.sh` 9 min 23 s for 1,490 samples, checkpoint md5 OK, `results_nusc_det.json` with 402,110 boxes (top-300 per frame), 7,930 with score ≥ 0.05 and 5,401 ≥ 0.4 — then died at `conda deactivate` (conda's `qt-main` activation hook reads an unset variable under `set -u`), and the scratch trap wiped the outputs. The job now copies `$OUT` home right after the detection JSON and wraps the deactivate in `set +u`.

Job 627070 (A40, ctit092, 23 min 31 s wall): staging 11 min 57 s, toolkit conversion 1 min 09 s + 24 s, `dist_eval.sh` 9 min 27 s, 1,490 samples, 402,110 boxes, 7,930 ≥ 0.05, 5,401 ≥ 0.4, checkpoint md5 OK, `coperceive convert griffin` 10 scenes in 3.5 s. The converter call then failed on a relative path (fixed in the sbatch: it reads the saved copies under `$OUT`). Because the GPU outputs were saved (`~/results/627070/{griffin_json,nusc_tables,results-*.pkl,test_*.log}`), the CPU tail was finished on the login node from them (each step under 30 s): trimmed tree without images (25 MB), `convert griffin` (10 scenes, 37,761 label lines, 3.7 s), `scripts/griffin/griffin_to_predictions.py` (7,930 boxes: car 7,268, pedestrian 451, bicycle 211; 394,180 dropped below 0.05; every scene validated), `eval run` A and B (6 s each). Artefacts: `~/results/627070/eval/{eval-config-iou.yaml,eval-config-centre.yaml,eval-run-*.txt,report-iou.md,report-centre.md,predictions/,scenes/}` (129 KB plus the scenes). Run ids `griffin25m-vehicle-agile-iter33024` (A) and `…-centre` (B, same boxes).

**Numbers (10 val scenes, vehicle `single`, within 50 m, operating point for tp/fp/miss = score 0.4; AP pooled across scenes, over every prediction ≥ 0.05).** GT boxes within 50 m: 8,827; predictions: 7,930.

| config | tp | fp | miss | precision | recall | AP thresholds | mAP | car AP |
| --- | ---: | ---: | ---: | ---: | ---: | --- | --- | --- |
| A: BEV IoU | 2,416 | 2,985 | 6,411 | 0.447 | 0.274 | 0.5 / 0.7 | 0.088 / 0.074 | 0.524 / 0.446 |
| B: centre distance | 2,884 | 2,517 | 5,943 | 0.534 | 0.327 | 4 / 2 / 1 / 0.5 m | 0.127 / 0.117 / 0.100 / 0.081 | 0.655 / 0.599 / 0.560 / 0.483 |

Per class (A at IoU 0.5; B at 2.0 m): car 0.524 / 0.599; pedestrian 0.000 / 0.087; bicycle 0.001 / 0.014; truck, bus and motorcycle 0.000 / 0.000. `eval run` warned that `truck`, `bus` and `motorcycle` appear in the ground truth of Town10HD-012/014 but not in `run.json` (the model emits car/bicycle/pedestrian only). Under decision 2 (union of classes, 0 for a class with ground truth and no prediction) those three classes pull the mean down: over the model's three classes the IoU-0.5 mean would be (0.524 + 0.001 + 0.000) / 3 = 0.175. Whether the toolkit's own evaluation maps truck/bus to car, or ignores them, was not checked; this is the first open question below.

Not a comparison with the paper: no paper table was quoted. The only reference figure on this machine is the toolkit's own evaluation log beside the checkpoint (`ckpts/griffin_50scenes_25m/logs/vehicle-side-eval.log`: `mAP: 0.1682`, `NDS: 0.1991`, car AP at 2.0 m `0.5197`), which uses nuScenes' metric (recall/precision below 0.1 clipped, per-class ranges) on the full 25 m val split and is not the same computation as either config here.

Did not match the plan: four build attempts and three inference attempts before a complete run (causes above, all my own: the manifest's predicted wheel names, the missing transitive closure, ninja's wheel tag under `pip check`, a TF32-tight matmul probe, `zip(strict=True)` under Python 3.8, a comment after a line continuation, `conda deactivate` under `set -u`, a relative path after `cd`); the CPU tail of the final run was done on the login node instead of inside the job; the viewer has still not been opened on these predictions.

### Open questions for Adarsh (Task 12)

1. Class vocabulary: map GT `truck`/`bus`/`motorcycle` onto the model's classes for the headline (as the toolkit may do), restrict the mean to `run.json` categories, or keep the union as decided? The per-class table is unaffected either way.
2. Rerun inside one job (the fixed `griffin-infer.sbatch`, ~24 min of A40) so the whole chain is a single job log, or accept the saved-output tail?
3. Open the viewer on `~/results/627070/eval/scenes` with `--predictions ~/results/627070/eval/predictions` (images are not staged; boxes and lidar-free scenes will show) and judge the teal by eye.
4. Drone-side and cooperative runs (the remaining checkpoints) as the next phase?

### Follow-ups after the first real run (2026-10-17)

1. `mAP@<t> all` (union of classes with ground truth) and `mAP@<t> model` (`run.json` categories) are both reported by `eval report`/`compare`; `metrics.json` carries `mean` and `mean_model` (`af3d4e8`). `gt_class_map` is a new eval-config key, applied to ground truth before matching, because the Griffin toolkit maps classes in code: `tools/griffin_data_converter/trans_kitti2nuscenes.py`, `obj_type_mapping` (`"truck": "car"`, `"bus": "car"`, `"motorcycle": "bicycle"`) and `projects/mmdet3d_plugin/datasets/griffin_dataset.py`, `class_names_nuscenes_mappings` (`'Truck': 'car'`, `'Bus': 'car'`), commit `9c02ba4a`. Spec, ADR and `docs/eval.md` updated; gates job 627098 at `af3d4e8`: 352 passed / 2 skipped, pyright 0, ruff clean.
2. Clean single-job rerun: **job 627093** (A40 ctit092, 23 min 36 s; staging 11 min 17 s, toolkit conversion 1 min 32 s, inference 9 min 37 s, coperceive convert 2.4 s), everything inside the job including both evaluations. Compared with the saved-output run 627070 scene by scene (metrics `outputs` with the new `mean_model` key stripped, and every shared `matches.parquet` column): **identical** for `griffin25m-vehicle-agile-iter33024` and `…-centre`, 10 scenes each. Results: `~/results/627093/` (`report-iou.md`, `report-centre.md`, `predictions/`, `scenes/`, `griffin_json/`, logs).
3. Mapped evaluations (`gt_class_map: {truck: car, bus: car, motorcycle: bicycle}`), run from the saved outputs under `~/results/627070/eval/` as runs `…-mapped` and `…-centre-mapped` (same 7,930 boxes):

| config | tp | fp | miss | precision | recall | mAP all = model | car AP | bicycle AP | pedestrian AP |
| --- | ---: | ---: | ---: | ---: | ---: | --- | --- | --- | --- |
| A unmapped, IoU 0.5 / 0.7 | 2,416 | 2,985 | 6,411 | 0.447 | 0.274 | all 0.088 / 0.074 · model 0.175 / 0.149 | 0.524 / 0.446 | 0.001 / 0.000 | 0.000 / 0.000 |
| A mapped, IoU 0.5 / 0.7 | 3,003 | 2,398 | 5,824 | 0.556 | 0.340 | 0.150 / 0.106 | 0.423 / 0.316 | 0.026 / 0.000 | 0.000 / 0.000 |
| B unmapped, centre 4 / 2 / 1 / 0.5 m | 2,884 | 2,517 | 5,943 | 0.534 | 0.327 | all 0.127 / 0.117 / 0.100 / 0.081 · model 0.255 / 0.234 / 0.200 / 0.163 | 0.655 / 0.599 / 0.560 / 0.483 | 0.014 / 0.014 / 0.011 / 0.003 | 0.095 / 0.087 / 0.029 / 0.002 |
| B mapped, centre 4 / 2 / 1 / 0.5 m | 3,968 | 1,433 | 4,859 | 0.735 | 0.450 | 0.345 / 0.296 / 0.228 / 0.148 | 0.696 / 0.567 / 0.458 / 0.351 | 0.244 / 0.233 / 0.197 / 0.090 | 0.095 / 0.087 / 0.029 / 0.002 |

With the map the union and model means coincide (every GT class is one of the model's three). Car AP at IoU 0.5 drops under the map (0.524 → 0.423) while precision and recall rise: trucks and buses now count as `car` ground truth, the model finds many of them within 2–4 m but not with 0.5 BEV IoU. The inference sbatch now writes the map into both configs for future runs. None of this is a comparison with the paper (no table quoted).

Viewing from a laptop. The server runs on a compute node (not the login node), bound to loopback, reached through a two-hop tunnel. The `.venv`'s editable install points at `main`, so `PYTHONPATH` must name the worktree, and modules do not inherit, so load the Python module first.

Terminal 1 — allocate a node and serve there:

```bash
ssh nanjaiyalathaa@hpc-head1.ewi.utwente.nl
srun --partition=main --cpus-per-task=2 --mem=8G --time=1:00:00 --pty bash
hostname                      # e.g. hpc-node03; this is the node for the tunnel below
module load python/3.13.14
PYTHONPATH=$HOME/coperceive-adr0010 $HOME/coperceive-adr0010/.venv/bin/coperceive serve --host 127.0.0.1 --port 8731 \
    --root $HOME/results/627070/eval/scenes --predictions $HOME/results/627070/eval/predictions
```

Terminal 2 — the tunnel from the laptop through the head node to that node (the node name is resolved on the head node):

```bash
ssh -J nanjaiyalathaa@hpc-head1.ewi.utwente.nl -L 8731:127.0.0.1:8731 nanjaiyalathaa@<node>
```

then open `http://127.0.0.1:8731/#/viewer` on the laptop. The second hop works only while the `srun` allocation holds the node. `627070/eval` holds all four runs (unmapped and mapped, IoU and centre); `627093/` holds the two unmapped runs under `scenes/` and `predictions/` directly. The scenes were converted from the trimmed tree: no lidar and no image files, so the page shows boxes, predictions and poses only. Checked through `TestClient` only, not in a browser.

## Viewer check after Adarsh's first session (2026-10-17)

Adarsh viewed `~/results/627070/eval` from his laptop (server on `hpc-head1`, single-hop tunnel) and reported: (1) Play does not advance frames, (2) the scene selector does not switch and feels hard-coded to Town03-000, (3) no Predictions block or prediction boxes even with `--predictions`, (4) points, images and map empty, (5) silent panes when data is missing.

**What reproduced in headless Chromium** (Playwright 1.64, `chromium_headless_shell` 156, SwiftShader WebGL, on the login node, niced, ~25 s per run; script and JSON under the session scratchpad, screenshots sent to Adarsh): against the same root and the same committed bundle (`coperceive/app/static/assets/index-BBhIjMFy.js`), Play advanced the sample index with the clock (index 4 at 0.41 s, 49 at 4.97 s, Pause shown) and choosing Town06-007 switched: `GET /api/scenes/Town06-007`, `/frame?time_us=1752300000`, `/map/lines`, the status line, the crumb, the tick labels (1752.3 s … 1767.1 s) all followed. The server side answers correctly for every scene (`TestClient` probe: list, summary, frames, map lookup, predictions, 0.01–0.3 s each). So (1) and (2) did **not** reproduce here; the cross-engine runs (Firefox, WebKit, 250 ms latency, DPR 2, small window, real pointer events, switch while playing) are reported below. (3) reproduced exactly: the committed bundle has **zero** occurrences of "predictions". It was built at `049145c` (2026-10-16, before `92fafe0` added the predictions UI) and CI does not rebuild it (the `web` job only type-checks). (4) is the data: the eval scenes were converted from the trimmed tree without lidar or images, and no maps root existed. (5) was true: `no map.` in the status line was the only hint.

**Root causes and fixes** (commits `dc93828`, `49aa354`, `560f980`, `8c7a745` on `feat/eval-step-a`):

- (3) Stale packaged bundle. New `coperceive serve --static DIR` serves an out-of-tree build (`web/node_modules/.bin/vite build --outDir ~/coperceive-web-dist --emptyOutDir`, 3 s on the login node; nothing under `coperceive/app/static` was touched or committed). With it the Predictions block renders on the real run (Town06-007 sample 1: TP 1, FP 2, Miss 4, Ignored 8 at score 0.40). **Adarsh: `coperceive/app/static` still needs a rebuild and commit from a machine that may do that; the wheel ships the old page until then.**
- (4) `scripts/griffin/stage_viewer.py` stages the 10 val scenes on a compute node without copying the dataset: val-only pose/label/calib plus the vehicle lidar (~150 MB) rsynced into `/local/$SLURM_JOB_ID/coperceive-viewer/raw`, `convert griffin` in process (so `points.bin` is written), `<scene>/<side>/camera` symlinked to the release (no PNG copied; served through the new `coperceive serve --media-root <release>`, which lets an image link resolve into an explicitly allowed directory and nothing else), the four CARLA town maps ingested and aligned. The OpenDRIVE files are at `~/maps-src/carla-0.9.15/` with `SHA256SUMS`: they came from public GitHub mirrors (autonomousvision/fail2drive and four byte-identical copies), every digest equal to the ADR 0008 pin; the 8.4 GB CARLA package was never downloaded. Section in `docs/datasets/griffin.md`. A one-scene run on the login node (Town06-007, `--no-lidar`, 1 worker) took 6.7 s; served with `--media-root` the first vehicle front image came back as a 2,831,892-byte PNG, without it 400; map lines 444,416 bytes; alignment identity, median residual 0.0011 m.
- (5) Empty states: `GET /api/scenes/{id}` adds `point_sweeps`, `image_rows`, `images_staged`; the page shows a missing-data pill in the viewport (`Points: no lidar staged.`, `Camera: no camera images staged.`, `Map: no map for Town06.`, `Predictions: no prediction runs for this scene.` / `no predictions root; start the server with --predictions.`), repeats the points and map lines in the Layers pane, and the camera panel says `No image: …`. The map effect asks `/map` first, so the note names the scene's own town. The camera strip no longer requests images for a scene that has none staged (before: nine 404s per sample, ~90 per second during play on Griffin).
- (1)(2) `tests/test_viewer_browser.py` pins the behaviour in a real browser: Play advances with the clock and pauses at the end; switching scenes in both directions re-reads frames, retakes the timeline and follows each town's map; the Predictions block names the run with counts; each pane names what is missing. Four tests, 26 s on the login node. CI gains an `e2e` job that builds `web/` fresh (so it tests the source, not the stale bundle) and uploads the screenshots. `playwright` joined the `dev` extra; on the HPC it is installed in the shared `.venv` with the browsers under `~/.cache/ms-playwright`.

Serving the staged val scenes (replaces the block above for a full view; the old block still works for boxes-only):

```bash
ssh nanjaiyalathaa@hpc-head1.ewi.utwente.nl
srun --partition=main --cpus-per-task=2 --mem=8G --time=2:00:00 --pty bash
hostname                      # the node for the tunnel
module load python/3.13.14
export PYTHONPATH=$HOME/coperceive-adr0010
$HOME/coperceive-adr0010/.venv/bin/python $HOME/coperceive-adr0010/scripts/griffin/stage_viewer.py \
    --raw /datasets/eemcs/ps/cv/huggingface/griffin/datasets/griffin_50scenes_25m/griffin_50scenes_25m/griffin-release \
    --split $HOME/src/griffin/data/split_datas/griffin_50scenes_25m.json \
    --xodr-dir $HOME/maps-src/carla-0.9.15 \
    --predictions $HOME/results/627093/predictions \
    --static $HOME/coperceive-web-dist \
    --workers 2
# ... then run the `coperceive serve` line it prints last, in the same shell
```

Terminal 2 from the laptop: `ssh -J nanjaiyalathaa@hpc-head1.ewi.utwente.nl -L 8731:127.0.0.1:8731 nanjaiyalathaa@<node>` (the real node name, no angle brackets), then `http://127.0.0.1:8731/#/viewer`. The ten-scene run with lidar has not been timed in an allocation yet (expect a few minutes for the rsync and the convert with two workers).

### Gates for the viewer check

Job 627308 (`ps,main-cpu`, hpc-node12, 2 min 18 s, `REPO=~/coperceive-adr0010`, `COPERCEIVE_WEB_DIST=~/coperceive-web-dist`, `PYTEST_ADDOPTS=-rs`): ruff check and format clean, pyright 1.1.414 0 errors, **364 passed, 2 skipped** (the two Griffin-raw skips) in 62 s. The four browser tests and the five staging tests ran on the compute node, so headless Chromium starts there without extra system libraries; their screenshots are in `~/results/viewer-e2e/`.
