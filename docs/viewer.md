# Viewer

Phase 5 draws a schema 1 scene in the local page and can write the same samples to a Rerun `.rrd`. The desktop shell is a plan in [ADR 0007](adr/0007-desktop.md). It is not built.

## Page

```bash
coperceive serve --root /path/to/scene-parent
```

Open `http://127.0.0.1:8731/`. The Viewer tab is the default. Scene directories are the direct children of `--root` that contain `manifest.json`.

The scrubber steps through recorded pose times. A time that is not a sample is omitted. Poses use `max_gap_us` 0, so the page does not interpolate. Camera images are not drawn. Each camera with `fy` and `cy` is a frustum: vertical field of view `2 * atan(cy / fy)`, aspect `width / height` when both sizes are set, otherwise `cx / cy`. The lines are 4 m deep in the sensor frame, with +Z forward. The world is z-up. Vehicle agents are drawn in `#e67828` and UAV agents in `#2878dc`. A checkbox hides that agent's points, boxes, frustums, and axes.

Lidar points are 6 CSS pixels and do not shrink with distance, so a sparse sweep stays visible. The page talks to the server only over HTTP. Rebuild the static files from `web/` with `npm run build` after a UI change. `npm` needs `--legacy-peer-deps` because `openapi-typescript` 7.13.0 declares a TypeScript 5 peer and this app uses TypeScript 6.

## HTTP

| Method | Path | Body |
| --- | --- | --- |
| GET | `/api/scenes` | `{scene_id}` rows |
| GET | `/api/scenes/{scene_id}` | agents, sensors, `times_us` |
| GET | `/api/scenes/{scene_id}/frame?time_us=` | poses, box lines, frustums, point-sensor counts |
| GET | `/api/scenes/{scene_id}/points?time_us=&agent_id=&sensor_id=` | little-endian float32 `x, y, z` in the world frame |
| POST | `/api/scenes/{scene_id}/export/rerun` | `{path, samples}` |

A scene id that is empty, contains a slash or backslash, or contains `..` is 400. A missing `manifest.json` is 404. Point bytes are empty when that sweep is absent. The server keeps one decoded scene in memory and replaces it when the path changes.

`POST` still requires `X-Coperceive-Request: 1`. When `--token` is set, `/api/*` requires `Authorization: Bearer`. Auth is checked before that header, so a POST with no token is 401 and a token without the header is 403. The export writes `exports/<scene_id>.rrd` under the server root, through `confine`.

## Rerun

```bash
coperceive export rerun /path/scene --out /path/scene.rrd
```

The recording uses the timeline name `coperceive`, in seconds. Transforms and boxes use Rerun's xyzw quaternion. The JSON pose on the HTTP API stays `(qw, qx, qy, qz)`. `rr.init` uses `spawn=False` and the application id `coperceive`. Before init, the command runs `rerun analytics disable` from the same environment when that binary is present. A missing `manifest.json` exits 2. A missing `viewer` extra exits 2 with `RerunUnavailable`.

`coperceive.viewdata` does not import Rerun. `export_rerun` imports it inside the function. The `[viewer]` extra is `rerun-sdk==0.22.1` and `viser==0.2.23`. viser is not the page.

## What this phase does not do

It does not draw camera images, open a second viewer port, or package a Tauri or Electron app. It does not load Griffin recordings into the repository.
