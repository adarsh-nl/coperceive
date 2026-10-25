# ADR 0008 — Shared HD map

Status: accepted. Schema 1 is unchanged. The map is a sidecar, not a column in the scene.

## Context

Griffin stores `map_name` on each agent, for example `Carla/Maps/Town03`. The 25 m archives have no road geometry. A centreline check and a drawing of the town both need that geometry once per town, not once per scene.

The public description of an HD-map layer drawn with the sensors is the only outside idea used here. kesai-labs/py123d and the adarsh-nl fork were not installed, imported, or opened. The on-disk layout, the names, and the parser are written for schema 1: a JSON manifest, a Parquet table, and a float32 blob.

CARLA 0.9.15 is the package. Its Git tag does not contain the town OpenDRIVE files. They are members of `https://downloads.carlasim.com/Linux/CARLA_0.9.15.tar.gz`. The file on this machine is 8,386,636,048 bytes, SHA-256 `7b2f432ce74b251593f95dcc8dee6d99ad9625c2a71a2d6d48f24d879de19ef7`. The four files used are `Town03.xodr`, `Town06.xodr`, `Town07.xodr`, and `Town10HD.xodr`. Those bytes are not committed. `coperceive maps fetch` extracts them into a directory the caller chooses.

The 0.9.15 README, fetched 2026-10-25 from `https://github.com/carla-simulator/carla/blob/0.9.15/README.md` (raw `https://raw.githubusercontent.com/carla-simulator/carla/0.9.15/README.md`), says CARLA-specific code is MIT and CARLA-specific assets are CC-BY. It does not say CC-BY 4.0. A maintainer reply on 2024-07-31 in `https://github.com/carla-simulator/carla/discussions/7984` repeats that split and also gives no version. The town files travel in the packaged assets, not in the MIT git tree, so the map licence recorded by `coperceive maps ingest` is CC-BY. The earlier sentence "CARLA is MIT" is the code licence. It is the wrong licence for `Town03.xodr`. The brief's "CC-BY 4.0" names the right family and a version the README does not state.

## Options

1. **Bake the polylines into every scene.** A Town03 scene would carry the whole town. Fifty scenes would store it fifty times, and a converter run would be required before the viewer could draw a map.
2. **Bump schema 1** and add map tables inside the scene directory. Old readers that require the known column list would break, and the geometry would still be copied per scene.
3. **A sidecar directory keyed by map name.** The scene keeps the string it already stores. A maps root holds one directory per town. Readers apply an explicit transform when they want scene-world metres.

## Criteria

- A scene that already has `map_name` does not need to be rewritten.
- Schema 1 stays the scene contract in `docs/spec/format.md`.
- Vertices on disk stay in the OpenDRIVE frame, so a later alignment does not rewrite the blob.
- A reflection is allowed. A quaternion cannot store one, so the transform is not an SE3.
- The centreline figure is a measurement. It does not fail `coperceive validate`.
- The OpenDRIVE bytes stay out of git.

## Decision

One directory per map:

```text
maps/Town03/
  manifest.json
  polylines.parquet
  vertices.bin
```

`map_format` is `coperceive-hdmap`, `map_format_version` is 1. The directory name is the last path segment of `map_name`. `.`, `..`, a slash, a backslash, and a leading dot are rejected.

`vertices.bin` is little-endian float32 `x, y, z`, 12 bytes, no header, in the OpenDRIVE frame. `polylines.parquet` addresses it by vertex index (`vertex_offset`, `vertex_count`), not by byte offset. Columns: `polyline_id` int64, `layer` string, `road_id` string, `lane_id` int64 null, `side` string null, `lane_type` string null, `junction_id` string null, `vertex_offset` int64, `vertex_count` int64.

`world_from_opendrive` is `p_world = R @ p_opendrive + t`. `R` is nine numbers, `t` is three metres, `measured` is a bool. Readers apply it. The blob is not rewritten.

Layers:

