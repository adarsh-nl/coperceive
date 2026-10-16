# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import pytest
from coperceive.errors import ExtrapolationError
from coperceive.model import (
    SCHEMA_VERSION,
    Agent,
    AgentType,
    Annotation,
    BoxFrame,
    Clock,
    Modality,
    Scene,
    Sensor,
    StampedPose,
)
from coperceive.project import ProjectionStatus, project_box, project_camera_point
from coperceive.se3 import SE3


def _scene(
    sensor: Sensor,
    *,
    translation: tuple[float, float, float] = (0.0, 0.0, 0.0),
) -> Scene:
    tx, ty, tz = translation
    agent = Agent(
        "cam",
        AgentType.VEHICLE,
        (StampedPose(0, SE3.identity().translation(tx, ty, tz)),),
    )
    return Scene(
        schema_version=SCHEMA_VERSION,
        scene_id="projection",
        world_frame="ENU",
        clock=Clock("us", "projection golden"),
        agents=(agent,),
        sensors=(sensor,),
        annotations=(),
        objects=(),
    )


def _pinhole(
    *,
    width: int | None = 10,
    height: int | None = 12,
    fx: float = 10.0,
    fy: float = 20.0,
    cx: float = 5.0,
    cy: float = 6.0,
    extrinsic: SE3 | None = None,
) -> Sensor:
    return Sensor(
        "cam",
        "cam",
        Modality.CAMERA,
        SE3.identity() if extrinsic is None else extrinsic,
        image_width_px=width,
        image_height_px=height,
        fx=fx,
        fy=fy,
        cx=cx,
        cy=cy,
    )


def _world_box(
    center: tuple[float, float, float],
    size: tuple[float, float, float],
) -> Annotation:
    return Annotation(
        "cam",
        0,
        None,
        "car",
        center,
        size,
        SE3.identity(),
        None,
        BoxFrame.WORLD,
    )


def test_pinhole_pixels_match_the_hand_formula_and_are_not_rounded() -> None:
    sensor = _pinhole()
    origin = project_camera_point((0.0, 0.0, 10.0), sensor)
    assert origin.u_px == 5.0
    assert origin.v_px == 6.0
    assert origin.status is ProjectionStatus.IN_IMAGE

    shifted = project_camera_point((1.0, 2.0, 4.0), sensor)
    assert shifted.u_px == 7.5
    assert shifted.v_px == 16.0
    assert shifted.status is ProjectionStatus.OUT_OF_IMAGE

    behind = project_camera_point((0.0, 0.0, -1.0), sensor)
    assert behind.u_px is None
    assert behind.v_px is None
    assert behind.status is ProjectionStatus.BEHIND_CAMERA
    on_plane = project_camera_point((0.0, 0.0, 0.0), sensor)
    assert on_plane.u_px is None
    assert on_plane.status is ProjectionStatus.BEHIND_CAMERA

    unsized = project_camera_point((0.0, 0.0, 10.0), _pinhole(width=None, height=None))
    assert unsized.u_px == 5.0
    assert unsized.v_px == 6.0
    assert unsized.status is ProjectionStatus.NO_IMAGE_SIZE


def test_box_corner_projects_with_half_extents() -> None:
    scene = _scene(_pinhole())
    projected = project_box(
        scene,
        _world_box((0.0, 0.0, 10.0), (2.0, 4.0, 6.0)),
        "cam",
        "cam",
        max_gap_us=0,
    )
    corner = projected.corners[0]
    assert corner.x_m == pytest.approx(-1.0)
    assert corner.y_m == pytest.approx(-2.0)
    assert corner.z_m == pytest.approx(7.0)
    assert corner.u_px == pytest.approx(5.0 - 10.0 / 7.0)
    assert corner.v_px == pytest.approx(6.0 - 40.0 / 7.0)
    assert corner.status is ProjectionStatus.IN_IMAGE
    assert projected.center.u_px == 5.0
    assert projected.center.v_px == 6.0
    assert projected.any_in_image is True


