# Viewer

Phase 5 draws a schema 1 scene in the local page and can write the same samples to a Rerun `.rrd`. The desktop shell is a plan in [ADR 0007](adr/0007-desktop.md). It is not built.

## Page

```bash
coperceive serve --root /path/to/scene-parent --maps /path/to/maps
```

Open `http://127.0.0.1:8731/`. The Viewer tab is the default. Scene directories are the direct children of `--root` that contain `manifest.json`.

The page shell is a light control surface (`#F4F4ED`, borders `#D8D8C7`) around a dark rounded viewport. A dark theme keeps the same viewport and darkens the panels. The choice is stored with the other view settings. Light is the default. Geist is the UI face and Geist Mono is used for changing numbers. The top bar holds the scene, the Viewer and Desk tabs, export, the theme, and a settings popover for the bearer token. The token stays in `sessionStorage`.

The left panel has layers, agents, classes, and display settings. The right panel shows speed, heading, altitude for a UAV, and the point count. The timeline under the view has a `#DE3B21` playhead, ticks, sparklines for points, speed, and visible boxes, and markers when a sweep gap is wider than 150 ms or an agent enters or leaves an 80 m horizontal range of the vehicle.

Lidar, box wires, and agent trails are on a bloom layer (`UnrealBloomPass`). The map and the ground grid stay matte. A UAV gets a vertical line to `z = 0` and an HTML label with its id and altitude. Viewpoint changes ease with a 0.48 s time constant. Pressing Chase again returns to orbit. `prefers-reduced-motion: reduce` still snaps.

The selected camera is a draggable picture-in-picture panel on the viewport, with a compass, speed, heading, and timestamp, and with boxes and lidar projected on the image. Export is one dialog for a sequence, a still, the bird's-eye plot, the camera overlay, and Rerun.

The scrubber steps through recorded pose times. A time that is not a sample is omitted. Poses use `max_gap_us` 0, so the page does not interpolate. Play shows the sample that matches elapsed time at `(t[i+1] − t[i]) / speed`. Image decoding does not schedule steps. A draw that runs long skips to the sample the clock has reached. The sample index does not run ahead of that clock. Speeds are 0.25×, 0.5×, 1×, 2×, and 4×. A non-positive gap uses 100 ms. Loop waits one gap on the last sample, then wraps. Without loop, playback pauses on the last sample. Left and Right step one sample and pause. The frame JSON and point sweeps are kept for 16 frames and 48 sweeps, and the next four samples are requested after each draw. Hiding an agent does not drop that cache.

Each camera with `fy` and `cy` is a frustum: vertical field of view `2 * atan(cy / fy)`, aspect `width / height` when both sizes are set, otherwise `cx / cy`. The lines are 4 m deep in the sensor frame, with +Z forward. The world is z-up. Vehicle agents are drawn in `#F58A1F` and UAV agents in `#3C8DFF`. A checkbox hides that agent's points, boxes, frustums, and axes. The status line counts points, boxes, and frustums for agents that are still shown.

The first visit to a scene, in orbit, places the camera on the vehicle pose, or on the median point when that pose is missing. Points farther than 80 m in the horizontal plane are left out of that frame. The target is the centre of the remaining box. The camera sits at `span` along x, `-span` along y, and `0.75 * span` along z, with `span = max(box diagonal * 0.55, 18)`. Later scrubs keep that orbit camera until the viewpoint changes. The town map is not part of the frame, so a map that spans hundreds of metres does not zoom the view out.

