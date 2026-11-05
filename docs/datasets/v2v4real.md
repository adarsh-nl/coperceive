# V2V4Real (OpenCOOD layout)

V2V4Real is a real-world vehicle-to-vehicle cooperative perception dataset
(Xu et al., CVPR 2023, [arXiv:2303.07601](https://arxiv.org/abs/2303.07601)).
Coperceive reads an **already extracted, OpenCOOD-style release tree**; it never
downloads or writes into the dataset root.

## Licence

**Unknown; not assumed.** The repository README
([ucla-mobility/V2V4Real](https://github.com/ucla-mobility/V2V4Real)) links the
paper and does not state a data licence. The repository
[LICENSE](https://github.com/ucla-mobility/V2V4Real/blob/main/LICENSE) is an
Academic Software License for the code, not a grant for the data. Terms are
unknown as of 2026-11-05. Cite the paper. Research use on the HPC only; do not
commit frames, crops, screenshots or derived scene files to this repository.

## Raw layout (published reference)

```text
<root>/
  train/
    testoutput_CAV_data_<YYYY-MM-DD-HH-MM-SS>_<n>/
      0/<frame>.yaml, <frame>.pcd, <frame>_cameraK.png
      1/...
  test/
    testoutput_CAV_data_<YYYY-MM-DD-HH-MM-SS>_<n>/
      0/...
      1/...
```

- Scenario names start with `testoutput_CAV_data_` **in both `train/` and
  `test/`**. This prefix is intentional and is the detection cue.
- Two vehicle agents per scenario, directories `0/` and `1/`.
- Each frame is an OpenCOOD YAML record (`lidar_pose`, `camera0`…`camera3`,
  `vehicles`), a PCD (x y z rgb) and camera PNGs.
- Split names are exactly `train` and `test`. There is no `validate` split in
  the published V2V4Real release; Coperceive rejects it rather than guessing.
- The cluster copy is currently **zip archives only** and may be partial
  (`test-001.zip`, `train-002.zip`, `v2vreal-…/Day20_test.zip`). Extraction is
  Adarsh's decision and location; the adapter only consumes the root it is
  given. Do not assume the copy is complete: the zip suffixes `-001`/`-002`
  may be parts of a split download, not separate splits.

**To verify against the cluster copy:** exact per-frame file names, camera count
and component names, scenario-name pattern, and whether `Day20_test/` is an
extra scenario root rather than a split. The adapter is written against the
published OpenCOOD layout and will be corrected from the listing.

## Agent roles, frames and sensors

- **Agents:** two connected **vehicles** (`0`, `1`). No roadside unit, drone or
  fixed infrastructure in V2V4Real.
- **World frame:** CARLA map frame, left-handed UE axes. ADR 0012 applies one
  y-reflection before the schema, exactly as for OPV2V/V2XSet. A source pose
  `(10, 20, 1.9)` becomes schema `(10, -20, 1.9)`; the vehicle moving towards
  source +y moves towards schema −y.
- **Poses:** `lidar_pose = [x, y, z, roll, yaw, pitch]` in the source frame for
  each agent; the pose stream is the agent's LiDAR frame. There is no global
  infrastructure→vehicle calibration as in DAIR-V2X-C: each agent's own
  `lidar_pose` is the calibration.
- **Cameras:** `camera0`…`camera3` records with `extrinsic` (camera→LiDAR) and
  `intrinsic`; 800×600 PNGs. The manifest derives image size as
  `2 × principal point`, as for OPV2V.
- **LiDAR:** one PCD per frame per agent, `FIELDS x y z rgb`; intensity is not
  present and is filled with `0.0`.
- **Boxes:** `vehicles` mapping keyed by actor id with `location`, `center`
  (box-frame offset), `extent` (half sizes), `angle = [roll, yaw, pitch]` in
  degrees and `speed`; `category` is filled as `vehicle` because the release
  has no class field.

## Coordinate transform checks

Hand-computed expectations, covered by `tests/test_adapters_v2v4real.py`:

- Source lidar pose `(10, 20, 1.9)`, yaw 90 → schema translation
  `(10, -20, 1.9)`; the box at source `(16, 30, 0)` with yaw 90 becomes schema
  centre `(16, -30, 0.75)`.
- The box centre offset `(0, 0, 0.75)` on a yaw-90 box points along source +y,
  hence schema −y.
- Applying the pose to the lidar-frame point `(10, -6, -1.15)` gives schema
  world `(16, -30, 0.75)`, which is the same reflected centre as the box; the
  unreflected point `(16, 30, 0.75)` must not appear.

## Coperceive commands

```bash
coperceive adapters check v2v4real --raw-root <root> --param split=train
coperceive dataset detect --path <root>/train
coperceive dataset index --adapter v2v4real --raw-root <root>/train
```

**Verified:** synthetic OpenCOOD V2V4Real-layout fixtures only (below).
**Not yet verified:** the real cluster tree, because it is still zipped and the
extracted listing has not been supplied. No real frames are in this repository.

## Synthetic fixture

`tests/opencood_release.py` writes the layout without dataset bytes; the
V2V4Real tests rename scenarios to `testoutput_CAV_data_…` and agents to
`0`/`1`. A wrong split (`validate`) and a scenario without agent directories
must fail with a clear message.
