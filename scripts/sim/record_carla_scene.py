# SPDX-License-Identifier: Apache-2.0
"""Record four lidar collaborators in CARLA 0.9.15.

The drone is a lidar actor, not a vehicle. Its xy follows the centroid of
``car-0``, ``car-1`` and ``car-2``. Its z stays at 40 m. The lidar rotation
pitch is -90 degrees, so the rays point down.

Written from the CARLA 0.9.15 Python API
(https://carla.readthedocs.io/en/0.9.15/python_api/ and
https://carla.readthedocs.io/en/0.9.15/ref_sensors/).
"""

from __future__ import annotations

import argparse
import queue
import sys
from pathlib import Path

from scripts.sim.capture_writer import (
    COLLABORATORS,
    DRONE_HEIGHT_M,
    FRAME_COUNT,
    LIDAR_ATTRIBUTES,
    ROOF_LIDAR_Z_M,
    Box,
    CaptureError,
    Location,
    Pose,
    Rotation,
    build_manifest,
    choose_blueprint_ids,
    choose_spawn_indices,
    four_wheel_ids,
    require_complete,
    time_us_for,
    write_agent_tick,
    write_boxes,
    write_manifest,
)

TM_PORT = 8000


def main(argv: list[str] | None = None) -> int:
    import carla

    args = _parse(argv)
    client = carla.Client("127.0.0.1", args.port)
    client.set_timeout(60.0)
    try:
        _record(carla, client, args)
    except CaptureError as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


def _parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Record a Town03 lidar scene")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--map", default="Town03")
    parser.add_argument("--warmup-ticks", type=int, default=20)
    parser.add_argument("--ticks", type=int, default=FRAME_COUNT)
    parser.add_argument("--step", type=float, default=0.1)
    args = parser.parse_args(argv)
    if args.ticks != FRAME_COUNT:
        raise SystemExit(f"--ticks must be {FRAME_COUNT}")
    if args.step != 0.1:
        raise SystemExit("--step must be 0.1")
    if args.warmup_ticks < 0:
        raise SystemExit("--warmup-ticks must be >= 0")
    return args


def _record(carla: object, client: object, args: argparse.Namespace) -> None:
    # The names below are the 0.9.15 Python API. The carla package has no stubs.
    world = client.load_world(args.map)  # type: ignore[attr-defined]
    settings = world.get_settings()
    settings.synchronous_mode = True
    settings.fixed_delta_seconds = args.step
    world.apply_settings(settings)
    traffic = client.get_trafficmanager(TM_PORT)  # type: ignore[attr-defined]
    traffic.set_synchronous_mode(True)
    traffic.set_random_device_seed(args.seed)
    # Enter synchronous mode before any spawn. CARLA drops actors spawned earlier.
    world.tick()

    library = world.get_blueprint_library()
    spawn_points = list(world.get_map().get_spawn_points())
    wheel_rows: list[tuple[str, int | None]] = []
    by_id: dict[str, object] = {}
    for blueprint in sorted(library.filter("vehicle.*"), key=lambda item: item.id):
        wheels: int | None = None
        if blueprint.has_attribute("number_of_wheels"):
            wheels = int(blueprint.get_attribute("number_of_wheels"))
        wheel_rows.append((str(blueprint.id), wheels))
        by_id[str(blueprint.id)] = blueprint
    candidate_ids = four_wheel_ids(wheel_rows)
    chosen = choose_spawn_indices(len(spawn_points), 3, args.seed)
    blueprint_ids = choose_blueprint_ids(candidate_ids, 3, args.seed, len(spawn_points))
    lidar_bp = library.find("sensor.lidar.ray_cast")
    for name, value in LIDAR_ATTRIBUTES.items():
        lidar_bp.set_attribute(name, value)

    vehicles = []
    sensors: dict[str, object] = {}
    pending: dict[str, queue.Queue[object]] = {}
    try:
        for agent_id, index, blueprint_id in zip(
            ("car-0", "car-1", "car-2"), chosen, blueprint_ids, strict=True
        ):
            vehicle = world.spawn_actor(by_id[blueprint_id], spawn_points[index])
            if not vehicle.is_alive:
                raise CaptureError(f"{blueprint_id} at spawn {index} was destroyed at spawn")
            vehicle.set_autopilot(True, TM_PORT)
            vehicles.append(vehicle)
            print(f"spawn blueprint={blueprint_id} index={index} id={vehicle.id}", flush=True)
            lidar = world.spawn_actor(
                lidar_bp,
                carla.Transform(carla.Location(z=ROOF_LIDAR_Z_M)),  # type: ignore[attr-defined]
                attach_to=vehicle,
            )
            stream: queue.Queue[object] = queue.Queue()
            lidar.listen(stream.put)
            sensors[agent_id] = lidar
            pending[agent_id] = stream

        drone_bp = library.find("sensor.lidar.ray_cast")
        for name, value in LIDAR_ATTRIBUTES.items():
            drone_bp.set_attribute(name, value)
        # Move the spectator, not the listening lidar. set_transform on a sensor
        # that is already listening destroys that actor and the stream aborts.
        spectator = world.get_spectator()
        spectator.set_transform(_drone_transform(carla, _centroid(vehicles)))
        drone = world.spawn_actor(
            drone_bp,
            carla.Transform(),  # type: ignore[attr-defined]
            attach_to=spectator,
        )
        drone_stream: queue.Queue[object] = queue.Queue()
        drone.listen(drone_stream.put)
        sensors["drone"] = drone
        pending["drone"] = drone_stream

        for _ in range(args.warmup_ticks):
            spectator.set_transform(_drone_transform(carla, _centroid(vehicles)))
            world.tick()
            _drain(pending)

        for frame_id in range(args.ticks):
            spectator.set_transform(_drone_transform(carla, _centroid(vehicles)))
            _ = world.tick()
            measurements = _drain(pending)
            time_us = time_us_for(frame_id, args.step)
            for agent_id in COLLABORATORS:
                measurement = measurements[agent_id]
                write_agent_tick(
                    args.out,
                    agent_id,
                    frame_id,
                    time_us,
                    _pose(measurement.get_transform()),
                    bytes(measurement.raw_data),
                    int(measurement.frame),
                )
            write_boxes(args.out, frame_id, _boxes(world))
        version = str(client.get_server_version())  # type: ignore[attr-defined]
        write_manifest(
            args.out,
            build_manifest(
                seed=args.seed,
                map_name=str(world.get_map().name),
                step_s=args.step,
                frames=args.ticks,
                carla_version=version,
                agents=_agent_rows(blueprint_ids, chosen),
                sensors=_sensor_rows(),
            ),
        )
        require_complete(args.out, COLLABORATORS, args.ticks)
    finally:
        for sensor in sensors.values():
            if sensor.is_listening:  # type: ignore[attr-defined]
                sensor.stop()  # type: ignore[attr-defined]
            if sensor.is_alive:  # type: ignore[attr-defined]
                sensor.destroy()  # type: ignore[attr-defined]
        for vehicle in vehicles:
            if vehicle.is_alive:
                vehicle.destroy()
        settings.synchronous_mode = False
        world.apply_settings(settings)


