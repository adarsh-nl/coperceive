# SPDX-License-Identifier: Apache-2.0
"""Seeded random evaluation problems (``docs/adr/0016-evaluator-verification.md`` §4).

:func:`random_run` turns ``(seed, profile)`` into a
:class:`~tests.evalcheck.scenario.RunSpec`. The same pair always gives the same
run, on every platform: only :class:`random.Random` and :mod:`math` are used.

A world is drawn first (objects with a centre, a size, a heading and a class),
then agents that each see a subset of it, then predictions derived from what an
agent sees plus false positives. The profiles push on the cases a metric gets
wrong quietly: headings at ±π, heavy overlap, duplicate rows, equal scores,
empty samples, boxes exactly on the range limit, tiny and huge boxes, worlds far
from the origin, tilted agents, body- and world-frame rows.

Nothing here knows how a metric is computed, and nothing is imported from
``coperceive`` or from the reference evaluator.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, replace

from tests.evalcheck.scenario import (
    CRITERION_CENTRE,
    CRITERION_IOU_BEV,
    FRAME_BODY,
    FRAME_WORLD,
    SOURCE_FUSED,
    SOURCE_SINGLE,
    AgentSpec,
    BoxSpec,
    ConfigSpec,
    GtSpec,
    LidarSpec,
    PoseSample,
    PredSpec,
    Quat,
    RunSpec,
    SceneSpec,
    Vec3,
)

CLASSES = ("car", "truck", "pedestrian", "cyclist")
# (length, width, height) a class is drawn around.
_CLASS_SIZE: dict[str, Vec3] = {
    "car": (4.5, 1.9, 1.6),
    "truck": (8.0, 2.5, 3.2),
    "pedestrian": (0.6, 0.6, 1.75),
    "cyclist": (1.8, 0.7, 1.7),
}
_NEAR_PI_OFFSETS = (0.0, 1e-12, 1e-9, 1e-6, 1e-3)
_SAMPLE_STEP_US = 100_000


@dataclass(frozen=True)
class Profile:
    """Knobs of :func:`random_run`. Probabilities are per draw."""

    name: str
    scenes: tuple[int, int] = (1, 2)
    agents: tuple[int, int] = (1, 3)
    samples: tuple[int, int] = (1, 3)
    objects: tuple[int, int] = (0, 12)
    extent_m: float = 60.0  # objects are drawn in a square of this half-side
    world_offset_m: float = 0.0  # added to every world coordinate
    p_seen: float = 0.7  # an agent annotates an object
    p_detected: float = 0.8  # an output predicts a box its agent annotates
    p_cluster: float = 0.15  # an object is placed on top of the previous one
    p_duplicate_pred: float = 0.1
    p_duplicate_gt: float = 0.03
    p_false_positive: float = 0.4  # per sample, an extra unrelated prediction
    p_wrong_class: float = 0.05
    p_empty_sample: float = 0.15  # an output has no prediction at a sample
    p_body_frame: float = 0.5
    p_near_pi: float = 0.2
    p_right_angle: float = 0.15
    p_tilt: float = 0.0  # an agent or a box gets roll and pitch
    p_visibility: float = 0.3  # a ground-truth row carries a visibility
    p_untracked: float = 0.2  # a ground-truth row has no track id
    score_mode: str = "mixed"  # continuous | discrete | constant | mixed
    centre_noise_m: tuple[float, ...] = (0.02, 0.3, 0.8, 2.5)
    size_scale: tuple[float, ...] = (1.0,)
    range_limit: bool = False  # max_range_m is set and some boxes sit exactly on it
    p_sweep: float = 0.75  # an agent has a LiDAR sweep at a pose time
    p_hit: float = 0.6  # a sweep holds points inside a box (its own or another agent's)
    p_fused_extra: float = 0.5  # a fused output also predicts a box its agent does not annotate


PROFILES: tuple[Profile, ...] = (
    Profile("default"),
    Profile("dense-overlap", objects=(6, 20), extent_m=8.0, p_cluster=0.5, p_duplicate_pred=0.3),
    Profile("score-ties", score_mode="discrete", p_duplicate_pred=0.3, objects=(2, 10)),
    Profile("constant-score", score_mode="constant", objects=(2, 10)),
    Profile("near-pi", p_near_pi=0.9, p_right_angle=0.05),
    Profile("empty", objects=(0, 3), p_empty_sample=0.6, p_seen=0.4, p_detected=0.5),
    Profile("range-limit", range_limit=True, p_body_frame=0.0, objects=(4, 12)),
    Profile("tiny-and-huge", size_scale=(1e-3, 1e-2, 1.0, 30.0, 300.0), extent_m=400.0),
    Profile("far-world", world_offset_m=3.0e5, objects=(2, 10)),
    Profile("tilted", p_tilt=0.6),
    Profile("many-agents", agents=(3, 4), scenes=(2, 3), p_seen=0.6),
)


def profile_named(name: str) -> Profile:
    """The profile called ``name``."""
    for profile in PROFILES:
        if profile.name == name:
            return profile
    raise KeyError(name)


# --- quaternions (inputs only; neither evaluator uses these) -----------------------


def quat_mul(a: Quat, b: Quat) -> Quat:
    aw, ax, ay, az = a
    bw, bx, by, bz = b
    return (
        aw * bw - ax * bx - ay * by - az * bz,
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
    )


def quat_conj(q: Quat) -> Quat:
    return (q[0], -q[1], -q[2], -q[3])


def quat_unit(q: Quat) -> Quat:
    norm = math.sqrt(q[0] * q[0] + q[1] * q[1] + q[2] * q[2] + q[3] * q[3])
    return (q[0] / norm, q[1] / norm, q[2] / norm, q[3] / norm)


def quat_rotate(q: Quat, v: Vec3) -> Vec3:
    _w, x, y, z = quat_mul(quat_mul(q, (0.0, v[0], v[1], v[2])), quat_conj(q))
    return (x, y, z)


def quat_from_rpy(roll: float, pitch: float, yaw: float) -> Quat:
    """``Rz(yaw) Ry(pitch) Rx(roll)`` as a unit quaternion, angles in radians."""
    qx: Quat = (math.cos(roll / 2.0), math.sin(roll / 2.0), 0.0, 0.0)
    qy: Quat = (math.cos(pitch / 2.0), 0.0, math.sin(pitch / 2.0), 0.0)
    qz: Quat = (math.cos(yaw / 2.0), 0.0, 0.0, math.sin(yaw / 2.0))
    return quat_unit(quat_mul(quat_mul(qz, qy), qx))


def to_body(pose: PoseSample, center: Vec3, rotation: Quat) -> tuple[Vec3, Quat]:
    """A world centre and rotation expressed in the body frame of ``pose``."""
    inverse = quat_conj(pose.rotation)
    offset = (
        center[0] - pose.translation[0],
        center[1] - pose.translation[1],
        center[2] - pose.translation[2],
    )
    return quat_rotate(inverse, offset), quat_unit(quat_mul(inverse, rotation))


# --- the world -----------------------------------------------------------------------


@dataclass(frozen=True)
class _Object:
    track_id: str
    category: str
    center: Vec3
    size_lwh: Vec3
    rotation: Quat


def _heading(rng: random.Random, profile: Profile) -> float:
    draw = rng.random()
    if draw < profile.p_near_pi:
        offset = rng.choice(_NEAR_PI_OFFSETS)
        return rng.choice((math.pi - offset, -math.pi + offset))
    if draw < profile.p_near_pi + profile.p_right_angle:
        return rng.choice((0.0, math.pi / 2.0, -math.pi / 2.0, math.pi, -math.pi))
    return rng.uniform(-math.pi, math.pi)


def _rotation(rng: random.Random, profile: Profile) -> Quat:
    roll = pitch = 0.0
    if rng.random() < profile.p_tilt:
        roll = rng.uniform(-0.3, 0.3)
        pitch = rng.uniform(-0.3, 0.3)
    return quat_from_rpy(roll, pitch, _heading(rng, profile))


def _size(rng: random.Random, profile: Profile, category: str) -> Vec3:
    scale = rng.choice(profile.size_scale)
    base = _CLASS_SIZE[category]
    return (
        base[0] * scale * rng.uniform(0.8, 1.25),
        base[1] * scale * rng.uniform(0.8, 1.25),
        base[2] * scale * rng.uniform(0.8, 1.25),
    )


def _objects(rng: random.Random, profile: Profile, classes: tuple[str, ...]) -> list[_Object]:
    objects: list[_Object] = []
    offset = profile.world_offset_m
    for index in range(rng.randint(*profile.objects)):
        category = rng.choice(classes)
        if objects and rng.random() < profile.p_cluster:
            anchor = objects[-1].center
            center = (
                anchor[0] + rng.uniform(-1.5, 1.5),
                anchor[1] + rng.uniform(-1.5, 1.5),
                anchor[2],
            )
        else:
            center = (
                offset + rng.uniform(-profile.extent_m, profile.extent_m),
                offset + rng.uniform(-profile.extent_m, profile.extent_m),
                rng.uniform(-1.0, 1.0),
            )
        objects.append(
            _Object(
                track_id=f"t{index}",
                category=category,
                center=center,
                size_lwh=_size(rng, profile, category),
                rotation=_rotation(rng, profile),
            )
        )
    return objects


def _agents(rng: random.Random, profile: Profile) -> tuple[AgentSpec, ...]:
    agents: list[AgentSpec] = []
    offset = profile.world_offset_m
    for index in range(rng.randint(*profile.agents)):
        start_us = rng.choice((0, 0, _SAMPLE_STEP_US))
        count = rng.randint(*profile.samples)
        if profile.range_limit:
            # Integer origins and no rotation: a box at an integer offset has an exact range.
            origin: Vec3 = (
                offset + float(rng.randint(-20, 20)),
                offset + float(rng.randint(-20, 20)),
                0.0,
            )
            velocity: Vec3 = (float(rng.randint(-2, 2)), 0.0, 0.0)
            rotation: Quat = (1.0, 0.0, 0.0, 0.0)
        else:
            origin = (
                offset + rng.uniform(-profile.extent_m, profile.extent_m),
                offset + rng.uniform(-profile.extent_m, profile.extent_m),
                rng.uniform(0.0, 2.0),
            )
            velocity = (rng.uniform(-1.5, 1.5), rng.uniform(-1.5, 1.5), 0.0)
            rotation = _rotation(rng, profile)
        poses = tuple(
            PoseSample(
                time_us=start_us + step * _SAMPLE_STEP_US,
                rotation=rotation,
                translation=(
                    origin[0] + velocity[0] * step,
                    origin[1] + velocity[1] * step,
                    origin[2],
                ),
            )
            for step in range(count)
        )
        agents.append(AgentSpec(agent_id=f"agent{index}", poses=poses))
    return tuple(agents)


def _box(
    rng: random.Random,
    profile: Profile,
    pose: PoseSample,
    center: Vec3,
    size_lwh: Vec3,
    rotation: Quat,
) -> BoxSpec:
    if rng.random() < profile.p_body_frame:
        body_center, body_rotation = to_body(pose, center, rotation)
        return BoxSpec(body_center, size_lwh, body_rotation, FRAME_BODY)
    return BoxSpec(center, size_lwh, rotation, FRAME_WORLD)


def _sweep(
    rng: random.Random,
    agent_id: str,
    pose: PoseSample,
    visible: list[_Object],
    objects: list[_Object],
) -> LidarSpec:
    """A sweep in the agent's body frame: points inside some boxes, plus strays.

    Most hits are on boxes the agent annotates; some fall inside other objects of the
    world (an object the agent does not list but its LiDAR touches), and some boxes the
    agent lists get no point at all. The union rule of spec A.4 tests exactly this.
    """
    points: list[Vec3] = []
    pool = list(visible) + [obj for obj in objects if rng.random() < 0.3]
    for obj in pool:
        if rng.random() >= 0.6:
            continue
        for _ in range(rng.randint(1, 3)):
            local: Vec3 = (
                rng.uniform(-0.45, 0.45) * obj.size_lwh[0],
                rng.uniform(-0.45, 0.45) * obj.size_lwh[1],
                rng.uniform(-0.45, 0.45) * obj.size_lwh[2],
            )
            offset = quat_rotate(obj.rotation, local)
            world = (
                obj.center[0] + offset[0],
                obj.center[1] + offset[1],
                obj.center[2] + offset[2],
            )
            body, _rotation = to_body(pose, world, (1.0, 0.0, 0.0, 0.0))
            points.append(body)
    for _ in range(rng.randint(0, 4)):
        points.append((rng.uniform(-80.0, 80.0), rng.uniform(-80.0, 80.0), rng.uniform(-2.0, 2.0)))
    return LidarSpec(agent_id=agent_id, time_us=pose.time_us, points=tuple(points))


def _score(rng: random.Random, mode: str) -> float:
    if mode == "constant":
        return 0.5
    if mode == "discrete":
        return rng.choice((0.1, 0.3, 0.5, 0.7, 0.9))
    return rng.uniform(0.01, 0.99)


def _shared_tracks(ground_truth: list[GtSpec]) -> tuple[tuple[str, str], ...]:
    """Ids that at least two agents use under exactly one category, in id order."""
    agents: dict[str, set[str]] = {}
    categories: dict[str, set[str]] = {}
    for gt in ground_truth:
        if gt.track_id is None:
            continue
        agents.setdefault(gt.track_id, set()).add(gt.agent_id)
        categories.setdefault(gt.track_id, set()).add(gt.category)
    return tuple(
        (track_id, next(iter(categories[track_id])))
        for track_id in sorted(agents)
        if len(agents[track_id]) >= 2 and len(categories[track_id]) == 1
    )


def _scene(
    rng: random.Random,
    profile: Profile,
    scene_id: str,
    classes: tuple[str, ...],
    score_mode: str,
    max_range_m: float | None,
) -> SceneSpec:
    agents = _agents(rng, profile)
    objects = _objects(rng, profile, classes)
    ground_truth: list[GtSpec] = []
    sweeps: list[LidarSpec] = []
    # (agent, time) -> the objects that agent annotates at that time, as world boxes.
    seen: dict[tuple[str, int], list[_Object]] = {}
    for agent in agents:
        for pose in agent.poses:
            visible: list[_Object] = []
            for obj in objects:
                if rng.random() >= profile.p_seen:
                    continue
                # Each agent's annotation of an object is its own measurement.
                jitter = rng.choice((0.0, 0.0, 0.05, 1.0))
                center = (
                    obj.center[0] + rng.uniform(-jitter, jitter),
                    obj.center[1] + rng.uniform(-jitter, jitter),
                    obj.center[2] + rng.uniform(-jitter, jitter),
                )
                visible.append(replace(obj, center=center))
            if max_range_m is not None and profile.range_limit and rng.random() < 0.7:
                # A box exactly on the limit, one just inside and one just outside.
                for delta in (0.0, -0.5, 0.5):
                    category = rng.choice(classes)
                    axis = rng.choice(((1.0, 0.0), (-1.0, 0.0), (0.0, 1.0), (0.0, -1.0)))
                    distance = max_range_m + delta
                    visible.append(
                        _Object(
                            track_id=f"r{len(ground_truth)}-{len(visible)}",
                            category=category,
                            center=(
                                pose.translation[0] + axis[0] * distance,
                                pose.translation[1] + axis[1] * distance,
                                0.0,
                            ),
                            size_lwh=_CLASS_SIZE[category],
                            rotation=(1.0, 0.0, 0.0, 0.0),
                        )
                    )
            seen[(agent.agent_id, pose.time_us)] = visible
            if rng.random() < profile.p_sweep:
                sweeps.append(_sweep(rng, agent.agent_id, pose, visible, objects))
            for obj in visible:
                row = GtSpec(
                    agent_id=agent.agent_id,
                    time_us=pose.time_us,
                    category=obj.category,
                    track_id=None if rng.random() < profile.p_untracked else obj.track_id,
                    visibility=rng.random() if rng.random() < profile.p_visibility else None,
                    box=_box(rng, profile, pose, obj.center, obj.size_lwh, obj.rotation),
                )
                ground_truth.append(row)
                if rng.random() < profile.p_duplicate_gt:
                    ground_truth.append(row)

    outputs: list[tuple[AgentSpec, str]] = []
    for agent in agents:
        kind = rng.random()
        if kind < 0.45:
            outputs += [(agent, SOURCE_SINGLE), (agent, SOURCE_FUSED)]
        elif kind < 0.65:
            outputs.append((agent, SOURCE_SINGLE))
        elif kind < 0.8:
            outputs.append((agent, SOURCE_FUSED))
        elif kind < 0.9:
            outputs.append((agent, "late"))
    rng.shuffle(outputs)

    predictions: list[PredSpec] = []
    for agent, source in outputs:
        for pose in agent.poses:
            if rng.random() < profile.p_empty_sample:
                continue
            targets = list(seen[(agent.agent_id, pose.time_us)])
            if source == SOURCE_FUSED and rng.random() < profile.p_fused_extra:
                for other in agents:
                    if other.agent_id != agent.agent_id:
                        targets += seen.get((other.agent_id, pose.time_us), [])
            for obj in targets:
                if rng.random() >= profile.p_detected:
                    continue
                noise = rng.choice(profile.centre_noise_m)
                center = (
                    obj.center[0] + rng.uniform(-noise, noise),
                    obj.center[1] + rng.uniform(-noise, noise),
                    obj.center[2] + rng.uniform(-0.2, 0.2),
                )
                size = (
                    obj.size_lwh[0] * rng.uniform(0.9, 1.1),
                    obj.size_lwh[1] * rng.uniform(0.9, 1.1),
                    obj.size_lwh[2] * rng.uniform(0.9, 1.1),
                )
                turn = rng.choice((0.0, 0.0, 0.0, math.pi, rng.uniform(-0.2, 0.2)))
                rotation = quat_unit(quat_mul(obj.rotation, quat_from_rpy(0.0, 0.0, turn)))
                category = obj.category
                if rng.random() < profile.p_wrong_class:
                    category = rng.choice(classes)
                row = PredSpec(
                    agent_id=agent.agent_id,
                    source=source,
                    time_us=pose.time_us,
                    category=category,
                    score=_score(rng, score_mode),
                    box=_box(rng, profile, pose, center, size, rotation),
                )
                predictions.append(row)
                if rng.random() < profile.p_duplicate_pred:
                    lower = rng.choice((row.score, row.score, row.score * 0.5))
                    predictions.append(replace(row, score=lower))
            if rng.random() < profile.p_false_positive:
                category = rng.choice(classes)
                center = (
                    pose.translation[0] + rng.uniform(-profile.extent_m, profile.extent_m),
                    pose.translation[1] + rng.uniform(-profile.extent_m, profile.extent_m),
                    0.0,
                )
                predictions.append(
                    PredSpec(
                        agent_id=agent.agent_id,
                        source=source,
                        time_us=pose.time_us,
                        category=category,
                        score=_score(rng, score_mode),
                        box=_box(
                            rng,
                            profile,
                            pose,
                            center,
                            _size(rng, profile, category),
                            _rotation(rng, profile),
                        ),
                    )
                )
    if rng.random() < 0.5:
        rng.shuffle(predictions)
    return SceneSpec(
        scene_id=scene_id,
        agents=agents,
        ground_truth=tuple(ground_truth),
        predictions=tuple(predictions),
        shared_tracks=_shared_tracks(ground_truth),
        lidar=tuple(sweeps),
    )


def _config(rng: random.Random, profile: Profile, classes: tuple[str, ...]) -> ConfigSpec:
    criterion = CRITERION_CENTRE if rng.random() < 0.2 else CRITERION_IOU_BEV
    if criterion == CRITERION_IOU_BEV:
        thresholds = rng.choice(((0.5, 0.7), (0.5, 0.7), (0.3, 0.5, 0.7), (0.7,), (0.25, 0.5)))
        special = tuple(max(0.05, value - 0.2) for value in thresholds)
    else:
        thresholds = rng.choice(((2.0, 1.0), (4.0, 2.0, 1.0, 0.5), (1.0,)))
        special = tuple(value * 0.5 for value in thresholds)
    class_thresholds: dict[str, tuple[float, ...]] = {}
    if "pedestrian" in classes and rng.random() < 0.3:
        class_thresholds["pedestrian"] = special
    gt_class_map: dict[str, str] = {}
    if "truck" in classes and "car" in classes and rng.random() < 0.3:
        gt_class_map["truck"] = "car"
    max_range_m: float | None = None
    if profile.range_limit:
        max_range_m = float(rng.choice((20, 30, 50)))
    elif rng.random() < 0.3:
        max_range_m = rng.uniform(15.0, profile.extent_m * 1.2)
    return ConfigSpec(
        criterion=criterion,
        thresholds=thresholds,
        display_threshold=rng.choice(thresholds),
        class_thresholds=class_thresholds,
        ignore_gt_visibility_below=rng.choice((None, None, 0.25, 0.5)),
        max_range_m=max_range_m,
        score_threshold=rng.choice((0.0, 0.3, 0.3, 0.5, 0.9)),
        gt_class_map=gt_class_map,
        ranking_scope=rng.choice(("per_frame", "per_frame", "pooled")),
        gt_scope=rng.choice(("connected_union", "connected_union", "ego_only")),
        comm_range_m=rng.choice((30.0, 70.0, 70.0, 200.0)),
    )


def random_run(seed: int, profile: Profile) -> RunSpec:
    """The run of ``(seed, profile)``. Deterministic."""
    rng = random.Random(f"{profile.name}:{seed}")
    classes = tuple(rng.sample(CLASSES, rng.randint(1, len(CLASSES))))
    declared = tuple(
        name for name in CLASSES if (name in classes and rng.random() < 0.8) or rng.random() < 0.1
    )
    config = _config(rng, profile, classes)
    score_mode = profile.score_mode
    if score_mode == "mixed":
        score_mode = rng.choice(("continuous", "continuous", "discrete"))
    scenes = tuple(
        _scene(rng, profile, f"scene-{index:02d}", classes, score_mode, config.max_range_m)
        for index in range(rng.randint(*profile.scenes))
    )
    if rng.random() < 0.5:
        scenes = tuple(reversed(scenes))
    return RunSpec(categories=declared, config=config, scenes=scenes)
