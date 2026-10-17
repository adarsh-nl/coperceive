# SPDX-License-Identifier: Apache-2.0
"""Greedy per-class matching: the normative cases of ``docs/spec/predictions.md``
§Matching under ``centre`` and ``iou_bev``, equal-score order, range and the AP pass."""

from __future__ import annotations

from types import MappingProxyType

import pytest
from coperceive.evaluation.config import EvalConfig, parse_config
from coperceive.evaluation.geometry import WorldBox
from coperceive.evaluation.matching import (
    GtBox,
    MatchResult,
    Pairing,
    PredBox,
    match_sample,
)
from coperceive.se3 import SE3


def _centre_config(**overrides: object) -> EvalConfig:
    """The config of the spec's normative cases: centre, [2.0, 1.0], display 2.0."""
    payload: dict[str, object] = {
        "criterion": "centre",
        "thresholds": [2.0, 1.0],
        "display_threshold": 2.0,
    }
    payload.update(overrides)
    return parse_config(payload)


def _iou_config(**overrides: object) -> EvalConfig:
    """The default criterion: iou_bev, [0.5, 0.7], display 0.5."""
    payload: dict[str, object] = {}
    payload.update(overrides)
    return parse_config(payload)


def _world(cx: float, cy: float, length: float = 4.0, width: float = 2.0) -> WorldBox:
    return WorldBox(center=(cx, cy, 0.0), size_lwh=(length, width, 1.5), rotation=SE3.identity())


def _gt(
    cx: float,
    cy: float,
    *,
    row: int = 0,
    category: str = "car",
    visibility: float | None = None,
    range_m: float = 0.0,
) -> GtBox:
    return GtBox(
        row=row,
        category=category,
        track_id=None,
        visibility=visibility,
        world=_world(cx, cy),
        range_m=range_m,
    )


def _pred(
    cx: float,
    cy: float,
    *,
    row: int = 0,
    category: str = "car",
    score: float = 0.9,
    range_m: float = 0.0,
) -> PredBox:
    return PredBox(
        row=row,
        category=category,
        score=score,
        track_id=None,
        world=_world(cx, cy),
        range_m=range_m,
    )


def _match(
    gt: list[GtBox],
    preds: list[PredBox],
    config: EvalConfig,
    *,
    threshold_index: int = 0,
    apply_score_threshold: bool = True,
) -> MatchResult:
    return match_sample(
        gt,
        preds,
        config,
        threshold_index=threshold_index,
        apply_score_threshold=apply_score_threshold,
    )


# --- the five normative cases, criterion centre -----------------------------------


def test_centre_tp_at_two_metres_and_fp_with_a_miss_at_one() -> None:
    gt = [_gt(0.0, 0.0)]
    preds = [_pred(1.5, 0.0, score=0.9)]
    config = _centre_config()

    loose = _match(gt, preds, config, threshold_index=0)
    assert loose.pred_status == {0: "tp"}
    assert loose.gt_status == {0: "tp"}
    assert len(loose.pairs) == 1
    pair = loose.pairs[0]
    assert pair.pred_row == 0
    assert pair.gt_row == 0
    assert pair.centre_error_m == pytest.approx(1.5)
    assert pair.yaw_error_deg == pytest.approx(0.0)
    # 4 x 2 boxes shifted 1.5 along the length: intersection 5, union 11.
    assert pair.iou_bev == pytest.approx(5 / 11)

    strict = _match(gt, preds, config, threshold_index=1)
    assert strict.pred_status == {0: "fp"}
    assert strict.gt_status == {0: "miss"}
    assert strict.pairs == ()


def test_centre_two_predictions_on_one_gt_give_one_tp_and_one_fp() -> None:
    gt = [_gt(0.0, 0.0)]
    preds = [_pred(0.2, 0.0, row=0, score=0.9), _pred(0.4, 0.0, row=1, score=0.8)]

    result = _match(gt, preds, _centre_config())

    assert result.pred_status == {0: "tp", 1: "fp"}
    assert result.gt_status == {0: "tp"}
    assert [(p.pred_row, p.gt_row) for p in result.pairs] == [(0, 0)]