def _centroid(vehicles: list[object]) -> tuple[float, float]:
    xs: list[float] = []
    ys: list[float] = []
    for vehicle in vehicles:
        location = vehicle.get_transform().location  # type: ignore[attr-defined]
        xs.append(float(location.x))
        ys.append(float(location.y))
    return sum(xs) / len(xs), sum(ys) / len(ys)


def _drone_transform(carla: object, xy: tuple[float, float]) -> object:
    return carla.Transform(  # type: ignore[attr-defined]
        carla.Location(x=xy[0], y=xy[1], z=DRONE_HEIGHT_M),  # type: ignore[attr-defined]
        carla.Rotation(pitch=-90.0),  # type: ignore[attr-defined]
    )


def _drain(pending: dict[str, queue.Queue[object]]) -> dict[str, object]:
    found: dict[str, object] = {}
    for agent_id, stream in pending.items():
        try:
            found[agent_id] = stream.get(timeout=30.0)
        except queue.Empty as exc:
            raise CaptureError(f"{agent_id} produced no lidar for this tick") from exc
    return found


def _pose(transform: object) -> Pose:
    location = transform.location  # type: ignore[attr-defined]
    rotation = transform.rotation  # type: ignore[attr-defined]
    return Pose(
        location=Location(x=float(location.x), y=float(location.y), z=float(location.z)),
        rotation=Rotation(
            pitch=float(rotation.pitch),
            yaw=float(rotation.yaw),
            roll=float(rotation.roll),
        ),
    )


def _boxes(world: object) -> list[Box]:
    actors = list(world.get_actors().filter("vehicle.*")) + list(  # type: ignore[attr-defined]
        world.get_actors().filter("walker.*")  # type: ignore[attr-defined]
    )
    boxes: list[Box] = []
    for actor in actors:
        transform = actor.get_transform()
        bounding = actor.bounding_box
        center = transform.transform(bounding.location)
        boxes.append(
            Box(
                actor_id=int(actor.id),
                type_id=str(actor.type_id),
                center=Location(x=float(center.x), y=float(center.y), z=float(center.z)),
                half_extent=Location(
                    x=float(bounding.extent.x),
                    y=float(bounding.extent.y),
                    z=float(bounding.extent.z),
                ),
                rotation=Rotation(
                    pitch=float(transform.rotation.pitch),
                    yaw=float(transform.rotation.yaw),
                    roll=float(transform.rotation.roll),
                ),
            )
        )
    return boxes


def _agent_rows(blueprint_ids: list[str], spawn_indexes: list[int]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for agent_id, index, blueprint_id in zip(
        ("car-0", "car-1", "car-2"), spawn_indexes, blueprint_ids, strict=True
    ):
        rows.append(
            {
                "agent_id": agent_id,
                "kind": "vehicle",
                "blueprint": blueprint_id,
                "spawn_index": index,
                "autopilot": True,
            }
        )
    rows.append(
        {
            "agent_id": "drone",
            "kind": "sensor",
            "height_m": DRONE_HEIGHT_M,
            "placement": (
                "xy is the centroid of car-0, car-1 and car-2 each tick; "
                "z is fixed at 40 m; the actor is a lidar, not a vehicle"
            ),
        }
    )
    return rows


def _sensor_rows() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for agent_id in ("car-0", "car-1", "car-2"):
        rows.append(
            {
                "agent_id": agent_id,
                "sensor_id": "lidar_top",
                "blueprint": "sensor.lidar.ray_cast",
                "attributes": dict(LIDAR_ATTRIBUTES),
                "attachment_z_m": ROOF_LIDAR_Z_M,
            }
        )
    rows.append(
        {
            "agent_id": "drone",
            "sensor_id": "lidar_top",
            "blueprint": "sensor.lidar.ray_cast",
            "attributes": dict(LIDAR_ATTRIBUTES),
            "rotation_pitch_deg": -90.0,
        }
    )
    return rows


if __name__ == "__main__":
    raise SystemExit(main())
