# Predictions and evaluation, format 1

This is the contract for model output next to schema 1 scenes, and for the match table and metrics file `coperceive eval run` writes. The decision is [ADR 0010](../adr/0010-eval-predictions.md). Schema 1 is unchanged: nothing here is written into a scene directory.

Every rule a result depends on is in this document or in the config copied into `metrics.json`. A reader that has `metrics.json` can say which rules produced a number.

## Layout

```text
<predictions root>/
  <run_id>/
    run.json
    <scene_id>/
      boxes.parquet          written by the model or by `coperceive predictions import`
      matches.parquet        written by `coperceive eval run`
      metrics.json           written by `coperceive eval run`
```

The predictions root is `<scenes root>/predictions` when that directory contains a child with `run.json`. `--predictions` overrides it for `eval`, `predictions import`, `export bev` and `serve`. A `run_id` and a `scene_id` follow the scene-id rule: non-empty, not `.` or `..`, no slash or backslash, no leading dot. Every path a reader opens is resolved and must stay under the predictions root.

## `run.json`

One UTF-8 JSON object.

| Key | Type | Meaning |
| --- | --- | --- |
| `format` | string | `coperceive-predictions`. |
| `format_version` | int | `1`. |
| `run_id` | string | Equal to the directory name. |
| `model` | string | Free text: name, checkpoint, commit. Not interpreted. |
| `created_utc` | string | ISO 8601 with a `Z` or an offset. |
| `score_note` | non-empty string | What `score` is (sigmoid, calibrated probability, logit, …). Required, like `clock.scale_note`. |
| `categories` | list of string | Every class the model can emit, in the ground-truth vocabulary. A class listed here with no prediction still gets a row in the metrics. A class in the evaluated ground truth or in the predictions that is not listed here is still evaluated; `eval run` prints one `warning\t<scene_id>: category '<name>' is not in run.json categories` line per scene and class. |
| `sources` | object string → string | Each `source` value the run uses, with a one-line description. `single` and `fused` are the conventional names. |
| `notes` | string, optional | Free text. |

Unknown keys are ignored. A missing required key, a different `format` or `format_version`, or a `run_id` that does not match the directory is an error.

## `boxes.parquet`

One row per predicted box. Arrow types as in schema 1. Extra columns are ignored. A missing column from this list is an error.

| Column | Type | Null | Meaning |
| --- | --- | --- | --- |
| `agent_id` | string | no | The agent whose output this is. Must be an agent of the scene. |
| `source` | string | no | Which pipeline on that agent produced the box. Must be a key of `run.json` `sources`. |
| `time_us` | int64 | no | A pose time of that agent. |
| `category` | string | no | Class label in the ground-truth vocabulary. |
| `score` | float64 | no | Confidence, finite. Higher is more confident. |
| `track_id` | string | yes | Tracker id, or null. |
| `cx`, `cy`, `cz` | float64 | no | Box centre, metres, in `frame`. |
| `length`, `width`, `height` | float64 | no | Box size, metres, positive. |
| `qw`, `qx`, `qy`, `qz` | float64 | no | Pure rotation of the box in `frame`. Unit norm within 1e-6. |
| `frame` | string | no | `body` (the agent body at `time_us`) or `world`. |

Validation (`coperceive predictions import`, `coperceive eval run`, and `write_run`): every row is checked against the scene before anything is written or matched; the first failing row is reported with its row number.

### Import

`coperceive predictions import <file> --run <run_id> --root <scenes> [--scene <scene_id>] [--predictions <root>] [--model TEXT] --score-note TEXT [--categories a,b] [--source-note NAME=TEXT ...]`

`<file>` is JSON lines (one object per box with the column names above) or a Parquet file with those columns. An optional `scene_id` column names the scene per row; without it `--scene` is required. The command validates every row, writes `boxes.parquet` per scene and `run.json`, and refuses to overwrite a scene that already has `boxes.parquet` unless `--force`. `categories` defaults to the set of categories seen; `sources` defaults to the set of sources seen with the note `imported`.

`coperceive.evaluation.predictions.write_run(root, run_id, meta, rows)` is the same for a training script: `meta` is a `RunMeta`, `rows` an iterable of `PredictionBox`, grouped by scene by the function.

## Evaluation config

`coperceive eval run` reads a YAML file (`--config`), or uses these defaults. The parsed config is copied into `metrics.json` under `config`, with every key present.

