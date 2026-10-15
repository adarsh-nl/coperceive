# Handover

Phase 2 is in the tree. Stop here. Do not start a viewer, a second dataset, or training code.

## Goal

An independent cooperative perception data platform Adarsh owns. Phase 2 downloads Griffin and converts one release into the Phase 1 hybrid store. The contract is still `docs/spec/format.md`. Conventions for this dataset are `docs/datasets/griffin.md`. The distribution, import, and command name are `coperceive`.

Clean-room rule: do not install, import, copy, paraphrase, or model naming or schema on kesai-labs/py123d. It was not opened for this phase.

## State

- Schema 1 is unchanged. One scene holds the vehicle and the drone. `write_scene` / `read_scene` are the store.
- `coperceive download griffin --subset 25m|40m|55m|random` fetches from `datasets/wjh-svm/Griffin`. `--preset dev` is `drone_metadata`, `vehicle_metadata`, and `vehicle_lidar`. `--components metadata,lidar` is the same set. Other tokens are archive basenames from the Hub tree.
- `--dry-run` prints the Hub tree sizes and writes nothing. Resume skips a payload whose MD5 already matches. The `md5.txt` self-line does not match the file; payload lines are checked.
- `griffin_100scenes_random` camera RGB archives are `*.z01` plus `*.zip`. They are concatenated and opened with `zipfile`. Those parts were not downloaded. A synthetic split covers the path.
- Free space is checked before the first byte (download size plus another copy of each compressed archive, and a further copy when a split archive is reassembled), then again from `ZipInfo.file_size` before each extract.
- The MIT card and the toolkit `LICENSE.txt` are fetched at download time. `--accept-griffin-terms` writes `$XDG_DATA_HOME/coperceive/griffin-licence-accepted` (or `~/.local/share/coperceive/`). No token is stored there.
- `HF_TOKEN` comes from the environment or the keyring entry `huggingface` / `HF_TOKEN`, and is passed only as the `huggingface_hub` token argument. If neither is set, the argument is `False`.
- `coperceive convert griffin` writes one directory per scene, in parallel, and skips a directory whose `COMPLETE.json` lists the same frames. A failed scene does not delete scenes that finished.
- Vehicle LiDAR in the 25 m zip is ASCII PLY (`x, y, z, I`), not `.bin`. 7,000 clouds, 0 `.bin`.
- Griffin-25m on this machine: 47 scenes, 7,000 frames (45 × 149, one 148, one 147), 177,697 label lines, all 12 fields. Convert wrote 333,097,959 bytes in 5 s. Shared-track residual max 4.576455661277233e-13 m over 813 tracks. Details and commands are in `docs/datasets/griffin.md`.

## Decisions already in the code

- Schema version stays `1`.
- A missing visibility field is stored as null. The toolkit fills `1.0`. This 25 m subset had no missing visibility.
- A 9-field label uses roll 0 and pitch 0, and visibility null. The toolkit indexes the raw string in that branch and fills visibility `1.0`. This subset had no 9-field line.
- East is the toolkit `PoseData` comment (`x` East, yaw 0 East, positive yaw counterclockwise). It was not checked against a map.
- Drone `lidar_bottom`, `lidar_front`, and `lidar_virtual` calib files are stored as lidar sensors. No drone cloud is attached.
- `time_us = frame_id * 100000` stays the 10 Hz assumption from the Phase 0 note. The paper PDF was not re-opened.
- Source: https://github.com/adarsh-nl/coperceive

## Open questions for Adarsh

1. Visibility. Keep null when the field is absent, or match the toolkit and store `1.0`?
2. Drone lidar calibs with no clouds. Keep them as sensors, or drop stems that have no point files?
3. East. The toolkit comment is the only evidence. A map check was not done. Say if one is required before the world frame is treated as ENU in a published sense.
4. Image paths are written for every camera calib even when the dev tier did not download images. Say if a metadata-only convert should omit those refs.
5. Schema number. Still `1`. A rename of `category`, or a sensor-frame box, is a new version.
6. Viewer. three.js inside this app is the accepted later plan. It is not built.
7. Second dataset. Not this phase.

## Gates on this machine

Python 3.12.3. Commands and results from 2026-10-15:

- `ruff check .` — all checks passed (ruff 0.17.0)
- `ruff format --check .` — 56 files already formatted
- `pyright` — 0 errors, 0 warnings (pyright 1.1.414, strict on the core; `coperceive/griffin` and `tests/test_griffin*.py` are excluded)
- `pytest -o addopts= -q` — 51 passed, 1 skipped, 4 warnings in 1.08s (pytest 9.1.1). The skip is `test_real_griffin_release_loads` without `COPERCEIVE_GRIFFIN_RAW`. The warnings are `DeprecationWarning` from `fork()` inside that parallel convert test.
- `pytest --cov=coperceive --cov-report=term-missing -q` — exit 0, coverage 87% of `coperceive` (1895 statements, 194 missed, branch coverage on)
- `python -m build --wheel` — built `dist/coperceive-0.0.0-py3-none-any.whl`
- Fresh venv `/tmp/coperceive-fresh`: `pip install dist/coperceive-0.0.0-py3-none-any.whl`, then `coperceive --help` printed “Cooperative multi-agent perception data tools.” and listed `download` and `convert`. `coperceive schema` printed `1`.
- `mkdocs build` — succeeded (MkDocs 1.6.1, Material 9.7.7). Material printed its MkDocs 2.0 warning. The site was written to `site/`.
- `gitleaks detect --source . --redact --no-banner --exit-code 1` (8.28.0) — no leaks
- `gitleaks detect --no-git --source . --redact --no-banner --exit-code 1` — no leaks in the working tree (3.99 MB scanned)

The slow test was also run on the extracted 25 m release: `COPERCEIVE_GRIFFIN_RAW=/tmp/griffin-phase2/griffin_50scenes_25m/griffin-release pytest -o addopts= -q -m slow` — 1 passed in 0.77s.

Not run here:

- `pytest` on Python 3.10, 3.11, and 3.13. This machine is 3.12.3 only.
- `python3 -c "import torch"` and a `DataLoader` with `num_workers > 0`. Torch is not installed.
- A browser pass. This phase did not change the web page.
- `zip -s 0` on a Griffin split. Reassembly is pure Python. The unit test concatenates a synthetic zip. The random-subset `.z01` files were listed and not downloaded.
- `coperceive download griffin --subset 40m` and `--subset 55m`. Their trees were not listed again in this phase.
- A map check of which horizontal axis is east.
- The Griffin paper PDF. The 10 Hz note is the one already in `docs/design/griffin-verification.md`.

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
coperceive download griffin --subset 25m --preset dev --dest /path/outside/the/repo --dry-run
coperceive convert griffin --subset 25m --raw-root /path/griffin-release --out /path/scenes
```

The real 25 m download and convert used `/tmp/griffin-phase2` and `/tmp/griffin-scenes`. Those directories are not in git.

## Next

Stop. Phase 2 is the Griffin download and the conversion into schema 1. Do not start Phase 3 from this note.
