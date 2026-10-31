# Agent Handbook & CI Guidelines

This handbook defines the rules of engagement and workflow expectations for AI coding agents (Antigravity, DeepSeek, etc.) collaborating on the Coperceive repository.

---

## 1. CI Run Reporting (One Run Per Change)

CI is configured to run on `pull_request` for all PR branches, and on `push` only for protected integration branches (`main` and `feat/m2-import`).

- **Report One Run**: When reporting CI status to the user, report **only the single PR run**:
  - Head commit SHA
  - PR run ID (e.g. `gh run list --branch <branch>`)
  - Pass/fail outcome across jobs
- **Automatic Cancellation**: The workflow defines a concurrency group with `cancel-in-progress: true`. Pushing a new commit automatically cancels any obsolete in-progress run on that PR branch. Do not poll or report cancelled obsolete runs.
- **Single CI Check Per Push**: Do not poll CI in tight loops. If waiting for CI, schedule a check after the expected duration or wait for the completion notification.

---

## 2. Pre-Push Hook (Mandatory)

To prevent breaking CI and wasting runner capacity, agents must install and use the repository pre-push hook:

```bash
bash scripts/dev/install-hooks.sh
```

The hook executes targeted, incremental checks on changed files only (< 30 seconds on a warm machine):
- **Python**: `ruff check` and `ruff format --check` on modified Python files; `pyright` on changed Python modules.
- **Frontend**: `npm test` and `npx tsc -b` if files in `web/` changed.
- **Desktop / Rust**: `cargo check` if files in `src-tauri/` changed.

If any check fails, the push is aborted with an actionable error. Fix the issue locally before pushing.

---

## 3. Scope & Boundary Rules

1. **Branch Hygiene**:
   - Always branch off the designated integration branch (e.g., `feat/m2-import`).
   - Never push directly to `main` or `feat/m2-import`.
   - Never merge, tag, publish releases, or modify repository settings unless explicitly instructed by the user.
2. **File Ownership**:
   - Respect file ownership and boundaries between agents. Stay strictly out of files assigned to other agents (e.g., DeepSeek's HPC modules: `HpcServers.tsx`, `hpc.ts`, `job_submit.rs`, `run_plan.rs`, `scripts/desktop/e2e_hpc_*`).
3. **Artifact Hygiene**:
   - Never commit generated web static bundles (`coperceive/app/static`). Static assets must be built dynamically in CI or runtime.
   - Never weaken or loosen test assertions to force a test to pass.
