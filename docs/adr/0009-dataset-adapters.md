# ADR 0009 — Dataset adapters

Status: accepted on 2026-10-17. The eight decisions at the end were taken by Adarsh; the outcomes are recorded there. Schema 1 (`docs/spec/format.md`) is unchanged and stays the only output format.

## Context

Griffin is the only dataset today. Its reader is hand-written Python: `coperceive/griffin/raw.py` parses the files, and `coperceive/griffin/convert.py` runs the scenes in parallel with `.partial/` directories, `COMPLETE.json` and resume. A second dataset written the same way would copy the driver and re-implement path templates, pose decoding, calibration decoding, box decoding and lidar parsing.

The goal is that a new dataset is mostly a declarative description of where things are and what the numbers mean. Code is needed only where the source format really needs it.

The adapter layer is clean-room. It was designed from the Griffin reader in this tree and from the public descriptions of the file layouts named below. kesai-labs/py123d and the adarsh-nl fork were not opened, installed or imported. The names, the manifest keys and the module layout here are ours.

The cooperative datasets Adarsh has on the cluster (OPV2V, V2XSet, DAIR-V2X-C; see the storage table in the HPC notes) are the targets that the generality claims below have to survive. None of them were opened for this ADR. Their layouts are described from memory of their public documentation and are not verified.

HANDOVER, CONTRIBUTING and the format spec all say "no second dataset". This ADR does not change that. The generic readers are checked against synthetic fixtures until Adarsh approves a second real dataset.

## Options

1. **Keep hand-written readers.** One package per dataset, each with its own driver. This is the status quo, and it scales badly.
2. **Declarative manifest only.** Every dataset is a data file. Anything the vocabulary cannot express cannot be ingested.
3. **Declarative manifest plus a plugin hook.** A manifest covers the common shapes. A named reader registered through a Python entry point covers the rest. Manifest-built adapters and code-built adapters satisfy the same `Adapter` protocol.
4. **Python-only adapter classes with shared helpers.** This is flexible, but every dataset is code, and "check this manifest" cannot be done without running it.

## Criteria

- Schema 1 stays the only output. The adapter layer adds no columns and no manifest keys to a scene.
- Griffin rewritten as an adapter produces identical scenes to today's `coperceive convert griffin` (see "Regression" below).
- A manifest can be validated (`adapters check`) without writing anything.
- The manifest is data: no `eval`, no expression language, no arbitrary import paths.
- Paths stay under the raw root after globbing and symlink resolution.
- Units, angle conventions, rotation encodings, matrix direction and the time mapping are always stated, never defaulted.
- One driver handles parallelism, `.partial/`, `COMPLETE.json` and resume for every adapter.
- A dataset with a varying set of agents per scene (OPV2V-style CAV folders) can be described.

## Decision

Option 3.

### Package layout

```text
coperceive/adapters/
  __init__.py        Adapter protocol, SceneRef, registry lookup
  spec.py            manifest dataclasses, loader, validation errors with a key path
  build.py           ManifestAdapter: spec + readers -> Scene
  ingest.py          the generic driver (moved out of griffin/convert.py)
  paths.py           template expansion/inversion, glob, confinement
  rotation.py        Euler / quaternion / matrix decoding into SE3
  readers/
    records.py       json_record, json_table, yaml_record, csv_rows, text_rows
    lidar.py         ply (ascii, binary_little_endian), pcd (ascii, binary), bin_f32
    calib.py         matrix and pinhole decoding
    images.py        image folders -> ImageRef
  builtin/
    griffin.yaml
```

The built-in manifests ship as package data. `coperceive.griffin.download` and `coperceive.griffin.licence` stay where they are: downloading and licence acknowledgement are not ingest.

### The adapter protocol

```python
@dataclass(frozen=True)
class SceneRef:
    scene_id: str
    frames: tuple[int, ...]  # or source sample keys, see "Samples"
    params: tuple[tuple[str, str], ...]


class Adapter(Protocol):
    name: str

    def scenes(self, raw_root: Path, params: Mapping[str, str]) -> Sequence[SceneRef]: ...
    def load(self, raw_root: Path, ref: SceneRef) -> Scene: ...
    def check(self, raw_root: Path, params: Mapping[str, str]) -> CheckReport: ...
```

`ManifestAdapter(spec)` implements it. A plugin can implement it directly in Python. The driver only sees `Adapter`.

### Manifest

