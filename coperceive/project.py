# SPDX-License-Identifier: Apache-2.0
"""Project a box into an agent's camera.

The camera frame is the sensor frame. Depth is +Z, matching the Griffin
toolkit's camera-to-image step: the intrinsic multiplies ``(x, y, z)`` and
the pixel is divided by ``z``. This function does not round to integer pixels.
``z <= 0`` is behind the camera. A point is in the image when
``0 <= u < width`` and ``0 <= v < height``. Missing width or height cannot
decide that, so a point in front is reported as ``no_image_size``.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

from coperceive.model import Annotation, BoxFrame, Scene, Sensor
from coperceive.transform import body_frame, sensor_frame, transform, world_frame

# (sign length, sign width, sign height). Half-extents, then the box rotation.
CORNER_SIGNS: tuple[tuple[int, int, int], ...] = (
    (-1, -1, -1),
    (-1, -1, 1),
    (-1, 1, -1),
    (-1, 1, 1),
    (1, -1, -1),
    (1, -1, 1),
    (1, 1, -1),
    (1, 1, 1),
)


class ProjectionStatus(enum.Enum):
    IN_IMAGE = "in_image"
    OUT_OF_IMAGE = "out_of_image"
    BEHIND_CAMERA = "behind_camera"
    NO_IMAGE_SIZE = "no_image_size"


@dataclass(frozen=True)
class ProjectedPoint:
    """One point in the camera.

    ``u_px`` and ``v_px`` are set only when ``z_m > 0``. Behind the camera,
    including ``z_m == 0``, they are null.
    """

    x_m: float
    y_m: float
    z_m: float
    u_px: float | None
    v_px: float | None
    status: ProjectionStatus


@dataclass(frozen=True)
class ProjectedBox:
    camera_agent: str
    camera_sensor: str
    camera_time_us: int
    center: ProjectedPoint
    corners: tuple[ProjectedPoint, ...]

    @property
    def any_in_image(self) -> bool:
        if self.center.status is ProjectionStatus.IN_IMAGE:
            return True
        return any(corner.status is ProjectionStatus.IN_IMAGE for corner in self.corners)


def project_box(
    scene: Scene,
    annotation: Annotation,
    camera_agent: str,
    camera_sensor: str,
    *,
    max_gap_us: int,
    camera_time_us: int | None = None,
) -> ProjectedBox:
    """Project one box into a camera.

    The box is placed with the annotating agent's pose at ``annotation.time_us``.
    The camera pose is at ``camera_time_us``, or at the box time when that
    argument is omitted. Both lookups use ``max_gap_us`` and do not extrapolate.
    """
    when = annotation.time_us if camera_time_us is None else camera_time_us
    world_points = box_world_points(scene, annotation, max_gap_us=max_gap_us)
    camera_from_world = transform(
        scene,
        sensor_frame(camera_agent, camera_sensor),
        world_frame(),
        when,
        max_gap_us=max_gap_us,
    )
    sensor = scene.sensor(camera_agent, camera_sensor)
    center = project_camera_point(camera_from_world.apply(world_points[0]), sensor)
    corners = tuple(
        project_camera_point(camera_from_world.apply(point), sensor) for point in world_points[1:]
    )
    return ProjectedBox(
        camera_agent=camera_agent,
        camera_sensor=camera_sensor,
        camera_time_us=when,
        center=center,
        corners=corners,
    )


def box_world_points(
    scene: Scene,
    annotation: Annotation,
    *,
    max_gap_us: int,
) -> tuple[tuple[float, float, float], ...]:
    """Centre, then the eight corners, in the world frame."""
    length, width, height = annotation.size_lwh
    local = [(0.0, 0.0, 0.0)]
    for sx, sy, sz in CORNER_SIGNS:
        local.append((sx * length / 2.0, sy * width / 2.0, sz * height / 2.0))
    in_frame: list[tuple[float, float, float]] = []
    for point in local:
        rotated = annotation.box_from_rpy.apply(point)
        in_frame.append(
            (
                annotation.center[0] + rotated[0],
                annotation.center[1] + rotated[1],
                annotation.center[2] + rotated[2],
            )
        )
    if annotation.frame is BoxFrame.WORLD:
        return tuple(in_frame)
    world_from_body = transform(
        scene,
        world_frame(),
        body_frame(annotation.agent_id),
        annotation.time_us,
        max_gap_us=max_gap_us,
    )
    return tuple(world_from_body.apply(point) for point in in_frame)


def project_camera_point(
    point: tuple[float, float, float],
    sensor: Sensor,
) -> ProjectedPoint:
    """Pinhole projection of one camera-frame point."""
    x, y, z = point
    if sensor.fx is None or sensor.fy is None or sensor.cx is None or sensor.cy is None:
        raise ValueError(f"{sensor.agent_id}/{sensor.sensor_id} has no pinhole")
    if z <= 0.0:
        return ProjectedPoint(x, y, z, None, None, ProjectionStatus.BEHIND_CAMERA)
    u = sensor.fx * x / z + sensor.cx
    v = sensor.fy * y / z + sensor.cy
    width = sensor.image_width_px
    height = sensor.image_height_px
    if width is None or height is None:
        return ProjectedPoint(x, y, z, u, v, ProjectionStatus.NO_IMAGE_SIZE)
    if 0.0 <= u < width and 0.0 <= v < height:
        status = ProjectionStatus.IN_IMAGE
    else:
        status = ProjectionStatus.OUT_OF_IMAGE
    return ProjectedPoint(x, y, z, u, v, status)
