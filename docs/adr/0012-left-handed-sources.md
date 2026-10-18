# ADR 0012 — Left-handed sources, per-agent box mappings and typed discovery

Status: accepted by Adarsh on 2026-10-18 (plan review). Implemented on `feat/adapter-opencood` for the OPV2V and V2XSet manifests. Extends the manifest of [ADR 0009](0009-dataset-adapters.md); `spec_version` stays `1` because every new key is optional and a manifest without them reads exactly as before (the Griffin regression test is the proof).

## Context

OPV2V and V2XSet share the OpenCOOD layout: one directory per scene, one per agent, and per frame one YAML, one PCD and four PNGs. The YAML carries the lidar pose, the four cameras and the boxes of the vehicles that agent sees. Three facts of that format do not fit the ADR 0009 manifest:

1. The frame is CARLA's, which the CARLA documentation describes as "the Unreal Engine coordinates system. This is a Z-up left-handed system", with pitch, yaw and roll about Y, Z and X in degrees. Schema 1 is right-handed (`FLU` body, `RDF` camera). `AxisRemap` refuses a left-handed letter triple on purpose.
2. A box is given as the actor's `location`, a `center` offset from that point to the box centre, and `extent`, "half length, width and height of the vehicle in meter" (OpenCOOD data-annotation page). The boxes of a frame are a mapping keyed by the actor id, not a list.
3. In V2XSet the roadside unit is the agent directory `-1` beside the vehicles; discovery assigns one type per glob.

A fourth point is not a format gap but a reading of the brief. Item 3 of Adarsh's brief asked for one shared box set per frame "not duplicated per agent". The data are not duplicates: in OPV2V `train/2021_08_16_22_26_54` at stem `000069` the three agents list 33, 32 and 29 vehicles, union 42, intersection 18, and a shared id has identical geometry in every file. The OpenCOOD page says a list holds "surrounding vehicles that have at least one LiDAR point hit from the agent", so each list is that agent's visibility-filtered truth. Adarsh accepted this reading on 2026-10-18.

## Decision

### 1. One explicit reflection for a left-handed source

```yaml
conventions:
  source_handedness: left     # right (default) | left
  reflect_axis: y             # only y in spec_version 1
  body_axes: FLU              # the axes AFTER the reflection
  camera_axes: RDF
```

The builder applies the fixed reflection `M = diag(1, -1, 1)` once, as the first step, to everything it reads from a left-handed source:

- points and translations: `p' = M p`;
- rotations: `R' = M R M` (a similarity transform, so the result is a proper rotation; in Euler terms yaw and roll change sign and pitch keeps it);
- poses, sensor extrinsics, box centres and box rotations, lidar points.

`body_axes` and `camera_axes` describe the axes after the reflection and are applied afterwards by the existing remaps. A manifest may not negate a single field instead: the reflection is one declared operation, so the manifest says what happened and a reader can undo it. Rejected: a left-handed `AxisRemap` letter triple (silently a reflection, and it would reach only the body, not the world); per-field sign flips in the field mappings (not a transform, and easy to apply to half the quantities).

Evidence: with the source conventions kept consistent, lidar points fall inside 33 of 33 boxes on a real OPV2V frame (throwaway check in the plan, derived from the CARLA page only). The adapter's known-answer test asserts the same on a synthetic release after conversion.

### 2. Boxes from a location, a rotated offset and half extents; records as a mapping

```yaml
boxes:
  reader: yaml_record
  path: "{agent_root}/{frame:06d}.yaml"
  records: {path: vehicles, mapping_key: track_id}
  centre: {location: [location.0, location.1, location.2],
           offset: [center.0, center.1, center.2], offset_frame: box}
  size: {half_extents: [extent.0, extent.1, extent.2]}
  rotation: {euler: {fields: [angle.0, angle.2, angle.1], order: zyx, frame: intrinsic}}
  fill: {category: vehicle, reason: "the format has no class field; the OPV2V paper annotates vehicles only"}
```

