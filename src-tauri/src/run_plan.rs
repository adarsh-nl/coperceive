// SPDX-License-Identifier: Apache-2.0
//! "New evaluation" run plan: build, check and save a run plan for one server
//! profile — **without submitting anything**.
//!
//! A plan is a pipeline (a script plus a typed argument schema) resolved against
//! a saved server profile: a typed argv (values are always separate argv
//! elements, never one shell string), the resolved environment, a checkpoint and
//! test split with their sha256 (read read-only over SSH), light login-node
//! checks, and a rendered Slurm contract-check script for a few frames. The plan
//! is saved under the profile's `results_root` as `plans/<id>.json` (atomic,
//! 0600). Nothing is submitted here: job submission is the next step, after the
//! user sees the script.

use std::collections::BTreeMap;
use std::time::Duration;

use serde::{Deserialize, Serialize};

use crate::hpc::{
    ArgumentKind, ArgumentRole, EnvironmentKind, EnvironmentSetup, PipelineSpec, ServerProfile,
    coperceive_argv, is_under, normalise_path, validate_scratch_template,
};
use crate::server_profiles::ServerEntry;
use crate::slurm_adapter::RemoteRunner;
use crate::ssh::shell_quote;

pub const RUN_PLAN_SCHEMA: &str = "coperceive-run-plan-1";
/// The AP protocol used when a plan does not name one: the evaluator's default
/// (per-frame ranking, connected-union ground truth within 70 m).
pub const DEFAULT_EVAL_PROTOCOL: &str = "default";
pub const EVAL_PROTOCOLS: [&str; 2] = ["default", "legacy"];

fn default_eval_protocol() -> String {
    DEFAULT_EVAL_PROTOCOL.to_string()
}
pub const PIPELINE_SPEC_FILE: &str = "coperceive-pipeline.json";
pub const CONTRACT_CHECK_FRAMES: u32 = 3;

const READ_TIMEOUT: Duration = Duration::from_secs(8);
const ENVIRONMENT_TIMEOUT: Duration = Duration::from_secs(8);
const CHECK_TIMEOUT: Duration = Duration::from_secs(10);
/// The split's listing walks every file of a scenes tree (on NFS or Lustre for a real
/// dataset), so it gets its own, longer bound than a single-path check.
const LISTING_TIMEOUT: Duration = Duration::from_secs(120);
const WRITE_TIMEOUT: Duration = Duration::from_secs(10);

/// One pipeline offered in the "New evaluation" panel.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct PipelineEntry {
    pub id: String,
    pub name: String,
    #[serde(default)]
    pub description: String,
    /// `builtin` (installed model plugin) or `custom` (user registration).
    pub source: String,
    pub spec: PipelineSpec,
    /// Absolute path of the `coperceive-pipeline.json` that was read (custom).
    #[serde(default)]
    pub registration_path: Option<String>,
    /// Manifest path of an installed plugin (builtin).
    #[serde(default)]
    pub plugin_manifest: Option<String>,
    /// What `coperceive model list --json` reported for an installed plugin.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub builtin: Option<BuiltinPlugin>,
}

/// One model of an installed plugin, as its manifest declares it.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, Default)]
pub struct BuiltinModel {
    /// `resources.gpu` of the manifest.
    #[serde(default)]
    pub gpu: bool,
    /// The `--option` keys the model takes (`options:` of the manifest).
    #[serde(default)]
    pub options: Vec<String>,
    /// `resources.cpus` and `resources.memory_gb`: `plan_sbatch` writes them as
    /// `--cpus-per-task` and `--mem`. None from a listing that does not carry them.
    #[serde(default)]
    pub cpus: Option<u32>,
    #[serde(default)]
    pub memory_gb: Option<u32>,
    /// `outputs.transient_artifacts`: artefacts that stay in the job's scratch.
    #[serde(default)]
    pub transient_artifacts: Vec<String>,
    /// The models its `depends_on` entries name (run first, in the same job).
    #[serde(default)]
    pub depends_on: Vec<String>,
    /// `resources.slurm_constraint`: `plan_sbatch` writes it as `#SBATCH --constraint`
    /// (Griffin pins the A40). None from a listing that does not carry it.
    #[serde(default)]
    pub slurm_constraint: Option<String>,
    /// `resources.seconds_per_sample`, `staging_seconds` and `conversion_seconds`:
    /// `plan_sbatch`'s `--time` estimate (`time_limit`). None from an older listing.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub seconds_per_sample: Option<serde_json::Number>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub staging_seconds: Option<serde_json::Number>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub conversion_seconds: Option<serde_json::Number>,
}

/// What a built-in model's manifest asks of the job, recorded in the plan from the
/// cluster's `coperceive model list --json` so the submitted script can write it as
/// `plan_sbatch` does (coperceive/models/slurm.py).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, Default)]
pub struct ModelResources {
    /// `resources.gpu`: the job must request a GPU.
    pub gpu: bool,
    /// `--cpus-per-task` when neither the profile nor the user sets it.
    #[serde(default)]
    pub cpus: Option<u32>,
    /// `--mem=<n>G` when neither the profile nor the user sets it.
    #[serde(default)]
    pub memory_gb: Option<u32>,
    /// `<model>/<artifact>` of the run's model closure whose artefacts are transient: the
    /// work copy home excludes them first, as `work_copy_filter` does.
    #[serde(default)]
    pub transient_artifacts: Vec<String>,
    /// The model's `resources.slurm_constraint`, written as `#SBATCH --constraint="..."`
    /// whatever GPU type discovery found, as `plan_sbatch` does.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub constraint: Option<String>,
}

/// The interpreter that runs a built-in plan's `coperceive model run`, read on the
/// cluster when the plan is checked and saved (a read-only check). The job loads its
/// `python/<version>` module right after `module purge` and records `environment.json`
/// with it, as `plan_sbatch` does for `--slurm-python` (coperceive/models/slurm.py).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, Default)]
pub struct RunInterpreter {
    /// The interpreter, or None when it cannot be known before the job runs.
    #[serde(default)]
    pub python: Option<String>,
    /// How it was found: `command` (`<python> -m coperceive`), `beside` (the `python`
    /// beside a `<dir>/coperceive` console script), `shebang` (the interpreter that
    /// script's shebang names), `profile` (a bare `coperceive` on a conda or venv
    /// profile) or `unknown`.
    pub source: String,
    /// The program the profile runs (`<python>` or the console script).
    #[serde(default)]
    pub program: String,
    /// `python/<version>` from the interpreter's `pyvenv.cfg` (`interpreter_module`).
    #[serde(default)]
    pub module: Option<String>,
    /// The parent of the coperceive package this interpreter imports, read on the cluster
    /// with `python -c 'import coperceive'`: the job exports it as PYTHONPATH, as
    /// `plan_sbatch` exports the root of the coperceive that emitted the job.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub package_root: Option<String>,
}

/// The `--work` of a built-in run: the job's own scratch, as the CLI's
/// `--work "$SCRATCH/work"`. The job script expands it; only the work directory's logs
/// and artefacts are copied into `<output_dir>/work` afterwards.
pub const WORK_IN_SCRATCH: &str = "$SCRATCH_DIR/work";

/// One installed plugin of `coperceive model list --json` (coperceive/models/commands.py).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, Default)]
pub struct BuiltinPlugin {
    pub name: String,
    /// The manifest file, resolved on the cluster; None when it could not be read.
    #[serde(default)]
    pub manifest: Option<String>,
    #[serde(default)]
    pub error: Option<String>,
    /// `environment.modules` of the manifest (the CUDA modules for Griffin).
    #[serde(default)]
    pub modules: Vec<String>,
    #[serde(default)]
    pub models: BTreeMap<String, BuiltinModel>,
}

/// The user's inputs for one plan. All values are the user's choices.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct RunPlanRequest {
    pub profile_id: String,
    pub pipeline: PipelineSpec,
    #[serde(default)]
    pub pipeline_source: String,
    #[serde(default)]
    pub registration_path: Option<String>,
    #[serde(default)]
    pub plugin_manifest: Option<String>,
    #[serde(default)]
    pub values: BTreeMap<String, serde_json::Value>,
    #[serde(default)]
    pub checkpoint: String,
    #[serde(default)]
    pub split: String,
    /// Built-in model key from the plugin manifest (e.g. `vehicle-side`).
    #[serde(default)]
    pub model: String,
    /// Raw release/media roots for a model with raw inputs.
    #[serde(default)]
    pub raw_root: String,
    #[serde(default)]
    pub media_root: String,
    /// A built-in model parameter the plan records as a path (Griffin's `ckpt_root`).
    #[serde(default)]
    pub ckpt_root: String,
    /// The AP protocol the run is made for: `default` or `legacy`.
    #[serde(default = "default_eval_protocol")]
    pub eval_protocol: String,
    #[serde(default)]
    pub environment_override: Option<EnvironmentSetup>,
}

/// A path recorded in the plan, with the sha256 read over SSH when it is a file.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, Default)]
pub struct PlanArtifact {
    pub path: String,
    #[serde(default)]
    pub sha256: Option<String>,
    /// `file`, `folder` or `missing`.
    #[serde(default)]
    pub kind: String,
    /// Real path reported by the cluster, so symlink escapes are visible.
    #[serde(default)]
    pub real_path: Option<String>,
    /// A folder has no single sha256: the sha256 of its listing (the relative path and
    /// byte size of every regular file, sorted), computed by the same read-only check.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub listing_sha256: Option<String>,
    /// How many regular files that listing holds.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub file_count: Option<u64>,
}

impl PlanArtifact {
    /// A file with its sha256, or a non-empty folder with its listing fingerprint.
    pub fn is_fingerprinted(&self) -> bool {
        self.sha256.is_some()
            || (self.kind == "folder"
                && self.listing_sha256.is_some()
                && self.file_count.unwrap_or(0) > 0)
    }
}

/// One light login-node check. Failures are plain language and never abort the
/// flow; they just make the plan unsaveable until the user fixes them.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, Default)]
pub struct PlanCheck {
    pub name: String,
    pub label: String,
    pub ok: bool,
    #[serde(default)]
    pub detail: String,
    #[serde(default)]
    pub hint: Option<String>,
    #[serde(default)]
    pub duration_ms: u64,
}

/// Which pipeline a plan runs, without any cluster value or secret.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, Default)]
pub struct PlanPipelineRef {
    pub id: String,
    pub name: String,
    #[serde(default)]
    pub version: String,
    /// `builtin` or `custom`.
    pub source: String,
}

/// The saved run plan. Contains no credentials and no cluster defaults that were
/// not chosen by the user.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, Default)]
pub struct RunPlan {
    pub schema: String,
    pub id: String,
    pub created_unix: u64,
    pub profile_id: String,
    pub profile_name: String,
    pub pipeline: PlanPipelineRef,
    pub argv: Vec<String>,
    pub environment: EnvironmentSetup,
    #[serde(default)]
    pub environment_from_override: bool,
    #[serde(default)]
    pub environment_note: String,
    #[serde(default)]
    pub checkpoint: PlanArtifact,
    #[serde(default)]
    pub split: PlanArtifact,
    /// Built-in model key and raw/media roots the plan runs with.
    #[serde(default)]
    pub model: String,
    #[serde(default)]
    pub raw_root: PlanArtifact,
    #[serde(default)]
    pub media_root: PlanArtifact,
    /// The model's `ckpt_root` parameter: the released checkpoints root the plan runs
    /// with. Empty for a custom pipeline, a model that declares no such option, and on
    /// plans saved before it existed.
    #[serde(default)]
    pub ckpt_root: PlanArtifact,
    /// The `--option` keys the model's manifest declared when the plan was saved, so a
    /// submission can check a required parameter without asking the cluster again. Empty
    /// for a custom pipeline and on plans saved before this field existed.
    #[serde(default)]
    pub model_options: Vec<String>,
    /// The AP protocol the run was made for (`default` or `legacy`), recorded so a later
    /// `coperceive eval run` uses it. A plan saved before this field is `default`.
    #[serde(default = "default_eval_protocol")]
    pub eval_protocol: String,
    /// Work directory (inside the run output) and the staging seconds the job
    /// measured. Empty/None on plans saved before these fields existed.
    #[serde(default)]
    pub work_dir: String,
    #[serde(default)]
    pub staging_seconds: Option<f64>,
    /// Where a later submission would write the run (`results_root/runs/<id>`).
    pub output_dir: String,
    /// Scratch directory the contract-check job uses (profile template, expanded
    /// by the job at run time).
    pub scratch_dir: String,
    /// GPU default for a model evaluation on the chosen partition, or None for a
    /// CPU-only partition. The user can override it at submit time, but not clear it for
    /// a model whose manifest needs a GPU.
    #[serde(default)]
    pub gres: Option<String>,
    /// A built-in model's manifest resources (None for a custom pipeline).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub model_resources: Option<ModelResources>,
    /// A built-in model's interpreter and its module (None for a custom pipeline or a
    /// container, and on plans saved before it was recorded).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub run_interpreter: Option<RunInterpreter>,
    /// A built-in run's `--time` as `plan_sbatch` estimates it (`time_limit` over the
    /// manifest's resources and the scenes' samples): the form's default walltime.
    #[serde(default)]
    pub time_estimate: Option<String>,
    /// The profile partition's time limit as discovery found it; a longer walltime is
    /// refused before the preview. None when none was discovered.
    #[serde(default)]
    pub partition_max_time: Option<String>,
    /// A built-in run's Slurm log folder: `<cluster HOME>/logs`, the default of
    /// `coperceive model run --slurm --slurm-logs` (coperceive/models/commands.py). The job
    /// writes `<logs>/%x-%j.{out,err}` there; None for a custom pipeline (results_root).
    #[serde(default)]
    pub logs_dir: Option<String>,
    pub checks: Vec<PlanCheck>,
    pub contract_check_script: String,
    /// Always true in this step: the app never submits from a plan.
    pub nothing_submitted: bool,
    #[serde(default)]
    pub saved_path: Option<String>,
}

impl RunPlan {
    /// Every check passed, so the plan may be saved and later submitted.
    pub fn is_saveable(&self) -> bool {
        self.checks.iter().all(|check| check.ok)
            && (self.checkpoint.path.is_empty() || self.checkpoint.sha256.is_some())
            && (self.split.path.is_empty() || self.split.is_fingerprinted())
    }
}

// ---------------------------------------------------------------------------
// Pure validation and building blocks
// ---------------------------------------------------------------------------

fn check(name: &str, label: &str, ok: bool, detail: impl Into<String>) -> PlanCheck {
    PlanCheck {
        name: name.to_string(),
        label: label.to_string(),
        ok,
        detail: detail.into(),
        hint: None,
        duration_ms: 0,
    }
}

fn with_hint(mut check: PlanCheck, hint: impl Into<String>) -> PlanCheck {
    check.hint = Some(hint.into());
    check
}

/// The environment a plan runs with: a per-pipeline/per-plan override wins, else
/// the server profile's environment.
pub fn resolve_environment(
    profile: &ServerProfile,
    pipeline: &PipelineSpec,
    override_setup: Option<&EnvironmentSetup>,
) -> (EnvironmentSetup, bool, String) {
    if let Some(setup) = override_setup {
        return (
            setup.clone(),
            true,
            "Environment overridden for this plan. Change it in the plan form.".to_string(),
        );
    }
    if pipeline.environment.kind != EnvironmentKind::None {
        return (
            pipeline.environment.clone(),
            true,
            "Environment from the chart's pipeline registration, not the server profile."
                .to_string(),
        );
    }
    (
        profile.environment.clone(),
        false,
        "Environment from the server profile.".to_string(),
    )
}

/// Normalised profile roots (data + code), using the shared containment helpers
/// from `crate::hpc` (the same normalisation #45 introduced for server profiles).
fn normalised_roots(profile: &ServerProfile) -> Result<Vec<String>, String> {
    profile
        .data_roots
        .iter()
        .chain(profile.code_roots.iter())
        .filter(|root| !root.trim().is_empty())
        .map(|root| normalise_path(root, "profile root"))
        .collect()
}

/// Checks that a user-picked path is inside the profile's data or code roots.
/// This is the local (cheap) half; the real path check over SSH catches symlink
/// escapes.
pub fn validate_path_in_roots(
    profile: &ServerProfile,
    path: &str,
    what: &str,
) -> Result<(), String> {
    if path.trim().is_empty() {
        return Err(format!("Choose a {what} path."));
    }
    if path.chars().any(|c| c.is_control()) {
        return Err(format!("The {what} path contains a control character."));
    }
    if path.split('/').any(|part| part == "..") {
        return Err(format!(
            "The {what} path may not contain '..'. Paths must stay inside the server profile's \
             data and code folders."
        ));
    }
    if !path.starts_with('/') && !path.starts_with('~') {
        return Err(format!(
            "The {what} path must be an absolute path on the cluster (it may start with ~)."
        ));
    }
    let mut roots: Vec<String> = profile.data_roots.clone();
    roots.extend(profile.code_roots.iter().cloned());
    if path.starts_with('~') {
        // The remote check resolves ~ and verifies containment against the roots.
        return Ok(());
    }
    let normalised = normalise_path(path, &format!("{what} path"))
        .map_err(|error| format!("The {what} path {path:?} is not usable: {error}"))?;
    let allowed = normalised_roots(profile)?;
    if !allowed.iter().any(|root| is_under(&normalised, root)) {
        return Err(format!(
            "The {what} path {path:?} is outside this server's data and code folders. Pick a path \
             under {roots:?}."
        ));
    }
    Ok(())
}

/// Coerces one typed argument value exactly like the pipeline schema says.
pub fn coerce_value(
    name: &str,
    kind: ArgumentKind,
    choices: &[String],
    value: &serde_json::Value,
) -> Result<serde_json::Value, String> {
    match kind {
        ArgumentKind::Str | ArgumentKind::Path => match value {
            serde_json::Value::String(text) if !text.is_empty() => Ok(value.clone()),
            serde_json::Value::String(_) => Err(format!("Argument {name:?} must not be empty.")),
            _ => Err(format!("Argument {name:?} must be text.")),
        },
        ArgumentKind::Int => match value {
            serde_json::Value::Number(number) if number.is_i64() || number.is_u64() => {
                Ok(value.clone())
            }
            _ => Err(format!("Argument {name:?} must be a whole number.")),
        },
        ArgumentKind::Float => match value {
            serde_json::Value::Number(_) => Ok(value.clone()),
            _ => Err(format!("Argument {name:?} must be a number.")),
        },
        ArgumentKind::Bool => match value {
            serde_json::Value::Bool(_) => Ok(value.clone()),
            _ => Err(format!("Argument {name:?} must be true or false.")),
        },
        ArgumentKind::Enum => match value {
            serde_json::Value::String(text) if choices.iter().any(|choice| choice == text) => {
                Ok(value.clone())
            }
            serde_json::Value::String(text) => Err(format!(
                "Argument {name:?} must be one of: {}. Got {text:?}.",
                choices.join(", ")
            )),
            _ => Err(format!(
                "Argument {name:?} must be one of: {}.",
                choices.join(", ")
            )),
        },
    }
}

/// Turns JSON values into the typed values the pipeline accepts, applying
/// required/default rules.
pub fn typed_values(
    pipeline: &PipelineSpec,
    values: &BTreeMap<String, serde_json::Value>,
) -> Result<BTreeMap<String, serde_json::Value>, String> {
    let known: Vec<&str> = pipeline
        .arguments
        .iter()
        .map(|argument| argument.name.as_str())
        .collect();
    for key in values.keys() {
        if !known.contains(&key.as_str()) {
            return Err(format!("Unknown pipeline argument {key:?}."));
        }
    }
    let mut typed = BTreeMap::new();
    for argument in &pipeline.arguments {
        let raw = match values.get(&argument.name) {
            Some(value) => {
                if argument.multiple {
                    let items = value.as_array().ok_or_else(|| {
                        format!("Argument {:?} takes a list of values.", argument.name)
                    })?;
                    let mut coerced = Vec::new();
                    for item in items {
                        coerced.push(coerce_value(
                            &argument.name,
                            argument.kind,
                            &argument.choices,
                            item,
                        )?);
                    }
                    serde_json::Value::Array(coerced)
                } else {
                    coerce_value(&argument.name, argument.kind, &argument.choices, value)?
                }
            }
            None if argument.required => {
                return Err(format!(
                    "Argument {:?} is required. {}",
                    argument.name,
                    argument.help.trim()
                ));
            }
            None => match &argument.default {
                Some(default) => {
                    if argument.multiple && !default.is_array() {
                        serde_json::Value::Array(vec![default.clone()])
                    } else {
                        default.clone()
                    }
                }
                None => continue,
            },
        };
        typed.insert(argument.name.clone(), raw);
    }
    Ok(typed)
}

fn value_argv(value: &serde_json::Value) -> String {
    match value {
        serde_json::Value::String(text) => text.clone(),
        serde_json::Value::Bool(true) => "true".to_string(),
        serde_json::Value::Bool(false) => "false".to_string(),
        other => other.to_string(),
    }
}

/// Remove any app-owned `--output-dir` argument, in both the separate and `=`
/// forms, before appending the value the app computed.
pub(crate) fn strip_output_dir(argv: &mut Vec<String>) {
    let mut index = 0;
    while index < argv.len() {
        if argv[index] == "--output-dir" {
            let end = (index + 2).min(argv.len());
            argv.drain(index..end);
        } else if argv[index].starts_with("--output-dir=") {
            argv.remove(index);
        } else {
            index += 1;
        }
    }
}

/// Every `path`-typed pipeline argument must stay inside the profile roots, not
/// only the checkpoint and split.
fn validate_path_arguments(
    profile: &ServerProfile,
    pipeline: &PipelineSpec,
    values: &BTreeMap<String, serde_json::Value>,
) -> Result<(), String> {
    for argument in &pipeline.arguments {
        if argument.kind != ArgumentKind::Path {
            continue;
        }
        let Some(value) = values.get(&argument.name).or(argument.default.as_ref()) else {
            continue;
        };
        let texts: Vec<&str> = match value {
            serde_json::Value::String(text) => vec![text.as_str()],
            serde_json::Value::Array(items) => items
                .iter()
                .filter_map(serde_json::Value::as_str)
                .collect(),
            _ => Vec::new(),
        };
        for text in texts {
            validate_path_in_roots(profile, text, &format!("--{} argument", argument.name))?;
        }
    }
    Ok(())
}

/// Builds the full argv for one run. Values become separate argv elements; none
/// of them is ever joined into shell text.
pub fn build_pipeline_argv(
    pipeline: &PipelineSpec,
    values: &BTreeMap<String, serde_json::Value>,
    output_dir: &str,
) -> Result<Vec<String>, String> {
    pipeline.validate()?;
    let typed = typed_values(pipeline, values)?;
    let mut argv: Vec<String> = pipeline.interpreter.clone();
    argv.push(pipeline.script.clone());
    for argument in &pipeline.arguments {
        let Some(value) = typed.get(&argument.name) else {
            continue;
        };
        let flag = format!("--{}", argument.name);
        if argument.multiple {
            if let Some(items) = value.as_array() {
                for item in items {
                    argv.push(flag.clone());
                    argv.push(value_argv(item));
                }
            }
            continue;
        }
        match (argument.kind, value) {
            (ArgumentKind::Bool, serde_json::Value::Bool(true)) => argv.push(flag),
            (ArgumentKind::Bool, serde_json::Value::Bool(false)) => {}
            _ => {
                argv.push(flag);
                argv.push(value_argv(value));
            }
        }
    }
    argv.push("--output-dir".to_string());
    argv.push(output_dir.to_string());
    Ok(argv)
}

/// A path chosen by the user. The remote revalidation below records its real
/// path and sha, like checkpoint/split, but a folder has no sha.
fn artifact_from_check_for_input(path: &str, kind: &str) -> PlanArtifact {
    PlanArtifact {
        path: path.trim().to_string(),
        kind: kind.to_string(),
        ..PlanArtifact::default()
    }
}

/// Built-in argv for an installed model plugin: `coperceive model run` with its own
/// output flags (`--out`, `--run-id`, `--work`; it has no `--output-dir`). A
/// checkpoint is passed as `--option checkpoint=<path>` only when one is given, which
/// the callers allow only for a model whose manifest declares a `checkpoint` option.
pub fn build_builtin_argv(
    profile: &ServerProfile,
    plugin_manifest: &str,
    plan_id: &str,
    values: &BTreeMap<String, serde_json::Value>,
    split: &str,
    checkpoint: &str,
    output_dir: &str,
    model: &str,
    raw_root: &str,
    media_root: &str,
    ckpt_root: &str,
    eval_protocol: &str,
    work_dir: &str,
) -> Result<Vec<String>, String> {
    if plugin_manifest.trim().is_empty() {
        return Err(
            "This built-in model has no pipeline manifest. Reinstall the model or choose a \
             custom pipeline."
                .to_string(),
        );
    }
    let mut argv: Vec<String> = coperceive_argv(
        profile,
        &[
            "model".to_string(),
            "run".to_string(),
            "--manifest".to_string(),
            plugin_manifest.to_string(),
            "--scenes".to_string(),
            split.to_string(),
            "--out".to_string(),
            output_dir.to_string(),
            "--run-id".to_string(),
            plan_id.to_string(),
        ],
    );
    if !checkpoint.trim().is_empty() {
        argv.push("--option".to_string());
        argv.push(format!("checkpoint={}", checkpoint.trim()));
    }
    if !model.trim().is_empty() {
        argv.push("--model".to_string());
        argv.push(model.trim().to_string());
    }
    if !raw_root.trim().is_empty() {
        argv.push("--raw-root".to_string());
        argv.push(raw_root.trim().to_string());
    }
    if !media_root.trim().is_empty() {
        argv.push("--media-root".to_string());
        argv.push(media_root.trim().to_string());
    }
    // A model parameter the plan records as a path (Griffin's ckpt_root) reaches the
    // worker through the manifest's commands.batch template; model run takes it as an
    // --option, like the CLI's own --option ckpt_root=DIR.
    if !ckpt_root.trim().is_empty() {
        argv.push("--option".to_string());
        argv.push(format!("ckpt_root={}", ckpt_root.trim()));
    }
    // Record the AP protocol in the run so eval run can use it. The default is left
    // out, exactly as the CLI's no-flag command leaves it out; the parity then holds.
    if eval_protocol == "legacy" {
        argv.push("--protocol".to_string());
        argv.push("legacy".to_string());
    }
    if !work_dir.trim().is_empty() {
        argv.push("--work".to_string());
        argv.push(work_dir.trim().to_string());
    }
    if let Some(serde_json::Value::Array(extras)) = values.get("extras") {
        for extra in extras {
            argv.push("--option".to_string());
            argv.push(value_argv(extra));
        }
    }
    Ok(argv)
}

fn coperceive_version_command(profile: &ServerProfile) -> String {
    format!(
        "{} --version 2>/dev/null | head -n 1",
        quoted_command(&profile.environment.coperceive_command)
    )
}

/// Quotes argv for a shell script, keeping `$OUT_DIR` as a runtime expansion only
/// where this function places it.
fn quoted_command(argv: &[String]) -> String {
    quoted_command_with(argv, &[])
}

/// Quotes argv for a job script. The value after each flag of `expanded` is written as
/// the given shell text instead (a double-quoted runtime path such as `"$OUT_DIR"`);
/// every other element is single-quoted as needed, so nothing else expands.
pub(crate) fn quoted_command_with(argv: &[String], expanded: &[(&str, &str)]) -> String {
    let mut words = Vec::with_capacity(argv.len());
    let mut replace_next: Option<&str> = None;
    for element in argv {
        match replace_next.take() {
            Some(text) => words.push(text.to_string()),
            None => {
                replace_next = expanded
                    .iter()
                    .find(|(flag, _)| flag == element)
                    .map(|(_, text)| *text);
                words.push(shell_quote(element));
            }
        }
    }
    words.join(" ")
}

/// The environment block of a job script: a modules profile's modules, and for a
/// built-in model on any other non-container profile the manifest's modules (Griffin's
/// CUDA), each after `module purge` as `plan_sbatch` writes them; then a conda/venv
/// profile's interpreter on PATH and a wheelhouse. A container brings its own.
pub(crate) fn environment_lines(environment: &EnvironmentSetup, builtin: bool) -> Vec<String> {
    let mut lines = Vec::new();
    let loads_modules = match environment.kind {
        EnvironmentKind::Modules => true,
        EnvironmentKind::Conda | EnvironmentKind::Venv | EnvironmentKind::None => builtin,
        EnvironmentKind::Container => false,
    };
    if loads_modules && !environment.modules.is_empty() {
        lines.push("module purge".to_string());
        for module in &environment.modules {
            lines.push(format!("module load {}", shell_quote(module)));
        }
    }
    if matches!(environment.kind, EnvironmentKind::Conda | EnvironmentKind::Venv) {
        if let Some(interpreter) = environment
            .interpreter
            .as_deref()
            .filter(|interpreter| !interpreter.is_empty())
        {
            lines.push(format!("export COPERCEIVE_PYTHON={}", shell_quote(interpreter)));
            let parent = std::path::Path::new(interpreter)
                .parent()
                .map(|path| path.to_string_lossy().to_string())
                .unwrap_or_default();
            lines.push(format!("export PATH={}:$PATH", shell_quote(&parent)));
        }
    }
    if let Some(wheelhouse) = environment
        .wheelhouse
        .as_deref()
        .filter(|wheelhouse| !wheelhouse.is_empty())
    {
        lines.push(format!(
            "export PIP_FIND_LINKS={}\nexport PIP_NO_INDEX=1",
            shell_quote(wheelhouse)
        ));
    }
    lines
}

/// The `model run` output checks: `<out>/<run_id>/run.json` and one
/// `<out>/<run_id>/<scene>/boxes.parquet` per scene with boxes
/// (coperceive/evaluation/predictions.py write_run, called from models/batch.py).
///
/// Success is `model run`'s exit 0 (checked before these lines) and a run.json. A
/// boxes.parquet is required only when run.json says rows were written
/// (`provenance.boxes_written` above zero, coperceive/models/batch.py): scenes without a
/// detection write no boxes.parquet, and the CLI's job then exits 0 too.
pub(crate) fn model_run_output_checks(out: &str, run_id: &str) -> Vec<String> {
    let run_dir = format!("{out}/{run_id}");
    vec![
        format!(
            "test -s \"{run_dir}/run.json\" || {{ echo \"coperceive model run wrote no \
             run.json in {run_dir}.\" >&2; exit 1; }}"
        ),
        format!(
            "if grep -Eq '\"boxes_written\": *[1-9]' \"{run_dir}/run.json\"; then compgen -G \
             \"{run_dir}/*/boxes.parquet\" >/dev/null || {{ echo \"coperceive model run wrote \
             boxes but no boxes.parquet for any scene in {run_dir}.\" >&2; exit 1; }}; fi"
        ),
    ]
}

/// Validates the scratch and results folders selected by the profile.
pub fn validate_plan_destinations(profile: &ServerProfile) -> Result<(), String> {
    validate_scratch_template(&profile.scratch_template)?;
    if profile.results_root.trim().is_empty() {
        return Err("Set the server profile's shared results folder first.".to_string());
    }
    profile.validate_output_dir(&format!(
        "{}/runs/placeholder",
        profile.results_root.trim_end_matches('/')
    ))
}

