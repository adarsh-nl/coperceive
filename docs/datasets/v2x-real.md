# V2X-Real (OpenCOOD layout)

V2X-Real is a large-scale real-world vehicle-to-everything (V2X) cooperative
perception dataset with both connected autonomous vehicles and smart roadside
infrastructure (Xiang et al., ECCV 2024,
[arXiv:2403.16034](https://arxiv.org/abs/2403.16034)). Coperceive reads an
**already extracted, OpenCOOD-style V2X-Real tree** via the built-in manifest
`coperceive/adapters/builtin/v2x-real.yaml`; it never downloads or writes into
the dataset root.

## Licence and citation

**Data licence: unknown; not assumed.** The official repository README
([ucla-mobility/V2X-Real](https://github.com/ucla-mobility/V2X-Real)) links the
paper and the UCLA Mobility Lab project page
([mobility-lab.seas.ucla.edu/v2x-real/](https://mobility-lab.seas.ucla.edu/v2x-real/)),
and does not state a data licence. The repository
[LICENSE](https://github.com/ucla-mobility/V2X-Real/blob/main/LICENSE) is an
Academic Software License for the code, not a grant for the dataset. Do not
commit real V2X-Real frames, point clouds, images, or derived scene files to
this repository.

The authors give the following citation in the repository README:

```bibtex
@article{xiang2024v2x,
  title={V2X-Real: a Large-Scale Dataset for Vehicle-to-Everything Cooperative Perception},
  author={Xiang, Hao and Zheng, Zhaoliang and Xia, Xin and Xu, Runsheng and Gao, Letian and Zhou, Zewei and Han, Xu and Ji, Xinkai and Li, Mingxi and Meng, Zonglin and others},
  journal={arXiv preprint arXiv:2403.16034},
  year={2024}
}
```

## What is supported

- **Splits:** `train`, `validate`, and `test` (passed via `--param split=<name>`).
- **Agents:** automatic discovery of connected autonomous vehicles (non-negative
  directory IDs, e.g. `1`, `2`, mapped to `vehicle`) and smart infrastructure
  roadside units (`-1`, `-2`, mapped to `infrastructure`), matching the
  `int(idx) < 0` rule in
  [`opencood/data_utils/datasets/basedataset.py`](https://github.com/ucla-mobility/V2X-Real/blob/main/opencood/data_utils/datasets/basedataset.py).
- **Poses:** per-frame `lidar_pose = [x, y, z, roll, yaw, pitch]` (metres and
  degrees) from `<frame>.yaml`.
- **LiDAR:** per-frame headerless float32 `N × 4` `<frame>.bin` point clouds
  (`bin_f32` reader, `stride: 4`, `x/y/z` at offsets `0/1/2`). Intensity is
  filled with `0.0` because the official loader calls
  `pcd_utils.load_lidar_bin(..., zero_intensity=True)` with the comment
  `"hard coded, no intensity"`
  ([`opencood/utils/pcd_utils.py`](https://github.com/ucla-mobility/V2X-Real/blob/main/opencood/utils/pcd_utils.py)).
- **Multi-class 3D bounding boxes:** per-frame `vehicles` mapping keyed by
  object ID, reading `location`, `center`, `extent` (half-extents),
  `angle` (`[roll, yaw, pitch]` in degrees), and `obj_type` (mapped from the
  V2X-Real fine-grained labels in
  [`opencood/data_utils/__init__.py`](https://github.com/ucla-mobility/V2X-Real/blob/main/opencood/data_utils/__init__.py),
  keeping any unmapped class string intact).
- **Cameras:** not ingested by default. The original `ucla-mobility/V2X-Real`
  loader is LiDAR-only. Its maintained successor
  ([`ucla-mobility/QuantV2X`](https://github.com/ucla-mobility/QuantV2X/blob/main/opencood/data_utils/datasets/basedataset/v2xreal_basedataset.py))
  loads `_cam1.jpeg`…`_cam4.jpeg` (4 on vehicles, 2 on infrastructure) and
  derives camera-to-LiDAR extrinsics from world-frame `camN.cords` via
  `x1_to_x2(cam_cords, lidar_pose_clean)` rather than a stored 4×4 `extrinsic`
  matrix. Manifest spec version 1 does not compute relative extrinsics from two
  world poses, so `sensors` keeps optional `camera*` records for OpenCOOD-style
  trees and always registers the fixed `lidar` sensor.

## Raw layout

From the official README and `basedataset.py`:

```text
<root>/
  train/
    <YYYY-MM-DD-HH-MM-SS>_<m>_<n>/
      -1/                          # smart infrastructure (RSU)
        000000.yaml
        000000.bin
      -2/                          # second smart infrastructure (RSU)
        ...
      1/                           # connected autonomous vehicle
        000000.yaml
        000000.bin
      2/                           # second connected autonomous vehicle
        ...
  validate/
    ...
  test/
    ...
```

## Conventions and unverified items

Verified from the authors' loader (`ucla-mobility/V2X-Real` and
`ucla-mobility/QuantV2X`):

- **Split names:** `train`, `validate`, `test` (`README.md`).
- **Agent role rule:** negative integer directory names (`-1`, `-2`) are
  infrastructure; non-negative directory names (`1`, `2`) are vehicles
  (`basedataset.py`).
- **Pose and box angle order and units:** `[x, y, z, roll, yaw, pitch]` with
  angles in **degrees**, composed by `x_to_world` in
  [`opencood/utils/transformation_utils.py`](https://github.com/ucla-mobility/V2X-Real/blob/main/opencood/utils/transformation_utils.py)
  using `np.radians`.
- **LiDAR file format:** `<frame>.bin` headerless float32 `N × 4` with zeroed
  intensity (`basedataset.py`, `pcd_utils.py`).
- **Box class field:** `obj_type` on each entry of `vehicles` (`box_utils.py`,
  `opencood/data_utils/__init__.py`).

**Unverified against a real extracted V2X-Real release** (no real V2X-Real
files were inspected when authoring this manifest):

- **Coordinate handedness / y-reflection (`world_frame: v2x-real-world`):**
  `x_to_world` applies no reflection and the repository states only that the
  data is in "OPV2V format". Like `v2v4real.yaml`, this manifest provisionally
  sets `source_handedness: left` with `reflect_axis: y` (ADR 0012). Visual
  inspection of a real extracted V2X-Real scene is required to confirm whether
  traffic is mirrored; if it is, remove `source_handedness` and `reflect_axis`
  from `coperceive/adapters/builtin/v2x-real.yaml`.
- **Frame stem width and cadence (`scale_us: 100000`):** the manifest uses
  6-digit stems (`{frame:06d}`) and assumes consecutive stems advance by 1 at
  10 Hz (`0.1 s` per step, matching `time_delay // 100` in `basedataset.py`).
  If a real release uses 5-digit stems or step-2 ticks, update the template and
  `clock.scale_us`.
- **Box `center` offset frame:** `box_utils.py` adds `location + center`
  directly in world coordinates, whereas OpenCOOD's annotation tutorial defines
  `center` in the box frame. The manifest follows `opv2v.yaml` / `v2v4real.yaml`
  (`offset_frame: box`), which agrees whenever `center` is `[0, 0, z]` with
  zero roll and pitch.
- **Additional infrastructure agent IDs:** `agents.discover.types` maps `-1`
  and `-2` to `infrastructure`. If any scenario uses further negative IDs, add
  them to `types`.
- **Camera ingestion:** `_cam1.jpeg`…`_cam4.jpeg` and `camN.cords` from
  `QuantV2X` are not yet wired into the manifest.

## Commands

```bash
coperceive adapters check v2x-real --raw-root <root> --param split=train
coperceive adapters ingest v2x-real --raw-root <root> --param split=train --out scenes
coperceive validate scenes
```

## Synthetic fixture

A hand-crafted synthetic fixture lives under `tests/fixtures/v2x_real/` with
one scenario (`2023-03-17-15-53-02_1_0`), one vehicle agent (`1`), one
infrastructure agent (`-1`), two 10 Hz frames (`000000`, `000001`), multi-class
boxes (`Car`, `Pedestrian`, `Truck`), and synthetic `.bin` point clouds whose
points lie inside each annotated box. `tests/test_adapters_v2x_real.py` checks,
loads, and ingests this fixture and verifies the hand-computed coordinate
transforms.
