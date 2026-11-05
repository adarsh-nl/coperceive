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
pub const PIPELINE_SPEC_FILE: &str = "coperceive-pipeline.json";
pub const CONTRACT_CHECK_FRAMES: u32 = 3;

const READ_TIMEOUT: Duration = Duration::from_secs(8);
const ENVIRONMENT_TIMEOUT: Duration = Duration::from_secs(8);
const CHECK_TIMEOUT: Duration = Duration::from_secs(10);
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
    /// Where a later submission would write the run (`results_root/runs/<id>`).
    pub output_dir: String,
    /// Scratch directory the contract-check job uses (profile template, expanded
    /// by the job at run time).
    pub scratch_dir: String,
    /// GPU default for a model evaluation on the chosen partition, or None for a
    /// CPU-only partition. The user can override it at submit time.
    #[serde(default)]
    pub gres: Option<String>,
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
            && (self.split.path.is_empty() || self.split.sha256.is_some())
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

/// Built-in argv for an installed model plugin (the plugin owns its checkpoint
/// handling, so a checkpoint is passed as a model option).
pub fn build_builtin_argv(
    profile: &ServerProfile,
    plugin_manifest: &str,
    plan_id: &str,
    values: &BTreeMap<String, serde_json::Value>,
    split: &str,
    checkpoint: &str,
    output_dir: &str,
) -> Vec<String> {
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
            "--option".to_string(),
            format!("checkpoint={checkpoint}"),
        ],
    );
    if let Some(serde_json::Value::Array(extras)) = values.get("extras") {
        for extra in extras {
            argv.push("--option".to_string());
            argv.push(value_argv(extra));
        }
    }
    argv
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
    argv.iter()
        .map(|element| shell_quote(element))
        .collect::<Vec<_>>()
        .join(" ")
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
        format!(
            "# Contract check for run plan {plan_id}: {CONTRACT_CHECK_FRAMES} frames through the \
             pipeline's output contract."
        ),
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
                .filter(|interpreter| !interpreter.is_empty())
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
        .filter(|wheelhouse| !wheelhouse.is_empty())
    {
        lines.push(format!(
            "export PIP_FIND_LINKS={}\nexport PIP_NO_INDEX=1",
            shell_quote(wheelhouse)
        ));
    }
    lines.push(format!(
        "echo \"Contract check on $(hostname) for plan {plan_id}\""
    ));
    let check_command = format!(
        "{} --max-frames {CONTRACT_CHECK_FRAMES} --output-dir \"$OUT_DIR\"",
        quoted_command(&command)
    );
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
fi"#;

fn path_check_script(path: &str) -> String {
    format!("sh -c '{}' sh {}", PATH_CHECK_BODY, shell_quote(path))
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
            .map(|value| value.trim().to_string())
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
    let (needs_checkpoint, needs_split) =
        required_plan_artifacts(&request.pipeline_source, &pipeline);
    if needs_checkpoint {
        validate_path_in_roots(profile, &request.checkpoint, "checkpoint")?;
    }
    if needs_split {
        validate_path_in_roots(profile, &request.split, "split")?;
    }
    validate_plan_destinations(profile)?;

    let plan_id = new_plan_id();
    let base = profile.results_root.trim_end_matches('/').to_string();
    let output_dir = format!("{base}/runs/{plan_id}");
    let (environment, overridden, env_note) =
        resolve_environment(profile, &pipeline, request.environment_override.as_ref());
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
            &path_check_script(&request.split),
            CHECK_TIMEOUT,
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
    } else {
        checks.push(with_hint(
            check(
                "split-sha",
                "Test split sha256",
                split.kind == "folder",
                if split.kind == "folder" {
                    "The split is a folder; its listing is recorded but folders have no single sha256."
                } else {
                    "Could not read the split file's checksum."
                },
            ),
            "A split file gives a verifiable sha256; a folder is accepted but not hashed.",
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

    // 2. Environment pieces available on the login node (light).
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
            let manifest = request
                .plugin_manifest
                .as_deref()
                .map(str::trim)
                .filter(|value| !value.is_empty())
                .ok_or_else(|| {
                    "This built-in model has no pipeline manifest. Reinstall the model or choose a \
                     custom pipeline."
                        .to_string()
                })?;
            build_builtin_argv(
                profile,
                manifest,
                &plan_id,
                &request.values,
                &request.split,
                &request.checkpoint,
                &output_dir,
            )
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
        render_contract_check_script(profile, &environment, &argv, &plan_id)?;
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
        output_dir,
        scratch_dir: profile.scratch_template.clone(),
        gres: default_eval_gres(entry),
        checks,
        contract_check_script,
        nothing_submitted: true,
        saved_path: None,
    })
}

