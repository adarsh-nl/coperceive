# What the evaluator computes

This page states exactly what `coperceive eval run` and `coperceive eval report` compute, compares it with what the field means by the same words, and lists every place the two differ. It exists so that a number copied into a paper means what the reader of that paper will assume. The decision record is [ADR 0016](adr/0016-evaluator-verification.md).

It has four parts:

- **Part A** defines every reported number in plain mathematics. It is complete on its own: the reference evaluator in `tests/reference_eval/` was written from Part A and from nothing else.
- **Part B** records how the benchmarks define their metrics, with a source for every claim. A claim nobody could confirm is marked **unverified**.
- **Part C** lists each difference between A and B and says which choice is intended.
- **Part D** holds golden cases worked by hand.

[`docs/spec/predictions.md`](spec/predictions.md) remains the contract for the files (`run.json`, `boxes.parquet`, `matches.parquet`, `metrics.json`). Where it and this page disagree, one of them is wrong; the tests in `tests/test_eval_differential.py` decide which.

Code anchors are written `file::symbol`. A test checks that every anchor on this page names a symbol that exists.

---

## Part A. The computed definitions

### A.0 Vocabulary

- A **scene** holds agents, their poses, and ground-truth boxes (annotations). A **run** holds predicted boxes for some scenes.
- Each annotation belongs to one agent: it is a box that agent's ground truth lists at one time. Which agents' annotations an output is scored against is its ground-truth set (A.4).
- Each prediction row names an agent, a **source** (free text; `single` and `fused` have a meaning in A.9) and a time.
- A run **covers** a scene when it has a prediction file for that scene, even an empty one. `coperceive model run` writes one for every scene it finished, and `coperceive predictions import --scene` for the scene it names, also when the model found nothing.
- The **outputs of the run** are the pairs `(agent_id, source)` that occur in at least one prediction row of any covered scene, ordered by first occurrence over the covered scenes taken in ascending `scene_id` order.
- The **outputs of a covered scene** are the outputs of the run whose agent is an agent of that scene: first those that occur in the scene's own rows, in the order of their first row, then the others in run order. An output with no prediction row in a scene is still evaluated there, and its ground truth counts as missed. (Before 2026-11-16 an output existed in a scene only if it had a row there, so a scene or an agent a model found nothing in dropped out of every count. CHANGELOG.)
- A **sample** of an output is one pose time of its agent. Every pose time of the agent is a sample, whether or not it has ground truth or predictions.
- A **row index** is the 0-based position of an annotation in the scene's annotation list, or of a prediction in the scene's prediction list (`boxes.parquet` row order).

Before anything is matched, every prediction row of a scene is checked: its agent must be an agent of the scene and its time must be one of that agent's pose times, whatever its frame. A scene with a row that fails is reported as an error and nothing is written for it; the other scenes of the run are still evaluated. So in an evaluated scene every prediction row belongs to exactly one sample.

Anchors: `coperceive/evaluation/evaluate.py::_collect_outputs`, `coperceive/evaluation/evaluate.py::run_outputs`, `coperceive/evaluation/evaluate.py::_place_gt`, `coperceive/evaluation/predictions.py::validate_boxes`, `coperceive/evaluation/predictions.py::write_run`.

### A.1 Conventions

