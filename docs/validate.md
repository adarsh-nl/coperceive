# Validate

`coperceive validate <dataset-root>` reads every schema 1 scene directory under that root and writes a JSON report and a Markdown report. It does not import the Griffin converter. The subset name comes from each agent's `platform` map.

```bash
coperceive validate /path/scenes --json report.json --markdown report.md
coperceive validate /path/scenes --maps /path/maps --strict
```

Relative `--json` and `--markdown` paths are written from the current directory. The defaults are `coperceive-validate.json` and `coperceive-validate.md`. A root that is not a directory exits 2 and writes nothing. A scene that fails to read is one row with `error` and does not stop the run. After both files are written the process exits 0. `--strict` exits 1 when `hard_failure_count` is not zero. The stdout line is `scenes N failed M missing_frames K warnings W hard H`.

Hard failures are an unreadable scene, a missing frame, or a cross-agent pose miss at `sync_tolerance_us`. Speed and acceleration stays warnings. A frame count that is not exactly 150 stays informational.

## What the report contains

Each scene, and the aggregate, record:

- Time sync. Per agent, the distribution of pose `dt`, the modal gap (a tie keeps the smaller gap), the rate, and missing frames. A gap larger than `gap_factor` times that modal gap counts `max(0, round(dt / nominal) - 1)` missing frames. Cross-agent queries use `sync_tolerance_us` (default 0). Nearest absolute `dt` uses `nearest_tolerance_us` (default 1 s) and is labelled separately.
- Altitude. UAV height above the vehicle is drone `z` minus vehicle `z` at shared timestamps. The expected band is the toolkit figure for that subset: 25±2 m, 40±2 m, 55±2 m, or 20–60 m. Those figures are the ones already recorded in [griffin-verification.md](design/griffin-verification.md) from `Dataset_Preparation.md`. They apply to the per-scene `cruise_altitude_m` field, not to every per-frame difference. Cruise samples and a climb or descent are reported separately when a phase is found.
- Pose sanity. Speed is the translation step divided by `dt`. Instantaneous acceleration is the change in that speed divided by the mean of the two neighbouring `dt` values. Smoothed speed is the trailing mean of instantaneous speed over `smooth_window_s` (0.5 s). Smoothed acceleration uses the same span on that mean. A step over 40 m/s, or either acceleration over 20 m/s², is a warning. The thresholds are written into the report.
- Cross-agent boxes. Shared track ids (two or more agents, one category) report median centre error, yaw disagreement, and length, width, and height disagreement. Yaw is the yaw of `Rz(yaw) @ Ry(pitch) @ Rx(roll)`. A body-frame box is composed with `lookup_pose` at `max_gap_us` 0. Pairs that do not share a track id are a fallback. The gate is `max(2 m, 0.5 * the longer box length)`. Those pairs are `computed`, `ground_truth` false, and are not written into `scene.objects`.
- Coverage. Track ids annotated by the vehicle only, the drone only, or both.
- Official counts. Scene count against 47, 54, 50, or 104. Frame count against “about 150”. A scene whose frame count is not exactly 150 is counted. That count is not a hard failure: the toolkit text is “about 150”, and this converter follows `scene_infos.json`.
- Centreline. With a maps root, each scene records the distance from vehicle poses to the driving-lane centre and to the OpenDRIVE reference line, the distance from vehicle-agent boxes to the lane centre, the heading error, and the pose `z`. The aggregate pools the poses and the boxes. `lane_centre_median_minus_claim_m` is that lane-centre median minus 0.24 m. The block is `not measured` when the maps root is missing or the scene's `map_name` does not resolve. It is not a hard failure. `--maps` selects the root. Otherwise `<root>/maps` is used when a child directory contains `manifest.json`.

A phase is the longest prefix (climb) or suffix (descent) whose median is at least 2 m below the median of the other frames, and at least 80% of whose steps move by at least 0.05 m in that direction. It must be at least 5 frames and at most 40% of the scene. Both ends matching is left as `none`.

## Griffin-25m dev tier

Command, from `/workspace`, on 2026-10-15. The scenes were the Phase 2 conversion at `/tmp/griffin-scenes` (47 directories). They were not re-downloaded. `validate_root` then `write_report` wrote `/tmp/coperceive-validate-25m.json` and `/tmp/coperceive-validate-25m.md`. Elapsed 5.737 s (`time.perf_counter` around that call, after the warning and gate changes). Those files are not in git.

