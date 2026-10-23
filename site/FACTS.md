# Facts on the landing page

Every sentence the page states, and where it comes from. The page prints no
dataset name, no frame count, no scene count, no metric, no size, and no
percentage. Drawing parameters in the bird's-eye scene are not printed.

Checked against this tree on 2026-10-23.

## Page claims

| Claim on the page | Source |
| --- | --- |
| See what your car can’t. | The headline for this page. It is not a measured result. |
| Illustrative. Illustrative scenes are synthetic. | The bird's-eye scenes are generated in `site/src/backdrop/scene.ts`, a copy of `web/src/desktop/backdrop/scene.ts` from `feat/desktop` at `bf36d0d`. They are not frames from a recording. |
| One clock. Platforms share this scene. | `site/src/backdrop/scene.ts` moves two vehicle platforms (`vehAt`, `partnerAt`) and a drone (`uavAt`) on one scene clock. The page does not state a count. |
| The blind spot. One view is blocked. Another is not. | `occlusionTime()` in `site/src/backdrop/story.ts` picks a synthetic frame where one platform misses objects another platform sees. The page prints no count. |
| Play. Several viewpoints of one scene. | `README.md:29` names the viewer viewpoints: orbit, bird's-eye, chase, drone, and a camera frustum. The page does not count them. |
| Check. A written report for a folder of scenes. | `README.md:86-89`. `coperceive validate` writes a JSON report and a Markdown report for a directory of scenes. |
| Compare. A prediction next to ground truth. | `README.md:70`. `eval run` matches each output to that agent's own ground truth. The page states no score. |
| Map. Roads drawn with the sensors. | `README.md:36`. Lane centres and road edges are drawn under the lidar when a map resolves for the scene. |
| Export. A picture or a clip. | `README.md:29` and `README.md:33`. Export sequence writes a camera MP4, GIF, or PNG sequence. |
| Desktop. Specified, and not a download. | `docs/adr/0014-desktop-app.md:9` and `:84` specify the desktop app. `README.md:5` says this tree does not build the desktop shell. The page does not link a file. |
| Apache-2.0 | `LICENSE:3`. `docs/adr/0006-licence.md:3`. |
| Geist, OFL-1.1. Geist Mono, OFL-1.1. | Packages `@fontsource-variable/geist` and `@fontsource-variable/geist-mono`. Licence texts are `site/public/licenses/OFL-Geist.txt` and `site/public/licenses/OFL-Geist-Mono.txt`. |
| Join the waitlist. Enter an email address. Waitlist opens soon. Sending… You’re on the list. Could not join. Try again. | `site/src/components/Waitlist.tsx`. The address is checked in the browser. When `VITE_WAITLIST_ENDPOINT` is unset, submit sends no request, stores nothing, and shows "Waitlist opens soon." "You’re on the list." is shown only after the endpoint responds with a success status. No service URL is written in the source. |

The wordmark is the product name. The red block is the design token `cursor` in `site/src/tokens.ts` (`#E03A22`), sampled from the mark. It is not a measurement of the software.

## Not stated

- A dataset name, a town name, a frame count, a scene count, a duration, a rate, a size, a percentage, or a model score.
- A personal name or an author line. None is printed in the page, the document title, or the social tags.
- Real-time, live, or zero configuration.
- A repository link or a download.
- AirSim. No file in this tree states it.
- Ring radii, altitudes, and the second platform's colour. Those are drawing parameters in `site/src/backdrop/scene.ts` and `site/src/backdrop/drawBev.ts`. They are not printed.
- The recorded clip previously under `site/public/hero/`. The page does not load it, and those files are not part of this site.