```yaml
criterion: iou_bev                   # iou_bev | centre
thresholds: [0.5, 0.7]               # IoU for iou_bev, metres for centre; strictly increasing for iou_bev, strictly decreasing for centre
display_threshold: 0.5               # must be one of thresholds; statuses in matches.parquet use it
class_thresholds: {}                 # class -> list, same length as thresholds, e.g. {pedestrian: [0.25, 0.5]}
ignore_gt_visibility_below: null     # null, or a number: GT with visibility below it is ignored; GT with null visibility is kept
max_range_m: null                    # null, or metres from the agent body origin at that time; GT and predictions farther are ignored
score_threshold: 0.3                 # operating point for statuses and per-sample counts
```

Unknown keys are errors. `display_threshold` not in `thresholds` is an error.

## Geometry

All matching is in the scene world frame. A `body` box is placed with the agent pose at `time_us` through `lookup_pose` with `max_gap_us = 0`; a time that is not a pose sample is an error at validation.

**Yaw** of a world rotation `R` is the yaw of `R = Rz(yaw) @ Ry(pitch) @ Rx(roll)`, as `coperceive.validate` computes it.

**Footprint** of a box: the rectangle in the world XY plane with centre `(cx, cy)`, extent `length` along the box x axis and `width` along the box y axis, rotated by the yaw. Roll and pitch are ignored.

**BEV IoU** of two boxes: the area of the intersection of the two footprints divided by the area of their union. The intersection of two convex quadrilaterals is computed by clipping one against each edge of the other (Sutherland–Hodgman); its area by the shoelace formula. A union of area 0 gives IoU 0.

**Horizontal centre distance** is `hypot(cx_a - cx_b, cy_a - cy_b)` in the world frame.

**Yaw error** of a pair is the absolute difference of the two yaws wrapped into `[0, π]`, in degrees.

Normative values, all in metres:

- Two identical 4 × 2 boxes: IoU `1.0`.
- A 4 × 2 box at the origin and the same box at `x = 2`: intersection 4, union 12, IoU `1/3`.
- A 4 × 2 box and a 2 × 4 box, both at the origin, both yaw 0: IoU `1/3`.
- A 4 × 2 box at yaw 0 and the same box at yaw 90°: the footprints are a 4 × 2 and a 2 × 4 rectangle, IoU `1/3`.
- A 4 × 2 box at the origin and a 4 × 2 box at `x = 4`: they touch along an edge, intersection 0, IoU `0.0`.
- A 4 × 2 box at the origin and a 1 × 1 box at `(0.5, 0.25)`: intersection 1, union 8, IoU `0.125`.
- Centre distance between boxes at `(0, 0, 0)` and `(3, 4, 10)` is `5.0`; `z` does not count.
- Yaw error between 10° and −10° is `20°`; between 170° and −170° is `20°`.

## Matching

Done per scene, per `(agent_id, source)` pair that appears in `boxes.parquet` (an **output**), per `time_us`, per `category`. Ground truth is the agent's own `annotations` at that time.

1. Ground-truth boxes are **ignored** when `ignore_gt_visibility_below` is a number and the box's visibility is a number below it, or when `max_range_m` is a number and the box centre is farther than that from the agent body origin at that time. Predictions farther than `max_range_m` are ignored before matching.
2. Predictions are sorted by `score` descending; equal scores keep file order.
3. Each prediction in turn considers the ground-truth boxes of its category that are not yet matched, ignored ones included, and takes the best one under the criterion: the largest BEV IoU, or the smallest centre distance. The pair's threshold is the class's entry in `class_thresholds` or else the global `thresholds` entry, at the index of `display_threshold`.
   - If the best box is not ignored and passes the threshold (IoU `>=`, distance `<=`): **tp**, and the box is matched.
   - If the best box is ignored and passes the threshold: the prediction is **ignored**; the box stays unmatched.
   - Otherwise: **fp**.
4. A ground-truth box that is not ignored and was not matched is a **miss**.
5. Categories never cross: a pedestrian predicted as a cyclist is one `fp` and one `miss`.
6. Statuses and per-sample counts use predictions with `score >= score_threshold`; a prediction below it is written with status `below_threshold` and does not take a box.
7. AP is computed separately at every entry of `thresholds`, over every prediction regardless of `score_threshold`, by running steps 2–4 with that threshold.

Normative cases, one agent, one sample, criterion `centre`, thresholds `[2.0, 1.0]`, display 2.0, score threshold 0.3:

