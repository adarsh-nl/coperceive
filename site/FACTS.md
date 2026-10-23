# Facts on the landing page

Every sentence the page states, and where it comes from. The page prints no
dataset name, no frame count, no scene count, no metric, no size, and no
percentage.

Checked against this tree on 2026-10-23.

## Page claims

| Claim on the page | Source |
| --- | --- |
| Every agent. One view. | The headline for this page. It is not a measured result. The hero draws every platform pose stored in the recorded clip: one vehicle and one drone (`site/src/utils/clipLoader.ts` reads both poses from `site/public/hero/clip.bin`). The page does not state a count. |
| Sensor data and perception, on one timeline. | The same frames carry that vehicle's lidar points and the boxes, coloured by which of those two platforms sees each track (`scripts/site/export_hero_clip.py` writes the poses, the points, and the box flags). Playback walks the frames in order. The page does not say real-time or live. |
| Recorded | The hero is `site/public/hero/clip.bin` and the still `site/public/hero/poster.webp`, written by `scripts/site/export_hero_clip.py`. It is not the generated backdrop in `site/src/backdrop/scene.ts`. The page does not name the dataset. |
| Coming soon. | `docs/adr/0014-desktop-app.md` specifies a desktop app. `README.md:5` says this tree does not build the desktop shell. The page gives no date and no download. |
| The desktop app for multi-agent perception. | The same sources. It names the product the ADR specifies. It does not say the shell ships from this tree. |
| Vehicle. Drone. | Labels drawn next to the two platform poses in `site/public/hero/clip.bin`. They are the platform ids the exporter writes (`scripts/site/export_hero_clip.py` reads `agent_id` vehicle and drone). They are not a count, and they are not measured body sizes. |
| Join the waitlist. Enter an email address. Waitlist opens soon. Sending… You’re on the list. Could not join. Try again. | `site/src/components/Waitlist.tsx`. The address is checked in the browser. When `VITE_WAITLIST_ENDPOINT` is unset, submit sends no request, stores nothing, and shows "Waitlist opens soon." "You’re on the list." is shown only after the endpoint responds with a success status. No service URL is written in the source. |

The wordmark is the product name. The red block is the design token `cursor` in `site/src/tokens.ts` (`#E03A22`). It is not a measurement of the software.

## Clip platforms

The binary stores two platform poses on every frame. Both move. The drone pose sits about 27 m above the vehicle pose. There is no second ground-vehicle pose and no infrastructure pose in the file. Boxes are tracks seen by the vehicle, the drone, or both. They are not extra platforms.

The hero draws a marker at each recorded position. The vehicle marker is a box in `#F58A1F`, turned by that platform's recorded orientation. The drone marker is a camera-facing icon in `#3C8DFF` at the recorded position, with a vertical line down to a disc on the ground under that position. The icon does not use the drone's recorded attitude. Those shapes are markers. The file does not store a body mesh, and the page does not print a size. Lidar points in the file are the vehicle sweep only (`docs/datasets/griffin.md` records that the drone has no point files). Those points are drawn in the vehicle colour. Boxes use the exporter's agent flag: vehicle only in the vehicle colour, drone only in the drone colour, and a track seen by both in `#1FA3A3`. The camera follows the midpoint of the two poses so both stay in frame. Nothing else is added.

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
