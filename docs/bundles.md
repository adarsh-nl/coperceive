# Result bundles (`.cpbundle`)

A result bundle is **one file** a researcher can send a colleague. It holds the
repro card, the score report and config, the run's boxes for the included
frames, rendered frame previews with boxes/ground truth, and optionally paper
figures and raw sensor files. The recipient opens it **read-only**, with no
dataset and no cluster access.

Bundles are zip files with a versioned `bundle.json` manifest. Every listed file
has a SHA-256 and an uncompressed size; the manifest is validated before
anything is extracted.

## Create

```bash
coperceive bundle export <run-id> \
  --root <scenes> --predictions <predictions-root> \
  -o results.cpbundle [--title "…"] [--frames worst:6] \
  [--include-raw] [--include-full-predictions] [--compare <run>] [--config eval.yaml]
```

By default only the selected frames' predictions are included (six frames, or
`worst:N`, or `scene@time_us,...`). The full run's predictions are opt-in and
the CLI shows their size. Raw LiDAR/camera files are **not** included by default;
`--include-raw` includes only the selected frames' sensor files and records the
dataset licence note. **Check the dataset licence before sharing raw data.**

The app's Run history has an **Export bundle…** action with the same review:
every file with size, the total, what the scrubber removed, and licence notes.
Nothing is uploaded anywhere.

## Privacy

Every JSON/text file in the bundle is scrubbed with the repro-card scrubber:
the runtime user/host/home markers, personal identifiers, emails and
secret-shaped values. The repro card is also checked against its own schema.
The bundle contains no credentials and does not run anything from the card.

## Open

```bash
coperceive bundle open results.cpbundle [--data-root ~/.coperceive]
```

Opening validates and extracts under `<data-root>/bundles/<bundle-sha>/`. It
refuses absolute paths, `..`, symlinks, duplicates, unlisted files, oversized
headers, hash mismatches, malformed JSON and bundles made by a newer schema.
It never executes scripts, pickles or repro-card commands. The app's
**Open bundle…** shows the run summary, scores, comparison and frames in the
viewer; the rerun command is text with a copy button only.

## Schema

`bundle.json` follows [docs/spec/result-bundle.schema.json](spec/result-bundle.schema.json).
`repro/card.json` follows [docs/spec/repro-card.schema.json](spec/repro-card.schema.json).

## Licence

The bundle format and scrubber are Coperceive code (Apache-2.0). Dataset and
model contents keep their own licences; the export review lists the known
notes. No real dataset frames are committed to this repository; tests use the
synthetic demo scene.
