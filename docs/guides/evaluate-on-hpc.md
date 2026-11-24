# Evaluate a model on the HPC from the app

This guide walks through evaluating a built-in model plugin or custom pipeline on a Slurm cluster from the desktop app (`web/src/desktop/`), from setting up a server profile to reviewing scores in the viewer.

> **Status on `release/v0.1.2`:** Per-run **AP protocol** selection (`PR #211`), **Plugin interpreter (optional)** (`PR #244`), in-job scoring for built-in models (**Score the run after it finishes**, `PR #245`), and streaming **Open predictions** (`PR #247` / `PR #250`) are all landed on `release/v0.1.2`.

## End-to-end flow

1. **Servers** — configure an SSH cluster profile, shared results folder, and optional per-server plugin interpreter default.
2. **Datasets** — index and prepare the cluster dataset split into schema 1 scenes.
3. **New evaluation** — build, contract-check, and save a run plan (`Plan <id>`).
4. **Submit this run** — review the generated Slurm script, confirm, and submit.
5. **Job watch** — monitor live status, tails, predictions, and terminal report/scoring.
6. **Run history** — inspect past runs, re-run, export repro cards or result bundles, or open predictions.
7. **Viewer** — inspect boxes and `mAP@0.5` / `mAP@0.7` with the run's AP protocol label and compare compatible runs.

---

## 1. Servers (and Settings → Model runs)

Open **Servers** in the sidebar (`HpcServers`) to add or test your SSH profile:

- **Results folder (`results_root`)**: required before checking a plan (`"Set the server profile's shared results folder first."`), and must be an absolute path without spaces, `#`, quotes, backslashes, or control characters (`src-tauri/src/hpc.rs`).
- **Data roots (`data_roots`)** and **Code roots (`code_roots`)**: all checkpoint, split, raw, media, and custom-script paths must stay inside these folders.
- **Environment**: default modules, `conda`, `Python virtualenv`, or `Container (Apptainer/Singularity)` used unless overridden per plan.
- **Plugin interpreter default (optional, `PR #244`)**: in **Settings → Model runs** (`ModelRunSettings.tsx`), choose a server and set **Plugin interpreter for built-in models** (e.g. `/path/to/envs/<env>/bin/python`). **New evaluation** prefills **Plugin interpreter (optional)** with this per-server value.

---

## 2. Datasets (index, prepare, and stale detection)

Open your raw dataset from the **Servers** sidebar (`DatasetView.tsx`):

- Run indexing and click **Prepare whole dataset** or **Prepare remaining** (`PR #220` / `#236` — submits only `not_prepared`, `failed`, and `stale` scenes, skipping `ready` and live `preparing` scenes).
- **Stale scenes (`PR #209`)**: scenes prepared with an older adapter manifest (for example, `v0.1.1` OPV2V, V2XSet, or V2V4Real scenes before `PR #207` geometry fixes) are marked **Stale** with **Re-prepare** and the explanation `"Coperceive now converts this dataset differently than when this scene was prepared (adapter manifest changed). Re-prepare it to view it with the current conversion."`
- **Large splits (`PR #219`)**: if the raw-file walk hits its entry or time bound, **Datasets** shows `"Raw check incomplete: the cluster could not check every raw file in time, so prepared scenes stay ready and a change to the raw files may not show here."` Ready scenes are never marked stale from a truncated walk.

---

## 3. New evaluation (plan builder)

Open **New evaluation** (`NewEvaluation.tsx`) in the sidebar. The right-hand **Run plan** inspector tracks progress (`"Ready to save"` / `"<N> of 6 steps complete"`). Nothing is submitted while building or saving a plan.

### Step 1: Server

Choose a saved profile from **Server**. Selecting a server loads its installed model plugins (`coperceive model list --json`) and prefills its **Plugin interpreter (optional)** default.

### Step 2: Pipeline