def test_centre_wrong_class_is_an_fp_and_a_miss() -> None:
    gt = [_gt(0.0, 0.0, category="car")]
    preds = [_pred(0.0, 0.0, category="truck")]

    result = _match(gt, preds, _centre_config())

    assert result.pred_status == {0: "fp"}
    assert result.gt_status == {0: "miss"}
    assert result.pairs == ()


def test_centre_low_visibility_gt_ignores_the_prediction_on_it() -> None:
    gt = [_gt(0.0, 0.0, visibility=0.1)]
    preds = [_pred(0.0, 0.0)]

    result = _match(gt, preds, _centre_config(ignore_gt_visibility_below=0.5))

    assert result.pred_status == {0: "ignored"}
    assert result.gt_status == {0: "ignored"}
    assert result.pairs == ()


def test_centre_below_threshold_prediction_leaves_a_miss_but_the_ap_pass_matches() -> None:
    gt = [_gt(0.0, 0.0)]
    preds = [_pred(0.1, 0.0, score=0.2)]
    config = _centre_config()

    operating = _match(gt, preds, config, apply_score_threshold=True)
    assert operating.pred_status == {0: "below_threshold"}
    assert operating.gt_status == {0: "miss"}
    assert operating.pairs == ()

    ap_pass = _match(gt, preds, config, apply_score_threshold=False)
    assert ap_pass.pred_status == {0: "tp"}
    assert ap_pass.gt_status == {0: "tp"}
    assert [(p.pred_row, p.gt_row) for p in ap_pass.pairs] == [(0, 0)]


# --- the same five under iou_bev ----------------------------------------------------


def test_iou_identical_box_is_tp_at_both_thresholds_and_a_shift_of_two_is_fp() -> None:
    gt = [_gt(0.0, 0.0)]
    config = _iou_config()

    same = [_pred(0.0, 0.0)]
    for index in (0, 1):
        result = _match(gt, same, config, threshold_index=index)
        assert result.pred_status == {0: "tp"}
        assert result.gt_status == {0: "tp"}
        assert result.pairs[0].iou_bev == pytest.approx(1.0)
        assert result.pairs[0].centre_error_m == 0.0

    shifted = [_pred(2.0, 0.0)]
    result = _match(gt, shifted, config, threshold_index=0)
    assert result.pred_status == {0: "fp"}
    assert result.gt_status == {0: "miss"}
    assert result.pairs == ()


def test_iou_exactly_at_the_threshold_is_a_match() -> None:
    """Rule 3: IoU passes with ``>=``. A 3 x 2 box and the same box shifted 1 m along
    its length: intersection 2 x 2 = 4, union 6 + 6 - 4 = 8, IoU exactly 0.5 (every
    coordinate is representable, so the clipping is exact). A tp at 0.5, an fp at 0.7."""
    gt = [
        GtBox(
            row=0,
            category="car",
            track_id=None,
            visibility=None,
            world=_world(0.0, 0.0, length=3.0),
            range_m=0.0,
        )
    ]
    preds = [
        PredBox(
            row=0,
            category="car",
            score=0.9,
            track_id=None,
            world=_world(1.0, 0.0, length=3.0),
            range_m=0.0,
        )
    ]
    config = _iou_config()

    at_half = _match(gt, preds, config, threshold_index=0)
    assert at_half.pairs[0].iou_bev == 0.5
    assert at_half.pred_status == {0: "tp"}
    assert at_half.gt_status == {0: "tp"}

    at_seven_tenths = _match(gt, preds, config, threshold_index=1)
    assert at_seven_tenths.pred_status == {0: "fp"}
    assert at_seven_tenths.gt_status == {0: "miss"}
    assert at_seven_tenths.pairs == ()


