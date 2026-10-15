# Scene format, schema 1

This is the contract for one cooperative scene. A writer and a reader that follow it can exchange a directory without using this library. The normative algorithms for interpolation and time queries are included because a pose or a `dt_us` is part of what the file means.

Schema version is the integer `1`. A file whose `schema_version` is anything else is rejected. This document is schema 1. The Phase 0 scaffold wrote a thinner directory (no `frame` column, no platform object, `agent_type` not restricted to the set below). That directory is not a released corpus and this reader does not accept it.

## Scene

One directory is one scene. It holds every agent. There is one named world frame and one clock.

| Field | Type | Meaning |
| --- | --- | --- |
| `schema_version` | int64, value `1` | This document. |
| `scene_id` | non-empty string | Caller-chosen name. Not interpreted. |
| `world_frame` | non-empty string | Name of the world frame, for example `ENU`. The name is not a CRS. |
| `clock.unit` | string | `us`. Schema 1 has no other unit. |
| `clock.scale_note` | non-empty string | How the source was mapped onto microseconds. Required. Never implied. |
| agents | list | N agents. `agent_id` unique in the scene. |
| sensors | list | Sensors mounted on those agents. |
| poses | table | `world ← body` samples. |
| annotations | table | Per-agent 3D boxes. |
| objects | table | Scene-level tracks, and only the shared ones. |
| images | table | Paths. Bytes are not stored. |
| points | blob + index | Lidar (or other) sweeps. |

Time is an int64 count of microseconds. It has to fit in `[-9223372036854775808, 9223372036854775807]`. Per agent, pose times are strictly increasing. There is no requirement that two agents share a timestamp.

## Agents

`agent_id` is a non-empty string. It is the dataset's name (`vehicle`, `drone`, a serial number). It is not the type.

`agent_type` is one of `vehicle`, `uav`, `infrastructure`, `other`. A Griffin drone is type `uav`. Its `agent_id` can still be `drone`.

`platform` is a JSON object of string to string. Keys are non-empty. Order is not significant. An empty object is allowed. This is free metadata (a vehicle name, a site id), not a second pose.

## Poses

Each sample is `world ← body`: it takes a point in the agent body frame into the world frame at that microsecond.

A rigid transform is a unit quaternion `(qw, qx, qy, qz)` and a translation `(tx, ty, tz)` in metres. Either quaternion sign is the same rotation. The action on a point is `p' = R p + t`. The quaternion product is the Hamilton product. Composition `A ∘ B` applies `B` first.

`SE3.from_rpy_deg(roll, pitch, yaw)` builds `R = Rz(yaw) @ Ry(pitch) @ Rx(roll)` with angles in degrees. That is the composition under which Griffin-25m vehicle and drone boxes that share a track id land on the same world point (at most 5.7e-14 m on the Town03-000 sample in `docs/design/griffin-verification.md`). Griffin conventions, including the toolkit's east-axis comment, are in `docs/datasets/griffin.md`. The axis that is east was not measured against a map. The world-frame name does not decide it.

## Sensors

A sensor belongs to one agent. `(agent_id, sensor_id)` is unique.

`modality` is `camera`, `lidar`, or `radar`.

`body ← sensor` is the rigid extrinsic. It takes a sensor-frame point into the agent body. It does not depend on time. A query that names the sensor still has to land inside that agent's pose span, so a sensor pose is not returned for a time the agent was not recorded.

Camera intrinsics are an optional pinhole, each field independent:

| Field | Meaning |
| --- | --- |
| `fx`, `fy` | Focal length in pixels. |
| `cx`, `cy` | Principal point in pixels. |
| `image_width_px`, `image_height_px` | Positive pixel size, or null. |

Null means the source did not record that number. Lidar and radar have no measurement model in schema 1. They still store the extrinsic. Distortion is not stored.

## Annotations

A row is one 3D box from one agent at one time.

| Column | Type | Null | Meaning |
| --- | --- | --- | --- |
| `agent_id` | string | no | Annotating agent. |
| `time_us` | int64 | no | Microseconds. |
| `track_id` | string | yes | Absent when the source has no id. Empty string is not a null. |
| `category` | string | no | The class label. |
| `cx`, `cy`, `cz` | float64 | no | Box centre, metres, in `frame`. |
| `length`, `width`, `height` | float64 | no | Box size, metres. |
| `qw`, `qx`, `qy`, `qz` | float64 | no | Pure rotation of the box in `frame`. Translation is not stored. |
| `visibility` | float64 | yes | Absent when the source has no visibility. No range is required. |
| `frame` | string | no | `body` or `world`. |

