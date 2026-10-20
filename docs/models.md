# Run a model

`coperceive model` drives a detection model as an isolated subprocess in its own Python environment over schema 1 scenes and writes a predictions format 1 run (`run.json` with `provenance` plus one `boxes.parquet` per scene). Every [step A consumer](eval.md)—`eval run`, `eval report`, `eval compare`, `serve --predictions` and `export bev --run`—reads the output unchanged. The normative contract is [the model-plugin spec](spec/model-plugin.md); the architectural decisions are in [ADR 0011](adr/0011-live-model-plugin.md).

```bash
coperceive model list
coperceive model inspect griffin
coperceive model check fake --manifest tests/fake_model_plugin/model.yaml
coperceive model run fake --manifest tests/fake_model_plugin/model.yaml \
  --scenes /path/scenes --out /path/predictions --run-id demo
coperceive model cache prune --out /path/predictions --plugin fake
```

## Commands

- `coperceive model list` enumerates installed plugins registered under the `coperceive.models` entry-point group (`name  source  location`).
- `coperceive model inspect <plugin> [--manifest FILE]` validates the manifest, prints it as canonical YAML, and reports the resolved interpreter path (or why it cannot be resolved).
- `coperceive model check <plugin> [--manifest FILE] [--model NAME] [--keep] [--work DIR] [--raw-root DIR] [--option k=v ...]` builds a synthetic cooperative scene (vehicle and UAV, four pose times, camera PNGs and lidar sweeps as requested by the model's modalities) and verifies the ten conformance rules of [the spec](spec/model-plugin.md#conformance-coperceive-model-check):
  1. `hello` arrives first with `protocol: 1`, matching plugin name and version, every manifest model, and a 32-hex `environment.checkpoint_md5`.
  2. `configure` is answered with `configured` (or `error` code `unsupported`, which marks the model `skipped`).
  3. Every `frame` is answered in order within `frame_timeout_s`.
  4. Every box passes `PredictionBox` and `validate_boxes` with a declared source and category.
  5. No files are written outside `{work_dir}`.
  6. The child does not read ground truth (`annotations.parquet` is `chmod 000` during the check).
  7. Clean exit `0` within `grace_s` of `bye`.
  8. Two passes over the same scene return identical boxes (unless `outputs.deterministic: false`).
  9. `cancel` on an outstanding frame is answered with `cancelled` when `capabilities.cancel` is true.
  10. Nothing but protocol JSON lines appears on `stdout`.
- `coperceive model run <plugin> --scenes ROOT --out PREDICTIONS --run-id ID` runs a model in batch mode over every scene (or `--scene-ids a,b`), validates every returned box, writes `<out>/<run_id>/<scene_id>/boxes.parquet` and `<out>/<run_id>/run.json`, and populates the per-scene cache. When a plugin defines multiple models, `--model NAME` selects one (or the model keyed `default` is chosen implicitly). Exit codes: `0` when all selected scenes complete, `1` on a user or configuration error, `3` on a protocol violation or early child exit (leaving a `partial` run), `4` on a frame or run timeout (`partial`).
- `coperceive model cache prune --out PREDICTIONS [--run-id ID] [--plugin NAME] [--model NAME]` removes cached scene directories and prints `pruned <count>`.

## Worked example: the fake plugin

`tests/fake_model_plugin/` is a complete, dependency-free plugin that runs in `coperceive`'s own interpreter (`environment: {current: true}`) and finishes in under a second on a login node. Its manifest (`tests/fake_model_plugin/model.yaml`):

```yaml
spec_version: 1
name: fake
version: "0.0.1"
licence:
  notice: "fixture, no licence"
environment:
  current: true
models:
  default:
    description: one car box five metres ahead of each requested agent
    inputs:
      modalities: [camera, lidar]
      frames: single
    outputs:
      categories: [car, pedestrian]
      score_note: constant 0.9
      sources:
        single: fake constant offset
    resources:
      gpu: false
      cpus: 1
      memory_gb: 2
      seconds_per_sample: 0.01
    options:
      extra: {type: bool, default: false, description: emit a pedestrian too}
      misbehave: {type: str, default: "", description: fault injection for tests}
  known-answer:
    description: the ground truth of every requested agent, shifted by offset_m along x (tests only)
    inputs:
      modalities: [camera]
      frames: single
    outputs:
      categories: [car, pedestrian, sign, truck, pole, cone]
      score_note: constant 0.9
      sources:
        single: ground truth shifted by offset_m
    resources:
      gpu: false
      cpus: 1
      memory_gb: 2
      seconds_per_sample: 0.01
    options:
      offset_m: {type: float, default: 0.0, description: shift along world x in metres}
      scramble_labels: {type: bool, default: false, description: rotate each box category to a different class}
commands:
  batch: ["{python}", "{plugin_dir}/worker.py", "--model", "{model}"]
```

### Writing a manifest

Every manifest has `spec_version: 1`, `name`, `version`, `licence` (`notice` required, optional `spdx` and `citation`, copied into `run.json` `notes`), `environment`, `models` and `commands`. Unknown keys anywhere fail validation with their dotted key path.

- **`environment`** picks the interpreter without activating a shell:
  - `conda: <name or prefix>` resolves `<prefix>/bin/python` under `$CONDA_ENVS_PATH` or `~/.conda/envs/<name>`.
  - `python: <path>` uses a specific interpreter path.
  - `current: true` uses `coperceive`'s own `sys.executable`.
  - `modules: [...]` lists HPC environment modules for the Slurm emitter (`module load ...`).
  - `secrets: [VAR_NAME, ...]` names environment variables to pass through from the parent process; values are never stored in manifests, `run.json`, cache entries, logs or sbatch files, and any secret value of 4 or more characters is redacted to `[redacted]` in `parent.log`, `plugin.log` and error strings.
  - `inherit_ld_library_path: true` forwards `LD_LIBRARY_PATH` when required by CUDA modules.
- **`inputs`** declares `modalities` (`camera`, `lidar`, `radar`), `agents` (agent types or `any`), `sensors` (glob patterns), `frames` (`single` or stateful `sequence`), and optional `raw` path templates (with `{scene}`, `{agent}`, `{agent_root}`, `{sensor}`, `{time_us}`, `{frame}` plus `raw_agent_roots` and `raw_clock`). Every path sent to the child is resolved through `confine` against `--scenes`, `--raw-root` or `--media-root`.
- **`outputs`** sets `categories`, `score_note`, `sources`, `agent` (`request` or a fixed agent type such as `vehicle` for fused outputs), `artifacts`, `transient_artifacts`, and `deterministic`.
- **`resources`** sets `gpu`, `cpus`, `memory_gb`, `seconds_per_sample`, `staging_seconds`, `conversion_seconds`, `frame_timeout_s`, `run_timeout_s`, `grace_s`, `slurm_constraint` and `max_inflight`.
- **`commands.batch`** is an argv template using only `{python}`, `{plugin_dir}`, `{work_dir}` and `{model}`, executed with `shell=False`.

## Protocol

The parent and child communicate over `stdin` and `stdout` with UTF-8 newline-delimited JSON (`protocol: 1`, max 16 MiB per line). `stderr` is written to `<work_dir>/plugin.log`, and every protocol line goes to `<work_dir>/parent.log` after secret redaction.

1. **`hello` (child → parent, first line):** reports `plugin`, `version`, `models`, `capabilities` (`live`, `cancel`, `max_inflight`), `git_commit`, and `environment`. `environment.checkpoint_md5` (32 lowercase hex chars) is mandatory; when `environment.checkpoint_path` is given, the parent hashes the file on disk itself and rejects any mismatch as a protocol violation. Models without weights hash their own worker source file.
2. **`configure` (parent → child, once per scene):** sends `model`, `scene_id`, `agents`, `source`, validated `options`, `work_dir`, `scene_dir`, optional `raw_root` and `artifacts` (`{model: {artifact: path}}`), `device`, calibrated `sensors`, and the ordered `samples` list (`time_us`). Ground truth is never sent.
3. **`configured` (child → parent):** confirms setup for `scene_id` and may attach `timings` (`{"conversion_s": ..., "inference_s": ...}`). The child may emit `progress` messages before `configured` while a dataset-level toolkit initializes.
4. **`frame` (parent → child, in increasing `time_us`):** sends `id`, `scene_id`, `time_us`, `poses` of all agents present at that timestamp, confined camera `images`, and lidar `points` extracted into `<work_dir>/inputs/<id>/` as little-endian float32 `x, y, z, intensity` (`xyzi_f32le`, removed once the frame is answered).
5. **`result` (child → parent):** returns `id`, `boxes` (`agent_id`, `source`, `category`, `score`, `track_id`, `center`, `size_lwh`, `quaternion`, `frame` in `{body, world}`), and any finished `artifacts`. Every box is validated with `PredictionBox` and `validate_boxes`, and boxes for an unrequested agent fail the scene as a protocol violation.
6. **`bye` (parent → child):** closes the session; the parent waits `grace_s`, then sends `SIGTERM` and `SIGKILL` to the child's process group if needed.

## Caching and transient artefacts

Completed scenes are cached under the run directory at:

```text
<predictions root>/<run_id>/cache/<plugin>/<model>/<checkpoint_md5>/<scene_id>/
  result.json
  artifacts/
```

Before sending `configure` for a scene, `model run` checks the cache. On a rerun, the parent never trusts the old `run.json` digest alone: it checks `provenance.checkpoint` (`path`, `size`, `mtime_ns`, `md5`) against the file on disk, re-computing the MD5 whenever the file's size or mtime changed. When `manifest_sha256` and effective `options` match and all cached rows validate, `boxes.parquet` is written directly from the cache without starting the child process (`cache_hits` increments in `provenance`). `--no-cache` disables reading and writing the cache.

### Transient artefacts (`outputs.transient_artifacts`)

Some intermediate artefacts are too large to duplicate into every scene's cache directory or copy back from node-local scratch to `$HOME`. In Griffin, the drone-side pass writes 1,490 `track_query` pickle files (11 GB across the 10 val scenes) that only the `instance-fusion` pass in the same job reads. Listing `transient_artifacts: [track_query]` under `outputs`:

- keeps `track_query` in `<work_dir>/artifacts/drone-side/track_query/` for dependent models in the same run;
- skips copying `track_query` into `<run_id>/cache/.../artifacts/`;
- adds `--exclude='artifacts/drone-side/track_query/***'` to the Slurm job's copy-home `rsync`.

If a later run needs a dependent model whose cached dependency no longer has its transient artefact on disk, `model run` automatically bypasses the cache for the dependency and re-runs it to regenerate the artefact.

## Dependencies (`depends_on`)

A model that consumes another model's artefacts declares `depends_on` in the manifest (`tests/fake_model_plugin/model-two.yaml` and Griffin's `instance-fusion`):

