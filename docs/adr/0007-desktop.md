# ADR 0007 — Desktop packaging

Status: accepted as a plan. The desktop shell is not built in Phase 5.

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

Keep the split above. When a shell is built, prefer Tauri, and use Electron if the Python sidecar or the runtime bundle is harder under Tauri. Do not build either shell in this phase. Do not add `src-tauri/`, an Electron main process, or a PyInstaller spec.

A later shell would:

1. Ship the Vite build from `coperceive/app/static`, or load it from the installed wheel.
2. Start `coperceive serve --host 127.0.0.1 --port <chosen> --token <generated> --root <user data directory>`.
3. Open the window at that loopback origin. The page already stores the bearer token in `sessionStorage`. The shell can set that value before the first API call. Do not put the token in a URL query string.
4. Stop the server when the window closes.

The same `coperceive` command remains usable from a terminal.

## Consequences

- CI does not package a desktop app.
- The routes in `docs/viewer.md` are the contract a shell would call.
- Tauri is the planned shell. Electron is the fallback. Adarsh can reverse that choice before any shell work starts.