/// Renders the complete Slurm contract-check script for a few frames. It is
/// shown to the user and stored in the plan; step 4/6 submits it. It is never
/// submitted or run from this step.
pub fn render_contract_check_script(
    profile: &ServerProfile,
    environment: &EnvironmentSetup,
    argv: &[String],
    plan_id: &str,
    builtin: bool,
) -> Result<String, String> {
    validate_scratch_template(&profile.scratch_template)?;
    let partition = profile
        .defaults
        .partition
        .as_deref()
        .map(str::trim)
        .filter(|partition| !partition.is_empty())
        .ok_or_else(|| {
            "Choose a partition on the server profile before rendering a contract-check \
             script."
                .to_string()
        })?;
    let safe_id: String = plan_id
        .chars()
        .map(|ch| {
            if ch.is_ascii_alphanumeric() || ch == '-' || ch == '_' {
                ch
            } else {
                '-'
            }
        })
        .collect();
    let job_name = format!("coperceive-check-{safe_id}");
    let mut lines: Vec<String> = vec![
        "#!/bin/bash".to_string(),
        if builtin {
            format!(
                "# Contract check for run plan {plan_id}: coperceive model run over the split, \
                 into scratch."
            )
        } else {
            format!(
                "# Contract check for run plan {plan_id}: {CONTRACT_CHECK_FRAMES} frames through \
                 the pipeline's output contract."
            )
        },
        "# Rendered by Coperceive from the server profile. NOT submitted: the app shows this \
         script first."
            .to_string(),
        format!("#SBATCH --job-name={job_name}"),
        format!("#SBATCH --partition={partition}"),
    ];
    if let Some(time_limit) = profile.defaults.time_limit.as_deref() {
        lines.push(format!("#SBATCH --time={time_limit}"));
    }
    if let Some(memory) = profile.defaults.memory.as_deref() {
        lines.push(format!("#SBATCH --mem={memory}"));
    }
    if let Some(cpus) = profile.defaults.cpus_per_task {
        lines.push(format!("#SBATCH --cpus-per-task={cpus}"));
    }
    // Contract check is a light job: never request a GPU from the profile.
    lines.push("# No GPU requested for this contract check.".to_string());
    if let Some(account) = profile.defaults.account.as_deref() {
        lines.push(format!("#SBATCH --account={account}"));
    }
    if let Some(qos) = profile.defaults.qos.as_deref() {
        lines.push(format!("#SBATCH --qos={qos}"));
    }
    lines.push(String::new());
    lines.push("set -euo pipefail".to_string());
    lines.push(format!("SCRATCH_DIR=\"{}\"", profile.scratch_template));
    lines.push("mkdir -p -m 0700 -- \"$SCRATCH_DIR\"".to_string());
    lines.push(format!(
        "OUT_DIR=\"$SCRATCH_DIR/coperceive-contract-{safe_id}\""
    ));
    lines.push("rm -rf -- \"$OUT_DIR\"".to_string());
    lines.push("mkdir -p -m 0700 -- \"$OUT_DIR\"".to_string());
    lines.push(String::new());

    let mut command: Vec<String> = argv.to_vec();
    // Drop the plan's output-dir argument in either form; the contract check
    // writes to scratch.
    strip_output_dir(&mut command);
    lines.extend(environment_lines(environment, builtin));
    lines.push(format!(
        "echo \"Contract check on $(hostname) for plan {plan_id}\""
    ));
    let check_command = if builtin {
        // `coperceive model run` writes under its own --out/--run-id/--work; the check's
        // copies point into this job's scratch. It has no frame limit (no --max-frames in
        // `coperceive model run --help`; --scene-ids would need scene ids the plan does
        // not know), so the check runs the whole split.
        lines.push(
            "# coperceive model run has no frame limit (its --help lists none), so this check \
             runs the whole split."
                .to_string(),
        );
        quoted_command_with(
            &command,
            &[("--out", "\"$OUT_DIR\""), ("--work", "\"$OUT_DIR/work\"")],
        )
    } else {
        format!(
            "{} --max-frames {CONTRACT_CHECK_FRAMES} --output-dir \"$OUT_DIR\"",
            quoted_command(&command)
        )
    };
    match environment.kind {
        EnvironmentKind::Container => {
            let image = environment
                .container_image
                .as_deref()
                .filter(|image| !image.is_empty())
                .ok_or_else(|| {
                    "This plan uses a container environment but has no image path.".to_string()
                })?;
            lines.push(format!(
                "apptainer exec --cleanenv --bind \"$SCRATCH_DIR\" {} {check_command}",
                shell_quote(image)
            ));
        }
        _ => lines.push(check_command),
    }
    lines.push(String::new());
    if builtin {
        let run_id = command
            .iter()
            .position(|word| word == "--run-id")
            .and_then(|at| command.get(at + 1))
            .cloned()
            .unwrap_or_else(|| plan_id.to_string());
        lines.extend(model_run_output_checks("$OUT_DIR", &run_id));
        lines.push(
            "echo \"Contract check finished: run.json and a boxes.parquet are present.\""
                .to_string(),
        );
        lines.push(String::new());
        return Ok(lines.join("\n"));
    }
    lines.push(
        "test -s \"$OUT_DIR/predictions.jsonl\" || { echo \"The pipeline wrote no \
         predictions.jsonl; it does not meet the output contract.\" >&2; exit 1; }"
            .to_string(),
    );
    lines.push(
        "test -s \"$OUT_DIR/metrics.json\" || { echo \"The pipeline wrote no metrics.json; it \
         does not meet the output contract.\" >&2; exit 1; }"
            .to_string(),
    );
    lines.push(
        "grep -q '\"schema\"[[:space:]]*:[[:space:]]*\"coperceive-pipeline-metrics\"' \
         \"$OUT_DIR/metrics.json\" || { echo \"metrics.json does not declare the pipeline metrics \
         schema.\" >&2; exit 1; }"
            .to_string(),
    );
    lines.push(
        "grep -q '\"version\"[[:space:]]*:[[:space:]]*1' \"$OUT_DIR/metrics.json\" || { echo \
         \"metrics.json does not declare version 1.\" >&2; exit 1; }"
            .to_string(),
    );
    lines.push(
        "echo \"Contract check finished: predictions.jsonl, progress and metrics.json are \
                present.\""
            .to_string(),
    );
    lines.push(String::new());
    Ok(lines.join("\n"))
}

// ---------------------------------------------------------------------------
// Remote, bounded checks
// ---------------------------------------------------------------------------

const PATH_CHECK_BODY: &str = r#"# coperceive-check:plan-path
set -u
P=$1
case "${P:-}" in
  "~")
    if [ -n "${HOME:-}" ]; then P="$HOME"; else echo "ERROR=The remote shell has no HOME, so the ~ path cannot be expanded."; exit 0; fi
    ;;
  "~/"*)
    if [ -n "${HOME:-}" ]; then P="$HOME/${P#"~/"}"
    else echo "ERROR=The remote shell has no HOME, so the ~ path cannot be expanded."; exit 0; fi
    ;;
esac
if [ ! -e "$P" ]; then echo EXISTS=0; echo READABLE=0; echo EXEC=0; echo "REALPATH="; echo "SHA256="; exit 0; fi
echo EXISTS=1
if [ -r "$P" ]; then echo READABLE=1; else echo READABLE=0; fi
if [ -x "$P" ]; then echo EXEC=1; else echo EXEC=0; fi
if [ -d "$P" ]; then echo ISDIR=1; else echo ISDIR=0; fi
if command -v realpath >/dev/null 2>&1; then
  R=$(realpath -m -- "$P" 2>/dev/null) || { echo "ERROR=realpath failed for this path, so coperceive cannot check that it stays inside your folders."; exit 0; }
elif command -v readlink >/dev/null 2>&1; then
  R=$(readlink -f -- "$P" 2>/dev/null) || R=""
  if [ -z "$R" ]; then
    echo "ERROR=This server has neither realpath nor a readlink that supports -f, so coperceive cannot check that the path stays inside your folders. Ask your administrators to install coreutils."
    exit 0
  fi
else
  echo "ERROR=This server has neither realpath nor readlink, so coperceive cannot check that the path stays inside your folders. Ask your administrators to install coreutils."
  exit 0
fi
echo "REALPATH=$R"
if [ -f "$P" ]; then
  S=$(sha256sum -- "$P" 2>/dev/null | cut -d" " -f1)
  if [ -z "$S" ]; then S=$(shasum -a 256 -- "$P" 2>/dev/null | cut -d" " -f1); fi
  echo "SHA256=$S"
else
  echo SHA256=
fi
"#;

/// The test split's fingerprint, after the path check: the sha256 of the sorted list of
/// every regular file's relative path and byte size. `find -L` follows symlinks, as the
/// CLI's scene loader does (coperceive/viewdata.py lists scenes with `child.is_dir()`).
/// A file that cannot be read, or a symlink loop, fails the check with an ERROR line
/// instead of dropping out of the listing. Only the split is listed: a container
/// sandbox image or an interpreter folder is never walked.
const SPLIT_LISTING_BODY: &str = r#"
# coperceive-check:plan-split
if [ -d "$P" ]; then
  W=$(cd -- "$P" 2>/dev/null && find -L . -type f -exec wc -c {} + 2>/dev/null) || { echo "ERROR=A file under the test split $P could not be read (or a symlink in it loops), so its listing cannot be fingerprinted. Check the folder and its permissions."; exit 0; }
  L=$(printf "%s\n" "$W" | grep -v "^ *[0-9][0-9]* total\$" | grep -v "^\$" | sed "s/^ *//" | LC_ALL=C sort)
  N=$(printf "%s" "$L" | grep -c "")
  H=$(printf "%s\n" "$L" | sha256sum 2>/dev/null | cut -d" " -f1)
  if [ -z "$H" ]; then H=$(printf "%s\n" "$L" | shasum -a 256 2>/dev/null | cut -d" " -f1); fi
  echo "FILES=$N"
  echo "LISTING_SHA256=$H"
fi"#;

fn path_check_script(path: &str) -> String {
    format!("sh -c '{}' sh {}", PATH_CHECK_BODY, shell_quote(path))
}

/// The path check plus the folder listing, for the test split only.
fn split_check_script(path: &str) -> String {
    format!(
        "sh -c '{}{}' sh {}",
        PATH_CHECK_BODY,
        SPLIT_LISTING_BODY,
        shell_quote(path)
    )
}

/// The plain message a remote path check put in its `ERROR=` line, if any.
fn path_check_error(values: &BTreeMap<String, String>) -> Option<String> {
    values
        .get("ERROR")
        .map(|value| value.trim().to_string())
        .filter(|value| !value.is_empty())
}

pub const PLAN_ENV_SCRIPT: &str = r#"sh -c '# coperceive-check:plan-env
printf "MODULE=%s\n" "$(command -v module 2>/dev/null || true)"
printf "LMOD=%s\n" "${LMOD_CMD:-}"
printf "APPTAINER=%s\n" "$(command -v apptainer 2>/dev/null || true)"
printf "SINGULARITY=%s\n" "$(command -v singularity 2>/dev/null || true)"
printf "PYTHON3=%s\n" "$(command -v python3 2>/dev/null || true)"
printf "COPERCEIVE=%s\n" "$(command -v coperceive 2>/dev/null || true)"
printf "HOME=%s\n" "${HOME:-}"'"#;

/// Reads the interpreter that runs `coperceive model run` (read-only): `$1` is a python
/// (`$2` = `python`) or a `<dir>/coperceive` console script (`$2` = `script`). For a
/// script, the `python` beside it if there is one, else the first two lines of the script
/// (its shebang, and pip's `/bin/sh` launcher line for a long path). For a python, the
/// `home` and `version` lines of its venv's `pyvenv.cfg`, found as
/// `interpreter_module` finds it (coperceive/models/slurm.py): `<bin>/../pyvenv.cfg` with
/// the bin directory resolved, else beside the resolved interpreter's parent.
const INTERPRETER_CHECK_BODY: &str = r#"# coperceive-check:plan-interpreter
set -u
P=$1
if [ "$2" = script ]; then
  B="$(dirname -- "$P")/python"
  if [ -x "$B" ] && [ ! -d "$B" ]; then
    echo "BESIDE=$B"
    P=$B
  else
    echo "BESIDE="
    if [ -f "$P" ] && [ -r "$P" ]; then
      printf "SHEBANG=%s\n" "$(head -c 1024 -- "$P" 2>/dev/null | head -n 1 | tr -d "\r")"
      printf "SHEBANG2=%s\n" "$(head -c 2048 -- "$P" 2>/dev/null | sed -n 2p | tr -d "\r")"
    fi
    exit 0
  fi
fi
echo "PYTHON=$P"
C=
D=$(cd -- "$(dirname -- "$P")" 2>/dev/null && pwd -P) || D=
if [ -n "$D" ] && [ -f "$(dirname -- "$D")/pyvenv.cfg" ]; then
  C="$(dirname -- "$D")/pyvenv.cfg"
else
  R=$(realpath "$P" 2>/dev/null || readlink -f "$P" 2>/dev/null || true)
  if [ -n "$R" ] && [ -f "$(dirname -- "$(dirname -- "$R")")/pyvenv.cfg" ]; then
    C="$(dirname -- "$(dirname -- "$R")")/pyvenv.cfg"
  fi
fi
echo "PYVENV_CFG=$C"
if [ -n "$C" ]; then
  grep -E "^[[:space:]]*(home|version)[[:space:]]*=" "$C" 2>/dev/null | head -n 20 | sed "s/^/PYVENV_LINE=/"
fi
exit 0"#;

fn interpreter_check_script(program: &str, mode: &str) -> String {
    format!(
        "sh -c '{}' sh {} {}",
        INTERPRETER_CHECK_BODY,
        shell_quote(program),
        shell_quote(mode)
    )
}

/// `python/<version>` from the check's `PYVENV_LINE`s, by `interpreter_module`'s rule:
/// `home = /software/python/<v>/bin` gives `python/<v>`, else the `version` line, else
/// none (not a venv). A version that is not a plain module name is refused: it would be
/// written into the job's `module load` line.
fn pyvenv_module(output: &str) -> Result<Option<String>, String> {
    let mut home: Option<String> = None;
    let mut version: Option<String> = None;
    for line in output.lines().filter_map(|line| line.strip_prefix("PYVENV_LINE=")) {
        let Some((key, value)) = line.split_once('=') else {
            continue;
        };
        match key.trim() {
            "home" => home = Some(value.trim().to_string()),
            "version" => version = Some(value.trim().to_string()),
            _ => {}
        }
    }
    let from_home = home.and_then(|home| {
        let home = format!("{home}/");
        home.match_indices("/software/python/").find_map(|(at, marker)| {
            let rest = &home[at + marker.len()..];
            let end = rest.find('/')?;
            (end > 0).then(|| rest[..end].to_string())
        })
    });
    let Some(found) = from_home.or(version.filter(|version| !version.is_empty())) else {
        return Ok(None);
    };
    if !found
        .chars()
        .all(|ch| ch.is_ascii_alphanumeric() || matches!(ch, '.' | '_' | '+' | '-'))
    {
        return Err(format!(
            "The interpreter's pyvenv.cfg names the Python {found:?}, which is not a module \
             name coperceive can load in the job. Check the venv's pyvenv.cfg on the cluster."
        ));
    }
    Ok(Some(format!("python/{found}")))
}

/// The interpreter a console script's shebang names: `#!/abs/python [args]`, or pip's
/// launcher for a long path (`#!/bin/sh` and `'''exec' "/abs/python" "$0" "$@"`). None
/// for `#!/usr/bin/env python3` (resolved on PATH when the job runs) or anything else.
fn shebang_python(line1: &str, line2: &str) -> Option<String> {
    let first = line1.strip_prefix("#!")?.split_whitespace().next()?;
    let python = if first == "/bin/sh" {
        let rest = line2.trim().strip_prefix("'''exec' \"")?;
        &rest[..rest.find('"')?]
    } else {
        first
    };
    let usable = python.starts_with('/')
        && !python.ends_with("/env")
        && !python.chars().any(|ch| ch.is_whitespace() || ch.is_control());
    usable.then(|| python.to_string())
}

/// What runs `model run`, before any remote read: `(program, mode, source)`, or None when
/// the profile's command names no interpreter (a bare `coperceive` on a modules or plain
/// profile).
fn interpreter_program(
    command: &[String],
    environment: &EnvironmentSetup,
) -> Option<(String, &'static str, &'static str)> {
    match command {
        [python, dash_m, module] if dash_m == "-m" && module == "coperceive" => {
            Some((python.clone(), "python", "command"))
        }
        [script] if script.starts_with('/') && script.ends_with("/coperceive") => {
            Some((script.clone(), "script", "beside"))
        }
        [] => interpreter_program(&["coperceive".to_string()], environment),
        [script]
            if script == "coperceive"
                && matches!(environment.kind, EnvironmentKind::Conda | EnvironmentKind::Venv) =>
        {
            environment
                .interpreter
                .as_deref()
                .map(str::trim)
                .filter(|python| !python.is_empty())
                .map(|python| (python.to_string(), "python", "profile"))
        }
        _ => None,
    }
}

/// Runs the interpreter check once; a cluster that does not answer stops the plan.
fn run_interpreter_check(
    runner: &dyn RemoteRunner,
    program: &str,
    mode: &str,
) -> Result<String, String> {
    let failed = |detail: String| {
        format!(
            "Could not read the interpreter that runs coperceive ({program}) on the cluster: \
             {detail}. Check the server profile's coperceive command and try again."
        )
    };
    match runner.run(&interpreter_check_script(program, mode), CHECK_TIMEOUT, None) {
        Ok(result) if result.success => Ok(result.stdout),
        Ok(result) => Err(failed(result.stderr.trim().to_string())),
        Err(error) => Err(failed(error)),
    }
}

/// The interpreter of a built-in run and its `python/<version>` module, read on the
/// cluster: the python of `<python> -m coperceive`; for a `<dir>/coperceive` script the
/// `python` beside it, else the interpreter its shebang names; for a bare `coperceive` on
/// a conda or venv profile that profile's interpreter. When none of these names one, the
/// python is None (the job records `python3`) and no module is loaded.
fn read_run_interpreter(
    runner: &dyn RemoteRunner,
    profile: &ServerProfile,
    environment: &EnvironmentSetup,
) -> Result<RunInterpreter, String> {
    let command = &profile.environment.coperceive_command;
    let program = if command.is_empty() {
        "coperceive".to_string()
    } else {
        command.join(" ")
    };
    let Some((target, mode, source)) = interpreter_program(command, environment) else {
        return Ok(RunInterpreter { python: None, source: "unknown".to_string(), program, module: None, package_root: None });
    };
    if target.chars().any(char::is_control) {
        return Err(format!(
            "The interpreter {target:?} contains a line break or another control character. \
             Fix the server profile's coperceive command or interpreter."
        ));
    }
    let program = if mode == "script" { target.clone() } else { program };
    if !target.starts_with('/') {
        // A relative python is resolved on PATH when the job runs; there is no venv to read.
        return Ok(RunInterpreter { python: Some(target), source: source.to_string(), program, module: None, package_root: None });
    }
    let mut output = run_interpreter_check(runner, &target, mode)?;
    let values = parse_key_values(&output);
    let (python, source) = if mode == "script" {
        match values.get("BESIDE").filter(|beside| !beside.is_empty()) {
            Some(beside) => (beside.clone(), source),
            None => {
                let named = shebang_python(
                    values.get("SHEBANG").map(String::as_str).unwrap_or(""),
                    values.get("SHEBANG2").map(String::as_str).unwrap_or(""),
                );
                let Some(named) = named else {
                    return Ok(RunInterpreter {
                        python: None,
                        source: "unknown".to_string(),
                        program,
                        module: None,
                        package_root: None,
                    });
                };
                output = run_interpreter_check(runner, &named, "python")?;
                (named, "shebang")
            }
        }
    } else {
        (target, source)
    };
    Ok(RunInterpreter {
        module: pyvenv_module(&output)?,
        python: Some(python),
        source: source.to_string(),
        program,
        package_root: None,
    })
}

/// The interpreter's module goes first, right after `module purge`, as `plan_sbatch`
/// writes it; the profile's and the manifest's modules follow, each once. Any other
/// python module of the profile is dropped: Lmod keeps one module per name, so a
/// `python/3.11` loaded after the interpreter's `python/3.9.18` would swap it, and
/// `plan_sbatch` loads only the interpreter's (the job script says which were dropped,
/// `dropped_python_modules`).
fn with_interpreter_module(
    mut environment: EnvironmentSetup,
    interpreter: &RunInterpreter,
) -> EnvironmentSetup {
    if let Some(module) = &interpreter.module {
        environment.modules.retain(|name| name != module && !is_python_module(name));
        environment.modules.insert(0, module.clone());
    }
    environment
}

/// `python` or `python/<anything>`: the module name Lmod swaps.
fn is_python_module(name: &str) -> bool {
    name == "python" || name.starts_with("python/")
}

/// The profile's python modules a built-in plan does not load because the interpreter's
/// own (`module`) replaces them (`with_interpreter_module`).
pub(crate) fn dropped_python_modules<'a>(
    profile_modules: &'a [String],
    planned: &[String],
    module: &str,
) -> Vec<&'a str> {
    profile_modules
        .iter()
        .filter(|name| is_python_module(name) && name.as_str() != module && !planned.contains(name))
        .map(String::as_str)
        .collect()
}

/// What the run's own python reports on the cluster (read-only): `ROOT=`, the parent of
/// the coperceive package it imports (`coperceive_root` in coperceive/models/slurm.py
/// resolves it the same way), and `SAMPLES=`, the pose times of every scene under the
/// scenes folder, which `plan_sbatch` counts for its `--time` estimate.
const PYTHON_FACTS_CODE: &str = r#"import os, sys
import coperceive
print("ROOT=" + os.path.dirname(os.path.dirname(os.path.realpath(coperceive.__file__))))
sys.stdout.flush()
from pathlib import Path
from coperceive.store import read_scene
from coperceive.viewdata import pose_times, scene_directories
print("SAMPLES=%d" % sum(len(pose_times(read_scene(d))) for d in scene_directories(Path(sys.argv[1]))))
"#;

/// `$1` the python, `$2` its module (loaded first when `module` exists, as the job loads
/// it), `$3` the scenes folder, `$4` the code: every value a positional parameter.
/// Read-only on the login node: `-B` writes no bytecode next to the cluster's coperceive,
/// and the code only imports and reads. It runs under `LISTING_TIMEOUT`, the limit of
/// the other login-node probe that reads the whole scenes tree (the split listing).
fn python_facts_script(python: &str, module: &str, scenes: &str) -> String {
    format!(
        "sh -c '# coperceive-check:plan-python\n\
         if [ -n \"$2\" ] && command -v module >/dev/null 2>&1; then module load \"$2\" >/dev/null 2>&1 || true; fi\n\
         \"$1\" -B -c \"$4\" \"$3\"' sh {} {} {} {}",
        shell_quote(python),
        shell_quote(module),
        shell_quote(scenes),
        shell_quote(PYTHON_FACTS_CODE)
    )
}

/// `time_limit` of coperceive/models/slurm.py: staging + conversion + per-sample x
/// samples, rounded up to the next 600 s (at least 600), doubled, `HH:MM:SS`.
pub(crate) fn cli_time_limit(per_sample: f64, staging: f64, conversion: f64, samples: u64) -> String {
    let estimate = staging + conversion + per_sample * samples as f64;
    let rounded = ((estimate / 600.0).ceil() as u64 * 600).max(600);
    let total = rounded * 2;
    format!("{:02}:{:02}:{:02}", total / 3600, (total % 3600) / 60, total % 60)
}

/// A Slurm time (`MM`, `MM:SS`, `HH:MM:SS`, `D-HH`, `D-HH:MM`, `D-HH:MM:SS`) in seconds;
/// None for `UNLIMITED`/`infinite` or anything else.
pub(crate) fn slurm_seconds(text: &str) -> Option<u64> {
    let text = text.trim();
    let (days, clock) = match text.split_once('-') {
        Some((days, clock)) => (days.parse::<u64>().ok()?, clock),
        None => (0, text),
    };
    let parts: Vec<u64> = clock
        .split(':')
        .map(|part| part.parse::<u64>().ok())
        .collect::<Option<Vec<u64>>>()?;
    let seconds = match (text.contains('-'), parts.as_slice()) {
        (false, [minutes]) => minutes * 60,
        (false, [minutes, seconds]) => minutes * 60 + seconds,
        (_, [hours, minutes, seconds]) => hours * 3600 + minutes * 60 + seconds,
        (true, [hours]) => hours * 3600,
        (true, [hours, minutes]) => hours * 3600 + minutes * 60,
        _ => return None,
    };
    Some(days * 86_400 + seconds)
}

/// `validate_logs_dir` of coperceive/models/slurm.py: an `#SBATCH --output`/`--error`
/// folder is read up to the first blank, so one with a space or a control character is
/// refused, as is one that is not absolute.
pub(crate) fn checked_logs_dir(logs_dir: &str) -> Result<&str, String> {
    if !logs_dir.starts_with('/')
        || logs_dir.chars().any(|ch| ch.is_whitespace() || ch.is_control())
    {
        return Err(format!(
            "The Slurm log folder {logs_dir:?} is not an absolute path without spaces or \
             control characters; #SBATCH --output/--error cannot hold it."
        ));
    }
    Ok(logs_dir)
}

/// A built-in run's log folder, as the CLI's `--slurm-logs` default: `~/logs` with the
/// cluster's HOME (read on the login node).
fn builtin_logs_dir(remote_home: Option<&str>) -> Result<String, String> {
    let home = remote_home.ok_or_else(|| {
        "The cluster did not report HOME, so the job's log folder (~/logs, where coperceive \
         model run --slurm writes it) is unknown. Check the server and try again."
            .to_string()
    })?;
    let logs = format!("{}/logs", home.trim_end_matches('/'));
    checked_logs_dir(&logs)?;
    Ok(logs)
}

/// The profile partition's discovered time limit, if discovery found one.
fn partition_max_time(entry: &ServerEntry) -> Option<String> {
    let partition = entry.profile.defaults.partition.as_deref()?.trim();
    entry
        .discovered
        .as_ref()?
        .partitions
        .iter()
        .find(|item| item.name == partition)?
        .max_time
        .clone()
        .filter(|text| slurm_seconds(text).is_some())
}

/// Reads the run's python on the cluster for a built-in plan: sets the interpreter's
/// `package_root` (refused with control characters) and returns the CLI's `--time`
/// estimate (None, said in the check, when it cannot be computed), with the plan check.
fn read_python_facts(
    runner: &dyn RemoteRunner,
    choice: &BuiltinChoice,
    interpreter: &mut RunInterpreter,
    scenes: &str,
) -> Result<(Option<String>, PlanCheck), String> {
    let Some(python) = interpreter.python.clone() else {
        return Ok((
            None,
            check(
                "python-facts",
                "Time estimate and PYTHONPATH",
                true,
                format!(
                    "The interpreter of {} cannot be known before the job runs, so the job \
                     exports no PYTHONPATH and there is no --time estimate: enter the walltime.",
                    interpreter.program
                ),
            ),
        ));
    };
    let module = interpreter.module.clone().unwrap_or_default();
    let result = runner
        .run(&python_facts_script(&python, &module, scenes), LISTING_TIMEOUT, None)
        .map_err(|error| format!("Could not run {python} on the cluster: {error}"))?;
    let values = parse_key_values(&result.stdout);
    let root = values.get("ROOT").map(|root| root.trim().to_string()).unwrap_or_default();
    if root.is_empty() {
        return Err(format!(
            "{python} on the cluster could not import coperceive, so coperceive model run could \
             not start with it either: {}. Check the server profile's coperceive command.",
            result.stderr.trim()
        ));
    }
    if root.chars().any(char::is_control) || !root.starts_with('/') {
        return Err(format!(
            "{python} reports its coperceive under {root:?}, which is not an absolute path \
             without control characters, so it cannot be written as the job's PYTHONPATH."
        ));
    }
    interpreter.package_root = Some(root.clone());
    let samples = values.get("SAMPLES").and_then(|text| text.trim().parse::<u64>().ok());
    let mut order: Vec<String> = Vec::new();
    choice.visit(&choice.key, &mut Vec::new(), &mut order)?;
    let mut sums = Some((0.0_f64, 0.0_f64, 0.0_f64));
    for key in &order {
        let model = if key == &choice.key { Some(&choice.model) } else { choice.models.get(key) };
        let number = |value: Option<&serde_json::Number>| value.and_then(serde_json::Number::as_f64);
        sums = match (sums, model) {
            (Some((per, staging, conversion)), Some(model)) => {
                match number(model.seconds_per_sample.as_ref()) {
                    Some(per_sample) => Some((
                        per + per_sample,
                        staging + number(model.staging_seconds.as_ref()).unwrap_or(0.0),
                        conversion + number(model.conversion_seconds.as_ref()).unwrap_or(0.0),
                    )),
                    None => None,
                }
            }
            _ => None,
        };
    }
    let estimate = match (sums, samples) {
        (Some((per, staging, conversion)), Some(samples)) => {
            Some((cli_time_limit(per, staging, conversion, samples), samples))
        }
        _ => None,
    };
    let detail = match &estimate {
        Some((limit, samples)) => format!(
            "{python} imports coperceive from {root} (the job's PYTHONPATH). --time {limit}, as \
             coperceive model run --slurm estimates it from the manifest's resources over \
             {samples} samples."
        ),
        None => format!(
            "{python} imports coperceive from {root} (the job's PYTHONPATH). No --time estimate: \
             {}; enter the walltime.",
            if samples.is_none() {
                "the scenes could not be counted on the cluster"
            } else {
                "the cluster's model list does not report the model's timing resources"
            }
        ),
    };
    Ok((
        estimate.map(|(limit, _)| limit),
        check("python-facts", "Time estimate and PYTHONPATH", true, detail),
    ))
}

/// The plan's record of the interpreter check.
fn interpreter_plan_check(interpreter: &RunInterpreter) -> PlanCheck {
    let detail = match (&interpreter.python, &interpreter.module) {
        (Some(python), Some(module)) => format!(
            "{python} runs coperceive; its venv's pyvenv.cfg names {module}, which the job \
             loads right after module purge."
        ),
        (Some(python), None) => format!(
            "{python} runs coperceive; it is not in a venv with a pyvenv.cfg, so the job \
             loads no python module for it."
        ),
        (None, _) => format!(
            "The interpreter of {} cannot be known before the job runs; environment.json \
             records python3.",
            interpreter.program
        ),
    };
    check("interpreter", "Interpreter and its module", true, detail)
}

fn plan_paths_script(scratch: &str, results: &str) -> String {
    format!(
        "sh -c '# coperceive-check:plan-folders\n\
         set -u\n\
         S=$1\n\
         R=$2\n\
         if [ -n \"$S\" ] && [ -e \"$S\" ] && [ -w \"$S\" ]; then echo SCRATCH_OK=1;\n\
         elif [ -n \"$S\" ] && [ ! -e \"$S\" ]; then\n\
           P=$(dirname -- \"$S\")\n\
           if [ -d \"$P\" ] && [ -w \"$P\" ]; then echo SCRATCH_OK=1; else echo SCRATCH_OK=0; fi\n\
         else echo SCRATCH_OK=0; fi\n\
         if [ -d \"$R\" ] && [ -w \"$R\" ]; then echo RESULTS_OK=1; else echo RESULTS_OK=0; fi' \
         sh {} {}",
        shell_quote(scratch),
        shell_quote(results)
    )
}

fn read_file_script(path: &str) -> String {
    format!(
        "sh -c '# coperceive-check:plan-read\ncat -- \"$1\"' sh {}",
        shell_quote(path)
    )
}

fn write_plan_script(final_path: &str) -> String {
    format!(
        "sh -c '# coperceive-check:plan-write\n\
         set -e\n\
         umask 077\n\
         mkdir -p -- \"$(dirname -- \"$1\")\"\n\
         cat > \"$1.tmp\"\n\
         chmod 600 -- \"$1.tmp\"\n\
         mv -- \"$1.tmp\" \"$1\"' sh {}",
        shell_quote(final_path)
    )
}

fn parse_key_values(output: &str) -> BTreeMap<String, String> {
    let mut values = BTreeMap::new();
    for line in output.lines() {
        if let Some((key, value)) = line.split_once('=') {
            values.insert(key.trim().to_string(), value.trim().to_string());
        }
    }
    values
}

fn artifact_from_check(path: &str, values: &BTreeMap<String, String>) -> PlanArtifact {
    let exists = values.get("EXISTS").map(String::as_str) == Some("1");
    let is_dir = values.get("ISDIR").map(String::as_str) == Some("1");
    PlanArtifact {
        path: path.to_string(),
        sha256: values
            .get("SHA256")
            .map(|value| value.trim().trim_start_matches('\\').to_string())
            .filter(|value| !value.is_empty()),
        kind: if !exists {
            "missing".to_string()
        } else if is_dir {
            "folder".to_string()
        } else {
            "file".to_string()
        },
        real_path: values
            .get("REALPATH")
            .map(|value| value.trim().to_string())
            .filter(|value| !value.is_empty()),
        listing_sha256: values
            .get("LISTING_SHA256")
            .map(|value| value.trim().to_string())
            .filter(|value| is_dir && value.len() == 64),
        file_count: values
            .get("FILES")
            .filter(|_| is_dir)
            .and_then(|value| value.trim().parse::<u64>().ok()),
    }
}

