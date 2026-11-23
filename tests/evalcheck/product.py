# SPDX-License-Identifier: Apache-2.0
"""Run a :class:`~tests.evalcheck.scenario.RunSpec` through the product evaluator.

Two paths, both through public entry points only:

* :func:`evaluate_in_memory` calls ``evaluate_scene`` per scene and reads the
  result objects. Fast; no pooled rows.
* :func:`evaluate_on_disk` writes the scenes and the predictions, calls
  ``evaluate_run`` and reads ``metrics.json`` and ``matches.parquet`` back, then
  builds the headline rows with ``collect``, ``aggregate`` and ``pooled_ap``.
  This is what ``coperceive eval run`` and ``coperceive eval report`` do, files
  included.

Nothing here computes a metric: every number is copied out of what the product
returned or wrote.
"""

from __future__ import annotations

import struct
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import MappingProxyType
from typing import cast

from coperceive.evaluation.config import EvalConfig, threshold_key
from coperceive.evaluation.evaluate import (
    GT_AGENT_COLUMN,
    KIND_GT,
    KIND_PREDICTION,
    evaluate_run,
    evaluate_scene,
    read_matches,
    read_metrics,
    scene_output_dir,
    status_at_column,
    status_at_columns,
)
from coperceive.evaluation.predictions import PredictionBox, RunMeta, write_run
from coperceive.evaluation.report import (
    Row,
    aggregate,
    ap_column,
    collect,
    model_ap_column,
    outputs_of,
    pooled_ap,
)
from coperceive.model import (
    SCHEMA_VERSION,
    Agent,
    AgentType,
    Annotation,
    BoxFrame,
    Clock,
    Modality,
    PointCloud,
    Scene,
    Sensor,
    SharedObject,
    StampedPose,
)
from coperceive.se3 import SE3
from coperceive.store import write_scene

from tests.evalcheck.scenario import (
    FRAME_BODY,
    FRAME_WORLD,
    ApResult,
    BoxSpec,
    ConfigSpec,
    CooperativeResult,
    GtCooperativeResult,
    OutputResult,
    PairResult,
    PooledResult,
    Quat,
    RunResult,
    RunSpec,
    SampleResult,
    SceneResult,
    SceneSpec,
    TotalsResult,
    TpErrorsResult,
    Vec3,
)

RUN_ID = "evalcheck"
LIDAR_ID = "lidar"
CREATED_UTC = "2026-11-15T00:00:00Z"
_MEAN_KEYS = ("mean", "mean_model")


# --- scenario -> product objects ----------------------------------------------------


def _rotation(q: Quat) -> SE3:
    return SE3(q[0], q[1], q[2], q[3], 0.0, 0.0, 0.0)


def _pose(q: Quat, t: Vec3) -> SE3:
    return SE3(q[0], q[1], q[2], q[3], t[0], t[1], t[2])


def _frame(box: BoxSpec) -> BoxFrame:
    if box.frame == FRAME_BODY:
        return BoxFrame.BODY
    if box.frame == FRAME_WORLD:
        return BoxFrame.WORLD
    raise ValueError(f"unknown frame {box.frame!r}")


def product_scene(spec: SceneSpec) -> Scene:
    """The schema-1 scene of ``spec``: agents, annotations and shared objects, no sensors."""
    agents = tuple(
        Agent(
            agent_id=agent.agent_id,
            agent_type=AgentType.VEHICLE,
            poses=tuple(
                StampedPose(pose.time_us, _pose(pose.rotation, pose.translation))
                for pose in agent.poses
            ),
        )
        for agent in spec.agents
    )
    annotations = tuple(
        Annotation(
            agent_id=gt.agent_id,
            time_us=gt.time_us,
            track_id=gt.track_id,
            category=gt.category,
            center=gt.box.center,
            size_lwh=gt.box.size_lwh,
            box_from_rpy=_rotation(gt.box.rotation),
            visibility=gt.visibility,
            frame=_frame(gt.box),
        )
        for gt in spec.ground_truth
    )
    with_lidar = sorted({sweep.agent_id for sweep in spec.lidar})
    sensors = tuple(
        Sensor(
            sensor_id=LIDAR_ID,
            agent_id=agent_id,
            modality=Modality.LIDAR,
            body_from_sensor=SE3.identity(),
        )
        for agent_id in with_lidar
    )
    clouds = tuple(
        PointCloud(
            agent_id=sweep.agent_id,
            sensor_id=LIDAR_ID,
            time_us=sweep.time_us,
            count=len(sweep.points),
            xyz_i_f32=b"".join(struct.pack("<4f", x, y, z, 0.0) for x, y, z in sweep.points),
        )
        for sweep in spec.lidar
    )
    return Scene(
        schema_version=SCHEMA_VERSION,
        scene_id=spec.scene_id,
        world_frame="world",
        clock=Clock(unit="us", scale_note="synthetic microseconds"),
        agents=agents,
        sensors=sensors,
        annotations=annotations,
        objects=tuple(
            SharedObject(track_id, category) for track_id, category in spec.shared_tracks
        ),
        point_clouds=clouds,
    )