Choose an installed plugin (`(installed plugin)`) or load a **Custom pipeline script on the cluster** (`(custom)`) with **Load pipeline** (requires a readable `coperceive-pipeline.json` beside the script).

For an installed built-in plugin (`selectedPipeline.source === "builtin"`), fill in:

- **Model key** (`vehicle-side` placeholder) — must match a model key in the plugin manifest.
- **Raw root** (`/data/griffin-release` placeholder) — required when the model manifest declares `inputs.raw`; when it does not (`PR #228`), a note says `"Optional: this model declares no raw inputs. Leave it empty and the job passes no --raw-root."`
- **Checkpoint root (`ckpt_root`)** (`/data/griffin/ckpts` placeholder) — shown when the model declares `ckpt_root` (such as Griffin, `PR #204`), with note `"Required: the released checkpoints folder. The model declares no default."`
- **Media root** (`/data/griffin-release` placeholder) — `"Optional: leave it empty and the job passes no --media-root, as the CLI does."` (`PR #203`)
- **AP protocol** (`PR #211`):
  - **Default** *(preselected)*: `"OpenCOOD-style: per-frame ranking, ground truth from every connected agent within 70 m"`
  - **Legacy (v0.1.1)**: `"for numbers comparable to v0.1.1"`
  - Note: `"Recorded with the run; a later eval run uses it unless told otherwise."` (`model run --protocol legacy` writes `provenance.eval_protocol` into `run.json`).
- **Plugin interpreter (optional)** (`PR #244`):
  - Note: `"A full path on the cluster, passed as --plugin-python. Empty: the model's manifest environment. The default is per server (Settings → Model runs)."`
- **Score the run after it finishes** (`PR #245`):
  - Checkbox (on by default) with note: `"The job runs eval run on the run under the AP protocol above, as coperceive model run --evaluate does, and writes metrics.json per scene and evaluation.json. If scoring fails after the model finished, the job exits 5."`
  - Review row **Scoring**: `"In the same job, after the run (eval run)"` or `"Not scored in the job"`.
- Any extra pipeline-schema arguments appear below these fields.

### Step 3: Checkpoint & Step 4: Test split

- **Checkpoint**: required when the built-in model declares a `checkpoint` option (e.g. OpenCOOD) or a custom pipeline declares a `checkpoint` argument; otherwise shows `"This pipeline takes no checkpoint."`
- **Test split**: for built-in models, enter the prepared scenes folder on the cluster; for custom pipelines without a `test_data` argument, shows `"This pipeline takes no test split."`

### Step 5: Environment

Uses the server profile's environment by default, or check **Override the environment for this plan (otherwise the server profile's environment is used)** to choose **No special environment**, **Environment modules**, **conda**, **Python virtualenv**, or **Container (Apptainer/Singularity)**.

### Step 6: Contract check & Save plan

1. Click **Check plan**. The app validates inputs locally and on the login node over SSH, fingerprints the checkpoint (`sha256`) and split (`listing sha256`), and renders:
   - **Typed argv — one element per line, never a shell string**
   - **Contract-check Slurm script — shown in full, NOT submitted**
2. Verify all checks pass (`Contract check`: `passed`) and click **Save plan to cluster**. The plan is saved to `<results_root>/plans/<plan_id>.json` (`"Saved to <path>. Nothing was submitted."`).

---

## 4. Submit this run

Once the plan is saved, the **Submit this run** card appears below the plan review:

1. Check the walltime summary (`"Walltime <HH:MM:SS> (coperceive model run --slurm estimates <HH:MM:SS>)"` or `"(no estimate: enter one under Advanced)"`).
2. Click **Advanced** if you need to adjust **Walltime**, **CPUs per task**, or **GPU** (`No GPU (CPU-only)` or a discovered partition GRES).
3. Click **Generate submission script** to render and verify the full Slurm batch script against the saved plan and fingerprints on the cluster.
4. Read the script, check **"I have read the full job script above and confirm this submission."**, and click **Submit job**.

