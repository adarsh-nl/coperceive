# SPDX-License-Identifier: Apache-2.0
"""Pose lookup and ``T(dst ← src, t)`` for world, body, and sensor frames.

Interpolation is linear in translation and a slerp in rotation. It happens
only between two samples whose separation is at most ``max_gap_us``. A query
outside the recorded span raises :class:`ExtrapolationError`. There is no
default for ``max_gap_us``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from coperceive.errors import ExtrapolationError, GapError, TimeQueryError
from coperceive.model import Agent, Scene, require_time_us
from coperceive.se3 import SE3, slerp

FrameKind = Literal["world", "body", "sensor"]


@dataclass(frozen=True)
class Frame:
    """A frame ``T`` can name.

    ``world`` is the scene world frame. ``body`` is one agent's body.
    ``sensor`` is one sensor on that agent. ``body_from_sensor`` is rigid, and
    a sensor query still has to land inside the agent's pose span.
    """

    kind: FrameKind
    agent_id: str | None = None
    sensor_id: str | None = None

    def __post_init__(self) -> None:
        if self.kind == "world":
            if self.agent_id is not None or self.sensor_id is not None:
                raise ValueError("the world frame has no agent or sensor")
        elif self.kind == "body":
            if not self.agent_id or self.sensor_id is not None:
                raise ValueError("a body frame needs an agent and no sensor")
        elif self.kind == "sensor":
            if not self.agent_id or not self.sensor_id:
                raise ValueError("a sensor frame needs an agent and a sensor")
        else:
            raise ValueError("frame kind must be world, body, or sensor")


def world_frame() -> Frame:
    return Frame("world")


def body_frame(agent_id: str) -> Frame:
    return Frame("body", agent_id)


def sensor_frame(agent_id: str, sensor_id: str) -> Frame:
    return Frame("sensor", agent_id, sensor_id)


def lookup_pose(agent: Agent, time_us: int, *, max_gap_us: int) -> SE3:
    require_time_us(time_us, "time_us")
    if type(max_gap_us) is not int:
        raise TimeQueryError("max_gap_us must be a Python int")
    if max_gap_us < 0:
        raise TimeQueryError("max_gap_us must be >= 0")
    poses = agent.poses
    if not poses:
        raise TimeQueryError(f"agent {agent.agent_id} has no poses")
    if time_us < poses[0].time_us or time_us > poses[-1].time_us:
        raise ExtrapolationError(
            f"time {time_us} is outside [{poses[0].time_us}, {poses[-1].time_us}] "
            f"for agent {agent.agent_id}"
        )
    for i, pose in enumerate(poses):
        if pose.time_us == time_us:
            return pose.world_from_body
        if pose.time_us > time_us:
            prev = poses[i - 1]
            gap = pose.time_us - prev.time_us
            if gap > max_gap_us:
                raise GapError(
                    f"gap {gap} us around {time_us} exceeds max_gap_us {max_gap_us} "
                    f"for agent {agent.agent_id}"
                )
            u = (time_us - prev.time_us) / gap
            return slerp(prev.world_from_body, pose.world_from_body, u)
    raise ExtrapolationError(f"time {time_us} fell through for agent {agent.agent_id}")


def _world_from_frame(scene: Scene, frame: Frame, time_us: int, max_gap_us: int) -> SE3:
    if frame.kind == "world":
        return SE3.identity()
    if frame.agent_id is None:
        raise TimeQueryError("body and sensor frames need an agent")
    world_from_body = lookup_pose(scene.agent(frame.agent_id), time_us, max_gap_us=max_gap_us)
    if frame.kind == "body":
        return world_from_body
    if frame.sensor_id is None:
        raise TimeQueryError("a sensor frame needs a sensor")
    sensor = scene.sensor(frame.agent_id, frame.sensor_id)
    return world_from_body.compose(sensor.body_from_sensor)


def transform(
    scene: Scene,
    dst: Frame,
    src: Frame,
    time_us: int,
    *,
    max_gap_us: int,
) -> SE3:
    """Return ``T(dst ← src, t)``.

    Stored agent poses are ``world ← body``. A sensor extrinsic is
    ``body ← sensor``. Therefore
    ``T(dst ← src) = inv(world ← dst) ∘ (world ← src)``.
    The world frame itself has no samples; asking only for world does not
    consult the clock. Any body or sensor frame raises outside that agent's
    span, or when the bracketing gap is larger than ``max_gap_us``.
    """
    world_from_dst = _world_from_frame(scene, dst, time_us, max_gap_us)
    world_from_src = _world_from_frame(scene, src, time_us, max_gap_us)
    return world_from_dst.inverse().compose(world_from_src)


def T_dst_from_src(
    scene: Scene,
    dst_agent: str,
    src_agent: str,
    time_us: int,
    *,
    max_gap_us: int,
) -> SE3:
    """``T(dst body ← src body, t)``."""
    return transform(
        scene,
        body_frame(dst_agent),
        body_frame(src_agent),
        time_us,
        max_gap_us=max_gap_us,
    )
