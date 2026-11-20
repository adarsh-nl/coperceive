# SPDX-License-Identifier: Apache-2.0
# pyright: reportUnknownMemberType=false
"""A real-size synthetic scene for the viewer benchmark (ADR 0020, phase 0).

    python scripts/bench/scale_fixture.py OUT [--agents 4] [--points 160000]
                                             [--cameras 16] [--samples 100]
                                             [--rate-hz 10] [--seed 0]

Writes one schema-1 scene through the store's own writer into ``OUT/<scene id>``
(with ``COMPLETE.json``, so ``coperceive serve --root OUT`` opens it):

- N vehicles driving a loop around a junction, with real rotating poses, so
  the server's sensor-to-world transform does real work on every sweep;
- one lidar per vehicle, P points per sweep in the sensor frame, spread over a
  ground plane, static "buildings" and the moving annotated boxes;
- a few annotated boxes per agent per sample (body frame, shared track ids);
- C cameras in all, spread round-robin over the vehicles, each with one small
  PNG per sample stamped with its frame index, so an image fetch costs what
  the format costs.

Everything is deterministic from ``--seed``: the same arguments write the same
bytes. The output is never committed; a target inside this repository is
refused. At the defaults (4 x 160 000 x 16 x 100 at 10 Hz) the scene is about
1 GB of points plus the images, and generation holds the points in memory once.

The last line on stdout is a JSON summary (scene directory, counts, bytes
written); a one-line note goes to stderr.
"""

from __future__ import annotations

import argparse
import io
import json
import math
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import numpy.typing as npt
from coperceive.diagnostics import shared_objects
from coperceive.model import (
    SCHEMA_VERSION,
    Agent,
    AgentType,
    Annotation,
    BoxFrame,
    Clock,
    ImageRef,
    Modality,
    PointCloud,
    Scene,
    Sensor,
    StampedPose,
)
from coperceive.se3 import SE3
from coperceive.store import write_scene
from PIL import Image, ImageDraw, ImageFont

REPO = Path(__file__).resolve().parents[2]
SCHEMA = "coperceive-scale-fixture-1"
DEFAULT_AGENTS = 4
DEFAULT_POINTS = 160_000
DEFAULT_CAMERAS = 16
DEFAULT_SAMPLES = 100
DEFAULT_RATE_HZ = 10.0
IMAGE_SIZE = (160, 120)
LIDAR_ID = "lidar_top"
LIDAR_MOUNT = (0.0, 0.0, 1.8)
LOOP_RADIUS_M = 30.0
SPEED_MPS = 10.0
GROUND_FRACTION = 0.5
BUILDING_FRACTION = 0.35  # the rest falls on the moving (annotated) boxes
GROUND_RANGE_M = (3.0, 70.0)
BUILDINGS = 16

F64 = npt.NDArray[np.float64]


@dataclass(frozen=True)
class _Mover:
    """An annotated actor on a circle around the junction."""

    track_id: str
    category: str
    size_lwh: tuple[float, float, float]
    radius_m: float
    phase: float
    speed_mps: float

    def at(self, t: float) -> tuple[tuple[float, float, float], float]:
        """World center (box middle) and yaw at ``t`` seconds."""
        angle = self.phase + self.speed_mps * t / self.radius_m
        x = self.radius_m * math.cos(angle)
        y = self.radius_m * math.sin(angle)
        return (x, y, self.size_lwh[2] / 2.0), angle + math.pi / 2.0


def _movers() -> tuple[_Mover, ...]:
    car = (4.4, 1.8, 1.5)
    return (
        _Mover("car-1", "car", car, 22.0, 0.3, 9.0),
        _Mover("car-2", "car", car, 22.0, 2.4, 8.0),
        _Mover("car-3", "car", car, 38.0, 1.1, 11.0),
        _Mover("car-4", "car", car, 38.0, 4.0, 10.0),
        _Mover("cyclist-1", "cyclist", (1.7, 0.7, 1.7), 26.0, 5.2, 5.0),
        _Mover("ped-1", "pedestrian", (0.6, 0.6, 1.75), 34.0, 3.3, 1.2),
    )


def _buildings(rng: np.random.Generator) -> tuple[F64, F64, F64]:
    """Static boxes on a ring outside the loop: centers, sizes (l, w, h), yaws."""
    angles = np.linspace(0.0, 2.0 * math.pi, BUILDINGS, endpoint=False)
    angles = angles + rng.uniform(-0.1, 0.1, BUILDINGS)
    radii = rng.uniform(50.0, 68.0, BUILDINGS)
    sizes = np.stack(
        [
            rng.uniform(8.0, 18.0, BUILDINGS),
            rng.uniform(6.0, 14.0, BUILDINGS),
            rng.uniform(4.0, 15.0, BUILDINGS),
        ],
        axis=1,
    )
    centers = np.stack([radii * np.cos(angles), radii * np.sin(angles), sizes[:, 2] / 2.0], axis=1)
    return centers, sizes, angles