- One GT car at `(0, 0)`, one prediction car at `(1.5, 0)` score 0.9: at 2.0 `tp`; at 1.0 `fp` and the GT is a `miss`.
- One GT car, two predictions on it (0.9 at 0.2 m, 0.8 at 0.4 m): one `tp` (the 0.9) and one `fp`.
- One GT car, one prediction `truck` on top of it: `fp` and `miss`.
- One GT car with visibility 0.1, `ignore_gt_visibility_below: 0.5`, one prediction car on it: prediction `ignored`, GT `ignored`, counts all zero.
- One GT car, one prediction car at 0.1 m with score 0.2: prediction `below_threshold`, GT `miss` at the operating point; AP at 2.0 is still `1.0`.

## Metrics

For one output (an `(agent_id, source)` pair):

- **Per sample** (`samples[]`, one row per pose time of the agent, in time order): `time_us`, `tp`, `fp`, `miss`, `ignored_gt`, `precision = tp / (tp + fp)` (null when the denominator is 0), `recall = tp / (tp + miss)` (null when 0).
- **Totals**: the sums over samples, plus `gt` (non-ignored ground-truth boxes) and `predictions` (all rows of the output), and the same `precision` and `recall` from the sums.
- **AP** per threshold per category, with `mean` over the categories that have an AP:
  1. Take every prediction of that category in the scene with its status at that threshold (steps 2–4 above, ignoring `score_threshold`), sorted by score descending, ties in file order.
  2. Walk the list accumulating `tp_i` and `fp_i`; `recall_i = tp_i / n_gt`, `precision_i = tp_i / (tp_i + fp_i)`; ignored predictions are skipped.
  3. Make the precision envelope monotone from the right: `p'_i = max(precision_j for j >= i)`.
  4. `AP = Σ_i (recall_i − recall_{i−1}) · p'_i` with `recall_0 = 0`.
  5. The categories of the table are the union of `run.json` `categories`, the categories of the agent's ground truth and the categories the output predicted. A category with `n_gt = 0` in the scene (listed or predicted only) has AP `null` and does not enter the mean; it is never `0`. A category with ground truth and no prediction has AP `0.0`.
- **`tp_errors`** over the true positives at the operating point: `centre_m` (`mean`, `median`), `yaw_deg` (`mean_abs`), `size_m` (`length`, `width`, `height`, each the mean absolute error), `iou_bev` (`mean`). All null when there is no true positive.
- **`cooperative`** counts (below).

Normative AP values:

- One GT, one `tp` at any score: `1.0`.
- One GT, one `fp` with a higher score than the one `tp`: `0.5`.
- One GT, one `tp` with a higher score than one `fp`: `1.0`.
- Two GT, one `tp`, no `fp`: `0.5`.
- A category with ground truth and no prediction: `0.0`; a category with no ground truth: `null`.

### Pooled AP

The AP in `metrics.json` is per scene and is a diagnostic. The headline AP of a run, per output `(agent_id, source)` and per class, pools every detection across scenes the way OpenCOOD and KITTI do: one precision/recall curve per class over the predictions of every evaluated scene of that output, with `n_gt` summed over the scenes.

1. Take, for every evaluated scene in scene-id order, the prediction rows of the output and the category from `matches.parquet` with their `status_at_<i>` (below) for the threshold, in `boxes.parquet` row order; concatenate them. `n_gt` is the sum over those scenes of the output's non-ignored ground-truth boxes of the category (`gt` rows with a status other than `ignored`).
2. Run steps 2–5 of **AP** once over that list: sort by score descending with a stable sort (ties keep scene order, then file order), walk, envelope, area. A class whose pooled `n_gt` is 0 has no pooled AP (`null`, rendered `n/a`); a class with ground truth and no prediction has `0.0`.
3. The pooled **mAP** at a threshold is the mean over the classes that have a pooled AP. The classes are the union, over the pooled scenes, of the categories of their `ap` tables.
4. Pooling over several outputs (the `all` row of `eval report`) is the same rule over the rows of every output: `n_gt` and the records are summed over outputs too.

Normative case: two scenes, each with one ground-truth car. Scene A has one `tp` at score 0.9; scene B one `fp` at 0.8 and one `tp` at 0.7. Pooled, `n_gt = 2` and the walk is tp, fp, tp: precisions 1, 1/2, 2/3 at recalls 1/2, 1/2, 1; the envelope is 1 at recall 1/2 and 2/3 at recall 1; AP `= 1/2 · 1 + 1/2 · 2/3 = 5/6 ≈ 0.833`. The mean of the per-scene APs (1.0 and 0.5) would be 0.75; that number is not reported anywhere.

