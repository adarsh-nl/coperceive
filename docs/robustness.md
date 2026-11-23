# Robustness sweeps

`coperceive robustness sweep` runs one model over one scene split under a grid of seeded
input perturbations and writes one `sweep.json` with the mAP of every cell, the mean and
standard deviation over seeds, and the degradation data to chart. Every cell is literally a
[`coperceive model run --perturb`](models.md) of the same scenes followed by
[`coperceive eval run`](eval.md) under every eval config, so the run directories stay
ordinary predictions runs and the per-scene cache is reused naturally. Ground truth is never
perturbed; the definitions are [the perturbation spec](spec/perturbations.md).

```bash
coperceive demo create /tmp/demo
coperceive robustness sweep sweep.yaml --manifest tests/fake_model_plugin/sweep-model.yaml \
  --scenes /tmp/demo/scenes --out /tmp/sweep --run-id demo
```

The numbers in this page come from the demo scene and the test fixture plugin
(`tests/fake_model_plugin/sweep-model.yaml`, whose `degrade` model reports the ground truth
in the pose the frame received). They are **demo/fixture numbers, not model results**; a real
detector's numbers come from its own manifest and scenes.

## The sweep definition

The sweep definition is YAML (or JSON) and is passed as the positional argument: a file path
when it names an existing file, otherwise the text itself. It is a flat mapping of
perturbation axes to value lists plus the seeds:

```yaml
# A small demo grid: 2 axes x 2 values, 2 seeds = 8 cells (under the 64-cell default).
pose_noise_xy: [0.0, 1.0]     # metres, standard deviation on world x and y
latency_frames: [0, 1]        # frames of delay
seeds: [0, 1]                 # one model run per seed
```

Every listed value is checked by `PerturbationSpec` (the [perturbation spec](spec/perturbations.md)),
so an out-of-range value is refused with the axis and value in the message:

| axis | unit | accepted values |
| --- | --- | --- |
| `latency_frames` | frames | integer, `>= 0` |
| `latency_ms` | ms | number, `>= 0` |
| `packet_loss` | fraction | number in `[0, 1]` |
| `pose_noise_xy` | m | number, `>= 0` (standard deviation on world x and y) |
| `pose_noise_yaw` | deg | number, `>= 0` (standard deviation about world z) |
| `max_agents` | agents | integer, `>= 1` (includes the ego) |
| `agent_dropout` | fraction | number in `[0, 1]` |

The definition has these rules:

- The axes are read in the canonical perturbation order, not YAML order; values are sorted
  ascending. A repeated value or a repeated seed is an error.
- `seeds` is optional and defaults to `[0]`. Seeds are integers `>= 0`; a seed that appears
  twice is an error.
- The grid is the cartesian product of the axes, and the sweep's cells are that product
  crossed with the seeds (the demo grid above: 2 axes x 2 values = 4 combinations, x 2
  seeds = 8 cells). The cell order is fixed: combinations in axis order, then seeds
  ascending. The same definition always plans the same cells in the same order.
- At least one axis is required; an empty definition, an empty axis list and an empty seed
  list are errors.
- A grid over `--max-cells` (default **64**, counting combinations x seeds) is refused with
  the count in the message: `the sweep has 108 cells (36 perturbation combinations x 3
  seeds), over the limit of 64; reduce the grid or raise it with --max-cells`.

Inline definitions work too:

```bash
coperceive robustness sweep 'pose_noise_xy: [0.2, 0.5, 1.0]
seeds: [0, 1, 2]' --manifest det.yaml --scenes /data/scenes --out /data/sweeps --run-id noise
```

## Results

Each cell gets a deterministic run id: `<run-id prefix>-<digest>` (the digest is opaque;
the examples below print it as `<digest>`), where the digest covers the resolved `--scenes`
root, the manifest's SHA-256, the model key, the canonical spec and every option that
changes the predictions (agents, source, scene ids, raw root, media roots, `--option`). The
id is therefore per scene split: the same `--out` and `--run-id` over a different split
plans different cells, so it never reuses another split's run directories. Re-running the
same sweep therefore reuses each cell's run directory:

