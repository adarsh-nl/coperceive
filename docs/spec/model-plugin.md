# Model plugins, spec version 1

This is the contract between `coperceive` and a model plugin: the manifest a plugin ships, the protocol the parent speaks to the plugin's process, the environment and the working directory the child gets, the provenance a plugin run adds to `run.json`, the cache key, the CLI and the conformance rules. The decision is [ADR 0011](../adr/0011-live-model-plugin.md). The output of a plugin run is a predictions format 1 run ([predictions spec](predictions.md)); nothing here changes `boxes.parquet`, `matches.parquet` or `metrics.json`.

Words: the **parent** is the `coperceive` process (`model run`, `model check`, or the server in live mode). The **child** is the plugin's process. A **frame** is one request for one sample time of one scene.

## Discovery

A plugin registers one entry point in the group `coperceive.models`. The entry point's name is the plugin name. Its value, once loaded, is a `str` or a `pathlib.Path` to the manifest file. `coperceive model list` shows every entry point; `--manifest FILE` on `run`, `check`, `inspect` and `serve --model` names a manifest file directly. A plugin named by both resolves to the file. The plugin directory (`{plugin_dir}`) is the manifest file's parent.

## Installation

A plugin is a pip-installable package with the entry point above. On the HPC, where compute nodes have no PyPI, its wheel is built on the login node into a local wheelhouse and installed into the `.venv` from there, with no index:

```text
pip wheel plugins/griffin --no-deps -w ~/wheelhouse
pip install --no-index --find-links ~/wheelhouse coperceive-griffin-model
```

Compute nodes install nothing. A plugin that is not installed runs with `--manifest FILE`. (Adarsh, 2026-10-18.)

## Manifest

YAML, one document, loaded with `yaml.safe_load`. An unknown key anywhere is an error that names its key path. Types: `str`, `int`, `float`, `bool`, `list`, `map`.

### Top level

| Key | Type | Required | Default | Meaning |
| --- | --- | --- | --- | --- |
| `spec_version` | int | yes | | `1`. |
| `name` | str | yes | | `[a-z0-9_-]+`; equal to the entry point name when installed. |
| `version` | str | yes | | Free; goes into `run.json` `model` and `provenance`. |
| `licence` | map | yes | | §Licence. |
| `environment` | map | yes | | §Environment. |
| `models` | map str → map | yes | | At least one model. Keys are `[a-z0-9_-]+`. |
| `commands` | map | yes | | §Commands. |
| `notes` | str | no | null | Free text. |

### Licence

| Key | Type | Required | Default | Meaning |
| --- | --- | --- | --- | --- |
| `spdx` | str | no | null | SPDX identifier, e.g. `MIT`. |
| `notice` | str | yes | | The copyright line to carry into `run.json` `notes`. |
| `citation` | str | no | null | A citation line, carried into `notes` after the notice. |

### Environment

Exactly one of `conda`, `python`, `current`, `container` is required.

| Key | Type | Default | Meaning |
| --- | --- | --- | --- |
| `conda` | str | | A conda environment name or prefix. A name resolves to `$CONDA_ENVS_PATH/<name>` entries, then `~/.conda/envs/<name>`. The interpreter is `<prefix>/bin/python`. Nothing is activated. |
| `python` | str | | A path to an interpreter. Relative paths resolve against `{plugin_dir}`. |
| `current` | bool | | `true`: the parent's own `sys.executable`. For plugins whose dependencies are installable in coperceive's environment, and for the conformance fake. |
| `container` | map | | `image` (str), `runtime` (`singularity`). Reserved: `model run` refuses it with exit 1 until a runtime is implemented. |
| `modules` | list str | `[]` | HPC module names the Slurm emitter writes as `module load`. Ignored elsewhere. |
| `secrets` | list str | `[]` | Environment variable names copied from the parent's environment to the child when present. Names only. |
| `inherit_ld_library_path` | bool | `false` | Pass the parent's `LD_LIBRARY_PATH` through. |

### Models

Each entry of `models`:

