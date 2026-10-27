// SPDX-License-Identifier: Apache-2.0
//! Shared HPC types for the Remote HPC workspace.
//!
//! This module is the Rust mirror of the slice-0 interfaces
//! (`coperceive/hpc/{profile,scheduler,pipeline}.py`, ADR 0015). It is owned by
//! the slice-0 branch; the slice-1 code imports these types and must not
//! redefine them. JSON keys are snake_case and identical between Python and
//! Rust.
//!
//! No secret is ever part of a profile: credentials live only in the user's
//! ssh-agent and `~/.ssh/config`.

use serde::{Deserialize, Serialize};

/// Which scheduler a server runs. Only Slurm is implemented today; a server
/// without a scheduler is still a valid workspace (files only).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "lowercase")]
pub enum SchedulerKind {
    Slurm,
    #[default]
    None,
}

impl SchedulerKind {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Slurm => "slurm",
            Self::None => "none",
        }
    }
}

/// Default resources offered for a job. Every value is optional: nothing here
/// may be invented per site; values come from the profile or from read-only
/// discovery the user accepted.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, Default)]
pub struct JobDefaults {
    #[serde(default)]
    pub partition: Option<String>,
    #[serde(default)]
    pub account: Option<String>,
    #[serde(default)]
    pub qos: Option<String>,
    #[serde(default)]
    pub gres: Option<String>,
    #[serde(default)]
    pub time_limit: Option<String>,
    #[serde(default)]
    pub memory: Option<String>,
    #[serde(default)]
    pub cpus_per_task: Option<u32>,
}

/// How to prepare the environment on compute nodes.
///
/// Compute nodes may be offline, so a job script may only install or download
/// something when `compute_has_internet` is true for this server.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "lowercase")]
pub enum EnvironmentKind {
    #[default]
    None,
    Modules,
    Conda,
    Venv,
    Container,
}

impl EnvironmentKind {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::None => "none",
            Self::Modules => "modules",
            Self::Conda => "conda",
            Self::Venv => "venv",
            Self::Container => "container",
        }
    }
}

/// Environment setup for one server, as chosen (or accepted) by the user.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, Default)]
pub struct EnvironmentSetup {
    #[serde(default)]
    pub kind: EnvironmentKind,
    #[serde(default)]
    pub modules: Vec<String>,
    /// Interpreter (or `conda run`/`apptainer exec` prefix) used to run code.
    #[serde(default)]
    pub interpreter: Option<String>,
    #[serde(default)]
    pub container_image: Option<String>,
    /// How to invoke coperceive on the compute node, as an argv list.
    #[serde(default)]
    pub coperceive_command: Vec<String>,
    /// Directory with pre-downloaded wheels for offline installs.
    #[serde(default)]
    pub wheelhouse: Option<String>,
    #[serde(default)]
    pub compute_has_internet: bool,
}

/// One cluster the user connected. Hosts, users, roots, partitions and the
/// environment are per-server profile values, never product defaults.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, Default)]
pub struct ServerProfile {
    #[serde(default)]
    pub name: String,
    /// Alias from the user's `~/.ssh/config`. When set, ssh resolves host, user
    /// and jump hosts itself; the fields below are only fallbacks/overrides.
    #[serde(default)]
    pub ssh_alias: Option<String>,
    #[serde(default)]
    pub host: Option<String>,
    #[serde(default)]
    pub user: Option<String>,
    #[serde(default)]
    pub data_roots: Vec<String>,
    /// Scratch location template, e.g. `/scratch/$USER` or `$SCRATCH`.
    #[serde(default)]
    pub scratch_template: Option<String>,
    #[serde(default)]
    pub scheduler: SchedulerKind,
    #[serde(default)]
    pub defaults: JobDefaults,
    #[serde(default)]
    pub environment: EnvironmentSetup,
    #[serde(default)]
    pub notes: Option<String>,
}

/// Argument value kind for a pipeline argument.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "lowercase")]
pub enum ArgumentKind {
    #[default]
    Str,
    Int,
    Float,
    Bool,
    Path,
    Enum,
}

/// One declared pipeline argument; drives the generic job form.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, Default)]
pub struct ArgumentSpec {
    pub name: String,
    #[serde(default)]
    pub kind: ArgumentKind,
    #[serde(default)]
    pub help: String,
    #[serde(default)]
    pub required: bool,
    #[serde(default)]
    pub default: Option<String>,
    #[serde(default)]
    pub choices: Vec<String>,
    #[serde(default)]
    pub multiple: bool,
}

/// One pipeline runnable on a server. Slice 0 owns the full shape; slice 1 only
/// needs enough to render a generic job script.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, Default)]
pub struct PipelineSpec {
    #[serde(default)]
    pub name: String,
    #[serde(default)]
    pub script: String,
    #[serde(default)]
    pub interpreter: Vec<String>,
    #[serde(default)]
    pub environment: EnvironmentSetup,
    #[serde(default)]
    pub arguments: Vec<ArgumentSpec>,
}

