# OPV2V and V2XSet adapters (OpenCOOD layout) — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** OPV2V and V2XSet scenes open in the viewer, with lidar, cameras, boxes and (where a town can be named) the ADR 0008 map, through two builtin manifests and the generic readers, with no new reader.

**Architecture:** Both datasets share the OpenCOOD per-agent YAML layout (`<scene>/<agent>/NNNNNN.{yaml,pcd,_cameraK.png}` plus `<scene>/data_protocol.yaml`), so one set of manifest rules serves both and the manifests differ in name, licence text, agent typing and the split list. Four small, general additions to the manifest spec carry what the Griffin manifest never needed: a left-handed source (one y-reflection applied to every pose, box and point before the existing axis remaps), a box centre given as an actor location plus a rotated offset with half extents, a box record that is a mapping keyed by track id, and an agent-id-to-type override for discovered agents. The town does not live in the data files, so a separate identification step picks it by the ADR 0008 aligner's residual and the manifest reads it from a sidecar table. Staging and the browser check reuse the step A scripts with a dataset switch.

**Tech Stack:** Python 3.10–3.13, PyYAML, PyArrow, the existing `yaml_record`, `pcd` and image-folder readers; Playwright for the staged check; Slurm `ps,main-cpu` for staging (no GPU anywhere in this plan).

**Spec:** `docs/adr/0009-dataset-adapters.md` (manifest and readers), `docs/spec/format.md` (schema 1), `docs/adr/0008-hd-map.md` (maps). This plan adds an "As built, OpenCOOD" section to ADR 0009 in Task 11; the rules below are the normative text until then.

## What the survey of 2026-10-18 established (measured, login node, read-only)

- Layout: OPV2V `train` 44, `validate` 9, `test` 16, `test_culver_city` 4 scenes; V2XSet `train` 33, `validate` 6, `test` 19, of which 29 of 58 have an RSU as agent `-1`. One OPV2V scene (`train/2021_08_16_22_26_54`): agents `641`, `650`, `659`, 419 frames each, stems `000069` to `000905` in steps of 2. ASCII PCD with fields `x y z rgb` (57,425 points in the frame checked), four 800×600 PNGs per frame with intrinsics in the YAML.
- Conventions, from public sources only: CARLA's Python API doc says "CARLA uses the Unreal Engine coordinates system. This is a Z-up left-handed system" and that pitch, yaw, roll are rotations about Y, Z, X in degrees. The OpenCOOD data-annotation page says `lidar_pose` is the "LiDAR pose under CARLA map coordinate system", camera `cords` are `x, y, z, roll, yaw, pitch` in that frame, camera `extrinsic` is "from camera to LiDAR", `extent` is "half length, width and height of the vehicle in meter", `center` is "the relative position from bounding box center to the frontal axis of this vehicle", `location` is the position of the vehicle's frontal axis in the map frame, `angle` is roll, yaw, pitch, and `ego_speed` is km/h. The OPV2V paper (arXiv 2109.07644) says "The sensor data is streamed at 20 Hz and recorded at 10 Hz" and that "the ground truth boxes are defined with respect to the aggregated lidar points from all CAVs". The V2X-ViT paper (arXiv 2203.10638) says "We record LiDAR point clouds at 10 Hz" and that infrastructure sensors sit "at the height of 14 feet".
- Tick: every `data_protocol.yaml` on disk has `world: {fixed_delta_seconds: 0.05, sync_mode: true}`. Stems step by 2, so one saved frame is 100 ms, consistent with the papers' 10 Hz. That the stem is the tick count is an assumption (the OpenCOOD page calls it a timestamp without a unit).
- Town: no `data_protocol.yaml` of either dataset names a town or map (keys: `carla_traffic_manager`, `current_time`, `description`, `scenario`, `vehicle_base`, `world`; the only map hint is the OpenCDA scenario template text "customized 2lanefree simple version" in the description). The OpenCOOD docs do not list scene-to-town either. **Item 6 of the brief cannot be done from the file**; Task 6 identifies the town by alignment instead.
- Licence: the OPV2V page (`https://mobility-lab.seas.ucla.edu/opv2v/`) states no licence, only "Copyright © 2021 UCLA Mobility Lab. All Rights Reserved"; the OpenCOOD and v2x-vit repositories are MIT for the code and say nothing about the data; the V2X-ViT paper says nothing about V2XSet's terms; nothing on disk. Both manifests therefore record `licence: unknown` with those links (Task 1).
- Known-answer evidence for the frame rules: a throwaway script in the session scratch (not committed) took one OPV2V frame (`641/000069`), transformed the 57,425 lidar points into the map frame with `lidar_pose` as a left-handed Rz·Ry·Rx in degrees, built every box as `location + R(angle) · center` with sizes `2 · extent`, and found points inside **33 of 33** boxes (0.15 m slack); without the `center` offset, 31 of 33. The conversion is right when done consistently in the source handedness; the adapter must apply the same reflection to everything.
- Per-agent box sets are not duplicates: at that frame the three agents list 33, 32 and 29 vehicles, union 42, intersection 18, and a shared id has identical `location` and `angle` in every agent's file. Each agent's list is the vehicles with at least one lidar hit from that agent (OpenCOOD page). Each CAV appears in the other CAVs' lists. **Item 3 of the brief is therefore reinterpreted** in Task 3: store each agent's own set once in the world frame; schema 1's `objects` rule already names a shared id once.

