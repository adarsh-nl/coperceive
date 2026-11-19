# Repro card

A repro card is one small JSON file that says what a coperceive run was, so someone else can repeat it on their own cluster. It is the file behind **Export repro card** in Run history and behind `coperceive repro export`.

The machine-readable contract is [`repro-card.schema.json`](repro-card.schema.json) (`schema_version` 1). This page is the meaning of the fields. Feature 1b (import and rerun) consumes the same file and is not part of this page. Cards are not signed and are not uploaded; export writes a local file only.

## Envelope

| Field | Meaning |
| --- | --- |
| `schema` | Always `coperceive-repro-card`. |
| `schema_version` | `1` for this document. A later version may add fields. `coperceive repro check` validates version 1 against the schema. A higher integer version is accepted as an envelope (name, id, created time) and is still scanned for secrets and absolute paths; fields this version does not know are left untouched. |
| `id` | `card-` plus 12 hex digits. Identifies this export, not the run. |
| `created_utc` | When the card was written, UTC, `YYYY-MM-DDTHH:MM:SSZ`. |
| `unknown` | Dotted field names that could not be filled. A missing fact is `null` and listed here. The card never invents a value. |

`perturbation` is `null` when the run has no perturbation spec. That absence is not listed in `unknown`. The same is true of `pipeline_report` when the pipeline wrote no free-form report.

## What was run

`coperceive` is the version of coperceive that wrote the card (`version`) and, when this checkout is a git repository, its `git_commit` (40 hex digits).

`dataset` is `id`, `version`, and `manifest_sha256` of a `dataset.json` object sitting next to the scenes (`{"id", "version"}`). The hash is the sha256 of that file's bytes. Without the file the three fields are `null`.

`split` is the split's file name (never its directory), `scene_ids` in the run, and `split_file_sha256` when the split is a file whose sha256 was recorded. Scene ids come from the evaluated scenes, from `predictions.jsonl`, or from a split file that lists one scene id per line.

`pipeline` is the pipeline name, the sha256 of the `coperceive-pipeline.json` registration (ADR 0015 §3c), and `arguments`: one entry per flag with `name`, `role` (`checkpoint`, `test_data`, `config`, `output`, `other`, or `null` when the registration did not say), and `value`. Path values are stored with a `paths` entry, not as a site path. A bool flag that was passed is `true`.

`checkpoint` is `file_name`, `sha256`, and `size_bytes`. The file name is the last path segment. The absolute path is never stored. A digest that is only an md5 (as in some model-plugin provenance) is not copied into `sha256`.

`code.git_commit` is the commit recorded on the run (`run.json` `provenance.git_commit`) when that value is 40 hex digits. It is not guessed from the coperceive checkout.

`resources` is what the job asked the scheduler for: `gpus` (the GRES string), `time`, and `memory`, as plain text. Values that were not recorded stay `null`.

## Paths

`paths` is a list of `{role, relative}`. `role` is `data_roots`, `code_roots`, or `results_root`: the profile root the path sits under (ADR 0015). `relative` has no leading slash and no `..` segment. A path that is not under one of those roots is omitted. The card then keeps the file name only where a name is required (the checkpoint) and lists the path field in `unknown`.

## Environment

`environment` is what the job wrote to `environment.json` in the run directory, filled in with the modules or container digest declared on the profile when the file does not carry them.

| Field | Meaning |
| --- | --- |
| `python` | `python -V` style version (`3.12.1`). |
| `key_packages` | Versions of a fixed set of libraries when they are installed (`numpy`, `pyarrow`, `torch`, `pillow`, `pyyaml`). |
| `pip_freeze_sha256` | sha256 of `pip freeze` output. The freeze text itself is not stored: it can contain file URLs. |
| `modules` | Module names declared on the profile or reported by the job (`LOADEDMODULES`). |
| `container_image_digest` | A `sha256:…` digest. An image path is not a digest and is not copied. |
| `cuda`, `driver` | From `nvidia-smi` on the compute node, when that command exists. |

The job script writes `environment.json` with `python3 - <<'PY'` (a fixed program, no interpolated secrets) before the pipeline command. A failure to write the file does not fail the job. The desktop reads the file back with the same bounded read it uses for other run outputs. No environment variable values are copied except the module list and a container digest that already looks like `sha256:`.

## Scores

`metrics` is the authoritative score. It is present only when `coperceive eval run` wrote `metrics.json` (ADR 0015 §3d, ADR 0010). The shape is:

```json
{
  "source": "coperceive eval run",
  "authoritative": true,
  "by_scene": {
    "<scene_id>": "<the metrics.json object, unchanged>"
  }
}
```

`by_scene.<scene_id>` is the file `coperceive eval run` wrote for that scene. A card check compares those objects to the files when a predictions root is available. There is no second scoring pass.

`predictions_sha256` is the sha256 of the bytes of `predictions.jsonl`. When that file is not on the machine that builds the card but a `predictions.sha256` sidecar (one hex line) was recorded, the sidecar is used and `unknown` does not list the field. When neither exists the field is `null`.

`pipeline_report` is the pipeline's own `metrics.json` when it is not the eval file. It is always labelled:

```json
{
  "authoritative": false,
  "label": "Pipeline report. Not the coperceive score.",
  "content": {}
}
```

The app may display `content`. It is not the score.

## Perturbation

When a run records a perturbation spec, the card carries it on `perturbation`. The spec is read from `run.json` `provenance.perturbation` when that value is an object, otherwise from `<run>/perturbation.json`. This matches the field draft PR #94 adds; the card does not require that code. Secrets inside the object are removed. When both are missing, `perturbation` is `null`.

## What a card must not contain

No token, password, key, cookie, or other credential. No environment-variable dump. No username, no hostname, no absolute path, no `~` path. `coperceive repro export` drops secret-shaped keys, replaces token-shaped values with `null`, and rewrites or drops paths before it writes the file. `coperceive repro check` fails if any of those remain.

## Commands

```bash
coperceive repro export <run_id> --out card.json --root <scenes>
coperceive repro export <plan_id> --out card.json --profile <profile.json>
coperceive repro export <run_id> --out card.json --markdown card.md
coperceive repro check card.json
coperceive repro check card.json --run-dir <run directory>
```

`--root` is a local scenes directory whose predictions root holds the run (`--predictions` overrides it), the same pair `coperceive eval run` uses. `--profile` is a server profile JSON. The plan is `<results_root>/plans/<id>.json` and the run output is `<results_root>/runs/<id>/`. A profile's `results_root` may be a directory on this machine (a copy, or a test fixture); nothing in the card is specific to one cluster.

`--markdown` writes a short companion rendered from the JSON.

`coperceive repro check` validates the schema (version 1), checks that `metrics.authoritative` is true and `pipeline_report.authoritative` is false, and scans for secrets and absolute paths. With `--run-dir`, it also recomputes the sha256 of `predictions.jsonl` in that directory and fails when the file no longer matches `predictions_sha256`.

`--time-limit`, `--gpus`, and `--memory` record scheduler resources the caller already knows (for example the time limit in Run history). They are plain strings. They are not read from the environment.

## Run history

On a finished run, **Export repro card** shows a short preview in plain language, including any field that is unknown. Saving uses the system save dialog and writes the same JSON the command writes. A control offers to show the file in the file manager. The preview is built by the same Python exporter the command uses.
