# DAIR-V2X-C

DAIR-V2X-C is the real-world cooperative subset of DAIR-V2X: the catalog calls the agents "vehicle + infrastructure", and the adapter builds one `vehicle` agent and one `infrastructure` agent. Coperceive reads an extracted copy, writes schema 1 scenes, and can score predictions. It does not download the sensor files.

## Licence, citation and download

**Licence.** Data licence: not stated by the authors in the linked README. Repository software licensing is not an explicit data grant. The repository LICENSE is Apache-2.0.

**Citation.** Use the canonical source and single BibTeX block in [Citation](#citation) below.

**Download.** [Project page](https://thudair.baai.ac.cn/index) or the authors [Google Drive folder](https://drive.google.com/drive/folders/1gnrw5llXAIxuB9sEKKCm6xTaJ5HQAw2e?usp=sharing).

Data access requires an application according to the catalog comment. Project-page access and terms have not been verified; consult that page before use.

## Overview

| | |
| --- | --- |
| Paper | [arXiv:2204.05575](https://arxiv.org/abs/2204.05575), linked from the [AIR-THU/DAIR-V2X README](https://github.com/AIR-THU/DAIR-V2X/blob/main/README.md). The catalog `paper_url` is [arXiv:2205.08540](https://arxiv.org/abs/2205.08540), which is a different paper |
| Official code | [AIR-THU/DAIR-V2X](https://github.com/AIR-THU/DAIR-V2X) (README title and code link) |
| Splits | `train`, `val` (`DairV2XCAdapter` `SPLITS`) |
| Adapter | `dair-v2x-c` |

The catalog status is `local-import`.

## What Coperceive reads

From `coperceive/adapters/dair_v2x_c.py` `load`. The test tree in `tests/test_adapters_dair_v2x_c.py` is synthetic and is not a measurement of the release.

| | |
| --- | --- |
| Agents | `vehicle` and `infrastructure` |
| LiDAR | One PCD per side. Fields `x y z`. Intensity is filled with `0.0` |
| Cameras | Not read. `load` builds no camera sensor and no image rows |
| Boxes and classes | Cooperative labels' `world_8_points` and `type`, stored in the world frame. The track id is the list index |
| Maps | Not read |
| Timestamps | `pointcloud_timestamp` stored as an integer. The clock note says microseconds. The unit is **unverified**. See [DAIR-14](../geometry/conventions.md#dair-14) |

Drone LiDAR is not part of this dataset. The two LiDARs are the vehicle LiDAR and the infrastructure virtual LiDAR.

## Expected folder layout

Paths the adapter opens (`_layout`, `_rows`, `load`) and that `tests/test_adapters_dair_v2x_c.py` builds. The test is a fixture, not the release's file count.

```text
/data/dair-v2x-c/
  cooperative-vehicle-infrastructure/
    cooperative/data_info.json
    vehicle-side/data_info.json
    vehicle-side/velodyne/{frame}.pcd
    infrastructure-side/data_info.json
    infrastructure-side/velodyne/{frame}.pcd
  DAIR-V2X-C_Complemented_Anno/
    train.json
    val.json
    new_labels/cooperative_label/label_world/{frame}.json
```

The published tree, including camera and calibration directories the adapter does not read, is the data README: <https://github.com/yujmo/DAIR-V2X/blob/main/data/README.md>.

## Open it in Coperceive

**Open Folder**, or the cluster browser.

```bash
coperceive adapters check dair-v2x-c --raw-root /data/dair-v2x-c --param split=train
coperceive adapters ingest dair-v2x-c --raw-root /data/dair-v2x-c --param split=train --out /data/dair-v2x-c-scenes
coperceive validate /data/dair-v2x-c-scenes
coperceive eval run RUN_ID --root /data/dair-v2x-c-scenes
```

`coperceive dataset prepare --help` lists `dair-v2x-c` as an `--adapter` choice. The adapter raises an error if `split` is missing or is not `train` or `val`.

## Known issues and conventions

- Pairing the two sides by equal file stem, and the complemented-annotation tree: the draft marks that tree **unverified**. See [DAIR-1](../geometry/conventions.md#dair-1) (draft, PR #166).
- World handedness and units: **unverified**. See [DAIR-2](../geometry/conventions.md#dair-2) (draft, PR #166).
- Calibration JSON nesting: **unverified**. See [DAIR-5](../geometry/conventions.md#dair-5) (draft, PR #166).
- Point-cloud encoding: **unverified**. `binary_compressed` is refused and intensity is dropped. See [DAIR-8](../geometry/conventions.md#dair-8) (draft, PR #166).
- Corner order: the draft's finding is that length and width are swapped and the heading is turned. See [DAIR-10](../geometry/conventions.md#dair-10) (draft, PR #166).
- Cameras are not ingested. In-plane camera axes and `cam_D`: **unverified**. See [DAIR-13](../geometry/conventions.md#dair-13) (draft, PR #166).

## Citation

Source: the Citation section of <https://github.com/AIR-THU/DAIR-V2X/blob/main/README.md>. The same section also cites V2X-Seq, which is a different dataset. The block below is the DAIR-V2X entry only.

```bibtex
@inproceedings{dair-v2x,
  title={Dair-v2x: A large-scale dataset for vehicle-infrastructure cooperative 3d object detection},
  author={Yu, Haibao and Luo, Yizhen and Shu, Mao and Huo, Yiyi and Yang, Zebang and Shi, Yifeng and Guo, Zhenglong and Li, Hanyu and Hu, Xing and Yuan, Jirui and Nie, Zaiqing},
  booktitle={Proceedings of the IEEE/CVF Conference on Computer Vision and Pattern Recognition},
  pages={21361--21370},
  year={2022}
}
```

## More detail

- [DAIR-V2X-C — details](dair-v2x-c-details.md)
