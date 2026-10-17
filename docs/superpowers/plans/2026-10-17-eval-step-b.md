# Evaluation engine, step B — live model plugin — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run a detection model as a plugin in its own environment, over schema 1 scenes, and write the result as an ordinary predictions format 1 run; emit the Slurm job for the same command on the HPC; stream a live session into the viewer and freeze it into a run; wrap the four released Griffin checkpoints as the first plugin.

**Architecture:** A new package `coperceive/models/` (pure Python, strict pyright: manifest, registry, protocol, runner, inputs, batch driver, cache, conformance check, Slurm emitter, live session, CLI). The child process speaks newline-delimited JSON over stdin/stdout and is started with an argv list. Batch mode writes runs through `coperceive.evaluation.predictions.write_run`; live mode matches frames through a new public `evaluate_frame` in `coperceive/evaluation/evaluate.py` and serves them under `/api/scenes/{id}/live`. The Griffin plugin lives in `plugins/griffin/` as Python 3.8 code and is excluded from pyright. A fake plugin under `tests/fake_model_plugin/` runs in coperceive's own interpreter and is what the unit tests and `model check` exercise.

**Tech Stack:** Python 3.10–3.13 (parent), Python 3.8 (Griffin worker), PyYAML, PyArrow, Pillow (synthetic PNGs), FastAPI/pydantic (app only), React + TypeScript (page). No new core dependency.

**Spec:** `docs/spec/model-plugin.md` (normative), `docs/adr/0011-live-model-plugin.md` (decisions), `docs/spec/predictions.md` (the output format). Section names below (§Manifest, §Protocol, …) are headings of the model-plugin spec unless they say otherwise.

## Global Constraints

- The output of every plugin run is a format 1 run: `write_run`, `validate_boxes` and `PredictionBox` (`coperceive/evaluation/predictions.py`) are the only writers and gates. `boxes.parquet`, `matches.parquet` and `metrics.json` do not change. `run.json` gains the optional `provenance` key only.
- No shell. Every child starts from an argv list with `shell=False`, as `coperceive/app/jobs.py` does. The child's environment is built from the §Environment allow-list, never copied from `os.environ`.
- Secrets: names in manifests, values only in the process environment. No fixture, test, log line, cache entry, `run.json` or emitted sbatch contains a secret value; Task 3 pins redaction.
- Every path the parent sends to a child or writes for it goes through `confine` (`coperceive/adapters/paths.py`) against the scenes root, the raw root, a `--media-root` or the work directory. `run_id` and `scene_id` pass `check_run_id` / `check_scene_id`.
- The server keeps the ADR 0004 rules: loopback by default, bearer token elsewhere, `X-Coperceive-Request: 1` on POST and DELETE. Live routes exist only when `serve --model` was given.
- Python in `coperceive/`: `from __future__ import annotations`, `# SPDX-License-Identifier: Apache-2.0`, ruff clean at 100 columns, strict pyright clean, `zip(..., strict=True)`. Python in `plugins/griffin/`: must run on 3.8.20 (no `match`, no `X | Y` at runtime, no `zip(strict=True)`, no `str.removeprefix`), checked by `tests/test_griffin_model_syntax.py` with `ast.parse(..., feature_version=(3, 8))`.
- Tests: pytest, TDD (write the test, watch it fail, then implement). Fixtures are synthetic: `tests/cooperative.py::cooperative_scene()` (agents `vehicle`, `uav`, `mast`; vehicle poses at 0, 100000, 200000, 300000 µs) and the fake plugin. No dataset bytes, no GPU, no network in CI.
- HPC login node: no command over a minute, single core. GPU and the Griffin toolkit run only inside Slurm jobs; every sbatch is shown to Adarsh before `--submit`. Gates run through `~/scripts/slurm/coperceive-gates.sbatch` with `REPO=<worktree>`.
- Conventional Commits; push after every task; the branch is `feat/eval-step-b` off `docs/adr-0011-eval-step-b`.

## Review Focus

Inputs the spec implies but no happy path exercises, most likely to bite first:

1. A `result` box for an agent the frame did not request (another agent of the scene) passes `validate_boxes` but is wrong → the scene fails with a protocol violation, nothing written (Task 4 test `test_box_for_an_unrequested_agent_fails_the_scene`).
2. A secret's value appears in the child's `error.message` or stderr → `parent.log`, `plugin.log` and the CLI's stderr show `[redacted]` (Task 3 test `test_secret_values_are_redacted_from_logs`).
3. A resumed run whose stored `provenance.manifest_sha256` differs from the current manifest → refused without `--force`, so one run never mixes two plugin versions (Task 4 test `test_resume_refuses_a_different_manifest_without_force`).
4. The emitted sbatch contains no value from the parent's environment, only the names in `environment.secrets` (Task 8 test `test_sbatch_carries_no_environment_values`).
5. A live frame request for a time that is not a pose time of the scene → 400 and no `frame` is sent to the child (Task 9 test `test_live_frames_reject_a_non_sample_time`).
6. A child that prints a stray non-JSON line on stdout → `model run` exits 3, `provenance.status` is `partial` with `reason` starting `plugin_error` (Task 3 test `test_stray_stdout_is_a_protocol_violation`, Task 6 rule 10).

## Task order

Tasks 1 → 2 → 3 → 4 → 5 → 6 → 7 are a chain. Task 8 (Slurm) needs 4 and 7. Task 9 (live server) needs 4 and 5; Task 10 (page) needs 9. Task 11 (Griffin batch) needs 7 and 8; Task 12 needs 11. Task 13 needs everything that was kept. Tasks 8 and 9 can run in parallel after Task 7; Task 11's code can start after Task 7 while its acceptance waits for Task 8. If Adarsh answers open question 3 with "batch only", Tasks 9 and 10 are dropped and Task 13 documents that.

---

### Task 1: Manifest and registry

**Files:**
- Create: `coperceive/models/__init__.py` (docstring only), `coperceive/models/spec.py`, `coperceive/models/registry.py`
- Create: `tests/fake_model_plugin/__init__.py` (`MANIFEST = Path(__file__).with_name("model.yaml")`), `tests/fake_model_plugin/model.yaml` (`name: fake`, `version: "0.0.1"`, `licence: {notice: "fixture, no licence"}`, `environment: {current: true}`, one model `default` with `inputs: {modalities: [camera, lidar], frames: single}`, `outputs: {categories: [car, pedestrian], score_note: "constant 0.9", sources: {single: "fake"}}`, `resources: {gpu: false, memory_gb: 1, seconds_per_sample: 0.01}`, `options: {extra: {type: bool, default: false}, misbehave: {type: str, default: ""}}`, `commands: {batch: ["{python}", "-m", "tests.fake_model_plugin.worker", "--model", "{model}"]}`)
- Test: `tests/test_model_spec.py`