- `reference` is the plan view, with no lane offset.
- `lane_centre` is the centre of each `driving` or `bidirectional` lane. Width polynomials and the lane offset are included. `t` is positive to the left of the reference heading.
- `road_edge` is the outer edge of the carriageway. `driving`, `bidirectional`, `shoulder`, `parking`, `stop`, `biking`, `border`, `entry`, `exit`, `onramp`, `offramp`, and `connectingramp` move that edge. `median` and `none` add their width and are not the edge. `sidewalk` and `walking` stop the walk.

Geometry kinds are `line`, `arc`, `spiral` (0.05 m steps), `poly3` (the geometry length is the local `u` range), and `paramPoly3` (`p` in `[0, 1]` when `pRange` is missing or `normalized`, and `p` in `[0, length]` when `pRange` is `arcLength`). Any other kind raises. The default sample step is 1.0 m, plus geometry and lane-section knots. The four CARLA towns used here contain only `line` and `arc`.

Positive lane ids are driven against `+s`. The nearest-segment heading for those lanes is reversed by π. The vertices stay in `+s` order.

Alignment tries eight horizontal maps that keep z up: `identity`, `reflect_y`, `reflect_x`, `rot180`, `rot90`, `rot270`, `swap`, `swap_neg`. The score is the median horizontal distance from vehicle poses to the driving-lane centre, then the median heading error. A horizontal translation is stored only when it cuts that median by at least 1 m. `tz` stays 0. Until `coperceive maps align` runs, the transform is the identity and `measured` is false.

The viewer blob is world-frame lane centres and road edges: `uint32` count, then per polyline a `uint32` layer code (1 lane centre, 2 road edge, 3 reference), a `uint32` vertex count, and float32 `x, y, z`. The default blob leaves the reference line out. `GET /api/scenes/{id}/map/lines` returns it. The page draws those lines under the lidar.

`coperceive validate --maps` records the distances. With no maps directory, the centreline block says `not measured`. It is not a hard failure.

The maps root is `<scenes>/maps` when that directory contains a child with `manifest.json`. `--maps` overrides it for validate and for serve.

## Measurements

Command, 2026-10-15, after ingest at 1.0 m from the 0.9.15 package. Scenes: the Griffin-25m dev conversion, 47 directories, `map_name` already written by the converter. `coperceive maps align --maps /tmp/coperceive-maps --scenes /tmp/griffin-scenes`.

Every town selected `identity`. No translation was stored. The determinant is +1.

| Map | Scenes | Poses | Median m | Heading median deg | Next candidate |
| --- | ---: | ---: | ---: | ---: | --- |
| Carla/Maps/Town03 | 13 | 1936 | 0.013712643507518118 | 0.24974331271204164 | rot180, 0.9566292293761771 m |
| Carla/Maps/Town06 | 12 | 1788 | 0.0019174357069388736 | 0.027407758135083782 | reflect_x, 23.651517233061078 m |
| Carla/Maps/Town07 | 10 | 1490 | 0.1647962688524098 | 2.4803696188618147 | swap_neg, 6.108792644120575 m |
| Carla/Maps/Town10HD | 12 | 1786 | 0.03035693727363501 | 0.23833585438623572 | reflect_x, 0.8693663665846356 m |

Each header `geoReference` is `+proj=tmerc +lat_0=0 +lon_0=0 +k=1 +x_0=0 +y_0=0 +datum=WGS84 +units=m +geoidgrids=egm96_15.gtx +vunits=m +no_defs`. In PROJ, `+X` is easting and `+Y` is northing. Identity means Griffin `+X` is OpenDRIVE `+X` and Griffin `+Y` is OpenDRIVE `+Y`. Griffin `+X` is east in that CRS, and Griffin `+Y` is north. This is the Griffin world against these OpenDRIVE files. It is not a compass survey.

`coperceive validate /tmp/griffin-scenes --maps /tmp/coperceive-maps` then wrote `/tmp/coperceive-validate-25m.json`. Scenes measured 47, skipped 0. Those report files are not in git.

Vehicle pose to driving-lane centre, 7,000 poses: min 6.541333573178878e-07 m, p05 8.874367090333857e-05 m, median 0.013712643507518118 m, p95 0.662319574473314 m, max 1.7440808533399281 m. Median minus 0.24 m is −0.22628735649248188 m. The 0.24 m figure is the earlier Town03 road-centreline claim. This median does not reproduce it.

