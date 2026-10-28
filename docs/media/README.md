# Media Assets and Attribution

This directory contains media assets for documentation and visual demonstration of the Coperceive viewer.

## Assets

- `Town03-000.png`: Screenshot of the Coperceive viewer rendering a multi-agent sequence (vehicle and UAV) in CARLA Town03 with 3D bounding boxes, LiDAR points, camera frustums, and OpenDRIVE lane geometry.
- `Town03-000-split.png`: Screenshot of the Coperceive viewer with split camera view and 3D scene.
- `hero_recording.mp4`: H.264 video (`yuv420p`, `+faststart`) showing the dynamic scene playback, vehicle LiDAR sweeping, drone camera view, and cooperative 3D box detections.
- `connect-backdrop.png`: Screenshot of the desktop Connect window with the animated 1b "Ledger" cooperative perception bird's-eye view backdrop.
- `connect-backdrop.mp4`: H.264 screen recording (`yuv420p`, `+faststart`, 30 fps) demonstrating the live looping backdrop animation behind the Connect card (bus occlusion recovery, sight rays, vehicle orange and UAV blue LiDAR, object boxes, and timeline ribbon).

## Sources and Attribution

### Animated Connect Backdrop (1b "Ledger")
The Connect screen's animated background is a synthetic 2D canvas bird's-eye view visualization ported from the Coperceive UI design specification (`Viewer Directions.dc.html`, section `1b` "Ledger"). The scene illustrates cooperative vehicle-drone perception (a vehicle approaching a parked bus occluding three pedestrians, recovered by the drone's aerial vantage point):
- **Visual Design**: Coperceive Design Specifications (2026-10-18)
- **Data Conventions**: Griffin benchmark sensor and agent taxonomy (vehicle `#F58A1F`, UAV `#3C8DFF`, recovery pulse `#1FA3A3`, blocked sight lines `#8E6CEF`).

### Griffin Benchmark and Dataset
The cooperative aerial-ground multi-agent scenarios depicted in synthetic sequences use data conventions and models inspired by:
- **Paper**: "Griffin: Aerial-Ground Cooperative Detection and Tracking Dataset and Benchmark"
- **Authors**: Wang et al., 2025.
- **Reference**: [arXiv:2503.06983](https://arxiv.org/abs/2503.06983)
- **License**: MIT License

### CARLA Map Geometry & OpenDRIVE Assets
The lane centerlines, road boundaries, and town layout geometry visible in the viewer renderings are derived from CARLA Town OpenDRIVE maps (`Town03.xodr`):
- **Source**: CARLA Simulator (Computer Vision Center (CVC) at Universitat Autònoma de Barcelona (UAB)).
- **Website**: https://carla.org / https://github.com/carla-simulator/carla
- **License**: CARLA assets and OpenDRIVE maps are distributed under the **Creative Commons Attribution 4.0 International (CC-BY 4.0)** license.
  - Attribution: *CARLA Simulator © Computer Vision Center (CVC) at Universitat Autònoma de Barcelona (UAB), used under CC-BY 4.0.*

### Dataset Policy
Per `docs/datasets/opencood.md`, OPV2V and V2XSet assets have unpublished / unknown licensing terms and are strictly barred from public repository distribution. No assets or derivative media from OPV2V or V2XSet are committed to this repository or included in release artifacts.