### What the job runs

1. Runs `coperceive model run ...` (with `--protocol legacy` if Legacy was chosen, and `--plugin-python <path>` last if set), writes `environment.json`, and copies `work/` logs and non-transient artefacts home.
2. **Scoring step (`PR #245`)**: when **Score the run after it finishes** is checked, if `<output_dir>/<plan_id>/run.json` exists, the job runs `<python> -m coperceive eval run <plan_id> --root <scenes> --predictions "$OUT_DIR"` (adding `--protocol legacy` for legacy plans, and wrapped in `apptainer exec` for container plans). If `run.json` is missing, it logs `eval run skipped: no run.json for <plan_id>` to stderr and sets `EVAL_RC=1`. The model's own non-zero exit code always wins; only if `RC=0` and `EVAL_RC!=0` does the job exit `5`.
3. If **Score the run after it finishes** was unchecked, score the run later on the cluster with:
   ```bash
   coperceive eval run <plan_id> --root <scenes> --predictions <output_dir>
   coperceive eval report <plan_id> --root <scenes> --predictions <output_dir>
   ```
4. Verifies `run.json` (and `boxes.parquet` when `boxes_written` is non-zero) and exits with `$RC`.

---

## 5. Job watch

After **Submit job**, **Job watch** (`JobWatch.tsx`) opens inline and polls `squeue`/`sacct` every 10 s while `pending` or `running`:

- Shows `Job <job_id>`, state (`PENDING — waiting in the queue...`, `RUNNING · <elapsed> · <left>`), `on <node>`, and `AP protocol: <label>`.
- Controls: **Refresh now**, **Tail stdout (50 lines)**, **Tail stderr (50 lines)**, and **Cancel job** (requires checking **I want to cancel this job**).
- While active, **Live output** counts streamed predictions (`<N> new prediction(s)`).
- When ended:
  - **Custom pipelines**: **Run report** reads `metrics.json` or shows `"The pipeline wrote no metrics.json, so the run is incomplete or partial. The contract requires metrics.json before a successful exit."` On failure (`state === "failed"`), shows **Run failed.**, the last 50 stderr lines, and `"Next action: Open the stderr tail above, fix the reported cause (checkpoint, environment, or split), then submit a new run from this plan."`
  - **Built-in runs — Scoring panel (`PR #245`)** (when **Score the run after it finishes** was enabled):
    - `Reading the scoring record…` while reading `evaluation.json`.
    - `Scored.` — shows `AP protocol: <protocol>`, `<N> scene(s) scored[, <N> skipped][, <N> failed to score]`, and `"Per-scene AP is in each scene's metrics.json; the run's pooled AP is what coperceive eval report prints."`
    - `Model finished, scoring failed.` (red, on exit `5`) — shows the `evaluation.json` error (or `"No evaluation.json was written (the run left no run.json to score); the stderr tail below says why."`), up to 5 failing scenes, protocol/counts, and `"The model run itself completed."` below.
    - `The model's finished scenes were scored.` — partial model run (exit `3` or `4`) whose finished scenes were scored; keeps the **Run failed.** panel below.
    - `Not scored: the model did not finish, so the job did not score it.` — model failure or timeout with no `evaluation.json`.
    - `Not scored: the job wrote no evaluation.json.` (red) — job exited `0` without writing `evaluation.json`.
    - `Scoring had errors.` / `Scoring failed.` — `evaluation.json` recorded `status: "errors"` or `"failed"`.
    - Unscored built-in run: `"Run finished. Not scored in the job: score it with coperceive eval run on the cluster."`
