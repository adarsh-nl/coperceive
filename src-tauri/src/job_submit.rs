// SPDX-License-Identifier: Apache-2.0
//! Remote HPC 4/6: submit a saved run plan to the scheduler.
//!
//! The user first previews the complete generated job script, then explicitly
//! confirms; nothing is ever submitted automatically. The submitted script is
//! rendered again on the server side from the saved plan, so the preview and the
//! submission describe the same run. No cluster value comes from a constant: the
//! partition, account, QoS, GRES, scratch folder and results folder all come
//! from the server profile (or the plan's validated environment).

use std::hash::{Hash, Hasher};
use std::time::Duration;

use serde::{Deserialize, Serialize};

use crate::hpc::{EnvironmentKind, JobState, ServerProfile};
use crate::run_plan::{RunPlan, strip_output_dir, validate_plan_id};
use crate::slurm_adapter::{RemoteRunner, map_slurm_state};
use crate::ssh::shell_quote;

const READ_TIMEOUT: Duration = Duration::from_secs(8);
const SUBMIT_TIMEOUT: Duration = Duration::from_secs(30);
const STATUS_TIMEOUT: Duration = Duration::from_secs(15);

/// Default wall time for a submitted run; the user can edit it under Advanced.
pub const DEFAULT_WALLTIME: &str = "04:00:00";
/// Number of frames a contract check runs (the plan's stored script), for reference.
pub const SUBMIT_TIMEOUT_SECS: u64 = SUBMIT_TIMEOUT.as_secs();

fn default_walltime() -> String {
    DEFAULT_WALLTIME.to_string()
}

/// What the user can change in Advanced before submitting.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SubmitOptions {
    /// Slurm time limit; defaults to four hours.
    #[serde(default = "default_walltime")]
    pub walltime: String,
    #[serde(default)]
    pub cpus_per_task: Option<u32>,
    #[serde(default)]
    pub account: Option<String>,
    #[serde(default)]
    pub qos: Option<String>,
}

impl Default for SubmitOptions {
    fn default() -> Self {
        Self {
            walltime: default_walltime(),
            cpus_per_task: None,
            account: None,
            qos: None,
        }
    }
}

/// The complete script shown before submission, plus a hash of exactly that text.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SubmissionPreview {
    pub plan_id: String,
    pub job_name: String,
    pub partition: String,
    pub walltime: String,
    pub cpus_per_task: Option<u32>,
    pub script: String,
    /// Hash of `script`; the confirm call must send it back unchanged.
    pub preview_hash: String,
}

/// What happened when the user confirmed a submission.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct JobSubmitOutcome {
    pub job_id: String,
    pub state: JobState,
    /// Slurm's queue reason when the job is pending (e.g. "Resources").
    #[serde(default)]
    pub queue_reason: Option<String>,
    /// The plan the submitted job belongs to.
    pub plan_id: String,
}

fn script_hash(script: &str) -> String {
    let mut hasher = std::collections::hash_map::DefaultHasher::new();
    script.hash(&mut hasher);
    format!("{:016x}", hasher.finish())
}

pub(crate) fn safe_job_name(plan_id: &str) -> String {
    let clean: String = plan_id
        .chars()
        .map(|ch| {
            if ch.is_ascii_alphanumeric() || ch == '-' || ch == '_' {
                ch
            } else {
                '-'
            }
        })
        .collect();
    format!("coperceive-run-{clean}")
}

/// Reads the plan the app saved under `results_root/plans/<id>.json` (0600).
///
/// Submission never trusts a client-supplied plan body: the server's saved copy
/// is the input, which the save step already revalidated against the profile.
pub fn load_saved_plan(
    runner: &dyn RemoteRunner,
    profile: &ServerProfile,
    plan_id: &str,
) -> Result<RunPlan, String> {
    validate_plan_id(plan_id)?;
    let path = format!(
        "{}/plans/{}.json",
        profile.results_root.trim_end_matches('/'),
        plan_id
    );
    profile.validate_output_dir(&path)?;
    let command = format!("sh -c 'cat -- \"$1\"' sh {}", shell_quote(&path));
    let result = runner
        .run(&command, READ_TIMEOUT, None)
        .map_err(|error| format!("Could not read the saved plan: {error}"))?;
    if !result.success {
        return Err(format!(
            "No readable saved plan at {path}. Save the plan first, then submit it."
        ));
    }
    let plan: RunPlan = serde_json::from_str(&result.stdout)
        .map_err(|error| format!("The saved plan is not valid JSON: {error}"))?;
    if plan.id != plan_id {
        return Err("The saved plan has a different id than the one requested.".to_string());
    }
    if !plan.nothing_submitted {
        return Err("This plan records a submission; it cannot be submitted again.".to_string());
    }
    Ok(plan)
}

