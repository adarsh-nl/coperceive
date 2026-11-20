# SPDX-License-Identifier: Apache-2.0
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
"""The connected-union ground truth of a fused output (``docs/eval-spec.md`` A.4).

OPV2V and V2X-ViT score a cooperative result against the vehicles hit by at least
one LiDAR point from any connected agent, the agents within 70 m of the ego. This
module holds the pieces of that rule that do not depend on the evaluator's types:

* which agents are connected to the ego at a time;
* the LiDAR returns of a set of agents at a time, in the world frame;
* whether a box holds at least one of them;
* which boxes of one list are the same object as a box of another (the pairing of
  spec A.9: shared track ids first, then nearest world centres within a gate).
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from coperceive.errors import CoperceiveError
from coperceive.evaluation.geometry import WorldBox
from coperceive.model import Scene
from coperceive.se3 import SE3
from coperceive.transform import lookup_pose

Points = npt.NDArray[np.float64]

# The gate of a computed pair (spec A.9): at least this far, or half the longer box.
PAIR_MIN_GATE_M = 2.0
PAIR_SIZE_FRACTION = 0.5


@dataclass(frozen=True)
class UnionBox:
    """What the pairing needs to know about one ground-truth box."""

    track_id: str | None
    world: WorldBox


def _pose_at(scene: Scene, agent_id: str, time_us: int) -> SE3 | None:
    """The agent's pose sample at exactly ``time_us``, or ``None`` when it has none."""
    for pose in scene.agent(agent_id).poses:
        if pose.time_us == time_us:
            return pose.world_from_body
    return None


def connected_agents(scene: Scene, ego: str, time_us: int, comm_range_m: float) -> list[str]:
    """Agents other than ``ego``, in scene order, with a pose at ``time_us`` within range.

    The distance is horizontal, between the pose translations (spec A.4, item 1).
    """
    origin = _pose_at(scene, ego, time_us)
    if origin is None:
        return []
    found: list[str] = []
    for agent in scene.agents:
        if agent.agent_id == ego:
            continue
        pose = _pose_at(scene, agent.agent_id, time_us)
        if pose is None:
            continue
        if math.hypot(pose.tx - origin.tx, pose.ty - origin.ty) <= comm_range_m:
            found.append(agent.agent_id)
    return found


def _matrix(q: SE3) -> Points:
    w, x, y, z = q.qw, q.qx, q.qy, q.qz
    return np.array(
        [
            [1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
            [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
            [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)],
        ],
        dtype=np.float64,
    )


def lidar_points_world(scene: Scene, agent_ids: Iterable[str], time_us: int) -> Points | None:
    """Every LiDAR return of ``agent_ids`` recorded at ``time_us``, in the world frame.

    ``None`` when none of them has a sweep at that time (spec A.4, item 2: their own
    annotation lists then stand for what they saw). A return goes through its
    sensor's mount and its agent's pose at that time.
    """
    wanted = set(agent_ids)
    parts: list[Points] = []
    for cloud in scene.point_clouds:
        if cloud.agent_id not in wanted or cloud.time_us != time_us:
            continue
        try:
            pose = lookup_pose(scene.agent(cloud.agent_id), time_us, max_gap_us=0)
        except CoperceiveError:
            continue
        mount = scene.sensor(cloud.agent_id, cloud.sensor_id).body_from_sensor
        world_from_sensor = pose.compose(mount)
        raw = np.frombuffer(cloud.xyz_i_f32, dtype="<f4").reshape(-1, 4)[:, :3]
        local = raw.astype(np.float64)
        rotation = _matrix(world_from_sensor)
        translation = np.array(
            [world_from_sensor.tx, world_from_sensor.ty, world_from_sensor.tz], dtype=np.float64
        )
        parts.append(local @ rotation.T + translation)
    if not parts:
        return None
    return np.concatenate(parts, axis=0)


def holds_a_point(box: WorldBox, points: Points) -> bool:
    """True when at least one point lies inside ``box``, boundary included."""
    if len(points) == 0:
        return False
    centre = np.array(box.center, dtype=np.float64)
    rotation = _matrix(box.rotation)
    local = (points - centre) @ rotation
    half = np.array(box.size_lwh, dtype=np.float64) / 2.0
    return bool(np.any(np.all(np.abs(local) <= half, axis=1)))


def pair_with(
    left: Sequence[UnionBox], right: Sequence[UnionBox], shared_ids: frozenset[str]
) -> set[int]:
    """Indices of ``right`` boxes that are the same object as a box of ``left`` (spec A.9).

    1. Shared track: for each ``left`` box in order whose track id is a shared id, the
       first unused ``right`` box with the same id.
    2. Computed: among the rest, every pair whose 3D world-centre distance is at most
       ``max(2.0, 0.5 * longer length)``, sorted by distance, then left index, then
       right index, taken greedily.
    """
    used_left: set[int] = set()
    used_right: set[int] = set()
    for i, a in enumerate(left):
        if a.track_id is None or a.track_id not in shared_ids:
            continue
        for j, b in enumerate(right):
            if j not in used_right and b.track_id == a.track_id:
                used_left.add(i)
                used_right.add(j)
                break
    candidates: list[tuple[float, int, int]] = []
    for i, a in enumerate(left):
        if i in used_left:
            continue
        for j, b in enumerate(right):
            if j in used_right:
                continue
            distance = math.dist(a.world.center, b.world.center)
            gate = max(
                PAIR_MIN_GATE_M, PAIR_SIZE_FRACTION * max(a.world.size_lwh[0], b.world.size_lwh[0])
            )
            if distance <= gate:
                candidates.append((distance, i, j))
    candidates.sort()
    for _distance, i, j in candidates:
        if i in used_left or j in used_right:
            continue
        used_left.add(i)
        used_right.add(j)
    return used_right
