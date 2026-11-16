# SPDX-License-Identifier: Apache-2.0
"""The product evaluator against the independent reference (``docs/eval-spec.md``, ADR 0016).

The same generated problem goes through ``coperceive.evaluation`` and through
``tests/reference_eval``, which was written from Part A of the spec alone and
shares no code with the product. They must agree: statuses, counts and pairings
exactly, every float within 1e-6. A difference means the product, the spec or
the reference is wrong, and the failure names the seed that shows it.

The seeds are fixed, so a red run is a regression and never bad luck.
"""

from __future__ import annotations

import math
import random
from collections import Counter
from pathlib import Path

import pytest
from coperceive.evaluation.evaluate import world_box
from coperceive.evaluation.geometry import (
    WorldBox,
    bev_iou,
    centre_distance_m,
    horizontal_range_m,
    yaw_error_deg,
)
from coperceive.model import BoxFrame
from coperceive.se3 import SE3
from coperceive.validate import yaw_deg

from tests import reference_eval
from tests.evalcheck.compare import TOLERANCE, diff_runs, diff_scenes
from tests.evalcheck.generate import (
    PROFILES,
    Profile,
    quat_from_rpy,
    random_run,
    to_body,
)
from tests.evalcheck.product import evaluate_in_memory, evaluate_on_disk, product_scene
from tests.evalcheck.scenario import (
    CRITERION_CENTRE,
    FRAME_BODY,
    FRAME_WORLD,
    OTHER_KIND_COMPUTED,
    OTHER_KIND_SHARED_TRACK,
    STATUS_BELOW_THRESHOLD,
    STATUS_FP,
    STATUS_IGNORED,
    STATUS_MISS,
    STATUS_TP,
    AgentSpec,
    BoxSpec,
    PoseSample,
    Quat,
    RunSpec,
    SceneSpec,
    Vec3,
)

# Per profile: seeds evaluated in memory (per-scene results) and on disk (headline rows too).
SEEDS_IN_MEMORY = range(30)
SEEDS_ON_DISK = range(4)
GEOMETRY_PAIRS = 3000
# World offsets of the geometry checks: a simulator town, 300 km out, a projected map frame.
_OFFSETS: tuple[tuple[float, float], ...] = ((0.0, 0.0), (3.0e5, -3.0e5), (4.0e5, 4.0e6))


def _closest_margin(run: RunSpec) -> float:
    """The smallest ``|value - threshold|`` over the pairs a matcher could judge.

    Diagnostic only, computed with the reference geometry: when two evaluators
    disagree on a status, this says whether a pair sits on a threshold.
    """
    config = run.config
    best = math.inf
    for scene in run.scenes:
        poses = {
            (agent.agent_id, pose.time_us): pose for agent in scene.agents for pose in agent.poses
        }
        for pred in scene.predictions:
            pose = poses[(pred.agent_id, pred.time_us)]
            placed = reference_eval.place_box(pred.box, pose)
            for gt in scene.ground_truth:
                if (gt.agent_id, gt.time_us) != (pred.agent_id, pred.time_us):
                    continue
                if config.gt_class_map.get(gt.category, gt.category) != pred.category:
                    continue
                other = reference_eval.place_box(gt.box, pose)
                if config.criterion == CRITERION_CENTRE:
                    value = reference_eval.centre_distance_m(placed, other)
                else:
                    value = reference_eval.bev_iou(placed, other)
                thresholds = config.class_thresholds.get(pred.category, config.thresholds)
                best = min(best, *(abs(value - threshold) for threshold in thresholds))
    return best


def _report(seed: int, run: RunSpec, differences: list[str]) -> str:
    margin = _closest_margin(run)
    return (
        f"seed {seed}: {len(differences)} differences "
        f"(closest pair is {margin:.3e} from a threshold); first: {differences[0]}"
    )


@pytest.mark.parametrize("profile", PROFILES, ids=lambda profile: profile.name)
def test_product_and_reference_agree_on_every_scene(profile: Profile) -> None:
    failures: list[str] = []
    for seed in SEEDS_IN_MEMORY:
        run = random_run(seed, profile)
        differences = diff_scenes(evaluate_in_memory(run), reference_eval.evaluate(run).scenes)
        if differences:
            failures.append(_report(seed, run, differences))
    assert not failures, "\n".join(failures[:8])


@pytest.mark.parametrize("profile", PROFILES, ids=lambda profile: profile.name)
def test_product_and_reference_agree_on_the_files_and_the_headline_rows(
    profile: Profile, tmp_path: Path
) -> None:
    """The same through ``eval run`` and ``eval report``: files written, read back, pooled."""
    failures: list[str] = []
    for seed in SEEDS_ON_DISK:
        run = random_run(seed, profile)
        product = evaluate_on_disk(run, tmp_path / f"seed-{seed}")
        differences = diff_runs(product, reference_eval.evaluate(run))
        if differences:
            failures.append(_report(seed, run, differences))
    assert not failures, "\n".join(failures[:8])


