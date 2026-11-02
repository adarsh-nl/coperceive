# SPDX-License-Identifier: Apache-2.0
"""Apply a perturbation spec to one cooperative sample.

Order, which the tests lock:

1. Agent dropout. Each non-ego agent draws one uniform and is removed when it
   is below ``agent_dropout``. The ego never draws.
2. ``max_agents``, including the ego. Survivors keep scene order, so the ego
   and the earliest remaining agents stay.
3. Latency. A remaining non-ego agent is replaced by its nearest earlier pose
   that is ``latency_frames`` steps back, or at least ``latency_ms`` earlier.
   If both are set, the earlier of the two is used. A missing earlier pose
   removes the agent. The current pose is not reused and nothing is extrapolated.
4. Packet loss. Each agent still present draws one uniform and its message is
   dropped when the draw is below ``packet_loss``.
5. Pose noise. Gaussian noise is added to x, y and yaw of each message that
   arrived. Draws use Box-Muller on ``random.Random`` so the sequence does not
   depend on the Python version's ``random.gauss``.

Every frame has its own generator, ``Random(blake2s(seed|scene_id|time_us))``,
so the result does not depend on which other frames were perturbed. Ground
truth on the scene is not written.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from coperceive.errors import TimeQueryError
from coperceive.model import Annotation, Scene
from coperceive.robustness.spec import PerturbationSpec
from coperceive.sample import AgentSample, CooperativeSample
from coperceive.se3 import SE3
from coperceive.transform import lookup_pose

PERTURBED_FORMAT = "coperceive-perturbed-inputs"
PERTURBED_VERSION = 1


def frame_rng(seed: int, scene_id: str, time_us: int) -> random.Random:
    """The generator for one scene time. The same inputs always rebuild it."""
    text = f"{seed}|{scene_id}|{time_us}".encode()
    digest = hashlib.blake2s(text, digest_size=8).digest()
    return random.Random(int.from_bytes(digest, "big"))


def standard_normal(rng: random.Random) -> float:
    """One standard normal from two ``rng.random`` draws (Box-Muller)."""
    u1 = max(rng.random(), 1e-12)
    u2 = rng.random()
    return math.sqrt(-2.0 * math.log(u1)) * math.cos(2.0 * math.pi * u2)


def source_time_us(times: Sequence[int], query: int, spec: PerturbationSpec) -> int | None:
    """The pose time to read, or None when the delay has no earlier sample."""
    ordered = tuple(sorted(times))
    if spec.latency_frames is None and spec.latency_ms is None:
        return query if query in ordered else None
    chosen: list[int] = []
    if spec.latency_frames is not None:
        eligible = [time_us for time_us in ordered if time_us <= query]
        if not eligible:
            return None
        index = len(eligible) - 1 - spec.latency_frames
        if index < 0:
            return None
        chosen.append(eligible[index])
    if spec.latency_ms is not None:
        limit = query - int(round(spec.latency_ms * 1000.0))
        earlier = [time_us for time_us in ordered if time_us <= limit]
        if not earlier:
            return None
        chosen.append(earlier[-1])
    return min(chosen) if chosen else None


@dataclass(frozen=True)
class AgentDecision:
    """What one agent contributes at one query time after the spec is applied."""

    agent_id: str
    present: bool
    message: bool
    source_time_us: int | None
    pose: SE3 | None

    def to_json(self) -> dict[str, object]:
        return {
            "agent_id": self.agent_id,
            "present": self.present,
            "message": self.message,
            "source_time_us": self.source_time_us,
            "pose": None if self.pose is None else _pose_json(self.pose),
        }


def decide(
    scene: Scene, time_us: int, spec: PerturbationSpec, ego_agent_id: str
) -> tuple[AgentDecision, ...]:
    """Decisions for every agent that has a pose at ``time_us``, ego first in scene order."""
    rng = frame_rng(spec.seed, scene.scene_id, time_us)
    available = [
        agent.agent_id for agent in scene.agents if _has_pose(scene, agent.agent_id, time_us)
    ]
    if ego_agent_id not in available:
        raise KeyError(ego_agent_id)
    dropped: set[str] = set()
    for agent_id in available:
        if agent_id == ego_agent_id or spec.agent_dropout is None:
            continue
        if rng.random() < spec.agent_dropout:
            dropped.add(agent_id)
    survivors = [agent_id for agent_id in available if agent_id not in dropped]
    if spec.max_agents is not None:
        kept = [ego_agent_id]
        for agent_id in survivors:
            if agent_id == ego_agent_id:
                continue
            if len(kept) >= spec.max_agents:
                break
            kept.append(agent_id)
        survivors = kept
    survivor_set = set(survivors)
    timed: dict[str, int | None] = {}
    for agent_id in survivors:
        if agent_id == ego_agent_id:
            timed[agent_id] = time_us
            continue
        times = tuple(pose.time_us for pose in scene.agent(agent_id).poses)
        timed[agent_id] = source_time_us(times, time_us, spec)
    present = [agent_id for agent_id in survivors if timed.get(agent_id) is not None]
    lost_packet: set[str] = set()
    for agent_id in present:
        if agent_id == ego_agent_id or spec.packet_loss is None:
            continue
        if rng.random() < spec.packet_loss:
            lost_packet.add(agent_id)
    decisions: list[AgentDecision] = []
    for agent_id in available:
        if agent_id not in survivor_set or timed.get(agent_id) is None:
            decisions.append(AgentDecision(agent_id, False, False, None, None))
            continue
        source = timed[agent_id]
        assert source is not None
        if agent_id in lost_packet:
            decisions.append(AgentDecision(agent_id, True, False, source, None))
            continue
        pose = lookup_pose(scene.agent(agent_id), source, max_gap_us=0)
        if agent_id != ego_agent_id:
            pose = _noise(pose, spec, rng)
        decisions.append(AgentDecision(agent_id, True, True, source, pose))
    return tuple(decisions)


def perturb_sample(
    scene: Scene, sample: CooperativeSample, spec: PerturbationSpec, *, ego_agent_id: str
) -> CooperativeSample:
    """A new sample. ``scene`` is not modified, including its annotations."""
    decisions = {item.agent_id: item for item in decide(scene, sample.query_us, spec, ego_agent_id)}
    rows: list[AgentSample] = []
    for agent in scene.agents:
        decision = decisions.get(agent.agent_id)
        if decision is None or not decision.present or decision.source_time_us is None:
            continue
        sensors = tuple(sensor for sensor in scene.sensors if sensor.agent_id == agent.agent_id)
        if not decision.message or decision.pose is None:
            rows.append(AgentSample(agent.agent_id, False, None, None, None, sensors, (), (), ()))
            continue
        source = decision.source_time_us
        rows.append(
            AgentSample(
                agent.agent_id,
                True,
                source,
                source - sample.query_us,
                decision.pose,
                sensors,
                _at(scene.annotations, agent.agent_id, source),
                tuple(
                    image
                    for image in scene.images
                    if image.agent_id == agent.agent_id and image.time_us == source
                ),
                tuple(
                    cloud
                    for cloud in scene.point_clouds
                    if cloud.agent_id == agent.agent_id and cloud.time_us == source
                ),
            )
        )
    return CooperativeSample(
        query_us=sample.query_us,
        tolerance_us=sample.tolerance_us,
        mode=sample.mode,
        agents=tuple(rows),
    )


def export_perturbed_inputs(
    scene: Scene,
    spec: PerturbationSpec,
    dest: Path,
    *,
    ego_agent_id: str | None = None,
) -> None:
    """Write ``perturbation.json`` and ``frames.json`` for an external script.

    ``ground_truth`` is a copy of the scene annotations. It is not perturbed.
    """
    ego = ego_agent_id or scene.agents[0].agent_id
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "perturbation.json").write_text(
        json.dumps(spec.to_json(), indent=2) + "\n", encoding="utf-8"
    )
    frames: list[dict[str, object]] = []
    for time_us in scene.pose_times(ego):
        if not _has_pose(scene, ego, time_us):
            continue
        decisions = decide(scene, time_us, spec, ego)
        frames.append(
            {"time_us": time_us, "agents": [decision.to_json() for decision in decisions]}
        )
    document = {
        "format": PERTURBED_FORMAT,
        "format_version": PERTURBED_VERSION,
        "scene_id": scene.scene_id,
        "ego_agent_id": ego,
        "perturbation": spec.to_json(),
        "ground_truth": [_annotation_json(ann) for ann in scene.annotations],
        "frames": frames,
    }
    (dest / "frames.json").write_text(
        json.dumps(document, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )


def _noise(pose: SE3, spec: PerturbationSpec, rng: random.Random) -> SE3:
    dx = dy = yaw = 0.0
    if spec.pose_noise_xy is not None:
        dx = standard_normal(rng) * spec.pose_noise_xy
        dy = standard_normal(rng) * spec.pose_noise_xy
    if spec.pose_noise_yaw is not None:
        yaw = standard_normal(rng) * spec.pose_noise_yaw
    if dx == 0.0 and dy == 0.0 and yaw == 0.0:
        return pose
    rotated = SE3.from_rpy_deg(0.0, 0.0, yaw).compose(
        SE3(pose.qw, pose.qx, pose.qy, pose.qz, 0.0, 0.0, 0.0)
    )
    return rotated.translation(pose.tx + dx, pose.ty + dy, pose.tz)


def _has_pose(scene: Scene, agent_id: str, time_us: int) -> bool:
    try:
        lookup_pose(scene.agent(agent_id), time_us, max_gap_us=0)
    except TimeQueryError:
        return False
    return True


def _at(annotations: tuple[Annotation, ...], agent_id: str, time_us: int) -> tuple[Annotation, ...]:
    return tuple(ann for ann in annotations if ann.agent_id == agent_id and ann.time_us == time_us)


def _pose_json(pose: SE3) -> dict[str, float]:
    return {
        "qw": pose.qw,
        "qx": pose.qx,
        "qy": pose.qy,
        "qz": pose.qz,
        "tx": pose.tx,
        "ty": pose.ty,
        "tz": pose.tz,
    }


def _annotation_json(ann: Annotation) -> dict[str, object]:
    return {
        "agent_id": ann.agent_id,
        "time_us": ann.time_us,
        "track_id": ann.track_id,
        "category": ann.category,
        "center": list(ann.center),
        "size_lwh": list(ann.size_lwh),
        "frame": ann.frame.value,
    }
