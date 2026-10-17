# SPDX-License-Identifier: Apache-2.0
"""Metrics of ``docs/spec/predictions.md`` §Metrics and §Cooperative views: the normative
AP values, counts and their ratios, true-positive errors and the cooperative counts."""

from __future__ import annotations

import pytest
from coperceive.evaluation.matching import MatchResult, Pairing
from coperceive.evaluation.metrics import (
    Counts,
    GtCooperative,
    average_precision,
    cooperative_counts,
    counts_of,
    tp_errors,
)

Size = tuple[float, float, float]


# --- average precision (§Metrics, normative values) ---


def test_ap_one_gt_one_tp_is_one() -> None:
    assert average_precision([(0.4, "tp")], 1) == 1.0


def test_ap_fp_above_the_tp_is_half() -> None:
    assert average_precision([(0.9, "fp"), (0.8, "tp")], 1) == 0.5


def test_ap_tp_above_the_fp_is_one() -> None:
    assert average_precision([(0.9, "tp"), (0.8, "fp")], 1) == 1.0


def test_ap_two_gt_one_tp_is_half() -> None:
    assert average_precision([(0.9, "tp")], 2) == 0.5


def test_ap_gt_without_prediction_is_zero_and_no_gt_is_none() -> None:
    assert average_precision([], 3) == 0.0
    assert average_precision([], 0) is None
    assert average_precision([(0.9, "fp")], 0) is None


def test_ap_sorts_by_score_and_keeps_file_order_on_ties() -> None:
    # Given in file order with the tp first but the lower score: the fp is walked first.
    assert average_precision([(0.8, "tp"), (0.9, "fp")], 1) == 0.5
    # Equal scores keep file order: tp first gives 1.0, fp first gives 0.5.
    assert average_precision([(0.9, "tp"), (0.9, "fp")], 1) == 1.0
    assert average_precision([(0.9, "fp"), (0.9, "tp")], 1) == 0.5


def test_ap_skips_ignored_predictions() -> None:
    assert average_precision([(0.95, "ignored"), (0.9, "tp")], 1) == 1.0


def test_ap_uses_the_monotone_precision_envelope() -> None:
    # tp, fp, tp over two GT: recalls 0.5, 0.5, 1.0; precisions 1, 0.5, 2/3.
    # Envelope: 1, 2/3, 2/3. AP = 0.5 * 1 + 0 + 0.5 * 2/3.
    records = [(0.9, "tp"), (0.8, "fp"), (0.7, "tp")]
    assert average_precision(records, 2) == pytest.approx(0.5 + 0.5 * 2 / 3)


# --- counts ---


def test_empty_counts_have_null_ratios() -> None:
    counts = Counts(0, 0, 0, 0)
    assert counts.precision is None
    assert counts.recall is None


def test_counts_ratios() -> None:
    counts = Counts(tp=3, fp=1, miss=1, ignored_gt=2)
    assert counts.precision == 0.75
    assert counts.recall == 0.75
    assert Counts(0, 2, 0, 0).precision == 0.0
    assert Counts(0, 2, 0, 0).recall is None
    assert Counts(0, 0, 2, 0).recall == 0.0
    assert Counts(0, 0, 2, 0).precision is None


def test_counts_of_a_match_result() -> None:
    result = MatchResult(
        pred_status={0: "tp", 1: "fp", 2: "below_threshold", 3: "ignored", 4: "tp"},
        gt_status={10: "tp", 11: "miss", 12: "ignored", 13: "tp", 14: "miss", 15: "miss"},
        pairs=(
            Pairing(0, 10, 0.9, 0.1, 1.0),
            Pairing(4, 13, 0.8, 0.2, 2.0),
        ),
    )
    assert counts_of(result) == Counts(tp=2, fp=1, miss=3, ignored_gt=1)


# --- tp errors ---


