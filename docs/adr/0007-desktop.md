# ADR 0007 — Desktop packaging

Status: implemented in 0.1.0. The shell is `desktop/`. Phase 5 only recorded the plan.

## Context

The viewer is three.js inside the local page (ADR 0003). The server is FastAPI on loopback (ADR 0004). Adarsh wants a standalone desktop app later. That package has to wrap the page and the server without folding them into one module.

The split already in the tree:

- The React page calls the server only over HTTP, through `apiBase()` and `authHeaders()` in `web/src/api/client.ts`. Relative URLs follow `document.baseURI`.
- `coperceive.viewdata` builds the frame JSON and the world-point bytes. It does not import FastAPI or a viewer SDK.
- `coperceive.rerun_export.export_rerun` imports `rerun` inside the function. Importing the module does not import Rerun.
- `coperceive serve` binds `127.0.0.1` unless `--token` is set. Mutating routes still require `X-Coperceive-Request: 1`.

## Options

1. **Tauri.** A native window loads the built static assets. It starts `coperceive serve --host 127.0.0.1` as a sidecar and stops that process when the window closes. The Python runtime is bundled beside the binary.
2. **Electron.** The same HTTP split, with a Node shell instead of a Rust shell. The download is larger. The server is still a separate Python process.
3. **One frozen Python process that also opens a window** (for example pywebview or a browser embedded in the interpreter). The page and the server stop being separable, and the UI can no longer be developed as a static build.

## Criteria

- The page stays an HTTP client.
- The frame builder stays free of FastAPI and of Rerun.
- The server still refuses a non-loopback bind without a token.
- Phase 5 does not add a shell project, a bundler spec, or a packaged binary.

## Decision

Keep the split above. The shell is Tauri. The Python server is a PyInstaller one-file sidecar, `coperceive-server`, spawned by the Rust process. It is not a shell plugin. Electron was the fallback and was not needed.

The 0.1.0 shell:

1. Ships the Vite build inside the sidecar, from `coperceive/app/static` collected at freeze time.
2. Starts `coperceive-server serve --host 127.0.0.1 --port <chosen> --token <generated> --root <app data directory>`.
3. Opens the window at that loopback origin. An initialization script sets `sessionStorage['coperceive-token']` before the first API call. The token is not a URL query string.
4. Stops the server when the window closes.

The same `coperceive` command remains usable from a terminal. A non-loopback bind still requires `--token`.

## Consequences

- The pull-request workflow builds a Linux AppImage and a deb. `.github/workflows/release.yml` builds those plus macOS and Windows bundles when a `v*` tag is pushed, and uploads artifacts only. It does not publish to PyPI and it does not create a GitHub Release.
- The routes in `docs/viewer.md` are the contract the shell calls.
- Tauri is the shell. The sidecar binary is not committed.
