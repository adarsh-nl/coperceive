# Generic multi-view dataset (`coperceive-generic-1`)

One small `scene.yaml` describes **any** set of viewpoints: moving vehicles,
roadside units, drones, or fixed inspection cameras/LiDARs. No ego vehicle, no
boxes, no map and no motion are required. It is read by the `generic` adapter
(`coperceive adapters check --format generic <dir>`); nothing writes into the
dataset root.

## Layout

```text
<dataset>/
  scene.yaml
  agents/                        # one layout choice, not required by the schema
    <agent_id>/
      poses.csv                  # optional for a moving agent
      boxes.csv                  # optional
      camera/{frame:06d}.png     # template example
      lidar/{frame:06d}.pcd      # or .bin, float32 xyzi
```

Only files named by `scene.yaml` are read. **Templates are free-form**: they are
relative to the dataset root, must stay inside it, and must contain exactly the
single field `{frame:06d}`, starting at frame 0. The `agents/` tree above is
just one layout; a user's existing paths can be named directly.

## `scene.yaml`

```yaml
schema: coperceive-generic-1
scene_id: inspection-01
handedness: right                 # right | left
frame_rate_hz: 10                 # OR timestamps_us: [...]
frame_count: 3                    # required with frame_rate_hz
# timestamps_us: [0, 100000, 200000]
# timestamp_file: timestamps.txt  # one integer µs per line
agents:
  - id: cam_north
    role: fixed camera            # free string; vehicle/uav/infrastructure special-cased
    fixed_pose:                   # exactly one of fixed_pose / poses
      translation_m: [0, -3, 2]
      rotation_rpy_deg: [0, 0, 0]
    sensors:
      - id: camera
        modality: camera          # camera | lidar
        files: "cam_north/images/{frame:06d}.png"
        intrinsic: [400, 400, 320, 240]
        body_from_sensor: {translation_m: [0, 0, 0], rotation_rpy_deg: [0, 0, 0]}
  - id: ego
    role: vehicle
    poses: ego/poses.csv
    boxes: ego/boxes.csv
    sensors:
      - id: lidar
        modality: lidar
        files: "ego/lidar/{frame:06d}.pcd"
```

- **Frames:** `timestamps_us` (integers, strictly increasing), a
  `timestamp_file`, or `frame_rate_hz` + `frame_count` (time in µs). When
  derived from `frame_rate_hz`, frame `index` (`0, 1, …, frame_count − 1`) gets
  timestamp `round(index * 1_000_000 / float(frame_rate_hz))` using Python's
  `round()` (nearest integer, ties to even, e.g. `2.5 → 2`, `3.5 → 4`) on
  IEEE 754 float64 division (`coperceive/adapters/generic.py`, `_timestamps`).
  Each frame is computed from `index` rather than accumulated step-by-step, so
  fractional periods do not drift. Any `poses.csv` or `boxes.csv` rows must use
  these exact rounded microsecond integers.

  *Example:* `frame_rate_hz: 30` and `frame_count: 4` produces timestamps
  `[0, 33333, 66667, 100000]` µs (`round(1_000_000 / 30.0) = 33333`,
  `round(2_000_000 / 30.0) = 66667`, `round(3_000_000 / 30.0) = 100000`).
- **Agent pose:** a `fixed_pose` (same pose every frame) or `poses.csv` with
  header `timestamp_us,x,y,z,roll_deg,pitch_deg,yaw_deg`. Pose is
  `world_from_agent_body`; translation is metres, angles degrees, composition
  `Rz(yaw) Ry(pitch) Rx(roll)` (intrinsic z-y-x). Pose timestamps must match
  the scene timestamps exactly; extra, missing or reordered timestamps are an
  error (not nearest or interpolated).
- **Sensor extrinsic:** `body_from_sensor` as translation/rpy **or** a 4×4
  `extrinsic` matrix (row-major, sensor→body). Default identity. The rpy order
  is `[roll, pitch, yaw]` degrees. **Camera frame:** depth is **+Z**, `x` right
  and `y` down, with `u = fx·x/z + cx`, `v = fy·y/z + cy` (see
  [format.md](format.md) §camera). A camera looking along the agent body +x,
  with image right along body −y and image down along body −z, has

  ```text
  R(body ← camera) = [[0, 0, 1],
                      [-1, 0, 0],
                      [0, -1, 0]]
  ```

  which is `rotation_rpy_deg: [-90, 0, -90]` in the
  `Rz(yaw)·Ry(pitch)·Rx(roll)` convention. The equivalent 4×4 `extrinsic` is
  `[[0,0,1,0], [-1,0,0,0], [0,-1,0,0], [0,0,0,1]]`. This is the convention the
  examples use; `[0, 90, 0]` would send image right to body −z (down) and is
  wrong.
- **Cameras:** `intrinsic: [fx, fy, cx, cy]` and optional
  `image_size: [width, height]`; files PNG/JPEG.
