# Accessibility Statement

Coperceive is committed to providing an accessible, usable experience for all researchers and developers.

## Current Accessibility Features

The following accessibility standards and features are implemented and tested in Coperceive:

### Automated Audits in CI
- **Automated Axe Checks**: User interface pages are checked using `@axe-core/playwright` during automated CI runs across both dark and light themes to prevent common accessibility regressions (ARIA roles, color contrast, accessible names, and form labels).

### Color Contrast & Theming
- **Semantic Contrast Tokens**: Design system color tokens are calibrated to satisfy WCAG AA contrast thresholds (minimum 4.5:1 for body text, 3:1 for large text and interactive components) in both dark and light modes.
- **System Preference Detection**: The application detects and respects system-level dark/light mode and reduced-motion preferences by default, with manual override available in **Settings**.

### Keyboard Navigation & Shortcuts
- **Full Keyboard Navigation**: All primary workflows outside the 3D viewport can be operated via keyboard:
  - **Tables & Lists**: `Up` / `Down` arrow keys to navigate rows; `Enter` to open or trigger actions; `Escape` to dismiss inspectors or modals.
  - **Search & Filters**: Press `/` to focus search filters instantly.
  - **Playback Controls**: `Space` to play/pause scene playback; `[` and `]` to step playback speed down/up; `P` and `N` to navigate to the previous or next scene.
  - **Camera Viewpoints**: Number keys (`1` through `5`) switch between camera viewpoints (Orbit, Bird's-Eye View, Chase, Drone, and Camera Frustum).
  - **Application Settings**: `Cmd+,` (macOS) or `Ctrl+,` (Windows/Linux) to access settings.
- **Focus Rings**: Focusable interactive elements feature high-contrast focus rings when navigated via keyboard.

---

## Known Limitations

- **3D WebGL Canvas**: The interactive 3D viewport uses Three.js and WebGL to render high-density LiDAR point clouds and bounding boxes. Individual points in a point cloud are rendered on a `<canvas>` element and cannot be directly exposed to screen readers.
  - *Mitigation*: Numerical telemetry, agent counts, point counts, bounding box dimensions, classes, and distances are exposed as accessible semantic text in the collapsible side panels and inspectors.
- **Real-Time Video Filmstrip**: Fast camera playback in the filmstrip may present rapid visual changes. Users can pause playback or use single-step controls (`Step`) to advance sample-by-sample.

---

## Reporting Accessibility Barriers

If you encounter accessibility barriers, difficult contrast, or keyboard traps while using Coperceive, please let us know:

- **GitHub Issues**: Open an issue using our [Bug Report](https://github.com/adarsh-nl/coperceive/issues/new?template=bug_report.yml) template and label or title it with `[a11y]`.
- **Direct Contact**: You can also reach project maintainers at `<CONTACT>`.

We review accessibility feedback and prioritize fixes in our release cycle.
