# Evaluate predictions

Put a model's boxes beside schema 1 scenes, match them to the ground truth, and read the table. The screenshot is the evaluation page with synthetic fixtures.

![Evaluation page: design mock, app with synthetic fixtures, and an empty evaluation page](../design/v0.1.1/screenshots/app-pages/evaluation-1600x1000-dark.jpg)

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
