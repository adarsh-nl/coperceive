# ADR 0010 — Evaluation engine, step A: predictions

Status: proposed. Nothing in this ADR is implemented. Schema 1 (`docs/spec/format.md`) is unchanged. Predictions are a sidecar, like the HD map (ADR 0008).

## Context

A scene holds ground-truth boxes per agent, per sample, in the agent body frame or the world frame. `scene.objects` lists the track ids two or more agents share. `coperceive.assoc.associate` already pairs boxes across agents by shared id first and world-centre distance second. The viewer draws boxes from `frame_document`, the timeline draws sparklines from pose telemetry, and the inspector reports a clicked box.

Step A adds the thing a perception researcher compares against that ground truth: a model's output. It has to be importable from files a training script writes, matched to ground truth with stated rules, shown in the viewer as true positives, false positives and misses, summarised per frame on the timeline and per scene as AP and recall, and comparable between two runs. Because the datasets are cooperative, it also has to answer the cooperative questions: what one agent misses that another agent sees, and what fusion recovers or loses.

Step B (live models through a subprocess) is not here. It writes into the same format, so this ADR fixes the format first.

## Options

### Where predictions live

1. **Inside the scene directory** (`<scene>/predictions/<run>/`). Simple paths, but it puts a second writer inside the schema 1 contract and a converter re-run deletes it.
2. **A sidecar root keyed by scene id**: `<predictions root>/<run_id>/<scene_id>/`. Schema 1 untouched, many runs per scene, a run can be deleted or shared as one directory. Same shape as the maps root.
3. **One table for all scenes per run.** Fewer files, but the viewer's per-scene reads would scan it, and a run on a subset of scenes is awkward.

### Matching criterion

1. **Centre distance** in the ground plane, per class threshold (the nuScenes detection rule). Cheap, well defined for boxes of any shape, robust to size errors that do not matter for cooperation studies.
2. **3D or BEV IoU** threshold (the KITTI rule). Standard for single-vehicle benchmarks; penalises size and yaw.
3. **Both**, selected by a config. More code paths to keep honest.

### Where matching runs

1. **In the browser** on each frame. No files, but AP needs every frame, and two tabs would compute it twice.
2. **Offline, once per run**, writing a per-box match table and a metrics file the viewer reads. Reproducible, diffable, and the viewer stays a reader.

### Comparing two runs

1. **Overlay** both runs in one viewport with different line styles, plus a metrics table with deltas.
2. **Split viewport.** Clearer, but it doubles the three.js scene and the camera sync is new UI.

## Criteria

- Schema 1 scenes are read, never written.
- The predictions format is writable from a training loop with PyArrow or plain JSON lines, without importing coperceive.
- Every matching rule is written in this ADR and in the report; nothing is a silent default.
- The viewer reads precomputed results; it does not re-match.
- Paths stay confined to configured roots. No shell. The token and CSRF rules of ADR 0004 hold.
- Step B can stream into the same tables.

## Decision

Sidecar root (option 2), centre-distance matching with IoU available later behind the same config (option 1 first), offline matching (option 2), overlay comparison first (option 1).

### Predictions format: `coperceive-predictions` version 1

```text
<predictions root>/
  <run_id>/
    run.json
    <scene_id>/
      boxes.parquet
```

`run_id` follows the scene-id rule: no `.`, `..`, slash, backslash or leading dot. The default predictions root is `<scenes root>/predictions` when it exists; `--predictions` overrides it for `eval` and `serve`, as `--maps` does.

`run.json`:

| Key | Meaning |
| --- | --- |
| `format` | `coperceive-predictions` |
| `format_version` | `1` |
| `run_id` | Same as the directory name. |
| `model` | Free string: name, checkpoint, commit. Not interpreted. |
| `created_utc` | ISO 8601. |
| `score_note` | Required. What `score` is (sigmoid, calibrated, …). Never implied, like `clock.scale_note`. |
| `categories` | The class names the model can emit, so an empty class still gets an AP row. |
| `sources` | The values the `source` column uses, each with a one-line description. |
| `notes` | Optional free text. |

`boxes.parquet` columns, Arrow types as in schema 1; unknown extra columns are ignored, a missing required column is an error:

| Column | Type | Null | Meaning |
| --- | --- | --- | --- |
| `agent_id` | string | no | The agent whose output this is. Must be a scene agent. |
| `source` | string | no | Which pipeline on that agent produced it: `single` for the agent's own sensors, `fused` for a cooperative output, or another name listed in `run.json`. |
| `time_us` | int64 | no | A pose time of that agent. |
| `category` | string | no | Class label, in the ground-truth vocabulary (the run maps its own labels before writing). |
| `score` | float64 | no | Confidence. Higher is more confident. Finite. |
| `track_id` | string | yes | Optional tracker id. |
| `cx`, `cy`, `cz`, `length`, `width`, `height`, `qw`, `qx`, `qy`, `qz` | float64 | no | As in `annotations.parquet`. |
| `frame` | string | no | `body` or `world`, as in schema 1. |

