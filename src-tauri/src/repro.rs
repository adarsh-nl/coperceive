// SPDX-License-Identifier: Apache-2.0
//! Build a repro card by asking the bundled Python exporter.
//!
//! The desktop copies the small run files it can read (plan, environment,
//! pipeline report, perturbation, registration) into a local mirror of
//! `results_root`, then runs `coperceive repro export` as an argv list. The
//! predictions file is hashed on the server with one bounded `sha256sum`; the
//! bytes are not pulled to the laptop. A test launch can point
//! `COPERCEIVE_TEST_REPRO_DIR` at a local fixture instead of SSH.

use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::Duration;

use crate::hpc::ServerProfile;
use crate::job_submit::load_saved_plan;
use crate::run_plan::RunPlan;
use crate::slurm_adapter::RemoteRunner;
use crate::ssh::shell_quote;
use crate::test_seams::test_env_active;

const READ_TIMEOUT: Duration = Duration::from_secs(15);
const HASH_TIMEOUT: Duration = Duration::from_secs(20);
const MAX_FILE_BYTES: usize = 256 * 1024;
/// Plan id the real-app e2e uses. Only honoured while the test seam is on.
pub const TEST_PLAN_ID: &str = "plan-1790000000-aabbccddeeff";

/// `COPERCEIVE_TEST_REPRO_DIR` when the app was launched by the e2e harness.
pub fn test_repro_dir() -> Option<PathBuf> {
    if !test_env_active() {
        return None;
    }
    let value = std::env::var("COPERCEIVE_TEST_REPRO_DIR").ok()?;
    let trimmed = value.trim();
    if trimmed.is_empty() {
        return None;
    }
    Some(PathBuf::from(trimmed))
}

/// Run `coperceive repro export` and parse the card from stdout.
pub fn card_from_profile(
    python: &Path,
    run_id: &str,
    profile: &Path,
    time_limit: Option<&str>,
    gpus: Option<&str>,
    memory: Option<&str>,
) -> Result<serde_json::Value, String> {
    if run_id.contains('/') || run_id.contains('\\') || run_id.contains('\0') || run_id.is_empty() {
        return Err("That run id is not a file name.".to_string());
    }
    let mut args = vec![
        "-m".to_string(),
        "coperceive".to_string(),
        "repro".to_string(),
        "export".to_string(),
        run_id.to_string(),
        "--profile".to_string(),
        profile.display().to_string(),
        "--stdout".to_string(),
    ];
    push_plain(&mut args, "--time-limit", time_limit);
    push_plain(&mut args, "--gpus", gpus);
    push_plain(&mut args, "--memory", memory);
    let output = Command::new(python)
        .args(&args)
        .output()
        .map_err(|error| format!("Could not start Python to build the repro card: {error}"))?;
    if !output.status.success() {
        let detail = String::from_utf8_lossy(&output.stderr);
        let line = detail
            .lines()
            .find(|line| !line.trim().is_empty())
            .unwrap_or("The repro card could not be built.");
        return Err(line.trim().to_string());
    }
    serde_json::from_slice(&output.stdout)
        .map_err(|error| format!("The repro card was not valid JSON: {error}"))
}

