# Changelog

## 0.0.0

The Griffin licence acknowledgement uses `timezone.utc`, which Python 3.10 provides. `datetime.UTC` does not exist there.

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