/// A discovered partition with its limits and GPU types.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, Default)]
pub struct PartitionInfo {
    pub name: String,
    #[serde(default)]
    pub is_default: bool,
    #[serde(default)]
    pub available: bool,
    #[serde(default)]
    pub max_walltime: Option<String>,
    #[serde(default)]
    pub cpus: Option<u32>,
    #[serde(default)]
    pub memory_mb: Option<u64>,
    #[serde(default)]
    pub gres: Option<String>,
    #[serde(default)]
    pub gpu_types: Vec<String>,
}

/// Read-only scheduler discovery result.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, Default)]
pub struct SchedulerDiscovery {
    #[serde(default)]
    pub scheduler: SchedulerKind,
    #[serde(default)]
    pub partitions: Vec<PartitionInfo>,
    /// Plain-language note when a scheduler was found but is not supported yet.
    #[serde(default)]
    pub note: Option<String>,
}

/// Handle for one submitted job. Only recorded jobs may be queried, cancelled
/// or read; the app never touches jobs it did not create.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, Default)]
pub struct JobHandle {
    #[serde(default)]
    pub job_id: String,
    #[serde(default)]
    pub job_name: String,
    #[serde(default)]
    pub log_path: Option<String>,
}

/// Snapshot of a job's state.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, Default)]
pub struct JobStatus {
    #[serde(default)]
    pub state: String,
    #[serde(default)]
    pub reason: Option<String>,
    #[serde(default)]
    pub elapsed: Option<String>,
    #[serde(default)]
    pub time_left: Option<String>,
    #[serde(default)]
    pub node: Option<String>,
}

/// A job in the user's own queue that this app recorded creating.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, Default)]
pub struct JobSummary {
    #[serde(default)]
    pub job_id: String,
    #[serde(default)]
    pub job_name: String,
    #[serde(default)]
    pub state: String,
    #[serde(default)]
    pub reason: Option<String>,
    #[serde(default)]
    pub elapsed: Option<String>,
    #[serde(default)]
    pub time_left: Option<String>,
    #[serde(default)]
    pub node: Option<String>,
}

/// Error from a scheduler adapter, in plain words for the UI.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SchedulerError {
    pub message: String,
}

impl SchedulerError {
    pub fn new(message: impl Into<String>) -> Self {
        Self {
            message: message.into(),
        }
    }
}

impl std::fmt::Display for SchedulerError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}", self.message)
    }
}

impl std::error::Error for SchedulerError {}

/// Adapter implemented by each scheduler backend (Slurm today, PBS/LSF later).
///
/// Every method is explicit so a new scheduler cannot half-work: discovery,
/// script rendering, submission, status, cancellation, logs and the recorded
/// own-job list. Implementations must use argv lists and return errors, never
/// panic or silently no-op.
pub trait SchedulerAdapter {
    fn discover(&self) -> Result<SchedulerDiscovery, SchedulerError>;
    fn render_job_script(&self, spec: &PipelineSpec) -> Result<String, SchedulerError>;
    fn submit(&self, script: &str, job_name: &str) -> Result<JobHandle, SchedulerError>;
    fn status(&self, job: &JobHandle) -> Result<JobStatus, SchedulerError>;
    fn cancel(&self, job: &JobHandle) -> Result<(), SchedulerError>;
    fn logs(&self, job: &JobHandle, max_lines: usize) -> Result<String, SchedulerError>;
    fn list_own_jobs(&self) -> Result<Vec<JobSummary>, SchedulerError>;
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn profile_json_round_trips_with_snake_case_keys() {
        let profile = ServerProfile {
            name: "Cluster A".to_string(),
            ssh_alias: Some("cluster-a".to_string()),
            data_roots: vec!["/data/scenes".to_string()],
            scratch_template: Some("/scratch/$USER".to_string()),
            scheduler: SchedulerKind::Slurm,
            defaults: JobDefaults {
                partition: Some("gpu-a100".to_string()),
                gres: Some("gpu:a100:1".to_string()),
                time_limit: Some("02:00:00".to_string()),
                memory: Some("16G".to_string()),
                cpus_per_task: Some(4),
                ..Default::default()
            },
            environment: EnvironmentSetup {
                kind: EnvironmentKind::Modules,
                modules: vec!["python/3.12".to_string()],
                compute_has_internet: false,
                ..Default::default()
            },
            ..Default::default()
        };
        let json = serde_json::to_string(&profile).expect("serialize");
        assert!(json.contains("\"scheduler\":\"slurm\""));
        assert!(json.contains("\"kind\":\"modules\""));
        assert!(json.contains("\"time_limit\":\"02:00:00\""));
        let back: ServerProfile = serde_json::from_str(&json).expect("deserialize");
        assert_eq!(back, profile);
    }

    #[test]
    fn scheduler_kind_defaults_to_none() {
        assert_eq!(SchedulerKind::default(), SchedulerKind::None);
        let profile: ServerProfile = serde_json::from_str("{}").expect("empty profile");
        assert_eq!(profile.scheduler, SchedulerKind::None);
        assert_eq!(profile.environment.kind, EnvironmentKind::None);
        assert!(!profile.environment.compute_has_internet);
    }
}
