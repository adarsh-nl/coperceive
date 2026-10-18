# Viewer

Phase 5 draws a schema 1 scene in the local page and can write the same samples to a Rerun `.rrd`. The desktop shell is a plan in [ADR 0007](adr/0007-desktop.md). It is not built.

## Page

```bash
coperceive serve --root /path/to/scene-parent --maps /path/to/maps --predictions /path/to/predictions
```

Open `http://127.0.0.1:8731/`. The Viewer tab is the default. Scene directories are the direct children of `--root` that contain `manifest.json`. Two more flags exist for a staged dataset: `--static DIR` serves a web build from that directory instead of the packaged `coperceive/app/static` (a directory without `index.html` exits 2), and `--media-root DIR` (repeatable) names a directory a scene's image links may resolve into, so a scene that links its `vehicle-side/camera` to the dataset copy serves those files; without it an image outside the scene directory is 400 as before. Both are described under [Staging the val scenes for the viewer](datasets/griffin.md#staging-the-val-scenes-for-the-viewer).

The page shell is a light control surface (`#F4F4ED`, borders `#D8D8C7`) around a dark rounded viewport. A dark theme keeps the same viewport and darkens the panels. The choice is stored with the other view settings. Light is the default. Geist is the UI face and Geist Mono is used for changing numbers. The top bar holds the scene, the Viewer and Desk tabs, export, the theme, and a settings popover for the bearer token. The token stays in `sessionStorage`.

The left panel has layers, agents, classes, and display settings. The right panel shows speed, heading, altitude for a UAV, and the point count. Agents are labelled by their `display_name` (`Vehicle 1`, `Drone 1`, `RSU 1`, generated at ingest) in the agent list, the telemetry cards, the camera strip and the split view; the raw `agent_id` is the hover text there and is printed in the selected-box card, and a scene without names shows the raw id. The timeline under the view has a `#DE3B21` playhead, ticks, sparklines for points, speed, and visible boxes, and markers when a sweep gap is wider than 150 ms or an agent enters or leaves an 80 m horizontal range of the vehicle.

Lidar, box wires, and agent trails are on a bloom layer (`UnrealBloomPass`). The map and the ground grid stay matte. A UAV gets a vertical line to `z = 0` and an HTML label with its id and altitude. Viewpoint changes ease with a 0.48 s time constant. Pressing Chase again returns to orbit. `prefers-reduced-motion: reduce` still snaps.

Cameras live in a strip docked between the 3D view and the timeline, 96 px tall, one thumbnail per camera grouped by agent with the agent's colour on the group's edge; `C` collapses it to a one-line bar. Only thumbnails that are on screen request their file, at 160 px, and nothing is requested when the scene summary says no image is staged. Clicking a thumbnail opens a split view: the 3D view on the left at its full height, the camera on the right with the Boxes and Lidar overlays, a compass, speed, heading and timestamp, and a draggable divider between them (30–80 %). `Esc`, the close button or the same thumbnail closes it. The thumbnail and the split pane share one download per file. The strip and split state (`stripOpen`, `splitCamera`, `splitRatio`) are stored in `coperceive-view`. Nothing floats over the 3D view: the status pill, legend and missing-data pill are pointer-transparent, and the browser tests check that the canvas centre and corners hit the canvas. When the browser gives no WebGL context (blocked GPU, remote desktop), the viewport shows a notice naming the reason instead of a blank page; the scene list, timeline and tables keep working, and a browser test launches Chromium with WebGL disabled to check it. Export is one dialog for a sequence, a still, the bird's-eye plot, the camera overlay, and Rerun.

The scrubber steps through recorded pose times. A time that is not a sample is omitted. Poses use `max_gap_us` 0, so the page does not interpolate. Play shows the sample that matches elapsed time at `(t[i+1] − t[i]) / speed`. Image decoding does not schedule steps. A draw that runs long skips to the sample the clock has reached. The sample index does not run ahead of that clock. Speeds are 0.25×, 0.5×, 1×, 2×, and 4×. A non-positive gap uses 100 ms. Loop waits one gap on the last sample, then wraps. Without loop, playback pauses on the last sample. Left and Right step one sample and pause. The frame JSON and point sweeps are kept for 16 frames and 48 sweeps, and the next four samples are requested after each draw. Hiding an agent does not drop that cache.