def test_iou_two_predictions_on_one_gt_give_one_tp_and_one_fp() -> None:
    gt = [_gt(0.0, 0.0)]
    preds = [_pred(0.0, 0.0, row=0, score=0.9), _pred(0.5, 0.0, row=1, score=0.8)]

    result = _match(gt, preds, _iou_config())

    assert result.pred_status == {0: "tp", 1: "fp"}
    assert result.gt_status == {0: "tp"}
    assert [(p.pred_row, p.gt_row) for p in result.pairs] == [(0, 0)]


def test_iou_wrong_class_is_an_fp_and_a_miss() -> None:
    gt = [_gt(0.0, 0.0, category="car")]
    preds = [_pred(0.0, 0.0, category="truck")]

    result = _match(gt, preds, _iou_config())

    assert result.pred_status == {0: "fp"}
    assert result.gt_status == {0: "miss"}
    assert result.pairs == ()


def test_iou_low_visibility_gt_ignores_the_prediction_on_it() -> None:
    gt = [_gt(0.0, 0.0, visibility=0.1)]
    preds = [_pred(0.0, 0.0)]

    result = _match(gt, preds, _iou_config(ignore_gt_visibility_below=0.5))

    assert result.pred_status == {0: "ignored"}
    assert result.gt_status == {0: "ignored"}
    assert result.pairs == ()


def test_iou_below_threshold_prediction_leaves_a_miss_but_the_ap_pass_matches() -> None:
    gt = [_gt(0.0, 0.0)]
    preds = [_pred(0.0, 0.0, score=0.2)]
    config = _iou_config()

    operating = _match(gt, preds, config, apply_score_threshold=True)
    assert operating.pred_status == {0: "below_threshold"}
    assert operating.gt_status == {0: "miss"}

    ap_pass = _match(gt, preds, config, apply_score_threshold=False)
    assert ap_pass.pred_status == {0: "tp"}
    assert ap_pass.gt_status == {0: "tp"}


# --- order, range, score threshold --------------------------------------------------


def test_equal_scores_keep_file_order() -> None:
    """Review Focus 2: with equal scores the earlier file row is matched first,
    even when the later row is the closer one."""
    gt = [_gt(0.0, 0.0)]
    preds = [_pred(0.4, 0.0, row=0, score=0.9), _pred(0.2, 0.0, row=1, score=0.9)]
    config = _centre_config()

    first = _match(gt, preds, config)
    assert first.pred_status == {0: "tp", 1: "fp"}
    assert [(p.pred_row, p.gt_row) for p in first.pairs] == [(0, 0)]

    # The same input gives the same result every time.
    again = _match(gt, preds, config)
    assert again == first


def test_higher_score_wins_over_file_order() -> None:
    gt = [_gt(0.0, 0.0)]
    preds = [_pred(0.2, 0.0, row=0, score=0.8), _pred(0.4, 0.0, row=1, score=0.9)]

    result = _match(gt, preds, _centre_config())

    assert result.pred_status == {0: "fp", 1: "tp"}
    assert [(p.pred_row, p.gt_row) for p in result.pairs] == [(1, 0)]


def test_max_range_ignores_a_gt_and_a_prediction_at_sixty_metres() -> None:
    gt = [_gt(10.0, 0.0, row=0, range_m=10.0), _gt(60.0, 0.0, row=1, range_m=60.0)]
    preds = [
        _pred(10.0, 0.0, row=0, score=0.9, range_m=10.0),
        _pred(60.0, 0.0, row=1, score=0.9, range_m=60.0),
    ]

    result = _match(gt, preds, _centre_config(max_range_m=50.0))

    assert result.pred_status == {0: "tp", 1: "ignored"}
    assert result.gt_status == {0: "tp", 1: "ignored"}
    assert [(p.pred_row, p.gt_row) for p in result.pairs] == [(0, 0)]


def test_a_gt_exactly_at_max_range_is_kept() -> None:
    gt = [_gt(50.0, 0.0, range_m=50.0)]
    preds = [_pred(50.0, 0.0, range_m=50.0)]

    result = _match(gt, preds, _centre_config(max_range_m=50.0))

    assert result.pred_status == {0: "tp"}
    assert result.gt_status == {0: "tp"}


