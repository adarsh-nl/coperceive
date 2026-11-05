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
  `timestamp_file`, or `frame_rate_hz` + `frame_count` (time in µs).
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
  [format.md](format.md) §camera). A camera looking along the agent body +x
  therefore has `body_from_sensor` rotation `[0, 90, 0]` (pitch 90°), not yaw
  90°.
- **Cameras:** `intrinsic: [fx, fy, cx, cy]`; files PNG/JPEG. **LiDAR:** PCD
  ASCII/binary with `x y z`, or a binary `.bin` of float32 `x y z i` records.
- **Missing frames:** the validator reports a warning per sensor, e.g.
  `sensor cam_north/camera: 3 of 120 frames missing (first: 000041)`, and load
  skips those frames rather than referencing absent files.
- **Boxes CSV** (optional, per agent):
  `timestamp_us,track_id,category,x,y,z,length,width,height,yaw_deg`, in the
  **world** frame. No boxes is valid; no track/category is required.
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
