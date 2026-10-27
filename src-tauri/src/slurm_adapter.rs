// SPDX-License-Identifier: Apache-2.0
//! Slurm implementation of the [`SchedulerAdapter`] interface, plus the
//! explicit no-scheduler adapter.
//!
//! Only read-only discovery commands are used (`sinfo`, `scontrol show
//! partition`); submissions always receive the script on stdin, never on a
//! command line, and every job name carries the `coperceive-` prefix. The
//! adapter can only inspect/cancel jobs it recorded.

use std::time::Duration;

use crate::hpc::{
    EnvironmentKind, JobHandle, JobStatus, JobSummary, PartitionInfo, PipelineSpec, SchedulerAdapter,
    SchedulerDiscovery, SchedulerError, SchedulerKind, ServerProfile,
};
use crate::ssh::{run_ssh_command, shell_quote, CommandResult, SshTarget};

pub const JOB_PREFIX: &str = "coperceive-";
const LOG_DIR: &str = "~/.coperceive/logs";

/// Runs one remote command. Implemented by SSH in production and by fixtures in
/// tests, so the adapter is testable without a cluster.
pub trait RemoteRunner: Send + Sync {
    fn run(
        &self,
        remote_cmd: &str,
        timeout: Duration,
        stdin: Option<&str>,
    ) -> Result<CommandResult, String>;
}

/// SSH-backed runner. The remote command line is built locally with quoted
/// arguments (or fixed scripts), never from raw user input.
pub struct SshRunner {
    pub ssh_bin: String,
    pub target: SshTarget,
}

impl SshRunner {
    pub fn new(ssh_bin: impl Into<String>, target: SshTarget) -> Self {
        Self {
            ssh_bin: ssh_bin.into(),
            target,
        }
    }
}

impl RemoteRunner for SshRunner {
    fn run(
        &self,
        remote_cmd: &str,
        timeout: Duration,
        stdin: Option<&str>,
    ) -> Result<CommandResult, String> {
        run_ssh_command(&self.ssh_bin, &self.target, remote_cmd, timeout, stdin)
            .map_err(|e| e.to_string())
    }
}

/// Fixed script emitted to the remote shell. The marker lets fixtures and logs
/// identify the check without parsing user data.
pub const SCHEDULER_DETECT_SCRIPT: &str = "sh -c '# coperceive-check:scheduler\nif command -v sbatch >/dev/null 2>&1; then echo slurm; elif command -v qsub >/dev/null 2>&1; then echo pbs; elif command -v bsub >/dev/null 2>&1; then echo lsf; else echo none; fi'";

/// `sinfo` format: partition, availability, max walltime, cpus/node, memory/NODE, gres.
pub const SINFO_PARTITIONS_SCRIPT: &str = "sh -c '# coperceive-check:sinfo\nsinfo -h -o \"%P|%a|%l|%c|%m|%G\" 2>/dev/null'";
pub const SCONTROL_PARTITIONS_SCRIPT: &str = "sh -c '# coperceive-check:scontrol\nscontrol show partition 2>/dev/null'";

/// One partition line as printed by `sinfo -h -o "%P|%a|%l|%c|%m|%G"`.
fn parse_sinfo_line(line: &str) -> Option<PartitionInfo> {
    let parts: Vec<&str> = line.split('|').map(str::trim).collect();
    if parts.len() < 6 || parts[0].is_empty() {
        return None;
    }
    let raw_name = parts[0];
    let is_default = raw_name.ends_with('*');
    let name = raw_name.trim_end_matches('*').to_string();
    let available = !matches!(parts[1].to_lowercase().as_str(), "down" | "drain" | "inact" | "inactive");
    let max_walltime = match parts[2] {
        "" | "INVALID" | "UNLIMITED" | "N/A" => None,
        other => Some(other.to_string()),
    };
    let cpus = parts[3].parse::<u32>().ok();
    let memory_mb = parts[4].parse::<u64>().ok();
    let gres = match parts[5] {
        "" | "(null)" | "N/A" | "NONE" => None,
        other => Some(other.to_string()),
    };
    let gpu_types = gres
        .as_deref()
        .map(parse_gpu_types)
        .unwrap_or_default();
    Some(PartitionInfo {
        name,
        is_default,
        available,
        max_walltime,
        cpus,
        memory_mb,
        gres,
        gpu_types,
    })
}

