# SPDX-License-Identifier: Apache-2.0
"""What an exporter tells the checks about the scene its export should become.

Plain data, shared by the exporters (which import nothing from ``coperceive``)
and the checks (which read the ingested scene).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from tests.geomworld.world import World


@dataclass(frozen=True)
class Expectation:
    """How a world maps onto the scene an adapter builds from its export.

    An exporter fills this in from the dataset's own rules: which objects an agent's
    file lists, what the agents and sensors are called on disk.

    * ``agents``: world agent id → scene agent id;
    * ``lidars``: world agent id → scene sensor id of its LiDAR;
    * ``cameras``: ``(world agent id, world camera id)`` → scene sensor id;
    * ``annotated``: ``(world agent id, frame)`` → the object ids that agent's
      annotations list at that frame;
    * ``track_ids``: object id → the scene's ``track_id`` for it;
    * ``categories``: world class → scene class.
    """

    agents: Mapping[str, str]
    lidars: Mapping[str, str]
    cameras: Mapping[tuple[str, str], str]
    annotated: Mapping[tuple[str, int], tuple[int, ...]]
    track_ids: Mapping[int, str]
    categories: Mapping[str, str] = field(default_factory=dict[str, str])


@dataclass(frozen=True)
class Export:
    """One world written in one dataset's native layout.

    ``adapter`` names the adapter that must read it; ``raw_root`` is the directory
    handed to it with ``params``; ``scene_id`` is the scene it should produce.
    ``world`` is what was written (a dataset without a roadside unit or an aerial
    agent gets a subset) and ``expectation`` maps it onto the scene. ``readings``
    records, for every convention the dataset's documentation leaves open, which
    reading this export used.
    """

    adapter: str
    raw_root: Path
    params: Mapping[str, str]
    scene_id: str
    world: World
    expectation: Expectation
    readings: Mapping[str, str] = field(default_factory=dict[str, str])
