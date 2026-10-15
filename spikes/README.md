# Spikes

These scripts were run against files in `/tmp`, which are not in git. They are the source of the numbers in `docs/design/measurements/`.

Do not point them at a checkout of another project. Griffin bytes come from the Hugging Face dataset `wjh-svm/Griffin`, subset `griffin_50scenes_25m`, files `md5.txt`, `drone_metadata.zip`, `vehicle_metadata.zip`, and `vehicle_lidar.zip`.

## Format

`bench_formats.py` writes `/tmp/spikes/format_results.json`. Use that file for write time and size. Its read timings for Arrow, the hybrid blob, and MCAP are not the ones cited in ADR 0002.

`format_latency_v2.json` is the corrected read timing. The driver that wrote it was not kept as a file. What it changed, relative to the first script: every read reduces the loaded values to a checksum, and MCAP queries use an exclusive `end_time` of `t + 1`.

Versions recorded with that run: PyArrow 26.0.0, zarr 3.4.1, h5py 3.16.0, mcap 1.5.0, NumPy 2.5.4.

## Viewers

`viewers.py` needs `rerun-sdk` and writes `/tmp/spikes/viewer/summary_partial.json` and a downsampled `frames.json`. `viser_server.py` serves that payload on `127.0.0.1:8766`. `three_server.py` plus `three_index.html` and `raf.mjs` produced `three_result.json`.

Camera zips were not downloaded. No image decode rate was measured.

## Docs and web

MkDocs 1.6.1 / Material 9.7.7 built a one-page site in 0.408 s wall time. Sphinx 9.1.0 with MyST 5.1.0 built the same page in 0.505 s. FastAPI `create_app` plus 100 health requests, and the same for a one-route Starlette app, are in `docs/design/measurements/doc_and_web.json`.

## Not run

- `python3 -c "import torch"` did not find torch. A `torch.utils.data.DataLoader` with workers was not run.
- gitleaks 8.28.0 was downloaded and run as `gitleaks detect --no-git --source /workspace` on 2026-10-15. It reported no leaks. CI runs the same release against the git checkout. Python 3.10, 3.11, and 3.13 were not installed here, so that matrix has not run locally.
- Python 3.10, 3.11, and 3.13 were not installed on this machine. CI is the matrix. Local gates ran on 3.12.3.