/// Extracts GPU model names from a Slurm GRES string such as
/// `gpu:a100:4`, `gpu:4`, `gpu:h100:2(S:0-1)` or `gpu:a100:1,gpu:h100:1`.
pub fn parse_gpu_types(gres: &str) -> Vec<String> {
    let mut types: Vec<String> = Vec::new();
    for token in gres.split(',') {
        let base = token.split('(').next().unwrap_or(token).trim();
        if base.is_empty() || base == "(null)" {
            continue;
        }
        let parts: Vec<&str> = base.split(':').collect();
        if !parts[0].eq_ignore_ascii_case("gpu") {
            continue;
        }
        let candidate = match parts.len() {
            0 => None,
            1 => Some("gpu"),
            2 => {
                if parts[1].chars().all(|c| c.is_ascii_digit()) {
                    Some("gpu")
                } else {
                    Some(parts[1])
                }
            }
            _ => {
                if parts[1].chars().all(|c| c.is_ascii_digit()) {
                    Some("gpu")
                } else {
                    Some(parts[1])
                }
            }
        };
        if let Some(name) = candidate {
            let name = name.trim();
            if !name.is_empty() && !types.iter().any(|t| t == name) {
                types.push(name.to_string());
            }
        }
    }
    types
}

pub fn parse_sinfo_output(output: &str) -> Vec<PartitionInfo> {
    output.lines().filter_map(parse_sinfo_line).collect()
}

/// Parses `scontrol show partition` blocks. This fallback is used when `sinfo`
/// is unavailable; GPU types are not exposed by this command, so the caller
/// must tell the user to pick a GPU type explicitly.
pub fn parse_scontrol_partition_output(output: &str) -> Vec<PartitionInfo> {
    let mut partitions: Vec<PartitionInfo> = Vec::new();
    let mut current: Option<PartitionInfo> = None;
    for line in output.lines() {
        let trimmed = line.trim();
        if trimmed.is_empty() {
            if let Some(p) = current.take() {
                partitions.push(p);
            }
            continue;
        }
        for token in trimmed.split_whitespace() {
            let Some((key, value)) = token.split_once('=') else {
                continue;
            };
            match key {
                "PartitionName" if !value.is_empty() => {
                    if let Some(p) = current.take() {
                        partitions.push(p);
                    }
                    current = Some(PartitionInfo {
                        name: value.to_string(),
                        available: true,
                        ..Default::default()
                    });
                }
                "State" => {
                    if let Some(p) = current.as_mut() {
                        p.available = !matches!(value.to_uppercase().as_str(), "DOWN" | "DRAIN" | "INACTIVE");
                    }
                }
                "Default" => {
                    if let Some(p) = current.as_mut() {
                        p.is_default = value.eq_ignore_ascii_case("YES");
                    }
                }
                "MaxTime" => {
                    if let Some(p) = current.as_mut() {
                        p.max_walltime = match value {
                            "" | "UNLIMITED" | "NONE" => None,
                            other => Some(other.to_string()),
                        };
                    }
                }
                "MaxCPUsPerNode" | "MaxNodes" => {
                    // MaxCPUsPerNode is a per-node cap we can offer only when numeric.
                }
                "TRES" => {
                    if let Some(p) = current.as_mut() {
                        for item in value.split(',') {
                            if let Some(cpu) = item.strip_prefix("cpu=") {
                                p.cpus = cpu.parse::<u32>().ok().or(p.cpus);
                            } else if let Some(mem) = item.strip_prefix("mem=") {
                                p.memory_mb = mem
                                    .trim_end_matches('M')
                                    .parse::<u64>()
                                    .ok()
                                    .or(p.memory_mb);
                            }
                        }
                    }
                }
                _ => {}
            }
        }
    }
    if let Some(p) = current.take() {
        partitions.push(p);
    }
    partitions
}

/// Slurm adapter. `profile` supplies the partition/account/QoS/resources; no
/// site value is ever hard-coded here.
pub struct SlurmAdapter {
    runner: Box<dyn RemoteRunner>,
    profile: ServerProfile,
}

impl SlurmAdapter {
    pub fn new(runner: Box<dyn RemoteRunner>, profile: ServerProfile) -> Self {
        Self { runner, profile }
    }

    fn plain_error(&self, action: &str, detail: &str) -> SchedulerError {
        SchedulerError::new(format!(
            "Could not {action} on this cluster. {detail} If this keeps happening, check that \
             Slurm works in a terminal on the login node."
        ))
    }
}

