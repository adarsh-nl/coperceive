# Changelog

## 0.0.0

Dataset adapters. A YAML manifest describes a dataset's layout, readers, units, rotation encodings and axis conventions, and `coperceive adapters list|check|ingest` turns the raw tree into schema 1 scenes with the same parallel driver, `.partial/` directory and `COMPLETE.json` resume that Griffin used. Generic readers: JSON, YAML and CSV records, joined JSON tables, whitespace label rows, PLY, PCD and float32 BIN lidar, image folders. Plugins register under the `coperceive.adapters` and `coperceive.readers` entry points. Griffin ships as the built-in manifest `coperceive/adapters/builtin/griffin.yaml`; `tests/test_adapters_griffin.py` checks it writes byte-identical `points.bin`, equal Parquet tables and equal scenes against `coperceive convert griffin`, which is unchanged until that test has passed on the real release. `COMPLETE.json` records the adapter, the manifest SHA-256 and the coperceive version. The body (`FLU`) and camera (`RDF`) axis conventions are written into `docs/spec/format.md`; schema 1 is unchanged. `pyyaml` is a dependency. The walkthrough is `docs/datasets/add-a-dataset.md` and the design is `docs/adr/0009-dataset-adapters.md`.

Web UI. The page opens on a landing view, then the viewer. The top bar is one 32 px row: brand and scene, Viewer/Desk, then Export, theme, and settings. The right column adds a scene summary, a box inspector, and an 80 m radar. Camera frustum images start off. Each camera can be turned on from the layer list, its picture-in-picture chip, or a click on the frustum, and only those frames are decoded. The viewer is a light control shell around a dark rounded viewport, with a dark theme as well. Geist and Geist Mono are the faces. Layers, agents, and classes sit on the left. Telemetry sits on the right and is computed from pose deltas, in m/s and km/h. The timeline has a red playhead, filled sparklines, and range markers. Lidar, trails, and box wires use a selective bloom. A UAV has a ground leader and a glass label that stays inside the view. The selected camera is a draggable, resizable picture-in-picture panel. Red is reserved for the playhead and Play. Sequence, still, bird's-eye, camera overlay, and Rerun export share one dialog. Playback timing is unchanged. The bearer token stays in `sessionStorage`.

Bird's-eye and camera-overlay figures. `coperceive export bev` and `coperceive export overlay`, and the same two buttons on the page, write a PNG or SVG for one recorded sample. The bird's-eye figure draws the map, boxes, lidar, and agent trails with east to the right. The overlay draws the stored camera image and the pinhole projection. The CI lint job installs the `app` extra so pyright can resolve FastAPI in `tests/test_hdmap.py`.

View settings. The page can hide points, boxes, frustums, the map, and axes, color boxes by agent or class, set point size and box style, filter categories, and switch dark or light. The choices are in `localStorage` under `coperceive-view`. The bearer token stays in `sessionStorage`.

Sequence export. `coperceive export sequence` and the page write one camera's images, or the live 3D view, to MP4, GIF, or PNG at a chosen size and fps. Each recorded sample is one frame. MP4 uses `ffmpeg` on `PATH`. Rerun export is unchanged.

Camera images. The page shows one panel per camera at the current sample. `GET /api/scenes/{id}/image` returns the file named in `images.parquet`, confined to that scene directory. Boxes and lidar can be projected onto the image. Image bytes stay outside the repository. Playback follows elapsed pose time. A slow draw skips to the sample the clock has reached and does not run ahead of it.

Playback and viewpoints. The local page plays recorded pose times at 0.25×–4×, loops, steps, and scrubs, and prefetches the next samples. Viewpoints are orbit, bird's-eye, chase, drone, and look-through a camera frustum. The page uses a dark theme. The feature checklist is `docs/design/viewer-features.md`. Images, sequence export, and the customisation panel are not in this slice. Rerun export is unchanged.

Shared HD map. `coperceive maps fetch`, `maps ingest`, and `maps align` turn a CARLA OpenDRIVE file into a sidecar directory (`manifest.json`, `polylines.parquet`, `vertices.bin`) and store `world_from_opendrive`. Schema 1 is unchanged. The viewer draws lane centres and road edges under the lidar. `coperceive validate` reports vehicle-pose distance to the lane centre. The centreline block is not a hard failure. The status line ignores hidden agents, the first camera frame uses the near vehicle cluster, and `/favicon.ico` returns the SVG icon. The design is `docs/adr/0008-hd-map.md`.

Phase 5 viewer. The local page draws lidar, boxes, and camera frustums with three.js, one scrubber, and a per-agent hide control. Lidar points are 2 CSS pixels. `coperceive export rerun` and `POST /api/scenes/{id}/export/rerun` write a Rerun `.rrd` of the same discrete samples. The desktop packaging plan is `docs/adr/0007-desktop.md`. The shell is not built.

Phase 4 validation. `coperceive validate` writes a JSON report and a Markdown report for a directory of schema 1 scenes: time sync, UAV height above the vehicle, pose speed and acceleration, shared-track and nearest-centre agreement, coverage, and scene and frame counts against the toolkit figures. Speed above 40 m/s and acceleration above 20 m/s² are warnings. A 0.5 s trailing mean of speed is the smoothed acceleration check. The computed-pair gate is `max(2 m, 0.5 * the longer box length)` and stays labelled computed. The default exit is 0. `--strict` exits 1 on an unreadable scene, a missing frame, or a cross-agent pose miss. The Griffin-25m dev-tier numbers are in `docs/validate.md`.

CI installs the CPU torch wheel and typechecks `CooperativeDataset`. The Griffin licence acknowledgement uses `timezone.utc`, which Python 3.10 provides. `datetime.UTC` does not exist there.

Phase 3 cooperative queries. `Scene.at` returns every agent's pose, sensors, and boxes at one time, with per-agent `dt_us`. A miss is reported. Camera projection uses the pinhole and the rigid extrinsic, and reports behind-camera and out-of-image points without rounding. Cross-agent box queries label a distance match as computed, not ground truth, when the track id is not a shared object. `CooperativeDataset` is the `torch` extra: configurable agents, sensors, and sampling, with a per-worker lazy open.

Phase 2 Griffin download and conversion. `coperceive download griffin` fetches a subset from `wjh-svm/Griffin`. `coperceive convert griffin` writes one schema 1 scene for the vehicle and the drone. Conventions are in `docs/datasets/griffin.md`.

Phase 1 scene model and schema 1 hybrid store. The contract is `docs/spec/format.md`.

- Agent types `vehicle`, `uav`, `infrastructure`, `other`, plus a string platform map.
- Sensors `camera`, `lidar`, `radar` with `body ← sensor` and an optional camera pinhole.
- Boxes carry a class, an optional track id, an optional visibility, and a `body` or `world` frame.
- `T(dst ← src, t)` covers world, body, and sensor. No default gap. No silent extrapolation.
- Per-agent time queries report `dt_us` for `exact`, `nearest`, `backward`, and `forward`.
- Scene objects are exactly the track ids shared by two or more agents that agree on one class.

Phase 0 scaffold. The distribution, import, and command name are `coperceive`.

- Scene model, time queries, and `T(dst ← src, t)` with explicit interpolation limits.
- Hybrid Parquet plus float32 point blob.
- Griffin-25m reader and an allow-listed downloader for metadata and vehicle LiDAR.
- Local FastAPI app and a prebuilt browser page.
- Design notes and measurements. No training code.
