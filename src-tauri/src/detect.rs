// SPDX-License-Identifier: Apache-2.0
//! "Test connection": light, time-bounded read-only checks against one server.
//!
//! Every step is individually bounded and the whole check is designed to finish
//! in under a minute on any login node. Nothing is written on the cluster: all
//! commands are reads (`true`, `command -v`, `sinfo`, `scontrol show partition`,
//! `df`, `quota`, `coperceive --version`). Missing things are reported with a
//! plain-language explanation, never silently filled with a guessed default.

use std::collections::BTreeSet;
use std::time::{Duration, Instant};

use serde::{Deserialize, Serialize};

use crate::hpc::{
    DiscoveredPartition, EnvironmentSetup, SchedulerAdapter, SchedulerKind, ServerProfile,
};
use crate::server_profiles::{
    proposed_environment, DiscoveredEnvironment, DiscoveredServer, ServerEntry, SshHostSettings,
};
use crate::slurm_adapter::{SlurmAdapter, SshRunner, SCHEDULER_DETECT_SCRIPT};
use crate::ssh::{classify_ssh_error, run_ssh_g, shell_quote, CommandResult, SshTarget, SshErrorKind};

/// One line in the connection checklist shown to the user.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, Default)]
pub struct CheckReport {
    pub name: String,
    pub label: String,
    pub ok: bool,
    pub duration_ms: u64,
    pub detail: String,
    #[serde(default)]
    pub hint: Option<String>,
}

/// Per-user scratch result.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, Default)]
pub struct ScratchReport {
    #[serde(default)]
    pub path: Option<String>,
    pub writable: bool,
    #[serde(default)]
    pub free_bytes: Option<u64>,
    #[serde(default)]
    pub quota: Option<String>,
    pub detail: String,
}