impl SchedulerAdapter for SlurmAdapter {
    fn discover(&self) -> Result<SchedulerDiscovery, SchedulerError> {
        let mut note = None;
        let sinfo = self
            .runner
            .run(SINFO_PARTITIONS_SCRIPT, Duration::from_secs(10), None)
            .map_err(|e| self.plain_error("list partitions", &e))?;
        let mut partitions = parse_sinfo_output(&sinfo.stdout);
        if partitions.is_empty() {
            // `sinfo` can be unavailable on a gateway node; `scontrol show
            // partition` is the documented read-only fallback.
            if let Ok(scontrol) = self.runner.run(
                SCONTROL_PARTITIONS_SCRIPT,
                Duration::from_secs(10),
                None,
            ) {
                partitions = parse_scontrol_partition_output(&scontrol.stdout);
                if !partitions.is_empty() {
                    note = Some(
                        "Partitions were read with `scontrol show partition`; GPU types may need to \
                         be chosen manually for this cluster."
                            .to_string(),
                    );
                }
            }
        }
        if partitions.is_empty() {
            note = Some(
                "The cluster did not report any partitions. You can still add one manually under \
                 the server's resources."
                    .to_string(),
            );
        }
        Ok(SchedulerDiscovery {
            scheduler: SchedulerKind::Slurm,
            partitions,
            note,
        })
    }

