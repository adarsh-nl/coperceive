# SPDX-License-Identifier: Apache-2.0
"""Rewrites of a :class:`~tests.evalcheck.scenario.RunSpec` that a metric must survive.

Each function returns a new run that describes the same detection problem in
another way (rows reordered, the world moved rigidly) or a deliberately related
one (a false positive added below every score, the predictions replaced by the
ground truth). The property tests state what the evaluator must then report.

Like the generator, this module computes no metric and imports neither evaluator.
"""

from __future__ import annotations

import math
import random
from dataclasses import replace

from tests.evalcheck.generate import quat_from_rpy, quat_mul, quat_rotate, quat_unit
from tests.evalcheck.scenario import (
    FRAME_WORLD,
    BoxSpec,
    GtSpec,
    PredSpec,
    RunSpec,
    SceneSpec,
    Vec3,
)


def permute_predictions(run: RunSpec, seed: int) -> RunSpec:
    """The same predictions with the rows of every scene in another order."""
    rng = random.Random(seed)
    scenes: list[SceneSpec] = []
    for scene in run.scenes:
        rows = list(scene.predictions)
        rng.shuffle(rows)
        scenes.append(replace(scene, predictions=tuple(rows)))
    return replace(run, scenes=tuple(scenes))


def permute_ground_truth(run: RunSpec, seed: int) -> RunSpec:
    """The same annotations with the rows of every scene in another order."""
    rng = random.Random(seed)
    scenes: list[SceneSpec] = []
    for scene in run.scenes:
        rows = list(scene.ground_truth)
        rng.shuffle(rows)
        scenes.append(replace(scene, ground_truth=tuple(rows)))
    return replace(run, scenes=tuple(scenes))


def has_score_ties(run: RunSpec) -> bool:
    """True when two prediction rows of the run carry the same score."""
    scores = [pred.score for scene in run.scenes for pred in scene.predictions]
    return len(scores) != len(set(scores))


def has_duplicate_ground_truth(run: RunSpec) -> bool:
    """True when two annotation rows of one agent and time are the same box."""
    for scene in run.scenes:
        seen: set[tuple[str, int, str, BoxSpec]] = set()
        for gt in scene.ground_truth:
            key = (gt.agent_id, gt.time_us, gt.category, gt.box)
            if key in seen:
                return True
            seen.add(key)
    return False


def add_low_false_positives(run: RunSpec) -> RunSpec:
    """Every output of every scene gains one prediction that can match nothing.

    The new row scores below every prediction of the run, sits 10 km beyond the
    farthest box of its scene (in the world frame, at the first pose time of the
    agent) and carries the category of the output's first row.
    """
    scores = [pred.score for scene in run.scenes for pred in scene.predictions]
    if not scores:
        return run
    low = min(scores) - 1.0
    scenes: list[SceneSpec] = []
    for scene in run.scenes:
        reach = 0.0
        for agent in scene.agents:
            for pose in agent.poses:
                reach = max(reach, abs(pose.translation[0]), abs(pose.translation[1]))
        for box in [gt.box for gt in scene.ground_truth] + [p.box for p in scene.predictions]:
            # A body-frame centre is at most its norm away from the agent.
            reach = max(reach, abs(box.center[0]) + abs(box.center[1]) + abs(box.center[2]))
        far = 2.0 * reach + 10_000.0
        extra: list[PredSpec] = []
        first_rows: dict[tuple[str, str], PredSpec] = {}
        for pred in scene.predictions:
            first_rows.setdefault((pred.agent_id, pred.source), pred)
        agents = {agent.agent_id: agent for agent in scene.agents}
        for (agent_id, source), first in first_rows.items():
            extra.append(
                PredSpec(
                    agent_id=agent_id,
                    source=source,
                    time_us=agents[agent_id].poses[0].time_us,
                    category=first.category,
                    score=low,
                    box=BoxSpec(
                        center=(far, far, 0.0),
                        size_lwh=(1.0, 1.0, 1.0),
                        rotation=(1.0, 0.0, 0.0, 0.0),
                        frame=FRAME_WORLD,
                    ),
                )
            )
        scenes.append(replace(scene, predictions=(*scene.predictions, *extra)))
    return replace(run, scenes=tuple(scenes))


