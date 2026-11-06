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

## What is verified from upstream OpenCOOD (public source, checked 2026-11-06)

The seam in `opencood_engine.py` was written against, and checked against, the public
[`DerrickXuNu/OpenCOOD`](https://github.com/DerrickXuNu/OpenCOOD) `main` branch on 2026-11-06
(no commit SHA is pinned here: the user's checkout may differ, and the cluster check below
records which one runs). These are the facts the plugin relies on, with the file each comes
from:

- **Pose convention.** `opencood/utils/transformation_utils.py::x_to_world` reads
  `[x, y, z, roll, yaw, pitch]` with angles in **degrees** and builds the rotation as
  `Rz(yaw) · Ry(-pitch) · Rx(-roll)`; `x1_to_x2(x1, x2)` is
  `inv(x_to_world(x2)) · x_to_world(x1)`.
  <https://github.com/DerrickXuNu/OpenCOOD/blob/main/opencood/utils/transformation_utils.py>
- **Box formats.** `opencood/utils/box_utils.py::boxes_to_corners_3d` writes the eight corners
  in the template order `(+,-,-), (+,+,-), (-,+,-), (-,-,-), (+,-,+), (+,+,+), (-,+,+), (-,-,+)`
  (`/2` on the box dimensions, then `Rz(heading)`, then the centre), and
  `corner_to_center` recovers the centre as the mean of corners `0,3,5,6`, the sizes from the
  edges, and the heading from the `0-3` edge. `project_box3d` projects corners with a 4×4
  matrix.
  <https://github.com/DerrickXuNu/OpenCOOD/blob/main/opencood/utils/box_utils.py>
- **Inference call pattern.** `opencood/tools/inference_utils.py::inference_intermediate_fusion`
  is `inference_early_fusion`: `output_dict['ego'] = model(batch_data['ego'])`, then
  `dataset.post_process(batch_data, output_dict)`.
  <https://github.com/DerrickXuNu/OpenCOOD/blob/main/opencood/tools/inference_utils.py>
- **Model loading.** `opencood/tools/train_utils.py::create_model(hypes)` imports
  `opencood.models.<hypes['model']['core_method']>` and instantiates the class whose name
  matches ignoring case; `to_device` moves nested dicts/lists. (Its `load_saved_model` only
  finds `latest.pth`/`net_epoch<N>.pth`, which a user-supplied archive has no reason to be
  named, so the plugin loads the weights itself.)
  <https://github.com/DerrickXuNu/OpenCOOD/blob/main/opencood/tools/train_utils.py>
- **Config loading.** `opencood/hypes_yaml/yaml_utils.py::load_yaml(file, opt=None)` loads a
  YAML file and applies its `yaml_parser` (for CoBEVT-style configs,
  `load_point_pillar_params`, which fills the anchor `W/H/D` and `vh/vw/vd`).
  <https://github.com/DerrickXuNu/OpenCOOD/blob/main/opencood/hypes_yaml/yaml_utils.py>
- **Dataset classes and communication range.** `opencood/data_utils/datasets/__init__.py`
  defines `COM_RANGE = 70` and the four dataset classes
  (`IntermediateFusionDataset` and `EarlyFusionDataset` are the ones this plugin supports;
  late fusion and the `V2` classes are refused with a clear error).
  <https://github.com/DerrickXuNu/OpenCOOD/blob/main/opencood/data_utils/datasets/__init__.py>
- **Dataset input/output shape.** `opencood/data_utils/datasets/intermediate_fusion_dataset.py`
  consumes `retrieve_base_data`'s `{cav: {ego, time_delay, lidar_np, params}}` dict and
  `collate_batch_test` sets the identity `transformation_matrix`; its post-processor
  (`opencood/data_utils/post_processor/voxel_postprocessor.py`) returns
  `(pred_box_tensor, scores)` with `pred_box_tensor` as `(N, 8, 3)` **ego-frame corners** after
  NMS and range filtering.
  <https://github.com/DerrickXuNu/OpenCOOD/blob/main/opencood/data_utils/datasets/intermediate_fusion_dataset.py>
  <https://github.com/DerrickXuNu/OpenCOOD/blob/main/opencood/data_utils/post_processor/voxel_postprocessor.py>
- **A public CoBEVT/V2XSet config exists**, `opencood/hypes_yaml/point_pillar_cobevt.yaml`
  (`model.core_method: point_pillar_cobevt`, `fusion.core_method: IntermediateFusionDataset`,
  `preprocess: SpVoxelPreprocessor` with `voxel_size [0.4, 0.4, 4]`,
  `postprocess: {core_method: VoxelPostprocessor, order: hwl, target_args.score_threshold: 0.2}`,
  `train_params.max_cav: 5`). It is an example of the *shape* of the config the plugin reads;
  it is **not** claimed to be the archive's config.
  <https://github.com/DerrickXuNu/OpenCOOD/blob/main/opencood/hypes_yaml/point_pillar_cobevt.yaml>

## What is unverified until the cluster run

1. **The archive's contents**: directory layout, config file name, weights file name/epoch, and
   therefore whether that config really is a CoBEVT/point-pillar config for V2XSet.
2. **The OpenCOOD commit in the user's environment** and whether that checkout exposes the
   entry points listed above unchanged.
3. **The numeric boxes**: no real scene has been inferred here; the mapping and box conversion
   are unit-tested against hand-computed matrices, not against ground truth.
4. **Whether the dataset `__init__` tolerates the empty `train/`/`validate/` roots** the
   engine gives it (it enumerates `validate_dir` even in test mode; empty on this side).
5. **Determinism on GPU**: before every frame the engine seeds numpy and torch from
   `blake2s(scene_id|time_us)` (`engine.frame_seed`), so OpenCOOD's point shuffle repeats for
   a second pass over the same frame — which is what `model check` rule 8 compares. CUDA
   kernels and non-deterministic torch ops are a separate question and not proven
   reproducible; if two runs differ box for box on the cluster, set
   `outputs.deterministic: false` in the manifest (rule 8 then reports `skipped`).
6. **AP**: to be measured — see [Evaluate](#evaluate).

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

**The expected archive shape is unverified.** The working assumption is one `.zip` holding a
`config.yaml` (name unknown) and one weights file (`.pth`/`.pt`/`.ckpt`, name unknown, possibly
inside a directory); the worker discovers them by suffix and prefers a config-named YAML and a
weight name containing `bestval`/`best`/`latest`/`epoch`. If the archive holds more than one
YAML or more than one weights file, the worker's error lists the candidates and you choose with
`--option checkpoint_config=<path inside the archive>` and
`--option checkpoint_weights=<path inside the archive>`. The worker extracts the archive
into a **temporary directory under the run's work directory**, discovers the two files, loads
the config and the weights into memory, deletes the extraction immediately, and logs which
files it used (name and archive-relative path) to `<work_dir>/plugin.log`. The archive itself
is never modified, and a multi-GB extraction does not stay under `<out>/.work` on a local run
(in a Slurm job the temporary directory is on the job's scratch and disappears with it).

Other options: `score_threshold` (float, negative means the checkpoint config's own value),
`device` (torch device; empty uses the parent's choice, `cuda:0` when it exposes a GPU).

## How the input is built

Schema 1 stores right-handed `FLU` poses after the [ADR 0012](../adr/0012-left-handed-sources.md)
reflection of the left-handed CARLA source. Per the upstream convention verified in
[What is verified from upstream OpenCOOD](#what-is-verified-from-upstream-opencood-public-source-checked-2026-11-06),
OpenCOOD reads `[x, y, z, roll, yaw, pitch]` in degrees and builds `x_to_world` as
`Rz(yaw)·Ry(-pitch)·Rx(-roll)`. The worker therefore inverts the reflection through that
convention: per CAV it sends the sweep with schema `y` negated, the lidar pose as CARLA
`(x, -y, z, roll, -yaw, -pitch)`, and the CAV-to-ego matrices OpenCOOD's dataset computes
(`x1_to_x2`, CARLA convention). **The mapping is exact because it inverts the adapter's own decode**, for any
roll/pitch; how large pitch and roll are in the released files is not measured in this
repository (the CARLA roads are flat, so they are expected to be near zero, but that is
unverified). The cluster checklist below includes a read-only command that prints the
roll/pitch range of real frame YAMLs; the cluster run confirms the mapping on a real scene.

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
# --predictions names the run root when it is not <scenes>/predictions
coperceive eval run opencood-cobevt --root /path/to/staged-scenes \
  --predictions /path/to/predictions
coperceive eval report opencood-cobevt --root /path/to/staged-scenes \
  --predictions /path/to/predictions
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
   md5sum /datasets/eemcs/ps/cv/opencood/v2xset_checkpoints/cobevt_lidar.zip
   ```

   Record the listing (`unzip -l`) and the digest (`md5sum`). Note the directory layout, the
   config file name and the weights file name/epoch. If there is exactly one YAML and one
   weights file, no extra options are needed. The digest is what `hello` and `run.json`
   `provenance.checkpoint.md5` will report; the parent recomputes it from the file and refuses
   a mismatch.

2. **Roll/pitch in the real frame YAMLs** (read-only) — is the assumption that flattened
   CARLA roads make pitch/roll ~0 true? The plugin's inversion is exact regardless, but this
   is the evidence the docs lack:

   ```bash
   .venv/bin/python - <<'PY'
   import glob
   import yaml
   paths = sorted(glob.glob("/datasets/eemcs/ps/cv/opencood/v2xset/validate/*/*/*.yaml"))[:200]
   poses = [yaml.safe_load(open(p, encoding="utf-8"))["lidar_pose"] for p in paths]
   print(len(poses), "poses")
   print("roll  deg: min %.6f max %.6f" % (min(p[3] for p in poses), max(p[3] for p in poses)))
   print("pitch deg: min %.6f max %.6f" % (min(p[5] for p in poses), max(p[5] for p in poses)))
   PY
   ```

   Record the range in the PR. If it is not ~0, the plugin still inverts the adapter's decode
   exactly, but a large range is the first thing to look at if boxes look rotated.

3. **Checkpoint config** — confirm it is a CoBEVT/point-pillar config and note the values the
   plugin will read (do not change them). The plugin reads the config out of the archive's
   discovered YAML; the command below only prints it:

   ```bash
   unzip -p /datasets/eemcs/ps/cv/opencood/v2xset_checkpoints/cobevt_lidar.zip '*config*.yaml' \
     | head -120
   unzip -p /datasets/eemcs/ps/cv/opencood/v2xset_checkpoints/cobevt_lidar.zip '*config*.yaml' \
     | grep -nE 'core_method|voxel_size|cav_lidar_range|root_dir|validate_dir|postprocess|target_args|score_threshold|order|max_cav'
   ```

   Look for `model.core_method`, `fusion.core_method`, `preprocess.args.voxel_size`,
   `postprocess.order`, `postprocess.target_args.score_threshold`, `train_params.max_cav`.
   `fusion.core_method` must be `IntermediateFusionDataset` or `EarlyFusionDataset` (the
   plugin refuses late fusion and the V2 classes with a clear error). If the grep does not
   match the expected keys, the archive's config is not the one this seam was written for:
   stop and report rather than editing the code around it.

4. **OpenCOOD environment** — the plugin's seam follows OpenCOOD `main`; record the checkout
   commit and that these imports exist in the environment the worker will use:

   ```bash
   git -C /path/to/OpenCOOD rev-parse --short HEAD
   conda run -n opencood python -c 'import opencood; print(opencood.__file__)'
   conda run -n opencood python -c \
     "import opencood, torch; from opencood.hypes_yaml import yaml_utils; \
      from opencood.tools import train_utils; import opencood.data_utils.datasets as d; \
      print(torch.__version__, d.COM_RANGE, hasattr(d, 'IntermediateFusionDataset'))"
   ```

   The second line is the interpreter and package path the Slurm job will use (both are
   recorded in `run.json` `provenance`); the third checks the seam's imports in one go.

5. **Manifest and interpreter** — from a checkout with the plugin installed (or with
   `--manifest`):

   ```bash
   coperceive model inspect opencood --manifest plugins/opencood/coperceive_opencood_model/model.yaml
   ```

   It must resolve the conda interpreter and print the manifest including `checkpoint: {option: checkpoint}`.

6. **First run on one scene** (inside the GPU allocation or as a one-scene Slurm job) and
   check that boxes exist, the world coordinates are plausible (e.g. in `export bev --run`
   or the viewer), and that `plugin.log` shows the config/weights files it discovered:

   ```bash
   coperceive model run opencood --scenes SCENES --out PREDICTIONS --run-id opencood-1 \
     --option checkpoint=/datasets/eemcs/ps/cv/opencood/v2xset_checkpoints/cobevt_lidar.zip \
     --scene-ids SCENE_ID --slurm --submit
   ```

   If the archives' boxes are mirrored relative to the ground truth, the convention in
   `opencood_convert.py` is the thing to revisit, not the adapter. After the run, the work directory
   must hold no extracted checkpoint tree (`<out>/.work/<run-id>/` outside a Slurm job): the
   extraction is a temporary directory removed once the model is loaded, and `plugin.log`
   names the config and weights files it used.

7. **Evaluate and record AP**, then replace "to be measured" here with the run id and the
   metrics per evaluation config.

8. **Check the observed timings** (`provenance.timings` in `run.json`): after one timed run
   the manifest's *estimate* values are replaced by `provenance.timings` in the next
   `--slurm` estimate; if they differ a lot, update the manifest's comments and values.

## What was checked here

Verified from the upstream public source on 2026-11-06 (list and file links above):
`x_to_world`/`x1_to_x2`, the `boxes_to_corners_3d` template and `corner_to_center`,
the `model(batch['ego'])` → `dataset.post_process(...)` call pattern, `create_model`'s import
rule, `load_yaml`'s `yaml_parser` application, `COM_RANGE = 70`, the dataset's CAV dict shape
and the ego-frame corner output.

Checked by this repository's tests:

- `tests/test_opencood_convert.py`: the inverse reflection against hand-computed `M R M`
  matrices, OpenCOOD's `x_to_world` elementary rotations, the per-CAV batch mapping
  round trip (hand-computed `x1_to_x2`, CAV selection), and both box formats including the
  corner-order positive volume.
- `tests/test_opencood_checkpoint.py`: archive MD5, safe extraction (zip-slip refused),
  config/weights discovery, ambiguity and hint errors.
- `tests/test_opencood_plugin.py`: the manifest and its licence wording, that the plugin
  imports neither coperceive nor OpenCOOD statically and holds no cluster path, the
  `model check` skip without a checkpoint, the full `model run` → `eval run` chain against
  `tests/fake_opencood` (a stand-in package, no GPU), and the emitted sbatch (`bash -n`,
  checkpoint staged to scratch before the run, inner `--option` rewritten to the staged path,
  never a copy-home target, `points.bin` staged for this lidar model).
- **Not checked here** (the [unverified list](#what-is-unverified-until-the-cluster-run)):
  the real OpenCOOD run, the real archive, the real numeric boxes and AP.