fn model_list_script(profile: &ServerProfile) -> String {
    format!(
        "sh -c '# coperceive-check:model-list\n{} model list 2>/dev/null'",
        quoted_command(&profile.environment.coperceive_command)
    )
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
    })
}

/// Lists installed model plugins as built-in pipelines. A cluster without
/// plugins simply returns an empty list.
pub fn list_builtin_pipelines(
    runner: &dyn RemoteRunner,
    profile: &ServerProfile,
) -> Vec<PipelineEntry> {
    let script = model_list_script(profile);
    let Ok(result) = runner.run(&script, READ_TIMEOUT, None) else {
        return Vec::new();
    };
    if !result.success {
        return Vec::new();
    }
    let mut entries = Vec::new();
    for line in result.stdout.lines() {
        let trimmed = line.trim();
        if trimmed.is_empty() || trimmed.starts_with("no model plugins") {
            continue;
        }
        let parts: Vec<&str> = trimmed.split_whitespace().collect();
        if parts.len() < 2 {
            continue;
        }
        let name = parts[0];
        let is_valid_name = name
            .chars()
            .next()
            .map(|ch| ch.is_ascii_lowercase())
            .unwrap_or(false)
            && name
                .chars()
                .all(|ch| ch.is_ascii_lowercase() || ch.is_ascii_digit() || ch == '-' || ch == '_');
        if !is_valid_name {
            continue;
        }
        let manifest = parts.last().unwrap_or(&"").to_string();
        let plugin_manifest = if manifest.contains('/') {
            Some(manifest.clone())
        } else {
            None
        };
        entries.push(PipelineEntry {
            id: name.to_string(),
            name: name.to_string(),
            description: "Installed model plugin; runs coperceive model run on the cluster."
                .to_string(),
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
        });
    }
    entries
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
    plugin_manifest: Option<String>,
    environment: EnvironmentSetup,
    overridden: bool,
    env_note: String,
}

