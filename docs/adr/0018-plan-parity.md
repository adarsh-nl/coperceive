# ADR 0018 — Plan parity: one test holds the app's job script to the CLI's

Status: proposed on 2026-11-16, for PM review before the build. Branch `agent/claude-plan-parity`, PR into `release/v0.1.2`. Nothing outside `docs/` changes in this commit.

## Context

Two code paths write the Slurm job script for one evaluation:

- **The app.** `build_run_plan` and `save_run_plan` (`src-tauri/src/run_plan.rs`), then `render_run_script` (`src-tauri/src/job_submit.rs`).
- **The CLI.** `coperceive model run --slurm` (`coperceive/models/slurm.py::plan_sbatch`).

[`docs/release/v0.1.1-eval-pipeline-test.md`](../release/v0.1.1-eval-pipeline-test.md) runs the Griffin acceptance job through the CLI and expects the app to submit the same job. Nothing checks that it does. The one existing test, `run_plan::tests::builtin_argv_parity_with_the_cli_golden_sections`, never reads the CLI golden it is named after.

### What a first comparison found

On 2026-11-16 both paths were rendered for the same inputs, at this branch's base (`a7779ad`). The app ran against a fake cluster: a stub runner that answers the read-only commands and refuses `sbatch`, `scancel` and `squeue`. The CLI ran with `--slurm` and never `--submit`. Nothing was submitted and no SSH connection was made.

**Blockers: an app-submitted Griffin job cannot succeed today.** All five are in `run_plan.rs` or `job_submit.rs`, which belong to the HPC owner (AGENTS.md §3), so they are routed, not fixed here.

1. The inner command passes `--output-dir`, which `coperceive model run` rejects: `build_parser()` exits 2 with "unrecognized arguments". Code: `run_plan.rs:2421-2425`, `job_submit.rs:276-280`.
2. It passes `--option checkpoint=<path>`. The Griffin vehicle-side model declares no `checkpoint` option, so `check_options` refuses it. Code: `run_plan.rs:528-529`.
3. A scenes **directory** cannot be saved. Save fails with "The cluster no longer reports a sha256 for the test split"; only a single file saves. Code: `run_plan.rs:2124-2129`.
4. The post-run checks require `predictions.jsonl` and `metrics.json`. `model run` writes neither, so a successful run exits 1. Code: `job_submit.rs:299-308`.
5. The manifest is the last token of the cluster's `coperceive model list` row, used only when it contains `/`. The real row prints the entry point `coperceive_griffin_model:MANIFEST` (`coperceive/models/registry.py:37-41`), so the app cannot plan Griffin. Code: `run_plan.rs:1797-1802`. **Unverified live**: the Griffin plugin is not installed where this was run, so this was read from the code and reproduced with a fake row.

**One CLI bug.** `slurm.py` ends the script with `echo "done: <shlex-quoted path> (exit $RC)"`. With `--out` under `te st/it's "q"`, the single-quoted word inside double quotes ends the string early, and the script fails `bash -n`. This file is not owned by another agent, so it is fixed here.

**Differences that are not yet decided** include:

- `#SBATCH` resources: `--time`, `--gres`, `--mem`, `--cpus-per-task`, `--constraint`, `--mail-type`.
- The log location.
- The CUDA modules and `PYTHONPATH` lines.
- The `--work` directory.
- The run id: the app's is `plan-…`; the release doc expects `griffin25m-vehicle-plugin`.
- The app refusing a plan with no `--media-root`.
- Values derived from `results_root`, which the app interpolates unquoted: a `"` breaks the script, `$(…)` would run on the node, and `~` is never expanded.

**Differences that are intended, each with a document behind it:**

| Difference | Source |
| --- | --- |
| The `coperceive-` job-name prefix | ADR 0015 |
| No staging block, so no `--staging-seconds` | release doc, "App-plan parity" |
| Output on `results_root` with no copy home | ADR 0015 §3a |
| The scratch subdirectory | `job_submit.rs:148-151` |
| Comment and echo lines | cosmetic |
| ProxyJump | transport only; it never reaches a script |

## Decision

### 1. One set of cases, both renderers, one comparator

- **`tests/fixtures/plan_parity/cases.json`**: the logical inputs of each case. That is the paths, model, extras, a fixed run id that both validators accept, and the cluster profile: partition, scratch template, results root, environment, GPU types, ProxyJump and port. The cases:
  - **base**: the release doc's Griffin vehicle run.
  - **(a) spaces and quotes**: every path under `te st/it's "q"`.
  - **(b) missing optional flag**: no `--media-root` and no extras.
  - **(c) ProxyJump plus module setup**: `modules: [python/3.12]`, a jump host and a port.
- **`src-tauri/tests/plan_parity.rs`** (new, the first file in `src-tauri/tests/`) renders the app's script for each case.
  - It drives the UI's call chain through public functions only, with a fake runner that panics on `sbatch`, `scancel` or `squeue`.
  - It writes nothing unless `COPERCEIVE_UPDATE_PARITY=1`; otherwise it asserts the committed render byte for byte.
  - For case (c) it also checks that ProxyJump changes the SSH arguments and leaves the script byte-identical.
- **`tests/test_plan_parity.py`** renders the CLI's script for each case with `--slurm`, never `--submit`. It then compares the two scripts section by section:
  - the `#SBATCH` keys;
  - the module and export lines;
  - the environment capture;
  - staging, exit-code capture, copy-home and post-run checks;
  - `bash -n` of each script;
  - the inner command. This is tokenised with `shlex`, so quoting style does not count. Runtime words like `$SCRATCH/scenes` are mapped back to their source, and the result is parsed by the real `build_parser()` and `check_options`.

### 2. A ledger of known differences, checked both ways

`tests/fixtures/plan_parity/allowed_differences.json` lists, per case, each difference with a `kind`, a reason and a citation:

- **`intended`**: a difference that a document decides.
- **`defect`**: a difference that is a bug, with the owner it is routed to and the blocker number above.

The test fails on any difference that is **not** in the ledger. It also fails on any ledger entry that **no longer** occurs, so a fix forces the entry out and the ledger cannot go stale. Every undecided difference above goes in as `defect` until the PM decides it, and the report lists them.

This keeps CI green while the blockers exist, which a red test in files this task may not edit could not do. It does not hide them, because each one is named in the ledger and in the report. See open question 1.

### 3. The CLI fix

`slurm.py`'s last `echo` is rewritten so the quoted path stays one shell word. Case (a) proves it: `bash -n` passes on the CLI script. Before the fix, the same case fails `bash -n`.

### 4. Never submit, never SSH

No test, fixture or script in this work calls `sbatch`, `scancel`, `squeue` or `ssh`. The fake runner panics if anything tries.

## Consequences

- Any change to either renderer that changes the job a cluster would run fails a test, unless the ledger records it with a reason.
- The ledger's `defect` entries list what must be fixed before the app is used for a real Griffin submission. **Until blockers 1–5 are fixed, Adarsh should not submit Griffin from the app.**
- `cargo test --manifest-path src-tauri/Cargo.toml` on Linux runs the Rust test. The macOS `cargo test --lib` job does not, because `--lib` excludes `tests/`. That job is in `.github/workflows/ci.yml`, the viewer owner's file, so this is routed if macOS coverage is wanted.

## Open questions for the PM

1. Is the `defect` kind in the ledger acceptable? The alternative is a test that stays red until the HPC owner fixes blockers 1–5.
2. Which of the undecided differences are intended? Above all the `#SBATCH` resources, the run id and the log location.
3. Before any real submission, does Adarsh's `results_root` start with `~`? The app writes it into the script unexpanded.