**Interfaces:**
- Consumes: `yaml.safe_load`; `coperceive.adapters.paths.Template` (for `inputs.raw`); `coperceive.errors.CoperceiveError`; `importlib.metadata.entry_points(group=...)` as `coperceive/adapters/registry.py` uses it.
- Produces (`spec.py`):
  - `SPEC_VERSION = 1`, `GROUP = "coperceive.models"`, `PLACEHOLDERS = frozenset({"python", "plugin_dir", "work_dir", "model"})`.
  - `class ModelSpecError(CoperceiveError)`.
  - Frozen dataclasses mirroring §Manifest, all `@dataclass(frozen=True)`:
    - `Licence(spdx: str | None, notice: str, citation: str | None)`
    - `Environment(kind: str, value: str | None, modules: tuple[str, ...], secrets: tuple[str, ...], inherit_ld_library_path: bool)` with `kind` in `{conda, python, current, container}`
    - `Inputs(modalities: tuple[str, ...], agents: tuple[str, ...] | None, sensors: tuple[str, ...], frames: str, raw: tuple[Template, ...], raw_agent_roots: Mapping[str, str], raw_clock: tuple[int, int] | None, other_runs: bool)`
    - `Outputs(categories: tuple[str, ...], score_note: str, sources: tuple[tuple[str, str], ...], agent: str, artifacts: tuple[str, ...], deterministic: bool)`
    - `Resources(gpu: bool, memory_gb: int, seconds_per_sample: float, staging_seconds: float, frame_timeout_s: float, run_timeout_s: float | None, grace_s: float, slurm_constraint: str | None, max_inflight: int)`
    - `Dependency(model: str, artifact: str)`
    - `OptionSpec(type: str, default: object, choices: tuple[object, ...] | None, description: str | None)`
    - `ModelSpec(key: str, description: str | None, inputs: Inputs, outputs: Outputs, resources: Resources, depends_on: tuple[Dependency, ...], options: Mapping[str, OptionSpec])`
    - `Commands(batch: tuple[str, ...], live: tuple[str, ...])`
    - `Manifest(spec_version: int, name: str, version: str, licence: Licence, environment: Environment, models: Mapping[str, ModelSpec], commands: Commands, notes: str | None, source: Path | None)`
  - `load_manifest(path: Path) -> Manifest`.
  - `parse_manifest(payload: object, *, source: Path | None) -> Manifest` — key-path errors (`models.default.colour`); cross checks: `depends_on.model` names a model, `depends_on.artifact` is in that model's `outputs.artifacts`, `{frame}` in a raw template needs `raw_clock`, `{agent_root}` needs `raw_agent_roots`, a template with `..` is refused, `spec_version` is `1`.
  - `manifest_sha256(manifest: Manifest) -> str` — over the `source` bytes; raises when `source` is None.
  - `render_argv(manifest: Manifest, command: str, *, python: Path, plugin_dir: Path, work_dir: Path, model: str) -> list[str]`.
  - `check_options(spec: ModelSpec, given: Mapping[str, str]) -> dict[str, object]` — typed, defaults filled, `choices` enforced, an unknown name is an error naming it.
  - `select_model(manifest: Manifest, name: str | None) -> ModelSpec` — the one model, or the named one; an error listing the names when ambiguous.
- Produces (`registry.py`): `@dataclass(frozen=True) class PluginInfo: name: str; source: str; location: str`; `class RegistryError(CoperceiveError)`; `list_plugins() -> tuple[PluginInfo, ...]`; `resolve_manifest(name: str | None, *, manifest: Path | None) -> Manifest`; `clear_cache() -> None`.

- [ ] **Step 1: Write the failing tests** (`tests/test_model_spec.py`): `test_fake_manifest_loads` (`name == "fake"`, `environment.kind == "current"`, `models` keys `("default",)`); `test_unknown_key_names_its_path` (`models.default.colour` → `ModelSpecError` matching `"models.default.colour"`); `test_two_environment_kinds_are_an_error` (matching `"environment"`); `test_unknown_placeholder_in_commands` (`"{shell}"` → matching `"commands.batch"`); `test_dependency_must_name_a_model` (matching `"nope"`); `test_frame_placeholder_needs_raw_clock` (matching `"raw_clock"`); `test_render_argv` (`== [sys.executable, "-m", "tests.fake_model_plugin.worker", "--model", "default"]`); `test_manifest_sha256_is_the_file_hash` (`== hashlib.sha256(FAKE.read_bytes()).hexdigest()`); `test_check_options_types_and_rejects_unknown` (`{"extra": "true"}["extra"] is True`; `{"bogus": "1"}` raises matching `"bogus"`); `test_list_plugins_from_entry_points` (with `metadata.entry_points` monkeypatched like `tests/test_adapter_cli.py::_fake_entry_points`, entry `("fake", "coperceive.models", "tests.fake_model_plugin:MANIFEST")`: one row, `source == "entry-point"`); `test_resolve_unknown_lists_known` (`RegistryError` matching `"known:"`); `test_resolve_manifest_file` (`resolve_manifest(None, manifest=FAKE).name == "fake"`).
- [ ] **Step 2: Run** `pytest tests/test_model_spec.py -q` — expected: ImportError.
- [ ] **Step 3: Implement** with the `_Node` key-path pattern of `coperceive/adapters/spec.py`.
- [ ] **Step 4: Run the test** — all pass. `ruff check`, `ruff format`, `pyright coperceive/models tests/test_model_spec.py tests/fake_model_plugin`.
- [ ] **Step 5: Commit** `feat(models): plugin manifest, validation and entry-point registry`.

### Task 2: Protocol codec

**Files:**
- Create: `coperceive/models/protocol.py`
- Test: `tests/test_model_protocol.py`

**Interfaces:**
- Consumes: `coperceive.evaluation.predictions.PredictionBox`, `PredictionError`; `coperceive.model.BoxFrame`; `coperceive.se3.SE3`.
- Produces:
  - `PROTOCOL = 1`, `MAX_LINE = 16 * 1024 * 1024`, `ERROR_CODES = frozenset({"unsupported", "configure_failed", "frame_failed", "cancelled", "bad_message", "internal"})`.
  - `class ProtocolError(CoperceiveError)` with `code: str` (`bad_message` for parse and schema failures).
  - Frozen dataclasses per §Messages:
    - `Capabilities(live: bool, cancel: bool, max_inflight: int)`
    - `Hello(plugin: str, version: str, models: tuple[str, ...], capabilities: Capabilities, environment: Mapping[str, str], git_commit: str | None)`
    - `AgentRef(agent_id: str, agent_type: str)`; `SensorJson` and `PoseJson` as `Mapping[str, object]` aliases
    - `Configure(model: str, scene_id: str, agents: tuple[AgentRef, ...], source: str, options: Mapping[str, object], work_dir: str, scene_dir: str, raw_root: str | None, artifacts: Mapping[str, Mapping[str, str]], device: str, sensors: tuple[SensorJson, ...], samples: tuple[int, ...])`
    - `Configured(scene_id: str)`
    - `ImageInput(agent_id: str, sensor_id: str, path: str)`
    - `PointsInput(agent_id: str, sensor_id: str, path: str, count: int, layout: str)`
    - `Frame(id: int, scene_id: str, time_us: int, poses: Mapping[str, PoseJson], images: tuple[ImageInput, ...], points: tuple[PointsInput, ...])`
    - `BoxRow(agent_id: str, source: str, category: str, score: float, track_id: str | None, center: tuple[float, float, float], size_lwh: tuple[float, float, float], quaternion: tuple[float, float, float, float], frame: str)`
    - `Result(id: int, boxes: tuple[BoxRow, ...], artifacts: tuple[str, ...])`
    - `Progress(id: int | None, fraction: float | None, text: str)`
    - `Error(id: int | None, code: str, message: str)`
    - `Cancel(id: int | None)`, `Bye()`
    - `Message = Hello | Configure | Configured | Frame | Result | Progress | Error | Cancel | Bye`
  - `encode(message: Message) -> bytes` — one line, `\n` terminated, no embedded newline.
  - `decode(line: bytes) -> Message` — rejects a missing or wrong `protocol`, an unknown `type`, a wrong field type, an unknown error code, a line over `MAX_LINE`.
  - `to_prediction_box(row: BoxRow, *, scene_id: str, time_us: int) -> PredictionBox` — the §`result` conversion; `PredictionError` propagates.
  - `class LineReader(stream: BinaryIO)` with `readline() -> bytes | None` enforcing `MAX_LINE` (`None` at EOF).

- [ ] **Step 1: Write the failing tests**: `test_every_message_round_trips` (parametrised over one instance per type; `decode(encode(m)) == m`); `test_missing_protocol_is_bad_message` (`decode(b'{"type":"frame"}\n')` → `ProtocolError`, `code == "bad_message"`, `"protocol"` in the text); `test_unknown_type_rejected`; `test_unknown_error_code_rejected` (`code: "boom"`); `test_long_line_rejected` (`MAX_LINE + 1` bytes); `test_box_with_non_unit_quaternion_fails` (`[1, 1, 0, 0]` → `PredictionError` matching `"unit quaternion"`); `test_box_converts` (`frame == BoxFrame.WORLD`, `size_lwh == (4.5, 1.9, 1.6)`, `scene_id` and `time_us` filled); `test_encode_has_one_trailing_newline`; `test_line_reader` (`io.BytesIO(b"a\nb\n")` yields `b"a\n"`, `b"b\n"`, `None`).
- [ ] **Step 2: Run** — ImportError.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run, lint, pyright.**
- [ ] **Step 5: Commit** `feat(models): newline-delimited JSON protocol codec`.