fn resolve_pipeline_for_save(
    runner: &dyn RemoteRunner,
    profile: &ServerProfile,
    request: &RunPlanRequest,
) -> Result<SavePipeline, String> {
    let source = request.pipeline_source.trim().to_string();
    let (spec, plugin_manifest) = if source == "builtin" {
        let wanted = request.pipeline.name.trim();
        if wanted.is_empty() {
            return Err("Choose the installed pipeline to run.".to_string());
        }
        let entry = list_builtin_pipelines(runner, profile)
            .into_iter()
            .find(|entry| entry.name == wanted || entry.id == wanted)
            .ok_or_else(|| {
                format!(
                    "The installed pipeline {wanted:?} is no longer on the cluster. Reload the                      pipeline list and rebuild the plan."
                )
            })?;
        (entry.spec, entry.plugin_manifest)
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
    let (mut environment, overridden, env_note) =
        resolve_environment(profile, &spec, None);
    // A plan never turns on the internet for compute nodes: that is a profile fact.
    environment.compute_has_internet = profile.environment.compute_has_internet;
    validate_environment_roots(profile, &environment)?;
    Ok(SavePipeline {
        spec,
        source: if source == "builtin" {
            "builtin".to_string()
        } else {
            "custom".to_string()
        },
        plugin_manifest,
        environment,
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
        let values = run_check(
            runner,
            &mut checks,
            &format!("{name}-path"),
            label,
            &path_check_script(path),
            CHECK_TIMEOUT,
        )?
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
        checks.push(check(
            &format!("{name}-sha"),
            &format!("{label} sha256"),
            artifact.sha256.is_some(),
            match &artifact.sha256 {
                Some(sha) => format!("sha256 {sha}"),
                None => "Could not read a checksum for this file.".to_string(),
            },
        ));
        if artifact.sha256.is_none() {
            return Err(format!(
                "The cluster no longer reports a sha256 for the {what}, so the plan cannot be \
                 saved and later verified."
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
    let (needs_checkpoint, needs_split) =
        required_plan_artifacts(&save_pipeline.source, &save_pipeline.spec);
    let (checkpoint, split, mut checks) = revalidate_plan_on_server(
        runner,
        profile,
        plan,
        &save_pipeline.environment,
        remote_home.as_deref(),
        needs_checkpoint,
        needs_split,
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
    let built_argv = if save_pipeline.source == "builtin" {
        let manifest = save_pipeline
            .plugin_manifest
            .as_deref()
            .map(str::trim)
            .filter(|value| !value.is_empty())
            .ok_or_else(|| {
                "This built-in model has no pipeline manifest. Reinstall the model or choose a \
                 custom pipeline."
                    .to_string()
            })?;
        build_builtin_argv(
            profile,
            manifest,
            &plan.id,
            &typed,
            &split.path,
            &checkpoint.path,
            &output_dir,
        )
    } else {
        build_pipeline_argv(&save_pipeline.spec, &typed, &output_dir)?
    };
    // Force the output directory pair to the recomputed value.
    let mut argv: Vec<String> = built_argv.clone();
    strip_output_dir(&mut argv);
    argv.push("--output-dir".to_string());
    argv.push(output_dir.clone());
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
        render_contract_check_script(profile, &save_pipeline.environment, &argv, &plan.id)?;
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
    validated.checks = checks;
    validated.output_dir = output_dir;
    validated.scratch_dir = profile.scratch_template.clone();
    validated.gres = default_eval_gres(entry);
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
                return Ok(CommandResult {
                    success: true,
                    stdout: r#"{"name":"contract-check","script":"/data/code/pipelines/contract_pipeline.py","interpreter":["/usr/bin/python3"],"environment":{"kind":"none"},"arguments":[{"name":"checkpoint","kind":"path","role":"checkpoint","required":true},{"name":"split","kind":"path","role":"test_data","required":true},{"name":"extra","kind":"str"}]}"# .to_string(),
                    stderr: String::new(),
                });
            }
            if remote_cmd.contains("coperceive-check:model-list") {
                return Ok(CommandResult {
                    success: true,
                    stdout: String::new(),
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
    fn a_builtin_plugin_still_requires_the_checkpoint_and_split() {
        let mut req = request();
        req.pipeline_source = "builtin".to_string();
        req.plugin_manifest = Some("/data/code/griffin/manifest.yaml".to_string());
        req.checkpoint = String::new();
        let error = build_run_plan(&checked_runner(), &entry(), &req).expect_err("builtin");
        assert!(error.contains("Choose a checkpoint path"), "{error}");
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
        let mut req = request();
        req.pipeline_source = "builtin".to_string();
        req.plugin_manifest = None;
        let error = build_run_plan(&checked_runner(), &entry(), &req)
            .expect_err("builtin without manifest");
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
        let echo = EchoRunner {
            written: Mutex::new(Vec::new()),
        };
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
        let runner = FakeRunner::default().with(
            "coperceive-check:model-list",
            "griffin  entry-point  /data/code/griffin/manifest.yaml\n",
        );
        let pipelines = list_builtin_pipelines(&runner, &entry().profile);
        assert_eq!(pipelines.len(), 1);
        assert_eq!(pipelines[0].id, "griffin");
        assert_eq!(pipelines[0].source, "builtin");
        assert_eq!(
            pipelines[0].plugin_manifest.as_deref(),
            Some("/data/code/griffin/manifest.yaml")
        );
        assert_eq!(pipelines[0].spec.arguments[0].name, "extras");
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
        let echo = EchoRunner {
            written: Mutex::new(Vec::new()),
        };
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
        let echo = EchoRunner {
            written: Mutex::new(Vec::new()),
        };
        let error = save_run_plan(&echo, &entry(), &plan, &cred_request)
            .expect_err("credential refused");
        assert!(error.contains("credential-like"), "{error}");
    }

    #[test]
    fn save_refuses_a_bad_id_or_a_path_outside_the_roots() {
        let plan = build_run_plan(&checked_runner(), &entry(), &request()).expect("build");
        let echo = || EchoRunner {
            written: Mutex::new(Vec::new()),
        };
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
    fn path_check_expands_tilde_against_remote_home() {
        if !std::path::Path::new("/bin/sh").exists() {
            return;
        }
        let root = std::env::temp_dir().join(format!("cp-tilde-{}", std::process::id()));
        let home = root.join("home");
        let bin = root.join("bin");
        std::fs::create_dir_all(home.join("sub")).unwrap();
        std::fs::create_dir_all(&bin).unwrap();
        std::fs::write(home.join("sub/file.txt"), b"x").unwrap();
        // A stand-in realpath: enough to prove the shell expanded ~ and passed the
        // right absolute path; the real tool's availability is tested separately.
        let fake_realpath = bin.join("realpath");
        std::fs::write(
            &fake_realpath,
            "#!/bin/sh\nfor last; do :; done\nprintf '%s\\n' \"$last\"\n",
        )
        .unwrap();
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&fake_realpath, std::fs::Permissions::from_mode(0o755)).unwrap();
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
            "--output-dir".to_string(),
            "/tmp/evil".to_string(),
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
        let echo = EchoRunner {
            written: Mutex::new(Vec::new()),
        };
        let saved = save_run_plan(&echo, &entry(), &forged, &req).expect("save");
        // Pipeline ref, argv, environment and output dir are rebuilt, not copied.
        assert_eq!(saved.pipeline.name, "contract-check");
        assert_eq!(saved.pipeline.source, "custom");
        assert!(!saved.argv.iter().any(|element| element == "evil"));
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
        let echo = EchoRunner {
            written: Mutex::new(Vec::new()),
        };
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
        use std::os::unix::fs::PermissionsExt;

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
        std::fs::set_permissions(&fake, std::fs::Permissions::from_mode(0o755)).unwrap();
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
        let mode = std::fs::metadata(&local).unwrap().permissions().mode() & 0o777;
        assert_eq!(mode, 0o600);
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
        let error = render_contract_check_script(&profile, &EnvironmentSetup::default(), &[], "p")
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
        );
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
        )
        .expect("contract script");
        assert!(
            script.contains("/home/bob/coperceive-app/.venv/bin/python -m coperceive model run"),
            "{script}"
        );
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
        );
        assert_eq!(argv[0], "/home/bob/coperceive-app/.venv/bin/python");
        assert_eq!(argv[1], "-m");
        assert_eq!(argv[2], "coperceive");
        assert!(argv.contains(&"--manifest".to_string()));
        assert!(argv.contains(&"/data/code/griffin/manifest.yaml".to_string()));
        assert!(argv.contains(&"checkpoint=/data/code/checkpoints/model.pt".to_string()));
        assert!(argv.contains(&"a b; touch /tmp/x".to_string()));
    }
}