## Global Constraints

- Clean room: conventions come from the CARLA documentation, the OpenCOOD documentation pages and the two papers quoted above; no OpenCOOD or v2x-vit code is read or copied. The throwaway check above derived its matrices from the CARLA page.
- No new reader. `yaml_record`, `pcd`, image folders and the glob discovery already exist. Spec additions are rules in `coperceive/adapters/spec.py` and `build.py`, each with a key-path error and a test.
- Output stays schema 1 exactly: `FLU` body, `RDF` camera, right-handed `world_frame`. No schema change.
- Python in `coperceive/`: `from __future__ import annotations`, SPDX header, ruff at 100 columns, strict pyright, `zip(strict=True)`. TDD: write the test, watch it fail, implement.
- Tests are synthetic (`tests/opencood_release.py` written in Task 1); no dataset bytes in the repository; the real-data checks run on the HPC and their numbers go to HANDOVER.
- HPC: login node commands under a minute and single core; the staging job is `ps,main-cpu`; every sbatch is shown to Adarsh before submission; `REPO=$HOME/wt/adapter-opencood` with the import-location check; nothing written under `/home` as scratch.
- Dataset bytes are read in place under `/datasets/eemcs/ps/cv/opencood/`; images are linked, never copied.
- Conventional Commits, push after every task, branch `feat/adapter-opencood` off main `4ae619d`; no merge.

## Review Focus

1. A scene where one agent's YAML lacks a frame another agent has (stems differ between agents) → the missing agent is skipped for that sample with `missing: skip`, the others keep it, and the scene still builds (Task 1 test `test_agents_with_different_stems_keep_their_own_samples`).
2. A V2XSet scene whose RSU folder `-1` also carries `camera0..3` keys with `cords` far from the lidar → the sensors are placed through the extrinsic, not the `cords`, so the RSU's cameras sit at the pole (Task 4 test `test_rsu_cameras_come_from_the_extrinsic`).
3. A pose with non-zero roll and pitch under the reflection → the mirrored rotation keeps the vehicle's forward axis pointing along its travel direction (Task 2 test `test_reflection_keeps_forward_along_travel`); a sign error here passes any yaw-only test.
4. A box whose `center` offset is non-zero and whose `angle` yaw is near 90° → the centre moves along the box's own axis, not the world axis (Task 3 test `test_centre_offset_is_rotated_by_the_box_angle`).
5. A scene whose town cannot be identified (Culver City, or a residual above the threshold) → the scene builds with no `map_name`, the viewer shows "no map for <scene>" instead of a wrong map (Task 6 test `test_unidentified_town_leaves_map_name_empty`).
6. `track_id` keys in the YAML are integers → written as strings, and the ego CAV's own id never appears in its own box list (Task 3 test `test_track_ids_are_strings_and_the_ego_is_not_its_own_box`).

## Task order

