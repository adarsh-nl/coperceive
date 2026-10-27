# Facts on the landing page

Every sentence the page states, and where it comes from. The page prints no
dataset name, no frame count, no scene count, no metric, no size, and no
percentage.

Checked against this tree on 2026-10-24. The clip formats, the legend and the camera were rechecked on 2026-10-27 for the N-agent player.

## Page claims

| Claim on the page | Source |
| --- | --- |
| Every agent. One view. | The headline for this page. It is not a measured result. The hero draws every agent stored in the recorded clip: for the committed clip, one vehicle and one drone (`site/src/utils/clipLoader.ts` reads both poses from `site/public/hero/clip.bin`); for a clip in the newer format, every agent in its agent table. The page does not state a count. |
| Sensor data and perception, on one timeline. | The same frames carry that vehicle's lidar points and the boxes (the `CPCVCLIP` exporter, `scripts/site/export_hero_clip.py` at `0ee039d`, wrote the poses, the points, and the box flags). Playback walks the frames in order. The page draws the points and the lane lines. It does not draw the boxes. The page does not say real-time or live. |
| Recorded | The hero plays `site/public/hero/clip.bin`, written by `scripts/site/export_hero_clip.py` as it was at `0ee039d`, and the still `site/public/hero/poster.webp` from that playback. Both platforms in that file are recorded. The page does not name the dataset. |
| Coming soon. | `docs/adr/0014-desktop-app.md` specifies a desktop app. `README.md:5` says this tree does not build the desktop shell. The page gives no date and no download. |
| The desktop app for multi-agent perception. | The same sources. It names the product the ADR specifies. It does not say the shell ships from this tree. |
| Vehicle. Drone. Lane. | The legend, built from the clip's agent table by `legendEntries` in `site/src/utils/agents.ts` (the table comes from `site/public/hero/manifest.json`, which the exporter writes from the same list as the clip). For the committed clip it is Vehicle (the recorded vehicle pose and its lidar) and Drone (the recorded drone pose). A clip with several agents of one kind gets numbered entries ("Vehicle 1", "Vehicle 2"); a roadside unit is "RSU". Lane is the map polylines in the same file, drawn in `#7dcea0`. Each dot is that agent's colour in the clip. The words are not a count and not a measurement. |
| Join the waitlist. Enter an email address. Waitlist opens soon. Sending… You’re on the list. Could not join. Try again. | `site/src/components/Waitlist.tsx`. The address is checked in the browser. When `VITE_WAITLIST_ENDPOINT` is unset, submit sends no request, stores nothing, and shows "Waitlist opens soon." "You’re on the list." is shown only after the endpoint responds with a success status. No service URL is written in the source. |

The wordmark is the product name. The red block is the design token `cursor` in `site/src/tokens.ts` (`#E03A22`). It is not a measurement of the software.

## Clip formats

`site/src/utils/clipLoader.ts` reads two formats. `scripts/site/export_hero_clip.py` now writes only the newer one (its layout is in that file's docstring); the older one was written by the same script at `0ee039d`.

- `CPCVCLIP` (version 1): per frame one vehicle pose, one drone pose, the vehicle lidar points and boxes. The committed `site/public/hero/clip.bin` is this format and was not re-exported (its sha256 is unchanged).
- `CPCVCLP2` (version 2): an agent table (id, kind, colour) and, per frame, each agent's pose and points, for two or more agents. Origin, window start, frame count and step come from arguments or from the scene (origin: the agents' centroid at the first frame). Map polylines are mapped into the scene world with the map's stored `world_from_opendrive`. No boxes, and no dataset, scene or map name in the manifest.

Version 1 is read as a two-agent table: the vehicle (`#F58A1F`, with the sweep) and the drone (`#3C8DFF`, no points). With that table the player draws the same frame as before.

## Clip platforms

The binary stores two platform poses on every frame. Both move. The drone pose sits about 27 m above the vehicle pose. There is no second ground-vehicle pose and no infrastructure pose in the file. Boxes are tracks seen by the vehicle, the drone, or both. They are not extra platforms. The hero does not draw them, so the frame stays the points, the lanes, and the drone marker.

The vehicle is the lidar sweep in the file (`docs/datasets/griffin.md` records that the drone has no point files). Those points are drawn as small additive dots. A point near the recorded vehicle pose is white. A point farther from that pose is `#F58A1F`. The page does not add points. The drone is a small glowing marker in `#3C8DFF` at the recorded position, with a faint line to the vehicle pose. The marker does not use the drone's recorded attitude. The file does not store a body mesh, and the page does not print a size. Lane lines are the map polylines, in `#7dcea0`. The camera orbits the centroid of the agents that carry a sweep in the clip, elevated, and follows it; in the committed clip that is the vehicle alone, so the camera follows the vehicle pose as before. The orbit widens only when those agents spread farther than the default view holds. No second vehicle is drawn for the committed clip.

For a version 2 clip, each agent with points is drawn the same way in its own colour (white near that agent, its colour farther away), each drone is a glowing marker in its colour with a faint line to the nearest ground agent, and the canvas label names the kinds ("Three vehicles and a drone.").

The only committed recording is `site/public/hero/clip.bin`. The exporter at `0ee039d` read one vehicle pose and one drone pose from a scene that is not in git. Griffin's adapter lists those two agents and no infrastructure agent (`coperceive/adapters/builtin/griffin.yaml`). Other town scenes are the same vehicle-and-drone pair, and their frames are not in this repository. A roadside unit appears only in the V2XSet adapter, which this page does not use. No second publishable recording with two ground vehicles, or a vehicle plus infrastructure, is in the tree. The hero scene was not switched.

Geist and Geist Mono stay on the OFL texts in `site/public/licenses/`. Those files are not linked from the page. The page footer is the wordmark only.

The generated bird's-eye scene under `site/src/backdrop/` is not mounted.

## Not stated

- A dataset name, a town name, a frame count, a scene count, a duration, a rate, a size, a percentage, or a model score.
- A personal name or an author line.
- Real-time, live, or zero configuration.
- A repository link or a download.
- AirSim. No file in this tree states it.
- "See what your car can't." That line is not on the page.
