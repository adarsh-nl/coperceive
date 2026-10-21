# SPDX-License-Identifier: Apache-2.0
"""JSON frames for the in-app viewer.

The page draws these with three.js. This module does not import a web
framework or a viewer SDK. Point bytes are little-endian float32 ``x, y, z``
in the scene world frame. The scrubber times are the recorded pose times.
Poses are the discrete samples: ``max_gap_us`` is 0, so a time that is not a
sample is omitted rather than interpolated.
"""

from __future__ import annotations

import array
import math
import sys
from pathlib import Path

from coperceive.diagnostics import annotation_center_world
from coperceive.errors import CoperceiveError
from coperceive.model import Annotation, BoxFrame, Modality, PointCloud, Scene, Sensor
from coperceive.se3 import SE3
from coperceive.transform import lookup_pose

_BOX_CORNERS = (
    (-1.0, -1.0, -1.0),
    (1.0, -1.0, -1.0),
    (1.0, 1.0, -1.0),
    (-1.0, 1.0, -1.0),
    (-1.0, -1.0, 1.0),
    (1.0, -1.0, 1.0),
    (1.0, 1.0, 1.0),
    (-1.0, 1.0, 1.0),
)
_BOX_EDGES = (
    (0, 1),
    (1, 2),
    (2, 3),
    (3, 0),
    (4, 5),
    (5, 6),
    (6, 7),
    (7, 4),
    (0, 4),
    (1, 5),
    (2, 6),
    (3, 7),
)


def scene_directories(root: Path) -> list[Path]:
    """Scene directories directly under ``root``, in name order."""
    if not root.is_dir():
        raise FileNotFoundError(root)
    if (root / "manifest.json").is_file():
        return [root]
    found: list[Path] = []
    for child in sorted(root.iterdir(), key=lambda path: path.name):
        if child.name.startswith(".") or not child.is_dir():
            continue
        if (child / "manifest.json").is_file():
            found.append(child)
    return found


def summarize(scene: Scene) -> dict[str, object]:
    """Agents, sensors, and the pose times a scrubber can select."""
    return {
        "scene_id": scene.scene_id,
        "world_frame": scene.world_frame,
        "clock_unit": scene.clock.unit,
        "agents": [
            {
                "agent_id": agent.agent_id,
                "agent_type": agent.agent_type.value,
                "display_name": agent.display_name,
            }
            for agent in scene.agents
        ],
        "sensors": [
            {
                "sensor_id": sensor.sensor_id,
                "agent_id": sensor.agent_id,
                "modality": sensor.modality.value,
            }
            for sensor in scene.sensors
        ],
        "times_us": list(pose_times(scene)),
        # What the scene carries, so the page can say "no lidar staged" instead of
        # drawing nothing. Whether image files exist is the server's to add.
        "point_sweeps": len(scene.point_clouds),
        "image_rows": len(scene.images),
    }


def pose_times(scene: Scene) -> tuple[int, ...]:
    """Sorted unique pose timestamps. These are the scrubber steps."""
    return tuple(sorted({pose.time_us for agent in scene.agents for pose in agent.poses}))


def frame_document(scene: Scene, time_us: int) -> dict[str, object]:
    """One scrubber step: poses, box lines, and camera frustums. No point bytes."""
    agents: list[dict[str, object]] = []
    for agent in scene.agents:
        try:
            pose = lookup_pose(agent, time_us, max_gap_us=0)
        except CoperceiveError:
            agents.append({"agent_id": agent.agent_id, "pose": None})
            continue
        agents.append({"agent_id": agent.agent_id, "pose": _pose_json(pose)})
    point_sensors: list[dict[str, object]] = []
    for cloud in scene.point_clouds:
        if cloud.time_us != time_us:
            continue
        point_sensors.append(
            {
                "agent_id": cloud.agent_id,
                "sensor_id": cloud.sensor_id,
                "count": cloud.count,
            }
        )
    return {
        "scene_id": scene.scene_id,
        "time_us": time_us,
        "interpolated": False,
        "agents": agents,
        "boxes": [_box_json(scene, ann) for ann in scene.annotations if ann.time_us == time_us],
        "frustums": _frustums(scene, time_us),
        "point_sensors": point_sensors,
    }


def image_path(scene: Scene, time_us: int, agent_id: str, sensor_id: str) -> str | None:
    """Relative path recorded for one camera sample, or ``None`` when it is absent."""
    for image in scene.images:
        if image.time_us == time_us and image.agent_id == agent_id and image.sensor_id == sensor_id:
            return image.path
    return None


def frame_points(scene: Scene, time_us: int, agent_id: str, sensor_id: str) -> bytes:
    """World ``x, y, z`` float32 bytes for one sweep. Empty when that sweep is absent."""
    for cloud in scene.point_clouds:
        if cloud.time_us == time_us and cloud.agent_id == agent_id and cloud.sensor_id == sensor_id:
            return world_points(scene, cloud)
    return b""


