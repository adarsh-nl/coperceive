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
use crate::run_plan::{
    environment_lines, model_run_output_checks, quoted_command_with, strip_output_dir,
    validate_plan_id, verify_plan_inputs, RunPlan, WORK_IN_SCRATCH,
};
use crate::slurm_adapter::{RemoteRunner, map_slurm_state};
use crate::ssh::shell_quote;

const READ_TIMEOUT: Duration = Duration::from_secs(8);
const SUBMIT_TIMEOUT: Duration = Duration::from_secs(30);
const STATUS_TIMEOUT: Duration = Duration::from_secs(15);

/// Wall time of a custom pipeline's run when the form leaves it empty. A built-in model's
/// run never uses it: its default is the CLI's own estimate (`RunPlan::time_estimate`).
pub const DEFAULT_WALLTIME: &str = "04:00:00";
/// Number of frames a contract check runs (the plan's stored script), for reference.
pub const SUBMIT_TIMEOUT_SECS: u64 = SUBMIT_TIMEOUT.as_secs();

/// What the user can change in Advanced before submitting.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SubmitOptions {
    /// Slurm time limit. Empty: a built-in run takes the plan's `time_estimate` (as
    /// `coperceive model run --slurm` estimates it) and is refused without one; a custom
    /// pipeline takes DEFAULT_WALLTIME.
    #[serde(default)]
    pub walltime: String,
    #[serde(default)]
    pub cpus_per_task: Option<u32>,
    #[serde(default)]
    pub account: Option<String>,
    #[serde(default)]
    pub qos: Option<String>,
    /// `None` keeps the plan's GPU default; `Some("")` explicitly requests no
    /// GPU; `Some(gres)` requests that GRES string.
    #[serde(default)]
    pub gres: Option<String>,
}