/// Runs one bounded remote read and appends a plain-language check.
fn run_check(
    runner: &dyn RemoteRunner,
    checks: &mut Vec<PlanCheck>,
    name: &str,
    label: &str,
    remote_cmd: &str,
    timeout: Duration,
) -> Result<Option<BTreeMap<String, String>>, String> {
    let started = std::time::Instant::now();
    match runner.run(remote_cmd, timeout, None) {
        Ok(result) if result.success => {
            checks.push(PlanCheck {
                name: name.to_string(),
                label: label.to_string(),
                ok: true,
                detail: String::new(),
                hint: None,
                duration_ms: started.elapsed().as_millis() as u64,
            });
            Ok(Some(parse_key_values(&result.stdout)))
        }
        Ok(result) => {
            let detail = if result.stderr.trim().is_empty() {
                result.stdout.trim().to_string()
            } else {
                result.stderr.trim().to_string()
            };
            checks.push(PlanCheck {
                name: name.to_string(),
                label: label.to_string(),
                ok: false,
                detail: format!("The cluster returned: {detail}"),
                hint: Some(
                    "Check that the path exists and that your account may read it, then try again."
                        .to_string(),
                ),
                duration_ms: started.elapsed().as_millis() as u64,
            });
            Ok(None)
        }
        Err(error) => {
            checks.push(PlanCheck {
                name: name.to_string(),
                label: label.to_string(),
                ok: false,
                detail: error,
                hint: Some(
                    "The cluster did not answer in time. Check the VPN and your connection, then \
                     try again."
                        .to_string(),
                ),
                duration_ms: started.elapsed().as_millis() as u64,
            });
            Ok(None)
        }
    }
}

/// Expands one profile root against the remote `$HOME` the same safe way as a
/// picked path: `~` becomes HOME and `~/x` becomes HOME/x.
fn expand_root_with_home(root: &str, remote_home: Option<&str>) -> String {
    match remote_home {
        Some(home) if root == "~" => home.to_string(),
        Some(home) => root
            .strip_prefix("~/")
            .map(|rest| format!("{home}/{rest}"))
            .unwrap_or_else(|| root.to_string()),
        None => root.to_string(),
    }
}

/// Reads the remote login node's HOME once for path/root expansion. Bounded and
/// read-only; a server without HOME simply gets no expansion.
fn remote_home(runner: &dyn RemoteRunner) -> Option<String> {
    let result = runner.run(PLAN_ENV_SCRIPT, ENVIRONMENT_TIMEOUT, None).ok()?;
    if !result.success {
        return None;
    }
    let values = parse_key_values(&result.stdout);
    values
        .get("HOME")
        .map(|home| home.trim().to_string())
        .filter(|home| home.starts_with('/'))
}

fn real_path_inside_roots(
    profile: &ServerProfile,
    real_path: &str,
    remote_home: Option<&str>,
) -> bool {
    let roots: Vec<String> = profile
        .data_roots
        .iter()
        .chain(profile.code_roots.iter())
        .map(|root| expand_root_with_home(root, remote_home))
        .filter_map(|root| normalise_path(&root, "profile root").ok())
        .collect();
    if real_path.starts_with('/') {
        normalise_path(real_path, "real path")
            .map(|normalised| roots.iter().any(|root| is_under(&normalised, root)))
            .unwrap_or(false)
    } else {
        // A `~`-based real path only remains when HOME was unavailable; compare
        // its tail against the roots' tails.
        let tail = real_path.trim_start_matches('~');
        roots.iter().any(|root| {
            let root = root.trim_start_matches('~');
            !root.is_empty() && (tail == root || tail.starts_with(&format!("{root}/")))
        })
    }
}

/// Checks that the environment's interpreter/image is inside the profile's
/// allowed roots (or clearly explains why it is refused).
pub fn validate_environment_roots(
    profile: &ServerProfile,
    environment: &EnvironmentSetup,
) -> Result<(), String> {
    environment.validate()?;
    match environment.kind {
        EnvironmentKind::Conda | EnvironmentKind::Venv => {
            if let Some(interpreter) = environment
                .interpreter
                .as_deref()
                .filter(|value| !value.trim().is_empty())
            {
                validate_path_in_roots(profile, interpreter, "python interpreter").map_err(
                    |error| {
                        format!(
                            "{error} Add the interpreter's folder to the profile's code                              folders, or use an interpreter inside them."
                        )
                    },
                )?;
            }
        }
        EnvironmentKind::Container => {
            if let Some(image) = environment
                .container_image
                .as_deref()
                .filter(|value| !value.trim().is_empty())
            {
                validate_path_in_roots(profile, image, "container image").map_err(|error| {
                    format!(
                        "{error} Add the image's folder to the profile's code folders, or use an                          image inside them."
                    )
                })?;
            }
        }
        EnvironmentKind::Modules | EnvironmentKind::None => {}
    }
    Ok(())
}

/// One GPU of the partition's detected type for a model evaluation. Light jobs
/// (contract check, viewer, scan) never use this. A CPU-only partition gets no
/// GRES; the UI says so.
fn default_eval_gres(entry: &ServerEntry) -> Option<String> {
    let partition_name = entry.profile.defaults.partition.as_deref()?.trim();
    if partition_name.is_empty() {
        return None;
    }
    let partition = entry
        .discovered
        .as_ref()?
        .partitions
        .iter()
        .find(|partition| partition.name == partition_name)?;
    let gpu = partition.gpu_types.first()?;
    Some(if gpu == "gpu" {
        "gpu:1".to_string()
    } else {
        format!("gpu:{gpu}:1")
    })
}

/// Which of the two dedicated pickers a pipeline needs. Builtin model plugins
/// always need a checkpoint and a split; a custom pipeline only when its own
/// registration declares the matching argument role.
fn required_plan_artifacts(source: &str, pipeline: &PipelineSpec) -> (bool, bool) {
    let builtin = source == "builtin";
    let checkpoint = builtin
        || pipeline
            .arguments
            .iter()
            .any(|argument| argument.role == ArgumentRole::Checkpoint);
    let split = builtin
        || pipeline
            .arguments
            .iter()
            .any(|argument| argument.role == ArgumentRole::TestData);
    (checkpoint, split)
}

/// The installed plugin and model a built-in plan runs, read from the cluster.
#[derive(Debug, Clone)]
struct BuiltinChoice {
    plugin: String,
    key: String,
    manifest: String,
    modules: Vec<String>,
    model: BuiltinModel,
    /// Every model of the plugin, for the run's `depends_on` closure.
    models: BTreeMap<String, BuiltinModel>,
}

impl BuiltinChoice {
    fn takes_checkpoint(&self) -> bool {
        self.model.options.iter().any(|option| option == "checkpoint")
    }

    /// Whether the model declares the required `ckpt_root` parameter (Griffin's released
    /// checkpoints root). The app materialises it as a plan field, like the raw root.
    fn declares_ckpt_root(&self) -> bool {
        self.model.options.iter().any(|option| option == "ckpt_root")
    }

    /// Model options exactly as the manifest declares them: a checkpoint only when the
    /// model has a `checkpoint` option, and every extra `K=V` a declared key. The CLI's
    /// `check_options` would refuse anything else on the node, after the job queued.
    fn check_options(
        &self,
        checkpoint: &str,
        ckpt_root: &str,
        values: &BTreeMap<String, serde_json::Value>,
    ) -> Result<(), String> {
        let declared = if self.model.options.is_empty() {
            "none".to_string()
        } else {
            self.model.options.join(", ")
        };
        // ckpt_root has no default in the manifest: it must be given for a model that
        // declares it, and is not invented from any site path when it is missing.
        if self.declares_ckpt_root() && ckpt_root.trim().is_empty() {
            return Err(format!(
                "The model {} {} needs the ckpt_root parameter (the released checkpoints root): choose it on the cluster before checking the plan. It is the folder that holds griffin_50scenes_25m.",
                self.plugin, self.key
            ));
        }
        if !self.declares_ckpt_root() && !ckpt_root.trim().is_empty() {
            return Err(format!(
                "The model {} {} has no ckpt_root option in its manifest. Clear the checkpoint root field and check the plan again. Options it declares: {declared}.",
                self.plugin, self.key
            ));
        }
        if !self.takes_checkpoint() && !checkpoint.trim().is_empty() {
            return Err(format!(
                "The model {} {} has no checkpoint option in its manifest (it loads its own \
                 weights), so it cannot take the checkpoint {}. Clear the checkpoint field and \
                 check the plan again. Options it declares: {declared}.",
                self.plugin,
                self.key,
                checkpoint.trim()
            ));
        }
        if let Some(serde_json::Value::Array(extras)) = values.get("extras") {
            for extra in extras {
                let text = value_argv(extra);
                let key = text.split_once('=').map(|(key, _)| key.trim()).unwrap_or("");
                if key.is_empty() {
                    return Err(format!("The model option {text:?} must be KEY=VALUE."));
                }
                if key == "checkpoint" || !self.model.options.iter().any(|option| option == key) {
                    return Err(format!(
                        "The model {} {} has no option {key:?} in its manifest. Options it \
                         declares: {declared}.",
                        self.plugin, self.key
                    ));
                }
            }
        }
        Ok(())
    }

    /// The GPU request, exactly as `plan_sbatch` makes it (coperceive/models/slurm.py): a
    /// model whose manifest says `gpu: false` asks for none, one that says `gpu: true`
    /// asks for `gpu:1`. The type is never taken from discovery: the manifest's
    /// `--constraint` picks it, and on a partition with several types (`a100` and `a40`)
    /// a typed GRES could contradict that constraint and fit no node. A partition
    /// discovery found without any GPU is refused, so a GPU model never runs on a CPU
    /// node unnoticed.
    fn gres(&self, entry: &ServerEntry) -> Result<Option<String>, String> {
        if !self.model.gpu {
            return Ok(None);
        }
        let partition = entry.profile.defaults.partition.as_deref().unwrap_or("").trim();
        let discovered = entry
            .discovered
            .as_ref()
            .and_then(|server| server.partitions.iter().find(|item| item.name == partition));
        match discovered {
            Some(found)
                if !found.gpu_types.is_empty()
                    || found.gres.as_deref().is_some_and(|gres| gres.contains("gpu")) =>
            {
                Ok(Some("gpu:1".to_string()))
            }
            Some(_) => Err(format!(
                "The model {} {} needs a GPU (gpu: true in its manifest), but the server's \
                 discovery found no GPU on partition {partition:?}. Choose a GPU partition in \
                 the server profile, or check the server again if it has GPUs now.",
                self.plugin, self.key
            )),
            None => Ok(Some("gpu:1".to_string())),
        }
    }

    /// The manifest's modules (Griffin's CUDA). `plan_sbatch` writes `module purge` and
    /// loads them whatever the interpreter is: on a modules profile after the profile's
    /// own modules (its interpreter's), on a conda, venv or plain profile as the only
    /// modules (`environment_lines` writes them for a built-in model). A container
    /// brings its own libraries and loads none.
    fn environment(&self, mut environment: EnvironmentSetup) -> EnvironmentSetup {
        match environment.kind {
            EnvironmentKind::Modules => {
                for module in &self.modules {
                    if !environment.modules.contains(module) {
                        environment.modules.push(module.clone());
                    }
                }
            }
            EnvironmentKind::Conda | EnvironmentKind::Venv | EnvironmentKind::None => {
                environment.modules = self.modules.clone();
            }
            EnvironmentKind::Container => {}
        }
        environment
    }

    /// What the manifest asks of the job: the model's GPU, CPUs, memory and constraint,
    /// and the transient artefacts of the model and of every model it depends on (each
    /// once, dependencies first), as `plan_sbatch` and `work_copy_filter` use them. A
    /// dependency loop is refused: `model_closure` would visit each model once, but no
    /// order runs a loop's models after their dependencies.
    fn resources(&self) -> Result<ModelResources, String> {
        let mut order: Vec<String> = Vec::new();
        self.visit(&self.key, &mut Vec::new(), &mut order)?;
        let transient_artifacts = order
            .iter()
            .flat_map(|key| {
                let model = if key == &self.key { Some(&self.model) } else { self.models.get(key) };
                model
                    .map(|model| model.transient_artifacts.clone())
                    .unwrap_or_default()
                    .into_iter()
                    .map(move |name| format!("{key}/{name}"))
            })
            .collect();
        Ok(ModelResources {
            gpu: self.model.gpu,
            cpus: self.model.cpus,
            memory_gb: self.model.memory_gb,
            transient_artifacts,
            constraint: self
                .model
                .slurm_constraint
                .clone()
                .filter(|constraint| !constraint.trim().is_empty()),
        })
    }

    /// Depth-first over `depends_on`: `visiting` holds the models being visited, so a
    /// model met again before it is done closes a loop.
    fn visit(
        &self,
        key: &str,
        visiting: &mut Vec<String>,
        order: &mut Vec<String>,
    ) -> Result<(), String> {
        if order.iter().any(|done| done == key) {
            return Ok(());
        }
        if let Some(at) = visiting.iter().position(|open| open == key) {
            let mut cycle = visiting[at..].to_vec();
            cycle.push(key.to_string());
            return Err(format!(
                "The models of the plugin {} depend on each other in a loop ({}), so no model \
                 can run first. Fix depends_on in the plugin's manifest, then reload the \
                 pipeline list.",
                self.plugin,
                cycle.join(" -> ")
            ));
        }
        visiting.push(key.to_string());
        let depends_on = if key == self.key {
            self.model.depends_on.clone()
        } else {
            self.models.get(key).map(|model| model.depends_on.clone()).unwrap_or_default()
        };
        for dependency in &depends_on {
            self.visit(dependency, visiting, order)?;
        }
        visiting.pop();
        order.push(key.to_string());
        Ok(())
    }
}

/// The login node's `module` command, for a built-in model's manifest modules on a
/// profile that is not a modules profile.
fn manifest_modules_check(module_available: bool, modules: &[String]) -> PlanCheck {
    with_hint(
        check(
            "env-manifest-modules",
            "Model modules",
            module_available,
            if module_available {
                format!("The model's manifest loads: {}.", modules.join(", "))
            } else {
                format!(
                    "The model's manifest needs the modules {}, but no `module` command was \
                     found on the login node.",
                    modules.join(", ")
                )
            },
        ),
        "The job loads the manifest's modules (module purge, then module load), as \
         coperceive model run --slurm does.",
    )
}

/// Finds `plugin`'s `model` in the cluster's `coperceive model list --json`.
fn resolve_builtin_model(
    runner: &dyn RemoteRunner,
    profile: &ServerProfile,
    plugin: &str,
    model: &str,
) -> Result<BuiltinChoice, String> {
    let plugins = read_builtin_plugins(runner, profile)?;
    let found = plugins
        .into_iter()
        .find(|item| item.name == plugin)
        .ok_or_else(|| {
            format!(
                "The installed pipeline {plugin:?} is no longer on the cluster. Reload the \
                 pipeline list and rebuild the plan."
            )
        })?;
    let manifest = found
        .manifest
        .clone()
        .filter(|path| path.starts_with('/'))
        .ok_or_else(|| {
            format!(
                "This built-in model has no pipeline manifest. Reinstall the model or choose a \
                 custom pipeline.{}",
                found
                    .error
                    .as_deref()
                    .map(|error| format!(" The cluster said: {error}"))
                    .unwrap_or_default()
            )
        })?;
    let spec = found.models.get(model).cloned().ok_or_else(|| {
        format!(
            "The plugin {plugin} has no model {model:?}; its manifest lists: {}.",
            found.models.keys().cloned().collect::<Vec<_>>().join(", ")
        )
    })?;
    Ok(BuiltinChoice {
        plugin: plugin.to_string(),
        key: model.to_string(),
        manifest,
        modules: found.modules,
        model: spec,
        models: found.models,
    })
}

/// Builds and checks a run plan. Remote work is read-only and bounded; nothing is
/// submitted.
pub fn build_run_plan(
    runner: &dyn RemoteRunner,
    entry: &ServerEntry,
    request: &RunPlanRequest,
) -> Result<RunPlan, String> {
    let profile = &entry.profile;
    profile.validate()?;
    let mut pipeline = request.pipeline.clone();
    if !pipeline.script.starts_with('/') && !pipeline.script.starts_with('~') {
        let root = profile
            .code_roots
            .first()
            .or_else(|| profile.data_roots.first())
            .ok_or_else(|| {
                "Add a code folder or a data folder to the server profile before registering a                  pipeline script."
                    .to_string()
            })?;
        pipeline.script = format!(
            "{}/{}",
            root.trim_end_matches('/'),
            pipeline.script.trim_start_matches('/')
        );
    }
    pipeline.validate()?;
    profile.validate_pipeline_script(&pipeline.script)?;
    validate_path_arguments(profile, &pipeline, &request.values)?;
    let builtin = request.pipeline_source == "builtin";
    let builtin_choice = if builtin {
        // The cluster's `model list --json` is the authority on the manifest (the
        // client's plugin_manifest is never used), so it is read first: a cluster too old
        // to report one, or a plugin whose manifest cannot be read, is named with the
        // cluster's own reason instead of a generic "no manifest".
        if request.model.trim().is_empty() {
            return Err("Choose the model from the plugin manifest before checking the plan.".to_string());
        }
        let choice = resolve_builtin_model(
            runner,
            profile,
            request.pipeline.name.trim(),
            request.model.trim(),
        )?;
        choice.check_options(&request.checkpoint, &request.ckpt_root, &request.values)?;
        // A dependency loop in the manifest is refused before any other check.
        choice.resources()?;
        Some(choice)
    } else {
        None
    };
    let (needs_checkpoint, needs_split) = match &builtin_choice {
        Some(choice) => (choice.takes_checkpoint(), true),
        None => required_plan_artifacts(&request.pipeline_source, &pipeline),
    };
    if needs_checkpoint {
        validate_path_in_roots(profile, &request.checkpoint, "checkpoint")?;
    }
    if needs_split {
        validate_path_in_roots(profile, &request.split, "split")?;
    }
    let gres = match &builtin_choice {
        Some(choice) => choice.gres(entry)?,
        None => default_eval_gres(entry),
    };
    if builtin {
        let eval_protocol = request.eval_protocol.trim();
        if !EVAL_PROTOCOLS.contains(&eval_protocol) {
            return Err(format!(
                "Choose the AP protocol for this run: one of {}.",
                EVAL_PROTOCOLS.join(", ")
            ));
        }
        let raw = request.raw_root.trim();
        if raw.is_empty() {
            return Err("Choose the raw root on the cluster before checking the plan.".to_string());
        }
        validate_path_in_roots(profile, raw, "raw root")?;
        // The media root is optional, as the CLI's repeatable --media-root is: an empty
        // one leaves the flag out of the job script instead of passing an empty path.
        let media = request.media_root.trim();
        if !media.is_empty() {
            validate_path_in_roots(profile, media, "media root")?;
        }
        if !request.ckpt_root.trim().is_empty() {
            validate_path_in_roots(profile, request.ckpt_root.trim(), "ckpt root")?;
        }
    }
    validate_plan_destinations(profile)?;

    let plan_id = new_plan_id();
    let base = profile.results_root.trim_end_matches('/').to_string();
    let output_dir = format!("{base}/runs/{plan_id}");
    let (environment, overridden, env_note) =
        resolve_environment(profile, &pipeline, request.environment_override.as_ref());
    let environment = match &builtin_choice {
        Some(choice) => choice.environment(environment),
        None => environment,
    };
    validate_environment_roots(profile, &environment)?;
    environment.validate()?;

    let mut checks: Vec<PlanCheck> = Vec::new();

    // 1. Checkpoint and split: existence, readability, sha256 and symlink escape.
    let checkpoint_values = if needs_checkpoint {
        run_check(
            runner,
            &mut checks,
            "checkpoint",
            "Checkpoint",
            &path_check_script(&request.checkpoint),
            CHECK_TIMEOUT,
        )?
    } else {
        None
    };
    let split_values = if needs_split {
        run_check(
            runner,
            &mut checks,
            "split",
            "Test split",
            &split_check_script(&request.split),
            LISTING_TIMEOUT,
        )?
    } else {
        None
    };
    for values in [checkpoint_values.as_ref(), split_values.as_ref()]
        .into_iter()
        .flatten()
    {
        if let Some(error) = path_check_error(values) {
            return Err(error);
        }
    }
    let checkpoint = if needs_checkpoint {
        artifact_from_check(
            &request.checkpoint,
            checkpoint_values.as_ref().unwrap_or(&BTreeMap::new()),
        )
    } else {
        PlanArtifact::default()
    };
    let split = if needs_split {
        artifact_from_check(
            &request.split,
            split_values.as_ref().unwrap_or(&BTreeMap::new()),
        )
    } else {
        PlanArtifact::default()
    };
    // Fail closed when the cluster cannot resolve the real path: a symlink escape
    // must never be waved through because realpath/readlink was missing.
    for (artifact, what) in [(&checkpoint, "checkpoint"), (&split, "test split")] {
        if !artifact.path.is_empty() && artifact.kind != "missing" && artifact.real_path.is_none() {
            return Err(format!(
                "The cluster did not report where the {what} resolves to, so coperceive cannot \
                 check that it stays inside your folders. Choose a path inside the profile's \
                 folders, or ask your administrators for a shell with realpath/readlink -f."
            ));
        }
    }

    let remote_home = remote_home(runner);
    if needs_checkpoint {
    if checkpoint.kind == "missing" {
        checks.push(check(
            "checkpoint-exists",
            "Checkpoint exists",
            false,
            format!("No file at {}.", request.checkpoint),
        ));
        if let Some(last) = checks.last_mut() {
            last.hint =
                Some("Type the checkpoint path exactly as it appears on the cluster.".to_string());
        }
    } else if checkpoint.sha256.is_some() {
        checks.push(check(
            "checkpoint-sha",
            "Checkpoint sha256",
            true,
            format!("sha256 {}", checkpoint.sha256.clone().unwrap_or_default()),
        ));
    } else if checkpoint.kind == "folder" {
        checks.push(with_hint(
            check(
                "checkpoint-sha",
                "Checkpoint sha256",
                false,
                "The checkpoint path is a folder; choose the checkpoint file itself.",
            ),
            "A checkpoint must be a file so its sha256 can be recorded.",
        ));
    } else {
        checks.push(with_hint(
            check(
                "checkpoint-sha",
                "Checkpoint sha256",
                false,
                "Could not read the checkpoint file's checksum.",
            ),
            "Check that the file is readable by your account.",
        ));
    }

    if let Some(real_path) = &checkpoint.real_path {
        let ok = real_path_inside_roots(profile, real_path, remote_home.as_deref());
        checks.push(with_hint(
            check(
                "checkpoint-roots",
                "Checkpoint stays inside your folders",
                ok,
                if ok {
                    format!("Resolved to {real_path}.")
                } else {
                    format!("Resolved to {real_path}, outside the profile's data and code folders.")
                },
            ),
            "Symlinks that point outside the server profile are refused.",
        ));
        if !ok {
            return Err(format!(
                "The checkpoint resolves to {real_path}, which is outside this server's data and \
                 code folders. Pick a checkpoint inside them."
            ));
        }
    }

    }

    if needs_split {
    if split.kind == "missing" {
        checks.push(check(
            "split-exists",
            "Test split exists",
            false,
            format!("No file or folder at {}.", request.split),
        ));
    } else if split.sha256.is_some() {
        checks.push(check(
            "split-sha",
            "Test split sha256",
            true,
            format!("sha256 {}", split.sha256.clone().unwrap_or_default()),
        ));
    } else if split.kind == "folder" {
        let ok = split.is_fingerprinted();
        checks.push(with_hint(
            check(
                "split-sha",
                "Test split fingerprint",
                ok,
                match (&split.listing_sha256, split.file_count) {
                    (Some(listing), Some(files)) if ok => format!(
                        "A folder of {files} file(s); its listing (path and size of every file) \
                         has sha256 {listing}."
                    ),
                    _ => "The split folder lists no files, so it cannot be fingerprinted."
                        .to_string(),
                },
            ),
            "A folder is recorded by the sha256 of its file listing; the save and the submit \
             check it again.",
        ));
    } else {
        checks.push(with_hint(
            check(
                "split-sha",
                "Test split sha256",
                false,
                "Could not read the split file's checksum.",
            ),
            "A split file gives a verifiable sha256.",
        ));
    }
    if let Some(real_path) = &split.real_path {
        let ok = real_path_inside_roots(profile, real_path, remote_home.as_deref());
        checks.push(with_hint(
            check(
                "split-roots",
                "Test split stays inside your folders",
                ok,
                if ok {
                    format!("Resolved to {real_path}.")
                } else {
                    format!("Resolved to {real_path}, outside the profile's data and code folders.")
                },
            ),
            "Symlinks that point outside the server profile are refused.",
        ));
        if !ok {
            return Err(format!(
                "The test split resolves to {real_path}, which is outside this server's data and \
                 code folders. Pick a split inside them."
            ));
        }
    }

    }

    // 2. The interpreter that runs a built-in model and its python module (the job
    //    loads it after module purge), then the environment pieces on the login node.
    let (environment, run_interpreter, time_estimate) = match &builtin_choice {
        Some(choice) if environment.kind != EnvironmentKind::Container => {
            let mut found = read_run_interpreter(runner, profile, &environment)?;
            checks.push(interpreter_plan_check(&found));
            let (estimate, facts) = read_python_facts(runner, choice, &mut found, &request.split)?;
            checks.push(facts);
            (with_interpreter_module(environment, &found), Some(found), estimate)
        }
        _ => (environment, None, None),
    };
    let env_values = run_check(
        runner,
        &mut checks,
        "environment",
        "Environment pieces",
        PLAN_ENV_SCRIPT,
        ENVIRONMENT_TIMEOUT,
    )?
    .unwrap_or_default();
    let module_available = !env_values
        .get("MODULE")
        .unwrap_or(&String::new())
        .is_empty()
        || !env_values.get("LMOD").unwrap_or(&String::new()).is_empty();
    let container_available = !env_values
        .get("APPTAINER")
        .unwrap_or(&String::new())
        .is_empty()
        || !env_values
            .get("SINGULARITY")
            .unwrap_or(&String::new())
            .is_empty();
    if builtin
        && matches!(
            environment.kind,
            EnvironmentKind::Conda | EnvironmentKind::Venv | EnvironmentKind::None
        )
        && !environment.modules.is_empty()
    {
        checks.push(manifest_modules_check(module_available, &environment.modules));
    }
    match environment.kind {
        EnvironmentKind::Modules => {
            let has_modules = !environment.modules.is_empty();
            checks.push(with_hint(
                check(
                    "env-modules",
                    "Module system",
                    module_available && has_modules,
                    if module_available {
                        format!("Modules: {}.", environment.modules.join(", "))
                    } else {
                        "No `module` command was found on the login node.".to_string()
                    },
                ),
                "The run needs its modules on compute nodes too. Change the environment if the \
                 cluster uses a different setup.",
            ));
        }
        EnvironmentKind::Conda | EnvironmentKind::Venv => {
            let interpreter = environment.interpreter.clone().unwrap_or_default();
            let exec_ok = if interpreter.is_empty() {
                false
            } else {
                let values = run_check(
                    runner,
                    &mut checks,
                    "env-python",
                    "Python interpreter",
                    &path_check_script(&interpreter),
                    CHECK_TIMEOUT,
                )?
                .unwrap_or_default();
                if let Some(error) = path_check_error(&values) {
                    return Err(error);
                }
                values.get("EXEC").map(String::as_str) == Some("1")
            };
            checks.push(with_hint(
                check(
                    "env-python-exec",
                    "Python interpreter is executable",
                    exec_ok,
                    if exec_ok {
                        format!("{interpreter} is executable.")
                    } else {
                        format!("{interpreter} is not executable on the login node.")
                    },
                ),
                "Choose the environment's python path in the server profile or override it for \
                 this plan.",
            ));
        }
        EnvironmentKind::Container => {
            let image = environment.container_image.clone().unwrap_or_default();
            let image_values = if image.is_empty() {
                BTreeMap::new()
            } else {
                run_check(
                    runner,
                    &mut checks,
                    "env-image",
                    "Container image",
                    &path_check_script(&image),
                    CHECK_TIMEOUT,
                )?
                .unwrap_or_default()
            };
            if let Some(error) = path_check_error(&image_values) {
                return Err(error);
            }
            checks.push(with_hint(
                check(
                    "env-container",
                    "Container runtime and image",
                    container_available
                        && image_values.get("EXISTS").map(String::as_str) == Some("1"),
                    format!(
                        "{}{}",
                        if container_available {
                            "Apptainer/Singularity found. "
                        } else {
                            "No Apptainer/Singularity found on the login node. "
                        },
                        if image_values.get("EXISTS").map(String::as_str) == Some("1") {
                            format!("Image {image} exists.")
                        } else {
                            format!("Image {image} was not found.")
                        }
                    ),
                ),
                "Choose an image inside the profile's folders, and make sure the cluster provides \
                 Apptainer or Singularity.",
            ));
        }
        EnvironmentKind::None => {
            checks.push(check(
                "env-none",
                "Environment",
                true,
                "No module/conda/container setup is needed.".to_string(),
            ));
        }
    }

    // 3. coperceive on the cluster.
    let started = std::time::Instant::now();
    let version_command = coperceive_version_command(profile);
    match runner.run(&version_command, READ_TIMEOUT, None) {
        Ok(result) if result.success => {
            let version = result
                .stdout
                .lines()
                .next()
                .unwrap_or("")
                .trim()
                .to_string();
            checks.push(PlanCheck {
                name: "coperceive".to_string(),
                label: "Coperceive on the cluster".to_string(),
                ok: !version.is_empty(),
                detail: if version.is_empty() {
                    "coperceive did not report a version.".to_string()
                } else {
                    format!("{version}.")
                },
                hint: if version.is_empty() {
                    Some("Ask your administrators to check the coperceive install.".to_string())
                } else {
                    None
                },
                duration_ms: started.elapsed().as_millis() as u64,
            });
        }
        _ => checks.push(with_hint(
            PlanCheck {
                name: "coperceive".to_string(),
                label: "Coperceive on the cluster".to_string(),
                ok: false,
                detail: "coperceive was not found on the login node.".to_string(),
                hint: Some(
                    "Coperceive must be installed on the cluster for the run. Ask your \
                     administrators for the install path."
                        .to_string(),
                ),
                duration_ms: started.elapsed().as_millis() as u64,
            },
            "This step never installs anything.",
        )),
    }

    // 4. Scratch template and results root sanity.
    let started = std::time::Instant::now();
    match runner.run(
        &plan_paths_script(&profile.scratch_template, &profile.results_root),
        CHECK_TIMEOUT,
        None,
    ) {
        Ok(result) if result.success => {
            let values = parse_key_values(&result.stdout);
            checks.push(PlanCheck {
                name: "scratch".to_string(),
                label: "Scratch folder".to_string(),
                ok: values.get("SCRATCH_OK").map(String::as_str) == Some("1"),
                detail: format!(
                    "Template {} (expanded inside the job).",
                    profile.scratch_template
                ),
                hint: if values.get("SCRATCH_OK").map(String::as_str) == Some("1") {
                    None
                } else {
                    Some(
                        "The scratch template is not writable for your account. Fix it in the \
                         server profile."
                            .to_string(),
                    )
                },
                duration_ms: started.elapsed().as_millis() as u64,
            });
            checks.push(PlanCheck {
                name: "results".to_string(),
                label: "Shared results folder".to_string(),
                ok: values.get("RESULTS_OK").map(String::as_str) == Some("1"),
                detail: format!("{}.", profile.results_root),
                hint: if values.get("RESULTS_OK").map(String::as_str) == Some("1") {
                    None
                } else {
                    Some(
                        "The results folder must already exist and be writable for your account."
                            .to_string(),
                    )
                },
                duration_ms: started.elapsed().as_millis() as u64,
            });
        }
        Ok(result) => checks.push(with_hint(
            PlanCheck {
                name: "scratch".to_string(),
                label: "Scratch and results folders".to_string(),
                ok: false,
                detail: format!(
                    "The cluster could not check the folders. {}",
                    result.stderr.trim()
                ),
                hint: Some("Check that the login node can read the profile's folders.".to_string()),
                duration_ms: started.elapsed().as_millis() as u64,
            },
            "Nothing was created on the cluster.",
        )),
        Err(error) => checks.push(with_hint(
            PlanCheck {
                name: "scratch".to_string(),
                label: "Scratch and results folders".to_string(),
                ok: false,
                detail: error,
                hint: None,
                duration_ms: started.elapsed().as_millis() as u64,
            },
            "The cluster did not answer in time.",
        )),
    }

    // 5. Typed argv, then the contract-check script rendered from it.
    let argv = match request.pipeline_source.as_str() {
        "builtin" => {
            // The manifest the cluster resolved, never the path the client sent.
            let choice = builtin_choice
                .as_ref()
                .ok_or_else(|| "This built-in model has no pipeline manifest.".to_string())?;
            build_builtin_argv(
                profile,
                &choice.manifest,
                &plan_id,
                &request.values,
                &request.split,
                if needs_checkpoint { &request.checkpoint } else { "" },
                &output_dir,
                request.model.trim(),
                request.raw_root.trim(),
                request.media_root.trim(),
                request.ckpt_root.trim(),
                request.eval_protocol.trim(),
                WORK_IN_SCRATCH,
            )?
        }
        _ => build_pipeline_argv(&pipeline, &request.values, &output_dir)?,
    };
    checks.push(check(
        "argv",
        "Typed argv",
        true,
        format!(
            "{} separate argument(s); no shell string is used.",
            argv.len()
        ),
    ));
    let contract_check_script =
        render_contract_check_script(profile, &environment, &argv, &plan_id, builtin)?;
    if !environment.compute_has_internet {
        for forbidden in [
            "pip install",
            "conda install",
            "conda create",
            "git clone",
            "curl ",
            "wget ",
        ] {
            if contract_check_script.contains(forbidden) {
                return Err(format!(
                    "The contract-check script contains {forbidden:?}, but this server's compute \
                     nodes have no internet. Remove installs/downloads from the pipeline."
                ));
            }
        }
    }

    let pipeline_ref = PlanPipelineRef {
        id: request.pipeline.name.trim().to_string(),
        name: pipeline.name.clone(),
        version: String::new(),
        source: if request.pipeline_source.is_empty() {
            "custom".to_string()
        } else {
            request.pipeline_source.clone()
        },
    };

    Ok(RunPlan {
        schema: RUN_PLAN_SCHEMA.to_string(),
        id: plan_id,
        created_unix: now_unix(),
        profile_id: entry.id.clone(),
        profile_name: profile.name.clone(),
        pipeline: pipeline_ref,
        argv,
        environment,
        environment_from_override: overridden,
        environment_note: env_note,
        checkpoint,
        split,
        model: if builtin { request.model.trim().to_string() } else { String::new() },
        raw_root: if builtin { artifact_from_check_for_input(&request.raw_root, "folder") } else { PlanArtifact::default() },
        media_root: if builtin { artifact_from_check_for_input(&request.media_root, "folder") } else { PlanArtifact::default() },
        ckpt_root: if builtin { artifact_from_check_for_input(&request.ckpt_root, "folder") } else { PlanArtifact::default() },
        model_options: builtin_choice.as_ref().map(|choice| choice.model.options.clone()).unwrap_or_default(),
        eval_protocol: if builtin { request.eval_protocol.trim().to_string() } else { DEFAULT_EVAL_PROTOCOL.to_string() },
        work_dir: if builtin { WORK_IN_SCRATCH.to_string() } else { String::new() },
        staging_seconds: None,
        output_dir,
        scratch_dir: profile.scratch_template.clone(),
        gres,
        model_resources: builtin_choice.as_ref().map(BuiltinChoice::resources).transpose()?,
        run_interpreter,
        time_estimate,
        partition_max_time: if builtin { partition_max_time(entry) } else { None },
        logs_dir: if builtin { Some(builtin_logs_dir(remote_home.as_deref())?) } else { None },
        checks,
        contract_check_script,
        nothing_submitted: true,
        saved_path: None,
    })
}

