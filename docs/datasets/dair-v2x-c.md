# DAIR-V2X-C (vehicle–infrastructure cooperative)

DAIR-V2X-C is the cooperative subset of DAIR-V2X (Yu et al., CVPR 2022,
[arXiv:2205.08540](https://arxiv.org/abs/2205.08540)) with one vehicle and one
roadside infrastructure unit. Coperceive reads the **published cooperative
layout**, optionally with the **CoAlign complemented annotations**.

## Availability and licence

- **Cluster copy:** `/datasets/eemcs/ps/cv/air-thu/dair-v2x-c_raw` contains only
  the CoAlign complemented annotations (`DAIR-V2X-C_Complemented_Anno/` with
  `train.json`, `val.json`, `readme.md`, `new_labels/cooperative_label` and
  `new_labels/vehicle-side_label`). The published sensor tree
  (`DAIR-V2X-C/Full Dataset (train&val)` and `Example`) is **empty**. Real
  cluster verification is blocked until Adarsh obtains the DAIR-V2X-C sensor
  data (application on the AIR/Tsinghua site).
- **Licence: unknown until verified.** The DAIR-V2X repository and data README
  do not state a data licence in the pages checked on 2026-11-05
  ([DAIR-V2X/data README](https://github.com/yujmo/DAIR-V2X/blob/main/data/README.md)).
  Cite the paper. Do not redistribute frames or derived scenes; research use on
  the HPC only, and nothing derived enters this repository.
- The complemented annotations are the CoAlign release
  ([site](https://siheng-chen.github.io/dataset/dair-v2x-c-complemented/),
  [CoAlign](https://github.com/yifanlu0227/CoAlign)); they regenerate the
  cooperative labels in the original format.

## Published raw layout (reference; to verify against the cluster copy)

```text
<root>/
  cooperative-vehicle-infrastructure/
    infrastructure-side/
      image/<id>.jpg
      velodyne/<id>.pcd
      calib/virtuallidar_to_world/<id>.json
      calib/virtuallidar_to_camera/<id>.json
      calib/camera/<id>.json
      label/virtuallidar/<id>.json
      label/camera/<id>.json
      data_info.json
    vehicle-side/
      image/<id>.jpg
      velodyne/<id>.pcd
      calib/lidar_to_novatel/<id>.json
      calib/novatel_to_world/<id>.json
      calib/lidar_to_camera/<id>.json
      calib/camera_intrinsic/<id>.json
      label/lidar/<id>.json
      label/camera/<id>.json
      data_info.json
    cooperative/
      data_info.json
      label_world/<id>.json
  DAIR-V2X-C_Complemented_Anno/
    train.json
    val.json
    new_labels/cooperative_label/label_world/<id>.json
    new_labels/vehicle-side_label/label_world/<id>.json
```

- `data_info.json` files are lists of frame records. The cooperative row has
  `infrastructure_image_path`, `infrastructure_pointcloud_path`,
  `vehicle_image_path`, `vehicle_pointcloud_path`, `cooperative_label_path`.
- Vehicle and infrastructure rows share `batch_id`; a batch is one scene
  (sequence). `pointcloud_timestamp` is microseconds.
- Splits are exactly `train` and `val` from the complemented annotation
  package. The published challenge test split is not in the cluster copy; do
  not invent it.

**To verify against the cluster copy:** the exact `data_info.json` field names,
whether cooperative labels are only under `cooperative/label_world/` or also in
the complemented tree, the camera file extension, and the calibration JSON
shapes. The adapter accepts the complemented label tree when present and falls
back to `cooperative_label_path`.

## Frames, agents and sensors

- **World frame:** DAIR's vehicle/world frame from `novatel_to_world` and the
  infrastructure `virtuallidar_to_world`. No y-reflection is applied: DAIR is
  not one of the CARLA left-handed OpenCOOD sources. Cooperative labels are
  `world_8_points`; boxes are stored in the **world** frame.
- **Agents:** `vehicle` (vehicle-side LiDAR) and `infrastructure`
  (infrastructure virtual LiDAR, roadside unit).
- **Vehicle pose:** `T_world_from_lidar = T_novatel_to_world ∘
  T_lidar_to_novatel`.
- **Infrastructure pose:** `T_world_from_virtuallidar =
  T_virtuallidar_to_world`.
- **LiDAR:** one PCD per side per frame (`x y z` + RGB); intensity is filled
  with `0.0` because no intensity field is published.
- **Cameras:** one image per side; the adapter exposes LiDAR point clouds and
  world boxes first. Camera intrinsics/extrinsics are kept for a later
  viewer-image pass.
- **Cooperative label:** a list of `{"type", "world_8_points",
  "system_error_offset"}`. Only car/truck/van/bus are annotated.

## Calibration transform checks

Hand-computed, covered by `tests/test_adapters_dair_v2x_c.py`:

- Vehicle at world origin (identity `lidar_to_novatel` and
  `novatel_to_world`), infrastructure `virtuallidar_to_world` translated
  `(5, 0, 0)` → the infrastructure origin in the vehicle LiDAR frame is
  `(5, 0, 0)`.
- A world-axis-aligned box with x∈[0,4.5], y∈[−1,1], z∈[0,1.5] has centre
  `(2.25, 0, 0.75)`, size `(4.5, 2.0, 1.5)` and yaw 0.
- The corner order of `world_8_points` is **to verify against the cluster
  copy**; the adapter derives the edges from corners 0→1 (length), 0→3 (width),
  0→4 (height). A different ring order changes axes, not the centre.

## Coperceive commands

```bash
coperceive adapters check dair-v2x-c --raw-root <root> --param split=train
coperceive dataset detect --path <root>
coperceive dataset index --adapter dair-v2x-c --raw-root <root>
```

**Verified:** synthetic fixture only (published layout + complemented labels),
no real frames. **Not verified:** the real cluster copy, because the sensor
data is absent. Do not treat this as a working real-cluster adapter yet.

## Synthetic fixture

`tests/test_adapters_dair_v2x_c.py` writes the layout above with one batch, one
frame and one world box. It checks the two agents, split validation, missing
frames, world box conversion and the infrastructure→vehicle transform.