## Cooperative views

Computed per ground-truth box of agent X at time t, written on its `gt` row in `matches.parquet` and counted per output in `metrics.json`:

| Column | Meaning |
| --- | --- |
| `status_single` | The box's status under X's `single` output; null when the run has no `single` output for X. |
| `status_fused` | The same under X's `fused` output; null when absent. |
| `seen_by_other_gt` | True when another agent annotates the same object at t: a shared track id (`scene.objects`), else a `computed` pair from `coperceive.assoc.associate` with `max_center_m = 2.0`, `size_fraction = 0.5`, `max_gap_us = 0`. |
| `other_kind` | `shared_track`, `computed`, or null. |
| `other_agent_tp` | True when the other agent's `single` output has a `tp` on that other box at t. Null when there is no other box or no other `single` output. |

Counts in `cooperative` for an output `(X, source)`, over X's ground-truth boxes with a non-null `status_single` and `status_fused`:

- `recovered`: `status_single = miss` and `status_fused = tp`.
- `lost_by_fusion`: `status_single = tp` and `status_fused = miss`.
- `only_other_agent`: `status_single = miss` and `other_agent_tp` true.
- `nobody`: `miss` on every non-null status of X and `other_agent_tp` is not true.
- `recovery_rate = recovered / (recovered + boxes that are miss under both)`; null when the denominator is 0.

These four are the same numbers for the `single` and `fused` outputs of one agent; they are reported on both.

## `matches.parquet`

One row per prediction and one per ground-truth box of every evaluated output, in this order: outputs in the order they first appear in `boxes.parquet`, then time, then `gt` rows before `prediction` rows, then file order.

| Column | Type | Null | Meaning |
| --- | --- | --- | --- |
| `kind` | string | no | `gt` or `prediction`. |
| `agent_id`, `source`, `time_us`, `category` | as in `boxes.parquet` | no | |
| `row` | int64 | yes | For a prediction, its row index in `boxes.parquet`; for GT, its row index in `annotations.parquet`. |
| `score` | float64 | yes | Null for GT. |
| `track_id` | string | yes | |
| `visibility` | float64 | yes | GT only. |
| `status` | string | no | `tp`, `fp`, `miss`, `ignored`, `below_threshold` (predictions only). |
| `pair_id` | int64 | yes | Shared by a `tp` prediction and its GT; unique within the file. |
| `iou_bev`, `centre_error_m`, `yaw_error_deg` | float64 | yes | Of the pair; null otherwise. |
| `cx`, `cy`, `cz`, `length`, `width`, `height`, `qw`, `qx`, `qy`, `qz` | float64 | no | The box in the **world** frame. |
| `status_single`, `status_fused`, `other_kind` | string | yes | GT only. |
| `seen_by_other_gt`, `other_agent_tp` | bool | yes | GT only. |
| `status_at_<i>` | string | yes | Predictions only, one column per entry of `config.thresholds` (`i` is its index: `status_at_0`, `status_at_1`, …): the prediction's status in the AP pass at `thresholds[i]` (§Matching step 7, `score_threshold` ignored): `tp`, `fp` or `ignored`. Null on GT rows. These let a reader build the pooled curve from the files without re-matching. |

The spec columns above `status_at_<i>` come first and are the same for every config; a reader that asks for them alone (the viewer) sees the same table whatever `thresholds` is.

## `metrics.json`

```json
{
  "format": "coperceive-eval",
  "format_version": 1,
  "run_id": "det-v1",
  "scene_id": "Town03-000",
  "created_utc": "2026-10-17T12:00:00Z",
  "coperceive_version": "0.0.0",
  "config": { "criterion": "iou_bev", "thresholds": [0.5, 0.7], "display_threshold": 0.5,
              "class_thresholds": {}, "ignore_gt_visibility_below": null, "max_range_m": null,
              "score_threshold": 0.3 },
  "outputs": [
    {
      "agent_id": "vehicle",
      "source": "single",
      "samples": [ { "time_us": 1118500000, "tp": 40, "fp": 3, "miss": 15, "ignored_gt": 0,
                     "precision": 0.9302325581395349, "recall": 0.7272727272727273 } ],
      "totals": { "tp": 0, "fp": 0, "miss": 0, "ignored_gt": 0, "gt": 0, "predictions": 0,
                  "precision": null, "recall": null },
      "ap": { "0.5": { "car": 0.91, "pedestrian": null, "mean": 0.91 },
              "0.7": { "car": 0.62, "pedestrian": null, "mean": 0.62 } },
      "tp_errors": { "centre_m": { "mean": null, "median": null }, "yaw_deg": { "mean_abs": null },
                     "size_m": { "length": null, "width": null, "height": null }, "iou_bev": { "mean": null } },
      "cooperative": { "recovered": 0, "lost_by_fusion": 0, "only_other_agent": 0, "nobody": 0,
                       "recovery_rate": null }
    }
  ]
}
```