/// `coperceive model list --json`: the plain listing's last word is the entry point
/// (`module:ATTR`), never the manifest path, so the app reads the JSON form. The
/// coperceive command goes in as `sh`'s positional parameters (`"$@"`), as the path and
/// interpreter checks pass theirs: a quoted word inside the single-quoted script body
/// would end it.
fn model_list_script(profile: &ServerProfile) -> String {
    format!(
        "sh -c '# coperceive-check:model-list\n\"$@\" model list --json' sh {}",
        quoted_command(&profile.environment.coperceive_command)
    )
}

fn model_list_plain_script(profile: &ServerProfile) -> String {
    format!(
        "sh -c '# coperceive-check:model-list\n\"$@\" model list 2>/dev/null' sh {}",
        quoted_command(&profile.environment.coperceive_command)
    )
}

/// What argparse prints when this coperceive has no `model list --json`.
const ARGPARSE_REFUSES_JSON: &str = "error: unrecognized arguments: --json";

pub const MODEL_LIST_FORMAT: &str = "coperceive-model-list";

const MODEL_LIST_TOO_OLD: &str = "The cluster's coperceive is too old to report where its model \
     manifests are: it has no `coperceive model list --json`. Update coperceive on the cluster \
     (the profile's coperceive command) to v0.1.1 or later, then reload the pipeline list.";

#[derive(Deserialize)]
struct ModelList {
    format: String,
    format_version: u32,
    #[serde(default)]
    plugins: Vec<BuiltinPlugin>,
}

/// The installed model plugins, from the cluster's `coperceive model list --json`
/// (read-only, bounded). An older coperceive without `--json` gets a plain error.
pub fn read_builtin_plugins(
    runner: &dyn RemoteRunner,
    profile: &ServerProfile,
) -> Result<Vec<BuiltinPlugin>, String> {
    let result = runner
        .run(&model_list_script(profile), READ_TIMEOUT, None)
        .map_err(|error| format!("Could not list the installed models on the cluster: {error}"))?;
    if !result.success {
        if result.stderr.contains(ARGPARSE_REFUSES_JSON) {
            return Err(MODEL_LIST_TOO_OLD.to_string());
        }
        return Err(format!(
            "Could not list the installed models on the cluster: {}",
            result.stderr.trim()
        ));
    }
    let listing: ModelList = serde_json::from_str(&result.stdout).map_err(|error| {
        format!(
            "The cluster's `coperceive model list --json` did not answer with the model list \
             ({error})."
        )
    })?;
    if listing.format != MODEL_LIST_FORMAT || listing.format_version != 1 {
        return Err(format!(
            "The cluster's model list has format {} {}, which this app does not read. Update \
             the app or the cluster's coperceive so they match.",
            listing.format, listing.format_version
        ));
    }
    Ok(listing.plugins)
}

fn is_plugin_name(name: &str) -> bool {
    name.chars()
        .next()
        .map(|ch| ch.is_ascii_lowercase())
        .unwrap_or(false)
        && name
            .chars()
            .all(|ch| ch.is_ascii_lowercase() || ch.is_ascii_digit() || ch == '-' || ch == '_')
}

fn builtin_entry(name: &str, description: String, plugin: Option<BuiltinPlugin>) -> PipelineEntry {
    let plugin_manifest = plugin
        .as_ref()
        .and_then(|plugin| plugin.manifest.clone())
        .filter(|path| path.starts_with('/'));
    PipelineEntry {
        id: name.to_string(),
        name: name.to_string(),
        description,
        source: "builtin".to_string(),
        spec: PipelineSpec {
            name: name.to_string(),
            script: plugin_manifest.clone().unwrap_or_default(),
            interpreter: vec!["coperceive".to_string()],
            environment: EnvironmentSetup::default(),
            arguments: vec![crate::hpc::ArgumentSpec {
                name: "extras".to_string(),
                kind: ArgumentKind::Str,
                help: "Extra --option KEY=VALUE pairs for the model run.".to_string(),
                required: false,
                default: None,
                choices: Vec::new(),
                multiple: true,
                role: crate::hpc::ArgumentRole::Other,
            }],
            output_contract: "coperceive-pipeline-1".to_string(),
            description: String::new(),
        },
        registration_path: None,
        plugin_manifest,
        builtin: plugin,
    }
}

/// Reads a custom pipeline registration (`coperceive-pipeline.json` beside the
/// script) over SSH, read-only and bounded.
pub fn load_pipeline_registration(
    runner: &dyn RemoteRunner,
    profile: &ServerProfile,
    script_path: &str,
) -> Result<PipelineEntry, String> {
    let mut script = script_path.trim().to_string();
    if script.is_empty() {
        return Err("Type the path of the pipeline script on the cluster.".to_string());
    }
    profile.validate_pipeline_script(&script)?;
    if !script.starts_with('/') && !script.starts_with('~') {
        let root = profile
            .code_roots
            .first()
            .or_else(|| profile.data_roots.first())
            .ok_or_else(|| "Add a code folder to the server profile first.".to_string())?;
        script = format!(
            "{}/{}",
            root.trim_end_matches('/'),
            script.trim_start_matches('/')
        );
    }
    profile.validate_pipeline_script(&script)?;
    let parent = std::path::Path::new(&script)
        .parent()
        .map(|path| path.to_string_lossy().to_string())
        .ok_or_else(|| "The pipeline script has no folder.".to_string())?;
    let registration_path = format!("{}/{}", parent.trim_end_matches('/'), PIPELINE_SPEC_FILE);
    let result = runner
        .run(&read_file_script(&registration_path), READ_TIMEOUT, None)
        .map_err(|error| format!("Could not read the pipeline registration: {error}"))?;
    if !result.success {
        return Err(format!(
            "No readable {PIPELINE_SPEC_FILE} next to the script at {script}. Add one so the \
             pipeline's typed arguments can be shared, or fill the fields in yourself."
        ));
    }
    let mut payload: serde_json::Value = serde_json::from_str(&result.stdout).map_err(|error| {
        format!("The {PIPELINE_SPEC_FILE} at {registration_path} is not valid JSON: {error}")
    })?;
    if payload
        .get("script")
        .and_then(serde_json::Value::as_str)
        .is_none()
    {
        if let Some(object) = payload.as_object_mut() {
            object.insert(
                "script".to_string(),
                serde_json::Value::String(script.clone()),
            );
        }
    } else if let Some(relative) = payload
        .get("script")
        .and_then(serde_json::Value::as_str)
        .filter(|script| !script.starts_with('/') && !script.starts_with('~'))
        .map(str::to_string)
    {
        if let Some(object) = payload.as_object_mut() {
            object.insert(
                "script".to_string(),
                serde_json::Value::String(format!(
                    "{}/{}",
                    parent.trim_end_matches('/'),
                    relative.trim_start_matches('/')
                )),
            );
        }
    }
    let spec: PipelineSpec = serde_json::from_value(payload)
        .map_err(|error| format!("The pipeline registration is not valid: {error}"))?;
    spec.validate()?;
    profile.validate_pipeline_script(&spec.script)?;
    Ok(PipelineEntry {
        id: spec.name.clone(),
        name: spec.name.clone(),
        description: spec.description.clone(),
        source: "custom".to_string(),
        spec,
        registration_path: Some(registration_path),
        plugin_manifest: None,
        builtin: None,
    })
}

/// Lists installed model plugins as built-in pipelines. A cluster without
/// plugins simply returns an empty list. A cluster whose coperceive is too old for
/// `model list --json` still lists its plugins by name, without a manifest and with
/// the reason as the description; planning one of them gives the same reason.
pub fn list_builtin_pipelines(
    runner: &dyn RemoteRunner,
    profile: &ServerProfile,
) -> Vec<PipelineEntry> {
    match read_builtin_plugins(runner, profile) {
        Ok(plugins) => plugins
            .into_iter()
            .filter(|plugin| is_plugin_name(&plugin.name))
            .map(|plugin| {
                let description = match &plugin.error {
                    Some(error) => format!("Installed model plugin; its manifest could not be read: {error}"),
                    None => "Installed model plugin; runs coperceive model run on the cluster."
                        .to_string(),
                };
                builtin_entry(&plugin.name.clone(), description, Some(plugin))
            })
            .collect(),
        Err(error) if error == MODEL_LIST_TOO_OLD => {
            let Ok(result) = runner.run(&model_list_plain_script(profile), READ_TIMEOUT, None) else {
                return Vec::new();
            };
            if !result.success {
                return Vec::new();
            }
            result
                .stdout
                .lines()
                .filter(|line| !line.trim().starts_with("no model plugins"))
                .filter_map(|line| line.split_whitespace().next())
                .filter(|name| is_plugin_name(name))
                .map(|name| builtin_entry(name, MODEL_LIST_TOO_OLD.to_string(), None))
                .collect()
        }
        Err(_) => Vec::new(),
    }
}

fn now_unix() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|duration| duration.as_secs())
        .unwrap_or(0)
}

/// The generated plan id format: `plan-<unix-seconds>-<hex suffix>`.
///
/// The id comes back from the UI at save time, so it is validated here instead of
/// trusted: no `..`, no path separators, no arbitrary characters.
pub(crate) fn validate_plan_id(id: &str) -> Result<(), String> {
    let rest = id.strip_prefix("plan-").ok_or_else(|| {
        "This plan id is not one coperceive generated; refusing to save it.".to_string()
    })?;
    let (unix, suffix) = rest.split_once('-').ok_or_else(|| {
        "This plan id is not one coperceive generated; refusing to save it.".to_string()
    })?;
    let valid = !unix.is_empty()
        && unix.len() <= 20
        && unix.chars().all(|ch| ch.is_ascii_digit())
        && !suffix.is_empty()
        && suffix.len() <= 32
        && suffix.chars().all(|ch| ch.is_ascii_hexdigit());
    if !valid {
        return Err(
            "This plan id is not one coperceive generated; refusing to save it.".to_string(),
        );
    }
    Ok(())
}

const CREDENTIAL_WORDS: [&str; 10] = [
    "password",
    "passwd",
    "secret",
    "token",
    "apikey",
    "privatekey",
    "credential",
    "credentials",
    "bearer",
    "authorization",
];

fn key_looks_like_credential(key: &str) -> bool {
    let normalized: String = key
        .chars()
        .filter(|ch| ch.is_ascii_alphanumeric())
        .map(|ch| ch.to_ascii_lowercase())
        .collect();
    let split_match = key
        .split(|ch: char| !ch.is_ascii_alphanumeric())
        .filter(|word| !word.is_empty())
        .any(|word| {
            let word = word.to_ascii_lowercase();
            CREDENTIAL_WORDS.contains(&word.as_str())
        });
    // `api_key` -> "apikey", `auth_token` -> "authtoken"; a path word like
    // "tokenizer" is not mistaken for "token" because it neither equals nor ends
    // with a credential word.
    split_match
        || CREDENTIAL_WORDS
            .iter()
            .any(|word| normalized == *word || normalized.ends_with(word))
}

/// A real API key shape, not a path or a short label beginning with `sk-`.
///
/// `sk-test` is a split name; a secret key has `sk-` plus 20 or more key
/// characters.
fn is_secret_key(lower: &str) -> bool {
    let Some(rest) = lower.strip_prefix("sk-") else {
        return false;
    };
    rest.len() >= 20 && rest.chars().all(|ch| ch.is_ascii_alphanumeric() || ch == '-' || ch == '_')
}

fn value_looks_like_credential(value: &str) -> bool {
    let lower = value.to_ascii_lowercase();
    lower.starts_with("bearer ")
        || is_secret_key(&lower)
        || lower.contains("-----begin ")
        || [
            "password=",
            "passwd=",
            "token=",
            "secret=",
            "api_key=",
            "private_key=",
        ]
        .iter()
        .any(|needle| lower.contains(needle))
}

/// Walks the plan JSON and reports the first credential-shaped key or value.
///
/// Only key names and credential-shaped values are inspected, so an ordinary path
/// like `/data/tokenizer.json` is not mistaken for a secret.
fn find_credential(value: &serde_json::Value, at: &str) -> Option<String> {
    match value {
        serde_json::Value::Object(map) => {
            for (key, item) in map {
                if key_looks_like_credential(key) {
                    return Some(format!("a credential-like field {key:?} at {at}"));
                }
                if let Some(found) = find_credential(item, &format!("{at}.{key}")) {
                    return Some(found);
                }
            }
            None
        }
        serde_json::Value::Array(items) => items
            .iter()
            .enumerate()
            .find_map(|(index, item)| find_credential(item, &format!("{at}[{index}]"))),
        serde_json::Value::String(text) if value_looks_like_credential(text) => {
            Some(format!("a credential-like value at {at}"))
        }
        _ => None,
    }
}

fn new_plan_id() -> String {
    use std::io::Read;
    let mut bytes = [0u8; 6];
    let ok = std::fs::File::open("/dev/urandom")
        .and_then(|mut file| file.read_exact(&mut bytes))
        .is_ok();
    let suffix: String = if ok {
        bytes.iter().map(|byte| format!("{byte:02x}")).collect()
    } else {
        format!("{:x}", now_unix())
    };
    format!("plan-{}-{}", now_unix(), suffix)
}

/// The pipeline and environment a save request resolves to on the server.
///
/// Nothing here comes from the client's plan: the pipeline registration is read
/// from the cluster (or the installed-plugin list), its script is validated
/// against the profile, and the environment is resolved from the profile or the
/// registration, never from a client-supplied override.
struct SavePipeline {
    spec: PipelineSpec,
    source: String,
    builtin: Option<BuiltinChoice>,
    environment: EnvironmentSetup,
    run_interpreter: Option<RunInterpreter>,
    overridden: bool,
    env_note: String,
}

fn resolve_pipeline_for_save(
    runner: &dyn RemoteRunner,
    profile: &ServerProfile,
    request: &RunPlanRequest,
) -> Result<SavePipeline, String> {
    let source = request.pipeline_source.trim().to_string();
    let (spec, builtin) = if source == "builtin" {
        let wanted = request.pipeline.name.trim();
        if wanted.is_empty() {
            return Err("Choose the installed pipeline to run.".to_string());
        }
        let choice = resolve_builtin_model(runner, profile, wanted, request.model.trim())?;
        let entry = builtin_entry(wanted, String::new(), None);
        let mut spec = entry.spec;
        spec.script = choice.manifest.clone();
        (spec, Some(choice))
    } else {
        let entry = load_pipeline_registration(runner, profile, &request.pipeline.script)?;
        (entry.spec, None)
    };
    spec.validate()?;
    profile.validate_pipeline_script(&spec.script)?;
    if let Some(collision) = spec
        .arguments
        .iter()
        .find(|argument| argument.name == "output-dir" || argument.name == "output_dir")
    {
        return Err(format!(
            "The pipeline argument {:?} collides with coperceive's own --output-dir; rename it.",
            collision.name
        ));
    }
    let (environment, overridden, env_note) = resolve_environment(profile, &spec, None);
    let mut environment = match &builtin {
        Some(choice) => choice.environment(environment),
        None => environment,
    };
    // A plan never turns on the internet for compute nodes: that is a profile fact.
    environment.compute_has_internet = profile.environment.compute_has_internet;
    validate_environment_roots(profile, &environment)?;
    // The interpreter and its module are read again on the cluster, as at build time.
    let run_interpreter = match &builtin {
        Some(choice) if environment.kind != EnvironmentKind::Container => {
            choice.resources()?;
            let found = read_run_interpreter(runner, profile, &environment)?;
            environment = with_interpreter_module(environment, &found);
            Some(found)
        }
        Some(choice) => {
            choice.resources()?;
            None
        }
        None => None,
    };
    Ok(SavePipeline {
        spec,
        source: if source == "builtin" {
            "builtin".to_string()
        } else {
            "custom".to_string()
        },
        builtin,
        environment,
        run_interpreter,
        overridden,
        env_note,
    })
}

/// Re-runs the server-side checks for a plan the UI sent back.
///
/// The client's `checks` and `sha256` values are never trusted: containment, the
/// login-node checks and every sha256 are recomputed here, on the cluster.
fn revalidate_plan_on_server(
    runner: &dyn RemoteRunner,
    profile: &ServerProfile,
    plan: &RunPlan,
    environment: &EnvironmentSetup,
    remote_home: Option<&str>,
    needs_checkpoint: bool,
    needs_split: bool,
    builtin: bool,
) -> Result<(PlanArtifact, PlanArtifact, Vec<PlanCheck>), String> {
    if needs_checkpoint {
        validate_path_in_roots(profile, &plan.checkpoint.path, "checkpoint")?;
    }
    if needs_split {
        validate_path_in_roots(profile, &plan.split.path, "test split")?;
    }
    let mut checks: Vec<PlanCheck> = Vec::new();
    let mut artifacts: [Option<PlanArtifact>; 2] = [None, None];
    for (index, (needed, path, name, label, what)) in [
        (needs_checkpoint, &plan.checkpoint.path, "checkpoint", "Checkpoint", "checkpoint"),
        (needs_split, &plan.split.path, "split", "Test split", "test split"),
    ]
    .into_iter()
    .enumerate()
    {
        if !needed {
            continue;
        }
        let (script, timeout) = if name == "split" {
            (split_check_script(path), LISTING_TIMEOUT)
        } else {
            (path_check_script(path), CHECK_TIMEOUT)
        };
        let values = run_check(runner, &mut checks, &format!("{name}-path"), label, &script, timeout)?
        .ok_or_else(|| format!("Could not read the {what} on the cluster."))?;
        if let Some(error) = path_check_error(&values) {
            return Err(error);
        }
        let artifact = artifact_from_check(path, &values);
        if artifact.kind == "missing" {
            return Err(format!(
                "The {what} {path} is no longer on the cluster. Fix the path and rebuild the plan."
            ));
        }
        let real_path = artifact.real_path.as_deref().ok_or_else(|| {
            format!(
                "The cluster did not report where the {what} resolves to, so coperceive cannot \
                 check that it stays inside your folders."
            )
        })?;
        if !real_path_inside_roots(profile, real_path, remote_home) {
            return Err(format!(
                "The {what} resolves to {real_path}, which is outside this server's data and \
                 code folders. Pick one inside them."
            ));
        }
        // A test split may be a folder (`model run --scenes` takes one): it is recorded
        // by the sha256 of its file listing. A checkpoint must be a file with a sha256.
        let folder_ok = name == "split" && artifact.kind == "folder";
        let fingerprinted = if folder_ok {
            artifact.is_fingerprinted()
        } else {
            artifact.sha256.is_some()
        };
        checks.push(check(
            &format!("{name}-sha"),
            &format!("{label} sha256"),
            fingerprinted,
            match (&artifact.sha256, &artifact.listing_sha256, artifact.file_count) {
                (Some(sha), _, _) => format!("sha256 {sha}"),
                (None, Some(listing), Some(files)) if fingerprinted => {
                    format!("folder of {files} file(s), listing sha256 {listing}")
                }
                _ => "Could not read a checksum for this path.".to_string(),
            },
        ));
        if folder_ok && !fingerprinted {
            return Err(format!(
                "The {what} folder {path} lists no files, so it has no fingerprint and the plan \
                 cannot be saved (the submit step compares that fingerprint with the cluster \
                 again). Choose the folder that holds the scenes."
            ));
        }
        if !fingerprinted {
            return Err(format!(
                "The cluster no longer reports a sha256 for the {what}, so the plan cannot be \
                 saved (the submit step compares that sha256 with the cluster again)."
            ));
        }
        artifacts[index] = Some(artifact);
    }
    let checkpoint = artifacts[0].clone().unwrap_or_default();
    let split = artifacts[1].clone().unwrap_or_default();

    // Environment pieces are re-checked instead of trusting the client's checks.
    let env_values = run_check(
        runner,
        &mut checks,
        "environment",
        "Environment pieces",
        PLAN_ENV_SCRIPT,
        ENVIRONMENT_TIMEOUT,
    )?
    .unwrap_or_default();
    let module_available = !env_values.get("MODULE").unwrap_or(&String::new()).is_empty()
        || !env_values.get("LMOD").unwrap_or(&String::new()).is_empty();
    let container_available = !env_values.get("APPTAINER").unwrap_or(&String::new()).is_empty()
        || !env_values.get("SINGULARITY").unwrap_or(&String::new()).is_empty();
    if builtin
        && matches!(
            environment.kind,
            EnvironmentKind::Conda | EnvironmentKind::Venv | EnvironmentKind::None
        )
        && !environment.modules.is_empty()
    {
        checks.push(manifest_modules_check(module_available, &environment.modules));
    }
    match environment.kind {
        EnvironmentKind::Modules => {
            let ok = module_available && !environment.modules.is_empty();
            checks.push(with_hint(
                check(
                    "env-modules",
                    "Module system",
                    ok,
                    if module_available {
                        format!("Modules: {}.", environment.modules.join(", "))
                    } else {
                        "No `module` command was found on the login node.".to_string()
                    },
                ),
                "The run needs its modules on compute nodes too.",
            ));
        }
        EnvironmentKind::Conda | EnvironmentKind::Venv => {
            let interpreter = environment.interpreter.clone().unwrap_or_default();
            let exec_ok = if interpreter.is_empty() {
                false
            } else {
                let values = run_check(
                    runner,
                    &mut checks,
                    "env-python",
                    "Python interpreter",
                    &path_check_script(&interpreter),
                    CHECK_TIMEOUT,
                )?
                .unwrap_or_default();
                if let Some(error) = path_check_error(&values) {
                    return Err(error);
                }
                values.get("EXEC").map(String::as_str) == Some("1")
            };
            checks.push(with_hint(
                check(
                    "env-python-exec",
                    "Python interpreter is executable",
                    exec_ok,
                    if exec_ok {
                        format!("{interpreter} is executable.")
                    } else {
                        format!("{interpreter} is not executable on the login node.")
                    },
                ),
                "Choose the environment's python path in the server profile.",
            ));
        }
        EnvironmentKind::Container => {
            let image = environment.container_image.clone().unwrap_or_default();
            let image_values = if image.is_empty() {
                BTreeMap::new()
            } else {
                run_check(
                    runner,
                    &mut checks,
                    "env-image",
                    "Container image",
                    &path_check_script(&image),
                    CHECK_TIMEOUT,
                )?
                .unwrap_or_default()
            };
            if let Some(error) = path_check_error(&image_values) {
                return Err(error);
            }
            checks.push(with_hint(
                check(
                    "env-container",
                    "Container runtime and image",
                    container_available
                        && image_values.get("EXISTS").map(String::as_str) == Some("1"),
                    format!(
                        "{}{}",
                        if container_available {
                            "Apptainer/Singularity found. "
                        } else {
                            "No Apptainer/Singularity found on the login node. "
                        },
                        if image_values.get("EXISTS").map(String::as_str) == Some("1") {
                            format!("Image {image} exists.")
                        } else {
                            format!("Image {image} was not found.")
                        }
                    ),
                ),
                "Choose an image inside the profile's folders.",
            ));
        }
        EnvironmentKind::None => checks.push(check(
            "env-none",
            "Environment",
            true,
            "No module/conda/container setup is needed.".to_string(),
        )),
    }

    let started = std::time::Instant::now();
    let version_command = coperceive_version_command(profile);
    match runner.run(&version_command, READ_TIMEOUT, None) {
        Ok(result) if result.success => {
            let version = result.stdout.lines().next().unwrap_or("").trim().to_string();
            checks.push(check(
                "coperceive",
                "Coperceive on the cluster",
                !version.is_empty(),
                if version.is_empty() {
                    "coperceive did not report a version.".to_string()
                } else {
                    format!("{version}.")
                },
            ));
        }
        _ => checks.push(with_hint(
            check(
                "coperceive",
                "Coperceive on the cluster",
                false,
                "coperceive was not found on the login node.".to_string(),
            ),
            "Coperceive must be installed on the cluster for the run.",
        )),
    }
    let _ = started;

    let folders = run_check(
        runner,
        &mut checks,
        "folders",
        "Scratch and results folders",
        &plan_paths_script(&profile.scratch_template, &profile.results_root),
        CHECK_TIMEOUT,
    )?
    .unwrap_or_default();
    checks.push(with_hint(
        check(
            "scratch",
            "Scratch folder",
            folders.get("SCRATCH_OK").map(String::as_str) == Some("1"),
            format!("Template {}.", profile.scratch_template),
        ),
        "The scratch template must be writable for your account.",
    ));
    checks.push(with_hint(
        check(
            "results",
            "Shared results folder",
            folders.get("RESULTS_OK").map(String::as_str) == Some("1"),
            format!("{}", profile.results_root),
        ),
        "The results folder must exist and be writable for your account.",
    ));

    if let Some(failed) = checks.iter().find(|check| !check.ok) {
        return Err(format!(
            "The cluster's checks no longer pass ({}): {}. Rebuild the plan, fix the item and \
             try again.",
            failed.label, failed.detail
        ));
    }
    Ok((checkpoint, split, checks))
}

/// Before a saved plan is submitted, its inputs are checked again on the cluster: the
/// checkpoint's sha256, and the test split's sha256 (a file) or listing fingerprint (a
/// folder), must equal what the save recorded, and each must still resolve to the same
/// real path. Read-only and bounded. A changed, missing or unreadable input is refused
/// with the reason, so nothing is submitted against data the user did not review.
pub fn verify_plan_inputs(runner: &dyn RemoteRunner, plan: &RunPlan) -> Result<(), String> {
    for (artifact, what, is_split) in [
        (&plan.checkpoint, "checkpoint", false),
        (&plan.split, "test split", true),
    ] {
        let path = artifact.path.trim();
        if path.is_empty() {
            continue;
        }
        if !artifact.is_fingerprinted() {
            return Err(format!(
                "The saved plan records no fingerprint for the {what} {path}, so it cannot be \
                 checked before submitting. Rebuild and save the plan. Nothing was submitted."
            ));
        }
        let (script, timeout) = if is_split && artifact.kind == "folder" {
            (split_check_script(path), LISTING_TIMEOUT)
        } else {
            (path_check_script(path), CHECK_TIMEOUT)
        };
        let result = runner.run(&script, timeout, None).map_err(|error| {
            format!(
                "Could not check the {what} {path} on the cluster before submitting: {error}. \
                 Nothing was submitted."
            )
        })?;
        if !result.success {
            return Err(format!(
                "Could not check the {what} {path} on the cluster before submitting: {}. \
                 Nothing was submitted.",
                result.stderr.trim()
            ));
        }
        let values = parse_key_values(&result.stdout);
        if let Some(error) = path_check_error(&values) {
            return Err(format!("{error} Nothing was submitted."));
        }
        let now = artifact_from_check(path, &values);
        if now.kind == "missing" {
            return Err(format!(
                "The {what} {path} is no longer on the cluster. Nothing was submitted."
            ));
        }
        let changed = if artifact.sha256.is_some() {
            (artifact.sha256 != now.sha256).then(|| {
                format!(
                    "sha256 {} was saved, the cluster now reports {}",
                    artifact.sha256.as_deref().unwrap_or("none"),
                    now.sha256.as_deref().unwrap_or("none")
                )
            })
        } else {
            (artifact.listing_sha256 != now.listing_sha256 || artifact.file_count != now.file_count)
                .then(|| {
                    format!(
                        "{} file(s) with listing sha256 {} were saved, the cluster now lists {} \
                         file(s) with listing sha256 {}",
                        artifact.file_count.unwrap_or(0),
                        artifact.listing_sha256.as_deref().unwrap_or("none"),
                        now.file_count.unwrap_or(0),
                        now.listing_sha256.as_deref().unwrap_or("none")
                    )
                })
        }
        .or_else(|| {
            (artifact.real_path.is_some() && artifact.real_path != now.real_path).then(|| {
                format!(
                    "it resolved to {}, it now resolves to {}",
                    artifact.real_path.as_deref().unwrap_or(""),
                    now.real_path.as_deref().unwrap_or("nothing")
                )
            })
        });
        if let Some(detail) = changed {
            return Err(format!(
                "The {what} {path} changed since the plan was saved ({detail}). Nothing was \
                 submitted. Check the {what}, then rebuild and save the plan."
            ));
        }
    }
    Ok(())
}