### Task 3: Subprocess runner, environment allow-list, cancellation

**Files:**
- Create: `coperceive/models/runner.py`, `tests/fake_model_plugin/worker.py`
- Test: `tests/test_model_runner.py`

**Interfaces:**
- Consumes: Tasks 1–2; `subprocess.Popen(argv, shell=False, stdin=PIPE, stdout=PIPE, stderr=<plugin.log>, env=..., cwd=work_dir, start_new_session=True)`; `signal.SIGTERM`, `SIGKILL`; a reader thread feeding a `queue.Queue` so `receive` can time out.
- Produces (`runner.py`):
  - `ALLOWED_ENV = ("PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR", "CUDA_VISIBLE_DEVICES", "CUDA_HOME", "NVIDIA_VISIBLE_DEVICES")`.
  - `child_environment(manifest: Manifest, *, plugin_dir: Path, work_dir: Path, parent: Mapping[str, str]) -> dict[str, str]` — §Environment of the child exactly.
  - `resolve_interpreter(env: Environment, *, plugin_dir: Path, conda_envs: Sequence[Path] | None = None) -> Path` — `conda_envs` defaults to the `$CONDA_ENVS_PATH` entries then `~/.conda/envs`; `container` raises `ModelSpecError` matching `"not implemented"`; a missing interpreter raises naming the path.
  - `redact(text: str, secrets: Mapping[str, str]) -> str` — every secret value of length ≥ 4 becomes `[redacted]`.
  - `class RunnerError(CoperceiveError)` (protocol violation, child exit) and `class RunnerTimeout(RunnerError)`.
  - `class PluginProcess`: `@classmethod start(manifest: Manifest, spec: ModelSpec, *, plugin_dir: Path, work_dir: Path, parent_env: Mapping[str, str], hello_timeout_s: float) -> PluginProcess` (starts, reads `hello`, checks §Ordering 1); attributes `hello: Hello`, `secrets: Mapping[str, str]`; `send(message: Message) -> None`; `receive(timeout_s: float) -> Message` (raises `RunnerTimeout`; a non-protocol line is a `RunnerError` and the process is closed); `cancel(frame_id: int | None) -> None`; `close(grace_s: float) -> int` (sends `bye`, waits `grace_s`, SIGTERM, waits `grace_s`, SIGKILL; returns the exit code); context manager. Every line in and out is appended to `<work_dir>/parent.log` after `redact`.
- Produces (`worker.py`, stdlib only): a conforming child for the fake manifest.
  - `hello` with `plugin: fake`, `version: 0.0.1`, `capabilities: {live: true, cancel: true, max_inflight: 1}`, `environment: {"python": sys.version.split()[0]}`; `configured` after `configure`.
  - For each `frame`, one `car` per requested agent at the agent's pose translation plus `(5, 0, 0)` in the world frame, size `(4.5, 1.9, 1.6)`, identity quaternion, score `0.9`, `track_id` null, `source: single`; plus a `pedestrian` at `+(8, 2, 0)` when option `extra` is true.
  - `misbehave` values read from `configure.options`:
    - `sleep`: 5 s per frame.
    - `no-exit`: ignore `bye`.
    - `wrong-id`: answer with `result.id + 1`.
    - `bad-agent`: a box for an agent not in `configure.agents`.
    - `stray-stdout`: print `hello world` before the result.
    - `write-outside`: create a file in `work_dir.parent`.
    - `nondeterministic`: score from `time.time()`.
    - `read-gt`: open `<scene_dir>/annotations.parquet`.
    - `secret`: put `os.environ.get("FAKE_SECRET", "")` into an error message.
    - `marker`: write `artifacts/<model>/marker/done.txt` and report it (Task 12).
  - `--count-file PATH` appends one line per frame served, so a test can count what the child saw.

- [ ] **Step 1: Write the failing tests**: `test_child_environment_allow_list` (parent `{"PATH": "/p", "HOME": "/h", "PYTHONPATH": "/x", "SECRET_X": "1", "HF_TOKEN": "t", "LD_LIBRARY_PATH": "/l"}`, manifest `secrets: [HF_TOKEN]` → `PYTHONPATH == str(plugin_dir)`, `HF_TOKEN == "t"`, no `SECRET_X`, no `LD_LIBRARY_PATH`, `PYTHONNOUSERSITE == "1"`); `test_ld_library_path_only_when_asked`; `test_resolve_conda_name` (`conda_envs=(tmp / "envs",)` → `tmp/envs/griffin/bin/python` once created; raises naming the path before); `test_container_not_implemented`; `test_start_reads_hello` (`hello.plugin == "fake"`); `test_configure_and_one_frame` (`Configured`, then `Result` with `id == 1` and one box); `test_stray_stdout_is_a_protocol_violation` (`RunnerError` matching `"protocol"`); `test_no_exit_is_killed` (`close(grace_s=0.3)` returns a negative code; `poll()` is not None); `test_frame_timeout` (`misbehave=sleep`, `receive(timeout_s=0.5)` → `RunnerTimeout`); `test_cancel_is_answered` (`Error(code="cancelled")` within 2 s); `test_secret_values_are_redacted_from_logs` (parent env `FAKE_SECRET=hunter22`, `secrets: [FAKE_SECRET]`, `misbehave=secret`: the returned `Error.message` and every line of `parent.log` contain `[redacted]` and not `hunter22`).
- [ ] **Step 2: Run** — ImportError.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run, lint, pyright** (the worker under `tests/` is strict as well).
- [ ] **Step 5: Commit** `feat(models): subprocess runner with environment allow-list, timeouts and cancellation`.

### Task 4: Frame inputs and the batch driver

**Files:**
- Create: `coperceive/models/inputs.py`, `coperceive/models/batch.py`
- Modify: `coperceive/evaluation/predictions.py` (`RunMeta.provenance: Mapping[str, object] | None = None`, written by `to_json`, read by `from_json`, must be a JSON object when present), `docs/spec/predictions.md` (one row in §`run.json`: `provenance`, object, optional, see the model-plugin spec)
- Test: `tests/test_model_batch.py`; two tests added to `tests/test_eval_predictions.py`

**Interfaces:**
- Consumes: Tasks 1–3; `coperceive.store.read_scene`, `read_points_at`; `coperceive.viewdata.scene_directories`, `pose_times`, `image_path`; `coperceive.transform.lookup_pose`; `coperceive.model.Scene`, `Sensor`, `Modality`, `PointCloud`; `coperceive.adapters.paths.confine`, `Template.expand`; `coperceive.app.jobs.confine_media` semantics (re-implemented in `inputs.py` so `coperceive/models` does not import the app); `coperceive.adapters.ingest.coperceive_version`; `write_run`, `validate_boxes`, `read_run_meta`, `check_run_id`.
- Produces (`inputs.py`):
  - `@dataclass(frozen=True) class InputFile: relative: str; absolute: Path; size: int; mtime_ns: int`.
  - `sensor_json(sensor: Sensor) -> dict[str, object]`, `pose_json(pose: SE3) -> dict[str, float]`.
  - `selected_sensors(scene: Scene, spec: ModelSpec, agent_ids: Sequence[str]) -> tuple[Sensor, ...]` — §Inputs `modalities` and `sensors` globs.
  - `extract_sweep(scene_dir: Path, cloud: PointCloud, dest: Path) -> int` — `read_points_at` to little-endian float32 `x, y, z, intensity`; returns the count.
  - `raw_files(spec: ModelSpec, *, scene_id: str, agent_id: str, agent_type: str, sensor_ids: Sequence[str], time_us: int, raw_root: Path) -> list[Path]` — `Template.expand` with `{scene, agent, agent_root, sensor, time_us, frame}`; `frame = (time_us - offset_us) // scale_us`, an error when not exact; every path through `confine(raw_root, ...)`.
  - `build_frame(scene: Scene, scene_dir: Path, spec: ModelSpec, *, frame_id: int, time_us: int, agent_ids: Sequence[str], work_dir: Path, media_roots: Sequence[Path], raw_root: Path | None) -> tuple[Frame, tuple[InputFile, ...]]` — images whose file exists, sweeps extracted to `inputs/<frame_id>/`, poses of every agent with a pose at `time_us`; the `InputFile` tuple is the cache's `inputs`.
