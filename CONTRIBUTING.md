# Contributing

Thanks for helping with Coperceive. Bug reports, dataset manifests, model plugins, docs fixes and viewer improvements are all welcome. If you're unsure whether something fits, open an issue first and describe what you want to do.

By contributing you agree that your contribution is licensed under the Apache License 2.0 (see `LICENSE`), and you agree to follow the [Code of Conduct](CODE_OF_CONDUCT.md). Report security problems privately as described in [SECURITY.md](SECURITY.md), not in a public issue.

## Set up

Python 3.11 to 3.13.

```bash
git clone https://github.com/adarsh-nl/coperceive.git && cd coperceive
python3 -m venv .venv
.venv/bin/pip install -e ".[app,griffin,dev]"
bash scripts/dev/install-hooks.sh
```

The web viewer (writes `coperceive/app/static`):

```bash
cd web && npm install --legacy-peer-deps && npm run build
```

The desktop app is in `src-tauri/` (Tauri v2); see `docs/desktop/` for building it.

## Run the checks

```bash
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/pyright
.venv/bin/pytest
cd web && npm test
```

Pyright is strict on `coperceive` and `tests`, except `coperceive/app`, `coperceive/griffin` and their tests. CI runs the same checks plus the desktop builds on macOS, Windows and Linux.

## Add a dataset

A new dataset layout is a manifest under `coperceive/adapters/builtin/`; a new file format is a reader in `coperceive/adapters/readers/`. Follow [Add a dataset](docs/datasets/add-a-dataset.md). Each one is tested against a small **synthetic** tree under `tests/fixtures/` that you write by hand or generate. The scene contract is [`docs/spec/format.md`](docs/spec/format.md).

Cite the dataset's official page for every fact you state about it (licence, sensors, frames, coordinate conventions). If you can't confirm something, mark it unverified rather than guessing.

## Add a model plugin

A model plugin is a small package with a `model.yaml` manifest and a worker that speaks the plugin protocol. Read [`docs/spec/model-plugin.md`](docs/spec/model-plugin.md) and [`docs/models.md`](docs/models.md), and use `plugins/griffin/` or `plugins/opencood/` as a worked example. Don't copy code from another project unless its licence allows it, and credit it in `THIRD_PARTY_NOTICES.md`.

## API changes

The viewer's TypeScript client is generated from the FastAPI schema. After changing a route:

```bash
.venv/bin/python -c "import json,tempfile; from coperceive.app.server import create_app; print(json.dumps(create_app(root=tempfile.mkdtemp(), token=None).openapi(), indent=2))" > web/openapi.json
cd web && npx openapi-typescript openapi.json -o src/api/schema.ts
```

Commit both files; the `web` CI job fails on drift in either direction.

## Docs

```bash
.venv/bin/pip install -r requirements-docs.txt
.venv/bin/mkdocs build --strict
```

## Pull request checklist

- [ ] One topic per PR, with a clear title in [Conventional Commits](https://www.conventionalcommits.org/) style (`feat:`, `fix:`, `docs:`, …).
- [ ] Tests for the change. A bug fix comes with a test that fails without it.
- [ ] `ruff`, `pyright`, `pytest` and `npm test` pass locally.
- [ ] Docs and `CHANGELOG.md` updated for anything a user would notice.
- [ ] No dataset bytes: no recordings, point clouds, images, Parquet scenes, `.rrd` files or checkpoints. Fixtures are synthetic.
- [ ] No secrets, tokens, keys, personal names, hostnames or home paths in code, tests, logs or docs. Credentials come from the environment or the system keyring.
