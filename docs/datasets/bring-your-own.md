# Bring your own (generic)

A generic dataset is any set of viewpoints you describe with one `scene.yaml`. Agents can be vehicles, roadside units, drones, or fixed sensors. Coperceive checks that file, ingests it to schema 1, and can view and score the result. It does not download anything, and it does not invent a licence.

## Overview

| | |
| --- | --- |
| Paper | none |
| Download | none. The files are yours |
| Official code | `coperceive/adapters/generic.py` and [the format page](../spec/generic-multiview.md) |
| Licence | not recorded. The generic format is not an entry in `coperceive/catalog/datasets.yaml` |
| Splits | none in the format |
| Adapter | `generic` |

## What Coperceive reads

From `docs/spec/generic-multiview.md`. Only paths named in `scene.yaml` are read.

| | |
| --- | --- |
| Agents | The `agents` list. A role containing `vehicle`, `uav` or `drone`, or `infrastructure`, `roadside`, or `fixed` sets the schema type. Anything else is `other` |
| LiDAR | PCD with `x y z`, or a float32 `.bin` of `x y z i` records, at the `files` template |
| Cameras | PNG or JPEG, with `intrinsic: [fx, fy, cx, cy]` |
| Boxes and classes | Optional `boxes.csv` in the world frame. No boxes is valid |
| Maps | Not in the format |
| Timestamps | `timestamps_us`, a `timestamp_file`, or `frame_rate_hz` plus `frame_count` |

## Expected folder layout

The format page says the `agents/` tree is one layout, not a requirement. Templates are relative to the dataset root and contain `{frame:06d}`:

```text
/data/mine/
  scene.yaml
  agents/<agent_id>/
    poses.csv
    boxes.csv
    camera/{frame:06d}.png
    lidar/{frame:06d}.pcd
```

Two synthetic examples live in the repository: `examples/generic/driving/` and `examples/generic/inspection/`. They are fixtures, not a published dataset.

## Open it in Coperceive

**Open Folder**.

```bash
coperceive adapters check --format generic /data/mine
coperceive adapters ingest generic --raw-root /data/mine --out /data/mine-scenes
coperceive validate /data/mine-scenes
coperceive eval run RUN_ID --root /data/mine-scenes
```

`adapters check --help` lists `--format {generic}`. `adapters ingest` takes the built-in name `generic`.

## Known issues and conventions

- The adapter names the world ENU. The spec does not. See [GEN-1](../geometry/conventions.md#gen-1) (draft, PR #166).
- The frame of LiDAR points: **unverified** in the spec. See [GEN-6](../geometry/conventions.md#gen-6) (draft, PR #166).
- The fourth `.bin` value is not read. See [GEN-7](../geometry/conventions.md#gen-7) (draft, PR #166).
- Box centre, length axis, and yaw sign: **unverified**. See [GEN-8](../geometry/conventions.md#gen-8) and [GEN-9](../geometry/conventions.md#gen-9) (draft, PR #166).
- `handedness: left` is specified to reflect points. The adapter does not. See [GEN-10](../geometry/conventions.md#gen-10) (draft, PR #166).

## Citation

Citation: see the paper link. There is no paper. The format is [Generic multi-view](../spec/generic-multiview.md).

## More detail

- [Generic format](../spec/generic-multiview.md)
- [Add a dataset](add-a-dataset.md)
- [Demo (synthetic)](demo.md)
