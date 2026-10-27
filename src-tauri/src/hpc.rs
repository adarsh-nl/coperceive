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
    pub scheduler: SchedulerKind,
    pub defaults: JobDefaults,
    pub environment: EnvironmentSetup,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub notes: Option<String>,
}

impl ServerProfile {
    /// The argv element to ssh to: the configured alias, or user@host.
    pub fn ssh_target(&self) -> Result<String, String> {
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
        if self.scratch_template.trim().is_empty() {
            return Err(
                "A per-user scratch path is required for job work, for example \
                 /scratch/$USER/$SLURM_JOB_ID."
                    .into(),
            );
        }
        self.defaults.validate()?;
        self.environment.validate()?;
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
        for (field, value) in [
            ("partition", &self.partition),
            ("account", &self.account),
            ("qos", &self.qos),
            ("gres", &self.gres),
        ] {
            if let Some(text) = value {
                if text.trim().is_empty() || text.chars().any(char::is_whitespace) {
                    return Err(format!("defaults.{field} must be non-empty and without spaces."));
                }
            }
        }
        if let Some(cpus) = self.cpus_per_task {
            if cpus < 1 {
                return Err("defaults.cpus_per_task must be at least 1.".into());
            }
        }
        Ok(())
    }
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
            return Err("coperceive_command must be non-empty argv elements.".into());
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
        if self.script.starts_with('/') || self.script.split('/').any(|part| part == "..") {
            return Err(
                "Pipeline script must stay inside the server's data roots; absolute paths \
                 and '..' are refused."
                    .into(),
            );
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
    fn render_job_script(&self, profile: &ServerProfile, request: &JobRequest) -> Result<String, String>;
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
            }],
            output_contract: "coperceive-pipeline-1".into(),
            description: String::new(),
        };
        spec.validate().expect("valid pipeline");
        let mut bad = spec.clone();
        bad.script = "/etc/passwd".into();
        assert!(bad.validate().is_err());
        bad.script = "pipelines/bev_fusion.py".into();
        bad.output_contract = "other".into();
        assert!(bad.validate().is_err());
        let text = serde_json::to_string(&spec).expect("json");
        let back: PipelineSpec = serde_json::from_str(&text).expect("back");
        assert_eq!(back, spec);
    }
}