/// Saves a plan under the profile's `results_root/plans/<id>.json`.
///
/// The plan and request from the UI are untrusted: the id, pipeline, script,
/// typed argv, environment, output directory, containment, checks and sha256 are
/// all resolved or recomputed here from the server profile and the cluster, and
/// validated again at save time. The write is atomic (temp file + rename) and
/// 0600, and contains no secrets. Nothing is submitted.
pub fn save_run_plan(
    runner: &dyn RemoteRunner,
    entry: &ServerEntry,
    plan: &RunPlan,
    request: &RunPlanRequest,
) -> Result<RunPlan, String> {
    if !plan.nothing_submitted {
        return Err(
            "This plan claims a job was submitted; a plan is saved before submission.".to_string(),
        );
    }
    if plan.profile_id != entry.id || request.profile_id != entry.id {
        return Err("This plan belongs to a different server profile.".to_string());
    }
    validate_plan_id(&plan.id)?;
    let profile = &entry.profile;
    profile.validate()?;
    let remote_home = remote_home(runner);
    let save_pipeline = resolve_pipeline_for_save(runner, profile, request)?;
    let plans_dir = format!("{}/plans", profile.results_root.trim_end_matches('/'));
    profile.validate_output_dir(&plans_dir)?;
    let final_path = format!("{plans_dir}/{}.json", plan.id);

    // The client's checks and hashes are deliberately ignored: recompute them.
    let (needs_checkpoint, needs_split) = match &save_pipeline.builtin {
        Some(choice) => {
            let given = if plan.checkpoint.path.trim().is_empty() {
                request.checkpoint.as_str()
            } else {
                plan.checkpoint.path.as_str()
            };
            let ckpt_root = if plan.ckpt_root.path.trim().is_empty() {
                request.ckpt_root.as_str()
            } else {
                plan.ckpt_root.path.as_str()
            };
            choice.check_options(given, ckpt_root, &request.values)?;
            (choice.takes_checkpoint(), true)
        }
        None => required_plan_artifacts(&save_pipeline.source, &save_pipeline.spec),
    };
    let gres = match &save_pipeline.builtin {
        Some(choice) => choice.gres(entry)?,
        None => default_eval_gres(entry),
    };
    let (checkpoint, split, mut checks) = revalidate_plan_on_server(
        runner,
        profile,
        plan,
        &save_pipeline.environment,
        remote_home.as_deref(),
        needs_checkpoint,
        needs_split,
        save_pipeline.builtin.is_some(),
    )?;
    let output_dir = format!(
        "{}/runs/{}",
        profile.results_root.trim_end_matches('/'),
        plan.id
    );
    profile.validate_output_dir(&output_dir)?;

    validate_path_arguments(profile, &save_pipeline.spec, &request.values)?;
    // Rebuild the typed values and argv from the server pipeline, not the client's.
    let mut typed = typed_values(&save_pipeline.spec, &request.values)?;
    for argument in &save_pipeline.spec.arguments {
        match argument.role {
            ArgumentRole::Checkpoint => {
                typed.insert(
                    argument.name.clone(),
                    serde_json::Value::String(checkpoint.path.clone()),
                );
            }
            ArgumentRole::TestData => {
                typed.insert(
                    argument.name.clone(),
                    serde_json::Value::String(split.path.clone()),
                );
            }
            _ => {}
        }
    }
    let eval_protocol = if request.eval_protocol.trim().is_empty() {
        plan.eval_protocol.trim()
    } else {
        request.eval_protocol.trim()
    };
    if save_pipeline.builtin.is_some() && !EVAL_PROTOCOLS.contains(&eval_protocol) {
        return Err(format!(
            "Choose the AP protocol for this run: one of {}.",
            EVAL_PROTOCOLS.join(", ")
        ));
    }
    let argv: Vec<String> = if let Some(choice) = &save_pipeline.builtin {
        let model = request.model.trim();
        let raw = request.raw_root.trim();
        let media = request.media_root.trim();
        let ckpt_root = if plan.ckpt_root.path.trim().is_empty() {
            request.ckpt_root.trim()
        } else {
            plan.ckpt_root.path.trim()
        };
        if model.is_empty() || raw.is_empty() {
            return Err(
                "This plan predates the model/raw fields; rebuild this plan before saving."
                    .to_string(),
            );
        }
        validate_path_in_roots(profile, raw, "raw root")?;
        // Optional, as the CLI's --media-root is; when empty build_builtin_argv writes no
        // --media-root at all.
        if !media.is_empty() {
            validate_path_in_roots(profile, media, "media root")?;
        }
        if !ckpt_root.is_empty() {
            validate_path_in_roots(profile, ckpt_root, "ckpt root")?;
        }
        // `coperceive model run` takes --out/--run-id/--work and has no --output-dir.
        build_builtin_argv(
            profile,
            &choice.manifest,
            &plan.id,
            &typed,
            &split.path,
            if needs_checkpoint { &checkpoint.path } else { "" },
            &output_dir,
            model,
            raw,
            media,
            ckpt_root,
            eval_protocol,
            WORK_IN_SCRATCH,
        )?
    } else {
        // A custom pipeline follows the output contract: force the output directory
        // pair to the recomputed value.
        let mut argv = build_pipeline_argv(&save_pipeline.spec, &typed, &output_dir)?;
        strip_output_dir(&mut argv);
        argv.push("--output-dir".to_string());
        argv.push(output_dir.clone());
        argv
    };
    if argv.is_empty() || argv.iter().any(|element| element.contains('\n')) {
        return Err("The rebuilt command arguments are empty or contain a line break.".to_string());
    }
    checks.push(check(
        "argv",
        "Typed argv",
        true,
        format!(
            "{} separate argument(s) rebuilt from the saved pipeline and typed values; no shell \
             string is used.",
            argv.len()
        ),
    ));

    let contract_check_script =
        render_contract_check_script(
            profile,
            &save_pipeline.environment,
            &argv,
            &plan.id,
            save_pipeline.builtin.is_some(),
        )?;
    if !save_pipeline.environment.compute_has_internet {
        for forbidden in [
            "pip install",
            "conda install",
            "conda create",
            "git clone",
            "curl ",
            "wget ",
        ] {
            if contract_check_script.contains(forbidden) {
                return Err(format!(
                    "The contract-check script contains {forbidden:?}, but this server's compute \
                     nodes have no internet. Remove installs/downloads from the pipeline."
                ));
            }
        }
    }

    let pipeline_ref = PlanPipelineRef {
        id: save_pipeline.spec.name.clone(),
        name: save_pipeline.spec.name.clone(),
        version: String::new(),
        source: save_pipeline.source.clone(),
    };
    let mut validated = plan.clone();
    // These identity fields come from the app at save time, never from the
    // client-supplied plan JSON.
    validated.schema = RUN_PLAN_SCHEMA.to_string();
    validated.created_unix = now_unix();
    validated.profile_id = entry.id.clone();
    validated.profile_name = profile.name.clone();
    validated.pipeline = pipeline_ref;
    validated.argv = argv;
    validated.environment = save_pipeline.environment;
    validated.environment_from_override = save_pipeline.overridden;
    validated.environment_note = save_pipeline.env_note;
    validated.checkpoint = checkpoint;
    validated.split = split;
    validated.model = if save_pipeline.source == "builtin" {
        request.model.trim().to_string()
    } else {
        String::new()
    };
    validated.raw_root = if save_pipeline.source == "builtin" {
        artifact_from_check_for_input(&request.raw_root, "folder")
    } else {
        PlanArtifact::default()
    };
    validated.media_root = if save_pipeline.source == "builtin" {
        artifact_from_check_for_input(&request.media_root, "folder")
    } else {
        PlanArtifact::default()
    };
    validated.ckpt_root = if save_pipeline.source == "builtin" {
        artifact_from_check_for_input(
            if plan.ckpt_root.path.trim().is_empty() {
                request.ckpt_root.trim()
            } else {
                plan.ckpt_root.path.trim()
            },
            "folder",
        )
    } else {
        PlanArtifact::default()
    };
    validated.model_options = save_pipeline
        .builtin
        .as_ref()
        .map(|choice| choice.model.options.clone())
        .unwrap_or_default();
    validated.eval_protocol = if save_pipeline.source == "builtin" {
        eval_protocol.to_string()
    } else {
        DEFAULT_EVAL_PROTOCOL.to_string()
    };
    validated.work_dir = if save_pipeline.source == "builtin" {
        WORK_IN_SCRATCH.to_string()
    } else {
        String::new()
    };
    validated.model_resources =
        save_pipeline.builtin.as_ref().map(BuiltinChoice::resources).transpose()?;
    // The run's python is read again too: its coperceive (PYTHONPATH) and the --time
    // estimate are never taken from the client's plan.
    let mut run_interpreter = save_pipeline.run_interpreter.clone();
    let mut time_estimate = None;
    if let Some(found) = run_interpreter.as_mut() {
        checks.push(interpreter_plan_check(found));
        if let Some(choice) = &save_pipeline.builtin {
            let (estimate, facts) = read_python_facts(runner, choice, found, &validated.split.path)?;
            checks.push(facts);
            time_estimate = estimate;
        }
    }
    validated.run_interpreter = run_interpreter;
    validated.time_estimate = time_estimate;
    validated.partition_max_time = if save_pipeline.builtin.is_some() {
        partition_max_time(entry)
    } else {
        None
    };
    validated.logs_dir = if save_pipeline.builtin.is_some() {
        Some(builtin_logs_dir(remote_home.as_deref())?)
    } else {
        None
    };
    validated.checks = checks;
    validated.output_dir = output_dir;
    validated.scratch_dir = profile.scratch_template.clone();
    validated.gres = gres;
    validated.contract_check_script = contract_check_script;
    validated.saved_path = None;

    let value = serde_json::to_value(&validated)
        .map_err(|error| format!("Could not serialise the run plan: {error}"))?;
    if let Some(found) = find_credential(&value, "plan") {
        return Err(format!(
            "The run plan contains {found}; refusing to save it. Secrets must come from the \
             environment, never from a plan."
        ));
    }
    let serialized = serde_json::to_string_pretty(&validated)
        .map_err(|error| format!("Could not serialise the run plan: {error}"))?;
    let result = runner
        .run(&write_plan_script(&final_path), WRITE_TIMEOUT, Some(&serialized))
        .map_err(|error| format!("Could not write the plan on the cluster: {error}"))?;
    if !result.success {
        return Err(format!(
            "Could not write the plan to {final_path}. {}",
            result.stderr.trim()
        ));
    }
    let read_back = runner
        .run(&read_file_script(&final_path), READ_TIMEOUT, None)
        .map_err(|error| format!("Could not verify the saved plan: {error}"))?;
    if !read_back.success {
        return Err(format!(
            "The plan was written to {final_path}, but it could not be read back for verification."
        ));
    }
    let saved: RunPlan = serde_json::from_str(&read_back.stdout)
        .map_err(|error| format!("The saved plan could not be read back as JSON: {error}"))?;
    if saved.id != validated.id {
        return Err("The saved plan read back with a different id.".to_string());
    }
    let mut saved_plan = saved;
    saved_plan.saved_path = Some(final_path);
    Ok(saved_plan)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::hpc::{ArgumentRole, EnvironmentSetup, JobDefaults};
    use crate::ssh::CommandResult;
    use std::sync::Mutex;

    type CallLog = std::sync::Arc<Mutex<Vec<(String, Option<String>)>>>;

    #[derive(Default)]
    struct FakeRunner {
        calls: CallLog,
        responses: Mutex<Vec<(String, CommandResult)>>,
    }

    impl FakeRunner {
        fn calls(&self) -> CallLog {
            std::sync::Arc::clone(&self.calls)
        }

        fn with(self, needle: &str, stdout: &str) -> Self {
            self.with_result(needle, stdout, true)
        }

        fn with_result(self, needle: &str, stdout: &str, success: bool) -> Self {
            self.responses.lock().unwrap().push((
                needle.to_string(),
                CommandResult {
                    success,
                    stdout: stdout.to_string(),
                    stderr: if success {
                        String::new()
                    } else {
                        "fake failure".to_string()
                    },
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

    /// Records plan writes and answers the bounded revalidation checks, then
    /// returns the written JSON for the read-back verification.
    struct EchoRunner {
        written: Mutex<Vec<(String, Option<String>)>>,
        pipeline_json: Option<String>,
        model_list: Option<String>,
    }

    impl EchoRunner {
        fn new() -> Self {
            Self {
                written: Mutex::new(Vec::new()),
                pipeline_json: None,
                model_list: None,
            }
        }
    }

    impl RemoteRunner for EchoRunner {
        fn run(
            &self,
            remote_cmd: &str,
            _timeout: Duration,
            stdin: Option<&str>,
        ) -> Result<CommandResult, String> {
            self.written
                .lock()
                .unwrap()
                .push((remote_cmd.to_string(), stdin.map(str::to_string)));
            if remote_cmd.contains("coperceive-pipeline.json") {
                let stdout = self.pipeline_json.clone().unwrap_or_else(|| {
                    r#"{"name":"contract-check","script":"/data/code/pipelines/contract_pipeline.py","interpreter":["/usr/bin/python3"],"environment":{"kind":"none"},"arguments":[{"name":"checkpoint","kind":"path","role":"checkpoint","required":true},{"name":"split","kind":"path","role":"test_data","required":true},{"name":"extra","kind":"str"}]}"#.to_string()
                });
                return Ok(CommandResult {
                    success: true,
                    stdout,
                    stderr: String::new(),
                });
            }
            if remote_cmd.contains("coperceive-check:model-list") {
                return Ok(CommandResult {
                    success: true,
                    stdout: self.model_list.clone().unwrap_or_default(),
                    stderr: String::new(),
                });
            }
            if remote_cmd.contains("coperceive-check:plan-python") {
                return Ok(CommandResult {
                    success: true,
                    stdout: "ROOT=/data/code/coperceive\nSAMPLES=1490\n".to_string(),
                    stderr: String::new(),
                });
            }
            if remote_cmd.contains("coperceive-check:plan-read") {
                let payload = self
                    .written
                    .lock()
                    .unwrap()
                    .iter()
                    .rev()
                    .find(|(command, _)| command.contains("coperceive-check:plan-write"))
                    .and_then(|(_, stdin)| stdin.clone())
                    .unwrap_or_default();
                return Ok(CommandResult {
                    success: true,
                    stdout: payload,
                    stderr: String::new(),
                });
            }
            let stdout = if remote_cmd.contains("coperceive-check:plan-path") {
                "EXISTS=1\nREADABLE=1\nEXEC=0\nISDIR=0\nREALPATH=/data/code/checkpoints/model.pt\nSHA256=abc123\n"
            } else if remote_cmd.contains("coperceive-check:plan-env") {
                "MODULE=/usr/share/lmod/lmod/libexec/lmod\nLMOD=/usr/share/lmod/lmod/libexec/lmod\nAPPTAINER=\nSINGULARITY=\nPYTHON3=/usr/bin/python3\nCOPERCEIVE=/apps/coperceive/bin/coperceive\nHOME=/home/bob\n"
            } else if remote_cmd.contains("--version") {
                "coperceive 0.4.1\n"
            } else if remote_cmd.contains("coperceive-check:plan-folders") {
                "SCRATCH_OK=1\nRESULTS_OK=1\n"
            } else {
                ""
            };
            Ok(CommandResult {
                success: true,
                stdout: stdout.to_string(),
                stderr: String::new(),
            })
        }
    }

    const CLUSTER_B: &str = include_str!(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/tests/fixtures/hpc/cluster-b.json"
    ));

    fn entry() -> ServerEntry {
        serde_json::from_str(CLUSTER_B).expect("cluster-b fixture")
    }

    fn pipeline() -> PipelineSpec {
        PipelineSpec {
            name: "contract-check".to_string(),
            script: "/data/code/pipelines/contract_pipeline.py".to_string(),
            interpreter: vec!["/usr/bin/python3".to_string()],
            environment: EnvironmentSetup::default(),
            arguments: vec![
                crate::hpc::ArgumentSpec {
                    name: "checkpoint".to_string(),
                    kind: ArgumentKind::Path,
                    help: "weights".to_string(),
                    required: true,
                    default: None,
                    choices: Vec::new(),
                    multiple: false,
                    role: ArgumentRole::Checkpoint,
                },
                crate::hpc::ArgumentSpec {
                    name: "split".to_string(),
                    kind: ArgumentKind::Path,
                    help: "test split".to_string(),
                    required: true,
                    default: None,
                    choices: Vec::new(),
                    multiple: false,
                    role: ArgumentRole::TestData,
                },
                crate::hpc::ArgumentSpec {
                    name: "extra".to_string(),
                    kind: ArgumentKind::Str,
                    help: "hostile test value".to_string(),
                    required: false,
                    default: None,
                    choices: Vec::new(),
                    multiple: false,
                    role: ArgumentRole::Other,
                },
            ],
            output_contract: "coperceive-pipeline-1".to_string(),
            description: String::new(),
        }
    }

    fn request() -> RunPlanRequest {
        let mut values = BTreeMap::new();
        values.insert(
            "checkpoint".to_string(),
            serde_json::json!("/data/code/checkpoints/model.pt"),
        );
        values.insert(
            "split".to_string(),
            serde_json::json!("/data/code/splits/test.txt"),
        );
        values.insert("extra".to_string(), serde_json::json!("a b; touch /tmp/x"));
        RunPlanRequest {
            profile_id: entry().id,
            pipeline: pipeline(),
            pipeline_source: "custom".to_string(),
            registration_path: Some("/data/code/pipelines/coperceive-pipeline.json".to_string()),
            plugin_manifest: None,
            values,
            checkpoint: "/data/code/checkpoints/model.pt".to_string(),
            split: "/data/code/splits/test.txt".to_string(),
            model: String::new(),
            raw_root: String::new(),
            media_root: String::new(),
            ckpt_root: String::new(),
            eval_protocol: "default".to_string(),
            environment_override: None,
        }
    }

    fn checked_runner() -> FakeRunner {
        FakeRunner::default()
            .with(
                "coperceive-check:plan-path",
                "EXISTS=1\nREADABLE=1\nEXEC=0\nISDIR=0\nREALPATH=/data/code/checkpoints/model.pt\nSHA256=abc123\n",
            )
            .with(
                "coperceive-check:plan-env",
                "MODULE=/usr/share/lmod/lmod/libexec/lmod\nLMOD=/usr/share/lmod/lmod/libexec/lmod\nAPPTAINER=\nSINGULARITY=\nPYTHON3=/usr/bin/python3\nCOPERCEIVE=/apps/coperceive/bin/coperceive\nHOME=/home/bob\n",
            )
            .with("--version", "coperceive 0.4.1\n")
            .with(
                "coperceive-check:plan-folders",
                "SCRATCH_OK=1\nRESULTS_OK=1\n",
            )
            .with("coperceive-check:model-list", MODEL_LIST_JSON)
            .with("coperceive-check:plan-python", "ROOT=/data/code/coperceive\nSAMPLES=1490\n")
    }

    /// `coperceive model list --json` on a cluster with Griffin installed: the
    /// manifest is a resolved path, with what each model needs (models/commands.py).
    const MODEL_LIST_JSON: &str = r#"{
  "format": "coperceive-model-list",
  "format_version": 1,
  "plugins": [
    {
      "error": null,
      "location": "coperceive_griffin_model:MANIFEST",
      "manifest": "/data/code/griffin/coperceive_griffin_model/model.yaml",
      "models": {
        "vehicle-side": {"cpus": 8, "depends_on": [], "gpu": true, "memory_gb": 64, "options": ["ckpt_root", "det_json", "min_score", "nusc_root", "subset"], "transient_artifacts": [], "seconds_per_sample": 0.394, "staging_seconds": 700.0, "conversion_seconds": 100.0},
        "drone-side": {"cpus": 8, "depends_on": [], "gpu": true, "memory_gb": 64, "options": ["ckpt_root", "subset"], "transient_artifacts": ["track_query"]},
        "fusion": {"cpus": 4, "depends_on": ["drone-side"], "gpu": true, "memory_gb": 32, "options": ["ckpt_root"], "transient_artifacts": []},
        "weights-from-option": {"cpus": 1, "depends_on": [], "gpu": false, "memory_gb": 2, "options": ["checkpoint"], "transient_artifacts": []}
      },
      "modules": ["nvidia/cuda-11.8", "nvidia/cuda-11.x_cudnn-8.6"],
      "name": "griffin",
      "source": "entry-point"
    }
  ]
}"#;

    /// A cluster with Griffin installed. It answers the read-only checks, keeps the
    /// one plan write for the read-back, and reports `directories` as folders.
    struct GriffinCluster {
        model_list: Result<String, String>,
        directories: Vec<String>,
        /// The interpreter check's answer per program (`coperceive-check:plan-interpreter`);
        /// any other program is a console script with a python beside it, or a python
        /// outside any venv.
        interpreters: Vec<(String, String)>,
        /// The run's python's answer to `coperceive-check:plan-python`.
        python_facts: String,
        /// The timeout the `plan-python` check ran under.
        facts_timeout: Mutex<Option<Duration>>,
        calls: Mutex<Vec<String>>,
        written: Mutex<Option<String>>,
    }

    impl GriffinCluster {
        fn new() -> Self {
            Self {
                model_list: Ok(MODEL_LIST_JSON.to_string()),
                directories: vec!["/data/scenes/val".to_string()],
                interpreters: Vec::new(),
                python_facts: "ROOT=/data/code/coperceive\nSAMPLES=1490\n".to_string(),
                facts_timeout: Mutex::new(None),
                calls: Mutex::new(Vec::new()),
                written: Mutex::new(None),
            }
        }

        fn with_interpreter(mut self, program: &str, answer: &str) -> Self {
            self.interpreters.push((program.to_string(), answer.to_string()));
            self
        }
    }

    /// `(program, mode)` of a `sh -c '<script>' sh <program> <mode>` check.
    fn program_and_mode(remote_cmd: &str) -> (String, String) {
        let rest = remote_cmd.rsplit_once("' sh ").map(|(_, rest)| rest).unwrap_or("");
        let mut words = rest.split(' ').map(|word| word.trim_matches('\'').to_string());
        (words.next().unwrap_or_default(), words.next().unwrap_or_default())
    }

    impl RemoteRunner for GriffinCluster {
        fn run(
            &self,
            remote_cmd: &str,
            timeout: Duration,
            stdin: Option<&str>,
        ) -> Result<CommandResult, String> {
            for forbidden in ["sbatch", "scancel", "squeue"] {
                assert!(!remote_cmd.contains(forbidden), "{remote_cmd}");
            }
            self.calls.lock().unwrap().push(remote_cmd.to_string());
            let ok = |stdout: String| {
                Ok(CommandResult { success: true, stdout, stderr: String::new() })
            };
            if remote_cmd.contains("coperceive-check:model-list") {
                return match &self.model_list {
                    Ok(stdout) => ok(stdout.clone()),
                    Err(stderr) => Ok(CommandResult {
                        success: false,
                        stdout: String::new(),
                        stderr: stderr.clone(),
                    }),
                };
            }
            if remote_cmd.contains("coperceive-check:plan-path") {
                let path = remote_cmd
                    .rsplit_once(" sh ")
                    .map(|(_, rest)| rest.trim().trim_matches('\'').to_string())
                    .unwrap_or_default();
                return ok(if self.directories.contains(&path) {
                    format!(
                        "EXISTS=1\nREADABLE=1\nEXEC=1\nISDIR=1\nREALPATH={path}\nSHA256=\n\
                         FILES=12\nLISTING_SHA256={}\n",
                        "1f".repeat(32)
                    )
                } else {
                    format!(
                        "EXISTS=1\nREADABLE=1\nEXEC={}\nISDIR=0\nREALPATH={path}\nSHA256={}\n",
                        if path.ends_with("/bin/python") { 1 } else { 0 },
                        "ab".repeat(32)
                    )
                });
            }
            if remote_cmd.contains("coperceive-check:plan-interpreter") {
                let (program, mode) = program_and_mode(remote_cmd);
                if let Some((_, answer)) =
                    self.interpreters.iter().find(|(name, _)| *name == program)
                {
                    return ok(answer.clone());
                }
                let beside = format!("{}python", program.trim_end_matches("coperceive"));
                return ok(if mode == "script" {
                    format!("BESIDE={beside}\nPYTHON={beside}\nPYVENV_CFG=\n")
                } else {
                    format!("PYTHON={program}\nPYVENV_CFG=\n")
                });
            }
            if remote_cmd.contains("coperceive-check:plan-python") {
                *self.facts_timeout.lock().unwrap() = Some(timeout);
                return ok(self.python_facts.clone());
            }
            if remote_cmd.contains("coperceive-check:plan-env") {
                return ok("MODULE=/usr/share/lmod/lmod/libexec/lmod\nLMOD=x\nAPPTAINER=\n\
                           SINGULARITY=\nPYTHON3=/usr/bin/python3\nCOPERCEIVE=\nHOME=/home/bob\n"
                    .to_string());
            }
            if remote_cmd.contains("--version") {
                return ok("coperceive 0.1.1\n".to_string());
            }
            if remote_cmd.contains("coperceive-check:plan-folders") {
                return ok("SCRATCH_OK=1\nRESULTS_OK=1\n".to_string());
            }
            if remote_cmd.contains("coperceive-check:plan-write") {
                *self.written.lock().unwrap() = stdin.map(str::to_string);
                return ok(String::new());
            }
            if remote_cmd.contains("cat -- \"$1\"") {
                return ok(self.written.lock().unwrap().clone().unwrap_or_default());
            }
            Ok(CommandResult {
                success: false,
                stdout: String::new(),
                stderr: format!("unexpected command {remote_cmd}"),
            })
        }
    }

    /// What NewEvaluation.tsx sends for Griffin vehicle-side on cluster-b: the scenes
    /// folder as the split and no checkpoint (the model pins its own weights).
    fn griffin_request() -> RunPlanRequest {
        let mut req = request();
        req.pipeline_source = "builtin".to_string();
        req.pipeline.name = "griffin".to_string();
        req.pipeline.script = "/data/code/griffin/coperceive_griffin_model/model.yaml".to_string();
        req.pipeline.arguments = Vec::new();
        req.registration_path = None;
        req.plugin_manifest = Some("/data/code/griffin/coperceive_griffin_model/model.yaml".to_string());
        req.values.clear();
        req.checkpoint = String::new();
        req.split = "/data/scenes/val".to_string();
        req.model = "vehicle-side".to_string();
        req.raw_root = "/data/scenes/raw".to_string();
        req.media_root = "/data/scenes/raw".to_string();
        req.ckpt_root = "/data/scenes/ckpts".to_string();
        req.eval_protocol = "default".to_string();
        req
    }

    #[test]
    fn a_builtin_plan_passes_model_run_its_own_output_flags_and_no_output_dir() {
        let cluster = GriffinCluster::new();
        let built = build_run_plan(&cluster, &entry(), &griffin_request()).expect("build");
        let saved = save_run_plan(&cluster, &entry(), &built, &griffin_request()).expect("save");
        for argv in [&built.argv, &saved.argv] {
            assert!(
                !argv.iter().any(|word| word.starts_with("--output-dir")),
                "{argv:?}"
            );
            let at = |flag: &str| argv.iter().position(|word| word == flag).map(|i| argv[i + 1].clone());
            assert_eq!(at("--out"), Some(saved.output_dir.clone()));
            assert_eq!(at("--run-id"), Some(saved.id.clone()));
            // The work directory is the job's scratch (the CLI's `--work "$SCRATCH/work"`).
            assert_eq!(at("--work"), Some("$SCRATCH_DIR/work".to_string()));
        }
    }

    #[test]
    fn a_model_without_a_checkpoint_option_gets_none_and_a_given_one_is_refused() {
        let cluster = GriffinCluster::new();
        let plan = build_run_plan(&cluster, &entry(), &griffin_request()).expect("build");
        assert!(
            !plan.argv.iter().any(|word| word.starts_with("checkpoint=")),
            "{:?}",
            plan.argv
        );
        assert!(plan.checkpoint.path.is_empty());
        let mut req = griffin_request();
        req.checkpoint = "/data/code/checkpoints/vehicle.pth".to_string();
        let error = build_run_plan(&cluster, &entry(), &req).expect_err("refused");
        assert!(error.contains("vehicle-side"), "{error}");
        assert!(error.contains("no checkpoint option"), "{error}");
        let error = save_run_plan(&cluster, &entry(), &plan, &req).expect_err("refused at save");
        assert!(error.contains("no checkpoint option"), "{error}");
    }

    #[test]
    fn a_model_that_declares_a_checkpoint_option_takes_the_checkpoint() {
        let cluster = GriffinCluster::new();
        let mut req = griffin_request();
        req.model = "weights-from-option".to_string();
        // This fixture model declares only checkpoint, not the required ckpt_root.
        req.ckpt_root = String::new();
        req.checkpoint = String::new();
        let error = build_run_plan(&cluster, &entry(), &req).expect_err("needs one");
        assert!(error.contains("Choose a checkpoint path"), "{error}");
        req.checkpoint = "/data/code/checkpoints/vehicle.pth".to_string();
        let plan = build_run_plan(&cluster, &entry(), &req).expect("build");
        let saved = save_run_plan(&cluster, &entry(), &plan, &req).expect("save");
        assert!(saved
            .argv
            .contains(&"checkpoint=/data/code/checkpoints/vehicle.pth".to_string()));
        assert_eq!(saved.checkpoint.sha256.as_deref(), Some("ab".repeat(32).as_str()));
    }

    #[test]
    fn a_scenes_directory_survives_save_and_load_with_its_listing_fingerprint() {
        let cluster = GriffinCluster::new();
        let plan = build_run_plan(&cluster, &entry(), &griffin_request()).expect("build");
        assert!(plan.is_saveable(), "checks: {:?}", plan.checks);
        let saved = save_run_plan(&cluster, &entry(), &plan, &griffin_request()).expect("save");
        let loaded =
            crate::job_submit::load_saved_plan(&cluster, &entry().profile, &saved.id).expect("load");
        assert_eq!(loaded.split.kind, "folder");
        let json = serde_json::to_value(&loaded).unwrap();
        assert_eq!(json["split"]["listing_sha256"], serde_json::json!("1f".repeat(32)));
        assert_eq!(json["split"]["file_count"], serde_json::json!(12));
    }

    #[test]
    fn an_empty_scenes_directory_cannot_be_saved() {
        struct Empty(GriffinCluster);
        impl RemoteRunner for Empty {
            fn run(&self, cmd: &str, t: Duration, stdin: Option<&str>) -> Result<CommandResult, String> {
                let mut result = self.0.run(cmd, t, stdin)?;
                result.stdout = result
                    .stdout
                    .replace("FILES=12", "FILES=0")
                    .replace(&"1f".repeat(32), &"e3".repeat(32));
                Ok(result)
            }
        }
        let cluster = GriffinCluster::new();
        let plan = build_run_plan(&cluster, &entry(), &griffin_request()).expect("build");
        let error = save_run_plan(&Empty(GriffinCluster::new()), &entry(), &plan, &griffin_request())
            .expect_err("an empty folder");
        assert!(error.contains("no files"), "{error}");
    }

    #[test]
    #[cfg(unix)]
    fn path_check_fingerprints_a_folder_by_its_file_listing() {
        if !std::path::Path::new("/bin/sh").exists() {
            return;
        }
        let root = std::env::temp_dir().join(format!("cp-listing-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&root);
        std::fs::create_dir_all(root.join("scenes/val00")).unwrap();
        std::fs::create_dir_all(root.join("scenes/val 01")).unwrap();
        std::fs::write(root.join("scenes/val00/manifest.json"), b"{}").unwrap();
        std::fs::write(root.join("scenes/val 01/manifest.json"), b"{\"a\":1}").unwrap();
        let bin = std::path::PathBuf::from(concat!(env!("CARGO_MANIFEST_DIR"), "/tests/fixtures/bin"));
        let check = || {
            let output = std::process::Command::new("/bin/sh")
                .args([
                    "-c",
                    &format!("{PATH_CHECK_BODY}{SPLIT_LISTING_BODY}"),
                    "sh",
                    &root.join("scenes").to_string_lossy(),
                ])
                .env("PATH", format!("{}:/usr/bin:/bin", bin.display()))
                .output()
                .expect("run sh");
            parse_key_values(&String::from_utf8_lossy(&output.stdout))
        };
        let first = check();
        assert_eq!(first.get("ISDIR").map(String::as_str), Some("1"), "{first:?}");
        assert_eq!(first.get("FILES").map(String::as_str), Some("2"), "{first:?}");
        let listing = first.get("LISTING_SHA256").cloned().unwrap_or_default();
        assert_eq!(listing.len(), 64, "{first:?}");
        assert_eq!(check().get("LISTING_SHA256"), Some(&listing), "deterministic");
        // A file that changes size, or a new file, changes the fingerprint.
        std::fs::write(root.join("scenes/val00/manifest.json"), b"{\"b\":22}").unwrap();
        let resized = check().get("LISTING_SHA256").cloned().unwrap_or_default();
        assert_ne!(resized, listing);
        std::fs::write(root.join("scenes/val00/extra.bin"), b"").unwrap();
        let added = check();
        assert_eq!(added.get("FILES").map(String::as_str), Some("3"));
        assert_ne!(added.get("LISTING_SHA256"), Some(&resized));
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn the_manifest_path_comes_from_model_list_json_not_the_entry_point_text() {
        let runner = FakeRunner::default().with("coperceive-check:model-list", MODEL_LIST_JSON);
        let pipelines = list_builtin_pipelines(&runner, &entry().profile);
        assert_eq!(pipelines.len(), 1);
        assert_eq!(
            pipelines[0].plugin_manifest.as_deref(),
            Some("/data/code/griffin/coperceive_griffin_model/model.yaml")
        );
        let calls = runner.calls();
        let calls = calls.lock().unwrap();
        assert!(calls.iter().any(|(command, _)| command.contains("model list --json")), "{calls:?}");
        // The plain listing's last word is the entry point, never a path. An answer in
        // that form is not the JSON listing: it is refused with the reason, and no
        // pipeline is offered with the entry point text as its manifest.
        let runner = FakeRunner::default().with(
            "coperceive-check:model-list",
            "griffin  entry-point  coperceive_griffin_model:MANIFEST\n",
        );
        let error = read_builtin_plugins(&runner, &entry().profile).expect_err("not JSON");
        assert!(error.contains("did not answer with the model list"), "{error}");
        let pipelines = list_builtin_pipelines(&runner, &entry().profile);
        assert!(pipelines.is_empty(), "{pipelines:?}");
        assert!(!pipelines.iter().any(|pipeline| pipeline
            .plugin_manifest
            .as_deref()
            .is_some_and(|path| path.contains("coperceive_griffin_model:MANIFEST"))));
    }

    #[test]
    fn a_cluster_coperceive_too_old_for_model_list_json_is_named() {
        let mut cluster = GriffinCluster::new();
        cluster.model_list = Err(
            "usage: coperceive [-h] ...\ncoperceive: error: unrecognized arguments: --json\n"
                .to_string(),
        );
        let error = build_run_plan(&cluster, &entry(), &griffin_request()).expect_err("too old");
        assert!(error.contains("too old"), "{error}");
        assert!(error.contains("model list --json"), "{error}");
    }

    /// Runs every command with this machine's `/bin/sh` from `cwd`, as the login shell
    /// runs the remote command string.
    #[cfg(unix)]
    struct LocalShell {
        cwd: std::path::PathBuf,
    }

    #[cfg(unix)]
    impl RemoteRunner for LocalShell {
        fn run(&self, cmd: &str, _timeout: Duration, _stdin: Option<&str>) -> Result<CommandResult, String> {
            let output = std::process::Command::new("/bin/sh")
                .args(["-c", cmd])
                .current_dir(&self.cwd)
                .env("PATH", "/usr/bin:/bin")
                .output()
                .map_err(|error| error.to_string())?;
            Ok(CommandResult {
                success: output.status.success(),
                stdout: String::from_utf8_lossy(&output.stdout).to_string(),
                stderr: String::from_utf8_lossy(&output.stderr).to_string(),
            })
        }
    }

    #[test]
    #[cfg(unix)]
    fn the_model_list_check_runs_a_coperceive_command_with_quoted_words_under_sh() {
        use std::os::unix::fs::PermissionsExt;
        // A venv under a folder whose name has a space, a quote and a `;`: shell_quote
        // single-quotes it, so it must reach `sh -c` as a positional parameter, never
        // inside the single-quoted script body.
        let root = std::env::temp_dir().join(format!("cp-model-list-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&root);
        let bin = root.join("my env's;touch pwned").join("bin");
        std::fs::create_dir_all(&bin).unwrap();
        let fake = |name: &str, too_old: bool| {
            let python = bin.join(name);
            let json = if too_old {
                "echo \"usage: coperceive [-h] ...\" >&2\n\
                 echo \"coperceive: error: unrecognized arguments: --json\" >&2\nexit 2\n"
                    .to_string()
            } else {
                format!("cat <<'EOF'\n{MODEL_LIST_JSON}\nEOF\nexit 0\n")
            };
            std::fs::write(
                &python,
                format!(
                    "#!/bin/sh\n\
                     [ \"$1 $2 $3 $4\" = \"-m coperceive model list\" ] || {{ echo \"bad argv: $*\" >&2; exit 9; }}\n\
                     if [ \"${{5:-}}\" = --json ]; then\n{json}fi\n\
                     echo \"griffin  entry-point  coperceive_griffin_model:MANIFEST\"\n"
                ),
            )
            .unwrap();
            std::fs::set_permissions(&python, std::fs::Permissions::from_mode(0o755)).unwrap();
            python.to_string_lossy().to_string()
        };
        let runner = LocalShell { cwd: root.clone() };
        let mut profile = entry().profile;
        profile.environment.coperceive_command =
            vec![fake("python", false), "-m".to_string(), "coperceive".to_string()];
        let plugins = read_builtin_plugins(&runner, &profile).expect("model list --json under sh");
        assert_eq!(plugins.len(), 1, "{plugins:?}");
        assert_eq!(plugins[0].name, "griffin");
        // The older coperceive: argparse refuses --json, the plain listing names griffin.
        profile.environment.coperceive_command =
            vec![fake("python-old", true), "-m".to_string(), "coperceive".to_string()];
        let error = read_builtin_plugins(&runner, &profile).expect_err("too old");
        assert_eq!(error, MODEL_LIST_TOO_OLD);
        let listed = list_builtin_pipelines(&runner, &profile);
        assert_eq!(listed.iter().map(|p| p.name.as_str()).collect::<Vec<_>>(), ["griffin"]);
        assert!(!root.join("pwned").exists(), "a word of the command ran as a command");
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn only_argparses_refusal_of_json_means_too_old() {
        struct Fails(&'static str);
        impl RemoteRunner for Fails {
            fn run(&self, _cmd: &str, _t: Duration, _stdin: Option<&str>) -> Result<CommandResult, String> {
                Ok(CommandResult { success: false, stdout: String::new(), stderr: self.0.to_string() })
            }
        }
        // A broken command whose error text happens to echo "--json" is not "too old".
        let error = read_builtin_plugins(
            &Fails("sh: 1: /home/u/my: not found\nenvs/bin/python -m coperceive model list --json\n"),
            &entry().profile,
        )
        .expect_err("broken");
        assert_ne!(error, MODEL_LIST_TOO_OLD);
        assert!(error.contains("/home/u/my: not found"), "{error}");
        let error = read_builtin_plugins(
            &Fails("usage: coperceive [-h] ...\ncoperceive: error: unrecognized arguments: --json\n"),
            &entry().profile,
        )
        .expect_err("too old");
        assert_eq!(error, MODEL_LIST_TOO_OLD);
    }

    #[test]
    fn a_gpu_model_without_gpu_discovery_requests_a_gpu_and_the_manifest_cuda_modules() {
        // cluster-b: a modules profile (python/3.12) with no discovery at all.
        let cluster = GriffinCluster::new();
        let plan = build_run_plan(&cluster, &entry(), &griffin_request()).expect("build");
        let saved = save_run_plan(&cluster, &entry(), &plan, &griffin_request()).expect("save");
        for plan in [&plan, &saved] {
            assert_eq!(plan.gres.as_deref(), Some("gpu:1"), "as plan_sbatch: --gres=gpu:1");
            assert_eq!(
                plan.environment.modules,
                ["python/3.12", "nvidia/cuda-11.8", "nvidia/cuda-11.x_cudnn-8.6"]
            );
        }
    }

    #[test]
    fn a_gpu_model_on_a_partition_discovery_found_without_gpus_is_refused() {
        let cluster = GriffinCluster::new();
        let entry = entry_with_discovered("main-cpu", &[]);
        let error = build_run_plan(&cluster, &entry, &griffin_request()).expect_err("no GPU");
        assert!(error.contains("needs a GPU"), "{error}");
        assert!(error.contains("main-cpu"), "{error}");
        // With discovered GPUs it is gpu:1, as plan_sbatch writes; the manifest's
        // --constraint picks the type.
        let entry = entry_with_discovered("gpu-a40", &["a40"]);
        let plan = build_run_plan(&cluster, &entry, &griffin_request()).expect("build");
        assert_eq!(plan.gres.as_deref(), Some("gpu:1"));
    }

    /// cluster-b's Griffin with `slurm_constraint: "a40"` on every model, as the real
    /// manifest pins it.
    fn model_list_with_a40_constraint() -> String {
        MODEL_LIST_JSON.replace("\"gpu\": true,", "\"gpu\": true, \"slurm_constraint\": \"a40\",")
    }

    #[test]
    fn the_time_estimate_is_the_clis_time_limit() {
        // coperceive/models/slurm.py time_limit: Griffin over the 1,490-sample fixture.
        assert_eq!(cli_time_limit(0.394, 700.0, 100.0, 1490), "01:00:00");
        assert_eq!(cli_time_limit(0.01, 0.0, 0.0, 5), "00:20:00");
        assert_eq!(cli_time_limit(1.0, 0.0, 0.0, 601), "00:40:00");
        assert_eq!(cli_time_limit(10.0, 0.0, 0.0, 9000), "50:00:00");
        for (text, seconds) in [
            ("30", Some(1800)),
            ("01:00:00", Some(3600)),
            ("1-00:00:00", Some(86_400)),
            ("2-12", Some(216_000)),
            ("1-01:30", Some(91_800)),
            ("infinite", None),
            ("UNLIMITED", None),
        ] {
            assert_eq!(slurm_seconds(text), seconds, "{text}");
        }
    }

    #[test]
    fn a_builtin_plan_carries_the_clis_time_estimate_and_its_pythons_coperceive() {
        let cluster = GriffinCluster::new();
        let mut entry = entry_with_discovered("gpu-a40", &["a40"]);
        entry.discovered.as_mut().unwrap().partitions[0].max_time = Some("2-00:00:00".to_string());
        let built = build_run_plan(&cluster, &entry, &griffin_request()).expect("build");
        let saved = save_run_plan(&cluster, &entry, &built, &griffin_request()).expect("save");
        for plan in [&built, &saved] {
            assert_eq!(plan.time_estimate.as_deref(), Some("01:00:00"));
            assert_eq!(plan.partition_max_time.as_deref(), Some("2-00:00:00"));
            assert_eq!(
                plan.run_interpreter.as_ref().and_then(|found| found.package_root.as_deref()),
                Some("/data/code/coperceive")
            );
            let facts = plan.checks.iter().find(|item| item.name == "python-facts").expect("check");
            assert!(facts.detail.contains("1490 samples"), "{}", facts.detail);
        }
        // The python ran with the scenes folder and its own module, every value a parameter.
        let calls = cluster.calls.lock().unwrap();
        let facts = calls.iter().find(|call| call.contains("coperceive-check:plan-python")).unwrap();
        assert!(facts.contains(" /data/scenes/val "), "{facts}");
        // Read-only and time-bounded on the login node: no bytecode written, and the same
        // limit as the split listing, the other probe that reads the whole scenes tree.
        assert!(facts.contains("\"$1\" -B -c "), "{facts}");
        assert_eq!(*cluster.facts_timeout.lock().unwrap(), Some(LISTING_TIMEOUT));
        // A client plan's estimate is never kept: save reads the cluster again.
        drop(calls);
        let mut edited = built.clone();
        edited.time_estimate = Some("99:00:00".to_string());
        let saved = save_run_plan(&cluster, &entry, &edited, &griffin_request()).expect("save");
        assert_eq!(saved.time_estimate.as_deref(), Some("01:00:00"));
    }

    #[test]
    fn a_builtin_plan_logs_to_the_clusters_home_logs_as_the_cli_does() {
        // coperceive model run --slurm: --slurm-logs defaults to ~/logs (commands.py).
        let cluster = GriffinCluster::new();
        let built = build_run_plan(&cluster, &entry(), &griffin_request()).expect("build");
        let mut edited = built.clone();
        edited.logs_dir = Some("/tmp/elsewhere".to_string());
        let saved = save_run_plan(&cluster, &entry(), &edited, &griffin_request()).expect("save");
        for plan in [&built, &saved] {
            assert_eq!(plan.logs_dir.as_deref(), Some("/home/bob/logs"), "never the client's");
        }
        // A custom pipeline keeps its logs in results_root.
        let custom = build_run_plan(&checked_runner(), &entry(), &request()).expect("custom");
        assert_eq!(custom.logs_dir, None);
    }

    #[test]
    fn a_python_that_cannot_import_coperceive_or_names_a_bad_root_is_refused() {
        let mut cluster = GriffinCluster::new();
        cluster.python_facts = String::new();
        let error = build_run_plan(&cluster, &entry(), &griffin_request()).expect_err("no import");
        assert!(error.contains("could not import coperceive"), "{error}");
        cluster.python_facts = "ROOT=/data/x\u{1b}[2J\nSAMPLES=1\n".to_string();
        let error = build_run_plan(&cluster, &entry(), &griffin_request()).expect_err("control");
        assert!(error.contains("PYTHONPATH"), "{error}");
        // Scenes that cannot be counted: no estimate, said so, the walltime is entered.
        cluster.python_facts = "ROOT=/data/code/coperceive\n".to_string();
        let plan = build_run_plan(&cluster, &entry(), &griffin_request()).expect("build");
        assert_eq!(plan.time_estimate, None);
        let facts = plan.checks.iter().find(|item| item.name == "python-facts").unwrap();
        assert!(facts.detail.contains("enter the walltime"), "{}", facts.detail);
    }

    #[test]
    fn a_mixed_partition_gets_gpu_1_and_the_manifest_constraint_picks_the_type() {
        // sinfo lists a100 first: a typed gpu:a100:1 next to --constraint="a40" fits no node.
        let mut cluster = GriffinCluster::new();
        cluster.model_list = Ok(model_list_with_a40_constraint());
        let entry = entry_with_discovered("gpu-mixed", &["a100", "a40"]);
        let plan = build_run_plan(&cluster, &entry, &griffin_request()).expect("build");
        let saved = save_run_plan(&cluster, &entry, &plan, &griffin_request()).expect("save");
        for plan in [&plan, &saved] {
            assert_eq!(plan.gres.as_deref(), Some("gpu:1"), "as plan_sbatch: --gres=gpu:1");
            assert_eq!(
                plan.model_resources.as_ref().and_then(|r| r.constraint.as_deref()),
                Some("a40")
            );
        }
    }

    #[test]
    fn a_model_whose_manifest_says_gpu_false_asks_for_no_gpu() {
        let cluster = GriffinCluster::new();
        let mut req = griffin_request();
        req.model = "weights-from-option".to_string();
        req.ckpt_root = String::new();
        req.checkpoint = "/data/code/checkpoints/vehicle.pth".to_string();
        for entry in [entry(), entry_with_discovered("gpu-a40", &["a40"])] {
            let plan = build_run_plan(&cluster, &entry, &req).expect("build");
            let saved = save_run_plan(&cluster, &entry, &plan, &req).expect("save");
            for plan in [&plan, &saved] {
                assert_eq!(plan.gres, None, "plan_sbatch writes no --gres for gpu: false");
            }
        }
    }

    #[test]
    fn a_custom_pipeline_keeps_the_partitions_typed_gpu() {
        let entry = entry_with_discovered("gpu-mixed", &["a100", "a40"]);
        let plan = build_run_plan(&checked_runner(), &entry, &request()).expect("plan");
        assert_eq!(plan.gres.as_deref(), Some("gpu:a100:1"));
    }

    // -- Round 2 (review findings) ------------------------------------------------------

    /// What NewEvaluation.tsx sends for a listed built-in pipeline (`request` in
    /// `checkPlan`): the listing's own spec, source and manifest, which is None when the
    /// cluster could not report one.
    fn ui_request(pipeline: &PipelineEntry) -> RunPlanRequest {
        let mut req = griffin_request();
        req.pipeline = pipeline.spec.clone();
        req.pipeline_source = pipeline.source.clone();
        req.registration_path = pipeline.registration_path.clone();
        req.plugin_manifest = pipeline.plugin_manifest.clone();
        req
    }

    /// A cluster whose coperceive predates `model list --json`: the JSON call fails with
    /// argparse's message, the plain listing names griffin.
    struct TooOld(GriffinCluster);

    impl RemoteRunner for TooOld {
        fn run(&self, cmd: &str, t: Duration, stdin: Option<&str>) -> Result<CommandResult, String> {
            if cmd.contains("model list 2>/dev/null") {
                return Ok(CommandResult {
                    success: true,
                    stdout: "griffin  entry-point  coperceive_griffin_model:MANIFEST\n".to_string(),
                    stderr: String::new(),
                });
            }
            self.0.run(cmd, t, stdin)
        }
    }

    #[test]
    fn a_too_old_cluster_is_named_for_the_request_the_ui_sends() {
        let mut cluster = GriffinCluster::new();
        cluster.model_list = Err(
            "usage: coperceive [-h] ...\ncoperceive: error: unrecognized arguments: --json\n"
                .to_string(),
        );
        let cluster = TooOld(cluster);
        let pipelines = list_builtin_pipelines(&cluster, &entry().profile);
        let griffin = pipelines.first().expect("griffin is listed by name");
        assert_eq!(griffin.plugin_manifest, None);
        let error = build_run_plan(&cluster, &entry(), &ui_request(griffin)).expect_err("too old");
        assert!(error.contains("too old"), "{error}");
        assert!(error.contains("Update coperceive on the cluster"), "{error}");
    }

    #[test]
    fn a_plugin_whose_manifest_cannot_be_read_shows_the_clusters_reason() {
        let mut cluster = GriffinCluster::new();
        cluster.model_list = Ok(r#"{"format": "coperceive-model-list", "format_version": 1,
            "plugins": [{"name": "griffin", "source": "entry-point",
              "location": "coperceive_griffin_model:MANIFEST", "manifest": null,
              "error": "RuntimeError: CUDA is not available on the login node",
              "modules": [], "models": {}}]}"#
            .to_string());
        let pipelines = list_builtin_pipelines(&cluster, &entry().profile);
        let griffin = pipelines.first().expect("griffin is listed with its error");
        let error = build_run_plan(&cluster, &entry(), &ui_request(griffin)).expect_err("broken");
        assert!(error.contains("no pipeline manifest"), "{error}");
        assert!(error.contains("CUDA is not available on the login node"), "{error}");
    }

    #[test]
    fn a_listed_griffin_plans_through_the_ui_request_shape() {
        let cluster = GriffinCluster::new();
        let pipelines = list_builtin_pipelines(&cluster, &entry().profile);
        let plan = build_run_plan(&cluster, &entry(), &ui_request(&pipelines[0])).expect("build");
        assert!(plan.is_saveable(), "{:?}", plan.checks);
    }

    #[test]
    fn a_builtin_plan_records_what_the_manifest_asks_of_the_job() {
        // plan_sbatch writes --cpus-per-task/--mem from the manifest and excludes the
        // closure's transient artefacts from the work copy (coperceive/models/slurm.py).
        let cluster = GriffinCluster::new();
        let built = build_run_plan(&cluster, &entry(), &griffin_request()).expect("build");
        let saved = save_run_plan(&cluster, &entry(), &built, &griffin_request()).expect("save");
        for plan in [&built, &saved] {
            let json = serde_json::to_value(plan).unwrap();
            assert_eq!(
                json["model_resources"],
                serde_json::json!({"gpu": true, "cpus": 8, "memory_gb": 64, "transient_artifacts": []}),
                "{json}"
            );
        }
        let mut req = griffin_request();
        req.model = "fusion".to_string();
        let built = build_run_plan(&cluster, &entry(), &req).expect("build fusion");
        let json = serde_json::to_value(&built).unwrap();
        assert_eq!(
            json["model_resources"]["transient_artifacts"],
            serde_json::json!(["drone-side/track_query"]),
            "{json}"
        );
    }

    #[test]
    fn a_builtin_plan_keeps_the_work_directory_in_scratch() {
        // The CLI runs `--work "$SCRATCH/work"` and copies only logs and artefacts home;
        // Griffin's work holds a toolkit copy and the converted dataset.
        let cluster = GriffinCluster::new();
        let built = build_run_plan(&cluster, &entry(), &griffin_request()).expect("build");
        let saved = save_run_plan(&cluster, &entry(), &built, &griffin_request()).expect("save");
        for plan in [&built, &saved] {
            let at = plan.argv.iter().position(|word| word == "--work").expect("--work");
            assert_eq!(plan.argv[at + 1], "$SCRATCH_DIR/work");
            assert!(!plan.argv.iter().any(|word| word.starts_with(&plan.output_dir) && word.ends_with("/work")));
        }
    }

    #[test]
    fn a_venv_profile_loads_the_manifest_modules_too() {
        // plan_sbatch writes `module purge` and the manifest's modules whatever the
        // interpreter is (coperceive/models/slurm.py); a venv profile ran Griffin without CUDA.
        let mut entry = entry();
        entry.profile.environment = EnvironmentSetup {
            kind: EnvironmentKind::Venv,
            interpreter: Some("/data/code/venv/bin/python".to_string()),
            coperceive_command: vec![
                "/data/code/venv/bin/python".to_string(),
                "-m".to_string(),
                "coperceive".to_string(),
            ],
            ..EnvironmentSetup::default()
        };
        let cluster = GriffinCluster::new();
        let built = build_run_plan(&cluster, &entry, &griffin_request()).expect("build");
        let saved = save_run_plan(&cluster, &entry, &built, &griffin_request()).expect("save");
        for plan in [&built, &saved] {
            assert_eq!(plan.environment.kind, EnvironmentKind::Venv);
            assert_eq!(plan.environment.modules, ["nvidia/cuda-11.8", "nvidia/cuda-11.x_cudnn-8.6"]);
        }
        let script = crate::job_submit::render_run_script(
            &entry.profile,
            &saved,
            &crate::job_submit::SubmitOptions::default(),
        )
        .expect("render")
        .script;
        assert!(
            script.contains("module purge\nmodule load nvidia/cuda-11.8\nmodule load nvidia/cuda-11.x_cudnn-8.6\n"),
            "{script}"
        );
    }

    /// A venv on this cluster's /software/python base, as `pyvenv.cfg` writes it.
    fn venv_answer(python: &str, home: &str, version: &str) -> String {
        let venv = python.trim_end_matches("/bin/python");
        format!(
            "PYTHON={python}\nPYVENV_CFG={venv}/pyvenv.cfg\nPYVENV_LINE=home = {home}\n\
             PYVENV_LINE=version = {version}\n"
        )
    }

    /// The form's submit: its walltime is the plan's estimate, or one the user entered.
    fn render_saved(entry: &ServerEntry, plan: &RunPlan) -> String {
        let walltime = plan.time_estimate.clone().unwrap_or_else(|| "02:00:00".to_string());
        crate::job_submit::render_run_script(
            &entry.profile,
            plan,
            &crate::job_submit::SubmitOptions { walltime, ..Default::default() },
        )
        .expect("render")
        .script
    }

    #[test]
    fn a_venv_interpreter_loads_its_python_module_right_after_module_purge() {
        // plan_sbatch reloads python/<v> from the venv's pyvenv.cfg after `module purge`
        // (coperceive/models/slurm.py interpreter_module); without it a venv on a module's
        // python cannot start on the compute node (libpython not found).
        let mut entry = entry();
        entry.profile.environment = EnvironmentSetup {
            kind: EnvironmentKind::Venv,
            interpreter: Some("/data/code/venv/bin/python".to_string()),
            coperceive_command: vec![
                "/data/code/venv/bin/python".to_string(),
                "-m".to_string(),
                "coperceive".to_string(),
            ],
            ..EnvironmentSetup::default()
        };
        let cluster = GriffinCluster::new().with_interpreter(
            "/data/code/venv/bin/python",
            &venv_answer("/data/code/venv/bin/python", "/software/python/3.9.18/bin", "3.9.18"),
        );
        let built = build_run_plan(&cluster, &entry, &griffin_request()).expect("build");
        let saved = save_run_plan(&cluster, &entry, &built, &griffin_request()).expect("save");
        let script = render_saved(&entry, &saved);
        assert!(
            script.contains(
                "module purge\nmodule load python/3.9.18\nmodule load nvidia/cuda-11.8\n\
                 module load nvidia/cuda-11.x_cudnn-8.6\n"
            ),
            "{script}"
        );
        // On a modules profile too: the interpreter's module first, then the profile's own
        // modules and the manifest's, each once.
        let mut modules = self::entry();
        modules.profile.environment.coperceive_command = vec![
            "/data/code/venv/bin/python".to_string(),
            "-m".to_string(),
            "coperceive".to_string(),
        ];
        modules.profile.environment.modules = vec!["nvidia/cuda-11.8".to_string()];
        let built = build_run_plan(&cluster, &modules, &griffin_request()).expect("build");
        let saved = save_run_plan(&cluster, &modules, &built, &griffin_request()).expect("save");
        let script = render_saved(&modules, &saved);
        assert!(
            script.contains(
                "module purge\nmodule load python/3.9.18\nmodule load nvidia/cuda-11.8\n\
                 module load nvidia/cuda-11.x_cudnn-8.6\n"
            ),
            "{script}"
        );
    }

    #[test]
    fn a_modules_profiles_other_python_module_never_swaps_the_interpreters() {
        // Lmod keeps one module per name: the profile's python/3.11 loaded after the venv's
        // python/3.9.18 would swap it, and the 3.9 venv would run on the wrong base.
        // plan_sbatch loads only the interpreter's python module.
        let mut entry = entry();
        entry.profile.environment.coperceive_command = vec![
            "/data/code/venv/bin/python".to_string(),
            "-m".to_string(),
            "coperceive".to_string(),
        ];
        entry.profile.environment.modules =
            vec!["python/3.11".to_string(), "gcc/12".to_string(), "python/3.9.18".to_string()];
        let cluster = GriffinCluster::new().with_interpreter(
            "/data/code/venv/bin/python",
            &venv_answer("/data/code/venv/bin/python", "/software/python/3.9.18/bin", "3.9.18"),
        );
        let built = build_run_plan(&cluster, &entry, &griffin_request()).expect("build");
        let saved = save_run_plan(&cluster, &entry, &built, &griffin_request()).expect("save");
        for plan in [&built, &saved] {
            assert_eq!(
                plan.environment.modules,
                ["python/3.9.18", "gcc/12", "nvidia/cuda-11.8", "nvidia/cuda-11.x_cudnn-8.6"]
            );
        }
        let script = render_saved(&entry, &saved);
        assert!(
            script.contains(
                "module purge\nmodule load python/3.9.18\nmodule load gcc/12\n\
                 module load nvidia/cuda-11.8\nmodule load nvidia/cuda-11.x_cudnn-8.6\n"
            ),
            "{script}"
        );
        assert!(!script.contains("module load python/3.11"), "{script}");
        let comment = script
            .lines()
            .find(|line| line.starts_with('#') && line.contains("python/3.11"))
            .unwrap_or_else(|| panic!("no comment names the dropped module:\n{script}"));
        assert!(comment.contains("python/3.9.18"), "{comment}");
        // Without a known interpreter module the profile's python module stays.
        let unknown = GriffinCluster::new().with_interpreter(
            "/data/code/venv/bin/python",
            "PYTHON=/data/code/venv/bin/python\nPYVENV_CFG=\n",
        );
        let built = build_run_plan(&unknown, &entry, &griffin_request()).expect("build");
        assert_eq!(built.environment.modules[0], "python/3.11");
        assert!(!render_saved(&entry, &built).lines().any(|line| line.starts_with('#') && line.contains("python/3.11")));
    }

    #[test]
    fn the_interpreter_module_follows_the_clis_pyvenv_rule() {
        // home = /software/python/<v>/bin wins; else the version line; else none.
        for (answer, module) in [
            (
                venv_answer("/data/code/venv/bin/python", "/software/python/3.12/bin", "3.12.4"),
                Some("python/3.12"),
            ),
            (
                venv_answer("/data/code/venv/bin/python", "/usr/bin", "3.11.2"),
                Some("python/3.11.2"),
            ),
            ("PYTHON=/data/code/venv/bin/python\nPYVENV_CFG=\n".to_string(), None),
        ] {
            let cluster =
                GriffinCluster::new().with_interpreter("/data/code/venv/bin/python", &answer);
            let mut entry = entry();
            entry.profile.environment.modules = vec!["nvidia/cuda-11.8".to_string()];
            entry.profile.environment.coperceive_command = vec![
                "/data/code/venv/bin/python".to_string(),
                "-m".to_string(),
                "coperceive".to_string(),
            ];
            let built = build_run_plan(&cluster, &entry, &griffin_request()).expect("build");
            let first = built.environment.modules.first().cloned();
            match module {
                Some(module) => assert_eq!(first.as_deref(), Some(module), "{answer}"),
                None => assert!(
                    !built.environment.modules.iter().any(|name| name.starts_with("python/")),
                    "{answer}: {:?}",
                    built.environment.modules
                ),
            }
        }
        // A version that is not a plain module name is refused, never written into the job.
        let cluster = GriffinCluster::new().with_interpreter(
            "/apps/python/3.12/bin/python",
            "PYTHON=/apps/python/3.12/bin/python\nPYVENV_CFG=/apps/python/3.12/pyvenv.cfg\n\
             PYVENV_LINE=version = 3.12; touch /tmp/x\n",
        );
        let mut entry = entry();
        entry.profile.environment.coperceive_command = vec![
            "/apps/python/3.12/bin/python".to_string(),
            "-m".to_string(),
            "coperceive".to_string(),
        ];
        let error =
            build_run_plan(&cluster, &entry, &griffin_request()).expect_err("unsafe version");
        assert!(error.contains("pyvenv.cfg"), "{error}");
    }

    #[test]
    #[cfg(unix)]
    fn the_interpreter_check_reads_pyvenv_cfg_and_the_shebang_on_a_real_shell() {
        // The check itself, run by /bin/sh on real files: a venv's bin/python (a symlink),
        // its pyvenv.cfg, and console scripts with and without a python beside them.
        let root = std::env::temp_dir().join(format!("cp-interp-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&root);
        let venv = root.join("venv");
        std::fs::create_dir_all(venv.join("bin")).unwrap();
        std::fs::write(
            venv.join("pyvenv.cfg"),
            "home = /software/python/3.9.18/bin\ninclude-system-site-packages = false\n\
             version = 3.9.18\nversion_info = 3.9.18.final.0\n",
        )
        .unwrap();
        std::os::unix::fs::symlink("/bin/sh", venv.join("bin/python")).unwrap();
        let pipx = root.join("bin");
        std::fs::create_dir_all(&pipx).unwrap();
        let script = pipx.join("coperceive");
        std::fs::write(&script, format!("#!{}/bin/python\nimport sys\n", venv.display()))
            .unwrap();
        let run = |program: &str, mode: &str| {
            let output = std::process::Command::new("/bin/sh")
                .args(["-c", &interpreter_check_script(program, mode)])
                .output()
                .unwrap();
            assert!(output.status.success(), "{output:?}");
            String::from_utf8_lossy(&output.stdout).to_string()
        };
        let python = format!("{}/bin/python", venv.display());
        let found = run(&python, "python");
        assert!(found.contains("PYVENV_LINE=home = /software/python/3.9.18/bin\n"), "{found}");
        assert!(found.contains("PYVENV_LINE=version = 3.9.18\n"), "{found}");
        assert!(!found.contains("version_info"), "{found}");
        assert_eq!(pyvenv_module(&found).unwrap(), Some("python/3.9.18".to_string()));
        // No python beside the script: its shebang.
        let shebang = run(&script.display().to_string(), "script");
        assert!(shebang.contains("BESIDE=\n"), "{shebang}");
        assert!(shebang.contains(&format!("SHEBANG=#!{python}\n")), "{shebang}");
        // The script inside the venv: the python beside it.
        std::fs::copy(&script, venv.join("bin/coperceive")).unwrap();
        let beside = run(&format!("{}/bin/coperceive", venv.display()), "script");
        assert!(beside.contains(&format!("BESIDE={python}\n")), "{beside}");
        assert!(beside.contains("PYVENV_LINE=home = /software/python/3.9.18/bin\n"), "{beside}");
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn a_dependency_loop_in_the_manifest_is_refused_instead_of_hanging() {
        // model_closure marks a model as seen before its dependencies; a loop (A -> B -> A,
        // or a model that depends on itself) must end plan building with a clear message.
        let looped = MODEL_LIST_JSON
            .replace(
                r#""drone-side": {"cpus": 8, "depends_on": []"#,
                r#""drone-side": {"cpus": 8, "depends_on": ["fusion"]"#,
            )
            .replace(
                r#""vehicle-side": {"cpus": 8, "depends_on": []"#,
                r#""vehicle-side": {"cpus": 8, "depends_on": ["vehicle-side"]"#,
            );
        for (model, names) in [
            ("fusion", ["fusion", "drone-side"]),
            ("vehicle-side", ["vehicle-side", "vehicle-side"]),
        ] {
            let (sender, receiver) = std::sync::mpsc::channel();
            let list = looped.clone();
            std::thread::spawn(move || {
                let mut cluster = GriffinCluster::new();
                cluster.model_list = Ok(list);
                let mut req = griffin_request();
                req.model = model.to_string();
                let _ = sender.send(build_run_plan(&cluster, &entry(), &req));
            });
            let result = receiver.recv_timeout(Duration::from_secs(2)).unwrap_or_else(|_| {
                panic!("building a plan for {model} did not finish: the dependency loop hangs")
            });
            let error = result.expect_err("a dependency loop");
            assert!(error.contains("loop"), "{error}");
            for name in names {
                assert!(error.contains(name), "{error}");
            }
        }
    }

    /// cluster-b with Griffin's `slurm_constraint` in the listing, on `partition` with
    /// what discovery found there (`None`: no discovery).
    fn constrained(partition: &str, gpu_types: Option<&[&str]>) -> (ServerEntry, GriffinCluster) {
        let mut entry = match gpu_types {
            Some(types) => entry_with_discovered(partition, types),
            None => entry(),
        };
        entry.profile.defaults.partition = Some(partition.to_string());
        let mut cluster = GriffinCluster::new();
        cluster.model_list = Ok(MODEL_LIST_JSON.replace(
            r#""vehicle-side": {"cpus": 8,"#,
            r#""vehicle-side": {"slurm_constraint": "a40", "cpus": 8,"#,
        ));
        (entry, cluster)
    }

    #[test]
    fn the_manifests_gpu_constraint_is_written_in_every_discovery_state() {
        // plan_sbatch writes resources.slurm_constraint as #SBATCH --constraint="a40" after
        // --time (Griffin's torch ships SASS for sm_86/sm_80 only). The GPU request is
        // gpu:1 in every state, as plan_sbatch writes it: the constraint picks the type.
        for (partition, types, gres) in [
            ("main", None, "gpu:1"),
            ("gpu-a40", Some(&["a40"][..]), "gpu:1"),
            ("gpu", Some(&["gpu"][..]), "gpu:1"),
            ("gpu-mixed", Some(&["a100", "a40"][..]), "gpu:1"),
        ] {
            let (entry, cluster) = constrained(partition, types);
            let built = build_run_plan(&cluster, &entry, &griffin_request()).expect("build");
            let saved =
                save_run_plan(&cluster, &entry, &built, &griffin_request()).expect("save");
            let script = render_saved(&entry, &saved);
            assert!(script.contains(&format!("#SBATCH --gres={gres}\n")), "{partition}: {script}");
            let lines: Vec<&str> = script.lines().collect();
            let time = lines.iter().position(|line| line.starts_with("#SBATCH --time=")).unwrap();
            assert_eq!(lines[time + 1], "#SBATCH --constraint=\"a40\"", "{partition}: {script}");
            assert_eq!(script.matches("--constraint").count(), 1, "{script}");
        }
        // A model without one gets no constraint line.
        let cluster = GriffinCluster::new();
        let built = build_run_plan(&cluster, &entry(), &griffin_request()).expect("build");
        let saved = save_run_plan(&cluster, &entry(), &built, &griffin_request()).expect("save");
        assert!(!render_saved(&entry(), &saved).contains("--constraint"));
    }

    #[test]
    fn a_console_script_without_a_python_beside_it_is_recorded_with_its_shebang() {
        // pipx (~/.local/bin/coperceive, a symlink into its own venv) and /usr/local/bin
        // have no python beside the script: environment.json uses the interpreter the
        // script's shebang names, read when the plan is checked, and its venv's module.
        let pipx_python = "/data/code/pipx/venvs/coperceive/bin/python";
        let launcher = format!("'''exec' \"{pipx_python}\" \"$0\" \"$@\"");
        for (shebang, line2) in [
            (format!("#!{pipx_python}"), String::new()),
            // pip's launcher for a path too long for a shebang line.
            ("#!/bin/sh".to_string(), launcher),
        ] {
            let mut entry = entry();
            entry.profile.environment.coperceive_command =
                vec!["/home/bob/.local/bin/coperceive".to_string()];
            let cluster = GriffinCluster::new()
                .with_interpreter(
                    "/home/bob/.local/bin/coperceive",
                    &format!("BESIDE=\nSHEBANG={shebang}\nSHEBANG2={line2}\n"),
                )
                .with_interpreter(
                    pipx_python,
                    &venv_answer(pipx_python, "/software/python/3.11.4/bin", "3.11.4"),
                );
            let built = build_run_plan(&cluster, &entry, &griffin_request()).expect("build");
            let saved =
                save_run_plan(&cluster, &entry, &built, &griffin_request()).expect("save");
            let script = render_saved(&entry, &saved);
            assert!(
                script.contains(&format!(
                    "\n{pipx_python} - \"$OUT_DIR/{}/environment.json\" <<'PY' || true\n",
                    saved.id
                )),
                "{script}"
            );
            assert!(!script.contains("/home/bob/.local/bin/python"), "{script}");
            assert!(script.contains("module purge\nmodule load python/3.11.4\n"), "{script}");
        }
        // `#!/usr/bin/env python3` names no interpreter before the job runs: python3, said so.
        let mut entry = entry();
        entry.profile.environment.coperceive_command =
            vec!["/usr/local/bin/coperceive".to_string()];
        let cluster = GriffinCluster::new().with_interpreter(
            "/usr/local/bin/coperceive",
            "BESIDE=\nSHEBANG=#!/usr/bin/env python3\nSHEBANG2=\n",
        );
        let built = build_run_plan(&cluster, &entry, &griffin_request()).expect("build");
        let saved = save_run_plan(&cluster, &entry, &built, &griffin_request()).expect("save");
        let script = render_saved(&entry, &saved);
        let lines: Vec<&str> = script.lines().collect();
        let capture = lines
            .iter()
            .position(|line| {
                line.ends_with(&format!("\"$OUT_DIR/{}/environment.json\" <<'PY' || true", saved.id))
            })
            .expect("capture line");
        assert!(lines[capture].starts_with("python3 - "), "{script}");
        assert!(
            lines[capture - 1].starts_with('#') && lines[capture - 1].contains("python3"),
            "{script}"
        );
        assert!(!script.contains("/usr/local/bin/python "), "{script}");
    }

    #[test]
    fn the_builtin_contract_check_runs_model_run_with_its_own_flags() {
        // `coperceive model run --help` has no --max-frames and no --output-dir, and
        // model run writes run.json and boxes.parquet, not predictions.jsonl.
        let cluster = GriffinCluster::new();
        let built = build_run_plan(&cluster, &entry(), &griffin_request()).expect("build");
        let saved = save_run_plan(&cluster, &entry(), &built, &griffin_request()).expect("save");
        for script in [&built.contract_check_script, &saved.contract_check_script] {
            for absent in ["--max-frames", "--output-dir", "predictions.jsonl", "metrics.json"] {
                assert!(!script.contains(absent), "{absent}:\n{script}");
            }
            let run = script
                .lines()
                .find(|line| !line.starts_with('#') && line.contains(" model run "))
                .unwrap_or_else(|| panic!("no model run line:\n{script}"));
            assert!(run.contains("--out \"$OUT_DIR\""), "{run}");
            assert!(run.contains("--work \"$OUT_DIR/work\""), "{run}");
            assert!(run.contains(&format!("--run-id {}", built.id)) || run.contains(&format!("--run-id {}", saved.id)), "{run}");
            assert!(script.contains("run.json"), "{script}");
            assert!(script.contains("boxes.parquet"), "{script}");
            assert!(script.contains("no frame limit"), "{script}");
        }
    }

    /// Runs the plan's path checks with this machine's shell (the fixture `realpath`
    /// stands in for GNU `realpath -m`) and answers the rest as GriffinCluster does.
    struct LocalPaths {
        cluster: GriffinCluster,
        timeouts: Mutex<Vec<(String, Duration)>>,
    }

    impl RemoteRunner for LocalPaths {
        fn run(&self, cmd: &str, timeout: Duration, stdin: Option<&str>) -> Result<CommandResult, String> {
            if cmd.contains("coperceive-check:plan-path") {
                self.timeouts.lock().unwrap().push((cmd.to_string(), timeout));
                let bin = concat!(env!("CARGO_MANIFEST_DIR"), "/tests/fixtures/bin");
                let output = std::process::Command::new("/bin/sh")
                    .args(["-c", cmd])
                    .env("PATH", format!("{bin}:/usr/bin:/bin"))
                    .output()
                    .map_err(|error| error.to_string())?;
                return Ok(CommandResult {
                    success: output.status.success(),
                    stdout: String::from_utf8_lossy(&output.stdout).to_string(),
                    stderr: String::from_utf8_lossy(&output.stderr).to_string(),
                });
            }
            self.cluster.run(cmd, timeout, stdin)
        }
    }

    /// A real scenes folder under a fresh temp root, and cluster-b with that root as its
    /// data folder.
    fn local_scenes(tag: &str) -> (std::path::PathBuf, ServerEntry, RunPlanRequest) {
        let root = std::env::temp_dir().join(format!("cp-{tag}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&root);
        std::fs::create_dir_all(root.join("scenes")).unwrap();
        let root = root.canonicalize().unwrap();
        let mut entry = entry();
        entry.profile.data_roots = vec![root.to_string_lossy().to_string()];
        let mut req = griffin_request();
        req.split = root.join("scenes").to_string_lossy().to_string();
        req.raw_root = root.join("raw").to_string_lossy().to_string();
        req.media_root = req.raw_root.clone();
        req.ckpt_root = req.raw_root.clone();
        (root, entry, req)
    }

    #[test]
    #[cfg(unix)]
    fn a_scenes_folder_of_symlinks_is_fingerprinted_as_the_cli_reads_it() {
        // coperceive/viewdata.py lists scenes with child.is_dir(), which follows symlinks.
        let (root, entry, req) = local_scenes("symlinked-scenes");
        std::fs::create_dir_all(root.join("store/val00")).unwrap();
        std::fs::write(root.join("store/val00/manifest.json"), b"{}").unwrap();
        std::fs::write(root.join("store/frames.bin"), b"0123").unwrap();
        std::fs::create_dir_all(root.join("scenes/val01")).unwrap();
        std::os::unix::fs::symlink(root.join("store/val00"), root.join("scenes/val00")).unwrap();
        std::os::unix::fs::symlink(root.join("store/frames.bin"), root.join("scenes/val01/frames.bin"))
            .unwrap();
        let runner = LocalPaths { cluster: GriffinCluster::new(), timeouts: Mutex::new(Vec::new()) };
        let plan = build_run_plan(&runner, &entry, &req).expect("build");
        let _ = std::fs::remove_dir_all(&root);
        assert_eq!(plan.split.file_count, Some(2), "{:?}", plan.split);
        assert!(plan.is_saveable(), "{:?}", plan.checks);
    }

    #[test]
    #[cfg(unix)]
    fn an_unreadable_scenes_file_fails_the_fingerprint_instead_of_dropping_out() {
        use std::os::unix::fs::PermissionsExt;
        let (root, entry, req) = local_scenes("unreadable-scene");
        std::fs::create_dir_all(root.join("scenes/val00")).unwrap();
        std::fs::write(root.join("scenes/val00/manifest.json"), b"{}").unwrap();
        let locked = root.join("scenes/val00/frames.bin");
        std::fs::write(&locked, b"0123").unwrap();
        std::fs::set_permissions(&locked, std::fs::Permissions::from_mode(0o000)).unwrap();
        let runner = LocalPaths { cluster: GriffinCluster::new(), timeouts: Mutex::new(Vec::new()) };
        let result = build_run_plan(&runner, &entry, &req);
        std::fs::set_permissions(&locked, std::fs::Permissions::from_mode(0o644)).unwrap();
        let _ = std::fs::remove_dir_all(&root);
        let error = result.expect_err("an unreadable file must fail the fingerprint");
        assert!(error.contains("could not be read"), "{error}");
    }

    #[test]
    #[cfg(unix)]
    fn the_split_listing_has_its_own_timeout_and_no_other_path_is_walked() {
        let (root, mut entry, req) = local_scenes("split-timeout");
        std::fs::create_dir_all(root.join("scenes/val00")).unwrap();
        std::fs::write(root.join("scenes/val00/manifest.json"), b"{}").unwrap();
        // A sandbox container image is a directory too: its check must not list it.
        std::fs::create_dir_all(root.join("image.sif/usr/lib")).unwrap();
        std::fs::write(root.join("image.sif/usr/lib/libx.so"), b"x").unwrap();
        let image = root.join("image.sif").to_string_lossy().to_string();
        entry.profile.code_roots.push(root.to_string_lossy().to_string());
        entry.profile.environment = EnvironmentSetup {
            kind: EnvironmentKind::Container,
            container_image: Some(image.clone()),
            coperceive_command: entry.profile.environment.coperceive_command.clone(),
            ..EnvironmentSetup::default()
        };
        let runner = LocalPaths { cluster: GriffinCluster::new(), timeouts: Mutex::new(Vec::new()) };
        let _ = build_run_plan(&runner, &entry, &req);
        let timeouts = runner.timeouts.lock().unwrap().clone();
        let bin = concat!(env!("CARGO_MANIFEST_DIR"), "/tests/fixtures/bin");
        let image_check = std::process::Command::new("/bin/sh")
            .args(["-c", PATH_CHECK_BODY, "sh", &image])
            .env("PATH", format!("{bin}:/usr/bin:/bin"))
            .output()
            .unwrap();
        let _ = std::fs::remove_dir_all(&root);
        let split = timeouts
            .iter()
            .find(|(cmd, _)| cmd.contains(&req.split))
            .expect("the split was checked");
        assert!(split.1 > CHECK_TIMEOUT, "split check timeout {:?}", split.1);
        let image_values = parse_key_values(&String::from_utf8_lossy(&image_check.stdout));
        assert_eq!(image_values.get("ISDIR").map(String::as_str), Some("1"), "{image_values:?}");
        assert!(!image_values.contains_key("FILES"), "{image_values:?}");
        assert!(!image_values.contains_key("LISTING_SHA256"), "{image_values:?}");
    }

    #[test]
    fn argv_values_stay_separate_elements_even_when_hostile() {
        let mut values = BTreeMap::new();
        values.insert(
            "checkpoint".to_string(),
            serde_json::json!("/data/code/checkpoints/model.pt"),
        );
        values.insert(
            "split".to_string(),
            serde_json::json!("/data/code/splits/test.txt"),
        );
        values.insert("extra".to_string(), serde_json::json!("a b; touch /tmp/x"));
        let argv = build_pipeline_argv(&pipeline(), &values, "/data/results/runs/plan-1").unwrap();
        assert!(argv.contains(&"a b; touch /tmp/x".to_string()));
        assert!(!argv.iter().any(|element| element.contains("--extra a b")));
        // Every element is a plain string; the hostile value is exactly one element.
        assert_eq!(
            argv.iter()
                .filter(|element| *element == "a b; touch /tmp/x")
                .count(),
            1
        );
        // The output directory is passed as its own element pair.
        let len = argv.len();
        assert_eq!(argv[len - 2], "--output-dir");
        assert_eq!(argv[len - 1], "/data/results/runs/plan-1");
    }

    #[test]
    fn plan_builds_checks_sha_and_contract_script_for_module_environment() {
        let runner = checked_runner();
        let plan = build_run_plan(&runner, &entry(), &request()).expect("build plan");
        assert_eq!(plan.schema, RUN_PLAN_SCHEMA);
        assert!(plan.nothing_submitted);
        assert!(plan.checkpoint.sha256.as_deref() == Some("abc123"));
        assert!(plan.is_saveable(), "checks: {:?}", plan.checks);
        assert!(plan.checks.iter().all(|check| check.name != "contract"));
        assert!(plan
            .checks
            .iter()
            .any(|check| check.name == "env-modules" && check.ok));
        assert!(plan
            .contract_check_script
            .contains("#SBATCH --job-name=coperceive-check-"));
        assert!(plan
            .contract_check_script
            .contains("#SBATCH --partition=main-cpu"));
        assert!(plan
            .contract_check_script
            .contains("module load python/3.12"));
        assert!(plan.contract_check_script.contains("--max-frames 3"));
        assert!(plan.contract_check_script.contains("'a b; touch /tmp/x'"));
        assert!(plan.contract_check_script.contains("predictions.jsonl"));
        assert!(plan.contract_check_script.contains("metrics.json"));
        assert!(!plan.contract_check_script.contains("pip install"));
        assert!(plan.environment_note.contains("server profile"));
        // The plan never contains a secret.
        let json = serde_json::to_string(&plan).unwrap().to_lowercase();
        for forbidden in ["token", "password", "private_key", "secret"] {
            assert!(!json.contains(forbidden), "plan leaked {forbidden}");
        }
        // No sbatch or scancel was ever called.
        let calls = runner.calls();
        let calls = calls.lock().unwrap();
        assert!(!calls
            .iter()
            .any(|(command, _)| command.contains("sbatch") || command.contains("scancel")));
    }

    #[test]
    fn container_override_is_resolved_and_rendered() {
        let runner = checked_runner().with(
            "coperceive-check:plan-path",
            "EXISTS=1\nREADABLE=1\nEXEC=0\nISDIR=0\nREALPATH=/data/code/checkpoints/model.pt\nSHA256=abc123\n",
        );
        // The fake answers every path check the same way; the image existence check
        // is answered by the same response, so the container check passes.
        let mut override_setup = EnvironmentSetup::default();
        override_setup.kind = EnvironmentKind::Container;
        override_setup.container_image = Some("/data/code/containers/pipeline.sif".to_string());
        let mut request = request();
        request.environment_override = Some(override_setup);
        let plan = build_run_plan(&runner, &entry(), &request).expect("build plan");
        assert!(plan.environment_from_override);
        assert_eq!(plan.environment.kind, EnvironmentKind::Container);
        assert!(plan.environment_note.contains("overridden"));
        assert!(plan
            .contract_check_script
            .contains("apptainer exec --cleanenv"));
        assert!(plan
            .contract_check_script
            .contains("/data/code/containers/pipeline.sif"));
        assert!(!plan
            .contract_check_script
            .contains("module load python/3.12"));
    }

    #[test]
    fn path_outside_the_profile_roots_is_refused_before_any_remote_call() {
        let runner = FakeRunner::default();
        let mut request = request();
        request.checkpoint = "/etc/passwd".to_string();
        let error = build_run_plan(&runner, &entry(), &request).expect_err("must refuse");
        assert!(error.contains("outside this server"));
        assert!(runner.calls().lock().unwrap().is_empty());
    }

    #[test]
    fn symlink_escape_reported_by_the_cluster_is_refused() {
        let runner = FakeRunner::default().with(
            "coperceive-check:plan-path",
            "EXISTS=1\nREADABLE=1\nEXEC=0\nISDIR=0\nREALPATH=/etc/shadow\nSHA256=abc123\n",
        );
        let error = build_run_plan(&runner, &entry(), &request()).expect_err("must refuse");
        assert!(error.contains("outside this server"));
    }

    #[test]
    fn scratch_template_only_allows_expansion_variables_coperceive_controls() {
        assert!(validate_scratch_template("/scratch/$USER/$SLURM_JOB_ID").is_ok());
        assert!(validate_scratch_template("/scratch/${USER}").is_ok());
        assert!(validate_scratch_template("/scratch/$(rm -rf /)").is_err());
        assert!(validate_scratch_template("/scratch/`whoami`").is_err());
        assert!(validate_scratch_template("/scratch/$OTHER").is_err());
    }

    #[test]
    fn a_custom_pipeline_without_generic_roles_needs_no_checkpoint_or_split() {
        let mut req = request();
        req.checkpoint = String::new();
        req.split = String::new();
        req.values.clear();
        req.values.insert(
            "input".to_string(),
            serde_json::json!("/data/code/inputs/scene.txt"),
        );
        req.pipeline.arguments = vec![crate::hpc::ArgumentSpec {
            name: "input".to_string(),
            kind: ArgumentKind::Path,
            help: "the pipeline's own input".to_string(),
            required: true,
            default: None,
            choices: Vec::new(),
            multiple: false,
            role: ArgumentRole::Other,
        }];
        let plan = build_run_plan(&checked_runner(), &entry(), &req).expect("roleless plan");
        assert!(plan.checkpoint.path.is_empty());
        assert!(plan.split.path.is_empty());
        assert!(plan.is_saveable(), "checks: {:?}", plan.checks);
        for name in ["checkpoint", "checkpoint-sha", "split", "split-sha"] {
            assert!(!plan.checks.iter().any(|check| check.name == name), "{name}");
        }
        assert!(plan.argv.contains(&"--input".to_string()));
    }

    #[test]
    fn a_builtin_plugin_requires_the_split_and_only_a_declared_checkpoint() {
        // A built-in model always needs its scenes; a checkpoint only when its manifest
        // declares a `checkpoint` option (Griffin vehicle-side declares none).
        let mut req = griffin_request();
        req.model = "weights-from-option".to_string();
        req.ckpt_root = String::new();
        req.checkpoint = String::new();
        let error = build_run_plan(&checked_runner(), &entry(), &req).expect_err("builtin");
        assert!(error.contains("Choose a checkpoint path"), "{error}");
        let mut req = griffin_request();
        req.split = String::new();
        let error = build_run_plan(&checked_runner(), &entry(), &req).expect_err("no split");
        assert!(error.contains("Choose a split path"), "{error}");
    }

    #[test]
    fn path_typed_arguments_outside_profile_roots_are_refused() {
        let mut req = request();
        req.pipeline.arguments.push(crate::hpc::ArgumentSpec {
            name: "config".to_string(),
            kind: ArgumentKind::Path,
            help: "config".to_string(),
            required: true,
            default: None,
            choices: Vec::new(),
            multiple: false,
            role: ArgumentRole::Other,
        });
        req.values
            .insert("config".to_string(), serde_json::json!("/etc/passwd"));
        let error = build_run_plan(&checked_runner(), &entry(), &req)
            .expect_err("path outside profile roots must be refused");
        assert!(error.contains("--config argument"), "{error}");
        assert!(error.contains("outside this server"), "{error}");
    }

    #[test]
    fn a_builtin_plan_without_a_plugin_manifest_is_refused() {
        // The cluster's listing is the authority for the manifest (the client's
        // plugin_manifest is ignored): a plugin listed without one is refused.
        let mut req = request();
        req.pipeline_source = "builtin".to_string();
        req.pipeline.name = "griffin".to_string();
        req.model = "vehicle-side".to_string();
        req.raw_root = "/data/code/raw".to_string();
        req.media_root = "/data/code/raw".to_string();
        req.plugin_manifest = None;
        let no_manifest = MODEL_LIST_JSON.replace(
            "\"manifest\": \"/data/code/griffin/coperceive_griffin_model/model.yaml\"",
            "\"manifest\": null",
        );
        assert_ne!(no_manifest, MODEL_LIST_JSON);
        let runner = FakeRunner::default()
            .with("coperceive-check:model-list", &no_manifest)
            .with("coperceive-check:plan-path", "EXISTS=1\nREADABLE=1\nEXEC=0\nISDIR=0\nREALPATH=/data/code/checkpoints/model.pt\nSHA256=abc123\n");
        let error = build_run_plan(&runner, &entry(), &req).expect_err("builtin without manifest");
        assert!(error.contains("no pipeline manifest"), "{error}");
    }

    #[test]
    fn output_dir_is_stripped_in_both_argument_forms() {
        let mut argv = vec![
            "coperceive".to_string(),
            "model".to_string(),
            "--output-dir=/tmp/a".to_string(),
            "--output-dir".to_string(),
            "/tmp/b".to_string(),
            "--keep".to_string(),
            "yes".to_string(),
        ];
        strip_output_dir(&mut argv);
        assert_eq!(argv, vec!["coperceive", "model", "--keep", "yes"]);
    }

    #[test]
    fn save_writes_json_atomically_via_stdin_and_names_the_plan_path() {
        let echo = EchoRunner::new();
        let mut plan = build_run_plan(&checked_runner(), &entry(), &request()).expect("build");
        assert!(plan.is_saveable(), "checks: {:?}", plan.checks);
        // Client-supplied identity must not survive the save.
        plan.schema = "tampered-schema".to_string();
        plan.created_unix = 1;
        plan.profile_name = "tampered profile".to_string();
        let saved = save_run_plan(&echo, &entry(), &plan, &request()).expect("save plan");
        assert_eq!(saved.schema, RUN_PLAN_SCHEMA);
        assert_eq!(saved.profile_name, entry().profile.name);
        assert!(saved.created_unix > 1, "save time must come from the app");
        assert!(saved
            .saved_path
            .as_deref()
            .unwrap()
            .starts_with("/data/results/plans/"));
        assert!(saved.saved_path.as_deref().unwrap().ends_with(".json"));
        let written = echo.written.lock().unwrap();
        let write_call = written
            .iter()
            .find(|(command, _)| command.contains("coperceive-check:plan-write"))
            .expect("plan write");
        assert!(write_call.0.contains("umask 077"));
        assert!(write_call.0.contains("chmod 600"));
        assert!(write_call.0.contains("mv --"));
        assert!(!write_call.0.contains("sbatch"));
        let payload = write_call.1.as_deref().expect("stdin payload");
        let parsed: serde_json::Value = serde_json::from_str(payload).expect("plan JSON");
        assert_eq!(parsed["schema"], RUN_PLAN_SCHEMA);
        assert_eq!(parsed["profile_name"], entry().profile.name);
        assert!(parsed["created_unix"].as_u64().unwrap_or(0) > 1);
        assert_eq!(parsed["nothing_submitted"], true);
        // The follow-up read verifies the saved bytes.
        assert!(written
            .iter()
            .any(|(command, _)| command.contains("coperceive-check:plan-read")));
    }

    #[test]
    fn reads_custom_pipeline_registration_and_resolves_the_script() {
        let runner = FakeRunner::default().with(
            "coperceive-check:plan-read",
            r#"{"name":"contract-check","interpreter":["/usr/bin/python3"],"environment":{"kind":"none"},"arguments":[{"name":"checkpoint","kind":"path","role":"checkpoint","required":true}]}"#,
        );
        let entry = self::entry();
        let pipeline = load_pipeline_registration(
            &runner,
            &entry.profile,
            "/data/code/pipelines/contract_pipeline.py",
        )
        .expect("registration");
        assert_eq!(pipeline.source, "custom");
        assert_eq!(pipeline.spec.name, "contract-check");
        assert_eq!(
            pipeline.spec.script,
            "/data/code/pipelines/contract_pipeline.py"
        );
        assert_eq!(
            pipeline.registration_path.as_deref(),
            Some("/data/code/pipelines/coperceive-pipeline.json")
        );
        assert_eq!(pipeline.spec.arguments.len(), 1);
    }

    #[test]
    fn lists_installed_plugins_as_builtin_pipelines() {
        let runner = FakeRunner::default().with("coperceive-check:model-list", MODEL_LIST_JSON);
        let pipelines = list_builtin_pipelines(&runner, &entry().profile);
        assert_eq!(pipelines.len(), 1);
        assert_eq!(pipelines[0].id, "griffin");
        assert_eq!(pipelines[0].source, "builtin");
        assert_eq!(
            pipelines[0].plugin_manifest.as_deref(),
            Some("/data/code/griffin/coperceive_griffin_model/model.yaml")
        );
        assert_eq!(pipelines[0].spec.arguments[0].name, "extras");
        let plugin = pipelines[0].builtin.as_ref().expect("the listing");
        assert!(plugin.models["vehicle-side"].gpu);
    }

    #[test]
    fn a_too_old_cluster_still_lists_its_plugins_with_the_reason() {
        let runner = FakeRunner::default()
            .with_result("model list --json", "", false)
            .with("coperceive-check:model-list", "griffin  entry-point  coperceive_griffin_model:MANIFEST\n");
        // FakeRunner's failure says "fake failure", not --json: no fallback, no list.
        assert!(list_builtin_pipelines(&runner, &entry().profile).is_empty());
        let mut cluster = GriffinCluster::new();
        cluster.model_list = Err("coperceive: error: unrecognized arguments: --json".to_string());
        struct Plain(GriffinCluster);
        impl RemoteRunner for Plain {
            fn run(&self, cmd: &str, t: Duration, stdin: Option<&str>) -> Result<CommandResult, String> {
                if cmd.contains("model list 2>/dev/null") {
                    return Ok(CommandResult {
                        success: true,
                        stdout: "griffin  entry-point  coperceive_griffin_model:MANIFEST\n".to_string(),
                        stderr: String::new(),
                    });
                }
                self.0.run(cmd, t, stdin)
            }
        }
        let pipelines = list_builtin_pipelines(&Plain(cluster), &entry().profile);
        assert_eq!(pipelines.len(), 1);
        assert_eq!(pipelines[0].name, "griffin");
        assert_eq!(pipelines[0].plugin_manifest, None);
        assert!(pipelines[0].description.contains("too old"), "{}", pipelines[0].description);
    }

    #[test]
    fn a_missing_plugin_list_is_not_an_error() {
        let runner = FakeRunner::default();
        assert!(list_builtin_pipelines(&runner, &entry().profile).is_empty());
    }

    #[test]
    fn plan_id_format_is_enforced() {
        validate_plan_id("plan-1793183539-a1b2c3d4e5f6").expect("generated id");
        for bad in [
            "../../etc/passwd",
            "plan-1-../../x",
            "plan-abc-1",
            "plan-1-ZZ",
            "plan-1-",
            "plan-1-a/b",
            "other-1-ab",
            "",
        ] {
            assert!(
                validate_plan_id(bad).is_err(),
                "plan id {bad:?} must be refused"
            );
        }
    }

    #[test]
    fn save_revalidates_on_the_cluster_and_ignores_client_claims() {
        let built = build_run_plan(&checked_runner(), &entry(), &request()).expect("build");
        // The UI could fake all checks and hashes; save must not trust them.
        let mut claimed = built.clone();
        claimed.checkpoint.sha256 = Some("f".repeat(64));
        claimed.split.sha256 = Some("f".repeat(64));
        let echo = EchoRunner::new();
        let saved = save_run_plan(&echo, &entry(), &claimed, &request()).expect("save");
        assert_eq!(
            saved.checkpoint.sha256.as_deref(),
            Some("abc123"),
            "save must record the server's sha256, not the client's"
        );
        assert_eq!(saved.split.sha256.as_deref(), Some("abc123"));
        assert!(!saved.checks.is_empty());
        assert!(saved.checks.iter().all(|check| check.ok));

        // A symlink escape discovered at save time is refused even when the client
        // said the plan was checked and saveable.
        let escaping = FakeRunner::default()
            .with(
                "coperceive-pipeline.json",
                r#"{"name":"contract-check","script":"/data/code/pipelines/contract_pipeline.py","interpreter":["/usr/bin/python3"],"environment":{"kind":"none"},"arguments":[{"name":"checkpoint","kind":"path","role":"checkpoint","required":true},{"name":"split","kind":"path","role":"test_data","required":true},{"name":"extra","kind":"str"}]}"#,
            )
            .with(
                "coperceive-check:plan-path",
                "EXISTS=1\nREADABLE=1\nEXEC=0\nISDIR=0\nREALPATH=/etc/shadow\nSHA256=abc123\n",
            )
            .with(
                "coperceive-check:plan-env",
                "MODULE=/usr/share/lmod/lmod/libexec/lmod\nLMOD=\nAPPTAINER=\nSINGULARITY=\nPYTHON3=/usr/bin/python3\nCOPERCEIVE=/apps/bin/coperceive\nHOME=/home/bob\n",
            )
            .with("coperceive-check:coperceive-version", "coperceive 0.4.1\n")
            .with("coperceive-check:plan-folders", "SCRATCH_OK=1\nRESULTS_OK=1\n");
        let error = save_run_plan(&escaping, &entry(), &built, &request()).expect_err("escape refused");
        assert!(
            error.contains("outside this server's data and code folders"),
            "{error}"
        );
    }

    #[test]
    fn credential_check_uses_keys_and_values_not_path_words() {
        assert!(find_credential(
            &serde_json::json!({"checkpoint": {"path": "/data/tokenizer.json"}}),
            "plan"
        )
        .is_none());
        assert!(find_credential(
            &serde_json::json!({"checkpoint": {"path": "/data/code/token.json"}}),
            "plan"
        )
        .is_none());
        assert!(find_credential(&serde_json::json!({"note": "Bearer abc"}), "plan").is_some());
        assert!(find_credential(&serde_json::json!({"password": "hunter2"}), "plan").is_some());
        assert!(find_credential(&serde_json::json!({"api_key": "x"}), "plan").is_some());
        assert!(find_credential(
            &serde_json::json!({"pipeline": {"argv": ["--x", "token=abc"]}}),
            "plan"
        )
        .is_some());

        // A plan rebuilt with a credential-shaped typed value is refused at save time.
        let mut cred_request = request();
        cred_request
            .values
            .insert("extra".to_string(), serde_json::json!("secret=abc"));
        let plan = build_run_plan(&checked_runner(), &entry(), &cred_request).expect("build");
        let echo = EchoRunner::new();
        let error = save_run_plan(&echo, &entry(), &plan, &cred_request)
            .expect_err("credential refused");
        assert!(error.contains("credential-like"), "{error}");
    }

    #[test]
    fn save_refuses_a_bad_id_or_a_path_outside_the_roots() {
        let plan = build_run_plan(&checked_runner(), &entry(), &request()).expect("build");
        let echo = || EchoRunner::new();
        let mut bad_id = plan.clone();
        bad_id.id = "../../etc/evil".to_string();
        assert!(save_run_plan(&echo(), &entry(), &bad_id, &request())
            .expect_err("bad id")
            .contains("plan id"));
        let mut escaped = plan;
        escaped.checkpoint.path = "/data/../etc/passwd".to_string();
        assert!(save_run_plan(&echo(), &entry(), &escaped, &request())
            .expect_err("traversal")
            .contains("'..'"));
    }

    #[test]
    #[cfg(unix)]
    fn path_check_expands_tilde_against_remote_home() {
        if !std::path::Path::new("/bin/sh").exists() {
            return;
        }
        let root = std::env::temp_dir().join(format!("cp-tilde-{}", std::process::id()));
        let home = root.join("home");
        let bin = std::path::PathBuf::from(concat!(env!("CARGO_MANIFEST_DIR"), "/tests/fixtures/bin"));
        std::fs::create_dir_all(home.join("sub")).unwrap();
        std::fs::write(home.join("sub/file.txt"), b"x").unwrap();
        let output = std::process::Command::new("/bin/sh")
            .args(["-c", PATH_CHECK_BODY, "sh", "~/sub/file.txt"])
            .env("HOME", &home)
            .env("PATH", format!("{}:/usr/bin:/bin", bin.display()))
            .output()
            .expect("run sh");
        let stdout = String::from_utf8_lossy(&output.stdout);
        assert!(stdout.contains("EXISTS=1"), "{stdout}");
        assert!(
            stdout.contains(&format!("REALPATH={}", home.join("sub/file.txt").display())),
            "{stdout}"
        );
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn path_check_fails_closed_without_home_or_realpath_tools() {
        if !std::path::Path::new("/bin/sh").exists() {
            return;
        }
        // No HOME: ~ cannot be expanded, so the check reports a plain error.
        let output = std::process::Command::new("/bin/sh")
            .args(["-c", PATH_CHECK_BODY, "sh", "~/x"])
            .env_remove("HOME")
            .env("PATH", "/usr/bin:/bin")
            .output()
            .expect("run sh");
        let stdout = String::from_utf8_lossy(&output.stdout);
        assert!(
            stdout.contains("ERROR=The remote shell has no HOME"),
            "{stdout}"
        );

        // Neither realpath nor readlink: the symlink check must fail closed, not
        // silently fall back to the unresolved path.
        let root = std::env::temp_dir().join(format!("cp-tools-{}", std::process::id()));
        std::fs::create_dir_all(&root).unwrap();
        std::fs::write(root.join("file.txt"), b"x").unwrap();
        let path = root.join("file.txt");
        let output = std::process::Command::new("/bin/sh")
            .args(["-c", PATH_CHECK_BODY, "sh", &path.to_string_lossy()])
            .env("PATH", "")
            .env("HOME", &root)
            .output()
            .expect("run sh");
        let stdout = String::from_utf8_lossy(&output.stdout);
        assert!(
            stdout.contains("ERROR=") && stdout.contains("neither realpath nor readlink"),
            "{stdout}"
        );
        assert!(
            !PATH_CHECK_BODY.contains(r#"|| printf "%s" "$P""#),
            "the old silent fallback must be gone"
        );
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn save_rebuilds_argv_pipeline_env_and_output_from_the_server() {
        let built = build_run_plan(&checked_runner(), &entry(), &request()).expect("build");
        let mut forged = built.clone();
        forged.argv = vec![
            "evil".to_string(),
            "--output-dir=/tmp/evil".to_string(),
            "--output-dir".to_string(),
            "/tmp/also-evil".to_string(),
        ];
        forged.pipeline = PlanPipelineRef {
            id: "evil".to_string(),
            name: "evil".to_string(),
            version: "9.9".to_string(),
            source: "builtin".to_string(),
        };
        forged.output_dir = "/tmp/evil".to_string();
        forged.environment = EnvironmentSetup {
            kind: EnvironmentKind::None,
            compute_has_internet: true,
            ..EnvironmentSetup::default()
        };
        forged.environment_note = "forged".to_string();
        let mut req = request();
        req.environment_override = Some(EnvironmentSetup {
            kind: EnvironmentKind::None,
            compute_has_internet: true,
            ..EnvironmentSetup::default()
        });
        let echo = EchoRunner::new();
        let saved = save_run_plan(&echo, &entry(), &forged, &req).expect("save");
        // Pipeline ref, argv, environment and output dir are rebuilt, not copied.
        assert_eq!(saved.pipeline.name, "contract-check");
        assert_eq!(saved.pipeline.source, "custom");
        assert!(!saved.argv.iter().any(|element| element == "evil"));
        assert!(!saved
            .argv
            .iter()
            .any(|element| element.contains("/tmp/evil") || element.contains("/tmp/also-evil")));
        assert!(saved.argv.contains(&"a b; touch /tmp/x".to_string()));
        let len = saved.argv.len();
        assert_eq!(saved.argv[len - 2], "--output-dir");
        assert_eq!(
            saved.argv[len - 1],
            format!(
                "{}/runs/{}",
                entry().profile.results_root.trim_end_matches('/'),
                forged.id
            )
        );
        assert_eq!(
            saved.output_dir,
            format!(
                "{}/runs/{}",
                entry().profile.results_root.trim_end_matches('/'),
                forged.id
            )
        );
        assert_eq!(saved.environment, entry().profile.environment);
        assert!(!saved.environment.compute_has_internet);
        assert!(!saved.environment_from_override);
        assert!(saved.environment_note.contains("server profile"));
    }

    #[test]
    fn save_refuses_a_forged_pipeline_script() {
        let built = build_run_plan(&checked_runner(), &entry(), &request()).expect("build");
        let mut req = request();
        req.pipeline.script = "/etc/evil.py".to_string();
        let echo = EchoRunner::new();
        let error = save_run_plan(&echo, &entry(), &built, &req).expect_err("script refused");
        assert!(
            error.contains("outside code_roots and data_roots")
                || error.contains("outside this server's data and code folders")
                || error.contains("'..'"),
            "{error}"
        );
    }

    #[test]
    fn tilde_roots_are_expanded_against_the_remote_home() {
        // A profile root of ~/scenes must match the absolute real path under HOME.
        let profile = ServerProfile {
            data_roots: vec!["/data/code".to_string(), "~/scenes".to_string()],
            ..entry().profile
        };
        assert!(real_path_inside_roots(
            &profile,
            "/home/bob/scenes/checkpoint.pt",
            Some("/home/bob")
        ));
        assert!(!real_path_inside_roots(
            &profile,
            "/home/bob/scenes/checkpoint.pt",
            None
        ));
        assert!(!real_path_inside_roots(
            &profile,
            "/home/eve/scenes/checkpoint.pt",
            Some("/home/bob")
        ));
        assert!(real_path_inside_roots(
            &profile,
            "/data/code/checkpoints/model.pt",
            Some("/home/bob")
        ));
    }

    #[test]
    fn short_sk_prefix_is_not_treated_as_an_api_key() {
        // A split named sk-test is a path/label, not an API key.
        assert!(!value_looks_like_credential("sk-test"));
        assert!(!value_looks_like_credential("/data/splits/sk-test.txt"));
        assert!(!value_looks_like_credential("sk-short"));
        let real = format!("sk-{}", "a".repeat(20));
        assert!(value_looks_like_credential(&real));
        assert!(value_looks_like_credential(&format!("sk-{}", "A1b2C3d4E5f6G7h8I9j0")));
    }

    #[test]
    fn environment_paths_must_be_inside_the_profile_roots() {
        let mut request = request();
        request.environment_override = Some(EnvironmentSetup {
            kind: EnvironmentKind::Venv,
            interpreter: Some("/etc/python3".to_string()),
            ..EnvironmentSetup::default()
        });
        let error = build_run_plan(&FakeRunner::default(), &entry(), &request)
            .expect_err("outside interpreter refused");
        assert!(error.contains("python interpreter"), "{error}");
        request.environment_override = Some(EnvironmentSetup {
            kind: EnvironmentKind::Container,
            container_image: Some("/etc/image.sif".to_string()),
            ..EnvironmentSetup::default()
        });
        let error = build_run_plan(&FakeRunner::default(), &entry(), &request)
            .expect_err("outside image refused");
        assert!(error.contains("container image"), "{error}");
        // Inside the profile's code folder is accepted by the local check.
        request.environment_override = Some(EnvironmentSetup {
            kind: EnvironmentKind::Venv,
            interpreter: Some("/data/code/venv/bin/python".to_string()),
            ..EnvironmentSetup::default()
        });
        let plan = build_run_plan(&checked_runner(), &entry(), &request).expect("build");
        assert_eq!(plan.environment.kind, EnvironmentKind::Venv);
        assert_eq!(
            plan.environment.interpreter.as_deref(),
            Some("/data/code/venv/bin/python")
        );
    }

    #[test]
    fn real_fake_ssh_builds_and_saves_a_plan_without_submitting() {
        use crate::ssh::SshTarget;

        let _turn = crate::test_seams::FAKE_SSH_LOCK
            .lock()
            .unwrap_or_else(|p| p.into_inner());

        struct EnvGuard;
        impl Drop for EnvGuard {
            fn drop(&mut self) {
                std::env::remove_var("FAKE_REMOTE_ROOT");
                std::env::remove_var("FAKE_SSH_LOG");
            }
        }

        let root =
            std::env::temp_dir().join(format!("coperceive-plan-test-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&root);
        std::fs::create_dir_all(root.join("data/code/pipelines")).unwrap();
        std::fs::create_dir_all(root.join("data/code/checkpoints")).unwrap();
        std::fs::create_dir_all(root.join("data/code/splits")).unwrap();
        std::fs::create_dir_all(root.join("data/results")).unwrap();
        std::fs::create_dir_all(root.join("work/alice")).unwrap();
        std::fs::write(
            root.join("data/code/checkpoints/model.pt"),
            b"checkpoint-bytes\n",
        )
        .unwrap();
        std::fs::write(
            root.join("data/code/splits/test.txt"),
            b"scene-a\nscene-b\n",
        )
        .unwrap();
        std::fs::write(
            root.join("data/code/pipelines/contract_pipeline.py"),
            b"#!/usr/bin/env python3\n",
        )
        .unwrap();
        std::fs::write(
            root.join("data/code/pipelines/coperceive-pipeline.json"),
            br#"{"name":"contract-check","script":"contract_pipeline.py","interpreter":["/usr/bin/python3"],"environment":{"kind":"none"},"arguments":[{"name":"checkpoint","kind":"path","role":"checkpoint","required":true},{"name":"split","kind":"path","role":"test_data","required":true},{"name":"extra","kind":"str"}]}"#,
        )
        .unwrap();
        let log = root.join("ssh.log");
        std::env::set_var("FAKE_REMOTE_ROOT", &root);
        std::env::set_var("FAKE_SSH_LOG", &log);
        let _guard = EnvGuard;
        let fake = format!(
            "{}/tests/fixtures/hpc/fake_ssh.sh",
            env!("CARGO_MANIFEST_DIR")
        );
        let runner = crate::slurm_adapter::SshRunner::new(fake, SshTarget::alias("cluster-b"));
        let entry = self::entry();
        let plan = build_run_plan(&runner, &entry, &request()).expect("build plan");
        assert!(plan.is_saveable(), "checks: {:?}", plan.checks);
        let checkpoint_sha = plan.checkpoint.sha256.clone().expect("checkpoint sha");
        assert_eq!(checkpoint_sha.len(), 64);
        assert!(checkpoint_sha.chars().all(|ch| ch.is_ascii_hexdigit()));
        let split_sha = plan.split.sha256.clone().expect("split sha");
        assert_eq!(split_sha.len(), 64);
        let saved = save_run_plan(&runner, &entry, &plan, &request()).expect("save plan");
        let saved_path = saved.saved_path.expect("saved path");
        let local = format!("{}{}", root.display(), saved_path);
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let mode = std::fs::metadata(&local).unwrap().permissions().mode() & 0o777;
            assert_eq!(mode, 0o600);
        }
        let text = std::fs::read_to_string(&local).unwrap();
        let stored: RunPlan = serde_json::from_str(&text).unwrap();
        assert_eq!(stored.nothing_submitted, true);
        assert!(stored.contract_check_script.contains("--max-frames 3"));
        let commands = std::fs::read_to_string(&log).unwrap();
        assert!(!commands.contains("sbatch"));
        assert!(!commands.contains("scancel"));
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn typed_values_apply_required_defaults_and_reject_unknown_arguments() {
        let mut spec = pipeline();
        spec.arguments[2].default = Some(serde_json::json!("default-extra"));
        let mut values = BTreeMap::new();
        values.insert(
            "checkpoint".to_string(),
            serde_json::json!("/data/code/checkpoints/model.pt"),
        );
        values.insert(
            "split".to_string(),
            serde_json::json!("/data/code/splits/test.txt"),
        );
        let typed = typed_values(&spec, &values).expect("typed");
        assert_eq!(
            typed.get("extra"),
            Some(&serde_json::json!("default-extra"))
        );
        values.insert("unknown".to_string(), serde_json::json!("x"));
        assert!(typed_values(&spec, &values).is_err());
    }

    #[test]
    fn contract_script_refuses_a_partition_that_was_never_chosen() {
        let mut profile = entry().profile;
        profile.defaults = JobDefaults::default();
        let error = render_contract_check_script(&profile, &EnvironmentSetup::default(), &[], "p", false)
            .expect_err("must refuse");
        assert!(error.contains("Choose a partition"));
    }

    fn entry_with_discovered(partition: &str, gpu_types: &[&str]) -> ServerEntry {
        let mut entry = entry();
        entry.profile.defaults.partition = Some(partition.to_string());
        entry.discovered = Some(crate::server_profiles::DiscoveredServer {
            partitions: vec![crate::hpc::DiscoveredPartition {
                name: partition.to_string(),
                nodes: Some(8),
                state: Some("up".to_string()),
                max_time: None,
                gres: gpu_types.first().map(|gpu| format!("gpu:{gpu}:1")),
                gpu_types: gpu_types.iter().map(|gpu| gpu.to_string()).collect(),
            }],
            gpu_types: gpu_types.iter().map(|gpu| gpu.to_string()).collect(),
            ..Default::default()
        });
        entry
    }

    #[test]
    fn an_eval_plan_on_a_gpu_partition_has_one_gpu_by_default() {
        let entry = entry_with_discovered("gpu-h100", &["h100"]);
        let plan = build_run_plan(&checked_runner(), &entry, &request()).expect("plan");
        assert_eq!(plan.gres.as_deref(), Some("gpu:h100:1"));
        // The light contract check never requests that GPU.
        assert!(!plan.contract_check_script.contains("--gres"));
        assert!(plan
            .contract_check_script
            .contains("No GPU requested for this contract check"));
    }

    #[test]
    fn an_eval_plan_on_a_cpu_only_partition_has_no_gres() {
        let entry = entry_with_discovered("main-cpu", &[]);
        let plan = build_run_plan(&checked_runner(), &entry, &request()).expect("plan");
        assert!(plan.gres.is_none());
        assert!(!plan.contract_check_script.contains("--gres"));
    }

    #[test]
    fn contract_check_ignores_an_old_profile_gpu_default() {
        let mut profile = entry().profile;
        profile.defaults.gres = Some("gpu:a100:1".to_string());
        let script = render_contract_check_script(
            &profile,
            &EnvironmentSetup::default(),
            &[],
            "plan-1793222864-c5af819dbd2b",
            false,
        )
        .expect("script");
        assert!(!script.contains("--gres"));
        assert!(script.contains("No GPU requested for this contract check"));
    }

    #[test]
    fn builtin_argv_and_contract_script_prefix_the_saved_command() {
        let profile = entry().profile;
        let argv = build_builtin_argv(
            &profile,
            "/data/code/griffin/manifest.yaml",
            "plan-1",
            &BTreeMap::new(),
            "/data/scenes",
            "/data/code/checkpoints/model.pt",
            "/data/results/runs/plan-1",
            "", "", "", "", "", "",
        )
        .expect("argv");
        assert_eq!(
            &argv[..3],
            &[
                "/home/bob/coperceive-app/.venv/bin/python",
                "-m",
                "coperceive"
            ]
        );
        let script = render_contract_check_script(
            &profile,
            &profile.environment,
            &argv,
            "plan-1793222864-c5af819dbd2b",
            true,
        )
        .expect("contract script");
        assert!(
            script.contains("/home/bob/coperceive-app/.venv/bin/python -m coperceive model run"),
            "{script}"
        );
    }

    #[test]
    fn builtin_argv_parity_with_the_cli_golden_sections() {
        // The app builds the inner `coperceive model run` argv; the CLI --slurm
        // path wraps the same kind of argv. Compare the shared sections and
        // pin the deliberate differences so a drift is visible in review.
        let argv = build_builtin_argv(
            &entry().profile,
            "/data/code/griffin/manifest.yaml",
            "plan-1790000000-aabbccddeeff",
            &BTreeMap::new(),
            "/data/scenes",
            // vehicle-side declares no checkpoint option: the plan passes none.
            "",
            "/data/results/runs/plan-1790000000-aabbccddeeff",
            "vehicle-side", "/data/raw", "/data/raw", "", "default", "/data/results/runs/plan-1790000000-aabbccddeeff/work",
        )
        .expect("argv");
        for item in [
            "model",
            "run",
            "--manifest",
            "/data/code/griffin/manifest.yaml",
            "--scenes",
            "/data/scenes",
            "--out",
            "/data/results/runs/plan-1790000000-aabbccddeeff",
            "--run-id",
            "plan-1790000000-aabbccddeeff",
            "--model",
            "vehicle-side",
            "--raw-root",
            "/data/raw",
            "--media-root",
            "/data/raw",
            "--work",
            "/data/results/runs/plan-1790000000-aabbccddeeff/work",
        ] {
            assert!(argv.iter().any(|value| value == item), "missing {item}: {argv:?}");
        }
        // Any flag not listed here is an unlisted difference and fails this test.
        let mut flags: Vec<&str> = argv
            .iter()
            .filter(|value| value.starts_with("--"))
            .map(String::as_str)
            .collect();
        flags.sort_unstable();
        assert_eq!(
            flags,
            [
                "--manifest", "--media-root", "--model", "--out",
                "--raw-root", "--run-id", "--scenes", "--work",
            ]
        );
        // The CLI-only staging timer is the one justified missing value: the app
        // runs against already-present cluster paths, not an rsync stage.
        assert!(!argv.iter().any(|value| value == "--staging-seconds"));
    }

    #[test]
    fn builtin_argv_uses_plugin_manifest_and_separate_option_elements() {
        let mut values = BTreeMap::new();
        values.insert(
            "extras".to_string(),
            serde_json::json!(["epochs=2", "a b; touch /tmp/x"]),
        );
        let argv = build_builtin_argv(
            &entry().profile,
            "/data/code/griffin/manifest.yaml",
            "plan-1",
            &values,
            "/data/scenes",
            "/data/code/checkpoints/model.pt",
            "/data/results/runs/plan-1",
            "", "", "", "", "", "",
        )
        .expect("argv");
        assert_eq!(argv[0], "/home/bob/coperceive-app/.venv/bin/python");
        assert_eq!(argv[1], "-m");
        assert_eq!(argv[2], "coperceive");
        assert!(argv.contains(&"--manifest".to_string()));
        assert!(argv.contains(&"/data/code/griffin/manifest.yaml".to_string()));
        assert!(argv.contains(&"checkpoint=/data/code/checkpoints/model.pt".to_string()));
        assert!(argv.contains(&"a b; touch /tmp/x".to_string()));
    }

    #[test]
    fn an_empty_plugin_manifest_is_not_an_argv_element() {
        let error = build_builtin_argv(
            &entry().profile,
            "  ",
            "plan-1",
            &BTreeMap::new(),
            "/data/scenes",
            "/data/code/checkpoints/model.pt",
            "/data/results/runs/plan-1",
            "", "", "", "", "", "",
        )
        .expect_err("empty manifest");
        assert!(error.contains("no pipeline manifest"), "{error}");
    }

    #[test]
    fn save_refuses_a_path_argument_outside_the_profile_roots() {
        let mut req = request();
        req.pipeline.arguments.push(crate::hpc::ArgumentSpec {
            name: "config".to_string(),
            kind: ArgumentKind::Path,
            help: "config".to_string(),
            required: true,
            default: None,
            choices: Vec::new(),
            multiple: false,
            role: ArgumentRole::Other,
        });
        req.values
            .insert("config".to_string(), serde_json::json!("/etc/passwd"));
        let plan = build_run_plan(&checked_runner(), &entry(), &request()).expect("build");
        let mut echo = EchoRunner::new();
        echo.pipeline_json = Some(
            r#"{"name":"contract-check","script":"/data/code/pipelines/contract_pipeline.py","interpreter":["/usr/bin/python3"],"environment":{"kind":"none"},"arguments":[{"name":"checkpoint","kind":"path","role":"checkpoint","required":true},{"name":"split","kind":"path","role":"test_data","required":true},{"name":"extra","kind":"str"},{"name":"config","kind":"path","role":"other","required":true}]}"#.to_string(),
        );
        let error = save_run_plan(&echo, &entry(), &plan, &req).expect_err("path refused");
        assert!(error.contains("--config argument"), "{error}");
        assert!(error.contains("outside this server"), "{error}");
        let written = echo.written.lock().unwrap();
        assert!(!written
            .iter()
            .any(|(command, _)| command.contains("coperceive-check:plan-write")));
    }

    #[test]
    fn save_refuses_an_installed_model_whose_manifest_path_is_missing() {
        let mut req = griffin_request();
        req.raw_root = "/data/code/raw".to_string();
        req.media_root = "/data/code/raw".to_string();
        req.plugin_manifest = Some("/tmp/client-manifest.yaml".to_string());
        let plan = build_run_plan(&checked_runner(), &entry(), &req).expect("build");
        let mut echo = EchoRunner::new();
        // The cluster lists the plugin, but its entry point names no readable manifest.
        echo.model_list = Some(
            r#"{"format":"coperceive-model-list","format_version":1,"plugins":[{"name":"griffin","manifest":null,"error":"entry point 'griffin' names a missing manifest","modules":[],"models":{}}]}"#
                .to_string(),
        );
        let error = save_run_plan(&echo, &entry(), &plan, &req).expect_err("no manifest");
        assert!(error.contains("no pipeline manifest"), "{error}");
        let written = echo.written.lock().unwrap();
        assert!(!written.iter().any(|(command, stdin)| {
            stdin.as_deref().unwrap_or("").contains("--manifest")
                && command.contains("coperceive-check:plan-write")
        }));
    }

    #[test]
    fn a_builtin_plan_carries_model_raw_media_and_work() {
        let mut req = griffin_request();
        req.split = "/data/code/splits/test.txt".to_string();
        req.model = "vehicle-side".to_string();
        req.raw_root = "/data/code/raw".to_string();
        req.media_root = "/data/code/raw".to_string();
        let plan = build_run_plan(&checked_runner(), &entry(), &req).expect("build");
        assert_eq!(plan.model, "vehicle-side");
        assert_eq!(plan.raw_root.path, "/data/code/raw");
        assert_eq!(plan.media_root.path, "/data/code/raw");
        // The job's scratch, as the CLI's `--work "$SCRATCH/work"`.
        assert_eq!(plan.work_dir, WORK_IN_SCRATCH);
        for item in ["--model", "vehicle-side", "--raw-root", "/data/code/raw", "--media-root", "/data/code/raw", "--work"] {
            assert!(plan.argv.iter().any(|value| value == item), "missing {item}: {:?}", plan.argv);
        }
    }

    #[test]
    fn a_builtin_plan_without_a_media_root_is_accepted_and_has_no_flag() {
        // The CLI's --media-root is optional (coperceive/cli.py); the media root is not
        // what identifies a plan field. An empty one must be accepted and leave the flag
        // out entirely, in both the built argv and the saved plan.
        let mut req = griffin_request();
        req.split = "/data/code/splits/test.txt".to_string();
        req.raw_root = "/data/code/raw".to_string();
        req.media_root = String::new();
        let plan = build_run_plan(&checked_runner(), &entry(), &req).expect("build");
        assert_eq!(plan.model, "vehicle-side");
        assert_eq!(plan.raw_root.path, "/data/code/raw");
        assert!(plan.media_root.path.is_empty());
        assert!(!plan.argv.iter().any(|word| word == "--media-root"), "{:?}", plan.argv);

        let mut echo = EchoRunner::new();
        echo.model_list = Some(MODEL_LIST_JSON.to_string());
        let saved = save_run_plan(&echo, &entry(), &plan, &req).expect("save");
        assert!(saved.media_root.path.is_empty());
        assert!(!saved.argv.iter().any(|word| word == "--media-root"), "{:?}", saved.argv);
    }

    #[test]
    fn a_builtin_plan_needs_its_manifest_required_ckpt_root_and_passes_it_through() {
        // The real Griffin manifest declares ckpt_root without a default. The plan must
        // carry the value the user gave, pass it as an --option (the worker gets it as
        // --ckpt-root from commands.batch), and refuse a missing one by name.
        let mut req = griffin_request();
        req.split = "/data/code/splits/test.txt".to_string();
        req.raw_root = "/data/code/raw".to_string();
        req.media_root = "/data/code/raw".to_string();
        req.ckpt_root = "/data/code/ckpts".to_string();
        let plan = build_run_plan(&checked_runner(), &entry(), &req).expect("build");
        assert_eq!(plan.ckpt_root.path, "/data/code/ckpts");
        assert!(plan.model_options.iter().any(|option| option == "ckpt_root"));
        let at = plan
            .argv
            .iter()
            .position(|word| word == "ckpt_root=/data/code/ckpts")
            .expect("ckpt_root option");
        assert_eq!(plan.argv[at - 1], "--option");

        req.ckpt_root = String::new();
        let error = build_run_plan(&checked_runner(), &entry(), &req).expect_err("missing");
        assert!(error.contains("ckpt_root"), "{error}");
        assert!(error.contains("vehicle-side"), "{error}");
    }

    #[test]
    fn a_legacy_plan_carries_the_protocol_and_an_old_plan_defaults() {
        // The plan records the AP protocol and passes it to model run, which writes it
        // into run.json for a later eval run. A plan saved before the field is default.
        let mut req = griffin_request();
        req.split = "/data/code/splits/test.txt".to_string();
        req.raw_root = "/data/code/raw".to_string();
        req.media_root = "/data/code/raw".to_string();
        req.ckpt_root = "/data/code/ckpts".to_string();
        req.eval_protocol = "legacy".to_string();
        let plan = build_run_plan(&checked_runner(), &entry(), &req).expect("build");
        assert_eq!(plan.eval_protocol, "legacy");
        let at = plan
            .argv
            .iter()
            .position(|word| word == "--protocol")
            .expect("--protocol");
        assert_eq!(plan.argv[at + 1], "legacy");
        let mut echo = EchoRunner::new();
        echo.model_list = Some(MODEL_LIST_JSON.to_string());
        let saved = save_run_plan(&echo, &entry(), &plan, &req).expect("save");
        assert_eq!(saved.eval_protocol, "legacy");
        assert!(saved.argv.iter().any(|word| word == "legacy"));

        let mut json = serde_json::to_value(&plan).unwrap();
        json.as_object_mut().unwrap().remove("eval_protocol");
        let old: RunPlan = serde_json::from_value(json).unwrap();
        assert_eq!(old.eval_protocol, DEFAULT_EVAL_PROTOCOL);
    }

    #[test]
    fn a_builtin_plan_refuses_an_unknown_evaluation_protocol() {
        let mut req = griffin_request();
        req.eval_protocol = "v0.1.1".to_string();
        let error = build_run_plan(&checked_runner(), &entry(), &req).expect_err("bad protocol");
        assert!(error.contains("AP protocol"), "{error}");
    }

    #[test]
    fn save_uses_the_installed_manifest_not_the_client_path() {
        let mut req = griffin_request();
        req.split = "/data/code/splits/test.txt".to_string();
        req.model = "vehicle-side".to_string();
        req.raw_root = "/data/code/raw".to_string();
        req.media_root = "/data/code/raw".to_string();
        req.plugin_manifest = Some("/tmp/client-manifest.yaml".to_string());
        // The installed plugin's spec only accepts its own arguments. Checkpoint
        // and split travel in the dedicated fields, not as unknown values.
        req.values.clear();
        let plan = build_run_plan(&checked_runner(), &entry(), &req).expect("build");
        let mut echo = EchoRunner::new();
        echo.model_list = Some(MODEL_LIST_JSON.to_string());
        let saved = save_run_plan(&echo, &entry(), &plan, &req).expect("save");
        assert!(saved
            .argv
            .contains(&"/data/code/griffin/coperceive_griffin_model/model.yaml".to_string()));
        assert!(!saved
            .argv
            .iter()
            .any(|element| element.contains("/tmp/client-manifest")));
        let manifest_at = saved
            .argv
            .iter()
            .position(|element| element == "--manifest")
            .expect("manifest flag");
        assert!(!saved.argv[manifest_at + 1].is_empty());
    }
}