Viewpoints: orbit (free, `camera.up` world +Z), bird's-eye (48 m above the vehicle, `camera.up` world +Y), chase (12 m behind body +X and 5 m up, looking 8 m ahead), drone (2 m behind the UAV and 1 m up, looking at the vehicle), and camera (the sensor pose). The view direction is the sensor +Z axis. `camera.up` is the sensor −Y axis. On Town03-000 at 1118500000 µs the vehicle front camera's +Y in the world is (0, −0.005, −1), so −Y is up and the road sits at the bottom of that view. The vertical field of view is the frustum's. The near plane is 0.05 m. Orbit controls are off in every locked view. A mode change eases over a 480 ms time constant. A new scene snaps. `prefers-reduced-motion: reduce` snaps every frame. Keyboard: Space, arrows, `L`, `[` `]`, and `1` through `5`. Keys are ignored in a text field, select, or while Space is repeating a focused button. Click the view first.

Lane centres (`#7dcea0`) and road edges (`#9aa7b2`) are drawn under the lidar when the scene's `map_name` resolves under the maps root. Pass `--maps`, or put the directories in `<root>/maps`. A missing map leaves the status ending in `no map.` The page requests `./favicon.svg`. `GET /favicon.ico` returns that same SVG.

Lidar points are 2 CSS pixels and do not shrink with distance. A 6 pixel size filled the dense part of a Griffin vehicle sweep. The page talks to the server only over HTTP. Rebuild the static files from `web/` with `npm run build` after a UI change. `npm` needs `--legacy-peer-deps` because `openapi-typescript` 7.13.0 declares a TypeScript 5 peer and this app uses TypeScript 6.

## HTTP

| Method | Path | Body |
| --- | --- | --- |
| GET | `/api/scenes` | `{scene_id}` rows |
| GET | `/api/scenes/{scene_id}` | agents, sensors, `times_us` |
| GET | `/api/scenes/{scene_id}/frame?time_us=` | poses, box lines, frustums, point-sensor counts |
| GET | `/api/scenes/{scene_id}/points?time_us=&agent_id=&sensor_id=` | little-endian float32 `x, y, z` in the world frame |
| GET | `/api/scenes/{scene_id}/image?time_us=&agent_id=&sensor_id=` | the image file recorded for that sample, or 404 |
| GET | `/api/scenes/{scene_id}/map` | whether a sidecar map is linked |
| GET | `/api/scenes/{scene_id}/map/lines` | world-frame polylines, or 404 |
| GET | `/favicon.ico` | `favicon.svg` as `image/svg+xml` |
| POST | `/api/scenes/{scene_id}/export/rerun` | `{path, samples}` |
| POST | `/api/scenes/{scene_id}/export/sequence` | `{path, frames, width, height, fps, format}` for one camera |
| POST | `/api/scenes/{scene_id}/export/view` | the same fields for uploaded canvas frames |
| POST | `/api/scenes/{scene_id}/export/bev` | bird's-eye PNG or SVG: `{path, format, time_us, width, height, map_drawn, points, boxes}` |
| POST | `/api/scenes/{scene_id}/export/overlay` | camera image with projected boxes and lidar, same fields |

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

## Camera images

The frame document's frustum rows also carry `image_width_px`, `image_height_px`, `fx`, `fy`, `cx`, and `cy`. A draggable picture-in-picture panel shows the selected camera at the current sample. The other cameras are buttons on that panel. `GET /api/scenes/{id}/image` resolves the path stored in `images.parquet` and confines it to that scene directory. A path that leaves the scene is 400. A missing row or a missing file is 404. The panel then says `No image file for this sample.`

Boxes and lidar are drawn on the image in the browser. The pixel is `u = fx * x / z + cx`, `v = fy * y / z + cy`, with `z` the camera-frame depth. `z <= 0` is not drawn. The strip sends each file to a worker, which decodes a 640 px wide bitmap. When that bitmap's size differs from `image_width_px` and `image_height_px`, the projected pixels are scaled onto the bitmap. The route still returns the original file. The 3D view redraws when the sample, the map, or the camera changes. It does not render a new frame on every display refresh while the view is still. The Boxes checkbox and the Lidar checkbox both start on. Hiding an agent hides that agent's boxes and points on the image as well. The next sample's images are requested after the current ones load, and a second request for the same file waits on the first. The strip keeps the last complete sample on screen until the next images arrive, so during play it can sit a sample or two behind the 3D view instead of flashing empty. A 404 is remembered until the scene changes, so a file that appears later needs a reload.