- **Open predictions** (`PR #247` / `PR #250`):
  - Opens the run directly in the app's SSH stream viewer (`#/viewer?predictions=<plan>&scene=<first scene>`) with no Slurm viewer job and no separate remote session. If the dataset is already open in the viewer, that stream server is reused; otherwise the app starts one for the run's scenes.
  - Copies only `run.json` and per-scene `boxes.parquet`, `metrics.json`, and `matches.parquet` over SSH (capped at `512 MiB`).
  - For a custom pipeline (`predictions.jsonl`), the plan does not record which dataset it ran on: open that dataset in **Datasets** first so **Open predictions** can import `predictions.jsonl` into `<output_dir>/viewer-predictions` on the cluster before streaming. On success, shows `"Opened the viewer on run <run_id>."`

---

## 6. Run history

Open **Run history** (`RunHistory.tsx`) in the sidebar to inspect jobs from `sacct` over the last 30 days:

- Filter chips: **All**, **Running**, **Pending**, **Completed**, **Failed**, **Cancelled**.
- Columns: **Job**, **Plan**, **AP protocol** (`Default` or `Legacy (v0.1.1)`), **State**, **Time used / limit**, **Started**.
- Inspector actions for the selected job:
  - **Open predictions** (for `completed` or `failed` jobs with a saved plan)
  - **Export repro card** (stages run metadata into a portable repro JSON)
  - **Re-run with same plan** (opens the full submission script preview and requires confirmation before submitting)
  - **Export bundle…** (creates a `.cpbundle` archive)
  - **Cancel job** / **Confirm cancel** (for active jobs)

---

## 7. Viewer (AP and protocol labels)

In the viewer's **Predictions** panel (`web/src/viewer/predictions.tsx`):

- **Run** selects an evaluated run (or marks unevaluated runs `<run_id> (not evaluated)`).
- Scores (`mAP@0.5`, `mAP@0.7`, precision, recall) appear alongside the run's protocol label (for example `ranking per_frame, ground truth connected_union (70 m)` or `legacy (pooled, ego-only)`, matching `coperceive eval report`).
- **Compare** lists only runs evaluated under the same `ranking_scope`, `gt_scope`, and `comm_range_m`. If incompatible runs exist, the panel prints:
  `"Only runs evaluated under the same protocol can be compared. <run_ids> was/were evaluated under another protocol, so the numbers cannot be compared; run coperceive eval run again with one config."`

---

## Error messages reference (verbatim from code)

### Pre-flight validation (`NewEvaluation.tsx` & `runPlan.ts`)

- `"Choose a server profile first."`
- `"Choose a server profile and a pipeline first."`
- `"Save the plan before preparing a submission."`
- `"Check the plan again before saving it."`
- `"Choose the checkpoint and the test split on the cluster for this pipeline."`
- `"Choose the test split on the cluster for this pipeline."`
- `"Choose the model key and raw root for this built-in model."`
- `"Choose the ckpt_root parameter (the released checkpoints root) for this built-in model."`
- `"“<name>” is required. <help>"`
- `"“<name>” must be a whole number."`
- `"“<name>” must be a number."`
- `"“<name>” must be one of: <choices>."`
- `"--plugin-python: the interpreter path contains a line break or another control character."`
- `"--plugin-python: give a full path (got <path>)"`
- `"Enter the walltime for this run: the plan has no time estimate."`
- `"The walltime <text> is longer than the partition's time limit <limit>; enter <limit> or less."`

### Plan build, check, and save (`src-tauri/src/run_plan.rs`)