- a plain re-run finds every scene already written and skips it (`skipped` in the per-cell
  progress line; no model work and no evaluation rewrite of the boxes);
- `--force` re-emits the cells but answers every scene from the per-scene cache: the
  progress line shows `1 cached` and `run.json` shows `cache_hits: 1, frames_run: 0`.

Changing an option, the scene split or a cell's spec changes its run id, so two different
predictions never share a run directory. Because the cells are plain runs, `coperceive eval report`,
`coperceive eval compare`, the viewer and `coperceive repro export` keep working on them.

The console prints one line per cell and a summary. This is the demo/fixture output of a
small two-cell sweep:

```text
cell 1/2: run sweep-<digest> (pose_noise_xy=0, seed=0) default mAP@0.5 all 1.000 [1 run, 0 skipped, 0 cached]
cell 2/2: run sweep-<digest> (pose_noise_xy=1, seed=0) default mAP@0.5 all 0.536 [1 run, 0 skipped, 0 cached]
wrote /tmp/sweep/sweep.json
cells 2 (2 perturbation combinations x 1 seed), eval 1
```

`mAP@<t> all` is the pooled AP that [`eval report`](eval.md) prints as its headline: one
precision/recall walk per class over every scene of the cell, with the ground-truth count
summed over scenes, then the mean over the classes that have a value — the `mAP@<t> all`
column of `metrics.json`. A cell's headline for the charts is its `display_threshold`
(`0.5` in the default config).

## Eval configs

Without `--config` the sweep evaluates every cell with the eval spec's default config,
recorded as `default`. `--config [NAME=]FILE` is repeatable: each file is recorded under
`NAME` (the file stem when no name is given) with its full config in `sweep.json`:

```bash
coperceive robustness sweep sweep.yaml --manifest det.yaml --scenes /data/scenes \
  --out /data/sweeps --run-id strict \
  --config strict=eval-strict.yaml --config eval.yaml
```

A cell's `eval` object then has one entry per config name, each with `criterion`, the
`thresholds`, the `mAP` values keyed by their `metrics.json` field name, and the headline
`mAP_display`. The evaluation files are written into the cell's run directory; because
`metrics.json` is one file per scene, the **last** config's evaluation is the one left on
disk in the run directory — `sweep.json` keeps every config's numbers.

## `sweep.json`

The file is written to `<out>/sweep.json` and its contract is
[`docs/spec/robustness-sweep.schema.json`](spec/robustness-sweep.schema.json): format
`coperceive-robustness-sweep`, version `1`. It has no wall-clock timestamp, so the same
command on the same inputs writes the same bytes.

```json
{
  "format": "coperceive-robustness-sweep",
  "format_version": 1,
  "model": {"plugin": "fake", "model": "degrade", "manifest": ".../sweep-model.yaml",
            "manifest_sha256": "<sha256 of the manifest file>"},
  "dataset": {"scenes_root": "/tmp/demo/scenes", "scene_ids": null},
  "eval": [{"name": "default", "path": null, "config": {"criterion": "iou_bev", "...": "..."}}],
  "grid": {
    "axes": [{"name": "pose_noise_xy", "unit": "m", "values": [0.0, 1.0]}],
    "seeds": [0],
    "combinations": 2,
    "cells": 2
  },
  "cells": [
    {"index": 0, "run_id": "sweep-<digest>", "canonical": "pose_noise_xy=0,seed=0",
     "spec": {"seed": 0, "latency_frames": null, "latency_ms": null, "packet_loss": null,
              "pose_noise_xy": 0.0, "pose_noise_yaw": null, "max_agents": null,
              "agent_dropout": null},
     "eval": {"default": {"criterion": "iou_bev", "thresholds": [0.5, 0.7],
               "mAP": {"mAP@0.5 all": 1.0, "mAP@0.7 all": 1.0},
               "mAP_display": {"threshold": 0.5, "field": "mAP@0.5 all", "value": 1.0}}}},
    {"index": 1, "run_id": "sweep-<digest>", "canonical": "pose_noise_xy=1,seed=0",
     "spec": {"seed": 0, "...": "..."},
     "eval": {"default": {"...": "...", "mAP_display": {"value": 0.536, "...": "..."}}}}
  ],
  "aggregates": [{"index": 0, "spec": {"seed": null, "...": "..."},
                  "eval": {"default": {"mAP_display": {"threshold": 0.5,
                            "field": "mAP@0.5 all", "mean": 0.768, "std": 0.0, "n": 1}}}}],
  "charts": [{"axis": "pose_noise_xy", "unit": "m", "eval": "default",
              "metric": "mAP@0.5 all", "values": [0.0, 1.0],
              "baseline": {"threshold": 0.5, "field": "mAP@0.5 all", "mean": 1.0,
                           "std": 0.0, "n": 1},
              "points": [{"value": 0.0, "mean": 1.0, "std": 0.0, "n": 1},
                         {"value": 1.0, "mean": 0.536, "std": 0.0, "n": 1}]}]
}
```