Task 1 (fixture, manifests, discovery, clock) → Task 2 (handedness) → Task 3 (boxes) → Task 4 (RSU typing) → Task 5 (lidar, cameras, images) → Task 7 (CLI, docs stubs). Task 6 (town identification) needs 2 and 5 and can run beside 3 and 4. Task 8 (real-data checks on the login node) needs 1–6. Task 9 (staging script and sbatch) needs 5–7; Task 10 (staging job and Playwright check) needs 9 and Adarsh's yes. Task 11 (docs, ADR addendum, HANDOVER) closes.

---

### Task 1: Synthetic OpenCOOD release, the two manifests, discovery and clock

**Files:**
- Create: `tests/opencood_release.py` (`write_release(root, *, scenes, agents, frames, rsu=False, left_handed=True)` writing the layout above with a `data_protocol.yaml` holding `world: {fixed_delta_seconds: 0.05, sync_mode: true}`; stems `frame * 2`; one ASCII PCD per frame with `x y z rgb` and a known point set inside each box; four 8×6 PNGs per frame; YAML with `lidar_pose`, `true_ego_pos`, `ego_speed`, `camera0..3` with `cords`, `extrinsic`, `intrinsic`, and `vehicles` keyed by int id with `angle`, `center`, `extent`, `location`, `speed`)
- Create: `coperceive/adapters/builtin/opv2v.yaml`, `coperceive/adapters/builtin/v2xset.yaml`
- Modify: `coperceive/adapters/spec.py` (new optional top-level `licence` block: `{status: unknown|spdx, spdx?, notice, links: [..]}`; a `licence.status: unknown` manifest loads and `adapters list` shows `licence unknown`)
- Test: `tests/test_adapters_opencood.py`

**Interfaces:**
- Produces: the two manifests, identical except `adapter`, `licence.notice`, `agents` (Task 4) and `params.split.allowed` (`opv2v`: `train, validate, test, test_culver_city`; `v2xset`: `train, validate, test`). Shared rules:
  - `scenes: {glob: {path: "{split}/{scene}/", sample_source: poses, platform: {split: {param: split}}}}`
  - `agents: {discover: {glob: "{split}/{scene}/{agent}/", type: vehicle}}` (Task 4 adds `types`)
  - `clock: {from: frame, unit: frame, scale_us: 50000, scale_note: "<the tick assumption, see Task 1 step 3>"}`
  - `conventions: {length_unit: m, angle_unit: deg, source_handedness: left (Task 2), body_axes: FLU, camera_axes: RDF}`
  - `streams.poses: {reader: yaml_record, path: "{agent_root}/{frame:06d}.yaml", missing: skip, translation: [lidar_pose.0, lidar_pose.1, lidar_pose.2], rotation: {euler: {fields: [lidar_pose.3, lidar_pose.5, lidar_pose.4], order: zyx, frame: intrinsic}}}` — the YAML order is `roll, yaw, pitch`, the manifest's euler fields are `(about_x, about_y, about_z)`, hence `.3, .5, .4`. The pose is the lidar's, so the lidar sensor's extrinsic is identity and the cameras' extrinsics ("from camera to LiDAR") are `child_to_parent` to the body.
  - `licence: {status: unknown, notice: "<text of step 2>", links: [...]}`
- Consumes: `Template`, `GlobSpec`, `DiscoverSpec`, `ClockSpec` as they exist.

