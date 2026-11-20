# Griffin

Griffin is a simulated aerial-ground dataset: the catalog describes it as cooperative vehicle and drone perception in CARLA and AirSim, with agents "vehicle + drone". Coperceive can download a preset, convert a release to schema 1 scenes, view them, and score predictions.

## Licence, citation and download

**Licence.** Data licence: MIT, as declared in the [authors dataset card](https://huggingface.co/datasets/wjh-svm/Griffin/blob/main/README.md). Code is separately MIT under [LICENSE.txt](https://github.com/wang-jh18-SVM/Griffin/blob/main/LICENSE.txt).

**Citation.** Use the canonical source and single BibTeX block in [Citation](#citation) below.

**Download.** [Hugging Face](https://huggingface.co/datasets/wjh-svm/Griffin) or the Baidu link in the [authors preparation guide](https://github.com/wang-jh18-SVM/Griffin/blob/main/docs/Dataset_Preparation.md).

The current arXiv record in Citation is canonical here; the README citation is older. Upstream asset terms have not been checked.

## Overview

| | |
| --- | --- |
| Paper | [arXiv:2503.06983](https://arxiv.org/abs/2503.06983) (catalog `paper_url`) |
| Official code | [wang-jh18-SVM/Griffin](https://github.com/wang-jh18-SVM/Griffin), the repository of catalog `licence_url` (`LICENSE.txt`) |
| Splits | No train/validate/test list. Subsets `25m`, `40m`, `55m`, `random` (`griffin.yaml` `params.subset.allowed`). Catalog presets: `dev`, `one-scene`, `full` |
| Adapter | `griffin` |

The catalog status is `ready` and `requires_terms` is true. The download command refuses to write dataset bytes until the MIT acknowledgement is recorded.

## What Coperceive reads

| | |
| --- | --- |
| Agents | `vehicle` (type `vehicle`, root `vehicle-side`) and `drone` (type `uav`, root `drone-side`) |
| LiDAR | Vehicle only: `lidar/lidar_top/{frame:06d}.ply` with `x y z` and intensity property `I`. If that directory is absent, clouds are skipped. Drone LiDAR: not in the release (`griffin.yaml` `streams.lidar.agents` and [GRF-9](../geometry/conventions.md#grf-9)) |
| Cameras | `{agent}/camera/{sensor}/{frame:06d}.png`. A missing file is allowed. The dev tier has no image bytes (`image_size` reason in the manifest) |
| Boxes and classes | Label text in the body frame. 12-, 11-, and 9-field lines. The category token is kept as written |
| Maps | `info.map_name` is copied onto the platform record. The map file itself is not in this adapter |
| Timestamps | Pose `timestamp` is the frame id. `time_us = frame_id × 100000`. The scale note says the files have no wall clock |

## Expected folder layout

From `coperceive/adapters/builtin/griffin.yaml`:

```text
/data/griffin/griffin-release/
  vehicle-side/
    scene_infos.json
    pose/{frame:06d}.json
    calib/{sensor}.json
    label/{frame:06d}.txt
    camera/{sensor}/{frame:06d}.png
    lidar/lidar_top/{frame:06d}.ply
  drone-side/
    scene_infos.json
    pose/{frame:06d}.json
    calib/{sensor}.json
    label/{frame:06d}.txt
    camera/{sensor}/{frame:06d}.png
```

`drone-side/scene_infos.json` must equal the vehicle file (`scenes.index.must_equal`).

## Open it in Coperceive

**Get Data** lists Griffin because the catalog status is `ready`. The download and convert commands are:

```bash
coperceive download griffin --subset 25m --preset dev --dest /data/griffin --accept-griffin-terms
coperceive convert griffin --subset 25m --raw-root /data/griffin/griffin-release --out /data/griffin-scenes
coperceive validate /data/griffin-scenes
coperceive eval run RUN_ID --root /data/griffin-scenes
```

`--subset` is `25m`, `40m`, `55m`, or `random`. `--preset` for download is `dev` (`coperceive download griffin --help`). `--raw-root` is the extracted `griffin-release` directory (`coperceive convert griffin --help`).

The same tree can be ingested with `coperceive adapters ingest griffin --raw-root /data/griffin/griffin-release --param subset=25m --out /data/griffin-scenes`.

## Known issues and conventions

- The adapter requires a pose key `timestamp` that no source documents. See [GRF-3](../geometry/conventions.md#grf-3).
- Image size is `2 × cx` by `2 × cy`, not a measured PNG, unless the principal point is the centre, which no source states. See [GRF-6](../geometry/conventions.md#grf-6).
- Whether stored points are already in the ego frame: **unverified**. See [GRF-7](../geometry/conventions.md#grf-7).
- The preparation note says `.bin`; the adapter reads `.ply` and requires a property `I`. Record layout: **unverified**. See [GRF-8](../geometry/conventions.md#grf-8).
- Category letter case: **unverified**. See [GRF-13](../geometry/conventions.md#grf-13).
- `info.map_name`, `info.cruise_altitude`, and an identical drone-side `scene_infos.json` are required by the adapter and are not in the sources the conventions page read. See [GRF-14](../geometry/conventions.md#grf-14).

## Citation

Source: <https://arxiv.org/bibtex/2503.06983>

```bibtex
@misc{wang2025griffinaerialgroundcooperativedetection,
      title={Griffin: Aerial-Ground Cooperative Detection and Tracking Dataset and Benchmark},
      author={Jiahao Wang and Xiangyu Cao and Jiaru Zhong and Yuner Zhang and Zeyu Han and Haibao Yu and Chuang Zhang and Lei He and Shaobing Xu and Jianqiang Wang},
      year={2025},
      eprint={2503.06983},
      archivePrefix={arXiv},
      primaryClass={cs.CV},
      doi={https://doi.org/10.1609/aaai.v40i12.37951},
      url={https://arxiv.org/abs/2503.06983},
}
```

## More detail

- [Griffin — details](griffin-details.md)