def product_boxes(spec: SceneSpec) -> tuple[PredictionBox, ...]:
    """The rows of ``boxes.parquet`` for ``spec``, in file order."""
    return tuple(
        PredictionBox(
            scene_id=spec.scene_id,
            agent_id=pred.agent_id,
            source=pred.source,
            time_us=pred.time_us,
            category=pred.category,
            score=pred.score,
            track_id=None,
            center=pred.box.center,
            size_lwh=pred.box.size_lwh,
            box_from_rpy=_rotation(pred.box.rotation),
            frame=_frame(pred.box),
        )
        for pred in spec.predictions
    )


def product_config(spec: ConfigSpec) -> EvalConfig:
    """The product config with the same values as ``spec``."""
    return EvalConfig(
        criterion=spec.criterion,
        thresholds=tuple(spec.thresholds),
        display_threshold=spec.display_threshold,
        class_thresholds=MappingProxyType(
            {name: tuple(values) for name, values in spec.class_thresholds.items()}
        ),
        ignore_gt_visibility_below=spec.ignore_gt_visibility_below,
        max_range_m=spec.max_range_m,
        score_threshold=spec.score_threshold,
        gt_class_map=MappingProxyType(dict(spec.gt_class_map)),
        ranking_scope=spec.ranking_scope,
        gt_scope=spec.gt_scope,
        comm_range_m=spec.comm_range_m,
    )


def product_meta(run: RunSpec) -> RunMeta:
    """``run.json`` for ``run``: its categories and every source its predictions use."""
    sources = dict.fromkeys(pred.source for scene in run.scenes for pred in scene.predictions)
    return RunMeta(
        run_id=RUN_ID,
        model="evalcheck",
        score_note="synthetic",
        categories=tuple(run.categories),
        sources=tuple((name, "synthetic") for name in sources),
        created_utc=CREATED_UTC,
    )


# --- product results -> plain data ----------------------------------------------------


def _opt_float(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"expected a number or null, got {value!r}")
    return float(value)


def _int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"expected an int, got {value!r}")
    return value


def _opt_str(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"expected a string or null, got {value!r}")
    return value


def _opt_bool(value: object) -> bool | None:
    if value is None:
        return None
    if not isinstance(value, bool):
        raise TypeError(f"expected a bool or null, got {value!r}")
    return value