def test_tp_errors_means_and_median() -> None:
    pairs = (
        Pairing(pred_row=0, gt_row=10, iou_bev=0.8, centre_error_m=0.1, yaw_error_deg=2.0),
        Pairing(pred_row=1, gt_row=11, iou_bev=0.6, centre_error_m=0.3, yaw_error_deg=4.0),
    )
    sizes: dict[int, tuple[Size, Size]] = {
        0: ((4.2, 1.9, 1.5), (4.0, 2.0, 1.5)),
        1: ((3.6, 2.0, 1.7), (4.0, 2.0, 1.5)),
    }
    errors = tp_errors(pairs, sizes)
    assert errors == {
        "centre_m": {"mean": pytest.approx(0.2), "median": pytest.approx(0.2)},
        "yaw_deg": {"mean_abs": pytest.approx(3.0)},
        "size_m": {
            "length": pytest.approx(0.3),
            "width": pytest.approx(0.05),
            "height": pytest.approx(0.1),
        },
        "iou_bev": {"mean": pytest.approx(0.7)},
    }


def test_tp_errors_median_of_three_is_the_middle_value() -> None:
    pairs = (
        Pairing(0, 10, 0.9, 0.1, 0.0),
        Pairing(1, 11, 0.9, 0.9, 0.0),
        Pairing(2, 12, 0.9, 0.2, 0.0),
    )
    same: tuple[Size, Size] = ((4.0, 2.0, 1.5), (4.0, 2.0, 1.5))
    errors = tp_errors(pairs, {0: same, 1: same, 2: same})
    centre = errors["centre_m"]
    assert isinstance(centre, dict)
    assert centre["median"] == pytest.approx(0.2)
    assert centre["mean"] == pytest.approx(0.4)


def test_tp_errors_without_true_positives_is_all_null() -> None:
    assert tp_errors((), {}) == {
        "centre_m": {"mean": None, "median": None},
        "yaw_deg": {"mean_abs": None},
        "size_m": {"length": None, "width": None, "height": None},
        "iou_bev": {"mean": None},
    }


# --- cooperative counts (§Cooperative views) ---


def _coop(
    single: str | None,
    fused: str | None,
    *,
    other_tp: bool | None = None,
    other_kind: str | None = None,
) -> GtCooperative:
    return GtCooperative(
        status_single=single,
        status_fused=fused,
        seen_by_other_gt=None if other_kind is None else True,
        other_kind=other_kind,
        other_agent_tp=other_tp,
    )


def test_cooperative_counts_cover_the_four_cases() -> None:
    rows = (
        _coop("miss", "tp"),  # recovered
        _coop("tp", "miss"),  # lost by fusion
        _coop("miss", "miss", other_tp=True, other_kind="shared_track"),  # only other agent
        _coop("miss", "miss", other_tp=False, other_kind="computed"),  # nobody
        _coop("miss", "miss"),  # nobody (no other box)
        _coop("tp", "tp", other_tp=True, other_kind="shared_track"),  # counted nowhere
    )
    counts = cooperative_counts(rows)
    assert counts["recovered"] == 1
    assert counts["lost_by_fusion"] == 1
    assert counts["only_other_agent"] == 1
    assert counts["nobody"] == 2
    # recovered / (recovered + miss under both): 1 / (1 + 3)
    assert counts["recovery_rate"] == 0.25


def test_recovery_rate_is_half_for_one_recovered_and_one_double_miss() -> None:
    counts = cooperative_counts((_coop("miss", "tp"), _coop("miss", "miss")))
    assert counts["recovered"] == 1
    assert counts["nobody"] == 1
    assert counts["recovery_rate"] == 0.5


def test_recovery_rate_is_null_when_nothing_was_recovered_or_double_missed() -> None:
    counts = cooperative_counts((_coop("tp", "tp"), _coop("tp", "miss")))
    assert counts["recovery_rate"] is None
    assert counts == {
        "recovered": 0,
        "lost_by_fusion": 1,
        "only_other_agent": 0,
        "nobody": 0,
        "recovery_rate": None,
    }
    assert cooperative_counts(()) == {
        "recovered": 0,
        "lost_by_fusion": 0,
        "only_other_agent": 0,
        "nobody": 0,
        "recovery_rate": None,
    }


def test_cooperative_counts_skip_boxes_without_both_statuses() -> None:
    # A box whose agent has no fused (or no single) output does not enter the counts.
    rows = (
        _coop("miss", None, other_tp=True, other_kind="shared_track"),
        _coop(None, "miss"),
        _coop("ignored", "ignored"),
    )
    assert cooperative_counts(rows) == {
        "recovered": 0,
        "lost_by_fusion": 0,
        "only_other_agent": 0,
        "nobody": 0,
        "recovery_rate": None,
    }
