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
use sha1::{Digest, Sha1};

use crate::hpc::{coperceive_argv, is_under, normalise_path, ServerProfile};
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

pub fn cache_slug(adapter: &str, raw_root: &str, split: Option<&str>) -> String {
    let mut hasher = Sha1::new();
    hasher.update(raw_root.as_bytes());
    let digest = hasher.finalize();
    let short: String = digest.iter().take(6).map(|byte| format!("{byte:02x}")).collect();
    format!(
        "coperceive-cache/{adapter}/{short}/{}",
        split.unwrap_or("all")
    )
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
    let results_root = normalise_path(&profile.results_root, "results_root")?;
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
    let job_name = format!(
        "coperceive-dataset-{}",
        cache_relative.rsplit('/').next().unwrap_or("prepare")
    );
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

pub fn cache_absolute(
    profile: &ServerProfile,
    adapter: &str,
    raw_root: &str,
    split: Option<&str>,
) -> Result<String, String> {
    let results = normalise_path(&profile.results_root, "results_root")?;
    Ok(format!(
        "{}/{}",
        results.trim_end_matches('/'),
        cache_slug(adapter, raw_root, split)
    ))
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
