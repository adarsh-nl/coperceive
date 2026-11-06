# SPDX-License-Identifier: Apache-2.0
"""The OpenCOOD plugin's pure conversions: ADR 0012 inverse, x_to_world, input and box mapping."""

from __future__ import annotations

import math
import struct
from pathlib import Path
from typing import Any, cast

import pytest

from tests.opencood_plugin import load

convert = load("convert")
batch = load("batch")

MIRROR = ((1.0, 0.0, 0.0), (0.0, -1.0, 0.0), (0.0, 0.0, 1.0))


def _pose(quat: list[float], translation: tuple[float, float, float]) -> dict[str, float]:
    return {
        "qw": quat[0],
        "qx": quat[1],
        "qy": quat[2],
        "qz": quat[3],
        "tx": translation[0],
        "ty": translation[1],
        "tz": translation[2],
    }


def _mirror(rotation: list[list[float]]) -> list[list[float]]:
    """``M R M`` for ``M = diag(1, -1, 1)``, written out by hand."""
    return [[MIRROR[i][i] * rotation[i][j] * MIRROR[j][j] for j in range(3)] for i in range(3)]


def _apply(matrix: list[list[float]], point: tuple[float, float, float]) -> tuple[float, ...]:
    return tuple(sum(matrix[i][j] * point[j] for j in range(3)) for i in range(3))


POSES = [
    (0.0, 0.0, 0.0),
    (0.0, 90.0, 0.0),
    (0.0, -32.0, 0.0),
    (2.0, 45.0, 5.0),
    (-7.5, 170.0, -12.0),
]


@pytest.mark.parametrize(("roll", "yaw", "pitch"), POSES)
def test_carla_pose_is_the_inverse_adr_reflection(roll: float, yaw: float, pitch: float) -> None:
    """schema pose -> CARLA pose -> x_to_world equals ``M R_schema M`` by hand."""
    quat = convert.quaternion_from_rpy_deg(roll, pitch, yaw)
    schema_rotation = convert.rotation_matrix(*quat)
    pose = _pose(quat, (10.0, 20.0, 1.9))
    carla = convert.carla_pose_from_schema(pose)
    assert carla[0] == pytest.approx(10.0) and carla[1] == pytest.approx(-20.0)
    assert carla[2] == pytest.approx(1.9)
    matrix = convert.carla_x_to_world(carla)
    expected = _mirror(schema_rotation)
    for row in range(3):
        for column in range(3):
            assert matrix[row][column] == pytest.approx(expected[row][column], abs=1e-9)
    assert [matrix[0][3], matrix[1][3], matrix[2][3]] == pytest.approx([10.0, -20.0, 1.9])


def test_carla_pose_angles_are_roll_minus_yaw_minus_pitch() -> None:
    """The inversion in OpenCOOD's angle convention, on a pose with all three angles."""
    pose = _pose(convert.quaternion_from_rpy_deg(10.0, 20.0, 30.0), (1.0, 2.0, 3.0))
    only = convert.carla_pose_from_schema(pose)
    assert only[3] == pytest.approx(10.0)  # roll keeps its sign
    assert only[4] == pytest.approx(-30.0)  # yaw changes sign
    assert only[5] == pytest.approx(-20.0)  # pitch changes sign (OpenCOOD's x_to_world)


def test_x_to_world_elementary_rotations() -> None:
    """Hand-checked matrices from the documented convention (degrees, CARLA axes)."""
    yaw90 = convert.carla_x_to_world([0.0, 0.0, 0.0, 0.0, 90.0, 0.0])
    assert _apply([row[:3] for row in yaw90], (1.0, 0.0, 0.0)) == pytest.approx((0.0, 1.0, 0.0))
    pitch30 = convert.carla_x_to_world([0.0, 0.0, 0.0, 0.0, 0.0, 30.0])
    assert _apply([row[:3] for row in pitch30], (1.0, 0.0, 0.0)) == pytest.approx(
        (math.sqrt(3.0) / 2.0, 0.0, 0.5)
    )
    roll90 = convert.carla_x_to_world([0.0, 0.0, 0.0, 90.0, 0.0, 0.0])
    assert _apply([row[:3] for row in roll90], (0.0, 1.0, 0.0)) == pytest.approx((0.0, 0.0, -1.0))


