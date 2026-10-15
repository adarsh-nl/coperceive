# SPDX-License-Identifier: Apache-2.0
"""Pose lookup and ``T(dst <- src, t)``.

Interpolation is linear in translation and a slerp in rotation. It happens
only between two samples whose separation is at most ``max_gap_us``. A query
outside the recorded span raises :class:`ExtrapolationError` instead of
extending the last pose.
"""

from __future__ import annotations

from pkg.errors import ExtrapolationError, GapError, TimeQueryError
from pkg.model import Agent, Scene
from pkg.se3 import SE3, slerp


def lookup_pose(agent: Agent, time_us: int, *, max_gap_us: int) -> SE3:
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


def T_dst_from_src(
    scene: Scene,
    dst_agent: str,
    src_agent: str,
    time_us: int,
    *,
    max_gap_us: int,
) -> SE3:
    """Return the transform that takes a point in ``src`` body frame into ``dst`` body frame.

    Poses stored on each agent are ``world_from_body``. Therefore
    ``T(dst <- src) = inv(world_from_dst) ∘ world_from_src``.
    """
    world_from_dst = lookup_pose(scene.agent(dst_agent), time_us, max_gap_us=max_gap_us)
    world_from_src = lookup_pose(scene.agent(src_agent), time_us, max_gap_us=max_gap_us)
    return world_from_dst.inverse().compose(world_from_src)
