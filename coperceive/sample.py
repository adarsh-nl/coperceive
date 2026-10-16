# SPDX-License-Identifier: Apache-2.0
"""A cooperative sample at one query time.

``Scene.at`` reports every agent. A miss is an empty pose and an empty box
list, with ``matched`` false. It is not omitted and it does not raise.
The pose is the discrete sample from :func:`coperceive.query.query_agents`,
not an interpolated pose. ``tolerance`` is the spec's ``tolerance_us``.
"""

from __future__ import annotations

from dataclasses import dataclass

from coperceive.model import Annotation, ImageRef, PointCloud, Scene, Sensor
from coperceive.query import TimeMode, query_agents
from coperceive.se3 import SE3


@dataclass(frozen=True)
class AgentSample:
    """One agent inside a cooperative sample.

    ``dt_us`` is ``sample_time_us - query_time_us`` when ``matched`` is true.
    Sensors are the agent's rigid mounts and are present on a miss. Boxes,
    images, and clouds are only those stamped at the chosen sample time.
    """

    agent_id: str
    matched: bool
    time_us: int | None
    dt_us: int | None
    pose: SE3 | None
    sensors: tuple[Sensor, ...]
    annotations: tuple[Annotation, ...]
    images: tuple[ImageRef, ...]
    point_clouds: tuple[PointCloud, ...]


@dataclass(frozen=True)
class CooperativeSample:
    query_us: int
    tolerance_us: int
    mode: TimeMode
    agents: tuple[AgentSample, ...]

    def agent(self, agent_id: str) -> AgentSample:
        for row in self.agents:
            if row.agent_id == agent_id:
                return row
        raise KeyError(agent_id)


def scene_at(
    scene: Scene,
    t: int,
    *,
    tolerance: int,
    mode: TimeMode = TimeMode.NEAREST,
) -> CooperativeSample:
    """Return every agent's sample at ``t``.

    ``mode`` defaults to nearest. A tie keeps the earlier sample, as in
    :func:`coperceive.query.query_time`. ``tolerance`` is required and is
    ``tolerance_us`` in microseconds.
    """
    hits = query_agents(scene, t, mode, tolerance_us=tolerance)
    rows: list[AgentSample] = []
    for hit in hits:
        sensors = tuple(sensor for sensor in scene.sensors if sensor.agent_id == hit.agent_id)
        if not hit.matched or hit.time_us is None or hit.dt_us is None or hit.index is None:
            rows.append(
                AgentSample(
                    agent_id=hit.agent_id,
                    matched=False,
                    time_us=None,
                    dt_us=None,
                    pose=None,
                    sensors=sensors,
                    annotations=(),
                    images=(),
                    point_clouds=(),
                )
            )
            continue
        pose = scene.agent(hit.agent_id).poses[hit.index].world_from_body
        rows.append(
            AgentSample(
                agent_id=hit.agent_id,
                matched=True,
                time_us=hit.time_us,
                dt_us=hit.dt_us,
                pose=pose,
                sensors=sensors,
                annotations=tuple(
                    ann
                    for ann in scene.annotations
                    if ann.agent_id == hit.agent_id and ann.time_us == hit.time_us
                ),
                images=tuple(
                    image
                    for image in scene.images
                    if image.agent_id == hit.agent_id and image.time_us == hit.time_us
                ),
                point_clouds=tuple(
                    cloud
                    for cloud in scene.point_clouds
                    if cloud.agent_id == hit.agent_id and cloud.time_us == hit.time_us
                ),
            )
        )
    return CooperativeSample(query_us=t, tolerance_us=tolerance, mode=mode, agents=tuple(rows))
