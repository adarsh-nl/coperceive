# Evaluate

`coperceive predictions import` puts a model's boxes next to a directory of schema 1 scenes. `coperceive eval run` matches them to the ground truth of each scene and writes a match table and a metrics file. `coperceive eval report` and `coperceive eval compare` print those files as one Markdown table. The viewer and the bird's-eye figure read the same two files. The contract is [the predictions spec](spec/predictions.md); the decision is [ADR 0010](adr/0010-eval-predictions.md). Nothing is written into a scene directory.

```bash
coperceive predictions import boxes.jsonl --run det-v1 --root /path/scenes --score-note "sigmoid of the class logit"
coperceive eval run det-v1 --root /path/scenes
coperceive eval report det-v1 --root /path/scenes
coperceive eval compare det-v1 det-v2 --root /path/scenes
```

## Format

Predictions are a sidecar root keyed by run and scene:

```text
<predictions root>/
  <run_id>/
    run.json
    <scene_id>/
      boxes.parquet          the model's boxes
      matches.parquet        written by eval run
      metrics.json           written by eval run
```

The predictions root is `<scenes root>/predictions` when that directory holds a child with `run.json`. `--predictions` names another directory, for `predictions import`, `eval`, `export bev` and `serve`. A run id and a scene id follow the scene-id rule: not empty, not `.` or `..`, no slash or backslash, no leading dot. Every path a command opens is resolved and has to stay under the predictions root.

`run.json` says what the run is: `format` (`coperceive-predictions`), `format_version` (`1`), `run_id`, `model`, `created_utc`, `score_note`, `categories`, `sources`, and an optional `notes`. `score_note` is required and says what `score` is, in the same way `clock.scale_note` says what a timestamp is. `categories` lists every class the model can emit, in the ground-truth vocabulary, so a class with ground truth and no prediction still gets an AP of 0. `sources` maps each `source` value to a one-line description; `single` and `fused` are the conventional names. A fused output is not a separate agent. It is `agent_id = vehicle, source = fused`, in that agent's frame.

