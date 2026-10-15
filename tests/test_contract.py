# SPDX-License-Identifier: Apache-2.0
"""Error paths the format spec requires a reader and a transform to raise."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from coperceive.diagnostics import annotation_center_world, track_center_spread_m
from coperceive.errors import SchemaVersionError, TimeQueryError
from coperceive.model import SCHEMA_VERSION, Agent, AgentType, StampedPose
from coperceive.query import TimeMode, query_time
from coperceive.se3 import SE3
from coperceive.store import write_scene
from coperceive.transform import Frame, lookup_pose

from tests.conftest import sample_scene
from tests.cooperative import cooperative_scene


def test_frame_names_are_checked() -> None:
    with pytest.raises(ValueError):
        Frame("world", "vehicle")
    with pytest.raises(ValueError):
        Frame("body", None)
    with pytest.raises(ValueError):
        Frame("sensor", "vehicle", None)


def test_lookup_rejects_a_negative_gap_and_an_empty_stream() -> None:
    scene = sample_scene()
    with pytest.raises(TimeQueryError):
        lookup_pose(scene.agent("vehicle"), 0, max_gap_us=-1)
    empty = Agent("bare", AgentType.OTHER, ())
    with pytest.raises(TimeQueryError, match="no poses"):
        lookup_pose(empty, 0, max_gap_us=0)


def test_query_time_rejects_bad_streams() -> None:
    with pytest.raises(TimeQueryError, match="no samples"):
        query_time([], 0, TimeMode.EXACT, tolerance_us=0)
    with pytest.raises(TimeQueryError, match="strictly increasing"):
        query_time([2, 1], 0, TimeMode.NEAREST, tolerance_us=10)
    with pytest.raises(TimeQueryError, match="tolerance_us"):
        query_time([0], 0, TimeMode.FORWARD, tolerance_us=-1)
    with pytest.raises(TimeQueryError, match="no sample at or after"):
        query_time([0, 10], 11, TimeMode.FORWARD, tolerance_us=100)


def test_world_frame_box_and_repeated_track() -> None:
    scene = cooperative_scene()
    cone = next(ann for ann in scene.annotations if ann.category == "cone")
    assert annotation_center_world(scene, cone, max_gap_us=0) == (21.0, 2.0, 0.0)
    repeated = scene.annotations[0]
    broken = replace(scene, annotations=scene.annotations[:1] + (repeated,))
    with pytest.raises(ValueError, match="repeated"):
        track_center_spread_m(broken, "42", max_gap_us=0)


def test_writer_rejects_another_schema_version(tmp_path: Path) -> None:
    scene = replace(sample_scene(), schema_version=SCHEMA_VERSION + 1)
    with pytest.raises(SchemaVersionError):
        write_scene(scene, tmp_path / "scene")


def test_non_finite_pose_is_rejected() -> None:
    with pytest.raises(ValueError):
        SE3(float("nan"), 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    with pytest.raises(ValueError, match="increasing"):
        Agent(
            "vehicle",
            AgentType.VEHICLE,
            (StampedPose(10, SE3.identity()), StampedPose(10, SE3.identity())),
        )
