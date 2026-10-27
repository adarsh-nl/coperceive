# Facts on the landing page

Every sentence the page states, and where it comes from. The page prints no
dataset name, no frame count, no scene count, no metric, no size, and no
percentage.

Checked against this tree on 2026-10-24.

## Page claims

| Claim on the page | Source |
| --- | --- |
| Every agent. One view. | The headline for this page. It is not a measured result. The hero draws every platform pose stored in the recorded clip: one vehicle and one drone (`site/src/utils/clipLoader.ts` reads both poses from `site/public/hero/clip.bin`). The page does not state a count. |
| Sensor data and perception, on one timeline. | The same frames carry that vehicle's lidar points and the boxes (`scripts/site/export_hero_clip.py` writes the poses, the points, and the box flags). Playback walks the frames in order. The page draws the points and the lane lines. It does not draw the boxes. The page does not say real-time or live. |
| Recorded | The hero plays `site/public/hero/clip.bin`, written by `scripts/site/export_hero_clip.py`, and the still `site/public/hero/poster.webp` from that playback. Both platforms in that file are recorded. The page does not name the dataset. |
| Coming soon. | `docs/adr/0014-desktop-app.md` specifies a desktop app. `README.md:5` says this tree does not build the desktop shell. The page gives no date and no download. |
| The desktop app for multi-agent perception. | The same sources. It names the product the ADR specifies. It does not say the shell ships from this tree. |
| Vehicle. Drone. Lane. | The legend. Vehicle is the recorded vehicle pose and its lidar. Drone is the recorded drone pose. Lane is the map polylines in the same file, drawn in `#7dcea0`. The words are not a count and not a measurement. |
| Join the waitlist. Email. Join the waitlist (button). | `site/src/components/Waitlist.tsx`. The heading, the field label and the button. |
| name@example.com (placeholder) and "Enter an email address, like name@example.com." | The field's example and its inline message when the address does not match `EMAIL` in `Waitlist.tsx` (checked in the browser). `example.com` is a reserved example domain, not a real account. |
| Waitlist opens soon. Nothing was sent. | Shown when `VITE_WAITLIST_ENDPOINT` is unset or blank: submit returns before any request and writes no storage (`tests/test_landing_waitlist.py` records zero requests of any kind and empty localStorage, sessionStorage, cookies and IndexedDB). No service URL is written in the source; no service has been chosen. |
| Sending… | Shown while the POST to the configured endpoint is in flight; the field and button are disabled and the form is `aria-busy`. |
| You’re on the list. | Shown only after the configured endpoint answers with a success status (`response.ok`). |
| Could not join. Check your connection and try again. | Shown when the request fails or the endpoint answers with an error status. The form stays filled so the person can retry. |

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
