# SPDX-License-Identifier: Apache-2.0
"""Discrete time queries.

``dt_us`` is ``sample_time_us - query_time_us``. A negative value means the
sample is earlier than the query.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

from coperceive.errors import TimeQueryError, ToleranceError
from coperceive.model import Scene, require_time_us


class TimeMode(enum.Enum):
    EXACT = "exact"
    NEAREST = "nearest"
    BACKWARD = "backward"
    FORWARD = "forward"


@dataclass(frozen=True)
class TimeHit:
    index: int
    time_us: int
    dt_us: int


@dataclass(frozen=True)
class AgentTimeHit:
    """One agent's discrete sample for a query.

    ``dt_us`` is ``sample_time_us - query_time_us`` when ``matched`` is true.
    A miss leaves the sample fields empty. It is not an exception: other
    agents may still have a sample. ``tolerance_us`` is applied per agent.
    """

    agent_id: str
    matched: bool
    index: int | None
    time_us: int | None
    dt_us: int | None


def query_time(
    times_us: tuple[int, ...] | list[int],
    query_us: int,
    mode: TimeMode,
    *,
    tolerance_us: int,
) -> TimeHit:
    """Pick one sample. Raises if the selection is farther than ``tolerance_us``."""
    require_time_us(query_us, "query_us")
    if type(tolerance_us) is not int:
        raise TimeQueryError("tolerance_us must be a Python int")
    if tolerance_us < 0:
        raise TimeQueryError("tolerance_us must be >= 0")
    if not times_us:
        raise TimeQueryError("no samples")
    if any(times_us[i] >= times_us[i + 1] for i in range(len(times_us) - 1)):
        raise TimeQueryError("times must be strictly increasing")

    if mode is TimeMode.EXACT:
        index = _exact_index(times_us, query_us, tolerance_us)
    elif mode is TimeMode.BACKWARD:
        index = _backward_index(times_us, query_us)
    elif mode is TimeMode.FORWARD:
        index = _forward_index(times_us, query_us)
    elif mode is TimeMode.NEAREST:
        index = _nearest_index(times_us, query_us)
    else:
        raise TimeQueryError(f"unknown mode {mode!r}")

    sample = times_us[index]
    dt = sample - query_us
    if abs(dt) > tolerance_us:
        raise ToleranceError(
            f"{mode.value} sample at {sample} is {dt} us from {query_us}; "
            f"tolerance is {tolerance_us} us"
        )
    return TimeHit(index=index, time_us=sample, dt_us=dt)


def query_agents(
    scene: Scene,
    query_us: int,
    mode: TimeMode,
    *,
    tolerance_us: int,
) -> tuple[AgentTimeHit, ...]:
    """Report every agent's ``dt_us``. An agent outside tolerance is a miss.

    Invalid ``tolerance_us`` still raises. A miss is an empty
    :class:`AgentTimeHit`, including an agent with no pose at or before the
    query in backward mode, and no pose at or after it in forward mode.
    """
    require_time_us(query_us, "query_us")
    if type(tolerance_us) is not int:
        raise TimeQueryError("tolerance_us must be a Python int")
    if tolerance_us < 0:
        raise TimeQueryError("tolerance_us must be >= 0")
    rows: list[AgentTimeHit] = []
    for agent in scene.agents:
        try:
            hit = query_time(
                scene.pose_times(agent.agent_id),
                query_us,
                mode,
                tolerance_us=tolerance_us,
            )
        except TimeQueryError:
            rows.append(AgentTimeHit(agent.agent_id, False, None, None, None))
        else:
            rows.append(AgentTimeHit(agent.agent_id, True, hit.index, hit.time_us, hit.dt_us))
    return tuple(rows)


def _exact_index(times_us: list[int] | tuple[int, ...], query_us: int, tolerance_us: int) -> int:
    best: int | None = None
    best_abs = tolerance_us + 1
    for i, t in enumerate(times_us):
        gap = abs(t - query_us)
        if gap <= tolerance_us and gap < best_abs:
            best = i
            best_abs = gap
    if best is None:
        raise ToleranceError(f"no sample within {tolerance_us} us of {query_us}")
    return best


def _backward_index(times_us: list[int] | tuple[int, ...], query_us: int) -> int:
    chosen: int | None = None
    for i, t in enumerate(times_us):
        if t <= query_us:
            chosen = i
        else:
            break
    if chosen is None:
        raise TimeQueryError(f"no sample at or before {query_us}")
    return chosen


def _forward_index(times_us: list[int] | tuple[int, ...], query_us: int) -> int:
    for i, t in enumerate(times_us):
        if t >= query_us:
            return i
    raise TimeQueryError(f"no sample at or after {query_us}")


def _nearest_index(times_us: list[int] | tuple[int, ...], query_us: int) -> int:
    best = 0
    best_abs = abs(times_us[0] - query_us)
    for i, t in enumerate(times_us):
        gap = abs(t - query_us)
        # Ties resolve to the earlier sample.
        if gap < best_abs:
            best = i
            best_abs = gap
    return best