```yaml
depends_on:
  - {model: drone-side, artifact: track_query}
```

When `model run` needs a child for the dependent model:

1. If `--artifacts DIR` is given, it looks up `DIR/<model>/<artifact>` and passes that path in `configure.artifacts`.
2. Otherwise (unless `--no-auto-dependencies` is set), it runs the dependency model first as a standalone predictions run named `<run_id>-<model>` sharing the same `--work` directory, so both runs get their own `boxes.parquet` and `run.json` provenance.
3. The artefact path reaches the dependent child exclusively through `configure.artifacts["drone-side"]["track_query"]`, and is recorded in `provenance.dependencies`. If the dependent model itself is served completely from the cache, no dependency is run.

## Slurm mode and provenance timings

Adding `--slurm` emits `<out>/.slurm/<run_id>.sbatch` along with the exact `rsync --files-from` lists (`.scenes.txt` and `.raw.txt`) for the selected scenes and prints the script without running the model; adding `--submit` submits it via `sbatch`.

- **Staging and scratch:** jobs use `SCRATCH="/local/$SLURM_JOB_ID"` with an `EXIT` trap, stage only the scene files (omitting `points.bin` for camera-only models) and the raw files expanded from `inputs.raw` across thedependency closure, and time the `rsync` wall clock (`--staging-seconds "$STAGING"`).
- **Fault-tolerant copy home:** the script runs `coperceive model run` under `set +e`, captures `RC=$?`, copies `$SCRATCH/out/`, every dependency run `<run_id>-<dep>`, and `<work>/` logs and non-transient artefacts (`*.log`, `artifacts/***`) to `~/results/$SLURM_JOB_ID/` and `<out>/` before exiting with `$RC`. A partial or failed run never loses its completed scenes or logs to the scratch trap.
- **Wall-time estimation (`--time`):** computed as:
  $$\text{staging\_s} + \text{conversion\_s} + \text{per\_sample\_s} \times N_{\text{samples}}$$
  summed over the model's dependency closure, rounded up to the next 10 minutes, and doubled for safety margin. On the first run, the three parts come from the manifest's `resources` (`staging_seconds`, `conversion_seconds`, `seconds_per_sample`). Once a run under `--out` has recorded a timing split in `provenance.timings` (`staging_s` from the parent's `rsync`, `conversion_s` and `inference_s` from the child's `configured.timings`, and `per_sample_s = inference_s / frames_run`), subsequent emissions automatically use the newest run's measured values.