def _write_points(path: Path, rows: list[tuple[float, float, float, float]]) -> None:
    path.write_bytes(struct.pack("<" + "f" * (4 * len(rows)), *[v for row in rows for v in row]))


def test_batch_mapping_round_trip_against_hand_computed_matrices(tmp_path: Path) -> None:
    """A synthetic frame -> base_data_dict with the CARLA poses and transforms by hand."""
    vehicle_pose = _pose(convert.quaternion_from_rpy_deg(0.0, 0.0, 0.0), (10.0, 20.0, 1.9))
    rsu_pose = _pose(convert.quaternion_from_rpy_deg(0.0, 0.0, 90.0), (0.0, 30.0, 4.3))
    (tmp_path / "vehicle.bin").parent.mkdir(parents=True, exist_ok=True)
    _write_points(tmp_path / "vehicle.bin", [(6.0, 4.0, -1.5, 0.0)])  # world (16, 24, 0.4)
    _write_points(tmp_path / "rsu.bin", [(1.0, 0.0, 0.0, 0.5)])
    sensors = [
        {
            "agent_id": "vehicle",
            "sensor_id": "lidar",
            "modality": "lidar",
            "body_from_sensor": {
                "qw": 1.0,
                "qx": 0.0,
                "qy": 0.0,
                "qz": 0.0,
                "tx": 0.0,
                "ty": 0.0,
                "tz": 0.0,
            },
        },
        {
            "agent_id": "-1",
            "sensor_id": "lidar",
            "modality": "lidar",
            "body_from_sensor": {
                "qw": 1.0,
                "qx": 0.0,
                "qy": 0.0,
                "qz": 0.0,
                "tx": 0.0,
                "ty": 0.0,
                "tz": 0.0,
            },
        },
    ]
    points = {
        "vehicle": (str(tmp_path / "vehicle.bin"), "lidar", 1, "xyzi_f32le"),
        "-1": (str(tmp_path / "rsu.bin"), "lidar", 1, "xyzi_f32le"),
    }
    data = batch.build_base_data(
        agents=[
            {"agent_id": "vehicle", "agent_type": "vehicle"},
            {"agent_id": "-1", "agent_type": "infrastructure"},
        ],
        sensors=sensors,
        poses={"vehicle": vehicle_pose, "-1": rsu_pose},
        points_by_agent=points,
        com_range=70.0,
        max_cav=5,
        preferred_ego="vehicle",
    )
    assert data is not None
    assert list(data) == ["0", "-1"]  # ego first, the RSU negative and last
    ego = cast(dict[str, Any], data["0"])
    rsu = cast(dict[str, Any], data["-1"])
    assert ego["ego"] is True and rsu["ego"] is False
    # CARLA poses: the schema translations reflected in y, angles through x_to_world.
    assert ego["params"]["lidar_pose"] == pytest.approx([10.0, -20.0, 1.9, 0.0, 0.0, 0.0])
    assert rsu["params"]["lidar_pose"] == pytest.approx([0.0, -30.0, 4.3, 0.0, -90.0, 0.0])
    # Hand-computed x1_to_x2(rsu, vehicle): Rz(-90) and translation (-10, -10, 2.4).
    transform = rsu["params"]["transformation_matrix"]
    expected = [
        [0.0, 1.0, 0.0, -10.0],
        [-1.0, 0.0, 0.0, -10.0],
        [0.0, 0.0, 1.0, 2.4],
        [0.0, 0.0, 0.0, 1.0],
    ]
    for row in range(4):
        assert transform[row] == pytest.approx(expected[row], abs=1e-9)
    # The RSU point (1, 0, 0) in its own frame lands at carla-ego (-10, -11, 2.4) by hand.
    point = rsu["lidar_np"][0]
    assert point == pytest.approx([1.0, 0.0, 0.0, 0.5])
    projected = [
        sum(transform[i][j] * [1.0, 0.0, 0.0, 1.0][j] for j in range(3)) + transform[i][3]
        for i in range(3)
    ]
    assert projected == pytest.approx([-10.0, -11.0, 2.4], abs=1e-9)
    # The schema point (6, 4, -1.5) of the ego sweep has its schema y negated in the batch.
    assert ego["lidar_np"][0] == pytest.approx([6.0, -4.0, -1.5, 0.0])
    assert ego["params"]["vehicles"] == {}  # ground truth is never part of the batch