def test_box_behind_the_camera_and_a_box_split_by_the_plane() -> None:
    scene = _scene(_pinhole())
    behind = project_box(
        scene,
        _world_box((0.0, 0.0, -5.0), (2.0, 4.0, 6.0)),
        "cam",
        "cam",
        max_gap_us=0,
    )
    assert behind.center.status is ProjectionStatus.BEHIND_CAMERA
    assert all(corner.status is ProjectionStatus.BEHIND_CAMERA for corner in behind.corners)
    assert all(corner.u_px is None for corner in behind.corners)
    assert behind.any_in_image is False

    split = project_box(
        scene,
        _world_box((0.0, 0.0, 1.0), (0.0, 0.0, 4.0)),
        "cam",
        "cam",
        max_gap_us=0,
    )
    assert split.center.status is ProjectionStatus.IN_IMAGE
    assert sum(corner.z_m < 0.0 for corner in split.corners) == 4
    assert sum(corner.status is ProjectionStatus.BEHIND_CAMERA for corner in split.corners) == 4
    assert sum(corner.status is ProjectionStatus.IN_IMAGE for corner in split.corners) == 4
    front = next(corner for corner in split.corners if corner.z_m > 0.0)
    assert front.z_m == pytest.approx(3.0)
    assert front.u_px == 5.0
    assert front.v_px == 6.0
    assert split.any_in_image is True


def test_extrinsic_shifts_the_camera_and_a_far_time_is_refused() -> None:
    sensor = _pinhole(
        width=1280,
        height=720,
        fx=1000.0,
        fy=1000.0,
        cx=640.0,
        cy=360.0,
        extrinsic=SE3.identity().translation(1.2, 0.0, 1.4),
    )
    scene = _scene(sensor)
    projected = project_box(
        scene,
        _world_box((1.2, 0.0, 11.4), (2.0, 0.0, 0.0)),
        "cam",
        "cam",
        max_gap_us=0,
    )
    assert projected.center.x_m == pytest.approx(0.0)
    assert projected.center.z_m == pytest.approx(10.0)
    assert projected.center.u_px == 640.0
    assert projected.center.v_px == 360.0
    assert projected.center.status is ProjectionStatus.IN_IMAGE
    xs = sorted(corner.u_px for corner in projected.corners if corner.u_px is not None)
    assert xs == pytest.approx([540.0, 540.0, 540.0, 540.0, 740.0, 740.0, 740.0, 740.0])

    plane = project_box(
        scene,
        _world_box((1.2, 0.0, 1.4), (0.2, 0.2, 0.2)),
        "cam",
        "cam",
        max_gap_us=0,
    )
    assert plane.center.z_m == pytest.approx(0.0)
    assert plane.center.status is ProjectionStatus.BEHIND_CAMERA
    assert plane.center.u_px is None

    with pytest.raises(ExtrapolationError):
        project_box(
            scene,
            _world_box((1.2, 0.0, 11.4), (2.0, 0.0, 0.0)),
            "cam",
            "cam",
            max_gap_us=0,
            camera_time_us=1_000_000,
        )
    with pytest.raises(ValueError):
        project_camera_point(
            (0.0, 0.0, 1.0),
            Sensor("cam", "cam", Modality.CAMERA, SE3.identity()),
        )


def test_body_frame_box_uses_the_agent_pose() -> None:
    scene = _scene(_pinhole(), translation=(10.0, 0.0, 0.0))
    body_box = Annotation(
        "cam",
        0,
        None,
        "car",
        (0.0, 0.0, 10.0),
        (0.0, 0.0, 0.0),
        SE3.identity(),
        None,
        BoxFrame.BODY,
    )
    projected = project_box(scene, body_box, "cam", "cam", max_gap_us=0)
    assert projected.center.x_m == pytest.approx(0.0)
    assert projected.center.y_m == pytest.approx(0.0)
    assert projected.center.z_m == pytest.approx(10.0)
    assert projected.center.u_px == 5.0
    assert projected.center.v_px == 6.0
    assert projected.center.status is ProjectionStatus.IN_IMAGE