- `"Choose a <what> path."` / `"The <what> path contains a control character."`
- `"The <what> path may not contain '..'. Paths must stay inside the server profile's data and code folders."`
- `"The <what> path must be an absolute path on the cluster (it may start with ~)."`
- `"The <what> path <path> is outside this server's data and code folders. Pick a path under <roots>."`
- `"Argument <name> must not be empty."` / `"Argument <name> must be text."` / `"Argument <name> must be a whole number."` / `"Argument <name> must be a number."` / `"Argument <name> must be true or false."` / `"Argument <name> must be one of: <choices>."`
- `"Unknown pipeline argument <key>."` / `"Argument <name> is required. <help>"`
- `"--plugin-python: the interpreter path contains a line break or another control character."`
- `"--plugin-python: give a full path (got <path>)"`
- `"This built-in model has no pipeline manifest. Reinstall the model or choose a custom pipeline."`
- `"Set the server profile's shared results folder first."`
- `"The interpreter's pyvenv.cfg names the Python <found>, which is not a module name coperceive can load in the job. Check the venv's pyvenv.cfg on the cluster."`
- `"The interpreter <target> contains a line break or another control character. Fix the server profile's coperceive command or interpreter."`
- `"The Slurm log folder <logs_dir> is not an absolute path without spaces or control characters; #SBATCH --output/--error cannot hold it."`
- `"<python> on the cluster could not import coperceive, so coperceive model run could not start with it either: <stderr>. Check the server profile's coperceive command."`
- `"<python> reports its coperceive under <root>, which is not an absolute path without control characters, so it cannot be written as the job's PYTHONPATH."`
- `"The model <plugin> <key> needs the ckpt_root parameter (the released checkpoints root): choose it on the cluster before checking the plan. It is the folder that holds griffin_50scenes_25m."`
- `"The model <plugin> <key> has no ckpt_root option in its manifest. Clear the checkpoint root field and check the plan again. Options it declares: <declared>."`
- `"The model <plugin> <key> has no checkpoint option in its manifest (it loads its own weights), so it cannot take the checkpoint <checkpoint>. Clear the checkpoint field and check the plan again. Options it declares: <declared>."`
- `"The model option <text> must be KEY=VALUE."`
- `"The model <plugin> <key> has no option <key> in its manifest. Options it declares: <declared>."`
- `"The model <plugin> <key> needs a GPU (gpu: true in its manifest), but the server's discovery found no GPU on partition <partition>. Choose a GPU partition in the server profile, or check the server again if it has GPUs now."`
- `"The models of the plugin <plugin> depend on each other in a loop (<cycle>), so no model can run first. Fix depends_on in the plugin's manifest, then reload the pipeline list."`
- `"Choose the model from the plugin manifest before checking the plan."`
- `"Choose the AP protocol for this run: one of default, legacy."`
- `"Choose the raw root on the cluster before checking the plan."`
- `"The cluster did not report where the <what> resolves to, so coperceive cannot check that it stays inside your folders. Choose a path inside the profile's folders, or ask your administrators for a shell with realpath/readlink -f."`
- `"The checkpoint resolves to <real_path>, which is outside this server's data and code folders. Pick a checkpoint inside them."`
- `"The test split resolves to <real_path>, which is outside this server's data and code folders. Pick a split inside them."`
- `"The contract-check script contains <forbidden>, but this server's compute nodes have no internet. Remove installs/downloads from the pipeline."`
- `"Could not list the installed models on the cluster: <stderr>"`
- `"The cluster's model list has format <format> <version>, which this app does not read. Update the app or the cluster's coperceive so they match."`
- `"Type the path of the pipeline script on the cluster."`
- `"No readable coperceive-pipeline.json next to the script at <script>. Add one so the pipeline's typed arguments can be shared, or fill the fields in yourself."`
- `"This plan id is not one coperceive generated; refusing to save it."`
- `"Choose the installed pipeline to run."`
- `"The pipeline argument <name> collides with coperceive's own --output-dir; rename it."`
- `"The <what> <path> is no longer on the cluster. Fix the path and rebuild the plan."`
- `"The <what> resolves to <real_path>, which is outside this server's data and code folders. Pick one inside them."`
- `"The <what> folder <path> lists no files, so it has no fingerprint and the plan cannot be saved (the submit step compares that fingerprint with the cluster again). Choose the folder that holds the scenes."`
- `"The cluster no longer reports a sha256 for the <what>, so the plan cannot be saved (the submit step compares that sha256 with the cluster again)."`
- `"The cluster's checks no longer pass (<label>): <detail>. Rebuild the plan, fix the item and try again."`
- `"This plan claims a job was submitted; a plan is saved before submission."`
- `"This plan belongs to a different server profile."`
- `"This plan predates the model/raw fields; rebuild this plan before saving."`
- `"The rebuilt command arguments are empty or contain a line break."`
- `"The run plan contains <found>; refusing to save it. Secrets must come from the environment, never from a plan."`
- `"Could not write the plan to <final_path>. <stderr>"`
- `"The plan was written to <final_path>, but it could not be read back for verification."`
- `"The saved plan read back with a different id."`