def test_batch_limits_cavs_by_range_and_max_cav(tmp_path: Path) -> None:
    far = _pose(convert.quaternion_from_rpy_deg(0.0, 0.0, 0.0), (500.0, 0.0, 0.0))
    near = _pose(convert.quaternion_from_rpy_deg(0.0, 0.0, 0.0), (5.0, 0.0, 0.0))
    middle = _pose(convert.quaternion_from_rpy_deg(0.0, 0.0, 0.0), (10.0, 0.0, 0.0))
    for name in ("ego", "near", "middle", "far"):
        _write_points(tmp_path / f"{name}.bin", [(0.0, 0.0, 0.0, 0.0)])
    agents = [
        {"agent_id": "ego", "agent_type": "vehicle"},
        {"agent_id": "near", "agent_type": "vehicle"},
        {"agent_id": "middle", "agent_type": "vehicle"},
        {"agent_id": "far", "agent_type": "vehicle"},
    ]
    sensors = [
        {
            "agent_id": agent["agent_id"],
            "sensor_id": "lidar",
            "modality": "lidar",
            "body_from_sensor": {
                "qw": 1.0,
                "qx": 0.0,
                "qy": 0.0,
                "qz": 0.0,
                "tx": 0.0,
                "ty": 0.0,
                "tz": 0.0,
            },
        }
        for agent in agents
    ]
    points = {
        name: (str(tmp_path / f"{name}.bin"), "lidar", 1, "xyzi_f32le")
        for name in ("ego", "near", "middle", "far")
    }
    poses = {"ego": near, "near": near, "middle": middle, "far": far}
    data = batch.build_base_data(
        agents=agents,
        sensors=sensors,
        poses=poses,
        points_by_agent=points,
        com_range=70.0,
        max_cav=2,
        preferred_ego="ego",
    )
    assert data is not None and list(data) == ["0", "1"]  # ego + the nearest CAV, not four
    assert cast(dict[str, Any], data["1"])["params"]["lidar_pose"][0] == pytest.approx(5.0)
    # A frame without the ego sweep yields no batch at all: no boxes for another agent.
    assert (
        batch.build_base_data(
            agents=agents,
            sensors=sensors,
            poses={"near": near},
            points_by_agent={"near": points["near"]},
            com_range=70.0,
            max_cav=5,
            preferred_ego="ego",
        )
        is None
    )


def test_points_in_body_applies_the_sensor_extrinsic() -> None:
    rows = [[1.0, 0.0, 0.0, 0.25]]
    moved = batch.points_in_body(rows, [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.5], "lidar")
    assert moved == [[1.0, 0.0, 1.5, 0.25]]
    yawed = batch.points_in_body(
        rows, [math.cos(math.pi / 4), 0.0, 0.0, math.sin(math.pi / 4), 0.0, 0.0, 0.0], "lidar"
    )
    assert yawed[0][:3] == pytest.approx([0.0, 1.0, 0.0], abs=1e-12)
    assert batch.points_in_body(rows, [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0], "lidar") is rows


# -- box conversion ------------------------------------------------------------------------