Each camera with `fy` and `cy` is a frustum: vertical field of view `2 * atan(cy / fy)`, aspect `width / height` when both sizes are set, otherwise `cx / cy`. The lines are 12 m deep in the sensor frame, with +Z forward. Frustum images start off. The layer toggle and the opacity slider stay. Each camera can be turned on from the layer list, from its picture-in-picture chip, or by clicking that frustum. Only enabled cameras are decoded, at 360 px on a worker, and the texture is reused. The choice is stored in `coperceive-view`. The picture-in-picture panel stays. Looking through a camera hides that camera’s plane so it does not sit on the lens. The world is z-up. Vehicle agents are drawn in `#F58A1F` and UAV agents in `#3C8DFF`; infrastructure and other agents in `#b7b1a6`. The legend in the 3D view names the agent types the scene has, then Lane, then the status chips of an evaluated run. In the split view the lidar overlay is the camera's own agent's sweep only (another agent's sweep seen from the camera is a wall of points under the horizon), without points behind the camera or closer than 1 m, coloured by depth from orange near to blue at 60 m and beyond, as 1.6 px squares. A checkbox hides that agent's points, boxes, frustums, and axes. The status line counts points, boxes, and frustums for agents that are still shown.

The first visit to a scene, in orbit, places the camera on the vehicle pose, or on the median point when that pose is missing. Points farther than 80 m in the horizontal plane are left out of that frame. The target is the centre of the remaining box. The camera sits at `span` along x, `-span` along y, and `0.75 * span` along z, with `span = max(box diagonal * 0.55, 18)`. Later scrubs keep that orbit camera until the viewpoint changes. The town map is not part of the frame, so a map that spans hundreds of metres does not zoom the view out.

Viewpoints: orbit (free, `camera.up` world +Z), bird's-eye (48 m above the vehicle, `camera.up` world +Y), chase (12 m behind body +X and 5 m up, looking 8 m ahead), drone (2 m behind the UAV and 1 m up, looking at the vehicle), and camera (the sensor pose). The view direction is the sensor +Z axis. `camera.up` is the sensor −Y axis. On Town03-000 at 1118500000 µs the vehicle front camera's +Y in the world is (0, −0.005, −1), so −Y is up and the road sits at the bottom of that view. The vertical field of view is the frustum's. The near plane is 0.05 m. Orbit controls are off in every locked view. A mode change eases over a 480 ms time constant. A new scene snaps. `prefers-reduced-motion: reduce` snaps every frame. Keyboard: Space, arrows, `L`, `[` `]`, `1` through `5`, `C` for the camera strip and `Esc` to close the split camera. Keys are ignored in a text field, select, or while Space is repeating a focused button. Click the view first.

Lane centres (`#7dcea0`) and road edges (`#9aa7b2`) are drawn under the lidar when the scene's `map_name` resolves under the maps root. Pass `--maps`, or put the directories in `<root>/maps`. The page asks `GET /api/scenes/{id}/map` first, so each scene's map follows its own town: a Town06 scene after a Town03 scene draws Town06 or says so. A missing map leaves the status ending in `no map for Town06.` (the last segment of the scene's `map_name`), or `no map name in this scene.` when no agent names one. The page requests `./favicon.svg`. `GET /favicon.ico` returns that same SVG.

**Empty states.** When a pane has nothing to show, it says why instead of staying blank. The scene summary carries `point_sweeps`, `image_rows` and `images_staged` (whether a sample of the recorded image files exists; `null` without rows). A pill under the status pill in the viewport lists every gap, one per pane: `Points: no lidar staged.`, `Camera: no camera images staged.` or `Camera: no camera images in this scene.`, `Map: no map for Town06.`, `Predictions: no prediction runs for this scene.` or `Predictions: no predictions root; start the server with --predictions.` The Layers pane repeats the points and map lines under their toggles, the camera panel shows `No image: …` with the same wording (and then requests no image files for that scene, instead of one 404 per camera per sample), and the Predictions block shows the predictions line. A scene that has lidar, image files, a map and a run shows no pill.

The Predictions block in the left panel appears when a predictions root is found (`--predictions`, or `<root>/predictions` with a run in it). Run lists every run that has `boxes.parquet` for the scene; a run that was imported but not evaluated is named so and shows the `coperceive eval run` line instead of controls. For an evaluated run, Compare picks a second run as B, Output picks the `agent · source` the scene card and the timeline follow, and three chip rows filter what is drawn: sources (`single`, `fused`, …), statuses (`tp`, `fp`, `miss`, `ignored`, `below_threshold`, each with its count at the sample), and the four cooperative views (`Recovered`, `Lost by fusion`, `Only other agent`, `Nobody`, each with its scene count). The score slider starts at the run's `score_threshold` and hides predictions below it; a button returns to the run's value. The slider changes the frame counts, not AP. The legend draws each status line as it appears on the data layer, with the B pattern beside it in compare mode. Match rows are drawn with bloom like the ground-truth wires, in the colours and styles of [the predictions spec](spec/predictions.md#colours-and-styles); a matched ground-truth box is dimmed. Clicking a prediction fills the inspector with run, source, score, status, category, the matched track id and the pair's IoU, centre error and yaw error; clicking a ground-truth box adds its status per output and who else sees it. The timeline adds precision, recall and the FP count of the chosen output, with run B as a thinner line, and marks the samples whose recall is more than 0.3 below the scene median. The score is the only prediction setting stored in `coperceive-view`, as `predictionScore`. The commands behind the block are on [the evaluate page](eval.md).

Lidar points are 2 CSS pixels and do not shrink with distance. A 6 pixel size filled the dense part of a Griffin vehicle sweep. The page talks to the server only over HTTP. Rebuild the static files from `web/` with `npm run build` after a UI change. `npm` needs `--legacy-peer-deps` because `openapi-typescript` 7.13.0 declares a TypeScript 5 peer and this app uses TypeScript 6. CI's `web` job rebuilds the bundle and fails when the committed `coperceive/app/static` differs from it, so the packaged page is the current `web/src`. To view a build without committing it, build elsewhere and serve it: `npx vite build --outDir /some/dir --emptyOutDir` then `coperceive serve --static /some/dir …`.

## Browser tests

`tests/test_viewer_browser.py` opens the page in headless Chromium through Playwright (the `dev` extra; run `playwright install chromium` once) against a server started in the test: two scenes in two towns, one with lidar, an image file, a Town03 map and an evaluated run, one with thirty 10 Hz samples and none of that. The tests check that Play moves the sample index with the clock and pauses on the last sample, that choosing the other scene re-reads its frames, retakes the timeline and follows its own town's map in both directions, that the Predictions block names the run and the status chips carry the sample's counts, and that each pane names what is missing. `COPERCEIVE_WEB_DIST` points the tests at a `vite build` directory; without it they use the packaged bundle, which may be stale. `COPERCEIVE_E2E_SHOTS` keeps the screenshots (`play.png`, `scene-switch.png`, `predictions.png`, `empty-states.png`). Without Playwright, a browser that can start, or any bundle, the module is skipped and says why. CI's `e2e` job builds the bundle from `web/` and uploads the screenshots as an artifact.

## HTTP

| Method | Path | Body |
| --- | --- | --- |
| GET | `/api/scenes` | `{scene_id}` rows |
| GET | `/api/scenes/{scene_id}` | agents, sensors, `times_us`, `point_sweeps`, `image_rows`, `images_staged` |
| GET | `/api/scenes/{scene_id}/frame?time_us=` | poses, box lines, frustums, point-sensor counts |
| GET | `/api/scenes/{scene_id}/points?time_us=&agent_id=&sensor_id=` | little-endian float32 `x, y, z` in the world frame |
| GET | `/api/scenes/{scene_id}/image?time_us=&agent_id=&sensor_id=` | the image file recorded for that sample, or 404; a link that resolves outside the scene is 400 unless it lands under a `--media-root` |
| GET | `/api/scenes/{scene_id}/map` | whether a sidecar map is linked |
| GET | `/api/scenes/{scene_id}/map/lines` | world-frame polylines, or 404 |
| GET | `/api/scenes/{scene_id}/predictions` | `{runs: [{run_id, run, evaluated, config, outputs}]}`: every run under the predictions root with `boxes.parquet` for the scene |
| GET | `/api/scenes/{scene_id}/predictions/{run_id}/frame?time_us=&compare=` | `{run_id, time_us, display_threshold, score_threshold, rows, compare}`: the `matches.parquet` rows at that sample with `center`, `size_lwh`, `pose`, `lines`; 404 until `coperceive eval run` |
| GET | `/api/scenes/{scene_id}/predictions/{run_id}/series?compare=` | `{run_id, outputs: [{agent_id, source, times_us, tp, fp, miss, precision, recall}], compare}` from `metrics.json` |
| GET | `/favicon.ico` | `favicon.svg` as `image/svg+xml` |
| POST | `/api/scenes/{scene_id}/export/rerun` | `{path, samples}` |
| POST | `/api/scenes/{scene_id}/export/sequence` | `{path, frames, width, height, fps, format}` for one camera |
| POST | `/api/scenes/{scene_id}/export/view` | the same fields for uploaded canvas frames |
| POST | `/api/scenes/{scene_id}/export/bev` | bird's-eye PNG or SVG: `{path, format, time_us, width, height, map_drawn, points, boxes, predictions}`; an optional `run_id` draws that run's match rows |
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

The frame document's frustum rows also carry `image_width_px`, `image_height_px`, `fx`, `fy`, `cx`, and `cy`. The camera strip shows every camera at the current sample; the split view shows the chosen one large. `GET /api/scenes/{id}/image` resolves the path stored in `images.parquet` and confines it to that scene directory. A path that leaves the scene is 400. A missing row or a missing file is 404. The panel then says `No image file for this sample.`

Each visible thumbnail requests its file (and the next sample's) at 160 px; the split pane requests the chosen camera at 960 px from the same download; a collapsed strip requests nothing. (Requesting all nine Griffin cameras per sample queued behind the browser's six connections per host and, over a tunnel, held back the frame and scene requests for seconds.) Choosing another scene pauses playback at once for the same reason. The scene list shows about a dozen scenes before it scrolls. Boxes and lidar are drawn on the image in the browser. The pixel is `u = fx * x / z + cx`, `v = fy * y / z + cy`, with `z` the camera-frame depth. `z <= 0` is not drawn. Each file goes to a worker, which decodes a bitmap at the requested width. When that bitmap's size differs from `image_width_px` and `image_height_px`, the projected pixels are scaled onto the bitmap. The route still returns the original file. The 3D view redraws when the sample, the map, or the camera changes. It does not render a new frame on every display refresh while the view is still. The Boxes checkbox and the Lidar checkbox both start on. Hiding an agent hides that agent's boxes and points on the image as well. The next sample's images are requested after the current ones load, and a second request for the same file waits on the first. The strip keeps the last complete sample on screen until the next images arrive, so during play it can sit a sample or two behind the 3D view instead of flashing empty. A 404 is remembered until the scene changes, so a file that appears later needs a reload.

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

`POST /api/scenes/{id}/export/bev` and `POST /api/scenes/{id}/export/overlay` return `{path, format, time_us, width, height, map_drawn, points, boxes, predictions}`. Both still require `X-Coperceive-Request: 1`.

`coperceive export bev --run <run_id>` and a `run_id` in the bird's-eye POST draw the match rows of that evaluated run at the sample on top of the ground-truth boxes, in the status colours and line styles of [the predictions spec](spec/predictions.md#colours-and-styles); the legend names each status and a matched ground-truth box is dimmed to 40 %. The predictions root is `<scenes root>/predictions` when it holds a run, or `--predictions` (`coperceive serve --predictions`, `coperceive export bev --predictions`). Without one, the three prediction routes and a `run_id` answer 404 `no predictions root`.

## What this phase does not do

It does not draw radar, semantic or instance labels, or depth colour, and it does not show exposure or provenance, because the Griffin-25m dev tier does not carry those fields. It does not open a second viewer port or package a Tauri or Electron app. It does not load Griffin recordings or OpenDRIVE bytes into the repository.
