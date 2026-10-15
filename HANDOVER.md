# Handover

Phase 3 is in the tree. Stop here. Do not start a viewer, a second dataset, or training code.

## Goal

An independent cooperative perception data platform Adarsh owns. Phase 3 reads a schema 1 scene at one time, projects boxes into a camera, queries boxes across agents, and exposes a worker-safe torch dataset. The on-disk contract is still schema 1 in `docs/spec/format.md`. The distribution, import, and command name are `coperceive`.

Clean-room rule: do not install, import, copy, paraphrase, or model naming or schema on kesai-labs/py123d. It was not opened for this phase.

## State

- `Scene.at(t, tolerance=...)` returns every agent in scene order. `tolerance` is `tolerance_us` and is required. `mode` defaults to nearest. A miss keeps the agent, with sensors, and with null pose, time, and `dt_us`. It does not raise and it does not drop other agents.
- The pose on a hit is the discrete sample from `query_agents`, not an interpolated pose.
- `project_box` uses `T(dst ← src, t)` for the box and the camera. `max_gap_us` is required. Depth is +Z. Pixels are `fx * x / z + cx` and `fy * y / z + cy`, not rounded. `z <= 0` is behind the camera. Missing image width or height is `no_image_size`.
- `exclusive_annotations`, `co_annotations`, `shared_track_annotations`, and `associate` are the box queries. A nearest-centre pair is `computed` and `ground_truth` false. It is not written into `scene.objects`. The same track id with disagreeing categories is not `shared_track`.
- `CooperativeDataset` is `coperceive.dataset` in the `torch` extra. `import coperceive` does not import it. The index is built in the parent from `manifest.json` and the pose `agent_id` / `time_us` columns. `__getstate__` drops the cached scene. Workers call `read_scene` lazily. `identity_collate` returns a list of samples.
- Schema version stays `1`. Griffin-25m conversion from Phase 2 was reused from `/tmp/griffin-scenes` (47 scene directories, 319M). It was not re-downloaded.

## Decisions already in the code

- `Scene.at` default mode is nearest. Ties keep the earlier sample. The returned pose is that sample, so query `50_000` with `tolerance` `50_000` on the synthetic vehicle is translation `(0, 0, 0)`, `dt_us` `-50_000`, not the interpolated `(5, 0, 0)`.
- Projection may interpolate, because the camera and the box can sit at different times. Discrete `at` does not.
- No integer rounding of pixels.
- `z <= 0` is behind the camera, including `z == 0`. A point in front with a null image size is `no_image_size`, not a guessed in or out.
- Computed pairs are not ground truth. Track `5` on the synthetic fixture (`car` vs `truck`) is `co_annotations` with `ground_truth` false and is not `AssociationKind.SHARED_TRACK`.
- The torch default collate is not used. Clouds differ in length, and a miss has no pose. `identity_collate` is the provided batch function.
- Dataset order with `seed` omitted is scene-directory name, then the reference agent's pose time. `stride` keeps every nth of those times. `seed` shuffles with `random.Random(seed)` in the parent before workers start.
- The reference agent is `agents[0]` when `agents` is set, otherwise the first manifest agent. Griffin scenes index the vehicle (7,000 samples), not the union of vehicle and drone times.
- `coperceive/dataset.py` and `tests/test_dataset.py` are excluded from pyright. The lint job does not install torch. Dataset tests call `pytest.importorskip("torch")`.
- Phase 2 Griffin choices stand: schema `1`, null visibility, drone lidar calibs without clouds, `time_us = frame_id * 100000`, east taken from the toolkit pose comment. Source: https://github.com/adarsh-nl/coperceive
- The Griffin licence acknowledgement uses `datetime.now(timezone.utc)`. `datetime.UTC` is Python 3.11+, and the 3.10 CI job failed while collecting the Griffin tests.

## Open questions for Adarsh

1. Should `Scene.at` grow an optional interpolated pose, or is the discrete sample the sample callers should train on?
2. Is +Z depth, with no pixel rounding, the camera contract to keep? It matches the Griffin toolkit's divide-by-z step and deliberately does not copy that toolkit's integer rounding.
3. `CooperativeDataset` indexes one reference agent. Say if the index should be the union of agent times, or a stride in seconds, instead of every nth pose.
4. Identity collate versus a padded cloud batch. Training code was not added.
5. CI does not install torch, so the worker test is skipped there. This VM ran it. Say if the CPU wheel should be added to the test job.
6. On this 4-vCPU VM, more DataLoader workers were slower than `num_workers=0` (each worker re-reads a scene). Say if a different shard, one scene per worker, is wanted.
7. Visibility. Keep null when the field is absent, or match the toolkit and store `1.0`?
8. Drone lidar calibs with no clouds. Keep them as sensors, or drop stems that have no point files?
9. East. The toolkit comment is the only evidence. A map check was not done.
10. Image paths are written for every camera calib even when the dev tier did not download images.
11. Schema number. Still `1`.
12. Viewer and a second dataset. Not this phase.

