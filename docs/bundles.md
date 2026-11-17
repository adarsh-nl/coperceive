# Result bundles (`.cpbundle`)

A result bundle is **one file** a researcher can send a colleague. It holds the
repro card, the score report and config, the run's boxes for the included
frames and their matches, rendered frame previews with boxes/ground truth,
and, for Coperceive demo scenes only, the frames' LiDAR points and camera
images. The recipient opens it **read-only**, with no
dataset and no cluster access.

Bundles are zip files with a versioned `bundle.json` manifest. Every listed file
has a SHA-256 and an uncompressed size; the manifest is validated before
anything is extracted.

## Create

```bash
coperceive bundle export <run-id> \
  --root <scenes> --predictions <predictions-root> \
  -o results.cpbundle [--title "…"] [--frames worst:6] [--dataset-id ID] \
  [--include-sensor-data] [--sensor-max-bytes N] \
  [--include-full-predictions] [--marker VALUE]
```

By default only the selected frames' predictions are included (`worst:6`, or
`worst:N`, or `scene@time_us,...`). `worst:N` takes the run's `failures.json`
ranking, or the run's first N predicted frames when it has none; neither reads
any scene that is not exported. The full run's predictions are opt-in and
the CLI shows their size.

Sensor data (LiDAR points and camera images for the included frames) is off by
default and included only when **all** hold:

- every included scene is a Coperceive demo scene: its `COMPLETE.json` records
  `generator: "coperceive demo"` and the `generic` adapter. A typed dataset id
  or a folder name is never proof;
- the exporter ticks the opt-in (`--include-sensor-data`, or "Include sensor
  data" in the app);
- the selected sensor bytes fit the cap (`--sensor-max-bytes`, default 50 MiB;
  Python: `BundleExportRequest.sensor_max_bytes`). The cap is checked before
  anything is written.

No third-party dataset is on the allow list. OPV2V, V2XSet, V2V4Real,
DAIR-V2X-C, generic folders and Griffin are refused by name (Griffin's catalog
entry is `requires_terms: true`: each user accepts its terms before
downloading). The bundle records `sensor_data` status, reason and licence.

When sensor data is omitted, opening looks for the same scene in the
recipient's library (`--library-root`, repeatable; the app passes its library
folder): same scene id, adapter and frame count, and its resolved path must stay
inside a library root, so a link out of the library is not followed. The viewer
reads that copy in place, never copies it into the bundle, and shows only the
bundle's frames. Otherwise it shows the boxes on an empty grid with the reason.

The export never follows a link. It walks the run folder and each exported
scene folder with `os.walk(followlinks=False)` and `lstat`s every entry first:
a symbolic link (to a file, a folder, another link, or a file in the same
scene), a hard link (a file with other names), a FIFO or any other non-regular
file refuses the whole export with an error that names it. A link in a scene
that is not exported does not matter. Each file is then read only through a
check that it is still the file the walk saw (same device and inode, opened
without following a final link), so a file or folder swapped in after the walk
is refused instead of read. Scene tables and the run's files are read from a
private copy made that way. Nothing is skipped silently, and a recorded image that is missing is
an error too. Links inside a scene are refused rather than copied: one rule for
every link keeps `bundle.json` one-to-one with regular files on disk. Replace a
link with a copy of its target to export the scene.

The app's Run history has an **Export bundle…** action with the same review:
every file with size, the total, what the scrubber removed per file, and licence
notes. Nothing is uploaded anywhere. `--compare` and paper `figures/` are not
part of this version.

Size caps are set from Python with `coperceive.bundle.open.OpenLimits`
(`max_files`, `max_file_bytes`, `max_total_bytes`, `max_sensor_bytes`); the CLI
uses the defaults: 20,000 files, 512 MiB per file, 2 GiB total and 50 MiB
sensor data. The sensor cap counts every member under `scenes/` other than a
scene's own tables (`manifest.json`, `COMPLETE.json`, and the poses,
annotations, objects and images tables), whatever its case, suffix or `kind`,
and is checked before anything else is extracted. A bundle that records sensor
data as omitted may hold no such member at all. Opening the same bundle again
checks the archive again and refuses an extracted copy that has changed.

## Privacy

Every JSON/text file in the bundle is scrubbed with the repro-card scrubber:
the runtime user/host/home markers, personal identifiers, emails and
secret-shaped values. The repro card is also checked against its own schema.
The bundle contains no credentials and does not run anything from the card.

## Open

```bash
coperceive bundle open results.cpbundle [--data-root ~/.coperceive] [--library-root DIR]
coperceive serve --root <opened>/scenes --predictions <opened>/predictions \
  --bundle-info <opened>/open.json
```

Opening validates and extracts under `<data-root>/bundles/<bundle-sha>/`. It
refuses absolute paths, `..`, symlinks, duplicates, unlisted files, oversized
headers, hash mismatches, malformed JSON, members named `open.json` or
`BUNDLE.JSON`, and bundles made by a newer schema.
It never executes scripts, pickles or repro-card commands. The app's
**Open bundle…** shows the run summary, scores, comparison and frames in the
viewer; the rerun command is text with a copy button only. The viewer's jobs
database for a bundle lives in a private temporary folder, never inside the
extracted bundle, and every route keeps the token, CSRF and loopback-Host
checks of `coperceive serve`. A local copy is served only while it still
resolves inside a library root that `open.json` records.

## Schema

`bundle.json` follows [docs/spec/result-bundle.schema.json](spec/result-bundle.schema.json).
`repro/card.json` follows [docs/spec/repro-card.schema.json](spec/repro-card.schema.json).

## Licence

The bundle format and scrubber are Coperceive code (Apache-2.0). Dataset and
model contents keep their own licences; the export review lists the known
notes. No real dataset frames are committed to this repository; tests use the
synthetic demo scene.