| Key | Type | Required | Default | Meaning |
| --- | --- | --- | --- | --- |
| `description` | str | no | null | One line. |
| `checkpoint_md5` | str | no | null | MD5 of the weights file the model loads. When set, a `hello.environment.checkpoint_md5` that differs is a protocol violation. |
| `inputs` | map | yes | | §Inputs. |
| `outputs` | map | yes | | §Outputs. |
| `resources` | map | yes | | §Resources. |
| `depends_on` | list map | no | `[]` | Each `{model: <model key>, artifact: <name>}`: an artefact a run of that model writes under `artifacts/` (§Working directory). |
| `options` | map str → map | no | `{}` | Each `{type: str|int|float|bool, default, choices?, description?}`. `--option k=v` values are checked against this. |

#### Inputs

| Key | Type | Required | Default | Meaning |
| --- | --- | --- | --- | --- |
| `modalities` | list str | yes | | Subset of `camera`, `lidar`, `radar` (schema 1 `Modality`). |
| `agents` | str or list str | no | `any` | `any`, or schema 1 agent types the model runs on. |
| `sensors` | list str | no | `["*"]` | Glob patterns over `sensor_id`; a sensor of a listed modality that matches is sent. |
| `frames` | str | yes | | `single` (each frame independent) or `sequence` (stateful within a scene; frames arrive in time order and `configure` resets state). |
| `raw` | list str | no | `[]` | Templates of raw-release files the model needs, relative to `--raw-root`, in the template syntax of `coperceive/adapters/paths.py` `Template` with the placeholders `{scene}`, `{agent}`, `{agent_root}`, `{sensor}`, `{time_us}` and `{frame}`. `--raw-root` is required when the list is non-empty. A `..` segment is refused at load. |
| `raw_agent_roots` | map str → str | no | `{}` | Agent type or agent id → the `{agent_root}` directory under the raw root (Griffin: `{vehicle: vehicle-side, drone: drone-side}`; the Griffin agent ids are `vehicle` and `drone`, `coperceive/adapters/builtin/griffin.yaml`). Required when a template uses `{agent_root}`. |
| `raw_clock` | map | no | null | `{scale_us: int, offset_us: int}`; `{frame} = (time_us - offset_us) / scale_us`, which must be exact. Required when a template uses `{frame}`. |
| `other_runs` | bool | no | `false` | The model needs the artefacts named in `depends_on`. |

#### Outputs

| Key | Type | Required | Default | Meaning |
| --- | --- | --- | --- | --- |
| `categories` | list str | yes | | `run.json` `categories`. |
| `score_note` | str | yes | | `run.json` `score_note`. Non-empty. |
| `sources` | map str → str | yes | | `run.json` `sources`; the child may only emit these. |
| `agent` | str | no | `request` | `request`: rows belong to the agent of the frame request; else a fixed agent type the rows are attributed to. |
| `artifacts` | list str | no | `[]` | Names of artefacts this model writes, for `depends_on`. |
| `deterministic` | bool | no | `true` | Two runs over the same inputs give identical boxes. `false` turns conformance rule 8 into `skipped`. |

#### Resources

| Key | Type | Required | Default | Meaning |
| --- | --- | --- | --- | --- |
| `gpu` | bool | yes | | Needs a GPU. |
| `memory_gb` | int | yes | | Slurm `--mem`. |
| `seconds_per_sample` | float | yes | | Author's estimate for the Slurm `--time` formula, used until a run of this plugin and model exists under `--out`; then that run's provenance `timings` take over (§Provenance). |
| `staging_seconds` | float | no | `0` | Same. |
| `frame_timeout_s` | float | no | `600` | Per frame. |
| `run_timeout_s` | float | no | null | Whole run; null means none. |
| `grace_s` | float | no | `10` | Between `cancel`, SIGTERM and SIGKILL. |
| `slurm_constraint` | str | no | null | Slurm `--constraint`. |
| `max_inflight` | int | no | `1` | Frames the parent may send before a result. |

### Commands

| Key | Type | Required | Meaning |
| --- | --- | --- | --- |
| `batch` | list str | yes | argv. Placeholders: `{python}`, `{plugin_dir}`, `{work_dir}`, `{model}`. No other `{…}` is allowed; no shell. |
| `live` | list str | no | argv for live sessions; defaults to `batch`. |

