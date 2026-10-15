# coperceive

A cooperative, multi-agent perception data library. Source: <https://github.com/adarsh-nl/coperceive>. The name is recorded in `docs/design/naming.md`.

Phase 1 is the scene model and the hybrid on-disk format. The contract a third party can implement is `docs/spec/format.md`. A Griffin reader and a local page came with the Phase 0 scaffold. This tree does not train models and does not embed a viewer.

## Install

Python 3.10–3.13.

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[app,griffin]"
coperceive --help
```

Extras: `torch`, `viewer` (rerun-sdk 0.22.1 and viser 0.2.23), `app`, `griffin`, `docs`, `dev`.

The licence is Apache-2.0. The repository stays private. See `docs/adr/0006-licence.md`.

## Local page

```bash
coperceive serve --root /path/to/a/workdir
```

The default bind is `127.0.0.1:8731`. A non-loopback host requires `--token`. Behind a proxy, pass `--root-path` with the prefix the proxy adds.

The page in the wheel is the Vite build in `coperceive/app/static`. Rebuild it from `web/` with `npm run build` after a UI change.

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
