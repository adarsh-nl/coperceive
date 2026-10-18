# ADR 0011 — Live model plugin (evaluation step B)

Status: proposed on 2026-10-17; reviewed by Adarsh on 2026-10-18, whose answers to the eleven open questions are folded in (section "Review of 2026-10-18"). Awaiting his review of this revision. Nothing here is built. The normative text is `docs/spec/model-plugin.md`; the implementation plan is `docs/superpowers/plans/2026-10-17-eval-step-b.md`. Predictions format 1 (`docs/spec/predictions.md`) is unchanged except for one optional key, `provenance`, in `run.json`. Schema 1 is untouched.

## Context

Step A (ADR 0010) evaluates a run that somebody wrote: by hand, with `coperceive predictions import`, or with a one-off converter. The first real run came from the Griffin vehicle-side checkpoint and took:

- a separate conda environment, `~/.conda/envs/griffin` (Python 3.8.20, torch 1.9.1+cu111, mmcv-full 1.4.0, mmdet3d v0.17.1), built offline from a 2.4 GB wheelhouse because compute nodes cannot reach PyPI (HANDOVER.md, "Task 12");
- one GPU job, `~/scripts/slurm/griffin-infer.sbatch`, 23 min 36 s wall on an A40 in job 627093, of which 11 min 17 s was staging 17.0 GB of camera PNGs to `/local/$SLURM_JOB_ID` and 9 min 37 s was `tools/dist_eval.sh` over 1,490 samples;
- the toolkit's own data conversion (`trans_kitti2nuscenes.py`, `generate_nuscenes_pkl.py`);
- `scripts/griffin/griffin_to_predictions.py` to turn `results_nusc_det.json` into `boxes.parquet` through `validate_boxes` and `write_run`.

The reusable pieces were the converter and the sbatch. Three more released checkpoints sit beside the vehicle one under `/datasets/eemcs/ps/cv/huggingface/griffin/ckpts/griffin_50scenes_25m/`: `drone-side/iter_33024.pth`, `early-fusion/iter_33024.pth` and `cooperative/instance_fusion/iter_33024.pth`. The toolkit also has a result-level late fusion (`tools/eval_late_fusion.sh`, `tools/result_converter/det_result_late_fusion.py`). Each would today be another sbatch and another converter.

Step B makes a model a plugin: something `coperceive` can list, check, run over scenes and, later, stream into the viewer, while the output stays an ordinary step A run so `eval run`, `report`, `compare`, the routes and the figures need no change.

Constraints that bind the design (ADR 0004, ADR 0009, `CLAUDE.md`):

- secrets only from the environment or a keyring, never in code, logs or manifests;
- the server binds `127.0.0.1` and requires a bearer token off loopback (`coperceive/cli.py` `_serve`), with `X-Coperceive-Request: 1` on every POST (`coperceive/app/server.py` `auth_and_csrf`);
- no shell anywhere, argv lists only (`coperceive/app/jobs.py`, `shell=False`);
- path confinement for everything the server reads or writes (`coperceive/adapters/paths.py` `confine`, `coperceive/app/jobs.py` `confine_media`);
- dataset licences respected; the Griffin run carried the MIT notice and the arXiv citation in `run.json` `notes`;
- on the HPC, GPU work runs in a Slurm job on `ps,main-gpu`, scratch lives in `/local/$SLURM_JOB_ID`, compute nodes have no internet, and every sbatch is shown before it is submitted.

## Options

### Plugin interface

1. **A Python protocol in the entry point**, like adapters (`coperceive/adapters/registry.py`). Simple, but the plugin would import into coperceive's interpreter, and the Griffin stack cannot (Python 3.8 against coperceive's 3.10+).
2. **A manifest file named by the entry point**, read without importing the plugin, plus a worker the manifest names. The manifest can be hashed and inspected from any interpreter.
3. **Metadata in `pyproject.toml`.** Not one hashable file, not usable for an uninstalled plugin on a compute node.

### Isolation

1. **In-process.** Ruled out by ADR 0009 decision 6 for models.
2. **A subprocess in the plugin's own interpreter**, argv list, no shell.
3. **A container.** The cluster lists `singularity/3.7.4`, `3.8.0` and `3.9.5` modules (`module avail`, 2026-10-17); no `apptainer` binary or module was found; Docker needs a daemon and is not a user tool here.

