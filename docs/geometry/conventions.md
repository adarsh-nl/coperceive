# Geometry conventions

This is a stub. The full table is the open pull request [166](https://github.com/adarsh-nl/coperceive/pull/166), file `docs/geometry/conventions.md` on `agent/claude-geometry-conformance`. It has not merged here. The sentences below repeat that draft's **finding** and **unverified** labels for the rows the dataset pages cite. They are not a new survey.

## OPV2V

### OPV-2

Which CARLA version wrote OPV2V: **unverified**. **draft finding from PR #166, not yet reviewed:** no source names the CARLA version, so the post-0.9.10 sensor frame is an assumption.

### OPV-3

**draft finding from PR #166, not yet reviewed:** the adapter drops the intensity that the loader reads. Positions are unaffected.

### OPV-6

**draft finding from PR #166, not yet reviewed:** non-zero pitch and roll are stored with the wrong sign. The adapter builds `Ry(pitch)·Rx(roll)` where the source builds `Ry(−pitch)·Rx(−roll)`.

### OPV-7

**draft finding from PR #166, not yet reviewed:** the adapter reads `extrinsic` as camera-to-LiDAR. The dumper and the documented numbers make it LiDAR-to-camera, so every camera pose is inverted.

### OPV-11

Whether camera blocks change between frames: **unverified**. **draft finding from PR #166, not yet reviewed:** the adapter uses the first frame for the whole scene.

### OPV-13

**draft finding from PR #166, not yet reviewed:** as OPV-6, every non-zero box pitch and roll is stored with the wrong sign.

### OPV-17

**draft finding from PR #166, not yet reviewed:** the adapter's `test_culver_city` differs from the documented `test_culvercity`.

## V2XSet

The draft says every OPV2V row applies to V2XSet, including the findings, except the split-name part of OPV-17.

### V2X-1

**draft finding from PR #166, not yet reviewed:** only `-1` is typed infrastructure, while the source allows any negative id.

### V2X-2

Whether a roadside unit's YAML holds `camera*` blocks: **unverified**. **draft finding from PR #166, not yet reviewed:** the adapter refuses an agent whose first YAML has no `camera*` key.

### V2X-3

**draft finding from PR #166, not yet reviewed.** The own-LiDAR-hit rule is **unverified** for V2XSet.

### V2X-4

The simulation tick: **unverified**. **draft finding from PR #166, not yet reviewed:** no V2XSet source gives the 0.05 s tick the stem scale assumes.

## V2V4Real

### V4R-1

Axes and handedness: **unverified**. **draft finding from PR #166, not yet reviewed:** the adapter mirrors every pose, box, and point on the assumption that the frame is CARLA's.

### V4R-2

**draft finding from PR #166, not yet reviewed.** Direction of +y and handedness of the LiDAR frame: **unverified**.

### V4R-4

**draft finding from PR #166, not yet reviewed:** the source shows a 4×4 pose where the adapter reads six Euler values, so that file does not load. A six-value list would store pitch and roll with the wrong sign, as in OPV-6.

### V4R-6

**draft finding from PR #166, not yet reviewed:** a non-zero pitch or roll is stored with the wrong sign.

### V4R-7

**draft finding from PR #166, not yet reviewed:** the source puts boxes in the listing vehicle's LiDAR frame and the adapter stores them as world boxes.

### V4R-9

Camera data is not released. Extrinsic direction, camera axes, and intrinsics: **unverified**. **draft finding from PR #166, not yet reviewed:** the camera rules are OPV2V's, with no V2V4Real source behind them.

### V4R-11

Stem start and step: **unverified**. **draft finding from PR #166, not yet reviewed:** one stem per 0.1 s is a repository observation, not a source statement.

## DAIR-V2X-C

### DAIR-1

The split files and the `DAIR-V2X-C_Complemented_Anno` tree: **unverified**. **draft finding from PR #166, not yet reviewed:** the adapter pairs the two sides by equal stem, which the source says does not hold, and it requires that complemented tree.

### DAIR-2

Directions of x and y, handedness, and units: **unverified**. **draft finding from PR #166, not yet reviewed:** the adapter assumes right-handed metres.

### DAIR-5

How the calibration lists are nested on disk: **unverified**. **draft finding from PR #166, not yet reviewed:** the adapter refuses the documented nested `lidar_to_novatel` form.

### DAIR-8

Encoding of the released files: **unverified**. **draft finding from PR #166, not yet reviewed:** the adapter refuses `binary_compressed` and drops intensity.

### DAIR-10

Order of the stored `world_8_points`: **unverified**. **draft finding from PR #166, not yet reviewed:** under the only documented order the adapter swaps length and width and turns the heading by 90°.

### DAIR-13

In-plane camera axes and the model of `cam_D`: **unverified**. **draft finding from PR #166, not yet reviewed:** no camera is ingested.

### DAIR-14

Timestamp unit: **unverified**. **draft finding from PR #166, not yet reviewed:** the microsecond unit is an inference from loader code, not a stated fact.

## Griffin

### GRF-3

**draft finding from PR #166, not yet reviewed:** the adapter requires a pose key `timestamp` that no source documents.

### GRF-6

**draft finding from PR #166, not yet reviewed:** image size is derived as `2cx × 2cy` instead of the documented 1920 × 1080. They agree only if the principal point is the image centre, which no source states.

### GRF-7

**draft finding from PR #166, not yet reviewed.** Whether stored points are in the `lidar_top` frame or the ego frame: **unverified**.

### GRF-8

Record layout and file names: **unverified**. **draft finding from PR #166, not yet reviewed:** the source says `.bin` and the adapter reads `.ply` and requires a property named `I`.

### GRF-9

**draft finding from PR #166, not yet reviewed.** The drone has no LiDAR. The adapter reads vehicle clouds only.

### GRF-13

Case of `type` in the files: **unverified**. **draft finding from PR #166, not yet reviewed:** the adapter keeps the token verbatim where the toolkit lowercases it.

### GRF-14

**draft finding from PR #166, not yet reviewed:** the adapter requires `info.map_name`, `info.cruise_altitude`, and a drone-side `scene_infos.json` identical to the vehicle file. No source the draft read documents those.

## Generic

### GEN-1

**draft finding from PR #166, not yet reviewed:** the adapter names the world ENU, which the spec does not claim.

### GEN-6

The frame of the points: **unverified**. **draft finding from PR #166, not yet reviewed:** the demo generator writes world coordinates and the adapter moves each cloud by the agent pose again.

### GEN-7

**draft finding from PR #166, not yet reviewed:** the adapter drops the intensity the spec describes for `.bin` records.

### GEN-8

Whether `x, y, z` is the centre or the bottom: **unverified**. **draft finding from PR #166, not yet reviewed:** the adapter takes the centre.

### GEN-9

**draft finding from PR #166, not yet reviewed.** Which box axis is the length, and the zero and sign of `yaw_deg`: **unverified**.

### GEN-10

**draft finding from PR #166, not yet reviewed:** the spec says a left-handed source reflects LiDAR points, and the adapter does not.