## Benchmark

Hardware, sampled just before the run on 2026-10-15: 4 vCPU, `/proc/cpuinfo` model name `Intel(R) Xeon(R) Processor` at `2400.000` MHz, cache size `327680 KB`, 15 GiB RAM (`free -h`: 11 GiB used, 4.5 GiB available, no swap), disk `/dev/vdc` 254G with 243G free. Python 3.12.3. `torch 2.14.1+cpu`. Page cache was warm: `/tmp/griffin-scenes` was already on disk and a fingerprint pass read it first.

`CooperativeDataset(/tmp/griffin-scenes, tolerance_us=0, include_points=True)`, `len` 7000, `DataLoader` `batch_size=4`, `shuffle=False`, `identity_collate`. Every sample had vehicle and drone matched (`dt_us` 0). Miss rows 0. Point count 19,922,961.

Unhashed iteration (count points and misses only):

- `num_workers=0`: 4.296 s, 1629.496 samples/s
- `num_workers=2`: 4.892 s, 1430.948 samples/s
- `num_workers=4`: 5.016 s, 1395.561 samples/s

Identical-output check, separate pass, SHA-256 over query time, per-agent match/`dt_us`/pose, categories, centres, and point bytes. `num_workers` 0 and 2 wrote the same digest file (`cmp` equal). Combined digest `efd7443ad07291f7c2b0b025adcc5d0fdee2253a092536b1013c9011ba51c1a8`. That pass was 4.898 s (1429.021 samples/s) and 5.126 s (1365.628 samples/s). The synthetic unit test compares full `CooperativeSample` equality for `num_workers` 0 and 2.

## Gates on this machine

Python 3.12.3. Commands and results from 2026-10-15:

- `ruff check .` — all checks passed (ruff 0.17.0)
- `ruff format --check .` — 64 files already formatted (ruff 0.17.0)
- `pyright` — 0 errors, 0 warnings (pyright 1.1.414, strict on the core). Excluded: `coperceive/app`, `coperceive/griffin`, `coperceive/dataset.py`, `tests/test_app.py`, `tests/test_griffin.py`, `tests/test_griffin_phase2.py`, `tests/test_dataset.py`
- `pytest -o addopts= -q` — 66 passed, 1 skipped, 6 warnings in 2.15s (pytest 9.1.1). The skip is `test_real_griffin_release_loads` without `COPERCEIVE_GRIFFIN_RAW`. The warnings are `DeprecationWarning` from `fork()` in the dataset worker test and the parallel convert test.
- `pytest --cov=coperceive --cov-report=term-missing -q` — exit 0, coverage 88% (2251 statements, 207 missed, branch coverage on)
- `python -m build --wheel` — built `dist/coperceive-0.0.0-py3-none-any.whl` (build 1.6.1, hatchling 1.32.4)
- Fresh venv `/tmp/coperceive-phase3-fresh`, run from `/tmp` so the source tree was not on `sys.path`: `pip install dist/coperceive-0.0.0-py3-none-any.whl`. `coperceive --help` printed “Cooperative multi-agent perception data tools.” and listed `schema`, `serve`, `download`, and `convert`. `coperceive schema` printed `1`. `import coperceive` loaded the wheel and exported `CooperativeSample`. `import coperceive.dataset` raised `ModuleNotFoundError: No module named 'torch'`.
- `mkdocs build` — succeeded (MkDocs 1.6.1, Material 9.7.7). Material printed its MkDocs 2.0 warning. The site was written to `site/`.
- `gitleaks detect --source . --redact --no-banner --exit-code 1` (8.28.0) — no leaks
- `gitleaks detect --no-git --source . --redact --no-banner --exit-code 1` — no leaks in the working tree (4.24 MB scanned)

The git-mode scan before this handover’s commit covered the commits already on the branch. The working tree, including this file, was the `--no-git` scan.

## Commands

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[app,griffin,dev,docs]"
.venv/bin/pip install torch --index-url https://download.pytorch.org/whl/cpu
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/pyright
.venv/bin/pytest
.venv/bin/pytest --cov=coperceive --cov-report=term-missing
.venv/bin/mkdocs build
coperceive download griffin --subset 25m --preset dev --dest /path/outside/the/repo --dry-run
coperceive convert griffin --subset 25m --raw-root /path/griffin-release --out /path/scenes
```

The Griffin-25m scenes used for the benchmark were already at `/tmp/griffin-scenes`. They are not in git.

## Next

Stop. Phase 3 is the cooperative sample, camera projection, cross-agent box queries, and the torch dataset. Do not start Phase 4 from this note.