Vehicle pose to the reference line, same 7,000 poses: min 0.005096134120426233 m, p05 0.804178786379716 m, median 1.7860191236754484 m, p95 12.248783832169195 m, max 12.27781525000623 m. That median is about half a 3.5 m lane, which is where a vehicle sits relative to the plan-view line. It is not 0.24 m either.

Vehicle boxes to the lane centre, 66,647 boxes on the vehicle agent: min 1.2417148474686602e-07 m, p05 0.00017469689533065996 m, median 0.05214550975125978 m, p95 3.2797206330750615 m, max 46.31949753942487 m. These boxes are the annotated road users, not only the ego pose.

Heading error, 7,000 poses: min 1.4023275866748008e-05 deg, p05 0.0031759777381807908 deg, median 0.20308361215973036 deg, p95 8.561008872962589 deg, max 161.4437770562339 deg.

Vehicle pose `z`, read from `poses.parquet` for the vehicle agent, 7,000 values: min −0.015592651441693306 m, p05 −0.012455367483198643 m, median −0.01233192440122366 m, p95 2.255683660507202 m, max 7.992929458618164 m. 5,895 are negative. 395 are above 1 m. On Town03-012, 15 poses (every 10th frame) sit at 7.98–7.99 m, and the vertical gap to a nearby lane-centre sample (every 5th vertex) has median −0.012375831604003906 m. `tz` stays 0 because that vertical gap is already about a centimetre. The fit does not move the road up to a body origin.

OpenDRIVE SHA-256, the files extracted on this machine:

| File | SHA-256 |
| --- | --- |
| Town03.xodr | `abf177bcd9b66cfefb7ac4919f6456815f1384f4229f1343c8a6a90bbb7713c9` |
| Town06.xodr | `a90b613175fa837fdc128ddfaf5ddad25797b128fdbd8cd945974bc21a60496e` |
| Town07.xodr | `be730652769e993e27f748ba5793dba9e49d299205146a7a8db0bcd17af877bf` |
| Town10HD.xodr | `5d883b799f634030af92be1e9d79d107845540ba04338e8c60e095be1aef7be7` |
| Town01.xodr | `97a7f6ac67812567e5c8ee0599cd823b23f80f30f3f97c502212e38b72e2b709` (added 2026-10-18 for `maps identify`; pinned to two byte-identical public mirrors, `autonomousvision/fail2drive` and `Scenic-Foundation/Scenic`, not checked against the package) |
| Town02.xodr | `953c05f17def231239ffcadba3307628a82d0d335b67b4e29c3098f7aed1dd7d` (added 2026-10-18 for `maps identify`; pinned to two byte-identical public mirrors, `autonomousvision/fail2drive` and `Scenic-Foundation/Scenic`, not checked against the package) |
| Town04.xodr | `4dddd1560ab7812b0e89cd4a523d8b6f2b93947eafb496d9eeb83cc772116c85` (added 2026-10-18 for `maps identify`; pinned to two byte-identical public mirrors, `autonomousvision/fail2drive` and `Scenic-Foundation/Scenic`, not checked against the package) |
| Town05.xodr | `7edc53b0be840e177598da7a4ed9571146d7b2595ce280d154c90f92e8a48851` (added 2026-10-18 for `maps identify`; pinned to two byte-identical public mirrors, `autonomousvision/fail2drive` and `Scenic-Foundation/Scenic`, not checked against the package) |

Polyline counts at 1.0 m: Town03 1,097 (42,748 vertices), Town06 847 (46,009), Town07 855 (23,231), Town10HD 387 (15,209).

## Consequences

- The viewer and the validator look up `map_name` under the maps root. A missing map is a 404 on the lines route and `not measured` in the report.
- Re-alignment rewrites the manifest transform. It does not rewrite `vertices.bin`.
- Town07's median, 0.165 m, is larger than the other three towns. Identity still wins by metres. A different CARLA package was not tried.
- The package SHA-256 above is `sha256sum` of the file downloaded on this machine.
