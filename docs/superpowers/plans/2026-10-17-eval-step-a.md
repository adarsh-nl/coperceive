# Evaluation engine, step A — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Import model predictions next to schema 1 scenes, match them to ground truth with written rules, write a match table and a metrics file, and show TP/FP/miss, per-frame metrics, scene AP/recall, two-run comparison and the cooperative views in the viewer and the bird's-eye figure.

**Architecture:** A new package `coperceive/evaluation/` (pure Python: format, geometry, config, matching, metrics, run driver, CLI) writes `matches.parquet` and `metrics.json` under a predictions sidecar root. `coperceive/evalview.py` turns those files into frame/series JSON without importing FastAPI. `coperceive/app/server.py` adds three GET routes and a `--predictions` root; `plot.py` draws prediction rows on the bird's-eye figure; the React page adds a Predictions block, a status layer, timeline sparklines and inspector fields. Schema 1 is not touched.

**Tech Stack:** Python 3.10–3.13, PyArrow, NumPy, PyYAML, FastAPI/pydantic (app only), matplotlib (figures), React + TypeScript + three.js (page). Strict pyright on everything outside `coperceive/app` and the excluded tests.

**Spec:** `docs/spec/predictions.md` (normative), `docs/adr/0010-eval-predictions.md` (decisions). Section names below (§Layout, §Geometry, …) are headings of the spec.

## Global Constraints

