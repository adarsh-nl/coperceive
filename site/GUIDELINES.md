# Web Interface Guidelines review

Reviewed against the Vercel Web Interface Guidelines
(`https://raw.githubusercontent.com/vercel-labs/web-interface-guidelines/main/command.md`)
on 2026-10-23. Findings below are the ones that applied to this page. Each is either
fixed in the cited line or kept on purpose.

## Fixed

- `site/src/index.css:14` — `scroll-padding-top: 1.5rem` on `html`, and `scroll-margin-top: 1.5rem` on `section[id]` and `details[id]`. There is no fixed header.
- `site/src/index.css:23` — `overscroll-behavior: none` on `html` inside `@media (pointer: fine)`.
- `site/src/index.css:48` — visible `:focus-visible` outline (`2px solid #3C8DFF`). No `outline: none`.
- `site/src/index.css:53` — `touch-action: manipulation` and an intentional tap highlight on links, buttons, and summaries.
- `site/src/index.css:10` — `color-scheme: dark` on `:root`. `site/index.html:6` theme-color is `#000000`.
- `site/src/index.css:60` — `text-wrap: balance` on headings and `text-wrap: pretty` on paragraphs.
- `site/src/App.tsx:11` — skip link to `#main-content`. One `h1`, then `h2` sections.
- `site/src/components/FloatingLogo.tsx:28` — the logo uses `env(safe-area-inset-*)`. It is `absolute`, and scroll changes opacity only.
- `site/src/components/Wordmark.tsx` — the wordmark is `translate="no"`, 28 px and 32 px, with a block cursor that stays opaque.
- `site/src/components/Hero.tsx` — the poster has `width`, `height`, and `fetchpriority="high"`. The player script loads after idle. Reduced motion keeps the still.
- `site/src/components/RealClip.tsx` — the canvas is `role="img"` with an `aria-label`. No pause control. The loop stops advancing when the hero leaves the viewport.
- `site/src/components/Waitlist.tsx` — the email field has a visible label, `autocomplete="email"`, `spellcheck` off, `type="email"`, `inputmode="email"`, and an explicit background and text colour. The browser's built-in check is off (`noValidate`) so the page's own message is the one that shows. Errors set `aria-invalid` and `aria-describedby`. The status is `aria-live="polite"`. The submit control is a `<button type="submit">` with a visible name, at least 44px tall.
- `site/src/components/ComingSoon.tsx` — one heading and one line. No motion.
- `site/src/components/Footer.tsx` — the wordmark only. No licence links on the page.
- `site/src/index.css` — `prefers-reduced-motion: reduce` stops the block-cursor blink.
- No `transition: all`. Transitions list `color`, `background-color`, or `transform`. The logo fade writes `opacity` from scroll and does not animate layout.
- No analytics, no font CDN, no zoom lock (`user-scalable` / `maximum-scale` are absent). `site/index.html` is `width=device-width, initial-scale=1`.
- No paste blocking, no `onPaste` handler.

## Kept on purpose

- Sentence-style headlines, as specified for this page. Not Chicago Title Case.
- The button label is "Join the waitlist", the one persistent call to action.
- The hero motion is a canvas. DOM motion stays on `transform` and `opacity`.
- `site/src/motion/useStory.ts` — `getBoundingClientRect` runs in the scroll listener, not during render.

## Not applicable

No selects, drag gestures, modals, or virtualized lists. No icon-only buttons. The email field is the only text input.