def test_centre_box_in_hwl_and_lwh_orders() -> None:
    """The 7-vector yaw is radians (upstream VoxelPostprocessor); we publish degrees."""
    yaw = math.radians(90.0)
    lwh = convert.boxes_from_opencood([[8.0, 2.0, -1.5, 4.0, 2.0, 1.5, yaw]], "lwh", [0.9])
    hwl = convert.boxes_from_opencood([[8.0, 2.0, -1.5, 1.5, 2.0, 4.0, yaw]], "hwl", [0.9])
    for parsed in (lwh[0], hwl[0]):
        assert parsed["center"] == pytest.approx([8.0, 2.0, -1.5])
        assert parsed["size_lwh"] == pytest.approx([4.0, 2.0, 1.5])
        assert parsed["heading"] == pytest.approx(90.0)
        assert parsed["score"] == pytest.approx(0.9)
    # 45° as radians -> 45° out; a raw degree value would have been misread.
    parsed = convert.boxes_from_opencood(
        [[0.0, 0.0, 0.0, 4.0, 2.0, 1.5, math.radians(45.0)]], "lwh", [0.5]
    )[0]
    assert parsed["heading"] == pytest.approx(45.0)


@pytest.mark.parametrize("order", ["lwh", "hwl"])
def test_corner_box_round_trip_keeps_positive_volume(order: str) -> None:
    """The corner template is always l, w, h, so the parser is order-independent.

    Upstream ``boxes_to_corners_3d`` reorders the 7-vector for ``hwl`` and then always uses
    the l, w, h corner template, and ``corner_to_center`` takes the two horizontal edges and
    the height from z for both orders. ``postprocess.order`` therefore cannot swap length
    and height on the corner path.
    """
    center = (8.0, 2.0, -1.5)
    size = (4.0, 2.0, 1.5)
    corners = convert.opencood_corners(center, size, 30.0)
    for some_order in ("lwh", "hwl"):
        parsed = convert.boxes_from_opencood([corners], some_order, [0.5])[0]
        assert parsed["center"] == pytest.approx(center, abs=1e-12)
        assert parsed["size_lwh"] == pytest.approx(size, abs=1e-12)
        assert parsed["heading"] == pytest.approx(30.0, abs=1e-9)
        length, width, height = cast(list[float], parsed["size_lwh"])
        assert length > 0.0 and width > 0.0 and height > 0.0
        assert length * width * height == pytest.approx(4.0 * 2.0 * 1.5)


def _upstream_hwl_corners(box7: list[float]) -> list[list[float]]:
    """A transcription of upstream ``boxes_to_corners_3d`` for a hwl 7-vector.

    ``box7`` is ``[x, y, z, h, w, l, yaw]``; upstream reorders it with
    ``[:, [0, 1, 2, 5, 4, 3, 6]]`` to ``[x, y, z, l, w, h, yaw]`` and then uses the l, w, h
    template, ``Rz(yaw)`` and the centre. Written out here so a test does not depend on the
    plugin's own corner helper.
    """
    x, y, z, height, width, length, yaw = box7
    cy, sy = math.cos(math.radians(yaw)), math.sin(math.radians(yaw))
    signs = (
        (1.0, -1.0, -1.0),
        (1.0, 1.0, -1.0),
        (-1.0, 1.0, -1.0),
        (-1.0, -1.0, -1.0),
        (1.0, -1.0, 1.0),
        (1.0, 1.0, 1.0),
        (-1.0, 1.0, 1.0),
        (-1.0, -1.0, 1.0),
    )
    out: list[list[float]] = []
    for sx, sy_, sz in signs:
        lx, ly, lz = sx * length / 2.0, sy_ * width / 2.0, sz * height / 2.0
        out.append([x + cy * lx - sy * ly, y + sy * lx + cy * ly, z + lz])
    return out


