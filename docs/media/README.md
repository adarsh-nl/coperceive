# Media Assets and Attribution

This directory contains media assets for documentation and visual demonstration of the Coperceive viewer.

## Assets

- `Town03-000.png`: Screenshot of the Coperceive viewer rendering a multi-agent sequence (vehicle and UAV) in CARLA Town03 with 3D bounding boxes, LiDAR points, camera frustums, and OpenDRIVE lane geometry.
- `Town03-000-split.png`: Screenshot of the Coperceive viewer with split camera view and 3D scene.
- `hero_recording.mp4`: H.264 video (`yuv420p`, `+faststart`) showing the dynamic scene playback, vehicle LiDAR sweeping, drone camera view, and cooperative 3D box detections.

## Sources and Attribution

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
