# SPDX-License-Identifier: Apache-2.0
"""Invariants the evaluator has to keep, whatever the scene (ADR 0016 §4).

Each test rewrites a generated problem in a way that must not change the
answer, or changes it in a way with a known answer, and checks what
``coperceive.evaluation`` then reports:

* reordering prediction rows changes nothing when no two scores are equal;
* reordering annotation rows changes nothing;
* a false positive scored below every other prediction leaves every AP as it was;
* predictions equal to the ground truth give AP 1;
* turning and moving the whole world leaves every status and every AP as it was;
* doubling every length leaves an IoU evaluation as it was, bit for bit;
* the counts add up.

What happens when scores *are* equal is a documented choice, not an invariant:
``docs/eval-spec.md`` Part C and the golden cases.
"""

from __future__ import annotations

import math
import random
from collections import Counter

import pytest
from coperceive.evaluation.matching import FP, IGNORED, TP
from coperceive.evaluation.metrics import average_precision
from hypothesis import given, settings
from hypothesis import strategies as st

from tests import reference_eval
from tests.evalcheck.compare import diff_scenes
from tests.evalcheck.generate import PROFILES, Profile, random_run
from tests.evalcheck.product import evaluate_in_memory
from tests.evalcheck.scenario import (
    CRITERION_IOU_BEV,
    STATUS_FP,
    STATUS_IGNORED,
    STATUS_MISS,
    STATUS_TP,
    OutputResult,
    RunSpec,
    SceneResult,
)
from tests.evalcheck.transform import (
    add_low_false_positives,
    break_score_ties,
    has_score_ties,
    perfect_predictions,
    permute_ground_truth,
    permute_predictions,
    rigid_transform,
    scale_lengths,
)

SEEDS = range(12)
_PROFILE_IDS = [profile.name for profile in PROFILES]

Summary = dict[tuple[str, str, str], tuple[object, ...]]


def _summary(scenes: tuple[SceneResult, ...]) -> Summary:
    """Everything of a result that does not name a row: counts, AP tables, errors."""
    out: Summary = {}
    for scene in scenes:
        for output in scene.outputs:
            out[(scene.scene_id, output.agent_id, output.source)] = (
                output.totals,
                output.samples,
                tuple(
                    (dict(table.per_category), table.mean, table.mean_model) for table in output.ap
                ),
                output.cooperative,
                Counter(output.pred_status.values()),
                Counter(output.gt_status.values()),
                tuple(Counter(statuses.values()) for statuses in output.pred_status_at),
            )
    return out


def _ap_tables(
    scenes: tuple[SceneResult, ...],
) -> dict[tuple[str, str, str, int, str], float | None]:
    out: dict[tuple[str, str, str, int, str], float | None] = {}
    for scene in scenes:
        for output in scene.outputs:
            for index, table in enumerate(output.ap):
                for category, value in table.per_category.items():
                    out[(scene.scene_id, output.agent_id, output.source, index, category)] = value
    return out


def _outputs(scenes: tuple[SceneResult, ...]) -> list[tuple[str, OutputResult]]:
    return [(scene.scene_id, output) for scene in scenes for output in scene.outputs]


@pytest.mark.parametrize("profile", PROFILES, ids=_PROFILE_IDS)
def test_row_order_of_predictions_does_not_matter_when_scores_differ(profile: Profile) -> None:
    for seed in SEEDS:
        run = break_score_ties(random_run(seed, profile))
        assert not has_score_ties(run)
        shuffled = permute_predictions(run, seed)
        assert _summary(evaluate_in_memory(shuffled)) == _summary(evaluate_in_memory(run)), (
            f"seed {seed}: the result changed with the order of the rows of boxes.parquet"
        )


@pytest.mark.parametrize("profile", PROFILES, ids=_PROFILE_IDS)
def test_row_order_of_annotations_does_not_matter(profile: Profile) -> None:
    for seed in SEEDS:
        run = random_run(seed, profile)
        shuffled = permute_ground_truth(run, seed)
        assert _summary(evaluate_in_memory(shuffled)) == _summary(evaluate_in_memory(run)), (
            f"seed {seed}: the result changed with the order of the annotation rows"
        )


@pytest.mark.parametrize("profile", PROFILES, ids=_PROFILE_IDS)
def test_a_false_positive_below_every_score_cannot_change_ap(profile: Profile) -> None:
    changed = 0
    for seed in SEEDS:
        run = random_run(seed, profile)
        with_extra = add_low_false_positives(run)
        changed += with_extra != run
        assert _ap_tables(evaluate_in_memory(with_extra)) == _ap_tables(evaluate_in_memory(run)), (
            f"seed {seed}: an AP moved when a lowest-score false positive was added"
        )
    assert changed > len(SEEDS) // 2