`boxes.parquet` has one row per box: `agent_id`, `source`, `time_us`, `category`, `score`, `track_id` (nullable), `cx`, `cy`, `cz`, `length`, `width`, `height`, `qw`, `qx`, `qy`, `qz`, `frame`. `time_us` has to be a pose time of that agent; `frame` is `body` or `world`; the quaternion is a unit quaternion within 1e-6; every number is finite. Extra columns are ignored. The full table with Arrow types is in [the spec](spec/predictions.md#boxesparquet).

## Import

`coperceive predictions import <file> --run <run_id> --root <scenes> --score-note TEXT` reads a JSON lines file (one object per box with the column names above) or a Parquet file with those columns. A `scene_id` column names the scene per row. Without it, `--scene` names the one scene for every row. `--model` is free text. `--categories a,b` replaces the default, which is the set of categories seen. `--source-note NAME=TEXT` gives a source its description; a source without one gets `imported`. `--predictions` writes under another root. `--force` overwrites a scene that already has `boxes.parquet`.

`--scene` and every `scene_id` value pass the scene-id rule before anything under the scenes root is opened. Then every row is checked against its scene: the agent exists, `time_us` is a pose time of that agent, `source` is a known source, the numbers are finite, the quaternion is a unit quaternion, `frame` is `body` or `world`. The first failing row is reported with its file row number, in one line on stderr, exit 1, and nothing is written. After the checks the command writes `run.json` and one `boxes.parquet` per scene and prints `wrote <path>` and `scenes N rows M`.

A training script can skip the file: `coperceive.evaluation.predictions.write_run(root, run_id, meta, rows)` takes a `RunMeta` and an iterable of `PredictionBox` (each with a `scene_id`), groups them by scene and writes the same layout. The same validation runs.

## Config

`eval run --config eval.yaml` reads a YAML file. Without one these defaults apply, and the config in use is copied into `metrics.json` under `config`, with every key present, so a number is never read without the rules that produced it.

```yaml
criterion: iou_bev                   # iou_bev | centre
thresholds: [0.5, 0.7]               # IoU for iou_bev, metres for centre
display_threshold: 0.5               # one of thresholds; the one statuses are stored at
class_thresholds: {}                 # class -> list of the same length, e.g. {pedestrian: [0.25, 0.5]}
ignore_gt_visibility_below: null     # a number drops ground truth below it; null visibility is kept
max_range_m: null                    # a number drops ground truth and predictions farther from the agent body origin
score_threshold: 0.3                 # the operating point for statuses and per-sample counts
ranking_scope: per_frame             # per_frame | pooled: the order of the AP walk
gt_scope: connected_union            # connected_union | ego_only: the ground truth of a fused output
comm_range_m: 70.0                   # metres, horizontal: the range of connected_union
protocol: default                    # default | legacy: see "Reproduce v0.1.1 numbers"
```

`thresholds` are strictly increasing for `iou_bev` and strictly decreasing for `centre`. With `criterion: centre` the `thresholds` key is required: the default list is IoU values. An unknown key, a `display_threshold` that is not one of `thresholds`, or a class list of the wrong length is an error that names the key.

### Reproduce v0.1.1 numbers

v0.1.2 scores the same predictions under another protocol than v0.1.1, so its AP differs (see the changelog). One switch brings back the old rules, in the config or on the command line:

```yaml
protocol: legacy                     # ranking_scope: pooled, gt_scope: ego_only
```

```bash
coperceive eval run <run_id> --root <scenes> --protocol legacy
```

`eval report` then heads the tables with `protocol: legacy (pooled, ego-only)`, the label a run evaluated by v0.1.1 has, and `eval compare` sets the two side by side. On the demo run (`coperceive demo create`), every count, precision, recall and AP of `metrics.json` and of `eval report` equals v0.1.1's on the same Python version. A class mean (`mean`, `mean_model`) can differ in the last bit between Python 3.11 and 3.12 or later, in v0.1.1 as in v0.1.2, because Python 3.12 changed how `sum` adds floats; the test recomputes each mean from v0.1.1's per-class APs with the running Python's `sum`. `tests/test_eval_legacy_protocol.py` checks this against values written by the v0.1.1 code. It checks the same for the cooperative test scene, which has a `fused` output.

The switch restores the protocol, not the defects v0.1.2 fixed. These still differ from v0.1.1 under `protocol: legacy`:

- the BEV IoU of a pair, in the last bits (`tp_errors.iou_bev`; at most 6e-16 on both test runs);
- the bottom `all` row of `eval report` and `eval compare`, which counts `recovered` and `lost_by_fusion` once per agent and merges the outputs in a fixed order. On the cooperative test scene its mAP is 0.343 where v0.1.1 printed 0.310, and `recovered` is 1 where it printed 2;
- every AP of `eval compare --by`, now the AP of the bin;
- the ground truth of scenes and agents with no prediction, which now counts.

## Matching rules

Matching is done per scene, per output (an `(agent_id, source)` pair of the run, evaluated in every covered scene where its agent exists), per pose time, per category. The ground truth is that agent's own annotations at that time, except for a `fused` output under the default `gt_scope: connected_union`, which is scored against the union of the connected agents' observed boxes ([What the evaluator computes](eval-spec.md), A.4). Everything is placed in the world frame first: a `body` box with the agent pose at `time_us`, with no interpolation. The footprint of a box is its rectangle in the world XY plane, rotated by the yaw of its world rotation; roll and pitch are ignored. BEV IoU is the area of the intersection of two footprints over the area of their union. Centre distance is the horizontal distance between the two centres.

In words:

1. A ground-truth box is ignored when `ignore_gt_visibility_below` is set and its visibility is a number below it, or when `max_range_m` is set and its centre is farther than that from the agent body origin at that time, measured horizontally (z does not count; a box exactly at the limit is kept). A prediction farther than `max_range_m` is ignored before anything else, including the score check.
2. Predictions are taken by score, highest first. Equal scores keep file order, so the same input gives the same result every time.
3. Each prediction looks at the ground-truth boxes of its own category that nobody has taken yet, ignored ones included, and picks the best one: the largest BEV IoU or the smallest centre distance. The threshold is the class's entry in `class_thresholds`, or else the global one, at the index of `display_threshold`. If the best box passes the threshold and is not ignored, the pair is a `tp` and the box is taken. If it passes and the box is ignored, the prediction is `ignored` and the box stays free. Otherwise the prediction is an `fp`.
4. A ground-truth box that is not ignored and was not taken is a `miss`.
5. Categories never cross. A pedestrian predicted as a cyclist is one `fp` and one `miss`.
6. Statuses and per-sample counts use predictions with `score >= score_threshold`. A prediction below it is written with status `below_threshold` and takes no box.
7. AP is computed at every entry of `thresholds`, over every prediction regardless of `score_threshold`, by running steps 2 to 4 again at that threshold.

The normative cases, with the numbers a test has to reproduce, are in [the spec](spec/predictions.md#matching) and [its geometry section](spec/predictions.md#geometry).

## What `eval run` writes

`matches.parquet` has one row per prediction and one per ground-truth box of every output: `kind` (`gt` or `prediction`), the output and time, `status`, `score`, `pair_id` shared by a `tp` and its ground truth, `iou_bev`, `centre_error_m` and `yaw_error_deg` of the pair, the box in the world frame, on ground-truth rows the cooperative columns `status_single`, `status_fused`, `seen_by_other_gt`, `other_kind`, `other_agent_tp`, and on prediction rows one `status_at_<i>` per entry of `thresholds`: the status of the AP pass at that threshold (`tp`, `fp` or `ignored`), which is what the pooled AP of `eval report` is built from.

`metrics.json` has the config and, per output: `samples` (one row per pose time with `tp`, `fp`, `miss`, `ignored_gt`, `precision`, `recall`), `totals` (the sums plus `gt` and `predictions`), `ap` per threshold per category with a `mean` over the categories that have one, `tp_errors` (centre, yaw, size and IoU over the true positives at the operating point), and `cooperative` counts. The categories in `ap` are the union of the run's `categories`, the categories in the agent's ground truth and the categories the output predicted. A category with no ground truth in the scene, whether listed or only predicted, has AP `null` and does not enter the mean; a category with ground truth and no prediction has `0.0`. Absent values are `null`, never `NaN`.

The cooperative counts are computed per box of the ground-truth set of agent X's `fused` output: X's own boxes and, under the default `connected_union`, the boxes connected agents added, which have no `single` status. For each box they use its status under X's `single` and `fused` outputs and whether another agent annotates the same object at that time (a shared track id, or else a computed pair whose 3D centre distance is within 2 m, or within half the longer box length when that is more). "Not found alone" means `miss` under `single`, or no `single` status because another agent added the box. The counts: `recovered` (not found alone, `tp` fused), `lost_by_fusion` (`tp` alone, `miss` fused), `only_other_agent` (not found alone, and the other agent's `single` output has a `tp` on it), `nobody` (not found alone, `miss` fused, and no other agent has it), and `recovery_rate = recovered / (recovered + not found alone and miss fused)`. A box ignored under either output counts in none of them ([What the evaluator computes](eval-spec.md), A.9). The four counts are the same numbers on the `single` and the `fused` output of one agent.

## CLI

`coperceive eval run <run_id> --root <scenes> [--predictions DIR] [--config FILE] [--scenes a,b]` evaluates every scene of the run that has `boxes.parquet`, or the listed ones, and prints `evaluated N skipped M errors E`. A scene that fails prints one `error\t<scene>: <reason>` line after that and the command exits 1 once every scene has been tried. A class that appears in a scene's ground truth or predictions but not in `run.json` `categories` is evaluated anyway and prints one `warning\t<scene>: category '<name>' is not in run.json categories` line; warnings do not change the exit code. A missing predictions root, an unknown run or a missing config file is one line on stderr and exit 1.

`coperceive eval report <run_id> --root <scenes>` first prints the protocol the run was scored under (`protocol: ranking per_frame, ground truth connected_union (70 m)`, or `protocol: legacy (pooled, ego-only)` for a run evaluated before 2026-11-16), then one Markdown row per scene and output: `scene`, `agent`, `source`, `gt`, `predictions`, `tp`, `fp`, `miss`, `precision`, `recall`, per threshold `mAP@<t> (BEV) all` and `mAP@<t> (BEV) model` (every AP is a bird's-eye-view AP; under `criterion: centre` the label is `m (centre)`) (on a scene row, that scene's mean over the classes with ground truth and over the `run.json` classes), `recovered`, `lost_by_fusion`. Then come the headline rows, labelled `all`: one per output and a last one over every output. They sum the counts, recompute precision and recall from the sums, and take `mAP@<t>` from the **pooled** curve: every prediction of every scene of the output in one precision/recall walk per class, with the ground-truth count summed over scenes, then the mean over the classes that have ground truth. The order of that walk is the `ranking_scope` of the eval config. The default, `per_frame`, takes each scene's predictions frame by frame, by score within a frame, with no sort over the whole list: OpenCOOD, whose evaluation produced the published OPV2V numbers, sorts detections only within each frame by default, joins the frames without a sort and builds one curve over the test set, and its README says the OPV2V paper did not perform the global sort ([OpenCOOD README](https://github.com/DerrickXuNu/OpenCOOD), "Test the model"; the per-frame sort is the behaviour of OpenCOOD at commit `31ba160` as read by a separate session, [What the evaluator computes](eval-spec.md) A.5). **With `per_frame` the AP depends on the order of the frames**, as in OpenCOOD: the same detections visited in another frame order can give another AP. The order here is fixed and does not depend on file order: scenes by `scene_id` (plain string order), then sample time, earliest first; within one time, by score, then `agent_id`, `source` and row order. Whether this is the order in which OpenCOOD's test loader visits a dataset's frames is unverified, so a number can differ from OpenCOOD's on the same detections for that reason alone. `pooled` does not depend on frame order, except through equal scores. `pooled` sorts every prediction once, as KITTI does over its split ([Simonelli et al. 2019](https://arxiv.org/abs/1905.12365), Sec. 1). A `fused` output is scored against the boxes of every agent within `comm_range_m` (70 m) that a connected agent's LiDAR hits, following OPV2V and V2X-ViT, with the choices their papers leave open listed in [What the evaluator computes](eval-spec.md), A.4 (`gt_scope: connected_union`); `gt_scope: ego_only` scores it against its own agent's boxes. `eval compare` refuses two runs scored under different protocols. See [What the evaluator computes](eval-spec.md), A.2, A.4 and A.5. The pooled value is not the mean of the per-scene values (two scenes with APs 1.0 and 0.5 can pool to 0.833, see [the spec](spec/predictions.md#pooled-ap)). A second table lists the pooled AP per class and output, `n/a` for a class without ground truth. `recovered` and `lost_by_fusion` belong to an agent, not to an output, so the last `all` row counts them once per scene and agent. A run that has not been evaluated exits 1 with a line that says to run `eval run`, and so does a run whose scenes were evaluated with different configs: one headline needs one rule set. What every number means, and how it compares with the benchmarks' own definitions, is in [What the evaluator computes](eval-spec.md).

`coperceive eval compare <run_a> <run_b> [run_c] [run_d] --root <scenes>` compares two, three or four runs. With exactly two runs and no `--by`, the Markdown is the `b − a` table above: every row of either run is listed, a scene one run has not evaluated shows `n/a` across, as does a value that is `null` on either side, the `all` rows and the per-class table compare the shared rows only, and a footer counts the rows (`rows: 3 shared, 1 only in det-v1, 0 only in det-v2`). Three or four runs print one summary of each run's pooled totals and then each run's report.

`--by distance,occlusion,agents,class,scene` adds a breakdown section. `--json path` writes `compare.json` (schema [compare.schema.json](spec/compare.schema.json), format `coperceive-compare` version 1) and does not change the two-run Markdown on stdout. `--from-scores` reads `outcomes.json` written by `eval run`, or the match table alone when that file is absent, and does not reopen the scene. `--distance-bins 0,30,50` and `--visibility-bins 0.2,0.6` are the defaults.

Distance is the horizontal distance from the reference agent — the first agent in the scene, the same agent `CooperativeDataset` uses when no agent list is passed — to the box centre at that time. The default bins are `0-30m`, `30-50m` and `50m+`. Occlusion is the ground-truth `visibility` fraction: below 0.2 is `heavy`, below 0.6 is `partial`, and the rest of `[0, 1]` is `clear`. A null visibility is `unknown` and is never given a name. A number outside `[0, 1]` keeps the label `visibility:<value>`. The agent count is how many agents annotate the object at that time, including the annotating agent. The pairing is the cooperative gate (a shared track, or a computed pair within 2 m). When only the match table is available, a box another agent sees is `2+` rather than an exact count.

Nothing is matched again inside a bin: the AP pass is filtered ([What the evaluator computes](eval-spec.md), A.11). A true positive counts in the bin of the ground-truth box it was paired with in that pass, and is ignored in every other bin, neither a true nor a false positive there. A false positive counts in its own distance, class and scene bin, and in every occlusion and agent-count bin, since an unmatched box has no visibility or agent count of its own. Average precision is then the walk of `eval report` on what is left, in the order of the run's `ranking_scope`, so a bin's AP lies in `[0, 1]`. A bin with no ground truth says `no objects` and its AP is null. It is not 0. Every AP is shown with `n_objects`, the number of non-ignored ground-truth boxes in the bin.

The per-object table in `compare.json` uses a ground-truth key `gt:<scene>:<agent>:<time_us>:<annotation row>` that does not include the run, and a false-positive key `fp:<run>:<scene>:<agent>:<source>:<time_us>:<prediction row>`. Each ground-truth object carries `tp`, `fn` or `ignored` per run and output, with the pair's IoU and centre error. `eval run` writes `outcomes.json` beside `metrics.json` so the desktop can take the fast path.

`coperceive eval failures <run_id> --root <scenes> [--top N] [--json [PATH]] [--weights FILE] [--latency-run RUN]` ranks frames. A frame is one output at one scene time. Its badness is the sum, over operating-point false negatives and false positives, of `class_weight(category) * distance_weight(distance_m)`. Vulnerable classes (pedestrian, cyclist, bicycle, motorcycle, rider) weigh 2 by default and every other class weighs 1. Distance weighs 2 under 20 m, 1 from 20 m up to 50 m, 0.5 at 50 m and beyond, and 1 when the distance is unknown. Ignored boxes and predictions under the score threshold add nothing. The worst frame is rank 1. Equal badness is broken by scene, time, agent and source, so the order does not move between runs.

Tags are rules. `missed_vru` is a vulnerable false negative within 20 m. `ghost_box` is a false positive more than 2 m from every ground-truth centre. `duplicate_boxes` is a false positive with bird's-eye IoU at least 0.5 against a true positive of the same class. `localisation_error` is a true positive whose centre error is at least 1 m or whose yaw error is at least 15°. `far_range_miss` is a false negative at 50 m or beyond. `occluded_miss` is a false negative whose visibility is a number below 0.6; a null visibility is not occluded. `fusion_disagreement` is a box the single output matched and the fused output missed, and it is attached to the fused frame only when both statuses exist. `latency_sensitive` is a true positive here that the named `--latency-run` missed, and that run must record `latency_frames` or `latency_ms`. Without the flag the tag is absent. `--weights` replaces any of these numbers. The file is `failures.json`, schema [failures.schema.json](spec/failures.schema.json).

On the synthetic breakdown scene, run `det-a` misses a pedestrian at 40 m with visibility 0.1 and a car at 60 m, and adds a false positive at 80 m. That one frame has badness 3: the pedestrian contributes 2 (class 2, distance 1), and the far miss and the ghost each contribute 0.5. Its tags are `ghost_box`, `far_range_miss` and `occluded_miss`. The pedestrian is outside 20 m, so it is not `missed_vru`.

## Viewer

```bash
coperceive serve --root /path/scenes --predictions /path/predictions
```

`--predictions` is optional when the runs are under `<root>/predictions`. The page's Predictions block lists the runs that have the scene, draws the match rows of the chosen run at the current sample in the status colours, filters them by source, status, cooperative view and score, puts precision, recall and the FP count of the chosen output on the timeline, and can overlay a second run. The block is described in [the viewer page](viewer.md); the three routes behind it are in that page's HTTP table.

## Figures

```bash
coperceive export bev /path/scenes/Town03-000 --out /path/bev.svg --time-us 1118500000 --run det-v1
```

`--run` draws the match rows of that evaluated run at the sample on top of the ground-truth boxes. `--predictions` names the root when it is not `<scene>/../predictions`. The styles are the ones in [the spec](spec/predictions.md#colours-and-styles): `tp` solid teal `#1FA3A3`, `fp` dashed amber `#E0A431`, `miss` dotted violet `#8E6CEF` and thicker, `ignored` and `below_threshold` thin grey `#6B6B6B` at half opacity. The legend names each status in words, and a ground-truth box that was matched is dimmed to 40 % so the `tp` wire reads on top. The command prints the counts it drew, ending in `predictions N`. The same figure is the bird's-eye export button on the page.

## Repro card

`coperceive repro export` writes one JSON file that describes a run: the dataset and split, the pipeline and its arguments, the checkpoint by hash and file name, the code commit, the environment, the scheduler request, and the scores `coperceive eval run` wrote. A pipeline's own metrics file is attached only as a non-authoritative report. The contract is [the repro card spec](spec/repro-card.md) and [its schema](spec/repro-card.schema.json).

```bash
coperceive repro export det-v1 --root /path/scenes --out card.json
coperceive repro check card.json --run-dir /path/scenes/predictions/det-v1
```

Run history has the same export. Fields that were not recorded are `null` and named in `unknown`; the card does not guess them.

## What was checked

Everything on this page has run on synthetic fixtures only. There is no real model output in the repository yet, and no number from a real detector has been through `eval run`.

- `tests/test_eval_geometry.py` pins the normative IoU, centre distance and yaw error values of the spec.
- `tests/test_eval_config.py` pins the defaults and the config errors.
- `tests/test_eval_matching.py` and `tests/test_eval_metrics.py` pin the normative matching and AP cases, equal-score ordering, the ignored and below-threshold statuses, and a `metrics.json` with no `NaN` and an AP of 0 for a listed class without predictions.
- `tests/test_eval_predictions.py` covers the format, validation (including a time that is a pose of a different agent), `write_run` and the scene-id rule.
- `tests/test_eval_run.py`, `tests/test_eval_cli.py`, `tests/test_evalview.py` and the prediction tests in `tests/test_app.py` cover `eval run|report|compare`, `predictions import`, the three routes, path traversal and the case of a run that was imported but not evaluated.
- `tests/test_eval_end_to_end.py` runs the whole chain on the cooperative fixture: scene, JSON lines, `predictions import`, `eval run`, the three routes, the bird's-eye export with a run, `eval compare`, and checks that the frame rows at 100000 µs count the same `tp`, `fp` and `miss` as the `metrics.json` sample row.
- `tests/test_model_end_to_end.py` runs the model plugin chain (`coperceive model list|inspect|check|run`, cache hit, `eval run|report|compare`, `model cache prune`, and `model run --slurm`) on the cooperative fixture.

Real detector runs on the ten Griffin-25m validation scenes (`vehicle-side`, `drone-side`, `instance-fusion`, and `early-fusion`) are produced through `coperceive model run griffin` and evaluated under BEV IoU and centre-distance configs; see [Run a model](models.md) and [ADR 0011](adr/0011-live-model-plugin.md).

## Class vocabulary

A model usually emits fewer classes than a dataset labels. `eval run` reports two means per threshold: `mAP@<t> all` over every class with ground truth, and `mAP@<t> model` over the classes listed in `run.json`. When the dataset's own toolkit maps classes before training, do the same with `gt_class_map` in the eval config so the two sides agree; for Griffin, the Griffin toolkit does this in code: `tools/griffin_data_converter/trans_kitti2nuscenes.py`, `obj_type_mapping` (`"truck": "car"`, `"bus": "car"`, `"motorcycle": "bicycle"`), and `projects/mmdet3d_plugin/datasets/griffin_dataset.py`, `class_names_nuscenes_mappings` (`'Truck': 'car'`, `'Bus': 'car'`), commit `9c02ba4a`. Predictions are never renamed. A class the ground truth has and `run.json` lacks is printed as a `warning` line by `eval run`.