- [ ] **Step 1: Write the failing tests** in `tests/test_adapters_opencood.py`: `test_both_manifests_load_and_list` (`adapters list` shows `opv2v builtin` and `v2xset builtin`, each with `licence unknown`); `test_scenes_and_agents_are_discovered_per_split` (two scenes in `train`, agents `641`, `650`; `--param split=train`); `test_clock_is_fifty_ms_per_tick` (stem `000070` → `time_us == 3_500_000`; `scale_note` contains `fixed_delta_seconds` and `assumption`); `test_agents_with_different_stems_keep_their_own_samples` (agent `650` lacks stem `000070`: scene builds, `pose_times("650")` lacks it, `pose_times("641")` has it).
- [ ] **Step 2: Run** — fails: unknown adapter, `licence` key unknown.
- [ ] **Step 3: Implement** the fixture, the `licence` block and the two manifests. The `scale_note` text, verbatim in both manifests: `"time_us = stem * 50000. Every data_protocol.yaml sets world.fixed_delta_seconds: 0.05 and sync_mode: true; stems advance by 2 per saved frame, which matches the OPV2V paper's 'streamed at 20 Hz and recorded at 10 Hz' (arXiv 2109.07644) and V2X-ViT's 'record LiDAR point clouds at 10 Hz' (arXiv 2203.10638). That the file stem is the simulator tick count is an assumption; the files hold no wall clock."` The `licence.notice`, verbatim: OPV2V: `"No licence is published. The dataset page (https://mobility-lab.seas.ucla.edu/opv2v/) states 'Copyright © 2021 UCLA Mobility Lab. All Rights Reserved.'; the OpenCOOD code (https://github.com/DerrickXuNu/OpenCOOD) is MIT and says nothing about the data. Terms unknown as of 2026-10-18; cite Xu et al., OPV2V, ICRA 2022, arXiv:2109.07644."` V2XSet: `"No licence is published. The v2x-vit code (https://github.com/DerrickXuNu/v2x-vit) is MIT and says nothing about the data; the V2X-ViT paper (arXiv:2203.10638) states no terms. Terms unknown as of 2026-10-18; cite Xu et al., V2X-ViT, ECCV 2022."`
- [ ] **Step 4: Run, lint, pyright.**
- [ ] **Step 5: Commit** `feat(adapters): opv2v and v2xset manifests with discovery, clock and licence block`.

### Task 2: Left-handed source: one y-reflection with mirrored rotations

**Files:**
- Modify: `coperceive/adapters/rotation.py` (`Reflection` with `rows = ((1,0,0),(0,-1,0),(0,0,1))`, `point(p)`, `se3(t: SE3) -> SE3` = `M ∘ t ∘ M` so translation gets `ty → -ty` and the rotation is conjugated, which mirrors yaw and roll and keeps pitch), `coperceive/adapters/spec.py` (`Conventions.source_handedness: str = "right"`, choices `right | left`, with `reflect_axis: y` as the only allowed axis in this version), `coperceive/adapters/build.py` (apply the reflection to every pose before `self._body.world_from_body`, to every box centre, box rotation and sensor extrinsic before their remaps, and to every lidar point before the body remap; `world_frame` is unchanged because the reflected world is right-handed)
- Test: `tests/test_adapter_rotation.py` (unit), `tests/test_adapters_opencood.py` (end to end)

**Interfaces:**
- Produces: `Reflection.Y: Reflection`; `Reflection.se3(SE3) -> SE3`; `Reflection.point(Vec3) -> Vec3`; `Conventions.source_handedness`.
- Rule text for ADR 0009: a `left` source is converted by the fixed reflection `M = diag(1, -1, 1)` applied as `p' = M p` to points and translations and `R' = M R M` to rotations, before `body_axes` and `camera_axes`, which describe the axes after the reflection. The reflection is explicit so the manifest says what happened; a manifest may not negate a single field by hand.