## Sequence export

Each recorded sample in the inclusive range is one frame. `fps` is the file's playback rate. It does not drop or repeat samples. On Griffin's 100 ms pose gap, 10 fps is the recorded pace. A non-positive gap is not resampled; the sample is still one frame.

```bash
coperceive export sequence /path/scene --out /path/front.mp4 \
  --agent vehicle --sensor front --start-us 1118500000 --end-us 1119400000 \
  --fps 10 --width 960 --height 540
```

`--format` is `mp4`, `gif`, or `png`. When it is omitted, `.mp4`, `.gif`, and `.png` select the format, and any other path is a directory of `frame_000001.png` onward. A `.png` file holds one frame. Width and height of 0 keep the image size. Both edges must be from 2 to 1920, and odd values are rounded down to an even size so MP4 can use `yuv420p`. The image path is confined to the scene directory. A missing file fails the export.

MP4 runs `ffmpeg` from `PATH` with `shell` false. GIF and PNG use Pillow. A missing `ffmpeg` leaves GIF and PNG working.

The page has the same camera export, plus **This view**, which steps the 3D canvas and posts those bitmaps. This view accepts at most 60 frames, because the bitmaps are uploaded. A camera export reads the files on the server and accepts 600. `POST /api/scenes/{id}/export/sequence` is the camera. `POST /api/scenes/{id}/export/view` is the canvas, with base64 PNG frames. Both write under `exports/` through `confine`, and both still require `X-Coperceive-Request: 1`. Rerun export is unchanged.

## View settings

The row under export toggles lidar points, boxes, frustums, the map, and agent axes. Color is by agent type or by box class. Point size is 1, 2, 3, or 4 CSS pixels. Boxes are a wireframe or a translucent solid with the wire kept. Categories in the current sample can be hidden. Theme is dark or light. The choices are stored in `localStorage` under `coperceive-view`. That object has no token. The bearer token stays in `sessionStorage` under `coperceive-token`. Reset view restores the defaults: every layer on, color by agent, 2 px points, wire boxes, dark, no hidden class.

Class color is used for boxes only. Points and frustums stay colored by agent, because a sweep has no class. The photo strip keeps its own Boxes and Lidar checkboxes and does not read this row.

## Bird's-eye and camera overlay

```bash
coperceive export bev /path/scene --out /path/bev.svg --maps /path/maps --time-us 1118500000
coperceive export overlay /path/scene --out /path/front.png \
  --agent vehicle --sensor front --time-us 1118500000
```

`--format` is `png` or `svg`. When it is omitted, the `--out` suffix selects it. The bird's-eye axes are east (+X) to the right and north (+Y) up. The window is the shown agents' trails, the boxes, and lidar within 120 m, with padding. It is not the whole town. Lane centres and road edges are drawn when `--maps` (or `<scene>/../maps`) resolves the scene's `map_name`. The camera figure is the stored image with the pinhole projection on top. `z <= 0` is omitted. A path that leaves the scene directory is refused.

The page buttons are **Bird's-eye** and **Camera overlay**. They use the current sample and the view settings (layers, theme, colour, hidden classes, hidden agents). The file is written under `exports/` and downloaded. PNG and SVG are both offered. The 3D bird's-eye viewpoint is unchanged.

`POST /api/scenes/{id}/export/bev` and `POST /api/scenes/{id}/export/overlay` return `{path, format, time_us, width, height, map_drawn, points, boxes}`. Both still require `X-Coperceive-Request: 1`.

## What this phase does not do

It does not draw radar, semantic or instance labels, or depth colour, and it does not show exposure or provenance, because the Griffin-25m dev tier does not carry those fields. It does not open a second viewer port or package a Tauri or Electron app. It does not load Griffin recordings or OpenDRIVE bytes into the repository.
