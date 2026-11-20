# SPDX-License-Identifier: Apache-2.0
"""The synthetic world written as a V2XSet release (OpenCOOD format).

Written from the OpenCOOD and v2x-vit documentation, the V2X-ViT paper and the CARLA
documentation, through this workflow's cite-checked research notes; the adapter that
reads V2XSet, its fixtures and the repository's convention pages were not opened. The
conversions live in :mod:`tests.geomworld.opencood_format`, shared with OPV2V, and quote
their sources there; the bracketed source names below are listed in that module's
docstring.

"V2XSet's data label format is nearly the same with OPV2V, except: OPV2V only has
vehicles while V2XSet has infrastructure sensors; all the infrastructure sensors' folder
is named with negative integer, e.g. `-1`" [OC-intro, V2XSet §2]. What is written under
``root`` (the release root that holds the split folders)::

    <split>/2026_11_16_00_00_00/data_protocol.yaml
    <split>/2026_11_16_00_00_00/<agent>/<stem>.pcd
    <split>/2026_11_16_00_00_00/<agent>/<stem>.yaml
    <split>/2026_11_16_00_00_00/<agent>/<stem>_camera<k>.png

The agent folders are 650 (the world's ego), 651 (its other vehicle) and -1 (the roadside
unit). The split folders are ``train``, ``validate`` and ``test`` [VIT-intro, directory
tree; OC-intro, V2XSet §1].

Cameras: "Each CAV and Infra is equipped with 4 RGB cameras" and "`camera0`, `camera1`,
`camera2`, and `camera3` represent the front, right rear, left rear, and back cameras
respectively." [VIT-intro §3.2]. The vehicles' four cameras are written as
``camera0`` .. ``camera3``; the roadside unit has one camera in the world, looking along
its body's +x, and it is written as ``camera0`` (its front camera) with no
``camera1`` .. ``camera3``: the world has nothing to put there.

What V2XSet cannot hold, and so is neither written nor expected:

* the aerial agent: an agent folder is a vehicle or (negative id) infrastructure
  [VIT-intro §3]. It is dropped with ``subset()``.
* pedestrians: only vehicles are annotated ("The pose and velocity of each surrounding
  human driving vehicle" [VIT-intro §3.3]). Their returns stay in the sweeps; they are in
  no ``vehicles`` list and not in ``annotated``.

Not written: the top-level ``RSU`` flag that OpenCDA's main-branch dumper adds. No
document says the released files carry it; the documented sign of an infrastructure
agent is its negative folder name.

Where the sources are silent or disagree, :func:`export` takes a keyword; its default is
the reading the sources support best. :data:`READINGS` lists every keyword with its
values (default first) and :data:`UNVERIFIED` those whose default rests on a toolkit's
code, an inference or documents that disagree.

It imports nothing from ``coperceive``.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from tests.geomworld.expectation import Export
from tests.geomworld.opencood_format import (
    CARLA_READINGS,
    CARLA_UNVERIFIED,
    carla_agent_names,
    check_readings,
    write_carla_scenario,
)
from tests.geomworld.scenes import subset
from tests.geomworld.world import World

ADAPTER = "v2xset"
# A scenario folder named like the docs' examples (a collection time).
SCENE_ID = "2026_11_16_00_00_00"
# The adapter's ``split`` parameter -> the split folder [VIT-intro, directory tree].
SPLITS: Mapping[str, str] = {"train": "train", "validate": "validate", "test": "test"}
# Vehicles 650, 651, ...; roadside units -1, -2, ... The world's object ids (1 and up)
# never meet either.
FIRST_VEHICLE_ID = 650

# The readings of :func:`export`, documented with their sources in
# ``opencood_format.CARLA_READINGS``, shared with OPV2V ("label format is nearly the same
# with OPV2V" [OC-intro, V2XSet §2]). The five-digit frame names of the V2XSet docs
# ("00000.pcd - 00700.pcd" [VIT-intro, directory tree]) are the reading
# ``frame_digits="5"``.
READINGS: Mapping[str, tuple[str, ...]] = CARLA_READINGS
UNVERIFIED: frozenset[str] = CARLA_UNVERIFIED
_KINDS = ("vehicle", "infrastructure")


def export(
    world: World,
    root: Path,
    *,
    split: str = "train",
    handedness: str = "left",
    euler: str = "carla",
    box_location: str = "actor_origin",
    points_frame: str = "sensor",
    camera_extrinsic: str = "lidar_to_camera",
    camera_axes: str = "sensor",
    pcd_encoding: str = "ascii",
    pcd_fields: str = "xyz_rgb",
    frame_digits: str = "6",
    frame_numbering: str = "from_zero",
    speed_unit: str = "km/h",
) -> Export:
    """Write the world's vehicles and roadside units under ``root`` as one V2XSet scenario.

    ``root`` is the release root (it gets the split folder) and is what the reader is
    handed. Every other keyword is a reading, see :data:`READINGS`; the values used are
    returned in ``Export.readings``.
    """
    if split not in SPLITS:
        raise ValueError(f"split={split!r}: expected one of {', '.join(SPLITS)}")
    readings = {
        "handedness": handedness,
        "euler": euler,
        "box_location": box_location,
        "points_frame": points_frame,
        "camera_extrinsic": camera_extrinsic,
        "camera_axes": camera_axes,
        "pcd_encoding": pcd_encoding,
        "pcd_fields": pcd_fields,
        "frame_digits": frame_digits,
        "frame_numbering": frame_numbering,
        "speed_unit": speed_unit,
    }
    check_readings(readings, READINGS)
    kept = subset(world, tuple(agent.agent_id for agent in world.agents if agent.kind in _KINDS))
    if not any(agent.kind == "vehicle" for agent in kept.agents):
        raise ValueError("the world has no vehicle")
    names = carla_agent_names(kept.agents, FIRST_VEHICLE_ID)
    expectation = write_carla_scenario(kept, root / SPLITS[split] / SCENE_ID, names, readings)
    return Export(
        adapter=ADAPTER,
        raw_root=root,
        params={"split": split},
        scene_id=SCENE_ID,
        world=kept,
        expectation=expectation,
        readings=readings,
    )
