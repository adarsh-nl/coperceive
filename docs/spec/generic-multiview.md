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
  `timestamp_file`, or `frame_rate_hz` + `frame_count` (time in µs). If more
  than one timing key is present, `timestamps_us` takes precedence over
  `timestamp_file`, which takes precedence over `frame_rate_hz`. When derived
  from `frame_rate_hz` and `frame_count`, frame `i` (`0 <= i < frame_count`) is
  stamped with `round(i * 1_000_000 / float(frame_rate_hz))` using Python's
  round-half-to-even (`round`), starting at `0` µs; the resulting timestamps
  must still be strictly increasing (`coperceive/adapters/generic.py`,
  `_timestamps`). `poses.csv` and `boxes.csv` must match these exact
  microsecond values.

  - Example (non-integer period): `frame_rate_hz: 30`, `frame_count: 4` yields
    `[0, 33333, 66667, 100000]` µs (cumulative division rounded per frame, not
    `i * 33333`).
  - Example (half-microsecond tie): `frame_rate_hz: 128`, `frame_count: 4`
    yields `[0, 7812, 15625, 23438]` µs (`7812.5` rounds to even `7812`;
    `23437.5` rounds to even `23438`).
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
  (the frame `body_from_sensor` maps into the agent body; schema 1 assumes
  `+X` forward, `+Y` left, `+Z` up — see
  [format.md](format.md#axis-conventions) and
  [format.md](format.md#pointsbin)). The adapter copies parsed `x, y, z`
  values directly into `PointCloud` without transforming them
  (`coperceive/adapters/generic.py`, `GenericAdapter.load`,
  `_sensor_extrinsic`), and consumers map each sweep into the world frame via
  `world_from_body ∘ body_from_sensor` (`coperceive/viewdata.py`,
  `world_points`; `coperceive/transform.py`, `_world_from_frame`). When
  `body_from_sensor` and `extrinsic` are omitted, `_sensor_extrinsic` defaults
  to identity, so the LiDAR sensor frame coincides with the agent body frame.

  - Example: an agent with `world_from_agent_body` at `(10, 0, 0)` and
    `rotation_rpy_deg: [0, 0, 0]`, and a LiDAR with
    `body_from_sensor: {translation_m: [0, 0, 1.8], rotation_rpy_deg: [0, 0, 0]}`,
    stores a return 5 m ahead of the sensor as `(5.0, 0.0, 0.0)` in the
    `.pcd`/`.bin` file; composing `world_from_body ∘ body_from_sensor` places
    it at `(5.0, 0.0, 1.8)` in the agent body frame and `(15.0, 0.0, 1.8)` in
    the world frame.
- **Missing frames:** the validator reports a warning per sensor, e.g.
  `sensor cam_north/camera: 3 of 120 frames missing (first: 000041)`, and load
  skips those frames rather than referencing absent files.
- **Boxes CSV** (optional, per agent):
  `timestamp_us,track_id,category,x,y,z,length,width,height,yaw_deg`, in the
  **world** frame (`BoxFrame.WORLD`). No boxes is valid; no track/category is
  required. Each `timestamp_us` must match a scene timestamp.

  - **Box position (`x, y, z`):** the **3D geometric centre** of the box in
    world metres — not the bottom-centre or a corner
    (`coperceive/adapters/generic.py`, `GenericAdapter._boxes`; corners extend
    symmetrically by `±length/2`, `±width/2`, `±height/2` around `center` in
    `coperceive/project.py`, `box_world_points`).
    - Example: a car resting on ground `z = 0.0` with `height = 1.6` at
      horizontal centre `(10.0, -1.75)` has `x = 10.0, y = -1.75, z = 0.8`
      (bottom face at `z = 0.0`, top face at `z = 1.6`, as in
      `examples/generic/driving/agents/ego/boxes.csv`).
  - **Box length axis and `yaw_deg`:** `size_lwh` is `(length, width, height)`
    with **`length` along box local `+x`**, `width` along `+y`, and `height`
    along `+z`. Orientation is a pure right-handed rotation `Rz(yaw_deg)`
    about world `+z` with `roll = 0` and `pitch = 0`
    (`coperceive/adapters/generic.py`, `GenericAdapter._boxes`;
    `coperceive/se3.py`, `SE3.from_rpy_deg`; `coperceive/project.py`,
    `box_world_points`). Thus `yaw_deg = 0` aligns the box's length axis
    (`+x`) with world **`+x`** (and width with world `+y`), and positive
    `yaw_deg` rotates **counter-clockwise** about world `+z` (matching the
    agent pose yaw convention). If the `yaw_deg` column is absent from the
    CSV, it defaults to `0.0`.
    - Example: a box centred at `(10.0, 0.0, 0.8)` with `length = 4.0`,
      `width = 2.0`, `height = 1.6` spans `x ∈ [8.0, 12.0]`, `y ∈ [-1.0, 1.0]`
      at `yaw_deg = 0`, and spans `x ∈ [9.0, 11.0]`, `y ∈ [-2.0, 2.0]` (length
      aligned with world `+y`) at `yaw_deg = 90`.
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
