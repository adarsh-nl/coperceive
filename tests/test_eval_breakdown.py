# SPDX-License-Identifier: Apache-2.0
"""Per-bin AP of ``eval compare --by`` (``docs/eval-spec.md`` A.11).

Nothing is matched again inside a bin. The AP pass and its pairs are filtered:
ground truth outside the bin is treated as ignored, so a true positive paired with
such a box is neither a true nor a false positive of the bin; a false positive
counts in the bin of its own attribute, or in every bin when it has none. Each box
is paired at most once in a pass, so a bin's AP lies in ``[0, 1]``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from coperceive.evaluation.breakdown import BREAKDOWNS, build_compare
from coperceive.evaluation.config import DEFAULT_CONFIG, EvalConfig, threshold_key
from coperceive.evaluation.evaluate import evaluate_run
from coperceive.evaluation.outcomes import load_run_records
from coperceive.evaluation.predictions import PredictionBox, RunMeta, write_run
from coperceive.evaluation.report import collect, pooled_ap
from coperceive.model import (
    SCHEMA_VERSION,
    Agent,
    AgentType,
    Annotation,
    BoxFrame,
    Clock,
    Scene,
    StampedPose,
)
from coperceive.se3 import SE3
from coperceive.store import write_scene

from tests.evalcheck.generate import PROFILES, Profile, random_run
from tests.evalcheck.product import RUN_ID, evaluate_on_disk

CREATED = "2026-11-16T00:00:00Z"


def _scene(scene_id: str, cars: tuple[tuple[float, float | None], ...]) -> Scene:
    """One agent at the origin, one pose at 0, world-frame 4 x 2 cars at ``(x, visibility)``."""
    agent = Agent(
        agent_id="vehicle", agent_type=AgentType.VEHICLE, poses=(StampedPose(0, SE3.identity()),)
    )
    annotations = tuple(
        Annotation(
            "vehicle",
            0,
            str(index),
            "car",
            (x, 0.0, 0.0),
            (4.0, 2.0, 1.5),
            SE3.identity(),
            visibility,
            BoxFrame.WORLD,
        )
        for index, (x, visibility) in enumerate(cars)
    )
    return Scene(
        schema_version=SCHEMA_VERSION,
        scene_id=scene_id,
        world_frame="ENU",
        clock=Clock(unit="us", scale_note="breakdown test"),
        agents=(agent,),
        sensors=(),
        annotations=annotations,
        objects=(),
    )


def _box(scene_id: str, score: float, x: float, y: float = 0.0) -> PredictionBox:
    return PredictionBox(
        scene_id=scene_id,
        agent_id="vehicle",
        source="single",
        time_us=0,
        category="car",
        score=score,
        track_id=None,
        center=(x, y, 0.0),
        size_lwh=(4.0, 2.0, 1.5),
        box_from_rpy=SE3.identity(),
        frame=BoxFrame.WORLD,
    )


def _compare(
    tmp_path: Path,
    scene: Scene,
    rows: tuple[PredictionBox, ...],
    config: EvalConfig = DEFAULT_CONFIG,
) -> dict[str, object]:
    root = tmp_path / "scenes"
    write_scene(scene, root / scene.scene_id)
    predictions = tmp_path / "predictions"
    meta = RunMeta(
        run_id="r",
        model="test",
        score_note="constant",
        categories=("car",),
        sources=(("single", "own sensors"),),
        created_utc=CREATED,
    )
    write_run(predictions, "r", meta, rows)
    report = evaluate_run(root, predictions, "r", config, created_utc=CREATED)
    assert report.errors == ()
    records, origin = load_run_records(root, predictions, "r", from_scores=False)
    return build_compare(
        [("r", records)],
        thresholds=config.thresholds,
        breakdowns=BREAKDOWNS,
        distance_source=origin,
    )


def _bin_ap(document: dict[str, object], dimension: str, label: str, threshold: float) -> object:
    output = document["outputs"][0]  # type: ignore[index]
    for row in output["breakdowns"][dimension]:  # type: ignore[index]
        if row["bin"] == label:
            return row["runs"]["r"]["ap"][threshold_key(threshold)]["mean"]
    raise KeyError(label)


def test_a_distance_bin_never_reports_an_ap_above_one(tmp_path: Path) -> None:
    """Cars at 29.2 m and 40 m. One prediction on the far car (score 0.9), one near the
    29.2 m car but centred at 30.2 m (score 0.2: under score_threshold, so it has no
    operating-point pair). At IoU 0.5 the second is paired with the 29.2 m car.

    Before: it was binned by its own centre into 30-50 m, which then held two true
    positives for one car, AP 2.0, and 0-30 m reported 0.0. Now each true positive
    counts in its car's bin: AP 1.0 in both.
    """
    document = _compare(
        tmp_path,
        _scene("s", ((29.2, 1.0), (40.0, 1.0))),
        (_box("s", 0.9, 40.0), _box("s", 0.2, 30.2)),
    )
    assert _bin_ap(document, "distance", "0-30m", 0.5) == pytest.approx(1.0)
    assert _bin_ap(document, "distance", "30-50m", 0.5) == pytest.approx(1.0)


def test_a_true_positive_under_the_score_threshold_follows_its_ground_truth(tmp_path: Path) -> None:
    """Two clear cars, two perfect predictions scored 0.9 and 0.2.

    Before: the 0.2 prediction had no operating-point pair and fell into the
    occlusion bin "unknown", so "clear" reported 0.5 for two perfect detections.
    """
    document = _compare(
        tmp_path,
        _scene("s", ((10.0, 1.0), (20.0, 1.0))),
        (_box("s", 0.9, 10.0), _box("s", 0.2, 20.0)),
    )
    for dimension, label in (("occlusion", "clear"), ("agents", "1"), ("distance", "0-30m")):
        assert _bin_ap(document, dimension, label, 0.5) == pytest.approx(1.0), dimension


def test_a_true_positive_of_another_bin_is_ignored_and_a_stray_box_counts_everywhere(
    tmp_path: Path,
) -> None:
    """A clear car at 10 m and a heavily occluded car at 25 m, both kept.

    Predictions: on the clear car (0.9), on the occluded car (0.8), and a stray box
    far from both (0.95). In the "clear" bin the true positive on the occluded car is
    ignored, the stray box is a false positive (an unmatched prediction has no
    visibility of its own, so it counts in every occlusion bin): fp, tp, AP 0.5. In
    "heavy" likewise: fp (stray), tp (occluded car), AP 0.5.
    """
    document = _compare(
        tmp_path,
        _scene("s", ((10.0, 1.0), (25.0, 0.1))),
        (_box("s", 0.9, 10.0), _box("s", 0.8, 25.0), _box("s", 0.95, 60.0, 60.0)),
    )
    assert _bin_ap(document, "occlusion", "clear", 0.5) == pytest.approx(0.5)
    assert _bin_ap(document, "occlusion", "heavy", 0.5) == pytest.approx(0.5)
    # By distance the stray box has its own bin (50 m+, no car there): it is no false
    # positive of 0-30 m, where both cars are found.
    assert _bin_ap(document, "distance", "0-30m", 0.5) == pytest.approx(1.0)


def _every_ap(document: dict[str, object]) -> list[float]:
    values: list[float] = []
    for output in document["outputs"]:  # type: ignore[union-attr]
        for rows in output["breakdowns"].values():
            for row in rows:
                for cell in row["runs"].values():
                    for entry in cell["ap"].values():
                        if entry["mean"] is not None:
                            values.append(entry["mean"])
                        for item in entry["by_class"].values():
                            if item["ap"] is not None:
                                values.append(item["ap"])
    return values


@pytest.mark.parametrize("profile", PROFILES, ids=lambda profile: profile.name)
def test_every_bin_ap_lies_in_zero_one_and_class_bins_equal_the_pooled_ap(
    profile: Profile, tmp_path: Path
) -> None:
    for seed in range(3):
        run = random_run(seed, profile)
        root = tmp_path / f"seed-{seed}"
        evaluate_on_disk(run, root)
        records, origin = load_run_records(
            root / "scenes", root / "predictions", RUN_ID, from_scores=False
        )
        document = build_compare(
            [(RUN_ID, records)],
            thresholds=run.config.thresholds,
            breakdowns=BREAKDOWNS,
            distance_source=origin,
            ranking_scope=run.config.ranking_scope,
        )
        values = _every_ap(document)
        assert all(0.0 <= value <= 1.0 + 1e-9 for value in values), (seed, max(values))
        # The class breakdown is the pooled per-class AP of eval report.
        rows = collect(root / "scenes", root / "predictions", RUN_ID)
        for output in document["outputs"]:  # type: ignore[union-attr]
            key = (output["agent_id"], output["source"])
            pooled = pooled_ap([row for row in rows if row.output == key], run.config.thresholds)
            for row in output["breakdowns"]["class"]:
                cell = row["runs"][RUN_ID]["ap"]
                for threshold in run.config.thresholds:
                    got = cell[threshold_key(threshold)]["by_class"].get(row["bin"], {}).get("ap")
                    want = pooled[threshold_key(threshold)].get(row["bin"])
                    if want is None:
                        assert got is None
                    else:
                        assert got == pytest.approx(want, abs=1e-6), (seed, key, row["bin"])


def test_an_outcomes_file_from_before_per_bin_pairs_is_refused(tmp_path: Path) -> None:
    """``--from-scores`` reads outcomes.json; one written before gt_row_at says to re-run."""
    import json

    from coperceive.errors import CoperceiveError

    root = tmp_path / "scenes"
    scene = _scene("s", ((10.0, 1.0),))
    write_scene(scene, root / "s")
    predictions = tmp_path / "predictions"
    meta = RunMeta(
        run_id="r",
        model="test",
        score_note="constant",
        categories=("car",),
        sources=(("single", "own sensors"),),
        created_utc=CREATED,
    )
    write_run(predictions, "r", meta, (_box("s", 0.9, 10.0),))
    evaluate_run(root, predictions, "r", DEFAULT_CONFIG, created_utc=CREATED)
    outcomes = predictions / "r" / "s" / "outcomes.json"
    document = json.loads(outcomes.read_text(encoding="utf-8"))
    for record in document["records"]:
        record.pop("gt_row_at", None)
    outcomes.write_text(json.dumps(document), encoding="utf-8")
    records, origin = load_run_records(root, predictions, "r", from_scores=True)
    with pytest.raises(CoperceiveError, match="eval run"):
        build_compare(
            [("r", records)],
            thresholds=DEFAULT_CONFIG.thresholds,
            breakdowns=("distance",),
            distance_source=origin,
        )