/// Copy the plan and the small run files into `dest` and return a profile whose
/// `results_root` is that local copy.
pub fn stage_remote_run(
    runner: &dyn RemoteRunner,
    profile: &ServerProfile,
    plan_id: &str,
    dest: &Path,
) -> Result<PathBuf, String> {
    let plan = load_saved_plan(runner, profile, plan_id)?;
    let results = dest.join("results");
    let plans = results.join("plans");
    let run = results.join("runs").join(plan_id);
    std::fs::create_dir_all(&plans)
        .map_err(|error| format!("Could not prepare the card: {error}"))?;
    std::fs::create_dir_all(&run)
        .map_err(|error| format!("Could not prepare the card: {error}"))?;
    let stored = plan.clone();
    if let Some(text) = read_capped(runner, &stored.output_dir, "environment.json") {
        write_child(&run, "environment.json", &text)?;
    }
    if let Some(text) = read_capped(runner, &stored.output_dir, "perturbation.json") {
        write_child(&run, "perturbation.json", &text)?;
    }
    if let Some(text) = read_capped(runner, &stored.output_dir, "metrics.json") {
        write_child(&run, "metrics.json", &text)?;
    }
    if let Some(hash) = remote_sha256(runner, &stored.output_dir, "predictions.jsonl") {
        write_child(&run, "predictions.sha256", &format!("{hash}\n"))?;
    }
    if let Some(text) = registration_text(runner, &stored) {
        write_child(&run, "coperceive-pipeline.json", &text)?;
    }
    let plan_path = plans.join(format!("{plan_id}.json"));
    let plan_bytes = serde_json::to_vec_pretty(&stored)
        .map_err(|error| format!("Could not store the plan for the card: {error}"))?;
    std::fs::write(&plan_path, plan_bytes)
        .map_err(|error| format!("Could not store the plan for the card: {error}"))?;
    let mut local = profile.clone();
    local.results_root = results.display().to_string();
    let profile_path = dest.join("profile.json");
    let profile_bytes = serde_json::to_vec_pretty(&local)
        .map_err(|error| format!("Could not store the profile for the card: {error}"))?;
    std::fs::write(&profile_path, profile_bytes)
        .map_err(|error| format!("Could not store the profile for the card: {error}"))?;
    Ok(profile_path)
}

fn push_plain(args: &mut Vec<String>, flag: &str, value: Option<&str>) {
    let Some(text) = value.map(str::trim).filter(|text| !text.is_empty()) else {
        return;
    };
    if text.contains('\n') || text.contains('\0') {
        return;
    }
    args.push(flag.to_string());
    args.push(text.to_string());
}

fn write_child(dir: &Path, name: &str, text: &str) -> Result<(), String> {
    std::fs::write(dir.join(name), text)
        .map_err(|error| format!("Could not store {name} for the card: {error}"))
}

fn remote_file(output_dir: &str, name: &str) -> Option<String> {
    if name.contains('/')
        || name.contains('\0')
        || output_dir.contains('\n')
        || output_dir.contains('\0')
    {
        return None;
    }
    Some(format!("{}/{}", output_dir.trim_end_matches('/'), name))
}

fn read_capped(runner: &dyn RemoteRunner, output_dir: &str, name: &str) -> Option<String> {
    let path = remote_file(output_dir, name)?;
    let command = format!(
        "sh -c 'head -c {MAX_FILE_BYTES} -- \"$1\"' sh {}",
        shell_quote(&path)
    );
    let result = runner.run(&command, READ_TIMEOUT, None).ok()?;
    if !result.success || result.stdout.trim().is_empty() {
        return None;
    }
    Some(result.stdout)
}

fn remote_sha256(runner: &dyn RemoteRunner, output_dir: &str, name: &str) -> Option<String> {
    let path = remote_file(output_dir, name)?;
    let command = format!("sha256sum -- {}", shell_quote(&path));
    let result = runner.run(&command, HASH_TIMEOUT, None).ok()?;
    if !result.success {
        return None;
    }
    let hex = result.stdout.split_whitespace().next().unwrap_or("");
    if hex.len() == 64 && hex.chars().all(|ch| ch.is_ascii_hexdigit()) {
        Some(hex.to_ascii_lowercase())
    } else {
        None
    }
}

