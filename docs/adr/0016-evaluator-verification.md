# ADR 0016 — Evaluator verification: cited spec, clean-room reference evaluator, differential tests

Status: proposed on 2026-11-15, for PM review before the build. Branch `agent/claude-eval-verification`, PR into `release/v0.1.2`. Nothing in `coperceive/` changes in this commit.

## Context

`coperceive eval run` writes the numbers that will go into papers: AP at IoU 0.5 and 0.7, precision, recall, `recovered` and `lost_by_fusion`. The product contract for them is [`docs/spec/predictions.md`](../spec/predictions.md) (ADR 0010). That document says what the evaluator is meant to do. Nothing yet proves that the code does it, and nothing compares it with what the field means by "AP@0.5" on OPV2V, V2XSet, V2V4Real or DAIR-V2X. A protocol mismatch would not fail a test; it would change a published number.

What a first read of `coperceive/evaluation/` shows (every line re-checked in the spec, step 1 below):

- One overlap measure, the BEV IoU of two rotated rectangles (`geometry.py`, `bev_iou`), plus a centre-distance criterion. There is **no 3D IoU**. The task brief says "AP@0.5 / AP@0.7 (BEV and 3D)"; see open question 1.
- Greedy matching by score, per agent, output, time and category, against the agent's own annotations (`matching.py`, `match_sample`). A prediction takes the best unmatched box, ignored boxes included.
- All-point AP: precision envelope made monotone from the right, area under the recall steps (`metrics.py`, `average_precision`). Per scene in `metrics.json`; pooled over scenes for the headline (`report.py`).
- Equal scores are ordered by file row, and by scene id when pooled, so a result can depend on the order of rows in `boxes.parquet`.
- `docs/eval.md` and `docs/spec/predictions.md` state that pooling is "how OpenCOOD and KITTI report AP". That sentence has no citation today.

## Decision

### 1. `docs/eval-spec.md`: what is computed, and how it compares

A new document, separate from `docs/spec/predictions.md`:

- **Part A, the computed definitions**, in plain mathematics, complete enough to implement from with no other file open: IoU type, footprint, yaw, world frame (ADR 0012), matching order and ties, ignore rules, range and class filtering, AP integration, per-sample, per-scene and pooled aggregation, the cooperative counts. Each section names the code it was read from as `file::symbol` anchors, and a test checks that every anchor exists. (The first draft of this ADR said `file:line`; line numbers rot with every edit, symbols do not.)
- **Part B, the field's conventions**, per benchmark (OPV2V/OpenCOOD, V2XSet/V2X-ViT, V2V4Real, DAIR-V2X, KITTI, PASCAL VOC/COCO, nuScenes, Griffin). Every claim has a source and a locator, and was re-opened by a second reader. A claim that could not be confirmed is marked **unverified**, never smoothed over.
- **Part C, the differences**, one row per place Coperceive differs, with which choice is intended. A difference nobody has decided yet is marked "PM decision" and is not changed in this PR.
- **Part D, golden cases**, a table with the arithmetic shown. A test parses the table and fails when a value in it disagrees with the test's constant, so the document cannot drift.

`docs/spec/predictions.md` stays the file-format contract. Where the two disagree, one of them has a bug, and the differential tests say which.

### 2. Clean room for the conventions

Sources are papers and written documentation only: paper text, documentation sites, READMEs, benchmark pages, devkit readme text, issue threads. Nobody working on this task opens the evaluation source of OpenCOOD, v2x-vit, V2V4Real, DAIR-V2X or the KITTI devkit. A fact that only their source code would settle goes into the spec as **unverified (code-only)**; see open question 2.

### 3. The reference evaluator

- **Where**: `tests/reference_eval/`. The wheel ships `packages = ["coperceive"]` only (`pyproject.toml`), so it cannot reach a user.
- **Written from Part A alone.** The implementer is a separate session that is given `docs/eval-spec.md` and nothing under `coperceive/evaluation/`. I have read the product code, so I do not write it.
- **No shared code.** It imports nothing from `coperceive`: not the IoU, not `SE3`, not the yaw helper, not the config or status constants. A test walks the package's AST and fails on any `coperceive` import. Its inputs and outputs are plain data (numbers, strings, lists), so pose composition and quaternion-to-yaw are implemented a second time as well.
- **IoU**: exact rotated-polygon overlay through shapely (GEOS), a different algorithm from the product's Sutherland–Hodgman clipping. `shapely>=2` and its type stubs go into the `dev` extra only; the runtime dependencies do not change.
  - Rejected: a second hand-written clipper (same algorithm family and the same author's blind spots); Monte-Carlo or grid sampling (cannot reach 1e-6).

### 4. Differential and property tests

One neutral scene description (plain data: agents, poses, ground truth, predictions, config) feeds two adapters, one into `evaluate_scene` and the pooled report, one into the reference.

- **Generators**: a seeded generator and Hypothesis strategies producing rotations near ±π, heavy overlap, duplicate predictions, score ties, empty frames, boxes at the range limit, tiny and huge boxes, several agents and outputs, body- and world-frame boxes. CI runs a fixed seed set (`derandomize`), so a red run is a regression and never luck; the failing seed is printed.
- **Agreement**: statuses, counts and pairings equal exactly; IoU, AP, precision, recall and error summaries within 1e-6. Any difference fails the test. The failure prints how far the closest pair of that case is from a threshold, so a pair sitting on one explains itself and nothing is dropped silently. Exact-boundary behaviour (a score equal to the score threshold, a box exactly on the range limit, a distance equal to the association gate) is tested on values that are exactly representable in binary, in the golden cases.
- **Invariants**:
  - AP unchanged under a rigid transform of a whole scene. For a BEV metric that means a rotation about the world z axis plus a translation; a tilt changes the footprints, so it is not an invariance of the metric and the spec says so.
  - AP unchanged when predictions are permuted. With equal scores this holds only if the tie order does not come from file order (open question 3).
  - A false positive scored below every other prediction cannot change AP.
  - Perfect predictions give AP = 1.
  - IoU symmetric and within [0, 1].
- **Golden cases** from Part D.
- **CI**: the suite runs in the existing `test` job on the three Python versions. No new job, no new runner. Budget: under 60 s of the job.

### 5. Fixing what the tests confirm

Only in `coperceive/evaluation/`. For each confirmed bug: a test that fails on the old code, the fix, and a CHANGELOG entry that names the reported numbers that change and by how much on the demo run. A disagreement that is a choice and not a bug is not "fixed": it goes into Part C.

## Open questions for the PM

1. **3D IoU.** The evaluator has BEV IoU and centre distance only. Is a 3D-IoU AP expected (a feature, outside this PR), or is "BEV and 3D" in the brief loose wording? Until answered, the spec states plainly that every AP is a BEV AP.
2. **Code-only facts.** Some protocol details of OpenCOOD-style benchmarks may be written down nowhere except their source. Default here: they stay "unverified". The alternative is a classic two-room split, where a party that never touches our evaluator reads that source and writes a prose description.
3. **Score ties.** File-order ties are deterministic but make AP depend on row order. Options: keep and document; or break ties by a key that does not depend on row order. Decided after the tests measure how much it moves a number.
4. **Dev dependency.** `shapely` (BSD-3-Clause) and `types-shapely` in the `dev` extra.

## Consequences

- A researcher can read one page and know what "AP@0.5" means here and where it differs from the benchmark they cite.
- A change to the evaluator that alters a number fails a test that names the rule it broke.
- The reference evaluator is a second implementation to maintain. It changes only when Part A changes.
- CI installs one more wheel in the `test` and `lint` jobs.
