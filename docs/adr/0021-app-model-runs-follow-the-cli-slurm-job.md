# ADR 0021 — App-submitted model runs follow the CLI's Slurm job

Status: accepted (PM decision, 2026-11-16)

## Rule

For a built-in model, the desktop app's job script writes what `coperceive model run
--slurm` (`coperceive/models/slurm.py` `plan_sbatch`) writes for the same inputs. The
CLI is the reference. A line may differ only for a reason in the table below, and
`tests/fixtures/plan_parity/allowed_differences.json` lists each such line with that
reason. `tests/test_plan_parity.py` and `src-tauri/tests/plan_parity.rs` enforce this.

## Remaining differences

| Line(s) | CLI | App | Why |
|---|---|---|---|
| `--job-name` | `model-<plugin>-<run id>` | `coperceive-run-<plan id>` | The app lists and manages only its own jobs by this prefix (ADR 0015). |
| Scratch setup | `SCRATCH`, staging folders, `RUN_ID` | `SCRATCH_DIR` (mode 0700), `TMPDIR`, `--work` folder, `OUT_DIR` | Same job-owned scratch, trap and TMPDIR under another name. The app's `--out` is already on results_root (ADR 0015 §3a). |
| Staging (`STAGE_START`, `rsync --files-from`, `STAGING`) | stages scenes and raw files into scratch | none | The app runs against data already on the cluster. |
| `model run` arguments | staged `--scenes`/`--raw-root`/`--out`, `--staging-seconds` | the cluster paths, `--out <results_root>/runs/<plan id>` | Follows from staging and the output location. Every other parsed field is equal. |
| Copy home (`rsync "$SCRATCH/out/"`, `echo "done:"`) | copies the run from scratch to results and `--out` | none | The app's run is written in place. Only the work directory's logs and artefacts are copied, with the same filter. |
| Work copy target | `<results>/$SLURM_JOB_ID/work/` | `$OUT_DIR/work/` | The same filter, into the run's own folder. |
| `environment.json` path | `$SCRATCH/out/$RUN_ID/` | `$OUT_DIR/<plan id>/` | The same file, in the same place relative to `run.json` and after the run, written where each side's run lives. |
| Exit | `exit "$RC"` at the end | `if [ "$RC" -ne 0 ]; then exit "$RC"; fi` before the post-run checks | The same status. The app then checks its outputs. |
| Post-run checks | none | `run.json` must exist; `boxes.parquet` is required only when `run.json` records `provenance.boxes_written` > 0 | Success is exit 0 plus `run.json`, as the results views need. A run without boxes passes. |
| Venv `COPERCEIVE_PYTHON` / `PATH` | none | exported on a venv profile | A bare `coperceive` must resolve to the profile's venv. The CLI runs an absolute interpreter. |
| Progress `echo` lines, comments | estimate notes | plan notes | Cosmetic. |

Everything else is identical, and the parity test fails on any other difference:

- `--partition`, `--gres=gpu:1` with the manifest's `--constraint` (no GRES for `gpu: false`), `--cpus-per-task`, `--mem`, `--mail-type=END,FAIL`.
- `--output`/`--error`: `<cluster HOME>/logs/%x-%j.{out,err}`, the CLI's `--slurm-logs` default. `%x` is the job name, `coperceive-run-<plan id>`, which the app's log viewer reads by. The app makes the folder with `mkdir -p` at submit, before `sbatch`.
- `--time`: the CLI's `time_limit` estimate is the form's default. The user can edit it, and a value over the partition's discovered limit is refused before the preview.
- `module purge`, the interpreter's `python/<v>` first, then the manifest's modules.
- `export PYTHONPATH=<the coperceive root the run's python imports>`.
- The run block (`set +e`, run, `RC=$?`, `set -e`, `echo`).
