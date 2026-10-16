# Viewer feature inventory

This list is the public description of py123d's viewer and plotting tools. It is the checklist for the coperceive page. The py123d source tree, configs, and UI were not opened. Names below are the feature names from those pages, not a layout to copy.

Our page stays a three.js view talking to `coperceive serve` over HTTP ([ADR 0007](../adr/0007-desktop.md)). Viser is a dependency of the Rerun extra and is not the page.

## Sources

- Documentation home, "Visualization tools with matplotlib and Viser": <https://kesai.eu/py123d/>
- README, built-in visualization (interactive 3D viewer via Viser at `http://localhost:8080`, matplotlib plotting) and the changelog entries below: <https://github.com/kesai-labs/py123d/blob/main/README.md>
- Paper section 3.2: an interactive 3D viewer for point clouds, images, bounding boxes, and map elements, plus matplotlib utilities for 2D plots such as a bird's-eye view and camera overlays. <https://arxiv.org/abs/2605.08084>
- Camera datatype page: an RGB image, a global extrinsic, `project_points_global` / `project_to_image`, and an exposure factor when a dataset provides one. <https://kesai.eu/py123d/reference/datatypes/sensors/01_camera/>

README changelog entries that name a viewer or plot feature:

| Version | Feature names |
| --- | --- |
| 0.7.0 | Camera strip with real camera names. Stabilized playback. MP4 export. Camera exposure and provenance metadata. Rigid follow-camera with a default viewpoint. |
| 0.6.0 | Radar visualization. Semantic and instance segmentation rendering. |
| 0.5.0 | Matplotlib observation rendering. |
| 0.4.0 | Color and rendering options. Configurable output resolution and frame rate. |
| 0.2.0 | Matplotlib camera-rig and lidar-reprojection utilities. |
| 0.1.0 | More control. Dark mode. |

The paper's body frame is ISO 8855 (x forward, y left, z up) and its camera frame is OpenCV (x right, y down, z forward). Griffin poses in this tree use +X forward in the world frame for the vehicle yaw. That Griffin fact is ours; it is recorded in `docs/datasets/griffin.md` and is not taken from py123d.

## What we implement, and where

| Documented feature | Status |
| --- | --- |
| Interactive 3D viewer: point clouds, boxes, map elements | Done. Lane centres and road edges stay under the lidar. |
| Playback | Done. Play, pause, 0.25×–4×, loop, step, keyboard, scrub. The clock uses the recorded gap between pose times. The page prefetches the next samples. |
| Follow camera | Done. Chase sits behind the vehicle. A separate drone view sits by the UAV and looks at the vehicle. |
| Bird's-eye viewpoint | Done. This is a 3D camera 48 m above the vehicle. It is not the matplotlib figure. |
| Look through a camera | Done. The 3D view uses the frustum pose, vertical field of view, and aspect. The RGB frame is the camera strip, not a texture on the frustum. |
| Free orbit | Done. The first visit to a scene still frames the near vehicle cluster. |
| Dark mode | Done. Light is the default. The view settings store dark or light. |
| Images, camera strip, project boxes or lidar onto the image | Done. One panel per camera at the current sample. Boxes and lidar are toggles. `HF_TOKEN` stays in the environment. Image bytes are not committed. |
| MP4, and a frame sequence at a chosen resolution and frame rate | Done, from the page and from the CLI. GIF and PNG frames are included. Each recorded sample is one frame, and `fps` is how fast the file plays them. Rerun `.rrd` export stays. |
| Color, point size, box style, per-layer toggles, category filters | Done. Settings live in `localStorage` under `coperceive-view` and must not include the bearer token. The token stays in `sessionStorage`. |
| Matplotlib bird's-eye plot | Done. `coperceive export bev` and the page write PNG or SVG for the current sample: map, boxes, lidar, and agent trails. East is +X, to the right. North is +Y, up. |
| Matplotlib camera-overlay plot | Done. `coperceive export overlay` and the page write the stored image with boxes and lidar projected by the pinhole, as PNG or SVG. |
| Radar visualization | Not done. The Griffin-25m dev tier this page is checked against has no radar sweeps. The schema already allows a radar sensor. No radar layer until a scene has radar sweeps. |
| Semantic and instance segmentation, depth colorization | Not done. The Griffin-25m dev tier does not carry those labels. No palette is invented for labels we do not have. |
| Exposure and provenance metadata | Not done. Shown only once image samples carry those fields. Griffin dev metadata does not. |

