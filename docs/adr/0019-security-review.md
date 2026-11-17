# ADR 0019 — Security review: profile sharing, job scripts, checkpoints, folder import, archives

Status: proposed on 2026-11-16, for PM review. Branch `agent/claude-security-review`, PR into `release/v0.1.2`. Nothing outside `docs/` changes in this commit.

## Context

Coperceive opens files other people made:

- server profiles shared within a lab;
- datasets downloaded from the internet or copied from a colleague;
- model checkpoints and result bundles.

It then turns some of their contents into shell commands that run on the user's own cluster account. Each of those is a place where a file can do more than its owner intended. The brief for this review names five areas:

1. **Profile import and sharing**: the `.coperceive-server.json` lab profile file. What its export can leak, and which of its fields reach an SSH argument vector, a remote shell command or a job script on import.
2. **Script-builder quoting**: the CLI's `--slurm` job script (`coperceive/models/slurm.py`) and the app's (`src-tauri/src/run_plan.rs`, `job_submit.rs`), plus every remote `sh -c` string assembled from user or profile values. The plan-parity work (ADR 0018) already found one CLI quoting bug, now fixed, and an unquoted `results_root` in the app.
3. **`torch.load` and other unsafe deserialisation**: when the legacy pickle loader runs on a user-supplied checkpoint, and whether any other path unpickles, `eval`s or imports user data.
4. **Folder import and path confinement**: dataset folders, scene and run ids used as path parts, symlinks, files named inside a manifest, and the local app server.
5. **Archive extraction**: zip-slip and tar-slip, link entries, and decompression bombs in bundles, downloads, map packages and checkpoint archives.

## Decision

### 1. Method

- **One reviewer per area.** Each lists every candidate finding with its location, the code quoted, a concrete scenario (who supplies the file, what the user does, what happens) and a fix. It also lists what it checked and found fine, so the report shows the coverage.
- **A second, skeptical reviewer per area** tries to refute each finding. It looks for validation or confinement elsewhere on the path, reproduces where it can, re-rates the severity, and lists what the first reviewer missed. A finding stays only if it is confirmed or plausible.
- **Reproductions run locally, on crafted inputs, in a scratch directory.** Nothing opens an SSH connection, talks to a cluster, starts the desktop app or uses a real secret.

### 2. Severity

| Severity | Meaning |
| --- | --- |
| critical | Code execution or credential theft from a file a user is likely to open. |
| high | Code execution or an arbitrary file write that needs a plausible user action. |
| medium | Reading or writing outside a root; command injection on the user's own cluster account from a shared file; loss of integrity. |
| low | Hardening and defence in depth. |
| info | Checked and fine. |

### 3. Output

- **`docs/security/review-2026-11-16.md`**: the findings table, one row per finding: area, severity, location, scenario, verdict, fix, owner.
- **Fixes:**
  - **In files no other agent owns**: fixed in this PR, each with a test that fails first, and a CHANGELOG entry.
  - **In files another agent owns** (AGENTS.md; the HPC modules, bundles, the viewer and desktop shell): not edited. They are listed in the table with a proposed fix and routed by the PM.
- **Code execution by design**, such as a model manifest that names a Python entry point, is recorded as a design property with the disclosure it needs. It is not a defect.

## Consequences

- The PM gets one table to route from, with the owner of every row.
- Rows marked info record what was checked, so a later review can start from what is not covered.

## Open questions for the PM

1. Is a shared server profile trusted input (it comes from a colleague) or untrusted (a file from anywhere)? The severity of the profile rows depends on the answer. The review rates them as untrusted.
2. Should the legacy pickle loader for checkpoints exist at all, behind an explicit opt-in, or be removed?
