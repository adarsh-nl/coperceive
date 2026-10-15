# Handover

Phase 0 only. Stop here for Adarsh’s approval. Do not start a second dataset, a 3D viewer product, or training code.

## Goal

An independent cooperative perception data platform Adarsh owns: a multi-agent scene model and on-disk format, Griffin as the first reader, a query API, and a local web app. The distribution name `pkg` is a placeholder.

Clean-room rule: do not install, import, or paraphrase kesai-labs/py123d. It is cited from its public GitHub description only.

## State

- Library, tests, Griffin reader, hybrid store, and `pkg serve` are in the tree.
- Local page is the Vite build in `pkg/app/static`.
- Design notes are in `docs/design/` and `docs/adr/`. Measurements are in `docs/design/measurements/`.
- Griffin bytes are in `/tmp/griffin-25m` and `/tmp/griffin-extract` on the machine that ran the spikes. They are not in git.
- Licence file is Apache-2.0 and is not confirmed.
- Format and viewer choices are proposed in ADR 0002 and ADR 0003.

## Decisions already in the code

- Schema version 1. One world frame, int64 microseconds, N agents, `world ← body` poses, body-frame boxes, shared objects only when ids and categories agree.
- Interpolation is linear translation plus slerp, only inside `max_gap_us`. No silent extrapolation.
- Hybrid Parquet tables plus `points.bin`.
- Web stack is FastAPI, SQLite jobs, argv allow-list (`echo` only), header CSRF, bearer token off loopback, Vite UI with relative URLs.
- Docs site is MkDocs Material.

## Open questions for Adarsh

1. Name. Candidates and the 2026-10-15 availability checks are in `docs/design/names.md`. Suggestion: look at poseledger, sceneory, and hexadview first.
2. Licence. Apache-2.0 is in the tree as a placeholder. Confirm or replace it.
3. Repository visibility. Prefer private until the real name and v0.1. A public repo called `pkg` would publish the placeholder.
4. On-disk format. ADR 0002 proposes the hybrid layout. HDF5 was faster for one timestamp and slower for a full lidar scan.
5. Viewer. ADR 0003 proposes three.js inside this app, a Rerun `.rrd` export, and viser as an optional extra. No viewer is bundled yet. Camera images were not downloaded, so image frame rate is unknown.
6. Second dataset. Leave it for a later phase. Not this one.
7. HD maps. The Griffin release that was opened has map name strings and no centreline geometry. The 0.24 m Town03 figure was not measured. Pursuing maps means a source outside this release.

## Gates on this machine

Python 3.12.3 only. Commands and results from 2026-10-15:

- `ruff check .` — all checks passed
- `ruff format --check .` — 42 files already formatted
- `pyright` — 0 errors
- `pytest` — 17 passed, coverage 86% of `pkg`
- `python -m build --wheel` — built `dist/pkg-0.0.0-py3-none-any.whl`
- Fresh venv: `pip install dist/pkg-0.0.0-py3-none-any.whl` then `pkg --help` printed the placeholder description, and `pkg schema` printed `1`
- `mkdocs build` — succeeded (MkDocs 1.6.1, Material 9.7.7)
- gitleaks 8.28.0 `--no-git` — no leaks
- Chrome loaded `http://127.0.0.1:8731/`, showed the empty job list and “API is up”, submitted echo with the text “phase0 desk check”, read `note.txt` from the server root, and showed an error for `../note.txt`. At a 390 px width the page did not scroll sideways.

Not run here: pytest on Python 3.10, 3.11, and 3.13 (CI workflow does that), and `torch.utils.data.DataLoader`.

## Commands

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[app,griffin,dev,docs]"
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/pyright
.venv/bin/pytest
.venv/bin/mkdocs build
pkg serve --root /path/to/workdir
```

Rebuild the page with `cd web && npm install && npm run build`.

## Next, after approval

Pick the name, confirm the licence and visibility, then keep or replace the hybrid store and the viewer plan. Only then add another dataset.