Example (abridged from the Griffin plugin):

```yaml
spec_version: 1
name: griffin
version: "0.1.0"
licence: {spdx: MIT, notice: "Copyright (c) [2025] [Jiahao Wang]", citation: "Wang et al., Griffin, arXiv:2503.06983"}
environment: {conda: griffin, modules: [nvidia/cuda-11.8, nvidia/cuda-11.x_cudnn-8.6], inherit_ld_library_path: true}
models:
  vehicle-side:
    inputs: {modalities: [camera], agents: [vehicle], frames: sequence,
             raw: ["{agent_root}/pose/{frame:06d}.json", "{agent_root}/label/{frame:06d}.txt", "{agent_root}/calib/{sensor}.json", "vehicle-side/camera/{sensor}/{frame:06d}.png"],
             raw_agent_roots: {vehicle: vehicle-side, drone: drone-side}, raw_clock: {scale_us: 100000, offset_us: 0}}
    outputs: {categories: [car, bicycle, pedestrian], score_note: "sigmoid class score ...", sources: {single: "vehicle-side camera-only Agile model, no fusion"}}
    resources: {gpu: true, memory_gb: 64, seconds_per_sample: 0.39, staging_seconds: 700, slurm_constraint: "a40|a100"}  # 0.39 s = 9 min 37 s / 1,490 samples, job 627093
commands:
  batch: ["{python}", "-m", "coperceive_griffin_model.worker", "--model", "{model}"]
```

## Protocol

### Framing

UTF-8, one JSON object per line, `\n` terminated, no embedded newlines. Parent to child on the child's stdin; child to parent on the child's stdout. The child's stderr is free text and is copied to `<work_dir>/plugin.log`. A line that is not a JSON object with a string `type` is a protocol violation (§Errors). Lines over 16 MiB are a violation. Binary frames (`type: blob`) are reserved and not valid in version 1.

Every message has `type` (str) and `protocol` (int, `1`).

### Messages

**`hello`** (child, first line, within `frame_timeout_s` of start):

```json
{"type": "hello", "protocol": 1, "plugin": "griffin", "version": "0.1.0",
 "models": ["vehicle-side", "drone-side"], "capabilities": {"live": true, "cancel": true, "max_inflight": 1},
 "environment": {"python": "3.8.20", "torch": "1.9.1+cu111", "checkpoint_md5": "1201f5692e390a75e5b5ab0efa0cae19"},
 "git_commit": "9c02ba4a"}
```

`environment` is a flat map of strings. It must change whenever the child's outputs would change for the same inputs (framework versions, checkpoint digests); its `checkpoint_md5` key is required, must be 32 lowercase hexadecimal characters, and is part of the cache key (§Cache). A `hello` without it, or with any other form, is rejected as `bad_message` with the key path `message.environment.checkpoint_md5` (Adarsh, 2026-10-18). A model without weights reports the MD5 of whatever fixes its outputs, for instance its own worker source file; the fake plugin does that. `checkpoint_path` names that file (absolute): the parent hashes it itself and refuses a `hello` whose digest disagrees (a protocol violation), and on a rerun it recomputes the cache key from the file on disk, re-hashing only when the file's size or mtime differs from the record in the previous run's `provenance.checkpoint`; the digest in an old `run.json` is never trusted by itself (Adarsh, 2026-10-18). Without `checkpoint_path` the parent cannot verify a rerun and starts the child every time. `git_commit` is optional.

**`configure`** (parent, once per scene):

```json
{"type": "configure", "protocol": 1, "model": "vehicle-side", "scene_id": "Town03-000",
 "agents": [{"agent_id": "vehicle", "agent_type": "vehicle"}], "source": "single",
 "options": {}, "work_dir": "/local/627093/work/run", "scene_dir": "/local/627093/scenes/Town03-000",
 "raw_root": "/local/627093/raw", "artifacts": {"drone-side": {"track_query": "/local/627093/work/artifacts/drone-side/track_query"}},
 "device": "cuda:0", "sensors": [{"agent_id": "vehicle", "sensor_id": "front", "modality": "camera",
   "body_from_sensor": {"qw": 1, "qx": 0, "qy": 0, "qz": 0, "tx": 0, "ty": 0, "tz": 0},
   "image_width_px": 1920, "image_height_px": 1080, "fx": 960.0, "fy": 960.0, "cx": 960.0, "cy": 540.0}],
 "samples": [1118500000, 1118600000]}
```

