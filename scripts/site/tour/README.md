# Product tour capture

`npm run tour:capture` from `site/` generates synthetic scenes, serves the built app, re-records every frame, and writes `site/public/tour/`.

The scripts are the handoff capture scripts, with three checks added so a bad frame fails the run:

- Page text must not contain `$USER`, `$HOME`, this machine's hostname, home directory, user name, `tudelft`, `utwente`, `ewi.utwente.nl`, `nanjaiyalatha`, or `@`. The app's own `mAP@` score and `N @ N Hz` frame-rate labels are the two exceptions. The Servers list is not a tour frame; it is the one screen that still says the feature is only available in the desktop app.
- A loop frame that says Buffering, Failed to fetch, or disconnected fails the run.
- The desktop line "only available in the desktop app" fails the run.

The seed uses `/scratch/demo/coperceive` so the literal `$USER` never reaches the page. Stepping uses the Step forward button, not Next scene.

Nothing under `web/src`, `coperceive/`, or `src-tauri/` is edited. Scene data stays in the temp directory and is not committed. Each playback loop is one H.264 MP4 plus a WebP poster at 1800×1124.
