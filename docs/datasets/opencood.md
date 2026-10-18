# OPV2V and V2XSet (OpenCOOD layout)

Two CARLA-based cooperative perception datasets with the same on-disk layout, read through the builtin manifests `opv2v` and `v2xset` (`coperceive/adapters/builtin/`). The manifest rules they needed beyond Griffin's are [ADR 0012](../adr/0012-left-handed-sources.md). Nothing in this page or in the repository holds dataset bytes.

## Licence: unknown

Neither dataset publishes a licence. The OPV2V page states "Copyright © 2021 UCLA Mobility Lab. All Rights Reserved."; the OpenCOOD and v2x-vit code repositories are MIT and say nothing about the data; the V2X-ViT paper states no terms. Both manifests carry `licence: {status: unknown, ...}` with the links, and `coperceive adapters check` prints it. Rule in this project (2026-10-18): local viewing and evaluation on the HPC for research is fine; no frames, crops, screenshots or derived scene files from either dataset enter the public repository; test fixtures are synthetic (`tests/opencood_release.py`); Playwright screenshots stay under `~/results/`.

- OPV2V: <https://mobility-lab.seas.ucla.edu/opv2v/>, code <https://github.com/DerrickXuNu/OpenCOOD>, paper arXiv:2109.07644 (Xu et al., ICRA 2022).
- V2XSet: code <https://github.com/DerrickXuNu/v2x-vit>, paper arXiv:2203.10638 (Xu et al., V2X-ViT, ECCV 2022).

## Layout

```text
<split>/<scene>/data_protocol.yaml           # OpenCDA simulation configuration
<split>/<scene>/<agent>/NNNNNN.yaml          # lidar_pose, true_ego_pos, ego_speed, camera0..3, vehicles
<split>/<scene>/<agent>/NNNNNN.pcd           # ASCII PCD, fields x y z rgb
<split>/<scene>/<agent>/NNNNNN_cameraK.png   # 800x600
```

Scenes are named by their collection time; agents by their CARLA actor id. In V2XSet the roadside unit is the agent directory `-1` (29 of 58 scenes). On the HPC: `/datasets/eemcs/ps/cv/opencood/opv2v` (train 44, validate 9, test 16, test_culver_city 4 scenes) and `/datasets/eemcs/ps/cv/opencood/v2xset` (train 33, validate 6, test 19).

## Conventions, from public documentation only

- World and body frame: CARLA's, "the Unreal Engine coordinates system. This is a Z-up left-handed system" (CARLA Python API); pitch, yaw, roll are rotations about Y, Z, X in degrees. The manifests declare `source_handedness: left` and `reflect_axis: y`: one reflection `diag(1, -1, 1)` is applied to every pose, extrinsic, box and lidar point before the usual axis remaps, so source `+y` is schema `-y` and yaw and roll change sign. The box rotation is decoded as `Rz(yaw) Ry(pitch) Rx(roll)`.
- `lidar_pose` is `[x, y, z, roll, yaw, pitch]` in the map frame (OpenCOOD data-annotation page) and is the agent's pose: the body is the lidar frame and the fixed sensor `lidar` sits at identity. Camera `extrinsic` is "from camera to LiDAR" (`child_to_parent`); camera axes after the reflection are `FLU` (x forward is the optical axis, y left, z up), which the builder remaps to the schema's `RDF`. Checked on a real OPV2V frame: boxes left of the ego project into the left half of the front image, a box straight ahead into the centre.
- Boxes: `location` is the actor's position, `center` the offset to the box centre in the box's own axes, `extent` half sizes; the manifest's `centre: {location, offset, offset_frame: box}` and `size: {half_extents}` rules build `location + R · center` and `2 · extent`. `angle` is `[roll, yaw, pitch]`. The format has no class field: every box is `vehicle`. The `vehicles` mapping key becomes `track_id`.
- Per-agent boxes: each agent's list holds the vehicles with at least one lidar hit from that agent (OpenCOOD page). The lists differ per agent and are stored once each, in `frame: world`; a shared id is named once in `objects.parquet`. The ego vehicle appears in the other agents' lists, never in its own.
- Clock: `time_us = stem × 50000`. Every `data_protocol.yaml` has `world.fixed_delta_seconds: 0.05` and `sync_mode: true`; stems advance by 2 per saved frame, matching the OPV2V paper's "streamed at 20 Hz and recorded at 10 Hz" and V2X-ViT's "record LiDAR point clouds at 10 Hz". That the stem is the simulator tick count is an assumption; the files hold no wall clock.
- Town: not in the files. `coperceive maps identify` scores the vehicle poses against every ingested CARLA town at identity and accepts a town only below `--accept-m` (0.5 m) with `--margin-m` (2 m) over the runner-up; the result is a platform table that `adapters ingest --platform-table` applies. Culver City has no OpenDRIVE and stays map-less.

