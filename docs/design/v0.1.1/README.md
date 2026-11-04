# v0.1.1 design (Claude Design export)

The source of truth for the v0.1.1 UI refinement. Each `.dc.html` file is a
self-contained mock; `support.js` is the runtime they share. They load React
18.3.1, ReactDOM 18.3.1 and `@babel/standalone` 7.29.0 from unpkg.

| Mock | Screen | Code |
| --- | --- | --- |
| `App Pages.dc.html` | Library, Get Data, Open Folder, Servers (browse), New evaluation, Run history in one shell; click the sidebar to switch | `web/src/desktop/ConnectScreen.tsx`, `LibraryScreen.tsx`, `GetDataScreen.tsx`, `HpcServers.tsx`, `HpcBrowse.tsx`, `HpcWorkspace.tsx`, `NewEvaluation.tsx`, `RunHistory.tsx` |
| `HPC Dataset View v2.dc.html` | Dataset view v2 | `web/src/desktop/hpc/DatasetView.tsx` |
| `HPC Dataset View.dc.html` | Dataset view v1 (superseded by v2, kept for reference) | `web/src/desktop/hpc/DatasetView.tsx` |
| `Scene Viewer.dc.html` | Viewer: layers and agents panel, stage, camera strip, timeline, telemetry | `web/src/viewer/Viewer.tsx`, `SidePanel.tsx`, `controls.tsx`, `timeline.tsx`, `layout.ts`, `cameras.tsx`, `telemetry.ts` |

Values in the mocks are illustrative. The app shows only what it has: an
element whose data does not exist is hidden, and each PR lists what it hid.

Personal identifiers were replaced before committing (user names, host names,
the institution in server names and paths). The designer's input screenshots
are not committed.

## Rendering offline

The mocks fetch their three scripts from unpkg. To render them without the
network (for side-by-sides), install the same versions in a scratch folder
(`npm i react@18.3.1 react-dom@18.3.1 @babel/standalone@7.29.0`), serve this
folder over HTTP and answer the unpkg URLs from the local copies with
Playwright's `context.route`. Clicking a sidebar item in `App Pages` switches
pages.
