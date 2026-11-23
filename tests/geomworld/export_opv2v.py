# SPDX-License-Identifier: Apache-2.0
"""The synthetic world written as an OPV2V release (OpenCOOD format).

Written from the OpenCOOD documentation, the OPV2V paper and the CARLA documentation,
through this workflow's cite-checked research notes; the adapter that reads OPV2V, its
fixtures and the repository's convention pages were not opened. The conversions live in
:mod:`tests.geomworld.opencood_format`, shared with V2XSet, and quote their sources
there; the bracketed source names below are listed in that module's docstring.

What is written under ``root`` (the release root that holds the split folders)::

    <split>/2026_11_16_00_00_00/data_protocol.yaml
    <split>/2026_11_16_00_00_00/<agent>/<stem>.pcd
    <split>/2026_11_16_00_00_00/<agent>/<stem>.yaml
    <split>/2026_11_16_00_00_00/<agent>/<stem>_camera0.png .. <stem>_camera3.png

"Each scenario is named by the time it was gathered, e.g., `2021_08_22_21_41_24`."
[OC-intro §2]; the agent folders are the vehicles' integer ids, 650 for the world's ego
and 651 for its other vehicle (:func:`~tests.geomworld.opencood_format.carla_agent_names`);
``<stem>`` is the frame name (``000000``, ``000001``, ... by default; see the readings
``frame_digits`` and ``frame_numbering``). The split folders are ``train``,
``validate``, ``test`` and ``test_culvercity`` [OC-intro, directory tree]; the adapter's
``split`` parameter ``test_culver_city`` is written to ``test_culvercity``.

What OPV2V cannot hold, and so is neither written nor expected:

* the roadside unit and the aerial agent: "OPV2V only has vehicles while V2XSet has
  infrastructure sensors" [OC-intro, V2XSet §2]. They are dropped with ``subset()``.
* pedestrians: only vehicles are annotated ("the surrounding vehicles that have at least
  one LiDAR point hit from the agent" [OC-ann]). Their returns stay in the sweeps; they
  are in no ``vehicles`` list and not in ``annotated``.

Everything else of the world is expressible: CARLA's angle triple carries roll and pitch
(the docs' own example box has a roll of 0.096 and a pitch of 0.197 [OC-ann]), and the
four vehicle cameras map onto ``camera0`` .. ``camera3`` by where they look.

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

ADAPTER = "opv2v"
# A scenario folder named like the docs' examples (a collection time).
SCENE_ID = "2026_11_16_00_00_00"
# The adapter's ``split`` parameter -> the split folder [OC-intro, directory tree].
SPLITS: Mapping[str, str] = {
    "train": "train",
    "validate": "validate",
    "test": "test",
    "test_culver_city": "test_culvercity",
}
# The world's first vehicle becomes agent 650, the next 651, ...; the world's object ids
# (1 and up) stay below, so agents and objects never share an id.
FIRST_VEHICLE_ID = 650

# The readings of :func:`export`, documented with their sources in
# ``opencood_format.CARLA_READINGS``. OPV2V and V2XSet were made by one pipeline ("label
# format is nearly the same with OPV2V" [OC-intro, V2XSet §2]), so they share them.
READINGS: Mapping[str, tuple[str, ...]] = CARLA_READINGS
UNVERIFIED: frozenset[str] = CARLA_UNVERIFIED


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
    """Write the world's vehicles under ``root`` as one OPV2V scenario of ``split``.

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
    kept = subset(world, tuple(agent.agent_id for agent in world.agents if agent.kind == "vehicle"))
    if not kept.agents:
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