def world_points(scene: Scene, cloud: PointCloud) -> bytes:
    """Map sensor-frame ``x, y, z, intensity`` bytes into world ``x, y, z``."""
    pose = lookup_pose(scene.agent(cloud.agent_id), cloud.time_us, max_gap_us=0)
    sensor = scene.sensor(cloud.agent_id, cloud.sensor_id)
    world_from_sensor = pose.compose(sensor.body_from_sensor)
    if cloud.count == 0:
        return b""
    values = array.array("f")
    values.frombytes(cloud.xyz_i_f32)
    if sys.byteorder != "little":
        values.byteswap()
    out = array.array("f")
    for index in range(cloud.count):
        base = index * 4
        point = world_from_sensor.apply((values[base], values[base + 1], values[base + 2]))
        out.extend(point)
    if sys.byteorder != "little":
        out.byteswap()
    return out.tobytes()


def fov_y_rad(sensor: Sensor) -> float | None:
    """Vertical field of view from the pinhole. ``None`` when it cannot be formed."""
    if sensor.modality is not Modality.CAMERA:
        return None
    if sensor.fy is None or sensor.cy is None or sensor.fy == 0.0:
        return None
    return 2.0 * math.atan(sensor.cy / sensor.fy)


def _frustums(scene: Scene, time_us: int) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for sensor in scene.sensors:
        fov = fov_y_rad(sensor)
        if fov is None:
            continue
        try:
            pose = lookup_pose(scene.agent(sensor.agent_id), time_us, max_gap_us=0)
        except CoperceiveError:
            continue
        world_from_sensor = pose.compose(sensor.body_from_sensor)
        aspect = _aspect(sensor)
        rows.append(
            {
                "agent_id": sensor.agent_id,
                "sensor_id": sensor.sensor_id,
                "pose": _pose_json(world_from_sensor),
                "fov_y_rad": fov,
                "aspect": aspect,
                "image_width_px": sensor.image_width_px,
                "image_height_px": sensor.image_height_px,
                "fx": sensor.fx,
                "fy": sensor.fy,
                "cx": sensor.cx,
                "cy": sensor.cy,
            }
        )
    return rows


def _aspect(sensor: Sensor) -> float:
    if sensor.image_width_px is not None and sensor.image_height_px is not None:
        return sensor.image_width_px / sensor.image_height_px
    if sensor.cx is not None and sensor.cy is not None and sensor.cy != 0.0:
        return sensor.cx / sensor.cy
    return 1.0


def _box_json(scene: Scene, ann: Annotation) -> dict[str, object]:
    center = annotation_center_world(scene, ann, max_gap_us=0)
    rotation = _world_rotation(scene, ann)
    return {
        "agent_id": ann.agent_id,
        "track_id": ann.track_id,
        "category": ann.category,
        "center": [center[0], center[1], center[2]],
        "size_lwh": [ann.size_lwh[0], ann.size_lwh[1], ann.size_lwh[2]],
        "pose": _pose_json(rotation.translation(center[0], center[1], center[2])),
        "lines": _box_lines(center, ann.size_lwh, rotation),
    }


def _world_rotation(scene: Scene, ann: Annotation) -> SE3:
    if ann.frame is BoxFrame.WORLD:
        rotation = ann.box_from_rpy
        return SE3(rotation.qw, rotation.qx, rotation.qy, rotation.qz, 0.0, 0.0, 0.0)
    pose = lookup_pose(scene.agent(ann.agent_id), ann.time_us, max_gap_us=0)
    composed = pose.compose(ann.box_from_rpy)
    return SE3(composed.qw, composed.qx, composed.qy, composed.qz, 0.0, 0.0, 0.0)


def _box_lines(
    center: tuple[float, float, float],
    size: tuple[float, float, float],
    rotation: SE3,
) -> list[float]:
    corners: list[tuple[float, float, float]] = []
    for sx, sy, sz in _BOX_CORNERS:
        local = (sx * size[0] * 0.5, sy * size[1] * 0.5, sz * size[2] * 0.5)
        rotated = rotation.apply(local)
        corners.append((rotated[0] + center[0], rotated[1] + center[1], rotated[2] + center[2]))
    lines: list[float] = []
    for start, end in _BOX_EDGES:
        lines.extend(corners[start])
        lines.extend(corners[end])
    return lines


box_lines = _box_lines  # public name for coperceive.evalview; same 24-vertex edge list


def _pose_json(pose: SE3) -> dict[str, object]:
    return {
        "qw": pose.qw,
        "qx": pose.qx,
        "qy": pose.qy,
        "qz": pose.qz,
        "tx": pose.tx,
        "ty": pose.ty,
        "tz": pose.tz,
    }
