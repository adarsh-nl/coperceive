# OPV2V

OPV2V is a simulated vehicle-to-vehicle benchmark: the catalog calls the agents "vehicle + vehicle", and the adapter types every discovered agent directory as `vehicle`. Coperceive views an extracted tree, checks it, writes schema 1 scenes, and can score a prediction run against those scenes.

## Overview

| | |
| --- | --- |
| Paper | [arXiv:2109.07644](https://arxiv.org/abs/2109.07644) (`coperceive/catalog/datasets.yaml`) |
| Download | [DerrickXuNu/OPV2V](https://github.com/DerrickXuNu/OPV2V) (catalog `source_url`). Dataset page: [mobility-lab.seas.ucla.edu/opv2v](https://mobility-lab.seas.ucla.edu/opv2v/) (manifest `licence.links`) |
| Official code | [DerrickXuNu/OpenCOOD](https://github.com/DerrickXuNu/OpenCOOD) (manifest `licence.links`) |
| Licence | not recorded (catalog `licence: unknown`) |
| Splits | `train`, `validate`, `test`, `test_culver_city` (`opv2v.yaml` `params.split.allowed`) |
| Adapter | `opv2v` |

The catalog status is `local-import`. Its notice says to choose a folder you already have. Nothing is downloaded.

## What Coperceive reads

| | |
| --- | --- |
| Agents | One directory per agent under `{split}/{scene}/{agent}/`, type `vehicle` |
| LiDAR | `{frame:06d}.pcd`, fields `x y z`. Intensity is filled with `0.0`. A missing cloud is skipped |
| Cameras | `{frame:06d}_{sensor}.png` for each `camera*` record. A missing file does not fail the scene (`require_file: false`) |
| Boxes and classes | `vehicles` records in the world frame. There is no class field; every box is filled as `vehicle` |
| Maps | Not in the manifest. The town is not stored in the files; see [CARLA maps](carla-maps.md) |
| Timestamps | `time_us = stem × 50000`. The files hold no wall clock (`clock.scale_note`) |

## Expected folder layout

From `coperceive/adapters/builtin/opv2v.yaml` (`scenes.glob` and `streams`):

```text
/data/opv2v/
  {split}/{scene}/{agent}/
    {frame:06d}.yaml
    {frame:06d}.pcd
    {frame:06d}_{sensor}.png
```

`{split}` is one of the four names above.

## Open it in Coperceive

**Open Folder**, or the cluster browser. Get Data does not download this dataset.

```bash
coperceive adapters check opv2v --raw-root /data/opv2v --param split=test
coperceive adapters ingest opv2v --raw-root /data/opv2v --param split=test --out /data/opv2v-scenes
coperceive validate /data/opv2v-scenes
coperceive eval run RUN_ID --root /data/opv2v-scenes
```

Those flags are `coperceive adapters check --help`, `coperceive adapters ingest --help`, `coperceive validate --help`, and `coperceive eval run --help`. There is no `coperceive import` command.

## Known issues and conventions

The labels below are rows of [Geometry conventions](../geometry/conventions.md).

- Roll and pitch sign: the finding is that non-zero pitch and roll are stored with the wrong sign. See [OPV-6](../geometry/conventions.md#opv-6) (checked on real data (6 frames)) and [OPV-13](../geometry/conventions.md#opv-13).
- Which CARLA version wrote the clouds: **unverified**. See [OPV-2](../geometry/conventions.md#opv-2).
- Intensity is dropped. See [OPV-3](../geometry/conventions.md#opv-3).
- Camera extrinsic direction: the finding is that the stored matrix is inverted. See [OPV-7](../geometry/conventions.md#opv-7) (checked on real data (6 frames)).
- Whether camera blocks change across frames: **unverified**. See [OPV-11](../geometry/conventions.md#opv-11).
- The adapter split `test_culver_city` differs from the documented `test_culvercity`. See [OPV-17](../geometry/conventions.md#opv-17).

## Citation

Source: <https://arxiv.org/bibtex/2109.07644>

```bibtex
@misc{xu2022opv2vopenbenchmarkdataset,
      title={OPV2V: An Open Benchmark Dataset and Fusion Pipeline for Perception with Vehicle-to-Vehicle Communication},
      author={Runsheng Xu and Hao Xiang and Xin Xia and Xu Han and Jinlong Li and Jiaqi Ma},
      year={2022},
      eprint={2109.07644},
      archivePrefix={arXiv},
      primaryClass={cs.CV},
      url={https://arxiv.org/abs/2109.07644},
}
```

## More detail

- [OPV2V and V2XSet — details](opencood.md)
- [Add a dataset](add-a-dataset.md)