### Transport between parent and child

1. **Newline-delimited JSON over stdin/stdout.** Works under `subprocess`, `srun` and `singularity exec` alike; standard library on Python 3.8; nothing to confine or clean up.
2. **A Unix socket.** A path to create, confine and remove; otherwise the same messages.
3. **gRPC or HTTP.** A dependency in the plugin's environment and a port to protect.

### Live transport to the page

1. **SSE.** The server already streams `/api/events` as `text/event-stream` (`coperceive/app/server.py` `_event_stream`) and the page already reads it with `fetch` and a `ReadableStream` (`web/src/App.tsx`). Control goes through POSTs that keep the CSRF header.
2. **WebSocket.** Bidirectional, but a second authentication path and an extra uvicorn dependency check.

### Where the batch job runs

1. **In the CLI process**, wherever it is started: a laptop, or inside a Slurm job.
2. **Through the server's `/api/jobs`.** `ALLOW` would need `sbatch`; a web page would spend GPU hours.
3. **A long-lived model service.** An allocation ends.

## Criteria

- The result is a format 1 run and every step A consumer reads it unchanged.
- The plugin's environment is its own; coperceive never imports it.
- Every rule a result depends on is in the manifest, the protocol or `run.json`: nothing silent.
- No shell, an environment allow-list, path confinement, secrets by name only.
- A rerun with the same inputs costs nothing; an interrupted run keeps what it finished and says so.
- The same command works on a laptop and inside a Slurm job; the HPC specifics live in one emitter.
- The Griffin vehicle run of job 627093 can be reproduced through the plugin and compared box for box.

## Decision summary

Option 2 for the interface, option 2 for isolation with containers reserved, option 1 for both transports, option 1 for scheduling. A model plugin is an installed Python package found through the entry point group `coperceive.models`, described by a YAML manifest, and run as a subprocess in its own interpreter with newline-delimited JSON over stdin and stdout. `coperceive model run` drives it over scenes and writes a step A run through `write_run`, with a per-frame cache under the predictions root. `coperceive model run --slurm` emits the sbatch for the same command. Live mode is `coperceive serve --model <plugin>` with a session started by POST and frame results pushed over SSE; a live run looks like a stored run to the page and can be frozen into one. Griffin is the first plugin.

### Data flow, batch

1. `model run` resolves the manifest (entry point or `--manifest`), picks the model, validates `--option` values, resolves the interpreter.
2. It starts the child with the §Environment allow-list and reads `hello`.
3. For every selected scene, in scene-id order: `configure` with the agents, sensors, directories and the full sample list; then one `frame` per pose time with that sample's poses, image paths and extracted sweeps.
4. Each `result` becomes `PredictionBox` rows, passes `validate_boxes`, and is stored in the cache; a frame already in the cache is not sent.
5. When a scene is complete, `write_run` writes `run.json` and that scene's `boxes.parquet`.
6. `bye`; `run.json` is rewritten with the final `provenance`.
7. `eval run`, `eval report`, the viewer, as today.

## Decisions

### 1. Plugin interface: entry point `coperceive.models` and a YAML manifest

A plugin registers one entry point in the group `coperceive.models`. The value is a `str` or `Path` to the plugin's manifest, the convention `coperceive.adapters` already accepts (`coperceive/adapters/registry.py` `resolve_adapter`). `--manifest FILE` names a manifest without an installed entry point, which is how a plugin reaches a compute node that cannot `pip install`.

