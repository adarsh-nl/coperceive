# SPDX-License-Identifier: Apache-2.0
"""Seen-by is offered only for datasets that record which agent observed each object."""

from __future__ import annotations

import json
from pathlib import Path

from coperceive.model import Agent, AgentType, Annotation, Clock, Scene, StampedPose
from coperceive.se3 import SE3
from coperceive.seen_by import adapter_name, seen_by_for_scene

_BOX = SE3.identity()


def _scene(world: str, *agents: tuple[str, AgentType], track: str | None = "7") -> Scene:
    rows = tuple(Agent(agent_id, agent_type, ()) for agent_id, agent_type in agents)
    # An agent with no poses is refused; give each one a pose.
    posed = tuple(Agent(agent.agent_id, agent.agent_type, (_pose(),)) for agent in rows)
    annotations = tuple(
        Annotation(agent.agent_id, 0, track, "car", (1.0, 0.0, 0.0), (4.0, 2.0, 1.5), _BOX)
        for agent in posed
    )
    return Scene(
        schema_version=1,
        scene_id="seen",
        world_frame=world,
        clock=Clock("us", "seen-by fixture"),
        agents=posed,
        sensors=(),
        annotations=annotations,
        objects=(),
    )


def _pose() -> StampedPose:
    return StampedPose(0, SE3.identity())


def test_each_dataset_reports_the_field_that_records_an_observer() -> None:
    opv2v = seen_by_for_scene(_scene("carla-map", ("cav", AgentType.VEHICLE)), "opv2v")
    assert opv2v["available"] is True
    assert "vehicles" in str(opv2v["field"])

    v2xset = seen_by_for_scene(
        _scene("carla-map", ("cav", AgentType.VEHICLE), ("rsu", AgentType.INFRASTRUCTURE)),
        "v2xset",
    )
    assert v2xset["available"] is False
    assert v2xset["dataset"] == "v2xset"

    v2v = seen_by_for_scene(
        _scene("v2v4real-world", ("0", AgentType.VEHICLE), ("1", AgentType.VEHICLE)),
        "v2v4real",
    )
    assert v2v["available"] is True
    assert "vehicles" in str(v2v["field"])

    dair = seen_by_for_scene(
        _scene(
            "dair-v2x-c-world",
            ("vehicle", AgentType.VEHICLE),
            ("infrastructure", AgentType.INFRASTRUCTURE),
        ),
        "dair-v2x-c",
    )
    assert dair["available"] is False
    assert "label_world" in str(dair["field"])

    griffin = seen_by_for_scene(
        _scene("ENU", ("vehicle", AgentType.VEHICLE), ("drone", AgentType.UAV)),
        "griffin",
    )
    assert griffin["available"] is True
    assert "track_id" in str(griffin["field"])

    generic = seen_by_for_scene(
        _scene("right-handed ENU", ("cav-a", AgentType.VEHICLE), ("rsu", AgentType.INFRASTRUCTURE)),
        "generic",
    )
    assert generic["available"] is False
    assert generic["dataset"] == "generic"


def test_a_scene_without_track_ids_hides_seen_by_even_on_opv2v() -> None:
    report = seen_by_for_scene(
        _scene("carla-map", ("cav", AgentType.VEHICLE), track=None),
        "opv2v",
    )
    assert report["available"] is False
    assert "track_id" in str(report["field"])


def test_carla_map_without_an_adapter_name_is_not_guessed(tmp_path: Path) -> None:
    report = seen_by_for_scene(_scene("carla-map", ("cav", AgentType.VEHICLE)), None)
    assert report["available"] is False
    assert report["dataset"] == "unknown"
    marker = tmp_path / "COMPLETE.json"
    marker.write_text(json.dumps({"adapter": "opv2v"}) + "\n", encoding="utf-8")
    assert adapter_name(tmp_path) == "opv2v"
    assert adapter_name(tmp_path / "missing") is None


def test_a_unique_world_frame_names_the_dataset_when_the_marker_has_no_adapter() -> None:
    report = seen_by_for_scene(_scene("v2v4real-world", ("0", AgentType.VEHICLE)), None)
    assert report["dataset"] == "v2v4real"
    assert report["available"] is True
    demo = seen_by_for_scene(_scene("right-handed ENU", ("cav-a", AgentType.VEHICLE)), None)
    assert demo["dataset"] == "generic"
    assert demo["available"] is False
