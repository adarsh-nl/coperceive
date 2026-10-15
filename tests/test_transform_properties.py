# SPDX-License-Identifier: Apache-2.0
"""Hypothesis checks for SE3 and T(dst ← src): composition, inversion, round-trip."""

from __future__ import annotations

import math

from coperceive.errors import ExtrapolationError
from coperceive.model import (
    SCHEMA_VERSION,
    Agent,
    AgentType,
    Clock,
    Modality,
    Scene,
    Sensor,
    StampedPose,
)
from coperceive.se3 import SE3, slerp
from coperceive.transform import body_frame, sensor_frame, transform, world_frame
from hypothesis import given, settings
from hypothesis import strategies as st

_unit = st.floats(min_value=-1.0, max_value=1.0, allow_nan=False, allow_infinity=False)
_metres = st.floats(min_value=-50.0, max_value=50.0, allow_nan=False, allow_infinity=False)


def _pack(parts: tuple[tuple[float, float, float, float], tuple[float, float, float]]) -> SE3:
    quat, translation = parts
    norm = math.sqrt(sum(component * component for component in quat))
    return SE3(
        quat[0] / norm,
        quat[1] / norm,
        quat[2] / norm,
        quat[3] / norm,
        translation[0],
        translation[1],
        translation[2],
    )


_se3 = st.tuples(
    st.tuples(_unit, _unit, _unit, _unit).filter(
        lambda quat: sum(component * component for component in quat) > 1e-6
    ),
    st.tuples(_metres, _metres, _metres),
).map(_pack)


@settings(max_examples=40, deadline=None)
@given(pose=_se3, point=st.tuples(_metres, _metres, _metres))
def test_inverse_and_point_round_trip(pose: SE3, point: tuple[float, float, float]) -> None:
    assert pose.inverse().inverse().almost_equal(pose, abs_tol=1e-6)
    assert pose.compose(pose.inverse()).almost_equal(SE3.identity(), abs_tol=1e-6)
    assert pose.inverse().compose(pose).almost_equal(SE3.identity(), abs_tol=1e-6)
    back = pose.inverse().apply(pose.apply(point))
    assert math.isclose(back[0], point[0], abs_tol=1e-5)
    assert math.isclose(back[1], point[1], abs_tol=1e-5)
    assert math.isclose(back[2], point[2], abs_tol=1e-5)


@settings(max_examples=40, deadline=None)
@given(a=_se3, b=_se3, c=_se3)
def test_composition_is_associative(a: SE3, b: SE3, c: SE3) -> None:
    left = a.compose(b).compose(c)
    right = a.compose(b.compose(c))
    assert left.almost_equal(right, abs_tol=1e-5)


@settings(max_examples=20, deadline=None)
@given(a=_se3, b=_se3)
def test_slerp_endpoints_stay_on_samples(a: SE3, b: SE3) -> None:
    assert slerp(a, b, 0.0).almost_equal(a)
    assert slerp(a, b, 1.0).almost_equal(b)
    try:
        slerp(a, b, 1.01)
    except ExtrapolationError:
        pass
    else:
        raise AssertionError("slerp extrapolated")


@settings(max_examples=25, deadline=None)
@given(
    poses=st.tuples(_se3, _se3, _se3),
    extrinsics=st.tuples(_se3, _se3, _se3),
    point=st.tuples(_metres, _metres, _metres),
)
def test_frame_composition_inversion_and_round_trip(
    poses: tuple[SE3, SE3, SE3],
    extrinsics: tuple[SE3, SE3, SE3],
    point: tuple[float, float, float],
) -> None:
    agents: list[Agent] = []
    sensors: list[Sensor] = []
    for index, pose in enumerate(poses):
        agent_id = f"a{index}"
        agents.append(
            Agent(
                agent_id=agent_id,
                agent_type=AgentType.OTHER,
                poses=(StampedPose(0, pose),),
            )
        )
        sensors.append(
            Sensor(
                sensor_id=f"s{index}",
                agent_id=agent_id,
                modality=Modality.LIDAR,
                body_from_sensor=extrinsics[index],
            )
        )
    scene = Scene(
        schema_version=SCHEMA_VERSION,
        scene_id="property",
        world_frame="world",
        clock=Clock(unit="us", scale_note="property test"),
        agents=tuple(agents),
        sensors=tuple(sensors),
        annotations=(),
        objects=(),
    )
    t_ab = transform(scene, body_frame("a0"), body_frame("a1"), 0, max_gap_us=0)
    t_bc = transform(scene, body_frame("a1"), body_frame("a2"), 0, max_gap_us=0)
    t_ac = transform(scene, body_frame("a0"), body_frame("a2"), 0, max_gap_us=0)
    assert t_ab.compose(t_bc).almost_equal(t_ac, abs_tol=1e-5)
    t_ba = transform(scene, body_frame("a1"), body_frame("a0"), 0, max_gap_us=0)
    assert t_ba.almost_equal(t_ab.inverse(), abs_tol=1e-5)
    world_from_sensor = transform(scene, world_frame(), sensor_frame("a0", "s0"), 0, max_gap_us=0)
    world_from_body = transform(scene, world_frame(), body_frame("a0"), 0, max_gap_us=0)
    body_from_sensor = transform(scene, body_frame("a0"), sensor_frame("a0", "s0"), 0, max_gap_us=0)
    assert world_from_body.compose(body_from_sensor).almost_equal(world_from_sensor, abs_tol=1e-5)
    back = t_ac.inverse().apply(t_ac.apply(point))
    assert math.isclose(back[0], point[0], abs_tol=1e-4)
    assert math.isclose(back[1], point[1], abs_tol=1e-4)
    assert math.isclose(back[2], point[2], abs_tol=1e-4)
