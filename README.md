# coperceive

A cooperative, multi-agent perception data library. Source: <https://github.com/adarsh-nl/coperceive>. The name is recorded in `docs/design/naming.md`.

Phase 1 is the scene model and the hybrid on-disk format. Phase 3 adds `Scene.at`, camera projection, cross-agent box queries, and `CooperativeDataset` in the `torch` extra. The contract a third party can implement is `docs/spec/format.md`. A Griffin reader and a local page came with the Phase 0 scaffold. This tree does not train models and does not embed a viewer.

## Install

Python 3.10–3.13.

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[app,griffin]"
coperceive --help
```

Extras: `torch` (`coperceive.dataset.CooperativeDataset`; not imported by `coperceive`), `viewer` (rerun-sdk 0.22.1 and viser 0.2.23), `app`, `griffin`, `docs`, `dev`.

The licence is Apache-2.0. The repository stays private. See `docs/adr/0006-licence.md`.

## Local page

```bash
coperceive serve --root /path/to/a/workdir
```

The default bind is `127.0.0.1:8731`. A non-loopback host requires `--token`. Behind a proxy, pass `--root-path` with the prefix the proxy adds.

The page in the wheel is the Vite build in `coperceive/app/static`. Rebuild it from `web/` with `npm run build` after a UI change.

## Griffin

```bash
coperceive download griffin --subset 25m --preset dev --dest /path/outside/the/repo --dry-run
coperceive convert griffin --subset 25m --raw-root /path/griffin-release --out /path/scenes
```

`--preset dev` is drone metadata, vehicle metadata, and vehicle LiDAR. Camera archives are selected only with `--components`. The download needs the `griffin` extra, checks the MIT card and toolkit licence, and writes outside the git checkout. Recordings are not committed. Conventions are in `docs/datasets/griffin.md`. Earlier measurements are in `docs/design/griffin-verification.md`.

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
