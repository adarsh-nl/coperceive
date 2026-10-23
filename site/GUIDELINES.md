# Web Interface Guidelines review

Reviewed against the Vercel Web Interface Guidelines
(`https://raw.githubusercontent.com/vercel-labs/web-interface-guidelines/main/command.md`)
on 2026-10-23. Findings below are the ones that applied to this page. Each is either
fixed in the cited line or kept on purpose.

## Fixed

- `site/src/index.css:14` — fragment targets can sit under the fixed header. `scroll-padding-top: 4.5rem` on `html`, and `scroll-margin-top: 4.5rem` on `section[id]` and `details[id]` (`site/src/index.css:20`).
- `site/src/index.css:25` — `overscroll-behavior: none` on `html` inside `@media (pointer: fine)`.
- `site/src/index.css:48` — visible `:focus-visible` outline (`2px solid #3C8DFF`). No `outline: none`.
- `site/src/index.css:56` — `touch-action: manipulation` and an intentional tap highlight on links, buttons, and summaries.
- `site/src/index.css:10` — `color-scheme: dark` on `:root`. `site/index.html:6` theme-color is `#000000`.
- `site/src/index.css:62` — `text-wrap: balance` on headings and `text-wrap: pretty` on paragraphs.
- `site/src/index.css:71` — `.num` uses tabular numerals.
- `site/src/App.tsx:15` — skip link to `#main-content`. One `h1`, then `h2` sections.
- `site/src/components/Header.tsx:6` — header padding uses `env(safe-area-inset-*)`.
- `site/src/components/Ledger.tsx:82` — legend swatches are `aria-hidden`. The text names the colour.
- `site/src/components/Specs.tsx:81` — open and closed spec cards write the hash (`#spec-…` or `#specs`).
- `site/src/components/Specs.tsx:97` and `site/src/components/GetStarted.tsx:55` — command blocks are `translate="no"`.
- `site/src/components/GetStarted.tsx:62` — copy result is an `aria-live="polite"` region. The failure text says what to do next.
- `site/src/components/RealClip.tsx:132` — poster has `width`, `height`, and `loading="lazy"`. `site/src/components/RealData.tsx:66` is the same for the reduced-motion still.
- `site/src/components/RealClip.tsx:137` — loading copy ends with `…` and is `aria-live="polite"`.
- `site/src/components/Hero.tsx:64` — the hero loop is longer than five seconds and has a Pause control. Reduced motion removes it and holds a still (`site/src/components/Hero.tsx:63`).
- `site/src/components/RealClip.tsx:148` — the clip has Pause. Reduced motion never mounts the player (`site/src/components/RealData.tsx:62`).
- `site/src/components/Specs.tsx:89` — the plus icon animates `transform` only, and `motion-reduce:transition-none` drops it.
- `site/src/components/Footer.tsx:44` — the citation link meets the 44px hit target (`min-h-11`), with a hover colour.
- `site/src/components/CtaLink.tsx:4` — the primary control is an `<a>`, so command-click and middle-click work. Hover raises the contrast.
- No `transition: all`. Transitions list `color`, `background-color`, or `transform`.
- No analytics, no font CDN, no zoom lock (`user-scalable` / `maximum-scale` are absent). `site/index.html:4` is `width=device-width, initial-scale=1`.

## Kept on purpose

- `site/src/components/Hero.tsx:52` and the `h2` strings in `StoryShell` — sentence-style headlines, as specified for this page. Not Chicago Title Case.
- `site/src/components/CtaLink.tsx:11` and `:24` — the button labels are the two download states from the brief, including "View on GitHub" and "Download for macOS (Apple Silicon) · beta".
- `site/src/backdrop/BevCanvas.tsx` and `site/src/components/RealClip.tsx` — motion is canvas and WebGL, which is the specified renderer. DOM motion stays on `transform` and `opacity`.
- `site/src/motion/useStory.ts:68` — `getBoundingClientRect` runs in the scroll listener, not during render.
- `site/src/components/RealData.tsx:8` — `14.8 s` and `10 Hz` use a non-breaking space. They are sourced figures, not `Intl` formatting of a user-entered number.
- Spec cards use the URL hash rather than a query parameter. That is the deep link.
- Illustrative counts stay next to the "Illustrative scene" label and are not placed beside a model claim (`site/src/components/Occlusion.tsx:18`, `site/src/components/Perspectives.tsx:58`).

## Not applicable

No text inputs, selects, forms, drag gestures, modals, or virtualized lists. No icon-only buttons: Pause, Play, and Copy all have visible names.
