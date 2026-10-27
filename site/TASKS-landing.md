# Landing page

`TASKS.md` is on `feat/desktop` and is not in this branch. These are the landing-page
notes for the draft PR into `main`. Do not copy the rest of `TASKS.md` here.

## Landing Motion (2026-10-27, `agent/claude-site-landing-motion`)

The page is now the Claude Design file "Landing Motion", built as designed at Adarsh's request (supersedes the story sections of #34). Layout: a scroll-pinned story (600vh) over a simulated city canvas with four captions and a progress bar; nav (Pipeline, Install, GitHub); pipeline cards; install with a typed terminal and "Read the docs" / "View on GitHub"; a pinned evaluate demo (simulated drive, IoU and run toggles); wordmark footer. Code: `site/src/landing/` (`Landing.tsx`, `story.ts`, `evalScene.ts`, `util.ts`). Both canvases are lazy chunks, so first-load JS is 75.33 kB gzip (story 7.58 kB, evaluate 5.45 kB); `three` is no longer loaded. Fonts stay self-hosted. Reduced motion freezes both simulations, shows sections at once and skips the typing. `site/FACTS.md` lists the sources and marks every simulated number.

Not used any more by the page (files left in place): `Hero.tsx`, `RealClip.tsx`, `ComingSoon.tsx`, `Waitlist.tsx`, `FloatingLogo.tsx`, `utils/clipLoader.ts`, `backdrop/`, `site/public/hero/*`.

## Shipped (before Landing Motion)

- A quiet page: floating wordmark, full-bleed recorded clip (vehicle lidar and a drone marker), one Coming soon beat, one waitlist form, a wordmark footer.
- The clip player is a separate chunk. The poster paints first. `three` is not in the first-load script and is not modulepreloaded.
- One token source: `site/src/tokens.ts`, imported by `site/tailwind.config.ts`. The block-cursor red is `cursor`. Vehicle and drone colours are `vehicle` and `drone`.
- Backdrop files remain a copy of `web/src/desktop/backdrop/scene.ts` and `drawBev.ts` from `feat/desktop` at `bf36d0d` (bytes of those two files were the base; the page does not mount them). Unify with `web/` after `feat/desktop` merges. This page does not import `web/`.
- The waitlist posts to `VITE_WAITLIST_ENDPOINT` when that build setting is present. Unset, it sends nothing. See `site/README.md`.
- No repository link, no personal name, no dataset name, and no statistic on the page. Facts are in `site/FACTS.md`. Guidelines review is `site/GUIDELINES.md`.
- GitHub Pages workflow: `.github/workflows/pages.yml`. It does not enable Pages. Pull requests touching `site/**` or the workflow run the same install and build and check `dist/` (base `/coperceive/`, `404.html`, `.nojekyll`); only a push to `main` or a manual run deploys. `ci.yml`'s `landing` job uses the same plain `npm ci` and action versions.

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