    fn render_job_script(&self, spec: &PipelineSpec) -> Result<String, SchedulerError> {
        let safe_name = sanitize_job_name(&spec.name)?;
        if self.profile.scheduler != SchedulerKind::Slurm {
            return Err(SchedulerError::new(
                "This server is not set up as a Slurm cluster.",
            ));
        }
        let defaults = &self.profile.defaults;
        let partition = defaults.partition.as_deref().map(str::trim).filter(|p| !p.is_empty()).ok_or_else(|| {
            SchedulerError::new(
                "Choose a partition for this server before submitting a job. Partitions are listed \
                 under the server's resources.",
            )
        })?;

        let mut lines: Vec<String> = Vec::new();
        lines.push("#!/bin/bash".to_string());
        lines.push("# Generated by Coperceive for this server's profile.".to_string());
        lines.push(format!("#SBATCH --job-name={JOB_PREFIX}{safe_name}"));
        lines.push(format!("#SBATCH --partition={partition}"));
        if let Some(cpus) = defaults.cpus_per_task {
            lines.push(format!("#SBATCH --cpus-per-task={cpus}"));
        }
        if let Some(memory) = defaults.memory.as_deref() {
            lines.push(format!("#SBATCH --mem={memory}"));
        }
        if let Some(time_limit) = defaults.time_limit.as_deref() {
            lines.push(format!("#SBATCH --time={time_limit}"));
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
        lines.push(String::new());
        lines.push("set -euo pipefail".to_string());
        lines.push(String::new());
        // Logs go to the user's own per-user directory, never a shared path.
        lines.push(format!(
            "LOG_DIR=\"$HOME/.coperceive/logs\"\nmkdir -p -m 0700 \"$LOG_DIR\"\nexec > \"$LOG_DIR/${{SLURM_JOB_NAME:-coperceive-job}}-${{SLURM_JOB_ID:-0}}.log\" 2>&1"
        ));
        lines.push(String::new());

        let env = &self.profile.environment;
        if !env.wheelhouse.as_deref().unwrap_or("").is_empty() || !env.compute_has_internet {
            if let Some(wheelhouse) = env.wheelhouse.as_deref().filter(|w| !w.is_empty()) {
                lines.push(format!(
                    "# Preinstalled wheels only; compute nodes do not download packages.\nexport PIP_FIND_LINKS={}\nexport PIP_NO_INDEX=1\nexport COPERCEIVE_WHEELHOUSE={}",
                    shell_quote(wheelhouse),
                    shell_quote(wheelhouse)
                ));
                lines.push(String::new());
            }
        }
        if env.kind == EnvironmentKind::Modules && !env.modules.is_empty() {
            lines.push(
                "if ! command -v module >/dev/null 2>&1; then\n  echo \"This server expects a module system, but 'module' is not available on the compute node. Ask the cluster administrators, or change the server's environment settings.\" >&2\n  exit 1\nfi"
                    .to_string(),
            );
            for module in &env.modules {
                lines.push(format!("module load {}", shell_quote(module)));
            }
            lines.push(String::new());
        }
        if env.kind == EnvironmentKind::Conda {
            lines.push(
                "if ! command -v conda >/dev/null 2>&1; then\n  echo \"This server expects conda, but conda is not available on the compute node. Change the server's environment settings.\" >&2\n  exit 1\nfi"
                    .to_string(),
            );
            if let Some(interpreter) = env.interpreter.as_deref().filter(|p| !p.is_empty()) {
                let base = std::path::Path::new(interpreter)
                    .parent()
                    .map(|p| p.to_string_lossy().to_string())
                    .unwrap_or_default();
                lines.push(format!(
                    "export COPERCEIVE_PYTHON={}\nexport PATH={}:$PATH",
                    shell_quote(interpreter),
                    shell_quote(&base)
                ));
            }
            lines.push(String::new());
        }

        let mut command: Vec<String> = Vec::new();
        if let Some(interpreter) = env.interpreter.as_deref().filter(|p| !p.trim().is_empty()) {
            command.push(interpreter.to_string());
        }
        command.extend(spec.interpreter.iter().cloned());
        if !spec.script.trim().is_empty() {
            command.push(spec.script.clone());
        }
        if command.is_empty() {
            return Err(SchedulerError::new(
                "This job has no command to run. Choose a pipeline and an interpreter.",
            ));
        }
        let rendered_command = command
            .iter()
            .map(|arg| shell_quote(arg))
            .collect::<Vec<_>>()
            .join(" ");
        match env.kind {
            EnvironmentKind::Container => {
                let image = env
                    .container_image
                    .as_deref()
                    .filter(|p| !p.trim().is_empty())
                    .ok_or_else(|| {
                        SchedulerError::new(
                            "This server uses a container environment but no container image is \
                             set. Add the image path in the server's environment settings.",
                        )
                    })?;
                lines.push(format!(
                    "exec apptainer exec --cleanenv {} {}",
                    shell_quote(image),
                    rendered_command
                ));
            }
            _ => lines.push(format!("exec {rendered_command}")),
        }
        lines.push(String::new());
        Ok(lines.join("\n"))
    }

    fn submit(&self, script: &str, job_name: &str) -> Result<JobHandle, SchedulerError> {
        let safe_name = sanitize_job_name(job_name)?;
        let rendered_name = format!("{JOB_PREFIX}{safe_name}");
        let result = self
            .runner
            .run(
                "sbatch --parsable",
                Duration::from_secs(20),
                Some(script),
            )
            .map_err(|e| self.plain_error("submit the job", &e))?;
        if !result.success {
            return Err(self.plain_error(
                "submit the job",
                &format!("Slurm said: {}", result.stderr.trim()),
            ));
        }
        let job_id = result
            .stdout
            .trim()
            .split(';')
            .next()
            .unwrap_or("")
            .trim()
            .to_string();
        if job_id.is_empty() || !job_id.chars().all(|c| c.is_ascii_digit()) {
            return Err(self.plain_error(
                "submit the job",
                "Slurm did not return a job number.",
            ));
        }
        Ok(JobHandle {
            log_path: Some(format!(
                "{LOG_DIR}/{rendered_name}-{job_id}.log"
            )),
            job_id,
            job_name: rendered_name,
        })
    }

    fn status(&self, job: &JobHandle) -> Result<JobStatus, SchedulerError> {
        validate_recorded_job(job)?;
        let squeue = self
            .runner
            .run(
                &format!(
                    "squeue -h -j {} -o '%T|%r|%M|%L|%N' 2>/dev/null",
                    job.job_id
                ),
                Duration::from_secs(10),
                None,
            )
            .map_err(|e| self.plain_error("check the job", &e))?;
        if let Some(line) = squeue.stdout.lines().find(|l| !l.trim().is_empty()) {
            let parts: Vec<&str> = line.split('|').map(str::trim).collect();
            return Ok(JobStatus {
                state: parts.first().unwrap_or(&"").to_string(),
                reason: parts.get(1).map(|s| s.to_string()).filter(|s| s != "None"),
                elapsed: parts.get(2).map(|s| s.to_string()),
                time_left: parts.get(3).map(|s| s.to_string()),
                node: parts.get(4).map(|s| s.to_string()).filter(|s| !s.is_empty()),
            });
        }
        // No longer queued: ask the accounting database, still read-only.
        let sacct = self.runner.run(
            &format!("sacct -j {} -n -P -o State 2>/dev/null", job.job_id),
            Duration::from_secs(10),
            None,
        );
        let state = sacct
            .ok()
            .and_then(|out| {
                out.stdout
                    .lines()
                    .next()
                    .map(|l| l.trim().trim_end_matches('+').to_string())
            })
            .filter(|s| !s.is_empty())
            .unwrap_or_else(|| "COMPLETED".to_string());
        Ok(JobStatus {
            state,
            ..Default::default()
        })
    }

    fn cancel(&self, job: &JobHandle) -> Result<(), SchedulerError> {
        validate_recorded_job(job)?;
        let result = self
            .runner
            .run(
                &format!("scancel {}", job.job_id),
                Duration::from_secs(10),
                None,
            )
            .map_err(|e| self.plain_error("cancel the job", &e))?;
        if !result.success {
            return Err(self.plain_error(
                "cancel the job",
                &format!("Slurm said: {}", result.stderr.trim()),
            ));
        }
        Ok(())
    }

    fn logs(&self, job: &JobHandle, max_lines: usize) -> Result<String, SchedulerError> {
        validate_recorded_job(job)?;
        let path = job.log_path.as_deref().ok_or_else(|| {
            SchedulerError::new("Coperceive has no log file recorded for this job.")
        })?;
        let relative = path.strip_prefix("~/").ok_or_else(|| {
            SchedulerError::new("The recorded log file is not inside the user's home folder.")
        })?;
        if relative.chars().any(|c| c.is_control()) {
            return Err(SchedulerError::new("The recorded log file path is not valid."));
        }
        let lines = max_lines.clamp(1, 5000);
        let result = self
            .runner
            .run(
                &format!("tail -n {lines} -- \"$HOME/{relative}\" 2>/dev/null"),
                Duration::from_secs(10),
                None,
            )
            .map_err(|e| self.plain_error("read the job log", &e))?;
        if !result.success {
            return Err(self.plain_error(
                "read the job log",
                "The log file may not exist yet.",
            ));
        }
        Ok(result.stdout)
    }

    fn list_own_jobs(&self) -> Result<Vec<JobSummary>, SchedulerError> {
        let result = self
            .runner
            .run(
                "squeue -h -u \"$USER\" -o '%i|%j|%T|%r|%M|%L|%N' 2>/dev/null",
                Duration::from_secs(10),
                None,
            )
            .map_err(|e| self.plain_error("list your jobs", &e))?;
        if !result.success {
            return Err(self.plain_error(
                "list your jobs",
                &format!("Slurm said: {}", result.stderr.trim()),
            ));
        }
        let mut jobs = Vec::new();
        for line in result.stdout.lines() {
            let parts: Vec<&str> = line.split('|').map(str::trim).collect();
            if parts.len() < 4 {
                continue;
            }
            let job_name = parts[1];
            // Only jobs this app created and recorded are ever managed.
            if !job_name.starts_with(JOB_PREFIX) {
                continue;
            }
            jobs.push(JobSummary {
                job_id: parts[0].to_string(),
                job_name: job_name.to_string(),
                state: parts[2].to_string(),
                reason: parts.get(3).map(|s| s.to_string()).filter(|s| s != "None"),
                elapsed: parts.get(4).map(|s| s.to_string()),
                time_left: parts.get(5).map(|s| s.to_string()),
                node: parts.get(6).map(|s| s.to_string()).filter(|s| !s.is_empty()),
            });
        }
        Ok(jobs)
    }
}

/// Adapter for a server with no scheduler. Every operation explains the
/// limitation instead of silently doing nothing.
pub struct NoSchedulerAdapter;

impl NoSchedulerAdapter {
    fn unavailable(action: &str) -> SchedulerError {
        SchedulerError::new(format!(
            "This server has no scheduler, so Coperceive cannot {action}. Files can still be \
             opened directly over SSH."
        ))
    }
}

impl SchedulerAdapter for NoSchedulerAdapter {
    fn discover(&self) -> Result<SchedulerDiscovery, SchedulerError> {
        Ok(SchedulerDiscovery {
            scheduler: SchedulerKind::None,
            partitions: Vec::new(),
            note: Some(
                "No scheduler was found on this server. Coperceive can open files over SSH, but \
                 cannot submit jobs here."
                    .to_string(),
            ),
        })
    }

