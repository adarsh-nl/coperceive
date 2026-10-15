# ADR 0003 — Viewer

Status: proposed. Phase 0 does not embed a 3D viewer. Adarsh confirms the choice before one is built.

## Context

The viewer has to show more than one agent: lidar, camera frustums, a shared scrubber, and a way to hide an agent. It also has to be reachable on a laptop and through a JupyterHub proxy or an SSH tunnel, and it has to work offline after the data is local.

Spike scene: Griffin-25m `Town03-000`. Camera image zips were not downloaded, so no image was decoded. Saved records:

- `docs/design/measurements/summary_partial.json` — Rerun 0.22.1 logged 149 frames in 0.582 s. The `.rrd` is 5,965,363 bytes and was left in `/tmp`.
- `docs/design/measurements/viser_ready.json` — viser 0.2.23 on `127.0.0.1:8766`, 22 frames, 9 frustums.
- `docs/design/measurements/three_result.json` — three.js 0.186.1, WebGL2, 22 frames, 2,929 points, 9 frustums. `requestAnimationFrame` reported 58.92 fps. Unthrottled render of that frame was 0.145 ms. This VM has no GPU. 58.92 is consistent with a refresh cap, not with a stress test.

A Chrome `requestAnimationFrame` count for the Rerun and viser pages was not written to a file. Those two numbers are not reported.

Rerun’s quaternion was passed as xyzw. Viser’s quaternion is wxyz. Frustums used the pinhole from the calib JSON and ignored distortion. Rerun analytics printed a first-run notice; `rerun analytics disable` was run after it. The notice said no usage data had been sent yet.

## Options

1. **Rerun.** Timeline, offline `.rrd`, Python logging. The viewer is a separate port. Licence on the repo is MIT OR Apache-2.0, with an open-core product around it. Embedding in our page means an iframe or a second proxy route.
2. **viser.** Python process, web UI, sliders and per-agent checkboxes in the spike server. Also a separate port. Apache-2.0. Jupyter and SSH tunneling are ordinary port forwards. A JupyterHub path prefix does not automatically apply to that second port unless a proxy route is added.
3. **three.js in this app.** Same origin as `pkg serve`, so the page’s relative URLs and `root_path` cover it. More UI work. The spike showed the scene can be drawn. Licence of three.js is MIT.

Foxglove Studio is archived and its open-source edition was discontinued (see the positioning note). MCAP remains a file format, not the viewer.

## Criteria

- Lidar and nine frustums (four vehicle cameras, five drone cameras), even without images.
- One scrubber.
- Per-agent visibility.
- A path that works behind a hub prefix and offline.
- A licence Adarsh can ship.

## Decision

Do not bundle a viewer in Phase 0. When one is built, draw it with three.js inside this web app, and keep a Rerun `.rrd` export for the timeline. viser stays an optional `[viewer]` extra for a notebook session, not the page behind the hub prefix.

The nine camera *images* are still unmeasured. Frustums are not video.

## Consequences

- `[viewer]` pins `rerun-sdk==0.22.1` and `viser==0.2.23` so the spike can be repeated. Importing them is not required to use the library.
- JupyterHub’s Tornado proxy has a default websocket message cap of 10 MiB (`tornado/websocket.py` on the `stable` branch: `_default_max_message_size = 10 * 1024 * 1024`). This app’s API is HTTP and server-sent events. Viser and Rerun open their own sockets. Whether a given hub raises that cap was not tested. A large point cloud over a viser socket can hit it. The in-app three.js path can send HTTP range or chunked reads instead.
- Offline use is the `.rrd` file or the static three.js page plus local data. Neither was tried through an SSH tunnel in this run.