/// Always give a job its own scratch subdirectory. The profile template may be a
/// per-user folder such as `/local/alice`; removing that on exit would delete the
/// user's whole scratch. A template that already ends in SLURM_JOB_ID is used
/// as-is because it is already job-owned.
fn job_scratch_dir(template: &str) -> String {
    let trimmed = template.trim_end_matches('/');
    if trimmed.ends_with("$SLURM_JOB_ID") || trimmed.ends_with("${SLURM_JOB_ID}") {
        trimmed.to_string()
    } else {
        format!("{trimmed}/coperceive-${{SLURM_JOB_ID}}")
    }
}

/// Renders the complete Slurm job for the full run described by `plan`.
///
/// Deterministic: the same plan and options render the same script, so the
/// confirm call can recompute it and require the user's preview hash to match.
pub fn render_run_script(
    profile: &ServerProfile,
    plan: &RunPlan,
    options: &SubmitOptions,
) -> Result<SubmissionPreview, String> {
    if !plan.nothing_submitted {
        return Err("This plan records a submission; it cannot be submitted again.".to_string());
    }
    validate_plan_id(&plan.id)?;
    profile.validate()?;
    plan.environment.validate()?;
    validate_plan_destinations(profile)?;
    profile.validate_output_dir(&plan.output_dir)?;
    if plan.argv.is_empty()
        || plan.argv.iter().any(|element| element.is_empty() || element.contains('\n'))
    {
        return Err("The plan's command arguments are empty or contain a line break.".to_string());
    }

    let mut defaults = profile.defaults.clone();
    defaults.time_limit = Some(options.walltime.clone());
    if let Some(cpus) = options.cpus_per_task {
        defaults.cpus_per_task = Some(cpus);
    }
    if let Some(account) = options.account.as_deref().filter(|value| !value.trim().is_empty()) {
        defaults.account = Some(account.to_string());
    }
    if let Some(qos) = options.qos.as_deref().filter(|value| !value.trim().is_empty()) {
        defaults.qos = Some(qos.to_string());
    }
    defaults.validate()?;
    let partition = defaults
        .partition
        .as_deref()
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .ok_or_else(|| {
            "Choose a partition on the server profile before submitting a job.".to_string()
        })?;
    let job_name = safe_job_name(&plan.id);
    let safe_output = plan.output_dir.trim_end_matches('/').to_string();
    let log_dir = profile.results_root.trim_end_matches('/');
    let mut lines: Vec<String> = vec![
        "#!/bin/bash".to_string(),
        format!("# Full evaluation run for plan {}.", plan.id),
        format!("# Pipeline: {}.", plan.pipeline.name),
        "# Rendered by Coperceive from the saved plan and the server profile.".to_string(),
        "# The user reviewed this exact script before confirming the submission.".to_string(),
        format!("#SBATCH --job-name={job_name}"),
        format!("#SBATCH --partition={partition}"),
        format!("#SBATCH --time={}", defaults.time_limit.clone().unwrap_or_default()),
    ];
    if let Some(memory) = defaults.memory.as_deref() {
        lines.push(format!("#SBATCH --mem={memory}"));
    }
    if let Some(cpus) = defaults.cpus_per_task {
        lines.push(format!("#SBATCH --cpus-per-task={cpus}"));
    }
    if let Some(gres) = defaults.gres.as_deref() {
        lines.push(format!("#SBATCH --gres={gres}"));
    }
    if let Some(account) = defaults.account.as_deref() {
        lines.push(format!("#SBATCH --account={account}"));
    }
    if let Some(qos) = defaults.qos.as_deref() {
        lines.push(format!("#SBATCH --qos={qos}"));
    }
    lines.push(format!("#SBATCH --output={log_dir}/{job_name}-%j.out"));
    lines.push(format!("#SBATCH --error={log_dir}/{job_name}-%j.err"));
    lines.push(String::new());
    lines.push("set -euo pipefail".to_string());
    lines.push(format!(
        "SCRATCH_DIR=\"{}\"",
        job_scratch_dir(&profile.scratch_template)
    ));
    lines.push("mkdir -p -m 0700 -- \"$SCRATCH_DIR\"".to_string());
    lines.push("trap 'rm -rf -- \"$SCRATCH_DIR\"' EXIT".to_string());
    lines.push("export TMPDIR=\"$SCRATCH_DIR/tmp\"".to_string());
    lines.push("mkdir -p -m 0700 -- \"$TMPDIR\"".to_string());
    lines.push(format!("OUT_DIR=\"{}\"", safe_output));
    lines.push("mkdir -p -m 0700 -- \"$OUT_DIR\"".to_string());
    lines.push(String::new());
    render_environment(&plan.environment, &mut lines)?;
    lines.push(format!(
        "echo \"Starting run for plan {} on $(hostname)\"",
        plan.id
    ));

    let mut command = plan.argv.clone();
    // The app appends its own --output-dir; remove the plan's copy in either
    // form so the script cannot contain two different output directories.
    strip_output_dir(&mut command);
    let run_command = format!("{} --output-dir \"$OUT_DIR\"", quoted_command(&command));
    match plan.environment.kind {
        EnvironmentKind::Container => {
            let image = plan
                .environment
                .container_image
                .as_deref()
                .filter(|image| !image.is_empty())
                .ok_or_else(|| {
                    "This plan uses a container environment but has no image path.".to_string()
                })?;
            lines.push(format!(
                "apptainer exec --cleanenv --bind \"$SCRATCH_DIR\" --bind \"$OUT_DIR\" {} {run_command}",
                shell_quote(image)
            ));
        }
        _ => lines.push(run_command),
    }
    lines.push(String::new());
    lines.push(
        "test -s \"$OUT_DIR/predictions.jsonl\" || { echo \"The run wrote no \
         predictions.jsonl; it does not meet the output contract.\" >&2; exit 1; }"
            .to_string(),
    );
    lines.push(
        "test -s \"$OUT_DIR/metrics.json\" || { echo \"The run wrote no metrics.json; it does \
         not meet the output contract.\" >&2; exit 1; }"
            .to_string(),
    );
    lines.push(format!(
        "echo \"Run finished for plan {}: $OUT_DIR\"",
        plan.id
    ));
    lines.push(String::new());
    let script = lines.join("\n");
    let preview_hash = script_hash(&script);
    Ok(SubmissionPreview {
        plan_id: plan.id.clone(),
        job_name,
        partition: partition.to_string(),
        walltime: defaults.time_limit.clone().unwrap_or_default(),
        cpus_per_task: defaults.cpus_per_task,
        script,
        preview_hash,
    })
}

