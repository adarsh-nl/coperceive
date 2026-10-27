# Facts on the landing page

Every sentence the page states, and where it comes from. The page prints no
dataset name, no frame count, no scene count, no metric, no size, and no
percentage.

Checked against this tree on 2026-10-24. The timeline, tagline and cluster rows were added on 2026-10-27.

## Page claims

| Claim on the page | Source |
| --- | --- |
| Every agent. One view. | The headline for this page. It is not a measured result. The hero draws every platform pose stored in the recorded clip: one vehicle and one drone (`site/src/utils/clipLoader.ts` reads both poses from `site/public/hero/clip.bin`). The page does not state a count. |
| Sensor data and perception, on one timeline. | The same frames carry that vehicle's lidar points and the boxes (`scripts/site/export_hero_clip.py` writes the poses, the points, and the box flags). Playback walks the frames in order. The page draws the points and the lane lines. It does not draw the boxes. The page does not say real-time or live. |
| Recorded | The hero plays `site/public/hero/clip.bin`, written by `scripts/site/export_hero_clip.py`, and the still `site/public/hero/poster.webp` from that playback. Both platforms in that file are recorded. The page does not name the dataset. |
| Every sensor. One timeline. | Section heading. The viewer steps every agent's recorded samples on one scrubber: `pose_times` in `coperceive/viewdata.py` merges the pose times of all agents, and `frame_document` returns every agent's pose, boxes and camera frustums for one time. `HANDOVER.md` (State) records playback, the per-camera image strip, and lidar drawn under the boxes. Not a measured result. |
| Lidar, cameras and detections from each agent, played back together. | Lidar: `frame_points` (`coperceive/viewdata.py`) per agent and sensor. Cameras: `GET /api/scenes/{id}/image` and the per-camera panel (`HANDOVER.md`, State). Detections: model predictions are imported and drawn as true positives, false positives and misses on the timeline (`docs/adr/0010-eval-predictions.md` line 9; the 3D legend's status chips in `HANDOVER.md`). Played back together: playback walks the merged times at the recorded gap (`HANDOVER.md`, State). No rate is claimed; the page does not say real-time or live. |
| Vehicle. Drone. Detections. (timeline schematic) | Row labels of the decorative schematic in `site/src/components/Timeline.tsx`, `aria-hidden`. Vehicle and Drone match the hero legend and colours; Detections uses the lane mint. Tick positions are fixed decoration, not data, not a rate and not a count. The playhead sweep is CSS only and stops under reduced motion. |
| What one agent misses, another one sees. | The core benefit, stated as the product's premise, not a measured result. `docs/adr/0010-eval-predictions.md` line 9 states the cooperative question the evaluation answers: "what one agent misses that another agent sees". Supporting: the scene model keeps a per-agent visibility on each annotation and groups tracks seen by more than one agent (`coperceive/model.py` `Annotation.visibility`; `coperceive/diagnostics.py` `shared_objects`), and `site/FACTS.md` Clip platforms records that the hero's boxes are tracks seen by the vehicle, the drone, or both. No case, distance or rate is stated. |
| Coming soon (label) | Marks the cluster section as not shipped. `docs/adr/0014-desktop-app.md` specifies it; this tree does not build the desktop shell (`README.md`). |
| Evaluate on your own cluster. | `docs/adr/0014-desktop-app.md`, Execution Target B, HPC over SSH: the app submits evaluations to the user's Slurm cluster. Marked coming soon. |
| Connect over SSH and run evaluations next to your data. | The same ADR: the system `ssh` with the user's own config and agent, a Slurm allocation that runs `coperceive serve` on the compute node, and a local port forward; datasets stay on the cluster's storage. Remote model runs reuse `coperceive model run --slurm`. Marked coming soon. |
| Coming soon. | `docs/adr/0014-desktop-app.md` specifies a desktop app. `README.md:5` says this tree does not build the desktop shell. The page gives no date and no download. |
| The desktop app for multi-agent perception. | The same sources. It names the product the ADR specifies. It does not say the shell ships from this tree. |
| Vehicle. Drone. Lane. | The legend. Vehicle is the recorded vehicle pose and its lidar. Drone is the recorded drone pose. Lane is the map polylines in the same file, drawn in `#7dcea0`. The words are not a count and not a measurement. |
| Join the waitlist. Enter an email address. Waitlist opens soon. Sending… You’re on the list. Could not join. Try again. | `site/src/components/Waitlist.tsx`. The address is checked in the browser. When `VITE_WAITLIST_ENDPOINT` is unset, submit sends no request, stores nothing, and shows "Waitlist opens soon." "You’re on the list." is shown only after the endpoint responds with a success status. No service URL is written in the source. |

The wordmark is the product name. The red block is the design token `cursor` in `site/src/tokens.ts` (`#E03A22`). It is not a measurement of the software.

## Clip platforms

The binary stores two platform poses on every frame. Both move. The drone pose sits about 27 m above the vehicle pose. There is no second ground-vehicle pose and no infrastructure pose in the file. Boxes are tracks seen by the vehicle, the drone, or both. They are not extra platforms. The hero does not draw them, so the frame stays the points, the lanes, and the drone marker.

The vehicle is the lidar sweep in the file (`docs/datasets/griffin.md` records that the drone has no point files). Those points are drawn as small additive dots. A point near the recorded vehicle pose is white. A point farther from that pose is `#F58A1F`. The page does not add points. The drone is a small glowing marker in `#3C8DFF` at the recorded position, with a faint line to the vehicle pose. The marker does not use the drone's recorded attitude. The file does not store a body mesh, and the page does not print a size. Lane lines are the map polylines, in `#7dcea0`. The camera orbits the recorded vehicle, elevated, and follows that pose. No second vehicle is drawn.

The only committed recording is `site/public/hero/clip.bin`. `scripts/site/export_hero_clip.py` reads one vehicle pose and one drone pose from a scene that is not in git. Griffin's adapter lists those two agents and no infrastructure agent (`coperceive/adapters/builtin/griffin.yaml`). Other town scenes are the same vehicle-and-drone pair, and their frames are not in this repository. A roadside unit appears only in the V2XSet adapter, which this page does not use. No second publishable recording with two ground vehicles, or a vehicle plus infrastructure, is in the tree. The hero scene was not switched.

Geist and Geist Mono stay on the OFL texts in `site/public/licenses/`. Those files are not linked from the page. The page footer is the wordmark only.

The generated bird's-eye scene under `site/src/backdrop/` is not mounted.

## Not stated

- A dataset name, a town name, a frame count, a scene count, a duration, a rate, a size, a percentage, or a model score.
- A personal name or an author line.
- Real-time, live, or zero configuration.
- A repository link or a download.
- AirSim. No file in this tree states it.
- "See what your car can't." That line is not on the page.
- A credit line for CARLA map assets (CC-BY). Whether and where to credit them is open for Adarsh; nothing was added.