- Schema 1 scenes are read, never written. No new column, key or file in a scene directory.
- Every path under the predictions root goes through a `confine`-style check: `resolve()` then `is_relative_to(root)`. `run_id` and `scene_id` obey the scene-id rule (non-empty, not `.`/`..`, no `/` or `\`, no leading dot).
- No shell, no `subprocess` with `shell=True`. Tokens never appear in code, logs, fixtures or docs.
- The page keeps the bearer token in `sessionStorage` under `coperceive-token`; prediction settings go nowhere persistent except the view settings object `coperceive-view` (score threshold only, as `predictionScore`), which must not contain the token.
- `#DE3B21` is only the playhead and Play. Status colours and line styles are exactly §Colours and styles.
- Python: `from __future__ import annotations`, `# SPDX-License-Identifier: Apache-2.0` header, ruff clean at line length 100, strict pyright clean (numpy member types may carry `# pyright: ignore[reportUnknownMemberType]`), `zip(..., strict=True)`.
- Tests: pytest, TDD (write the test, watch it fail, then implement). Fixtures are synthetic; no dataset bytes. The cooperative fixture is `tests/cooperative.py::cooperative_scene()` (agents `vehicle`, `uav`, `mast`; vehicle poses at 0, 100000, 200000, 300000 µs; most boxes at 100000 µs; track `42` is a shared car, `99` vehicle-only, `5` is car on the vehicle and truck on the uav, an untracked `sign` on the uav, track `7` a pole on the mast at 0 µs).
- HPC login node: no command over a minute, single core. `npm` is not run on the HPC; the web build is CI's.
- Conventional Commits; push after every task; the branch is `feat/eval-step-a` off `docs/adr-0010-eval-step-a`.

## Review Focus

Inputs the spec implies but no single task's happy path exercises, most likely to bite first:

1. A prediction whose `time_us` is a pose time of a *different* agent (a drone time on the vehicle) → `PredictionError` naming the row, nothing written (Task 3 test `test_validate_rejects_a_time_that_is_not_a_pose_of_that_agent`).
2. Two predictions with identical `score` → the earlier file row is matched first, every run of the matcher gives the same `matches.parquet` (Task 4 test `test_equal_scores_keep_file_order`).
3. A category present in `run.json` `categories` with ground truth but zero predictions → AP `0.0` and a sample row with `miss > 0`, no `NaN` anywhere in `metrics.json` (Task 6 test `test_metrics_json_has_no_nan_and_a_zero_ap_for_an_unpredicted_class`).
4. A `run_id` with `..` or a slash in the HTTP path → 400 or 404, no file read outside the predictions root (Task 9 test `test_prediction_routes_refuse_traversal`).
5. A run with `boxes.parquet` but no `metrics.json` (imported, not evaluated) → `/predictions` lists it with `evaluated: false`; `/frame` and `/series` answer 404 with a detail that says to run `coperceive eval run` (Task 8 test `test_unevaluated_run_is_listed_but_has_no_frame`).

---

### Task 1: Geometry

**Files:**
- Create: `coperceive/evaluation/__init__.py` (docstring only), `coperceive/evaluation/geometry.py`
- Test: `tests/test_eval_geometry.py`

**Interfaces:**
- Consumes: `coperceive.se3.SE3`, `coperceive.validate.yaw_deg(pose: SE3) -> float`.
- Produces:
  - `@dataclass(frozen=True) class WorldBox: center: tuple[float, float, float]; size_lwh: tuple[float, float, float]; rotation: SE3` (a box already in the world frame; `rotation` is a pure rotation).
  - `footprint(box: WorldBox) -> tuple[tuple[float, float], ...]` — four XY corners, counter-clockwise, from §Geometry.
  - `polygon_area(points: Sequence[tuple[float, float]]) -> float` — shoelace, absolute.
  - `clip_convex(subject, clip) -> tuple[tuple[float, float], ...]` — Sutherland–Hodgman.
  - `bev_iou(a: WorldBox, b: WorldBox) -> float`
  - `centre_distance_m(a: WorldBox, b: WorldBox) -> float`
  - `yaw_error_deg(a: WorldBox, b: WorldBox) -> float` — in `[0, 180]`.
  - `horizontal_range_m(box: WorldBox, origin: tuple[float, float, float]) -> float`

- [ ] **Step 1: Write the failing tests** — one test per normative value in §Geometry: identical boxes `== 1.0`; shift by 2 along length `== pytest.approx(1/3)`; 4×2 vs 2×4 same centre `approx(1/3)`; yaw 0 vs yaw 90° `approx(1/3)`; touching edge `== 0.0`; the 1×1 inside case `approx(0.125)`; `centre_distance_m` of `(0,0,0)` and `(3,4,10)` `== 5.0`; `yaw_error_deg` for 10°/−10° `approx(20)` and 170°/−170° `approx(20)`; plus `polygon_area` of the unit square `== 1.0` and `clip_convex` of two unit squares offset by 0.5 has area `0.25`.
- [ ] **Step 2: Run** `pytest tests/test_eval_geometry.py -q` — expected: ImportError.
- [ ] **Step 3: Implement** the functions above. Yaw of a `WorldBox` is `math.radians(yaw_deg(box.rotation))`. Corners are `(±l/2, ±w/2)` rotated by yaw and translated.
- [ ] **Step 4: Run the test** — expected: all pass. Run `ruff check`, `ruff format`, `pyright coperceive/evaluation tests/test_eval_geometry.py`.
- [ ] **Step 5: Commit** `feat(evaluation): BEV IoU, centre distance and yaw error geometry`.

### Task 2: Evaluation config

**Files:**
- Create: `coperceive/evaluation/config.py`
- Test: `tests/test_eval_config.py`

**Interfaces:**
- Consumes: `yaml.safe_load`.
- Produces:
  - `@dataclass(frozen=True) class EvalConfig: criterion: str; thresholds: tuple[float, ...]; display_threshold: float; class_thresholds: Mapping[str, tuple[float, ...]]; ignore_gt_visibility_below: float | None; max_range_m: float | None; score_threshold: float`
  - `DEFAULT_CONFIG: EvalConfig` with the §Evaluation config defaults.
  - `load_config(path: Path | None) -> EvalConfig` — `None` returns the default.
  - `parse_config(payload: object) -> EvalConfig` — validation with key-path messages (`ConfigError`, subclass of `CoperceiveError`).
  - `config_json(config: EvalConfig) -> dict[str, object]` — the exact object written into `metrics.json`.
  - `threshold_for(config: EvalConfig, category: str, index: int) -> float`
  - `display_index(config: EvalConfig) -> int`
  - `threshold_key(value: float) -> str` — `repr(float(value))` (`"0.5"`, `"2.0"`).

- [ ] **Step 1: Write the failing tests**: defaults equal the spec values; an unknown key raises `ConfigError` matching `"colour"`; `display_threshold: 0.6` raises matching `"display_threshold"`; `class_thresholds: {pedestrian: [0.25]}` (wrong length) raises matching `"pedestrian"`; `criterion: centre` with `thresholds: [2.0, 1.0]` loads and `threshold_for(cfg, "car", 1) == 1.0`; `criterion: iou_bev` with `thresholds: [0.7, 0.5]` raises (not increasing); `config_json(DEFAULT_CONFIG)` round-trips through `parse_config`.
- [ ] **Step 2: Run** — ImportError. **Step 3: Implement.** **Step 4: Run, lint, pyright.** **Step 5: Commit** `feat(evaluation): validated evaluation config`.

### Task 3: Predictions format

**Files:**
- Create: `coperceive/evaluation/predictions.py`
- Test: `tests/test_eval_predictions.py`, helper `tests/eval_fixture.py`

**Interfaces:**
- Consumes: `coperceive.store` Parquet helpers pattern (`pa.Table.from_pylist`, `pq.write_table`), `coperceive.transform.lookup_pose`, `coperceive.model.Scene`, `BoxFrame`, `SE3`, `coperceive.adapters.paths.confine`.
- Produces:
  - `FORMAT = "coperceive-predictions"`, `FORMAT_VERSION = 1`, `BOX_SCHEMA: pa.Schema` (§`boxes.parquet` columns, nullability as listed).
  - `class PredictionError(CoperceiveError)`.
  - `@dataclass(frozen=True) class RunMeta: run_id: str; model: str; score_note: str; categories: tuple[str, ...]; sources: tuple[tuple[str, str], ...]; created_utc: str; notes: str | None = None` with `to_json()` / `from_json(payload, *, directory_name)`.
  - `@dataclass(frozen=True) class PredictionBox: scene_id: str | None; agent_id: str; source: str; time_us: int; category: str; score: float; track_id: str | None; center: tuple[float, float, float]; size_lwh: tuple[float, float, float]; box_from_rpy: SE3; frame: BoxFrame` (validation in `__post_init__`: finite score, positive sizes, unit quaternion within 1e-6, pure rotation).
  - `check_run_id(value: str) -> str`
  - `predictions_root(scenes_root: Path, override: Path | None) -> Path | None` — the §Layout default rule.
  - `write_run(root: Path, run_id: str, meta: RunMeta, rows: Iterable[PredictionBox], *, force: bool = False) -> tuple[str, ...]` — returns scene ids written; raises if a scene has `boxes.parquet` and not `force`; writes `run.json`.
  - `read_run_meta(root: Path, run_id: str) -> RunMeta`
  - `list_runs(root: Path) -> tuple[str, ...]`
  - `read_boxes(root: Path, run_id: str, scene_id: str) -> tuple[PredictionBox, ...]` (file order kept; `scene_id` filled in).
  - `validate_boxes(scene: Scene, meta: RunMeta, boxes: Sequence[PredictionBox]) -> None` — agent exists, source in meta, `time_us` is a pose time of that agent, category non-empty; error text `"row {i}: ..."`.
  - `load_rows(path: Path) -> tuple[PredictionBox, ...]` — `.jsonl` or `.parquet`; `scene_id` optional.
  - `tests/eval_fixture.py::synthetic_predictions(scene: Scene) -> tuple[RunMeta, tuple[PredictionBox, ...]]`: for every vehicle and uav GT box at every time, a `single` prediction of the same category, centre `+0.1` m along world x, score `0.9`, **except** track `99` (left out, a miss) and track `5` on the vehicle (predicted as `truck`, a wrong class); one extra `single` car at `(50, 50, 0)` size `(4, 2, 1.5)` score `0.8` per vehicle time (an FP); a `fused` output on the vehicle with every vehicle GT box at `+0.1` m score `0.95` (recovers `99`, still mislabels `5`). Boxes in the `body` frame of their agent. `RunMeta(run_id="synthetic", model="fixture", score_note="constant", categories=("car","truck","pedestrian","sign","pole"), sources=(("single","own sensors"),("fused","cooperative")), created_utc="2026-10-17T00:00:00Z")`.

- [ ] **Step 1: Write the failing tests**: `write_run` + `read_run_meta` + `read_boxes` round-trip the fixture (same tuples); `write_run` refuses a second write without `force`; a `run_id` of `"../x"` raises; `validate_boxes` passes on the fixture; rejects an unknown agent (`"row 0"` in the message), an unknown source, a time that belongs to the uav but is given to the vehicle (Review Focus 1), a non-unit quaternion; `load_rows` reads a JSONL file with three rows (one with `scene_id`) and a Parquet file written by `pq.write_table`; `predictions_root(tmp, None)` is `None` until `tmp/predictions/<run>/run.json` exists, and the override wins.
- [ ] **Step 2: Run** — ImportError. **Step 3: Implement.** **Step 4: Run, lint, pyright.** **Step 5: Commit** `feat(evaluation): predictions format, run metadata and import rows`.

### Task 4: Matching

**Files:**
- Create: `coperceive/evaluation/matching.py`
- Test: `tests/test_eval_matching.py`

**Interfaces:**
- Consumes: Task 1 (`WorldBox`, `bev_iou`, `centre_distance_m`, `yaw_error_deg`), Task 2 (`EvalConfig`, `threshold_for`).
- Produces:
  - `@dataclass(frozen=True) class GtBox: row: int; category: str; track_id: str | None; visibility: float | None; world: WorldBox; range_m: float`
  - `@dataclass(frozen=True) class PredBox: row: int; category: str; score: float; track_id: str | None; world: WorldBox; range_m: float`
  - `@dataclass(frozen=True) class Pairing: pred_row: int; gt_row: int; iou_bev: float; centre_error_m: float; yaw_error_deg: float`
  - `@dataclass(frozen=True) class MatchResult: pred_status: Mapping[int, str]; gt_status: Mapping[int, str]; pairs: tuple[Pairing, ...]` — statuses are the §Matching words.
  - `match_sample(gt: Sequence[GtBox], preds: Sequence[PredBox], config: EvalConfig, *, threshold_index: int, apply_score_threshold: bool) -> MatchResult` — rules 1–6 of §Matching exactly; `apply_score_threshold=False` is the AP pass (rule 7).

- [ ] **Step 1: Write the failing tests** — the five normative cases of §Matching, each as its own test, with `criterion: centre, thresholds: [2.0, 1.0]`; the same five under `iou_bev` with boxes that overlap by construction (identical box → tp at 0.5 and 0.7; a 4×2 box shifted 2 m → IoU 1/3 → fp at 0.5); `test_equal_scores_keep_file_order` (two predictions with score 0.9 on one GT: the first row is the tp); `max_range_m` ignores a GT and a prediction at 60 m; the AP pass ignores `score_threshold`.
- [ ] **Step 2: Run** — ImportError. **Step 3: Implement** with a stable sort `sorted(preds, key=lambda p: -p.score)`. **Step 4: Run, lint, pyright.** **Step 5: Commit** `feat(evaluation): greedy per-class matching with ignore rules`.

### Task 5: Metrics

**Files:**
- Create: `coperceive/evaluation/metrics.py`
- Test: `tests/test_eval_metrics.py`

**Interfaces:**
- Consumes: Task 4 types.
- Produces:
  - `average_precision(records: Sequence[tuple[float, str]], n_gt: int) -> float | None` — `records` are `(score, status)` in file order; the function sorts by score descending (stable) and applies §Metrics AP steps 1–5; `None` when `n_gt == 0`.
  - `@dataclass(frozen=True) class Counts: tp: int; fp: int; miss: int; ignored_gt: int` with `precision` and `recall` properties returning `float | None`.
  - `counts_of(result: MatchResult) -> Counts`
  - `tp_errors(pairs: Sequence[Pairing], sizes: Mapping[int, tuple[tuple[float, float, float], tuple[float, float, float]]]) -> dict[str, object]` — the §Metrics `tp_errors` object; `sizes` maps `pred_row` to `(pred_size, gt_size)`.
  - `@dataclass(frozen=True) class GtCooperative: status_single: str | None; status_fused: str | None; seen_by_other_gt: bool | None; other_kind: str | None; other_agent_tp: bool | None`
  - `cooperative_counts(rows: Sequence[GtCooperative]) -> dict[str, object]` — the §Cooperative views counts and `recovery_rate`.

- [ ] **Step 1: Write the failing tests**: the five normative AP values; `Counts(0,0,0,0).precision is None`; `counts_of` on a hand-built `MatchResult`; `tp_errors` with two pairs gives means and the median; `tp_errors(())` is all-null; `cooperative_counts` on rows covering all four cases plus `recovery_rate == 0.5` for one recovered and one double miss, and `None` when neither.
- [ ] **Step 2: Run** — ImportError. **Step 3: Implement.** **Step 4: Run, lint, pyright.** **Step 5: Commit** `feat(evaluation): AP, counts, TP errors and cooperative counts`.

### Task 6: Scene evaluation, files, run driver, report and compare

**Files:**
- Create: `coperceive/evaluation/evaluate.py`, `coperceive/evaluation/report.py`
- Test: `tests/test_eval_run.py`

**Interfaces:**
- Consumes: Tasks 1–5; `coperceive.assoc.associate`; `coperceive.diagnostics.annotation_center_world`; `coperceive.viewdata.scene_directories`; `coperceive.store.read_scene`; `coperceive.adapters.ingest.coperceive_version`.
- Produces (`evaluate.py`):
  - `MATCH_SCHEMA: pa.Schema` (§`matches.parquet`), `METRICS_FORMAT = "coperceive-eval"`, `METRICS_VERSION = 1`.
  - `world_box(scene: Scene, agent_id: str, time_us: int, center, size_lwh, rotation: SE3, frame: BoxFrame) -> WorldBox` — body boxes through `lookup_pose(max_gap_us=0)`.
  - `@dataclass(frozen=True) class SceneEvaluation: matches: tuple[dict[str, object], ...]; metrics: dict[str, object]`
  - `evaluate_scene(scene: Scene, meta: RunMeta, boxes: Sequence[PredictionBox], config: EvalConfig, *, created_utc: str) -> SceneEvaluation` — outputs in first-appearance order; per time per category `match_sample` for statuses, then once more per threshold for AP; cooperative columns from §Cooperative views; row order of §`matches.parquet`; `pair_id` counts from 0 within the file.
  - `write_scene_evaluation(directory: Path, result: SceneEvaluation) -> None` — `matches.parquet` and `metrics.json` (`json.dumps(..., allow_nan=False)`).
  - `read_metrics(directory: Path) -> dict[str, object]`, `read_matches(directory: Path) -> pa.Table`.
  - `@dataclass(frozen=True) class RunReport: evaluated: tuple[str, ...]; skipped: tuple[str, ...]; errors: tuple[str, ...]`
  - `evaluate_run(scenes_root: Path, predictions_root: Path, run_id: str, config: EvalConfig, *, scene_ids: Sequence[str] | None = None, created_utc: str) -> RunReport` — every scene directory under the root that has `<run_id>/<scene_id>/boxes.parquet`; validation errors per scene go into `errors` and do not stop the run.
- Produces (`report.py`):
  - `@dataclass(frozen=True) class Row: scene_id: str; agent_id: str; source: str; values: Mapping[str, object]`
  - `collect(scenes_root: Path, predictions_root: Path, run_id: str) -> tuple[Row, ...]`
  - `render_report(rows: Sequence[Row], thresholds: Sequence[float]) -> str` — the Markdown table of §`metrics.json`'s last paragraph with the aggregate row.
  - `render_compare(rows_a, rows_b, thresholds) -> str` — the same with `b − a` deltas.

- [ ] **Step 1: Write the failing tests** on `cooperative_scene()` + `synthetic_predictions()`: `evaluate_scene` gives, for the vehicle `single` output at 100000 µs, `tp == n_vehicle_gt_at_100000 − 2`, `fp == 2` (the far car and the mislabelled truck), `miss == 2` (track 99 and track 5), where `n_vehicle_gt_at_100000` is counted from the fixture; the vehicle `fused` output has `miss == 1`; cooperative counts for the vehicle have `recovered == 1` (track 99) and `lost_by_fusion == 0`; `seen_by_other_gt` is `True` for track `42`'s vehicle row with `other_kind == "shared_track"`; `matches.parquet` round-trips through `read_matches` with the §`matches.parquet` column list exactly; `test_metrics_json_has_no_nan_and_a_zero_ap_for_an_unpredicted_class` (`pedestrian` has GT and no prediction → `0.0`; `json.loads` of the written file succeeds; no `None` key); `evaluate_run` over a root with two copies of the scene evaluates both and skips a scene without `boxes.parquet`; `render_report` contains the aggregate row and one `AP@0.5` column; `render_compare` shows a `+0.000` delta when both runs are the same files.
- [ ] **Step 2: Run** — ImportError. **Step 3: Implement.** **Step 4: Run, lint, pyright.** **Step 5: Commit** `feat(evaluation): scene evaluation, match table, metrics file, run driver and reports`.

### Task 7: CLI

**Files:**
- Create: `coperceive/evaluation/commands.py`
- Modify: `coperceive/cli.py` (register two subparsers next to `add_adapters_parser(sub)`)
- Test: `tests/test_eval_cli.py`

**Interfaces:**
- Consumes: Tasks 3 and 6.
- Produces: `add_parser(sub)` registering
  - `coperceive predictions import <file> --run ID --root DIR [--scene ID] [--predictions DIR] [--model TEXT] --score-note TEXT [--categories a,b] [--source-note NAME=TEXT ...] [--force]`
  - `coperceive eval run <run_id> --root DIR [--predictions DIR] [--config FILE] [--scenes a,b]`
  - `coperceive eval report <run_id> --root DIR [--predictions DIR]`
  - `coperceive eval compare <run_a> <run_b> --root DIR [--predictions DIR]`
  - stdout of `eval run`: `evaluated N skipped M errors E` then one `error\t<scene>: <text>` line per error; exit 1 when `errors` is non-empty. `created_utc` is `datetime.now(timezone.utc)` formatted `%Y-%m-%dT%H:%M:%SZ`.

- [ ] **Step 1: Write the failing tests** through `coperceive.cli.main`: write the fixture scene to `tmp/scenes/coop`, dump `synthetic_predictions` to JSONL with a `scene_id` column, `predictions import` creates `tmp/scenes/predictions/synthetic/coop/boxes.parquet` and `run.json`; `eval run synthetic --root tmp/scenes` prints `evaluated 1`; `eval report` prints a table containing `coop`; `eval compare synthetic synthetic` prints `+0.000`; a missing `--score-note` exits 2 (argparse); `eval run nope` exits 1 with `nope` on stderr.
- [ ] **Step 2–5** as before. Commit `feat(evaluation): predictions import and eval run|report|compare commands`.

### Task 8: Evaluation view data (no FastAPI)

**Files:**
- Create: `coperceive/evalview.py`
- Test: `tests/test_evalview.py`

**Interfaces:**
- Consumes: Task 3 (`read_run_meta`, `list_runs`, `check_run_id`), Task 6 (`read_metrics`, `read_matches`), `coperceive.viewdata._box_lines` semantics (re-implement the 24-vertex edge list locally or import the private helper — import it and add it to `viewdata.__all__` as `box_lines`).
- Produces:
  - `class EvalViewError(CoperceiveError)` with `status: int` (404 or 400).
  - `runs_for_scene(root: Path, scene_id: str) -> dict[str, object]` — the §HTTP `/predictions` body.
  - `frame_rows(root: Path, run_id: str, scene_id: str, time_us: int, *, compare: str | None) -> dict[str, object]` — the `/frame` body; rows at that time from `matches.parquet` with `center`, `size_lwh`, `pose`, `lines`.
  - `series(root: Path, run_id: str, scene_id: str, *, compare: str | None) -> dict[str, object]` — the `/series` body.
  - A small per-process cache keyed by `(run_id, scene_id, mtime_ns)` for the matches table.

- [ ] **Step 1: Write the failing tests** on a root prepared with `write_run` + `evaluate_run`: `runs_for_scene` lists `synthetic` with `evaluated: True` and the vehicle outputs; `test_unevaluated_run_is_listed_but_has_no_frame` (a second run written but not evaluated → listed `evaluated: False`; `frame_rows` raises `EvalViewError` with `status == 404` and `"eval run"` in the message); `frame_rows` at 100000 µs has one row per `matches.parquet` row at that time, each with 72 numbers in `lines`; `compare` returns the other run's rows; `series` has `len(times_us) == len(tp)`; `run_id` `"../x"` raises with `status == 400`.
- [ ] **Step 2–5** as before. Commit `feat(evalview): prediction runs, frame rows and series for the page`.

### Task 9: Server routes, serve flag, figure

**Files:**
- Modify: `coperceive/app/server.py` (state `predictions`, three routes, `run_id` on `PlotRequest`), `coperceive/app/serve.py` (`predictions` kwarg), `coperceive/cli.py` (`serve --predictions`, `export bev --run`), `coperceive/plot.py` (`render_bev(..., predictions: Sequence[PredictionDraw] = ())`), `docs/viewer.md` (HTTP table rows), `web/openapi.json` (regenerate from `create_app(root=..., token=None).openapi()` in a Python one-liner; `web/src/api/schema.ts` is regenerated by CI or by Adarsh, see HANDOVER).
- Test: `tests/test_app.py` (new tests; file is outside strict pyright), `tests/test_plot.py`.

**Interfaces:**
- Consumes: Task 8; `coperceive.evaluation.predictions.predictions_root`.
- Produces:
  - `create_app(..., predictions: str | Path | None = None)`; `app.state.predictions = predictions_root(root, predictions)`.
  - `GET /api/scenes/{scene_id}/predictions`, `GET /api/scenes/{scene_id}/predictions/{run_id}/frame?time_us=&compare=`, `GET /api/scenes/{scene_id}/predictions/{run_id}/series?compare=` — bodies from Task 8; `EvalViewError.status` → `HTTPException`; no predictions root → 404 `"no predictions root"`.
  - `PlotRequest.run_id: str | None = None`; `_export_bev` reads the rows with `frame_rows` and passes `PredictionDraw(center, size_lwh, rotation, status, kind)` items.
  - `plot.py`: `@dataclass(frozen=True) class PredictionDraw: center; size_lwh; rotation: SE3; status: str; kind: str`; drawn after GT boxes with §Colours and styles (matplotlib `linestyle` `"-"`, `(0, (4, 2))`, `(0, (1, 2))`; `linewidth` 1.0 / 1.0 / 1.5 / 0.8, alpha 0.5 for ignored). A matched GT (`pair_id` set) is drawn at alpha 0.4.

- [ ] **Step 1: Write the failing tests**: `test_prediction_routes_list_frame_and_series` (prepare a root as in Task 8, `TestClient(create_app(root=tmp, token=None))`, the three routes return 200 and the shapes of §HTTP; `compare=synthetic` echoes); `test_prediction_routes_refuse_traversal` (`/api/scenes/coop/predictions/..%2Fx/frame?time_us=100000` and `/api/scenes/coop/predictions/a%2Fb/frame` are 400 or 404 and no file under `tmp` outside `predictions/` was read — assert on status only); `test_prediction_routes_404_without_root`; `test_bev_export_draws_predictions` in `tests/test_plot.py` (`render_bev(..., predictions=(PredictionDraw(...),))` writes a PNG whose `PlotResult` reports `predictions == 1` — add that field to `PlotResult`).
- [ ] **Step 2–5** as before. Commit `feat(app): prediction routes, --predictions root and predictions on the bird's-eye figure`.

### Task 10: Page

**Files:**
- Create: `web/src/viewer/predictions.tsx` (the Predictions block: run select, compare select, source chips, status chips, cooperative chips, score slider, legend), `web/src/viewer/predictionLayer.ts` (three.js `LineSegments` per status with `LineBasicMaterial` / `LineDashedMaterial`, `computeLineDistances()`, dash values from §Colours and styles, run B dash offset), `web/src/viewer/predictionTypes.ts` (`RunInfo`, `PredictionRow`, `PredictionFrame`, `PredictionSeries`, guards `isPredictionFrame`, `isPredictionSeries`, `isRunList`).
- Modify: `web/src/viewer/Viewer.tsx` (fetch `/predictions` on scene change, `/frame` per sample with the frame cache pattern, `/series` once per run; feed the layer, the timeline and the side panel), `web/src/viewer/timeline.tsx` (new optional props `evalSeries?: {precision: number[]; recall: number[]; fp: number[]}` and `evalCompare?: same`; two extra sparklines in the existing style, run B thinner; a range marker where recall is below the scene median by more than 0.3), `web/src/viewer/SidePanel.tsx` (`SceneCard` gains mAP at each threshold, recall, the four cooperative counts, A/B deltas; `Inspector` shows run, source, score, status, category, matched GT track id, IoU, centre and yaw error, and the cooperative flags when a prediction or a GT row is picked), `web/src/viewer/appearance.ts` (`predictionScore: number | null` in `Appearance`, default `null` meaning "the run's score_threshold"), `web/src/viewer/ExportDialog.tsx` (bird's-eye export passes `run_id` when a run is selected), `web/src/index.css` (chip and legend styles; colours as CSS variables `--eval-tp: #3BAA6B; --eval-fp: #E0A431; --eval-miss: #8E6CEF; --eval-ignored: #6B6B6B`).

**Interfaces:**
- Consumes: the §HTTP bodies (Task 8/9) through `fetch` with `apiBase()` and `authHeaders()` as `Viewer.tsx` already does.
- Produces: no new persistent state except `predictionScore` in `coperceive-view`.

- [ ] **Step 1:** `predictionTypes.ts` with the three guards; a type-level check that `PredictionRow` has every §`matches.parquet` column plus `center`, `size_lwh`, `pose`, `lines`.
- [ ] **Step 2:** `predictionLayer.ts`: `buildPredictionObjects(rows, options) -> THREE.Group` with one `LineSegments` per status (and per run in compare mode); `options.showStatuses: Set<string>`, `options.showSources: Set<string>`, `options.cooperative: Set<"recovered"|"lost_by_fusion"|"only_other_agent"|"nobody">`, `options.minScore: number`, `options.runTag: "A"|"B"`. A GT row with `pair_id` set is omitted from this group (the GT wire is already drawn by the frame) and reported back as `dimmedGtKeys: Set<string>` so `Viewer.tsx` can draw those GT wires at 40% opacity.
- [ ] **Step 3:** `predictions.tsx` block; chips are `<button aria-pressed>` with the status word in text (the legend is text + a 24 px line sample in the status style, not colour alone).
- [ ] **Step 4:** wire `Viewer.tsx`, `timeline.tsx`, `SidePanel.tsx`, `ExportDialog.tsx`, `appearance.ts`, `index.css`.
- [ ] **Step 5:** `tsc -b` and `oxlint` from `web/` where node is available (CI otherwise); confirm `coperceive/app/static` is **not** committed from the HPC.
- [ ] **Step 6: Commit** `feat(web): predictions layer, status chips, eval sparklines, inspector and summary fields`.

### Task 11: End-to-end, docs, handover

**Files:**
- Create: `docs/eval.md` (user doc: format, import, config, matching rules in words with a pointer to the spec, CLI, viewer, figures, what was checked), `tests/test_eval_end_to_end.py`
- Modify: `README.md` (an "Evaluate" section after "Other datasets"), `CHANGELOG.md`, `mkdocs.yml` (nav: `Evaluate: eval.md`), `docs/adr/0010-eval-predictions.md` ("As built" section), `HANDOVER.md` (state, decisions in code, open questions, gates, the acceptance-run plan), `docs/viewer.md` (Predictions block paragraph).

- [ ] **Step 1: Write the end-to-end test**: fixture scene written to `tmp/scenes/coop`; JSONL → `predictions import` → `eval run` → `TestClient` `/predictions`, `/frame`, `/series` → `export bev` with `run_id` → `eval compare synthetic synthetic`; assert the chain of files exists and the frame rows' statuses match `metrics.json` totals at 100000 µs.
- [ ] **Step 2: Run** — fails until the earlier tasks are merged; then passes.
- [ ] **Step 3:** Write the docs; `mkdocs build` in the gates job.
- [ ] **Step 4: Commit** `docs: evaluation step A user doc, changelog, handover`.

### Task 12: Acceptance (not code; after Tasks 1–11)

A real `single` run on Griffin-25m from the released `vehicle-side.pth` through the Griffin toolkit's inference, exported with `write_run`, evaluated with `eval run`, opened in the viewer. Needs a GPU job and the toolkit environment; planned separately with Adarsh. Until it has run, the page has only been seen with synthetic predictions.

---

## Self-review notes

- Spec coverage: §Layout/§`run.json`/§`boxes.parquet`/§Import → Task 3 and 7; §Evaluation config → Task 2; §Geometry → Task 1; §Matching → Task 4; §Metrics → Task 5 and 6; §Cooperative views → Task 5 (counts) and 6 (columns); §`matches.parquet`/§`metrics.json` → Task 6; §HTTP → Tasks 8–9; §Colours and styles → Tasks 9 (figure) and 10 (page); `eval report|compare` → Tasks 6–7.
- Names used across tasks: `WorldBox`, `EvalConfig`, `threshold_for`, `RunMeta`, `PredictionBox`, `GtBox`/`PredBox`/`Pairing`/`MatchResult`, `match_sample`, `average_precision`, `Counts`, `GtCooperative`, `evaluate_scene`, `write_scene_evaluation`, `read_metrics`, `read_matches`, `evaluate_run`, `frame_rows`, `series`, `runs_for_scene`, `PredictionDraw`, `predictions_root` — each defined once above.
- Review Focus items 1–5 are pinned to Tasks 3, 4, 6, 9, 8 respectively.