fn render_environment(
    environment: &crate::hpc::EnvironmentSetup,
    lines: &mut Vec<String>,
) -> Result<(), String> {
    match environment.kind {
        EnvironmentKind::Modules => {
            if !environment.modules.is_empty() {
                lines.push("module purge".to_string());
                for module in &environment.modules {
                    lines.push(format!("module load {}", shell_quote(module)));
                }
            }
        }
        EnvironmentKind::Conda | EnvironmentKind::Venv => {
            if let Some(interpreter) = environment
                .interpreter
                .as_deref()
                .filter(|value| !value.is_empty())
            {
                lines.push(format!(
                    "export COPERCEIVE_PYTHON={}",
                    shell_quote(interpreter)
                ));
                let parent = std::path::Path::new(interpreter)
                    .parent()
                    .map(|path| path.to_string_lossy().to_string())
                    .unwrap_or_default();
                lines.push(format!("export PATH={}:$PATH", shell_quote(&parent)));
            }
        }
        EnvironmentKind::None | EnvironmentKind::Container => {}
    }
    if let Some(wheelhouse) = environment
        .wheelhouse
        .as_deref()
        .filter(|value| !value.is_empty())
    {
        lines.push(format!(
            "export PIP_FIND_LINKS={}\nexport PIP_NO_INDEX=1",
            shell_quote(wheelhouse)
        ));
    }
    Ok(())
}

fn quoted_command(argv: &[String]) -> String {
    argv.iter()
        .map(|element| shell_quote(element))
        .collect::<Vec<_>>()
        .join(" ")
}

fn validate_plan_destinations(profile: &ServerProfile) -> Result<(), String> {
    crate::run_plan::validate_plan_destinations(profile)
}