def _obj(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise TypeError(f"expected an object, got {value!r}")
    return {str(key): item for key, item in cast(dict[object, object], value).items()}


def _ap_result(entry: Mapping[str, object]) -> ApResult:
    return ApResult(
        per_category={
            name: _opt_float(value) for name, value in entry.items() if name not in _MEAN_KEYS
        },
        mean=_opt_float(entry.get("mean")),
        mean_model=_opt_float(entry.get("mean_model")),
    )


def _output_result(
    output: Mapping[str, object],
    rows: Sequence[Mapping[str, object]],
    thresholds: Sequence[float],
) -> OutputResult:
    """One output of ``metrics.json`` with its rows of the match table."""
    agent_id = str(output["agent_id"])
    source = str(output["source"])
    mine = [row for row in rows if row["agent_id"] == agent_id and row["source"] == source]
    pred_status: dict[int, str] = {}
    gt_status: dict[int, str] = {}
    pred_status_at: list[dict[int, str]] = [{} for _ in thresholds]
    gt_cooperative: dict[int, GtCooperativeResult] = {}
    gt_of_pair: dict[int, int] = {}
    pred_of_pair: dict[int, Mapping[str, object]] = {}
    for row in mine:
        index = _int(row["row"])
        if row["kind"] == KIND_GT:
            gt_status[index] = str(row["status"])
            seen = _opt_bool(row["seen_by_other_gt"])
            if seen is None:
                raise TypeError("seen_by_other_gt is null on a gt row")
            annotated_by = _opt_str(row.get(GT_AGENT_COLUMN))
            gt_cooperative[index] = GtCooperativeResult(
                status_single=_opt_str(row["status_single"]),
                status_fused=_opt_str(row["status_fused"]),
                seen_by_other_gt=seen,
                other_kind=_opt_str(row["other_kind"]),
                other_agent_tp=_opt_bool(row["other_agent_tp"]),
                added_by=None if annotated_by in (None, agent_id) else annotated_by,
            )
            if row["pair_id"] is not None:
                gt_of_pair[_int(row["pair_id"])] = index
        elif row["kind"] == KIND_PREDICTION:
            pred_status[index] = str(row["status"])
            for i in range(len(thresholds)):
                pred_status_at[i][index] = str(row[status_at_column(i)])
            if row["pair_id"] is not None:
                pred_of_pair[_int(row["pair_id"])] = row
        else:
            raise TypeError(f"unknown kind {row['kind']!r}")
    if set(gt_of_pair) != set(pred_of_pair):
        raise AssertionError("a pair_id is not shared by exactly one gt and one prediction row")
    pairs = tuple(
        PairResult(
            pred_row=_int(row["row"]),
            gt_row=gt_of_pair[pair_id],
            iou_bev=float(cast(float, row["iou_bev"])),
            centre_error_m=float(cast(float, row["centre_error_m"])),
            yaw_error_deg=float(cast(float, row["yaw_error_deg"])),
        )
        for pair_id, row in sorted(pred_of_pair.items())
    )
    samples = tuple(
        SampleResult(
            time_us=_int(sample["time_us"]),
            tp=_int(sample["tp"]),
            fp=_int(sample["fp"]),
            miss=_int(sample["miss"]),
            ignored_gt=_int(sample["ignored_gt"]),
            precision=_opt_float(sample["precision"]),
            recall=_opt_float(sample["recall"]),
        )
        for sample in (_obj(item) for item in cast(Sequence[object], output["samples"]))
    )
    totals = _obj(output["totals"])
    ap = _obj(output["ap"])
    errors = _obj(output["tp_errors"])
    centre, yaw, size, iou = (
        _obj(errors["centre_m"]),
        _obj(errors["yaw_deg"]),
        _obj(errors["size_m"]),
        _obj(errors["iou_bev"]),
    )
    cooperative = _obj(output["cooperative"])
    return OutputResult(
        agent_id=agent_id,
        source=source,
        pred_status=pred_status,
        gt_status=gt_status,
        pred_status_at=tuple(pred_status_at),
        pairs=pairs,
        samples=samples,
        totals=TotalsResult(
            tp=_int(totals["tp"]),
            fp=_int(totals["fp"]),
            miss=_int(totals["miss"]),
            ignored_gt=_int(totals["ignored_gt"]),
            gt=_int(totals["gt"]),
            predictions=_int(totals["predictions"]),
            precision=_opt_float(totals["precision"]),
            recall=_opt_float(totals["recall"]),
        ),
        ap=tuple(_ap_result(_obj(ap[threshold_key(t)])) for t in thresholds),
        tp_errors=TpErrorsResult(
            centre_mean_m=_opt_float(centre["mean"]),
            centre_median_m=_opt_float(centre["median"]),
            yaw_mean_abs_deg=_opt_float(yaw["mean_abs"]),
            size_length_m=_opt_float(size["length"]),
            size_width_m=_opt_float(size["width"]),
            size_height_m=_opt_float(size["height"]),
            iou_bev_mean=_opt_float(iou["mean"]),
        ),
        cooperative=CooperativeResult(
            recovered=_int(cooperative["recovered"]),
            lost_by_fusion=_int(cooperative["lost_by_fusion"]),
            only_other_agent=_int(cooperative["only_other_agent"]),
            nobody=_int(cooperative["nobody"]),
            recovery_rate=_opt_float(cooperative["recovery_rate"]),
        ),
        gt_cooperative=gt_cooperative,
    )


def _scene_result(
    scene_id: str,
    metrics: Mapping[str, object],
    rows: Sequence[Mapping[str, object]],
    thresholds: Sequence[float],
) -> SceneResult:
    outputs = tuple(
        _output_result(_obj(item), rows, thresholds)
        for item in cast(Sequence[object], metrics["outputs"])
    )
    return SceneResult(scene_id=scene_id, outputs=outputs)


# --- the two paths ----------------------------------------------------------------


def evaluate_in_memory(run: RunSpec) -> tuple[SceneResult, ...]:
    """``evaluate_scene`` on every scene of ``run``, in ``scene_id`` order.

    The run covers every scene (spec A.0): each is evaluated with the outputs of the
    run, as ``evaluate_run`` does with ``run_outputs``.
    """
    meta = product_meta(run)
    config = product_config(run.config)
    ordered = sorted(run.scenes, key=lambda scene: scene.scene_id)
    outputs = tuple(
        dict.fromkeys((pred.agent_id, pred.source) for spec in ordered for pred in spec.predictions)
    )
    results: list[SceneResult] = []
    for spec in ordered:
        evaluation = evaluate_scene(
            product_scene(spec),
            meta,
            product_boxes(spec),
            config,
            created_utc=CREATED_UTC,
            outputs=outputs,
        )
        results.append(
            _scene_result(
                spec.scene_id, evaluation.metrics, evaluation.matches, run.config.thresholds
            )
        )
    return tuple(results)


def _pooled_row(
    agent_id: str | None,
    source: str | None,
    rows: Sequence[Row],
    thresholds: Sequence[float],
) -> PooledResult:
    totals = aggregate(rows, thresholds)
    per_class = pooled_ap(rows, thresholds)
    ap = tuple(
        ApResult(
            per_category=dict(per_class[threshold_key(t)]),
            mean=_opt_float(totals[ap_column(t)]),
            mean_model=_opt_float(totals[model_ap_column(t)]),
        )
        for t in thresholds
    )
    return PooledResult(
        agent_id=agent_id,
        source=source,
        gt=_int(totals["gt"]),
        predictions=_int(totals["predictions"]),
        tp=_int(totals["tp"]),
        fp=_int(totals["fp"]),
        miss=_int(totals["miss"]),
        precision=_opt_float(totals["precision"]),
        recall=_opt_float(totals["recall"]),
        recovered=_int(totals["recovered"]),
        lost_by_fusion=_int(totals["lost_by_fusion"]),
        ap=ap,
    )


def evaluate_on_disk(run: RunSpec, root: Path) -> RunResult:
    """The full path under ``root``: write, ``evaluate_run``, read the files back, pool."""
    scenes_root = root / "scenes"
    predictions_root = root / "predictions"
    for spec in run.scenes:
        write_scene(product_scene(spec), scenes_root / spec.scene_id)
    meta = product_meta(run)
    boxes = [box for spec in run.scenes for box in product_boxes(spec)]
    write_run(
        predictions_root, RUN_ID, meta, boxes, scene_ids=[spec.scene_id for spec in run.scenes]
    )
    report = evaluate_run(
        scenes_root,
        predictions_root,
        RUN_ID,
        product_config(run.config),
        created_utc=CREATED_UTC,
    )
    if report.errors:
        raise AssertionError(f"evaluate_run reported errors: {report.errors}")
    thresholds = tuple(run.config.thresholds)
    columns = [*read_matches_columns(), *status_at_columns(len(thresholds)), GT_AGENT_COLUMN]
    scenes: list[SceneResult] = []
    for scene_id in sorted(report.evaluated):
        directory = scene_output_dir(predictions_root, RUN_ID, scene_id)
        metrics = read_metrics(directory)
        table = read_matches(directory, columns=columns)
        rows = cast(list[dict[str, object]], table.to_pylist())
        scenes.append(_scene_result(scene_id, metrics, rows, thresholds))
    collected = collect(scenes_root, predictions_root, RUN_ID)
    pooled = [
        _pooled_row(key[0], key[1], [row for row in collected if row.output == key], thresholds)
        for key in outputs_of(collected)
    ]
    pooled.append(_pooled_row(None, None, collected, thresholds))
    return RunResult(scenes=tuple(scenes), pooled=tuple(pooled))


def read_matches_columns() -> list[str]:
    """The base columns of ``matches.parquet`` (``MATCH_SCHEMA``), in file order."""
    from coperceive.evaluation.evaluate import MATCH_SCHEMA

    return list(MATCH_SCHEMA.names)
