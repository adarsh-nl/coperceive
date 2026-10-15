# Contributing

The maintainer is Adarsh (github.com/adarsh-nl). Phase 1 is the scene model and `docs/spec/format.md`. Do not add detectors, fusion, accounts, a viewer, or a second dataset. Open decisions are in `HANDOVER.md`.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[app,griffin,dev]"
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/pyright
.venv/bin/pytest
```

Python 3.10 through 3.13. Pyright is strict on `coperceive` and `tests`, except `coperceive/app`, `coperceive/griffin`, and their tests.

Docs:

```bash
.venv/bin/pip install -e ".[docs]"
.venv/bin/mkdocs build
```

The web page:

```bash
cd web && npm install && npm run build
```

That writes `coperceive/app/static`. Regenerate the client after an API change:

```bash
cd web && npx openapi-typescript openapi.json -o src/api/schema.ts
```

`openapi.json` is produced by FastAPI. Refresh it from `create_app(...).openapi()` when the routes change.

## Secrets

Tokens and keys come from the environment or the system keyring. Do not put them in code, config, logs, commits, or `HANDOVER.md`. Pass them to child processes through the environment only.

## Data

Do not commit Griffin recordings, point clouds, Parquet scenes, `.rrd` files, or other dataset bytes.

## Commits

[Conventional Commits](https://www.conventionalcommits.org/). `spikes/` is excluded from ruff because those scripts are records of a measurement, not the library.
