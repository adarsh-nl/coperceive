# SPDX-License-Identifier: Apache-2.0
# pyright: reportUnknownMemberType=false
"""The conformance checks (``docs/adr/0017-geometry-conformance.md`` §4).

Given the world an exporter wrote, the scene an adapter made of it and an
:class:`Expectation` that says which scene id stands for which world id, each
check returns the cases that fail as :class:`Finding` rows. Nothing is compared
in a dataset's own convention: every quantity is taken to the world frame (or to
pixels) through the *imported* poses and compared with what the generator knows.

Tolerances are the ones of the task: 1 cm and 0.1° for a pose, 1 px for a
projected corner, 95 % of an object's returns inside its box, 2 cm between
agents. Point clouds are stored as float32, so a point 150 m from its sensor is
only known to about 1e-5 m; that is far inside every tolerance here.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from coperceive.model import Annotation, BoxFrame, Modality, Scene
from coperceive.se3 import SE3

from tests.geomworld.expectation import Expectation
from tests.geomworld.world import CORNER_SIGNS, Array, Pose, World, WorldObject, inside_box

POSITION_TOL_M = 0.01
ANGLE_TOL_DEG = 0.1
SIZE_TOL_M = 0.001
PIXEL_TOL = 1.0
INSIDE_FRACTION = 0.95
INSIDE_MARGIN_M = 0.01
REGISTRATION_TOL_M = 0.02
MIN_POINTS = 10
# A corner closer to the image plane than this, or projected this far outside the
# image, turns a millimetre into pixels; such corners are left out of the pixel check.
MIN_DEPTH_M = 1.0
MAX_PIXEL = 20_000.0


@dataclass(frozen=True)
class Finding:
    """One failing case: which check, where, what was expected and what came out."""

    check: str
    agent_id: str
    frame: int
    subject: str
    expected: str
    got: str

    def __str__(self) -> str:
        return (
            f"[{self.check}] agent {self.agent_id} frame {self.frame} {self.subject}: "
            f"expected {self.expected}, got {self.got}"
        )


# --- reading the scene ----------------------------------------------------------------


def pose_of(transform: SE3) -> Pose:
    """An ``SE3`` of the scene as a rotation matrix and a translation."""
    w, x, y, z = transform.qw, transform.qx, transform.qy, transform.qz
    norm = math.sqrt(w * w + x * x + y * y + z * z)
    w, x, y, z = w / norm, x / norm, y / norm, z / norm
    rotation = np.array(
        [
            [1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
            [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
            [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)],
        ]
    )
    return Pose(rotation, np.array([transform.tx, transform.ty, transform.tz]))


def frame_times(scene: Scene, agent_id: str) -> tuple[int, ...]:
    """The pose times of a scene agent, ascending: frame ``i`` of the world is entry ``i``."""
    return scene.pose_times(agent_id)


def world_from_body(scene: Scene, agent_id: str, frame: int) -> Pose:
    return pose_of(scene.agent(agent_id).poses[frame].world_from_body)


def world_from_sensor(scene: Scene, agent_id: str, sensor_id: str, frame: int) -> Pose:
    sensor = scene.sensor(agent_id, sensor_id)
    return world_from_body(scene, agent_id, frame).compose(pose_of(sensor.body_from_sensor))


def cloud_in_sensor(scene: Scene, agent_id: str, sensor_id: str, time_us: int) -> Array | None:
    """The points of one sweep in the sensor frame, ``(N, 3)`` float64, or ``None``."""
    for cloud in scene.point_clouds:
        if (cloud.agent_id, cloud.sensor_id, cloud.time_us) == (agent_id, sensor_id, time_us):
            flat = np.frombuffer(cloud.xyz_i_f32, dtype=np.float32)
            return flat.reshape(-1, 4)[:, :3].astype(np.float64)
    return None


def box_in_world(scene: Scene, annotation: Annotation, frame: int) -> Pose:
    """``world_from_box`` of an annotation through the imported pose of its agent."""
    in_frame = Pose(pose_of(annotation.box_from_rpy).rotation, np.array(annotation.center))
    if annotation.frame is BoxFrame.WORLD:
        return in_frame
    return world_from_body(scene, annotation.agent_id, frame).compose(in_frame)


def rotation_error_deg(a: Array, b: Array) -> float:
    """The angle of the rotation that takes ``a`` onto ``b``, in degrees."""
    cosine = (float(np.trace(a.T @ b)) - 1.0) / 2.0
    return math.degrees(math.acos(max(-1.0, min(1.0, cosine))))


def _pose_findings(
    check: str, agent_id: str, frame: int, subject: str, truth: Pose, got: Pose
) -> list[Finding]:
    findings: list[Finding] = []
    offset = float(np.linalg.norm(got.translation - truth.translation))
    if offset > POSITION_TOL_M:
        findings.append(
            Finding(
                check,
                agent_id,
                frame,
                f"{subject} position",
                f"{np.round(truth.translation, 3).tolist()} within {POSITION_TOL_M} m",
                f"{np.round(got.translation, 3).tolist()} ({offset:.3f} m off)",
            )
        )
    angle = rotation_error_deg(truth.rotation, got.rotation)
    if angle > ANGLE_TOL_DEG:
        findings.append(
            Finding(
                check,
                agent_id,
                frame,
                f"{subject} rotation",
                f"within {ANGLE_TOL_DEG}°",
                f"{angle:.3f}° off",
            )
        )
    return findings


def _annotations(scene: Scene, agent_id: str, time_us: int) -> dict[str | None, Annotation]:
    return {
        ann.track_id: ann
        for ann in scene.annotations
        if ann.agent_id == agent_id and ann.time_us == time_us
    }


# --- the checks -------------------------------------------------------------------------


def check_frames(world: World, scene: Scene, expectation: Expectation) -> list[Finding]:
    """Every agent is there, with one pose per frame and its sensors."""
    findings: list[Finding] = []
    scene_agents = {agent.agent_id for agent in scene.agents}
    for agent in world.agents:
        scene_id = expectation.agents[agent.agent_id]
        if scene_id not in scene_agents:
            findings.append(Finding("frames", agent.agent_id, 0, "agent", scene_id, "missing"))
            continue
        count = len(frame_times(scene, scene_id))
        if count != world.n_frames:
            findings.append(
                Finding("frames", agent.agent_id, 0, "pose count", str(world.n_frames), str(count))
            )
    return findings


def check_sensor_poses(world: World, scene: Scene, expectation: Expectation) -> list[Finding]:
    """LiDAR and camera poses in the world: 1 cm and 0.1°, and the camera intrinsics."""
    findings: list[Finding] = []
    for agent in world.agents:
        scene_id = expectation.agents[agent.agent_id]
        for frame in range(world.n_frames):
            findings += _pose_findings(
                "sensor-pose",
                agent.agent_id,
                frame,
                "lidar",
                world.world_from_lidar(agent.agent_id, frame),
                world_from_sensor(scene, scene_id, expectation.lidars[agent.agent_id], frame),
            )
            for camera in agent.cameras:
                sensor_id = expectation.cameras[(agent.agent_id, camera.sensor_id)]
                findings += _pose_findings(
                    "sensor-pose",
                    agent.agent_id,
                    frame,
                    f"camera {camera.sensor_id}",
                    world.world_from_camera(agent.agent_id, camera.sensor_id, frame),
                    world_from_sensor(scene, scene_id, sensor_id, frame),
                )
        for camera in agent.cameras:
            sensor = scene.sensor(scene_id, expectation.cameras[(agent.agent_id, camera.sensor_id)])
            expected = (camera.fx, camera.fy, camera.cx, camera.cy, camera.width, camera.height)
            got = (
                sensor.fx,
                sensor.fy,
                sensor.cx,
                sensor.cy,
                sensor.image_width_px,
                sensor.image_height_px,
            )
            close = sensor.modality is Modality.CAMERA and all(
                g is not None and abs(float(g) - float(e)) <= 1e-6 * max(1.0, abs(float(e)))
                for g, e in zip(got, expected, strict=True)
            )
            if not close:
                findings.append(
                    Finding(
                        "intrinsics",
                        agent.agent_id,
                        0,
                        f"camera {camera.sensor_id}",
                        str(expected),
                        str(got),
                    )
                )
    return findings


def check_boxes(world: World, scene: Scene, expectation: Expectation) -> list[Finding]:
    """Every listed object is there with its pose (1 cm, 0.1°), size (1 mm) and class."""
    findings: list[Finding] = []
    for agent in world.agents:
        scene_id = expectation.agents[agent.agent_id]
        times = frame_times(scene, scene_id)
        for frame in range(min(world.n_frames, len(times))):
            objects = {obj.object_id: obj for obj in world.objects[frame]}
            imported = _annotations(scene, scene_id, times[frame])
            listed = expectation.annotated[(agent.agent_id, frame)]
            wanted = {expectation.track_ids[object_id] for object_id in listed}
            extra = sorted(str(key) for key in set(imported) - wanted)
            if extra:
                findings.append(
                    Finding("boxes", agent.agent_id, frame, "extra annotations", "none", str(extra))
                )
            for object_id in listed:
                obj = objects[object_id]
                subject = f"object {object_id}"
                annotation = imported.get(expectation.track_ids[object_id])
                if annotation is None:
                    findings.append(
                        Finding("boxes", agent.agent_id, frame, subject, "an annotation", "missing")
                    )
                    continue
                findings += _pose_findings(
                    "boxes",
                    agent.agent_id,
                    frame,
                    subject,
                    obj.world_from_box,
                    box_in_world(scene, annotation, frame),
                )
                size_error = max(
                    abs(a - b) for a, b in zip(annotation.size_lwh, obj.size_lwh, strict=True)
                )
                if size_error > SIZE_TOL_M:
                    findings.append(
                        Finding(
                            "boxes",
                            agent.agent_id,
                            frame,
                            f"{subject} size",
                            str(obj.size_lwh),
                            str(annotation.size_lwh),
                        )
                    )
                category = expectation.categories.get(obj.category, obj.category)
                if annotation.category != category:
                    findings.append(
                        Finding(
                            "boxes",
                            agent.agent_id,
                            frame,
                            f"{subject} class",
                            category,
                            annotation.category,
                        )
                    )
    return findings


def _world_points(
    world: World, scene: Scene, expectation: Expectation, agent_id: str, frame: int
) -> tuple[Array | None, Array]:
    """The imported sweep in the world (or ``None``), and the generated one."""
    scene_id = expectation.agents[agent_id]
    sensor_id = expectation.lidars[agent_id]
    truth = world.world_from_lidar(agent_id, frame).apply(
        world.lidar_returns(agent_id, frame).points
    )
    times = frame_times(scene, scene_id)
    if frame >= len(times):
        return None, truth
    local = cloud_in_sensor(scene, scene_id, sensor_id, times[frame])
    if local is None:
        return None, truth
    return world_from_sensor(scene, scene_id, sensor_id, frame).apply(local), truth


def check_points(world: World, scene: Scene, expectation: Expectation) -> list[Finding]:
    """At least 95 % of an object's returns lie inside its imported box, in the world.

    The returns are the imported sweep taken to the world through the imported
    LiDAR pose; a return belongs to the object the generator hit, by row. A sweep
    whose row count changed cannot be attributed and is a finding of its own.
    """
    findings: list[Finding] = []
    for agent in world.agents:
        scene_id = expectation.agents[agent.agent_id]
        times = frame_times(scene, scene_id)
        for frame in range(world.n_frames):
            points, truth = _world_points(world, scene, expectation, agent.agent_id, frame)
            if points is None or len(points) != len(truth):
                findings.append(
                    Finding(
                        "points",
                        agent.agent_id,
                        frame,
                        "sweep",
                        f"{len(truth)} points",
                        "missing" if points is None else f"{len(points)} points",
                    )
                )
                continue
            ids = world.lidar_returns(agent.agent_id, frame).object_ids
            imported = _annotations(scene, scene_id, times[frame])
            for object_id in expectation.annotated[(agent.agent_id, frame)]:
                mine = points[ids == object_id]
                annotation = imported.get(expectation.track_ids[object_id])
                if len(mine) < MIN_POINTS or annotation is None:
                    continue
                inside = inside_box(
                    mine,
                    box_in_world(scene, annotation, frame),
                    annotation.size_lwh,
                    margin_m=INSIDE_MARGIN_M,
                )
                fraction = float(inside.mean())
                if fraction < INSIDE_FRACTION:
                    findings.append(
                        Finding(
                            "points",
                            agent.agent_id,
                            frame,
                            f"object {object_id}",
                            f"at least {INSIDE_FRACTION:.0%} of {len(mine)} returns inside its box",
                            f"{fraction:.1%}",
                        )
                    )
    return findings


def check_registration(world: World, scene: Scene, expectation: Expectation) -> list[Finding]:
    """Agents agree on where an object is: 2 cm between the centroids they give it.

    Two agents strike different faces of an object, so their raw centroids differ
    by design. What must agree is where each agent's imported returns put the
    object relative to where the generator put those same returns: the offset of
    the imported centroid from the generated one, per agent, and the difference of
    those offsets between any two agents that see the object.
    """
    findings: list[Finding] = []
    for frame in range(world.n_frames):
        offsets: dict[int, dict[str, Array]] = {}
        for agent in world.agents:
            points, truth = _world_points(world, scene, expectation, agent.agent_id, frame)
            if points is None or len(points) != len(truth):
                continue
            ids = world.lidar_returns(agent.agent_id, frame).object_ids
            for object_id in np.unique(ids).tolist():
                mask = ids == object_id
                if int(mask.sum()) < MIN_POINTS:
                    continue
                offset = points[mask].mean(axis=0) - truth[mask].mean(axis=0)
                offsets.setdefault(int(object_id), {})[agent.agent_id] = offset
        for object_id, per_agent in sorted(offsets.items()):
            for agent_id, offset in per_agent.items():
                distance = float(np.linalg.norm(offset))
                if distance > REGISTRATION_TOL_M:
                    findings.append(
                        Finding(
                            "registration",
                            agent_id,
                            frame,
                            f"object {object_id} centroid",
                            f"within {REGISTRATION_TOL_M} m of the generated one",
                            f"{distance:.3f} m off",
                        )
                    )
            agents = sorted(per_agent)
            for index, first in enumerate(agents):
                for second in agents[index + 1 :]:
                    gap = float(np.linalg.norm(per_agent[first] - per_agent[second]))
                    if gap > REGISTRATION_TOL_M:
                        findings.append(
                            Finding(
                                "registration",
                                f"{first}+{second}",
                                frame,
                                f"object {object_id}",
                                f"agents agree within {REGISTRATION_TOL_M} m",
                                f"{gap:.3f} m apart",
                            )
                        )
    return findings


def _corners(world_from_box: Pose, size_lwh: tuple[float, float, float]) -> Array:
    return world_from_box.apply(CORNER_SIGNS * (np.array(size_lwh) / 2.0))


def _usable(obj: WorldObject, camera_from_world: Pose, width: int, height: int) -> bool:
    """All eight corners well in front of the camera and not absurdly far off the image."""
    del width, height
    corners = camera_from_world.apply(obj.corners_world())
    return bool(np.all(corners[:, 2] > MIN_DEPTH_M))


def check_cameras(world: World, scene: Scene, expectation: Expectation) -> list[Finding]:
    """Corners of the imported box, through the imported camera, land on the generated pixels."""
    findings: list[Finding] = []
    for agent in world.agents:
        scene_id = expectation.agents[agent.agent_id]
        times = frame_times(scene, scene_id)
        for frame in range(min(world.n_frames, len(times))):
            imported = _annotations(scene, scene_id, times[frame])
            objects = {obj.object_id: obj for obj in world.objects[frame]}
            for camera in agent.cameras:
                sensor_id = expectation.cameras[(agent.agent_id, camera.sensor_id)]
                sensor = scene.sensor(scene_id, sensor_id)
                if None in (sensor.fx, sensor.fy, sensor.cx, sensor.cy):
                    continue  # reported by check_sensor_poses
                truth = world.camera_truth(agent.agent_id, camera.sensor_id, frame)
                truth_camera_from_world = world.world_from_camera(
                    agent.agent_id, camera.sensor_id, frame
                ).inverse()
                camera_from_world = world_from_sensor(scene, scene_id, sensor_id, frame).inverse()
                for object_id in expectation.annotated[(agent.agent_id, frame)]:
                    annotation = imported.get(expectation.track_ids[object_id])
                    obj = objects[object_id]
                    if annotation is None or not _usable(
                        obj, truth_camera_from_world, camera.width, camera.height
                    ):
                        continue
                    expected = truth[object_id].pixels
                    if float(np.abs(expected).max()) > MAX_PIXEL:
                        continue
                    corners = camera_from_world.apply(
                        _corners(box_in_world(scene, annotation, frame), annotation.size_lwh)
                    )
                    depth = corners[:, 2]
                    if bool(np.any(depth <= 0.0)):
                        findings.append(
                            Finding(
                                "cameras",
                                agent.agent_id,
                                frame,
                                f"object {object_id} in {camera.sensor_id}",
                                "in front of the camera",
                                "behind it",
                            )
                        )
                        continue
                    assert sensor.fx is not None and sensor.fy is not None
                    assert sensor.cx is not None and sensor.cy is not None
                    u = sensor.fx * corners[:, 0] / depth + sensor.cx
                    v = sensor.fy * corners[:, 1] / depth + sensor.cy
                    error = float(np.abs(np.stack([u, v], axis=-1) - expected).max())
                    if error > PIXEL_TOL:
                        findings.append(
                            Finding(
                                "cameras",
                                agent.agent_id,
                                frame,
                                f"object {object_id} in {camera.sensor_id}",
                                f"corners within {PIXEL_TOL} px",
                                f"{error:.1f} px off",
                            )
                        )
    return findings


CHECKS = {
    "frames": check_frames,
    "sensor-pose": check_sensor_poses,
    "boxes": check_boxes,
    "points": check_points,
    "registration": check_registration,
    "cameras": check_cameras,
}


def run_checks(world: World, scene: Scene, expectation: Expectation) -> dict[str, list[Finding]]:
    """Every check by name. An empty list is a pass."""
    frames = check_frames(world, scene, expectation)
    if frames:
        # Without the agents and their frames nothing else can be attributed.
        return {"frames": frames}
    return {name: check(world, scene, expectation) for name, check in CHECKS.items()}
