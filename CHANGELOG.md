# Changelog

## 0.0.0

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