    fn render_job_script(&self, _spec: &PipelineSpec) -> Result<String, SchedulerError> {
        Err(Self::unavailable("build a job script"))
    }

    fn submit(&self, _script: &str, _job_name: &str) -> Result<JobHandle, SchedulerError> {
        Err(Self::unavailable("submit a job"))
    }

    fn status(&self, _job: &JobHandle) -> Result<JobStatus, SchedulerError> {
        Err(Self::unavailable("check a job"))
    }

    fn cancel(&self, _job: &JobHandle) -> Result<(), SchedulerError> {
        Err(Self::unavailable("cancel a job"))
    }

    fn logs(&self, _job: &JobHandle, _max_lines: usize) -> Result<String, SchedulerError> {
        Err(Self::unavailable("read job logs"))
    }

    fn list_own_jobs(&self) -> Result<Vec<JobSummary>, SchedulerError> {
        Err(Self::unavailable("list jobs"))
    }
}

fn sanitize_job_name(name: &str) -> Result<String, SchedulerError> {
    let trimmed = name.trim();
    if trimmed.is_empty() {
        return Err(SchedulerError::new(
            "Give this job a name using letters, numbers, dots, dashes or underscores.",
        ));
    }
    if !trimmed
        .chars()
        .all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_' || c == '.')
    {
        return Err(SchedulerError::new(
            "Job names may only use letters, numbers, dots, dashes and underscores.",
        ));
    }
    Ok(trimmed.to_string())
}

fn validate_recorded_job(job: &JobHandle) -> Result<(), SchedulerError> {
    if !job.job_name.starts_with(JOB_PREFIX) {
        return Err(SchedulerError::new(
            "Coperceive only manages jobs it created itself, and this job was not recorded by it.",
        ));
    }
    if job.job_id.is_empty() || !job.job_id.chars().all(|c| c.is_ascii_digit()) {
        return Err(SchedulerError::new("The recorded job number is not valid."));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Mutex;

    type CallLog = std::sync::Arc<Mutex<Vec<(String, Option<String>)>>>;

    #[derive(Default)]
    struct FakeRunner {
        calls: CallLog,
        responses: Mutex<Vec<(String, CommandResult)>>,
    }

    impl FakeRunner {
        fn calls_handle(&self) -> CallLog {
            std::sync::Arc::clone(&self.calls)
        }

        fn with(mut self, needle: &str, stdout: &str) -> Self {
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

    fn profile() -> ServerProfile {
        ServerProfile {
            name: "Cluster A".to_string(),
            ssh_alias: Some("cluster-a".to_string()),
            scheduler: SchedulerKind::Slurm,
            defaults: crate::hpc::JobDefaults {
                partition: Some("gpu-a100".to_string()),
                gres: Some("gpu:a100:1".to_string()),
                time_limit: Some("02:00:00".to_string()),
                memory: Some("16G".to_string()),
                cpus_per_task: Some(4),
                account: Some("research".to_string()),
                qos: Some("normal".to_string()),
            },
            environment: crate::hpc::EnvironmentSetup {
                kind: EnvironmentKind::Modules,
                modules: vec!["python/3.12".to_string()],
                interpreter: Some("/apps/python/3.12/bin/python".to_string()),
                compute_has_internet: false,
                ..Default::default()
            },
            ..Default::default()
        }
    }

    fn spec(name: &str) -> PipelineSpec {
        PipelineSpec {
            name: name.to_string(),
            script: "/data/pipelines/detect.py".to_string(),
            interpreter: vec![],
            environment: crate::hpc::EnvironmentSetup::default(),
            arguments: vec![],
        }
    }

    #[test]
    fn discover_parses_sinfo_partitions_and_gpu_types() {
        let runner = FakeRunner::default().with(
            "coperceive-check:sinfo",
            "gpu-a100*|up|2-00:00:00|8|64000|gpu:a100:4\ngpu-h100|up|1-00:00:00|16|128000|gpu:h100:2\ncpu|up|4:00:00|32|128000|(null)\ndrain|down|infinite|8|32000|(null)\n",
        );
        let adapter = SlurmAdapter::new(Box::new(runner), profile());
        let discovery = adapter.discover().expect("discover");
        assert_eq!(discovery.scheduler, SchedulerKind::Slurm);
        assert_eq!(discovery.partitions.len(), 4);
        assert_eq!(discovery.partitions[0].name, "gpu-a100");
        assert!(discovery.partitions[0].is_default);
        assert_eq!(discovery.partitions[0].gpu_types, vec!["a100"]);
        assert_eq!(discovery.partitions[1].gpu_types, vec!["h100"]);
        assert!(discovery.partitions[2].gpu_types.is_empty());
        assert!(!discovery.partitions[3].available);
    }

    #[test]
    fn discover_falls_back_to_scontrol_when_sinfo_is_empty() {
        let runner = FakeRunner::default()
            .with("coperceive-check:sinfo", "")
            .with(
                "coperceive-check:scontrol",
                "PartitionName=main-cpu\n   Default=YES MaxTime=2-00:00:00 State=UP TRES=cpu=64,mem=515000M,node=8\n\nPartitionName=gpu-h100\n   Default=NO MaxTime=1-00:00:00 State=UP TRES=cpu=32,mem=256000M,node=4\n",
            );
        let adapter = SlurmAdapter::new(Box::new(runner), profile());
        let discovery = adapter.discover().expect("discover");
        assert_eq!(discovery.partitions.len(), 2);
        assert_eq!(discovery.partitions[0].name, "main-cpu");
        assert!(discovery.partitions[0].is_default);
        assert_eq!(discovery.partitions[0].max_walltime.as_deref(), Some("2-00:00:00"));
        assert_eq!(discovery.partitions[1].name, "gpu-h100");
        assert!(discovery.note.is_some());
    }

    #[test]
    fn parse_gpu_types_handles_models_counts_and_multiple_gres() {
        assert_eq!(parse_gpu_types("gpu:a100:4"), vec!["a100"]);
        assert_eq!(parse_gpu_types("gpu:4"), vec!["gpu"]);
        assert_eq!(parse_gpu_types("gpu:h100:2(S:0-1)"), vec!["h100"]);
        assert_eq!(parse_gpu_types("gpu:a100:1,gpu:h100:1"), vec!["a100", "h100"]);
        assert!(parse_gpu_types("(null)").is_empty());
        assert!(parse_gpu_types("").is_empty());
    }

    #[test]
    fn render_job_script_uses_profile_values_only_and_no_site_default() {
        let runner = FakeRunner::default();
        let adapter = SlurmAdapter::new(Box::new(runner), profile());
        let script = adapter.render_job_script(&spec("detect")).expect("render");
        assert!(script.contains("#SBATCH --job-name=coperceive-detect"));
        assert!(script.contains("#SBATCH --partition=gpu-a100"));
        assert!(script.contains("#SBATCH --gres=gpu:a100:1"));
        assert!(script.contains("#SBATCH --account=research"));
        assert!(script.contains("#SBATCH --qos=normal"));
        assert!(script.contains("module load python/3.12"));
        assert!(script.contains("exec /apps/python/3.12/bin/python /data/pipelines/detect.py"));
        // Offline compute nodes: no installs or downloads.
        for forbidden in ["pip install", "conda create", "conda install", "git clone", "curl ", "wget "] {
            assert!(
                !script.contains(forbidden),
                "script must not contain {forbidden:?} for an offline cluster:\n{script}"
            );
        }
        // No token or secret is ever written into a script.
        let lower = script.to_lowercase();
        assert!(!lower.contains("token"));
        assert!(!lower.contains("password"));
        assert!(!lower.contains("secret"));
    }

    #[test]
    fn render_job_script_requires_a_chosen_partition_instead_of_inventing_one() {
        let runner = FakeRunner::default();
        let mut profile = profile();
        profile.defaults.partition = None;
        let adapter = SlurmAdapter::new(Box::new(runner), profile);
        let err = adapter.render_job_script(&spec("detect")).unwrap_err();
        assert!(err.message.contains("Choose a partition"));
    }

    #[test]
    fn render_job_script_rejects_unsafe_job_names() {
        let runner = FakeRunner::default();
        let adapter = SlurmAdapter::new(Box::new(runner), profile());
        let err = adapter
            .render_job_script(&spec("bad; touch /tmp/x"))
            .unwrap_err();
        assert!(err.message.contains("Job names may only use"));
    }

    #[test]
    fn render_job_script_quotes_wheelhouse_path_with_spaces_and_semicolon() {
        let runner = FakeRunner::default();
        let mut profile = profile();
        profile.environment.wheelhouse =
            Some("/data/wheels dir/it's; touch /tmp/x".to_string());
        let adapter = SlurmAdapter::new(Box::new(runner), profile);
        let script = adapter.render_job_script(&spec("detect")).expect("render");
        assert!(script.contains("COPERCEIVE_WHEELHOUSE='/data/wheels dir/it'\\''s; touch /tmp/x'"));
        assert!(!script.contains("\n; touch /tmp/x"));
    }

    #[test]
    fn submit_sends_script_on_stdin_and_records_prefixed_job() {
        let runner = FakeRunner::default().with("sbatch --parsable", "44321;cluster-a\n");
        let calls_handle = runner.calls_handle();
        let adapter = SlurmAdapter::new(Box::new(runner), profile());
        let script = "#!/bin/bash\necho hi\n";
        let handle = adapter.submit(script, "detect").expect("submit");
        assert_eq!(handle.job_id, "44321");
        assert_eq!(handle.job_name, "coperceive-detect");
        assert_eq!(
            handle.log_path.as_deref(),
            Some("~/.coperceive/logs/coperceive-detect-44321.log")
        );

        let calls = calls_handle.lock().unwrap().clone();
        let submit_call = calls
            .iter()
            .find(|(cmd, _)| cmd.contains("sbatch --parsable"))
            .expect("sbatch call");
        assert_eq!(submit_call.1.as_deref(), Some(script));
        assert!(
            !submit_call.0.contains("echo hi"),
            "script content must not be on the command line"
        );
    }

    #[test]
    fn cancel_and_status_only_accept_recorded_prefixed_jobs() {
        let runner = FakeRunner::default();
        let adapter = SlurmAdapter::new(Box::new(runner), profile());
        let foreign = JobHandle {
            job_id: "123".to_string(),
            job_name: "someone-elses-job".to_string(),
            log_path: None,
        };
        assert!(adapter.status(&foreign).is_err());
        assert!(adapter.cancel(&foreign).is_err());
        assert!(adapter.logs(&foreign, 10).is_err());
    }

    #[test]
    fn list_own_jobs_filters_to_coperceive_names() {
        let runner = FakeRunner::default().with(
            "squeue -h -u",
            "101|coperceive-detect|RUNNING|None|00:01:00|01:59:00|node01\n102|other-job|RUNNING|None|00:01:00|01:59:00|node02\n",
        );
        let adapter = SlurmAdapter::new(Box::new(runner), profile());
        let jobs = adapter.list_own_jobs().expect("list");
        assert_eq!(jobs.len(), 1);
        assert_eq!(jobs[0].job_id, "101");
    }

    #[test]
    fn status_parses_squeue_and_falls_back_to_sacct() {
        let runner = FakeRunner::default()
            .with("squeue -h -j 555", "RUNNING|None|00:10:00|01:50:00|node01\n")
            .with("sacct -j 556", "COMPLETED\n");
        let adapter = SlurmAdapter::new(Box::new(runner), profile());
        let running = adapter
            .status(&JobHandle {
                job_id: "555".to_string(),
                job_name: "coperceive-detect".to_string(),
                log_path: None,
            })
            .expect("status");
        assert_eq!(running.state, "RUNNING");
        assert_eq!(running.node.as_deref(), Some("node01"));
        let ended = adapter
            .status(&JobHandle {
                job_id: "556".to_string(),
                job_name: "coperceive-detect".to_string(),
                log_path: None,
            })
            .expect("status ended");
        assert_eq!(ended.state, "COMPLETED");
    }

    #[test]
    fn no_scheduler_adapter_explains_every_unavailable_operation() {
        let adapter = NoSchedulerAdapter;
        let discovery = adapter.discover().expect("discover");
        assert_eq!(discovery.scheduler, SchedulerKind::None);
        for message in [
            adapter.render_job_script(&spec("x")).unwrap_err().message,
            adapter.submit("", "x").unwrap_err().message,
            adapter
                .status(&JobHandle::default())
                .unwrap_err()
                .message,
            adapter.cancel(&JobHandle::default()).unwrap_err().message,
            adapter.logs(&JobHandle::default(), 10).unwrap_err().message,
            adapter.list_own_jobs().unwrap_err().message,
        ] {
            assert!(message.contains("no scheduler"), "{message}");
        }
    }

}