The fields a reader or the chart UI needs:

- **`cells`** — one per cell, sorted: `index`, `run_id`, `canonical` (the `key=value` spec of
  the perturbation), the full `spec` (including `seed`), and `eval` per config name.
- **`aggregates`** — one per perturbation combination: the same `spec` shape with
  `seed: null`, and per eval config `mAP` per threshold plus `mAP_display` as
  `{mean, std, n}`. `std` is the sample standard deviation over the seeds (`n - 1`, `0.0`
  when a combination has one seed); `n` counts the cells with a value, `0` with `null` mean
  when a metric is unavailable.
- **`charts`** — the degradation data for the v0.1.2 chart. One entry per eval config and
  per swept axis: `values` is the full axis domain, `points` one `{value, mean, std, n}` per
  axis value, `baseline` the all-neutral point (`mean`/`std`/`n` for the combination where
  every swept axis is 0) and `metric` the `metrics.json` field the means come from. The
  points are the slice where **every other swept axis is at its neutral value 0**, so a
  grid that does not contain 0 for the other axes has fewer (or no) points for that axis.
  Axes whose value cannot be 0 are not chart axes: `max_agents` is excluded because its
  valid range starts at 1, so a grid that sweeps it has no neutral 0 slice for the others
  and no chart entries from that grid. The complete grid is always in `aggregates`.

## Slurm

`--slurm` emits **one sbatch per cell** (not a job array) under `<out>/.slurm/` and prints
each script; nothing is submitted and `--submit` does not exist. Each script is the
ordinary [`model run --slurm`](models.md) plan for the cell's spec: it `rsync -a
--files-from` stages exactly the scene files (and raw/checkpoint files when the model needs
them) into the cluster profile's scratch template, runs the same `coperceive model run
--perturb ...` on the node, and copies the run home before the trap wipes the scratch —
results never live in the scratch. The site values are the same as `model run --slurm`:
`--slurm-python`, `--slurm-partition` (or `COPERCEIVE_SLURM_PARTITION`),
`--slurm-scratch-template` (or `COPERCEIVE_SLURM_SCRATCH_TEMPLATE`, normally
`/scratch/$USER/$SLURM_JOB_ID`), `--slurm-logs` and `--slurm-results`.

After the jobs copy their runs home, run the same command **without** `--slurm`: every cell
is already complete, so the sweep only evaluates and writes `sweep.json`.

## Limitations

- The sweep evaluates one scene split with one eval config set; it does not compare models.
  For a two-run delta use `coperceive eval compare` on the cell run directories.
- `sweep.json` has the pooled mAP only. Distance, occlusion, class and per-scene breakdowns
  are in the cell runs' `metrics.json` and `coperceive eval compare --by`.
- Slurm mode emits one script per cell; a site with many cells should submit them with its
  own scheduler policy, and coperceive does not track those jobs.
- A chart slice needs the neutral value 0 in every other swept axis; the full grid is always
  available in `aggregates`.
- The per-cell run id is stable but opaque; the mapping from a cell to its run is in
  `sweep.json` (`cells[].run_id`), not the other way round.
