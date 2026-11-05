# coperceive

A cooperative, multi-agent perception data library. Source: <https://github.com/adarsh-nl/coperceive>. The name is recorded in `docs/design/naming.md`.

Phase 1 is the scene model and the hybrid on-disk format. Phase 3 adds `Scene.at`, camera projection, cross-agent box queries, and `CooperativeDataset` in the `torch` extra. Phase 4 adds `coperceive validate`, which writes a JSON report and a Markdown report for a directory of schema 1 scenes. Phase 5 draws those scenes in the local page with three.js and can write a Rerun `.rrd`. A shared HD map per CARLA town is a sidecar next to those scenes. The contract a third party can implement is `docs/spec/format.md`. A Griffin reader and a local page came with the Phase 0 scaffold. This tree does not train models and does not build a desktop shell.

## Install

Python 3.10–3.13.

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[app,griffin]"
bash scripts/build_web_static.sh   # build the viewer page for this checkout
coperceive --help
```

Extras: `torch` (`coperceive.dataset.CooperativeDataset`; not imported by `coperceive`), `viewer` (rerun-sdk 0.22.1 and viser 0.2.23), `app`, `griffin`, `docs`, `dev`.

The licence is Apache-2.0. The repository stays private. See `docs/adr/0006-licence.md`.

## Local page

```bash
coperceive serve --root /path/to/a/workdir --maps /path/to/maps
```

The default bind is `127.0.0.1:8731`. A non-loopback host requires `--token`. Behind a proxy, pass `--root-path` with the prefix the proxy adds.

The page is the Vite build in `coperceive/app/static`, built (not committed) by `scripts/build_web_static.sh` — run it after a UI change and in a source checkout before `coperceive serve`; it needs Node.js. A release wheel already contains the built page. To view a build from elsewhere, `npx vite build --outDir DIR --emptyOutDir` and `coperceive serve --static DIR`. The Viewer tab lists scene directories under `--root`, plays recorded pose times, and can hide an agent. Viewpoints are orbit, bird's-eye, chase, drone, and a camera frustum. A strip under the transport shows each camera image for the current sample, with boxes and lidar projected onto it when those files exist beside the scene. Export Rerun writes `exports/<scene_id>.rrd` under that root and needs the `viewer` extra. Export sequence writes a camera MP4, GIF, or PNG sequence, and the page can also save the live 3D view. A view row toggles layers, colors, point size, box style, categories, and dark or light, and stores that in `localStorage` without the bearer token. The same exports from a terminal:

```bash
coperceive export rerun /path/scene --out /path/scene.rrd
coperceive export sequence /path/scene --out /path/front.mp4 --agent vehicle --sensor front
```

The page talks to the server only over HTTP. `coperceive.viewdata` does not import FastAPI or Rerun. Lane centres and road edges are drawn under the lidar when `--maps` resolves the scene's `map_name`. A later desktop package is described in `docs/adr/0007-desktop.md` and is not built.

## OPV2V and V2XSet

Builtin manifests `opv2v` and `v2xset` read the OpenCOOD layout (left-handed CARLA frame through one declared reflection, ADR 0012). Licence unknown: see `docs/datasets/opencood.md` before using the data anywhere but locally.

## Griffin

```bash
coperceive download griffin --subset 25m --preset dev --dest /path/outside/the/repo --dry-run
coperceive convert griffin --subset 25m --raw-root /path/griffin-release --out /path/scenes
```

`--preset dev` is drone metadata, vehicle metadata, and vehicle LiDAR. Camera archives are selected only with `--components`. The download needs the `griffin` extra, checks the MIT card and toolkit licence, and writes outside the git checkout. Recordings are not committed. Conventions are in `docs/datasets/griffin.md`. Earlier measurements are in `docs/design/griffin-verification.md`.

## Other datasets

```bash
coperceive adapters list
coperceive adapters check griffin --raw-root /path/griffin-release --param subset=25m
coperceive adapters ingest my-dataset.yaml --raw-root /path/raw --out /path/scenes
```

A dataset is described by a YAML manifest: folders and file patterns, the sample and time source, poses, sensors, boxes, images and lidar, with units, rotation encodings and axis conventions written out. `check` validates it against the raw tree and writes nothing. `ingest` writes schema 1 scenes. Griffin is the built-in manifest. Readers and adapters that need code register under the `coperceive.adapters` and `coperceive.readers` entry points. The walkthrough is `docs/datasets/add-a-dataset.md`; the design is `docs/adr/0009-dataset-adapters.md`.

## Evaluate

```bash
coperceive predictions import boxes.jsonl --run det-v1 --root /path/scenes --score-note "sigmoid"
coperceive eval run det-v1 --root /path/scenes
coperceive eval report det-v1 --root /path/scenes
coperceive eval compare det-v1 det-v2 --root /path/scenes
```

Model output goes in a sidecar root, `<scenes>/predictions/<run_id>/<scene_id>/boxes.parquet` with a `run.json`, from JSON lines or Parquet, or from a training script through `coperceive.evaluation.predictions.write_run`. `eval run` matches each output to the agent's own ground truth in the world frame, BEV IoU at 0.5 and 0.7 by default or centre distance by config, and writes `matches.parquet` and `metrics.json` with the config inside. `report` and `compare` print one Markdown table. The viewer draws the match rows in the status colours, filters them, puts precision and recall on the timeline and overlays a second run; `export bev --run` draws the same on the figure. The user page is `docs/eval.md`, the contract is `docs/spec/predictions.md`, the design is `docs/adr/0010-eval-predictions.md`.

## Run a model

```bash
coperceive model list
coperceive model check griffin --model vehicle-side
coperceive model run griffin --model vehicle-side --scenes /path/scenes --raw-root /path/raw --out /path/predictions --run-id det-v1
coperceive model run griffin --model instance-fusion --scenes /path/scenes --raw-root /path/raw --out /path/predictions --run-id det-coop --slurm
```

A model plugin is a YAML manifest plus a worker subprocess in its own Python environment, speaking newline-delimited JSON over stdin and stdout. `model run` writes a format 1 predictions run with a `provenance` block and a per-scene `cache/` under the run directory; `--slurm` emits an sbatch script for the HPC. The user page is `docs/models.md`, the contract is `docs/spec/model-plugin.md`, the design is `docs/adr/0011-live-model-plugin.md`.

## Validate

```bash
coperceive validate /path/scenes --json report.json --markdown report.md
```

The report covers time sync, UAV height above the vehicle, pose speed and acceleration, shared-track and nearest-centre box agreement, coverage, scene and frame counts, and, when a maps root is present, the distance from vehicle poses to the driving-lane centre. The centreline block is a measurement. It does not fail the run. The default exit is 0 once both files exist. `--strict` exits 1 on a hard failure: an unreadable scene, a missing frame, or a cross-agent pose miss. Speed and acceleration are warnings. A missing directory exits 2. The Griffin-25m figures are in `docs/validate.md`.

## Maps

```bash
coperceive maps fetch --dest /path/outside/the/repo
coperceive maps ingest --xodr /path/Town03.xodr --map-name Carla/Maps/Town03 --out /path/maps
coperceive maps align --maps /path/maps --scenes /path/scenes
```

`fetch` extracts Town03, Town06, Town07, and Town10HD from the CARLA 0.9.15 package. The OpenDRIVE bytes are not committed. The sidecar layout and the Griffin-25m residuals are in `docs/adr/0008-hd-map.md`.

## Tests

```bash
.venv/bin/pip install -e ".[app,griffin,dev]"
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/pyright
.venv/bin/pytest
```

## Docs

```bash
.venv/bin/pip install -e ".[docs]"
.venv/bin/mkdocs serve
```

Design notes live in `docs/`. Current state and open decisions are in `HANDOVER.md`.
