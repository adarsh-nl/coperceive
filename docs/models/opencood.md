# OpenCOOD model plugin: CoBEVT LiDAR on V2XSet

`plugins/opencood/` (`coperceive-opencood-model`) makes `coperceive model run opencood …`
run an **OpenCOOD checkpoint** over Coperceive schema-1 scenes — first model:
`cobevt-lidar-v2xset`, CoBEVT LiDAR intermediate fusion on the V2XSet/OPV2V release layout —
locally and through `model run --slurm`, writing an ordinary predictions format 1 run that
`eval run`, `report`, `compare`, the viewer and `export bev` read unchanged.

> **Unverified from this repository.** The checkpoint archive on the cluster is not reachable
> from the development checkout. Its contents (config file name, epoch, fusion method, voxel
> size, weight file name), the OpenCOOD commit in the user's conda environment and every AP
> number are **unknown** here. They are cluster checks: see
> [What Adarsh must check on the cluster](#what-adarsh-must-check-on-the-cluster). Expected
> AP is **to be measured**, not quoted.

## Licence

- The plugin's own code is **Apache-2.0**.
- OpenCOOD is a **runtime dependency**: the worker imports `opencood` from the user's own
  conda environment. It is **not vendored, installed by this package, or redistributed**.
- OpenCOOD is **not Apache-2.0**. Upstream
  [`LICENSE`](https://github.com/DerrickXuNu/OpenCOOD/blob/main/LICENSE) (checked 2026-11-06)
  is the "Academic Software License: © 2021 UCLA Mobility Lab": academic or nonprofit
  researchers may use it free of charge for educational or academic research; transferring
  the software (including derivatives) to any other person or entity is prohibited; commercial
  entities must contact <jiaqima@ucla.edu>. Individual source files also carry a
  `License: TDG-Attribution-NonCommercial-NoDistrib` header.
  **Licence status: known (link above), but the user must check their own checkout and the
  checkpoint archive; any commercial or redistributed use needs a licence review.**
- The checkpoint archive is user-supplied and is not distributed with Coperceive. Its
  licence is unknown here.
- The OPV2V and V2XSet datasets publish no licence: see [datasets/opencood.md](../datasets/opencood.md).
  Research use on the HPC only; no frames, crops, screenshots or derived scene files enter a
  public repository.

## Setup on the cluster (login node; compute nodes have no internet)

1. Build the OpenCOOD environment **once on the login node**, because compute nodes have no
   internet. Clone `https://github.com/DerrickXuNu/OpenCOOD` and follow *that checkout's*
   `README.md` installation section — Coperceive does not pin or copy its requirements, and
   the Python/torch/CUDA versions of the environment are the user's choice. Note the commit
   (`git -C <checkout> rev-parse --short HEAD`) and the versions
   (`python -c "import opencood, torch; print(torch.__version__)"`); they decide whether this
   plugin's seam runs and belong in the report.
2. The manifest resolves the environment by conda name: `environment: {conda: opencood}`.
   A different name is a one-line change in `model.yaml`, or pass a prefix. Nothing is
   activated; the parent starts `<prefix>/bin/python` directly, so the same command works
   inside a Slurm job.
3. Install the plugin wheel into the Coperceive `.venv` from a local wheelhouse, as the
   [model plugin spec](../spec/model-plugin.md#installation) describes:

   ```bash
   .venv/bin/pip wheel plugins/opencood --no-deps --no-build-isolation -w ~/wheelhouse
   .venv/bin/pip install --no-index --find-links ~/wheelhouse coperceive-opencood-model
   ```

   `coperceive model inspect opencood` then prints the manifest and the resolved interpreter.
   Without the install, `--manifest plugins/opencood/coperceive_opencood_model/model.yaml`
   works too.
4. If this cluster's OpenCOOD environment needs CUDA modules on compute nodes, add them under
   `environment.modules` in the manifest; the Slurm emitter writes one `module load` per
   entry. None are pinned here because the environment's CUDA stack has not been checked.

## The checkpoint

Pass the archive on the command line; there is deliberately **no default and no hard-coded
cluster path** in the plugin:

```bash
--option checkpoint=/datasets/eemcs/ps/cv/opencood/v2xset_checkpoints/cobevt_lidar.zip
```

The model option is required and the plugin fails with exit 1 when it is missing. The
archive's own `config.yaml` fixes the model architecture, fusion method, voxel size, anchor
range, box order and score threshold: the plugin reads those from the checkpoint and never
assumes them. Inside a `--slurm` job the archive is staged into the job's scratch
(`$SCRATCH/checkpoints/checkpoint/<archive name>`) and the inner `--option` points at the
staged copy; the shared path is not read by the compute node.

If the archive holds more than one YAML or more than one weights file, the worker's error
lists the candidates and you choose with `--option checkpoint_config=<path inside the archive>`
and `--option checkpoint_weights=<path inside the archive>`. The worker extracts the archive
into the run's work directory, discovers the two files, loads the weights itself (a
user-supplied archive has no fixed weight file name) and logs which files it used to
`<work_dir>/plugin.log`.

Other options: `score_threshold` (float, negative means the checkpoint config's own value),
`device` (torch device; empty uses the parent's choice, `cuda:0` when it exposes a GPU).

## How the input is built

Schema 1 stores right-handed `FLU` poses after the [ADR 0012](../adr/0012-left-handed-sources.md)
reflection of the left-handed CARLA source. OpenCOOD reads `[x, y, z, roll, yaw, pitch]` in
degrees in the CARLA world and builds `x_to_world` as `Rz(yaw)·Ry(-pitch)·Rx(-roll)`. The
worker therefore inverts the reflection through that convention: per CAV it sends the sweep
with schema `y` negated, the lidar pose as CARLA `(x, -y, z, roll, -yaw, -pitch)`, and the
CAV-to-ego matrices OpenCOOD's dataset computes (`x1_to_x2`, CARLA convention). On
OPV2V/V2XSet roll and pitch are zero in the files, so in practice this is the y reflection
with a negated yaw. The mapping and both box formats are unit-tested; a cluster check should
confirm it on a real scene (see below).

Per frame:

- **Agents asked of the parent:** `vehicle` and `infrastructure`, so the V2XSet roadside
  unit's sweep is part of the fusion (`inputs.agents`).
- **Ego:** the first requested `vehicle` in scene order (`configure.agents`). A frame where
  that ego has no pose or sweep produces no boxes rather than boxes attributed to another
  agent. `--agents` restricts which CAVs are sent and can therefore change the ego; do not
  pass a subset when reproducing a fused result.
- **CAV selection:** the ego plus every CAV within the checkpoint environment's
  `COM_RANGE` (70 m in OpenCOOD `main`), nearest first, infrastructure last, at most the
  checkpoint config's `max_cav` CAVs (`5` in the CoBEVT config).
- **Fused rows:** attributed to the ego vehicle, `source: fused`, category `vehicle` (the
  releases annotate vehicles only), `frame: world`, converted from OpenCOOD's ego-frame
  corners through the ego pose and the reflection. Score is the checkpoint's classification
  probability after its own score threshold.

Not stored in schema 1 and therefore fixed: `ego_speed` is sent as `0` (the model's prior
encoding may use it; CoBEVT's configured model does not appear to, but this is unverified),
and camera images are not used at all (the first model is LiDAR-only).

## Run

Staged scenes come from `coperceive adapters ingest v2xset …` (see
[datasets/opencood.md](../datasets/opencood.md)); lidar points must be present, and
`model run --slurm` stages `points.bin` for this lidar model.

Local (inside an allocation or on a workstation with the OpenCOOD env):

```bash
coperceive model run opencood \
  --scenes /path/to/staged-scenes \
  --out /path/to/predictions \
  --run-id opencood-cobevt \
  --option checkpoint=/path/to/cobevt_lidar.zip
```

Slurm (partition and scratch template come from your cluster profile/environment):

```bash
coperceive model run opencood \
  --scenes /path/to/staged-scenes \
  --out /path/to/predictions \
  --run-id opencood-cobevt \
  --option checkpoint=/path/to/cobevt_lidar.zip \
  --slurm --submit
```

`--slurm` without `--submit` prints the sbatch and writes it under `<out>/.slurm/`, along
with the `.scenes.txt` files-from list; the checkpoint is staged into scratch before the run
and never copied home as a result. `resources.seconds_per_sample`, `staging_seconds` and
`conversion_seconds` in the manifest are **estimates** (inference, checkpoint staging and
model loading have not been timed on the cluster from this repository): `--time` uses them
only until a run under `--out` reports `provenance.timings`.

## Evaluate

```bash
coperceive eval run opencood-cobevt --root /path/to/staged-scenes
coperceive eval report opencood-cobevt --root /path/to/staged-scenes
```

The run is a normal predictions run: categories/sources/score note are in `run.json`,
`provenance` records the plugin, model, manifest hash, checkpoint archive digest and hashed
file record, environment and timings. Predicted category `vehicle` matches the ground-truth
category the OPV2V/V2XSet adapters write.

**Expected AP: to be measured.** The CoBEVT paper
([arXiv:2207.02202](https://arxiv.org/abs/2207.02202)) reports numbers for its own training
and evaluation protocol, and the OpenCOOD model zoo report other numbers for other
checkpoints; neither is a claim about *this archive's* epoch/config. Quote a number only
after a cluster run of this exact checkpoint on this exact staged split, and record the run id
and job id next to it.

## What Adarsh must check on the cluster

All commands below are read-only. Record the output in the task/PR (no dataset bytes, no
checkpoint bytes in the repository).

1. **Archive contents** (they are unknown to this repository):

   ```bash
   unzip -l /datasets/eemcs/ps/cv/opencood/v2xset_checkpoints/cobevt_lidar.zip
   ```

   Note the directory layout, the config file name and the weights file name/epoch. If there
   is exactly one YAML and one weights file, no extra options are needed.

2. **Checkpoint config** — confirm it is a CoBEVT/point-pillar config and note the values the
   plugin will read (do not change them):

   ```bash
   unzip -p /datasets/eemcs/ps/cv/opencood/v2xset_checkpoints/cobevt_lidar.zip '*config*.yaml' \
     | head -120
   ```

   Look for `model.core_method`, `fusion.core_method`, `preprocess.args.voxel_size`,
   `postprocess.order`, `postprocess.target_args.score_threshold`, `train_params.max_cav`.
   `fusion.core_method` must be `IntermediateFusionDataset` or `EarlyFusionDataset` (the
   plugin refuses late fusion and the V2 classes with a clear error).

3. **OpenCOOD environment** — the plugin's seam follows OpenCOOD `main`; record the checkout
   commit and that these imports exist in the environment the worker will use:

   ```bash
   git -C /path/to/OpenCOOD rev-parse --short HEAD
   conda run -n opencood python -c \
     "import opencood, torch; from opencood.hypes_yaml import yaml_utils; \
      from opencood.tools import train_utils; import opencood.data_utils.datasets as d; \
      print(torch.__version__, d.COM_RANGE, hasattr(d, 'IntermediateFusionDataset'))"
   ```

4. **Manifest and interpreter** — from a checkout with the plugin installed (or with
   `--manifest`):

   ```bash
   coperceive model inspect opencood --manifest plugins/opencood/coperceive_opencood_model/model.yaml
   ```

   It must resolve the conda interpreter and print the manifest including `checkpoint: {option: checkpoint}`.

5. **First run on one scene** (inside the GPU allocation or as a one-scene Slurm job) and
   check that boxes exist, the world coordinates are plausible (e.g. in `export bev --run`
   or the viewer), and that `plugin.log` shows the config/weights files it discovered:

   ```bash
   coperceive model run opencood --scenes SCENES --out PREDICTIONS --run-id opencood-1 \
     --option checkpoint=/datasets/eemcs/ps/cv/opencood/v2xset_checkpoints/cobevt_lidar.zip \
     --scene-ids SCENE_ID --slurm --submit
   ```

   If the archives' boxes are mirrored relative to the ground truth, the convention in
   `convert.py` is the thing to revisit, not the adapter.

6. **Evaluate and record AP**, then replace "to be measured" here with the run id and the
   metrics per evaluation config.

7. **Check the observed timings** (`provenance.timings` in `run.json`): after one timed run
   the manifest's *estimate* values are replaced by `provenance.timings` in the next
   `--slurm` estimate; if they differ a lot, update the manifest's comments and values.

## What was checked here

- `tests/test_opencood_convert.py`: the inverse reflection against hand-computed `M R M`
  matrices, OpenCOOD's `x_to_world` elementary rotations, the per-CAV batch mapping
  round trip (hand-computed `x1_to_x2`, CAV selection), and both box formats including the
  corner-order positive volume.
- `tests/test_opencood_checkpoint.py`: archive MD5, safe extraction (zip-slip refused),
  config/weights discovery, ambiguity and hint errors.
- `tests/test_opencood_plugin.py`: the manifest, that the plugin imports neither coperceive
  nor OpenCOOD statically and holds no cluster path, the `model check` skip without a
  checkpoint, the full `model run` → `eval run` chain against `tests/fake_opencood` (a
  stand-in package, no GPU), and the emitted sbatch (`bash -n`, checkpoint staged before the
  run, inner `--option` rewritten to the staged path, never a copy-home target).
- **Not checked here:** the real OpenCOOD run, the real archive, the real boxes and AP.
