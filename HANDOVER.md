# Handover

The desktop application is the primary active focus (M2/M3/M4) per Adarsh's instructions.
All engineering tasks and work items are tracked in **[TASKS.md](TASKS.md)** and on the **[Coperceive Notion Board](https://www.notion.so)**.

---

## Current State: Desktop Application (`feat/m2-import` & `feat/desktop`)

- **Architecture (ADR 0007 / ADR 0014)**:
  - Native Apple Silicon desktop application built on **Tauri v2** (`tauri` 2.x, `tauri-plugin-dialog`, `@tauri-apps/api`, `@tauri-apps/cli`).
  - Bundled pinned `python-build-standalone` (`cpython-3.13.1+20250115-aarch64-apple-darwin`), locked dependencies (`scripts/desktop/requirements-darwin-arm64.lock`), and stripped runtime in `Contents/Resources/resources/python`.
  - Installed `.app` bundle size: **~213 MB**; packaged `.dmg`: **~61 MB**.
  - Local sidecar execution target: allocates ephemeral loopback port (`127.0.0.1:0`), mode 0600 token authentication, constant-time token comparison (`hmac.compare_digest`), auto-terminates child process on window destruction or application quit.
  - Remote HPC execution target: Slurm session manager (`coperceive remote ...`), SSH local port forward tunnel (`ssh -L`), heartbeat lease watchdog, and bounded connection tests.

- **Security & Conformance**:
  - Tokens are never passed on argv or stored in URLs. Tokens are strictly passed via `COPERCEIVE_TOKEN` or `--token-file` (file mode `0600` or `0400` enforced).
  - Path confinement: all scene file loads and export destinations are strictly confined within the active dataset root (`confine()`).

- **Mac Acceptance Instructions**:
  1. **Scene to copy**: `Town03-000` (6.9 MB, 149 frames, vehicle + drone with bounding boxes and annotations):
     ```bash
     mkdir -p ~/coperceive-data
     scp -r nanjaiyalathaa@hpc-head1.ewi.utwente.nl:~/results/reference-stepb-20261020/staged-scenes/Town03-000 ~/coperceive-data/
     ```
  2. **Opening Unsigned App on macOS**:
     Download the `coperceive-desktop-macos-arm64` DMG from CI artifacts, mount it, copy `Coperceive.app` to `/Applications`, and clear the quarantine bit:
     ```bash
     xattr -cr /Applications/Coperceive.app
     ```
     *(Alternatively: Right-click `Coperceive.app` in Finder -> select **Open** -> click **Open**).*

---

## Open Decisions & Questions for Adarsh

1. 40 m/s and 20 m/s² stay warnings. Smoothed acceleration uses the same 20 m/s² and a 0.5 s trailing window. On Griffin-25m the smoothed check warns on 0 steps.
2. The computed gate is `max(2 m, 0.5 * the longer box length)`. Griffin-25m has 11 computed pairs, centre median 4.071134798976429 m, max 4.518411314835206 m.
3. Is the 2 m / 5-frame / 40% phase rule the split you want? It labels only Town06-001.
4. “About 150” frames stays informational. Every Griffin-25m scene is 147, 148, or 149.
5. The default exit is 0. `--strict` exits 1 on an unreadable scene, a missing frame, or a cross-agent pose miss. The centreline block does not count.

---

## Historical Engineering Logs (`docs/log/`)

Detailed historical logs, benchmark results, Slurm batch execution records, and slice-by-slice verification gates have been moved to dated logs in `docs/log/`:

- **[2026-10-25](docs/log/2026-10-25.md)**: CARLA town CC-BY licensing notes, Griffin dataset download dry-run and node capacity checks.
- **[2026-10-22](docs/log/2026-10-22.md)**: Milestone 1 (M1) close-out, e2e verification notices, loop persistence in `state.json`, and backdrop CPU measurements.
- **[2026-10-18](docs/log/2026-10-18.md)**: OPV2V and V2XSet adapters (ADR 0012 left-handed reflection, town identification), Evaluation Step B batch mode, and Slurm jobs 630818/630821.
- **[2026-10-17](docs/log/2026-10-17.md)**: Dataset adapters, Evaluation engine Step A (format 1 predictions, matching, AP metrics), Task 12 Griffin run, viewer check, and camera strip.
- **[2026-10-16](docs/log/2026-10-16.md)**: Phase 6 playback, camera checks, figures export, sequence export, view settings, and UI redesign verification gates.
- **[2026-10-15](docs/log/2026-10-15.md)**: UTwente HPC cluster empirical findings, project goals, foundational architecture decisions, and clean-room rules.
