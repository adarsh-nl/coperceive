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

An annotation of an agent that has an output in the scene (A.0) whose time is not one of that agent's pose times is an error.

**Ground-truth set.** For an output whose source is not `fused`, and for every output under `gt_scope: ego_only`, the set is the annotations of `X` at `t`, in row order.

For an output `(X, fused)` under `gt_scope: connected_union`, the set is the union over the agents connected to `X`:

1. *Connected agents.* `C` = the agents `Y ≠ X` of the scene, in scene order, that have a pose sample at exactly `t` and whose horizontal distance from `X` at `t` (between pose translations, as for Range in A.3) is at most `comm_range_m`.
2. *Observed.* An annotation of a `Y` in `C` at `t` is **observed** when at least one LiDAR return recorded at `t` by `X` or by an agent of `C` lies inside its box. A return is placed in the world through its sensor's mount and its agent's pose. It is inside when its coordinates in the box frame are within half the box size on every axis, boundary included. When neither `X` nor any agent of `C` has a LiDAR sweep at `t`, every annotation of every agent of `C` counts as observed: each agent's own list stands for what it saw. A sweep with no returns is still a sweep, and one sweep from any agent of `X` and `C` ends the fallback for all of them.
3. *The set.* `X`'s own annotations at `t` come first, in row order, and are always in the set. Then each `Y` of `C` in turn adds its observed annotations at `t`, except those that are the same object as a box already in the set. Same object means paired by the pairing of A.9, run between the boxes already in the set, in the set's order (as the first agent), and `Y`'s observed boxes in row order (as the second). Categories are not compared, as in A.9. `Y`'s unpaired boxes are appended in row order.

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

*Ranking scope.* `per_frame`, the default, is what OpenCOOD's documentation says its default evaluation does. Its README says the OPV2V paper does not perform the global sort ([OpenCOOD README](https://github.com/DerrickXuNu/OpenCOOD), "Test the model"). The global sort is an opt-in flag added in 2023 ([OpenCOOD PR #105](https://github.com/DerrickXuNu/OpenCOOD/pull/105)), and the maintainer confirmed that scores are sorted only locally ([OpenCOOD issue #101](https://github.com/DerrickXuNu/OpenCOOD/issues/101)). **Unverified**: that "locally" means within one frame. That reading is the issue reporter's description in #101 (sorting within one batch), and that a batch is one frame at test time is not documented. The rest of OpenCOOD's AP computation exists only in code, which was not read (clean room). Under `per_frame` a prediction's place in the walk depends on when it occurred, so the same detections can give a different AP than under `pooled`; the README says global sorting increases the tolerance for false positives, and the maintainer says it can increase AP (B.1). `pooled` ranks every prediction of the walk together, as KITTI does over its split ([Simonelli et al. 2019](https://arxiv.org/abs/1905.12365), Sec. 1). It was Coperceive's only rule before 2026-11-16.

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

This part records, per benchmark, what bears on the AP it reports: overlap, which boxes are evaluated, matching, ranking scope, AP integration and aggregation. The sources are papers and written documentation only, including the prose of issue and pull-request threads; no evaluation source code was read (clean room, ADR 0016 §2). Each claim was re-opened at its source by a second reader, and where the reader narrowed a claim the narrowed form is given. An issue thread is weaker evidence than a paper or official documentation and is named as such. What could not be confirmed is listed under **Unverified**; **code-only** means the answer is written only in source code, which was not read.

### B.1 OPV2V and OpenCOOD