| Check | Result |
| --- | --- |
| Scenes read / failed | 47 / 0 |
| Official 25 m scene count | 47 measured, 47 expected, match |
| Frames | 7,000. 45 scenes of 149, Town03-010 of 148, Town10HD-009 of 147. All 47 differ from exactly 150 |
| Missing frames | 0. Every pose gap is 100,000 µs, rate 10 Hz |
| Cross-agent misses at tolerance 0 | 0 of 7,000 queries. Nearest absolute `dt` is 0 |
| Hard failures | 0 |
| Speed | max 24.579697456127064 m/s (Town03-002). Warnings at 40 m/s: 0 |
| Instantaneous acceleration | max 26.239874369602163 m/s² (Town10HD-011). Warnings at 20 m/s²: 22 |
| Smoothed acceleration | trailing mean over 0.5 s. Max 15.712867500264949 m/s². Warnings at 20 m/s²: 0 |
| Shared tracks | 61,302 pairs. Centre error median 2.929642751054232e-14 m, max 4.576455661277233e-13 m. Yaw median 0 deg, max 1.0231815394945443e-12 deg. Length, width, and height error 0 |
| Computed pairs | 11, `ground_truth` false. Gate `max(2 m, 0.5 * longer length)`. Centre min 1.092018395040389 m, median 4.071134798976429 m, max 4.518411314835206 m |
| Coverage | vehicle only 40 tracks, drone only 406, both 834 |

Per-frame drone `z` minus vehicle `z`, 7,000 samples: min 16.811032561585307 m, p05 24.353514404036105 m, median 26.410151720046997 m, p95 28.216206969693303 m, max 29.66659187246114 m. Inside [23, 27] m: 4,520. Outside: 2,480.

`cruise_altitude_m` over the 47 scenes, taken from the same report with the validator's percentile: min 23.08411411 m, p05 23.29079752 m, median 25.23606834 m, p95 26.69679135 m, max 26.91940837 m. Every scene's recorded cruise altitude is inside 25±2 m.

Scene median height minus `cruise_altitude_m`: min −0.9936694535794466 m, median 1.243236409414063 m, max 2.0082581264744377 m. The flown height sits about 1.2 m above the recorded cruise value, so many frames land just above 27 m while the cruise field stays inside the band.

Phase labels: descent 1, none 46. The only labelled phase is Town06-001, a 38-frame descent (17 of its 149 samples outside [23, 27], `cruise_altitude_m` 24.51687333). Phase samples (38): min 16.811032561585307 m, median 23.507026061415672 m, max 26.08574439957738 m. Cruise-only samples (6,962): min 21.820821456611156 m, median 26.431367035023868 m, max 29.66659187246114 m.

The Phase 2 range 16.8–29.7 m, median 26.4 m, is this per-frame difference. It is not the toolkit's 25±2 m cruise band. The low tail is the Town06-001 descent. Town06-011 climbs, but the rise is under 2 m, so the phase rule leaves it as `none`. There is no dataset-wide takeoff.

The 22 instantaneous acceleration warnings are single steps over 20 m/s². The same steps, after a 0.5 s trailing mean of speed, stay at or below 15.712867500264949 m/s², so the smoothed check warns on none of them. The 40 m/s speed limit warns on nothing on this tier. `--strict` would exit 0 on this directory: hard failures are 0.

The earlier 5 m gate reported 46 computed pairs. The size-scaled 2 m floor leaves 11. Those 11 are still labelled computed.

## Centreline on Griffin-25m

Same scenes, 2026-10-15, with `--maps /tmp/coperceive-maps` after `coperceive maps align`. The report is `/tmp/coperceive-validate-25m.json`. It is not in git. Scenes measured 47, skipped 0, status `measured`. The maps were CARLA 0.9.15 OpenDRIVE, sample step 1.0 m, transform `identity`.

| Check | Result |
| --- | --- |
| Vehicle pose to lane centre | 7,000 poses. min 6.541333573178878e-07 m, p05 8.874367090333857e-05 m, median 0.013712643507518118 m, p95 0.662319574473314 m, max 1.7440808533399281 m |
| Median minus 0.24 m | −0.22628735649248188 m. The 0.24 m figure is the earlier claim. This difference is not a gate |
| Vehicle pose to reference line | 7,000 poses. min 0.005096134120426233 m, median 1.7860191236754484 m, max 12.27781525000623 m |
| Vehicle boxes to lane centre | 66,647 boxes. median 0.05214550975125978 m, p95 3.2797206330750615 m, max 46.31949753942487 m |
| Heading error | median 0.20308361215973036 deg, p95 8.561008872962589 deg, max 161.4437770562339 deg |

Per-town pose medians from the alignment, which are the same lane-centre distances: Town03 0.013712643507518118 m, Town06 0.0019174357069388736 m, Town07 0.1647962688524098 m, Town10HD 0.03035693727363501 m. The full candidate table is in [ADR 0008](adr/0008-hd-map.md).

The 40 m, 55 m, and random subsets were not validated.
