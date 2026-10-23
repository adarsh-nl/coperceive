# Landing page

`TASKS.md` is on `feat/desktop` and is not in this branch. These are the landing-page
notes for the draft PR into `main`. Do not copy the rest of `TASKS.md` here.

## Shipped

- A quiet page: floating wordmark, full-bleed synthetic hero with two vehicles and a drone, two pinned scenes, short tool cards, one waitlist form, a small footer.
- One token source: `site/src/tokens.ts`, imported by `site/tailwind.config.ts`. The block-cursor red is `cursor`. The second vehicle is `vehicleAlt`.
- Backdrop is a copy of `web/src/desktop/backdrop/scene.ts` and `drawBev.ts` from `feat/desktop` at `bf36d0d` (bytes unchanged through `89fa0aa`), owned under `site/src/backdrop/`. A second vehicle platform is landing-only. Unify with `web/` after `feat/desktop` merges. This page does not import `web/`.
- The waitlist posts to `VITE_WAITLIST_ENDPOINT` when that build setting is present. Unset, it sends nothing. See `site/README.md`.
- No repository link, no personal name, no dataset name, and no statistic on the page. Facts are in `site/FACTS.md`. Guidelines review is `site/GUIDELINES.md`.
- GitHub Pages workflow: `.github/workflows/pages.yml`. It does not enable Pages.

## Gates, 2026-10-23

| Gate | Result |
| --- | --- |
| `npm run build` in `site/` | Passed. First-load JS `index-DlN-P-iC.js` gzip 77.90 KB. No separate `three` chunk. `dist/index.html` does not modulepreload a clip. `404.html` and `.nojekyll` are written. |
| Lighthouse desktop, `vite preview` at `/coperceive/` | Performance 100, Accessibility 100, Best Practices 100, SEO 100. |
| Lighthouse mobile | Performance 99, Accessibility 100, Best Practices 100, SEO 100. |
| Playwright `tests/test_landing_page.py` | 2 passed. No header, no pause control, no repository link, logo fades and is not fixed. Hero canvas moves. Reduced motion holds a still and stops the cursor blink. 375 px has no pin. axe serious and critical: 0. |
| Scroll video | `docs/media/landing-scroll.mp4`, 1440×900, 25.48 s, 2.8 MB. |
| OG image | `site/public/og.png`, 1200×630, icon variant. |

## Left for the maintainer

1. Merge this PR into `main`.
2. Settings → Pages → Source: GitHub Actions.
3. Run the pages workflow, or let the merge push run it.
4. Open https://adarsh-nl.github.io/coperceive/
5. Optional: set `VITE_WAITLIST_ENDPOINT` for a later build when a form endpoint exists. Do not put that URL in the repository.