### Submission & script verification (`src-tauri/src/job_submit.rs` & `run_plan.rs`)

- `"The saved plan records no fingerprint for the <what> <path>, so it cannot be checked before submitting. Rebuild and save the plan. Nothing was submitted."`
- `"Could not check the <what> <path> on the cluster before submitting: <stderr>. Nothing was submitted."`
- `"The <what> <path> is no longer on the cluster. Nothing was submitted."`
- `"The <what> <path> changed since the plan was saved (<detail>). Nothing was submitted. Check the <what>, then rebuild and save the plan."`
- `"No readable saved plan at <path>. Save the plan first, then submit it."`
- `"The saved plan has a different id than the one requested."`
- `"This plan records a submission; it cannot be submitted again."`
- `"This saved plan predates the model/raw/work fields. Rebuild this plan before submitting."`
- `"This saved plan records an unknown AP protocol. Rebuild this plan before submitting."`
- `"This saved plan has no ckpt_root value, but its model declares the required ckpt_root parameter. Rebuild this plan before submitting."`
- `"This saved plan was made before built-in runs used coperceive model run's own output flags, and it would fail on the cluster. Rebuild this plan before submitting."`
- `"The plan's command arguments are empty or contain a line break."`
- `"The model <model> needs a GPU (gpu: true in its manifest), so it cannot be submitted without one. Keep the plan's GPU request (<gres>) or choose another GPU. Nothing was submitted."`
- `"The walltime <walltime> is longer than partition <partition>'s time limit <limit>, so Slurm would refuse the job. Enter <limit> or less. Nothing was submitted."`
- `"The GPU request <gres> asks for <gpu_type>, but the model's manifest pins the GPU with --constraint=\"<constraint>\", so no node would fit. Choose gpu:1 (the constraint picks the type) or a type it names. Nothing was submitted."`
- `"The saved plan's coperceive root <root> is not an absolute path without control characters. Rebuild this plan before submitting."`
- `"The saved plan names a transient artefact <item> that is not <model>/<name>. Rebuild this plan before submitting."`
- `"The saved plan's GPU constraint <constraint> is not a Slurm feature expression coperceive can write. Check slurm_constraint in the model's manifest, then rebuild this plan."`
- `"The saved plan's interpreter <value> contains a line break or another control character, which cannot go into the job script. Fix the server profile, then rebuild this plan."`
- `"The saved plan records the interpreter as found by <source>, which this app does not write. Rebuild this plan before submitting."`
- `"This saved plan's run line does not pass --plugin-python as the plan records it (<path>). Rebuild this plan before submitting."`
- `"This saved built-in plan does not run coperceive model run. Rebuild this plan before submitting."`
- `"This saved plan passes <flag> in the <flag>=VALUE form, which coperceive does not write. Rebuild this plan before submitting."`
- `"This saved plan runs model run with <flag> <value>, but the plan's <what> is <expected>. Rebuild this plan before submitting."`
- `"This saved plan runs model run without <flag>. Rebuild this plan before submitting."`
- `"The scheduler returned <stdout>, which is not a job id. Nothing was submitted by coperceive."`
- `"Read the full job script and tick the confirmation before submitting. Nothing was submitted."`
- `"The rendered script changed since you reviewed it. Preview it again before submitting. Nothing was submitted."`
- `"Could not make the log folder <logs>: <stderr>. Nothing was submitted."`
- `"The scheduler rejected the submission: <stderr_or_stdout>"`
- `"This saved built-in plan does not run coperceive model run."`
- `"This saved built-in plan's run line names no --scenes to score. Rebuild this plan before submitting."`

