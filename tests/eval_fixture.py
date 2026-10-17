# SPDX-License-Identifier: Apache-2.0
"""Synthetic predictions for ``tests/cooperative.py::cooperative_scene()``.

The numbers below are the fixture contract (plan, Task 3):

* ``single`` on ``vehicle`` and ``uav``: every ground-truth box of that agent at
  every time, same category, centre ``+0.1`` m along world x, score ``0.9``,
  except track ``99`` (left out: a miss) and track ``5`` on the vehicle
  (predicted as ``truck``: a wrong class).
* One extra ``single`` car on the vehicle at body ``(50, 50, 0)``, size
  ``(4, 2, 1.5)``, score ``0.8``, at every vehicle pose time (a false positive).
* ``fused`` on the vehicle: every vehicle ground-truth box at ``+0.1`` m, score
  ``0.95`` (recovers ``99``, still mislabels ``5``).

Every prediction is in the ``body`` frame of its agent.
"""

from __future__ import annotations

from coperceive.evaluation.predictions import PredictionBox, RunMeta
from coperceive.model import Annotation, BoxFrame, Scene
from coperceive.se3 import SE3
from coperceive.transform import lookup_pose

SHIFT_X_M = 0.1
SINGLE_SCORE = 0.9
FUSED_SCORE = 0.95
FP_SCORE = 0.8
FP_CENTER = (50.0, 50.0, 0.0)
FP_SIZE = (4.0, 2.0, 1.5)


def synthetic_run_meta() -> RunMeta:
    return RunMeta(
        run_id="synthetic",
        model="fixture",
        score_note="constant",
        categories=("car", "truck", "pedestrian", "sign", "pole"),
        sources=(("single", "own sensors"), ("fused", "cooperative")),
        created_utc="2026-10-17T00:00:00Z",
    )


def synthetic_predictions(scene: Scene) -> tuple[RunMeta, tuple[PredictionBox, ...]]:
    rows: list[PredictionBox] = []
    for ann in scene.annotations:
        if ann.agent_id not in {"vehicle", "uav"}:
            continue
        if ann.track_id == "99":
            continue
        rows.append(_shifted(scene, ann, "single", SINGLE_SCORE))
    for time_us in scene.pose_times("vehicle"):
        rows.append(
            PredictionBox(
                scene_id=scene.scene_id,
                agent_id="vehicle",
                source="single",
                time_us=time_us,
                category="car",
                score=FP_SCORE,
                track_id=None,
                center=FP_CENTER,
                size_lwh=FP_SIZE,
                box_from_rpy=SE3.identity(),
                frame=BoxFrame.BODY,
            )
        )
    for ann in scene.annotations:
        if ann.agent_id != "vehicle":
            continue
        rows.append(_shifted(scene, ann, "fused", FUSED_SCORE))
    return synthetic_run_meta(), tuple(rows)


def _shifted(scene: Scene, ann: Annotation, source: str, score: float) -> PredictionBox:
    """``ann`` moved ``SHIFT_X_M`` along world x, expressed in the agent body frame."""
    world_from_body = lookup_pose(scene.agent(ann.agent_id), ann.time_us, max_gap_us=0)
    placed = ann.box_from_rpy.translation(*ann.center)
    world_from_box = world_from_body.compose(placed) if ann.frame is BoxFrame.BODY else placed
    shifted = world_from_box.translation(
        world_from_box.tx + SHIFT_X_M, world_from_box.ty, world_from_box.tz
    )
    body_from_box = world_from_body.inverse().compose(shifted)
    category = "truck" if ann.agent_id == "vehicle" and ann.track_id == "5" else ann.category
    return PredictionBox(
        scene_id=scene.scene_id,
        agent_id=ann.agent_id,
        source=source,
        time_us=ann.time_us,
        category=category,
        score=score,
        track_id=ann.track_id,
        center=(body_from_box.tx, body_from_box.ty, body_from_box.tz),
        size_lwh=ann.size_lwh,
        box_from_rpy=SE3(
            body_from_box.qw, body_from_box.qx, body_from_box.qy, body_from_box.qz, 0.0, 0.0, 0.0
        ),
        frame=BoxFrame.BODY,
    )
