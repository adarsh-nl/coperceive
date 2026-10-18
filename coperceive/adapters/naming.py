# SPDX-License-Identifier: Apache-2.0
"""Display names generated at ingest (schema 1 ``display_name``, optional per agent).

``<Type> <n>``: agents are numbered within each type in ascending raw-id order, numeric ids
numerically first and the rest alphabetically after them, so OPV2V's CAVs read Vehicle 1,
2, 3 and V2XSet's roadside unit ``-1`` reads RSU 1. The raw ``agent_id`` stays the key for
tracks, predictions, evaluation and URLs; the name is for people.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace

from coperceive.model import Agent, AgentType

TYPE_LABELS: Mapping[AgentType, str] = {
    AgentType.VEHICLE: "Vehicle",
    AgentType.UAV: "Drone",
    AgentType.INFRASTRUCTURE: "RSU",
    AgentType.OTHER: "Agent",
}


def _order_key(agent_id: str) -> tuple[int, int, str]:
    try:
        return (0, int(agent_id), agent_id)
    except ValueError:
        return (1, 0, agent_id)


def display_names(agents: Sequence[Agent]) -> tuple[Agent, ...]:
    """Every agent with its generated name; the scene order of ``agents`` is kept."""
    numbered: dict[str, str] = {}
    for kind in AgentType:
        of_kind = (agent.agent_id for agent in agents if agent.agent_type is kind)
        ids = sorted(of_kind, key=_order_key)
        for number, agent_id in enumerate(ids, start=1):
            numbered[agent_id] = f"{TYPE_LABELS[kind]} {number}"
    return tuple(replace(agent, display_name=numbered[agent.agent_id]) for agent in agents)
