# V2V4Real

V2V4Real is a real-world vehicle-to-vehicle recording: the catalog calls the agents "vehicle + vehicle", and the adapter types each discovered directory as `vehicle`. Coperceive reads an extracted OpenCOOD-style tree, writes schema 1 scenes, and can score predictions. It does not download the recordings.

## Licence, citation and download

**Licence.** Data licence: not stated by the authors in the linked README. Repository software licensing is not an explicit data grant. The repository LICENSE is an Academic Software License.

**Citation.** Use the canonical source and single BibTeX block in [Citation](#citation) below.

**Download.** [UCLA Box](https://ucla.app.box.com/v/UCLA-MobilityLab-V2V4REAL), linked from the [authors dataset page](https://mobility-lab.seas.ucla.edu/v2v4real/).

## Overview

| | |
| --- | --- |
| Paper | [arXiv:2303.07601](https://arxiv.org/abs/2303.07601) (catalog `paper_url`) |
| Official code | [ucla-mobility/V2V4Real](https://github.com/ucla-mobility/V2V4Real) (same repository) |
| Splits | `train`, `test` (`v2v4real.yaml` `params.split.allowed`) |
| Adapter | `v2v4real` |

The catalog status is `local-import`.

## What Coperceive reads

| | |
| --- | --- |
| Agents | `{split}/{scene}/{agent}/`, type `vehicle` |
| LiDAR | `{frame:06d}.pcd`, fields `x y z`. Intensity is filled with `0.0` |
| Cameras | No `images` stream. `camera*` records are optional. The manifest comment says the extracted release is LiDAR-only |
| Boxes and classes | `vehicles` stored with `frame: world`. Category is filled as `vehicle` |
| Maps | Not in the manifest. `world_frame` is `v2v4real-world`, marked unverified in the manifest |
| Timestamps | `time_us = stem × 100000`. The scale note cites the paper's 10 Hz sampling |

## Expected folder layout

From `coperceive/adapters/builtin/v2v4real.yaml`:

```text
/data/v2v4real/
  {split}/{scene}/{agent}/
    {frame:06d}.yaml
    {frame:06d}.pcd
```

`{split}` is `train` or `test`. The manifest header says scenario names start with `testoutput_CAV_data_` and the two vehicle directories are `0` and `1`.

## Open it in Coperceive

**Open Folder**, or the cluster browser.

```bash
coperceive adapters check v2v4real --raw-root /data/v2v4real --param split=test
coperceive adapters ingest v2v4real --raw-root /data/v2v4real --param split=test --out /data/v2v4real-scenes
coperceive validate /data/v2v4real-scenes
coperceive eval run RUN_ID --root /data/v2v4real-scenes
```

`coperceive dataset prepare --help` also lists `v2v4real` as an `--adapter` choice. Its `--split` text names OPV2V and V2XSet; this adapter's allowed values are still `train` and `test`.

## Known issues and conventions

- World-frame handedness: **unverified**. The adapter mirrors on a CARLA assumption. See [V4R-1](../geometry/conventions.md#v4r-1) (draft, PR #166).
- LiDAR +y: **unverified**. See [V4R-2](../geometry/conventions.md#v4r-2) (draft, PR #166).
- A published pose is a 4×4 matrix where the adapter reads six Euler numbers, and a six-value list would store pitch and roll with the wrong sign. See [V4R-4](../geometry/conventions.md#v4r-4) and [V4R-6](../geometry/conventions.md#v4r-6) (draft, PR #166).
- Boxes are annotated in the vehicle LiDAR frame and stored as world boxes. See [V4R-7](../geometry/conventions.md#v4r-7) (draft, PR #166).
- Camera data is not in the release the draft cites. Extrinsics are **unverified**. See [V4R-9](../geometry/conventions.md#v4r-9) (draft, PR #166).
- One stem per 0.1 s is not a source statement of the stem step. See [V4R-11](../geometry/conventions.md#v4r-11) (draft, PR #166).

## Citation

Source: <https://arxiv.org/bibtex/2303.07601>

```bibtex
@misc{xu2023v2v4realrealworldlargescaledataset,
      title={V2V4Real: A Real-world Large-scale Dataset for Vehicle-to-Vehicle Cooperative Perception},
      author={Runsheng Xu and Xin Xia and Jinlong Li and Hanzhao Li and Shuo Zhang and Zhengzhong Tu and Zonglin Meng and Hao Xiang and Xiaoyu Dong and Rui Song and Hongkai Yu and Bolei Zhou and Jiaqi Ma},
      year={2023},
      eprint={2303.07601},
      archivePrefix={arXiv},
      primaryClass={cs.CV},
      url={https://arxiv.org/abs/2303.07601},
}
```

## More detail

- [V2V4Real — details](v2v4real-details.md)
