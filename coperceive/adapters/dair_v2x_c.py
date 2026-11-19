# SPDX-License-Identifier: Apache-2.0
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownLambdaType=false, reportArgumentType=false, reportPrivateUsage=false, reportIndexIssue=false
"""DAIR-V2X-C cooperative adapter (published layout + CoAlign complemented labels).

The sensor data is not on the cluster (checked 2026-11-05); this adapter is
written against the published layout in the DAIR-V2X data README and the
CoAlign complemented-annotation tree. Names and calibration JSON shapes marked
"to verify against the cluster copy" in docs/datasets/dair-v2x-c.md.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from coperceive.adapters.build import BuildError, CheckReport, SceneRef
from coperceive.adapters.readers.lidar import read_pcd
from coperceive.model import (
    Agent,
    AgentType,
    Annotation,
    BoxFrame,
    Clock,
    Modality,
    PointCloud,
    Scene,
    Sensor,
    StampedPose,
)
from coperceive.se3 import SE3, Matrix3

NAME = "dair-v2x-c"
SPLITS = ("train", "val")
CVI = "cooperative-vehicle-infrastructure"
COMPLEMENTED = "DAIR-V2X-C_Complemented_Anno"


def _json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BuildError(f"{path}: {exc}") from None


def _calib(path: Path) -> SE3:
    payload = _json(path)
    if not isinstance(payload, dict):
        raise BuildError(f"{path}: calibration must be a mapping")
    rotation = payload.get("rotation")
    translation = payload.get("translation")
    if not isinstance(rotation, list) or len(rotation) != 3:
        raise BuildError(f"{path}: calibration rotation must be 3x3")
    if not isinstance(translation, list) or len(translation) != 3:
        raise BuildError(f"{path}: calibration translation must be [x, y, z]")
    try:
        rows: Matrix3 = tuple(
            (float(row[0]), float(row[1]), float(row[2]))
            for row in rotation  # type: ignore[index]
        )
        t = (float(translation[0]), float(translation[1]), float(translation[2]))
    except (TypeError, IndexError, ValueError) as exc:
        raise BuildError(f"{path}: calibration is not numeric: {exc}") from None
    return SE3.from_matrix(rows, t)


def _frame_of(path: str) -> str:
    stem = Path(path).stem
    return stem


def _world_corners(label: Mapping[str, object], path: Path) -> list[tuple[float, float, float]]:
    corners = label.get("world_8_points")
    if not isinstance(corners, list) or len(corners) != 8:
        raise BuildError(f"{path}: world_8_points must hold 8 corners")
    out: list[tuple[float, float, float]] = []
    for corner in corners:
        if not isinstance(corner, list) or len(corner) < 3:
            raise BuildError(f"{path}: a corner is not [x, y, z]")
        out.append((float(corner[0]), float(corner[1]), float(corner[2])))
    return out


def _box_from_corners(
    corners: Sequence[tuple[float, float, float]],
) -> tuple[tuple[float, float, float], tuple[float, float, float], SE3]:
    """Centre, length/width/height and yaw-about-z from the 8 world corners.

    The corner order used by DAIR's converter is a box ring; to verify against
    the cluster copy. We use the three edges from corner 0.
    """
    p0 = corners[0]

    def sub(
        a: tuple[float, float, float], b: tuple[float, float, float]
    ) -> tuple[float, float, float]:
        return (a[0] - b[0], a[1] - b[1], a[2] - b[2])

    def norm(v: tuple[float, float, float]) -> float:
        return math.sqrt(sum(c * c for c in v))

    edge_x = sub(corners[1], p0)
    edge_y = sub(corners[3], p0)
    edge_z = sub(corners[4], p0)
    length, width, height = norm(edge_x), norm(edge_y), norm(edge_z)
    center = (
        sum(c[0] for c in corners) / 8.0,
        sum(c[1] for c in corners) / 8.0,
        sum(c[2] for c in corners) / 8.0,
    )
    yaw = math.degrees(math.atan2(edge_x[1], edge_x[0]))
    return center, (length, width, height), SE3.from_rpy_deg(0.0, 0.0, yaw)


@dataclass(frozen=True)
class _Frame:
    frame: str
    coop: Mapping[str, object]
    vehicle: Mapping[str, object]
    infrastructure: Mapping[str, object]


class _Licence:
    status = "unknown"
    spdx = None
    notice = (
        "No data licence is stated in the DAIR-V2X data README checked 2026-11-05; "
        "cite arXiv:2205.08540. Research use on the HPC only."
    )
    links = (
        "https://github.com/yujmo/DAIR-V2X/blob/main/data/README.md",
        "https://arxiv.org/abs/2205.08540",
    )


class DairV2XCAdapter:
    name = NAME
    spec = None
    licence = _Licence()

    def __init__(self, root: Path) -> None:
        self.root = root

    def _layout(self, raw_root: Path) -> tuple[Path, Path]:
        cvi = raw_root / CVI if (raw_root / CVI).is_dir() else raw_root
        return cvi, raw_root / COMPLEMENTED

    def _rows(self, raw_root: Path) -> tuple[dict[str, _Frame], dict[str, list[str]]]:
        cvi, anno = self._layout(raw_root)
        coop = _json(cvi / "cooperative" / "data_info.json")
        vehicle = _json(cvi / "vehicle-side" / "data_info.json")
        infra = _json(cvi / "infrastructure-side" / "data_info.json")
        if not all(isinstance(value, list) for value in (coop, vehicle, infra)):
            raise BuildError(f"{cvi}: data_info.json files must be lists of frame records")
        vehicle_by_frame = {
            _frame_of(str(row["pointcloud_path"])): row
            for row in vehicle
            if isinstance(row, dict) and "pointcloud_path" in row
        }
        infra_by_frame = {
            _frame_of(str(row["pointcloud_path"])): row
            for row in infra
            if isinstance(row, dict) and "pointcloud_path" in row
        }
        frames: dict[str, _Frame] = {}
        for row in coop:
            if not isinstance(row, dict):
                raise BuildError(f"{cvi}/cooperative/data_info.json: a row is not a mapping")
            label = row.get("cooperative_label_path")
            if not isinstance(label, str):
                raise BuildError(f"{cvi}: cooperative row has no cooperative_label_path")
            frame = _frame_of(label)
            if frame not in vehicle_by_frame or frame not in infra_by_frame:
                raise BuildError(
                    f"{frame}: cooperative row has no matching vehicle/infrastructure frame"
                )
            frames[frame] = _Frame(frame, row, vehicle_by_frame[frame], infra_by_frame[frame])
        splits: dict[str, list[str]] = {}
        for split in SPLITS:
            path = anno / f"{split}.json"
            if not path.is_file():
                continue
            payload = _json(path)
            if not isinstance(payload, list):
                raise BuildError(f"{path}: split must be a list of frame ids")
            splits[split] = sorted(str(item) for item in payload if str(item) in frames)
        return frames, splits

    def scenes(self, raw_root: Path, params: Mapping[str, str]) -> tuple[SceneRef, ...]:
        split = params.get("split")
        if split is None:
            raise BuildError("param 'split' is required (--param split=train|val)")
        if split not in SPLITS:
            raise BuildError(f"param 'split' must be one of {', '.join(SPLITS)}")
        frames, splits = self._rows(raw_root)
        _cvi, anno = self._layout(raw_root)
        selected = splits.get(split)
        if selected is None:
            raise BuildError(f"split {split!r} has no complemented split file under {anno}")
        if not selected:
            raise BuildError(
                f"split {split!r} lists no cooperative frames present in data_info.json"
            )
        by_batch: dict[str, list[str]] = {}
        for frame in selected:
            row = frames[frame].vehicle
            batch = row.get("batch_id")
            if not isinstance(batch, str) or not batch:
                raise BuildError(f"{frame}: vehicle data_info has no batch_id")
            by_batch.setdefault(batch, []).append(frame)
        out: list[SceneRef] = []
        for batch, ids in sorted(by_batch.items()):
            out.append(SceneRef(batch, tuple(sorted(ids)), (), (("split", split),)))
        return tuple(out)

    def _sensor(self) -> Sensor:
        return Sensor("lidar", "vehicle", Modality.LIDAR, SE3.identity())

    def load(self, raw_root: Path, ref: SceneRef) -> Scene:
        frames, _splits = self._rows(raw_root)
        cvi, anno = self._layout(raw_root)
        vehicle_poses: list[StampedPose] = []
        infra_poses: list[StampedPose] = []
        annotations: list[Annotation] = []
        clouds: list[PointCloud] = []
        vehicle_sensor = Sensor("lidar", "vehicle", Modality.LIDAR, SE3.identity())
        infra_sensor = Sensor("lidar", "infrastructure", Modality.LIDAR, SE3.identity())
        for frame_id in ref.samples:
            frame = frames[str(frame_id)]
            veh = frame.vehicle
            inf = frame.infrastructure
            tf_v = _calib(cvi / str(veh["calib_novatel_to_world_path"])).compose(
                _calib(cvi / str(veh["calib_lidar_to_novatel_path"]))
            )
            tf_i = _calib(cvi / str(inf["calib_virtuallidar_to_world_path"]))
            time_v = int(float(veh["pointcloud_timestamp"]))
            time_i = int(float(inf["pointcloud_timestamp"]))
            vehicle_poses.append(StampedPose(time_v, tf_v))
            infra_poses.append(StampedPose(time_i, tf_i))
            for agent_id, info, _pose, _sensor in (
                ("vehicle", veh, tf_v, vehicle_sensor),
                ("infrastructure", inf, tf_i, infra_sensor),
            ):
                path = cvi / str(info["pointcloud_path"])
                count, packed = read_pcd(
                    path, fields={"x": "x", "y": "y", "z": "z"}, intensity_fill=0.0
                )
                clouds.append(
                    PointCloud(
                        agent_id, "lidar", int(float(info["pointcloud_timestamp"])), count, packed
                    )
                )
            label_path = anno / "new_labels/cooperative_label/label_world" / f"{frame_id}.json"
            if not label_path.is_file():
                label_path = cvi / str(frame.coop["cooperative_label_path"])
            labels = _json(label_path)
            if not isinstance(labels, list):
                raise BuildError(f"{label_path}: cooperative labels must be a list")
            for index, label in enumerate(labels):
                if not isinstance(label, dict):
                    raise BuildError(f"{label_path}: label {index} is not a mapping")
                corners = _world_corners(label, label_path)
                center, size, rotation = _box_from_corners(corners)
                annotations.append(
                    Annotation(
                        "vehicle",
                        time_v,
                        str(index),
                        str(label.get("type", "unknown")),
                        center,
                        size,
                        rotation,
                        frame=BoxFrame.WORLD,
                    )
                )
        agents = (
            Agent("vehicle", AgentType.VEHICLE, tuple(vehicle_poses)),
            Agent("infrastructure", AgentType.INFRASTRUCTURE, tuple(infra_poses)),
        )
        return Scene(
            schema_version=1,
            scene_id=ref.scene_id,
            world_frame="dair-v2x-c-world",
            clock=Clock("us", "DAIR pointcloud_timestamp is microseconds"),
            agents=agents,
            sensors=(vehicle_sensor, infra_sensor),
            annotations=tuple(annotations),
            objects=(),
            point_clouds=tuple(clouds),
        )

    def check(
        self, raw_root: Path, params: Mapping[str, str], *, scene_ids: Sequence[str] | None = None
    ) -> CheckReport:
        refs = self.scenes(raw_root, params)
        if scene_ids is not None:
            refs = tuple(ref for ref in refs if ref.scene_id in set(scene_ids))
        errors: list[str] = []
        counts: dict[str, tuple[tuple[str, int], ...]] = {}
        for ref in refs:
            try:
                scene = self.load(raw_root, ref)
                counts[ref.scene_id] = (
                    ("agents", len(scene.agents)),
                    ("boxes", len(scene.annotations)),
                    ("lidar", len(scene.point_clouds)),
                )
            except (BuildError, OSError, ValueError) as exc:
                errors.append(f"{ref.scene_id}: {exc}")
                counts[ref.scene_id] = ()
        return CheckReport(len(refs), tuple(errors), counts, ())
