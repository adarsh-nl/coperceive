# ADR 0018 — Plan parity: one test holds the app's job script to the CLI's

Status: accepted with [ADR 0021](0021-app-model-runs-follow-the-cli-slurm-job.md) (PM decision, 2026-11-16). This ADR describes the parity harness. ADR 0021 holds the decisions: the CLI is the reference, and it lists each difference the app may keep, with its reason. Where the two disagree, ADR 0021 wins.

## Context

Two code paths write the Slurm job script for one evaluation:

- **The app.** `build_run_plan` and `save_run_plan` (`src-tauri/src/run_plan.rs`), then `render_run_script` (`src-tauri/src/job_submit.rs`).
- **The CLI.** `coperceive model run --slurm` (`coperceive/models/slurm.py::plan_sbatch`).

[`docs/release/v0.1.1-eval-pipeline-test.md`](../release/v0.1.1-eval-pipeline-test.md) runs the Griffin acceptance job through the CLI and expects the app to submit the same job.

The first comparison (2026-11-16, PR #167) found six blockers: `--output-dir`, the checkpoint option, the scenes directory, the post-run checks, the manifest path and the GPU and CUDA modules. It also found a CLI quoting bug in the job's last `echo`. PR #180 fixed the six blockers in the app, and ADR 0021 decided the remaining differences. The quote fix reached `release/v0.1.2` as a backport (f5cbfa4). Its test is `tests/test_model_slurm.py::test_paths_with_spaces_and_quotes_keep_the_script_valid`.

## Decision

### 1. One set of cases, both renderers, one comparator

**`tests/fixtures/plan_parity/cases.json`** holds the logical inputs of each case: the paths, the model, its options, a fixed run id and the cluster profile.

Accepted cases:

| Case | What it covers |
| --- | --- |
| `base` | The release doc's Griffin vehicle run. |
| `mixed_partition` | A partition whose discovery lists a100 before a40. |
| `modules_no_gpu_discovery` | A modules profile (python/3.12) with no GPU discovery. |
| `modules_profile_other_python` | A profile python module other than the venv's. |
| `venv_profile` | A venv profile running `<venv>/bin/python -m coperceive`. |
| `venv_script_command` | A venv profile running the console script `<venv>/bin/coperceive`. |
| `proxy_jump_modules` | A modules profile reached through a jump host on port 2222, on a CPU partition, data and results under `/data`, with `--option min_score=0.05`. |
| `spaces_and_quotes` | Scenes, raw root and media root under `te st/it's "q"`, and a code root with a space. |

Refusals (`refusals` in `cases.json`) are inputs the app refuses. Each carries the exact message, a reason and a citation:

| Refusal | The app says | The CLI |
| --- | --- | --- |
| `results_root_with_space` | `#170`'s message: a results folder with a space, a `#` or a quote is refused. | Takes it. The CLI writes no results folder into an `#SBATCH` line, and refuses a `--slurm-logs` folder with a space for the same reason. |
| `no_media_root` | "Choose the media root on the cluster before checking the plan." | Takes it: `--media-root` is optional. Not decided (see below). |

**`src-tauri/tests/plan_parity.rs`** renders the app's side:

- It drives the New evaluation screen's call chain through public functions, with a fake cluster. The fake cluster panics on `sbatch`, `scancel` or `squeue`. Its `model list --json` answer is `model_list.json`, which `test_plan_parity.py` holds to the CLI's real output.
- It holds every render to the committed `app/<case>.sbatch` byte for byte. It writes those files only under `COPERCEIVE_UPDATE_PARITY=1`. Python cannot run this renderer, so this test owns staleness.
- It holds every refusal to its exact message, and checks that no render exists for it.
- `proxy_jump_changes_the_ssh_arguments_and_not_the_script`: for each case with a `proxy_jump`, the SSH arguments carry `-J <jump>` (and the port) with the jump host and no `-J` without it. Both renders must succeed, and they must be byte-identical to each other and to the committed render.

**`tests/test_plan_parity.py`** renders the CLI's side with `--slurm`, never `--submit`, and compares:

1. **The whole scripts, line by line.** Every line only one side has must match a row of `allowed_differences.json`. Every app-side row must match in every case it applies to, so the app cannot drop a listed line.
2. **The scratch setup, step by step** (`_scratch_setup`). Before `module purge`, both sides must, in this order: start with `set -euo pipefail`, assign the scratch folder the profile's template, `mkdir` it, set the EXIT `trap` that removes it, export `TMPDIR` under it, and make `TMPDIR` and the work folder.
3. **The run block, in place.** `set +e`, `model run`, `RC=$?`, `set -e` and the exit `echo` must come in that order. After them come `environment.json` (written by the run's interpreter), the work copy, and the exit with the run's status.
4. **The `model run` line, field by field.** It is parsed by the real `build_parser()`, with the CLI's staged paths mapped back. The ledger's `argv_fields` excuse only `out` and `staging_seconds`. The app's options also go through the manifest's `check_options`.
5. **`--gres=gpu:1`, the work copy's rsync filter, and `bash -n`** on both scripts.
6. **The refusals.** `model run --slurm` takes each refused input and writes valid shell, so a refusal stays a listed difference rather than a hidden one.

### 2. What is compared, and what is only listed

Nothing is skipped without being listed. Every line is either identical on both sides or a ledger row with a reason. Some lines get a structural check as well. The lines PR #167's comparison did not reach are covered as follows:

| Line(s) | How it is held |
| --- | --- |
| `set -euo pipefail` | It is not in the ledger, so it must be identical on both sides. The scratch-setup check also requires it to be the first command. |
| `trap … EXIT` | A ledger row pins each side's line exactly. The scratch-setup check compares what it removes, the signal, and its place: after the `mkdir`, before anything runs. Before that check, moving the app's trap after the run block passed the harness. |
| `SCRATCH` / `SCRATCH_DIR` | A ledger row pins each side's line. Only the variable's name differs (ADR 0021, "Scratch setup"). The scratch-setup check requires both values to equal the profile's template. |
| `mkdir` of scratch, `TMPDIR`, work | A ledger row pins each side's line. The scratch-setup check compares the folders and their order. The app's `-m 0700` is listed in ADR 0021, "Scratch setup". |
| Other `mkdir` lines | Rows with reasons, compared nowhere else, because the other side has no counterpart: the CLI's staging, out, results and predictions folders, and the app's `OUT_DIR` and `OUT_DIR/<plan id>`. See ADR 0021, "Staging", "Copy home" and "`environment.json` path". |

### 3. The ledger

`tests/fixtures/plan_parity/allowed_differences.json` has one kind of row: `intended`, decided by ADR 0021, with a reason and a citation. The test fails on any one-sided line that no row matches. It also fails on any row that no longer matches, so the ledger cannot go stale. A row that names a case's paths lists the cases it applies to.

PR #167's `defect` and `undecided` kinds are gone, and so are its six blockers, each settled by PR #180:

- `--output-dir` is no longer passed;
- the checkpoint option is passed only when the model declares one;
- a scenes directory is saved with a listing fingerprint;
- the post-run checks require `run.json`, and `boxes.parquet` only when boxes were written;
- the manifest path comes from `model list --json`;
- the GPU request and CUDA modules come from the manifest.

PR #167's refusal cases `scenes_directory` and `model_list_entry_point` are dropped for the same reason: every accepted case now covers them.

### 4. Never submit, never SSH

No test, fixture or script in this work calls `sbatch`, `scancel`, `squeue` or `ssh`. The fake cluster panics if anything tries.

## Consequences

- Any change to either renderer that changes the job a cluster would run fails a test, unless ADR 0021 and the ledger record it with a reason.
- CI (`.github/workflows/ci.yml`; the three desktop jobs run on a pull request only when the `desktop` path filter matches, which includes `tests/fixtures/plan_parity/**` and `coperceive/models/**`):
  - **Linux** (`desktop-linux`, `ubuntu-22.04`) runs `cargo test --manifest-path src-tauri/Cargo.toml -q`, 20 times. This is the full suite, including `src-tauri/tests/plan_parity.rs`.
  - **macOS** (`desktop-macos`, `macos-15`) runs `cargo test --verbose --color=never` in `src-tauri`. This is also the full suite, so it includes `plan_parity.rs`.
  - **Windows** (`desktop-windows`, `windows-latest`) runs `cargo test --lib --verbose --color=never` in `src-tauri`. `--lib` excludes `tests/`, so `plan_parity.rs` does not run on Windows.
  - `tests/test_plan_parity.py` runs in the Python job's `pytest`.

## Not decided here

1. **No media root.** The app refuses a built-in plan with no media root. The CLI's `--media-root` is optional. ADR 0021 covers the script for inputs both sides accept, so it does not decide whether the app should accept an empty media root. The `no_media_root` refusal pins the current message, so a change in either direction is seen. This is for the PM.
2. **A single quote in the code root.** The app writes `PYTHONPATH` with `shell_quote` (`'\''`), while `plan_sbatch` uses `shlex.quote` (`'"'"'`). The value in the shell is the same, but the bytes differ, and ADR 0021 lists `PYTHONPATH` as identical. The fix belongs in `job_submit.rs`, which the HPC owner owns (AGENTS.md §3), so it is routed. Until it is fixed, `spaces_and_quotes` puts only a space in its code root.
