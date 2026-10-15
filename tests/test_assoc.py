# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import math

import pytest
from coperceive.assoc import (
    AssociationKind,
    agent_annotations,
    associate,
    co_annotations,
    exclusive_annotations,
    shared_track_annotations,
)

from tests.cooperative import cooperative_scene


def test_shared_track_exclusive_and_disagreeing_ids() -> None:
    scene = cooperative_scene()
    shared = co_annotations(scene, "vehicle", "uav", time_us=100_000)
    by_id = {row.track_id: row for row in shared}
    assert set(by_id) == {"42", "5"}
    assert by_id["42"].ground_truth is True
    assert {ann.agent_id for ann in by_id["42"].annotations} == {"vehicle", "uav"}
    assert by_id["5"].ground_truth is False
    assert {ann.category for ann in by_id["5"].annotations} == {"car", "truck"}

    only_vehicle = exclusive_annotations(scene, "vehicle", time_us=100_000)
    assert [ann.track_id for ann in only_vehicle] == ["99"]
    only_uav = exclusive_annotations(scene, "uav", time_us=100_000)
    assert [ann.track_id for ann in only_uav] == [None]
    assert only_uav[0].category == "sign"

    tracks = shared_track_annotations(scene, "42")
    assert [(ann.agent_id, ann.time_us) for ann in tracks] == [
        ("vehicle", 100_000),
        ("uav", 100_000),
        ("vehicle", 200_000),
    ]
    with pytest.raises(KeyError):
        shared_track_annotations(scene, "5")
    with pytest.raises(KeyError):
        shared_track_annotations(scene, "99")


def test_associate_labels_computed_pairs_as_not_ground_truth() -> None:
    scene = cooperative_scene()
    before = scene.objects
    tight = associate(
        scene,
        "vehicle",
        "uav",
        time_us=100_000,
        max_gap_us=0,
        max_center_m=0.0,
    )
    assert scene.objects == before
    assert len(tight) == 1
    assert tight[0].kind is AssociationKind.SHARED_TRACK
    assert tight[0].ground_truth is True
    assert tight[0].left.track_id == "42"
    assert tight[0].center_distance_m == 0.0

    wide = associate(
        scene,
        "vehicle",
        "uav",
        time_us=100_000,
        max_gap_us=0,
        max_center_m=100.0,
    )
    assert scene.objects == before
    assert [pair.kind for pair in wide] == [
        AssociationKind.SHARED_TRACK,
        AssociationKind.COMPUTED,
        AssociationKind.COMPUTED,
    ]
    assert [pair.ground_truth for pair in wide] == [True, False, False]
    pedestrian = wide[1]
    assert pedestrian.left.track_id == "99"
    assert pedestrian.right.track_id is None
    assert pedestrian.right.category == "sign"
    same_id = wide[2]
    assert same_id.left.track_id == "5"
    assert same_id.right.track_id == "5"
    assert same_id.kind is AssociationKind.COMPUTED
    assert same_id.ground_truth is False
    assert same_id.center_distance_m == pytest.approx(math.sqrt(650.0))
    assert pedestrian.center_distance_m == pytest.approx(math.sqrt(593.0))


def test_queries_reject_a_bad_agent_pair() -> None:
    scene = cooperative_scene()
    with pytest.raises(KeyError):
        agent_annotations(scene, "missing")
    with pytest.raises(ValueError):
        co_annotations(scene, "vehicle", "vehicle")
    with pytest.raises(ValueError):
        associate(
            scene,
            "uav",
            "uav",
            time_us=100_000,
            max_gap_us=0,
            max_center_m=1.0,
        )
    with pytest.raises(ValueError):
        associate(
            scene,
            "vehicle",
            "uav",
            time_us=100_000,
            max_gap_us=0,
            max_center_m=-1.0,
        )
    with pytest.raises(ValueError):
        associate(
            scene,
            "vehicle",
            "uav",
            time_us=100_000,
            max_gap_us=0,
            max_center_m=True,  # type: ignore[arg-type]
        )
