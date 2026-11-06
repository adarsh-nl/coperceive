# SPDX-License-Identifier: Apache-2.0
"""Which agent observed each object, and whether a dataset actually records it.

The viewer may colour a box "both", "ego only" or "the other agent only" only
when the source file for each agent is that agent's own list and a shared
``track_id`` is the same object. Distance and occlusion are not a substitute.

- OPV2V: yes. Per-agent YAML ``vehicles`` key, stored as ``annotations.agent_id``
  and ``annotations.track_id``. Each list is that agent's lidar hits.
- V2XSet: no. The same ``vehicles`` key is not each agent's own hits
  (``docs/datasets/opencood.md``).
- V2V4Real: yes. Per-agent YAML ``vehicles`` key on that CAV's frame file.
- DAIR-V2X-C: no. ``label_world`` is one shared list stored on the vehicle.
- Griffin: yes. ``{agent}/label/{frame}.txt`` ``track_id`` column.
- generic, including ``coperceive demo create``: no. The demo copies one
  catalog onto cav-a and cav-b and writes no boxes for the RSU.
- unknown: no. ``COMPLETE.json`` has no adapter, or the world frame is shared
  (``carla-map`` is both OPV2V and V2XSet) and is not guessed.

A dataset that records the field still hides the control when this scene's
boxes have no ``track_id``.
"""

from __future__ import annotations

import json
from pathlib import Path

from coperceive.model import Scene

# adapter id → (available, field). ``available`` is the dataset policy, before
# this scene's boxes are checked.
_POLICY: dict[str, tuple[bool, str]] = {
    "opv2v": (
        True,
        "per-agent YAML vehicles key, stored as annotations.agent_id + annotations.track_id",
    ),
    "v2xset": (
        False,
        "per-agent YAML vehicles key; the lists are not each agent's own observations",
    ),
    "v2v4real": (
        True,
        "per-agent YAML vehicles mapping key on that CAV's frame file",
    ),
    "dair-v2x-c": (
        False,
        "cooperative label_world is one shared list stored on the vehicle; no per-agent observer",
    ),
    "griffin": (
        True,
        "{agent}/label/{frame}.txt track_id column",
    ),
    "generic": (
        False,
        "generic boxes are not an observation field; the demo copies one catalog onto each vehicle",
    ),
}

# World frames that name one dataset when COMPLETE.json has no adapter id.
# carla-map and ENU do not: carla-map is OPV2V and V2XSet, ENU is also used by fixtures.
_WORLD_FRAME: dict[str, str] = {
    "v2v4real-world": "v2v4real",
    "dair-v2x-c-world": "dair-v2x-c",
    "right-handed ENU": "generic",
}


def adapter_name(scene_dir: Path | None) -> str | None:
    """The ``adapter`` string ``COMPLETE.json`` records, or None."""
    if scene_dir is None:
        return None
    path = scene_dir / "COMPLETE.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    name = payload.get("adapter")
    return name if isinstance(name, str) and name else None


def seen_by_for_scene(scene: Scene, adapter: str | None) -> dict[str, object]:
    """``{available, dataset, field}`` for the scene summary."""
    dataset = adapter if adapter in _POLICY else _WORLD_FRAME.get(scene.world_frame)
    if dataset is None or dataset not in _POLICY:
        return {
            "available": False,
            "dataset": dataset or "unknown",
            "field": "no recorded observer field for this scene",
        }
    available, field = _POLICY[dataset]
    if available and not any(ann.track_id for ann in scene.annotations):
        return {
            "available": False,
            "dataset": dataset,
            "field": f"{field}; this scene's boxes have no track_id",
        }
    return {"available": available, "dataset": dataset, "field": field}