A fused output is therefore not a virtual agent: it is `agent_id = vehicle, source = fused`. Its boxes are in that agent's body frame or the world frame, and the cooperative views below compare `single` with `fused` on the same agent.

`coperceive predictions import` reads JSON lines (one object per box with the column names above) or a Parquet file with those columns, validates every row against the scene (agent exists, `time_us` is a pose time, finite numbers, unit quaternion within 1e-6, `frame` in the set), and writes the layout above. A Python helper `coperceive.predictions.write_run(root, run_id, rows, run_meta)` does the same for a training script. There is no manifest-driven reader in step A; a plugin hook is deferred until a second real format is in hand.

### Matching

Done by `coperceive eval run <run_id>`, per scene, per `(agent_id, source)`, per sample time, per category. Ground truth is that agent's annotations at that time. Everything is in the world frame: body boxes are placed with `lookup_pose` at `max_gap_us = 0`, as the viewer does.

Config is a YAML file (`--config`), validated like an adapter manifest; the defaults below are written into the report so a result is never read without them:

```yaml
criterion: centre            # centre | iou_bev | iou_3d  (step A implements centre)
threshold:                   # metres for centre, IoU for the others
  default: 2.0
  pedestrian: 0.5
  cyclist: 1.0
ignore_gt_visibility_below: null   # null keeps every box; a number drops GT below it
max_range_m: null            # null keeps every box; a number drops GT and predictions farther from the agent body origin
score_threshold: 0.3         # the operating point for TP/FP/miss display and per-frame counts
```

Rules, normative:

1. Predictions are sorted by `score` descending. Ties keep file order.
2. Each prediction in turn takes the unmatched ground-truth box of the same category with the smallest horizontal centre distance, if that distance is at most the class threshold. That pair is a true positive. A prediction with no such box is a false positive. A ground-truth box no prediction took is a miss.
3. A ground-truth box dropped by `ignore_gt_visibility_below` or `max_range_m` is ignored. A prediction whose nearest same-class box within threshold is an ignored box is ignored too, not a false positive. Ignored boxes appear in the match table with status `ignored`.
4. Matching across categories is not attempted. A pedestrian predicted as a cyclist is one false positive and one miss.
5. Per-frame counts (TP, FP, miss, precision, recall) use predictions with `score >= score_threshold`. AP uses every prediction.
6. **AP** per category: precision and recall are accumulated over the score-sorted predictions of the whole scene (every sample), the precision envelope is made monotone from the right, and AP is the area under that envelope over recall in `[0, 1]` (the all-points rule). A category with no ground truth in the scene has no AP and is reported as `null`, not 0.
7. **mAP** is the mean over categories that have an AP. **Recall at the operating point** is TP / (TP + miss) over the scene.
8. For true positives at the operating point, the report also gives the mean and median horizontal centre error, the mean absolute yaw error (yaw of `Rz(yaw) @ Ry(pitch) @ Rx(roll)`, as `validate` already does), and the mean absolute size error per axis. These are the step A analogue of nuScenes' translation, orientation and scale errors.

Normative checks the test suite pins: one GT car at the origin and one prediction at 1.5 m is a TP at threshold 2.0 and a miss plus a false positive at 1.0; two predictions on one GT box give one TP (the higher score) and one FP; a prediction of the wrong class is never a TP; AP of a scene with one GT and one TP at any score is 1.0, with one FP ranked above it is 0.5.

Outputs under `<predictions root>/<run_id>/<scene_id>/`:

- `matches.parquet`: one row per prediction and per ground-truth box. Columns: `kind` (`prediction` | `gt`), `agent_id`, `source`, `time_us`, `category`, `score` (null for GT), `status` (`tp` | `fp` | `miss` | `ignored`), `pair_id` (int64, shared by a TP and its GT, null otherwise), `centre_error_m`, `yaw_error_deg`, plus the box columns in the world frame so the viewer does not re-place them.
- `metrics.json`: the config, per-sample rows (`time_us`, per `(agent_id, source)`: tp, fp, miss, precision, recall), scene totals, AP per category, mAP, recall, the TP error statistics, and the cooperative counts below.

`coperceive eval run` writes these; `coperceive eval report <run_id>` prints a Markdown table over the scenes of a root; `coperceive eval compare <run_a> <run_b>` prints the same table with deltas.

### Cooperative views

Computed in the same pass and stored on the ground-truth rows of `matches.parquet` as extra columns:

| Column | Meaning |
| --- | --- |
| `status_single`, `status_fused` | That GT box's status under the agent's `single` and `fused` sources (null when the run has no such source). |
| `seen_by_other_gt` | True when another agent annotates the same object at that time: the shared track id in `scene.objects`, or a `computed` pair from `associate` with the existing size-scaled gate. The kind is stored in `other_kind`. |
| `other_agent_tp` | True when another agent's `single` output has a TP on that object at that time. |

From those, four named filters, each a count in `metrics.json` and a chip in the viewer:

- **recovered**: `status_single = miss` and `status_fused = tp`.
- **lost by fusion**: `status_single = tp` and `status_fused = miss`.
- **only the other agent sees it**: `status_single = miss` and `other_agent_tp`.
- **nobody sees it**: miss on every source of every agent that annotates it.

