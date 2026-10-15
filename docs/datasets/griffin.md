# Griffin

Phase 2 reads the public Griffin release and writes schema 1 scenes. Recordings are not committed. The measurements below that name a byte count, a digest, or a residual were taken on this machine on 2026-10-15 unless the sentence points at an earlier page.

Source repository: [wang-jh18-SVM/Griffin](https://github.com/wang-jh18-SVM/Griffin). Hugging Face dataset: `wjh-svm/Griffin`, files under `datasets/`. The toolkit `LICENSE.txt` is MIT, `Copyright (c) [2025] [Jiahao Wang]`. The dataset card YAML says `license: mit`. That is not permission to commit the recordings. See `THIRD_PARTY_NOTICES.md`.

## Commands

```bash
coperceive download griffin --subset 25m --preset dev --dest /path/outside/the/repo --accept-griffin-terms
coperceive download griffin --subset 25m --components metadata,lidar --dest /path/outside/the/repo --dry-run
coperceive convert griffin --subset 25m --raw-root /path/griffin-release --out /path/scenes
```

`--subset` is `25m`, `40m`, `55m`, or `random`. Those names map to `griffin_50scenes_25m`, `griffin_50scenes_40m`, `griffin_50scenes_55m`, and `griffin_100scenes_random`.

`--components` is a comma-separated list. `metadata` means `drone_metadata` and `vehicle_metadata`. `lidar` means `vehicle_lidar`. Any other token is an archive basename taken from the Hub tree (`drone_camera_front`, and so on). `--preset dev` selects `drone_metadata`, `vehicle_metadata`, and `vehicle_lidar`. Pass one of `--preset` or `--components`.

`--dry-run` asks the Hub tree for each selected file's size and prints those integers. It does not download, extract, or write the acknowledgement.

## Where the files go

```
<dest>/griffin_50scenes_25m/
  md5.txt
  DOWNLOAD.txt
  archives/
  reassembled/          # only a split archive
  griffin-release/
    vehicle-side/
    drone-side/
```

`coperceive convert` reads `--raw-root` as that `griffin-release` directory. One output directory is one scene and holds both agents. The layout of a scene is `docs/spec/format.md`.

A leading `griffin-release/` segment inside a zip is stripped, as is any wrapper directory in front of `vehicle-side` or `drone-side`. On the 25 m dev tier the members are `griffin_50scenes_25m/griffin-release/<side>/...`, and that prefix is removed. The raw member names from the zip central directory are appended to `zip-members.txt` before the extract.

## MD5

`md5.txt` has a line for itself. On the 25 m file measured earlier, that line lists `904ee8d88c0d1db9878337298697306a` and the file's digest is `1cbc937bd675d2bee7c42919415e3782` (`docs/design/griffin-verification.md`). The downloader ignores the self-line and checks every payload it fetched. `md5sum -c md5.txt` still fails on the self-line, and on any archive that was not downloaded.

A local payload whose digest already matches is not fetched again. A local file that does not match is deleted and fetched again. `md5.txt` itself cannot be resumed by its self-line. It is kept when it already names every selected payload.

## Split archives

On `griffin_100scenes_random`, the camera RGB archives are split. The tree listing on 2026-10-15 has `*.z01` of 26,843,545,600 bytes plus a closing `*.zip` for each of `drone_camera_{front,back,left,right,bottom}` and `vehicle_camera_{front,back,left,right}`. Metadata, LiDAR, and instance-segmentation archives in that subset are a single zip. The 25 m, 40 m, and 55 m trees have no `.z01`.

Parts are concatenated in order `.z01`, `.z02`, …, then `.zip`, which is the same byte stream `zip -s 0` writes. The result is opened with `zipfile` and `testzip()` before extract. The dev preset does not select these parts. The reassembly path is covered by a synthetic archive in the unit tests. The 25 GiB parts were not downloaded here.

## Disk

Before the first byte is written, free space must cover the download plus a second copy of each compressed archive. A split archive also needs a third copy for the reassembled zip. That 2× figure is a lower bound. ASCII PLY expands by more than that, so each zip is opened and the sum of `ZipInfo.file_size` is required again before that zip is extracted.

## Licence acknowledgement

The download reads the dataset card and `LICENSE.txt` from the toolkit repository. It refuses to write dataset bytes unless the card's YAML `license` is `mit` and the toolkit file contains `MIT License` and `Copyright (c) [2025] [Jiahao Wang]`.

The acknowledgement is one file per machine:

`$XDG_DATA_HOME/coperceive/griffin-licence-accepted`

or `~/.local/share/coperceive/griffin-licence-accepted` when `XDG_DATA_HOME` is unset. `--accept-griffin-terms` writes it. Later runs reuse it. `--dry-run` does not write it. The file stores the licence lines and a UTC timestamp. It does not store a token.

## Token

`HF_TOKEN` is read from the environment if it is already set. Otherwise the keyring service `huggingface`, username `HF_TOKEN`, is tried. The value is passed as the `token` argument to `huggingface_hub`. If neither source has one, the argument is `False`, so a cached Hub login is not used. The token is not put on the command line, written into the repo, or copied into `DOWNLOAD.txt` or the acknowledgement. A missing token is fine while the dataset accepts anonymous reads.

## Conversion

Scenes are converted in parallel, at most 8 processes. A scene directory with a `COMPLETE.json` whose frame list matches `scene_infos.json` is left untouched. A `.partial/<scene_id>/` directory that already has that marker is renamed into place. Any other partial or incomplete directory is removed and built again. One scene's exception does not delete scenes that already finished. The process exits non-zero, and the next run retries the unfinished scenes.

`COMPLETE.json` is not part of schema 1. `read_scene` ignores it.

Both agents get the same platform strings: `subset` (the short name, such as `25m`), `map_name`, and `cruise_altitude_m`.

When `vehicle-side/lidar/lidar_top/` exists, every frame listed for the scene must have a cloud. A metadata-only tree, with no LiDAR directory, writes zero clouds. A missing pose or label for a listed frame fails that scene.

## Conventions

Boxes are stored in the agent body frame (`frame: body`). Poses are `world ← body`. The world frame name on the scene is `ENU`.

### Frame ids and time

`scene_infos.json` lists frames. The pose, label, and LiDAR file stem is that integer, zero-padded to six digits. The pose JSON field `timestamp` is the same integer. Microseconds are `frame_id * 100000`. That scale is the 10 Hz lidar rate used in `docs/design/griffin-verification.md`. The JSON has no wall-clock field. The assumption is copied into the scene clock note. This phase did not re-open the paper PDF.

`Dataset_Preparation.md` says each scene is about 15 seconds and about 150 frames. The 25 m metadata measured earlier has 47 scenes (the folder name says 50): 45 scenes of 149 frames, one of 148, and one of 147, summing to 7,000. The converter uses the list in `scene_infos.json`. It does not assume 150.

### Boxes

A 12-field label line is `type x y z l w h roll pitch yaw id visibility`. Translations and `l, w, h` are metres. Roll, pitch, and yaw are degrees. The toolkit's `ObjectData` docstring says the box is in ego coordinates, which this store calls the body frame.

An 11-field line is the same without visibility. The toolkit fills visibility with `1.0`. This converter stores null, so a missing measurement is not invented.

A 9-field line is `type x y z l w h yaw id`. The toolkit's branch for that width indexes the raw string (`line[0]`, `line[1]`, …) rather than the split tokens, and it fills roll, pitch, and visibility. This converter splits the tokens, uses roll 0 and pitch 0, and stores visibility as null. Those zeros are the toolkit's fill-in, not a measured angle.

The field count is taken per line. The toolkit takes it from the first line of the file and then applies that width to every line. The error string in `load_label` says it expected 9 or 12, while the branch above that also accepts 11.

### Ego, world, and axes

The toolkit's `PoseData` docstring says the ego pose is in an ENU frame: `x` East, `y` North, `z` height from the ground, metres. Roll positive means left side up. Pitch positive means nose down. Yaw 0 means East, and positive yaw is counterclockwise.

`trans_ego_vs_ENU` builds that pose with `Rotation.from_euler('xyz', [roll, pitch, yaw], degrees=True)`. That scipy sequence is `R = Rz(yaw) @ Ry(pitch) @ Rx(roll)`, which is `SE3.from_rpy_deg`. The same call is used for an object box in ego coordinates.

The extrinsic in a calibration JSON is the sensor-to-ego matrix (`trans_sensor_vs_ego` returns it as `T_sensor_to_ego`). The scene stores that as `body ← sensor`. A matrix under `intrinsic` is the camera pinhole; image width and height are `2 * cx` and `2 * cy` rounded, which is the same stand-in the Phase 0 reader used. Image bytes are not in the dev tier, so pixel size was not measured from a PNG.

Which horizontal axis is east is the toolkit comment above. It was not checked against a map. The earlier Town03-000 sample showed vehicle and drone boxes that share a track id landing on the same world point to 5.7e-14 m, which says the two agents share one frame. It does not say which axis is east. The shared-track residual for this conversion is in the run section below.

### LiDAR

`Dataset_Preparation.md` says lidar is `.bin` and that the drone has no lidar directory. The 25 m vehicle archive measured earlier contains ASCII PLY (`x`, `y`, `z`, `I` as float32) and no `.bin`. The converter reads that PLY. Intensity is the fourth float, stored little-endian in `points.bin` as schema 1 requires. There is no drone lidar archive in the 25 m tree.

## This run

Commands, from `/workspace`, on 2026-10-15. `HF_TOKEN` was unset. The Hub printed that the requests were unauthenticated. The download still finished.

```bash
.venv/bin/coperceive download griffin --subset 25m --preset dev \
  --dest /tmp/griffin-phase2 --accept-griffin-terms
.venv/bin/coperceive convert griffin --subset 25m \
  --raw-root /tmp/griffin-phase2/griffin_50scenes_25m/griffin-release \
  --out /tmp/griffin-scenes
```

Download elapsed 13 s (`SECONDS` around that command). Convert elapsed 5 s the same way, 4 workers (`min(8, nproc)`; this machine has 4 CPUs). A second convert of the same output skipped all 47 scenes in 0.029 s (`time.perf_counter` around `convert_griffin`).

`md5sum` of the files just downloaded:

| File | Digest |
| --- | --- |
| md5.txt | `1cbc937bd675d2bee7c42919415e3782` |
| drone_metadata.zip | `48e23147f1acf0761fb42340f677ad8c` |
| vehicle_metadata.zip | `e119733a51a0ee8125017f986d26833d` |
| vehicle_lidar.zip | `f044c8f1671bdfe4da21fc405abe8da7` |

The self-line in `md5.txt` still lists `904ee8d88c0d1db9878337298697306a`. Payload lines match the three zip digests. Tree sizes were 898, 14,384,997, 10,876,283, and 214,487,013 bytes. The pre-download free-space requirement was 479,497,484 bytes, which is those four sizes plus the three zip sizes again.

Extracted member counts from the central directories: drone metadata 14,012 entries (14,009 files), vehicle metadata 14,009 entries (14,006 files), vehicle LiDAR 7,002 entries (7,000 `.ply` files, 0 `.bin`). One sampled cloud, `021960.ply`, is ASCII with `property float32` x, y, z, I and 2,699 vertices. `du -sb` of `/tmp/griffin-phase2` was 880,896,098 bytes, zips included.

`scene_infos.json` on the two sides are equal. 47 scenes. Frame counts: 45 × 149, `Town03-010` × 148 (frames 014046–014193), `Town10HD-009` × 147 (frames 026768–026914). Sum 7,000. `Town03-000` is frames 11185–11333. Map counts: Town03 13, Town06 12, Town07 10, Town10HD 12. `cruise_altitude` min 23.084114109076907 m, median 25.236068336378246 m, max 26.919408368893848 m.

Label lines: 177,697, every one 12 fields. No 11-field or 9-field line in this subset.

The converter wrote 47 directories, 7,000 clouds, 19,922,961 points. `du -sb /tmp/griffin-scenes` was 333,097,959 bytes. `read_scene` on every directory: both agents, schema 1, world frame `ENU`. Drone platform on `Town03-000` is `subset=25m`, `map_name=Carla/Maps/Town03`, `cruise_altitude_m=25.7331649`. That scene has 149 poses, 149 clouds, and 40 shared objects.

Shared-track world residual (`track_center_spread_m`, `max_gap_us=0`) over 813 tracks that occur on both agents at one timestamp: min 1.5694642960426368e-14 m, median 8.533456124013766e-14 m, max 4.576455661277233e-13 m. The earlier 15-frame Town03-000 sample was 5.7e-14 m. This is the full 25 m conversion. It is still roundoff. It still does not say which axis is east.

Per-frame pose `z`, 7,000 frames. Drone: min 16.798789978027344 m, median 26.5300931930542 m, max 34.67467498779297 m. Drone minus vehicle: min 16.811032561585307 m, median 26.410048661287874 m, max 29.66659187246114 m. The scene `cruise_altitude` stays near 25 m. The per-frame separation does not.

Drone calibration includes `lidar_bottom.json`, `lidar_front.json`, and `lidar_virtual.json`. They have an extrinsic and no intrinsic, so they are stored as lidar sensors. No drone point file exists, and no cloud is attached to them. Vehicle sensors are the four cameras plus `lidar_top`.

The acknowledgement file is `/home/ubuntu/.local/share/coperceive/griffin-licence-accepted`. It records the MIT lines and `acknowledged_utc=2026-10-15T10:33:46Z`. It has no token.

Not re-measured here: the 40 m and 55 m trees, the random-subset `.z01` bytes (the names and sizes were listed; the parts were not fetched), image pixels, and a map check of the east axis. The slow test `tests/test_griffin_phase2.py::test_real_griffin_release_loads` passed with `COPERCEIVE_GRIFFIN_RAW` pointed at this release. Without that variable the same test is skipped.

## Validation

`coperceive validate /tmp/griffin-scenes` was run again on this conversion on 2026-10-15 after the warning and gate changes. Elapsed 5.737 s. The aggregate is in `docs/validate.md`. The per-frame drone-minus-vehicle height (min 16.811032561585307 m, median 26.410151720046997 m, max 29.66659187246114 m) is wider than 25±2 m. Every scene's `cruise_altitude_m` is inside that band. The toolkit band is the cruise field. The low tail is a descent on Town06-001, 38 frames. Missing frames on this tier: 0. Hard failures: 0. Instantaneous acceleration warnings: 22. Smoothed acceleration warnings over 0.5 s: 0 (max 15.712867500264949 m/s²). Shared-track centre error stays at roundoff (median 2.929642751054232e-14 m over 61,302 pairs). Computed pairs under the size-scaled gate: 11.
