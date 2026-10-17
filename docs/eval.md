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
```

`thresholds` are strictly increasing for `iou_bev` and strictly decreasing for `centre`. An unknown key, a `display_threshold` that is not one of `thresholds`, or a class list of the wrong length is an error that names the key.

## Matching rules

Matching is done per scene, per output (an `(agent_id, source)` pair that appears in `boxes.parquet`), per pose time, per category. The ground truth is that agent's own annotations at that time. Everything is placed in the world frame first: a `body` box with the agent pose at `time_us`, with no interpolation. The footprint of a box is its rectangle in the world XY plane, rotated by the yaw of its world rotation; roll and pitch are ignored. BEV IoU is the area of the intersection of two footprints over the area of their union. Centre distance is the horizontal distance between the two centres.

In words:

1. A ground-truth box is ignored when `ignore_gt_visibility_below` is set and its visibility is a number below it, or when `max_range_m` is set and its centre is farther than that from the agent body origin at that time. A prediction farther than `max_range_m` is ignored before anything else, including the score check.
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

The cooperative counts are computed per ground-truth box of agent X from its status under X's `single` and `fused` outputs and from whether another agent annotates the same object at that time (a shared track id, or else a computed pair from `coperceive.assoc.associate` at 2 m): `recovered` (`miss` alone, `tp` fused), `lost_by_fusion` (`tp` alone, `miss` fused), `only_other_agent` (`miss` alone, and the other agent's `single` output has a `tp` on it), `nobody` (`miss` on every output and no other agent has it), and `recovery_rate = recovered / (recovered + miss under both)`. The four counts are the same numbers on the `single` and the `fused` output of one agent.

## CLI

`coperceive eval run <run_id> --root <scenes> [--predictions DIR] [--config FILE] [--scenes a,b]` evaluates every scene of the run that has `boxes.parquet`, or the listed ones, and prints `evaluated N skipped M errors E`. A scene that fails prints one `error\t<scene>: <reason>` line after that and the command exits 1 once every scene has been tried. A class that appears in a scene's ground truth or predictions but not in `run.json` `categories` is evaluated anyway and prints one `warning\t<scene>: category '<name>' is not in run.json categories` line; warnings do not change the exit code. A missing predictions root, an unknown run or a missing config file is one line on stderr and exit 1.

`coperceive eval report <run_id> --root <scenes>` prints one Markdown row per scene and output: `scene`, `agent`, `source`, `gt`, `predictions`, `tp`, `fp`, `miss`, `precision`, `recall`, one `AP@<t>` column per threshold (that scene's mean over categories), `recovered`, `lost_by_fusion`. Then come the headline rows, labelled `all`: one per output and a last one over every output. They sum the counts, recompute precision and recall from the sums, and take `AP@<t>` from the **pooled** curve: every prediction of every scene of the output in one precision/recall walk per class, with the ground-truth count summed over scenes, then the mean over the classes that have ground truth. That is how OpenCOOD and KITTI report AP; it is not the mean of the per-scene values (two scenes with APs 1.0 and 0.5 can pool to 0.833, see [the spec](spec/predictions.md#pooled-ap)). A second table lists the pooled AP per class and output, `n/a` for a class without ground truth. A run that has not been evaluated exits 1 with a line that says to run `eval run`.

`coperceive eval compare <run_a> <run_b> --root <scenes>` prints the same tables with `b − a` in each numeric cell. Every row of either run is listed: a scene one run has not evaluated shows `n/a` across, as does a value that is `null` on either side. The `all` rows and the per-class table compare the shared rows only, and a footer under the first table says how many rows are shared and how many only one run has: `rows: 3 shared, 1 only in det-v1, 0 only in det-v2`.

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

## What was checked

Everything on this page has run on synthetic fixtures only. There is no real model output in the repository yet, and no number from a real detector has been through `eval run`.

- `tests/test_eval_geometry.py` pins the normative IoU, centre distance and yaw error values of the spec.
- `tests/test_eval_config.py` pins the defaults and the config errors.
- `tests/test_eval_matching.py` and `tests/test_eval_metrics.py` pin the normative matching and AP cases, equal-score ordering, the ignored and below-threshold statuses, and a `metrics.json` with no `NaN` and an AP of 0 for a listed class without predictions.
- `tests/test_eval_predictions.py` covers the format, validation (including a time that is a pose of a different agent), `write_run` and the scene-id rule.
- `tests/test_eval_run.py`, `tests/test_eval_cli.py`, `tests/test_evalview.py` and the prediction tests in `tests/test_app.py` cover `eval run|report|compare`, `predictions import`, the three routes, path traversal and the case of a run that was imported but not evaluated.
- `tests/test_eval_end_to_end.py` runs the whole chain on the cooperative fixture: scene, JSON lines, `predictions import`, `eval run`, the three routes, the bird's-eye export with a run, `eval compare`, and checks that the frame rows at 100000 µs count the same `tp`, `fp` and `miss` as the `metrics.json` sample row.

The first real run is planned as an acceptance check: a `single` run on Griffin-25m from the released vehicle-side checkpoint through the Griffin toolkit's inference, written with `write_run`, evaluated with `eval run` and opened in the viewer. That needs the toolkit environment and a GPU job and has not been done. Until it has, the page has only been seen with synthetic predictions.