/// Parses `sbatch --parsable` output and requires a numeric job id.
fn parse_job_id(stdout: &str) -> Result<String, String> {
    let first = stdout
        .split_whitespace()
        .next()
        .unwrap_or("")
        .trim()
        .trim_end_matches(';');
    let id = first.split(';').next().unwrap_or("").trim();
    if id.is_empty() || !id.chars().all(|ch| ch.is_ascii_digit()) || id.len() > 20 {
        return Err(format!(
            "The scheduler returned {stdout:?}, which is not a job id. Nothing was submitted \
             by coperceive."
        ));
    }
    Ok(id.to_string())
}

/// Submits a saved plan after the user confirmed the previewed script.
///
/// `confirmed` must be true and `preview_hash` must match the script rendered
/// now; otherwise nothing is submitted.
pub fn submit_saved_plan(
    runner: &dyn RemoteRunner,
    profile: &ServerProfile,
    plan_id: &str,
    options: &SubmitOptions,
    preview_hash: &str,
    confirmed: bool,
) -> Result<JobSubmitOutcome, String> {
    if !confirmed {
        return Err(
            "Read the full job script and tick the confirmation before submitting. Nothing was \
             submitted."
                .to_string(),
        );
    }
    let plan = load_saved_plan(runner, profile, plan_id)?;
    let preview = render_run_script(profile, &plan, options)?;
    if preview.preview_hash != preview_hash {
        return Err(
            "The rendered script changed since you reviewed it. Preview it again before \
             submitting. Nothing was submitted."
                .to_string(),
        );
    }
    let submitted = runner
        .run("sbatch --parsable", SUBMIT_TIMEOUT, Some(&preview.script))
        .map_err(|error| format!("Could not submit the job: {error}"))?;
    if !submitted.success {
        return Err(format!(
            "The scheduler rejected the submission: {}",
            if submitted.stderr.trim().is_empty() {
                submitted.stdout.trim()
            } else {
                submitted.stderr.trim()
            }
        ));
    }
    let job_id = parse_job_id(&submitted.stdout)?;
    let (state, queue_reason) = job_state(runner, &job_id);
    Ok(JobSubmitOutcome {
        job_id,
        state,
        queue_reason,
        plan_id: plan.id,
    })
}