- `records` may be a string (a list field, as before) or `{path, mapping_key}`: the field is a mapping, each value is one box, and the key is written into the column named by `mapping_key`.
- `centre` is an alternative to the `cx, cy, cz` field columns: the box centre is `location + R · offset`, with `R` the box rotation, in the source frame before the reflection. `offset_frame: box` is the only value in `spec_version` 1 and must be written out.
- `size` is an alternative to the `length, width, height` columns: `size_lwh = 2 · half_extents`.
- `fill.category` may be a string. Every other fill stays numeric.
- A manifest that gives both forms of centre or size is refused with the key path.

### 3. Per-agent box lists, shared ids named once

Each agent's box list is stored once, under that agent, in `frame: world`. Nothing is copied from one agent to another. Schema 1's `objects.parquet` rule names an id once when two or more agents carry it with one category; geometry stays on the per-agent rows. Per-agent evaluation keeps meaning "this agent's visibility-filtered truth", unchanged from Griffin. A union set per frame attached to one agent was rejected: it would change every agent's recall and need an evaluator change.

### 4. Typed discovery

```yaml
agents:
  discover: {glob: "{split}/{scene}/{agent}/", type: vehicle, types: {"-1": infrastructure}}
```

`types` maps a discovered agent id to a schema 1 agent type; ids not listed get `type`. Keys are strings (quote `-1`).

### 5. Sensors from one record, a fixed lidar, and a sample for the calibration file

```yaml
sensors:
  reader: yaml_record
  path: "{agent_root}/{frame:06d}.yaml"
  sample: first                       # fill {frame} with the agent's first sample
  records: {prefix: camera}           # top-level keys camera0..camera3 are the sensors
  extrinsic: {matrix: {field: extrinsic, shape: 4x4}, direction: child_to_parent}
  intrinsic: {matrix: intrinsic}
  image_size: {derive: principal_point_x2, reason: "800x600 PNGs; cx, cy are 400, 300 in every file checked"}
  modality: [{has_intrinsic: true, modality: camera}, {default: camera}]
  fixed: [{id: lidar, modality: lidar}]   # identity extrinsic: the pose stream is the lidar pose
```

`sample: first` lets a calibration record live inside a per-frame file; `records.prefix` names which keys of that record are sensors; `fixed` adds identity sensors that no file describes. The pose stream of these datasets is the lidar's pose, so the body is the lidar frame and the camera extrinsics ("from camera to LiDAR") are `child_to_parent` to the body.

### 6. Licence block and platform tables

A manifest may carry `licence: {status: unknown | known, spdx?, notice, links: [..]}`; `adapters check` prints it. Platform values that are not in the data (the CARLA town, decision 7 of the plan) come from a sidecar table given on the command line, `--platform-table FILE`, a YAML mapping `scene_id -> {key: value}`; keys starting with `_` are notes and are ignored. `coperceive maps identify` writes such a table (ADR 0008 addendum).

**Town rule (revised 2026-10-18).** `maps identify` scores a scene's vehicle poses at the identity transform against every ingested town and accepts the best town when its median lane-centre distance is at most 0.5 m (`--accept-m`), the runner-up's is at least 10 times the best (`--ratio`), and the runner-up is at least 0.5 m worse in absolute terms (`--gap-m`). The first version demanded a fixed 2 m margin over the runner-up; staging job 629196 then left V2XSet `train/2021_08_20_20_39_00` map-less at Town06 0.046 m against Town03 1.060 m over 376 poses, and Adarsh replaced the margin with the relative rule, under which that scene is Town06 and the OPV2V scene (Town06 0.020 m against Town05 50.8 m) is unchanged. A scene that fails the rule stays map-less and the table records the reason.

## Consequences

- Right-handed manifests are unchanged: the Griffin regression stays byte-identical.
- The reflection is visible in the manifest and in the ADR; a reader who sees `source_handedness: left` knows that source `+y` is schema `-y`.
- Boxes with a rotated offset depend on the box rotation being decoded before the centre; the builder does that.
- `types` is the only way to type a discovered agent other than its glob; a dataset with mixed types in one directory level needs it.

## Open

- Only `reflect_axis: y` is accepted; another axis would need the same treatment and a dataset that needs it.
- `offset_frame: actor` (an offset in the actor's frame when the box rotation differs from the actor's) is not needed by OpenCOOD, where the box rotation is the actor's.