@pytest.mark.parametrize("profile", PROFILES, ids=_PROFILE_IDS)
def test_perfect_predictions_give_ap_one(profile: Profile) -> None:
    numbers = 0
    for seed in SEEDS:
        run = perfect_predictions(random_run(seed, profile), seed)
        for scene_id, output in _outputs(evaluate_in_memory(run)):
            where = f"seed {seed} {scene_id}/{output.agent_id}/{output.source}"
            has_gt = any(status != STATUS_IGNORED for status in output.gt_status.values())
            for statuses in output.pred_status_at:
                if has_gt:
                    assert set(statuses.values()) <= {STATUS_TP, STATUS_IGNORED}, where
            for table in output.ap:
                for category, value in table.per_category.items():
                    if value is not None:
                        numbers += 1
                        assert value == pytest.approx(1.0, abs=1e-12), f"{where} {category}"
    assert numbers > 0


def _rigid_motion(profile: Profile, seed: int) -> tuple[float, tuple[float, float, float]]:
    rng = random.Random(f"rigid:{profile.name}:{seed}")
    if profile.range_limit:
        # Boxes sit exactly on the range limit: keep the motion exact (whole metres, no turn).
        return 0.0, (float(rng.randint(-900, 900)), float(rng.randint(-900, 900)), 0.0)
    return (
        rng.uniform(-math.pi, math.pi),
        (rng.uniform(-900.0, 900.0), rng.uniform(-900.0, 900.0), rng.uniform(-5.0, 5.0)),
    )


@pytest.mark.parametrize("profile", PROFILES, ids=_PROFILE_IDS)
def test_a_rigid_motion_of_the_world_changes_nothing(profile: Profile) -> None:
    """A turn about world z and a translation: same statuses, same pairs, same numbers.

    A tilt is not tested because it is not an invariance of a BEV metric: it changes
    the footprints (``docs/eval-spec.md`` A.3).
    """
    for seed in SEEDS:
        run = random_run(seed, profile)
        yaw, translation = _rigid_motion(profile, seed)
        moved = rigid_transform(run, yaw, translation)
        differences = diff_scenes(evaluate_in_memory(run), evaluate_in_memory(moved))
        assert not differences, f"seed {seed}: {differences[:3]}"


@pytest.mark.parametrize("profile", PROFILES, ids=_PROFILE_IDS)
def test_moving_the_world_into_map_coordinates_changes_nothing(profile: Profile) -> None:
    """The same scene 400 km east and 4000 km north of where it was, in whole metres."""
    for seed in SEEDS:
        run = random_run(seed, profile)
        moved = rigid_transform(run, 0.0, (400_000.0, 4_000_000.0, 0.0))
        differences = diff_scenes(evaluate_in_memory(run), evaluate_in_memory(moved))
        assert not differences, f"seed {seed}: {differences[:3]}"


@pytest.mark.parametrize("profile", PROFILES, ids=_PROFILE_IDS)
def test_doubling_every_length_leaves_an_iou_evaluation_bit_for_bit(profile: Profile) -> None:
    """Multiplying by 2 is exact in binary, so an IoU evaluation must not move at all."""
    checked = 0
    for seed in SEEDS:
        run = random_run(seed, profile)
        if run.config.criterion != CRITERION_IOU_BEV:
            continue
        checked += 1
        doubled = evaluate_in_memory(scale_lengths(run, 2.0))
        base = evaluate_in_memory(run)
        assert _ap_tables(doubled) == _ap_tables(base), f"seed {seed}"
        for (_, left), (_, right) in zip(_outputs(base), _outputs(doubled), strict=True):
            assert left.pred_status == right.pred_status, f"seed {seed}"
            assert left.gt_status == right.gt_status, f"seed {seed}"
            assert left.pred_status_at == right.pred_status_at, f"seed {seed}"
    assert checked > len(SEEDS) // 2


def _kept_gt_per_category(run: RunSpec, scene_id: str, output: OutputResult) -> Counter[str]:
    scene = next(scene for scene in run.scenes if scene.scene_id == scene_id)
    rename = run.config.gt_class_map
    kept: Counter[str] = Counter()
    for row, gt in enumerate(scene.ground_truth):
        if gt.agent_id == output.agent_id and output.gt_status[row] != STATUS_IGNORED:
            kept[rename.get(gt.category, gt.category)] += 1
    return kept


