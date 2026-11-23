# Run a model

`coperceive model` starts a plugin in its own process and writes a predictions run. The OpenCOOD plugin is one such plugin. The conformance check uses a synthetic scene, not a dataset.

## Steps

1. List installed plugins: `coperceive model list`.
2. Check a plugin against the synthetic scene:

    ```bash
    coperceive model check fake --manifest tests/fake_model_plugin/model.yaml
    ```

3. Run it on schema 1 scenes:

    ```bash
    coperceive model run PLUGIN --scenes /data/demo/scenes --out /data/predictions --run-id RUN_ID
    ```

`--scenes` and `--out` are required (`coperceive model run --help`). `--model` selects one model when the plugin defines several.

4. Score the run with [Evaluate predictions](evaluate.md).

## More detail

- [Run a model](../models.md)
- [OpenCOOD plugin](../models/opencood.md)
- [Model plugin contract](../spec/model-plugin.md)
- [ADR 0011](../adr/0011-live-model-plugin.md)
