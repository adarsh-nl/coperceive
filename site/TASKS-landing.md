# Landing page

`TASKS.md` is on `feat/desktop` and is not in this branch. These are the landing-page
notes for the draft PR into `main`. Do not copy the rest of `TASKS.md` here.

## Shipped

- Sections 1–9. Stretch sections 2 (two agents), 4 (two perspectives), 5 (ledger), and 7 (spec cards) shipped with the required sections.
- One token source: `site/src/tokens.ts`, imported by `site/tailwind.config.ts`. `site/tailwind.config.js` is removed.
- Backdrop is a copy of `web/src/desktop/backdrop/scene.ts` and `drawBev.ts` from `feat/desktop` at `bf36d0d` (bytes unchanged through `89fa0aa`), owned under `site/src/backdrop/`. Unify with `web/` after `feat/desktop` merges. This page does not import `web/`.
- CTA default is `github` in `site/src/cta.ts`. Flip `DOWNLOAD_MODE` to `"release"` only after a real GitHub Release exists.
- Visibility 1.0 is not on the page. Facts are in `site/FACTS.md`. Guidelines review is `site/GUIDELINES.md`.
- GitHub Pages workflow: `.github/workflows/pages.yml`. It does not enable Pages. Adarsh does that.

## Gates, 2026-10-23

| Gate | Result |
| --- | --- |
| `npm run build` in `site/` | Passed. First-load JS `index-DpnFTnr6.js` gzip 82.38 KB (Vite). RealClip chunk gzip 132.87 KB. `three` is only in that chunk. `dist/index.html` does not modulepreload it. |
| Lighthouse desktop, `vite preview` at `/coperceive/` | Performance 100, Accessibility 100, Best Practices 100, SEO 100. |
| Lighthouse mobile | Performance 99, Accessibility 100, Best Practices 100, SEO 100. |
| Playwright `tests/test_landing_page.py` | 2 passed. Canvases non-blank and change with scroll. Reduced motion has no pin and a stable hero. 375 px has no pin. axe serious and critical: 0. Keyboard focus visible. Same-origin links 200. GitHub links checked by URL shape. No console errors. |
| Scroll video | `docs/media/landing-scroll.mp4`, 1440×900, 24.56 s, 1.6 MB. |
| OG image | `site/public/og.png`, 1200×630, occlusion moment. |

## Left for Adarsh

1. Merge this PR into `main`.
2. Settings → Pages → Source: GitHub Actions.
3. Run the pages workflow, or let the merge push run it.
4. Open https://adarsh-nl.github.io/coperceive/
5. Optional: Release `v0.1.0-beta` with the dmg, then set `DOWNLOAD_MODE` to `"release"`.

Nothing in the stretch list was left unfinished.