@pytest.mark.parametrize("profile", PROFILES, ids=_PROFILE_IDS)
def test_the_counts_add_up(profile: Profile) -> None:
    for seed in SEEDS:
        run = random_run(seed, profile)
        display = run.config.thresholds.index(run.config.display_threshold)
        scores = {
            (scene.scene_id, row): pred.score
            for scene in run.scenes
            for row, pred in enumerate(scene.predictions)
        }
        for scene_id, output in _outputs(evaluate_in_memory(run)):
            where = f"seed {seed} {scene_id}/{output.agent_id}/{output.source}"
            totals = output.totals
            predictions = Counter(output.pred_status.values())
            ground_truth = Counter(output.gt_status.values())
            assert totals.predictions == len(output.pred_status), where
            assert totals.tp == predictions[STATUS_TP] == ground_truth[STATUS_TP], where
            assert totals.fp == predictions[STATUS_FP], where
            assert totals.miss == ground_truth[STATUS_MISS], where
            assert totals.ignored_gt == ground_truth[STATUS_IGNORED], where
            assert totals.gt == totals.tp + totals.miss, where
            assert totals.tp == sum(sample.tp for sample in output.samples), where
            assert totals.fp == sum(sample.fp for sample in output.samples), where
            assert totals.miss == sum(sample.miss for sample in output.samples), where
            for value in (totals.precision, totals.recall):
                assert value is None or 0.0 <= value <= 1.0, where
            # One prediction, one box: no row is in two pairs.
            assert len({pair.pred_row for pair in output.pairs}) == len(output.pairs), where
            assert len({pair.gt_row for pair in output.pairs}) == len(output.pairs), where
            assert len(output.pairs) == totals.tp, where
            for pair in output.pairs:
                assert 0.0 <= pair.iou_bev <= 1.0 and 0.0 <= pair.yaw_error_deg <= 180.0, where
            # AP is a number in [0, 1] exactly when the class has kept ground truth.
            kept = _kept_gt_per_category(run, scene_id, output)
            for table in output.ap:
                for category, value in table.per_category.items():
                    if kept[category] == 0:
                        assert value is None, f"{where} {category}"
                    else:
                        assert value is not None and 0.0 <= value <= 1.0 + 1e-12, (
                            f"{where} {category}"
                        )
            # With no prediction under the score threshold the operating point is the
            # AP pass at the display threshold.
            mine = [scores[(scene_id, row)] for row in output.pred_status]
            if all(score >= run.config.score_threshold for score in mine):
                assert output.pred_status == output.pred_status_at[display], where
            cooperative = output.cooperative
            assert cooperative.recovered >= 0 and cooperative.lost_by_fusion >= 0, where
            both = [
                columns
                for columns in output.gt_cooperative.values()
                if columns.status_single is not None and columns.status_fused is not None
            ]
            # recovered - lost_by_fusion is the net change in true positives.
            gained = sum(c.status_fused == STATUS_TP for c in both) - sum(
                c.status_single == STATUS_TP for c in both
            )
            assert cooperative.recovered - cooperative.lost_by_fusion == gained, where


# --- the AP walk on its own ---------------------------------------------------------

_STATUSES = st.sampled_from((TP, FP, IGNORED))
_RECORDS = st.lists(
    st.tuples(st.floats(min_value=0.0, max_value=1.0, allow_nan=False), _STATUSES), max_size=40
)
_PROPERTY = settings(max_examples=300, deadline=None, derandomize=True)


def _n_gt(records: list[tuple[float, str]], extra: int) -> int:
    return sum(status == TP for _, status in records) + extra


@_PROPERTY
@given(_RECORDS, st.integers(min_value=0, max_value=5))
def test_ap_is_between_zero_and_one(records: list[tuple[float, str]], extra: int) -> None:
    n_gt = _n_gt(records, extra)
    ap = average_precision(records, n_gt)
    if n_gt == 0:
        assert ap is None
    else:
        assert ap is not None and 0.0 <= ap <= 1.0 + 1e-12


@_PROPERTY
@given(_RECORDS, st.integers(min_value=0, max_value=5))
def test_ap_matches_the_reference_walk(records: list[tuple[float, str]], extra: int) -> None:
    n_gt = _n_gt(records, extra)
    ap = average_precision(records, n_gt)
    expected = reference_eval.average_precision(records, n_gt)
    if ap is None or expected is None:
        assert ap is expected
    else:
        assert ap == pytest.approx(expected, abs=1e-12)


@_PROPERTY
@given(_RECORDS, st.integers(min_value=1, max_value=5))
def test_ap_ignores_a_false_positive_below_every_score(
    records: list[tuple[float, str]], extra: int
) -> None:
    n_gt = _n_gt(records, extra)
    lowest = min((score for score, _ in records), default=0.0) - 0.5
    assert average_precision([*records, (lowest, FP)], n_gt) == average_precision(records, n_gt)


@_PROPERTY
@given(_RECORDS, st.integers(min_value=1, max_value=5))
def test_ap_ignores_ignored_predictions(records: list[tuple[float, str]], extra: int) -> None:
    n_gt = _n_gt(records, extra)
    kept = [record for record in records if record[1] != IGNORED]
    assert average_precision(kept, n_gt) == average_precision(records, n_gt)


@_PROPERTY
@given(_RECORDS, st.integers(min_value=1, max_value=5), st.randoms(use_true_random=False))
def test_ap_does_not_depend_on_record_order_when_scores_differ(
    records: list[tuple[float, str]], extra: int, rng: random.Random
) -> None:
    distinct = list({score: (score, status) for score, status in records}.values())
    n_gt = _n_gt(distinct, extra)
    shuffled = list(distinct)
    rng.shuffle(shuffled)
    assert average_precision(shuffled, n_gt) == average_precision(distinct, n_gt)


@_PROPERTY
@given(st.lists(st.floats(min_value=0.0, max_value=1.0, allow_nan=False), min_size=1, max_size=40))
def test_ap_of_only_true_positives_is_the_recall_reached(scores: list[float]) -> None:
    records = [(score, TP) for score in scores]
    assert average_precision(records, len(records)) == pytest.approx(1.0, abs=1e-12)
    assert average_precision(records, 2 * len(records)) == pytest.approx(0.5, abs=1e-12)