YAML, loaded with `yaml.safe_load`, then validated into frozen dataclasses. An unknown key is an error with its key path (`streams.boxes.variants.12[3]`), so a typo does not silently fall back to a default. `spec_version: 1` is the manifest format. It is separate from the scene `schema_version`.

Top-level keys:

| Key | Meaning |
| --- | --- |
| `adapter` | Name. Lower-case, `[a-z0-9_-]+`. |
| `spec_version` | `1`. |
| `params` | Run parameters passed on the command line (`--param subset=25m`), with an allowed set. They are not read from the data. |
| `scenes` | How scenes and their sample list are found. |
| `world_frame` | String copied to the scene. |
| `clock` | Source time field to microseconds, plus the required `scale_note`. |
| `agents` | A fixed list, or a discovery rule. |
| `conventions` | Source axes, units and angle conventions, and the remap into ours. |
| `streams` | `poses`, `sensors`, `boxes`, `images`, `lidar`. Each names a reader and its field mapping. |
| `checks` | Cross-file consistency rules. |
| `report` | Counters the driver writes into `COMPLETE.json`. |

#### Paths and templates

A path is a template relative to the raw root, the agent root or the scene root, for example `pose/{frame:06d}.json`. Placeholders are a fixed set: `{scene}`, `{agent}`, `{agent_root}`, `{sensor}`, `{frame}`, `{sample}` and any declared `param`. The only format specs are `d` with an optional zero-padded width, and `s`. There is no regex. A template can be expanded (frame to path) or inverted against a glob (path to frame), and an inverted match that does not round-trip is an error.

Every resolved path is `resolve()`d and must satisfy `is_relative_to(raw_root.resolve())`. This is the same `confine` rule the web app uses (ADR 0004). A `..` segment in a template is refused at load time.

#### Scenes and samples

Two sources:

- `index`: one file lists scenes, for example a JSON list of records, with field paths for `scene_id`, the sample list and per-scene platform values. `must_equal: [other/file.json]` requires byte-for-byte equal parsed content. Griffin needs this.
- `glob`: one directory per scene (`{scene}/`). Samples are discovered by inverting a stream's template, with a declared `sample_source` stream.

Field paths are dotted keys with integer list indices (`info.frames`, `calib.0.matrix`). They are not JSONPath. There are no filters or wildcards.

A sample key is either an integer `frame` or an opaque string `sample` (for token-keyed tables). The clock maps it to `time_us`.

#### Clock

```yaml
clock:
  from: frame            # frame | field
  field: null            # dotted path when from: field
  unit: frame            # frame | s | ms | us | ns
  scale_us: 100000       # required when unit: frame
  offset_us: 0
  rounding: exact        # exact | nearest; exact raises on a fractional microsecond
  scale_note: "..."      # required, copied to Clock.scale_note
```

`exact` is the default because a silent rounding of a source clock is a measurement change. Float seconds need `rounding: nearest` written out.

#### Agents

A fixed list:

```yaml
agents:
  - {id: vehicle, type: vehicle, root: vehicle-side}
  - {id: drone,   type: uav,     root: drone-side}
```

or discovery:

```yaml
agents:
  discover: {glob: "{scene}/{agent}/", type: vehicle, exclude: [additional]}
```

`type` is one of the four schema 1 agent types. Output order is the list order, or the sorted discovered ids.

#### Conventions

Schema 1 does not name a body axis convention. The code assumes one: chase sits behind body `+X`, and projection treats camera sensor `+Z` as depth with `−Y` as up in the viewer. The adapter output convention is written down here:

- body: `+X` forward, `+Y` left, `+Z` up, metres;
- camera sensor: `+X` right, `+Y` down, `+Z` forward;
- lidar sensor: same axes as the body.

A manifest states the source convention, and the builder applies a fixed 3×3 axis remap:

```yaml
conventions:
  length_unit: m              # m | cm | mm
  angle_unit: deg             # deg | rad
  body_axes: FLU              # FLU | RFU | FRD | ... (three letters from F/B/L/R/U/D)
  camera_axes: RDF            # OpenCV-style; RUB for OpenGL-style sources
```

The remap is applied to poses, extrinsics, boxes and lidar points before the `Scene` is built. Griffin is `FLU` (the toolkit's `PoseData` docstring, `docs/datasets/griffin.md`) and `RDF` (the Town03-000 front camera's `+Y` points down, HANDOVER), so its remap is the identity.