`body` means the annotating agent's body at `time_us`. `world` means the scene world frame. The centre in a `body` box is mapped to world by applying `world ← body` at that time. That lookup follows the interpolation rules and does not extrapolate.

## Scene-level objects

`objects` has one row per track that is actually shared:

- the track id is present on the annotation
- at least two agents use that id
- those agents, across every box they attach to the id, name exactly one category

Every other id stays on the per-agent boxes. Geometry is not copied onto the object row. Row order is not significant. A reader rejects a table that is not exactly this set.

On Griffin-25m Town03-000, ids that pass this rule also agree in world coordinates to at most 5.7e-14 m, with no category mismatch in that sample. That measurement is why a shared id is treated as one object. The file does not store a residual. A caller who wants the residual computes it from the boxes and the poses.

## Transforms

`T(dst ← src, t)` takes a point in `src` at time `t` into `dst`. Frames are:

| Frame | What it is |
| --- | --- |
| world | The scene world frame. It has no samples. |
| body of agent `a` | That agent's `world ← body` stream. |
| sensor `s` of agent `a` | `world ← body` composed with `body ← sensor`. |

`T(dst ← src) = inv(world ← dst) ∘ (world ← src)`.

`max_gap_us` is a required argument. There is no default. The world frame alone does not consult the clock. Any body or sensor frame uses that agent's pose lookup:

1. No samples: error.
2. `t` before the first sample or after the last: extrapolation error. The last pose is not held.
3. `t` equal to a sample: that sample. The gap is not consulted.
4. Otherwise the samples `prev < t < next`. `gap = next.time_us - prev.time_us`. If `gap > max_gap_us`, gap error. `gap == max_gap_us` is allowed.
5. `u = (t - prev.time_us) / gap`. Translation is `(1-u) * prev + u * next`. Rotation is the slerp below.

Slerp of rotations `A` and `B` at fraction `u`:

- `u` outside `[0, 1]` is an extrapolation error. `u = 0` returns `A`. `u = 1` returns `B`.
- Let `d` be the quaternion dot product. If `d < 0`, negate `B` so the short arc is used.
- If `d > 0.9995`, use normalized lerp: `(1-u) A + u B`, then divide by its norm.
- Otherwise `θ = acos(clamp(d, -1, 1))`, weights `sin((1-u)θ) / sin θ` and `sin(uθ) / sin θ`.

Normative checks, all in a right-handed frame:

- Yaw 90° sends `(1, 0, 0)` to `(0, 1, 0)`.
- Slerp from identity to yaw 90° at `u = 0.5` sends `(1, 0, 0)` to `(cos 45°, sin 45°, 0)`.
- Vehicle samples at 0 µs `(0, 0, 0)` and 100_000 µs `(10, 0, 0)`, query 50_000, `max_gap_us` 100_000: translation `(5, 0, 0)`. The same query with `max_gap_us` 99_999 is a gap error. Query 100_001 is an extrapolation error.
- At 100_000 µs the vehicle body is at `(10, 0, 0)` and a second body is at `(10, 5, 25)`. `T(second ← vehicle)` applied to `(0, 0, 0)` is `(0, -5, -25)`.

## Time queries

A discrete query picks one pose sample per agent. It does not interpolate.

`dt_us = sample_time_us - query_time_us`. Negative means the sample is earlier than the query. `tolerance_us` is required, is `>= 0`, and is applied per agent. A hit with `abs(dt_us) > tolerance_us` is a miss for that agent.

| Mode | Candidate | Then |
| --- | --- | --- |
| `exact` | Samples with `abs(dt_us) <= tolerance_us`. Closest `abs(dt_us)`. A tie keeps the earlier sample. No such sample is a miss. | The tolerance is already applied by the candidate rule. |
| `nearest` | Smallest `abs(dt_us)`. A tie keeps the earlier sample. | Miss when `abs(dt_us) > tolerance_us`. |
| `backward` | Last sample with `time_us <= query`. No such sample is a miss. | Miss when `abs(dt_us) > tolerance_us`. |
| `forward` | First sample with `time_us >= query`. No such sample is a miss. | Miss when `abs(dt_us) > tolerance_us`. |

A miss is reported for that agent. It is not an exception, and it does not drop other agents. An ordered-pair alignment includes a pair only when both agents matched, and it reports both `dt_us` values.

