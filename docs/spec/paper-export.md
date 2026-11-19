# Paper export

`coperceive export paper` writes one zip for Overleaf. It works on a single scored run (`metrics.json` and, for the precision-recall figure, `matches.parquet`). Richer inputs are optional. A missing file is a sentence in `notes.txt` and in the matching `.tex` file.

## Zip

| Path | Contents |
| --- | --- |
| `manifest.json` | `coperceive-paper-export` version 1. `run_ids`, `repro_card_ids`, `inputs`, `notes`, `files`. |
| `tables/metrics.tex` | booktabs table. siunitx `S` columns. |
| `tables/comparison.tex`, `tables/breakdowns.tex` | Breakdown tables when a comparison file is present. |
| `tables/failures.tex` | Worst frames when a failure file is present. |
| `figures/pr.pdf`, `figures/pr.svg` | Precision-recall curves. |
| `figures/breakdown.pdf`, `figures/breakdown.svg` | Grouped bars for the first breakdown. |
| `figures/degradation.pdf`, `figures/degradation.svg` | Degradation lines. |
| `qualitative/` | Bird's-eye and camera PNG and PDF for allowed frames. |

Numbers use a fixed number of decimal places. Bold marks the best value in a column of the metric table, and the best run in each breakdown row. The palette is Wong, Nature Methods 8, 441 (2011).

## Optional inputs

Discovered next to the run when `--run` and `--predictions` are set, or passed explicitly. An explicit file with the wrong `format` stops the export. A discovered file with the wrong format is ignored.

| File | Format | Producer |
| --- | --- | --- |
| `compare.json` | `coperceive-compare` version 1 | `coperceive eval compare --json` (draft, not on this branch) |
| `failures.json` | `coperceive-failures` version 1 | `coperceive eval failures --json` (draft, not on this branch) |
| `degradation.json` | `coperceive-degradation` version 1 | Not written yet. A robustness sweep can drop this file. |
| `repro-card.json` or `repro-cards/*.json` | `coperceive-repro-card`, id `card-` plus 12 hex digits | `coperceive repro export` (draft, not on this branch) |
| `bookmarks.json` | `{ "frames": [ { "scene_id", "time_us", "agent_id", "sensor_id" } ] }` | Bookmark list, when that feature is present |

`coperceive-degradation` is:

```json
{
  "format": "coperceive-degradation",
  "format_version": 1,
  "x_label": "Latency (frames)",
  "y_label": "AP",
  "series": [
    {"name": "model", "points": [{"level": 0, "value": 0.7}, {"level": 2, "value": 0.4}]}
  ]
}
```

A flat series is also accepted: objects with `name` or `label`, `level`, and `value`.

## Qualitative frames

The dataset id is read from the scene manifest (`dataset` string or `dataset.id`). When that key is absent, a clock note that says fixture or synthetic is a fixture. The map name is not a dataset id.

The allow list defaults to `fixture` and `synthetic`. `griffin` and `carla` are drawn only when `--allow-dataset` names them (or the paper dialog's Allow Griffin / Allow CARLA boxes). `opv2v`, `v2xset`, and `v2v4real` are never drawn, including when someone asks for them. The licence decision for the other datasets is still open, so they stay off unless a person opts in for that export.

Frames are the matplotlib bird's-eye and camera overlay, the same figures as `export bev` and `export overlay`, in the light theme, with the existing legend. They are written into the zip at export time and are not stored in the repository.
