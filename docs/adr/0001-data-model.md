# ADR 0001 — Scene data model

Status: accepted for the Phase 0 scaffold. Schema version 1.

## Context

A cooperative scene has several agents. Griffin’s vehicle and drone share frame ids and, under the rotation checked in `docs/design/griffin-verification.md`, share world points of boxes that share a track id. The library has to represent that without pretending every integer id is a shared object, and without inventing a pose outside the recorded span.

## Options

1. One ego agent, extra agents stuffed into sensor names.
2. A scene with N agents, poses as `world ← body`, per-agent annotations, and a separate shared-object table.
3. One object list in the world frame only, agents reduced to sensors.

## Criteria

- Both Griffin agents live in one scene.
- Time is an int64 microsecond value, with an explicit note when a dataset’s files are only frame ids.
- `T(dst ← src, t)` is defined, and interpolation cannot silently walk past the last sample.
- Discrete queries can be exact, nearest, backward, or forward, and each hit reports `dt_us = sample_time − query_time`.
- A scene-level object exists only when the id is shared and the categories agree.

## Decision

Option 2. Implemented in `coperceive.model`, `coperceive.query`, `coperceive.transform`, and `coperceive.diagnostics`.

- `Scene` has `world_frame`, `Clock`, agents, sensors, annotations, optional shared objects, point clouds, and image paths.
- `Clock.scale_note` is required. Griffin’s note says `time_us = frame_id * 100000` assumes the paper’s 10 Hz lidar rate.
- Agent poses are strictly increasing `StampedPose` values. `world_from_body` is an `SE3`.
- `Sensor.body_from_sensor` takes a sensor point into the agent body. Intrinsics are optional.
- Annotations are in the annotating agent’s body frame.
- `shared_objects` promotes a track id only when at least two agents use it and they agree on exactly one category.
- `lookup_pose` returns the sample on an exact hit. Outside the span it raises `ExtrapolationError`. Inside a bracket it linearly interpolates translation and slerps rotation, and only when `gap ≤ max_gap_us`. Otherwise it raises `GapError`.
- `T(dst ← src) = inv(world_from_dst) ∘ world_from_src`.
- `query_time` modes are `exact`, `nearest`, `backward`, and `forward`. Nearest ties keep the earlier sample. A hit farther than `tolerance_us` raises `ToleranceError`.
- `SCHEMA_VERSION` is 1. A file with another version raises `SchemaVersionError`.

`SE3.from_rpy_deg` uses `R = Rz(yaw) @ Ry(pitch) @ Rx(roll)`. On Town03-000, 251 shared pairs across 15 frames, that composition put vehicle and drone boxes within 5.7e-14 m and produced no category mismatch.

## Consequences

- Callers must pass `max_gap_us` and `tolerance_us`. There is no default that hides a gap.
- Shared ids are a derived table. Geometry stays on the per-agent boxes.
- The Griffin 10 Hz mapping is an assumption written into the clock, not a measured clock.
- Changing the schema means a new version, not a quiet extra interpretation of old files.
