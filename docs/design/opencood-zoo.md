# OpenCOOD model zoo

Proposed on 2026-11-28 for v0.1.3. It is built only on Phase 0 (the read-only inventory of
2026-11-24) and on strict-load job **858174**. That job ran on 2026-11-28 at this branch's
`cf0309f`, on an A40, with torch 1.12.1+cu113 and spconv-cu113 2.3.6, against OpenCOOD
`31ba160` with a clean tree. Its per-checkpoint table is on #254. The plugin is cited at
`cf0309f`. Anything neither source settles is marked **unverified**. No code is written until
Adarsh approves this note.

## What the job found

All 24 weights files on the cluster were read without running pickle code. Each contains only
tensors, and each sits beside its own `config.yaml`.

| Result at OpenCOOD `31ba160` | # | Checkpoints |
|---|---|---|
| Loads strictly | 15 | 1, 2, 3, 6–12, 17–21 |
| Builds, but 12 sparse-conv weights have their axes in another order | 4 | 13–16 (SECOND) |
| Doesn't build: pcdet CUDA ops not compiled | 1 | 5 (FPVRCNN) |
| Module not in OpenCOOD | 4 | 4 (CoSDH), 22–23 (CoBEVT camera), 24 (v2vreal) |

- **#4 and #24** load strictly in their own codebases: CoSDH (`Xu2729/CoSDH` `b270b69`) and
  v2x-vit (`DerrickXuNu/v2x-vit` `f0e6c13`).
- **#4's config** is refused by the plugin's safe reader, because of a
  `!!python/object/apply:collections.OrderedDict` tag.
- **#22–23** are camera models (`CamIntermediateFusionDataset`). The plugin is LiDAR only.
- **SECOND (#13–16):** the checkpoints store `[3, 3, 3, in, out]`, and spconv 2.3.6 builds
  `[out, 3, 3, 3, in]`. spconv has a `SPCONV_SAVED_WEIGHT_LAYOUT` option for this. As I read
  `spconv/pytorch/conv.py` L659–673, it permutes twice when `ALL_WEIGHT_IS_KRSC` is true. A
  working fix is **unverified**, and testing one needs a second job.

## DECISION (Adarsh): evaluation noise

Three of the 15 strict loaders were trained with simulated noise in `wild_setting`:

| # | Checkpoint | Delay (`async`, mode `sim`) | Location noise (`loc_err`) | Seed |
|---|---|---|---|---|
| 2 | Where2comm, V2XSet | on: 100 ms, which is 1 frame | on: `xyz_std` 0.2, `ryp_std` 0.2 | 20 |
| 3 | V2X-ViT, V2XSet | on: 100 ms, which is 1 frame | on: `xyz_std` 0.2, `ryp_std` 0.2 | 25 |
| 11 | PointPillar late fusion, OPV2V | off | on: `xyz_std` 0.1, `ryp_std` 0.2 | 20 |

#1, #20, #21 and #24 have a `wild_setting` with both switches off. The others have none.

- **Upstream applies the noise when it evaluates, too.** All line numbers are in
  `opencood/data_utils/datasets/basedataset.py`.
  - `BaseDataset.__init__` reads `wild_setting` whether training or not (L70–102).
  - `retrieve_base_data` delays each non-ego agent by `async_overhead // 100` frames
    (L244–250; `time_delay_calculation` L345–376).
  - `reform_param` adds Gaussian noise to each non-ego pose's x, y, z and yaw. Roll and pitch
    are left alone (L445–450; `add_loc_noise` L378–399).
  - `add_loc_noise` reseeds NumPy with the config's seed on every call (L393), so every call
    draws the same noise.
  - Nothing in `opencood/tools` overrides `wild_setting`.
  - v2x-vit `f0e6c13` has the same code (`v2xvit/data_utils/datasets/basedataset.py` L54–86,
    with `add_loc_noise` at L354 reseeding at L369, and L421–427).
- **The plugin skips it.** It overrides `retrieve_base_data` and always sends
  `time_delay = 0.0` and an identity `spatial_correction_matrix`
  (`opencood_engine.py` module docstring; `opencood_batch.py` L198–205). So a Coperceive
  run of #2, #3 or #11 is noise-free, whereas the paper's number was measured with noise.

**Options:**

- **A. Apply it:** run upstream's own delay and location-noise code with the config's values
  and seed.
  - Pro: matches upstream's protocol.
  - Con: delay means reading an earlier frame for each non-ego agent. Schema-1 scenes would
    need that frame present. Whether the plugin's per-frame batch can supply it is
    **unverified**.
- **B. Record `noise: none` in `run.json` and label it in the app,** for example a
  "noise-free (trained with delay + location noise)" tag on the run.
  - Pro: no new inference path, and the run says what it is.
  - Con: its numbers aren't comparable with the paper's.

Either way, checkpoints without noise need neither, so the decision gates only #2, #3 and #11.

## Which checkpoints to ship first

**Proposal.** Ship a checkpoint first only if all of these hold:

1. It loads strictly at OpenCOOD `31ba160`, the plugin's environment.
2. The plugin's safe reader reads its config.
3. It is LiDAR.
4. Its fusion is one the plugin supports: `IntermediateFusionDataset` or `EarlyFusionDataset`
   (`opencood_engine.py` `SUPPORTED_FUSION`).
5. It was trained without noise, so it doesn't wait on the decision above.

| Order | # | Model | Train set | Why |
|---|---|---|---|---|
| shipped | 1 | CoBEVT LiDAR (`cobevt-lidar-v2xset`) | V2XSet | already in `model.yaml` |
| first | 20, 21 | CoBEVT LiDAR, with and without compression | OPV2V | same model class as #1 |
| first | 10 | F-Cooper (`point_pillar_fcooper`) | OPV2V | intermediate, simple fusion |
| first | 7 | PointPillar attentive fusion | OPV2V | OPV2V's own baseline |
| first | 9 | PointPillar early fusion | OPV2V | early-fusion path |
| next | 6, 8, 12, 17–19 | PIXOR early, attentive + compression, V2VNet, VoxelNet ×3 | OPV2V | same criteria; #12 also has an `eval.yaml` |
| after the decision | 2, 3 | Where2comm, V2X-ViT | V2XSet | noise (above) |
| later | 11 | PointPillar late fusion | OPV2V | late fusion unsupported; noise too |
| later | 13–16 | SECOND ×4 | OPV2V | weight layout (above) |
| not planned | 4, 5, 22–24 | CoSDH, FPVRCNN, CoBEVT camera ×2, v2vreal | — | other codebase, unbuilt ops, or camera |

- **#2's train set:** "V2XSet" comes from the folder and file names. Its config's `root_dir`
  doesn't name a dataset.
- **Unverified for every row:** whether each model's `forward` reads only what the plugin
  sends (no `time_delay`, identity correction), and the AP of any checkpoint here. Each new
  model's first plugin run on the cluster checks both.

## What this doesn't change

- **Licences:** no checkpoint bytes, configs or dataset frames enter the repo. The checkpoint
  archive's licence is still unknown, as is OPV2V and V2XSet's.
- **The plugin's safety rules:** strict weights and no pickle code without
  `trust_checkpoint_pickle`.
- **Scope:** model entries, tests and any noise path come after approval, each in its own PR.
