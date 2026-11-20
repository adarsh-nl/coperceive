# Conventions probe

`conventions_probe.py` reads one frame of a real dataset and scores the readings of the conventions that [conventions.md](../../docs/geometry/conventions.md) leaves open. It is read-only: it opens files for reading and writes nothing (no cache, no temporary file, no bytecode). It prints the dataset name, the frame id and numbers only, never a path or a user name. One frame takes a few seconds on one core; a sweep is cut to 100 000 points with a fixed seed.

## Where to run it

On the cluster login node, from the repository root, with the repository's venv. It needs numpy and PyYAML only (no GPU, no network, no multiprocessing).

```bash
cd <repo>
source .venv/bin/activate
```

## Commands

Pick any frame that has annotated vehicles near the sensor. Replace the `<...>` placeholders; the arguments are paths and ids only.

```bash
# OPV2V: OPV-7 camera extrinsic direction, OPV-6 roll and pitch signs
python scripts/verify/conventions_probe.py opv2v <opv2v>/test/<scenario> <agent> <frame>
#   <agent> is the agent folder name (e.g. 650), <frame> the file stem (e.g. 000068)

# V2XSet: the same questions (a vehicle, or a roadside unit such as -1)
python scripts/verify/conventions_probe.py v2xset <v2xset>/test/<scenario> <agent> <frame>

# V2V4Real: V4R-7 box frame, V4R-1 handedness
python scripts/verify/conventions_probe.py v2v4real <v2v4real>/test/<scenario> <agent> <frame>
#   <agent> is 0 or 1, <frame> the file stem

# DAIR-V2X-C: DAIR-10 order of world_8_points
python scripts/verify/conventions_probe.py dair <dair-root> <cooperative frame id>
#   <dair-root> is the folder that holds cooperative-vehicle-infrastructure/
#   the frame id is the cooperative label's stem (normally the vehicle frame id)
```

Exit code 0 means a report was printed; 2 means an input problem (the message names the file by its role, for example "the agent YAML is missing"); 3 is a bug in the probe.

## What to paste back

The whole standard output of each run, from `conventions_probe 1` to the last `verdict` line. It holds no paths. If a run fails, paste the one `error:` line.

## How to read the scores

Every section ends with a line

```
verdict <row> <question>: <reading> margin <m> (next <reading> <score>)
```

The named reading has the highest score; `margin` is its lead over the next one, on a 0-to-1 scale. A margin above 0.3 is clear; the probe says `undecided` below 0.05 or with fewer than 30 points behind the best score.

- **OPV-6 `euler`** (`opv2v`, `v2xset`). `fraction` is the share of LiDAR points near a box that lie inside one, with `lidar_pose` and every box built from their angles. `adapter` composes `Rz(yaw) Ry(pitch) Rx(roll)` as the adapter does; `flipped` composes `Rz(yaw) Ry(-pitch) Rx(-roll)`, as OpenCOOD's `x_to_world`. Higher wins. When every roll and pitch in the frame is below 0.5 degrees the frame carries no evidence and the verdict is `undecided`; try a frame on a slope.
- **OPV-7 `camera_extrinsic`** (`opv2v`, `v2xset`). For each camera, every box in view is projected through the camera's own world pose (`cords`) and `lidar_pose`; the LiDAR points inside that box are projected through the `extrinsic` under each reading. `fraction` is the share of those points that land inside their box's projected outline. The right direction scores near 1, the wrong one far lower. `axes` says whether the camera side of the extrinsic is CARLA's sensor axes (x forward) or optical axes (z forward); each direction is scored with its better axes. `t_mismatch_m` is the distance between the extrinsic's translation under that reading and the one `cords` and `lidar_pose` give: near 0 for the right row.
- **V4R-7 `box_frame`** (`v2v4real`). `fraction` as for OPV-6, with the boxes read in the LiDAR frame (`lidar`) or in the frame of `lidar_pose` (`world`). Higher wins.
- **V4R-1 `handedness`** (`v2v4real`). Containment cannot decide it: a frame and its mirror image put the same points in the same boxes, so the `right` and `left` rows are equal and the verdict is always `undecided`. The traffic-side cue below it counts oncoming vehicles (heading opposite to the LiDAR's +x) on each side. In right-hand traffic, a right-handed frame with +x forward has them on `+y`; most of them on `-y` points to a left-handed (mirrored) frame. Several frames on two-way roads make this cue stronger.
- **DAIR-10 `corner_order`** (`dair`). The first table is the share of each side's points near a box that lie inside one. It is the same for every order (the eight corners span the same box whatever their order) and checks only that the frame, the pairing and the calibration were read right: expect it well above 0.5. The second table decides: `right_handed` counts boxes whose corners, read in that order, give a right-handed box frame, and `forward_agrees` counts boxes whose front, read in that order, points within 20 degrees of the heading of the matching single-view box (`rotation` read as radians from +x). `score` is `forward_agrees / matched`; higher wins. If every order scores low and `offset_deg` shows 90 or 270, the single-view `rotation` is measured from another axis (DAIR-12) and the order is still undecided; paste the table.
