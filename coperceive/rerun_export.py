# SPDX-License-Identifier: Apache-2.0
"""Write one scene to a Rerun ``.rrd`` file.

Rerun stays an optional extra. Importing this module does not import
``rerun``. The recording is the same discrete samples :mod:`coperceive.viewdata`
serves to the three.js page. ``spawn`` is false, so this does not open a viewer.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any, cast

from coperceive.model import Scene
from coperceive.viewdata import frame_document, frame_points, pose_times


class RerunUnavailable(RuntimeError):
    """The ``viewer`` extra is not installed."""


def export_rerun(scene: Scene, dest: Path) -> int:
    """Write ``dest`` and return the number of timeline samples."""
    try:
        import rerun as rr
    except ImportError as exc:
        raise RerunUnavailable(
            "Rerun is not installed. Install the viewer extra: pip install 'coperceive[viewer]'"
        ) from exc
    _disable_analytics()
    dest.parent.mkdir(parents=True, exist_ok=True)
    rr.init("coperceive", spawn=False)
    rr.save(dest)
    times = pose_times(scene)
    for time_us in times:
        rr.set_time_seconds("coperceive", time_us / 1_000_000.0)
        _log_frame(scene, time_us)
    return len(times)


def _disable_analytics() -> None:
    binary = Path(sys.executable).parent / "rerun"
    if not binary.is_file():
        return
    subprocess.run(
        [str(binary), "analytics", "disable"],
        check=False,
        capture_output=True,
        timeout=30,
    )


def _log_frame(scene: Scene, time_us: int) -> None:
    import rerun as rerun_module

    frame = frame_document(scene, time_us)
    agents = _as_list(frame["agents"])
    for item in agents:
        row = _as_dict(item)
        agent_id = row["agent_id"]
        if not isinstance(agent_id, str):
            continue
        pose = row["pose"]
        if isinstance(pose, dict):
            pose_map = cast(dict[object, object], pose)
            rerun_module.log(
                f"agents/{agent_id}",
                rerun_module.Transform3D(
                    translation=_translation(pose_map),
                    quaternion=_quaternion(pose_map),
                ),
            )
    for item in _as_list(frame["point_sensors"]):
        row = _as_dict(item)
        agent_id = row.get("agent_id")
        sensor_id = row.get("sensor_id")
        if not isinstance(agent_id, str) or not isinstance(sensor_id, str):
            continue
        blob = frame_points(scene, time_us, agent_id, sensor_id)
        positions = _positions(blob)
        if not positions:
            continue
        rerun_module.log(
            f"agents/{agent_id}/{sensor_id}",
            rerun_module.Points3D(positions),
        )
    by_agent: dict[str, list[dict[str, object]]] = {}
    for item in _as_list(frame["boxes"]):
        row = _as_dict(item)
        agent_id = row.get("agent_id")
        if isinstance(agent_id, str):
            by_agent.setdefault(agent_id, []).append(row)
    for agent_id, boxes in by_agent.items():
        centers: list[list[float]] = []
        sizes: list[list[float]] = []
        quaternions: list[object] = []
        labels: list[str] = []
        for box in boxes:
            pose = box.get("pose")
            size = box.get("size_lwh")
            if not isinstance(pose, dict) or not isinstance(size, list):
                continue
            centers.append(_translation(cast(dict[object, object], pose)))
            sizes.append([_number(value) for value in cast(list[object], size)])
            quaternions.append(_quaternion(cast(dict[object, object], pose)))
            category = box.get("category")
            labels.append(category if isinstance(category, str) else "")
        if centers:
            rerun_module.log(
                f"agents/{agent_id}/boxes",
                rerun_module.Boxes3D(
                    centers=centers,
                    sizes=sizes,
                    quaternions=quaternions,
                    labels=labels,
                ),
            )
    for item in _as_list(frame["frustums"]):
        row = _as_dict(item)
        agent_id = row.get("agent_id")
        sensor_id = row.get("sensor_id")
        pose = row.get("pose")
        fov = row.get("fov_y_rad")
        aspect = row.get("aspect")
        if (
            not isinstance(agent_id, str)
            or not isinstance(sensor_id, str)
            or not isinstance(pose, dict)
            or not isinstance(fov, float)
            or not isinstance(aspect, (int, float))
        ):
            continue
        pose_map = cast(dict[object, object], pose)
        rerun_module.log(
            f"agents/{agent_id}/{sensor_id}",
            rerun_module.Transform3D(
                translation=_translation(pose_map),
                quaternion=_quaternion(pose_map),
            ),
            rerun_module.Pinhole(
                fov_y=fov,
                aspect_ratio=float(aspect),
                camera_xyz=rerun_module.ViewCoordinates.RDF,
                image_plane_distance=3.0,
            ),
        )


def _translation(pose: dict[object, object]) -> list[float]:
    return [float(_number(pose[key])) for key in ("tx", "ty", "tz")]


def _quaternion(pose: dict[object, object]) -> Any:
    import rerun as rerun_module

    return rerun_module.Quaternion(
        xyzw=[
            float(_number(pose["qx"])),
            float(_number(pose["qy"])),
            float(_number(pose["qz"])),
            float(_number(pose["qw"])),
        ]
    )


def _positions(blob: bytes) -> list[list[float]]:
    import array
    import sys

    if not blob:
        return []
    values = array.array("f")
    values.frombytes(blob)
    if sys.byteorder != "little":
        values.byteswap()
    rows: list[list[float]] = []
    for index in range(0, len(values), 3):
        rows.append([values[index], values[index + 1], values[index + 2]])
    return rows


def _number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("expected a number")
    return float(value)


def _as_list(value: object) -> list[object]:
    if not isinstance(value, list):
        raise TypeError("expected a list")
    return cast(list[object], value)


def _as_dict(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise TypeError("expected an object")
    raw = cast(dict[object, object], value)
    result: dict[str, object] = {}
    for key, item in raw.items():
        if isinstance(key, str):
            result[key] = item
    return result