impl Default for SubmitOptions {
    fn default() -> Self {
        Self {
            walltime: String::new(),
            cpus_per_task: None,
            account: None,
            qos: None,
            gres: None,
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
    /// The GRES line in the preview, or None for a CPU-only job.
    pub gres: Option<String>,
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
pub(crate) fn job_scratch_dir(template: &str) -> String {
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
    // A genuinely old plan is one saved before model/raw_root/media_root/work_dir were
    // introduced (9b4d9a0): serde defaults leave all of them empty. work_dir is the marker
    // for that schema version: every built-in plan saved since then records the job's work
    // directory, so an empty one means the plan predates those fields. The media root is
    // not the marker: it is optional now, and an empty one is a valid new plan.
    if plan.pipeline.source == "builtin"
        && (plan.model.trim().is_empty()
            || plan.raw_root.path.trim().is_empty()
            || plan.work_dir.trim().is_empty())
    {
        return Err(
            "This saved plan predates the model/raw/work fields. Rebuild this plan before submitting."
                .to_string(),
        );
    }
    // A model option with no manifest default (Griffin's ckpt_root) cannot be guessed: a
    // saved plan that records the model's option keys but no value is refused here, before
    // anything is submitted. A plan saved before model_options existed has none recorded;
    // its argv then lacks the option and `coperceive model run` refuses it by name.
    if plan.pipeline.source == "builtin"
        && plan.model_options.iter().any(|option| option == "ckpt_root")
        && plan.ckpt_root.path.trim().is_empty()
    {
        return Err(
            "This saved plan has no ckpt_root value, but its model declares the required ckpt_root parameter. Rebuild this plan before submitting."
                .to_string(),
        );
    }
    let builtin = plan.pipeline.source == "builtin";
    if builtin
        && plan
            .argv
            .iter()
            .any(|word| word == "--output-dir" || word.starts_with("--output-dir="))
    {
        // Saved before `coperceive model run` got its real flags: such a plan passes
        // --output-dir (and, for Griffin, a checkpoint its manifest refuses).
        return Err(
            "This saved plan was made before built-in runs used coperceive model run's own \
             output flags, and it would fail on the cluster. Rebuild this plan before submitting."
                .to_string(),
        );
    }
    validate_plan_id(&plan.id)?;
    if builtin {
        check_builtin_run_words(plan)?;
    }
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
    // The profile never supplies a GPU default. A model evaluation uses the
    // plan's default GPU (one of the partition's detected type); the user can
    // explicitly choose no GPU with an empty string.
    defaults.gres = match options.gres.as_deref() {
        None => plan.gres.clone().filter(|value| !value.trim().is_empty()),
        Some(value) if value.trim().is_empty() => None,
        Some(value) => Some(value.trim().to_string()),
    };
    let resources = plan.model_resources.as_ref().filter(|_| builtin);
    if defaults.gres.is_none() && resources.is_some_and(|resources| resources.gpu) {
        return Err(format!(
            "The model {} needs a GPU (gpu: true in its manifest), so it cannot be submitted \
             without one. Keep the plan's GPU request ({}) or choose another GPU. Nothing was \
             submitted.",
            plan.model,
            plan.gres.as_deref().unwrap_or("gpu:1")
        ));
    }
    defaults.time_limit = Some(run_walltime(plan, builtin, &options.walltime, partition_name(&defaults))?);
    if let Some(cpus) = options.cpus_per_task {
        defaults.cpus_per_task = Some(cpus);
    }
    // The manifest's CPUs and memory when neither the profile nor the user set them, as
    // plan_sbatch writes `--cpus-per-task` and `--mem` (coperceive/models/slurm.py).
    if let Some(resources) = resources {
        if defaults.cpus_per_task.is_none() {
            defaults.cpus_per_task = resources.cpus;
        }
        if defaults.memory.is_none() {
            defaults.memory = resources.memory_gb.map(|gb| format!("{gb}G"));
        }
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
    // A built-in run logs where coperceive model run --slurm does: <logs>/%x-%j, with %x
    // the job name; a custom pipeline into results_root under its job name.
    let (log_out, log_err) = if builtin {
        let logs = plan
            .logs_dir
            .as_deref()
            .ok_or_else(|| "The saved plan does not record its log folder.".to_string())
            .and_then(|logs| crate::run_plan::checked_logs_dir(logs).map(str::to_string))
            .map_err(|error| format!("{error} Rebuild this plan before submitting."))?;
        let logs = logs.trim_end_matches('/').to_string();
        (format!("{logs}/%x-%j.out"), format!("{logs}/%x-%j.err"))
    } else {
        let root = profile.results_root.trim_end_matches('/');
        (format!("{root}/{job_name}-%j.out"), format!("{root}/{job_name}-%j.err"))
    };
    let mut lines: Vec<String> = vec![
        "#!/bin/bash".to_string(),
        format!("# Full evaluation run for plan {}.", plan.id),
        format!("# Pipeline: {}.", plan.pipeline.name),
        "# Rendered by Coperceive from the saved plan and the server profile.".to_string(),
        "# The user reviewed this exact script before confirming the submission.".to_string(),
        format!("#SBATCH --job-name={job_name}"),
        format!("#SBATCH --partition={partition}"),
    ];
    // The order plan_sbatch writes: GRES, CPUs, memory, time.
    match defaults.gres.as_deref() {
        Some(gres) => lines.push(format!("#SBATCH --gres={gres}")),
        None => lines.push("# No GPU requested (CPU-only job).".to_string()),
    }
    if let Some(cpus) = defaults.cpus_per_task {
        lines.push(format!("#SBATCH --cpus-per-task={cpus}"));
    }
    if let Some(memory) = defaults.memory.as_deref() {
        lines.push(format!("#SBATCH --mem={memory}"));
    }
    lines.push(format!(
        "#SBATCH --time={}",
        defaults.time_limit.clone().unwrap_or_default()
    ));
    // The manifest's GPU pin, written whatever type discovery found (plan_sbatch).
    if let Some(constraint) = resources.and_then(|resources| resources.constraint.as_deref()) {
        let constraint = checked_constraint(constraint)?;
        check_gres_type(defaults.gres.as_deref(), constraint)?;
        lines.push(format!("#SBATCH --constraint=\"{constraint}\""));
    }
    if let Some(account) = defaults.account.as_deref() {
        lines.push(format!("#SBATCH --account={account}"));
    }
    if let Some(qos) = defaults.qos.as_deref() {
        lines.push(format!("#SBATCH --qos={qos}"));
    }
    lines.push(format!("#SBATCH --output={log_out}"));
    lines.push(format!("#SBATCH --error={log_err}"));
    if builtin {
        // plan_sbatch always asks Slurm to mail the end or failure of the run.
        lines.push("#SBATCH --mail-type=END,FAIL".to_string());
    }
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
    if builtin {
        // model run's --work: the job's scratch, as the CLI's `--work "$SCRATCH/work"`.
        lines.push(format!("mkdir -p -m 0700 -- \"{WORK_IN_SCRATCH}\""));
    }
    lines.push(format!("OUT_DIR=\"{}\"", safe_output));
    lines.push("mkdir -p -m 0700 -- \"$OUT_DIR\"".to_string());
    lines.push(String::new());
    lines.extend(environment_lines(&plan.environment, builtin));
    if let Some(comment) = dropped_python_comment(profile, plan, builtin) {
        lines.push(comment);
    }
    if builtin {
        lines.extend(pythonpath_line(plan)?);
    }
    // environment.json: a built-in run records it after `model run`, in the run directory
    // beside run.json (`<out>/<run_id>/environment.json`), as plan_sbatch does; a custom
    // pipeline before its run, in its output directory.
    let (capture_comment, capture_python) =
        if builtin && plan.environment.kind != EnvironmentKind::Container {
            let (python, comment) = capture_interpreter(plan)?;
            (comment, python)
        } else {
            (None, "python3".to_string())
        };
    if !builtin {
        lines.extend(crate::slurm_adapter::environment_capture_lines(
            &capture_python,
            "\"$OUT_DIR/environment.json\"",
        ));
    }
    lines.push(format!(
        "echo \"Starting run for plan {} on $(hostname)\"",
        plan.id
    ));

    let mut command = plan.argv.clone();
    // The app appends its own --output-dir; remove the plan's copy in either
    // form so the script cannot contain two different output directories.
    strip_output_dir(&mut command);
    // A built-in model runs `coperceive model run`, which writes under its own --out
    // and --run-id (the plan's output directory and id) and has no --output-dir.
    let run_command = if builtin {
        quoted_command_with(&command, &[("--work", "\"$SCRATCH_DIR/work\"")])
    } else {
        format!("{} --output-dir \"$OUT_DIR\"", quoted_command(&command))
    };
    let run_line = match plan.environment.kind {
        EnvironmentKind::Container => {
            let image = plan
                .environment
                .container_image
                .as_deref()
                .filter(|image| !image.is_empty())
                .ok_or_else(|| {
                    "This plan uses a container environment but has no image path.".to_string()
                })?;
            format!(
                "apptainer exec --cleanenv --bind \"$SCRATCH_DIR\" --bind \"$OUT_DIR\" {} {run_command}",
                shell_quote(image)
            )
        }
        _ => run_command,
    };
    if builtin {
        // As plan_sbatch: the run's status is kept so the work directory's logs and
        // artefacts come home whether or not it finished; then the job exits with it.
        lines.push(
            "# The run's exit status is kept: its logs and artefacts come home below either way."
                .to_string(),
        );
        lines.push("set +e".to_string());
        lines.push(run_line);
        lines.push("RC=$?".to_string());
        lines.push("set -e".to_string());
        lines.push("echo \"model run exit $RC\"".to_string());
        // `model run` made the run directory unless it failed early; made here either way
        // so the record lands, as plan_sbatch's `mkdir -p "$SCRATCH/out/$RUN_ID"`.
        lines.push(format!("mkdir -p -m 0700 -- \"$OUT_DIR/{}\"", plan.id));
        lines.extend(capture_comment);
        lines.extend(crate::slurm_adapter::environment_capture_lines(
            &capture_python,
            &format!("\"$OUT_DIR/{}/environment.json\"", plan.id),
        ));
        lines.push(
            "# The work directory's logs and artefacts, not its inputs or any toolkit copy."
                .to_string(),
        );
        lines.push(format!(
            "rsync -a {} \"$SCRATCH_DIR/work/\" \"$OUT_DIR/work/\"",
            work_copy_filter(plan)?.join(" ")
        ));
        lines.push("if [ \"$RC\" -ne 0 ]; then exit \"$RC\"; fi".to_string());
        lines.push(String::new());
        // What `model run` writes: <out>/<run_id>/run.json and one
        // <out>/<run_id>/<scene>/boxes.parquet per scene with boxes.
        lines.extend(model_run_output_checks("$OUT_DIR", &plan.id));
    } else {
        lines.push(run_line);
        lines.push(String::new());
        lines.push(
            "test -s \"$OUT_DIR/predictions.jsonl\" || { echo \"The run wrote no \
             predictions.jsonl; it does not meet the output contract.\" >&2; exit 1; }"
                .to_string(),
        );
        lines.push(
            "test -s \"$OUT_DIR/metrics.json\" || { echo \"The run wrote no metrics.json; it \
             does not meet the output contract.\" >&2; exit 1; }"
                .to_string(),
        );
    }
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
        gres: defaults.gres.clone(),
        script,
        preview_hash,
    })
}

fn partition_name(defaults: &crate::hpc::JobDefaults) -> &str {
    defaults.partition.as_deref().map(str::trim).unwrap_or("")
}

/// The job's `--time`: the form's walltime; when it is empty, a built-in run's
/// `time_estimate` (`plan_sbatch`'s estimate, never a fixed default) and a custom
/// pipeline's DEFAULT_WALLTIME. A walltime longer than the partition's discovered limit
/// is refused here, before the preview; with no discovered limit nothing is refused.
fn run_walltime(plan: &RunPlan, builtin: bool, given: &str, partition: &str) -> Result<String, String> {
    let walltime = match given.trim() {
        "" if builtin => plan.time_estimate.clone().ok_or_else(|| {
            "Enter the walltime for this run: the plan has no time estimate (the cluster could \
             not count the scenes or does not report the model's timing). Nothing was submitted."
                .to_string()
        })?,
        "" => DEFAULT_WALLTIME.to_string(),
        text => text.to_string(),
    };
    if let Some(limit) = plan.partition_max_time.as_deref() {
        if let (Some(wanted), Some(max)) = (
            crate::run_plan::slurm_seconds(&walltime),
            crate::run_plan::slurm_seconds(limit),
        ) {
            if wanted > max {
                return Err(format!(
                    "The walltime {walltime} is longer than partition {partition}'s time limit \
                     {limit}, so Slurm would refuse the job. Enter {limit} or less. Nothing was \
                     submitted."
                ));
            }
        }
    }
    Ok(walltime)
}

/// A typed GPU request (`gpu:<type>:<n>`) must be a type the manifest's constraint names:
/// `gpu:a100:1` beside `--constraint="a40"` fits no node. An untyped `gpu:<n>` leaves the
/// type to the constraint, as `plan_sbatch` writes it.
fn check_gres_type(gres: Option<&str>, constraint: &str) -> Result<(), String> {
    let Some(gres) = gres else {
        return Ok(());
    };
    for request in gres.split(',') {
        let parts: Vec<&str> = request.trim().split(':').collect();
        let gpu_type = match parts.as_slice() {
            ["gpu", kind, _] => Some(*kind),
            ["gpu", kind] if !kind.chars().all(|ch| ch.is_ascii_digit()) => Some(*kind),
            _ => None,
        };
        let Some(gpu_type) = gpu_type else {
            continue;
        };
        let named = constraint
            .split(|ch: char| !(ch.is_ascii_alphanumeric() || matches!(ch, '_' | '-' | '.')))
            .any(|feature| feature.eq_ignore_ascii_case(gpu_type));
        if !named {
            return Err(format!(
                "The GPU request {gres} asks for {gpu_type}, but the model's manifest pins the \
                 GPU with --constraint=\"{constraint}\", so no node would fit. Choose gpu:1 (the \
                 constraint picks the type) or a type it names. Nothing was submitted."
            ));
        }
    }
    Ok(())
}

/// `export PYTHONPATH=<root>`, quoted as `plan_sbatch` quotes its own (`shlex.quote`):
/// the root of the coperceive the run's python imports, read on the cluster. Nothing
/// when the interpreter could not be known (said in a comment).
fn pythonpath_line(plan: &RunPlan) -> Result<Vec<String>, String> {
    if plan.environment.kind == EnvironmentKind::Container {
        return Ok(Vec::new());
    }
    let root = plan.run_interpreter.as_ref().and_then(|found| found.package_root.as_deref());
    let Some(root) = root else {
        return Ok(vec![
            "# No PYTHONPATH: the interpreter of this run could not be read when the plan was \
             checked."
                .to_string(),
        ]);
    };
    if !root.starts_with('/') || root.chars().any(char::is_control) {
        return Err(format!(
            "The saved plan's coperceive root {root:?} is not an absolute path without control \
             characters. Rebuild this plan before submitting."
        ));
    }
    Ok(vec![format!(
        "export PYTHONPATH={}  # the coperceive the run's python imports, as plan_sbatch exports its own",
        shell_quote(root)
    )])
}

/// For a built-in plan on a modules profile: a comment naming the profile's python
/// modules the job does not load, because the interpreter's own python module (loaded
/// first) would be swapped by them (`with_interpreter_module`, run_plan.rs).
fn dropped_python_comment(profile: &ServerProfile, plan: &RunPlan, builtin: bool) -> Option<String> {
    if !builtin || plan.environment.kind != EnvironmentKind::Modules {
        return None;
    }
    let module = plan.run_interpreter.as_ref()?.module.as_deref()?;
    let dropped = crate::run_plan::dropped_python_modules(
        &profile.environment.modules,
        &plan.environment.modules,
        module,
    );
    if dropped.is_empty() {
        return None;
    }
    // Escaped: a module name is free text in the profile, and this is a comment line.
    let named: Vec<String> = dropped.iter().map(|name| name.escape_default().to_string()).collect();
    Some(format!(
        "# Not loaded: the profile's {}. Loaded after the interpreter's {} it would swap it; \
         plan_sbatch loads only the interpreter's python module.",
        named.join(", "),
        module.escape_default()
    ))
}

/// The rsync filter for the work directory, as `work_copy_filter` in
/// coperceive/models/slurm.py: the closure's transient artefacts excluded first, then
/// every `*.log` and the artefacts (rsync takes the first rule that matches).
fn work_copy_filter(plan: &RunPlan) -> Result<Vec<String>, String> {
    let mut filter = Vec::new();
    for item in plan
        .model_resources
        .as_ref()
        .map(|resources| resources.transient_artifacts.as_slice())
        .unwrap_or_default()
    {
        let safe = item.split('/').count() == 2
            && item.split('/').all(|part| {
                !part.is_empty()
                    && part != ".."
                    && part
                        .chars()
                        .all(|ch| ch.is_ascii_alphanumeric() || matches!(ch, '-' | '_' | '.'))
            });
        if !safe {
            return Err(format!(
                "The saved plan names a transient artefact {item:?} that is not <model>/<name>. \
                 Rebuild this plan before submitting."
            ));
        }
        filter.push(format!("--exclude='artifacts/{item}/***'"));
    }
    filter.extend(
        [
            "--include='*/'",
            "--include='*.log'",
            "--include='artifacts/***'",
            "--exclude='*'",
            "--prune-empty-dirs",
        ]
        .map(str::to_string),
    );
    Ok(filter)
}

/// The manifest's `slurm_constraint`, checked before it goes into a `#SBATCH` line: a
/// Slurm feature expression (`a40`, `a40|a100`, `[a40*2&ib]`) and nothing that could end
/// the quoted value or the line.
fn checked_constraint(constraint: &str) -> Result<&str, String> {
    let ok = !constraint.is_empty()
        && constraint.chars().all(|ch| {
            ch.is_ascii_alphanumeric() || matches!(ch, '_' | '-' | '.' | '&' | '|' | ',' | ':' | '[' | ']' | '*' | '!' | '(' | ')')
        });
    if ok {
        Ok(constraint)
    } else {
        Err(format!(
            "The saved plan's GPU constraint {constraint:?} is not a Slurm feature expression \
             coperceive can write. Check slurm_constraint in the model's manifest, then rebuild \
             this plan."
        ))
    }
}

/// The interpreter `environment.json` records for a built-in run: the one that runs
/// `coperceive model run`, never whatever `python3` the node finds first. It was read on
/// the cluster when the plan was checked and saved (`RunInterpreter`, run_plan.rs):
///
/// - `<python> -m coperceive`: that python.
/// - `<dir>/coperceive` (a console script): the `python` beside it, else the interpreter
///   its shebang names (pipx and `/usr/local/bin` have no `python` beside the script).
/// - a bare `coperceive` on a conda or venv profile: the profile's interpreter, whose bin
///   directory the job puts first on PATH.
/// - otherwise (`#!/usr/bin/env python3`, a bare `coperceive` on a modules profile) the
///   interpreter cannot be known before the job runs: `python3`, and the comment says so.
///
/// Returns the quoted interpreter and a comment line naming the choice. A value with a
/// line break or another control character is refused: it would end the comment line.
fn capture_interpreter(plan: &RunPlan) -> Result<(String, Option<String>), String> {
    let found = plan.run_interpreter.as_ref().ok_or_else(|| {
        "This saved plan does not record the interpreter that runs coperceive or its python \
         module, so the job could not load it. Rebuild this plan before submitting."
            .to_string()
    })?;
    for value in [found.python.as_deref().unwrap_or(""), found.program.as_str()] {
        if value.chars().any(char::is_control) {
            return Err(format!(
                "The saved plan's interpreter {value:?} contains a line break or another \
                 control character, which cannot go into the job script. Fix the server \
                 profile, then rebuild this plan."
            ));
        }
    }
    // The source is read back from the saved plan with the rest, and is named in the
    // comment below: only the values save_run_plan writes (RunInterpreter) are accepted.
    if !matches!(found.source.as_str(), "command" | "beside" | "shebang" | "profile" | "unknown") {
        return Err(format!(
            "The saved plan records the interpreter as found by {:?}, which this app does not \
             write. Rebuild this plan before submitting.",
            found.source
        ));
    }
    let program = &found.program;
    let Some(python) = found.python.as_deref() else {
        return Ok((
            "python3".to_string(),
            Some(format!(
                "# environment.json: the interpreter of {program} cannot be known before the \
                 job runs (no python beside it and no shebang naming one), so python3 is \
                 recorded instead."
            )),
        ));
    };
    let comment = match found.source.as_str() {
        "command" => None,
        "beside" => Some(format!(
            "# environment.json: {python}, the interpreter beside {program} in its bin \
             directory."
        )),
        "shebang" => Some(format!(
            "# environment.json: {python}, the interpreter the shebang of {program} names \
             (read when the plan was checked)."
        )),
        "profile" => Some(format!(
            "# environment.json: {python}, the profile's interpreter; its bin directory is \
             first on PATH, where coperceive resolves."
        )),
        other => Some(format!("# environment.json: {python} ({other}).")),
    };
    Ok((shell_quote(python), comment))
}

/// A built-in plan's `model run` must write where the plan says: `--out` is the plan's
/// output directory, `--run-id` its id and `--work` the job's scratch. The post-run
/// checks and the results views rely on it, so a saved plan that says otherwise (stale
/// or edited) is refused, as `--output-dir` is.
fn check_builtin_run_words(plan: &RunPlan) -> Result<(), String> {
    let Some(start) = plan
        .argv
        .windows(2)
        .position(|pair| pair[0] == "model" && pair[1] == "run")
    else {
        return Err(
            "This saved built-in plan does not run coperceive model run. Rebuild this plan \
             before submitting."
                .to_string(),
        );
    };
    let words = &plan.argv[start + 2..];
    let output_dir = plan.output_dir.trim_end_matches('/');
    for (flag, expected, what) in [
        ("--out", output_dir, "output directory"),
        ("--run-id", plan.id.as_str(), "id"),
        ("--work", WORK_IN_SCRATCH, "job scratch work directory"),
    ] {
        if words.iter().any(|word| word.starts_with(&format!("{flag}="))) {
            return Err(format!(
                "This saved plan passes {flag} in the {flag}=VALUE form, which coperceive does \
                 not write. Rebuild this plan before submitting."
            ));
        }
        let values: Vec<&String> = words
            .iter()
            .enumerate()
            .filter(|(_, word)| *word == flag)
            .filter_map(|(at, _)| words.get(at + 1))
            .collect();
        match values.as_slice() {
            [value] if value.as_str() == expected => {}
            [value] => {
                return Err(format!(
                    "This saved plan runs model run with {flag} {value:?}, but the plan's {what} \
                     is {expected:?}. Rebuild this plan before submitting."
                ))
            }
            [] => {
                return Err(format!(
                    "This saved plan runs model run without {flag}. Rebuild this plan before \
                     submitting."
                ))
            }
            _ => {
                return Err(format!(
                    "This saved plan passes {flag} more than once. Rebuild this plan before \
                     submitting."
                ))
            }
        }
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
pub(crate) fn parse_job_id(stdout: &str) -> Result<String, String> {
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
    // The checkpoint and the test split must still be what the save recorded.
    verify_plan_inputs(runner, &plan)?;
    let preview = render_run_script(profile, &plan, options)?;
    if preview.preview_hash != preview_hash {
        return Err(
            "The rendered script changed since you reviewed it. Preview it again before \
             submitting. Nothing was submitted."
                .to_string(),
        );
    }
    // Slurm opens --output/--error before the script runs, so a built-in run's log folder
    // must exist first; the CLI's ~/logs is made here, never in the script.
    if plan.pipeline.source == "builtin" {
        let logs = plan.logs_dir.as_deref().unwrap_or_default();
        let made = runner
            .run(&logs_dir_script(logs), SUBMIT_TIMEOUT, None)
            .map_err(|error| format!("Could not make the log folder {logs}: {error}. Nothing was submitted."))?;
        if !made.success {
            return Err(format!(
                "Could not make the log folder {logs}: {}. Nothing was submitted.",
                made.stderr.trim()
            ));
        }
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

/// `mkdir -p` of a built-in run's log folder (already refused by `checked_logs_dir` in the
/// render when it has a space or a control character), as a positional parameter.
fn logs_dir_script(logs: &str) -> String {
    format!(
        "sh -c '# coperceive-submit:logs-dir\nmkdir -p -- \"$1\"' sh {}",
        shell_quote(logs)
    )
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
            model: String::new(),
            raw_root: crate::run_plan::PlanArtifact::default(),
            media_root: crate::run_plan::PlanArtifact::default(),
            ckpt_root: crate::run_plan::PlanArtifact::default(),
            model_options: Vec::new(),
            work_dir: String::new(),
            staging_seconds: None,
            checkpoint: crate::run_plan::PlanArtifact {
                path: "/data/code/checkpoints/model.pt".to_string(),
                sha256: Some("a".repeat(64)),
                kind: "file".to_string(),
                real_path: Some("/data/code/checkpoints/model.pt".to_string()),
                ..crate::run_plan::PlanArtifact::default()
            },
            split: crate::run_plan::PlanArtifact {
                path: "/data/code/splits/test.txt".to_string(),
                sha256: Some("b".repeat(64)),
                kind: "file".to_string(),
                real_path: Some("/data/code/splits/test.txt".to_string()),
                ..crate::run_plan::PlanArtifact::default()
            },
            output_dir: format!(
                "{}/runs/plan-1793222864-c5af819dbd2b",
                profile.results_root.trim_end_matches('/')
            ),
            scratch_dir: profile.scratch_template.clone(),
            gres: None,
            model_resources: None,
            run_interpreter: None,
            time_estimate: None,
            partition_max_time: None,
            logs_dir: None,
            checks: Vec::new(),
            contract_check_script: String::new(),
            nothing_submitted: true,
            saved_path: None,
        }
    }

    /// The cluster's path-check answer for an unchanged file: what the plan recorded.
    fn file_answer(artifact: &crate::run_plan::PlanArtifact) -> String {
        format!(
            "EXISTS=1\nREADABLE=1\nEXEC=0\nISDIR=0\nREALPATH={}\nSHA256={}\n",
            artifact.real_path.clone().unwrap_or_else(|| artifact.path.clone()),
            artifact.sha256.clone().unwrap_or_default()
        )
    }

    fn saved_runner(profile: &ServerProfile, plan: &RunPlan) -> FakeRunner {
        let _ = profile;
        let plan_name = format!("/plans/{}.json", plan.id);
        FakeRunner::default()
            .with(&plan_name, &serde_json::to_string(plan).expect("plan json"))
            .with(&plan.checkpoint.path, &file_answer(&plan.checkpoint))
            .with(&plan.split.path, &file_answer(&plan.split))
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
    fn an_eval_plan_uses_one_gpu_from_its_partition_and_the_user_can_clear_it() {
        let profile = profile("gpu-h100");
        let mut plan = plan(&profile);
        plan.gres = Some("gpu:h100:1".to_string());
        let preview =
            render_run_script(&profile, &plan, &SubmitOptions::default()).expect("render");
        assert_eq!(preview.gres.as_deref(), Some("gpu:h100:1"));
        assert!(preview.script.contains("#SBATCH --gres=gpu:h100:1"));
        assert!(!preview.script.contains("No GPU requested"));

        let no_gpu = SubmitOptions {
            gres: Some(String::new()),
            ..SubmitOptions::default()
        };
        let preview = render_run_script(&profile, &plan, &no_gpu).expect("render");
        assert!(preview.gres.is_none());
        assert!(!preview.script.contains("--gres="));
        assert!(preview.script.contains("No GPU requested (CPU-only job)."));
    }

    #[test]
    fn a_cpu_only_plan_renders_no_gres() {
        let profile = profile("main-cpu");
        let plan = plan(&profile);
        assert!(plan.gres.is_none());
        let preview =
            render_run_script(&profile, &plan, &SubmitOptions::default()).expect("render");
        assert!(preview.gres.is_none());
        assert!(!preview.script.contains("--gres="));
        assert!(preview.script.contains("No GPU requested (CPU-only job)."));
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
    fn an_old_builtin_plan_asks_for_a_rebuild_instead_of_submitting() {
        // A plan saved before 9b4d9a0 has none of model/raw_root/media_root/work_dir; the
        // work_dir marker alone must still refuse it, with or without a media root.
        let profile = profile("ps");
        let mut plan = plan(&profile);
        plan.pipeline.source = "builtin".to_string();
        plan.model = String::new();
        plan.raw_root = crate::run_plan::PlanArtifact::default();
        plan.media_root = crate::run_plan::PlanArtifact::default();
        plan.work_dir = String::new();
        let error = render_run_script(&profile, &plan, &SubmitOptions::default())
            .expect_err("old plan");
        assert!(error.contains("Rebuild this plan"), "{error}");

        // model and raw root are present, but the plan predates work_dir: still old.
        let mut no_work = builtin_plan(&profile);
        no_work.work_dir = String::new();
        let error = render_run_script(&profile, &no_work, &SubmitOptions::default())
            .expect_err("plan without work_dir");
        assert!(error.contains("Rebuild this plan"), "{error}");
    }

    #[test]
    fn a_new_builtin_plan_without_a_media_root_is_accepted_and_omits_the_flag() {
        // The CLI's --media-root is optional; the app now accepts an empty one and the job
        // script must contain no --media-root word at all (not an empty one).
        let profile = profile("ps");
        let mut plan = builtin_plan(&profile);
        plan.media_root = crate::run_plan::PlanArtifact::default();
        let at = plan
            .argv
            .iter()
            .position(|word| word == "--media-root")
            .expect("fixture passes --media-root");
        plan.argv.drain(at..at + 2);
        let preview =
            render_run_script(&profile, &plan, &SubmitOptions::default()).expect("render");
        assert!(!preview.script.contains("--media-root"), "{}", preview.script);
        assert!(preview.script.contains("--raw-root /data/scenes/raw"), "{}", preview.script);
    }

    #[test]
    fn a_saved_plan_missing_its_required_ckpt_root_is_refused_before_submission() {
        // ckpt_root has no manifest default: a plan that records the model's options but
        // no value must be rebuilt, not submitted with a guessed path.
        let profile = profile("ps");
        let mut plan = builtin_plan(&profile);
        plan.ckpt_root = crate::run_plan::PlanArtifact::default();
        let at = plan
            .argv
            .iter()
            .position(|word| word == "ckpt_root=/data/scenes/ckpts")
            .expect("fixture passes ckpt_root");
        plan.argv.drain(at - 1..at + 1);
        let error = render_run_script(&profile, &plan, &SubmitOptions::default())
            .expect_err("missing ckpt_root");
        assert!(error.contains("ckpt_root"), "{error}");
        assert!(error.contains("Rebuild this plan"), "{error}");
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

    /// A built-in model plan as `save_run_plan` writes it: `coperceive model run` with
    /// its own `--out`/`--run-id`/`--work` and nothing a custom pipeline takes.
    fn builtin_plan(profile: &ServerProfile) -> RunPlan {
        let mut plan = plan(profile);
        let out = plan.output_dir.clone();
        plan.pipeline = crate::run_plan::PlanPipelineRef {
            id: "griffin".to_string(),
            name: "griffin".to_string(),
            version: String::new(),
            source: "builtin".to_string(),
        };
        plan.model = "vehicle-side".to_string();
        plan.raw_root = crate::run_plan::PlanArtifact {
            path: "/data/scenes/raw".to_string(),
            kind: "folder".to_string(),
            ..crate::run_plan::PlanArtifact::default()
        };
        plan.media_root = plan.raw_root.clone();
        plan.ckpt_root = crate::run_plan::PlanArtifact {
            path: "/data/scenes/ckpts".to_string(),
            kind: "folder".to_string(),
            ..crate::run_plan::PlanArtifact::default()
        };
        plan.model_options = [
            "ckpt_root", "det_json", "min_score", "nusc_root", "subset",
        ]
        .iter()
        .map(|option| option.to_string())
        .collect();
        plan.work_dir = "$SCRATCH_DIR/work".to_string();
        plan.checkpoint = crate::run_plan::PlanArtifact::default();
        plan.split = crate::run_plan::PlanArtifact {
            path: "/data/scenes/val".to_string(),
            kind: "folder".to_string(),
            real_path: Some("/data/scenes/val".to_string()),
            listing_sha256: Some("1f".repeat(32)),
            file_count: Some(12),
            ..crate::run_plan::PlanArtifact::default()
        };
        plan.argv = [
            "/opt/venv/bin/python", "-m", "coperceive", "model", "run",
            "--manifest", "/data/code/griffin/coperceive_griffin_model/model.yaml",
            "--scenes", "/data/scenes/val",
            "--out", out.as_str(),
            "--run-id", plan.id.as_str(),
            "--model", "vehicle-side",
            "--raw-root", "/data/scenes/raw",
            "--media-root", "/data/scenes/raw",
            "--option", "ckpt_root=/data/scenes/ckpts",
            "--work", "$SCRATCH_DIR/work",
        ]
        .iter()
        .map(|word| word.to_string())
        .collect();
        // What save_run_plan records for `/opt/venv/bin/python -m coperceive` outside a venv
        // with a pyvenv.cfg.
        plan.run_interpreter = Some(crate::run_plan::RunInterpreter {
            python: Some("/opt/venv/bin/python".to_string()),
            source: "command".to_string(),
            program: "/opt/venv/bin/python -m coperceive".to_string(),
            module: None,
            package_root: Some("/opt/coperceive".to_string()),
        });
        // As save_run_plan records it: plan_sbatch's estimate for Griffin over 1,490 samples.
        plan.time_estimate = Some("01:00:00".to_string());
        // The CLI's --slurm-logs default, ~/logs, with the cluster's HOME.
        plan.logs_dir = Some("/home/alice/logs".to_string());
        plan
    }

    #[test]
    fn a_builtin_job_logs_where_the_cli_logs_and_a_custom_one_is_unchanged() {
        let profile = profile("ps");
        let plan = builtin_plan(&profile);
        let script = render_run_script(&profile, &plan, &SubmitOptions::default()).unwrap().script;
        // plan_sbatch: <logs>/%x-%j.{out,err}; %x is the job name, coperceive-run-<plan id>,
        // which job_watch reads the log by.
        assert!(script.contains("\n#SBATCH --output=/home/alice/logs/%x-%j.out\n"), "{script}");
        assert!(script.contains("\n#SBATCH --error=/home/alice/logs/%x-%j.err\n"), "{script}");
        assert!(script.contains(&format!("\n#SBATCH --job-name={}\n", safe_job_name(&plan.id))));
        assert_eq!(safe_job_name(&plan.id), format!("coperceive-run-{}", plan.id));
        // A folder Slurm cannot take in an #SBATCH line (validate_logs_dir), or none.
        for bad in [Some("/home/my logs"), Some("logs"), Some("/home/a\nb"), None] {
            let mut edited = plan.clone();
            edited.logs_dir = bad.map(str::to_string);
            let error = render_run_script(&profile, &edited, &SubmitOptions::default())
                .expect_err("a bad log folder");
            assert!(error.contains("Rebuild this plan"), "{bad:?}: {error}");
        }
        let custom = render_run_script(&profile, &plan_for_custom(&profile), &SubmitOptions::default())
            .unwrap()
            .script;
        assert!(
            custom.contains(&format!(
                "#SBATCH --output={}/{}-%j.out",
                profile.results_root.trim_end_matches('/'),
                safe_job_name(&plan.id)
            )),
            "{custom}"
        );
    }

    #[test]
    fn a_builtin_submit_makes_the_log_folder_before_sbatch_and_a_custom_one_does_not() {
        let profile = profile("ps");
        let mut plan = builtin_plan(&profile);
        plan.logs_dir = Some("/home/alice/o'neil logs".replace(' ', "_"));
        let runner = builtin_saved_runner(&profile, &plan);
        let preview = render_run_script(&profile, &plan, &SubmitOptions::default()).expect("render");
        submit_saved_plan(&runner, &profile, &plan.id, &SubmitOptions::default(), &preview.preview_hash, true)
            .expect("submit");
        let calls = runner.calls();
        let calls = calls.lock().unwrap();
        let mkdir = calls
            .iter()
            .position(|(cmd, _)| cmd.contains("coperceive-submit:logs-dir"))
            .unwrap_or_else(|| panic!("no log folder mkdir: {calls:?}"));
        let sbatch = calls.iter().position(|(cmd, _)| cmd.contains("sbatch --parsable")).unwrap();
        assert!(mkdir < sbatch, "{calls:?}");
        // The folder is a quoted positional parameter, never part of the script body.
        assert!(calls[mkdir].0.ends_with(&format!(" sh {}", shell_quote("/home/alice/o'neil_logs"))), "{}", calls[mkdir].0);
        drop(calls);
        let custom = plan_for_custom(&profile);
        let runner = saved_runner(&profile, &custom);
        let preview = render_run_script(&profile, &custom, &SubmitOptions::default()).expect("render");
        submit_saved_plan(&runner, &profile, &custom.id, &SubmitOptions::default(), &preview.preview_hash, true)
            .expect("submit");
        assert!(!runner.calls().lock().unwrap().iter().any(|(cmd, _)| cmd.contains("logs-dir")));
        // A failed mkdir submits nothing.
        let runner = builtin_saved_runner(&profile, &plan);
        runner.responses.lock().unwrap().insert(
            0,
            (
                "coperceive-submit:logs-dir".to_string(),
                CommandResult { success: false, stdout: String::new(), stderr: "Permission denied".into() },
            ),
        );
        let error = submit_saved_plan(&runner, &profile, &plan.id, &SubmitOptions::default(), &preview_for(&profile, &plan), true)
            .expect_err("mkdir failed");
        assert!(error.contains("Permission denied") && error.contains("Nothing was submitted"), "{error}");
        assert!(!runner.calls().lock().unwrap().iter().any(|(cmd, _)| cmd.contains("sbatch")));
    }

    /// `saved_runner` with the built-in plan's scenes folder answered as a folder.
    fn builtin_saved_runner(profile: &ServerProfile, plan: &RunPlan) -> FakeRunner {
        let runner = saved_runner(profile, plan);
        // A built-in plan has no checkpoint: an empty needle would answer every command.
        runner.responses.lock().unwrap().retain(|(needle, _)| !needle.is_empty());
        runner.responses.lock().unwrap().insert(
            0,
            (
                plan.split.path.clone(),
                CommandResult {
                    success: true,
                    stdout: format!(
                        "EXISTS=1\nREADABLE=1\nEXEC=1\nISDIR=1\nREALPATH={}\nSHA256=\nFILES=12\nLISTING_SHA256={}\n",
                        plan.split.path,
                        "1f".repeat(32)
                    ),
                    stderr: String::new(),
                },
            ),
        );
        runner
    }

    fn preview_for(profile: &ServerProfile, plan: &RunPlan) -> String {
        render_run_script(profile, plan, &SubmitOptions::default()).unwrap().preview_hash
    }

    #[test]
    fn a_builtin_job_mails_and_exports_the_clusters_coperceive_as_the_cli_does() {
        let profile = profile("ps");
        let plan = builtin_plan(&profile);
        let script = render_run_script(&profile, &plan, &SubmitOptions::default())
            .expect("render")
            .script;
        let lines: Vec<&str> = script.lines().collect();
        // plan_sbatch: --output, --error, then --mail-type=END,FAIL.
        let error_at = lines.iter().position(|line| line.starts_with("#SBATCH --error=")).unwrap();
        assert_eq!(lines[error_at + 1], "#SBATCH --mail-type=END,FAIL", "{script}");
        // plan_sbatch exports the root of its coperceive, quoted, after the module loads.
        let export_at = lines
            .iter()
            .position(|line| line.starts_with("export PYTHONPATH=/opt/coperceive  # "))
            .unwrap_or_else(|| panic!("no PYTHONPATH line:\n{script}"));
        let last_module = lines.iter().rposition(|line| line.starts_with("module load ")).unwrap();
        assert!(last_module < export_at, "{script}");
        // A root with a space is quoted; one the interpreter could not name is not exported.
        let mut spaced = plan.clone();
        spaced.run_interpreter.as_mut().unwrap().package_root = Some("/opt/my coperceive".into());
        let script = render_run_script(&profile, &spaced, &SubmitOptions::default()).unwrap().script;
        assert!(script.contains("\nexport PYTHONPATH='/opt/my coperceive'  # "), "{script}");
        let mut unknown = plan.clone();
        unknown.run_interpreter.as_mut().unwrap().package_root = None;
        let script = render_run_script(&profile, &unknown, &SubmitOptions::default()).unwrap().script;
        assert!(!script.contains("export PYTHONPATH"), "{script}");
        // A saved root with a line break is refused: it would end the export line.
        let mut hostile = plan.clone();
        hostile.run_interpreter.as_mut().unwrap().package_root = Some("/opt/x\nrm -rf /".into());
        let error = render_run_script(&profile, &hostile, &SubmitOptions::default())
            .expect_err("a line break in PYTHONPATH");
        assert!(error.contains("Rebuild this plan"), "{error}");
        // A custom pipeline is unchanged: no mail, no PYTHONPATH.
        let script = render_run_script(&profile, &plan_for_custom(&profile), &SubmitOptions::default())
            .unwrap()
            .script;
        assert!(!script.contains("--mail-type") && !script.contains("PYTHONPATH"), "{script}");
    }

    fn plan_for_custom(profile: &ServerProfile) -> RunPlan {
        plan(profile)
    }

    #[test]
    fn a_builtin_walltime_defaults_to_the_clis_estimate_never_to_four_hours() {
        let profile = profile("ps");
        let plan = builtin_plan(&profile);
        let blank = SubmitOptions { walltime: String::new(), ..SubmitOptions::default() };
        let preview = render_run_script(&profile, &plan, &blank).expect("render");
        assert_eq!(preview.walltime, "01:00:00");
        assert!(preview.script.contains("#SBATCH --time=01:00:00\n"), "{}", preview.script);
        // The form's value wins.
        let set = SubmitOptions { walltime: "02:00:00".into(), ..SubmitOptions::default() };
        assert_eq!(render_run_script(&profile, &plan, &set).unwrap().walltime, "02:00:00");
        // Without an estimate the walltime must be entered.
        let mut unknown = plan.clone();
        unknown.time_estimate = None;
        let error = render_run_script(&profile, &unknown, &blank).expect_err("no walltime");
        assert!(error.contains("walltime"), "{error}");
        assert_eq!(SubmitOptions::default().walltime, "", "no implicit 04:00:00");
    }

    #[test]
    fn a_walltime_over_the_partitions_limit_is_refused_before_the_preview() {
        let profile = profile("ps");
        let mut plan = builtin_plan(&profile);
        plan.partition_max_time = Some("1-00:00:00".to_string());
        for (walltime, ok) in [("1-00:00:00", true), ("23:59:59", true), ("1-00:00:01", false), ("48:00:00", false)] {
            let options = SubmitOptions { walltime: walltime.into(), ..SubmitOptions::default() };
            match render_run_script(&profile, &plan, &options) {
                Ok(_) => assert!(ok, "{walltime} must be refused"),
                Err(error) => {
                    assert!(!ok, "{walltime}: {error}");
                    assert!(error.contains("1-00:00:00") && error.contains("ps"), "{error}");
                }
            }
        }
        // No discovered limit: nothing to refuse.
        plan.partition_max_time = None;
        let long = SubmitOptions { walltime: "7-00:00:00".into(), ..SubmitOptions::default() };
        render_run_script(&profile, &plan, &long).expect("no limit discovered");
    }

    #[test]
    fn a_gpu_type_that_contradicts_the_manifests_constraint_is_refused() {
        let profile = profile("gpu-mixed");
        let mut plan = with_resources(
            builtin_plan(&profile),
            serde_json::json!({"gpu": true, "cpus": 8, "memory_gb": 64,
                               "transient_artifacts": [], "constraint": "a40"}),
        );
        plan.gres = Some("gpu:1".to_string());
        let pick = |gres: &str| SubmitOptions { gres: Some(gres.into()), ..SubmitOptions::default() };
        let error = render_run_script(&profile, &plan, &pick("gpu:a100:1")).expect_err("a100 vs a40");
        assert!(error.contains("a100") && error.contains("a40"), "{error}");
        render_run_script(&profile, &plan, &pick("gpu:a40:1")).expect("the constraint's own type");
        render_run_script(&profile, &plan, &pick("gpu:2")).expect("an untyped request");
        // A constraint that allows either type.
        let either = with_resources(
            plan.clone(),
            serde_json::json!({"gpu": true, "cpus": 8, "memory_gb": 64,
                               "transient_artifacts": [], "constraint": "a40|a100"}),
        );
        render_run_script(&profile, &either, &pick("gpu:a100:1")).expect("a40|a100");
    }

    #[test]
    fn a_builtin_plan_without_its_interpreter_record_must_be_rebuilt() {
        // Without the interpreter read at plan time the job cannot load its python module
        // after module purge; such a plan is rebuilt, not run.
        let profile = profile("ps");
        let mut plan = builtin_plan(&profile);
        plan.run_interpreter = None;
        let error = render_run_script(&profile, &plan, &SubmitOptions::default())
            .expect_err("no interpreter record");
        assert!(error.contains("Rebuild this plan"), "{error}");
    }

    #[test]
    fn a_builtin_run_passes_model_run_no_output_dir() {
        // `coperceive model run` has no --output-dir (coperceive/models/commands.py,
        // the `run` parser): argparse exits 2 on it, so the job died at once.
        let profile = profile("ps");
        let plan = builtin_plan(&profile);
        let preview =
            render_run_script(&profile, &plan, &SubmitOptions::default()).expect("render");
        assert!(!preview.script.contains("--output-dir"), "{}", preview.script);
        let run_line = preview
            .script
            .lines()
            .find(|line| line.contains(" -m coperceive model run "))
            .expect("the model run line");
        assert!(run_line.contains(&format!("--out {}", plan.output_dir)), "{run_line}");
        assert!(run_line.contains(&format!("--run-id {}", plan.id)), "{run_line}");
    }

    #[test]
    fn a_builtin_plan_saved_before_the_fix_must_be_rebuilt() {
        // Every built-in plan saved before this fix carries --output-dir (and, for
        // Griffin, a checkpoint option its manifest refuses): rebuild, do not run it.
        let profile = profile("ps");
        let mut plan = builtin_plan(&profile);
        plan.argv.push("--output-dir".to_string());
        plan.argv.push(plan.output_dir.clone());
        let error = render_run_script(&profile, &plan, &SubmitOptions::default())
            .expect_err("an old built-in plan");
        assert!(error.contains("Rebuild this plan"), "{error}");
    }

    #[test]
    fn a_builtin_run_checks_the_files_model_run_writes() {
        // model run writes <out>/<run_id>/run.json and <out>/<run_id>/<scene>/boxes.parquet
        // (coperceive/evaluation/predictions.py write_run, called by models/batch.py).
        let profile = profile("ps");
        let plan = builtin_plan(&profile);
        let script = render_run_script(&profile, &plan, &SubmitOptions::default())
            .expect("render")
            .script;
        assert!(!script.contains("predictions.jsonl"), "{script}");
        assert!(!script.contains("metrics.json"), "{script}");
        assert!(
            script.contains(&format!("test -s \"$OUT_DIR/{}/run.json\"", plan.id)),
            "{script}"
        );
        assert!(script.contains("boxes.parquet"), "{script}");
    }

    #[test]
    fn a_custom_pipeline_keeps_its_output_dir_and_output_contract_checks() {
        let profile = profile("ps");
        let script = render_run_script(&profile, &plan(&profile), &SubmitOptions::default())
            .expect("render")
            .script;
        assert_eq!(script.matches("--output-dir \"$OUT_DIR\"").count(), 1, "{script}");
        assert!(script.contains("test -s \"$OUT_DIR/predictions.jsonl\""), "{script}");
        assert!(script.contains("test -s \"$OUT_DIR/metrics.json\""), "{script}");
        assert!(!script.contains("run.json"), "{script}");
        assert!(script.contains("python3 - \"$OUT_DIR/environment.json\""), "{script}");
    }

    #[test]
    fn environment_json_is_recorded_with_the_runs_interpreter() {
        let profile = profile("ps");
        let plan = builtin_plan(&profile);
        let script = render_run_script(&profile, &plan, &SubmitOptions::default())
            .expect("render")
            .script;
        let capture = format!(
            "/opt/venv/bin/python - \"$OUT_DIR/{}/environment.json\" <<'PY' || true",
            plan.id
        );
        assert!(script.contains(&capture), "{script}");
        assert!(!script.contains("python3 - "), "{script}");
        // As plan_sbatch: after model run, in the run directory beside run.json (made
        // first), before the work copy and the exit-status check.
        let lines: Vec<&str> = script.lines().collect();
        let at = |wanted: &str| {
            lines
                .iter()
                .position(|line| *line == wanted)
                .unwrap_or_else(|| panic!("no line {wanted:?}:\n{script}"))
        };
        let capture_at = at(&capture);
        let mkdir_at = at(&format!("mkdir -p -m 0700 -- \"$OUT_DIR/{}\"", plan.id));
        let echo_at = at("echo \"model run exit $RC\"");
        let rc_check_at = at("if [ \"$RC\" -ne 0 ]; then exit \"$RC\"; fi");
        assert!(echo_at < mkdir_at && mkdir_at < capture_at && capture_at < rc_check_at, "{script}");
        assert_eq!(script.matches("environment.json\" <<'PY'").count(), 1, "{script}");
    }

    /// `plan` with what its built-in model's manifest asks of the job, as
    /// `save_run_plan` records it from `coperceive model list --json`.
    fn with_resources(plan: RunPlan, resources: serde_json::Value) -> RunPlan {
        let mut value = serde_json::to_value(&plan).expect("plan json");
        value["model_resources"] = resources;
        serde_json::from_value(value).expect("plan with resources")
    }

    fn griffin_resources() -> serde_json::Value {
        serde_json::json!({"gpu": true, "cpus": 8, "memory_gb": 64, "transient_artifacts": []})
    }

    fn no_job_defaults(mut profile: ServerProfile) -> ServerProfile {
        profile.defaults = crate::hpc::JobDefaults {
            partition: profile.defaults.partition.clone(),
            ..crate::hpc::JobDefaults::default()
        };
        profile
    }

    #[test]
    fn a_builtin_run_works_in_scratch_and_copies_only_logs_and_artifacts_home() {
        let profile = profile("ps");
        let script = render_run_script(&profile, &builtin_plan(&profile), &SubmitOptions::default())
            .expect("render")
            .script;
        let run_line = script
            .lines()
            .find(|line| line.contains(" model run "))
            .expect("the model run line");
        assert!(run_line.ends_with("--work \"$SCRATCH_DIR/work\""), "{run_line}");
        assert!(script.contains("mkdir -p -m 0700 -- \"$SCRATCH_DIR/work\""), "{script}");
        assert!(
            script.contains(
                "rsync -a --include='*/' --include='*.log' --include='artifacts/***' \
                 --exclude='*' --prune-empty-dirs \"$SCRATCH_DIR/work/\" \"$OUT_DIR/work/\""
            ),
            "{script}"
        );
        // The copy runs whatever model run's status, then the job exits with it.
        let run_at = script.find(run_line).unwrap();
        let copy_at = script.find("rsync -a").unwrap();
        let exit_at = script.find("exit \"$RC\"").expect("exit with the run's status");
        assert!(run_at < copy_at && copy_at < exit_at, "{script}");
    }

    #[test]
    fn transient_artifacts_of_the_closure_stay_in_scratch() {
        let profile = profile("ps");
        let mut plan = with_resources(
            builtin_plan(&profile),
            serde_json::json!({"gpu": true, "cpus": 4, "memory_gb": 32,
                               "transient_artifacts": ["drone-side/track_query"]}),
        );
        plan.gres = Some("gpu:1".to_string());
        let script = render_run_script(&profile, &plan, &SubmitOptions::default())
            .expect("render")
            .script;
        assert!(
            script.contains(
                "rsync -a --exclude='artifacts/drone-side/track_query/***' --include='*/' "
            ),
            "{script}"
        );
    }

    #[test]
    fn a_builtin_plan_whose_out_run_id_or_work_differ_is_refused() {
        let profile = profile("ps");
        let plan = builtin_plan(&profile);
        let out = plan.output_dir.clone();
        for (flag, wrong) in [
            ("--out", "/data/results/runs/elsewhere".to_string()),
            ("--run-id", "plan-1-ab".to_string()),
            ("--work", format!("{out}/work")),
        ] {
            let mut changed = plan.clone();
            let at = changed.argv.iter().position(|word| word == flag).expect(flag);
            changed.argv[at + 1] = wrong.clone();
            let error = render_run_script(&profile, &changed, &SubmitOptions::default())
                .expect_err(flag);
            assert!(error.contains("Rebuild this plan"), "{flag} {wrong}: {error}");
            assert!(error.contains(flag), "{flag}: {error}");
        }
        let mut missing = plan.clone();
        let at = missing.argv.iter().position(|word| word == "--work").unwrap();
        missing.argv.drain(at..at + 2);
        let error = render_run_script(&profile, &missing, &SubmitOptions::default())
            .expect_err("no --work");
        assert!(error.contains("--work"), "{error}");
    }

    #[test]
    fn a_builtin_run_requests_the_manifest_cpus_and_memory_unless_they_are_set() {
        let profile = no_job_defaults(profile("ps"));
        let mut plan = with_resources(builtin_plan(&profile), griffin_resources());
        plan.gres = Some("gpu:1".to_string());
        let preview = render_run_script(&profile, &plan, &SubmitOptions::default()).expect("render");
        assert!(preview.script.contains("#SBATCH --cpus-per-task=8\n"), "{}", preview.script);
        assert!(preview.script.contains("#SBATCH --mem=64G\n"), "{}", preview.script);
        assert_eq!(preview.cpus_per_task, Some(8));
        // The profile's memory and the user's CPUs win.
        let mut set = profile.clone();
        set.defaults.memory = Some("8G".to_string());
        let options = SubmitOptions { cpus_per_task: Some(16), ..SubmitOptions::default() };
        let script = render_run_script(&set, &plan, &options).expect("render").script;
        assert!(script.contains("#SBATCH --cpus-per-task=16\n"), "{script}");
        assert!(script.contains("#SBATCH --mem=8G\n"), "{script}");
        assert!(!script.contains("--mem=64G"), "{script}");
    }

    #[test]
    fn a_gpu_model_cannot_be_submitted_without_a_gpu() {
        let profile = profile("gpu-a40");
        let mut plan = with_resources(builtin_plan(&profile), griffin_resources());
        plan.gres = Some("gpu:a40:1".to_string());
        let no_gpu = SubmitOptions { gres: Some(String::new()), ..SubmitOptions::default() };
        let error = render_run_script(&profile, &plan, &no_gpu).expect_err("a GPU model");
        assert!(error.contains("needs a GPU"), "{error}");
        // Another GPU request is still the user's choice.
        let other = SubmitOptions { gres: Some("gpu:1".to_string()), ..SubmitOptions::default() };
        let preview = render_run_script(&profile, &plan, &other).expect("render");
        assert_eq!(preview.gres.as_deref(), Some("gpu:1"));
        // A plan whose model needs no GPU may still clear it.
        let cpu = with_resources(
            builtin_plan(&profile),
            serde_json::json!({"gpu": false, "cpus": 1, "memory_gb": 2, "transient_artifacts": []}),
        );
        render_run_script(&profile, &cpu, &no_gpu).expect("a CPU model");
    }

    #[test]
    fn environment_json_uses_the_python_beside_a_coperceive_script() {
        // A discovered profile runs `<venv>/bin/coperceive`; pip writes that script with the
        // shebang of the environment's own interpreter, `<venv>/bin/python`.
        let profile = profile("ps");
        let mut plan = builtin_plan(&profile);
        plan.argv.splice(0..3, ["/opt/venv/bin/coperceive".to_string()]);
        plan.run_interpreter = Some(crate::run_plan::RunInterpreter {
            python: Some("/opt/venv/bin/python".to_string()),
            source: "beside".to_string(),
            program: "/opt/venv/bin/coperceive".to_string(),
            module: None,
            package_root: None,
        });
        let script = render_run_script(&profile, &plan, &SubmitOptions::default())
            .expect("render")
            .script;
        assert!(
            script.contains(&format!(
                "/opt/venv/bin/python - \"$OUT_DIR/{}/environment.json\" <<'PY' || true",
                plan.id
            )),
            "{script}"
        );
        assert!(script.contains("/opt/venv/bin/coperceive model run "), "{script}");
    }

    #[test]
    fn environment_json_uses_the_venv_python_for_a_bare_coperceive_on_a_venv_profile() {
        let mut profile = profile("ps");
        profile.environment = EnvironmentSetup {
            kind: EnvironmentKind::Venv,
            interpreter: Some("/data/code/venv/bin/python".to_string()),
            ..EnvironmentSetup::default()
        };
        let mut plan = builtin_plan(&profile);
        plan.environment = profile.environment.clone();
        plan.argv.splice(0..3, ["coperceive".to_string()]);
        plan.run_interpreter = Some(crate::run_plan::RunInterpreter {
            python: Some("/data/code/venv/bin/python".to_string()),
            source: "profile".to_string(),
            program: "coperceive".to_string(),
            module: None,
            package_root: None,
        });
        let script = render_run_script(&profile, &plan, &SubmitOptions::default())
            .expect("render")
            .script;
        assert!(
            script.contains(&format!(
                "/data/code/venv/bin/python - \"$OUT_DIR/{}/environment.json\"",
                plan.id
            )),
            "{script}"
        );
    }

    #[test]
    fn environment_json_says_so_when_the_interpreter_cannot_be_known() {
        let mut profile = profile("ps");
        profile.environment = EnvironmentSetup { kind: EnvironmentKind::None, ..EnvironmentSetup::default() };
        let mut plan = builtin_plan(&profile);
        plan.environment = profile.environment.clone();
        plan.argv.splice(0..3, ["coperceive".to_string()]);
        plan.run_interpreter = Some(crate::run_plan::RunInterpreter {
            python: None,
            source: "unknown".to_string(),
            program: "coperceive".to_string(),
            module: None,
            package_root: None,
        });
        let script = render_run_script(&profile, &plan, &SubmitOptions::default())
            .expect("render")
            .script;
        let capture = script
            .lines()
            .position(|line| line.ends_with("/environment.json\" <<'PY' || true"))
            .expect("the capture line");
        let lines: Vec<&str> = script.lines().collect();
        assert!(lines[capture].starts_with("python3 - "), "{script}");
        assert!(
            lines[capture - 1].starts_with('#') && lines[capture - 1].contains("cannot be known"),
            "{script}"
        );
    }

    #[test]
    fn an_interpreter_with_a_line_break_never_reaches_the_job_script() {
        // The interpreter is named in a comment line of the job; a line break in it would
        // end the comment and run the rest as a command.
        let hostile = "/data/code/venv/bin/python\ntouch /tmp/owned";
        let mut profile = profile("ps");
        profile.environment = EnvironmentSetup {
            kind: EnvironmentKind::Venv,
            interpreter: Some(hostile.to_string()),
            ..EnvironmentSetup::default()
        };
        let mut plan = builtin_plan(&profile);
        plan.environment = profile.environment.clone();
        plan.argv.splice(0..3, ["coperceive".to_string()]);
        let mut value = serde_json::to_value(&plan).expect("plan json");
        value["run_interpreter"] =
            serde_json::json!({"python": hostile, "source": "profile", "program": "coperceive"});
        let plan: RunPlan = serde_json::from_value(value.clone()).expect("plan");
        let error = render_run_script(&profile, &plan, &SubmitOptions::default())
            .expect_err("a line break in the interpreter");
        assert!(error.contains("line break"), "{error}");
        // The source is read back from the saved plan too, and named in the same comment:
        // only the values save_run_plan writes are accepted.
        for source in ["x)\nrm -rf -- \"$OUT_DIR\"\n#", "elsewhere"] {
            let mut edited = value.clone();
            edited["run_interpreter"] = serde_json::json!({
                "python": "/data/code/venv/bin/python", "source": source, "program": "coperceive"
            });
            let plan: RunPlan = serde_json::from_value(edited).expect("plan");
            let error = render_run_script(&profile, &plan, &SubmitOptions::default())
                .expect_err("an unknown interpreter source");
            assert!(error.contains("Rebuild this plan"), "{source:?}: {error}");
        }
        for source in ["command", "beside", "shebang", "profile", "unknown"] {
            let mut edited = value.clone();
            edited["run_interpreter"] = serde_json::json!({
                "python": "/data/code/venv/bin/python", "source": source, "program": "coperceive"
            });
            let plan: RunPlan = serde_json::from_value(edited).expect("plan");
            render_run_script(&profile, &plan, &SubmitOptions::default()).expect(source);
        }
    }

    #[test]
    fn submit_refuses_a_plan_whose_inputs_changed_since_it_was_saved() {
        let profile = profile("ps");
        let plan = plan(&profile);
        let preview = render_run_script(&profile, &plan, &SubmitOptions::default()).expect("render");
        // The checkpoint file was replaced after the save.
        let mut changed = plan.checkpoint.clone();
        changed.sha256 = Some("c".repeat(64));
        let runner = FakeRunner::default()
            .with(&format!("/plans/{}.json", plan.id), &serde_json::to_string(&plan).unwrap())
            .with(&plan.checkpoint.path, &file_answer(&changed))
            .with(&plan.split.path, &file_answer(&plan.split))
            .with("sbatch --parsable", "424242;cluster-b\n");
        let error = submit_saved_plan(
            &runner, &profile, &plan.id, &SubmitOptions::default(), &preview.preview_hash, true,
        )
        .expect_err("a changed checkpoint");
        assert!(error.contains("checkpoint"), "{error}");
        assert!(error.contains("changed since the plan was saved"), "{error}");
        assert!(!runner.calls().lock().unwrap().iter().any(|(cmd, _)| cmd.contains("sbatch")));

        // A scenes folder whose listing changed (a scene added, removed or resized).
        let builtin = builtin_plan(&profile);
        let preview =
            render_run_script(&profile, &builtin, &SubmitOptions::default()).expect("render");
        let runner = FakeRunner::default()
            .with(&format!("/plans/{}.json", builtin.id), &serde_json::to_string(&builtin).unwrap())
            .with(
                &builtin.split.path,
                &format!(
                    "EXISTS=1\nREADABLE=1\nEXEC=1\nISDIR=1\nREALPATH=/data/scenes/val\nSHA256=\nFILES=13\nLISTING_SHA256={}\n",
                    "2e".repeat(32)
                ),
            )
            .with("sbatch --parsable", "424242;cluster-b\n");
        let error = submit_saved_plan(
            &runner, &profile, &builtin.id, &SubmitOptions::default(), &preview.preview_hash, true,
        )
        .expect_err("changed scenes");
        assert!(error.contains("test split"), "{error}");
        assert!(error.contains("changed since the plan was saved"), "{error}");
        assert!(!runner.calls().lock().unwrap().iter().any(|(cmd, _)| cmd.contains("sbatch")));

        // Unchanged scenes submit.
        let runner = FakeRunner::default()
            .with(&format!("/plans/{}.json", builtin.id), &serde_json::to_string(&builtin).unwrap())
            .with(
                &builtin.split.path,
                &format!(
                    "EXISTS=1\nREADABLE=1\nEXEC=1\nISDIR=1\nREALPATH=/data/scenes/val\nSHA256=\nFILES=12\nLISTING_SHA256={}\n",
                    "1f".repeat(32)
                ),
            )
            .with("sbatch --parsable", "424242;cluster-b\n");
        submit_saved_plan(
            &runner, &profile, &builtin.id, &SubmitOptions::default(), &preview.preview_hash, true,
        )
        .expect("unchanged inputs submit");
    }

    /// Runs the rendered job with bash against a fake `python` that parses `model run`'s
    /// flags as strictly as argparse does and writes what `model run` writes.
    #[test]
    #[cfg(unix)]
    fn a_good_builtin_run_exits_zero_and_one_without_output_does_not() {
        use std::os::unix::fs::PermissionsExt;
        if !std::path::Path::new("/bin/bash").exists() {
            return;
        }
        let root = std::env::temp_dir()
            .join(format!("cp-builtin-run-{}", std::process::id()))
            .canonicalize_or_create();
        let python = root.join("venv/bin/python");
        std::fs::create_dir_all(python.parent().unwrap()).unwrap();
        std::fs::create_dir_all(root.join("results")).unwrap();
        std::fs::write(
            &python,
            r#"#!/bin/bash
if [ "$1" = "-" ]; then cat >/dev/null; printf '{}\n' > "$2"; exit 0; fi
[ "$1 $2 $3 $4" = "-m coperceive model run" ] || { echo "not model run: $*" >&2; exit 2; }
shift 4
while [ $# -gt 0 ]; do
  case "$1" in
    --manifest|--scenes|--model|--raw-root|--media-root|--option) shift 2 ;;
    --work) WORK=$2; shift 2 ;;
    --out) OUT=$2; shift 2 ;;
    --run-id) RID=$2; shift 2 ;;
    *) echo "coperceive: error: unrecognized arguments: $1" >&2; exit 2 ;;
  esac
done
mkdir -p "$OUT/$RID/val00" "$WORK/vehicle-side/toolkit" "$WORK/artifacts/vehicle-side/det_json"
printf 'log\n' > "$WORK/vehicle-side/worker.log"
printf 'copy\n' > "$WORK/vehicle-side/toolkit/test.py"
printf '{}\n' > "$WORK/artifacts/vehicle-side/det_json/results_nusc_det.json"
# run.json as write_run indents it; provenance.boxes_written counts the rows written.
N=3; [ -z "${FAKE_ZERO_BOXES:-}" ] || N=0
printf '{\n  "provenance": {\n    "boxes_written": %s\n  }\n}\n' "$N" > "$OUT/$RID/run.json"
[ -n "${FAKE_NO_BOXES:-}${FAKE_ZERO_BOXES:-}" ] || printf 'PAR1' > "$OUT/$RID/val00/boxes.parquet"
exit "${FAKE_EXIT:-0}"
"#,
        )
        .unwrap();
        std::fs::set_permissions(&python, std::fs::Permissions::from_mode(0o755)).unwrap();
        let mut profile = profile("ps");
        profile.data_roots = vec![root.join("data").to_string_lossy().to_string()];
        profile.code_roots = Vec::new();
        profile.results_root = root.join("results").to_string_lossy().to_string();
        profile.scratch_template = format!("{}/scratch/$SLURM_JOB_ID", root.display());
        profile.environment = EnvironmentSetup {
            kind: EnvironmentKind::None,
            ..EnvironmentSetup::default()
        };
        let mut plan = builtin_plan(&profile);
        plan.environment = profile.environment.clone();
        plan.argv[0] = python.to_string_lossy().to_string();
        // As save_run_plan records it for this `<python> -m coperceive`.
        if let Some(found) = plan.run_interpreter.as_mut() {
            found.python = Some(plan.argv[0].clone());
        }
        let script = render_run_script(&profile, &plan, &SubmitOptions::default())
            .expect("render")
            .script;
        let path = root.join("job.sbatch");
        std::fs::write(&path, &script).unwrap();
        let run_full = |no_boxes: bool, zero_boxes: bool, exit: &str| {
            let mut command = std::process::Command::new("/bin/bash");
            command.arg(&path).env("SLURM_JOB_ID", "7").env("USER", "tester").env("FAKE_EXIT", exit);
            if no_boxes {
                command.env("FAKE_NO_BOXES", "1");
            }
            if zero_boxes {
                command.env("FAKE_ZERO_BOXES", "1");
            }
            command.output().expect("bash")
        };
        let run_with = |no_boxes: bool, exit: &str| run_full(no_boxes, false, exit);
        let run = |no_boxes: bool| run_with(no_boxes, "0");
        let good = run(false);
        assert!(
            good.status.success(),
            "a good run must exit 0: {}\n{script}",
            String::from_utf8_lossy(&good.stderr)
        );
        // environment.json goes beside run.json, in <out>/<run_id>/, as the CLI writes it.
        let run_dir = std::path::Path::new(&plan.output_dir).join(&plan.id);
        assert!(run_dir.join("environment.json").is_file(), "{script}");
        assert!(run_dir.join("run.json").is_file());
        assert!(!std::path::Path::new(&plan.output_dir).join("environment.json").exists());
        // The work directory ran in scratch; only its logs and artefacts came home, as the
        // CLI's work_copy_filter copies them (coperceive/models/slurm.py).
        let work = std::path::Path::new(&plan.output_dir).join("work");
        assert!(work.join("vehicle-side/worker.log").is_file(), "{script}");
        assert!(work.join("artifacts/vehicle-side/det_json/results_nusc_det.json").is_file());
        assert!(!work.join("vehicle-side/toolkit").exists(), "the toolkit copy stays in scratch");
        assert!(!root.join("scratch/7").exists(), "the trap removed the scratch");
        std::fs::remove_dir_all(&plan.output_dir).unwrap();
        // run.json says boxes were written but no scene has boxes.parquet: a failed run.
        let empty = run(true);
        assert_eq!(empty.status.code(), Some(1), "{}", String::from_utf8_lossy(&empty.stderr));
        assert!(String::from_utf8_lossy(&empty.stderr).contains("boxes.parquet"));
        // run.json says no boxes were written (scenes without detections): success, as the
        // CLI's job exits with model run's own 0.
        std::fs::remove_dir_all(&plan.output_dir).unwrap();
        let none = run_full(false, true, "0");
        assert!(none.status.success(), "{}\n{script}", String::from_utf8_lossy(&none.stderr));
        // A run.json from an older coperceive has no provenance.boxes_written: the box count
        // is unknown, so only model run's exit 0 and run.json count, never a missing
        // boxes.parquet.
        std::fs::remove_dir_all(&plan.output_dir).unwrap();
        let fake = std::fs::read_to_string(&python).unwrap();
        let older = fake.replace(
            r#"printf '{\n  "provenance": {\n    "boxes_written": %s\n  }\n}\n' "$N""#,
            r#"printf '{\n  "provenance": {\n    "seed": 0\n  }\n}\n'"#,
        );
        assert_ne!(older, fake, "the fake's run.json line moved");
        std::fs::write(&python, &older).unwrap();
        let unknown = run(true);
        assert!(
            unknown.status.success(),
            "a run.json without boxes_written must not fail the run: {}\n{script}",
            String::from_utf8_lossy(&unknown.stderr)
        );
        assert!(run_dir.join("run.json").is_file());
        assert!(!run_dir.join("val00/boxes.parquet").exists());
        std::fs::write(&python, &fake).unwrap();
        // No run.json at all: a failed run.
        std::fs::remove_dir_all(&plan.output_dir).unwrap();
        std::fs::write(&python, fake.replace("> \"$OUT/$RID/run.json\"", "> /dev/null")).unwrap();
        let missing = run(false);
        assert_eq!(missing.status.code(), Some(1), "{}", String::from_utf8_lossy(&missing.stderr));
        assert!(String::from_utf8_lossy(&missing.stderr).contains("run.json"));
        std::fs::write(&python, fake).unwrap();
        // A partial run (exit 3) still records environment.json and brings its logs home,
        // then the job exits with the run's status.
        std::fs::remove_dir_all(&plan.output_dir).unwrap();
        let partial = run_with(false, "3");
        assert_eq!(partial.status.code(), Some(3), "{}", String::from_utf8_lossy(&partial.stderr));
        assert!(run_dir.join("environment.json").is_file(), "{script}");
        assert!(std::path::Path::new(&plan.output_dir).join("work/vehicle-side/worker.log").is_file());
        let _ = std::fs::remove_dir_all(&root);
    }

    trait CanonicalOrCreate {
        fn canonicalize_or_create(self) -> std::path::PathBuf;
    }

    impl CanonicalOrCreate for std::path::PathBuf {
        /// macOS puts the temp dir behind /var -> /private/var; the job script must name
        /// the real path so results_root containment holds.
        fn canonicalize_or_create(self) -> std::path::PathBuf {
            let _ = std::fs::remove_dir_all(&self);
            std::fs::create_dir_all(&self).unwrap();
            self.canonicalize().unwrap()
        }
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
