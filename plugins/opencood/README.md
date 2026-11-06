# coperceive-opencood-model

The Coperceive model plugin for an OpenCOOD checkpoint. It makes
`coperceive model run opencood …` run an OpenCOOD detection model — first model:
CoBEVT LiDAR intermediate fusion on V2XSet — over Coperceive schema-1 scenes, locally
or through `--slurm`, and writes an ordinary predictions format 1 run.

Full setup, cluster checks and the unverified list: [`docs/models/opencood.md`](../../docs/models/opencood.md).
The plugin contract itself is [`docs/spec/model-plugin.md`](../../docs/spec/model-plugin.md).

## What it is

- `model.yaml` — the manifest: plugin `opencood`, model `cobevt-lidar-v2xset`, lidar input
  from every CAV, fused world-frame boxes attributed to the ego vehicle, `gpu: true`, the
  `checkpoint` option that names the archive (there is **no** default checkpoint path).
- `worker.py` — the protocol-1 child, run by the OpenCOOD conda environment's interpreter.
- `opencood_convert.py` / `opencood_batch.py` — pure schema-1 ↔ OpenCOOD conversions: the inverse of the
  ADR 0012 `diag(1, -1, 1)` reflection, OpenCOOD's `x_to_world` pose convention, both box
  formats, and the per-CAV `base_data_dict` OpenCOOD's datasets take.
- `opencood_checkpoint.py` — archive hashing, safe extraction, discovery of `config.yaml` and the
  weights file inside the archive.
- `opencood_engine.py` — the thin OpenCOOD seam: loads the checkpoint's own config and weights and
  runs OpenCOOD's own dataset/preprocessing model-forward/postprocessing calls. OpenCOOD is
  imported from the user's environment at runtime; no OpenCOOD code is vendored.

## Runtime dependency and licence

The plugin's own code is Apache-2.0. **OpenCOOD is a runtime dependency and is not
installed, vendored or redistributed by this package**: the worker imports `opencood` from
the user's own OpenCOOD conda environment.

OpenCOOD is **not Apache-2.0**. Its repository `LICENSE`
(<https://github.com/DerrickXuNu/OpenCOOD/blob/main/LICENSE>, fetched 2026-11-06) is the
"Academic Software License: © 2021 UCLA Mobility Lab": academic or nonprofit researchers may
use it free of charge for educational or academic research only; transferring the software
(including derivatives) to any other person or entity is prohibited; commercial entities
must contact <jiaqima@ucla.edu>. Individual source files also carry a
`License: TDG-Attribution-NonCommercial-NoDistrib` header. **Check the licence of your own
OpenCOOD checkout and of the checkpoint archive before use; any commercial or redistributed
use needs a licence review.** The checkpoint archive is user-supplied; its contents, licence
and performance are **unverified** from this repository (the cluster checks are in the docs).

## Setup (HPC, no internet on compute nodes)

Build the wheel on the login node and install it into the Coperceive `.venv` from a local
wheelhouse, as for any plugin (compute nodes install nothing):

```bash
.venv/bin/pip wheel plugins/opencood --no-deps --no-build-isolation -w ~/wheelhouse
.venv/bin/pip install --no-index --find-links ~/wheelhouse coperceive-opencood-model
```

The OpenCOOD environment (default conda name `opencood`) is built once on the login node,
as `docs/models/opencood.md` describes; `coperceive model inspect opencood` prints the
resolved interpreter.

## Run

```bash
coperceive model run opencood \
  --scenes /path/to/staged-scenes --out /path/to/predictions --run-id opencood-cobevt \
  --option checkpoint=/path/to/cobevt_lidar.zip
```

Add `--slurm` (and `--slurm-partition`, `--slurm-scratch-template`, …) to get the job
script; the checkpoint archive is staged into the job's scratch and the inner `--option`
is rewritten to the staged copy.

## Unverified from this repository

- the checkpoint archive's contents and names (config file name, epoch, fusion method,
  voxel size, weight file name);
- the OpenCOOD commit in the user's conda environment (the seam follows the public
  OpenCOOD `main` API: `yaml_utils.load_yaml`, `train_utils.create_model`,
  `IntermediateFusionDataset`/`EarlyFusionDataset`, `collate_batch_test`, `post_process`);
- every AP/mAP number — **to be measured** on the cluster.
