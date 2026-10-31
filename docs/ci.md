# CI Pipeline Architecture & Performance Audit

This document records the empirical performance audit of the Coperceive CI pipeline, the architectural bottlenecks identified from live GitHub Actions telemetry, and the optimizations applied to reduce wall-clock time and runner consumption.

---

## 1. Baseline Performance Audit (30 Runs Sampled)

Audit performed using `gh api` across 30 recent CI runs on `feat/m2-import` and active agent branches (`agent/agy-*`, `agent/deepseek-*`).

### 1.1 `desktop-macos` (macOS 15 Apple Silicon Runner) — Critical Path

The `desktop-macos` job constitutes the entire critical path of the CI pipeline, running between **10.5 and 11.8 minutes** on every push.

| Step / Phase | Median (s) | Median (min) | p90 (s) | p90 (min) | % of Critical Path |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Queue Wait** | 3.0 s | 0.1 m | 10.1 s | 0.2 m | 0.5% |
| **Checkout & Setup** (git, node, npm ci, web static) | 22.5 s | 0.4 m | 27.1 s | 0.5 m | 3.6% |
| **Bundled Python Runtime** (download + pip install) | 26.5 s | 0.4 m | 32.0 s | 0.5 m | 4.2% |
| **Rust Unit Tests** (`cargo test`) | 116.5 s | 1.9 m | 132.6 s | 2.2 m | 18.6% |
| **Tauri App & DMG Build** (`build_dmg.sh` / `cargo build`) | 271.5 s | 4.5 m | 311.3 s | 5.2 m | 43.3% |
| **Total Rust Compilation & Build** | **381.5 s** | **6.4 m** | **443.9 s** | **7.4 m** | **60.8%** |
| **Real-App macOS UI E2E & Smoke Tests** | 182.5 s | 3.0 m | 199.0 s | 3.3 m | 29.1% |
| *(of which Real-App UI Test `e2e_mac.sh`)* | 142.0 s | 2.4 m | 152.0 s | 2.5 m | 22.6% |
| **Artifact Packaging & Upload** | 2.0 s | 0.0 m | 3.0 s | 0.1 m | 0.3% |
| **Total `desktop-macos` Duration** | **627.5 s** | **10.5 m** | **707.9 s** | **11.8 m** | **100.0%** |

### 1.2 Parallel Jobs (Linux `ubuntu-latest`) — Coarse Breakdown

| Job | Phase | Median (s) | Median (min) | p90 (s) | p90 (min) |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **`e2e`** | Setup (pip, playwright, npm ci, vite build) | 48.0 s | 0.8 m | 67.3 s | 1.1 m |
| | Playwright Browser Tests (`test_viewer_browser.py`) | 126.0 s | 2.1 m | 144.1 s | 2.4 m |
| | Artifact Upload | 1.5 s | 0.0 m | 2.0 s | 0.0 m |
| | **Total `e2e` Duration** | **182.0 s** | **3.0 m** | **200.1 s** | **3.3 m** |
| **`test`** (3.10 – 3.13) | Setup (pip install `coperceive[app,griffin,dev,viewer]` + torch) | 58.0 s | 1.0 m | 66.0 s | 1.1 m |
| | Test Execution (`pytest`) | 66.0 s | 1.1 m | 72.0 s | 1.2 m |
| | **Total `test` Duration** | **128.0 s** | **2.1 m** | **140.1 s** | **2.3 m** |
| **`lint`** | Setup (venv creation, pip install, torch cpu wheel) | 60.5 s | 1.0 m | 67.0 s | 1.1 m |
| | Lint Execution (ruff check, ruff format, pyright) | 11.0 s | 0.2 m | 13.0 s | 0.2 m |
| | **Total `lint` Duration** | **74.5 s** | **1.2 m** | **82.1 s** | **1.4 m** |
| **`web`** | Setup & OpenAPI diff, tsc, static build check | 42.0 s | 0.7 m | 46.5 s | 0.8 m |
| **`desktop-check`** | Linux & Windows `cargo check` cross-compilation | 25.0 s | 0.4 m | 32.0 s | 0.5 m |
| **`gitleaks`** | Secret scanning | 7.5 s | 0.1 m | 9.0 s | 0.2 m |

---

## 2. Cache Hit Rate Audit

Audit of the repository cache keys (`gh cache list`):

| Cache Target | Implementation | Hit Rate (Baseline) | Root Cause |
| :--- | :--- | :--- | :--- |
| **Rust dependencies & build artifacts (`src-tauri`)** | None | **0% (Miss)** | No caching step configured. All crates are fetched and compiled from scratch on every run. |
| **Python Standalone Runtime Tarball** | In-script cache (`~/.cache/coperceive-desktop`) | **0% (Miss)** | Runner disk is ephemeral; cache path was not persisted with `actions/cache`. |
| **Linux pip dependencies & wheels (torch, dev deps)** | Standard `setup-python` | **0% (Miss)** | `cache: pip` was not configured on `actions/setup-python@v5`. |
| **Node.js dependencies (`web/node_modules`)** | `actions/setup-node@v4` with `cache: npm` | **~95% (Hit)** | Keyed on `web/package-lock.json`. Restores ~45 MB on macOS, ~75 MB on Linux. |

---

## 3. Key Findings & Optimization Strategy

1. **Rust Build is the Dominant Factor (60.8% of Critical Path)**:
   - Compiling Tauri desktop dependencies from scratch takes ~6.4 minutes median.
   - Implementing `Swatinem/rust-cache` with a stable workspace key and shared-key inheritance from `feat/m2-import` and `main` drastically cuts incremental Rust compilation time.
2. **Duplicate Runs on Agent Branches**:
   - Every push to an agent branch with an open PR triggered two simultaneous runs: `push` and `pull_request`.
   - Limiting `push` triggers to `main` and `feat/m2-import` (and release tags) cuts runner usage by **50%**.
   - Adding `concurrency` with `cancel-in-progress: true` terminates obsolete runs immediately when an agent pushes new commits.
3. **Redundant macOS Builds on Python-Only Changes**:
   - `desktop-macos` is costly and only relevant when desktop bundle, native Rust code, web UI, or app assets change.
   - Using a path filter (`changes` job) skips `desktop-macos` when changes only affect Python library code, CLI tools, documentation, or backend tests, while preserving required check status and running nightly full verification.
4. **Pre-Push Hook**:
   - Running lightweight linters (`ruff`, `pyright`, `tsc -b`, `cargo check`) locally on changed files only catches failures before pushing, eliminating wasted CI queue time.
