# Facts on the landing page

The page is the Claude Design file "Landing Motion" (imported 2026-10-27), built as designed at Adarsh's request. That decision replaced the earlier copy rules for this page: it now has a nav bar and GitHub links, names datasets and the licence, and shows two **simulations** with numbers drawn on them. This file records where each claim comes from and which numbers are simulated.

Checked against `main` at `0ee039d` on 2026-10-27.

## Simulations (not recorded data, not measured results)

- **Story canvas** (`site/src/landing/story.ts`): a generated city block (seeded), 3 cars and 2 drones, 24 objects. The HUD ("seen", "shared", "agents"), the per-agent speeds and altitudes, the lidar beam count and range, the camera footprint, track ids, the hover tooltips and the timeline's microsecond counter are all values of this simulation. Visibility is ray-cast against the generated buildings.
- **Evaluate canvas** (`site/src/landing/evalScene.ts`): a simulated drive with simulated ground truth and two simulated detector runs named "det-v1" and "det-v2". Matching (greedy by BEV IoU at the chosen threshold), hits, misses, false positives, precision and recall are computed live from those simulated boxes. No model was run; none of the numbers is a result of coperceive's evaluation on real data.

## Page claims

| Claim on the page | Source |
| --- | --- |
| Desktop app for Mac, Windows and Linux / Download for Mac / Windows / Linux (`DownloadPanel.tsx`) | Hero pill updated for `v0.1.2`; `#install` uses `site/src/download/DownloadPanel.tsx` to offer the visitor's platform installer from the latest GitHub Release (`https://api.github.com/repos/adarsh-nl/coperceive/releases/latest`). |
| Many agents. One scene. | Headline. The scene model holds N agents in one scene (`coperceive/model.py` `Scene.agents`). |
| Every car, drone and roadside unit in one world frame, on one clock. | `AgentType` is vehicle, uav, infrastructure, other (`coperceive/model.py`); one `world_frame` and one `Clock` per scene (`docs/spec/format.md`). |
| One car sees what's in front of it. … stays a grey outline. | Describes the simulation (captions 1–3 narrate the story canvas). |
| Each vehicle keeps its own sensors, poses and boxes. coperceive keeps them in one scene instead of one log per car. | `Agent.poses`, `Sensor.agent_id`, `Annotation` per agent in one `Scene` (`coperceive/model.py`). |
| Objects seen by more than one agent … only when the id is actually shared. | Shared objects group annotations by shared track id (`coperceive/diagnostics.py` `shared_objects`). On the canvas this is the simulation's rule. |
| Frames from every agent land on one integer-microsecond clock. Scrub it, hide an agent, export to Rerun or video. | `time_us` is an int in microseconds (`docs/spec/format.md`; `require_time_us`). Viewer playback and per-agent visibility (`HANDOVER.md`, State); Rerun `.rrd` export and MP4/GIF sequence export (`README.md`, `CHANGELOG.md`). |
| From raw logs to a shared view. One Python package. Runs on your workstation or behind a JupyterHub proxy. | `pyproject.toml` (one package); JupyterHub path-prefix support (`coperceive/cli.py`, `docs/adr/0004-web-app.md`). |
| 01 ingest — Any dataset, one manifest. A YAML file with units, rotations and axes written out. Griffin, OPV2V, V2XSet, V2V4Real and DAIR-V2X-C are built in. `adapters ingest my.yaml` | `docs/adr/0009-dataset-adapters.md`; built-in manifests under `coperceive/adapters/builtin/`; `README.md` (`adapters ingest`). |
| 02 validate — Check before you train. Time sync, pose speed, cross-agent box agreement and coverage, as JSON and Markdown. `validate ./scenes --strict` | `coperceive/validate.py`; `README.md`. |
| 03 view — Every agent in 3D. LiDAR, camera strips and HD-map lanes. Orbit, bird's-eye, chase, drone and camera views, or stream over SSH. `serve --root ./scenes` | `HANDOVER.md` (State); `docs/design/viewer-features.md`; `docs/datasets/cluster-datasets.md`. |
| 04 evaluate — Score in the world frame. Per-frame OpenCOOD-style or legacy AP protocol, cooperative breakdowns, and side-by-side run comparison. `eval compare v1 v2` | `docs/adr/0010-eval-predictions.md`; `docs/eval.md` (`PR #165`, `PR #211`). |
| What’s new in v0.1.2 (`#whats-new`) | `docs/release/v0.1.2-notes.md` (`PR #150`, `#155`, `#165`, `#183`, `#207`, `#209`, `#211`, `#213`, `#220`, `#244`, `#245`, `#247`, `#248`, `#250`). |
| Three commands to your first scene. (`pip install -e ".[app,griffin]"`, `coperceive convert griffin --subset 25m --raw-root ./griffin-release --out ./scenes`, `coperceive serve --root ./scenes`, `→ http://127.0.0.1:8731`) | `README.md` install and quick start; default port 8731 in `coperceive/cli.py`. The `convert` line matches `coperceive convert griffin --help` on `feat/m2-import` at `75640be` (`--subset`, `--raw-root` and `--out` are required); `./griffin-release` is the extracted release directory, as in `README.md`. |
| Python 3.11–3.13. Apache-2.0. Local by default — nothing leaves your machine. | `README.md` ("Python 3.11 to 3.13"); `pyproject.toml` `requires-python = ">=3.11"`, `license = "Apache-2.0"`; the server binds loopback unless `--token` is set (`HANDOVER.md`, Phase 5 choices). |
| Score every detection where it happened. Predictions match the agent's own ground truth in the world frame by BEV IoU. | `docs/adr/0010-eval-predictions.md`; BEV IoU at 0.5 and 0.7 (`README.md`). The canvas beneath is a simulation (above). |
| Read the docs / View on GitHub / GitHub | Links to `https://github.com/adarsh-nl/coperceive/tree/main/docs` and `https://github.com/adarsh-nl/coperceive`. The design left them as `#`. |
| multi-agent perception data | Footer tagline from the design. |

