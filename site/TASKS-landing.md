# Landing page

`TASKS.md` is on `feat/desktop` and is not in this branch. These are the landing-page
notes for the draft PR into `main`. Do not copy the rest of `TASKS.md` here.

## Shipped

- A quiet page: floating wordmark, full-bleed recorded clip (vehicle lidar and a drone marker), one Coming soon beat, one waitlist form, a wordmark footer.
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

## Size budget (CI)

`scripts/site/check_budget.py site/dist` runs in CI's `landing` job after the build and fails when first-load JS (every module script and modulepreload in `index.html`) is over 80 kB gzip, the RealClip chunk over 150 kB gzip, `hero/clip.bin` over 1.5 MB gzip, or `hero/poster.webp` over 200 kB, or when `three` or RealClip is modulepreloaded or in first-load JS. kB are 1000 bytes, gzip level 9; its gzip figures run about 0.5% under Vite's report. `tests/test_site_budget.py` covers the checker on synthetic builds and checks in a browser that `clip.bin` is requested after `poster.webp` has loaded and after first and largest contentful paint.

On this branch's build (2026-10-27): first-load JS 72,326 B, RealClip 132,549 B, clip.bin 771,256 B gzip, poster.webp 126,888 B; nothing preloaded.

`scripts/site/run_lighthouse.py` served `dist/` at `/`, so the page it audited could not load `/coperceive/assets/`; it now serves under `/coperceive/` and takes the dist path as an argument. Scores measured before this fix (the 2026-10-23 table above) may not be of the full page.

## Lighthouse, 2026-10-27 (commit `c2965ef`)

`scripts/site/run_lighthouse.py` on this commit's build (served under `/coperceive/`, text gzipped), HPC job 643686 on `ps,main-cpu`:

| Preset | Performance | Accessibility | Best Practices | SEO |
| --- | --- | --- | --- | --- |
| Desktop | 100 | 100 | 100 | 100 |
| Mobile | 96 | 100 | 100 | 100 |

Mobile FCP 1.7 s, LCP 2.6 s (the h1), CLS 0, TBT 0 ms. Before the static hero shell, the same runner without gzip gave mobile performance 87 (job 643650) and 89 with only a poster preload (643670, 643677); LCP was 3.4–3.5 s because the h1 needed the script.

## Left for the maintainer

1. Merge this PR into `main`.
2. Settings → Pages → Source: GitHub Actions.
3. Run the pages workflow, or let the merge push run it.
4. Open https://adarsh-nl.github.io/coperceive/
5. Optional: set `VITE_WAITLIST_ENDPOINT` for a later build when a form endpoint exists. Do not put that URL in the repository.
