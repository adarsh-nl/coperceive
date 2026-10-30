// SPDX-License-Identifier: Apache-2.0
//! Slurm implementation of the slice-0 [`SchedulerAdapter`], plus the explicit
//! no-scheduler adapter.
//!
//! Discovery is read-only (`sinfo`, with `scontrol show partition` as the
//! documented fallback). A submitted script always travels on stdin, never on a
//! command line. Job names must already carry the `coperceive-` prefix, and only
//! jobs with that prefix are listed as ours.

use std::time::Duration;

use crate::hpc::{
    DiscoveredPartition, Discovery, EnvironmentKind, JobRequest, JobState, SchedulerAdapter,
    SchedulerKind, ServerProfile,
};
use crate::ssh::{run_ssh_command, shell_quote, CommandResult, SshTarget};

pub const JOB_PREFIX: &str = "coperceive-";

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

/// `sinfo` format: partition, availability, max walltime, nodes, gres.
pub const SINFO_PARTITIONS_SCRIPT: &str = "sh -c '# coperceive-check:sinfo\nsinfo -h -o \"%P|%a|%l|%D|%G\" 2>/dev/null'";
pub const SCONTROL_PARTITIONS_SCRIPT: &str = "sh -c '# coperceive-check:scontrol\nscontrol show partition 2>/dev/null'";

/// `sinfo -h` can print one row per (partition, node-GRES) combination. The UI
/// offers one choice per partition, so merge rows with the same name: union the
/// GPU types and sum the node counts for that partition.
fn merge_partitions(partitions: Vec<DiscoveredPartition>) -> Vec<DiscoveredPartition> {
    let mut merged: Vec<DiscoveredPartition> = Vec::new();
    for partition in partitions {
        if let Some(existing) = merged.iter_mut().find(|item| item.name == partition.name) {
            for gpu in partition.gpu_types {
                if !existing.gpu_types.contains(&gpu) {
                    existing.gpu_types.push(gpu);
                }
            }
            existing.nodes = match (existing.nodes, partition.nodes) {
                (Some(left), Some(right)) => Some(left + right),
                (left, right) => left.or(right),
            };
            if existing.max_time.is_none() {
                existing.max_time = partition.max_time;
            }
            if existing.gres.is_none() {
                existing.gres = partition.gres;
            }
            if partition.state.as_deref() == Some("down") {
                existing.state = Some("down".to_string());
            }
        } else {
            merged.push(partition);
        }
    }
    merged
}