### Job watch, report, predictions, and history (`job_watch.rs`, `job_report.rs`, `job_history.rs`, `main.rs`)

- `"<job_id> is not a Slurm job id; refusing to query or act on it."`
- `"Job <job_id> is <raw_name>, not this app's job (<expected_name>)."`
- `"Job <job_id> belongs to user <raw_user>, not <expected>; refusing to watch it."`
- `"Slurm returned no state for the job."`
- `"Slurm has no record of job <job_id> for plan <plan_id>. It may have been purged from the accounting database; the run is not running any more."`
- `"<other> is not a log stream; use stdout or stderr."`
- `"The job log path <normalised> escapes its log folder <root>; refusing to read it."`
- `"Ask for between 10 and 200 log lines, got <lines>."`
- `"No log lines yet. The scheduler may not have created the file; try again once the job starts."`
- `"Tick the confirmation before cancelling the job. Nothing was cancelled."`
- `"Job <job_id> is <state>, so there is nothing to cancel."`
- `"Slurm refused the cancellation: <detail>"`
- `"The run output directory <output_dir> escapes results_root <root>."`
- `"The run output <path> escapes results_root."`
- `"No predictions.jsonl yet. The pipeline has not written its first prediction."`
- `"metrics.json does not declare the coperceive-pipeline-metrics schema; the pipeline output does not meet the contract."`
- `"metrics.json does not declare version 1."`
- `"This saved built-in plan's run line names no --scenes."`
- `"This run is from a custom pipeline, whose plan does not record the dataset it ran on. Open that dataset in Datasets first, then Open predictions again."`
- `"Could not read the run: <error>"` / `"Could not copy the predictions: <error>"`
- `"Run <plan_id> at <store>/<plan_id> has no scene with predictions (no boxes.parquet)."`
- `"The run's <what> path contains a control character."` / `"The run's <what> path <path> is not an absolute path."` / `"The run's <what> path <path> may not contain '..'."` / `"The run's <what> path <path> is outside this server's data, code and results folders; Open predictions will not stream it."`
- `"This run has no predictions on the cluster: <at> does not exist. The job may not have started writing yet, or its output was removed."`
- `"This run's predictions folder <at> has no run.json yet, so it is not a finished prediction run."`
- `"The run's predictions are larger than 512 MiB; Open predictions copies at most that."`
- `"The cluster did not finish sending the predictions within 300 seconds."`
- `"The cluster sent a file Open predictions does not read: <path>."`
- `"Could not copy the predictions from <at>: <detail>"`
- `"<data_root> is not one of this profile's data roots; refusing to import predictions there."`
- `"The predictions root escapes the data root."`
- `"predictions.jsonl is empty or missing. Wait for the run to write its first predictions, then try again."`
- `"predictions.jsonl has no scene_id and metrics.json is not written yet. Wait for the run to finish, then open predictions."`
- `"predictions.jsonl has no scene_id and the run covers several scenes. Ask the pipeline to include a scene_id key; coperceive will not guess."`
- `"coperceive predictions import failed: <detail>"`
- `"The plans directory escapes results_root."`
- `"The scheduler did not return the run history. Check that sacct is available on this server."`
- `"Could not read evaluation.json: <error>"` / `"evaluation.json is not valid JSON: <error>"` / `"evaluation.json is not a coperceive-evaluation format 1 record."` / `"evaluation.json has an unknown status <status>."` / `"Could not read the scoring record: <error>"`

## More detail

- [Evaluate predictions](evaluate.md)
- [Run a model](../models.md)
- [Cluster contract](../datasets/cluster-datasets.md)
- [Desktop settings](../desktop/settings.md)
- [Desktop HPC and viewer](../desktop/hpc-and-viewer.md)
