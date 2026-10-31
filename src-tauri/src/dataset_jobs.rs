// SPDX-License-Identifier: Apache-2.0
//! Preview and submit the CPU-only Slurm job that prepares a cluster dataset.
//!
//! The script is rendered from the profile and the cache path returned by
//! `dataset index`; nothing is submitted until the user confirms the exact
//! preview hash. Preparation never reads or writes the raw root from the login
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
    pub workers: u32,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct DatasetPreparePreview {
    pub plan_id: String,
    pub job_name: String,
    pub partition: String,
    pub walltime: String,
    pub cpus_per_task: u32,
    pub gres: Option<String>,
    pub cache_root: String,
    pub estimated_cache_bytes: u64,
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

/// The workers a prepare job actually uses: omitted = min(selected scenes, 8),
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
        "--cache".to_string(),
        cache.to_string(),
        "--workers".to_string(),
        request.workers.to_string(),
    ];
    if let Some(scenes) = &request.scenes {
        args.push("--scenes".to_string());
        args.push(scenes.join(","));
    } else {
        args.push("--all".to_string());
    }
    coperceive_argv(profile, &args)
}

/// Render the full prepare script; `estimated_cache_bytes` comes from index.
pub fn render_prepare_script(
    profile: &ServerProfile,
    request: &DatasetPrepareRequest,
    cache_relative: &str,
    estimated_cache_bytes: u64,
) -> Result<DatasetPreparePreview, String> {
    if request.workers == 0 {
        return Err("Choose at least one worker for dataset preparation.".to_string());
    }
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
        format!("#SBATCH --cpus-per-task={}", request.workers),
        format!(
            "#SBATCH --output={}/dataset-prepare-%j.out",
            results_root.trim_end_matches('/')
        ),
        format!(
            "#SBATCH --error={}/dataset-prepare-%j.err",
            results_root.trim_end_matches('/')
        ),
        "# No GPU requested (CPU-only job).".to_string(),
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
        "{}",
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
        cpus_per_task: request.workers,
        gres: None,
        cache_root: cache.clone(),
        estimated_cache_bytes,
        preview_hash: preview_hash(&script),
        script,
    })
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
        }
    }

    #[test]
    fn preview_is_cpu_only_uses_the_workers_and_has_a_job_scratch_trap() {
        let entry = entry();
        let preview = render_prepare_script(
            &entry.profile,
            &request(),
            "coperceive-cache/opv2v/abc123/test",
            12_345,
        )
        .expect("preview");
        assert_eq!(preview.cpus_per_task, 4);
        assert_eq!(preview.gres, None);
        assert_eq!(preview.estimated_cache_bytes, 12_345);
        assert!(preview.cache_root.ends_with("coperceive-cache/opv2v/abc123/test"));
        assert!(preview.script.contains("# No GPU requested (CPU-only job)."));
        assert!(preview.script.contains("/coperceive-${SLURM_JOB_ID}"));
        assert!(preview.script.contains("trap 'rm -rf -- \"$SCRATCH_DIR\"' EXIT"));
        assert!(preview.script.contains("dataset prepare"));
        assert!(preview.script.contains("--workers 4"));
        assert!(preview.script.contains("--scenes scene_0001"));
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
            "coperceive-cache/opv2v/abc/test",
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
        assert!(argv.contains(&"--all".to_string()) || argv.contains(&"--scenes".to_string()));
    }
}