The manifest is YAML, loaded with `yaml.safe_load` and validated into frozen dataclasses with key-path errors, as `coperceive/adapters/spec.py` does. YAML and not a Python object because the manifest must be readable without importing the plugin (its environment is not coperceive's), because its bytes are hashed for provenance and the cache key the way `manifest_sha256` already hashes an adapter manifest (`coperceive/adapters/ingest.py`), and because ADR 0009 decision 1 made YAML the house manifest format.

Top-level keys, defined in the spec:

| Key | Content |
| --- | --- |
| `spec_version` | `1`. |
| `name`, `version` | Plugin name (`[a-z0-9_-]+`) and version string. |
| `licence` | `spdx`, a required `notice` and an optional `citation`, copied into every `run.json` `notes`. |
| `environment` | Exactly one of `conda` (an env name or prefix), `python` (an interpreter path), `current` (coperceive's own interpreter) or `container` (reserved); plus `modules` for the Slurm emitter, `secrets` (variable names), `inherit_ld_library_path`. |
| `models` | One or more named models (`vehicle-side`, `drone-side`, …), each with `inputs`, `outputs`, `resources`, `options` and `depends_on` (artefacts of another model of the same plugin). A plugin with one model names it `default`. |
| `inputs` | `modalities`, `agents`, `sensors`, `frames` (`single`, or `sequence` for a model that is stateful across a scene), `raw` templates of raw-release files in the `Template` syntax of `coperceive/adapters/paths.py`, `raw_agent_roots`, `raw_clock`. |
| `outputs` | `categories`, `score_note`, `sources` with notes, the `agent` rows belong to, `artifacts` the model writes, `deterministic`. These fill `RunMeta`. |
| `resources` | `gpu`, `memory_gb`, `seconds_per_sample`, `staging_seconds`, `frame_timeout_s`, `run_timeout_s`, `grace_s`, `slurm_constraint`, `max_inflight`. |
| `commands` | `batch` and `live` argv templates with the closed placeholder set `{python}`, `{plugin_dir}`, `{work_dir}`, `{model}`. |

Installing a plugin on the HPC (Adarsh, 2026-10-18): a plugin is a pip-installable package. Its wheel is built on the login node into a local wheelhouse (`pip wheel plugins/griffin --no-deps -w ~/wheelhouse`) and installed into the `.venv` from there (`pip install --no-index --find-links ~/wheelhouse coperceive-griffin-model`); compute nodes install nothing and never reach PyPI. The Griffin plugin lives in this repository under `plugins/griffin/`; `--manifest FILE` remains the way to run a plugin that is not installed.

### 2. Process isolation: a subprocess over stdin/stdout, containers later through Singularity

The plugin runs as a child started with an argv list and `shell=False`, as `coperceive/app/jobs.py` `run_job` does. The interpreter is the one `environment` resolves to; the parent never activates a conda environment, it runs `<prefix>/bin/python` directly.

The protocol is newline-delimited JSON, one object per line, parent to child on stdin and child to parent on stdout; stderr is the child's log and is copied to `<work_dir>/plugin.log`. Large payloads are passed as paths: image files are the scene's own files, resolved the way `GET /api/scenes/{id}/image` resolves them (`confine_media` with `--media-root`); lidar sweeps are written by the parent into `<work_dir>/inputs/<frame>/` as little-endian float32 `x, y, z, intensity`; plugin artefacts go to `<work_dir>/artifacts/`. Length-prefixed binary frames are not in protocol version 1; the spec reserves the type.

Message types, fields in the spec:

- `hello` (child): protocol version, plugin name and version, models, capabilities, an `environment` description that is part of the cache key, an optional git commit.
- `configure` (parent, per scene): model, scene, agents, source, options, directories, sensors with calibration, the full sample list, device, dependency artefacts.
- `configured` (child).
- `frame` (parent): an increasing id, the sample time, every agent's pose, image paths, extracted sweeps.
- `result` (child): the boxes of that frame, optional artefact names.
- `progress` (child): free text and a fraction.
- `error` (child): a code from a closed set, a message, the frame id when it concerns a frame.
- `cancel` (parent): a frame id or everything.
- `bye` (parent).

Ground truth is never sent; the child sees poses, calibration and sensor data only.

The child's environment is built from an empty map. It receives `PATH`, `HOME`, `LANG`, `LC_ALL`, `LC_CTYPE`, `TMPDIR`, `CUDA_VISIBLE_DEVICES`, `CUDA_HOME` and `NVIDIA_VISIBLE_DEVICES` when the parent has them; `LD_LIBRARY_PATH` only when the manifest says `inherit_ld_library_path: true` (the Griffin env takes CUDA 11.8 from a module that sets it); the variables named in `environment.secrets`, by name, when present; and values the parent sets itself: `PYTHONNOUSERSITE=1`, `PYTHONPATH=<plugin_dir>`, `COPERCEIVE_MODEL_WORK=<work_dir>`, `COPERCEIVE_MODEL_PROTOCOL=1`. Nothing else. Secret values never appear in `run.json`, the cache, the logs or the emitted sbatch; the parent redacts them from anything it writes.

Containers: Singularity/Apptainer first, when built at all, because that is what the cluster has. `environment.container` is reserved in `spec_version` 1 and refused by `model run` until a runtime exists. Adarsh (2026-10-18): plain subprocess first, Singularity later.

### 3. Batch mode writes a step A run

```text
coperceive model run <plugin> --scenes ROOT --out PREDICTIONS --run-id ID
    [--model NAME] [--agents a,b] [--source single|fused] [--scene-ids a,b]
    [--raw-root DIR] [--media-root DIR ...] [--artifacts DIR] [--option k=v ...]
    [--frame-timeout S] [--run-timeout S] [--no-cache] [--work DIR] [--force]
    [--slurm [--submit]]
```

Scenes are taken in the order `coperceive/viewdata.py` `scene_directories` returns them (name order), samples in `pose_times` order, agents in `scene.agents` order; rows keep the child's order within a frame. Results become `PredictionBox` rows, pass `validate_boxes` (`coperceive/evaluation/predictions.py`), and are written per completed scene with `write_run(root, run_id, meta, rows)`, which writes `run.json` and that scene's `boxes.parquet` and refuses an existing `boxes.parquet` without `force`. A scene whose `boxes.parquet` exists is skipped on a rerun, so a run resumes per scene; a resume whose stored `provenance.manifest_sha256` differs from the current manifest is refused without `--force`, so one run never mixes two plugin versions.

`run.json` is a `RunMeta`: `model` is `"<plugin> <model> <version>"`, `score_note`, `categories` and `sources` come from the manifest's `outputs`, `notes` carries the licence notice and citation. The one new key is `provenance`: plugin name and version, `manifest_sha256`, the child's `environment` description, the plugin's git commit when reported, host, start and end time, `status` (`complete` or `partial`), the effective options, cache hits and frames run, dependency artefacts, the Slurm job id when set, and the coperceive version (`coperceive/adapters/ingest.py` `coperceive_version`). `RunMeta.from_json` already ignores unknown keys, so an older reader still loads the file.

Then `coperceive eval run ID --root ROOT --predictions PREDICTIONS` as today.

A partial run (decision 6) is evaluated on its finished scenes only and is marked as such: `eval run` writes `run_status: partial` and `missing_scenes` into every `metrics.json` of that run, and `eval report` and `eval compare` print `partial: N of M scenes` under the title (Adarsh, 2026-10-18).

Rejected: the plugin writing `boxes.parquet` itself (every plugin would reimplement the gate, and a Python 3.8 child would need PyArrow); a streaming Parquet writer per frame (the per-scene cache of decision 7 gives resume without a second file format).

### 4. Live mode: a session started by POST, results pushed over SSE

Scope (Adarsh, 2026-10-18): step B builds batch mode only. Live mode is designed here and in the spec so that the protocol, the cache and the provenance of batch mode fit it, and it is built after batch mode works, as a step of its own. SSE stays the transport: one-way is enough for frames.

`coperceive serve --model <plugin> [--model-name NAME] [--manifest FILE] [--eval-config FILE]` loads the manifest at start and adds a route family under `/api/scenes/{scene_id}/live`:

- `POST /live` starts the one session the server allows (body: model, agents, source, options) and answers `{session_id, run_id: "live:<session_id>"}`;
- `GET /live/{session}/events` is the SSE stream of per-frame results, progress and state;
- `POST /live/{session}/frames` asks for sample times (the page sends the current sample and the next four, the prefetch depth `docs/viewer.md` already describes for frames);
- `POST /live/{session}/freeze` writes the frames seen so far as a step A run with the batch writer;
- `DELETE /live/{session}` stops the child (decision 6).

The server binds loopback by default and requires the token elsewhere, unchanged; POST and DELETE need the CSRF header.

Each result is matched on arrival against the scene's ground truth with the step A matcher at the server's eval config: boxes are placed with `world_box` and matched with `match_sample` (`coperceive/evaluation/evaluate.py`, `coperceive/evaluation/matching.py`) through a new public `evaluate_frame` factored out of `evaluate_scene`. The SSE event carries the `/predictions/{run}/frame` row shape, so the page draws it with `buildPredictionObjects` (`web/src/viewer/predictionLayer.ts`) in the same colours and styles, and the TP/FP/miss chips count the live rows.

In the Predictions block the session appears in the run list as `run_id: "live:<session>"`, `evaluated: true`, `live: true`; Run and Compare work as for a stored run, and `/predictions/live:<session>/frame` and `/series` answer from the session's memory. A Freeze button posts to `/freeze`; the frozen run then appears beside the live one. AP is not computed live; `eval run` on the frozen run gives it.

### 5. Scheduling: Slurm for batch, a shared allocation for live

`--slurm` writes `<out>/.slurm/<run_id>.sbatch` and prints it; `--submit` additionally runs `sbatch <file>` as an argv list. The default is emit only, because the HPC rule is to show the script before submitting. The emitted script:

- `--partition=ps,main-gpu` and `--gres=gpu:1` when `resources.gpu` is true, else `ps,main-cpu`; `--mem` from `memory_gb`; `--constraint` from `slurm_constraint`; `--mail-type=END,FAIL`; logs under `~/logs/%x-%j.{out,err}`;
- `--time` = `staging_seconds + seconds_per_sample × samples`, rounded up to the next ten minutes, doubled; the two numbers come from the manifest for the first run, and after that from the newest run of the same plugin and model under `--out`, whose `provenance.timings` hold the measured `staging_s` and `per_sample_s` (Adarsh, 2026-10-18: estimates from the manifest, then updated from the last run's provenance);
- `set -euo pipefail`; `module purge` then `module load` of `environment.modules`; `SCRATCH=/local/$SLURM_JOB_ID` with the `trap` of the `CLAUDE.md` template;
- staging with `rsync -a --files-from` of exactly the scene files and the `inputs.raw` files the selected scenes need, listed the way `scripts/griffin/stage_viewer.py` `staged_files` lists them;
- the same `coperceive model run` without `--slurm` against the staged copy, `--work "$SCRATCH/work"`;
- `rsync` of the run into `$HOME/results/$SLURM_JOB_ID/` before the trap fires, then into `--out`.

The script contains no environment value; `environment.secrets` are read from the job's environment, which `sbatch --export` controls. On a laptop or a workstation the same command runs in-process; only the emitter knows about Slurm.

Live mode, once built, on the HPC means `srun --partition=ps,main-gpu --gres=gpu:1 --pty bash`, then `coperceive serve --model griffin` on that node bound to `127.0.0.1`, reached through the two-hop tunnel of HANDOVER.md "Viewing from a laptop". The server and the child share the allocation. The login node runs neither.

### 6. Cancellation and timeouts

The parent sends `cancel`; after `grace_s` (default 10 s, manifest `resources.grace_s`) it sends SIGTERM, and after another `grace_s` SIGKILL. Per-frame `frame_timeout_s` and per-run `run_timeout_s` come from the manifest and are overridden by `--frame-timeout` and `--run-timeout`. A frame timeout cancels that frame and fails the scene; a run timeout stops the run. Completed scenes are kept, the interrupted scene is not written, answered frames stay in the cache, and `provenance.status` is `partial` with `reason`. `evaluate_run` skips a scene without `boxes.parquet` (`coperceive/evaluation/evaluate.py`), so a partial run evaluates as far as it got, and says so (decision 3): `run_status: partial` with `missing_scenes` in every `metrics.json` of the run, `partial: N of M scenes` in `eval report` and `eval compare`. A partial run is never silently complete. Ctrl-C in `model run` is a cancel. Exit codes: 3 for a protocol violation or a child that died, 4 for a timeout or cancel.

### 7. Caching per scene under the run directory

Adarsh's choice (2026-10-18): the cache lives under the run directory as `cache/`, keyed by the plugin, the model, the checkpoint MD5 and the scene.

Location: `<predictions root>/<run_id>/cache/<plugin>/<model>/<checkpoint_md5>/<scene_id>/`. The directory holds `result.json`, the child's validated rows for the whole scene before they became `boxes.parquet`, with the `manifest_sha256`, the effective options, the `hello.environment` and the listing of the inputs the scene used (path, size, `mtime_ns`); and the scene's artefacts (`track_query/`, `det_pkl`, `det_json`, whatever `outputs.artifacts` names). `checkpoint_md5` is the digest the child reports in `hello.environment` (`none` for a model without weights); a manifest may pin it per model and a `hello` that reports another digest is a protocol violation.

Use: before `configure`, the parent looks the scene up; a hit with the same `manifest_sha256` and options is written straight to `boxes.parquet` without starting the scene on the child, and counts in `provenance.cache_hits`; an entry whose recorded manifest digest or options differ is ignored and overwritten; an entry that fails validation on read is deleted and the scene runs again. `--no-cache` neither reads nor writes. `coperceive model cache prune --out ROOT [--run-id ID] [--plugin NAME] [--model NAME]` deletes cache directories and prints the count. Dependencies read from the same place: instance fusion takes the drone-side `track_query` artefacts from `cache/griffin/drone-side/<md5>/<scene>/` of its own run, or from the run `--artifacts DIR` names.

`cache` becomes a reserved child name of a run directory in predictions format 1: `list_runs`, `evaluate_run`, `eval report` and the routes skip it when they enumerate scenes (an additive line in `docs/spec/predictions.md`, written with the code). A scene id can never be `cache`; `check_scene_id` refuses it.

Rejected: the first draft's per-frame cache under `<predictions root>/.cache/models/` (one JSON file per frame, 1,490 per model for the val scenes; per-frame granularity has no use while the first plugin is scene-level, and a cache shared across runs made a run's inputs harder to account for); a cache beside the scene (schema 1 forbids a second writer in a scene directory); SQLite (a second format for small JSON files; directories are rsync-friendly).

### 8. Reuse of step A

A run written by `model run` is a format 1 run. `eval run`, `eval report`, `eval compare`, the three prediction routes, the Predictions block, `export bev --run` and `plot.py` read it unchanged. Step A changes, all additive: `RunMeta` gains an optional `provenance` mapping; `runs_for_scene` (`coperceive/evalview.py`) gains a `live` boolean per run, false for stored runs; `evaluate_scene` is split so a public `evaluate_frame(scene, meta, boxes, config, *, time_us)` exists, with `evaluate_scene` calling it. No column changes in `boxes.parquet` or `matches.parquet`.

### 9. Griffin as the first plugin

One plugin, `griffin`, with four models, measured in the toolkit at commit `9c02ba4a` under `projects/configs_griffin_50scenes_25m/`:

| Model | Config | Cameras | Checkpoint | Needs | Rows |
| --- | --- | --- | --- | --- | --- |
| `vehicle-side` | `vehicle-side/tiny_track_r50_stream_bs8_48epoch_3cls.py` | 4 (`num_cams = 4`) | `vehicle-side/iter_33024.pth` | raw tree | `vehicle`, `single` |
| `drone-side` | `drone-side/tiny_track_r50_stream_bs8_24epoch_3cls_eval.py` (`save_track_query = True`) | 5 (`num_cams = 5`) | `drone-side/iter_33024.pth` | raw tree | `drone`, `single` |
| `early-fusion` | `early-fusion/tiny_track_r50_stream_bs8_48epoch_3cls.py` | 9 (`num_cams = 9`, both sides) | `early-fusion/iter_33024.pth` | raw tree, converter `--early_fusion` | `vehicle`, `fused` |
| `instance-fusion` | `cooperative/instance_fusion/tiny_track_r50_stream_bs8_48epoch_3cls.py` | 4 | `cooperative/instance_fusion/iter_33024.pth` | `drone-side` artefact `track_query` (`read_track_query_file_root`) | `vehicle`, `fused` |
| `late-fusion` (optional) | `cooperative/late_fusion/…` through `det_result_late_fusion.py` | none | none | both single-agent `det_pkl` artefacts | `vehicle`, `fused` |

The brief said instance fusion needs the two single-agent outputs. The toolkit says otherwise: instance fusion takes the drone-side track queries that the drone-side eval config writes, and late fusion is the model that takes both detection pickles. The manifest's `depends_on` names artefacts per model, and the driver runs the dependency model first unless `--artifacts DIR` points at an earlier run's cache. Adarsh confirmed the dependency on 2026-10-18: instance fusion depends on the drone pass. Late fusion stays optional and unscheduled.

Environment: `conda: griffin` (`~/.conda/envs/griffin`), `modules: [nvidia/cuda-11.8, nvidia/cuda-11.x_cudnn-8.6]`, `inherit_ld_library_path: true`, no secrets. The worker is Python 3.8 code in `plugins/griffin/coperceive_griffin_model/`, with no `zip(strict=True)`, no `match`, no `X | Y` at runtime (job 627063 failed on exactly that).

Inputs: `inputs.raw` templates for both sides' `scene_infos.json`, `calib/{sensor}.json`, `pose/{frame:06d}.json`, `label/{frame:06d}.txt` and the cameras the model uses, with `raw_agent_roots: {vehicle: vehicle-side, drone: drone-side}` and `raw_clock: {scale_us: 100000, offset_us: 0}` (`griffin.yaml` `clock.scale_us: 100000`). The label files are listed because the toolkit's `trans_kitti2nuscenes.py` reads them to build its tables; the detector's forward pass takes images and poses. The plan's Griffin task checks that claim by running one scene with its label contents replaced and comparing the boxes.

How the worker answers frames: the toolkit's inference is a whole-dataset `tools/dist_eval.sh` over nuScenes-style tables, not a per-frame API, and the Agile track head is stateful within a scene (`frames: sequence`). The first version runs the toolkit once per `configure` over the requested scenes (conversion, info pkl, `tools/test.py`, as `griffin-infer.sbatch` does) and answers each `frame` from the resulting `results_nusc_det.json`. Frames are then free; the first costs the whole scene. Per-frame inference through `model.forward_test` is not attempted in step B (Adarsh, 2026-10-18): Griffin is a scene-level batch plugin, which is what the per-scene cache of decision 7 is shaped for. A `det_json` option makes the worker answer from an existing detection JSON without a GPU, which is what `model check` and the conversion tests use.

Conversion is the rules of `scripts/griffin/griffin_to_predictions.py`: `translation` is the world (ENU) centre, `size` is nuScenes `wlh` and becomes `size_lwh = (size[1], size[0], size[2])`, `rotation` is `[qw, qx, qy, qz]`, normalised when its norm is within `1e-3` of 1 (`QUATERNION_SLACK`) and refused otherwise, `detection_name` is the category, `detection_score` the score, `frame` is `world`, and the sample token maps to `scene_id` and `time_us` through `scene.json`, `sample.json`, `BASE_US` and `STEP_US`. `run.json` carries the `score_note`, `model` and `notes` texts of the vehicle run of job 627093: the MIT notice `Copyright (c) [2025] [Jiahao Wang]` and the citation `arXiv:2503.06983`. The eval config stays the user's; the Griffin `gt_class_map` is documented in `docs/eval.md`.

Acceptance: the plugin's vehicle-side run over the ten val scenes must equal job 627093's `boxes.parquet` per scene (`pa.Table.equals(check_metadata=False)`), and `eval compare` between the two runs must print `+0.000` throughout. Same checkpoint, same rules; any difference is a finding.

Two checks that need no GPU (Adarsh, 2026-10-18: scrambled labels plus one known-answer fixture plugin). The scrambled-label run of one scene, with every staged `label/*.txt` replaced by one unrelated line, must give the same boxes as the normal run, which shows the detector never read the labels. The fake plugin of the tests gains a `known-answer` model that emits each requested agent's ground-truth boxes shifted by an `offset_m` option: `model run` then `eval run` must give the exactly predictable counts (offset 0: every ground-truth box a true positive; an offset beyond the matching threshold: every box a false positive and every ground truth a miss). That is the proof that the driver, the writer and step A agree, independent of any real model.

## Security review

| Threat | Mitigation |
| --- | --- |
| A manifest or option injects a command | `commands` are argv templates with a closed placeholder set; `shell=False`; a placeholder outside the set is a load error, as `coperceive/adapters/paths.py` `Template` already refuses unknown placeholders. |
| The child reads outside the scene or raw root | The parent resolves every path it sends through `confine`; the child runs with `cwd=<work_dir>`; on the HPC the staged copy under `/local/$SLURM_JOB_ID` is all it sees. The parent cannot stop a hostile child from opening other files; an installed plugin is trusted like any installed package, as ADR 0009 says for adapters. |
| The child writes outside `<work_dir>` | The parent reads only `artifacts/` and `plugin.log` under `<work_dir>`; `boxes.parquet` is written by the parent; conformance rule 5 compares a directory listing before and after. |
| A secret leaks | Secrets reach the child only by the names in `environment.secrets`; the emitted sbatch, `run.json`, the cache, `plugin.log`, `parent.log` and the SSE stream never carry environment values; the parent redacts every secret value from anything it writes. |
| Parent environment leaks | The child's environment is the allow-list of decision 2; `PYTHONPATH` is set, not inherited. |
| The child exhausts memory or time | `frame_timeout_s`, `run_timeout_s`, SIGTERM then SIGKILL; Slurm enforces `--mem` and `--time`. |
| A malformed message crashes the parent | Every message is validated before use; an invalid line ends the run with exit 3 and `status: partial`. |
| Path traversal in `run_id`, `scene_id`, `session_id` | `check_run_id` and `check_scene_id` (`coperceive/evaluation/predictions.py`); a `session_id` is a parent-generated token looked up in memory, never joined to a path. |
| Live routes start a GPU process from the network | `POST /live` exists only when `serve --model` was given; loopback by default, token elsewhere; POST and DELETE need `X-Coperceive-Request: 1`; one session per server. |
| Ground truth reaches the model | The protocol has no field for annotations; conformance rule 6 runs the child with `annotations.parquet` unreadable. |
| Licence terms ignored | `licence.notice` is required and copied into every `run.json`; the Griffin plugin carries the MIT line and the arXiv citation. |

## Consequences

- A run from a plugin and a run from a converter are indistinguishable to every step A consumer. The only visible difference is `provenance` in `run.json`.
- `coperceive/models/` is a new package under strict pyright. The Griffin worker under `plugins/griffin/` is Python 3.8 code, excluded from pyright, parsed with `feature_version=(3, 8)` in a test, and exercised by running it.
- A plugin needs no PyPI access on the HPC: its wheel is built on the login node into a local wheelhouse and installed from there, or it is passed with `--manifest`. Its model environment is the plugin author's problem, as it was for the Griffin build.
- The viewer changes only when live mode is built, later: one run-list flag, a Live row and one route family, reusing the prediction layer, chips, inspector and timeline. Step B leaves the page as it is.
- The cache adds one directory per scene, model and checkpoint under the run directory, with the child's rows and artefacts; `cache` becomes a reserved name in a run directory. The size is not measured until the first run.
- `coperceive serve` gains four flags. The default server behaviour without `--model` is unchanged.

## What step B does not do

Live mode (designed in decision 4 and the spec, built after batch mode works), tracking metrics, image-space outputs, several live sessions at once, container execution (Singularity later), a parent on a different host than the child, binary payload frames, per-frame Griffin inference (Griffin stays a scene-level batch plugin), and any dataset other than the ones schema 1 already holds.

## Review of 2026-10-18

Adarsh answered the eleven open questions of the first draft; each answer is folded into the decision it names.

| # | Question | Answer | Where |
| --- | --- | --- | --- |
| 1 | SSE or WebSocket for live frames | SSE: one-way is fine for frames. | Decision 4. |
| 2 | Plain subprocess first, Singularity later | Yes. | Decision 2. |
| 3 | Live mode now, or batch only | Batch only now. Live mode is designed and built after batch works. | Decision 4; plan Tasks 9 and 10 deferred. |
| 4 | Cache location | Under the run directory's `cache/`, keyed by plugin, model, checkpoint MD5 and scene. | Decision 7; spec §Cache. |
| 5 | Installing plugins on the HPC | Pip-installable wheels from a local wheelhouse built on the login node. | Decision 1; spec §Installation; plan Task 11. |
| 6 | One Griffin plugin or four | One plugin exposing four models. | Decision 9. |
| 7 | Per-frame Griffin inference | Not yet. Griffin is a scene-level batch plugin. | Decision 9. |
| 8 | Dependency correction | Confirmed: instance fusion depends on the drone pass. | Decision 9. |
| 9 | Partial runs | Evaluated on the finished scenes only, marked `partial` in `metrics.json` and the report. | Decisions 3 and 6; spec §Partial runs. |
| 10 | Labels staged for the toolkit | Scrambled labels plus one known-answer fixture plugin. | Decision 9 acceptance; plan Task 4. |
| 11 | Resource estimates | From the manifest, then updated from the last run's provenance. | Decision 5; spec §Provenance `timings`. |

No step B code exists. The next step is Adarsh's review of this revision.
