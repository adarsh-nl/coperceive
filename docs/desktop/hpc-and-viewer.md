# Desktop HPC and viewer behaviour

This note summarizes the `v0.1.2` desktop and remote-viewer changes across cluster datasets, geometry conventions, HPC model evaluation, and viewer job leases.

## HPC model evaluation

- **Task guide**: see [Evaluate a model on the HPC from the app](../guides/evaluate-on-hpc.md) for the full **Servers → Datasets → New evaluation → Submit this run → Job watch → Run history → Viewer** flow and verbatim error messages.
- **Per-run AP protocol (`PR #211`)**: **New evaluation** offers **Default** (`"OpenCOOD-style: per-frame ranking, ground truth from every connected agent within 70 m"`) and **Legacy (v0.1.1)** (`"for numbers comparable to v0.1.1"`). The choice is saved in the plan, passed as `--protocol legacy` when Legacy is selected, shown in **Job watch**, **Run history**, and **Viewer**, and enforced by the viewer's **Compare** menu.
- **Plugin interpreter (`PR #244`, `PR #213`)**: **Settings → Model runs** stores an optional per-server `--plugin-python` default, and **New evaluation** exposes **Plugin interpreter (optional)** for built-in models.
- **In-job scoring for built-in runs (`PR #245`)**: **New evaluation** enables **Score the run after it finishes** by default (`coperceive eval run` after the model finishes, keeping the model's exit code and exiting `5` when the model succeeded but scoring failed); **Job watch** displays the **Scoring** status and `AP protocol` from `evaluation.json`.
- **Optional roots and required `ckpt_root` (`PR #203`, `PR #204`, `PR #228`)**: **Media root** is optional; **Raw root** is required only when the model manifest declares `inputs.raw`; Griffin models require **Checkpoint root (`ckpt_root`)**.
- **Open predictions over the SSH stream path (`PR #247`, `PR #250`)**: **Job watch → Open predictions** and **Run history → Open predictions** copy the run's viewer files (`run.json`, `boxes.parquet`, `metrics.json`, `matches.parquet`, up to `512 MiB`) over SSH and open the viewer directly (`#/viewer?predictions=<plan>&scene=<scene>`) with no Slurm viewer job, reusing an already-open stream server for that dataset. Custom pipelines require opening the dataset in **Datasets** first so `predictions.jsonl` can be imported into `<output_dir>/viewer-predictions`. See [Cluster contract](../datasets/cluster-datasets.md#open-predictions-job-watch-run-history).

## Cluster datasets: stale detection and preparation

- **Manifest-change stale detection (`PR #209`)**: every prepared scene's `COMPLETE.json` records `manifest_sha256`. When a dataset's adapter manifest changes—such as `v0.1.2`'s OPV2V, V2XSet, and V2V4Real geometry fixes (`PR #207`)—cluster status marks previously prepared scenes **Stale** (`reason: "adapter manifest changed"`), **DatasetView** explains `"Coperceive now converts this dataset differently than when this scene was prepared (adapter manifest changed). Re-prepare it to view it with the current conversion."` with a **Re-prepare** action, and local `coperceive adapters ingest` rebuilds them instead of skipping. Markers without a recorded digest are kept.
- **Truncated raw checks (`PR #219`)**: raw-file walks stop at `200,000` entries or `30 s`. An incomplete walk reports `raw_check.state = "incomplete"` and never marks ready scenes stale; the dataset view displays `"Raw check incomplete: the cluster could not check every raw file in time, so prepared scenes stay ready and a change to the raw files may not show here."`
- **Prepare remaining (`PR #220`, `PR #236`)**: **Prepare remaining** submits only `not_prepared`, `failed`, and `stale` scenes, excluding `ready` scenes and scenes held by live jobs.
- **Canonical cache path & job naming (`PR #221`)**: cluster dataset caches use one canonical path regardless of trailing slashes, `..`, or symlinks, and prepare array jobs are named per dataset with per-task exit codes.

## Geometry and generic-multiview conventions

- **Adapter convention fixes (`PR #207`)**: fixed `OPV2V` and `V2XSet` roll/pitch Euler signs (`OPV-6`, `OPV-13`) and camera extrinsic inversion (`parent_to_child`, `OPV-7`), plus `V2V4Real` body-frame boxes (`frame: body`, `V4R-7`). See [Geometry conventions](../geometry/conventions.md).
- **Generic-multiview conventions (`PR #223`)**: [Generic format](../spec/generic-multiview.md) documents the four implicit rules implemented by `GenericAdapter` (`frame_rate_hz` rounding, sensor-frame LiDAR points, 3D geometric box centre in world metres, and `length` along `+x` with right-handed `Rz(yaw_deg)`).

## CLI and remote viewer lease renewal (`PR #248`)

- `coperceive remote start` viewer jobs hold a lease file (`~/.coperceive/sessions/<uuid>.heartbeat`) watched with `COPERCEIVE_HEARTBEAT_TIMEOUT` (default `600` s; `0` disables the lease).
- `remote_serve.sbatch` passes `coperceive serve --heartbeat-file <lease>` when the lease is enabled (`#248`). While the served viewer answers authenticated `/api` requests (excluding `/api/health` and unauthenticated `401` or `OPTIONS` requests), it renews the lease file at most once every `10` s and never recreates a removed lease.
- An idle viewer job stops renewing and still ends one lease after its last request. `coperceive remote heartbeat` remains available for manual renewal.
- Desktop **Datasets → Open scene** streams over SSH via `coperceive serve --remote-stream-argv` without a viewer job or lease. See [CLI reference](../reference/cli.md#coperceive-remote).

## More detail

- [Evaluate a model on the HPC from the app](../guides/evaluate-on-hpc.md)
- [Desktop settings](settings.md)
- [Cluster contract](../datasets/cluster-datasets.md)
- [Geometry conventions](../geometry/conventions.md)
