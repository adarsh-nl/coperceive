# Evaluate predictions

Put a model's boxes beside schema 1 scenes, match them to the ground truth, and read the table. The screenshot is the evaluation page with synthetic fixtures.

## Steps

1. Import boxes. `coperceive predictions import` writes `run.json` and one `boxes.parquet` per scene. `--score-note` is required:

    ```bash
    coperceive predictions import boxes.jsonl --run det-v1 --root /data/demo/scenes \
      --score-note "sigmoid of the class logit"
    ```

2. Match and report:

    ```bash
    coperceive eval run det-v1 --root /data/demo/scenes
    coperceive eval report det-v1 --root /data/demo/scenes
    ```

3. Compare two to four run ids with `coperceive eval compare`. The help text says two runs keep the b − a table.

## Score a model run in one step

`coperceive model run` writes `run.json` and `boxes.parquet`; on its own it does not score. Add `--evaluate` and it scores the run as step 2's `eval run` would, on the same scenes and predictions, under the run's protocol:

```bash
coperceive model run <plugin> --scenes /path/to/scenes --out /path/to/predictions \
  --run-id my-run --protocol default --evaluate
coperceive eval report my-run --root /path/to/scenes --predictions /path/to/predictions
```

- `--eval-config PATH` passes an evaluation config (as `eval run --config`). Its `protocol` must agree with `--protocol`; a disagreement or a config that does not load is an error before the model runs.
- With `--slurm`, scoring is a step of the same job, after the run has been copied home, on the scenes the job staged; `metrics.json` comes home with the predictions.
- The run directory gains `evaluation.json`: that scoring ran, the protocol, and the SHA-256 of the config it used ([Predictions, `evaluation.json`](../spec/predictions.md#evaluationjson)). `eval run` writes the same file.
- Exit codes: the model's own (`0`, `1`, `3`, `4`) always wins; `5` means the run completed and scoring failed ([model-plugin spec §Exit codes](../spec/model-plugin.md#exit-codes)). A partial run is still scored on its finished scenes.

**In the desktop app:** New evaluation's **Score the run after it finishes** (on by default for a built-in model) adds the same scoring step to the app's job, under the AP protocol chosen above. The step runs after the model and before the job exits, on the run's own scenes and output directory (the app's run is written in place, so nothing is copied home). The job watch then reads the run's `evaluation.json`:

- **Scored.** It shows the AP protocol and the number of scenes scored.
- **Model finished, scoring failed.** The job exited 5. The panel shows the record's error, or, when the run left no `run.json`, the stderr tail.
- **Not scored: the model did not finish.** The job was a model failure or a timeout, and it keeps the existing failure panel.

The app does not show AP numbers yet: per-scene AP is in each scene's `metrics.json`, and the run's pooled AP is what `eval report` prints.

## AP protocol

The same predictions score differently under different AP protocols, so the protocol is chosen **per run and recorded with it**. There are two:

- **Default**: OpenCOOD-style: per-frame ranking, ground truth from every connected agent within 70 m.
- **Legacy (v0.1.1)**: for numbers comparable to v0.1.1 (pooled ranking, each output against its own agent's ground truth).

On the command line, `coperceive eval run <run> --root <scenes> --protocol legacy` (or `protocol: legacy` in the eval YAML) selects one. In the app, **New evaluation → AP protocol** records the choice in the run plan and passes it to `model run`; the run then records it in `run.json` provenance, and a later `eval run` without `--protocol` or a config protocol uses it. An explicit `--protocol` or a config file's `protocol` key wins over the recorded value.

`eval report` prints the protocol above its table. `eval compare` refuses runs evaluated under different protocols: AP under another protocol is another quantity. The app's viewer shows the same protocol line and offers only runs with the same protocol in its Compare menu.

The demo's own run id is `demo` after `coperceive demo create`. That run is labelled as a simulated detector in the generator, not as a trained model.

## More detail

- [Evaluate](../eval.md)
- [Predictions](../spec/predictions.md)
- [Evaluation decision](../adr/0010-eval-predictions.md)
