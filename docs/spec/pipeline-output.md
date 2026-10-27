# Pipeline output contract — `coperceive-pipeline-1`

This is the open contract between a registered pipeline and coperceive. Any model
can implement it: a checkpoint adapter, a baseline, a paper reproduction. The
contract is deliberately small and is **not** tied to OpenCOOD, Griffin, py123d or
any other codebase.

A pipeline is a user-registered script plus a typed argument schema (see
`docs/spec/pipeline.schema.json` and ADR 0015). A lab can carry the registration
next to the script as **`coperceive-pipeline.json`** (same JSON, `script` optional:
the sibling script's name is used), so one file plus the script can be shared.
Arguments may declare a `role` (`checkpoint`, `test_data`, `config`, `output`,
`other`) so the app can offer a picker backed by `coperceive remote scan`; roles are
metadata and the value is still passed as one argv element. coperceive starts it with
`<interpreter...> <script> --<arg> <value> ... --output-dir <dir>`, passing only
argv elements, and reads two files from `<dir>`:

- `predictions.jsonl` — streamed while the run progresses;
- `metrics.json` — written once at the end.

Nothing else is required. A pipeline may write any other files it likes in
`<dir>`; coperceive ignores them.

## `predictions.jsonl`

One JSON object per line, one line per predicted box. The object uses the
**`boxes.parquet` columns of `docs/spec/predictions.md`**, unchanged:

| Key | Type | Required | Meaning |
| --- | --- | --- | --- |
| `agent_id` | string | yes | An agent of the scene. |
| `source` | string | yes | Which pipeline output this is; keys `metrics.json` `outputs`. |
| `time_us` | integer | yes | A pose time of that agent. |
| `category` | string | yes | Class label in the scene's ground-truth vocabulary. |
| `score` | number | yes | Finite confidence; higher is more confident. |
| `track_id` | string or null | yes (may be null) | Tracker id. |
| `cx`, `cy`, `cz` | number | yes | Box centre in metres, in `frame`. |
| `length`, `width`, `height` | number | yes | Positive box size in metres. |
| `qw`, `qx`, `qy`, `qz` | number | yes | Unit quaternion (norm within 1e-6), pure rotation. |
| `frame` | string | yes | `body` (agent body at `time_us`) or `world`. |

Rules:

- Lines are `\n`-terminated UTF-8 JSON objects; a malformed line is an error the
  pipeline must not emit (coperceive reports the line number).
- Order is free. coperceive sorts by `(agent_id, time_us)` when importing.
- Additional keys are ignored by the importer but discouraged; they are not
  persisted.
- An optional `scene_id` key names the scene per row. If it is absent, the run is
  started for one scene and coperceive attaches that scene id.
- Boxes may be in `body` or `world` frame. `body` boxes are placed with the
  agent pose at `time_us` by the importer/evaluator; `time_us` must then be a pose
  sample of that agent (`max_gap_us = 0`).
- Lines should be flushed as they are produced (line buffering) so the viewer
  streams them live.

### Progress line

A progress line is a JSON object with a `type` key:

```json
{"type": "progress", "scenes_done": 2, "scenes_total": 5, "frames_done": 300, "frames_total": 745}
```

`type` must be `"progress"`. Any subset of the counters is allowed; they are
non-negative integers and must not decrease. coperceive shows them but never
fails a run because progress is missing. A pipeline that writes only predictions
is valid.

## `metrics.json`

Written once when the pipeline finishes, before it exits 0:

```json
{
  "schema": "coperceive-pipeline-metrics",
  "version": 1,
  "run": {"model": "bev-fusion", "checkpoint": "ckpt.pt", "scenes": ["Town03-000"]},
  "outputs": [
    {"agent_id": "vehicle", "source": "bev-fusion", "boxes": 1490, "scenes": 1}
  ],
  "metrics": {"latency_ms_per_frame": 41.2},
  "notes": "optional free text"
}
```

Rules:

- `schema` must be `"coperceive-pipeline-metrics"` and `version` must be `1`.
- `outputs` is a list of `{agent_id, source, boxes, scenes}`; `source` values must
  match the `source` keys in `predictions.jsonl`.
- `metrics` is a free object of numbers or strings for the user's own reporting;
  coperceive displays it but does not interpret it.
- If the pipeline exits non-zero, coperceive keeps whatever was written and marks
  the run partial; a missing `metrics.json` marks it incomplete. It never treats
  a partial run as complete.

## `metrics.json` is the pipeline's report, not the score

`metrics.json` is free-form reporting owned by the pipeline. coperceive's score
for a run comes from `coperceive eval run`, which matches the imported predictions
against the scene ground truth (ADR 0010). The app shows the pipeline's metrics as
context but never uses them as the score and never accepts them as ground truth.

## Converting to a stored run

```
coperceive predictions import predictions.jsonl \
  --run <run_id> --root <scenes root> --scene <scene_id> \
  --score-note "the pipeline's score meaning" --model <name>
```

`coperceive predictions import` validates every row against the scene (the same
checks as `eval run`), writes `boxes.parquet` and `run.json`, and refuses to
overwrite an existing scene unless `--force`. `--source-note NAME=TEXT` fills the
source note. A streaming pipeline can be imported while it runs; a final import
after exit replaces nothing unless asked.

## Reference bytes

The importer reads the file incrementally; it does not load a whole run into
memory. A size cap is not specified here — a pipeline producing millions of boxes
should write per-scene files and be imported per scene.