Normative tie: samples `0` and `100_000`, query `50_000`, `nearest`, `tolerance_us` `50_000` selects `0` and `dt_us` `-50_000`. `tolerance_us` `49_999` misses.

An empty overlap is a query at which every agent misses. Agents whose spans do not contain the query, or whose nearest sample is farther than the tolerance, miss independently.

## Cooperative sample

`scene.at(t, tolerance, mode=nearest)` returns one row per agent, in `scene.agents` order. `tolerance` is `tolerance_us` in microseconds. It is required and uses the same rules as a discrete query. `mode` defaults to nearest.

Each row carries `matched`, `time_us`, `dt_us`, the discrete pose at the chosen sample (not an interpolated pose), that agent's sensors, and the annotations, images, and point clouds stamped at the chosen sample time.

A miss sets `matched` false and leaves `time_us`, `dt_us`, and the pose null. Sensors are still listed. Annotations, images, and clouds are empty. A miss is not an exception and does not drop other agents. An invalid `tolerance` still raises.

On the synthetic fixture, query `100_000` with `tolerance` `0` matches vehicle and uav at `dt_us` `0` and misses mast (`dt` would be `-100_000`). Query `50_000` with `tolerance` `0` misses vehicle: nearest is a tie between `0` and `100_000`, the earlier sample is `50_000` us away, and that exceeds the tolerance. The same query with `tolerance` `50_000` matches vehicle at time `0`, `dt_us` `-50_000`, translation `(0, 0, 0)`. That pose is the recorded sample, not the interpolated `(5, 0, 0)`.

## Camera projection

`project_box` places the box with the annotating agent's pose at `annotation.time_us` and the camera with its pose at `camera_time_us` (the box time when omitted). Both use `T(dst ← src, t)` and `max_gap_us`. They interpolate inside an allowed gap and do not extrapolate.

The camera frame is the sensor frame. Depth is +Z. The pixel is `u = fx * x / z + cx`, `v = fy * y / z + cy`. Pixels are not rounded to integers.

`z <= 0`, including `z == 0`, is behind the camera. `u` and `v` are then null. A point with `z > 0` and a missing `image_width_px` or `image_height_px` is `no_image_size`. Otherwise the point is `in_image` when `0 <= u < width` and `0 <= v < height`, and `out_of_image` otherwise.

The projected points are the centre, then the eight corners. Corner signs `(sx, sy, sz)` run through `{-1, 1}` in the order `(-1,-1,-1)`, `(-1,-1,1)`, `(-1,1,-1)`, `(-1,1,1)`, `(1,-1,-1)`, `(1,-1,1)`, `(1,1,-1)`, `(1,1,1)`. The offset is `(sx * length / 2, sy * width / 2, sz * height / 2)`, rotated by the box rotation, added to the centre, then taken from body to world when the box frame is body.

Normative camera, identity extrinsic, `fx = 10`, `fy = 20`, `cx = 5`, `cy = 6`, image `10 × 12`:

- `(0, 0, 10)` → `u = 5`, `v = 6`, in image.
- `(1, 2, 4)` → `u = 7.5`, `v = 16`, out of image.
- `(0, 0, -1)` and `(0, 0, 0)` → behind, pixels null.
- A world box at `(0, 0, 10)` with size `(2, 4, 6)` and identity rotation has a corner at `(-1, -2, 7)` with `u = 5 - 10/7` and `v = 6 - 40/7`.
- A world box at `(0, 0, -5)` with that same size is entirely behind the camera.
- A world box at `(0, 0, 1)` with size `(0, 0, 4)` has four corners at `z = -1` (behind) and four at `z = 3` (in image).

A missing pinhole (`fx`, `fy`, `cx`, or `cy` null) raises. `any_in_image` is true when the centre or any corner is in the image.

## Cross-agent boxes

`agent_annotations` is one agent's boxes, optionally at one `time_us`. `exclusive_annotations` keeps a box whose track id is not used by any other agent in that same time filter. A null track id is exclusive to that box. `co_annotations` is the track ids used by both named agents. `ground_truth` is true only when that id is a row of `scene.objects`. The same id on both agents with disagreeing categories is returned with `ground_truth` false. `shared_track_annotations` returns every box for a scene-object id and raises `KeyError` for any other id.

`associate` pairs boxes at one timestamp. Scene-object ids are paired first, one to one, kind `shared_track`, `ground_truth` true. Remaining boxes are paired greedily by world-centre distance, closest first, one to one, and only when the distance is at most `max_center_m`. Those pairs are kind `computed` and `ground_truth` false. The function does not write `scene.objects`. A computed pair is not ground truth, including when the two boxes happen to share an id that `shared_objects` rejected.