## Commands

```bash
coperceive adapters check opv2v --raw-root /datasets/eemcs/ps/cv/opencood/opv2v --param split=test --scenes 2021_08_20_21_10_24
coperceive maps ingest --xodr ~/maps-src/carla-0.9.15/Town06.xodr --map-name Carla/Maps/Town06 --out MAPS   # one per town
coperceive maps identify --maps MAPS --scenes SCENES --out scene_towns.yaml
coperceive adapters ingest v2xset --raw-root /datasets/eemcs/ps/cv/opencood/v2xset --param split=train --out SCENES --platform-table scene_towns.yaml
```

On the HPC the whole flow runs in `scripts/opencood/stage_viewer.py` inside a compute-node allocation (`~/scripts/slurm/opencood-viewer-stage.sbatch`), which stages into `/local/$SLURM_JOB_ID`, links the images from the dataset through `--media-root`, and prints the serve line.

## What was measured (2026-10-18, login node, five samples per scene)

- OPV2V `test/2021_08_20_21_10_24` (3 CAVs, 99 samples): boxes at the first sample 14, 31 and 27, union 33, intersection 11; boxes with at least one of the agent's own lidar points inside (0.15 m slack) over five samples: 73 of 73, 156 of 157, 133 of 133; about 58,000 points per cloud; town identified as `Carla/Maps/Town06` at 0.050 m median residual, runner-up Town05 at 74.8 m.
- V2XSet `train/2021_08_20_20_39_00` (RSU `-1` and CAV `1752`, 376 samples): boxes 34 and 19, union 34, intersection 19; points inside 166 of 170 (RSU) and 65 of 86 (CAV); RSU lidar pose at z = 3.50 m (V2X-ViT says the sensors sit at 14 feet, 4.27 m; the difference is recorded, not resolved); town not identified on five samples: Town06 at 0.043 m against Town03 at 0.245 m is within the 2 m margin. The full scene decides in the staging job.
- Loading five samples of three agents took 7.7 s, so the ASCII PCD parse is about 0.5 s per cloud; whole scenes are parsed in the staging job only.
- No OPV2V scene has a `-1` directory.

## What was measured (2026-10-18, staging job 629196, compute node, whole scenes)

- OPV2V `test/2021_08_20_21_10_24`: staging wall time 131 s (poses pass 31.2 s, full ingest 73.8 s with 4 workers, identify 8.7 s, align 9.2 s); 16,942,800 points in 295 clouds; town identified as `Carla/Maps/Town06` at 0.020 m over 295 poses, runner-up Town05 at 50.8 m; the viewer shows `172860 points, 72 boxes, 12 frustums. map drawn.` with 7 camera thumbnails decoded and the split view open.
- V2XSet `train/2021_08_20_20_39_00`: staging wall time 281 s (poses pass 92.3 s, full ingest 176.5 s); 29,237,703 points in 752 clouds; town unidentified: Town06 at 0.046 m against Town03 at 1.060 m over 376 poses is within the 2 m margin, so the scene is map-less and the table says so; the viewer shows `77782 points, 53 boxes, 8 frustums. no map name in this scene.` with 7 thumbnails and the split view open, agent `-1` typed `infrastructure`.
- Both scenes in headless Chromium with no page errors; the only HTTP errors are the `/predictions` probes (no predictions root). Screenshots stay under `~/results/629196/viewer-check/`.