1. Lengths are metres. Times are integer microseconds. Angles are radians unless a name ends in `_deg`.
2. Frames are right-handed. The **BEV plane** is the world XY plane; world Z is the axis that BEV ignores. (How a left-handed source such as CARLA becomes right-handed is the adapters' job, [ADR 0012](adr/0012-left-handed-sources.md); the evaluator only ever sees the converted scene.)
3. A rotation is a unit quaternion `q = (w, x, y, z)`. Its matrix is

   ```text
   R(q) = | 1-2(y²+z²)   2(xy-wz)    2(xz+wy)  |
          | 2(xy+wz)    1-2(x²+z²)   2(yz-wx)  |
          | 2(xz-wy)     2(yz+wx)   1-2(x²+y²) |
   ```

   Composition `q_a ∘ q_b` is the Hamilton product (apply `q_b` first), so `R(q_a ∘ q_b) = R(q_a) R(q_b)`.
4. A pose sample of an agent is `world_from_body = (q, t)`: `p_world = R(q) p_body + t`.
5. A box is a centre, a size `(length, width, height)` and a rotation, in a frame that is `body` (the agent body at the row's time) or `world`. Length lies along the box x axis, width along box y, height along box z.
6. Quaternions are used as given; they are not renormalised. A prediction's quaternion is refused unless `| |q| − 1 | ≤ 1e-6`; that check happens when the row is read, so such a row never reaches matching (the scene fails as in A.0). Pose and annotation quaternions are not checked (Part C). Every definition on this page assumes unit quaternions; for a quaternion off unit norm by `ε` the placed centre and the yaw are off by `O(ε)`.

Anchors: `coperceive/se3.py::SE3`, `coperceive/evaluation/predictions.py::_rotation`.

### A.2 Config

| Key | Default | Meaning |
| --- | --- | --- |
| `criterion` | `iou_bev` | `iou_bev` or `centre`. |
| `thresholds` | `[0.5, 0.7]` | IoU values for `iou_bev` (strictly increasing, each in `(0, 1]`), metres for `centre` (strictly decreasing, each positive). AP is computed at every entry. |
| `display_threshold` | `0.5` | One of `thresholds`. Its index is the **display index** `d`. |
| `class_thresholds` | `{}` | Class → list of the same length as `thresholds`; replaces the global list for that class. |
| `ignore_gt_visibility_below` | `null` | See A.4 rule 1. |
| `max_range_m` | `null` | See A.4 rule 1. |
| `score_threshold` | `0.3` | The operating point: A.4 rule 3. |
| `gt_class_map` | `{}` | Ground-truth class → class, applied to ground truth before anything else. Predictions are never renamed. |
| `ranking_scope` | `per_frame` | `per_frame` or `pooled`: the order in which the AP walk visits predictions (A.5, A.8). |
| `gt_scope` | `connected_union` | `connected_union` or `ego_only`: the ground truth a `fused` output is scored against (A.4). |
| `comm_range_m` | `70.0` | Communication range of `connected_union`, in metres, positive (A.4). |

A config that breaks a rule of the table is refused, and so is an unknown key. `display_threshold` must equal an entry of `thresholds` exactly. A `class_thresholds` list obeys the same order and range rules as `thresholds`. With `criterion: centre` the `thresholds` key is required: the default list is IoU values. `gt_class_map` is applied once, not transitively (`{a: b, b: c}` turns `a` into `b`).

`ranking_scope`, `gt_scope` and `comm_range_m` are the **protocol**. Their defaults follow the OPV2V and V2X-ViT protocol (A.4, A.5). Every adapter Coperceive ships reads a cooperative dataset, so the defaults apply to all of them; set `ranking_scope: pooled` and `gt_scope: ego_only` to reproduce the rules used before 2026-11-16. The protocol is copied into `metrics.json` with the rest of the config. A stored `metrics.json` whose config has no `ranking_scope` or `gt_scope` was produced by those earlier rules: every reader labels it **legacy (pooled, ego-only)** and never as the current protocol.

The threshold a pair is judged at, for threshold index `i` and prediction category `c`, is `class_thresholds[c][i]` when `c` is a key of `class_thresholds`, else `thresholds[i]`. A class with its own list is therefore judged at its own values while every table still names the global threshold.

Anchors: `coperceive/evaluation/config.py::EvalConfig`, `coperceive/evaluation/config.py::DEFAULT_CONFIG`, `coperceive/evaluation/config.py::parse_config`, `coperceive/evaluation/config.py::threshold_for`.

### A.3 Geometry

**Placing a box in the world.** For a box `(c, size, q_box, frame)` on a row of agent `X` at time `t`:

- `frame = world`: centre `c_w = c`, rotation `q_w = q_box`.
- `frame = body`: let `(q_p, t_p)` be the pose sample of `X` whose time is exactly `t`. Then `c_w = R(q_p) c + t_p` and `q_w = q_p ∘ q_box`. There is no interpolation: a time that is not a pose time is an error.

The size is never changed.

**Yaw.** With `R = R(q_w)`: `ψ = atan2(R[1][0], R[0][0])`, that is `atan2(2(xy + wz), 1 − 2(y² + z²))`. This is the heading, in the BEV plane, of the image of the box x axis. `atan2(0, 0)` is `0`. Roll and pitch do not otherwise enter. For a box whose x axis is vertical to within rounding, both arguments are rounding noise and the yaw is arbitrary; such a box has no heading in the BEV plane.

**Footprint.** The rectangle in the world XY plane with centre `(c_w.x, c_w.y)`, side `length` along the direction `(cos ψ, sin ψ)` and side `width` along `(−sin ψ, cos ψ)`. Its corners are `(c_w.x, c_w.y) + s_x (length/2)(cos ψ, sin ψ) + s_y (width/2)(−sin ψ, cos ψ)` for `s_x, s_y ∈ {−1, +1}`. The sizes are the 3D sizes as given: a tilted box is not projected, its footprint keeps `length × width`. A prediction's sizes are refused unless all three are positive, when the row is read; an annotation's sizes are not checked (Part C).

**BEV IoU** of boxes `a` and `b`: with `A_a = length_a · width_a`, `A_b` likewise and `I` the area of the intersection of the two footprints,

```text
IoU = I / (A_a + A_b − I)      and IoU = 0 when A_a + A_b − I ≤ 0.
```

Height and z do not enter. There is no 3D IoU anywhere in the evaluator.

*Numerical note.* IoU does not change when both boxes are moved by the same vector, so it is evaluated with both footprints expressed relative to a point near the pair (the product uses the midpoint of the two centres: subtract it from each centre first, then add the half extents). Corners formed in absolute world coordinates are rounded to the spacing of doubles at that magnitude, about 1e-9 m at 4,000 km from the origin, and areas computed from absolute coordinates lose far more; for a small box that is no longer negligible.

**Centre distance**: `hypot(c_w,a.x − c_w,b.x, c_w,a.y − c_w,b.y)`. z does not enter.

**Yaw error** of a pair, in degrees: `e = |ψ_a − ψ_b|` in degrees, reduced modulo 360, then `min(e, 360 − e)`. It lies in `[0, 180]`.

**Range** of a placed box for agent `X` at time `t`: `hypot(c_w.x − t_p.x, c_w.y − t_p.y)` with `t_p` the translation of `X`'s pose sample at `t`. It is horizontal and is measured from the agent body origin.

Anchors: `coperceive/evaluation/evaluate.py::world_box`, `coperceive/validate.py::yaw_deg`, `coperceive/evaluation/geometry.py::footprint`, `coperceive/evaluation/geometry.py::bev_iou`, `coperceive/evaluation/geometry.py::centre_distance_m`, `coperceive/evaluation/geometry.py::yaw_error_deg`, `coperceive/evaluation/geometry.py::horizontal_range_m`.

### A.4 Matching one sample

Inputs: an output `(X, s)`, one pose time `t` of `X`, a threshold index `i`, and a flag `operating` (true for the operating-point pass, false for an AP pass).

- `G`: the output's **ground-truth set** at `t` (below), in its order, each box placed in the world (A.3), each with its category replaced by `gt_class_map[category]` when the category is a key of the map.
- `P`: the prediction rows of the output with time `t`, in row order, each placed in the world.

An annotation of an evaluated agent whose time is not one of that agent's pose times is an error.

**Ground-truth set.** For an output whose source is not `fused`, and for every output under `gt_scope: ego_only`, the set is the annotations of `X` at `t`, in row order.

For an output `(X, fused)` under `gt_scope: connected_union`, the set is the union over the agents connected to `X`:

1. *Connected agents.* `C` = the agents `Y ≠ X` of the scene, in scene order, that have a pose sample at exactly `t` and whose horizontal distance from `X` at `t` (between pose translations, as for Range in A.3) is at most `comm_range_m`.
2. *Observed.* An annotation of a `Y` in `C` at `t` is **observed** when at least one LiDAR return recorded at `t` by `X` or by an agent of `C` lies inside its box. A return is placed in the world through its sensor's mount and its agent's pose. It is inside when its coordinates in the box frame are within half the box size on every axis, boundary included. When neither `X` nor any agent of `C` has a LiDAR sweep at `t`, every annotation of every agent of `C` counts as observed: each agent's own list stands for what it saw.
3. *The set.* `X`'s own annotations at `t` come first, in row order, and are always in the set. Then each `Y` of `C` in turn adds its observed annotations at `t`, except those that are the same object as a box already in the set. Same object means paired by the pairing of A.9, run between the boxes already in the set (as the first agent) and `Y`'s observed boxes in row order (as the second). `Y`'s unpaired boxes are appended in row order.

A box of the set keeps its annotation row index and the agent that annotated it. Rule 1 (ignoring) applies to every box of the set, with ranges measured from `X`.

*Source.* OPV2V counts as evaluation targets the "vehicles that are hit by at least one LiDAR point from any connected vehicle", and ignores sensing messages from vehicles outside the 70 m broadcast range ([OPV2V paper](https://arxiv.org/abs/2109.07644), Sec. V-B). V2X-ViT states the same rule and the same 70 m range ([V2X-ViT paper](https://arxiv.org/abs/2203.10638), Sec. 4.2). In the OpenCOOD data each agent's per-frame file lists the vehicles with at least one LiDAR point from that agent ([OpenCOOD documentation](https://opencood.readthedocs.io/en/latest/md_files/data_annotation_tutorial.html)). On OPV2V and V2XSet the union of the connected agents' lists is therefore that set of vehicles, and the point test changes nothing. **Unverified**, because neither paper says, so these are our choices:

- whether the range is horizontal or 3D;
- that the ego's own boxes are kept without a point test;
- how a box annotated by two agents is counted once.

**Rule 1, ignoring.** A ground-truth box is *ignored* when either holds:

- `ignore_gt_visibility_below` is a number, the box's visibility is a number, and `visibility < ignore_gt_visibility_below`;
- `max_range_m` is a number and the box's range (A.3) is `> max_range_m`.

A prediction is *out of range* when `max_range_m` is a number and its range is `> max_range_m`. A box exactly at `max_range_m` is kept.

**Rule 2, order.** Predictions are visited by score, highest first. Predictions with equal scores are visited in row order.

**Rule 3, each prediction in turn.**

1. If it is out of range: status `ignored`. Stop.
2. If `operating` and `score < score_threshold`: status `below_threshold`. Stop. (A score equal to the threshold is kept.)
3. Its candidates are the boxes of `G` that have its category and are not yet matched. Ignored boxes are candidates. If there is no candidate: status `fp`. Stop.
4. The best candidate is the one with the largest BEV IoU (`iou_bev`) or the smallest centre distance (`centre`). Among equals, the one earliest in the order of the ground-truth set (row order for an agent's own annotations).
5. The pair passes when `IoU ≥ threshold` (`iou_bev`) or `distance ≤ threshold` (`centre`), with the threshold of A.2 for index `i` and the prediction's category. If it does not pass: status `fp`. Stop.
6. If the best candidate is ignored: status `ignored`. The box is **not** marked matched, so later predictions can be absorbed by it too. Stop.
7. Otherwise: status `tp`. The box is marked matched, and the pair is recorded with its BEV IoU, centre distance and yaw error. All three are recorded under either criterion.

Only the best candidate is tested. A prediction whose best candidate fails, or is ignored, does not fall through to a second-best box.

**Rule 4, ground truth.** A matched box has status `tp`. An unmatched ignored box has status `ignored`. Any other box has status `miss`. An ignored box is never matched, so a box is `ignored` exactly when rule 1 ignores it, in every pass.

**Rule 5.** Categories never cross.

Two kinds of pass are run for every sample:

- the **operating-point pass**: `i = d` (the display index), `operating = true`;
- one **AP pass** per threshold index `i`: `operating = false`.

Anchors: `coperceive/evaluation/matching.py::match_sample`, `coperceive/evaluation/matching.py::_gt_ignored`, `coperceive/evaluation/matching.py::_pred_ignored`, `coperceive/evaluation/evaluate.py::_match_output`, `coperceive/evaluation/evaluate.py::_union_gt`, `coperceive/evaluation/union.py::connected_agents`, `coperceive/evaluation/union.py::lidar_points_world`, `coperceive/evaluation/union.py::holds_a_point`, `coperceive/evaluation/union.py::pair_with`.

### A.5 Average precision of one scene

For an output `(X, s)` of a scene, a category `c` and a threshold index `i`:

1. `n_gt(c)`: the number of boxes of the output's ground-truth sets over all its samples in the scene whose mapped category is `c` and that rule 1 does not ignore. It does not depend on `i`.
2. If `n_gt(c) = 0`: AP is **null**.
3. Take every prediction row of the output with category `c`, over all times, with its status in the AP pass at `i`. Order them by the ranking scope. `pooled`: by score, highest first, equal scores in row order. `per_frame`: by sample time, earliest first, then within one time by score, highest first, equal scores in row order.
4. Walk the list. A `tp` adds one to `TP`, an `fp` adds one to `FP`, an `ignored` prediction is skipped. After each `tp` or `fp`, record the point `r_k = TP / n_gt(c)`, `p_k = TP / (TP + FP)`.
5. Envelope: `p'_k = max(p_j for j ≥ k)`.
6. `AP = Σ_k (r_k − r_{k−1}) · p'_k` with `r_0 = 0`. With no recorded point the sum is empty and `AP = 0`.

This is the area under the monotone precision envelope taken at every recall step ("all-point" AP). There is no 11-point, 40-point or 101-point sampling, and no point is added at recall 0.

*Ranking scope.* `per_frame`, the default, is what OpenCOOD's documentation says its default evaluation does. Its README says the OPV2V paper does not perform the global sort ([OpenCOOD README](https://github.com/DerrickXuNu/OpenCOOD), "Test the model"). The global sort is an opt-in flag added in 2023 ([OpenCOOD PR #105](https://github.com/DerrickXuNu/OpenCOOD/pull/105)), and the maintainer confirmed that scores are sorted only locally ([OpenCOOD issue #101](https://github.com/DerrickXuNu/OpenCOOD/issues/101)). **Unverified**: that "locally" means within one frame. That reading is the issue reporter's description in #101 (sorting within one batch), and that a batch is one frame at test time is not documented. The rest of OpenCOOD's AP computation exists only in code, which was not read (clean room). Under `per_frame` a prediction's place in the walk depends on when it occurred, so the same detections can give a different AP than under `pooled`; the README says global sorting gives the higher number. `pooled` ranks every prediction of the walk together, as KITTI does over its split ([Simonelli et al. 2019](https://arxiv.org/abs/1905.12365), Sec. 1). It was Coperceive's only rule before 2026-11-16.

The **category set** of the output is the union of the run's declared categories, the mapped categories of all boxes of its ground-truth sets in the scene (ignored ones included), and the categories of the output's predictions. Then, per threshold index:

- `mean`: the mean of the non-null APs over the category set; null when there is none.
- `mean_model`: the mean of the non-null APs over the run's declared categories; null when there is none.

Anchors: `coperceive/evaluation/metrics.py::average_precision`, `coperceive/evaluation/evaluate.py::_ap_table`, `coperceive/evaluation/evaluate.py::_ap_records`.

### A.6 Counts, precision and recall

From the operating-point pass, for each sample (in time order):

```text
tp          = predictions with status tp
fp          = predictions with status fp
miss        = ground-truth boxes with status miss
ignored_gt  = ground-truth boxes with status ignored
precision   = tp / (tp + fp),   null when tp + fp = 0
recall      = tp / (tp + miss), null when tp + miss = 0
```

Predictions with status `ignored` or `below_threshold` count nowhere.

**Totals** of the output in the scene: the sums of `tp`, `fp`, `miss` and `ignored_gt` over its samples; `gt = tp + miss`; `predictions` = the number of prediction rows of the output; `precision` and `recall` by the same formulas from the sums.

Precision and recall are therefore at one operating point: the display threshold and the score threshold. They are not read off the AP curve.

Anchors: `coperceive/evaluation/metrics.py::counts_of`, `coperceive/evaluation/metrics.py::Counts`, `coperceive/evaluation/evaluate.py::_samples_and_totals`.

### A.7 Errors of the true positives

Over the pairs of the operating-point pass of the output, all samples together:

- `centre_m.mean`, `centre_m.median`: mean and median of the centre distances (the median of an even count is the mean of the two middle values);
- `yaw_deg.mean_abs`: mean of the yaw errors;
- `size_m.length`, `.width`, `.height`: mean of `|size_pred − size_gt|` per component;
- `iou_bev.mean`: mean of the BEV IoUs.

All are null when there is no pair.

Anchors: `coperceive/evaluation/metrics.py::tp_errors`, `coperceive/evaluation/evaluate.py::_tp_errors`.

### A.8 Headline rows: pooling over scenes

A scene is **evaluated** for a run when the run covers it (A.0). For one output `(X, s)`, over the evaluated scenes in which it is an output (A.0: those in which its agent exists), taken in ascending `scene_id` order (plain string order):

- `gt`, `predictions`, `tp`, `fp`, `miss`: sums of the per-scene totals (A.6);
- `recovered`, `lost_by_fusion`: sums of the per-scene counts (A.9);
- `precision = tp / (tp + fp)`, null when the denominator is 0; `recall = tp / gt`, null when `gt = 0`;
- **pooled AP** per category `c` and threshold index `i`: `n_gt(c)` is the sum of the per-scene `n_gt(c)`. The list of step 3 of A.5 is built from each scene's rows of category `c` with their AP-pass statuses. Under `pooled` they are concatenated in scene order, in row order within a scene, and sorted once by score, highest first; equal scores keep scene order, then row order. Under `per_frame` each scene's rows are put in the per-frame order of A.5 and the scenes are concatenated in scene order, with no sort over the whole list. Steps 2 to 6 of A.5 then run once on that list.
- the categories are the union of the per-scene category sets; **mAP all** is the mean of the non-null pooled APs over them, **mAP model** the mean of the non-null pooled APs over the run's declared categories; each is null when no AP is left.

The row over **every output** applies the same rules to all `(scene, output)` pairs at once, ordered by scene and, within a scene, by output order:

- `gt`, `predictions`, `tp`, `fp`, `miss` are summed over all of them, and the lists of the pooled AP are concatenated in that order (and, under `pooled`, sorted once by score as above). A ground-truth box of an agent with two outputs is therefore counted once per output in `gt`, `miss` and `n_gt(c)`.
- `recovered` and `lost_by_fusion` are **not** per-output facts: they compare two outputs of one agent, and every output of that agent carries the same value (A.9). They are summed once per `(scene, agent)`: for each scene and each agent that has at least one output there, the value of that agent's first output in the scene.

The per-scene AP of A.5 is a diagnostic. The headline is the pooled AP. The mean of per-scene APs is not reported.

Anchors: `coperceive/evaluation/report.py::collect`, `coperceive/evaluation/report.py::_pools`, `coperceive/evaluation/report.py::pooled_ap`, `coperceive/evaluation/report.py::aggregate`.

### A.9 Cooperative counts

These compare the `single` and the `fused` output of the same agent, object by object, at the operating point. The objects are the boxes of the ground-truth set of `(X, fused)` at each time `t` (A.4). Under `gt_scope: ego_only` that set is `X`'s own boxes; under `connected_union` it also holds the boxes the connected agents add.

For a box `g` of that set at time `t`:

- `status_fused`: the status of `g` in the operating-point pass of `(X, fused)`.
- `status_single`: the status of `g` in the operating-point pass of `(X, single)` when `g` is one of `X`'s own boxes. It is **absent** when another agent added `g`: the `single` output is scored against `X`'s own boxes only, so such an object cannot be one of its true positives.
- When the scene has no `(X, single)` output, `status_single` is null for every box; when it has no `(X, fused)` output, the set is `X`'s own boxes and `status_fused` is null.
- **Other agents' boxes paired with `g`.** For each other agent `Y` of the scene, in scene order, that has at least one annotation at time `t`, pair the annotations of `X` at `t` with those of `Y` at `t`:
  1. *Shared track.* The scene declares a set of shared track ids (only the ids are used here; there is no distance or category condition). For each box of `X` in row order whose `track_id` is in that set: the first box of `Y` in row order, not already used, with the same `track_id` is paired with it. Both are then used.
  2. *Computed.* Among the boxes not used in step 1 (a box with a shared id that found no partner included), form every pair `(a of X, b of Y)` whose distance is at most the gate. Categories are not compared. The distance is the **3D** Euclidean distance between the two world centres. The gate of a pair is `max(2.0, 0.5 · max(length_a, length_b))` metres. Sort the pairs by distance, then by the position of `a` among `X`'s boxes at `t`, then by the position of `b`; take them greedily, skipping a pair when either box is already used.
- For a box another agent `Y` added, the pairing below is not run: `seen_by_other_gt` is true, `other_kind` is null, and `other_agent_tp` is the status of that annotation under `(Y, single)` being `tp` (null when the scene has no `(Y, single)` output).
- `seen_by_other_gt`: true when `g` is in at least one pair, over all other agents.
- `other_kind`: `shared_track` when any of its pairs came from step 1, else `computed` when it has a pair, else null.
- `other_agent_tp`: consider each box `h` of an agent `Y` paired with `g` for which the scene has an output `(Y, single)`. Null when there is no such `h`; true when any such `h` has status `tp` in the operating-point pass of `(Y, single)`; false otherwise.

Counts of an output `(X, s)` in a scene, over the boxes of the set when the scene has both a `(X, single)` and a `(X, fused)` output. "Not found alone" means `status_single` is `miss` or absent.

```text
recovered         = boxes not found alone with status_fused = tp
lost_by_fusion    = boxes with status_single = tp and status_fused = miss
only_other_agent  = boxes not found alone with other_agent_tp = true
double_miss       = boxes not found alone with status_fused = miss
nobody            = double_miss boxes whose other_agent_tp is not true
recovery_rate     = recovered / (recovered + double_miss), null when the denominator is 0
```

Under `ego_only` no status is absent, and these are the definitions used before 2026-11-16. Under `connected_union`, `recovered` also counts the objects only a connected agent annotated that the fused output found. Those are the objects cooperation is meant to add.

The counts do not depend on `s`: every output of an agent, a third source included, carries the same five values (`double_miss` is not reported). An agent that lacks the `single` or the `fused` output has all counts 0 and a null rate. `only_other_agent` does not look at `status_fused`, so a box can count in both `recovered` and `only_other_agent`; the counts are not a partition. A box whose status is `ignored` under either output falls in none of them.

Anchors: `coperceive/evaluation/evaluate.py::_cooperative_columns`, `coperceive/evaluation/evaluate.py::_other_boxes`, `coperceive/assoc.py::associate`, `coperceive/evaluation/metrics.py::cooperative_counts`.

### A.10 What is not computed

- No 3D IoU and no AP on 3D overlap. Every AP is a BEV AP (or a centre-distance AP).
- Under `gt_scope: ego_only`, and for every output whose source is not `fused`, no union of agents' ground truth: those outputs are scored against their own agent's annotations.
- No interpolation of AP over fixed recall positions.
- No evaluation range unless `max_range_m` is set; when set it is a radius around the agent, not a rectangle.

### A.11 Breakdowns (`eval compare --by`)

A breakdown splits one output's pooled evaluation (A.8) by an attribute:

- **distance**: horizontal distance from the scene's first agent to the box centre at its time, in the bins of `--distance-bins`;
- **occlusion**: ground-truth visibility, in the bins of `--visibility-bins`;
- **agents**: the number of agents that annotate the object;
- **class** and **scene**.

For a bin `B` and threshold index `i` nothing is matched again. The AP pass of A.4 at `i` and its pairs are filtered:

- `n_gt(c)`: the kept ground-truth boxes of category `c` whose attribute is in `B`.
- A prediction that is a `tp` at `i` counts as a `tp` in `B` when the box it was paired with in that pass is in `B`. Otherwise it is **ignored** in `B`: it found an object of another bin, so it is neither a true nor a false positive of this one.
- A prediction that is an `fp` at `i` counts as an `fp` in `B` when its own attribute is in `B`, and is ignored otherwise. For distance, class and scene a prediction has its own attribute (its centre, its class, its scene). For occlusion and agent count it has none, so an unmatched prediction counts as an `fp` in every bin of those two breakdowns.
- `ignored` predictions are skipped.

Steps 2 to 6 of A.5 then run on what is left, in the order of the ranking scope. Each box is paired at most once in a pass, so `TP ≤ n_gt(c)` and the AP of a bin lies in `[0, 1]`.

*Source.* Treating ground truth outside the bin as ignored is how the benchmarks handle objects they do not evaluate:

- COCO: when objects carry the ignore flag, detections matched to them count as neither false positives nor true positives ([COCO detection evaluation](https://cocodataset.org/dataset/detection-eval.htm), Sec. 4).
- PASCAL VOC: objects marked difficult are discarded, and no penalty is incurred for detecting them ([Everingham et al. 2010](http://host.robots.ox.ac.uk/pascal/VOC/pubs/everingham10.pdf), Sec. 3.3).
- KITTI: detections smaller than a difficulty's minimum size do not count as false positives ([KITTI 2D benchmark](https://www.cvlibs.net/datasets/kitti/eval_object.php?obj_benchmark=2d)).

Two parts are our choices:

- Not matching again within the bin: the benchmarks above apply their ignore rules during matching. Coperceive filters the pairs of the full pass.
- Counting an unmatched prediction as a false positive in every occlusion or agent-count bin: without it, the AP of those bins would be a recall.

Before 2026-11-16 a prediction followed its ground truth into a bin only through the operating-point pair. A true positive scored under `score_threshold` was binned by its own attributes, and a distance bin could report an AP of 2.0 (CHANGELOG).

Anchors: `coperceive/evaluation/breakdown.py::_scored`, `coperceive/evaluation/breakdown.py::_own_bin`, `coperceive/evaluation/breakdown.py::_ap_cell`.

### A.12 Labels

Under `criterion: iou_bev` every AP is a bird's-eye-view AP. `metrics.json` carries `"iou": "bev"`, and `eval report`, `eval compare` (its tables and its `--by` breakdowns) and the paper export label it `AP@<t> (BEV)` (and `mAP@<t> (BEV)`). Under `centre` the label is `AP@<t> m (centre)` and `"iou"` is null. OPV2V computes its IoU on the x-y plane too ([OPV2V paper](https://arxiv.org/abs/2109.07644), Sec. V-B).

The protocol of A.2 goes with every number:

- `eval report` and `eval compare` print it on their first line (`protocol: ranking per_frame, ground truth connected_union (70 m)`, or `protocol: legacy (pooled, ego-only)`). `eval compare` refuses two runs scored under different protocols, or a legacy run beside a current one.
- `compare.json` records it as its `protocol` object (`label`, `legacy`, `criterion`, `iou`, `ranking_scope`, `gt_scope`, `comm_range_m`). A file written before 2026-11-16 has none, and the paper export then says "protocol not recorded".
- The paper export names it in the caption of every metrics and breakdown table.
- The repro card holds each scene's `metrics.json` unchanged, config included.

The app's scores view (`web/src/viewer/SidePanel.tsx`) still shows `mAP@<t>` with no label and no protocol. That file belongs to the viewer and the change is routed (PR #165).

---

## Part B. The field's conventions

**Being written.** The research behind this part is done: claims about OPV2V/OpenCOOD, V2XSet/V2X-ViT, V2V4Real, DAIR-V2X, KITTI, PASCAL VOC/COCO, nuScenes, Griffin and later OpenCOOD-style work were collected from papers and written documentation only (no evaluation source code, ADR 0016 §2), and every claim was re-opened by a second reader. The text is not in this commit. The facts that bear most directly on Part A, each confirmed by the second reader:

- OPV2V computes IoU on the x-y plane only, for every detector ([OPV2V paper](https://arxiv.org/abs/2109.07644), Sec. V-B).
- OpenCOOD's default evaluation sorts detection scores only locally, not over the whole test set. Its README says the OPV2V paper does not perform the global sort, and a global sort is an opt-in flag added later ([OpenCOOD README](https://github.com/DerrickXuNu/OpenCOOD), "Test the model"; [OpenCOOD PR #105](https://github.com/DerrickXuNu/OpenCOOD/pull/105); maintainer reply in [issue #101](https://github.com/DerrickXuNu/OpenCOOD/issues/101)). That "locally" means within one frame is the issue reporter's description in #101, not documentation: **unverified**.
- OPV2V's evaluation targets are the vehicles hit by at least one LiDAR point from any connected vehicle, so ground truth is defined on the aggregated points of all connected vehicles, and collaborators outside the 70 m communication range are ignored ([OPV2V paper](https://arxiv.org/abs/2109.07644), Sec. III-B and V-B).
- KITTI computes AP from one precision/recall curve over the whole split ([Simonelli et al. 2019](https://arxiv.org/abs/1905.12365), Sec. 1).

---

## Part C. Where Coperceive differs

**Being written.** It will list, row by row, each place where Part A differs from Part B and which choice is intended. Defects found by this verification that are already fixed are in the CHANGELOG entries of PR #165.

---

## Part D. Golden cases

Each case is small enough to check with a pencil. Unless a row says otherwise: one agent `ego` at the world origin with no rotation, one pose time, class `car`, boxes 4 m long and 2 m wide given in the world frame, the default config of A.2 (IoU thresholds 0.5 and 0.7, display 0.5, score threshold 0.3). "Far" means a prediction that overlaps nothing.

`tests/test_eval_golden.py` reads this table. It builds every case and asks both the product and the reference evaluator for the value in the last column; a value neither reproduces to 1e-9 fails the test, and so does a row without a case or a case without a row.

| ID | Case | Arithmetic | Value |
| --- | --- | --- | --- |
| D01 | IoU of a box with itself. | I = 8, union = 8 + 8 − 8 = 8. | `1` |
| D02 | IoU of a box at x = 0 and the same box at x = 2. | They share 2 m of length: I = 2 · 2 = 4. Union = 8 + 8 − 4 = 12. | `1/3` |
| D03 | IoU of a box and the same box turned 90° about its centre. | The 4 × 2 and the 2 × 4 rectangle share a 2 × 2 square: I = 4. Union = 12. | `1/3` |
| D04 | IoU of a 4 × 2 box at the origin and a 1 × 1 box at (0.5, 0.25). | The small box is inside the large one: I = 1. Union = 8 + 1 − 1 = 8. | `1/8` |
| D05 | IoU of a box at x = 0 and the same box at x = 4. | They touch along one edge: I = 0. | `0` |
| D06 | IoU of a 2 × 2 box and the same box turned 45°. | The intersection is a regular octagon of area 8(√2 − 1) = 3.3137. Union = 8 − 3.3137 = 4.6863. The ratio is (√2 − 1)/(2 − √2) = 1/√2. | `0.7071067811865476` |
| D07 | IoU of a box with heading 180° and the same box with heading −180°. | The two headings are the same direction, so it is D01. | `1` |
| D08 | Yaw error between headings 179° and −179°. | e = 358, and min(358, 360 − 358) = 2. | `2` |
| D09 | Centre distance between (0, 0, 0) and (3, 4, 10). | z does not count: hypot(3, 4). | `5` |
| D10 | The agent stands at (10, 5) with heading 90°. A prediction in its body frame at (2, 0), heading 0. Ground truth in the world at (10, 7), heading 90°. IoU of the pair. | A.3: R(90°) · (2, 0) = (0, 2), plus (10, 5), gives (10, 7). Heading 90° + 0° = 90°. The two boxes coincide. | `1` |
| D11 | The centre error of the pair of D10. | Both centres are (10, 7). | `0` |
| D12 | One ground-truth car. A far prediction at score 0.9 and a prediction on the car at 0.8. AP at 0.5. | Walk: fp, tp. Points (r, p): (0, 0), (1, 1/2). Envelope 1/2, 1/2. AP = (0 − 0) · 1/2 + (1 − 0) · 1/2. | `1/2` |
| D13 | Three ground-truth cars. Predictions by score: on car 1, far, on car 2, far, on car 3. AP at 0.5. | Walk: tp, fp, tp, fp, tp. Points: (1/3, 1), (1/3, 1/2), (2/3, 2/3), (2/3, 1/2), (1, 3/5). Envelope: 1, 2/3, 2/3, 3/5, 3/5. AP = 1/3 · 1 + 0 + 1/3 · 2/3 + 0 + 1/3 · 3/5 = 15/45 + 10/45 + 9/45. | `34/45` |
| D14 | Two ground-truth cars, one prediction on the first. AP at 0.5. | One point (1/2, 1). AP = 1/2 · 1. Recall never reaches 1 and nothing is added for the missing half. | `1/2` |
| D15 | One ground-truth car and two identical predictions on it, scores 0.9 and 0.8. AP at 0.5. | The first takes the car (tp). The second has no unmatched candidate (fp). Points (1, 1), (1, 1/2). AP = 1 · 1 + 0. | `1` |
| D16 | Precision at the operating point in D15. | tp = 1, fp = 1. | `1/2` |
| D17 | Two ground-truth cars at x = 0 and x = 1 (their IoU is 6/10). Two identical predictions on the first, scores 0.9 and 0.8. AP at 0.5. | The first takes car 1. For the second, car 1 is matched; the best unmatched box is car 2 at IoU 0.6 ≥ 0.5, so it is a tp too. Two tp of two cars. | `1` |
| D18 | The same at 0.7. | Now 0.6 < 0.7: the second prediction is an fp. Points (1/2, 1), (1/2, 1/2). AP = 1/2 · 1 + 0. | `1/2` |
| D19 | Two ground-truth cars at x = 0 (visibility 0.1) and x = 1 (visibility 0.9), `ignore_gt_visibility_below: 0.5`. One prediction exactly on the first. Recall. | The first car is ignored. It is still the best candidate (IoU 1 against 0.6), so the prediction is `ignored` and the second car is not tried: a miss. tp = 0, miss = 1. | `0` |
| D20 | AP at 0.5 in D19. | n_gt = 1 and no tp or fp is recorded: the sum is empty. | `0` |
| D21 | One ground-truth car. Two predictions with the same score 0.5: row 0 far, row 1 on the car. AP at 0.5. | Equal scores keep row order: fp, tp. As D12. | `1/2` |
| D22 | The same two predictions with the rows swapped. | Walk: tp, fp. Points (1, 1), (1, 1/2). AP = 1. The same detections, a different number. | `1` |
| D23 | Scene `a`: one car, one prediction on it at 0.9. Scene `b`: one car, a far prediction at 0.8 and one on the car at 0.7. Pooled AP at 0.5. | n_gt = 2. Walk: tp, fp, tp. Points (1/2, 1), (1/2, 1/2), (1, 2/3). Envelope 1, 2/3, 2/3. AP = 1/2 · 1 + 0 + 1/2 · 2/3. | `5/6` |
| D24 | The per-scene AP of scene `b` in D23. | As D12. (Scene `a` has AP 1; the mean of the two, 3/4, is reported nowhere.) | `1/2` |
| D25 | One car, one prediction on it with score exactly 0.3. Recall. | 0.3 is not below the score threshold 0.3: tp. | `1` |
| D26 | The same with score 0.29. Recall. | `below_threshold`: the car is a miss at the operating point. | `0` |
| D27 | AP at 0.5 in D26. | The AP pass does not apply the score threshold: tp. | `1` |
| D28 | `max_range_m: 50`. Ground truth at (50, 0) and at (0, 50.5), a prediction on each. True positives. | Ranges 50 and 50.5. Exactly 50 is kept, so the first pair is a tp. The second box is ignored and its prediction is out of range. | `1` |
| D29 | Ignored ground truth in D28. | The box at 50.5 m. | `1` |
| D30 | Recall in D28. | tp = 1, miss = 0. | `1` |
| D31 | One ground-truth `truck`, `gt_class_map: {truck: car}`, one `car` prediction on it. AP of `car` at 0.5. | The ground truth is renamed to `car` before matching: tp. | `1` |
| D32 | The agent has a `single` and a `fused` output. One car. The `single` prediction is far, the `fused` one is on the car. `recovered`. | status_single = miss, status_fused = tp. | `1` |
| D33 | The reverse: `single` on the car, `fused` far. `lost_by_fusion`. | status_single = tp, status_fused = miss. | `1` |
| D34 | Two scenes, each as D32. `recovered` in the row over every output. | One box per scene, once per agent: 1 + 1. (Summed per output it would be 4.) | `2` |
| D35 | A second agent `rsu` annotates the same car 2.0 m away along x. Is the car `seen_by_other_gt`? (1 = yes.) | Gate = max(2.0, 0.5 · 4) = 2.0. Distance 2.0 ≤ 2.0. | `1` |
| D36 | The same with the second annotation 2.5 m higher instead. | The distance is 3D: 2.5 > 2.0. | `0` |
| D37 | The same as D36 with both boxes 6 m long. | Gate = max(2.0, 0.5 · 6) = 3.0. 2.5 ≤ 3.0. | `1` |