On the synthetic fixture at `100_000`, track `42` is a scene object (both agents say `car`, world centres coincide). Track `99` is only on the vehicle. The untracked sign is only on the uav. Track `5` is `car` on the vehicle and `truck` on the uav, so it is not a scene object. With `max_center_m` `0`, the only pair is track `42`. With `max_center_m` `100`, the pedestrian is then paired with the sign and track `5` is paired with itself, both computed.

## Torch dataset

`CooperativeDataset` lives in `coperceive.dataset` and needs the `torch` extra. `import coperceive` does not import it. This is library behaviour, not a schema 1 change.

`root` is a directory of scene directories. A child whose name starts with `.`, or that has no `manifest.json`, is skipped. The index is one reference agent's pose times. The reference agent is the first name in `agents`, or the first manifest agent when `agents` is omitted. `stride` keeps every nth of those times, starting at the first; `stride` is an int `>= 1`. `seed` shuffles the finished index with `random.Random(seed)` in the parent process. Omitting `seed` keeps scene-directory name order, then time.

`tolerance_us` is required and is passed to `scene.at`. `mode` defaults to nearest. `agents` limits and orders the rows, and a miss is still returned. `sensors` limits sensors, images, and clouds. `include_points` false drops clouds after the read.

The parent builds the index from `manifest.json` and the `agent_id` and `time_us` columns of `poses.parquet`. It does not read `points.bin` while indexing. `__getitem__` calls `read_scene` and caches the last scene on the instance. `__getstate__` drops that cache, so a worker is not given an open file or a loaded blob. Each worker opens lazily.

`identity_collate` returns the list of samples. The default torch collate cannot stack clouds of different lengths, or a miss whose pose is null.

## Directory

```text
scene/
  manifest.json
  poses.parquet
  annotations.parquet
  objects.parquet
  images.parquet
  point_index.parquet
  points.bin
```

UTF-8 JSON, one object, trailing newline allowed. Unknown manifest keys are ignored. Unknown Parquet columns are ignored when the reader asks for the column list below. A missing column from that list is an error. Parquet compression is not part of the contract.

### `manifest.json`

```json
{
  "schema_version": 1,
  "scene_id": "coop-fixture",
  "world_frame": "ENU",
  "clock": {"unit": "us", "scale_note": "how time_us was obtained"},
  "agents": [
    {"agent_id": "vehicle", "agent_type": "vehicle", "platform": {"name": "ground-car"}}
  ],
  "sensors": [
    {
      "sensor_id": "lidar_top",
      "agent_id": "vehicle",
      "modality": "lidar",
      "qw": 1.0, "qx": 0.0, "qy": 0.0, "qz": 0.0,
      "tx": 0.0, "ty": 0.0, "tz": 1.5,
      "image_width_px": null,
      "image_height_px": null,
      "fx": null, "fy": null, "cx": null, "cy": null
    }
  ]
}
```

Sensor keys in that list are required. Null is JSON `null`.

### Parquet

`poses.parquet`

| Column | Arrow |
| --- | --- |
| `agent_id` | string, required |
| `time_us` | int64, required |
| `qw`, `qx`, `qy`, `qz`, `tx`, `ty`, `tz` | float64, required |

`annotations.parquet`: columns in the annotation table above. `track_id` and `visibility` are nullable. `frame` is required.

`objects.parquet`: `track_id` string required, `category` string required.

`images.parquet`: `agent_id`, `sensor_id`, `path` string required; `time_us` int64 required. `path` is a path string, not file bytes. The sensor it names has to exist.

`point_index.parquet`: `agent_id`, `sensor_id` string required; `time_us`, `offset`, `nbytes`, `count` int64 required. `offset` and `nbytes` and `count` are `>= 0`. `nbytes` equals `count * 16`. The sensor it names has to exist.

### `points.bin`

Little-endian float32, no header, no padding between sweeps. Each point is `x, y, z, intensity` in the sensor frame, 16 bytes. Sweep `i` occupies `[offset, offset + nbytes)` as given by its index row. A reader seeks and reads. It does not scan other sweeps.

## What schema 1 does not store

Radar or lidar measurement models, distortion, map geometry, embeddings, and image bytes. A second dataset is out of scope. The viewer reads this layout. It does not add fields to it.
