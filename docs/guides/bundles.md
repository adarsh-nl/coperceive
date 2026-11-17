# Result bundles

A result bundle is one `.cpbundle` file: the repro card, the score report, boxes for the chosen frames, and rendered previews. The recipient opens it read-only. Raw sensor files are off unless you pass `--include-raw`.

## Steps

```bash
coperceive bundle export RUN_ID \
  --root /data/demo/scenes \
  --predictions /data/demo/predictions \
  -o /data/results.cpbundle
```

`--root`, `--predictions`, and `-o` are required (`coperceive bundle export --help`). `--frames` defaults to `worst:6`.

Open a file you received with:

```bash
coperceive bundle open /data/results.cpbundle
```

Check the dataset licence before `--include-raw`. The bundle page says raw files are not included by default.

## More detail

- [Result bundles](../bundles.md)
- [Repro card](../spec/repro-card.md)
- [Bundle schema](../spec/result-bundle.schema.json)