- **LiDAR:** PCD ASCII/binary with `x y z`, or a binary `.bin` of float32
  `x y z i` records. Points are stored in the **LiDAR sensor frame** in metres
  (see [format.md](format.md) §`points.bin` and §Axis conventions: `+x`
  forward, `+y` left, `+z` up in the sensor frame, mapped into body by
  `body_from_sensor`). The adapter reads `x, y, z` verbatim into `PointCloud`
  without transforming coordinates (`coperceive/adapters/generic.py`,
  `GenericAdapter.load`; `coperceive/adapters/readers/lidar.py`, `read_bin_f32`
  and `read_pcd`), and consumers map sweeps into the world frame via
  `world_from_body ∘ body_from_sensor` (`coperceive/viewdata.py`,
  `world_points`).

  *Example:* an agent at world `(10, 0, 2)` (identity rotation) with a roof
  LiDAR at `body_from_sensor.translation_m: [0, 0, 1.5]` (identity rotation)
  stores a return 5 m straight ahead of the sensor as `(5, 0, 0)` in the
  PCD/`.bin` file (not body `(5, 0, 1.5)` and not world `(15, 0, 3.5)`);
  `world_points` places it at `(15, 0, 3.5)`.
- **Missing frames:** the validator reports a warning per sensor, e.g.
  `sensor cam_north/camera: 3 of 120 frames missing (first: 000041)`, and load
  skips those frames rather than referencing absent files.
- **Boxes CSV** (optional, per agent):
  `timestamp_us,track_id,category,x,y,z,length,width,height,yaw_deg`, in the
  **world** frame. No boxes is valid; no track/category is required.
  - **Box position (`x, y, z`):** the 3D **geometric centre** of the box in the
    world frame (metres), not the bottom-centre or a corner
    (`coperceive/adapters/generic.py`, `GenericAdapter._boxes`; the eight
    corners are placed at `center + R · (±length/2, ±width/2, ±height/2)` in
    `coperceive/project.py`, `box_world_points`).

    *Example:* a car with `length = 4.0, width = 2.0, height = 1.6` resting on
    the ground (`z = 0`) at horizontal position `(10.0, -1.75)` has
    `x = 10.0, y = -1.75, z = 0.8` (bottom face at `z = 0.0`, top face at
    `z = 1.6`).
  - **Box extent axes and `yaw_deg`:** `length` lies along the box's local
    **`+x`** axis, `width` along **`+y`**, and `height` along **`+z`**
    (`coperceive/adapters/generic.py`, `GenericAdapter._boxes`;
    `coperceive/project.py`, `box_world_points` / `CORNER_SIGNS`). `yaw_deg` is
    a right-handed rotation `Rz(yaw_deg)` about world **`+z`** in degrees with
    roll and pitch fixed to `0` (`coperceive/adapters/generic.py`,
    `GenericAdapter._boxes`; `coperceive/se3.py`, `SE3.from_rpy_deg`):
    **`yaw_deg = 0`** aligns the length axis (`+x`) with world **`+x`** (and
    width with world `+y`), positive `yaw_deg` rotates **counter-clockwise**
    from world `+x` toward world `+y` (matching the agent pose yaw convention),
    and omitting the `yaw_deg` column defaults to `0.0`.

    *Example:* a box centred at `(10, 0, 0.8)` with `length = 4.0, width = 2.0,
    height = 1.6` spans `x ∈ [8, 12], y ∈ [-1, 1]` at `yaw_deg = 0`, and spans
    `x ∈ [9, 11], y ∈ [-2, 2]` at `yaw_deg = 90` (length axis along world `+y`).
- **World frame:** right-handed, metres, +z up. `handedness: left` applies one
  y-reflection (ADR 0012) to poses, extrinsics, points and boxes, so a
  left-handed source can say so instead of converting files.
- **Roles** are arbitrary strings; word matching maps a role containing
  `vehicle`, `uav`/`drone`, or `infrastructure`/`roadside`/`fixed` to the schema
  type, everything else is `other` (so `fixed camera` is infrastructure). The viewer uses this: no Chase/Drone view without a vehicle/UAV,
  Orbit works for fixed sensors.

## Validation

`coperceive adapters check --format generic <dir>` prints one line naming the
file and field, e.g. `agent cam_north: sensors[0].extrinsic must be 4×4`, and
exits non-zero. Missing marker, schema, agents, sensors, frame timing, camera
intrinsics, or referenced files are errors; an agent may have no boxes.

## Examples

- `examples/generic/driving/` — two moving vehicles, LiDAR + one camera, boxes.
- `examples/generic/inspection/` — two fixed cameras and one fixed LiDAR, no
  boxes and no vehicle.

Both are synthetic and under 100 KB. The inspection scene is used by the
viewer browser test (Orbit only, no Chase/Drone).
