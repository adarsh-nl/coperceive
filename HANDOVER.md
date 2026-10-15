# Handover

Phase 1 is in the tree. Stop here. Do not start a viewer, a second dataset, or training code.

## Goal

An independent cooperative perception data platform Adarsh owns. Phase 1 is the multi-agent scene model and the hybrid on-disk format. The contract is `docs/spec/format.md`. The distribution, import, and command name are `coperceive`.

Clean-room rule: do not install, import, copy, paraphrase, or model naming or schema on kesai-labs/py123d. It was not opened for this phase.

## State

- Schema 1 is specified and implemented: one scene holds every agent, Parquet tables plus `points.bin`.
- `T(dst ← src, t)` names world, body, and sensor. `max_gap_us` is required. There is no silent extrapolation.
- Time queries are `exact`, `nearest`, `backward`, and `forward`. Each agent reports `dt_us` or a miss. `tolerance_us` is required.
- Scene objects are exactly the track ids used by at least two agents that agree on one category. Geometry stays on the boxes.
- Synthetic fixture: vehicle at 10 Hz, uav at a nominal 20 Hz with one late stamp and a 0.5 m offset, infrastructure spanning a wider interval. Tests quote the expected microseconds and metres.
- Griffin reader and `coperceive serve` are still the Phase 0 scaffold. A Griffin drone is `agent_type` `uav`; `agent_id` can still be `drone`.
- Licence is Apache-2.0. The repository stays private. The hybrid store and a later three.js viewer are the accepted plan. No viewer is bundled.

## Decisions already in the code

- Schema version stays `1`. This document's format spec is that version. A Phase 0 directory with no `frame` column, or with `agent_type` `drone`, does not read. No such directory is in git.
- Agent types are `vehicle`, `uav`, `infrastructure`, `other`. Platform metadata is a string-to-string map.
- Sensors are `camera`, `lidar`, `radar`. The extrinsic is `body ← sensor`. A camera may carry a pinhole. Lidar and radar have no measurement model in schema 1.
- A box stores its class in `category`, an optional `track_id`, an optional `visibility`, and `frame` `body` or `world`.
- Interpolation is linear translation plus slerp, only when `gap <= max_gap_us`. Slerp takes the short arc and uses normalized lerp when the quaternion dot is above 0.9995.
- Nearest-time ties keep the earlier sample. `exact` may return a non-equal sample inside `tolerance_us` (closest, earlier on a tie).
- `R = Rz(yaw) @ Ry(pitch) @ Rx(roll)` in degrees. The Griffin residual cited in the spec is the existing Town03-000 figure, 5.7e-14 m. It was not re-measured in this phase.
- Source: https://github.com/adarsh-nl/coperceive

## Open questions for Adarsh

1. Schema number. It stayed `1` because nothing was released. Say if a Phase 0 directory should fail as schema 2 instead.
2. Class column. The field is still `category`. A rename is a new schema version.
3. Shared objects. Promotion is id plus one category, not a metre check. `track_center_spread_m` reports the world residual when asked. Say if a tolerance should reject an id before it is written.
4. Box frames are `body` and `world` only. Sensor-frame boxes are not in schema 1.
5. HD maps, which horizontal axis is east, and the camera image rate. Unchanged from Phase 0. Images were not downloaded. The 0.24 m Town03 centreline figure was not measured.
6. Viewer. three.js inside this app is the accepted later plan. It is not built. Rerun export and viser stay as in ADR 0003.
7. Second dataset. Not this phase.

## Gates on this machine

Python 3.12.3. Commands and results from 2026-10-15:

- `ruff check .` — all checks passed (ruff 0.17.0)
- `ruff format --check .` — 49 files already formatted
- `pyright` — 0 errors, 0 warnings (pyright 1.1.414, strict on the core)
- `pytest -o addopts= -q` — 35 passed in 0.84s
- `pytest --cov=coperceive --cov-report=term-missing -q` — exit 0, coverage 86% of `coperceive` (1149 statements, 119 missed, branch coverage on)
- `python -m build --wheel` — built `dist/coperceive-0.0.0-py3-none-any.whl`
- Fresh venv: `pip install dist/coperceive-0.0.0-py3-none-any.whl` then `coperceive --help` printed “Cooperative multi-agent perception data tools.” and `coperceive schema` printed `1`
- `mkdocs build` — succeeded (MkDocs 1.6.1, Material 9.7.7)
- `gitleaks detect --source . --redact --no-banner --exit-code 1` (8.28.0) — no leaks
- `gitleaks detect --no-git --source . --redact --no-banner --exit-code 1` — no leaks in the working tree (3.69 MB scanned)

Not run here:

- `pytest` on Python 3.10, 3.11, and 3.13. The CI workflow does that. This machine is 3.12.3 only.
- A `torch.utils.data.DataLoader` with `num_workers > 0`. `python3 -c "import torch"` was not run because torch is not installed for this phase, same as the Phase 0 note.
- `load_scene` on a Griffin release. `/tmp/griffin-25m` and `/tmp/griffin-extract` are not on this machine. The Griffin test builds a two-frame tree under pytest's `tmp_path`.
- The local web page in a browser. Phase 1 did not change it.

## Commands

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[app,griffin,dev,docs]"
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/pyright
.venv/bin/pytest
.venv/bin/pytest --cov=coperceive --cov-report=term-missing
.venv/bin/mkdocs build
coperceive serve --root /path/to/workdir
```

The format spec is `docs/spec/format.md`. Hypothesis is in the `dev` extra. Property tests cover SE3 composition, inversion, and point round-trip, and `T` across world, body, and sensor.

## Next

Stop. Phase 2 has not started.