def test_ap_pass_ignores_score_threshold_but_not_range() -> None:
    gt = [_gt(0.0, 0.0, row=0), _gt(60.0, 0.0, row=1, range_m=60.0)]
    preds = [
        _pred(0.1, 0.0, row=0, score=0.05),
        _pred(60.0, 0.0, row=1, score=0.95, range_m=60.0),
    ]

    result = _match(
        gt,
        preds,
        _centre_config(max_range_m=50.0, score_threshold=0.9),
        apply_score_threshold=False,
    )

    assert result.pred_status == {0: "tp", 1: "ignored"}
    assert result.gt_status == {0: "tp", 1: "ignored"}


def test_the_best_box_being_ignored_leaves_the_other_gt_a_miss() -> None:
    """Rule 3: the prediction takes the best box, ignored ones included; when that
    box is ignored the prediction is ignored and no other box is matched."""
    gt = [_gt(0.0, 0.0, row=0, visibility=0.1), _gt(1.0, 0.0, row=1, visibility=0.9)]
    preds = [_pred(0.1, 0.0)]

    result = _match(gt, preds, _centre_config(ignore_gt_visibility_below=0.5))

    assert result.pred_status == {0: "ignored"}
    assert result.gt_status == {0: "ignored", 1: "miss"}
    assert result.pairs == ()


def test_an_ignored_gt_that_fails_the_threshold_gives_an_fp() -> None:
    gt = [_gt(0.0, 0.0, visibility=0.1)]
    preds = [_pred(5.0, 0.0)]

    result = _match(gt, preds, _centre_config(ignore_gt_visibility_below=0.5))

    assert result.pred_status == {0: "fp"}
    assert result.gt_status == {0: "ignored"}


def test_null_visibility_gt_is_kept_under_a_visibility_rule() -> None:
    gt = [_gt(0.0, 0.0, visibility=None)]
    preds = [_pred(0.0, 0.0)]

    result = _match(gt, preds, _centre_config(ignore_gt_visibility_below=0.5))

    assert result.pred_status == {0: "tp"}
    assert result.gt_status == {0: "tp"}


def test_class_thresholds_override_the_global_entry() -> None:
    gt = [_gt(0.0, 0.0, category="pedestrian")]
    preds = [_pred(0.75, 0.0, category="pedestrian")]
    config = _centre_config(class_thresholds={"pedestrian": [0.5, 0.25]})

    result = _match(gt, preds, config, threshold_index=0)

    assert result.pred_status == {0: "fp"}
    assert result.gt_status == {0: "miss"}


def test_statuses_are_keyed_by_row_not_by_position() -> None:
    gt = [_gt(0.0, 0.0, row=7)]
    preds = [_pred(0.0, 0.0, row=3)]

    result = _match(gt, preds, _centre_config())

    assert result.pred_status == {3: "tp"}
    assert result.gt_status == {7: "tp"}
    assert result.pairs == (
        Pairing(pred_row=3, gt_row=7, iou_bev=1.0, centre_error_m=0.0, yaw_error_deg=0.0),
    )


def test_empty_inputs_give_an_empty_result() -> None:
    result = _match([], [], _centre_config())

    assert result == MatchResult(
        pred_status=MappingProxyType({}), gt_status=MappingProxyType({}), pairs=()
    )
    assert dict(result.pred_status) == {}
    assert dict(result.gt_status) == {}


def test_a_prediction_with_no_gt_of_its_class_is_an_fp() -> None:
    result = _match([], [_pred(0.0, 0.0)], _centre_config())

    assert result.pred_status == {0: "fp"}
    assert dict(result.gt_status) == {}


def test_pairs_follow_score_order() -> None:
    gt = [_gt(0.0, 0.0, row=0), _gt(10.0, 0.0, row=1)]
    preds = [_pred(0.0, 0.0, row=0, score=0.5), _pred(10.0, 0.0, row=1, score=0.9)]

    result = _match(gt, preds, _centre_config())

    assert [(p.pred_row, p.gt_row) for p in result.pairs] == [(1, 1), (0, 0)]