## Kept from the earlier rules

- Fonts are self-hosted (`@fontsource-variable/geist`, `geist-mono`); the design's Google Fonts link was not used. The page fetches nothing from another origin (asserted in `tests/test_landing_page.py`).
- No analytics, trackers or third-party scripts.
- No viewer screenshots, `.xodr` or dataset bytes are committed. The design project's `web/public/landing/*.png` were not used.

## Tour screenshots (`site/public/tour/`)

Every file in `site/public/tour/` is captured by `scripts/site/tour/run_capture.py` (using `scripts/site/tour/desk.js`, `viewer.js`, `datasets.js`, `stream.js`) against `coperceive serve` serving only generated `demo` or `synthetic` data—never real dataset frames, checkpoints, or CARLA bytes:

| Screen key | File(s) | Source (`demo` / `synthetic`) | How generated / seeded |
| --- | --- | --- | --- |
| `library` | `library.webp` | `synthetic` | `desk.js` seeded library table (`demo-junction`, `scene-0001`, `scene-0002`, `Town03-000`) |
| `openfolder` | `openfolder.webp` | `synthetic` | `desk.js` seeded local folder paths (`~/coperceive-data/*`) |
| `dataset` | `dataset.webp` | `synthetic` | `desk.js` seeded 44-scene `demo dataset (synthetic)` (`scene_0000`–`scene_0043`) |
| `neweval` | `neweval.webp` | `synthetic` | `desk.js` seeded `Lab GPU cluster` and custom `my-detector-eval` pipeline |
| `history` | `history.webp` | `synthetic` | `desk.js` seeded 3 synthetic jobs (`700214`, `700198`, `700001`) |
| `orbit` | `orbit.webp` | `demo` | `viewer.js` on `rich_demo.py` (`coperceive.demo.generate`) + `second_run.py` |
| `bev` | `bev.webp` | `demo` | `viewer.js` Bird's-eye view on `demo-junction` |
| `compare` | `compare.webp` | `demo` | `viewer.js` comparing `demo` vs `baseline` synthetic runs on `demo-junction` |
| `scrolled` | `scrolled.webp` | `demo` | `viewer.js` scrolled to Compare menu on `demo-junction` |
| `loop_orbit` | `loop_orbit.mp4`, `loop_orbit.webp` | `demo` | `viewer.js` 80 stepped frames of `demo-junction` (Orbit) |
| `loop_chase` | `loop_chase.mp4`, `loop_chase.webp` | `demo` | `viewer.js` 80 stepped frames of `demo-junction` (Chase) |
| `getdata_all` | `getdata_all.webp` | `synthetic` | `datasets.js` seeded from `coperceive dataset catalog` metadata |
| `getdata_opv2v` | `getdata_opv2v.webp` | `synthetic` | `datasets.js` catalog entry for OPV2V |
| `getdata_v2xset` | `getdata_v2xset.webp` | `synthetic` | `datasets.js` catalog entry for V2XSet |
| `getdata_dair` | `getdata_dair.webp` | `synthetic` | `datasets.js` catalog entry for DAIR-V2X-C |
| `getdata_v2v4real` | `getdata_v2v4real.webp` | `synthetic` | `datasets.js` catalog entry for V2V4Real |
| `view_opv2v` | `view_opv2v.webp` | `synthetic` | `datasets.js` opening `variants.py` synthetic `opv2v-style-junction` |
| `view_v2xset` | `view_v2xset.webp` | `synthetic` | `datasets.js` opening `variants.py` synthetic `v2xset-style-junction` |
| `view_dair` | `view_dair.webp` | `synthetic` | `datasets.js` opening `variants.py` synthetic `dair-style-intersection` |
| `view_v2v4real` | `view_v2v4real.webp` | `synthetic` | `datasets.js` opening `variants.py` synthetic `v2v4real-style-highway` |
| `view_griffin` | `view_griffin.webp` | `synthetic` | `datasets.js` opening `variants.py` synthetic `griffin-style-aerial` |
| `stream` | `stream.webp` | `synthetic` | `stream.js` streaming `variants.py` `opv2v-style-junction` over `--remote-stream-argv` |

## Not on this page any more

- The recorded hero clip (`site/public/hero/clip.bin`), its poster and the `three` player. The files stay in the tree; this page does not load them.
- The waitlist form. The design has none.
