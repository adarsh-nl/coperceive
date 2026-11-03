// SPDX-License-Identifier: Apache-2.0
//! Preview and submit the CPU-only Slurm job that prepares a cluster dataset.
//!
//! The script is rendered from the profile and the cache path returned by
//! `dataset index`; nothing is submitted until the user confirms the exact
//! preview hash. It is a job array: one 1-CPU task per scene, at most
//! `throttle` running at once (`--array=0-N%throttle`), so scenes start as
//! single CPUs free up and none waits for a whole multi-CPU allocation. Preparation never reads or writes the raw root from the login
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

/// Scenes a prepare array runs at once: omitted = min(selected scenes, 8),
/// always clamped to 1..=16.
pub fn resolve_workers(requested: Option<u32>, selected_scenes: usize) -> u32 {
    requested
        .unwrap_or(selected_scenes.min(8) as u32)
        .clamp(1, 16)
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
    if !matches!(request.adapter.as_str(), "griffin" | "opv2v" | "v2xset") {
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
        String::new(),
        "set -euo pipefail".to_string(),
    ];
    lines.push(format!(
        "SCRATCH_DIR=\"{}\"",
        job_scratch_dir(&profile.scratch_template)
    ));
    lines.push("mkdir -p -m 0700 -- \"$SCRATCH_DIR\"".to_string());
    lines.push("trap 'rm -rf -- \"$SCRATCH_DIR\"' EXIT".to_string());
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
        gres: None,
        cache_root: cache.clone(),
        estimated_cache_bytes,
        images: request.images.clone(),
        estimated_preview_bytes,
        preview_hash: preview_hash(&script),
        script,
    })
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