- [ ] **Step 1: Write the failing tests**: `test_reflection_mirrors_yaw_and_roll_keeps_pitch` (`Reflection.Y.se3(SE3.from_rpy_deg(10, 20, 30))` equals `SE3.from_rpy_deg(-10, 20, -30)` to 1e-12 and translation `(1, 2, 3)` → `(1, -2, 3)`); `test_reflection_is_an_involution`; `test_left_handed_manifest_refuses_other_axes` (`reflect_axis: x` → `SpecError`); `test_reflection_keeps_forward_along_travel` (fixture: a vehicle moving along source `+y` with yaw 90° and roll 5°; after conversion the body `+x` axis points along the world displacement between consecutive poses, dot product > 0.99); **known answer** `test_box_centres_sit_inside_their_lidar_points` (fixture with three boxes at known places and a point cloud with points only inside the boxes: after `ingest`, for every annotation the world-frame centre and the box's own axes contain at least one point from `points.bin` of that agent and frame, and no box is empty).
- [ ] **Step 2: Run** — fails: `source_handedness` unknown.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run, lint, pyright.** Also run the whole `tests/test_adapters_griffin.py` and `tests/test_adapter_toy.py`: a right-handed manifest must be byte-identical to before (the Griffin regression test covers it).
- [ ] **Step 5: Commit** `feat(adapters): left-handed sources through one explicit y-reflection with mirrored rotations`.

### Task 3: Boxes from location, rotated centre offset, half extents, mapping records

**Files:**
- Modify: `coperceive/adapters/spec.py` (`BoxesSpec.records` may be `{path: vehicles, mapping_key: track_id}`; new `BoxesSpec.centre: {location: [..3 fields], offset: [..3 fields], offset_frame: box}` as an alternative to `cx, cy, cz` fields; new `BoxesSpec.size: {half_extents: [..3 fields]}` as an alternative to `length, width, height`; the two forms are mutually exclusive per manifest and a key-path error names the clash), `coperceive/adapters/build.py` (`_box`: `centre = location + R(angle) · offset`, sizes `2 · extent`, in source axes before the reflection and remap), both manifests (`boxes: {reader: yaml_record, path: "{agent_root}/{frame:06d}.yaml", missing: skip, frame: world, records: {path: vehicles, mapping_key: track_id}, centre: {location: [location.0, location.1, location.2], offset: [center.0, center.1, center.2], offset_frame: box}, size: {half_extents: [extent.0, extent.1, extent.2]}, rotation: {euler: {fields: [angle.0, angle.2, angle.1], order: zyx, frame: intrinsic}}, fill: {category: vehicle}, track_id_cast: int_string}`)
- Test: `tests/test_adapters_opencood.py`, `tests/test_adapter_spec.py`

**Interfaces:**
- Produces: the three spec additions; `category` is the constant `vehicle` for both datasets (the YAML has no class field; the OPV2V paper annotates "3D vehicle bounding boxes").
- Shared ground truth (item 3 of the brief, reinterpreted): boxes are `frame: world` rows per agent, exactly the dataset's per-agent lists; nothing is copied from one agent to another; `objects.parquet` holds each id shared by two or more agents once (schema 1 rule). The evaluator's per-agent ground truth stays the agent's own list, which is what the dataset defines ("at least one LiDAR point hit from the agent"). The per-scene union is `objects` plus the single-agent rows.

- [ ] **Step 1: Write the failing tests**: `test_centre_offset_is_rotated_by_the_box_angle` (offset `(1, 0, 0)` with yaw 90° moves the centre by one metre along the source `+y`, which after reflection is world `-y`... state the expected world coordinates numerically in the test); `test_half_extents_are_doubled` (`extent (2.45, 1.06, 0.76)` → `size_lwh (4.90, 2.12, 1.52)`); `test_mapping_records_use_the_key_as_track_id` (ids `650`, `659` become `"650"`, `"659"`); `test_track_ids_are_strings_and_the_ego_is_not_its_own_box`; `test_shared_ids_are_one_object_with_identical_world_centres` (two agents list id `677` with the same source geometry → one `objects` row, centres equal to 1e-9); `test_per_agent_sets_are_not_duplicated` (agent `641` lists ids `{677, 685}` and `650` lists `{685, 689}` → annotations count 4, not 6); spec tests: `test_boxes_centre_and_cx_fields_are_exclusive`, `test_half_extents_and_length_are_exclusive`.
- [ ] **Step 2: Run** — fails.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run, lint, pyright**; Griffin regression unchanged.
- [ ] **Step 5: Commit** `feat(adapters): boxes from a location plus rotated offset, half extents, mapping records`.

### Task 4: Agent type override: `-1` is infrastructure

**Files:**
- Modify: `coperceive/adapters/spec.py` (`DiscoverSpec.types: Mapping[str, AgentType]`, an id-to-type override applied after discovery; ids must be strings in the manifest, `"-1"` quoted), `coperceive/adapters/build.py` (`_agents` uses `types.get(agent_id, discover.type)`), `v2xset.yaml` (`agents: {discover: {glob: ..., type: vehicle, types: {"-1": infrastructure}}}`), `opv2v.yaml` unchanged (an OPV2V scene with a `-1` folder would be a finding, Task 8 checks none exists)
- Test: `tests/test_adapters_opencood.py`, `tests/test_adapter_spec.py`

- [ ] **Step 1: Write the failing tests**: `test_rsu_folder_becomes_infrastructure` (fixture `rsu=True`: agent `-1` has `agent_type == AgentType.INFRASTRUCTURE`, the others `VEHICLE`); `test_rsu_cameras_come_from_the_extrinsic` (the RSU's `camera0` body-from-sensor translation equals the fixture's extrinsic column, not its `cords`); `test_types_override_rejects_unknown_type` (`types: {"-1": drone}` → `SpecError` naming `agents.discover.types.-1`).
- [ ] **Step 2: Run** — fails. **Step 3: Implement. Step 4: Run, lint, pyright.**
- [ ] **Step 5: Commit** `feat(adapters): id-to-type override for discovered agents; V2XSet RSU is infrastructure`.

### Task 5: Lidar, cameras and images through the existing readers

**Files:**
- Modify: both manifests (`sensors`: `reader: yaml_record`, `path: "{agent_root}/{frame:06d}.yaml"` taken from the agent's first sample, `records: {camera0, camera1, camera2, camera3}` as sensor ids with `extrinsic: {matrix: {field: extrinsic, shape: 4x4}, direction: child_to_parent}`, `intrinsic: {matrix: intrinsic}`, `image_size: {derive: principal_point_x2, reason: "800x600 PNGs; cx, cy = 400, 300 in every file checked"}`, plus a fixed `lidar` sensor at identity; `lidar: {reader: pcd, sensor: lidar, path: "{agent_root}/{frame:06d}.pcd", fields: {x: x, y: y, z: z}, intensity_fill: 0.0, intensity_reason: "the PCD carries rgb, not intensity"}`; `images: {for_each: camera, path: "{agent_root}/{frame:06d}_{sensor}.png", require_file: false}`), `coperceive/adapters/spec.py` only if the sensors stream cannot name a fixed identity lidar sensor beside YAML-read cameras (then add `sensors.fixed: [{id: lidar, modality: lidar, identity: true}]` with a test)
- Test: `tests/test_adapters_opencood.py`

- [ ] **Step 1: Write the failing tests**: `test_four_cameras_and_one_lidar_per_agent`; `test_camera_extrinsic_is_camera_to_lidar_child_to_parent` (camera0 of the fixture sits 3 m behind and 0.9 m above the lidar, as the real file's extrinsic column `(-3.0, 0.0, 0.9)` says, after reflection and remap the body-frame translation is `(-3.0, 0.0, 0.9)`); `test_pcd_points_are_reflected_like_the_poses` (a source point `(1, 2, 3)` in the lidar frame lands at body `(1, -2, 3)`); `test_image_refs_name_the_png_files`.
- [ ] **Step 2–4** as above.
- [ ] **Step 5: Commit** `feat(adapters): opencood cameras, lidar and images through yaml_record, pcd and image folders`.

### Task 6: Town identification by alignment, map name from a sidecar

The files name no town. The ADR 0008 aligner already scores vehicle poses against a map's lane centres; identification runs it against every ingested CARLA town and keeps a clear winner.

**Files:**
- Create: `coperceive/hdmap/identify.py` (`identify_town(scene: Scene, maps_root: Path, *, accept_m: float = 0.5, margin_m: float = 2.0) -> TownGuess` with `TownGuess(map_name: str | None, residuals: Mapping[str, float], heading: Mapping[str, float])`: the best residual must be at most `accept_m` and the runner-up at least `margin_m` larger, else `map_name is None`; identity transform only, as every CARLA town so far aligned at identity)
- Modify: `coperceive/hdmap/commands.py` (`coperceive maps identify --maps ROOT --scenes ROOT --out scene_towns.yaml` printing one line per scene with the chosen town and the two best residuals), `coperceive/adapters/spec.py` (`PlatformValue.table: {file: Template, key: scene, field: map_name}`: a platform value read from a YAML or JSON mapping file at build time; a missing key leaves the value empty), both manifests (`scenes.glob.platform.map_name: {table: {file: "{param:towns}", key: scene}}` when `--param towns=FILE` is given, else absent)
- Modify: `~/maps-src/carla-0.9.15/` (Town01, Town02, Town04, Town05 `.xodr` from the same public mirrors, SHA-256 pinned in `SOURCE.md` and `SHA256SUMS`, with ADR 0008's pin table extended; done on the login node, each file under 10 MB)
- Test: `tests/test_hdmap_identify.py`, `tests/test_adapters_opencood.py`

- [ ] **Step 1: Write the failing tests**: `test_identify_picks_the_map_the_poses_follow` (two synthetic maps, poses along one of them → that `map_name`); `test_unidentified_town_leaves_map_name_empty` (poses matching neither within 0.5 m → `None`; the scene builds; the summary route says no map); `test_platform_table_value_is_read_per_scene` (a `scene_towns.yaml` of `{"2021_08_16_22_26_54": "Carla/Maps/Town03"}` puts `map_name` on every agent's platform of that scene and nothing on a scene not in the table).
- [ ] **Step 2–4** as above.
- [ ] **Step 5: Commit** `feat(hdmap): identify a scene's CARLA town by alignment residual; platform values from a sidecar table`.

### Task 7: CLI and adapter docs stubs

**Files:**
- Modify: `coperceive/adapters/commands.py` only if `--param split=` needs a default (none: the split is required, an error names the allowed values), `docs/datasets/opencood.md` (new: layout, conventions with the quotations of the survey section, the tick assumption, the licence status, the commands), `mkdocs.yml` nav, `README.md` one line under datasets
- Test: `tests/test_adapter_cli.py` (`coperceive adapters run opv2v --raw-root ... --param split=train --out ...` on the fixture writes two scenes; without `--param split` exits 1 naming `train, validate, test, test_culver_city`)

- [ ] **Steps 1–4** as above. **Step 5: Commit** `docs(datasets): opencood adapters, conventions and licence status`.

### Task 8: Real-data checks on the login node (read-only, under a minute each)

No code. A short script in the session scratch runs the adapter on one OPV2V scene and one V2XSet scene with an RSU, metadata only (`--no-lidar` is not an option of `adapters run`; use `--scenes` with one scene and accept the ASCII PCD parse time, measured first on one frame: 57,425 points parse in well under a second with NumPy), and records in HANDOVER:

- [ ] the known-answer check of Task 2 on real data: fraction of boxes with at least one own lidar point inside, per agent, for frame 1 and a mid-scene frame (expected to match the throwaway check's 33 of 33 within the dataset's own visibility rule);
- [ ] per-agent box counts, union and intersection at one frame (the 33/32/29, 42, 18 of the survey as the reference);
- [ ] `maps identify` over the pinned towns for both scenes, with residuals; the chosen town or `None`;
- [ ] that no OPV2V scene has a `-1` folder (a `ls` over the four splits);
- [ ] the RSU's `lidar_pose` and `camera` extrinsics of the V2XSet scene after conversion (height about 4.3 m, which is 14 feet, per the V2X-ViT paper; the scene checked has `z = 3.5` in `lidar_pose`, a discrepancy to record, not resolve).

### Task 9: Staging script and sbatch

**Files:**
- Create: `scripts/opencood/stage_viewer.py` (the shape of `scripts/griffin/stage_viewer.py` without the Griffin planning: `--raw`, `--dataset opv2v|v2xset`, `--split`, `--scenes a,b`, `--scratch /local/$SLURM_JOB_ID/...`, `--xodr-dir`, `--towns scene_towns.yaml` (optional; when absent it runs `maps identify` itself after ingest and prints the residuals), `--static`, `--workers`; steps: ingest the named scenes with the adapter (lidar parsed from ASCII PCD, timed per scene), link each agent's PNG directory into the scratch raw root for `--media-root`, ingest and align the identified towns' maps, print the serve line)
- Create: `~/scripts/slurm/opencood-viewer-stage.sbatch` (`ps,main-cpu`, 4 CPUs, 16 GB, 1 h; `REPO` required, import-location check; stages one OPV2V `test` scene and V2XSet `train/2021_08_20_20_39_00` (RSU `-1`); then `scripts/griffin/check_staged_scenes.py --scenes <the two ids>` against the staged roots; keeps `report.json`, PNGs, maps and scenes under `~/results/<job>/`)
- Modify: `scripts/griffin/check_staged_scenes.py` (no change expected; it is dataset-agnostic; if the RSU scene needs a different "thumbnail present" expectation because the RSU has cameras too, none is needed)
- Test: `tests/test_stage_viewer_opencood.py` (dry planning on the fixture: the file list, the serve line, refusal of `/home` scratch)

- [ ] **Steps 1–4** as above; `bash -n` on the sbatch; the sbatch text goes into HANDOVER and the report for Adarsh. **Step 5: Commit** `feat(scripts): opencood viewer staging for one scene per dataset`.

### Task 10: Staging job and Playwright check (after Adarsh's yes)

- [ ] Submit `opencood-viewer-stage.sbatch` with `--export=ALL,REPO=$HOME/wt/adapter-opencood`; record wall time per step, point counts per scene, the identified towns and residuals, the two scenes' status lines, thumbnail counts and HTTP errors from `report.json`, and attach the four PNGs, as for job 628385.
- [ ] If a town could not be identified, the scene is reported with "no map" and the residual table; no map is forced.

### Task 11: Docs, ADR addendum, HANDOVER

**Files:**
- Modify: `docs/adr/0009-dataset-adapters.md` ("As built, OpenCOOD (2026-10-xx)": the four spec additions with their rule text, the reflection, the town identification and its thresholds, the licence block), `docs/adr/0008-hd-map.md` (pins for the four new towns; `maps identify`), `docs/datasets/opencood.md` (numbers from Tasks 8 and 10), `CHANGELOG.md`, `HANDOVER.md` (state, measured numbers, the two reinterpretations of the brief, the licence status, open questions)

- [ ] **Step 1:** write; `mkdocs build` in the gates job. **Step 2: Commit** `docs: opencood adapters as built, maps identify, handover`.

---

## Gates

After every task: `sbatch --export=ALL,REPO=$HOME/wt/adapter-opencood,COPERCEIVE_WEB_DIST=$HOME/coperceive-web-dist,PYTEST_ADDOPTS=-rs ~/scripts/slurm/coperceive-gates.sbatch`. Expected: ruff and format clean, pyright 0 errors, every test green with the two Griffin-raw skips; the Griffin regression (`tests/test_adapters_griffin.py`) byte-identical, which is what proves the spec additions change nothing for a right-handed manifest.

## Decisions Adarsh needs to take before Task 1 starts

1. **Town (item 6).** The town is not in `data_protocol.yaml`. Task 6 identifies it by alignment residual against the pinned CARLA towns and stores it in a sidecar `scene_towns.yaml` that the manifest reads. Alternatives: ask the OPV2V authors for the scene-to-town list, or leave maps out of this step. The Culver City split has no OpenDRIVE and stays map-less either way.
2. **Shared ground truth (item 3).** The per-agent lists differ (33/32/29 boxes, union 42, intersection 18 in the frame checked) and are the dataset's own visibility-filtered truth, so Task 3 stores each agent's list once and lets `objects.parquet` name shared ids once. The alternative, one union set per frame attached to one agent, would change what every agent's recall means and would need an evaluator change. Confirm the reinterpretation.
3. **Licence (item 7).** Both manifests say `unknown` with the links above. Say whether to write to the authors before staging anything to a shared viewer, or whether in-group research use under the paper citation is enough for now.
4. **Four more OpenDRIVE files.** Town01, 02, 04, 05 from the public mirrors, pinned like the first four, so identification can see all eight towns the papers name.
5. **A new ADR or an ADR 0009 addendum** for the left-handed rule and the other three spec additions. The plan assumes the addendum.

## Self-review notes

- Spec coverage against the brief: item 1 → Task 2; item 2 → Task 3; item 3 → Task 3 (reinterpreted, decision 2); item 4 → Task 4; item 5 → Task 1 step 3; item 6 → Task 6 (decision 1); item 7 → Task 1 step 3 (decision 3); item 8 → Tasks 9–10; clean room → Global Constraints and the survey's source list.
- Names used across tasks: `write_release`, `Reflection`, `Conventions.source_handedness`, `BoxesSpec.centre`, `BoxesSpec.size`, `BoxesSpec.records.mapping_key`, `DiscoverSpec.types`, `PlatformValue.table`, `identify_town`, `TownGuess`, `maps identify`, `scene_towns.yaml`, `scripts/opencood/stage_viewer.py`, `opencood-viewer-stage.sbatch` — each defined once above.
- Measured numbers used here: the survey counts (scenes, agents, frames, 57,425 points, 800×600), the throwaway check (33 of 33; 31 of 33 without the offset), the per-agent sets (33/32/29, 42, 18), `fixed_delta_seconds: 0.05`, the papers' 20 Hz / 10 Hz. Every other duration or count is measured in Tasks 8 and 10.
- Review Focus items 1–6 are pinned to Tasks 1, 4, 2, 3, 6 and 3.
