# Viewer

Phase 5 draws a schema 1 scene in the local page and can write the same samples to a Rerun `.rrd`. The desktop shell is a plan in [ADR 0007](adr/0007-desktop.md). It is not built.

## Page

```bash
coperceive serve --root /path/to/scene-parent --maps /path/to/maps
```

Open `http://127.0.0.1:8731/`. The Viewer tab is the default. Scene directories are the direct children of `--root` that contain `manifest.json`.

The scrubber steps through recorded pose times. A time that is not a sample is omitted. Poses use `max_gap_us` 0, so the page does not interpolate. Play waits `(t[i+1] − t[i]) / speed` between samples. Speeds are 0.25×, 0.5×, 1×, 2×, and 4×. A non-positive gap uses 100 ms. Loop waits one gap on the last sample, then wraps. Without loop, playback pauses on the last sample. Left and Right step one sample and pause. The frame JSON and point sweeps are kept for 16 frames and 48 sweeps, and the next four samples are requested after each draw. Hiding an agent does not drop that cache.

Camera images are not drawn. Each camera with `fy` and `cy` is a frustum: vertical field of view `2 * atan(cy / fy)`, aspect `width / height` when both sizes are set, otherwise `cx / cy`. The lines are 4 m deep in the sensor frame, with +Z forward. The world is z-up. Vehicle agents are drawn in `#e67828` and UAV agents in `#2878dc`. A checkbox hides that agent's points, boxes, frustums, and axes. The status line counts points, boxes, and frustums for agents that are still shown.

The first visit to a scene, in orbit, places the camera on the vehicle pose, or on the median point when that pose is missing. Points farther than 80 m in the horizontal plane are left out of that frame. The target is the centre of the remaining box. The camera sits at `span` along x, `-span` along y, and `0.75 * span` along z, with `span = max(box diagonal * 0.55, 18)`. Later scrubs keep that orbit camera until the viewpoint changes. The town map is not part of the frame, so a map that spans hundreds of metres does not zoom the view out.

Viewpoints: orbit (free, `camera.up` world +Z), bird's-eye (48 m above the vehicle, `camera.up` world +Y), chase (12 m behind body +X and 5 m up, looking 8 m ahead), drone (2 m behind the UAV and 1 m up, looking at the vehicle), and camera (the sensor pose). The view direction is the sensor +Z axis. `camera.up` is the sensor −Y axis. On Town03-000 at 1118500000 µs the vehicle front camera's +Y in the world is (0, −0.005, −1), so −Y is up and the road sits at the bottom of that view. The vertical field of view is the frustum's. The near plane is 0.05 m. Orbit controls are off in every locked view. A mode change eases over a 180 ms time constant. A new scene snaps. `prefers-reduced-motion: reduce` snaps every frame. Keyboard: Space, arrows, `L`, `[` `]`, and `1` through `5`. Keys are ignored in a text field, select, or while Space is repeating a focused button. Click the view first.

Lane centres (`#7dcea0`) and road edges (`#9aa7b2`) are drawn under the lidar when the scene's `map_name` resolves under the maps root. Pass `--maps`, or put the directories in `<root>/maps`. A missing map leaves the status ending in `no map.` The page requests `./favicon.svg`. `GET /favicon.ico` returns that same SVG.

Lidar points are 2 CSS pixels and do not shrink with distance. A 6 pixel size filled the dense part of a Griffin vehicle sweep. The page talks to the server only over HTTP. Rebuild the static files from `web/` with `npm run build` after a UI change. `npm` needs `--legacy-peer-deps` because `openapi-typescript` 7.13.0 declares a TypeScript 5 peer and this app uses TypeScript 6.

## HTTP

| Method | Path | Body |
| --- | --- | --- |
| GET | `/api/scenes` | `{scene_id}` rows |
| GET | `/api/scenes/{scene_id}` | agents, sensors, `times_us` |
| GET | `/api/scenes/{scene_id}/frame?time_us=` | poses, box lines, frustums, point-sensor counts |
| GET | `/api/scenes/{scene_id}/points?time_us=&agent_id=&sensor_id=` | little-endian float32 `x, y, z` in the world frame |
| GET | `/api/scenes/{scene_id}/map` | whether a sidecar map is linked |
| GET | `/api/scenes/{scene_id}/map/lines` | world-frame polylines, or 404 |
| GET | `/favicon.ico` | `favicon.svg` as `image/svg+xml` |
| POST | `/api/scenes/{scene_id}/export/rerun` | `{path, samples}` |

A scene id that is empty, contains a slash or backslash, or contains `..` is 400. A missing `manifest.json` is 404. Point bytes are empty when that sweep is absent. The server keeps one decoded scene in memory and replaces it when the path changes.

`POST` still requires `X-Coperceive-Request: 1`. When `--token` is set, `/api/*` requires `Authorization: Bearer`. Auth is checked before that header, so a POST with no token is 401 and a token without the header is 403. The export writes `exports/<scene_id>.rrd` under the server root, through `confine`.

## Rerun

```bash
coperceive export rerun /path/scene --out /path/scene.rrd
```

The recording uses the timeline name `coperceive`, in seconds. Transforms and boxes use Rerun's xyzw quaternion. The JSON pose on the HTTP API stays `(qw, qx, qy, qz)`. `rr.init` uses `spawn=False` and the application id `coperceive`. Before init, the command runs `rerun analytics disable` from the same environment when that binary is present. A missing `manifest.json` exits 2. A missing `viewer` extra exits 2 with `RerunUnavailable`.

`coperceive.viewdata` does not import Rerun. `export_rerun` imports it inside the function. The `[viewer]` extra is `rerun-sdk==0.22.1` and `viser==0.2.23`. viser is not the page.

## Map lines blob

Little-endian. A `uint32` polyline count, then for each polyline a `uint32` layer code, a `uint32` vertex count, and float32 `x, y, z`. Code 1 is the lane centre, code 2 is the road edge. The reference line is stored in the map directory and is not in this blob. The transform `world_from_opendrive` has already been applied.

## What this phase does not do

It does not draw camera images, export an MP4, or store view settings. Those are the next slices in [viewer-features.md](design/viewer-features.md). It does not open a second viewer port or package a Tauri or Electron app. It does not load Griffin recordings or OpenDRIVE bytes into the repository.