fn parse_sinfo_line(line: &str) -> Option<DiscoveredPartition> {
    let parts: Vec<&str> = line.split('|').map(str::trim).collect();
    if parts.len() < 5 || parts[0].is_empty() {
        return None;
    }
    let name = parts[0].trim_end_matches('*').to_string();
    let state = match parts[1].to_lowercase().as_str() {
        "" => None,
        "up" => Some("up".to_string()),
        "down" | "drain" | "inact" | "inactive" => Some("down".to_string()),
        other => Some(other.to_string()),
    };
    let max_time = match parts[2] {
        "" | "INVALID" | "UNLIMITED" | "N/A" => None,
        other => Some(other.to_string()),
    };
    let nodes = parts[3].parse::<u64>().ok();
    let gres = match parts[4] {
        "" | "(null)" | "N/A" | "NONE" => None,
        other => Some(other.to_string()),
    };
    let gpu_types = gres.as_deref().map(parse_gpu_types).unwrap_or_default();
    Some(DiscoveredPartition {
        name,
        nodes,
        state,
        max_time,
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
        if !parts.first().is_some_and(|p| p.eq_ignore_ascii_case("gpu")) {
            continue;
        }
        let candidate = match parts.len() {
            0 | 1 => Some("gpu"),
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

pub fn parse_sinfo_output(output: &str) -> Vec<DiscoveredPartition> {
    output.lines().filter_map(parse_sinfo_line).collect()
}

/// Parses `scontrol show partition` blocks. This fallback is used when `sinfo`
/// is unavailable; `scontrol` does not expose GPU types per partition, so the
/// user must choose a GPU type explicitly in that case.
pub fn parse_scontrol_partition_output(output: &str) -> Vec<DiscoveredPartition> {
    let mut partitions: Vec<DiscoveredPartition> = Vec::new();
    let mut current: Option<DiscoveredPartition> = None;
    for line in output.lines() {
        let trimmed = line.trim();
        if trimmed.is_empty() {
            if let Some(partition) = current.take() {
                partitions.push(partition);
            }
            continue;
        }
        for token in trimmed.split_whitespace() {
            let Some((key, value)) = token.split_once('=') else {
                continue;
            };
            match key {
                "PartitionName" if !value.is_empty() => {
                    if let Some(partition) = current.take() {
                        partitions.push(partition);
                    }
                    current = Some(empty_partition(value));
                }
                "State" => {
                    if let Some(partition) = current.as_mut() {
                        partition.state = Some(match value.to_uppercase().as_str() {
                            "UP" => "up".to_string(),
                            "DOWN" | "DRAIN" | "INACTIVE" => "down".to_string(),
                            other => other.to_lowercase(),
                        });
                    }
                }
                "MaxTime" => {
                    if let Some(partition) = current.as_mut() {
                        partition.max_time = match value {
                            "" | "UNLIMITED" | "NONE" => None,
                            other => Some(other.to_string()),
                        };
                    }
                }
                "TotalNodes" | "Nodes" => {
                    if let Some(partition) = current.as_mut() {
                        partition.nodes = value.parse::<u64>().ok().or(partition.nodes);
                    }
                }
                _ => {}
            }
        }
    }
    if let Some(partition) = current.take() {
        partitions.push(partition);
    }
    partitions
}

fn empty_partition(name: &str) -> DiscoveredPartition {
    DiscoveredPartition {
        name: name.to_string(),
        nodes: None,
        state: None,
        max_time: None,
        gres: None,
        gpu_types: Vec::new(),
    }
}

/// Maps a Slurm state word to the shared job state.
pub fn map_slurm_state(state: &str) -> JobState {
    match state.trim().to_lowercase().trim_end_matches('+') {
        "pending" | "configuring" | "requeued" | "suspended" | "resizing" => JobState::Pending,
        "running" | "completing" | "stage_out" | "signaling" => JobState::Running,
        "completed" => JobState::Completed,
        "cancelled" | "canceled" => JobState::Cancelled,
        "failed" | "timeout" | "node_fail" | "preempted" | "out_of_memory" | "boot_fail"
        | "deadline" => JobState::Failed,
        _ => JobState::Unknown,
    }
}

fn validate_job_id(job_id: &str) -> Result<(), String> {
    if job_id.is_empty() || !job_id.chars().all(|c| c.is_ascii_digit()) {
        return Err(format!(
            "The job number {job_id:?} is not a valid Slurm job id."
        ));
    }
    Ok(())
}

fn validate_job_name(name: &str) -> Result<(), String> {
    let trimmed = name.trim();
    if !trimmed.starts_with(JOB_PREFIX) {
        return Err(format!(
            "App job names must start with {JOB_PREFIX:?} so Coperceive can find the jobs it \
             created; got {name:?}."
        ));
    }
    if !trimmed
        .chars()
        .all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_' || c == '.')
    {
        return Err(
            "Job names may only use letters, numbers, dots, dashes and underscores.".to_string(),
        );
    }
    Ok(())
}

/// Slurm adapter. Resources come from the profile/request the user confirmed;
/// nothing here supplies a site default.
pub struct SlurmAdapter {
    runner: Box<dyn RemoteRunner>,
}

impl SlurmAdapter {
    pub fn new(runner: Box<dyn RemoteRunner>) -> Self {
        Self { runner }
    }

    fn plain_error(&self, action: &str, detail: &str) -> String {
        format!(
            "Could not {action} on this cluster. {detail} If this keeps happening, check that \
             Slurm works in a terminal on the login node."
        )
    }
}

impl SchedulerAdapter for SlurmAdapter {
    fn discover(&self) -> Result<Discovery, String> {
        let sinfo = self
            .runner
            .run(SINFO_PARTITIONS_SCRIPT, Duration::from_secs(10), None)?;
        let mut partitions = merge_partitions(parse_sinfo_output(&sinfo.stdout));
        if partitions.is_empty() {
            // `sinfo` can be unavailable on a gateway node; `scontrol show
            // partition` is the documented read-only fallback.
            let scontrol = self.runner.run(
                SCONTROL_PARTITIONS_SCRIPT,
                Duration::from_secs(10),
                None,
            )?;
            partitions = parse_scontrol_partition_output(&scontrol.stdout);
        }
        let mut gpu_types: Vec<String> = Vec::new();
        for partition in &partitions {
            for gpu in &partition.gpu_types {
                if !gpu_types.iter().any(|existing| existing == gpu) {
                    gpu_types.push(gpu.clone());
                }
            }
        }
        Ok(Discovery {
            scheduler: SchedulerKind::Slurm,
            partitions,
            gpu_types,
        })
    }

    fn render_job_script(
        &self,
        profile: &ServerProfile,
        request: &JobRequest,
    ) -> Result<String, String> {
        profile.validate()?;
        request.validate()?;
        validate_job_name(&request.job_name)?;
        if profile.scheduler != SchedulerKind::Slurm {
            return Err("This server is not set up as a Slurm cluster.".to_string());
        }
        let defaults = &profile.defaults;
        let partition = request
            .partition
            .clone()
            .or_else(|| defaults.partition.clone())
            .filter(|partition| !partition.trim().is_empty())
            .ok_or_else(|| {
                "Choose a partition for this job (or set one on the server profile) before \
                 submitting. Partitions are listed under the server's resources."
                    .to_string()
            })?;
        let account = request.account.clone().or_else(|| defaults.account.clone());
        let qos = request.qos.clone().or_else(|| defaults.qos.clone());
        let gres = request.gres.clone().or_else(|| defaults.gres.clone());
        let time_limit = request
            .time_limit
            .clone()
            .or_else(|| defaults.time_limit.clone());
        let memory = request.memory.clone().or_else(|| defaults.memory.clone());
        let cpus = request.cpus_per_task.or(defaults.cpus_per_task);

        let mut lines: Vec<String> = Vec::new();
        lines.push("#!/bin/bash".to_string());
        lines.push("# Generated by Coperceive for this server's profile.".to_string());
        lines.push(format!("#SBATCH --job-name={}", request.job_name));
        lines.push(format!("#SBATCH --partition={partition}"));
        if let Some(cpus) = cpus {
            lines.push(format!("#SBATCH --cpus-per-task={cpus}"));
        }
        if let Some(memory) = memory.as_deref().filter(|m| !m.trim().is_empty()) {
            lines.push(format!("#SBATCH --mem={memory}"));
        }
        if let Some(time_limit) = time_limit.as_deref().filter(|t| !t.trim().is_empty()) {
            lines.push(format!("#SBATCH --time={time_limit}"));
        }
        if let Some(gres) = gres.as_deref().filter(|g| !g.trim().is_empty()) {
            lines.push(format!("#SBATCH --gres={gres}"));
        }
        if let Some(account) = account.as_deref().filter(|a| !a.trim().is_empty()) {
            lines.push(format!("#SBATCH --account={account}"));
        }
        if let Some(qos) = qos.as_deref().filter(|q| !q.trim().is_empty()) {
            lines.push(format!("#SBATCH --qos={qos}"));
        }
        lines.push(String::new());
        lines.push("set -euo pipefail".to_string());
        lines.push(String::new());
        // Logs go to the user's own per-user directory, never a shared path.
        lines.push(
            "LOG_DIR=\"$HOME/.coperceive/logs\"\nmkdir -p -m 0700 \"$LOG_DIR\"\nexec > \
             \"$LOG_DIR/${SLURM_JOB_NAME:-coperceive-job}-${SLURM_JOB_ID:-0}.log\" 2>&1"
                .to_string(),
        );
        lines.push(String::new());

        let env = &profile.environment;
        if let Some(wheelhouse) = env.wheelhouse.as_deref().filter(|w| !w.is_empty()) {
            lines.push(format!(
                "# Preinstalled wheels only; compute nodes do not download packages.\nexport \
                 PIP_FIND_LINKS={}\nexport PIP_NO_INDEX=1\nexport COPERCEIVE_WHEELHOUSE={}",
                shell_quote(wheelhouse),
                shell_quote(wheelhouse)
            ));
            lines.push(String::new());
        }
        if env.kind == EnvironmentKind::Modules && !env.modules.is_empty() {
            lines.push(
                "if ! command -v module >/dev/null 2>&1; then\n  echo \"This server expects a \
                 module system, but 'module' is not available on the compute node. Ask the \
                 cluster administrators, or change the server's environment settings.\" >&2\n  \
                 exit 1\nfi"
                    .to_string(),
            );
            for module in &env.modules {
                lines.push(format!("module load {}", shell_quote(module)));
            }
            lines.push(String::new());
        }
        if matches!(env.kind, EnvironmentKind::Conda | EnvironmentKind::Venv) {
            if let Some(interpreter) = env.interpreter.as_deref().filter(|p| !p.trim().is_empty()) {
                let base = std::path::Path::new(interpreter)
                    .parent()
                    .map(|p| p.to_string_lossy().to_string())
                    .unwrap_or_default();
                lines.push(format!(
                    "export COPERCEIVE_PYTHON={}\nexport PATH={}:$PATH",
                    shell_quote(interpreter),
                    shell_quote(&base)
                ));
                lines.push(String::new());
            }
        }

        if request.command_argv.is_empty() {
            return Err(
                "This job has no command to run. Choose a pipeline and an interpreter."
                    .to_string(),
            );
        }
        let rendered_command = request
            .command_argv
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
                        "This server uses a container environment but no container image is set. \
                         Add the image path in the server's environment settings."
                            .to_string()
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

    fn submit(&self, script: &str) -> Result<String, String> {
        let result = self
            .runner
            .run("sbatch --parsable", Duration::from_secs(20), Some(script))
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
        validate_job_id(&job_id).map_err(|_| {
            self.plain_error("submit the job", "Slurm did not return a job number.")
        })?;
        Ok(job_id)
    }

    fn status(&self, job_id: &str) -> Result<JobState, String> {
        validate_job_id(job_id)?;
        let squeue = self
            .runner
            .run(
                &format!("squeue -h -j {job_id} -o '%T' 2>/dev/null"),
                Duration::from_secs(10),
                None,
            )
            .map_err(|e| self.plain_error("check the job", &e))?;
        if let Some(line) = squeue.stdout.lines().find(|line| !line.trim().is_empty()) {
            return Ok(map_slurm_state(line));
        }
        // No longer queued: ask the accounting database, still read-only.
        let sacct = self
            .runner
            .run(
                &format!("sacct -j {job_id} -n -P -o State 2>/dev/null"),
                Duration::from_secs(10),
                None,
            )
            .map_err(|e| self.plain_error("check the job", &e))?;
        match sacct.stdout.lines().next() {
            Some(state) if !state.trim().is_empty() => Ok(map_slurm_state(state)),
            _ => Ok(JobState::Unknown),
        }
    }

    fn cancel(&self, job_id: &str) -> Result<(), String> {
        validate_job_id(job_id)?;
        let result = self
            .runner
            .run(&format!("scancel {job_id}"), Duration::from_secs(10), None)
            .map_err(|e| self.plain_error("cancel the job", &e))?;
        if !result.success {
            return Err(self.plain_error(
                "cancel the job",
                &format!("Slurm said: {}", result.stderr.trim()),
            ));
        }
        Ok(())
    }

    fn logs(&self, job_id: &str) -> Result<String, String> {
        validate_job_id(job_id)?;
        let result = self
            .runner
            .run(
                &format!(
                    "tail -n 200 -- \"$HOME\"/.coperceive/logs/coperceive-*\"{job_id}\".log \
                     2>/dev/null"
                ),
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

    fn list_own_jobs(&self) -> Result<Vec<(String, JobState)>, String> {
        let result = self
            .runner
            .run(
                "squeue -h -u \"$USER\" -o '%i|%j|%T' 2>/dev/null",
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
            if parts.len() < 3 {
                continue;
            }
            // Only jobs this app created are ever managed.
            if !parts[1].starts_with(JOB_PREFIX) {
                continue;
            }
            jobs.push((parts[0].to_string(), map_slurm_state(parts[2])));
        }
        Ok(jobs)
    }
}

/// Adapter for a server with no scheduler. Matches the slice-0 `NullAdapter`:
/// discovery is empty, status is unknown, everything that would start or manage
/// work explains the limitation instead of doing nothing silently.
pub struct NoSchedulerAdapter;

impl SchedulerAdapter for NoSchedulerAdapter {
    fn discover(&self) -> Result<Discovery, String> {
        Ok(Discovery {
            scheduler: SchedulerKind::None,
            partitions: Vec::new(),
            gpu_types: Vec::new(),
        })
    }

    fn render_job_script(&self, _: &ServerProfile, _: &JobRequest) -> Result<String, String> {
        Err(
            "This server has no scheduler, so Coperceive will not run heavy work on it. Run \
             the command in a terminal on the server yourself; Coperceive can still read data \
             over SSH."
                .to_string(),
        )
    }

    fn submit(&self, _: &str) -> Result<String, String> {
        Err(
            "This server has no scheduler, so Coperceive will not submit jobs on it. Ask the \
             cluster administrators how to run jobs there."
                .to_string(),
        )
    }

    fn status(&self, _: &str) -> Result<JobState, String> {
        Ok(JobState::Unknown)
    }

    fn cancel(&self, _: &str) -> Result<(), String> {
        Err("This server has no scheduler, so there is no job to cancel.".to_string())
    }

    fn logs(&self, _: &str) -> Result<String, String> {
        Err("This server has no scheduler, so Coperceive has no job logs to read.".to_string())
    }

    fn list_own_jobs(&self) -> Result<Vec<(String, JobState)>, String> {
        Ok(Vec::new())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::hpc::{EnvironmentSetup, JobDefaults};
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
            host: None,
            user: None,
            data_roots: vec!["/data/scenes".to_string()],
            scratch_template: "/scratch/$USER/$SLURM_JOB_ID".to_string(),
            results_root: "/data/results".to_string(),
            code_roots: Vec::new(),
            scheduler: SchedulerKind::Slurm,
            defaults: JobDefaults {
                partition: Some("gpu-a100".to_string()),
                gres: Some("gpu:a100:1".to_string()),
                time_limit: Some("02:00:00".to_string()),
                memory: Some("16G".to_string()),
                cpus_per_task: Some(4),
                account: Some("research".to_string()),
                qos: Some("normal".to_string()),
            },
            environment: EnvironmentSetup {
                kind: EnvironmentKind::Modules,
                modules: vec!["python/3.12".to_string()],
                interpreter: Some("/apps/python/3.12/bin/python".to_string()),
                compute_has_internet: false,
                ..EnvironmentSetup::default()
            },
            notes: None,
        }
    }

    fn request() -> JobRequest {
        JobRequest {
            job_name: "coperceive-detect".to_string(),
            command_argv: vec![
                "/apps/python/3.12/bin/python".to_string(),
                "/data/scenes/pipelines/detect.py".to_string(),
            ],
            partition: None,
            account: None,
            qos: None,
            gres: None,
            time_limit: None,
            memory: None,
            cpus_per_task: None,
        }
    }

    #[test]
    fn discover_parses_sinfo_partitions_and_gpu_types() {
        let runner = FakeRunner::default().with(
            "coperceive-check:sinfo",
            "gpu-a100*|up|2-00:00:00|8|gpu:a100:4\ngpu-h100|up|1-00:00:00|16|gpu:h100:2\ncpu|up|4:00:00|32|(null)\ndrain|down|infinite|8|(null)\n",
        );
        let adapter = SlurmAdapter::new(Box::new(runner));
        let discovery = adapter.discover().expect("discover");
        assert_eq!(discovery.scheduler, SchedulerKind::Slurm);
        assert_eq!(discovery.partitions.len(), 4);
        assert_eq!(discovery.partitions[0].name, "gpu-a100");
        assert_eq!(discovery.partitions[0].nodes, Some(8));
        assert_eq!(discovery.partitions[0].gpu_types, vec!["a100"]);
        assert_eq!(discovery.partitions[1].gpu_types, vec!["h100"]);
        assert!(discovery.partitions[2].gpu_types.is_empty());
        assert_eq!(discovery.partitions[3].state.as_deref(), Some("down"));
        assert_eq!(discovery.gpu_types, vec!["a100", "h100"]);
    }

    #[test]
    fn discover_merges_rows_for_one_partition_and_unions_gpu_types() {
        let runner = FakeRunner::default().with(
            "coperceive-check:sinfo",
            "main-cpu|up|4:00:00|8|gpu:a100:2\nmain-cpu|up|4:00:00|4|gpu:h100:1\ncpu|up|4:00:00|32|(null)\n",
        );
        let adapter = SlurmAdapter::new(Box::new(runner));
        let discovery = adapter.discover().expect("discover");
        assert_eq!(discovery.partitions.len(), 2);
        let main = &discovery.partitions[0];
        assert_eq!(main.name, "main-cpu");
        assert_eq!(main.gpu_types, vec!["a100", "h100"]);
        assert_eq!(main.nodes, Some(12));
        assert_eq!(discovery.gpu_types, vec!["a100", "h100"]);
    }

    #[test]
    fn discover_falls_back_to_scontrol_when_sinfo_is_empty() {
        let runner = FakeRunner::default()
            .with("coperceive-check:sinfo", "")
            .with(
                "coperceive-check:scontrol",
                "PartitionName=main-cpu\n   Default=YES MaxTime=2-00:00:00 Nodes=8 State=UP TotalCPUs=64\n\nPartitionName=gpu-h100\n   Default=NO MaxTime=1-00:00:00 Nodes=4 State=UP\n",
            );
        let adapter = SlurmAdapter::new(Box::new(runner));
        let discovery = adapter.discover().expect("discover");
        assert_eq!(discovery.partitions.len(), 2);
        assert_eq!(discovery.partitions[0].name, "main-cpu");
        assert_eq!(
            discovery.partitions[0].max_time.as_deref(),
            Some("2-00:00:00")
        );
        assert_eq!(discovery.partitions[1].name, "gpu-h100");
        assert_eq!(discovery.partitions[1].nodes, Some(4));
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
        let adapter = SlurmAdapter::new(Box::new(runner));
        let script = adapter
            .render_job_script(&profile(), &request())
            .expect("render");
        assert!(script.contains("#SBATCH --job-name=coperceive-detect"));
        assert!(script.contains("#SBATCH --partition=gpu-a100"));
        assert!(script.contains("#SBATCH --gres=gpu:a100:1"));
        assert!(script.contains("#SBATCH --account=research"));
        assert!(script.contains("#SBATCH --qos=normal"));
        assert!(script.contains("module load python/3.12"));
        assert!(script
            .contains("exec /apps/python/3.12/bin/python /data/scenes/pipelines/detect.py"));
        // Offline compute nodes: no installs or downloads.
        for forbidden in [
            "pip install",
            "conda create",
            "conda install",
            "git clone",
            "curl ",
            "wget ",
        ] {
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
        let adapter = SlurmAdapter::new(Box::new(runner));
        let mut profile = profile();
        profile.defaults.partition = None;
        let err = adapter
            .render_job_script(&profile, &request())
            .expect_err("partition required");
        assert!(err.contains("Choose a partition"));
    }

    #[test]
    fn render_job_script_rejects_unsafe_job_names_and_foreign_prefix() {
        let runner = FakeRunner::default();
        let adapter = SlurmAdapter::new(Box::new(runner));
        let mut bad = request();
        bad.job_name = "bad; touch /tmp/x".to_string();
        let err = adapter.render_job_script(&profile(), &bad).unwrap_err();
        assert!(err.contains("coperceive-"));
        bad.job_name = "coperceive-bad;name".to_string();
        let err = adapter.render_job_script(&profile(), &bad).unwrap_err();
        assert!(err.contains("Job names may only use"));
    }

    #[test]
    fn render_job_script_quotes_wheelhouse_path_with_spaces_and_semicolon() {
        let runner = FakeRunner::default();
        let adapter = SlurmAdapter::new(Box::new(runner));
        let mut profile = profile();
        profile.environment.wheelhouse = Some("/data/wheels dir/it's; touch /tmp/x".to_string());
        let script = adapter
            .render_job_script(&profile, &request())
            .expect("render");
        assert!(script.contains("COPERCEIVE_WHEELHOUSE='/data/wheels dir/it'\\''s; touch /tmp/x'"));
        assert!(!script.contains("\n; touch /tmp/x"));
    }

    #[test]
    fn submit_sends_script_on_stdin_and_returns_the_job_id() {
        let runner = FakeRunner::default().with("sbatch --parsable", "44321;cluster-a\n");
        let calls_handle = runner.calls_handle();
        let adapter = SlurmAdapter::new(Box::new(runner));
        let script = "#!/bin/bash\necho hi\n";
        let job_id = adapter.submit(script).expect("submit");
        assert_eq!(job_id, "44321");

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
    fn submit_rejects_non_numeric_job_id() {
        let runner = FakeRunner::default().with("sbatch --parsable", "not-a-job\n");
        let adapter = SlurmAdapter::new(Box::new(runner));
        assert!(adapter.submit("#!/bin/bash\n").is_err());
    }

    #[test]
    fn status_parses_squeue_and_falls_back_to_sacct() {
        let runner = FakeRunner::default()
            .with("squeue -h -j 555", "RUNNING\n")
            .with("sacct -j 556", "COMPLETED\n");
        let adapter = SlurmAdapter::new(Box::new(runner));
        assert_eq!(adapter.status("555").expect("status"), JobState::Running);
        assert_eq!(adapter.status("556").expect("status"), JobState::Completed);
        assert!(adapter.status("555; touch /tmp/x").is_err());
    }

    #[test]
    fn list_own_jobs_filters_to_coperceive_names() {
        let runner = FakeRunner::default().with(
            "squeue -h -u",
            "101|coperceive-detect|RUNNING\n102|other-job|RUNNING\n",
        );
        let adapter = SlurmAdapter::new(Box::new(runner));
        let jobs = adapter.list_own_jobs().expect("list");
        assert_eq!(jobs, vec![("101".to_string(), JobState::Running)]);
    }

    #[test]
    fn no_scheduler_adapter_matches_the_null_adapter_contract() {
        let adapter = NoSchedulerAdapter;
        let discovery = adapter.discover().expect("discover");
        assert_eq!(discovery.scheduler, SchedulerKind::None);
        assert!(discovery.partitions.is_empty());
        assert_eq!(adapter.status("1").expect("status"), JobState::Unknown);
        assert!(adapter.list_own_jobs().expect("list").is_empty());
        for message in [
            adapter
                .render_job_script(&profile(), &request())
                .unwrap_err(),
            adapter.submit("").unwrap_err(),
            adapter.cancel("1").unwrap_err(),
            adapter.logs("1").unwrap_err(),
        ] {
            assert!(message.contains("no scheduler"), "{message}");
        }
    }
}