- Produces (`batch.py`):
  - `@dataclass(frozen=True) class RunOptions: model: str | None; agent_ids: tuple[str, ...] | None; source: str; scene_ids: tuple[str, ...] | None; raw_root: Path | None; media_roots: tuple[Path, ...]; artifacts_dir: Path | None; auto_dependencies: bool; options: Mapping[str, str]; frame_timeout_s: float | None; run_timeout_s: float | None; cache: bool; hash_inputs: bool; work_dir: Path | None; force: bool`.
  - `@dataclass(frozen=True) class BatchReport: completed: tuple[str, ...]; skipped: tuple[str, ...]; failed: tuple[str, ...]; frames_run: int; cache_hits: int; boxes: int; status: str; reason: str | None`.
  - `run_meta_for(manifest: Manifest, spec: ModelSpec, *, run_id: str, created_utc: str, provenance: Mapping[str, object]) -> RunMeta` — §Provenance's `model` and `notes` text rules.
  - `provenance_json(manifest, spec, hello, *, started_utc, ended_utc, status, completed, reason, options, cache_hits, frames_run, dependencies) -> dict[str, object]` — every §Provenance key; `slurm_job_id` from the environment.
  - `run_batch(manifest: Manifest, scenes_root: Path, out_root: Path, run_id: str, options: RunOptions, *, created_utc: str, cache: FrameCache | None = None, parent_env: Mapping[str, str] | None = None) -> BatchReport`:
    - one child per run, started once, closed at the end;
    - scenes in `scene_directories` order, or `options.scene_ids` among them; a scene with `boxes.parquet` is skipped unless `force`;
    - a stored `run.json` whose `provenance.manifest_sha256` differs from the current manifest is refused unless `force`;
    - per scene: `configure`, then frames in `pose_times` order, one `frame` per sample with the selected agents (`scene.agents` order; default every agent whose type is in `inputs.agents`);
    - each `result` → `to_prediction_box` per box; a box for an agent outside the request is a `RunnerError`; then `validate_boxes(scene, meta, rows)`;
    - `write_run(out_root, run_id, meta, rows, force=options.force)` per completed scene;
    - per-scene errors go to `failed` and the run goes on; a `RunnerError` or `RunnerTimeout` on the child ends the run with `status == "partial"`;
    - `run.json` is rewritten at the end with the final provenance;
    - `options.artifacts_dir` is passed in `configure.artifacts` when the model has `depends_on`; the automatic dependency run is Task 12.

- [ ] **Step 1: Write the failing tests** on `cooperative_scene()` written to `tmp/scenes/coop` with the fake plugin: `test_batch_writes_a_complete_run` (`status == "complete"`, `completed == ("coop",)`, `frames_run == len(pose_times(scene))`; `read_run_meta` has `provenance["plugin"] == "fake"`, `provenance["status"] == "complete"`, a 64-hex `manifest_sha256`, `model == "fake default 0.0.1"`, `notes` starting with the licence notice); `test_rows_are_time_major_and_single` (`read_boxes` has one row per (time, agent) for the fixture's agents, `source == "single"`); `test_rerun_skips_a_complete_scene` (`skipped == ("coop",)`, `frames_run == 0`); `test_box_for_an_unrequested_agent_fails_the_scene` (`misbehave=bad-agent`: `failed == ("coop",)`, no `boxes.parquet`, `status == "partial"`, `reason` starts with `plugin_error`); `test_resume_refuses_a_different_manifest_without_force` (edit the manifest copy's `version`, rerun → `ModelSpecError` matching `"manifest_sha256"`; `force=True` reruns); `test_run_timeout_marks_partial` (`run_timeout_s=0.5`, `misbehave=sleep`: `status == "partial"`, `reason` starts with `timeout`); `test_build_frame_extracts_sweeps` (`points[0].layout == "xyzi_f32le"`, file size `== count * 16`); in `tests/test_eval_predictions.py`: `test_run_meta_provenance_round_trips` and `test_run_json_without_provenance_loads` (`provenance is None`).
- [ ] **Step 2: Run** — ImportError.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run, lint, pyright.**
- [ ] **Step 5: Commit** `feat(models): frame inputs and the batch driver writing a predictions run with provenance`.

### Task 5: Per-frame cache

**Files:**
- Create: `coperceive/models/cache.py`
- Modify: `coperceive/models/batch.py` (read before `frame`, write after validation)
- Test: `tests/test_model_cache.py`

**Interfaces:**
- Consumes: Task 2 `Result`, Task 4 `InputFile`; `hashlib.sha256`; `json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=True)`.
- Produces:
  - `@dataclass(frozen=True) class CacheFields: plugin: str; version: str; manifest_sha256: str; environment: Mapping[str, str]; model: str; scene_id: str; agent_ids: tuple[str, ...]; source: str; time_us: int; first_time_us: int | None; options: Mapping[str, object]; inputs: tuple[InputFile, ...]; hash_inputs: bool` with `to_json() -> dict[str, object]` (§Cache object; `sha256` per input when `hash_inputs`).
  - `cache_key(fields: CacheFields) -> str` — 64 lower-case hex characters.
  - `class FrameCache(root: Path)` (root is `<predictions root>/.cache/models`): `path_for(plugin: str, key: str) -> Path`; `get(plugin: str, key: str) -> Result | None` (a corrupt or invalid entry is deleted and `None`); `put(plugin: str, key: str, fields: CacheFields, result: Result) -> Path`; `prune(*, plugin: str | None, older_than_days: float | None) -> int`.

