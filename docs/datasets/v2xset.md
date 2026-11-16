# V2XSet

V2XSet is a simulated vehicle-and-infrastructure benchmark: the catalog calls the agents "vehicle + infrastructure", and the adapter types the directory `-1` as `infrastructure` and every other agent as `vehicle`. Coperceive views an extracted tree, writes schema 1 scenes, and can score a prediction run. It does not download the frames.

## Overview

| | |
| --- | --- |
| Paper | [arXiv:2203.10638](https://arxiv.org/abs/2203.10638) (catalog `paper_url`) |
| Download | not recorded as a separate URL. Catalog `source_url`: [DerrickXuNu/v2x-vit](https://github.com/DerrickXuNu/v2x-vit) |
| Official code | [DerrickXuNu/v2x-vit](https://github.com/DerrickXuNu/v2x-vit) (catalog `source_url` and manifest `licence.links`) |
| Licence | not recorded (catalog `licence: unknown`) |
| Splits | `train`, `validate`, `test` (`v2xset.yaml` `params.split.allowed`) |
| Adapter | `v2xset` |

The catalog status is `local-import`.

## What Coperceive reads

The stream rules match `opv2v.yaml` except the agent type and the split list (`v2xset.yaml` header comment, and the geometry draft's V2XSet section).

| | |
| --- | --- |
| Agents | Directories under `{split}/{scene}/{agent}/`. `-1` is `infrastructure`. Any other id is `vehicle` |
| LiDAR | `{frame:06d}.pcd`, fields `x y z`. Intensity is filled with `0.0` |
| Cameras | `{frame:06d}_{sensor}.png` when the YAML has `camera*` keys. A missing file is allowed |
| Boxes and classes | `vehicles` in the world frame. No class field; category is filled as `vehicle` |
| Maps | Not in the manifest. See [CARLA maps](carla-maps.md) |
| Timestamps | `time_us = stem × 50000`. The scale note says the files hold no wall clock |

## Expected folder layout

From `coperceive/adapters/builtin/v2xset.yaml`:

```text
/data/v2xset/
  {split}/{scene}/{agent}/
    {frame:06d}.yaml
    {frame:06d}.pcd
    {frame:06d}_{sensor}.png
```

The roadside unit, when present, is the agent directory `-1`.

## Open it in Coperceive

**Open Folder**, or the cluster browser.

```bash
coperceive adapters check v2xset --raw-root /data/v2xset --param split=validate
coperceive adapters ingest v2xset --raw-root /data/v2xset --param split=validate --out /data/v2xset-scenes
coperceive validate /data/v2xset-scenes
coperceive eval run RUN_ID --root /data/v2xset-scenes
```

## Known issues and conventions

- The OPV2V rows apply, including the pitch and roll sign finding. See [OPV-6](../geometry/conventions.md#opv-6) and [OPV-13](../geometry/conventions.md#opv-13) (draft, PR #166).
- Only the id `-1` is typed as infrastructure. See [V2X-1](../geometry/conventions.md#v2x-1) (draft, PR #166).
- Whether a roadside unit's YAML has `camera*` blocks: **unverified**. See [V2X-2](../geometry/conventions.md#v2x-2) (draft, PR #166).
- The own-LiDAR-hit rule is **unverified** for V2XSet. See [V2X-3](../geometry/conventions.md#v2x-3) (draft, PR #166).
- The 0.05 s tick is **unverified** for V2XSet. See [V2X-4](../geometry/conventions.md#v2x-4) (draft, PR #166).

## Citation

Source: <https://arxiv.org/bibtex/2203.10638>

```bibtex
@misc{xu2022v2xvitvehicletoeverythingcooperativeperception,
      title={V2X-ViT: Vehicle-to-Everything Cooperative Perception with Vision Transformer},
      author={Runsheng Xu and Hao Xiang and Zhengzhong Tu and Xin Xia and Ming-Hsuan Yang and Jiaqi Ma},
      year={2022},
      eprint={2203.10638},
      archivePrefix={arXiv},
      primaryClass={cs.CV},
      url={https://arxiv.org/abs/2203.10638},
}
```

## More detail

- [OPV2V and V2XSet — details](opencood.md)
