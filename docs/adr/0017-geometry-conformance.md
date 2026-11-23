# ADR 0017 — Geometry conformance: one synthetic world, six native exports, one suite

Status: proposed on 2026-11-16, for PM review before the build. Branch `agent/claude-geometry-conformance`, PR into `release/v0.1.2`. Nothing in `coperceive/` changes in this commit, and nothing in `coperceive/adapters/` changes in this PR at all (see §5).

## Context

The most frequent bug class in this project is a frame convention read wrongly: rotation argument order and units (degrees or radians, `[roll, yaw, pitch]` in the OpenCOOD and V2V4Real files), left- or right-handed sources ([ADR 0012](0012-left-handed-sources.md)), camera axes. Each adapter has been fixed one bug at a time.

What exists today:

- Six adapters: OPV2V, V2XSet, V2V4Real and Griffin as manifests (`coperceive/adapters/builtin/*.yaml`), DAIR-V2X-C and the generic multi-view format as code (`coperceive/adapters/dair_v2x_c.py`, `generic.py`).
- Each has its own synthetic fixture (`tests/opencood_release.py`, `tests/griffin_release.py`, the DAIR and generic test helpers). Each fixture was written together with its adapter, by the same hands, from the same reading of the source convention. A fixture and an adapter that share a wrong reading agree with each other.
- The fixtures check a handful of facts each. None has ray-cast LiDAR returns (the demo generator in `coperceive/demo/generate.py` ray-casts, but it is product code and its world reaches the generic adapter only). Only the generic adapter's test projects a point through a camera (`tests/test_generic_adapter.py`). None sweeps yaw through ±π, and the Griffin fixture writes roll and pitch as 0.
- Two conventions are recorded as unverified in the repository itself: the V2V4Real world frame (`docs/datasets/v2v4real.md`) and the corner order of DAIR-V2X-C `world_8_points` (`docs/datasets/dair-v2x-c.md`).

## Decision

### 1. `docs/geometry/conventions.md`

One page, one section per dataset. For every frame transform it gives two columns side by side:

- **what the source says**: handedness and axes of each frame, the argument order and units of every rotation, how rotations compose, LiDAR → ego → world, camera extrinsics (which frame to which) and intrinsics. Each cell has a source and a locator from the dataset's own paper or documentation, or is marked **unverified**.
- **what the adapter does** with that field, with the manifest key or the function it was read from.

A row where the two columns differ, or where the left column is unverified, is a finding before any test runs.

### 2. One synthetic world

`tests/geomworld/` builds a world in a right-handed, z-up frame with known answers:

- **objects**: boxes with a class, a size and a full pose. Headings sweep from −π to π in small steps, including the values on and next to ±π and the right angles; some objects carry roll and pitch.
- **agents**: vehicles, a roadside unit and an aerial agent, each with a full pose (roll and pitch included), a LiDAR and pinhole cameras.
- **LiDAR returns** by ray casting: rays from the sensor origin on a regular angular grid, intersected with every box (slab test in the box frame), nearest hit kept, each hit tagged with the object it struck. No noise: a return lies on the surface of its box.
- **camera truth**: the eight corners of every box projected through the camera's pose and intrinsics, kept as pixel coordinates with the 2D box around them. The image file written is a small PNG with those corners drawn, so a failure can be looked at.

The world is plain data (numbers and arrays). Nothing here reads a real frame, and the generator is committed, not its output.

### 3. Six exporters, written from the conventions page

`tests/geomworld/export_<dataset>.py` writes the world in each dataset's native on-disk layout: OPV2V, V2XSet, V2V4Real, DAIR-V2X-C, Griffin and the generic multi-view format.

- An exporter is written from `docs/geometry/conventions.md` and the dataset's public format description, **not** from the adapter. The implementer is a separate session that does not open `coperceive/adapters/` or the existing fixtures. Otherwise the exporter would invert the adapter, and the suite would prove only that the adapter is its own inverse.
- An exporter converts the right-handed truth into the source convention (a reflection for a left-handed source, the dataset's angle order and units, its box parameterisation) and writes files. It imports nothing from `coperceive`; a test checks that.
- Where the source convention is unverified, the exporter takes a named parameter for each plausible reading. The suite then reports which reading the adapter implements. That is a finding, not a pass.

### 4. The conformance suite

`tests/test_geometry_conformance.py`, for each adapter: export the world, run the adapter through the public `ingest`, read the scene back, and assert:

| Check | Rule |
| --- | --- |
| Points | At least 95 % of the ray hits of an object lie inside its imported box, after every transform (LiDAR → body → world), per object and per agent. |
| Boxes | The imported pose matches the generated one within 1 cm in position and 0.1° in each of roll, pitch and heading; the size within 1 mm. |
| Cameras | The corners of the imported 3D box, projected through the imported camera pose and intrinsics, land within 1 px of the generated pixels. |
| Agents | The centroid of an object's hits, in world space, agrees between any two agents that see it within 2 cm. |
| Yaw sweep | Every heading of the sweep passes the box and point checks. A degree/radian mix-up or a sign flip fails most of them, not one. |
| Tilt | For the datasets that store roll and pitch, objects and agents with roll, pitch ≠ 0 pass the box and point checks. |

A heading comparison is made modulo 2π. A box turned by π has the same footprint and is still a failure: the suite compares the pose, not the overlap.

The suite runs in the existing `test` job. No new job, no new runner, numpy only. Budget: under 60 s.

### 5. Failures are routed, not fixed here

DeepSeek owns the adapters. A failing case goes into this PR as a test marked `xfail(strict=True)` with the reason and the issue, so it turns red the day the adapter is fixed and the mark is forgotten. The PM routes the fix. I change an adapter only if the PM says so.

The PR carries a findings table: dataset, check, expected, got, the convention row it traces to, issue.

## Open questions for the PM

1. **Unverified conventions.** For the V2V4Real world frame and the DAIR-V2X-C corner order the public documentation may not settle the convention. The suite can then say what the adapter does, not whether it is right. Checking against real data is outside this PR (no real frames in the repository; no cluster access here).
2. **Existing fixtures.** They stay as they are. Should they later be replaced by the shared world, or kept as the adapters' own unit tests?
3. **Cameras without a documented model.** Where a dataset documents intrinsics and no distortion, the suite assumes a pinhole camera without distortion and says so on the conventions page.

## Consequences

- A convention read wrongly in an adapter fails several checks at once, on the first run, instead of surfacing as one odd scene later.
- A new adapter gets an exporter and inherits the whole suite.
- The exporters are a second description of each format to maintain. They change only when a dataset's layout changes.