`samples` is the full ordered list of times the parent will request, so a `sequence` model can plan. `raw_root` and `artifacts` are present only when the model declares them. Ground truth is never sent.

**`configured`** (child): `{"type": "configured", "protocol": 1, "scene_id": "Town03-000"}`.

**`frame`** (parent):

```json
{"type": "frame", "protocol": 1, "id": 17, "scene_id": "Town03-000", "time_us": 1118500000,
 "poses": {"vehicle": {"qw": 1, "qx": 0, "qy": 0, "qz": 0, "tx": 12.5, "ty": -3.0, "tz": 0.2}},
 "images": [{"agent_id": "vehicle", "sensor_id": "front", "path": "/local/627093/scenes/Town03-000/vehicle-side/camera/front/000000.png"}],
 "points": [{"agent_id": "vehicle", "sensor_id": "lidar_top", "path": "/local/627093/work/run/inputs/17/vehicle-lidar_top.bin", "count": 2929, "layout": "xyzi_f32le"}]}
```

`id` increases by one per frame within a run. `poses` has every agent that has a pose at `time_us` (world from body). `images` lists the sensors of §Inputs that have a recorded file; `points` lists lidar sweeps the parent extracted from the scene's `points.bin` into `inputs/<id>/`. Missing files are omitted, not invented.

**`result`** (child):

```json
{"type": "result", "protocol": 1, "id": 17, "boxes": [
  {"agent_id": "vehicle", "source": "single", "category": "car", "score": 0.81, "track_id": null,
   "center": [20.1, -4.2, 0.9], "size_lwh": [4.5, 1.9, 1.6], "quaternion": [0.99, 0, 0, 0.12], "frame": "world"}],
 "artifacts": ["det_pkl"]}
```