def test_hwl_corners_carry_length_and_height_apart() -> None:
    """The audit bug: hwl must not publish size_lwh=(h, w, l) (a 1.56 m long, 3.9 m high car)."""
    # A long, low car in upstream's hwl 7-vector order [x, y, z, h, w, l, yaw].
    corners = _upstream_hwl_corners([8.0, 2.0, -1.5, 1.5, 2.0, 4.0, 90.0])
    center, size, heading = convert.center_from_corners(corners)
    assert center == pytest.approx([8.0, 2.0, -1.5], abs=1e-12)
    assert size == pytest.approx([4.0, 2.0, 1.5], abs=1e-12)
    assert heading == pytest.approx(90.0, abs=1e-9)
    parsed = convert.boxes_from_opencood([corners], "hwl", [0.9])[0]
    assert cast(list[float], parsed["size_lwh"]) == pytest.approx([4.0, 2.0, 1.5])
    # The 7-vector path keeps the order mapping: the same box read as 'lwh' is h=1.5, w=2,
    # l=4 in the vector, so its size_lwh is (1.5, 2.0, 4.0) -- that path is order-dependent.
    from_centre7 = convert.boxes_from_opencood([[8.0, 2.0, -1.5, 1.5, 2.0, 4.0, 90.0]], "hwl")
    assert cast(list[float], from_centre7[0]["size_lwh"]) == pytest.approx([4.0, 2.0, 1.5])


def test_corner_box_degenerate_is_refused() -> None:
    flat = [[0.0, 0.0, 0.0]] * 8
    with pytest.raises(convert.ConvertError, match="degenerate|zero"):
        convert.boxes_from_opencood([flat], "lwh", None)


def test_reflection_flips_the_ego_frame_box() -> None:
    """A CARLA-ego box at y=+2, yaw 90 becomes schema y=-2, world yaw -90."""
    identity_ego = _pose([1.0, 0.0, 0.0, 0.0], (0.0, 0.0, 0.0))
    box = convert.box_to_protocol(
        center=[8.0, 2.0, -1.5],
        size_lwh=[4.0, 2.0, 1.5],
        heading_deg=90.0,
        ego_pose=identity_ego,
        agent_id="vehicle",
        source="fused",
        category="vehicle",
        score=0.9,
    )
    assert box["center"] == pytest.approx([8.0, -2.0, -1.5])
    assert box["frame"] == "world"
    quaternion = cast(list[float], box["quaternion"])
    forward = convert.quaternion_rotate(quaternion, (1.0, 0.0, 0.0))
    assert forward == pytest.approx([0.0, -1.0, 0.0], abs=1e-12)  # yaw -90: +x -> -y


def _yaw_deg(quaternion: list[float]) -> float:
    """World yaw in degrees from a protocol ``[qw, qx, qy, qz]`` box rotation."""
    qw, qx, qy, qz = quaternion
    return math.degrees(math.atan2(2.0 * (qw * qz + qx * qy), 1.0 - 2.0 * (qy * qy + qz * qz)))


def test_reviewer_hand_computation_ego_yaw_30_box_yaw_45() -> None:
    """The reviewer's numeric case, hand-calculated: p_scene = T_ego·S·p_ego, yaw -= box yaw."""
    ego = _pose(convert.quaternion_from_rpy_deg(0.0, 0.0, 30.0), (10.0, 20.0, 1.9))
    box = convert.box_to_protocol(
        center=[0.0, 2.0, 0.5],  # +y in the CARLA ego frame
        size_lwh=[4.0, 2.0, 1.5],
        heading_deg=45.0,
        ego_pose=ego,
        agent_id="vehicle",
        source="fused",
        category="vehicle",
        score=0.9,
    )
    # S·(0,2,0.5) = (0,-2,0.5); Rz(30°)·(0,-2) = (1,-√3); +(10,20,1.9).
    expected_center = [11.0, 20.0 - 2.0 * math.cos(math.radians(30.0)), 2.4]
    assert expected_center == pytest.approx([11.0, 18.267949192431123, 2.4], abs=1e-12)
    assert box["center"] == pytest.approx(expected_center, abs=1e-12)
    # yaw_world = ego_yaw_schema - box_yaw_carla = 30 - 45.
    assert _yaw_deg(cast(list[float], box["quaternion"])) == pytest.approx(-15.0, abs=1e-9)