| Topic | What the sources say |
| --- | --- |
| Overlap | IoU is computed on the x-y plane only, to keep the comparison fair, because PIXOR ignores the z coordinates; AP is reported at IoU 0.5 and 0.7 ([OPV2V paper](https://arxiv.org/abs/2109.07644), Sec. V-B 'Metrics', p.6; Table IV). OpenCOOD's OPV2V LiDAR table lists AP at 0.7 only and does not say BEV or 3D ([OpenCOOD README](https://github.com/DerrickXuNu/OpenCOOD), 'Benchmark and model zoo' > 'Results on OPV2V LiDAR-track'). |
| Ground truth | The evaluation targets are the vehicles hit by at least one LiDAR point from any connected vehicle, so ground truth is defined on the aggregated points of all connected vehicles, not only on what the ego sees. Collaborators outside the 70 m communication range are ignored. One fixed vehicle is the ego of each test scenario. No difficulty levels, visibility classes or don't-care regions are defined ([OPV2V paper](https://arxiv.org/abs/2109.07644), Sec. III-B, p.4; Sec. V-B, p.6; footnote 3, p.5). Each agent's per-timestamp annotation file lists the vehicles hit by at least one point of that agent's own LiDAR ([OpenCOOD docs, annotation tutorial](https://opencood.readthedocs.io/en/latest/md_files/data_annotation_tutorial.html), intro and the 'vehicles' field). A user reports in an issue thread that the connected vehicles are not in the ground truth; the maintainer said this affects AP slightly, equally for all methods ([OpenCOOD issue #141](https://github.com/DerrickXuNu/OpenCOOD/issues/141), issue body and maintainer reply). |
| Range and classes | x in [-140, 140] m and y in [-40, 40] m around the ego, with no z extent and nothing on whether it filters ground truth, detections or both. One class, vehicles ([OPV2V paper](https://arxiv.org/abs/2109.07644), Sec. V-B, p.6; Abstract). According to the maintainer, ranges are in LiDAR coordinates with the origin at the sensor ([OpenCOOD issue #82](https://github.com/DerrickXuNu/OpenCOOD/issues/82), maintainer replies). |
| Matching | Not written (Unverified). |
| Ranking scope | The inference option `global_sort_detections` sets whether detections are sorted globally by confidence. The README calls global sorting the mainstream AP method, says it increases the tolerance for false positives, and states that the OPV2V paper does not perform the global sort ([OpenCOOD README](https://github.com/DerrickXuNu/OpenCOOD), 'Test the model', item `global_sort_detections`). The option is an opt-in flag added later: the pull request that added it writes global AP only when the flag is passed ([OpenCOOD pull request #105](https://github.com/DerrickXuNu/OpenCOOD/pull/105), PR description). According to the maintainer in an issue thread, the original evaluation sorts scores only locally, and sorting globally can increase AP ([OpenCOOD issue #101](https://github.com/DerrickXuNu/OpenCOOD/issues/101), maintainer reply). The reporter in that thread describes local sorting as sorting within a single batch ([OpenCOOD issue #101](https://github.com/DerrickXuNu/OpenCOOD/issues/101), issue body). That "locally" means within one frame rests on that description, not on documentation: **unverified**. A contributor reports AttFusion AP50 0.905 without and 0.936 with global sorting (AP70 0.815 and 0.881) ([OpenCOOD issue #104](https://github.com/DerrickXuNu/OpenCOOD/issues/104), issue body). |
| AP integration | Not written (Unverified). |
| Aggregation | The two test sets, CARLA default towns and Culver City, are reported separately and not averaged ([OPV2V paper](https://arxiv.org/abs/2109.07644), Sec. V-C, p.6; Table IV). A user in an issue thread says the evaluation puts all prediction results together while the confidence sort is applied in every single batch; the maintainer replied there that he saw no issue in the evaluation utility, and later confirmed in #101 that scores are sorted only locally ([OpenCOOD issue #69](https://github.com/DerrickXuNu/OpenCOOD/issues/69), comment of 6 Apr 2023 and maintainer reply). No thread states that true and false positives are pooled into one AP per threshold, and none describes per-scene or per-frame averaging. |

**Unverified**

- Code-only: how the x-y overlap is computed; the matching rule (order, one-to-one, choice of box, duplicates, ties); AP integration (11-, 40-, 101-point or all-point); whether a batch is one frame at test time (code-only or config-only; a later paper calls the official benchmark frame-wise, B.9).
- Not stated, or only in user reports (issues #139 and #84): whether the range filters ground truth or detections, by centre or corners, and its z extent; whether CARLA vehicle sub-types are merged; how detections on unannotated objects or outside the range count; whether the union is over all connected vehicles or those within 70 m, and whether No Fusion is scored against it; the confidence and NMS thresholds behind Table IV, any per-frame detection cap or score calibration.
- That all frames are pooled into one AP: only users in issue threads say so.

### B.2 V2XSet and V2X-ViT

| Topic | What the sources say |
| --- | --- |
| Overlap | AP at IoU 0.5 and 0.7; the paper says only IoU, not BEV or 3D ([V2X-ViT paper](https://arxiv.org/abs/2203.10638), Sec. 4.2 'Evaluation metrics', ECCV camera-ready p.9). The headline table is titled as a 3D detection comparison, for a Perfect and a Noisy setting ([V2X-ViT paper](https://arxiv.org/abs/2203.10638), Table 1, ECCV camera-ready p.10). |
| Ground truth | The targets are the vehicles hit by at least one LiDAR point from any connected agent; no difficulty levels, don't-care regions or visibility classes are described. Agents outside the ego's 70 m radius are ignored. That the ground-truth set is the union over the agents within 70 m is a plausible inference, not a statement, and no separate set is defined for No Fusion ([V2X-ViT paper](https://arxiv.org/abs/2203.10638), Sec. 4.2, 'Evaluation metrics' and 'Implementation details', ECCV camera-ready p.10). |
| Range and classes | x in [-140, 140] m and y in [-40, 40] m, no z extent, not said whether it filters ground truth, detections or both ([V2X-ViT paper](https://arxiv.org/abs/2203.10638), Sec. 4.2, first sentence, ECCV camera-ready p.9). Vehicles only ([V2X-ViT paper](https://arxiv.org/abs/2203.10638), Sec. 4.2; Sec. 5). |
| Matching | Not written (Unverified). |
| Ranking scope | The paper does not mention sorting. According to the OpenCOOD maintainer in an issue thread (8 Jul 2023), the evaluation sorts scores only locally, global sorting can increase AP, and all methods using OpenCOOD use the local sort; this is about OpenCOOD in general, not V2XSet ([OpenCOOD issue #101](https://github.com/DerrickXuNu/OpenCOOD/issues/101), reply by DerrickXuNu). The opt-in global sort was merged on 8 Aug 2023, after the paper ([OpenCOOD pull request #105](https://github.com/DerrickXuNu/OpenCOOD/pull/105), PR description). |
| AP integration | Not written (Unverified). |
| Aggregation | One AP per IoU threshold and setting on V2XSet, whose test split has 2,833 frames; no averaging over classes or distance ranges is described ([V2X-ViT paper](https://arxiv.org/abs/2203.10638), Sec. 4.1; Table 1). The OpenCOOD thread implies, but does not state, that AP comes from one concatenated sequence over the evaluated data rather than a mean of per-frame or per-scene APs ([OpenCOOD issue #101](https://github.com/DerrickXuNu/OpenCOOD/issues/101), issue body and maintainer reply). |

**Unverified**

- Code-only: BEV or 3D IoU; the matching rule; AP integration; the range test (ground truth or detections, centre or corners, z extent, 140 m in the paper or 140.8 m in the config tutorial) and detections outside it.
- The ranking scope behind Table 1: not stated by the paper or its README; the local-sort default rests on 2023 OpenCOOD statements about OpenCOOD-based methods in general, and whether a batch is one frame is code-only or config-only.
- Not stated: which agent is the fixed ego of each test scene, and which ground-truth set No Fusion and Late Fusion are scored against; that true and false positives are pooled over all test frames (implied only by issue threads); the thresholds behind the headline.

### B.3 V2V4Real

| Topic | What the sources say |
| --- | --- |
| Overlap | AP at IoU 0.5 and 0.7; the paper does not say BEV or 3D ([V2V4Real paper](https://arxiv.org/abs/2303.07601), Sec. 4.1 'Evaluation.', p.6). |
| Ground truth | Ground truth is defined in the ego's frame; at test time the ego is always the Tesla. The other vehicle transforms its annotated boxes into the ego frame, so the ground truth is not limited to boxes annotated from the ego's LiDAR, and where both annotated an object, "we select the one annotated in the ego vehicle as the groundtruth" ([V2V4Real paper](https://arxiv.org/abs/2303.07601), Sec. 4.1 'Groundtruth.', p.6). No Fusion uses only the ego point cloud; the paper does not say it is scored against another set ([V2V4Real paper](https://arxiv.org/abs/2303.07601), Sec. 4.1, 'No Fusion', p.6). No communication-range cut-off is described; the two vehicles stayed within 150 m of each other ([V2V4Real paper](https://arxiv.org/abs/2303.07601), Sec. 3.1, 'Driving Route', p.4). |
| Range and classes | x in [-100, 100] m and y in [-40, 40] m around the ego, no z extent, not said which boxes it filters. Five annotated vehicle types are merged into one class ([V2V4Real paper](https://arxiv.org/abs/2303.07601), Sec. 4.1 'Evaluation.', p.6; Sec. 3.2, p.4). |
| Matching | Not written (Unverified). |
| Ranking scope | The README documents no flag for IoU type, ranking scope or range ([V2V4Real README](https://raw.githubusercontent.com/ucla-mobility/V2V4Real/main/README.md), 'Test the model', 'Arguments Explanation'). |
| AP integration | Not written (Unverified). |
| Aggregation | The test split has 3,986 frames ([V2V4Real paper](https://arxiv.org/abs/2303.07601), Sec. 5.1, p.7). AP is reported under Sync and Async (100 ms delay) ([V2V4Real paper](https://arxiv.org/abs/2303.07601), Sec. 4.1 'Evaluation.', p.6), as Overall and 0-30 m, 30-50 m, 50-100 m columns whose labels are the only definition. Overall is not the unweighted mean of the bins (No Fusion at 0.5: 39.8 against 69.2, 29.3 and 4.8; arithmetic on the table) ([V2V4Real paper](https://arxiv.org/abs/2303.07601), Table 3, p.7). |

**Unverified**

- Code-only: BEV or 3D overlap; the matching rule; AP integration; ranking per frame or over the test set; the range test and how the bin distance is measured; any difficulty, don't-care or minimum-point filter (none is described); pooling over the 3,986 frames or averaging per frame or scene, and how Overall relates to the bins.
- Not stated: whether No Fusion is scored against the same two-vehicle ground truth, and whether boxes with no LiDAR point from either vehicle are kept. Code-only or config-only: the confidence and NMS thresholds, any per-frame cap, any communication-range cut-off.
- The CVPR camera-ready PDF could not be opened (HTTP 403); paper statements are from arXiv 2303.07601v2. Any readme shipped with the KITTI-format data release was not read.

### B.4 DAIR-V2X

| Topic | What the sources say |
| --- | --- |
| Overlap | The VIC3D benchmark on DAIR-V2X-C reports AP_3D and AP_BEV side by side at IoU 0.5, without saying how the overlap is computed ([DAIR-V2X paper](https://arxiv.org/abs/2204.05575), Table 3, Sec. 5.1). FFNet, by the same lead author, describes the metrics as KITTI's: BEV and 3D mAP at IoU 0.5 and 0.7 ([FFNet paper](https://arxiv.org/abs/2303.10552), Sec. 4.2; Table 1). |
| Ground truth | The vehicle is the ego and the infrastructure the helper; the ground truth is the union of vehicle-view and infrastructure-view ground truth at the vehicle timestamp ([DAIR-V2X paper](https://arxiv.org/abs/2204.05575), Sec. 5.1; Sec. 4.1 'Ground Truth', Eq. (1)). The cooperative labels were built by converting infrastructure boxes into the vehicle frame and adding one wherever no vehicle box with the same location and category existed, then adjusting by hand ([DAIR-V2X paper](https://arxiv.org/abs/2204.05575), Sec. 3.2 'Annotation'). VIC3D has no difficulty split, and no occlusion, truncation, visibility or minimum-point rule is stated for it ([DAIR-V2X paper](https://arxiv.org/abs/2204.05575), Table 3 vs Tables 4 and 5). |
| Range | The evaluation area is [0, -39.12, 100, 39.12] around the ego, and objects outside it are removed ([DAIR-V2X paper](https://arxiv.org/abs/2204.05575), Sec. 4.2 'Average Precision'); with x forward from the LiDAR, it lies in front of the vehicle (an inference) ([DAIR-V2X paper](https://arxiv.org/abs/2204.05575), Sec. 3.1 'Coordinate'). The documented example uses y within ±39.68 m, so the sources disagree on y ([OpenDAIRV2X docs, get_started.md](https://raw.githubusercontent.com/AIR-THU/DAIR-V2X/main/docs/get_started.md), 'Evaluation', second command block). Distance bands ([0,30], [30,50], [50,100]) and the full [0,100] are separate evaluation runs ([OpenDAIRV2X late-fusion PointPillars README](https://raw.githubusercontent.com/AIR-THU/DAIR-V2X/main/configs/vic3d/late-fusion-pointcloud/pointpillars/README.md), 'Evaluation'); for V2X-Seq-SPD the bounds are documented as x_min and x_max, so a band is an interval of forward x, not radial distance (applying this to DAIR-V2X-C is an inference) ([OpenDAIRV2X docs, get_started_spd.md](https://raw.githubusercontent.com/AIR-THU/DAIR-V2X/main/docs/get_started_spd.md), 'Evaluation', EXTEND_RANGE_START). |
| Classes | FFNet counts only Car ([FFNet paper](https://arxiv.org/abs/2303.10552), Sec. 4.2). A `pred-class` argument selects the class; the one explicit documented command passes car, the other documented VIC3D commands are wrapper scripts that do not show the class, and every documented or pasted output seen is a car line ([OpenDAIRV2X docs, get_started.md](https://raw.githubusercontent.com/AIR-THU/DAIR-V2X/main/docs/get_started.md), 'Evaluation'). |
| Matching | FFNet counts a prediction as positive when its IoU is at least the threshold; nothing written gives the order, one-to-one rule, duplicates or ties ([FFNet paper](https://arxiv.org/abs/2303.10552), Appendix A.2, after Eq. (7)). |
| Ranking scope | Not written (Unverified). |
| AP integration | The paper introduces AP with a citation to the PASCAL VOC paper and does not say how it is integrated ([DAIR-V2X paper](https://arxiv.org/abs/2204.05575), Sec. 4.2 'Average Precision'). FFNet states an 11-point interpolated AP ([FFNet paper](https://arxiv.org/abs/2303.10552), Appendix A.2, Eq. (7)). Two open user reports with no reply say the routine integrates over a recall interval narrower than [0, 1] ([DAIR-V2X issue #101](https://github.com/AIR-THU/DAIR-V2X/issues/101), original post) and omits the area between recall 0 and the first recall ([DAIR-V2X issue #92](https://github.com/AIR-THU/DAIR-V2X/issues/92), original post). |
| Aggregation | The documented evaluator adds each frame's predictions and labels and prints AP once after all frames; pooling across frames is the natural reading but an inference, and the ranking is not stated ([OpenDAIRV2X docs, customized_models.md](https://raw.githubusercontent.com/AIR-THU/DAIR-V2X/main/docs/apis/customized_models.md), 'Evaluator'). FFNet's mAP averages over classes, so with Car alone it is the car AP (an inference) ([FFNet paper](https://arxiv.org/abs/2303.10552), Appendix A.2, last sentence). Other papers use a different protocol on the same data (B.9). |

**Unverified**

- Code-only or not stated: how the BEV and 3D overlaps are computed, and which is the headline; matching beyond the IoU test; ranking per frame or over the set; the integration rule (FFNet and two user reports conflict).
- Code-only: the range test (centre or corners), whether the z interval is enforced, ±39.68 m against ±39.12 m; whether van, truck and bus are merged into car, and what the converter's class-merge option maps. The documentation portal (thudair.baai.ac.cn) could not be reached.
- Not stated for VIC3D: rules on ignored or don't-care boxes, detections that match ignored objects, occlusion, truncation or minimum points; whether Overall and the bands use the same frames, and how frames with no ground truth in a band are handled; any score threshold or NMS. The VIC3D Challenge 2022 ranking rule: source inaccessible (HTTP 403).

### B.5 KITTI

| Topic | What the sources say |
| --- | --- |
| Overlap | 3D bounding-box overlap of 70% for cars and 50% for pedestrians and cyclists ([KITTI 3D benchmark page](https://www.cvlibs.net/datasets/kitti/eval_object.php?obj_benchmark=3d), intro); the BEV benchmark uses bird's-eye-view overlap with the same thresholds ([KITTI BEV benchmark page](https://www.cvlibs.net/datasets/kitti/eval_object.php?obj_benchmark=bev), intro). The inequality is worded inconsistently: the readme says larger than 50%, the 2012 paper more than 50% in one place and at least 50% in another ([KITTI paper (CVPR 2012)](https://www.cvlibs.net/publications/Geiger2012CVPR.pdf), Sec. 2.5, p.5; Sec. 3.4, p.7). |
| Boxes evaluated | Car, Pedestrian and Cyclist; a Van is not a false positive for Car, nor a sitting person for Pedestrian ([KITTI devkit readme (third-party mirror)](https://raw.githubusercontent.com/kl456123/kitti_devkit/master/readme.txt), '3D Object Detection Benchmark'). Easy needs a 2D box at least 40 px high, full visibility and at most 15% truncation; Moderate 25 px, partly occluded, 30%; Hard 25 px, difficult to see, 50% ([KITTI 3D benchmark page](https://www.cvlibs.net/datasets/kitti/eval_object.php?obj_benchmark=3d), 'Difficulties are defined as follows'). There is no range in metres; far objects are filtered by 2D box height, ground truth exists only for objects on the image plane, and detections off it are not ignored, so they can become false positives ([KITTI 3D benchmark page](https://www.cvlibs.net/datasets/kitti/eval_object.php?obj_benchmark=3d), intro). The 3D and BEV pages say objects in don't-care areas are not false positives ([KITTI 3D benchmark page](https://www.cvlibs.net/datasets/kitti/eval_object.php?obj_benchmark=3d), intro); a third-party paper reports that the scripts do not apply DontCare in BEV and 3D ([Complexer-YOLO paper](https://arxiv.org/abs/1904.07537), Sec. 4.2, p.6). On the 2D page, detections smaller than the difficulty's minimum size are not false positives ([KITTI 2D benchmark page](https://www.cvlibs.net/datasets/kitti/eval_object.php?obj_benchmark=2d), paragraph on the PASCAL criteria). |
| Matching | The KITTI paper describes assigning detections to ground-truth labels iteratively, starting with the largest overlap, with further detections of a detected object counted as false positives ([KITTI paper (CVPR 2012)](https://www.cvlibs.net/publications/Geiger2012CVPR.pdf), Sec. 2.5, p.5). The pages refer to the PASCAL criteria, under which detections are assigned in order of decreasing confidence ([VOC paper (IJCV 2010)](http://host.robots.ox.ac.uk/pascal/VOC/pubs/everingham10.pdf), Sec. 4.2, p.314). |
| Ranking scope | MonoDIS describes KITTI's AP at the level of the whole dataset: one confident correct detection per difficulty in the entire dataset gives about 9% under the 11-point AP ([MonoDIS paper](https://arxiv.org/abs/1905.12365), Sec. 1, p.2; Sec. 6). KITTI's own pages do not say that detections are ranked or pooled over the whole test set; one curve per class and difficulty is an inference. |
| AP integration | Since 08.10.2019, 40 recall positions instead of 11 ([KITTI 3D benchmark page](https://www.cvlibs.net/datasets/kitti/eval_object.php?obj_benchmark=3d), 'Note 2'). The 11-point AP is the mean over recalls 0, 0.1, ..., 1 of the maximum precision at any recall at or above each; the 40-point AP averages over 1/40, ..., 1, without recall 0. With 11 points one correct detection gives 1/11 for the whole split, the stated reason for the change ([MonoDIS paper](https://arxiv.org/abs/1905.12365), Sec. 6, p.7; Sec. 9, p.11). |
| Aggregation | AP is computed per difficulty ([MonoDIS paper](https://arxiv.org/abs/1905.12365), Sec. 6, p.7). Methods are ranked by Moderate, with one table per class and no average over difficulties or classes ([KITTI 3D benchmark page](https://www.cvlibs.net/datasets/kitti/eval_object.php?obj_benchmark=3d), sentence after the difficulty list; results headings). |

**Unverified**

- Code-only: whether the official overlap uses yaw-rotated boxes (only MV3D describes its own metrics as oriented) and its formula; tie-breaking; which box a detection overlapping several gets; confidence order (PASCAL wording) or overlap order (KITTI paper wording), neither description being specific to the 3D or BEV benchmarks; how matching is repeated per score threshold; how the recall positions become score thresholds and how precision is taken at each; whether DontCare applies in 3D and BEV (the official page and Complexer-YOLO disagree).
- Not stated in KITTI's own pages or readme: that detections are ranked or pooled over the whole test set; that ground truth outside the evaluated difficulty is ignored rather than missed in 3D and BEV; a minimum-points rule; the mapping of occlusion codes to difficulty levels; detections matching Truck, Tram or Misc; camera or Velodyne coordinates for the overlap; any score threshold or NMS.

### B.6 PASCAL VOC and COCO

| Topic | What the sources say |
| --- | --- |
| Overlap | VOC: intersection over union of the two boxes must exceed 50%, one threshold ([VOC2012 devkit document](http://host.robots.ox.ac.uk/pascal/VOC/voc2012/devkit_doc.pdf), Sec. 4.4, Eq. (1), p.13). COCO: the headline AP averages ten IoU thresholds from 0.50 to 0.95; AP at 0.50, which COCO calls the PASCAL VOC metric, and at 0.75 are reported separately ([COCO detection evaluation page](https://cocodataset.org/dataset/detection-eval.htm), Sec. 2, metric list). |
| Boxes evaluated | VOC: objects flagged difficult are discarded, and no penalty is incurred for detecting them ([VOC paper (IJCV 2010)](http://host.robots.ox.ac.uk/pascal/VOC/pubs/everingham10.pdf), Sec. 3.3, p.309; [VOC2012 devkit document](http://host.robots.ox.ac.uk/pascal/VOC/voc2012/devkit_doc.pdf), Sec. 4.4, p.14). COCO: crowd regions are ignored ([COCO paper](https://arxiv.org/abs/1405.0312v3), end of Sec. 4.3, p.6); detections matched to ignore-flagged objects count as neither false nor true positives, stated for the analysis curves, not the headline AP ([COCO detection evaluation page](https://cocodataset.org/dataset/detection-eval.htm), Sec. 4, item 4). Size ranges are by pixel area, with no distance range; at most 100 detections per image are considered ([COCO detection evaluation page](https://cocodataset.org/dataset/detection-eval.htm), Sec. 2, 'AP Across Scales', notes 4 and 6). |
| Matching | VOC assigns detections in order of decreasing confidence ([VOC paper (IJCV 2010)](http://host.robots.ox.ac.uk/pascal/VOC/pubs/everingham10.pdf), Sec. 4.2, p.314), and "Multiple detections of the same object in an image are considered false detections" ([VOC2012 devkit document](http://host.robots.ox.ac.uk/pascal/VOC/voc2012/devkit_doc.pdf), Sec. 4.4, p.14). ILSVRC, which adopted the VOC criterion, writes it as an algorithm: per image and class, by descending score, each detection takes the still-unmatched box with the highest IoU among those at or above the threshold, else it is a false positive ([ILSVRC paper](https://arxiv.org/abs/1409.0575v3), Sec. 4.3, Algorithm 2, pp.16-17). |
| Ranking scope | VOC builds a class's precision/recall curve from the method's ranked output over the test set, submitted as one file per class ([VOC paper (IJCV 2010)](http://host.robots.ox.ac.uk/pascal/VOC/pubs/everingham10.pdf), Sec. 4.2, p.313; [VOC2012 devkit document](http://host.robots.ox.ac.uk/pascal/VOC/voc2012/devkit_doc.pdf), Sec. 4.3, p.13). COCO matches per image and aggregates precision and recall over the whole dataset ([COCO detection evaluation page](https://cocodataset.org/dataset/detection-eval.htm), Sec. 3). |
| AP integration | VOC2007: 11-point interpolated AP ([VOC paper (IJCV 2010)](http://host.robots.ox.ac.uk/pascal/VOC/pubs/everingham10.pdf), Sec. 4.2, Eqs. (1)-(2), p.313). From VOC2010: precision is made monotonically decreasing and AP is the area under it, sampled at every distinct recall ([VOC2010 devkit document](http://host.robots.ox.ac.uk/pascal/VOC/voc2010/devkit_doc_08-May-2010.pdf), Sec. 3.4.1, p.10). COCO evaluates precision at 101 recall thresholds and does not say in words how it is read off ([COCO detection evaluation page](https://cocodataset.org/dataset/detection-eval.htm), Sec. 3, recThrs and precision). |
| Aggregation | VOC: one curve and one AP per class over the test set; the devkit documents define no class average ([VOC paper (IJCV 2010)](http://host.robots.ox.ac.uk/pascal/VOC/pubs/everingham10.pdf), Sec. 4.2, p.313). COCO averages AP over categories and the ten thresholds ([COCO detection evaluation page](https://cocodataset.org/dataset/detection-eval.htm), Sec. 2, notes 1 and 2). |

**Unverified**

- Code-only: VOC's choice of box when several satisfy the criterion, ties, and what a detection on a difficult object becomes (the only written algorithm is ILSVRC's); COCO's matching rule, its reading of precision at each recall threshold (only TIDE, a secondary source, describes it), whether its IoU test is inclusive, crowd matching, and how area ranges apply.
- Neither benchmark documents a switch between per-frame and whole-test-set ranking, or a distance range. The COCO `#detection-eval` URL served only the navigation shell, so its content fragment was read; the VOC devkit PDFs were read instead of the HTML.

### B.7 nuScenes

| Topic | What the sources say |
| --- | --- |
| Overlap | No IoU: a match is a 2D centre distance on the ground plane within 0.5, 1, 2 or 4 m; the true-positive errors use one 2 m threshold ([nuScenes paper](https://arxiv.org/abs/1903.11027), Sec. 3.1, 'Average Precision metric' and 'True Positive metrics'). |
| Boxes evaluated | Ten classes, from the 23 annotated ones by merging similar classes and removing rare ones ([nuScenes paper](https://arxiv.org/abs/1903.11027), Sec. 3.1; Supplementary A). The range is an upper bound per class: 30 m for barrier and traffic cone, 40 m for bicycle, motorcycle and pedestrian, 50 m for car, bus, construction vehicle, trailer and truck ([nuScenes detection task page](https://www.nuscenes.org/object-detection), detection ranges table). It is a range from the ego vehicle, how measured is not specified, and ground-truth and predicted boxes beyond it are removed, as are ground-truth boxes with no lidar or radar point; predictions are not filtered by points ([nuScenes detection README](https://github.com/nutonomy/nuscenes-devkit/blob/master/python-sdk/nuscenes/eval/detection/README.md), paragraph before the range table; 'Preprocessing', bullets 1 and 3). According to a collaborator in an issue thread, a prediction on a zero-point box is a false positive ([nuscenes-devkit issue #807](https://github.com/nutonomy/nuscenes-devkit/issues/807), first reply). Every sample of the evaluation set must appear in the results ([nuScenes detection README](https://github.com/nutonomy/nuscenes-devkit/blob/master/python-sdk/nuscenes/eval/detection/README.md), 'Results format'). |
| Matching | A prediction is matched to the ground-truth box of smallest centre distance within the threshold ([nuScenes detection README](https://github.com/nutonomy/nuscenes-devkit/blob/master/python-sdk/nuscenes/eval/detection/README.md), 'Average Precision metric'), per class ([nuScenes paper](https://arxiv.org/abs/1903.11027), Sec. 3.1). According to a collaborator, a matched box is taken within its sample, and two predictions on one box do not both count as true positives; the thread does not literally say the duplicate is a false positive ([nuscenes-devkit issue #992](https://github.com/nutonomy/nuscenes-devkit/issues/992), first reply). |
| Ranking scope | The README does not say; it stores the results of an evaluation set in one file ([nuScenes detection README](https://github.com/nutonomy/nuscenes-devkit/blob/master/python-sdk/nuscenes/eval/detection/README.md), 'Results format'). |
| AP integration | The normalised area under the precision/recall curve for recall and precision above 10%; a class with no point there gets AP 0 ([nuScenes paper](https://arxiv.org/abs/1903.11027), Sec. 3.1, 'Average Precision metric'). |
| Aggregation | mAP is the unweighted mean over classes and the four thresholds ([nuScenes paper](https://arxiv.org/abs/1903.11027), Sec. 3.1, Eq. (1)). mAP is from evaluating the entire val set; neither per-scene averaging nor pooling into one curve is stated ([nuScenes paper](https://arxiv.org/abs/1903.11027), Supplementary, Table 6 caption). |

**Unverified**

- Code-only: processing in confidence order, ties, strict or inclusive threshold (a duplicate being a false positive is only implied); the recall sample points, interpolation and envelope; ranking per frame or over the split; how the distance to the ego is measured (centre or corners, 2D or 3D, origin); the geometric bike-rack test.
- Not stated: that all keyframes are pooled into one curve per class and threshold; a visibility filter, don't-care regions, the frame in which the centre distance is computed, or an evaluator-side score threshold or NMS. The nuScenes forum could not be read (timeout); the arXiv versions of the paper were read.

### B.8 Griffin

| Topic | What the sources say |
| --- | --- |
| Overlap | Griffin defines its metric only as AP from the nuScenes benchmark, and states no IoU type, centre distance or threshold ([Griffin paper (v2)](https://arxiv.org/abs/2503.06983v2), Sec. 4.3, p.5). UniV2X, which Griffin re-implements as a baseline, defines its own DAIR-V2X AP with a prediction positive at IoU of at least 0.5, Car only; nothing in Griffin's text adopts it, and the text cannot confirm which rule the released code applies ([UniV2X paper](https://arxiv.org/abs/2404.00717), Appendix 'Evaluation Metric Details', Eq. (7), p.12). |
| Boxes evaluated | The ego is the ground vehicle; the fused result is evaluated against the objects of the vehicle and drone union whose centre lies inside the region of interest; whether detections are filtered the same way is not said ([Griffin paper (v2)](https://arxiv.org/abs/2503.06983v2), Sec. 4.1, GT_detect formula, p.5). The region is a 102.4 m square centred on the ego ([Griffin paper (v2)](https://arxiv.org/abs/2503.06983v2), Appendix B.3, p.11). Labels are occlusion-filtered: an object with low visibility from an agent is removed from that agent's labels, the cooperative ground truth keeps objects visible to either, and no cut-off is given ([Griffin paper (v2)](https://arxiv.org/abs/2503.06983v2), Sec. 3.2, 'Occlusion-Aware Annotation', p.4). According to the owner, No Fusion is the model that uses the ground vehicle only ([Griffin issue #8](https://github.com/wang-jh18-SVM/Griffin/issues/8), owner comment). |
| Matching | No rule of its own; the cited nuScenes paper matches per class ([nuScenes paper](https://arxiv.org/abs/1903.11027), Sec. 3.1, 'True Positive metrics'). |
| Ranking scope | Not written (Unverified). |
| AP integration | Inherited by citation, not restated: nuScenes' normalised area above 10% recall and precision ([nuScenes paper](https://arxiv.org/abs/1903.11027), Sec. 3.1). UniV2X's own AP is 11-point interpolated; written sources cannot confirm which Griffin's code follows ([UniV2X paper](https://arxiv.org/abs/2404.00717), Appendix, Eq. (7), p.12). |
| Aggregation | One car AP per method and subset (Griffin-25m, -40m, -55m, -Random), each on its own validation split; pooling over frames or averaging per scene is not stated ([Griffin paper (v2)](https://arxiv.org/abs/2503.06983v2), Table 3, p.6; Appendix B.3, p.11). |

**Unverified**

- Not stated or code-only: BEV IoU, 3D IoU or centre distance and the thresholds; the matching rule; AP integration (Griffin's text points by citation to the nuScenes definition; written sources cannot confirm whether the released code follows it or a UniV2X-style 11-point AP); ranking per frame or over the split; whether the square applies to detections, its frame and vertical limits; the class mapping; the visibility cut-off and any minimum-point or don't-care rule.
- Not stated: pooling over frames or averaging per scene; the frame of comparison; any drone-to-vehicle range limit; which ground-truth set No Fusion is scored against; score threshold, NMS, box cap, how latency is realised and the noise seeds; whether the nuScenes devkit performs the evaluation.

### B.9 Later OpenCOOD-style work

| Topic | What the sources say |
| --- | --- |
| Overlap | CoAlign reports AP at IoU 0.5 and 0.7 without saying BEV or 3D ([CoAlign paper](https://arxiv.org/abs/2211.07214), Sec. V-C; Table I); its author says in an issue thread that the AP is 3D ([CoAlign issue #6](https://github.com/yifanlu0227/CoAlign/issues/6), reply by yifanlu0227). SparseAlign's main AP uses BEV IoU, with 3D-IoU AP in its supplement ([SparseAlign paper](https://arxiv.org/abs/2503.12982), Supplementary Sec. E, p.13). OpenCOOD-Air describes the OPV2V protocol as a 2D evaluation of 3D boxes projected onto the BEV plane ([OpenCOOD-Air paper](https://arxiv.org/abs/2603.13919), Sec. IV-C, p.6). V2X-Sim evaluates BEV boxes at 0.5 and 0.7 ([V2X-Sim paper](https://arxiv.org/abs/2202.08449), Sec. IV-A, pp.4-5). |
| Boxes evaluated | CoAlign evaluates OPV2V on x in [-140, 140] m and y in [-40, 40] m, and DAIR-V2X on x in [-100, 100] m and y in [-40, 40] m ([CoAlign paper](https://arxiv.org/abs/2211.07214), Sec. V-A, p.5). CoAlign and Where2comm replace the original DAIR-V2X labels, which cover only the vehicle camera's view, with labels completed to 360 degrees ([CoAlign paper](https://arxiv.org/abs/2211.07214), Sec. V-A, p.5; also Where2comm Sec. 5.1). V2X-Real evaluates in the ego frame and takes the ego's annotation where agents' boxes disagree ([V2X-Real paper](https://arxiv.org/abs/2403.16034), Sec. 4.2 'Ground truth', p.11). |
| Matching | Not written (Unverified). |
| Ranking scope | SparseAlign says its OPV2V table uses "frame-wise local score sorting for AP, same to the official benchmark", and its other results global sorting ([SparseAlign paper](https://arxiv.org/abs/2503.12982), Table 1, footnote 2, p.6). CoAnchor describes global sorting, where all boxes of the split are pooled and sorted into one curve per threshold, and scene-wise sorting, and says absolute AP under the two can differ ([CoAnchor paper](https://arxiv.org/abs/2608.21055), Appendix A.1, p.11). According to the CoAlign author, arXiv v2 used OpenCOOD's evaluation utilities, which he says do not sort detections by score, and this was fixed for v3; the thread reports CoAlign's DAIR-V2X AP at 0.5 without noise going from 0.598 to 0.746 ([CoAlign issue #4](https://github.com/yifanlu0227/CoAlign/issues/4), reply by yifanlu0227). |
| AP integration | Not written (Unverified). |
| Aggregation | V2X-Real takes mAP as the unweighted mean of per-class AP ([V2X-Real paper](https://arxiv.org/abs/2403.16034), Sec. 4.2, Eq. (1), p.11). |

**Unverified**

- Code-only: the matching rule; AP integration (a secondary study on HEAL uses the 11 VOC recall positions for its own experiments only); BEV or 3D IoU for Where2comm, CoAlign, CoBEVT, HEAL and V2X-Real (the CoAlign author says 3D, SparseAlign and OpenCOOD-Air say BEV); the ranking scope of HEAL, Where2comm, CoBEVT, V2X-Real and V2X-Sim; how each range is applied; which ground-truth set a fused result is scored against, and how the test ego is chosen, in Where2comm, CoAlign, CoBEVT and HEAL.
- Not described: evaluation-time ignore, difficulty or minimum-point rules; pooling or per-scene averaging in the primary papers (only CoAnchor, a secondary source, contrasts them); score and NMS thresholds. No paper was found that says its AP follows OPV2V or OpenCOOD; CoAlign issue #4 says the opposite for v3.

---

## Part C. Where Coperceive differs

Each row of C.1 is one place where what Coperceive computes (Part A) is not what a benchmark of Part B computes, or where a reader would assume something the evaluator does not do. The Status column is one of:

- **intended (decision of 2026-11-16)**: the protocol decided on 2026-11-16 and built in PR #165 (A.2, A.12);
- **intended**: a choice, kept from before or made in a fix of PR #165 (C.2), and stated here so nobody has to guess;
- **difference, documented**: not a convention anyone chose; stated so a number is read correctly, and not changed;
- **PM decision**: not settled. Nothing was changed in the evaluator, and a number that depends on it should be published with the row in mind.

Defects fixed in PR #165 are in C.2, defects found and not fixed in C.3. "Unverified" in the benchmark column means Part B could not confirm it from a paper or documentation; it does not mean the benchmark has no rule.

### C.1 Differences in what is measured

| # | Topic | Coperceive (Part A) | Benchmarks (Part B) | Effect on a number | Status |
| --- | --- | --- | --- | --- | --- |
| 1 | Overlap measure | BEV IoU of the two footprints, or centre distance (A.3). No 3D IoU (A.10); a 3D-IoU AP is outside PR #165 (ADR 0016, question 1). Every AP is labelled `AP@<t> (BEV)`, or `AP@<t> m (centre)` (A.12). | OPV2V: IoU on the x-y plane, at 0.5 and 0.7 (B.1). V2XSet and V2V4Real: IoU at 0.5 and 0.7, BEV or 3D not stated (B.2, B.3). DAIR-V2X: AP_3D and AP_BEV side by side (B.4). KITTI: separate 3D and BEV benchmarks (B.5). nuScenes: centre distance at 0.5, 1, 2 and 4 m (B.7). Later work disagrees on BEV or 3D (B.9). | For any pair the 3D IoU is at most the BEV IoU (the height overlap is at most either box's height), so a pair that passes in 3D passes in BEV, and a BEV AP is usually the higher number. A height or z error costs nothing here. A BEV AP is not comparable with a 3D AP. | intended (decision of 2026-11-16) |
| 2 | Ranking scope | `ranking_scope: per_frame` by default: each scene's predictions by time, then by score within a time, and scenes joined in scene order with no sort over the whole list. `pooled`, one sort by score over every prediction, is an option and was the only rule before 2026-11-16 (A.2, A.5, A.8). | OpenCOOD's default, which the OPV2V paper reports, sorts scores only locally; the global sort is an opt-in flag (B.1). That "locally" means one frame rests on the issue reporter's description (sorting within a single batch) and on a batch being one frame, which is code-only: **unverified** (B.1). In July 2023 the OpenCOOD maintainer said all methods using OpenCOOD use the local sort (B.2); later OpenCOOD-style work departs from it (B.9). V2X-ViT does not mention sorting (B.2). SparseAlign calls the official OPV2V benchmark frame-wise (B.9). KITTI: one curve over the whole split according to a third-party paper (MonoDIS); KITTI's own pages do not state it (B.5). VOC and COCO: over the test set (B.6). V2V4Real, DAIR-V2X, nuScenes, Griffin: not stated (B.3, B.4, B.7, B.8). | Under `per_frame` a prediction's place in the walk depends on its time, so the same detections can give another AP than under `pooled` (A.5). Neither is always higher: one OpenCOOD report has AttFusion AP50 0.905 local and 0.936 global (B.1), while on the demo run `per_frame` raised mAP@0.5 (BEV) of cav-a from 0.516 to 0.528 and lowered cav-b from 0.567 to 0.563 (CHANGELOG). Absolute AP under global and scene-wise sorting can differ (CoAnchor, B.9), and `per_frame` and `pooled` differ in the same way (A.5), so quote the protocol line with the number (A.12). | intended (decision of 2026-11-16) |
| 3 | Ground truth of a fused output | Under `gt_scope: connected_union` (default) a `fused` output is scored against its agent's own boxes plus the boxes of each agent within `comm_range_m` (70 m) that hold a LiDAR return of the ego or a connected agent; a box two agents annotated counts once, and the ego's box is the one kept. Every other output, and every output under `ego_only`, is scored against its own agent's boxes (A.4). **Unverified**, so our choices: the range is horizontal; the ego's boxes are kept without a point test; the A.9 pairing, which does not compare categories, decides what is one object (A.4). | OPV2V: the vehicles hit by at least one LiDAR point from any connected vehicle; collaborators beyond 70 m ignored (B.1). V2X-ViT: the same rule and range; that the set is the union over agents within 70 m is an inference (B.2). V2V4Real: both vehicles' boxes in the ego frame, the ego's annotation for an object both annotated; no communication-range cut-off is described (code-only or config-only, **unverified**) (B.3). DAIR-V2X: the union of vehicle and infrastructure ground truth, an infrastructure box added where no vehicle box has the same location and category (B.4). Griffin: objects visible to either agent inside the region (B.8). Which set No Fusion is scored against: not stated (B.1, B.2, B.3). | A correct fused detection of an object only a connected agent annotated is a true positive, the object is in `n_gt`, and it can be `recovered` (A.4, A.9). A `single` output is still scored against its own agent's boxes, so `single` and `fused` of one agent have different `gt`, and a `single` AP is not the No Fusion number of a table that scores No Fusion against the union (A.4, A.9). | intended (decision of 2026-11-16) |
| 4 | Evaluation range | None by default. `max_range_m` is a horizontal radius around the agent's body origin, tested at the box centre: a ground-truth box beyond it is ignored and a prediction beyond it is out of range (A.3, A.4 rule 1). `comm_range_m` only decides which agents join a fused output's ground truth (A.4). | OPV2V and V2XSet: x in [-140, 140] m and y in [-40, 40] m around the ego (B.1, B.2); OpenCOOD's ranges are in LiDAR coordinates, according to its maintainer (B.1). V2V4Real: x in [-100, 100] m, y in [-40, 40] m (B.3). DAIR-V2X: x from 0 to 100 m, y within ±39.12 m (±39.68 m in the docs) (B.4). Griffin: a 102.4 m square, ground truth by centre (B.8). nuScenes: a per-class distance from the ego, 30 to 50 m, for ground truth and predictions (B.7). KITTI: no range in metres (B.5). Whether the OPV2V-style ranges filter ground truth, detections or both, and by centre or corners: **unverified** (B.1, B.2, B.3). | A disc and a rectangle keep different boxes. Without `max_range_m` every annotation counts, however far (A.10). For an aerial agent "within 50 m" is a 50 m disc on the ground, whatever the altitude (A.3). A rectangular range in the agent frame cannot be expressed today. | PM decision |
| 5 | AP integration | All-point: the area under the monotone precision envelope at every recall step, with nothing added at recall 0 (A.5). | VOC from 2010: the same all-point area; VOC2007: 11 points (B.6). KITTI: 40 recall positions since 2019, 11 before (B.5). COCO: 101 recall thresholds (B.6). nuScenes: normalised area above 10% recall and precision (B.7). DAIR-V2X: 11 points according to FFNet, contested by user reports (B.4). OPV2V, V2XSet, V2V4Real and later OpenCOOD-style work: code-only, **unverified** (B.1, B.2, B.3, B.9). | For the same curve, sampling at fixed recall positions gives another number. The 11-point rule includes recall 0, so one correct detection already scores 1/11 (B.5); all-point AP adds nothing at recall 0 (A.5). nuScenes leaves out the part below 10% (B.7). | intended |
| 6 | A second detection of a matched object | It is compared with the best ground-truth box that is still unmatched, so it can become a true positive on a neighbour (A.4 rule 3; golden cases D17, D18). | ILSVRC writes the same rule: each detection takes the still-unmatched box with the highest IoU among those at or above the threshold (B.6). VOC counts multiple detections of one object as false (B.6), KITTI likewise (B.5); according to a collaborator in an issue thread, nuScenes marks a matched box as taken within its sample (B.7). Whether VOC, COCO or OpenCOOD-style code assigns a duplicate to a neighbour: code-only, **unverified** (B.1, B.6). | In dense scenes a duplicate can be credited to the object next to it. | intended |
| 7 | Ignored ground truth | An ignored box competes like any other. A prediction whose best box is ignored becomes `ignored`, even when a kept box would also pass, and that kept box stays a miss. An ignored box is never marked matched, so it can absorb several predictions (A.4 rule 3 step 6; D19, D20). | VOC: difficult objects are discarded and detecting them costs nothing (B.6). COCO: detections matched to ignore-flagged objects are neither true nor false positives, stated for the analysis curves (B.6). KITTI: its 3D and BEV pages say objects in don't-care areas are not false positives; a third-party paper reports the scripts do not apply DontCare in BEV and 3D, so this is **unverified** (B.5). nuScenes: ground truth with no lidar or radar point is removed; according to a collaborator in an issue thread, a prediction on it is a false positive (B.7). What a detection that overlaps an ignored and a kept box becomes: code-only, **unverified** (B.5, B.6). | With `max_range_m` or `ignore_gt_visibility_below` set, recall and AP are lower next to ignored objects than under a rule that prefers kept boxes. The rule is pinned by `tests/test_eval_matching.py::test_the_best_box_being_ignored_leaves_the_other_gt_a_miss`. | PM decision |
| 8 | Equal scores | Row order of `boxes.parquet`, in matching and in the AP walk. Under `pooled` equal scores keep scene order, then row order; under `per_frame` only equal scores at one time of one scene meet (A.4 rule 2, A.5, A.8; D21, D22). Equal IoU or distance: the box earliest in the ground-truth set (A.4 rule 3 step 4). Whether to define an order-free rule is ADR 0016, question 3. | No benchmark in Part B documents a tie rule: code-only, **unverified** (B.5, B.6, B.7). | The same detections written in another order can give another AP (D21, D22). With distinct scores row order never changes a per-scene result or a per-output headline row (`tests/test_eval_properties.py::test_row_order_of_predictions_does_not_matter_when_scores_differ`). Under `per_frame` it can still change the row over every output: row order sets the order of a scene's outputs (A.0), and that row joins the outputs of a scene without a sort (A.8). Example: one scene, a true positive at 0.5 of one agent and a false positive at 0.9 of another, one object each: mAP 0.5 or 0.25 depending on which agent's row comes first (row 10). | PM decision |
| 9 | Scenes and agents without a prediction | A run covers a scene when it has a prediction file for it, even an empty one. Every output of the run is evaluated in every covered scene where its agent exists, and with no row there its ground truth counts as missed (A.0, A.8). A scene without a prediction file is not evaluated, and an agent with no row in any covered scene is not an output of the run (A.0, A.8). | KITTI computes one curve over the whole split according to MonoDIS, not KITTI's own pages (B.5), and VOC one AP per class over the test set (B.6). nuScenes requires every sample of the evaluation set in the results (B.7). DAIR-V2X's evaluator adds every frame before it prints AP (B.4). | Missed ground truth always counts in a covered scene; before 2026-11-16 it dropped out, always in the model's favour (C.2). A scene the model never finished has no prediction file and is still left out; `coperceive model run` writes one for every scene it finished (A.0). | intended |
| 10 | The row over every output | One curve per class over all agents and sources, each ground-truth box counted once per output scored against it. `recovered` and `lost_by_fusion` are counted once per scene and agent (A.8). | No benchmark in Part B reports such a number. | It is not the AP of any model output. The robustness sweep takes its mAP from this row (`coperceive/robustness/sweep.py::_evaluate_cell`). Under `per_frame` it depends on the order of a scene's outputs, which is their first occurrence in `boxes.parquet` (A.0, A.8), so writing the same rows in another order can change it (row 8). Merging the outputs of one frame by score before the walk would remove that; it is not done. | PM decision |
| 11 | Operating-point numbers | Precision, recall, `tp_errors` and the cooperative counts (`recovered`, `lost_by_fusion`, `only_other_agent`, `nobody`, `recovery_rate`) are taken at the display threshold and `score_threshold` (A.6, A.7, A.9). | Every benchmark in Part B reports AP (B.1 to B.9). nuScenes also reports true-positive errors, matched at one 2 m centre distance (B.7). No benchmark in Part B defines a cooperative count. | They move with `score_threshold` and with the score scale of the model. `recovered` and `lost_by_fusion` compare two outputs cut at the same score. Under `connected_union`, `recovered` also counts objects only a connected agent annotated (A.9), so it does not compare with a value computed under `ego_only`. Quote them with both thresholds and the protocol. | intended |
| 12 | Per-class thresholds | A class in `class_thresholds` is judged at its own values; every table still names the global threshold (A.2). | KITTI judges cars at 0.7 and pedestrians and cyclists at 0.5, with one table per class and no class average (B.5). nuScenes uses the same four distances for every class (B.7). | A column headed `mAP@0.5 (BEV)` can average a class judged at 0.25. Say so when `class_thresholds` is set. | intended |
| 13 | Heading | A box turned by 180° has the same footprint, so its IoU is 1 (A.3: the footprint is symmetric under a half turn; D07 covers only the ±180° wrap). The flip shows only in the yaw error, which is not taken modulo 180°: a flip gives 180 (A.3, A.7). | True of every overlap and centre-distance criterion of Part B (B.1 to B.9). | AP says nothing about heading direction. | intended |
| 14 | Tilted boxes | The footprint is the full `length × width` rectangle at the heading of the box x axis; roll and pitch are not projected (A.3). | OpenCOOD-Air describes the OPV2V protocol as 3D boxes projected onto the BEV plane (B.9). How the OPV2V-style overlap is computed: code-only, **unverified** (B.1, B.2, B.3). | Small on roads; larger for boxes expressed in a tilted aerial frame. | difference, documented |
| 15 | Breakdown bins | A bin of `eval compare --by` is not matched again. Ground truth outside the bin is treated as ignored, a true positive counts in the bin of the box it was paired with, and an unmatched prediction counts as a false positive in its own distance, class or scene bin and in every occlusion and agent-count bin. Distance is horizontal, from the scene's first agent (A.11). | COCO, VOC and KITTI apply their ignore rules during matching (A.11; B.5, B.6). DAIR-V2X evaluates each distance band as a separate run with its own range, and its bands may be intervals of forward x (B.4). V2V4Real's distance bins are defined only by their column labels (B.3). | A bin's AP can differ from a separate evaluation over the bin alone, because a prediction paired with a box of another bin is ignored there instead of being matched again (A.11). Both rules are our choices (A.11). | intended |

### C.2 Defects fixed in PR #165

Each fix has a test that fails on the code before it. All but the last have a CHANGELOG entry under `[Unreleased]` that names the reported numbers that change. On the demo run (`coperceive demo create`) only the per-bin AP fix changes a printed number; the IoU fix changes `metrics.json` in the last bits (CHANGELOG). The protocol changes of the same PR are decisions, not fixes: C.1 rows 1 to 3.

| Defect | What was wrong | Now | Test |
| --- | --- | --- | --- |
| IoU precision | Footprints were clipped and their areas summed in absolute world coordinates. Against an exact polygon overlay the IoU was off by up to 1e-13 within 30 m of the origin, 4e-5 at 300 km, 8e-4 at (400 km, 4000 km) and 4e-2 at 10,000 km. | Both footprints are taken about the midpoint of the pair (A.3, numerical note). The error is under 3e-15 at every offset. | `tests/test_eval_geometry.py::test_iou_does_not_depend_on_where_the_world_origin_is` |
| `recovered` in the last `all` row | The row over every output summed `recovered` and `lost_by_fusion` once per output: twice the number of boxes for an agent with a `single` and a `fused` output, three times with a third source. | Once per scene and agent (A.8, golden case D34). | `tests/test_eval_run.py::test_the_all_row_counts_a_recovered_box_once_per_agent` |
| Mixed configs in one run | `eval run --scenes ... --config ...` can leave a run whose scenes were judged by different rules; the headline pooled them, with exit code 0. | `eval report`, `eval compare` and the robustness sweep refuse the run, name two of its scenes, and say to run `eval run` again over every scene with one config. | `tests/test_eval_run.py::test_a_run_whose_scenes_were_evaluated_with_different_configs_is_not_pooled` |
| `eval compare` across configs | Only the threshold lists of the runs were compared. Runs scored with another range, score threshold, class map or criterion were put side by side with nothing to show it. | A `note:` line names every config key that differs, with both values; the tables are still printed. Runs scored under a different `ranking_scope`, `gt_scope` or, under `connected_union`, `comm_range_m`, or a legacy run beside a current one, are refused (A.12). | `tests/test_eval_cli.py::test_compare_says_when_two_runs_were_evaluated_with_different_configs`, `tests/test_eval_cli.py::test_compare_refuses_runs_scored_under_different_protocols` |
| `criterion: centre` without `thresholds` | The IoU defaults `[0.5, 0.7]` were read as metres, an increasing list the centre rule forbids, and the report still said `mAP@0.5`. | Refused (A.2). | `tests/test_eval_config.py::test_centre_without_thresholds_is_refused` |
| A class named `mean` or `mean_model` | Its AP was overwritten by the mean in the `ap` table of `metrics.json`, then averaged into `mean_model` as that mean. | `eval run` refuses the scene and names the class. | `tests/test_eval_run.py::test_a_class_named_like_a_key_of_the_ap_table_is_refused` |
| Scenes and agents with no prediction | A scene the model found nothing in had no prediction file and was skipped, and an agent with no row in a scene had no output there. Their ground truth left `gt`, `miss`, recall and AP, always in the model's favour. | A run covers every scene it has a prediction file for, even an empty one, and `coperceive model run` and `predictions import --scene` write that file when the model found nothing. Every output of the run is evaluated in every covered scene where its agent exists (A.0). | `tests/test_eval_run.py::test_a_scene_the_model_found_nothing_in_still_counts_its_ground_truth`, `tests/test_eval_run.py::test_an_agent_the_model_found_nothing_for_in_one_scene_still_counts`, `tests/test_model_batch.py::test_a_finished_scene_with_no_box_still_gets_a_prediction_file` |
| Per-bin AP of `eval compare --by` | A prediction followed its ground truth into a bin only through the operating-point pair. A true positive scored under `score_threshold` landed in a bin of its own, so a distance bin could report AP 2.0, and occlusion and agent-count bins held no false positive, which made them a recall. | The AP of the bin, within [0, 1] (A.11). `matches.parquet` gains `gt_row_at_<i>`, and `eval compare --by` refuses an evaluation written before. Demo, cav-a, objects seen by 2 agents, under `ranking_scope: pooled`: AP@0.5 0.659 → 0.516 (CHANGELOG); 0.528 under the default `per_frame` (C.1 row 2). | `tests/test_eval_breakdown.py::test_a_distance_bin_never_reports_an_ap_above_one`, `tests/test_eval_breakdown.py::test_a_true_positive_under_the_score_threshold_follows_its_ground_truth`, `tests/test_eval_breakdown.py::test_every_bin_ap_lies_in_zero_one_and_class_bins_equal_the_pooled_ap` |
| `only_other_agent` and an ignored added box (before release) | A box a connected agent added has no `single` status, so a `fused` status of `ignored` (beyond `max_range_m` from the ego) was not checked, and the box counted in `only_other_agent` when the other agent's `single` output found it. Found by the differential suite (`tests/test_eval_differential.py`) before the connected union shipped, so no released number changed and there is no CHANGELOG entry. | A box ignored under either output falls in none of the counts (A.9). | `tests/test_eval_protocol.py::test_an_added_box_the_fused_pass_ignores_counts_in_no_cooperative_count` |

### C.3 Defects found and not fixed here

These are real and not intended, and this change could not settle them. Each needs a decision or belongs to another owner.

1. **Ground truth and poses are not held to the rules predictions are.** A prediction is refused unless its quaternion is unit within 1e-6 and its sizes are positive (A.1, A.3). An annotation or a pose with a non-unit quaternion, and an annotation with a zero or negative size, are read and evaluated. Measured by the readers: on a 4 × 2 m annotation at yaw 45°, a quaternion of norm 1.1 turns a perfect prediction into IoU 0.855 (the effect depends on the heading and is nil at yaw 0); a pose quaternion (1.01, 0, 0, 0) moves a body-frame box at 50 m by about 1 m, because placement rotates by q p q*, which scales by |q|², not by the matrix R(q) of A.1; an annotation with zero length or width can never be matched under `iou_bev` and is counted as a miss for every model. Whether any converted dataset holds such rows is unverified. The scene reader is outside `coperceive/evaluation/`.
2. **The paper export's metrics table prints the per-scene diagnostic** (A.5) as `AP@<t> (BEV)`, one row per scene, and has no pooled row, although A.8 calls the pooled value the headline. Its breakdown tables print one AP per run, at the first threshold with a value in string order of the keys. `coperceive/paper_export.py` is outside `coperceive/evaluation/`.
3. **The viewer's counts are not the evaluator's.** The status chips count drawn rows, and the `Only other agent` and `Nobody` filters keep boxes that have one of the two statuses while `metrics.json` requires both. The `Recovered` and `Only other agent` views also need `status_single` to be `miss`, so they leave out the boxes a connected agent added, which A.9 counts. The web viewer is outside this change.
4. **The app's scores view is not labelled.** `web/src/viewer/SidePanel.tsx` shows `mAP@<t>` with no "(BEV)" label and no protocol line (A.12). That file belongs to the viewer owner, and the change is routed (PR #165).
5. **`eval compare --from-scores` can change a breakdown AP.** `outcomes.json` stores scores rounded to six decimals, so scores that round to the same six decimals (which needs them to be less than 1e-6 apart) become ties and fall back to row order. Reproduced before the per-bin fix: a false positive at 0.9000001 before a true positive at 0.9000004 gives AP 1.0 from the scene and 0.5 with `--from-scores`.
6. **Nothing ties `metrics.json` to the `boxes.parquet` it was computed from.** If the predictions are replaced and `eval run` then fails for that scene, `eval report` keeps printing the old result.

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
