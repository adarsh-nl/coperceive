# SPDX-License-Identifier: Apache-2.0
"""Display names generated at ingest: ``<Type> <n>`` within each type, by raw id."""

from __future__ import annotations

from dataclasses import replace

from coperceive.adapters.naming import TYPE_LABELS, display_names
from coperceive.model import Agent, AgentType
from coperceive.viewdata import summarize

from tests.conftest import sample_scene


def _agent(agent_id: str, kind: AgentType) -> Agent:
    return Agent(agent_id, kind, ())


def test_numbers_within_each_type_numeric_ids_first_then_alphabetical() -> None:
    agents = (
        _agent("b", AgentType.VEHICLE),
        _agent("10", AgentType.VEHICLE),
        _agent("-1", AgentType.INFRASTRUCTURE),
        _agent("9", AgentType.VEHICLE),
        _agent("a", AgentType.VEHICLE),
        _agent("drone", AgentType.UAV),
        _agent("x", AgentType.OTHER),
    )
    named = display_names(agents)
    assert [a.agent_id for a in named] == [a.agent_id for a in agents]  # scene order kept
    assert {a.agent_id: a.display_name for a in named} == {
        "9": "Vehicle 1",
        "10": "Vehicle 2",
        "a": "Vehicle 3",
        "b": "Vehicle 4",
        "-1": "RSU 1",
        "drone": "Drone 1",
        "x": "Agent 1",
    }


def test_labels_cover_every_agent_type() -> None:
    assert set(TYPE_LABELS) == set(AgentType)
    assert TYPE_LABELS[AgentType.UAV] == "Drone"
    assert TYPE_LABELS[AgentType.INFRASTRUCTURE] == "RSU"


def test_scene_summary_carries_the_display_name_or_null() -> None:
    scene = sample_scene()
    rows = {row["agent_id"]: row["display_name"] for row in _agents(summarize(scene))}
    assert set(rows.values()) == {None}
    named = replace(scene, agents=display_names(scene.agents))
    rows = {row["agent_id"]: row["display_name"] for row in _agents(summarize(named))}
    assert rows["vehicle"] == "Vehicle 1"
    assert rows["drone"] == "Drone 1"


def _agents(summary: dict[str, object]) -> list[dict[str, object]]:
    agents = summary["agents"]
    assert isinstance(agents, list)
    return [row for row in agents if isinstance(row, dict)]  # pyright: ignore[reportUnknownVariableType]