#### Rotation encodings

One decoder, used by poses, extrinsics and boxes:

```yaml
rotation:
  euler: {fields: [roll, pitch, yaw], order: zyx, frame: intrinsic}
  # or quaternion: {fields: [qw, qx, qy, qz]}      # field order is stated, not inferred
  # or matrix: {field: extrinsic, shape: 4x4, layout: row_major}
  # or axis_angle / yaw_only: {field: rotation_y, axis: z}
transform_direction: child_to_parent   # sensor->body, body->world; or parent_to_child (inverted on read)
missing_angles: error                   # error | zero (zero needs a reason string)
```

`order: zyx` with `frame: intrinsic` is `R = Rz(yaw) @ Ry(pitch) @ Rx(roll)`, which is `SE3.from_rpy_deg`. A unit test pins each order against that function.

#### Streams

Each stream has `reader`, `path`, `missing` (`error` | `skip`) and a field mapping. Readers are named and registered. A manifest can name only readers from the built-in set or from an installed `coperceive.readers` entry point. It cannot name `module:attr`.

- `poses`: one record per sample (`json_record`, `yaml_record`) or one table per agent (`csv_rows`, `json_table`). Translation fields, rotation, optional `check` (field must equal the sample key).
- `sensors`: one calibration file per sensor (glob over `{sensor}`), or a table. Extrinsic, optional intrinsic (3×3 or `fx/fy/cx/cy` fields), image size (fields, or `derive: principal_point_x2`, which the manifest has to name, because it is a stand-in), and a modality rule. Modality rules, first match wins: `has_intrinsic: camera`, `stem_prefix: {lidar: lidar}`, `default: camera`.
- `boxes`: one text file of whitespace rows (`text_rows`), or JSON records. `variants` keyed on field count, each an ordered column list. Missing optional columns are null, never filled, unless the manifest says `fill` with a reason. `centre_origin: centre | bottom`, `size_order: lwh | hwl | wlh`, `frame: body | world | sensor:<id>`. A `sensor:` box is moved to the body frame with that sensor's extrinsic at ingest, because schema 1 stores only `body` and `world`. `class_map` maps source labels to output categories, and `class_map_unknown: keep | error | drop`. Track ids get `track_id_cast: string | int_string`, where `int_string` is `str(int(float(v)))`.
- `images`: `for_each: camera` and a path template. `require_file: false` writes the ref even when the file is absent, which is what Griffin does today. The path is stored relative to the scene output directory, because `GET /api/scenes/{id}/image` confines to that directory.
- `lidar`: agents and sensor id, path template, reader (`ply`, `pcd`, `bin_f32` with a stride and field offsets), field map `{x, y, z, intensity}`. `intensity: {fill: 0.0, reason: "..."}` is required when the source has none. `when_directory_absent: skip` lets a metadata-only tree produce no clouds. `missing: error` then requires one cloud per sample once the directory exists. Points are packed as little-endian float32 `x, y, z, intensity`, as schema 1 requires.

#### Checks

Cross-file rules that are not field mappings: `must_equal` (above), `required_per_sample: [poses, boxes]` (every listed sample has a file) and `scene_id_safe` (always on: no `.`, `..`, slash, backslash or leading dot).

#### Report

`report: {variant_counts: boxes}` makes the driver count `text_rows` variants by field count and write them into `COMPLETE.json` as `label_field_counts`, which is the key Griffin writes now. Other counters are not part of `spec_version` 1.

### Generic readers

| Reader | Shapes it covers | Checked against |
| --- | --- | --- |
| `json_record`, `yaml_record` | one record per sample per agent (Griffin poses, OPV2V-style per-CAV YAML) | Griffin synthetic fixture; OPV2V not checked |
| `json_table` | a JSON list of records joined on a key (nuScenes-style `sample`, `ego_pose`, `calibrated_sensor` tables; DAIR-V2X-style index files) | synthetic fixture only |
| `csv_rows` | one pose table per agent | synthetic fixture only |
| `text_rows` | whitespace label files with per-line width variants (KITTI-style, Griffin) | Griffin synthetic fixture |
| `ply` | ASCII and binary little-endian, named float properties | Griffin synthetic fixture (ASCII) |
| `pcd` | ASCII and binary, `FIELDS`/`SIZE`/`TYPE` header; `binary_compressed` refused | synthetic fixture only |
| `bin_f32` | headerless float32 records with a declared stride (KITTI-style `.bin`) | synthetic fixture only |
| image folder | `ImageRef` paths; bytes are never copied | Griffin synthetic fixture |