## Playback and viewpoints

Playback speeds are 0.25, 0.5, 1, 2, and 4. At 1× the delay from sample *i* to sample *i+1* is `(t[i+1] − t[i]) / 1000` milliseconds. A non-positive gap falls back to 100 ms, which is the Griffin 10 Hz step. The clock follows elapsed time. A late draw skips to the sample that elapsed time has reached. It does not play ahead of that clock. Loop waits that same gap on the last sample, then wraps. Without loop, playback pauses on the last sample.

Keyboard, ignored while a text field, select, or textarea is focused: Space play/pause, Left/Right step one sample and pause, `L` loop, `[` and `]` speed, `1` orbit, `2` bird's-eye, `3` chase, `4` drone, `5` camera.

Viewpoints:

- Orbit. User orbit after the near-cluster frame. `camera.up` is world +Z.
- Bird's-eye. Camera 48 m above the vehicle, looking down. `camera.up` is world +Y so the view is not degenerate.
- Chase. 12 m behind body +X and 5 m up, looking 8 m ahead. `camera.up` is world +Z.
- Drone. 2 m behind the UAV's body +X and 1 m up, looking at the vehicle. If that sample has no UAV pose, the camera holds and the status says so.
- Camera. The three.js camera sits on the sensor pose. The view direction is sensor +Z. `camera.up` is sensor −Y. On Griffin Town03-000 at 1118500000 µs the vehicle front camera's +Y points down, world (0, −0.005, −1), so −Y is up. Field of view is the frustum's vertical angle. Near plane is 0.05 m. Orbit controls are off.

Mode changes ease the camera with a 180 ms time constant. `prefers-reduced-motion: reduce` snaps. A new scene snaps, so the camera does not fly across towns. Follow views keep easing while the recording plays.

The frame JSON and the point buffers are cached in memory (16 frames, 48 sweeps) and the next four samples are requested after each draw. Hiding an agent does not drop the cache. The status line still counts only agents that are shown.

## Figures

The bird's-eye figure and the camera overlay are matplotlib, not a second 3D view. Both use the sample the scrubber is on. The figure follows the view settings: which agents are shown, whether points, boxes, and the map are on, color by agent or class, wire or solid boxes, hidden classes, and dark or light. Trails are every recorded pose of a shown agent. Lidar farther than 120 m in the horizontal plane from those agents is left out of the bird's-eye figure, so a town-sized map does not set the frame. The window is square, east to the right and north up, padded around the trails, boxes, and nearby points. Lane centres and road edges use the same colors as the 3D map. Class color is boxes only. Points and trails stay colored by agent type: vehicle `#e67828`, UAV `#2878dc`. Lidar marks in an SVG are rasterized; the map, boxes, and trails stay vectors. The camera figure scales `u` and `v` when the file size differs from `image_width_px` and `image_height_px`. `z <= 0` is not drawn. A path that leaves the scene directory is refused. `POST /api/scenes/{id}/export/bev` and `POST /api/scenes/{id}/export/overlay` write under `exports/` through `confine` and still require `X-Coperceive-Request: 1`.

Sequence export writes the samples from a start time through an end time. The page can save the current sample as a PNG still, or the range as MP4, GIF, or a PNG directory, from the live 3D view or from one camera. The CLI writes one camera. 10 fps is the Griffin recorded pace. The live view is limited to 60 frames. A camera export can hold 600.

`GET /api/scenes/{scene_id}/image` returns the file named in `images.parquet` for that sample. The path is confined to the scene directory. A path that leaves the scene is 400. A missing row or a missing file is 404. The strip shows one panel per camera, with Boxes on and Lidar off. It keeps the last complete sample until the next images arrive. The pixel is `u = fx * x / z + cx`, `v = fy * y / z + cy`. When the decoded image size differs from `image_width_px` and `image_height_px`, those pixels are scaled onto the file. `z <= 0` is not drawn. The next sample's images are requested after the current ones. No shell. Bind and token rules are unchanged.
