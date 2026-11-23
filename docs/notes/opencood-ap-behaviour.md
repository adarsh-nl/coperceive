> Written by a separate session (room A, [ADR 0016](../adr/0016-evaluator-verification.md) two-room) from OpenCOOD's evaluation code at commit `31ba160`, in prose only, with no code copied; Coperceive's evaluator side read only this note.

# OpenCOOD: how detections are ranked for AP

- Source remote: https://github.com/DerrickXuNu/OpenCOOD.git
- Commit read: 31ba16025da27ffe4e336f011290dfbc66f9a1f1 (merge of upstream PR 147, committed 2024-08-16)
- Working tree: clean (git status showed no local modifications), so this copy matches that commit.
- Date: 2026-11-20
- Read by room A (ADR 0016); no code copied.

Files read: `opencood/utils/eval_utils.py` (functions `voc_ap`, `caluclate_tp_fp` [sic, misspelled in the source], `calculate_ap`, `eval_final_results`), `opencood/tools/inference.py` (functions `test_parser`, `main`), and `opencood/utils/common_utils.py` (functions `compute_iou`, `convert_format`). A repository-wide search found no other caller of these evaluation functions. Only the inference script uses them.

---

## 1. Default ranking (global-sort option not set)

**Answer.** Detections are ranked by score **within each frame only**. The per-frame results are then concatenated in dataloader order and never re-sorted. In order:

1. The inference script builds one result store per IoU threshold (0.3, 0.5, 0.7). Each store holds a list of true-positive flags, a list of false-positive flags, a running ground-truth count and a list of scores.
2. The test dataloader runs with batch size 1 and shuffling off, so frames are processed one at a time in dataset index order.
3. For each frame, the matching routine is called once per threshold. It sorts that frame's detections by score, high to low. It walks them in that order and appends one TP flag and one FP flag per detection, in that order, to the store's lists. It adds the frame's ground-truth box count to the running total and appends the frame's sorted scores to the score list.
4. After all frames, the AP routine takes the TP and FP lists exactly as accumulated: frame 1's detections in score order, then frame 2's in score order, and so on. In default mode it ignores the stored scores entirely.
5. It turns the two lists into running cumulative sums in that concatenated order. At each position, recall is cumulative TPs over the pooled ground-truth total, and precision is cumulative TPs over cumulative detections.
6. That single precision/recall sequence goes to the AP integral (question 4).

So the default is "sort within each frame, concatenate frames in dataset order, build one pooled curve". The curve is not score-monotone across frames, and the AP depends on frame order.

**Where.** `opencood/utils/eval_utils.py`: `caluclate_tp_fp` (per-frame sort and flagging) and `calculate_ap` (non-global branch, cumulative sums, recall and precision). `opencood/tools/inference.py`: `main` (result-store setup, dataloader settings, per-frame calls).

**Confidence.** High.

## 2. The global option

**What it changes.** When set, the AP routine takes the pooled TP flags, FP flags and stored scores from all frames. It checks that the three lists have equal length, then reorders the TP and FP flags together by descending score across the whole dataset before the cumulative sums. Nothing else changes:

- Matching is still done per frame, with the same thresholds.
- Which detections are TP and which are FP is identical in both modes. Only the order the flags are accumulated in changes.
- The AP integral is the same.
- The results file is written under a different name: `eval_global_sort.yaml` with the option set, `eval.yaml` without it. Both go into the model directory passed as `--model_dir`, so running both modes on one checkpoint leaves both files side by side.

**Flag and default.** The command-line flag is `--global_sort_detections`, defined in the inference script's argument parser. It is a store-true switch, so it defaults to off (False). The value is passed through `eval_final_results` into `calculate_ap` for each of the three thresholds.

**Help text (paraphrase).** The help text says the option sorts detections globally by confidence score. It calls this "the mainstream AP computing method". It warns that it "would increase the tolerance for FP" (false positives). The help text frames the "mainstream" remark as applying when the option is set. The help string is joined from separate pieces with no spaces at the joins: one join falls between two sentences and the other in the middle of a sentence. This is cosmetic.

**Where.** `opencood/tools/inference.py`: `test_parser`, and `main` where it calls `eval_final_results`. `opencood/utils/eval_utils.py`: `calculate_ap` (global branch) and `eval_final_results` (output file name).

**Confidence.** High.

## 3. True and false positives per frame

**Matching order.** Predictions are matched greedily in descending score order within the frame. Each of the three thresholds runs its own pass, with its own fresh copy of the frame's ground-truth list.

**IoU used.** Each box is reduced to a 2D polygon from the x and y coordinates of its first four corners, that is a bird's-eye-view footprint, and IoU is polygon intersection area over polygon union area. It is not 3D IoU, and height is ignored.

**Threshold comparison.** For each prediction, the routine computes IoU against every ground-truth box still unmatched in that frame. If none remain, or the best IoU is strictly below the threshold, the prediction is a false positive. Otherwise it is a true positive. So a true positive needs IoU **≥ threshold**. The thresholds are 0.3, 0.5 and 0.7. The reported AP keys are for 0.3, 0.5 and 0.7, and the precision/recall arrays are kept only for 0.5 and 0.7.