`recovery_rate = recovered / (recovered + misses that stay misses after fusion)` is the one derived ratio; everything else is a count.

### Viewer

Routes, all GET, all read-only, all confined to the predictions root:

- `GET /api/scenes/{id}/predictions` — runs that have this scene, with `run.json` and the scene totals from `metrics.json`.
- `GET /api/scenes/{id}/predictions/{run}/frame?time_us=` — prediction and GT rows from `matches.parquet` at that time, with the world-frame `lines` the page already draws for boxes, `status`, `score`, `pair_id` and the cooperative columns.
- `GET /api/scenes/{id}/predictions/{run}/series` — the per-sample rows of `metrics.json` for the sparklines.
- `?compare=<run_b>` on the last two returns both runs.

Page:

- A **Predictions** block in the layer column: run selector (and a second selector for compare), source chips (`single`, `fused`, …), status chips (TP, FP, miss, ignored), the four cooperative chips, and a score slider that starts at the run's operating point. The slider filters what is drawn and updates the frame counts; it does not recompute AP.
- Colours, on the data layer with bloom like the GT wires. Each status gets a colour and a line style, so the state survives colour-blindness and a greyscale export: TP green `#3BAA6B` solid, FP amber `#E0A431` dashed, miss violet `#8E6CEF` dotted and thicker, ignored `#6B6B6B` thin. Red-orange `#DE3B21` stays the playhead and Play. Ground-truth wires keep their agent or class colour; a matched GT box is drawn fainter so the TP reads on top. In compare mode run B uses the same hues with a dash offset and a small "B" tag in the inspector.
- **Inspector**: clicking a prediction shows run, source, score, status, category, matched GT track id, centre and yaw error, and the cooperative flags of the GT it matched. Clicking a GT box adds its status per source and the "seen by" line.
- **Timeline**: sparklines for precision, recall and FP count per frame for the selected `(agent, source)`, in the sparkline style already in `telemetry.ts`. Compare mode draws run B as a second thinner line. A frame where recall drops below the scene median by more than 0.3 gets a range marker, the same marker the timeline already draws.
- **Scene summary** card gains mAP, recall, and the four cooperative counts, with the A/B delta in compare mode.
- Export: the bird's-eye figure gains `--predictions <run>` and draws the same colours, so a paper figure matches the screen.

### What step A does not do

Live models (step B), tracking metrics (MOTA/HOTA), 2D detections in image space, segmentation, per-range AP breakdowns beyond `max_range_m`, IoU criteria (the config key exists so a run file written now stays valid), and a plugin hook for foreign prediction formats.

## Consequences

- Schema 1 is untouched. A run is one directory that can be deleted, copied or sent.
- `eval run` is the only matcher. The viewer and the figures read `matches.parquet` and `metrics.json`. Changing a rule means re-running `eval run`, and the config in `metrics.json` says which rules a file used.
- The predictions root is a third confined root beside scenes and maps. `serve` gains `--predictions`.
- Step B writes `boxes.parquet` row groups as frames arrive and calls the same matcher per frame; nothing in this format assumes a finished run.
- Griffin-25m has two agents and no model output in the repository. The first real run has to come from Adarsh's training code; the tests use synthetic predictions over the synthetic fixtures.

## Decisions for Adarsh

1. **Matching default.** Centre distance, 2.0 m default with 0.5 m pedestrians and 1.0 m cyclists, IoU deferred. Recommendation: yes; it is the rule cooperative-perception papers on OPV2V and V2XSet mostly do *not* use (they use IoU 0.5/0.7), so say if IoU must be in step A for comparability with those numbers.
2. **AP rule.** All-points area under the monotone precision envelope. The alternative is nuScenes' 101-point interpolation with recall and precision below 0.1 clipped. Recommendation: all-points; it needs no clipping constants.
3. **Fused outputs as `source = fused` on the real agent**, not a virtual agent. Recommendation: yes; it keeps frames well defined.
4. **Predictions root** `<scenes root>/predictions` by default, `--predictions` to override. Recommendation: yes, mirroring `--maps`.
5. **Colours and styles** for TP/FP/miss/ignored as listed. Recommendation: as listed; say if green/amber clash with the agent colours (`#e67828`, `#2878dc`).
6. **Operating point** default `score_threshold: 0.3`. Recommendation: 0.3, overridable per run.
7. **Visibility.** Keep every GT box by default (`ignore_gt_visibility_below: null`). Griffin stores visibility; its semantics were not measured. Recommendation: null until a visibility histogram has been looked at.
8. **Compare UX.** Overlay plus a delta table first; split viewport later if overlay is unreadable. Recommendation: overlay.
9. **Import formats.** JSON lines and Parquet only in step A. Recommendation: yes; a KITTI-style text importer waits for a real need.
10. **Where to get a first real run.** A single-agent detector on Griffin-25m vehicle lidar from Adarsh's code, written with `write_run`, so the viewer is checked on real output before step B. Recommendation: plan it as the acceptance check of this phase, run on the HPC by Adarsh.