- [ ] **Step 1: Write the failing tests**: `test_key_is_stable` (equal fields → equal 64-hex keys); `test_key_changes_with_inputs_and_environment` (`mtime_ns`, `environment`, `first_time_us` each change it); `test_put_get_round_trip`; `test_corrupt_entry_is_dropped` (a file containing `{` → `get` is `None` and the file is gone); `test_prune_deletes` (`prune(older_than_days=0)` → 1); `test_second_batch_run_is_all_hits` (`run_batch` twice with `force=True` and a `FrameCache`: the second report has `cache_hits == first.frames_run`, `frames_run == 0`, and the fake's `--count-file` did not grow); `test_no_cache_option_sends_every_frame`; `test_cache_dir_is_not_a_run` (`list_runs(out_root)` does not list `.cache`).
- [ ] **Step 2: Run** — ImportError.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run, lint, pyright.**
- [ ] **Step 5: Commit** `feat(models): per-frame result cache under the predictions root`.

### Task 6: Conformance check

**Files:**
- Create: `coperceive/models/synthetic.py`, `coperceive/models/check.py`
- Test: `tests/test_model_check.py`

**Interfaces:**
- Consumes: Tasks 1–5; `coperceive.store.write_scene`; Pillow for one PNG per camera.
- Produces (`synthetic.py`): `synthetic_scene() -> Scene` (the shape of `tests/cooperative.py`: a vehicle and a uav, four pose times, a car and a pedestrian; cameras with `fx, fy, cx, cy`; one lidar sensor on the vehicle with a small cloud); `write_synthetic_scene(root: Path, spec: ModelSpec) -> Path` (PNGs when `camera` is a modality, `points.bin` when `lidar` is).
- Produces (`check.py`):
  - `@dataclass(frozen=True) class RuleResult: number: int; name: str; status: str; reason: str | None` with `status` in `{ok, fail, skipped, not checked}`.
  - `RULES: tuple[str, ...]` — the ten names of §Conformance.
  - `check_plugin(manifest: Manifest, *, model: str | None, work_root: Path, keep: bool, raw_root: Path | None, parent_env: Mapping[str, str]) -> tuple[RuleResult, ...]` — one pass per model (or the named one) over `write_synthetic_scene`; rule 5 by a directory listing before and after; rule 6 by `chmod 000` on `annotations.parquet` (reported `not checked` when the file stays readable); rule 8 by a second pass compared box for box, `skipped` when `outputs.deterministic` is false; rule 9 only when `hello.capabilities.cancel`.
  - `render_check(name: str, results: Sequence[RuleResult]) -> str` — the §Conformance output.

- [ ] **Step 1: Write the failing tests**: `test_fake_plugin_passes` (no `fail`; rule 6 may be `not checked`); `test_stray_stdout_fails_rule_10`; `test_write_outside_fails_rule_5`; `test_nondeterministic_fails_rule_8_unless_declared` (fails; passes as `skipped` with a manifest copy saying `deterministic: false`); `test_read_gt_fails_rule_6_when_enforced` (skip the assertion when the chmod had no effect); `test_wrong_id_fails_rule_3`; `test_render_check_counts_add_up` (ends with `check fake: N ok, M fail, K skipped`; `N + M + K + not_checked == 10`).
- [ ] **Step 2: Run** — ImportError.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run, lint, pyright.**
- [ ] **Step 5: Commit** `feat(models): conformance check over a synthetic scene`.

### Task 7: CLI

**Files:**
- Create: `coperceive/models/commands.py`
- Modify: `coperceive/cli.py` (`add_models_parser(sub)` beside `add_evaluation_parser(sub)`)
- Test: `tests/test_model_cli.py`

**Interfaces:**
- Consumes: Tasks 1–6.
- Produces: `add_parser(sub)` registering `model list`, `model inspect`, `model check`, `model run` (every flag of §CLI except `--slurm`/`--submit`, which Task 8 adds), `model cache prune`. User errors print one line to stderr and exit 1 (`_guarded` as in `coperceive/evaluation/commands.py`); exits 3 and 4 per §Exit codes from `BatchReport.status`/`reason`. `run` prints the §CLI lines. `created_utc` is `datetime.now(timezone.utc)` formatted `%Y-%m-%dT%H:%M:%SZ`.

- [ ] **Step 1: Write the failing tests** through `coperceive.cli.main` with the fake entry point monkeypatched: `test_model_list` (a line with `fake` and `entry-point`); `test_model_inspect` (prints `environment:` and the resolved interpreter path); `test_model_check_exits_zero` (prints `check fake:`); `test_run_then_eval_chain` (`model run fake --scenes tmp/scenes --out tmp/predictions --run-id r1` prints `wrote tmp/predictions/r1`; `eval run r1 --root tmp/scenes --predictions tmp/predictions` prints `evaluated 1`; `eval report r1 ...` prints a table containing `coop`); `test_existing_run_needs_force` (exit 1, `use --force` on stderr); `test_unknown_plugin_lists_known` (exit 1, `known:`); `test_bad_option_named` (exit 1 naming `bogus`); `test_timeout_exits_4` (`--option misbehave=sleep --run-timeout 0.5`; `provenance.status == "partial"`); `test_protocol_violation_exits_3` (`--option misbehave=stray-stdout`); `test_cache_prune_prints_count` (`pruned N`).
- [ ] **Step 2: Run** — failures (`model` is not a command).
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run, lint, pyright.**
- [ ] **Step 5: Commit** `feat(models): model list|inspect|check|run and cache prune commands`.

### Task 8: Slurm emitter

**Files:**
- Create: `coperceive/models/slurm.py`
- Modify: `coperceive/models/commands.py` (`--slurm`, `--submit`, `--slurm-python PATH`, `--slurm-logs DIR`, `--slurm-results DIR`)
- Test: `tests/test_model_slurm.py`

**Interfaces:**
- Consumes: Tasks 1, 4; `scripts/griffin/stage_viewer.py::staged_files` as the pattern for an `rsync --files-from` list; the `CLAUDE.md` sbatch template; `~/scripts/slurm/griffin-infer.sbatch` for the module and constraint lines.
- Produces:
  - `time_limit(seconds_per_sample: float, staging_seconds: float, samples: int) -> str` — `staging + per_sample × samples`, rounded up to the next 600 s, doubled, as `HH:MM:SS`.
  - `staged_files(scenes_root: Path, scenes: Mapping[str, Scene], spec: ModelSpec, *, raw_root: Path | None, agent_ids: Mapping[str, Sequence[str]]) -> tuple[list[str], list[str]]` — scene files to copy (every file of each scene directory except image bytes, and except `points.bin` when `lidar` is not a modality) and raw files from `raw_files` over every sample; relative, sorted, unique.
  - `@dataclass(frozen=True) class SlurmPlan: job_name: str; partition: str; gres: str | None; mem_gb: int; time: str; constraint: str | None; modules: tuple[str, ...]; scene_files: tuple[str, ...]; raw_files: tuple[str, ...]; command: tuple[str, ...]; script: str`.
  - `plan_sbatch(manifest: Manifest, spec: ModelSpec, *, scenes_root: Path, scenes: Mapping[str, Scene], run_id: str, out_root: Path, options: RunOptions, python: Path, logs_dir: Path, results_dir: Path) -> SlurmPlan` — the script of ADR 0011 decision 5, in this order:
    - `#SBATCH` lines: `--job-name=model-<plugin>-<run_id>`, `--partition`, `--gres` (GPU only), `--cpus-per-task=8`, `--mem`, `--time`, `--constraint` (when set), `--output`/`--error` under `logs_dir` as `%x-%j.{out,err}`, `--mail-type=END,FAIL`;
    - `set -euo pipefail`; `SCRATCH="/local/$SLURM_JOB_ID"`; `mkdir -p "$SCRATCH"`; `trap 'rm -rf "$SCRATCH"' EXIT`; `export TMPDIR="$SCRATCH/tmp"`;
    - `module purge` and one `module load` per entry of `environment.modules`;
    - two `rsync -a --files-from=` lines reading `.slurm/<run_id>.scenes.txt` from `scenes_root` into `$SCRATCH/scenes/` and `.slurm/<run_id>.raw.txt` from `raw_root` into `$SCRATCH/raw/` (the second only when raw files exist);
    - the `coperceive model run` command through `python` with `--scenes "$SCRATCH/scenes"`, `--raw-root "$SCRATCH/raw"` when staged, `--work "$SCRATCH/work"`, `--out "$SCRATCH/out"`, every other flag as given, never `--slurm`;
    - `mkdir -p "<results_dir>/$SLURM_JOB_ID"` and `rsync -a "$SCRATCH/out/" "<results_dir>/$SLURM_JOB_ID/"` right after the run, then `rsync -a "$SCRATCH/out/<run_id>/" "<out_root>/<run_id>/"`.
    - Values are `shlex.quote`d. The emitter reads no environment value except `$HOME` for defaults; secret names appear in one comment line (`# secrets read from the job environment: HF_TOKEN`).
  - `write_plan(plan: SlurmPlan, out_root: Path, run_id: str) -> Path` — `<out_root>/.slurm/<run_id>.sbatch` plus the two file lists.
  - `submit(path: Path) -> str` — `subprocess.run(["sbatch", str(path)], shell=False, capture_output=True, text=True, check=True)`; returns the id parsed from `Submitted batch job N`.

- [ ] **Step 1: Write the failing tests**: `test_time_limit_griffin` (`time_limit(0.39, 700, 1490) == "01:00:00"`: 1281.1 s → 1800 → 3600); `test_time_limit_small` (`time_limit(0.01, 0, 4) == "00:20:00"`); `test_gpu_script_lines` (a manifest copy with `gpu: true`, `memory_gb: 64`, `slurm_constraint: "a40|a100"`, `modules: [nvidia/cuda-11.8]` → the script contains `#SBATCH --partition=ps,main-gpu`, `#SBATCH --gres=gpu:1`, `#SBATCH --mem=64G`, `#SBATCH --constraint="a40|a100"`, `#SBATCH --mail-type=END,FAIL`, `module load nvidia/cuda-11.8`, `SCRATCH="/local/$SLURM_JOB_ID"`, `trap 'rm -rf "$SCRATCH"' EXIT`, `--files-from=`, `model run fake`, and not `--slurm`); `test_cpu_script_has_no_gres` (`ps,main-cpu`); `test_sbatch_carries_no_environment_values` (parent env `HF_TOKEN=hunter22`, manifest `secrets: [HF_TOKEN]`: `"hunter22" not in script`; `HF_TOKEN` only in a comment line); `test_staged_files_for_a_camera_model` (lists `coop/manifest.json`, `coop/annotations.parquet`, not `coop/points.bin` with `modalities: [camera]`); `test_script_passes_bash_n` (`subprocess.run(["bash", "-n", path])` returns 0; skipped without bash); `test_slurm_flag_emits_without_running` (`main([... "--slurm"])` prints the sbatch path, exit 0, the fake's `--count-file` stays empty); `test_submit_parses_job_id` (`sbatch` replaced on `PATH` by a script printing `Submitted batch job 123` → `job 123`).
- [ ] **Step 2: Run** — ImportError.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run, lint, pyright.**
- [ ] **Step 5: Commit** `feat(models): emit and submit the Slurm job for a batch run`.

### Task 9: Live session and server routes

**Files:**
- Create: `coperceive/models/live.py`
- Modify: `coperceive/evaluation/evaluate.py` (public `evaluate_frame`; `evaluate_scene` calls it per sample), `coperceive/evalview.py` (`live` flag, live rows), `coperceive/app/server.py` (`create_app(..., model: LiveModelConfig | None = None)`, the §Live session routes, live-aware `/predictions*`), `coperceive/app/serve.py` and `coperceive/cli.py` (`serve --model`, `--model-name`, `--manifest`, `--eval-config`), `docs/viewer.md` (HTTP table rows), `web/openapi.json` (regenerate from `create_app(root=..., token=None).openapi()`)
- Test: `tests/test_model_live.py` (strict), `tests/test_app.py` (routes; outside strict pyright), `tests/test_eval_run.py` (one test for `evaluate_frame`)

**Interfaces:**
- Consumes: Tasks 1–5; `match_sample`, `GtBox`, `PredBox` (`coperceive/evaluation/matching.py`); `world_box` (`coperceive/evaluation/evaluate.py`); `EvalConfig`, `load_config`; `asyncio.Queue`; `secrets.token_hex`.
- Produces (`evaluate.py`):
  - `@dataclass(frozen=True) class FrameEvaluation: rows: tuple[dict[str, object], ...]; counts: Mapping[tuple[str, str], Counts]`.
  - `evaluate_frame(scene: Scene, meta: RunMeta, boxes: Sequence[PredictionBox], config: EvalConfig, *, time_us: int) -> FrameEvaluation` — the statuses of §Matching (predictions spec) at the display threshold and operating point for that sample, rows in the `matches.parquet` column set with the world-frame box; the cooperative columns are null in a live frame (they need the whole scene).
  - `evaluate_scene` is unchanged in behaviour; its per-sample loop calls the same internal matcher `evaluate_frame` uses, and the existing tests of `tests/test_eval_run.py` stay green.
- Produces (`evalview.py`):
  - `class LiveRunView(Protocol)` with `run_id: str`, `meta: RunMeta`, `config: EvalConfig`, `frame(time_us: int) -> dict[str, object]`, `series() -> dict[str, object]`, `outputs() -> list[dict[str, object]]`.
  - `runs_for_scene(root: Path, scene_id: str, *, live: Sequence[LiveRunView] = ())` adds `"live": False` to stored runs and appends live ones with `"live": True`, `"evaluated": True`.
  - `frame_rows` and `series` take `live_lookup: Callable[[str], LiveRunView | None] | None = None` so a `live:<id>` run is answered from memory before any path code runs; a `live:` id with no session is `EvalViewError(status=404)`.
- Produces (`live.py`):
  - `@dataclass(frozen=True) class LiveModelConfig: manifest: Manifest; model: str; eval_config: EvalConfig; plugin_dir: Path; work_root: Path; media_roots: tuple[Path, ...]`.
  - `class LiveSession`:
    - `@classmethod start(config: LiveModelConfig, scene: Scene, scene_dir: Path, *, agents: Sequence[str] | None, source: str, options: Mapping[str, str], parent_env: Mapping[str, str]) -> LiveSession`
    - `session_id: str` (`secrets.token_hex(8)`), `run_id: str` (`"live:<session_id>"`), `state: str`
    - `request(times_us: Sequence[int]) -> int` — raises `ValueError` for a time not in `pose_times(scene)` before anything is sent; returns how many were queued
    - `subscribe() -> asyncio.Queue[dict[str, object]]`
    - `frame(time_us: int) -> dict[str, object]` — `EvalViewError(status=404)` when not answered yet
    - `series() -> dict[str, object]`, `outputs() -> list[dict[str, object]]`
    - `freeze(out_root: Path, run_id: str) -> BatchReport` — the Task 4 writer over the answered frames; `status == "partial"` when samples remain
    - `stop(grace_s: float) -> int`
  - The child runs on one worker thread; results are matched with `evaluate_frame` on arrival and pushed to every subscriber.
- Produces (`server.py`): the §Live session table; `app.state.live: LiveSession | None`; 404 for every live route when `model` is None.

- [ ] **Step 1: Write the failing tests**: in `tests/test_eval_run.py`, `test_evaluate_frame_matches_the_scene_sample` (`cooperative_scene()` + `synthetic_predictions()` at 100000 µs: the vehicle `single` counts equal `evaluate_scene`'s sample row); in `tests/test_model_live.py`: `test_session_streams_matched_rows` (`start`, `request([100000])`, the queue yields a `frame` event whose rows have `status` in `{tp, fp, miss}` and `counts` with `fp >= 1`, the fake's car at +5 m being a false positive against the fixture); `test_live_frames_reject_a_non_sample_time` (`request([123])` → `ValueError`; the fake's `--count-file` is unchanged); `test_freeze_writes_an_evaluable_run` (`evaluate_run` on it has `len(evaluated) == 1`); `test_stop_returns_zero`; in `tests/test_app.py`: `test_live_routes_404_without_model`; `test_live_session_lifecycle` (`POST /live` 200 with `run_id` starting `live:`; `GET /predictions` lists it with `live: true` and a stored run with `live: false`; `POST .../frames` with `{"times_us": [100000]}` 200; the SSE body read through `client.stream` contains `event: frame`; a second `POST /live` is 409; `DELETE` 200 and the run disappears); `test_live_post_needs_csrf_header` (403).
- [ ] **Step 2: Run** — failures.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run, lint, pyright** (`tests/test_model_live.py` is strict: no inline `TestClient` calls there, per the HANDOVER rule).
- [ ] **Step 5: Commit** `feat(app): live model sessions over SSE, frame matching and freeze into a run`.

### Task 10: Viewer live run

**Files:**
- Create: `web/src/viewer/live.ts` (`subscribeLive(sceneId: string, sessionId: string, token: string | null, onEvent: (event: LiveEvent) => void): () => void`, the `fetch` + `ReadableStream` reader of `web/src/App.tsx`, parsing `event:`/`data:` pairs), `web/src/viewer/liveTypes.ts` (`LiveSessionInfo`, `LiveEvent`, guards `isLiveSessionList`, `isLiveEvent`)
- Modify: `web/src/viewer/predictionTypes.ts` (`RunInfo.live: boolean`; `isRunList` accepts it), `web/src/viewer/predictions.tsx` (a `Live` tag on a live run; a Live row with Start, Stop and Freeze buttons and a run-id field, shown only when `GET /api/scenes/{id}/live` is not 404), `web/src/viewer/Viewer.tsx` (fetch `/live` with `/predictions`; on a `frame` event for the current sample re-request `/predictions/live:<s>/frame`; `POST /live/{s}/frames` with the current and next four samples on scrub; Freeze posts and refreshes the run list), `web/src/index.css` (the tag), `tests/test_viewer_browser.py`
- No new persistent state.

- [ ] **Step 1:** types and guards; `tsc -b` from `web/` where node is available.
- [ ] **Step 2:** `live.ts` and the block controls; the Live row uses `apiBase()`, `authHeaders()` and the CSRF header exactly as the export dialog does.
- [ ] **Step 3:** wire `Viewer.tsx`; the live run draws through the existing `buildPredictionObjects` path with no new colours.
- [ ] **Step 4:** browser test `test_live_run_draws_and_freezes`: server started with `--model fake --manifest tests/fake_model_plugin/model.yaml`; click Start; within 10 s the status chips show `FP 1` or more at the first sample; Freeze with run id `frozen`; the run list gains `frozen`. `oxlint` clean.
- [ ] **Step 5:** `npm run build` and commit `coperceive/app/static` as `build(web): rebuild static bundle` (the rule since 2026-10-17, HANDOVER.md "Static bundle").
- [ ] **Step 6: Commit** `feat(web): live model run in the Predictions block`.

### Task 11: Griffin plugin, batch, single-agent models

**Files:**
- Create: `plugins/griffin/pyproject.toml` (name `coperceive-griffin-model`, `requires-python = ">=3.8"`, no dependencies, `[project.entry-points."coperceive.models"] griffin = "coperceive_griffin_model:MANIFEST"`), `plugins/griffin/README.md`, `plugins/griffin/coperceive_griffin_model/__init__.py`, `model.yaml`, `worker.py`, `convert.py`, `toolkit.py`, `plugins/griffin/tests/test_convert.py`, `plugins/griffin/tests/fixtures/results_nusc_det.json` (three boxes, two samples, one scene; synthetic numbers), `plugins/griffin/tests/fixtures/{sample,scene}.json`
- Create: `tests/test_griffin_model_manifest.py`, `tests/test_griffin_model_syntax.py`
- Modify: `pyproject.toml` (`[tool.pyright] exclude` gains `plugins`; ruff `src` gains `plugins`), the CI workflow (pytest also runs `plugins/griffin/tests` under the matrix interpreters)

**Interfaces:**
- Consumes: the rules and functions of `scripts/griffin/griffin_to_predictions.py` (`scene_id_of`, `frame_id_of`, `read_samples`, `unit_rotation`, `convert_boxes`, `BASE_US`, `STEP_US`, `QUATERNION_SLACK`, `DEFAULT_CATEGORIES`), ported to Python 3.8 without importing coperceive; the staging rules of `scripts/griffin/scene_infos_val.py`; the steps of `~/scripts/slurm/griffin-infer.sbatch`.
- Produces (`model.yaml`):
  - `name: griffin`, `version: "0.1.0"`.
  - `licence: {spdx: MIT, notice: "Copyright (c) [2025] [Jiahao Wang] (MIT License, LICENSE.txt of wang-jh18-SVM/Griffin; checkpoints from HF wjh-svm/Griffin, license: mit)", citation: "Wang et al., Griffin: Aerial-Ground Cooperative Detection and Tracking Dataset and Benchmark, arXiv:2503.06983"}` — the texts of the job 627093 `run.json`.
  - `environment: {conda: griffin, modules: [nvidia/cuda-11.8, nvidia/cuda-11.x_cudnn-8.6], inherit_ld_library_path: true}` — the modules of `~/scripts/slurm/griffin-infer.sbatch`.
  - model `vehicle-side`:
    - `inputs`: `modalities: [camera]`, `agents: [vehicle]`, `frames: sequence`; raw templates for both sides' `scene_infos.json`, `{agent_root}/calib/{sensor}.json`, `{agent_root}/pose/{frame:06d}.json`, `{agent_root}/label/{frame:06d}.txt`, `vehicle-side/camera/{sensor}/{frame:06d}.png`; `raw_agent_roots: {vehicle: vehicle-side, drone: drone-side}`; `raw_clock: {scale_us: 100000, offset_us: 0}`.
    - `outputs`: `categories: [car, bicycle, pedestrian]`, `score_note: <the job 627093 text>`, `sources: {single: "vehicle-side camera-only Agile model, no fusion"}`, `artifacts: [det_pkl, det_json]`.
    - `resources`: `gpu: true`, `memory_gb: 64`, `seconds_per_sample: 0.39`, `staging_seconds: 700`, `frame_timeout_s: 3600`, `slurm_constraint: "a40|a100"`.
    - `options`: `toolkit_dir` (str, default `~/src/griffin`), `ckpt_root` (str, default `/datasets/eemcs/ps/cv/huggingface/griffin/ckpts`), `subset` (str, default `25m`, choices `25m, 40m, 55m, random`), `min_score` (float, default `0.05`), `det_json` (str, default empty).
  - model `drone-side`: `agents: [uav]`, the five drone cameras, config `drone-side/tiny_track_r50_stream_bs8_24epoch_3cls_eval.py` so `save_track_query = True`, `artifacts: [det_pkl, det_json, track_query]`; otherwise as `vehicle-side`.
- Produces (`worker.py`, Python 3.8): the protocol loop over stdin/stdout. On `configure`:
  - when `det_json` is set, read it and answer frames from it (no GPU; `model check` and the fixture test use this);
  - otherwise copy the toolkit tree to `<work_dir>/griffin` without `.git` and `work_dirs`, write the trimmed `scene_infos.json` and a renumbered split for the requested scenes (the rules of `scripts/griffin/scene_infos_val.py`), run `tools/griffin_data_converter/trans_kitti2nuscenes.py`, `generate_nuscenes_pkl.py` and `tools/test.py` with the model's config and checkpoint as argv lists (`toolkit.py`), check the checkpoint md5 when the toolkit documents one (`docs/Training_and_Evaluation.md` gives the vehicle-side value), then answer each `frame` from `results_nusc_det.json`;
  - `progress` between steps; `artifacts` under `artifacts/<model>/`.
- Produces (`convert.py`, Python 3.8): `scene_id_of`, `frame_id_of`, `read_samples`, `unit_rotation`, `boxes_for_sample(det: dict, samples: dict, token: str, *, agent_id: str, source: str, min_score: float) -> list` producing §`result` box objects: `size_lwh = (size[1], size[0], size[2])`, normalised quaternion, `frame: world`.

- [ ] **Step 1: Write the failing tests**: `plugins/griffin/tests/test_convert.py` (runs under coperceive's interpreter, and under 3.8 when present): `test_wlh_becomes_lwh` (`size [1.9, 4.5, 1.6]` → `size_lwh [4.5, 1.9, 1.6]`); `test_quaternion_slack` (norm `1.0005` normalised; norm `1.1` raises); `test_scene_id_of` (`"scene-0000-Town03-000"` → `"Town03-000"`); `test_frame_id_of` (`BASE_US + 3 * STEP_US` → `3`); `test_min_score_drops` (`detection_score 0.01` dropped at `0.05`); `tests/test_griffin_model_manifest.py`: `test_griffin_manifest_loads` (models `vehicle-side` and `drone-side`; `environment.kind == "conda"`; `licence.spdx == "MIT"`; `"arXiv:2503.06983" in licence.citation`); `tests/test_griffin_model_syntax.py`: `test_worker_is_python_38` (every `.py` under `plugins/griffin/coperceive_griffin_model` parses with `feature_version=(3, 8)`); `tests/test_griffin_model_check.py`: `test_check_with_det_json_fixture` (`check_plugin` with `--option det_json=<fixture>` and a raw root from `tests/griffin_release.py::write_release`: no `fail`).
- [ ] **Step 2: Run** — failures.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run; `ruff check plugins`; pyright unchanged (excluded).**
- [ ] **Step 5: Acceptance on the HPC (not CI):** `pip install -e plugins/griffin` into the login-node `.venv`; `coperceive model run griffin --model vehicle-side --scenes <staged val scenes> --raw-root <release> --out ~/results/stepb/predictions --run-id griffin25m-vehicle-plugin --slurm`; show the emitted sbatch to Adarsh; `--submit` after approval; compare with job 627093's `boxes.parquet` per scene using `pa.Table.equals(check_metadata=False)` (same checkpoint, same converter rules, so identical is expected and any difference is a finding); then `eval run` and `eval compare griffin25m-vehicle-agile-iter33024 griffin25m-vehicle-plugin` (expect `+0.000` throughout). Then the drone-side run. Record wall time, staging time and inference time in HANDOVER beside job 627093's 23 min 36 s, 11 min 17 s and 9 min 37 s.
- [ ] **Step 6: Scrambled-label check (HPC, one scene):** rerun vehicle-side on one scene with every staged `label/*.txt` replaced by one unrelated line; the boxes must equal the normal run's. Record the outcome; if they differ, stop and open a question for Adarsh before Task 12.
- [ ] **Step 7: Commit** `feat(plugins): Griffin model plugin with vehicle-side and drone-side batch models`.

### Task 12: Griffin plugin, fusion models and dependencies

**Files:**
- Modify: `coperceive/models/batch.py` (`resolve_dependencies(manifest: Manifest, spec: ModelSpec, *, artifacts_dir: Path | None, auto: bool, out_root: Path, run_id: str, run_dependency: Callable[[ModelSpec, Path], BatchReport]) -> Mapping[str, Mapping[str, Path]]`: for each `depends_on`, use `artifacts_dir/<model>/<artifact>` when given, else run the dependency model first into `<out_root>/.work/<run_id>/artifacts/` when `auto`, else raise naming the model), `plugins/griffin/coperceive_griffin_model/model.yaml` and `worker.py`
- Create: `tests/fake_model_plugin/model-two.yaml` (model `a` with `outputs.artifacts: [marker]`; model `b` with `depends_on: [{model: a, artifact: marker}]`)
- Test: `tests/test_model_batch.py` additions, `plugins/griffin/tests/test_convert.py` additions

**Interfaces:**
- `model.yaml` gains `early-fusion` (agents `[vehicle, uav]`; `num_cams = 9`, both sides' cameras; the toolkit converter runs with `--early_fusion`; `outputs: {sources: {fused: "early fusion of vehicle and drone cameras"}, agent: vehicle}`), `instance-fusion` (vehicle cameras; `depends_on: [{model: drone-side, artifact: track_query}]`; the worker places the artefact where the config's `read_track_query_file_root` points, under `<work_dir>/griffin/data/infos/<prefix>/drone-side/track_query/`; `sources: {fused: "instance fusion of vehicle queries with drone track queries"}`, `agent: vehicle`) and, optional, `late-fusion` (`depends_on` both `det_pkl`s; `tools/result_converter/det_result_late_fusion.py --veh-det-pkl … --inf-det-pkl … --out …`; `sources: {fused: "result-level late fusion"}`).
- The worker's `configure.artifacts` handling, and the converted `fused` rows attributed to `agent_id = vehicle`, `source = fused`, as ADR 0010 decision 3 requires.

- [ ] **Step 1: Write the failing tests**: `test_dependency_missing_without_auto` (`model-two.yaml`, model `b`, `auto_dependencies=False`, no `artifacts_dir` → `ModelSpecError` naming `a`); `test_dependency_runs_first` (`auto_dependencies=True`: `a` runs with `misbehave=marker`, `b`'s `configure.artifacts["a"]["marker"]` names an existing directory holding `done.txt`; `b`'s `run.json` has `provenance["dependencies"] == {"a": {"marker": "<path>"}}`); `test_dependency_from_artifacts_dir` (no `a` run; the directory is given); `test_manifest_rejects_undeclared_artifact` (load error); `test_griffin_manifest_fusion_models` (four or five models; `instance-fusion.depends_on == (Dependency("drone-side", "track_query"),)`); `test_fused_rows_belong_to_the_vehicle` (`agent_id == "vehicle"`, `source == "fused"`).
- [ ] **Step 2: Run** — failures.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run, lint, pyright.**
- [ ] **Step 5: Acceptance on the HPC:** early-fusion run, then instance-fusion with the drone-side artefacts of Task 11 through `--artifacts`, each as an emitted and approved sbatch; `eval run` with the Griffin `gt_class_map` config of `docs/eval.md`; `eval compare` single versus fused; open the viewer in an allocation and check that the `Recovered` and `Lost by fusion` chips show counts. Record the numbers in HANDOVER as measured; no comparison with the paper unless a table is quoted.
- [ ] **Step 6: Commit** `feat(plugins): Griffin early-fusion and instance-fusion models with artefact dependencies`.

### Task 13: Docs, handover

**Files:**
- Create: `docs/models.md` (user doc: what a plugin is, `model list|inspect|check|run`, the Slurm flow with the approval step, live mode and the tunnel, the Griffin plugin's install line and its models, what was checked), `tests/test_model_end_to_end.py`
- Modify: `README.md` ("Run a model" after "Evaluate"), `CHANGELOG.md`, `mkdocs.yml` (nav: `Run a model: models.md`), `docs/adr/0011-live-model-plugin.md` ("As built", status), `docs/eval.md` (a paragraph pointing at `models.md`), `docs/viewer.md` (the Live row), `HANDOVER.md` (state, decisions in code, open questions, gates, acceptance numbers)

- [ ] **Step 1: Write the end-to-end test**: fixture scene → `model run fake` → `eval run` → `TestClient` `/predictions` shows the run with `live: false` and `provenance` inside `run` → `model run fake --slurm` writes an sbatch that `bash -n` accepts → `LiveSession` start, request, freeze → `eval compare <batch run> <frozen run>` prints `+0.000` for the shared rows (the fake is deterministic).
- [ ] **Step 2: Run** — passes once Tasks 1–9 are merged.
- [ ] **Step 3:** write the docs; `mkdocs build` in the gates job.
- [ ] **Step 4: Commit** `docs: model plugins user doc, changelog, handover`.

---

## Gates

After every task, on the HPC: `sbatch --export=ALL,REPO=$HOME/<worktree> ~/scripts/slurm/coperceive-gates.sbatch` (`ps,main-cpu`; ruff check and format, pyright, pytest; the job prints its pyright, fastapi, starlette and httpx versions). Expected: ruff clean, pyright 0 errors over the whole repository, every test green with the two Griffin-raw skips. `tests/test_viewer_browser.py` needs `COPERCEIVE_WEB_DIST` set to a fresh `vite build` directory. Nothing in these gates touches a GPU; the Griffin acceptance steps are separate approved jobs.

## Self-review notes

- Spec coverage: §Discovery and §Manifest → Task 1; §Protocol → Task 2 (codec) and 3 (ordering, violations); §Environment of the child → Task 3; §Working directory → Tasks 3–4; §Provenance → Task 4; §Cache → Task 5; §Conformance → Task 6; §CLI and §Exit codes → Tasks 7–8; §Live session → Task 9 (server) and 10 (page); ADR decision 9 → Tasks 11–12.
- Names used across tasks: `Manifest`, `ModelSpec`, `Environment`, `Inputs`, `Outputs`, `Resources`, `Dependency`, `OptionSpec`, `load_manifest`, `manifest_sha256`, `render_argv`, `check_options`, `select_model`, `resolve_manifest`, `list_plugins`, `Hello`, `Configure`, `Configured`, `Frame`, `Result`, `BoxRow`, `Progress`, `Error`, `Cancel`, `Bye`, `encode`, `decode`, `to_prediction_box`, `LineReader`, `child_environment`, `resolve_interpreter`, `redact`, `PluginProcess`, `RunnerError`, `RunnerTimeout`, `InputFile`, `build_frame`, `extract_sweep`, `raw_files`, `selected_sensors`, `RunOptions`, `BatchReport`, `run_batch`, `run_meta_for`, `provenance_json`, `resolve_dependencies`, `CacheFields`, `cache_key`, `FrameCache`, `RuleResult`, `check_plugin`, `render_check`, `synthetic_scene`, `write_synthetic_scene`, `time_limit`, `staged_files`, `SlurmPlan`, `plan_sbatch`, `write_plan`, `submit`, `FrameEvaluation`, `evaluate_frame`, `LiveModelConfig`, `LiveSession`, `LiveRunView` — each defined once above.
- Review Focus items 1–6 are pinned to Tasks 4, 3, 4, 8, 9 and 3/6.
- Measured numbers used here: job 627093's 23 min 36 s wall, 11 min 17 s staging, 9 min 37 s inference for 1,490 samples (0.39 s per sample), the 17.0 GB of val camera PNGs, and the `time_limit` arithmetic. Every other duration or size is to be measured by the acceptance steps and written into HANDOVER.
