# Landing page

`TASKS.md` is on `feat/desktop` and is not in this branch. These are the landing-page
notes for the draft PR into `main`. Do not copy the rest of `TASKS.md` here.

## Shipped

- A quiet page: floating wordmark, full-bleed recorded clip (vehicle lidar and a drone marker); then one timeline section (every agent's sensors and detections on one timeline, with a decorative schematic), one large type tagline whose words light up on scroll, one cluster-over-SSH section marked Coming soon, the Coming soon beat, one waitlist form, and a wordmark footer. Sections rise and fade in once on scroll (IntersectionObserver). Spacing, type, radius and motion follow the landing-page-design skill mapped onto the existing Tailwind classes (no new dependency); its nav bar, FAQ, social proof and invented content are not used.
- Open for Adarsh: CARLA map assets are CC-BY. Whether and where to credit them on the page is not decided; nothing was added.
- The clip player is a separate chunk. The poster paints first. `three` is not in the first-load script and is not modulepreloaded.
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
