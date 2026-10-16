# SPDX-License-Identifier: Apache-2.0
"""Distance from vehicle poses and boxes to a shared map."""

from __future__ import annotations

import math
from dataclasses import dataclass

from coperceive.diagnostics import annotation_center_world
from coperceive.hdmap.index import SegmentIndex
from coperceive.hdmap.opendrive import LAYER_LANE_CENTRE, LAYER_REFERENCE
from coperceive.hdmap.store import HdMap
from coperceive.model import AgentType, Scene
from coperceive.se3 import SE3

_CLAIM_M = 0.24


def yaw_deg(pose: SE3) -> float:
    """Yaw of ``R = Rz(yaw) @ Ry(pitch) @ Rx(roll)``, degrees, range (-180, 180]."""
    r00 = 1.0 - 2.0 * (pose.qy * pose.qy + pose.qz * pose.qz)
    r10 = 2.0 * (pose.qx * pose.qy + pose.qw * pose.qz)
    return math.degrees(math.atan2(r10, r00))


def heading_error_deg(yaw: float, travel_rad: float) -> float:
    """Absolute difference in degrees, folded into [0, 180]."""
    delta = (yaw - math.degrees(travel_rad) + 180.0) % 360.0 - 180.0
    return abs(delta)


@dataclass(frozen=True)
class CentrelineSample:
    pose_lane_m: tuple[float, ...]
    pose_reference_m: tuple[float, ...]
    box_lane_m: tuple[float, ...]
    heading_error_deg: tuple[float, ...]
    pose_z_m: tuple[float, ...]


def collect_centreline(
    scene: Scene, lanes: SegmentIndex, reference: SegmentIndex
) -> CentrelineSample:
    """Vehicle poses and vehicle boxes against indexes already in the scene world."""
    pose_lane: list[float] = []
    pose_reference: list[float] = []
    headings: list[float] = []
    pose_z: list[float] = []
    for agent in scene.agents:
        if agent.agent_type is not AgentType.VEHICLE:
            continue
        for stamped in agent.poses:
            pose = stamped.world_from_body
            pose_z.append(pose.tz)
            lane_hit = lanes.nearest(pose.tx, pose.ty)
            ref_hit = reference.nearest(pose.tx, pose.ty)
            if lane_hit is not None:
                pose_lane.append(lane_hit.distance_m)
                headings.append(heading_error_deg(yaw_deg(pose), lane_hit.travel_heading_rad))
            if ref_hit is not None:
                pose_reference.append(ref_hit.distance_m)
    box_lane: list[float] = []
    vehicle_ids = {
        agent.agent_id for agent in scene.agents if agent.agent_type is AgentType.VEHICLE
    }
    for ann in scene.annotations:
        if ann.agent_id not in vehicle_ids:
            continue
        center = annotation_center_world(scene, ann, max_gap_us=0)
        hit = lanes.nearest(center[0], center[1])
        if hit is not None:
            box_lane.append(hit.distance_m)
    return CentrelineSample(
        tuple(pose_lane),
        tuple(pose_reference),
        tuple(box_lane),
        tuple(headings),
        tuple(pose_z),
    )


def centreline_numbers(scene: Scene, hdmap: HdMap) -> dict[str, object]:
    """Horizontal distances for the vehicle agent.

    Lane centre is the centre of each ``driving`` lane. Reference is the
    OpenDRIVE plan-view line, with no lane offset. Both are in the scene world
    after ``world_from_opendrive``. Box centres use the same lane-centre index.
    """
    lanes, reference = indexes_for(hdmap)
    sample = collect_centreline(scene, lanes, reference)
    return sample_as_json(hdmap, sample)


def indexes_for(hdmap: HdMap) -> tuple[SegmentIndex, SegmentIndex]:
    world = hdmap.world_polylines(frozenset({LAYER_LANE_CENTRE, LAYER_REFERENCE}))
    lanes = SegmentIndex(tuple(item for item in world if item.layer == LAYER_LANE_CENTRE))
    reference = SegmentIndex(tuple(item for item in world if item.layer == LAYER_REFERENCE))
    return lanes, reference


def sample_as_json(hdmap: HdMap, sample: CentrelineSample) -> dict[str, object]:
    from coperceive.validate import distribution

    lane_dist = distribution(list(sample.pose_lane_m))
    return {
        "status": "measured",
        "map_name": hdmap.map_name,
        "alignment_measured": hdmap.world_from_opendrive.measured,
        "sample_step_m": hdmap.sample_step_m,
        "vehicle_pose_to_lane_centre_m": lane_dist.as_json(),
        "vehicle_pose_to_reference_m": distribution(list(sample.pose_reference_m)).as_json(),
        "vehicle_box_to_lane_centre_m": distribution(list(sample.box_lane_m)).as_json(),
        "vehicle_pose_heading_error_deg": distribution(list(sample.heading_error_deg)).as_json(),
        "vehicle_pose_z_m": distribution(list(sample.pose_z_m)).as_json(),
        "claim_m": _CLAIM_M,
        "lane_centre_median_minus_claim_m": (
            None if lane_dist.median is None else lane_dist.median - _CLAIM_M
        ),
        "claim_note": (
            "0.24 m is the Town03 road-centreline figure recorded in "
            "docs/design/griffin-verification.md before this map check. "
            "lane_centre_median_minus_claim_m is the measured lane-centre median "
            "minus that figure, not a pass/fail gate."
        ),
    }
