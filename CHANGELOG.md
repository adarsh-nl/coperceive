# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Cross-platform desktop foundation for Windows and Linux targets (PR #122).
- Automated CI test matrix and headless smoke test pipelines for `ubuntu-latest` and `windows-latest`.
- Push CI trigger support on `release/**` branches for integration testing.

### Changed
- Tauri desktop dependencies and build configuration are no longer macOS-only.
- System log directories, application data directories, and clipboard interactions abstracted into platform-specific implementations.

### Fixed
- Diagnostics reporting and zip bundle export made cross-platform with clipboard fallback and clear missing-tool errors.
- Release asset packaging tightened to copy only exact application bundle and DMG assets, excluding stray dependencies.

## [0.1.0] - 2026-11-05

### Added
- Native desktop application for macOS (Apple Silicon) built with Tauri and React.
- Dense, calm user interface matching the Claude Design system across Library, Get Data, Servers, and Dataset View.
- HPC Cluster Workspace: SSH remote directory browser, Slurm batch job array preparation, and live cluster job monitoring.
- Zero-Slurm-job cluster dataset viewing over long-lived SSH streaming pipe (`coperceive dataset stream`).
- Interactive 3D scene viewer with lidar point cloud playback, 3D bounding boxes, camera frustums, and multi-agent tracking.
- Multi-agent camera filmstrip with scrolling thumbnails, active-camera split view, and depth-colored lidar point projection.
- Offline and local dataset inspection, validation, and conversion for OPV2V, V2XSet, and Griffin datasets.
- Privacy-preserving crash capture and user-initiated diagnostics dialog with automated secret redaction (preview, clipboard copy, zip bundle export).
- In-app update notifications via sidebar card with release notes popover and restart-to-install workflow.

### Changed
- Redesigned scene viewer and dataset view layout to eliminate element overlap at all window sizes down to 900 × 600.
- Unified window toolbar on macOS with inline traffic light controls.
- Optimized cold raw file reads and YAML parsing during Slurm dataset preparation.

### Fixed
- Prevented point cloud and bounding box desynchronization during playback over high-latency SSH connections.
- Fixed camera frustum image plane poses to stay synchronized with moving agent poses.
- Fixed concurrent remote viewer start conflicts when sharing remote manifests.