def perfect_predictions(run: RunSpec, seed: int) -> RunSpec:
    """Every output predicts exactly its agent's annotations, with arbitrary scores.

    The category is the annotation's category after ``gt_class_map``. Outputs keep
    their order of first appearance; a scene without predictions stays without.
    """
    rng = random.Random(seed)
    rename = run.config.gt_class_map
    scenes: list[SceneSpec] = []
    for scene in run.scenes:
        outputs = list(dict.fromkeys((pred.agent_id, pred.source) for pred in scene.predictions))
        rows: list[PredSpec] = []
        for agent_id, source in outputs:
            mine = [gt for gt in scene.ground_truth if gt.agent_id == agent_id]
            if not mine:
                # Keep the output in the scene: one row that can match nothing is not
                # perfect, so reuse one of its own rows only when it has no ground truth.
                rows.append(
                    next(
                        pred
                        for pred in scene.predictions
                        if (pred.agent_id, pred.source) == (agent_id, source)
                    )
                )
                continue
            for gt in mine:
                rows.append(
                    PredSpec(
                        agent_id=agent_id,
                        source=source,
                        time_us=gt.time_us,
                        category=rename.get(gt.category, gt.category),
                        score=rng.choice((0.2, 0.5, rng.random())),
                        box=gt.box,
                    )
                )
        scenes.append(replace(scene, predictions=tuple(rows)))
    return replace(run, scenes=tuple(scenes))


def rigid_transform(run: RunSpec, yaw: float, translation: Vec3) -> RunSpec:
    """The whole world of every scene turned by ``yaw`` about world z, then moved.

    Poses and world-frame boxes are transformed; body-frame boxes are relative to
    their agent and stay as they are.
    """
    turn = quat_from_rpy(0.0, 0.0, yaw)

    def point(p: Vec3) -> Vec3:
        x, y, z = quat_rotate(turn, p)
        return (x + translation[0], y + translation[1], z + translation[2])

    def box(spec: BoxSpec) -> BoxSpec:
        if spec.frame != FRAME_WORLD:
            return spec
        return replace(
            spec, center=point(spec.center), rotation=quat_unit(quat_mul(turn, spec.rotation))
        )

    scenes: list[SceneSpec] = []
    for scene in run.scenes:
        agents = tuple(
            replace(
                agent,
                poses=tuple(
                    replace(
                        pose,
                        rotation=quat_unit(quat_mul(turn, pose.rotation)),
                        translation=point(pose.translation),
                    )
                    for pose in agent.poses
                ),
            )
            for agent in scene.agents
        )
        ground_truth: tuple[GtSpec, ...] = tuple(
            replace(gt, box=box(gt.box)) for gt in scene.ground_truth
        )
        predictions: tuple[PredSpec, ...] = tuple(
            replace(pred, box=box(pred.box)) for pred in scene.predictions
        )
        scenes.append(
            replace(scene, agents=agents, ground_truth=ground_truth, predictions=predictions)
        )
    return replace(run, scenes=tuple(scenes))


def scale_lengths(run: RunSpec, factor: float) -> RunSpec:
    """Every length of the run multiplied by ``factor`` (positions, sizes, range limit).

    Thresholds are left alone, so this preserves an ``iou_bev`` evaluation and not a
    ``centre`` one.
    """

    def vec(v: Vec3) -> Vec3:
        return (v[0] * factor, v[1] * factor, v[2] * factor)

    def box(spec: BoxSpec) -> BoxSpec:
        return replace(spec, center=vec(spec.center), size_lwh=vec(spec.size_lwh))

    scenes = tuple(
        replace(
            scene,
            agents=tuple(
                replace(
                    agent,
                    poses=tuple(
                        replace(pose, translation=vec(pose.translation)) for pose in agent.poses
                    ),
                )
                for agent in scene.agents
            ),
            ground_truth=tuple(replace(gt, box=box(gt.box)) for gt in scene.ground_truth),
            predictions=tuple(replace(pred, box=box(pred.box)) for pred in scene.predictions),
        )
        for scene in run.scenes
    )
    config = run.config
    if config.max_range_m is not None:
        config = replace(config, max_range_m=config.max_range_m * factor)
    return replace(run, config=config, scenes=scenes)


def right_angle(quarter_turns: int) -> float:
    """``quarter_turns`` × 90° in radians."""
    return quarter_turns * math.pi / 2.0


def break_score_ties(run: RunSpec) -> RunSpec:
    """The same run with every score made distinct, later rows of a tie slightly lower.

    The k-th row (in scene order, then row order) that repeats a score has
    ``k * 1e-7`` taken off, repeated until no two rows of the run share a score.
    """
    used: set[float] = set()
    scenes: list[SceneSpec] = []
    for scene in sorted(run.scenes, key=lambda item: item.scene_id):
        rows: list[PredSpec] = []
        for pred in scene.predictions:
            score = pred.score
            while score in used:
                score -= 1e-7
            used.add(score)
            rows.append(replace(pred, score=score))
        scenes.append(replace(scene, predictions=tuple(rows)))
    return replace(run, scenes=tuple(scenes))