/// Queries the job state (and, while pending, Slurm's queue reason).
pub fn job_state(runner: &dyn RemoteRunner, job_id: &str) -> (JobState, Option<String>) {
    let command = format!(
        "squeue -h -j {} -o '%T|%r' 2>/dev/null",
        job_id
    );
    let mut state = JobState::Unknown;
    let mut reason: Option<String> = None;
    if let Ok(result) = runner.run(&command, STATUS_TIMEOUT, None) {
        if result.success {
            if let Some(line) = result.stdout.lines().find(|line| !line.trim().is_empty()) {
                let mut parts = line.splitn(2, '|');
                let raw_state = parts.next().unwrap_or("").trim();
                if !raw_state.is_empty() {
                    state = map_slurm_state(raw_state);
                }
                let raw_reason = parts.next().unwrap_or("").trim();
                if !raw_reason.is_empty() && raw_reason != "None" {
                    reason = Some(raw_reason.to_string());
                }
            }
        }
    }
    if state == JobState::Unknown {
        let fallback = format!("sacct -j {job_id} -n -X -o State%20 2>/dev/null");
        if let Ok(result) = runner.run(&fallback, STATUS_TIMEOUT, None) {
            if result.success {
                if let Some(line) = result.stdout.lines().find(|line| !line.trim().is_empty()) {
                    state = map_slurm_state(line.trim());
                }
            }
        }
    }
    (state, reason)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::hpc::{EnvironmentSetup, SchedulerKind};
    use crate::slurm_adapter::RemoteRunner;
    use crate::ssh::CommandResult;
    use std::sync::Mutex;
    use std::time::Duration;

    type Calls = std::sync::Arc<Mutex<Vec<(String, Option<String>)>>>;

    #[derive(Default)]
    struct FakeRunner {
        calls: Calls,
        responses: Mutex<Vec<(String, CommandResult)>>,
    }

    impl FakeRunner {
        fn calls(&self) -> Calls {
            std::sync::Arc::clone(&self.calls)
        }

        fn with(self, needle: &str, stdout: &str) -> Self {
            self.responses.lock().unwrap().push((
                needle.to_string(),
                CommandResult {
                    success: true,
                    stdout: stdout.to_string(),
                    stderr: String::new(),
                },
            ));
            self
        }
    }

    impl RemoteRunner for FakeRunner {
        fn run(
            &self,
            remote_cmd: &str,
            _timeout: Duration,
            stdin: Option<&str>,
        ) -> Result<CommandResult, String> {
            self.calls
                .lock()
                .unwrap()
                .push((remote_cmd.to_string(), stdin.map(str::to_string)));
            for (needle, response) in self.responses.lock().unwrap().iter() {
                if remote_cmd.contains(needle) {
                    return Ok(response.clone());
                }
            }
            Ok(CommandResult {
                success: true,
                stdout: String::new(),
                stderr: String::new(),
            })
        }
    }

    const CLUSTER_B: &str = include_str!(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/tests/fixtures/hpc/cluster-b.json"
    ));

    fn profile(partition: &str) -> ServerProfile {
        let entry: crate::server_profiles::ServerEntry =
            serde_json::from_str(CLUSTER_B).expect("cluster-b fixture");
        let mut profile = entry.profile;
        profile.defaults.partition = Some(partition.to_string());
        profile.environment = EnvironmentSetup {
            kind: EnvironmentKind::Modules,
            modules: vec!["python/3.12".to_string()],
            ..EnvironmentSetup::default()
        };
        profile
    }

    fn plan(profile: &ServerProfile) -> RunPlan {
        RunPlan {
            schema: crate::run_plan::RUN_PLAN_SCHEMA.to_string(),
            id: "plan-1793222864-c5af819dbd2b".to_string(),
            created_unix: 1_793_222_864,
            profile_id: profile.name.clone(),
            profile_name: profile.name.clone(),
            pipeline: crate::run_plan::PlanPipelineRef {
                id: "contract-check".to_string(),
                name: "contract-check".to_string(),
                version: String::new(),
                source: "custom".to_string(),
            },
            argv: vec![
                "/usr/bin/python3".to_string(),
                "/data/code/pipelines/contract_pipeline.py".to_string(),
                "--checkpoint".to_string(),
                "/data/code/checkpoints/model.pt".to_string(),
                "--split".to_string(),
                "/data/code/splits/test.txt".to_string(),
                "--extra".to_string(),
                "a b; touch /tmp/x".to_string(),
                "--output-dir".to_string(),
                format!("{}/runs/x", profile.results_root.trim_end_matches('/')),
            ],
            environment: profile.environment.clone(),
            environment_from_override: false,
            environment_note: "test".to_string(),
            checkpoint: crate::run_plan::PlanArtifact {
                path: "/data/code/checkpoints/model.pt".to_string(),
                sha256: Some("a".repeat(64)),
                kind: "file".to_string(),
                real_path: Some("/data/code/checkpoints/model.pt".to_string()),
            },
            split: crate::run_plan::PlanArtifact {
                path: "/data/code/splits/test.txt".to_string(),
                sha256: Some("b".repeat(64)),
                kind: "file".to_string(),
                real_path: Some("/data/code/splits/test.txt".to_string()),
            },
            output_dir: format!(
                "{}/runs/plan-1793222864-c5af819dbd2b",
                profile.results_root.trim_end_matches('/')
            ),
            scratch_dir: profile.scratch_template.clone(),
            checks: Vec::new(),
            contract_check_script: String::new(),
            nothing_submitted: true,
            saved_path: None,
        }
    }

    fn saved_runner(profile: &ServerProfile, plan: &RunPlan) -> FakeRunner {
        let plan_name = format!("/plans/{}.json", plan.id);
        FakeRunner::default()
            .with(&plan_name, &serde_json::to_string(plan).expect("plan json"))
            .with("sbatch --parsable", "424242;cluster-b\n")
            .with("squeue -h -j 424242", "PENDING|Resources\n")
    }

    #[test]
    fn script_has_partition_cpu_and_four_hour_default_without_submitting() {
        let profile = profile("ps");
        let plan = plan(&profile);
        let preview =
            render_run_script(&profile, &plan, &SubmitOptions::default()).expect("render");
        assert!(preview.script.contains("#SBATCH --partition=ps"));
        assert!(preview.script.contains("#SBATCH --time=04:00:00"));
        assert!(preview.script.contains("#SBATCH --cpus-per-task="));
        assert!(preview.script.contains("module load python/3.12"));
        assert!(preview.script.contains("/data/code/pipelines/contract_pipeline.py"));
        // The full run keeps its own output dir and never runs the contract-check frame cap.
        assert!(preview.script.contains("--output-dir \"$OUT_DIR\""));
        assert!(!preview.script.contains("--max-frames"));
        assert!(!preview.script.contains("sbatch "));
    }

    #[test]
    fn the_exit_trap_only_removes_a_job_owned_scratch_directory() {
        let mut profile = profile("ps");
        profile.scratch_template = "/local/alice".to_string();
        let plan = plan(&profile);
        let preview =
            render_run_script(&profile, &plan, &SubmitOptions::default()).expect("render");
        assert!(
            preview
                .script
                .contains("SCRATCH_DIR=\"/local/alice/coperceive-${SLURM_JOB_ID}\""),
            "{}",
            preview.script
        );
        assert!(preview
            .script
            .contains("trap 'rm -rf -- \"$SCRATCH_DIR\"' EXIT"));
        assert!(!preview.script.contains("rm -rf -- \"/local/alice\""));
        assert!(!preview.script.contains("rm -rf -- /local/alice"));

        profile.scratch_template = "/local/alice/$SLURM_JOB_ID".to_string();
        let preview =
            render_run_script(&profile, &plan, &SubmitOptions::default()).expect("render");
        assert!(preview
            .script
            .contains("SCRATCH_DIR=\"/local/alice/$SLURM_JOB_ID\""));
        assert!(!preview.script.contains("coperceive-${SLURM_JOB_ID}"));
    }

    #[test]
    fn a_scratch_template_with_quotes_or_a_subshell_is_refused_before_rendering() {
        let mut profile = profile("ps");
        let plan = plan(&profile);
        for bad in ["/scratch/\"quoted\"", "/scratch/$(id)"] {
            profile.scratch_template = bad.to_string();
            let error = render_run_script(&profile, &plan, &SubmitOptions::default())
                .expect_err("unsafe scratch template");
            assert!(error.contains("scratch folder"), "{bad}: {error}");
        }
    }

    #[test]
    fn the_job_script_quotes_the_saved_coperceive_argv() {
        let profile = profile("ps");
        let mut plan = plan(&profile);
        plan.argv = vec![
            "/home/u/venv/bin/python".to_string(),
            "-m".to_string(),
            "coperceive".to_string(),
            "eval".to_string(),
            "--out".to_string(),
            "$OUT_DIR".to_string(),
        ];
        let preview =
            render_run_script(&profile, &plan, &SubmitOptions::default()).expect("render");
        assert!(
            preview
                .script
                .contains("/home/u/venv/bin/python -m coperceive eval"),
            "{}",
            preview.script
        );
    }

    #[test]
    fn an_equals_form_output_dir_is_not_duplicated() {
        let profile = profile("ps");
        let mut plan = plan(&profile);
        plan.argv = vec![
            "/usr/bin/python3".to_string(),
            "/data/code/pipelines/contract_pipeline.py".to_string(),
            "--output-dir=/tmp/elsewhere".to_string(),
        ];
        let preview =
            render_run_script(&profile, &plan, &SubmitOptions::default()).expect("render");
        assert!(preview.script.contains("--output-dir \"$OUT_DIR\""));
        assert!(!preview.script.contains("/tmp/elsewhere"));
        assert_eq!(preview.script.matches("--output-dir").count(), 1);
    }

    #[test]
    fn advanced_options_edit_walltime_and_cpus() {
        let profile = profile("ps");
        let plan = plan(&profile);
        let options = SubmitOptions {
            walltime: "12:00:00".to_string(),
            cpus_per_task: Some(16),
            ..SubmitOptions::default()
        };
        let preview = render_run_script(&profile, &plan, &options).expect("render");
        assert!(preview.script.contains("#SBATCH --time=12:00:00"));
        assert!(preview.script.contains("#SBATCH --cpus-per-task=16"));
        assert_eq!(preview.walltime, "12:00:00");
        assert_eq!(preview.cpus_per_task, Some(16));
    }

    #[test]
    fn a_bad_walltime_is_refused() {
        let profile = profile("ps");
        let plan = plan(&profile);
        let options = SubmitOptions {
            walltime: "two hours".to_string(),
            ..SubmitOptions::default()
        };
        let error = render_run_script(&profile, &plan, &options).expect_err("bad time");
        assert!(error.contains("Walltime"), "{error}");
    }

    #[test]
    fn submit_requires_an_explicit_confirmation_and_matching_preview() {
        let profile = profile("ps");
        let plan = plan(&profile);
        let runner = saved_runner(&profile, &plan);
        let preview = render_run_script(&profile, &plan, &SubmitOptions::default()).expect("render");
        let error = submit_saved_plan(
            &runner,
            &profile,
            &plan.id,
            &SubmitOptions::default(),
            &preview.preview_hash,
            false,
        )
        .expect_err("unconfirmed");
        assert!(error.contains("confirmation"), "{error}");
        assert!(!runner
            .calls()
            .lock()
            .unwrap()
            .iter()
            .any(|(cmd, _)| cmd.contains("sbatch")));

        let error = submit_saved_plan(
            &runner,
            &profile,
            &plan.id,
            &SubmitOptions::default(),
            "deadbeef",
            true,
        )
        .expect_err("hash mismatch");
        assert!(error.contains("changed since you reviewed"), "{error}");
        assert!(!runner
            .calls()
            .lock()
            .unwrap()
            .iter()
            .any(|(cmd, _)| cmd.contains("sbatch")));
    }

    #[test]
    fn confirmed_submit_returns_job_id_and_pending_reason() {
        let profile = profile("ps");
        let plan = plan(&profile);
        let runner = saved_runner(&profile, &plan);
        let preview = render_run_script(&profile, &plan, &SubmitOptions::default()).expect("render");
        let outcome = submit_saved_plan(
            &runner,
            &profile,
            &plan.id,
            &SubmitOptions::default(),
            &preview.preview_hash,
            true,
        )
        .expect("submit");
        assert_eq!(outcome.job_id, "424242");
        assert_eq!(outcome.state, JobState::Pending);
        assert_eq!(outcome.queue_reason.as_deref(), Some("Resources"));
        let calls = runner.calls();
        let calls = calls.lock().unwrap();
        let sbatch = calls
            .iter()
            .find(|(cmd, _)| cmd.contains("sbatch --parsable"))
            .expect("sbatch call");
        let submitted = sbatch.1.as_deref().expect("script on stdin");
        assert!(submitted.contains("#SBATCH --partition=ps"));
        assert!(submitted.contains("#SBATCH --time=04:00:00"));
        assert!(!submitted.contains("--max-frames"));
    }

    #[test]
    fn a_non_numeric_job_id_is_refused() {
        assert!(parse_job_id("not-a-job").is_err());
        assert_eq!(parse_job_id("12345;cluster-b").expect("id"), "12345");
    }

    #[test]
    fn unsaved_plan_or_bad_id_submits_nothing() {
        let profile = profile("ps");
        let runner = FakeRunner::default();
        let error = submit_saved_plan(
            &runner,
            &profile,
            "../../etc/passwd",
            &SubmitOptions::default(),
            "x",
            true,
        )
        .expect_err("bad id");
        assert!(error.contains("plan id"), "{error}");
        assert!(!runner
            .calls()
            .lock()
            .unwrap()
            .iter()
            .any(|(cmd, _)| cmd.contains("sbatch")));
    }

    #[test]
    fn job_state_maps_pending_reason_and_unknown_without_squeue() {
        let runner = FakeRunner::default().with("squeue -h -j 7", "RUNNING|None\n");
        let (state, reason) = job_state(&runner, "7");
        assert_eq!(state, JobState::Running);
        assert!(reason.is_none());
        let empty = FakeRunner::default();
        let (state, _) = job_state(&empty, "9");
        assert_eq!(state, JobState::Unknown);
    }

    #[test]
    fn scheduler_kind_is_not_hard_coded_into_the_plan() {
        // The plan itself carries no cluster value; the script takes the partition
        // from the profile, so a differently-configured server renders differently.
        let profile_a = profile("ps");
        let mut profile_b = profile("ps");
        profile_b.defaults.partition = Some("main-gpu".to_string());
        let plan_a = plan(&profile_a);
        let mut plan_b = plan(&profile_b);
        plan_b.profile_id = profile_b.name.clone();
        let a = render_run_script(&profile_a, &plan_a, &SubmitOptions::default()).expect("a");
        let b = render_run_script(&profile_b, &plan_b, &SubmitOptions::default()).expect("b");
        assert!(a.script.contains("--partition=ps"));
        assert!(b.script.contains("--partition=main-gpu"));
        assert_eq!(profile_a.scheduler, SchedulerKind::Slurm);
    }
}
