# Landing page

`TASKS.md` is on `feat/desktop` and is not in this branch. These are the landing-page
notes for the draft PR into `main`. Do not copy the rest of `TASKS.md` here.

## Shipped

- A quiet page: floating wordmark, full-bleed recorded clip (vehicle lidar and a drone marker), one Coming soon beat, one waitlist form, a wordmark footer.
- The clip player is a separate chunk. The poster paints first. `three` is not in the first-load script and is not modulepreloaded.
- The hero player reads two clip formats: the committed `CPCVCLIP` clip (vehicle sweep plus drone pose) and `CPCVCLP2`, an agent table plus per-agent poses and points for two or more agents. Each agent is drawn in its own colour, the camera orbits the centroid of the agents with sweeps, and the legend chip is built from the agent table plus Lane. `scripts/site/export_hero_clip.py` writes only `CPCVCLP2`; its origin, window and agents are arguments (defaults from the scene). The committed `clip.bin` is unchanged (sha256 `822b8012273b8a905af31e4083915c088b32e29e6461f96f0c25cfcedc42a375`). A new clip is not committed until Adarsh approves it.
- Responsive and accessibility checks at 375, 768 and 1440 px are in `tests/test_landing_responsive.py` (no horizontal scroll, legend wraps with 4+ agents, logo clears safe-area insets, axe AA serious/critical 0, reduced motion = poster only and no WebGL context, skip link and waitlist focus).
- One token source: `site/src/tokens.ts`, imported by `site/tailwind.config.ts`. The block-cursor red is `cursor`. Vehicle and drone colours are `vehicle` and `drone`.
- Backdrop files remain a copy of `web/src/desktop/backdrop/scene.ts` and `drawBev.ts` from `feat/desktop` at `bf36d0d` (bytes of those two files were the base; the page does not mount them). Unify with `web/` after `feat/desktop` merges. This page does not import `web/`.
- The waitlist posts to `VITE_WAITLIST_ENDPOINT` when that build setting is present. Unset, it sends nothing. See `site/README.md`.
- No repository link, no personal name, no dataset name, and no statistic on the page. Facts are in `site/FACTS.md`. Guidelines review is `site/GUIDELINES.md`.
- GitHub Pages workflow: `.github/workflows/pages.yml`. It does not enable Pages.

## Gates, 2026-10-23

| Gate | Result |
| --- | --- |
| `npm run build` in `site/` | Passed. First-load JS `index-Co2OgOex.js` gzip 72.68 KB. `RealClip-azx7jctB.js` gzip 133.67 KB. `dist/index.html` does not modulepreload it. `404.html` and `.nojekyll` are written. |
| Lighthouse desktop, `vite preview` at `/coperceive/` | Performance 100, Accessibility 100, Best Practices 100, SEO 100. |
| Lighthouse mobile | Performance 96, Accessibility 100, Best Practices 100, SEO 100. |
| Playwright `tests/test_landing_page.py` | 2 passed. No header, no pause control, no repository link, no licence line. Logo is at least 28 px, fades, and is not fixed. The recorded canvas moves. Reduced motion keeps the poster and stops the cursor blink. axe serious and critical: 0. |
| Scroll video | `docs/media/landing-scroll.mp4`, 1440×900, 24.20 s, 3.9 MB. |
| OG image | `site/public/og.png`, 1200×630, icon variant. |

## Left for the maintainer

1. Merge this PR into `main`.
2. Settings → Pages → Source: GitHub Actions.
3. Run the pages workflow, or let the merge push run it.
4. Open https://adarsh-nl.github.io/coperceive/
5. Optional: set `VITE_WAITLIST_ENDPOINT` for a later build when a form endpoint exists. Do not put that URL in the repository.