The relational `json_table` join is declared as `join: {table: ego_pose, on: ego_pose_token}`, one hop per declaration and at most three hops per stream. A dataset that needs more than that is a plugin.

The lidar readers parse with NumPy and cast to float32. Today's Griffin reader packs Python floats with `struct`. Both round a double to the nearest float32, so the bytes should agree. The regression test is what proves it.

### Plugin hook

Two entry point groups:

- `coperceive.adapters`: the value is an `Adapter` instance, a zero-argument factory, or an `importlib.resources` path to a manifest. `adapters list` shows built-ins, then entry points, then `--manifest` files.
- `coperceive.readers`: the value is a reader callable with the same signature as a built-in reader. A manifest names it by its entry point name.

Plugins run in the CLI process. They are installed Python packages and are trusted the way any import is. This differs from the model plugins planned in the roadmap, which run in a subprocess, because a model brings its own environment and GPU stack. The web app's job `ALLOW` list is not changed in this phase. `adapters ingest` is not reachable from the page.

### CLI

```text
coperceive adapters list
coperceive adapters check  <name|manifest.yaml> --raw-root DIR [--param k=v ...] [--scenes ID,...]
coperceive adapters ingest <name|manifest.yaml> --raw-root DIR --out DIR [--param k=v ...] [--workers N] [--scenes ID,...]
```

`check` loads and validates the manifest, discovers scenes, reads the first and last sample of every stream for each scene (or for `--scenes`), builds the `Scene` in memory, and prints counts per stream and the first error per stream. It writes nothing.

`ingest` is the driver that `convert_griffin` is today, with the same `.partial/`, `COMPLETE.json`, resume and at most 8 workers. `coperceive convert griffin --subset 25m ...` stays and becomes `adapters ingest griffin --param subset=25m ...`.

### Griffin onto the manifest

Each rule in `raw.py` and `convert.py`, and where it goes:

| Today | Manifest |
| --- | --- |
| `scene_infos.json` on both sides, required equal | `scenes.index.file: vehicle-side/scene_infos.json`, `must_equal: [drone-side/scene_infos.json]` |
| Scene name, frames, `map_name`, `cruise_altitude` | index field paths `name`, `info.frames`, `info.map_name`, `info.cruise_altitude` |
| `subset` platform value | `params.subset: {allowed: [25m, 40m, 55m, random]}` and platform `{param: subset}` |
| `cruise_altitude_m` is `format(float(v), ".10g")`; a bool or non-number fails | platform `{field: info.cruise_altitude, number: true, format: ".10g"}` |
| Both agents get the same platform strings | `platform` declared at scene level, applied to every agent |
| `vehicle` is `vehicle`, `drone` is `uav` | fixed `agents` list |
| `time_us = frame * 100000`, clock note | `clock: {from: frame, unit: frame, scale_us: 100000}` with the existing note text |
| Pose file `pose/{frame:06d}.json`, `timestamp` must equal the frame | `poses.path`, `check: {timestamp: frame}` |
| Pose `x y z` metres, `roll pitch yaw` degrees, `Rz @ Ry @ Rx` | `rotation.euler: {fields: [roll, pitch, yaw], order: zyx, frame: intrinsic}`, `angle_unit: deg` |
| Calib glob `calib/*.json`, sorted by stem | `sensors.path: calib/{sensor}.json`, sorted |
| `extrinsic` 4×4 rows, sensor to ego | `matrix: {field: extrinsic, shape: 4x4, layout: row_major}`, `child_to_parent` |
| `intrinsic` 3×3 optional; width `round(2*cx)`, height `round(2*cy)` | `intrinsic: {field: intrinsic, shape: 3x3, optional: true}`, `image_size: {derive: principal_point_x2}` |
| Modality: stem starts `lidar` → lidar, else camera; an intrinsic forces camera | modality rules `has_intrinsic: camera`, `stem_prefix: {lidar: lidar}`, `default: camera` |
| Labels `label/{frame:06d}.txt`, width 12, 11 or 9 per line | `text_rows` with three `variants` |
| 9-field: roll and pitch 0, visibility null | `missing_angles: zero` with the reason from `docs/datasets/griffin.md`; visibility absent → null |
| Track id `str(int(float(...)))` | `track_id_cast: int_string` |
| Boxes in the body frame | `frame: body` |
| Image refs for every camera × frame, file not required | `images: {for_each: camera, path: "{agent_root}/camera/{sensor}/{frame:06d}.png", require_file: false}` |
| Vehicle-only ASCII PLY `x y z I`; no directory → no clouds; directory → every frame | `lidar: {agents: [vehicle], sensor: lidar_top, reader: ply, intensity: I, when_directory_absent: skip, missing: error}` |
| Drone lidar calibs with no clouds | they are sensors from the calib glob; no `lidar` stream names them |
| Pose and label required for every frame | `checks.required_per_sample: [poses, boxes]` |
| `objects` = `shared_objects(annotations)` | always derived by the builder, for every adapter; not a manifest key |
| Label width histogram in `COMPLETE.json` | `report.variant_counts: boxes` |
| Parallel, `.partial/`, resume, keep finished scenes on failure | the generic driver |

