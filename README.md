# pkg

A cooperative, multi-agent perception data library. The name **pkg** is a placeholder until Adarsh chooses one. See `docs/design/names.md`.

Phase 0 is the scaffold: a scene model, a hybrid on-disk layout, a Griffin reader, a local web page, and the measurements behind the design notes. It does not train models.

## Install

Python 3.10–3.13.

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[app,griffin]"
pkg --help
```

Extras: `torch`, `viewer` (rerun-sdk 0.22.1 and viser 0.2.23), `app`, `griffin`, `docs`, `dev`.

The licence file is Apache-2.0 and is waiting for confirmation. See `docs/adr/0006-licence.md`.

## Local page

```bash
pkg serve --root /path/to/a/workdir
```

The default bind is `127.0.0.1:8731`. A non-loopback host requires `--token`. Behind a proxy, pass `--root-path` with the prefix the proxy adds.

The page in the wheel is the Vite build in `pkg/app/static`. Rebuild it from `web/` with `npm run build` after a UI change.

## Griffin

Only `griffin_50scenes_25m` metadata and vehicle LiDAR are allow-listed. The download needs the `griffin` extra and writes outside the git checkout. Recordings are not committed. Checks and disagreements with the paper and the toolkit are in `docs/design/griffin-verification.md`.

## Tests

```bash
.venv/bin/pip install -e ".[app,griffin,dev]"
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/pyright
.venv/bin/pytest
```

## Docs

```bash
.venv/bin/pip install -e ".[docs]"
.venv/bin/mkdocs serve
```

Design notes live in `docs/`. Current state and open decisions are in `HANDOVER.md`.
