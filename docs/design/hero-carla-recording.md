# Hero recording in CARLA 0.9.15

Smoke test job 641508 on `hpc-node11` loaded `Carla/Maps/Town03` and returned lidar from one vehicle. This note is the recording that would replace the landing clip. Nothing in it has been run.

The simulator install is `/local/nanjaiyalathaa/carla-0.9.15` on `hpc-node11` only. Later jobs need `--nodelist=hpc-node11`. The client is that tree's `py310` venv after `module load python/3.10.7`.

## Scene

Town03, synchronous, `fixed_delta_seconds` 0.1 (10 Hz). Record 100 ticks, which is 10.0 s. `time_us` for tick `i` is `i * 100000`, starting at 0. Twenty warmup ticks run first and are not written.

Seed `20261026` is passed to `traffic_manager.set_random_device_seed` after `set_synchronous_mode(True)`. The same seed is passed to `random.Random` to choose three spawn-point indexes.

Collaborators are the actors that carry a lidar. Other vehicles and walkers, if the world already has them, are not collaborators. Their boxes are still written.

| Agent | What it is | Sensor |
| --- | --- | --- |
| `car-0`, `car-1`, `car-2` | Three different `vehicle.*` blueprints whose `number_of_wheels` is 4. The same `random.Random(20261026)` that picks the spawn indexes then picks the three blueprint ids. Each car is on autopilot | `sensor.lidar.ray_cast` named `lidar_top`, attached at `carla.Location(z=2.4)` |
| `drone` | A lidar actor, not a vehicle. It is attached to the spectator. Each tick the spectator moves to the centroid of the three cars at z = 40 m with pitch `-90`. The lidar itself is not teleported while it is listening | `sensor.lidar.ray_cast` named `lidar_top` |

## Spawn rule

No coordinates are fixed in this note. The recorder loads Town03 and reads `map.get_spawn_points()`. `random.Random(20261026).sample` chooses three indexes. The same call with the same spawn-point count returns the same indexes. The drone is not chosen from that list.

All three roof lidars use the same attributes. The drone uses the same attributes except the downward pitch. Names and defaults are from the 0.9.15 lidar sensor page, fetched 2026-10-26 from `https://carla.readthedocs.io/en/0.9.15/ref_sensors/`. `rotation_frequency` is 10 so one revolution matches one 0.1 s tick. `dropoff_general_rate` is 0 so the default 0.45 drop is not applied.

| Attribute | Value |
| --- | --- |
| `channels` | 32 |
| `range` | 50 |
| `points_per_second` | 56000 |
| `rotation_frequency` | 10 |
| `upper_fov` | 10 |
| `lower_fov` | -30 |
| `sensor_tick` | 0.0 |
| `dropoff_general_rate` | 0.0 |

Each tick calls `world.tick()`, then reads one `carla.LidarMeasurement` per collaborator. Point bytes are `measurement.raw_data` (float32 x, y, z, intensity in the sensor frame). The pose is `measurement.transform`.

## Files the recorder writes

Under `/local/nanjaiyalathaa/rec-<jobid>/capture/`, then copied to `~/results/carla-record-<jobid>/` only if `du -sb` reports under 2 GiB:

- `manifest.json` with the seed, map name, tick rate, agent and sensor configs, `carla_version`, and the licence line `CARLA 0.9.15: code MIT, assets CC-BY`.
- `agents/<agent_id>/poses.jsonl`, one JSON object per tick: `frame_id`, `time_us`, the sensor's world location and rotation, and the CARLA frame.
- `agents/<agent_id>/lidar/<frame_id>.bin`, the raw measurement bytes, float32 x, y, z, intensity in the sensor frame.
- `boxes.jsonl`, one JSON object per tick with the world-frame 3D box of every `vehicle.*` and `walker.*` actor. `half_extent` is CARLA's box extent.

The job exits non-zero unless every collaborator has lidar files and pose rows for frame ids 0 through 99 with no gaps, and the box file has the same ids.

A later converter, not this recording job, writes a coperceive scene directory. `scripts/site/export_hero_clip.py` reads that scene through `read_scene`, `frame_document`, and `frame_points`. The point blob has to stay sensor-frame x, y, z, intensity so `frame_points` can apply the pose and the sensor extrinsic. Poses go in `poses.parquet` at the 100 timestamps above. The four collaborators are four agents, each with sensor id `lidar_top`.

The exporter on this branch still reads only agent ids `vehicle` and `drone`, and only the vehicle's `lidar_top` points. Its origin, map window, and `START_TIME_US` are the previous Griffin clip (`ORIGIN_X = -105`, `ORIGIN_Y = -25`, `START_TIME_US = 1118500000`). Those have to be changed to this recording before a new clip is built. This note does not build that clip.

## Licence

Fetched 2026-10-26 from `https://github.com/carla-simulator/carla/blob/0.9.15/README.md`. CARLA-specific code is MIT (`https://github.com/carla-simulator/carla/blob/0.9.15/LICENSE`). CARLA-specific assets, including Town03, are CC-BY. The README does not name a CC-BY version.