def test_scene_ego_scene_round_trip_with_explicit_inverse() -> None:
    """Invert the documented map by hand: p_ego = S·(T_scene_ego^-1·p_scene)."""
    ego_pose = _pose(convert.quaternion_from_rpy_deg(0.0, 0.0, 30.0), (10.0, 20.0, 1.9))
    ego_yaw = 30.0
    ego_t = (10.0, 20.0, 1.9)
    scene_center = [12.5, 17.0, 2.4]
    scene_yaw = -15.0
    size = [4.0, 2.0, 1.5]
    # T_scene_ego^-1: rotate into the ego frame (Rz(-ego_yaw)), then subtract the translation.
    dx, dy, dz = scene_center[0] - ego_t[0], scene_center[1] - ego_t[1], scene_center[2] - ego_t[2]
    theta = math.radians(-ego_yaw)
    ex = math.cos(theta) * dx - math.sin(theta) * dy
    ey = math.sin(theta) * dx + math.cos(theta) * dy
    # S: the OpenCOOD ego frame is the mirror of the schema ego frame.
    ego_center = [ex, -ey, dz]
    ego_yaw_carla = ego_yaw - scene_yaw  # inverse of yaw_world = ego_yaw - box_yaw
    assert ego_yaw_carla == pytest.approx(45.0)
    box = convert.box_to_protocol(
        center=ego_center,
        size_lwh=size,
        heading_deg=ego_yaw_carla,
        ego_pose=ego_pose,
        agent_id="vehicle",
        source="fused",
        category="vehicle",
        score=0.7,
    )
    assert box["center"] == pytest.approx(scene_center, abs=1e-12)
    assert cast(list[float], box["size_lwh"]) == pytest.approx(size)
    assert _yaw_deg(cast(list[float], box["quaternion"])) == pytest.approx(scene_yaw, abs=1e-9)


def test_schema_yaw_90_maps_body_x_to_world_y() -> None:
    """The schema convention itself: a world yaw of +90 sends body +x to world +y."""
    quaternion = convert.quaternion_from_rpy_deg(0.0, 0.0, 90.0)
    assert convert.quaternion_rotate(quaternion, (1.0, 0.0, 0.0)) == pytest.approx(
        [0.0, 1.0, 0.0], abs=1e-12
    )
    # ... and that is a CARLA heading of -90 once the prediction box is converted.
    box = convert.box_to_protocol(
        center=[0.0, 0.0, 0.0],
        size_lwh=[4.0, 2.0, 1.5],
        heading_deg=-90.0,
        ego_pose=_pose([1.0, 0.0, 0.0, 0.0], (0.0, 0.0, 0.0)),
        agent_id="vehicle",
        source="fused",
        category="vehicle",
        score=0.5,
    )
    rotated = convert.quaternion_rotate(cast(list[float], box["quaternion"]), (1.0, 0.0, 0.0))
    assert rotated == pytest.approx([0.0, 1.0, 0.0], abs=1e-12)


def test_box_centre_is_taken_through_the_ego_pose() -> None:
    """A box in the CARLA ego frame goes through schema ego -> world with the ego pose."""
    ego = _pose(convert.quaternion_from_rpy_deg(0.0, 0.0, 90.0), (10.0, 20.0, 0.0))
    box = convert.box_to_protocol(
        center=[2.0, 0.0, 0.0],  # 2 m ahead in CARLA-ego x
        size_lwh=[4.0, 2.0, 1.5],
        heading_deg=0.0,
        ego_pose=ego,
        agent_id="vehicle",
        source="fused",
        category="vehicle",
        score=1.0,
    )
    # Schema ego: (2, 0, 0) rotated by +90 -> world (10, 22, 0), i.e. +y for schema yaw 90.
    assert box["center"] == pytest.approx([10.0, 22.0, 0.0], abs=1e-12)


def test_boxes_from_opencood_empty_and_bad_shapes() -> None:
    assert convert.boxes_from_opencood([], "lwh", []) == []
    with pytest.raises(convert.ConvertError, match="7 .*8|corners"):
        convert.boxes_from_opencood([[1.0, 2.0, 3.0]], "lwh", None)
    with pytest.raises(convert.ConvertError, match="order"):
        convert.boxes_from_opencood([[1.0] * 7], "nope", None)