Each box becomes a `PredictionBox` and passes the same checks as an import: finite numbers, positive sizes, unit quaternion within `1e-6`, `frame` in `{body, world}`, `source` in `outputs.sources`, `agent_id` an agent of the scene, `time_us` (the frame's) a pose time of that agent (`validate_boxes`). A frame may return no boxes. `artifacts` names files the child finished under `artifacts/`.

**`progress`** (child, any time): `{"type": "progress", "protocol": 1, "id": 17, "fraction": 0.4, "text": "loading checkpoint"}`. `id` and `fraction` optional.

**`error`** (child):

```json
{"type": "error", "protocol": 1, "id": 17, "code": "frame_failed", "message": "CUDA out of memory"}
```

`id` is null when the error is not about a frame. Codes: `unsupported` (model, option or input the child cannot serve), `configure_failed`, `frame_failed`, `cancelled`, `bad_message` (the child could not parse a parent line), `internal`.

**`cancel`** (parent): `{"type": "cancel", "protocol": 1, "id": 17}`; `id` null cancels everything outstanding. The child answers each cancelled frame with `error` code `cancelled`.

**`bye`** (parent): `{"type": "bye", "protocol": 1}`. The child exits 0 after it.

### Ordering

1. The child's first line is `hello`. The parent checks `protocol`, that the requested model is in `models`, and that `capabilities.max_inflight >= 1`.
2. The parent sends `configure`; the child answers `configured` or `error` before anything else.
3. The parent sends at most `min(capabilities.max_inflight, resources.max_inflight)` frames beyond the last answered one. Frames of one scene are sent in increasing `time_us`; a `sequence` model may rely on that. The child answers in request order, one `result` or one `error` per `id`.
4. A new scene is a new `configure`; the child discards per-scene state.
5. `bye` ends the session. A child that does not exit within `grace_s` is SIGTERM'd, then SIGKILL'd after another `grace_s`.
6. Anything the parent did not expect (an unknown `type`, an `id` it did not send, a second `hello`) is a protocol violation: the parent sends `bye`, kills on schedule, and exits 3.

## Environment of the child

The child's environment is built from an empty map:

- Copied from the parent when present: `PATH`, `HOME`, `LANG`, `LC_ALL`, `LC_CTYPE`, `TMPDIR`, `CUDA_VISIBLE_DEVICES`, `CUDA_HOME`, `NVIDIA_VISIBLE_DEVICES`.
- Copied when the manifest says `inherit_ld_library_path: true`: `LD_LIBRARY_PATH`.
- Copied when present and named in `environment.secrets`: those variables. Their values never appear in any file the parent writes or any message it logs.
- Set by the parent: `PYTHONNOUSERSITE=1`, `PYTHONPATH={plugin_dir}`, `COPERCEIVE_MODEL_WORK={work_dir}`, `COPERCEIVE_MODEL_PROTOCOL=1`.

`cwd` is `{work_dir}`. `shell` is false. The argv is `commands.batch` (or `live`) with placeholders substituted; a placeholder outside the set, or a `{` left over, is a manifest error at load time.

## Working directory

The parent creates `{work_dir}` and gives it to the child in `configure.work_dir`:

```text
<work_dir>/
  inputs/<frame id>/            sweeps the parent extracted for that frame; deleted after the result
  artifacts/<model>/<name>/     what the child writes for depends_on consumers; kept
  plugin.log                    the child's stderr
  parent.log                    the parent's protocol log, secrets redacted
```

`model run` puts `{work_dir}` under `<out>/.work/<run_id>/` unless `--work DIR` says otherwise; inside a Slurm job the emitter sets `--work /local/$SLURM_JOB_ID/work`. The scene directory and the raw root are given read-only in intent; the parent resolves every path it sends through `confine` against the scenes root, the raw root or a `--media-root`, as the image route does.

## Provenance in `run.json`

`run.json` gains one optional key, `provenance`, a JSON object. Readers that do not know it ignore it (`RunMeta.from_json` ignores unknown keys).

| Key | Type | Meaning |
| --- | --- | --- |
| `plugin` | str | Manifest `name`. |
| `plugin_version` | str | Manifest `version`. |
| `model` | str | The `models` key that ran. |
| `manifest_sha256` | str | SHA-256 of the manifest file bytes. |
| `environment` | map str → str | `hello.environment`. |
| `checkpoint` | object or null | `{path, size, mtime_ns, md5}` of the file `hello.environment.checkpoint_path` names, hashed by the parent. A rerun reuses `md5` only while `size` and `mtime_ns` are unchanged. |
| `git_commit` | str or null | `hello.git_commit`. |
| `coperceive_version` | str | `coperceive/adapters/ingest.py` `coperceive_version()`. |
| `host` | str | `socket.gethostname()`. |
| `started_utc`, `ended_utc` | str | ISO 8601 `Z`. |
| `status` | str | `complete` or `partial`. |
| `completed_scenes` | list str | Scene ids whose `boxes.parquet` was written by this invocation or found complete. |
| `reason` | str or null | For `partial`: `timeout`, `cancelled`, `plugin_error`, with the message. |
| `options` | map | The effective options after defaults. |
| `cache_hits`, `frames_run` | int | Scenes served from the cache, frames the child answered, in this invocation. |
| `timings` | map | `staging_s`, `per_sample_s`, `wall_s` measured by this invocation (`staging_s` is 0 outside a Slurm job). The Slurm emitter of the next run of the same plugin and model reads them in place of the manifest's estimates. |
| `dependencies` | map | `{<model>: {<artifact>: <path>}}` for every `depends_on` entry that was used; `{}` otherwise. |
| `slurm_job_id` | str or null | `$SLURM_JOB_ID` when set. |

`model` in `run.json` is `"<plugin> <model> <version>"`; `notes` is the licence `notice`, then `citation`, then the manifest `notes`, joined with ` | `. `score_note`, `categories` and `sources` are the model's `outputs`.

## Partial runs

A run whose `provenance.status` is `partial` is evaluated on its finished scenes only. `eval run` writes `run_status: "partial"` and `missing_scenes` (the selected scene ids without `boxes.parquet`) into every `metrics.json` it writes for that run; `eval report` and `eval compare` print `partial: N of M scenes` under the title. A complete run writes `run_status: "complete"` and no `missing_scenes`. (Adarsh, 2026-10-18.)

## Cache

Location: `<predictions root>/<run_id>/cache/<plugin>/<model>/<checkpoint_md5>/<scene_id>/`. The key is those four values: the manifest `name`, the `models` key, `hello.environment.checkpoint_md5` (required, 32 lowercase hex characters; a model without weights hashes its own code) and the scene id. `cache` is a reserved child name of a run directory; readers of predictions format 1 skip it when they enumerate scenes, and `check_scene_id` refuses it.

Contents of a scene's directory:

- `result.json`: `{"manifest_sha256": "…", "options": {…}, "environment": {…}, "inputs": [{"path": "…", "size": n, "mtime_ns": n}, …], "rows": [<result rows of every frame, in time order>], "written_utc": "…"}`. `inputs` lists every image, sweep and raw file the scene's frames referenced, sorted by path, relative to the scenes root or the raw root.
- the artefacts the model declared in `outputs.artifacts`, copied from `<work_dir>/artifacts/` for that scene (Griffin: `track_query/`, `det_pkl`, `det_json`).

Rules: the parent looks a scene up before `configure`; an entry whose `manifest_sha256` and `options` equal the current ones is written to `boxes.parquet` without running the scene and counts in `provenance.cache_hits`; an entry that differs in either is ignored and overwritten after the scene runs; an entry whose rows fail validation on read is deleted and the scene runs again. The parent writes an entry only after the scene's rows passed validation. `--no-cache` neither reads nor writes. A `depends_on` artefact is read from the dependency model's cache directory of the same run, or of the run `--artifacts DIR` names. `coperceive model cache prune --out ROOT [--run-id ID] [--plugin NAME] [--model NAME]` deletes the matching cache directories and prints the count.

## CLI

```text
coperceive model list
coperceive model inspect <plugin> [--manifest FILE]
coperceive model check <plugin> [--manifest FILE] [--model NAME] [--keep]
coperceive model run <plugin> --scenes ROOT --out PREDICTIONS --run-id ID
    [--manifest FILE] [--model NAME] [--agents a,b] [--source NAME] [--scene-ids a,b]
    [--raw-root DIR] [--media-root DIR ...] [--artifacts DIR | --no-auto-dependencies] [--option k=v ...]
    [--frame-timeout S] [--run-timeout S] [--no-cache] [--hash-inputs] [--work DIR] [--force]
    [--slurm [--submit]]
coperceive model cache prune --out ROOT [--run-id ID] [--plugin NAME] [--model NAME]
coperceive serve ... --model <plugin> [--model-name NAME] [--manifest FILE] [--eval-config FILE]
```

`list` prints `name  source  location` per plugin, as `adapters list` does. `inspect` prints the validated manifest as YAML plus the resolved interpreter path, or the reason it cannot be resolved. `run` prints one line per scene (`scene <id>: <frames> frames, <hits> cached, <boxes> boxes`), then `wrote <out>/<run_id>` and `scenes N frames M cached C boxes B`. `--slurm` prints the sbatch path and its text; `--submit` runs `sbatch` and prints the job id. `--model` is required when the plugin has more than one model, unless one of them is keyed `default`, which is then chosen (the convention of ADR 0011 decision 1; Adarsh, 2026-10-18). For a model with `depends_on`, the dependency model is run first and its artefacts land in the run's cache (§Cache) unless `--artifacts DIR` names an earlier run or `--no-auto-dependencies` turns the missing artefact into an error. A resumed run whose stored `provenance.manifest_sha256` differs from the current manifest is refused without `--force`.

## Exit codes

| Code | `model run` | Child |
| --- | --- | --- |
| 0 | Every selected scene complete. | Exited after `bye`. |
| 1 | User error: unknown plugin, invalid manifest, unresolvable environment, bad option, existing run without `--force`, missing `--raw-root`, container environment. | |
| 2 | argparse. | |
| 3 | Protocol violation or child exit before `bye`; the run is `partial`. | Any non-zero exit before `bye` is reported as 3 by the parent. |
| 4 | Timeout or cancel; the run is `partial`. | |

## Live session

Designed in this version, built after batch mode works (Adarsh, 2026-10-18: batch only now). Nothing in this section is implemented in step B; it is here so that batch mode's protocol, cache and provenance fit it.

Available only when `serve` was started with `--model`. One session per server. Paths are under `/api/scenes/{scene_id}/live`; POST and DELETE need `X-Coperceive-Request: 1`; the token rule is the server's.

| Method | Path | Body |
| --- | --- | --- |
| POST | `/live` | request `{model?, agents?, source?, options?}`; answer `{session_id, run_id: "live:<session_id>"}`; 409 when a session exists. |
| GET | `/live` | `{sessions: [{session_id, scene_id, model, frames_done, state}]}`; `state` is `starting`, `ready`, `running`, `stopped`, `failed`. |
| GET | `/live/{session}/events` | SSE. Events `frame` (`{time_us, rows, counts}` where `rows` is the `/predictions/{run}/frame` row shape after matching), `progress`, `error`, `state`. |
| POST | `/live/{session}/frames` | `{times_us: [...]}`; the parent requests those frames in order, skipping ones already answered; answer `{queued: n}`. |
| POST | `/live/{session}/freeze` | `{run_id}`; writes the answered frames as a run with the batch writer and `provenance.status` `partial` when frames remain; answer `{run_id, scenes, frames}`. |
| DELETE | `/live/{session}` | Stops the child (§Ordering 5); answer `{state}`. |

While a session exists, `GET /predictions` lists `{run_id: "live:<session_id>", evaluated: true, live: true, run: <the RunMeta the freeze would write>, config, outputs}`, and `/predictions/live:<session>/frame` and `/series` answer from memory with the stored-run shapes. Stored runs carry `live: false`.

Matching of a live frame uses `evaluate_frame` with the server's eval config, the same rules as `eval run` for statuses and counts at the operating point; AP is not computed live.

## Conformance: `coperceive model check`

`model check <plugin>` builds a synthetic scene in a temporary directory (`coperceive/models/synthetic.py`: two agents, a vehicle and a uav, four pose times, a car and a pedestrian, the shape of `tests/cooperative.py`; written with `coperceive.store.write_scene`, plus one PNG per camera and a small point cloud when the model lists those modalities; `--raw-root` adds a raw tree for models with `inputs.raw`), runs the child over it in batch mode with `environment` resolved, and reports pass or fail per rule. It writes nothing outside the temporary directory unless `--keep`. A plugin must pass every rule:

1. `hello` arrives first, within `frame_timeout_s`, with `protocol: 1`, the manifest's `name` and `version`, every manifest model in `models`, and a non-empty `environment`.
2. `configure` is answered with `configured` for every model named in the manifest, or with `error` code `unsupported` when the synthetic scene lacks an input the model needs (that model is then reported as `skipped`, not failed).
3. Every `frame` is answered with exactly one `result` or `error` carrying its `id`, in request order, within `frame_timeout_s`.
4. Every box in every `result` passes `PredictionBox` construction and `validate_boxes`, uses a `source` from `outputs.sources` and a `category` from `outputs.categories`.
5. The child writes only under `{work_dir}`: the check compares a listing of the temporary directory before and after.
6. The child does not read ground truth: the check runs with the scene's `annotations.parquet` made unreadable (mode `000`) and the run must still succeed. On a filesystem where that cannot be enforced the rule is reported as `not checked`.
7. The child exits 0 within `grace_s` after `bye`, and exits after SIGTERM within `grace_s` when a frame is cancelled mid-run.
8. Two runs over the same scene produce identical `result` messages, box for box, or the manifest sets `outputs.deterministic: false`.
9. `cancel` on an outstanding frame is answered with `error` code `cancelled` when `capabilities.cancel` is true.
10. No line on stdout is anything but a protocol message.

Output: one line per rule, `ok`, `fail <reason>`, `skipped <reason>` or `not checked <reason>`, then `check <plugin>: N ok, M fail, K skipped`. Exit 0 when `fail` is 0.

## What this version does not define

Binary payload frames, more than one live session, container execution, tracking outputs, image-space boxes, and a parent that runs on a different host than the child. The live session is defined above and not built in step B.