Threshold keys are the shortest `repr` of the float (`"0.5"`, `"2.0"`). Numbers are plain JSON numbers; absent values are `null`, never `NaN`.

`coperceive eval report <run_id>` prints, for a scenes root, two Markdown tables. The first has one row per scene and output (`scene`, `agent`, `source`, `gt`, `predictions`, `tp`, `fp`, `miss`, `precision`, `recall`, one `AP@<t>` column per threshold, `recovered`, `lost_by_fusion`), where `AP@<t>` is that scene's `ap.<t>.mean`; then one row labelled `all` per output (`all`, `agent`, `source`) and a last row `all` with empty `agent` and `source`. An `all` row sums the counts over its scenes (and, for the last row, over outputs), recomputes `precision` and `recall` from the sums, and takes each `AP@<t>` from **Pooled AP**: the pooled mAP over the pooled scenes of that output, or of every output for the last row. It is not a mean of the per-scene means. The second table, after a blank line, has `agent`, `source`, `class` and one `AP@<t>` per threshold: the pooled AP per class, per output and then for `all`; a class without pooled ground truth shows `n/a`. `coperceive eval compare <a> <b>` prints the same tables with `b − a` deltas.

## HTTP

All GET, read-only, under the same token and `root_path` rules as the other routes. Paths are confined to the predictions root.

| Path | Body |
| --- | --- |
| `/api/scenes/{scene_id}/predictions` | `{ "runs": [ { "run_id", "run": <run.json>, "evaluated": bool, "config": <config or null>, "outputs": [ { "agent_id", "source", "totals", "ap", "cooperative" } ] } ] }` — every run whose `<scene_id>/boxes.parquet` exists; `evaluated` is whether `metrics.json` exists. |
| `/api/scenes/{scene_id}/predictions/{run_id}/frame?time_us=&compare=` | `{ "run_id", "time_us", "display_threshold", "score_threshold", "rows": [ <row> ], "compare": <same object for the other run, or null> }`. A `<row>` is the `matches.parquet` row at that time plus `center: [x, y, z]`, `size_lwh: [l, w, h]`, `pose: {qw..tz}` and `lines: [...]` (the 24-vertex edge list the frame route already uses for boxes). 404 when the run or the scene's `metrics.json` is missing. |
| `/api/scenes/{scene_id}/predictions/{run_id}/series?compare=` | `{ "run_id", "outputs": [ { "agent_id", "source", "times_us": [], "tp": [], "fp": [], "miss": [], "precision": [], "recall": [] } ], "compare": <same or null> }`, straight from `metrics.json` `samples`. |

`POST /api/scenes/{scene_id}/export/bev` and `coperceive export bev` take an optional `run_id` (`--run`) and draw the prediction rows at that sample with the status colours and styles below, on top of the ground-truth boxes.

## Colours and styles

On the data layer, with bloom, like the ground-truth wires. Style is the primary cue; colour is the second; the legend names each status.

| Status | Colour | Line |
| --- | --- | --- |
| `tp` | `#1FA3A3` | solid |
| `fp` | `#E0A431` | dashed, 0.6 m dash, 0.3 m gap |
| `miss` (a GT row) | `#8E6CEF` | dotted, 0.15 m dash, 0.3 m gap, drawn 1.5× thicker |
| `ignored`, `below_threshold` | `#6B6B6B` | solid, thin, 50% opacity |

A matched GT box (`pair_id` set) is drawn at 40% opacity in its usual colour so the `tp` wire reads on top. `#DE3B21` stays the playhead and Play. In compare mode, run B uses the same colours with the dash pattern shifted by half a period and a `B` tag in the inspector.