def test_the_generated_runs_reach_every_rule() -> None:
    """The agreement above is about something: every status, count and branch occurs."""
    seen: Counter[str] = Counter()
    for profile in PROFILES:
        for seed in range(20):
            run = random_run(seed, profile)
            seen[f"criterion {run.config.criterion}"] += 1
            if run.config.max_range_m is not None:
                seen["max_range_m set"] += 1
            if run.config.ignore_gt_visibility_below is not None:
                seen["visibility rule set"] += 1
            if run.config.class_thresholds:
                seen["class_thresholds set"] += 1
            if run.config.gt_class_map:
                seen["gt_class_map set"] += 1
            if len(run.scenes) > 1:
                seen["several scenes"] += 1
            for scene in run.scenes:
                for box in [g.box for g in scene.ground_truth] + [p.box for p in scene.predictions]:
                    seen[f"frame {box.frame}"] += 1
            for scene in reference_eval.evaluate(run).scenes:
                for output in scene.outputs:
                    for status in output.pred_status.values():
                        seen[f"prediction {status}"] += 1
                    for status in output.gt_status.values():
                        seen[f"gt {status}"] += 1
                    for cooperative in output.gt_cooperative.values():
                        seen[f"other_kind {cooperative.other_kind}"] += 1
                        seen[f"other_agent_tp {cooperative.other_agent_tp}"] += 1
                    seen["recovered"] += output.cooperative.recovered
                    seen["lost_by_fusion"] += output.cooperative.lost_by_fusion
                    seen["only_other_agent"] += output.cooperative.only_other_agent
                    seen["nobody"] += output.cooperative.nobody
                    seen["samples"] += len(output.samples)
                    seen["empty samples"] += sum(
                        1 for s in output.samples if s.tp + s.fp + s.miss + s.ignored_gt == 0
                    )
                    for table in output.ap:
                        for value in table.per_category.values():
                            seen["ap null" if value is None else "ap number"] += 1
    wanted = (
        "criterion iou_bev",
        f"criterion {CRITERION_CENTRE}",
        "max_range_m set",
        "visibility rule set",
        "class_thresholds set",
        "gt_class_map set",
        "several scenes",
        f"frame {FRAME_BODY}",
        f"frame {FRAME_WORLD}",
        f"prediction {STATUS_TP}",
        f"prediction {STATUS_FP}",
        f"prediction {STATUS_IGNORED}",
        f"prediction {STATUS_BELOW_THRESHOLD}",
        f"gt {STATUS_TP}",
        f"gt {STATUS_MISS}",
        f"gt {STATUS_IGNORED}",
        f"other_kind {OTHER_KIND_SHARED_TRACK}",
        f"other_kind {OTHER_KIND_COMPUTED}",
        "other_kind None",
        "other_agent_tp True",
        "other_agent_tp False",
        "other_agent_tp None",
        "recovered",
        "lost_by_fusion",
        "only_other_agent",
        "nobody",
        "empty samples",
        "ap null",
        "ap number",
    )
    missing = [name for name in wanted if seen[name] < 20]
    assert not missing, f"the generator no longer reaches: {missing} (seen: {dict(seen)})"


# --- geometry, pair by pair --------------------------------------------------------


def _heading(rng: random.Random) -> float:
    kind = rng.random()
    if kind < 0.25:
        return rng.choice((math.pi, -math.pi)) - rng.choice((0.0, 1e-12, 1e-9, 1e-6)) * rng.choice(
            (1.0, -1.0)
        )
    if kind < 0.4:
        return rng.choice((0.0, math.pi / 2.0, -math.pi / 2.0))
    return rng.uniform(-math.pi, math.pi)


def _quat(rng: random.Random, *, tilt: bool) -> Quat:
    roll = rng.uniform(-0.4, 0.4) if tilt else 0.0
    pitch = rng.uniform(-0.4, 0.4) if tilt else 0.0
    return quat_from_rpy(roll, pitch, _heading(rng))


def _pair(rng: random.Random, offset: tuple[float, float]) -> tuple[BoxSpec, BoxSpec]:
    """Two world-frame boxes: heavy overlap, an exact copy, or an unrelated box."""
    scale = rng.choice((1.0, 1.0, 1.0, 1e-2, 50.0))
    size: Vec3 = (
        rng.uniform(0.5, 9.0) * scale,
        rng.uniform(0.4, 3.0) * scale,
        rng.uniform(0.5, 3.0) * scale,
    )
    center: Vec3 = (
        offset[0] + rng.uniform(-40.0, 40.0) * scale,
        offset[1] + rng.uniform(-40.0, 40.0) * scale,
        rng.uniform(-2.0, 2.0),
    )
    tilt = rng.random() < 0.3
    first = BoxSpec(center, size, _quat(rng, tilt=tilt), FRAME_WORLD)
    kind = rng.random()
    if kind < 0.1:
        return first, first
    if kind < 0.65:
        second = BoxSpec(
            (
                center[0] + rng.uniform(-1.5, 1.5) * scale,
                center[1] + rng.uniform(-1.5, 1.5) * scale,
                center[2] + rng.uniform(-1.0, 1.0),
            ),
            (
                size[0] * rng.uniform(0.8, 1.25),
                size[1] * rng.uniform(0.8, 1.25),
                size[2] * rng.uniform(0.8, 1.25),
            ),
            _quat(rng, tilt=tilt),
            FRAME_WORLD,
        )
        return first, second
    second = BoxSpec(
        (
            center[0] + rng.uniform(-8.0, 8.0) * scale,
            center[1] + rng.uniform(-8.0, 8.0) * scale,
            center[2],
        ),
        (
            rng.uniform(0.5, 9.0) * scale,
            rng.uniform(0.4, 3.0) * scale,
            rng.uniform(0.5, 3.0) * scale,
        ),
        _quat(rng, tilt=tilt),
        FRAME_WORLD,
    )
    return first, second