/// Full report of one "Test connection".
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct DetectionReport {
    pub reachable: bool,
    pub signed_in: bool,
    pub scheduler: SchedulerKind,
    #[serde(default)]
    pub scheduler_note: Option<String>,
    #[serde(default)]
    pub partitions: Vec<DiscoveredPartition>,
    #[serde(default)]
    pub gpu_types: Vec<String>,
    #[serde(default)]
    pub environment: EnvironmentSetup,
    #[serde(default)]
    pub discovered: DiscoveredServer,
    #[serde(default)]
    pub scratch: ScratchReport,
    #[serde(default)]
    pub checks: Vec<CheckReport>,
    #[serde(default)]
    pub ssh: SshHostSettings,
    pub total_ms: u64,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DetectOptions {
    pub ssh_bin: String,
}

impl Default for DetectOptions {
    fn default() -> Self {
        Self {
            ssh_bin: std::env::var("COPERCEIVE_SSH_BIN").unwrap_or_else(|_| "ssh".to_string()),
        }
    }
}

const REACHABLE_TIMEOUT: Duration = Duration::from_secs(8);
const SCHEDULER_TIMEOUT: Duration = Duration::from_secs(8);
const ENVIRONMENT_TIMEOUT: Duration = Duration::from_secs(8);
const COPERCEIVE_TIMEOUT: Duration = Duration::from_secs(10);
const SCRATCH_TIMEOUT: Duration = Duration::from_secs(8);
const SSH_G_TIMEOUT: Duration = Duration::from_secs(4);

/// Fixed remote script: prints KEY=VALUE lines for login-node environment facts.
pub const ENVIRONMENT_SCRIPT: &str = r#"sh -c '# coperceive-check:environment
printf "MODULE=%s\n" "$(command -v module 2>/dev/null || true)"
printf "LMOD=%s\n" "${LMOD_CMD:-}"
printf "CONDA=%s\n" "$(command -v conda 2>/dev/null || true)"
printf "CONDA_ENV=%s\n" "${CONDA_DEFAULT_ENV:-}"
printf "VIRTUAL_ENV=%s\n" "${VIRTUAL_ENV:-}"
printf "PYTHON=%s\n" "$(command -v python3 2>/dev/null || true)"
printf "APPTAINER=%s\n" "$(command -v apptainer 2>/dev/null || true)"
printf "SINGULARITY=%s\n" "$(command -v singularity 2>/dev/null || true)"
printf "COPERCEIVE=%s\n" "$(command -v coperceive 2>/dev/null || true)"
printf "SCRATCH=%s\n" "${SCRATCH:-}"
printf "WORK=%s\n" "${WORK:-}"
printf "TMPDIR=%s\n" "${TMPDIR:-}"
printf "HOME=%s\n" "${HOME:-}"'"#;

/// Fixed remote script: prints the installed coperceive version, if any.
///
/// A per-user install often has no `coperceive` on PATH: it is only importable by
/// the configured interpreter (or as a script next to it). Probe those first,
/// then PATH, then the system python3.
pub fn coperceive_probe_script(interpreter: Option<&str>) -> String {
    let python = shell_quote(interpreter.map(str::trim).unwrap_or(""));
    format!(
        r#"sh -c '# coperceive-check:coperceive-version
if [ -n "$1" ]; then
  out=$("$1" -m coperceive --version 2>/dev/null | head -n 1)
  if [ -n "$out" ]; then printf "%s
" "$out"; exit 0; fi
  cand="$(dirname "$1")/coperceive"
  if [ -x "$cand" ]; then
    out=$("$cand" --version 2>/dev/null | head -n 1)
    if [ -n "$out" ]; then printf "%s
" "$out"; exit 0; fi
  fi
fi
if command -v coperceive >/dev/null 2>&1; then
  coperceive --version 2>/dev/null | head -n 1
  exit 0
fi
python3 -c "import importlib.metadata as m; print(m.version("coperceive"))" 2>/dev/null
' sh {python}"#
    )
}

/// Backwards-compatible constant for callers that only have PATH.
pub const COPERCEIVE_VERSION_SCRIPT: &str = r#"sh -c '# coperceive-check:coperceive-version
if command -v coperceive >/dev/null 2>&1; then
  coperceive --version 2>/dev/null | head -n 1
else
  python3 -c "import importlib.metadata as m; print(m.version(\"coperceive\"))" 2>/dev/null
fi'"#;

pub fn coperceive_version_script() -> String {
    coperceive_probe_script(None)
}

/// Builds the fixed remote script that checks one scratch directory.
pub fn scratch_check_script(path: &str) -> String {
    let quoted = crate::ssh::shell_quote(path);
    format!(
        "sh -c '# coperceive-check:scratch\nDIR={quoted}\n\
         if [ -d \"$DIR\" ] && [ -w \"$DIR\" ]; then\n\
           echo WRITABLE\n\
           df -Pk \"$DIR\" 2>/dev/null | tail -n 1\n\
           quota -s 2>/dev/null | head -n 4\n\
         else\n\
           echo NOT_WRITABLE\n\
         fi'"
    )
}

/// Runs `ssh -G <alias>` and returns effective settings, for the add-server form.
pub fn probe_ssh_alias(alias: &str, ssh_bin: &str) -> Result<SshHostSettings, String> {
    let alias = alias.trim();
    if alias.is_empty() {
        return Err("Choose an SSH host first.".to_string());
    }
    let result = run_ssh_g(ssh_bin, alias, SSH_G_TIMEOUT)
        .map_err(|e| format!("Could not read ~/.ssh/config for {alias}: {e}"))?;
    if !result.success {
        return Err(format!(
            "SSH could not read the settings for {alias} from ~/.ssh/config. Check the Host entry \
             in that file and try again."
        ));
    }
    Ok(crate::server_profiles::parse_ssh_g_output(alias, &result.stdout))
}

/// Convenience: builds a profile from an ssh config alias using `ssh -G`.
pub fn profile_for_alias(alias: &str, ssh_bin: &str) -> Result<(ServerProfile, SshHostSettings), String> {
    let settings = probe_ssh_alias(alias, ssh_bin)?;
    Ok((
        crate::server_profiles::profile_from_ssh_alias(alias, &settings),
        settings,
    ))
}

fn host_settings(entry: &ServerEntry, ssh_bin: &str) -> SshHostSettings {
    if let Some(settings) = &entry.ssh {
        return settings.clone();
    }
    match entry.raw_alias() {
        Some(alias) => match run_ssh_g(ssh_bin, alias, SSH_G_TIMEOUT) {
            Ok(result) if result.success => {
                crate::server_profiles::parse_ssh_g_output(alias, &result.stdout)
            }
            _ => SshHostSettings {
                alias: alias.to_string(),
                ..Default::default()
            },
        },
        None => SshHostSettings::default(),
    }
}

fn skipped_check(name: &str, label: &str, reason: impl Into<String>) -> CheckReport {
    CheckReport {
        name: name.to_string(),
        label: label.to_string(),
        ok: false,
        duration_ms: 0,
        detail: reason.into(),
        hint: Some("When sign-in works, run Test connection again.".to_string()),
    }
}

/// Parses `KEY=VALUE` output of [`ENVIRONMENT_SCRIPT`].
pub fn parse_environment_output(output: &str) -> DiscoveredEnvironment {
    let mut values: std::collections::BTreeMap<String, String> = std::collections::BTreeMap::new();
    for line in output.lines() {
        if let Some((key, value)) = line.split_once('=') {
            values.insert(key.trim().to_string(), value.trim().to_string());
        }
    }
    let get = |key: &str| values.get(key).filter(|v| !v.is_empty()).cloned();
    let module_system = get("MODULE").is_some() || get("LMOD").is_some();
    let mut scratch_candidates = Vec::new();
    for key in ["SCRATCH", "WORK", "TMPDIR"] {
        if let Some(value) = get(key) {
            scratch_candidates.push(value);
        }
    }
    DiscoveredEnvironment {
        module_system,
        conda: get("CONDA"),
        virtualenv: get("VIRTUAL_ENV"),
        python: get("PYTHON"),
        container_runtime: get("APPTAINER").or_else(|| get("SINGULARITY")),
        coperceive_path: get("COPERCEIVE"),
        coperceive_version: None,
        scratch_candidates,
    }
}

/// Expands `$USER`, `${USER}`, `$HOME` and `${HOME}` from facts we already read,
/// instead of asking a remote shell to expand user text (which would execute it).
pub fn expand_path_template(template: &str, user: Option<&str>, home: Option<&str>) -> String {
    let mut expanded = template.to_string();
    for (name, value) in [("USER", user), ("HOME", home)] {
        if let Some(value) = value {
            expanded = expanded
                .replace(&format!("${{{name}}}"), value)
                .replace(&format!("${name}"), value);
        }
    }
    expanded
}

fn infer_user(entry: &ServerEntry, settings: &SshHostSettings, home: Option<&str>) -> Option<String> {
    entry
        .profile
        .user
        .clone()
        .or_else(|| settings.user.clone())
        .or_else(|| {
            home.and_then(|h| h.rsplit('/').next().map(str::to_string))
                .filter(|u| !u.is_empty())
        })
}

/// Parses the output of [`scratch_check_script`].
pub fn parse_scratch_output(path: &str, output: &str) -> ScratchReport {
    let mut lines = output.lines().map(str::trim).filter(|l| !l.is_empty());
    let first = lines.next().unwrap_or("");
    if first != "WRITABLE" {
        return ScratchReport {
            path: Some(path.to_string()),
            writable: false,
            free_bytes: None,
            quota: None,
            detail: format!(
                "Coperceive could not write to {path}. It may not exist for your account, or it \
                 may be full or read-only."
            ),
        };
    }
    let mut free_bytes = None;
    let mut quota_lines = Vec::new();
    for line in lines {
        if free_bytes.is_none() && line.split_whitespace().count() >= 4 {
            let tokens: Vec<&str> = line.split_whitespace().collect();
            // df -Pk: Filesystem 1024-blocks Used Available Capacity Mounted-on
            if let Ok(available_kb) = tokens[3].parse::<u64>() {
                free_bytes = Some(available_kb.saturating_mul(1024));
                continue;
            }
        }
        quota_lines.push(line.to_string());
    }
    let quota = if quota_lines.is_empty() {
        None
    } else {
        Some(quota_lines.join("\n"))
    };
    let detail = match free_bytes {
        Some(bytes) => format!(
            "Scratch folder is writable, with about {:.1} GB free.",
            bytes as f64 / 1_073_741_824.0
        ),
        None => "Scratch folder is writable.".to_string(),
    };
    ScratchReport {
        path: Some(path.to_string()),
        writable: true,
        free_bytes,
        quota,
        detail,
    }
}

fn record(
    checks: &mut Vec<CheckReport>,
    name: &str,
    label: &str,
    started: Instant,
    ok: bool,
    detail: impl Into<String>,
    hint: Option<String>,
) {
    checks.push(CheckReport {
        name: name.to_string(),
        label: label.to_string(),
        ok,
        duration_ms: started.elapsed().as_millis() as u64,
        detail: detail.into(),
        hint,
    });
}

/// Runs the full read-only connection test for one server entry.
pub fn detect_server(entry: &ServerEntry, opts: &DetectOptions) -> DetectionReport {
    let started = Instant::now();
    let settings = host_settings(entry, &opts.ssh_bin);
    let mut checks: Vec<CheckReport> = Vec::new();
    let mut report = DetectionReport {
        reachable: false,
        signed_in: false,
        scheduler: SchedulerKind::None,
        scheduler_note: None,
        partitions: Vec::new(),
        gpu_types: Vec::new(),
        environment: EnvironmentSetup::default(),
        discovered: DiscoveredServer::default(),
        scratch: ScratchReport::default(),
        checks: Vec::new(),
        ssh: settings.clone(),
        total_ms: 0,
    };

    let target: SshTarget = match entry.ssh_target() {
        Ok(target) => target,
        Err(error) => {
            record(
                &mut checks,
                "target",
                "Server address",
                Instant::now(),
                false,
                error,
                Some("Add or fix the SSH host for this server.".to_string()),
            );
            report.checks = checks;
            report.total_ms = started.elapsed().as_millis() as u64;
            return report;
        }
    };

    // 1. Reachable and signed in: one BatchMode ssh command.
    let reach_started = Instant::now();
    let reach = crate::ssh::run_ssh_command(
        &opts.ssh_bin,
        &target,
        "true",
        REACHABLE_TIMEOUT,
        None,
    );
    let mut environment: Option<DiscoveredEnvironment> = None;
    let mut env_home: Option<String> = None;
    match reach {
        Ok(CommandResult { success: true, .. }) => {
            report.reachable = true;
            report.signed_in = true;
            record(
                &mut checks,
                "reachable",
                "Reachable and signed in",
                reach_started,
                true,
                "The cluster answered and your SSH key was accepted.",
                None,
            );
        }
        Ok(result) => {
            match classify_ssh_error(&result.stderr) {
                SshErrorKind::PublicKeyAuthRequired(detail) => {
                    report.reachable = true;
                    record(
                        &mut checks,
                        "reachable",
                        "Reachable and signed in",
                        reach_started,
                        false,
                        format!("The cluster is reachable, but it did not accept your SSH key. {detail}"),
                        Some(
                            "Run `ssh-add` in Terminal to load your key, then try again. If your \
                             cluster uses two-factor sign-in, SSH keys are still needed; ask your \
                             administrators which key to use."
                                .to_string(),
                        ),
                    );
                }
                SshErrorKind::EduVpnOrNetworkUnreachable(detail) => {
                    record(
                        &mut checks,
                        "reachable",
                        "Reachable",
                        reach_started,
                        false,
                        format!("We could not reach the cluster. {detail}"),
                        Some(
                            "Check that you are online and connected to the campus VPN (for \
                             example eduVPN), then try again."
                                .to_string(),
                        ),
                    );
                }
                other => {
                    record(
                        &mut checks,
                        "reachable",
                        "Reachable and signed in",
                        reach_started,
                        false,
                        other.to_string(),
                        Some(
                            "Check that SSH to this host works in Terminal, and that the host \
                             entry in ~/.ssh/config is correct."
                                .to_string(),
                        ),
                    );
                }
            }
        }
        Err(error) => {
            record(
                &mut checks,
                "reachable",
                "Reachable",
                reach_started,
                false,
                error.to_string(),
                Some(
                    "The cluster did not answer in time. Check your connection and the VPN, then \
                     try again."
                        .to_string(),
                ),
            );
        }
    }

    if !report.signed_in {
        checks.push(skipped_check(
            "scheduler",
            "Scheduler",
            "Skipped because sign-in did not succeed.",
        ));
        checks.push(skipped_check(
            "partitions",
            "Partitions and GPUs",
            "Skipped because sign-in did not succeed.",
        ));
        checks.push(skipped_check(
            "environment",
            "Environment",
            "Skipped because sign-in did not succeed.",
        ));
        checks.push(skipped_check(
            "scratch",
            "Scratch folder",
            "Skipped because sign-in did not succeed.",
        ));
        report.checks = checks;
        report.total_ms = started.elapsed().as_millis() as u64;
        return report;
    }

    // 2. Scheduler.
    let scheduler_started = Instant::now();
    let mut scheduler_kind = SchedulerKind::None;
    let mut scheduler_note: Option<String> = None;
    let mut partitions: Vec<DiscoveredPartition> = Vec::new();
    match crate::ssh::run_ssh_command(
        &opts.ssh_bin,
        &target,
        SCHEDULER_DETECT_SCRIPT,
        SCHEDULER_TIMEOUT,
        None,
    ) {
        Ok(result) if result.success => {
            let detected = result.stdout.lines().find(|l| !l.trim().is_empty());
            match detected.map(|l| l.trim().to_lowercase()) {
                Some(kind) if kind == "slurm" => {
                    scheduler_kind = SchedulerKind::Slurm;
                    record(
                        &mut checks,
                        "scheduler",
                        "Scheduler",
                        scheduler_started,
                        true,
                        "Slurm was detected.",
                        None,
                    );
                }
                Some(kind) if kind == "pbs" || kind == "lsf" => {
                    scheduler_note = Some(format!(
                        "This cluster runs {} job scheduling. Coperceive can open files today but \
                         only Slurm job submission is supported so far.",
                        kind.to_uppercase()
                    ));
                    record(
                        &mut checks,
                        "scheduler",
                        "Scheduler",
                        scheduler_started,
                        false,
                        scheduler_note.clone().unwrap_or_default(),
                        Some(
                            "You can still use this server for files. Job submission needs a \
                             Slurm cluster for now."
                                .to_string(),
                        ),
                    );
                }
                _ => {
                    scheduler_note = Some(
                        "No scheduler was found on this server. Coperceive can open files over \
                         SSH, but cannot submit jobs here."
                            .to_string(),
                    );
                    record(
                        &mut checks,
                        "scheduler",
                        "Scheduler",
                        scheduler_started,
                        true,
                        scheduler_note.clone().unwrap_or_default(),
                        None,
                    );
                }
            }
        }
        Ok(result) => {
            record(
                &mut checks,
                "scheduler",
                "Scheduler",
                scheduler_started,
                false,
                format!(
                    "Could not ask the cluster which scheduler it runs. {}",
                    result.stderr.trim()
                ),
                Some(
                    "Check that Slurm commands such as `sinfo` work in Terminal on the login node."
                        .to_string(),
                ),
            );
        }
        Err(error) => {
            record(
                &mut checks,
                "scheduler",
                "Scheduler",
                scheduler_started,
                false,
                error.to_string(),
                None,
            );
        }
    }

    // 3. Partitions and GPU types (read-only Slurm discovery).
    if scheduler_kind == SchedulerKind::Slurm {
        let partitions_started = Instant::now();
        let runner = SshRunner::new(opts.ssh_bin.clone(), target.clone());
        let adapter = SlurmAdapter::new(Box::new(runner));
        match adapter.discover() {
            Ok(discovery) => {
                partitions = discovery.partitions;
                if partitions.is_empty() {
                    scheduler_note = Some(
                        "The cluster did not report any partitions. You can still type one in the \
                         server's resources if you know it."
                            .to_string(),
                    );
                }
                if !partitions.is_empty() {
                    let total_gpu_types: BTreeSet<String> = partitions
                        .iter()
                        .flat_map(|p| p.gpu_types.iter().cloned())
                        .collect();
                    let summary = if total_gpu_types.is_empty() {
                        format!("Found {} partition(s).", partitions.len())
                    } else {
                        format!(
                            "Found {} partition(s) and GPU types: {}.",
                            partitions.len(),
                            total_gpu_types.into_iter().collect::<Vec<_>>().join(", ")
                        )
                    };
                    record(
                        &mut checks,
                        "partitions",
                        "Partitions and GPUs",
                        partitions_started,
                        true,
                        summary,
                        None,
                    );
                } else {
                    record(
                        &mut checks,
                        "partitions",
                        "Partitions and GPUs",
                        partitions_started,
                        false,
                        "The cluster did not report any partitions. You can type one in the \
                         server's resources if you know it.",
                        None,
                    );
                }
            }
            Err(error) => {
                record(
                    &mut checks,
                    "partitions",
                    "Partitions and GPUs",
                    partitions_started,
                    false,
                    error,
                    Some(
                        "Check that `sinfo` works in Terminal on the login node.".to_string(),
                    ),
                );
            }
        }
        // The adapter's discover runs `sinfo`; a successful first run already
        // proved the read-only path. Keep a single partitions check entry.
        checks.retain(|c| c.name != "sinfo-probe");
    } else {
        checks.push(skipped_check(
            "partitions",
            "Partitions and GPUs",
            match &scheduler_note {
                Some(note) => note.clone(),
                None => "This server has no scheduler, so there are no partitions to list."
                    .to_string(),
            },
        ));
    }

    // 4. Environment and coperceive install.
    let env_started = Instant::now();
    match crate::ssh::run_ssh_command(
        &opts.ssh_bin,
        &target,
        ENVIRONMENT_SCRIPT,
        ENVIRONMENT_TIMEOUT,
        None,
    ) {
        Ok(result) if result.success => {
            let parsed = parse_environment_output(&result.stdout);
            env_home = result
                .stdout
                .lines()
                .find_map(|l| l.strip_prefix("HOME="))
                .map(str::trim)
                .filter(|v| !v.is_empty())
                .map(str::to_string);
            let mut found: Vec<&str> = Vec::new();
            if parsed.module_system {
                found.push("module system");
            }
            if parsed.conda.is_some() {
                found.push("conda");
            }
            if parsed.virtualenv.is_some() {
                found.push("virtualenv");
            }
            if parsed.container_runtime.is_some() {
                found.push("container runtime");
            }
            if parsed.python.is_some() {
                found.push("python3");
            }
            let detail = if found.is_empty() {
                "No module system, conda, virtualenv or container runtime was found. You can \
                 still open files; set the environment manually in the server's settings."
                    .to_string()
            } else {
                format!("Found: {}.", found.join(", "))
            };
            record(
                &mut checks,
                "environment",
                "Environment",
                env_started,
                true,
                detail,
                None,
            );
            environment = Some(parsed);
        }
        Ok(result) => record(
            &mut checks,
            "environment",
            "Environment",
            env_started,
            false,
            format!("Could not inspect the environment. {}", result.stderr.trim()),
            None,
        ),
        Err(error) => record(
            &mut checks,
            "environment",
            "Environment",
            env_started,
            false,
            error.to_string(),
            None,
        ),
    }

    let mut discovered = DiscoveredServer {
        partitions: partitions.clone(),
        environment: environment.clone().unwrap_or_default(),
        checked_unix: now_unix(),
        gpu_types: partitions
            .iter()
            .flat_map(|p| p.gpu_types.iter().cloned())
            .collect::<BTreeSet<_>>()
            .into_iter()
            .collect(),
    };

    if let Some(env) = environment.as_mut() {
        let version_started = Instant::now();
        let configured_interpreter = entry
            .profile
            .environment
            .interpreter
            .as_deref()
            .map(str::trim)
            .filter(|value| !value.is_empty())
            .or(env.python.as_deref());
        match crate::ssh::run_ssh_command(
            &opts.ssh_bin,
            &target,
            &coperceive_probe_script(configured_interpreter),
            COPERCEIVE_TIMEOUT,
            None,
        ) {
            Ok(result) if result.success => {
                let version = result
                    .stdout
                    .lines()
                    .next()
                    .unwrap_or("")
                    .trim()
                    .trim_start_matches("coperceive")
                    .trim()
                    .to_string();
                if version.is_empty() {
                    let detail = if let Some(interpreter) = configured_interpreter {
                        format!(
                            "coperceive was not found through PATH or the interpreter {interpreter} \
                             (-m coperceive). You can install it yourself, or ask your cluster \
                             administrators for a preinstalled copy."
                        )
                    } else {
                        "coperceive was not found on this server. You can install it yourself, or \
                         ask your cluster administrators for a preinstalled copy."
                            .to_string()
                    };
                    record(
                        &mut checks,
                        "coperceive",
                        "Coperceive on the cluster",
                        version_started,
                        false,
                        detail,
                        Some(
                            "Coperceive never installs anything on compute nodes. Add its location \
                             in the server's environment settings once it is available."
                                .to_string(),
                        ),
                    );
                } else {
                    env.coperceive_version = Some(version.clone());
                    let app_version = env!("CARGO_PKG_VERSION");
                    let detail = if version == app_version {
                        format!("coperceive {version} is installed.")
                    } else {
                        format!(
                            "coperceive {version} is installed (this desktop app is {app_version}); \
                             the version difference is reported, not treated as a failure. \
                             Job-submission compatibility was not checked."
                        )
                    };
                    record(
                        &mut checks,
                        "coperceive",
                        "Coperceive on the cluster",
                        version_started,
                        true,
                        detail,
                        if version == app_version {
                            None
                        } else {
                            Some(
                                "If job submission later fails on the cluster, align the app and \
                                 cluster coperceive versions."
                                    .to_string(),
                            )
                        },
                    );
                }
            }
            _ => record(
                &mut checks,
                "coperceive",
                "Coperceive on the cluster",
                version_started,
                true,
                "coperceive is installed, but its version could not be read.",
                None,
            ),
        }
    }

    // 5. Per-user scratch, writable check plus space/quota if available.
    let scratch_started = Instant::now();
    let settings = host_settings(entry, &opts.ssh_bin);
    let configured_scratch = entry.profile.scratch_template.trim().to_string();
    let scratch_template = if configured_scratch.is_empty() {
        discovered
            .environment
            .scratch_candidates
            .first()
            .cloned()
    } else {
        Some(configured_scratch)
    };
    if let Some(template) = scratch_template {
        let user = infer_user(entry, &settings, env_home.as_deref());
        let path = expand_path_template(&template, user.as_deref(), env_home.as_deref());
        match crate::ssh::run_ssh_command(
            &opts.ssh_bin,
            &target,
            &scratch_check_script(&path),
            SCRATCH_TIMEOUT,
            None,
        ) {
            Ok(result) if result.success => {
                let scratch = parse_scratch_output(&path, &result.stdout);
                record(
                    &mut checks,
                    "scratch",
                    "Scratch folder",
                    scratch_started,
                    scratch.writable,
                    scratch.detail.clone(),
                    if scratch.writable {
                        None
                    } else {
                        Some(
                            "Choose a scratch folder your account can write to in the server's \
                             settings."
                                .to_string(),
                        )
                    },
                );
                report.scratch = scratch;
            }
            Ok(result) => {
                report.scratch = ScratchReport {
                    path: Some(path.clone()),
                    writable: false,
                    detail: format!(
                        "Could not check the scratch folder {path}. {}",
                        result.stderr.trim()
                    ),
                    ..Default::default()
                };
                record(
                    &mut checks,
                    "scratch",
                    "Scratch folder",
                    scratch_started,
                    false,
                    report.scratch.detail.clone(),
                    Some("Choose a scratch folder in the server's settings.".to_string()),
                );
            }
            Err(error) => {
                report.scratch = ScratchReport {
                    path: Some(path.clone()),
                    writable: false,
                    detail: error.to_string(),
                    ..Default::default()
                };
                record(
                    &mut checks,
                    "scratch",
                    "Scratch folder",
                    scratch_started,
                    false,
                    error.to_string(),
                    None,
                );
            }
        }
    } else {
        report.scratch = ScratchReport {
            path: None,
            writable: false,
            detail: "No per-user scratch folder was found. You can type one in the server's \
                     settings; Coperceive never assumes a shared location."
                .to_string(),
            ..Default::default()
        };
        record(
            &mut checks,
            "scratch",
            "Scratch folder",
            scratch_started,
            false,
            report.scratch.detail.clone(),
            Some("Type the scratch folder for your account in the server's settings.".to_string()),
        );
    }

    if let Some(env) = &environment {
        discovered.environment = env.clone();
    }
    let mut proposed = proposed_environment(&discovered.environment);
    // If the version probe succeeded through the interpreter the user chose
    // (and coperceive is not a separate PATH executable), the exact argv is
    // `<interpreter> -m coperceive`. The UI takes this detected argv instead
    // of rebuilding it from whatever python the environment probe found.
    if proposed.coperceive_command == vec!["coperceive".to_string()] {
        if let Some(interpreter) = entry
            .profile
            .environment
            .interpreter
            .as_deref()
            .map(str::trim)
            .filter(|value| !value.is_empty())
        {
            if discovered.environment.coperceive_version.is_some() {
                proposed.coperceive_command =
                    vec![interpreter.to_string(), "-m".to_string(), "coperceive".to_string()];
            }
        }
    }
    report.environment = proposed;
    if scheduler_kind == SchedulerKind::Slurm && report.environment.interpreter.is_none() {
        // SINFO discovery succeeded but no interpreter: leave it to the user.
    }
    report.scheduler = scheduler_kind;
    report.scheduler_note = scheduler_note;
    report.partitions = partitions;
    report.discovered = discovered;
    report.checks = checks;
    report.total_ms = started.elapsed().as_millis() as u64;
    report
}

fn now_unix() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::unix::fs::PermissionsExt;
    use std::path::PathBuf;

    const FAKE_SSH: &str = include_str!(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/tests/fixtures/hpc/fake_ssh.sh"
    ));
    const CLUSTER_A: &str = include_str!(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/tests/fixtures/hpc/cluster-a.json"
    ));
    const CLUSTER_B: &str = include_str!(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/tests/fixtures/hpc/cluster-b.json"
    ));
    const CLUSTER_NONE: &str = include_str!(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/tests/fixtures/hpc/cluster-none.json"
    ));
    const CLUSTER_SAVE: &str = include_str!(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/tests/fixtures/hpc/cluster-save.json"
    ));

    struct FakeSsh {
        dir: PathBuf,
        bin: String,
    }

    impl FakeSsh {
        fn create() -> Self {
            let dir = std::env::temp_dir().join(format!(
                "coperceive-fake-ssh-{}-{}",
                std::process::id(),
                std::time::SystemTime::now()
                    .duration_since(std::time::UNIX_EPOCH)
                    .map(|d| d.as_nanos())
                    .unwrap_or(0)
            ));
            std::fs::create_dir_all(&dir).expect("temp dir");
            let path = dir.join("fake_ssh.sh");
            std::fs::write(&path, FAKE_SSH).expect("write fake ssh");
            std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o755))
                .expect("chmod fake ssh");
            Self {
                bin: path.to_string_lossy().to_string(),
                dir,
            }
        }
    }

    impl Drop for FakeSsh {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.dir);
        }
    }

    fn entry_from_fixture(json: &str) -> ServerEntry {
        serde_json::from_str(json).expect("fixture parses as ServerEntry")
    }

    fn options(fake: &FakeSsh) -> DetectOptions {
        DetectOptions {
            ssh_bin: fake.bin.clone(),
        }
    }

    #[test]
    fn coperceive_probe_uses_the_configured_interpreter_before_path() {
        let script = coperceive_probe_script(Some("/home/alice/coperceive-app/.venv/bin/python"));
        assert!(script.contains("coperceive-check:coperceive-version"));
        assert!(script.contains("-m coperceive --version"), "{script}");
        assert!(script.contains("dirname"), "{script}");
        assert!(
            script.contains("/home/alice/coperceive-app/.venv/bin/python"),
            "{script}"
        );
        assert!(script.contains("command -v coperceive"), "{script}");
        // With no configured interpreter the script still tries PATH and python3.
        let fallback = coperceive_probe_script(None);
        assert!(fallback.contains("command -v coperceive"), "{fallback}");
        assert!(fallback.contains("importlib.metadata"), "{fallback}");
    }

    #[test]
    fn portability_cluster_a_module_and_venv_profile() {
        let fake = FakeSsh::create();
        let entry = entry_from_fixture(CLUSTER_A);
        let report = detect_server(&entry, &options(&fake));
        assert!(report.reachable && report.signed_in, "fixture must sign in");
        assert_eq!(report.scheduler, SchedulerKind::Slurm);
        assert!(report
            .partitions
            .iter()
            .any(|p| p.name == "gpu-a100" && p.gpu_types == vec!["a100"]));
        assert!(report
            .partitions
            .iter()
            .any(|p| p.name == "main-cpu" && p.gpu_types.is_empty()));
        assert!(report.partitions[0].max_time.is_some());
        assert_eq!(
            report.ssh.proxy_jump.as_deref(),
            None,
            "cluster-a has no jump host"
        );
        assert!(report.environment.kind == crate::hpc::EnvironmentKind::Venv);
        assert_eq!(
            report.environment.interpreter.as_deref(),
            Some("/home/alice/venvs/coperceive/bin/python")
        );
        assert_eq!(
            report.discovered.environment.coperceive_version.as_deref(),
            Some("0.4.0")
        );
        assert!(report.scratch.writable);
        assert_eq!(report.scratch.path.as_deref(), Some("/scratch/alice"));
        assert!(report.scratch.free_bytes.unwrap() > 0);
        assert!(report.total_ms < 60_000);
    }

    #[test]
    fn cluster_save_typed_interpreter_finds_coperceive_and_infinite_walltime() {
        let fake = FakeSsh::create();
        let mut entry = entry_from_fixture(CLUSTER_SAVE);
        let interpreter = "/home/dana/coperceive-app/.venv/bin/python";
        entry.profile.environment.interpreter = Some(interpreter.to_string());
        let report = detect_server(&entry, &options(&fake));
        assert!(report.reachable && report.signed_in);
        assert_eq!(report.scheduler, SchedulerKind::Slurm);
        assert!(report.partitions.iter().all(|p| p.max_time.as_deref() == Some("infinite")));
        assert_eq!(report.environment.kind, crate::hpc::EnvironmentKind::None);
        assert_eq!(
            report.discovered.environment.coperceive_version.as_deref(),
            Some("0.0.0")
        );
        assert!(report.discovered.environment.coperceive_path.is_none());
        assert_eq!(
            report.environment.coperceive_command,
            vec![interpreter.to_string(), "-m".to_string(), "coperceive".to_string()]
        );
    }

    #[test]
    fn portability_cluster_b_proxyjump_module_profile() {
        let fake = FakeSsh::create();
        let entry = entry_from_fixture(CLUSTER_B);
        let report = detect_server(&entry, &options(&fake));
        assert!(report.signed_in);
        assert_eq!(report.scheduler, SchedulerKind::Slurm);
        assert_eq!(report.ssh.proxy_jump.as_deref(), Some("jump.example.org"));
        assert!(report
            .partitions
            .iter()
            .any(|p| p.name == "main-cpu" && p.gpu_types.is_empty()));
        assert!(report
            .partitions
            .iter()
            .any(|p| p.name == "gpu-h100" && p.gpu_types == vec!["h100"]));
        assert_eq!(
            report.environment.kind,
            crate::hpc::EnvironmentKind::Modules
        );
        assert!(report.scratch.writable);
        assert_eq!(report.scratch.path.as_deref(), Some("/work/alice"));
        assert!(report.total_ms < 60_000);
    }

    #[test]
    fn portability_none_scheduler_profile_reports_no_jobs_without_guessing() {
        let fake = FakeSsh::create();
        let entry = entry_from_fixture(CLUSTER_NONE);
        let report = detect_server(&entry, &options(&fake));
        assert!(report.reachable && report.signed_in);
        assert_eq!(report.scheduler, SchedulerKind::None);
        assert!(report.partitions.is_empty());
        assert!(report
            .scheduler_note
            .as_deref()
            .unwrap_or("")
            .contains("No scheduler"));
        assert!(!entry.profile.defaults.partition.is_some());
    }

    #[test]
    fn unreachable_server_explains_vpn_and_skips_remote_checks() {
        let fake = FakeSsh::create();
        let mut entry = entry_from_fixture(CLUSTER_A);
        entry.profile.ssh_alias = Some("unreachable".to_string());
        let report = detect_server(&entry, &options(&fake));
        assert!(!report.reachable);
        assert!(!report.signed_in);
        assert!(report.partitions.is_empty());
        let reachable = report.checks.iter().find(|c| c.name == "reachable").unwrap();
        assert!(!reachable.ok);
        assert!(
            reachable.hint.as_deref().unwrap_or("").contains("VPN"),
            "unreachable hint must mention the VPN: {:?}",
            reachable.hint
        );
        assert!(report
            .checks
            .iter()
            .filter(|c| c.name != "reachable")
            .all(|c| !c.ok));
    }

    #[test]
    fn unsigned_in_server_explains_agent_and_skips_remote_checks() {
        let fake = FakeSsh::create();
        let mut entry = entry_from_fixture(CLUSTER_A);
        entry.profile.ssh_alias = Some("locked".to_string());
        let report = detect_server(&entry, &options(&fake));
        assert!(report.reachable);
        assert!(!report.signed_in);
        let reachable = report.checks.iter().find(|c| c.name == "reachable").unwrap();
        assert!(!reachable.ok);
        assert!(reachable.hint.as_deref().unwrap_or("").contains("ssh-add"));
        assert!(report.checks.iter().any(|c| c.name == "scheduler" && !c.ok));
    }

    #[test]
    fn scratch_check_quotes_path_with_spaces_quotes_and_semicolon() {
        let path = "/scratch/alice dir/it's; touch /tmp/x";
        let script = scratch_check_script(path);
        assert!(
            script.contains("DIR='/scratch/alice dir/it'\\''s; touch /tmp/x'"),
            "path must be single-quoted: {script}"
        );
        assert!(!script.contains("\n; touch /tmp/x"));
    }

    #[test]
    fn expand_path_template_only_expands_known_values() {
        assert_eq!(
            expand_path_template("/scratch/$USER", Some("alice"), Some("/home/alice")),
            "/scratch/alice"
        );
        assert_eq!(
            expand_path_template("$SCRATCH", None, None),
            "$SCRATCH",
            "unknown variables stay literal and are quoted later"
        );
        assert_eq!(
            expand_path_template("$HOME/work", None, Some("/home/alice")),
            "/home/alice/work"
        );
    }

    #[test]
    fn scratch_output_parses_space_and_quota() {
        let report = parse_scratch_output(
            "/scratch/alice",
            "WRITABLE\n/dev/sdb1 1024 10 500000 1% /scratch\nDisk quotas for user alice: none\n",
        );
        assert!(report.writable);
        assert_eq!(report.free_bytes, Some(500_000 * 1024));
        assert!(report.quota.as_deref().unwrap_or("").contains("Disk quotas"));
    }

    #[test]
    fn detection_report_json_never_contains_token_or_password() {
        let fake = FakeSsh::create();
        let entry = entry_from_fixture(CLUSTER_A);
        let report = detect_server(&entry, &options(&fake));
        let json = serde_json::to_string(&report).expect("serialize").to_lowercase();
        for forbidden in ["token", "password", "private_key", "secret"] {
            assert!(!json.contains(forbidden), "report leaked {forbidden}: {json}");
        }
    }
}