- **GPU provenance and constraints:** `provenance.gpu` records the GPU model name (from `hello.environment.gpu` or `nvidia-smi`). Griffin acceptance runs pin `slurm_constraint: "a40"` so kernel execution matches the reference A40 runs byte for byte; `"a40|a100"` remains a supported manifest setting for non-acceptance runs (both carry compatible SASS in `torch 1.9.1+cu111`, whereas L40S `sm_89` does not).

## Griffin plugin (`plugins/griffin/`)

The Griffin plugin (`coperceive-griffin-model`) wraps the four released Griffin-25m camera-only Agile/UniAD checkpoints in the `~/.conda/envs/griffin` Python 3.8 environment. Because HPC compute nodes have no internet access, build the wheel on the login node into `~/wheelhouse` and install from there with `--no-index`:

```bash
.venv/bin/pip wheel plugins/griffin --no-deps --no-build-isolation --no-index -w ~/wheelhouse
.venv/bin/pip install --no-index --find-links ~/wheelhouse coperceive-griffin-model
```

| Model key | Agent & source | Cameras | Checkpoint MD5 | Dependency |
| --- | --- | --- | --- | --- |
| `vehicle-side` | `vehicle · single` | 4 vehicle | `1201f5692e390a75e5b5ab0efa0cae19` | none |
| `drone-side` | `drone · single` | 5 drone | `41734b8d764d1213935a231a3419f655` | writes transient `track_query` |
| `instance-fusion` | `vehicle · fused` | 4 vehicle | `7e1448188b6e99ca6303575c3466b97f` | `drone-side` `track_query` |
| `early-fusion` | `vehicle · fused` | 4 vehicle + 5 drone | `ccf39651d3e4381ec06e4d0709821949` | none (`--early_fusion` tables) |