**Can a GT be matched twice?** No. A true positive is assigned to the remaining ground truth with the highest IoU, and that ground truth is removed from the frame's pool. Later, lower-scored predictions are compared only against what remains.

This differs from the classic VOC rule, where a prediction whose best overlap is an already-claimed ground truth counts as a false positive. Here, a later prediction never sees the claimed ground truth. It can become a true positive by matching its second-best ground truth, provided that IoU reaches the threshold.

**Frames with no predictions.** When the prediction input is absent, nothing is appended to the TP, FP or score lists, but the frame's ground-truth count is still added to the total. Those objects count as misses.

**Where.** `opencood/utils/eval_utils.py`: `caluclate_tp_fp`. `opencood/utils/common_utils.py`: `compute_iou` and `convert_format`.

**Confidence.** High for matching order, threshold direction, single-use ground truth and BEV polygon IoU. Medium on how a prediction exactly at the threshold resolves, because IoU is returned as a 32-bit float. See Open points.

## 4. The AP integral

**Answer.** All-point interpolation, following the VOC 2010 convention (the function's docstring names it that way). It is not 11-point. Steps:

1. Recall gets a 0 prepended and a 1.0 appended. Precision gets a 0 prepended and a 0 appended.
2. The precision envelope is formed by sweeping from the end back to the start, replacing each precision value with the maximum of itself and the value after it. This makes precision non-increasing in list order.
3. AP is the sum, over every position where recall differs from the previous position, of the recall step times the envelope precision at that position. Because the appended end point has precision 0, the step up to recall 1.0 adds nothing.

In default mode the sequence fed into this step is in concatenated per-frame order, not in global score order. The envelope is still formed over list order.

**Where.** `opencood/utils/eval_utils.py`: `voc_ap`, called from `calculate_ap`.

**Confidence.** High.

## 5. Caveats: why "per frame" can mislead for the default

- **Only the sort and the matching are per frame.** No AP is computed per frame and then averaged. There is one pooled precision/recall curve over all frames, with one pooled ground-truth total. A better description of the default is "frame-local ranking, dataset-level curve".
- **Order dependence.** In default mode the pooled sequence is ordered by frame index first and score second. The cumulative precision at any point depends on which frames came earlier. Reordering the test set, for example with a different dataloader order or a different dataset index, can change AP even when every TP/FP decision is identical. The global option removes that dependence (except for score ties).
- **The envelope runs over a non-score-ordered list.** Because of the right-to-left maximum, a high-precision stretch from a late frame lifts the envelope for every earlier position. This differs from computing a per-frame AP.
- **Thresholds are independent passes.** The 0.3, 0.5 and 0.7 results each come from a separate per-frame matching pass, not from one matching reused at three thresholds.
- **In-place mutation (side note).** In default mode, the AP routine turns the stored TP and FP lists into cumulative sums in place. In global mode it works on reordered copies. Each threshold is evaluated only once in the shipped script, so this does not change the reported numbers. It would matter only if the AP routine were called twice on the same store.

**Where.** `opencood/utils/eval_utils.py`: `calculate_ap` and `caluclate_tp_fp`. `opencood/tools/inference.py`: `main`.

**Confidence.** High for the first four bullets. Medium for the practical size of the order effect, which I did not measure.

---

## Open points

1. **Score ties.** Both the per-frame sort and the global sort use a default (non-stable) numpy argsort. The order of equal-score detections is therefore undefined by the code. It can affect per-frame matching, which ground truth a tied prediction claims, and the global curve.
2. **Exact-threshold IoU.** IoU values come back as 32-bit floats and are compared against a plain Python float threshold (0.3, 0.5 or 0.7). The comparison precision depends on the NumPy version. Under NumPy 2 promotion rules the threshold is cast down to 32-bit; under NumPy 1.x the comparison is done in 64-bit. I did not check which NumPy is pinned. A prediction whose true IoU equals the threshold may therefore land on either side after rounding. In practice this is negligible, but it is not strictly "≥" in exact arithmetic.
3. **Zero ground truth.** If the whole test set has no ground-truth boxes but at least one detection, the recall step divides by zero and raises an error; there is no guard. With zero ground truth and zero detections, the lists are empty and AP comes out as 0 with no error. The same holds with ground truth present but no predictions at all.
4. **Degenerate polygons.** The IoU helper prints a debug message if any union area is zero, but does not guard the division. The division is done on plain Python floats, so a zero union raises an error rather than giving NaN. A zero union needs both the prediction and the ground-truth polygon to have zero area. A zero-area prediction against a normal ground-truth box just gets IoU 0 and becomes a false positive.
5. **Upstream filtering and NMS (not read in depth, low confidence).** The detections reaching the matcher have already been through each model's post-processor, `opencood/data_utils/post_processor/*`. Search hits show a score threshold (a strict greater-than mask in several post-processors) and NMS with a configurable threshold, both set from the model config. Which detections and scores enter the AP computation therefore depends on those settings. I did not read the post-processors or ground-truth generation fully. The range limits and the ground-truth selection for the ego frame are unverified here.
6. **Late fusion.** For late fusion, I did not check how per-agent detections are merged into the ego-frame list before matching. I assumed one prediction list and one ground-truth list per frame, as for the other fusion modes, since all three paths return the same triple to the matcher.
