# Facts on the landing page

The page is the Claude Design file "Landing Motion" (imported 2026-10-27), built as designed at Adarsh's request. That decision replaced the earlier copy rules for this page: it now has a nav bar and GitHub links, names datasets and the licence, and shows two **simulations** with numbers drawn on them. This file records where each claim comes from and which numbers are simulated.

Checked against `main` at `0ee039d` on 2026-10-27.

## Simulations (not recorded data, not measured results)

- **Story canvas** (`site/src/landing/story.ts`): a generated city block (seeded), 3 cars and 2 drones, 24 objects. The HUD ("seen", "shared", "agents"), the per-agent speeds and altitudes, the lidar beam count and range, the camera footprint, track ids, the hover tooltips and the timeline's microsecond counter are all values of this simulation. Visibility is ray-cast against the generated buildings.
- **Evaluate canvas** (`site/src/landing/evalScene.ts`): a simulated drive with simulated ground truth and two simulated detector runs named "det-v1" and "det-v2". Matching (greedy by BEV IoU at the chosen threshold), hits, misses, false positives, precision and recall are computed live from those simulated boxes. No model was run; none of the numbers is a result of coperceive's evaluation on real data.

## Page claims

| Claim on the page | Source |
| --- | --- |
| The Mac app is coming soon. / coperceive for Mac is coming soon — same scenes, no terminal. | `docs/adr/0014-desktop-app.md` specifies a desktop app (Tauri, macOS first). `README.md` says this tree does not build a desktop shell. No date. |
| Many agents. One scene. | Headline. The scene model holds N agents in one scene (`coperceive/model.py` `Scene.agents`). |
| Every car, drone and roadside unit in one world frame, on one clock. | `AgentType` is vehicle, uav, infrastructure, other (`coperceive/model.py`); one `world_frame` and one `Clock` per scene (`docs/spec/format.md`). |
| One car sees what's in front of it. … stays a grey outline. | Describes the simulation (captions 1–3 narrate the story canvas). |
| Each vehicle keeps its own sensors, poses and boxes. coperceive keeps them in one scene instead of one log per car. | `Agent.poses`, `Sensor.agent_id`, `Annotation` per agent in one `Scene` (`coperceive/model.py`). |
| Objects seen by more than one agent … only when the id is actually shared. | Shared objects group annotations by shared track id (`coperceive/diagnostics.py` `shared_objects`). On the canvas this is the simulation's rule. |
| Frames from every agent land on one integer-microsecond clock. Scrub it, hide an agent, export to Rerun or video. | `time_us` is an int in microseconds (`docs/spec/format.md`; `require_time_us`). Viewer playback and per-agent visibility (`HANDOVER.md`, State); Rerun `.rrd` export and MP4/GIF sequence export (`README.md`, `CHANGELOG.md`). |
| From raw logs to a shared view. One Python package. Runs on your workstation or behind a JupyterHub proxy. | `pyproject.toml` (one package); JupyterHub path-prefix support (`coperceive/cli.py`, `docs/adr/0004-web-app.md`). |
| 01 ingest — Any dataset, one manifest. A YAML file with units, rotations and axes written out. Griffin, OPV2V and V2XSet are built in. `adapters ingest my.yaml` | `docs/adr/0009-dataset-adapters.md`; built-in manifests under `coperceive/adapters/builtin/`; `README.md` (`adapters ingest`). |
| 02 validate — Check before you train. Time sync, pose speed, cross-agent box agreement and coverage, as JSON and Markdown. `validate ./scenes --strict` | `coperceive/validate.py`; `README.md`. |
| 03 view — Every agent in 3D. Lidar, camera strips and HD-map lanes. Orbit, bird's-eye, chase, drone and camera views. `serve --root ./scenes` | `HANDOVER.md` (State); `docs/design/viewer-features.md`. |
| 04 evaluate — Score in the world frame. Match predictions per agent, put precision and recall on the timeline, overlay a second run. `eval compare v1 v2` | `docs/adr/0010-eval-predictions.md` line 9; `README.md` (`eval compare`). |
| Three commands to your first scene. (`pip install -e ".[app,griffin]"`, `coperceive convert griffin --subset 25m --raw-root ./griffin-release --out ./scenes`, `coperceive serve --root ./scenes`, `→ http://127.0.0.1:8731`) | `README.md` install and quick start; default port 8731 in `coperceive/cli.py`. The `convert` line matches `coperceive convert griffin --help` on `feat/m2-import` at `75640be` (`--subset`, `--raw-root` and `--out` are required); `./griffin-release` is the extracted release directory, as in `README.md`. |
| Python 3.10–3.13. Apache-2.0. Local by default — nothing leaves your machine. | `README.md` ("Python 3.10–3.13"); `pyproject.toml` `license = "Apache-2.0"`; the server binds loopback unless `--token` is set (`HANDOVER.md`, Phase 5 choices). |
| Score every detection where it happened. Predictions match the agent's own ground truth in the world frame by BEV IoU. | `docs/adr/0010-eval-predictions.md`; BEV IoU at 0.5 and 0.7 (`README.md`). The canvas beneath is a simulation (above). |
| Read the docs / View on GitHub / GitHub | Links to `https://github.com/adarsh-nl/coperceive/tree/main/docs` and `https://github.com/adarsh-nl/coperceive`. The design left them as `#`. |
| multi-agent perception data | Footer tagline from the design. |

## Kept from the earlier rules

- Fonts are self-hosted (`@fontsource-variable/geist`, `geist-mono`); the design's Google Fonts link was not used. The page fetches nothing from another origin (asserted in `tests/test_landing_page.py`).
- No analytics, trackers or third-party scripts.
- No viewer screenshots, `.xodr` or dataset bytes are committed. The design project's `web/public/landing/*.png` were not used.

## Not on this page any more

- The recorded hero clip (`site/public/hero/clip.bin`), its poster and the `three` player. The files stay in the tree; this page does not load them.
- The waitlist form. The design has none.