Each model also accepts `--option det_json=<path>` and `--option nusc_root=<dir>` to index an existing `results_nusc_det.json` without a GPU, which is what `coperceive model check` and login-node verification use.

## Live mode (designed, deferred)

Live mode (`coperceive serve --model <plugin>` streaming per-frame matches over SSE and freezing a live session into a stored run) is specified in [the model-plugin spec](spec/model-plugin.md#live-session) and [ADR 0011 decision 4](adr/0011-live-model-plugin.md#4-live-mode-a-session-started-by-post-results-pushed-over-sse) so that batch mode's protocol, cache and provenance fit it without changes. Its server routes and viewer controls are deferred to a later step after batch mode.

## What was checked

- `tests/test_model_spec.py` and `tests/test_model_protocol.py` test manifest validation, key-path errors, entry-point discovery, argv rendering, and the newline-delimited JSON protocol codec.
- `tests/test_model_runner.py` tests the environment allow-list, conda interpreter resolution, timeouts, cancellation, process-group cleanup, and secret redaction across `parent.log`, `plugin.log` and error messages.
- `tests/test_model_batch.py` tests batch runs, `provenance` fields (including `gpu` and the `staging_s` / `conversion_s` / `inference_s` split), partial runs in `metrics.json` and `eval report|compare`, the `known-answer` model at offset `0 m` (exact match, `AP = 1.0`) and `10 m` (all misses), and the scrambled-label check (`scramble_labels=true` collapses `AP` from `1.0` to `0.0`).
- `tests/test_model_cache.py` and `tests/test_model_dependencies.py` test cache hits without starting a child, on-disk checkpoint re-hashing when bytes change, `depends_on` automatic `<run_id>-<model>` runs, `--artifacts`, and `transient_artifacts` exclusion.
- `tests/test_model_check.py`, `tests/test_model_cli.py`, `tests/test_model_slurm.py` and `tests/test_model_end_to_end.py` test all ten conformance rules, CLI exit codes, Slurm script generation (`bash -n`, dependency closure, `rsync` filter), and the full chain from `model run` through `eval run`, `/api/scenes/{id}/predictions`, `eval compare` and `--slurm`.
- On the HPC (NVIDIA A40), `griffin25m-vehicle-plugin` (job 629288) reproduced job 627093's `boxes.parquet` byte-for-byte across all 10 val scenes (7,930 boxes, `+0.000` across all four evaluation configs).