def _agent_pose(agent: int, agents: int, t: float) -> SE3:
    """A vehicle on the loop, yaw along its direction of travel (counter-clockwise)."""
    angle = 2.0 * math.pi * agent / agents + SPEED_MPS * t / LOOP_RADIUS_M
    yaw_deg = math.degrees(angle + math.pi / 2.0)
    return SE3.from_rpy_deg(0.0, 0.0, yaw_deg).translation(
        LOOP_RADIUS_M * math.cos(angle), LOOP_RADIUS_M * math.sin(angle), 0.0
    )


def _rotation(pose: SE3) -> F64:
    w, x, y, z = pose.qw, pose.qx, pose.qy, pose.qz
    return np.array(
        [
            [1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
            [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
            [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)],
        ],
        dtype=np.float64,
    )


def _box_surface(rng: np.random.Generator, count: int, centers: F64, sizes: F64, yaws: F64) -> F64:
    """``count`` points uniformly on the side and top faces of yawed boxes."""
    if count == 0 or len(centers) == 0:
        return np.zeros((0, 3), dtype=np.float64)
    length, width, height = sizes[:, 0], sizes[:, 1], sizes[:, 2]
    # Face areas per box: two x-faces, two y-faces, the top.
    faces = np.stack([2 * width * height, 2 * length * height, length * width], axis=1)
    weights = faces.sum(axis=1)
    box = rng.choice(len(centers), size=count, p=weights / weights.sum())
    face_p = faces[box] / weights[box][:, None]
    pick = rng.random(count)
    axis = (pick > face_p[:, 0]).astype(np.int64) + (pick > face_p[:, 0] + face_p[:, 1])
    local = rng.uniform(-0.5, 0.5, size=(count, 3))
    rows = np.arange(count)
    sign = np.where(rng.random(count) < 0.5, -0.5, 0.5)
    local[rows, axis] = np.where(axis == 2, 0.5, sign)
    local *= sizes[box]
    c, s = np.cos(yaws[box]), np.sin(yaws[box])
    out = np.empty_like(local)
    out[:, 0] = c * local[:, 0] - s * local[:, 1]
    out[:, 1] = s * local[:, 0] + c * local[:, 1]
    out[:, 2] = local[:, 2]
    return out + centers[box]


def _sweep(
    rng: np.random.Generator,
    points: int,
    world_from_sensor: SE3,
    buildings: tuple[F64, F64, F64],
    movers: tuple[_Mover, ...],
    t: float,
) -> bytes:
    """One sweep in the sensor frame: xyz + intensity, float32 little-endian."""
    origin = np.array([world_from_sensor.tx, world_from_sensor.ty, 0.0])
    n_ground = int(points * GROUND_FRACTION)
    n_building = int(points * BUILDING_FRACTION)
    n_mover = points - n_ground - n_building
    # Ground: denser near the sensor, like a spinning lidar's rings.
    radius = rng.uniform(*GROUND_RANGE_M, size=n_ground)
    angle = rng.uniform(0.0, 2.0 * math.pi, size=n_ground)
    ground = np.stack(
        [
            origin[0] + radius * np.cos(angle),
            origin[1] + radius * np.sin(angle),
            rng.normal(0.0, 0.02, size=n_ground),
        ],
        axis=1,
    )
    walls = _box_surface(rng, n_building, *buildings)
    placed = [mover.at(t) for mover in movers]
    mover_centers = np.array([center for center, _ in placed], dtype=np.float64)
    mover_sizes = np.array([mover.size_lwh for mover in movers], dtype=np.float64)
    mover_yaws = np.array([yaw for _, yaw in placed], dtype=np.float64)
    actors = _box_surface(rng, n_mover, mover_centers, mover_sizes, mover_yaws)
    world = np.concatenate([ground, walls, actors], axis=0)
    world += rng.normal(0.0, 0.01, size=world.shape)
    intensity = np.concatenate(
        [
            rng.uniform(0.05, 0.3, size=n_ground),
            rng.uniform(0.3, 0.7, size=n_building),
            rng.uniform(0.7, 1.0, size=n_mover),
        ]
    )
    # sensor = R^T (world - t)
    rotation = _rotation(world_from_sensor)
    translation = np.array([world_from_sensor.tx, world_from_sensor.ty, world_from_sensor.tz])
    sensor = (world - translation) @ rotation
    out = np.empty((points, 4), dtype="<f4")
    out[:, :3] = sensor
    out[:, 3] = intensity
    return out.tobytes()


_FONT_CACHE: dict[int, ImageFont.ImageFont | ImageFont.FreeTypeFont] = {}


def _font(size: int) -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
    if size not in _FONT_CACHE:
        try:
            _FONT_CACHE[size] = ImageFont.load_default(size=size)
        except (TypeError, OSError):  # an older Pillow or no FreeType: the bitmap font
            _FONT_CACHE[size] = ImageFont.load_default()
    return _FONT_CACHE[size]


def _image(camera: int, frame: int, size: tuple[int, int]) -> bytes:
    width, height = size
    hue = (camera * 47) % 256
    image = Image.new("RGB", size, (60 + hue // 3, 90 + (hue * 7) % 90, 150))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, height * 6 // 10, width, height), fill=(80, 80, 80))
    draw.text((6, 4), f"cam {camera:02d}", fill=(255, 255, 255), font=_font(max(10, height // 10)))
    draw.text(
        (6, height // 4), f"{frame:04d}", fill=(255, 255, 0), font=_font(max(12, height // 3))
    )
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=False)
    return buffer.getvalue()


def inside_repo(target: Path) -> bool:
    resolved = target.expanduser().resolve()
    return resolved == REPO or REPO in resolved.parents


def build(
    out: Path,
    *,
    agents: int = DEFAULT_AGENTS,
    points: int = DEFAULT_POINTS,
    cameras: int = DEFAULT_CAMERAS,
    samples: int = DEFAULT_SAMPLES,
    rate_hz: float = DEFAULT_RATE_HZ,
    seed: int = 0,
    scene_id: str | None = None,
    image_size: tuple[int, int] = IMAGE_SIZE,
) -> dict[str, object]:
    """Write the scene into ``out/<scene id>`` and return a summary."""
    if inside_repo(out):
        raise ValueError(f"refusing to write a fixture inside the repository ({REPO}): {out}")
    if agents < 1 or points < 0 or cameras < 0 or samples < 1 or rate_hz <= 0:
        raise ValueError("need agents >= 1, points >= 0, cameras >= 0, samples >= 1, rate > 0")
    scene_id = scene_id or f"scale-{agents}x{points}x{cameras}x{samples}"
    scene_dir = out.expanduser().resolve() / scene_id
    if scene_dir.exists():
        raise FileExistsError(f"{scene_dir} already exists")
    started = time.monotonic()
    gap_us = round(1_000_000 / rate_hz)
    times = [index * gap_us for index in range(samples)]
    rng = np.random.default_rng(seed)
    buildings = _buildings(rng)
    movers = _movers()
    agent_ids = [f"cav-{index}" for index in range(agents)]
    lidar_mount = SE3.from_rpy_deg(0.0, 0.0, 0.0).translation(*LIDAR_MOUNT)

    sensors: list[Sensor] = [
        Sensor(LIDAR_ID, agent_id, Modality.LIDAR, lidar_mount) for agent_id in agent_ids
    ]
    camera_keys: list[tuple[str, str]] = []
    per_agent = [0] * agents
    for camera in range(cameras):
        per_agent[camera % agents] += 1
    seen = [0] * agents
    width, height = image_size
    focal = width / 2.0  # a 90 degree horizontal field of view
    for camera in range(cameras):
        owner = camera % agents
        slot = seen[owner]
        seen[owner] += 1
        yaw = 360.0 * slot / per_agent[owner]
        # Optical frame (x right, y down, z forward) turned to look along ``yaw``.
        body_from_camera = (
            SE3.from_rpy_deg(0.0, 0.0, yaw)
            .compose(SE3.from_rpy_deg(-90.0, 0.0, -90.0))
            .translation(1.0 * math.cos(math.radians(yaw)), 1.0 * math.sin(math.radians(yaw)), 1.6)
        )
        sensor_id = f"cam_{slot:02d}"
        sensors.append(
            Sensor(
                sensor_id,
                agent_ids[owner],
                Modality.CAMERA,
                body_from_camera,
                width,
                height,
                focal,
                focal,
                width / 2.0,
                height / 2.0,
            )
        )
        camera_keys.append((agent_ids[owner], sensor_id))

    agent_rows: list[Agent] = []
    annotations: list[Annotation] = []
    clouds: list[PointCloud] = []
    for index, agent_id in enumerate(agent_ids):
        poses: list[StampedPose] = []
        for frame, time_us in enumerate(times):
            t = time_us / 1e6
            world_from_body = _agent_pose(index, agents, t)
            poses.append(StampedPose(time_us, world_from_body))
            body_from_world = world_from_body.inverse()
            body_yaw = 2.0 * math.atan2(world_from_body.qz, world_from_body.qw)
            for mover in movers:
                center, yaw = mover.at(t)
                annotations.append(
                    Annotation(
                        agent_id,
                        time_us,
                        mover.track_id,
                        mover.category,
                        body_from_world.apply(center),
                        mover.size_lwh,
                        SE3.from_rpy_deg(0.0, 0.0, math.degrees(yaw - body_yaw)),
                        1.0,
                        BoxFrame.BODY,
                    )
                )
            sweep_rng = np.random.default_rng([seed, index, frame])
            world_from_sensor = world_from_body.compose(lidar_mount)
            xyz_i = _sweep(sweep_rng, points, world_from_sensor, buildings, movers, t)
            clouds.append(PointCloud(agent_id, LIDAR_ID, time_us, points, xyz_i))
        agent_rows.append(Agent(agent_id, AgentType.VEHICLE, tuple(poses)))

    images: list[ImageRef] = []
    image_bytes: dict[str, bytes] = {}
    for camera, (agent_id, sensor_id) in enumerate(camera_keys):
        for frame, time_us in enumerate(times):
            path = f"camera/{agent_id}/{sensor_id}/{frame:06d}.png"
            images.append(ImageRef(agent_id, sensor_id, time_us, path))
            image_bytes[path] = _image(camera, frame, image_size)

    scene = Scene(
        schema_version=SCHEMA_VERSION,
        scene_id=scene_id,
        world_frame="ENU",
        clock=Clock("us", f"synthetic scale fixture, {rate_hz:g} Hz, seed {seed}"),
        agents=tuple(agent_rows),
        sensors=tuple(sensors),
        annotations=tuple(annotations),
        objects=shared_objects(annotations),
        point_clouds=tuple(clouds),
        images=tuple(images),
    )
    write_scene(scene, scene_dir)
    del scene, clouds
    for path, payload in image_bytes.items():
        target = scene_dir / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
    (scene_dir / "COMPLETE.json").write_text("{}\n", encoding="utf-8")
    written = sum(path.stat().st_size for path in scene_dir.rglob("*") if path.is_file())
    point_bytes = (scene_dir / "points.bin").stat().st_size
    return {
        "schema": SCHEMA,
        "root": str(scene_dir.parent),
        "scene_dir": str(scene_dir),
        "scene_id": scene_id,
        "agents": agents,
        "points": points,
        "cameras": cameras,
        "samples": samples,
        "rate_hz": rate_hz,
        "seed": seed,
        "image_size": [width, height],
        "bytes": written,
        "point_bytes": point_bytes,
        "image_bytes": sum(len(payload) for payload in image_bytes.values()),
        "seconds": round(time.monotonic() - started, 2),
    }


def _size(text: str) -> tuple[int, int]:
    width, _, height = text.lower().partition("x")
    try:
        parsed = (int(width), int(height))
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected WIDTHxHEIGHT, got {text!r}") from None
    if parsed[0] < 16 or parsed[1] < 16:
        raise argparse.ArgumentTypeError("images must be at least 16x16")
    return parsed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("out", type=Path, help="folder to write the scene into (not in the repo)")
    parser.add_argument("--agents", type=int, default=DEFAULT_AGENTS)
    parser.add_argument("--points", type=int, default=DEFAULT_POINTS, help="points per sweep")
    parser.add_argument("--cameras", type=int, default=DEFAULT_CAMERAS, help="cameras in all")
    parser.add_argument("--samples", type=int, default=DEFAULT_SAMPLES)
    parser.add_argument("--rate-hz", type=float, default=DEFAULT_RATE_HZ)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--scene-id", default=None)
    parser.add_argument("--image-size", type=_size, default=IMAGE_SIZE, metavar="WxH")
    args = parser.parse_args(argv)
    try:
        summary = build(
            args.out,
            agents=args.agents,
            points=args.points,
            cameras=args.cameras,
            samples=args.samples,
            rate_hz=args.rate_hz,
            seed=args.seed,
            scene_id=args.scene_id,
            image_size=args.image_size,
        )
    except (ValueError, FileExistsError) as exc:
        print(f"scale_fixture: {exc}", file=sys.stderr)
        return 2
    total = int(str(summary["bytes"]))
    print(
        f"scale_fixture: wrote {total} bytes ({total / 1024**2:.1f} MiB) to "
        f"{summary['scene_dir']} in {summary['seconds']} s",
        file=sys.stderr,
    )
    print(json.dumps(summary), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