Nothing in Griffin needs a plugin. The two rules that come closest are the modality override and the `principal_point_x2` size. Both became named, closed options rather than code.

### Regression

Two tests, added in the same PR as the Griffin manifest:

1. `tests/test_adapters_griffin.py`, in CI. It builds the synthetic release the Griffin tests already build, adds an 11-field and a 9-field label line, a calib with no intrinsic and a missing lidar directory case, then runs `convert_griffin` (the current code, kept until the PR merges) and `adapters ingest griffin` into two directories.
2. The same comparison marked `slow`, against `COPERCEIVE_GRIFFIN_RAW`. Adarsh runs it on real data through an sbatch job and pastes the output.

"Identical" means:

- `manifest.json` and `points.bin` are byte-for-byte equal;
- each Parquet file, read back, gives `pa.Table.equals(other, check_metadata=False)`: same schema, same row order, same values. Raw Parquet bytes are not compared, because writer metadata can differ;
- `read_scene` on both gives equal `Scene` dataclasses;
- `COMPLETE.json` is equal on `scene_id`, `schema_version`, `frames` and `label_field_counts`. Any key the adapter driver adds (see decision 4) is excluded by name.

After the comparison passes on real data, `coperceive/griffin/raw.py` and the scene-building half of `convert.py` are removed, and `convert griffin` calls the adapter.

## Consequences

- Schema 1 does not change. The body and camera axis conventions above are written into `docs/spec/format.md` as the adapter output convention, a documentation change to what the code already assumes.
- PyYAML becomes a core dependency, if decision 1 stays YAML.
- `coperceive/adapters` is under strict pyright. `coperceive/griffin` keeps its exclusion until its reader is deleted.
- A dataset the vocabulary cannot express needs a reader plugin, not a manifest key. New manifest keys mean `spec_version` 2.
- The "add a dataset in 20 minutes" doc walks through a synthetic KITTI-style toy dataset generated by a script in `tests/`. No real second dataset is ingested until Adarsh says so.
- Generic readers other than ASCII PLY, `json_record` and `text_rows` are checked only against synthetic fixtures. Nothing here claims they read OPV2V, V2XSet, DAIR-V2X or nuScenes correctly.

## Decisions

Taken by Adarsh on 2026-10-17.

1. **Manifest format.** YAML with `yaml.safe_load`. `pyyaml` becomes a core dependency.
2. **Axis convention.** `FLU` body and `RDF` camera are written into `docs/spec/format.md` as the adapter output convention. Schema version stays 1: it documents what the viewer and projection already assume.
3. **`convert griffin`.** Kept permanently as an alias that calls the adapter.
4. **Provenance.** `COMPLETE.json` gains `adapter`, `manifest_sha256` and `coperceive_version`. The resume check still reads only `scene_id`, `schema_version` and `frames`, so earlier outputs still skip. The three keys are excluded by name from the regression comparison.
5. **Entry point groups.** `coperceive.adapters` and `coperceive.readers`.
6. **In-process plugins.** Adapters and readers run in the CLI process. Model plugins (roadmap) run in a subprocess or container.
7. **Griffin 9-field lines.** Roll and pitch stay 0, visibility null, so the output stays identical. A test pins it. TODO: carry the missing-angle fact through the schema in a later version instead of storing a filled-in zero.
8. **Toy dataset.** The "add a dataset in 20 minutes" doc uses a synthetic KITTI-style dataset generated in tests. The first real second dataset will be OPV2V or V2XSet, when Adarsh says go; neither is ingested by this phase.