fn registration_text(runner: &dyn RemoteRunner, plan: &RunPlan) -> Option<String> {
    let script = plan
        .argv
        .iter()
        .find(|item| item.ends_with(".py") || item.ends_with(".sh"))?;
    let parent = script.rsplit_once('/').map(|(dir, _)| dir).unwrap_or("");
    if parent.is_empty() {
        return None;
    }
    let path = format!("{parent}/coperceive-pipeline.json");
    let command = format!(
        "sh -c 'head -c {MAX_FILE_BYTES} -- \"$1\"' sh {}",
        shell_quote(&path)
    );
    let result = runner.run(&command, READ_TIMEOUT, None).ok()?;
    if !result.success || result.stdout.trim().is_empty() {
        return None;
    }
    Some(result.stdout)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::hpc::{EnvironmentKind, EnvironmentSetup};
    use crate::ssh::CommandResult;
    use std::sync::Mutex;

    fn profile() -> ServerProfile {
        ServerProfile {
            name: "Fixture".into(),
            ssh_alias: Some("fixture".into()),
            host: None,
            user: None,
            data_roots: vec!["/data/scenes".into()],
            scratch_template: "/scratch/$USER/$SLURM_JOB_ID".into(),
            results_root: "/data/results".into(),
            code_roots: vec!["/data/code".into()],
            scheduler: crate::hpc::SchedulerKind::Slurm,
            defaults: crate::hpc::JobDefaults::default(),
            environment: EnvironmentSetup {
                kind: EnvironmentKind::None,
                ..EnvironmentSetup::default()
            },
            notes: None,
        }
    }

    struct FakeRunner {
        calls: Mutex<Vec<String>>,
    }

    impl FakeRunner {
        fn new() -> Self {
            Self {
                calls: Mutex::new(Vec::new()),
            }
        }
    }

    impl RemoteRunner for FakeRunner {
        fn run(
            &self,
            remote_cmd: &str,
            _: Duration,
            _: Option<&str>,
        ) -> Result<CommandResult, String> {
            self.calls.lock().unwrap().push(remote_cmd.to_string());
            if remote_cmd.contains("plans/") {
                let plan = serde_json::json!({
                    "schema": "coperceive-run-plan-1",
                    "id": TEST_PLAN_ID,
                    "created_unix": 1,
                    "profile_id": "fixture",
                    "profile_name": "Fixture",
                    "pipeline": {"id": "synthetic", "name": "synthetic", "source": "custom"},
                    "argv": ["python3", "/data/code/pipelines/run.py", "--checkpoint", "/data/scenes/model.pt"],
                    "environment": {"kind": "none"},
                    "checkpoint": {"path": "/data/scenes/model.pt", "sha256": "ab".repeat(32), "kind": "file"},
                    "split": {"path": "", "kind": ""},
                    "output_dir": "/data/results/runs/plan-1790000000-aabbccddeeff",
                    "scratch_dir": "/scratch/$USER/$SLURM_JOB_ID",
                    "checks": [],
                    "contract_check_script": "",
                    "nothing_submitted": true
                });
                return Ok(CommandResult {
                    success: true,
                    stdout: plan.to_string(),
                    stderr: String::new(),
                });
            }
            if remote_cmd.contains("environment.json") {
                return Ok(CommandResult {
                    success: true,
                    stdout: "{\"schema\":\"coperceive-environment\",\"schema_version\":1}\n".into(),
                    stderr: String::new(),
                });
            }
            if remote_cmd.contains("sha256sum") {
                return Ok(CommandResult {
                    success: true,
                    stdout: format!("{}  predictions.jsonl\n", "cd".repeat(32)),
                    stderr: String::new(),
                });
            }
            Ok(CommandResult {
                success: false,
                stdout: String::new(),
                stderr: "missing".into(),
            })
        }
    }

    #[test]
    fn stage_remote_run_copies_small_files_and_not_the_prediction_bytes() {
        let runner = FakeRunner::new();
        let dest =
            std::env::temp_dir().join(format!("coperceive-repro-stage-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dest);
        let profile_path =
            stage_remote_run(&runner, &profile(), TEST_PLAN_ID, &dest).expect("stage");
        let environment = dest
            .join("results")
            .join("runs")
            .join(TEST_PLAN_ID)
            .join("environment.json");
        assert!(environment.is_file());
        let hash = std::fs::read_to_string(
            dest.join("results")
                .join("runs")
                .join(TEST_PLAN_ID)
                .join("predictions.sha256"),
        )
        .unwrap();
        assert_eq!(hash.trim(), "cd".repeat(32));
        assert!(!dest
            .join("results")
            .join("runs")
            .join(TEST_PLAN_ID)
            .join("predictions.jsonl")
            .exists());
        let stored: serde_json::Value =
            serde_json::from_str(&std::fs::read_to_string(profile_path).unwrap()).unwrap();
        assert_eq!(
            stored["results_root"],
            dest.join("results").display().to_string()
        );
        let calls = runner.calls.lock().unwrap().clone();
        assert!(calls.iter().any(|call| call.contains("sha256sum")));
        assert!(calls
            .iter()
            .all(|call| !call.contains("predictions.jsonl\n{")));
        let _ = std::fs::remove_dir_all(&dest);
    }
}
