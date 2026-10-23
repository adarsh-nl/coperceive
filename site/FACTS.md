# Facts on the landing page

Every number and claim the page states, and where it comes from. Illustrative-scene
parameters are labelled on the page and are not Griffin sensor specifications.

Checked against this tree on 2026-10-23. `TASKS.md` is not on `feat/landing`; the
landing notes are in `site/TASKS-landing.md`.

## Page claims

| Claim on the page | Source |
| --- | --- |
| Two agents, vehicle and drone | `coperceive/adapters/builtin/griffin.yaml:34-36` |
| 9 cameras | `docs/datasets/griffin.md:46` lists drone front/back/left/right/bottom and vehicle front/back/left/right. `docs/datasets/griffin.md:151` lists the vehicle four cameras plus `lidar_top`. `docs/datasets/griffin.md:204` states 9 cameras. |
| 149 samples in Town03-000 | `docs/design/griffin-verification.md:55` — frames 11185 through 11333, 149 frames. |
| 14.8 s, first frame to last | Same frame ids. `(11333 − 11185) × 0.1 s = 14.8 s`. The 0.1 s step is the 10 Hz clock below. The verification note says scene lengths span 14.7–14.9 s; 14.8 s is this scene's first frame to last, not a separately published duration. |
| 10 Hz | `coperceive/adapters/builtin/griffin.yaml:16-19` (`time_us = frame_id * 100000`, the paper's 10 Hz lidar rate; the files have no wall clock). `docs/datasets/griffin.md:86` states the same. |
| Griffin 25 m, scene Town03-000 | `docs/design/griffin-verification.md:55`. Subset name `25m` is `griffin.yaml:9`. |
| Map name `Carla/Maps/Town03` | `docs/design/griffin-verification.md:59`. |
| Recordings are not in the repository | `docs/datasets/griffin.md:3`. `README.md:49`. |
| Player is a 100-frame excerpt | `scripts/site/export_hero_clip.py:35-37` (`START_TIME_US = 1118500000`, `NUM_FRAMES = 100`, `TIME_STEP_US = 100000`). `site/public/hero/manifest.json` `num_frames` is 100. This is not the full 149 frames. |
| Not published on PyPI | `docs/design/naming.md:9` — `GET https://pypi.org/pypi/coperceive/json` returned 404 on 2026-10-15. This page does not claim a later check. |
| Python 3.10–3.13 | `README.md:9`. |
| Install commands | `README.md:12-14`. |
| Download, convert, and serve commands | `README.md:24`, `README.md:45-46`. `--dry-run` is the command the page shows. A download that writes files also needs `--accept-griffin-terms` (`docs/datasets/griffin.md:10`). |
| Dev preset is drone metadata, vehicle metadata, and vehicle LiDAR | `README.md:49`. `docs/datasets/griffin.md:17`. |
| Validate writes JSON and Markdown, and the centreline block does not fail the run | `README.md:86-89`. |
| Viewpoints: orbit, bird's-eye, chase, drone, camera frustum | `README.md:29`. |
| Eval matches predictions to that agent's ground truth and does not quote a score here | `README.md:64-70`. The page states no precision, recall, or mAP. |
| HD maps: fetch Town03, Town06, Town07, Town10HD; OpenDRIVE bytes are not committed | `README.md:94-99`. `docs/adr/0008-hd-map.md:11`. |
| Export MP4, GIF, PNG sequence, and Rerun | `README.md:29-33`. |
| `--slurm` prints an sbatch script and does not submit unless `--submit` | `README.md:78-81`. `coperceive/models/commands.py:94` and `:258`. |
| macOS desktop app is specified, and there is no GitHub Release | `docs/adr/0014-desktop-app.md:9` (Apple Silicon), `:19-20` (Tauri v2), `:84` (`.dmg`). `README.md:7` and `README.md:37` still say this tree does not build the desktop shell. The page does not link an Actions artifact. |
| Software licence Apache-2.0 | `LICENSE:1` and `LICENSE:3`. `CITATION.cff:9`. `docs/adr/0006-licence.md:3` and `:23`. |
| Author: Adarsh, University of Twente | `CITATION.cff:6-8`. No surname is printed (`docs/adr/0006-licence.md:23`). |
| Griffin dataset, MIT | `coperceive/griffin/licence.py:5-6` and `:16`. `docs/datasets/griffin.md:5`. `THIRD_PARTY_NOTICES.md:5-10`. |
| CARLA map assets, CC-BY | CARLA's own README, `dev` branch lines 122 and 124, read on 2026-10-23: code is MIT, CARLA-specific assets are CC-BY. <https://github.com/carla-simulator/carla/blob/dev/README.md>. This repository's `docs/adr/0008-hd-map.md:11` and `THIRD_PARTY_NOTICES.md:37` call the CARLA package MIT, which matches the code licence. The page credits the assets because a map is drawn and the asset licence is CC-BY. OpenDRIVE bytes are not committed (`docs/adr/0008-hd-map.md:11`). |
| Animated scenes are synthetic and illustrative | The bird's-eye scenes are generated in `site/src/backdrop/scene.ts` (a copy of `web/src/desktop/backdrop/scene.ts` from `feat/desktop` at `bf36d0d`). They are not Griffin frames. |
| Geist and Geist Mono, OFL-1.1 | Packages `@fontsource-variable/geist` and `@fontsource-variable/geist-mono`. Licence texts are `site/public/licenses/OFL-Geist.txt` and `site/public/licenses/OFL-Geist-Mono.txt`. |

## Illustrative scene parameters

These appear only next to the "Illustrative scene" label. They are drawing parameters, not measured Griffin ranges.

| Parameter | Source in the copied backdrop |
| --- | --- |
| Car ring 45 m | `site/src/backdrop/scene.ts:252` (`dv <= 45`). Drawn at `site/src/backdrop/drawBev.ts:193`. |
| Drone ring 55 m | `site/src/backdrop/scene.ts:255` (`du <= 55`). Drawn at `site/src/backdrop/drawBev.ts:200`. |
| Drone height 24 m | `site/src/backdrop/scene.ts:150` (`z: 24`). |
| Occlusion counter | Count of in-range objects the car misses and the drone sees at `occlusionTime()` in `site/src/backdrop/story.ts`. The page says the count belongs to the illustrative scene, not to a model. |

The backdrop files are copied from `web/src/desktop/backdrop/` on `feat/desktop` at `bf36d0d` (bytes unchanged through `89fa0aa`). This page does not import `web/`.

## Not stated

- AirSim. No file in this tree states it. The page does not say it.
- Visibility 1.0, a 17.5 m occlusion distance, a 27 m altitude, a lidar channel count, or "0.0137 m" as a precision. Removed from the page and from `site/public/hero/manifest.json`.
- Model accuracy, mAP, recall of a real detector, fusion latency, or "zero configuration".
- A surname for Adarsh. The software is not described as MIT.
- Track 265. The exporter still stores `time_us` 1123700000 in the manifest. The page does not display it.