def _product_box(box: BoxSpec) -> WorldBox:
    q = box.rotation
    return WorldBox(box.center, box.size_lwh, SE3(q[0], q[1], q[2], q[3], 0.0, 0.0, 0.0))


@pytest.mark.parametrize("offset", _OFFSETS, ids=("town", "300km", "map-frame"))
def test_pair_geometry_matches_the_reference(offset: tuple[float, float]) -> None:
    """BEV IoU against an exact polygon overlay, and the two pair errors, on 3000 pairs.

    The IoU must also be symmetric and inside ``[0, 1]``, and 1 for a box with itself.
    """
    rng = random.Random(f"pairs:{offset}")
    worst_iou = worst_asymmetry = 0.0
    overlapping = 0
    for _ in range(GEOMETRY_PAIRS):
        first, second = _pair(rng, offset)
        a, b = _product_box(first), _product_box(second)
        ra = reference_eval.place_box(first, None)
        rb = reference_eval.place_box(second, None)
        iou = bev_iou(a, b)
        assert 0.0 <= iou <= 1.0
        if first is second:
            assert iou == pytest.approx(1.0, abs=1e-12)
        expected = reference_eval.bev_iou(ra, rb)
        worst_iou = max(worst_iou, abs(iou - expected))
        worst_asymmetry = max(worst_asymmetry, abs(iou - bev_iou(b, a)))
        overlapping += iou > 0.0
        assert centre_distance_m(a, b) == pytest.approx(
            reference_eval.centre_distance_m(ra, rb), abs=1e-9, rel=1e-12
        )
        assert yaw_error_deg(a, b) == pytest.approx(reference_eval.yaw_error_deg(ra, rb), abs=1e-7)
    assert overlapping > GEOMETRY_PAIRS // 2
    assert worst_iou <= 1e-9, f"largest |product - reference| IoU: {worst_iou:.3e}"
    assert worst_asymmetry <= 1e-12, f"largest |iou(a, b) - iou(b, a)|: {worst_asymmetry:.3e}"


def test_placing_a_body_box_matches_the_reference() -> None:
    """World centre, heading and range of body-frame boxes under tilted, turned poses."""
    rng = random.Random("placing")
    for _ in range(1500):
        offset = rng.choice(_OFFSETS)
        pose = PoseSample(
            time_us=0,
            rotation=_quat(rng, tilt=rng.random() < 0.5),
            translation=(
                offset[0] + rng.uniform(-200.0, 200.0),
                offset[1] + rng.uniform(-200.0, 200.0),
                rng.uniform(-5.0, 60.0),
            ),
        )
        world_center: Vec3 = (
            pose.translation[0] + rng.uniform(-80.0, 80.0),
            pose.translation[1] + rng.uniform(-80.0, 80.0),
            rng.uniform(-3.0, 3.0),
        )
        body_center, body_rotation = to_body(pose, world_center, _quat(rng, tilt=True))
        box = BoxSpec(body_center, (4.0, 2.0, 1.5), body_rotation, FRAME_BODY)
        scene = SceneSpec("s", (AgentSpec("a", (pose,)),), (), ())
        q = box.rotation
        product = world_box(
            product_scene(scene),
            "a",
            0,
            box.center,
            box.size_lwh,
            SE3(q[0], q[1], q[2], q[3], 0.0, 0.0, 0.0),
            BoxFrame.BODY,
        )
        reference = reference_eval.place_box(box, pose)
        for got, want in zip(product.center, reference.center, strict=True):
            assert got == pytest.approx(want, abs=1e-8)
        turn = math.radians(yaw_deg(product.rotation)) - reference_eval.yaw_rad(reference.rotation)
        assert abs(math.remainder(turn, 2.0 * math.pi)) <= 1e-9
        assert horizontal_range_m(product, pose.translation) == pytest.approx(
            reference_eval.horizontal_range_m(reference, pose.translation), abs=1e-8
        )


def test_tolerance_is_the_one_the_spec_states() -> None:
    assert TOLERANCE == 1e-6
