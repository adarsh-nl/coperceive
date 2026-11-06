// SPDX-License-Identifier: Apache-2.0
//! Remote HPC and SSH session manager for Coperceive Desktop.

use std::io::Read;
use std::process::{Child, Command, Stdio};
use std::time::Duration;

use serde::{Deserialize, Serialize};

use crate::port::find_free_loopback_port;
use crate::ssh::{
    build_ssh_command, build_ssh_tunnel_command, classify_ssh_error, quote_remote_argv, SshErrorKind,
};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RemoteStartResponse {
    pub status: String,
    #[serde(default)]
    pub uuid: Option<String>,
    #[serde(default)]
    pub job_id: Option<u64>,
    #[serde(default)]
    pub node: Option<String>,
    #[serde(default)]
    pub ip: Option<String>,
    #[serde(default)]
    pub port: Option<u16>,
    #[serde(default)]
    pub token: Option<String>,
    #[serde(default)]
    pub reason: Option<String>,
    #[serde(default)]
    pub error: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RemoteStatusResponse {
    pub status: String,
    #[serde(default)]
    pub state: Option<String>,
    #[serde(default)]
    pub uuid: Option<String>,
    #[serde(default)]
    pub job_id: Option<u64>,
    #[serde(default)]
    pub node: Option<String>,
    #[serde(default)]
    pub ip: Option<String>,
    #[serde(default)]
    pub port: Option<u16>,
    #[serde(default)]
    pub elapsed: Option<String>,
    #[serde(default)]
    pub time_left: Option<String>,
    #[serde(default)]
    pub time_left_seconds: Option<u64>,
    #[serde(default)]
    pub healthy: Option<bool>,
    #[serde(default)]
    pub reason: Option<String>,
    #[serde(default)]
    pub warning: Option<String>,
    #[serde(default)]
    pub stopped_reason: Option<String>,
    #[serde(default)]
    pub last_heartbeat_age_s: Option<f64>,
    #[serde(default)]
    pub error: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RemoteStopResponse {
    pub status: String,
    #[serde(default)]
    pub uuid: Option<String>,
    #[serde(default)]
    pub job_id: Option<u64>,
    #[serde(default)]
    pub cancelled: Option<bool>,
    #[serde(default)]
    pub error: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RemoteHeartbeatResponse {
    pub status: String,
    #[serde(default)]
    pub uuid: Option<String>,
    #[serde(default)]
    pub timestamp: Option<f64>,
    #[serde(default)]
    pub error: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RemoteSessionInfo {
    pub uuid: String,
    #[serde(default)]
    pub job_id: Option<u64>,
    #[serde(default)]
    pub job_name: Option<String>,
    #[serde(default)]
    pub state: Option<String>,
    #[serde(default)]
    pub node: Option<String>,
    #[serde(default)]
    pub ip: Option<String>,
    #[serde(default)]
    pub port: Option<u16>,
    #[serde(default)]
    pub elapsed: Option<String>,
    #[serde(default)]
    pub time_left: Option<String>,
    #[serde(default)]
    pub time_left_seconds: Option<u64>,
    #[serde(default)]
    pub data_root: Option<String>,
    #[serde(default)]
    pub last_heartbeat_age_s: Option<f64>,
    #[serde(default)]
    pub is_orphan: Option<bool>,
    #[serde(default)]
    pub orphan_reason: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RemoteListResponse {
    #[serde(default)]
    pub sessions: Vec<RemoteSessionInfo>,
}

/// Builds the remote command line for `coperceive remote ...`.
///
/// Every argument is quoted POSIX-style: user values (data roots, partitions,
/// UUIDs) may contain spaces, quotes or shell metacharacters and must reach the
/// remote coperceive as one argument each.
pub fn remote_command_line(command: &[String], args: &[String]) -> String {
    let mut argv = command.to_vec();
    if argv.is_empty() {
        argv.push("coperceive".to_string());
    }
    argv.push("remote".to_string());
    argv.extend(args.iter().cloned());
    quote_remote_argv(&argv)
}

/// Parses a JSON reply from the remote side without ever echoing the reply.
///
/// The start/status replies can contain a session token. An error message must
/// never include the raw output, so only the reply kind and its size are shown.
fn parse_remote_reply<T: serde::de::DeserializeOwned>(label: &str, stdout: &str) -> Result<T, String> {
    serde_json::from_str::<T>(stdout).map_err(|_| {
        format!(
            "The server sent an unexpected reply to the {label} request ({} bytes, not valid JSON). Check that coperceive is installed and up to date on the server, then try again.",
            stdout.len()
        )
    })
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct RemoteTokenResponse {
    #[serde(default)]
    status: String,
    #[serde(default)]
    token: Option<String>,
    #[serde(default)]
    error: Option<String>,
}

#[derive(Debug, Clone)]
pub struct RemoteClient {
    pub alias: String,
    pub ssh_bin: String,
    /// The profile's saved coperceive argv, e.g. a venv python plus `-m coperceive`.
    pub coperceive_command: Vec<String>,
}

/// Everything needed to start a remote serve job, taken from the server profile.
#[derive(Debug, Clone, Default)]
pub struct RemoteStartOptions {
    pub data_root: String,
    pub partition: Option<String>,
    pub walltime: Option<String>,
    pub cpus: Option<u32>,
    pub mem: Option<String>,
    pub python: Option<String>,
    pub uuid: Option<String>,
    /// Remote `coperceive/app/static` cache dir, uploaded before the session.
    pub static_dir: Option<String>,
    /// Profile's saved coperceive argv; empty means the legacy `coperceive` name.
    pub coperceive_command: Vec<String>,
    /// Directories the server may read camera images from: a cluster dataset's
    /// raw root, exactly, so its scenes' `raw` links resolve.
    pub media_roots: Vec<String>,
    /// Seconds `remote start` waits on the login node for the job to run.
    /// Short when the caller polls and reports a queued job itself.
    pub wait_timeout: Option<u32>,
}

/// A viewer job still waiting to run, reported while the app polls it.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct ViewerJobProgress {
    pub job_id: Option<u64>,
    pub state: String,
    pub reason: Option<String>,
}

impl RemoteClient {
    pub fn new(alias: &str) -> Self {
        Self {
            alias: alias.to_string(),
            ssh_bin: "ssh".to_string(),
            coperceive_command: vec!["coperceive".to_string()],
        }
    }

    pub fn with_ssh_bin(alias: &str, ssh_bin: &str) -> Self {
        Self {
            alias: alias.to_string(),
            ssh_bin: ssh_bin.to_string(),
            coperceive_command: vec!["coperceive".to_string()],
        }
    }

    pub fn with_coperceive_command(mut self, command: Vec<String>) -> Self {
        if !command.is_empty() {
            self.coperceive_command = command;
        }
        self
    }

    fn run_remote_cmd(&self, args: &[String]) -> Result<String, SshErrorKind> {
        let remote_cmd = remote_command_line(&self.coperceive_command, args);
        let ssh_args = build_ssh_command(&self.alias, &remote_cmd);

        let output = crate::platform::create_command(&self.ssh_bin)
            .args(&ssh_args)
            .output()
            .map_err(|e| SshErrorKind::Other(format!("Could not start SSH: {}", e)))?;

        let stderr = String::from_utf8_lossy(&output.stderr).to_string();
        if !output.status.success() {
            return Err(classify_ssh_error(&stderr));
        }

        let stdout = String::from_utf8_lossy(&output.stdout).trim().to_string();
        Ok(stdout)
    }

    pub fn start(
        &self,
        data_root: &str,
        partition: Option<&str>,
        walltime: Option<&str>,
        uuid: Option<&str>,
    ) -> Result<RemoteStartResponse, SshErrorKind> {
        self.start_with_options(&RemoteStartOptions {
            data_root: data_root.to_string(),
            partition: partition.map(str::to_string),
            walltime: walltime.map(str::to_string),
            uuid: uuid.map(str::to_string),
            ..Default::default()
        })
    }

    /// Starts a session with the resources chosen in the server profile. Only
    /// values that exist are sent; Python rejects a missing partition instead of
    /// inventing a site default.
    pub fn start_with_options(
        &self,
        options: &RemoteStartOptions,
    ) -> Result<RemoteStartResponse, SshErrorKind> {
        let mut args = vec![
            "start".to_string(),
            "--root".to_string(),
            options.data_root.clone(),
        ];
        if let Some(p) = options.partition.as_deref().filter(|p| !p.trim().is_empty()) {
            args.extend(["--partition".to_string(), p.to_string()]);
        }
        if let Some(w) = options.walltime.as_deref().filter(|w| !w.trim().is_empty()) {
            args.extend(["--time".to_string(), w.to_string()]);
        }
        if let Some(cpus) = options.cpus.filter(|c| *c > 0) {
            args.extend(["--cpus".to_string(), cpus.to_string()]);
        }
        if let Some(mem) = options.mem.as_deref().filter(|m| !m.trim().is_empty()) {
            args.extend(["--mem".to_string(), mem.to_string()]);
        }
        if let Some(python) = options.python.as_deref().filter(|p| !p.trim().is_empty()) {
            args.extend(["--python".to_string(), python.to_string()]);
        }
        if let Some(u) = options.uuid.as_deref().filter(|u| !u.trim().is_empty()) {
            args.extend(["--uuid".to_string(), u.to_string()]);
        }
        if let Some(static_dir) = options
            .static_dir
            .as_deref()
            .filter(|dir| !dir.trim().is_empty())
        {
            args.extend(["--static".to_string(), static_dir.to_string()]);
        }
        for media in options.media_roots.iter().filter(|root| !root.trim().is_empty()) {
            args.extend(["--media-root".to_string(), media.to_string()]);
        }
        if let Some(wait) = options.wait_timeout {
            args.extend(["--wait-timeout".to_string(), wait.to_string()]);
        }

        let stdout = self.run_remote_cmd(&args)?;
        let parsed: RemoteStartResponse =
            parse_remote_reply("start", &stdout).map_err(SshErrorKind::Other)?;
        Ok(parsed)
    }

    pub fn status(
        &self,
        uuid: Option<&str>,
        job_id: Option<u64>,
    ) -> Result<RemoteStatusResponse, SshErrorKind> {
        let mut args = vec!["status".to_string()];
        if let Some(u) = uuid {
            args.extend(["--uuid".to_string(), u.to_string()]);
        }
        if let Some(j) = job_id {
            args.extend(["--job-id".to_string(), j.to_string()]);
        }

        let stdout = self.run_remote_cmd(&args)?;
        let parsed: RemoteStatusResponse =
            parse_remote_reply("status", &stdout).map_err(SshErrorKind::Other)?;
        Ok(parsed)
    }

    pub fn stop(
        &self,
        uuid: Option<&str>,
        job_id: Option<u64>,
    ) -> Result<RemoteStopResponse, SshErrorKind> {
        let mut args = vec!["stop".to_string()];
        if let Some(u) = uuid {
            args.extend(["--uuid".to_string(), u.to_string()]);
        }
        if let Some(j) = job_id {
            args.extend(["--job-id".to_string(), j.to_string()]);
        }

        let stdout = self.run_remote_cmd(&args)?;
        let parsed: RemoteStopResponse =
            parse_remote_reply("stop", &stdout).map_err(SshErrorKind::Other)?;
        Ok(parsed)
    }

    pub fn heartbeat(&self, uuid: &str) -> Result<RemoteHeartbeatResponse, SshErrorKind> {
        let args = vec!["heartbeat".to_string(), "--uuid".to_string(), uuid.to_string()];
        let stdout = self.run_remote_cmd(&args)?;
        let parsed: RemoteHeartbeatResponse =
            parse_remote_reply("heartbeat", &stdout).map_err(SshErrorKind::Other)?;
        Ok(parsed)
    }

    pub fn list(&self) -> Result<RemoteListResponse, SshErrorKind> {
        let args = vec!["list".to_string()];
        let stdout = self.run_remote_cmd(&args)?;
        let parsed: RemoteListResponse =
            parse_remote_reply("list", &stdout).map_err(SshErrorKind::Other)?;
        Ok(parsed)
    }

    /// Reads the session token for an already running session over the encrypted
    /// SSH channel. The token is kept in memory only: it is never written to
    /// local disk, and it is never included in an error message.
    pub fn fetch_token(&self, uuid: &str) -> Result<String, SshErrorKind> {
        let args = vec!["token".to_string(), "--uuid".to_string(), uuid.to_string()];
        let stdout = self.run_remote_cmd(&args)?;
        let parsed: RemoteTokenResponse =
            parse_remote_reply("session key", &stdout).map_err(SshErrorKind::Other)?;
        if let Some(token) = parsed.token.filter(|t| !t.trim().is_empty()) {
            return Ok(token);
        }
        Err(SshErrorKind::Other(
            "Coperceive could not read the running session's key from the server. Disconnect and start a new session."
                .to_string(),
        ))
    }
}

pub struct SshTunnel {
    pub child: Option<Child>,
    pub local_port: u16,
    pub remote_ip: String,
    pub remote_port: u16,
    pub alias: String,
}

impl SshTunnel {
    pub fn open(
        alias: &str,
        local_port: u16,
        remote_ip: &str,
        remote_port: u16,
        ssh_bin: Option<&str>,
    ) -> Result<Self, SshErrorKind> {
        let bin = ssh_bin.unwrap_or("ssh");
        let args = build_ssh_tunnel_command(alias, local_port, remote_ip, remote_port);

        let mut child = crate::platform::create_command(bin)
            .args(&args)
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .map_err(|e| SshErrorKind::Other(format!("Failed to spawn ssh tunnel: {}", e)))?;

        // Give ssh tunnel a brief moment to detect immediate handshake failures
        std::thread::sleep(Duration::from_millis(150));
        if let Ok(Some(_status)) = child.try_wait() {
            let mut stderr = String::new();
            if let Some(mut err_pipe) = child.stderr.take() {
                let _ = err_pipe.read_to_string(&mut stderr);
            }
            return Err(classify_ssh_error(&stderr));
        }

        Ok(Self {
            child: Some(child),
            local_port,
            remote_ip: remote_ip.to_string(),
            remote_port,
            alias: alias.to_string(),
        })
    }

    pub fn is_alive(&mut self) -> bool {
        if let Some(child) = &mut self.child {
            matches!(child.try_wait(), Ok(None))
        } else {
            false
        }
    }

    pub fn close(&mut self) -> Result<(), std::io::Error> {
        if let Some(mut child) = self.child.take() {
            let _ = child.kill();
            let _ = child.wait();
        }
        Ok(())
    }
}

impl Drop for SshTunnel {
    fn drop(&mut self) {
        let _ = self.close();
    }
}

pub struct RemoteSessionManager {
    pub client: RemoteClient,
    pub tunnel: Option<SshTunnel>,
    pub uuid: String,
    pub local_port: u16,
    pub remote_ip: String,
    pub remote_port: u16,
    pub token: String,
    pub data_root: String,
    /// The Slurm job serving the viewer, when the server reported it.
    pub job_id: Option<u64>,
}

/// The viewer job runs and its session (node address, port) is known.
fn reachable(status: &RemoteStatusResponse) -> bool {
    // An older CLI sends "ip": "" and "port": 0 before the session exists.
    status.ip.as_deref().is_some_and(|ip| !ip.is_empty())
        && status.port.is_some_and(|port| port > 0)
        && (status.status == "running"
            || (status.state.as_deref() == Some("RUNNING") && status.status != "stopped"))
}

/// The job has not ended: queued, or running but its session not written yet.
/// An older CLI reports the latter as `stopped` with state RUNNING; so does
/// any reply whose Slurm state is still active.
fn is_starting(status: &RemoteStatusResponse) -> bool {
    matches!(status.status.as_str(), "pending" | "running")
        || matches!(
            status.state.as_deref(),
            Some("PENDING" | "CONFIGURING" | "RUNNING" | "COMPLETING" | "RESIZING")
        )
}

impl RemoteSessionManager {
    pub fn connect(
        alias: &str,
        data_root: &str,
        existing_uuid: Option<&str>,
        ssh_bin: Option<&str>,
    ) -> Result<Self, SshErrorKind> {
        Self::connect_with_options(
            alias,
            &RemoteStartOptions {
                data_root: data_root.to_string(),
                uuid: existing_uuid.map(str::to_string),
                ..Default::default()
            },
            ssh_bin,
        )
    }

    pub fn connect_with_options(
        alias: &str,
        options: &RemoteStartOptions,
        ssh_bin: Option<&str>,
    ) -> Result<Self, SshErrorKind> {
        let data_root = options.data_root.as_str();
        let existing_uuid = options.uuid.as_deref();
        let mut client = match ssh_bin {
            Some(bin) => RemoteClient::with_ssh_bin(alias, bin),
            None => RemoteClient::new(alias),
        };
        if !options.coperceive_command.is_empty() {
            client.coperceive_command = options.coperceive_command.clone();
        }

        let local_port = find_free_loopback_port()
            .map_err(|e| SshErrorKind::Other(format!("Failed to find free local port: {}", e)))?;

        // Reconnect / reattach or start fresh
        let (uuid, remote_ip, remote_port, token) = if let Some(u) = existing_uuid {
            let st = client.status(Some(u), None)?;
            if !reachable(&st) {
                return Err(SshErrorKind::Other(if is_starting(&st) {
                    format!(
                        "The viewer job is still starting (Slurm state {}). Try again in a moment.",
                        st.state.as_deref().unwrap_or("unknown")
                    )
                } else {
                    format!(
                        "Cannot reattach: session {} is not running (state: {:?})",
                        u, st.state
                    )
                }));
            }
            let ip = st
                .ip
                .ok_or_else(|| SshErrorKind::Other("The server did not report the session's node address.".to_string()))?;
            let port = st
                .port
                .ok_or_else(|| SshErrorKind::Other("The server did not report the session's port.".to_string()))?;
            let token = client.fetch_token(u)?;
            (u.to_string(), ip, port, token)
        } else {
            let resp = client.start_with_options(options)?;
            if resp.status != "running" {
                return Err(SshErrorKind::Other(format!(
                    "Failed to start remote session: {:?} (error: {:?})",
                    resp.status, resp.error
                )));
            }
            let u = resp
                .uuid
                .ok_or_else(|| SshErrorKind::Other("The server did not report a session id.".to_string()))?;
            let ip = resp
                .ip
                .ok_or_else(|| SshErrorKind::Other("The server did not report the session node address.".to_string()))?;
            let port = resp
                .port
                .ok_or_else(|| SshErrorKind::Other("The server did not report the session port.".to_string()))?;
            let tok = resp.token.unwrap_or_default();
            (u, ip, port, tok)
        };

        Self::attach(client, alias, ssh_bin, local_port, uuid, remote_ip, remote_port, token, data_root)
    }

    #[allow(clippy::too_many_arguments)]
    fn attach(
        client: RemoteClient,
        alias: &str,
        ssh_bin: Option<&str>,
        local_port: u16,
        uuid: String,
        remote_ip: String,
        remote_port: u16,
        token: String,
        data_root: &str,
    ) -> Result<Self, SshErrorKind> {
        let tunnel = SshTunnel::open(alias, local_port, &remote_ip, remote_port, ssh_bin)?;
        Ok(Self {
            client,
            tunnel: Some(tunnel),
            uuid,
            local_port,
            remote_ip,
            remote_port,
            token,
            data_root: data_root.to_string(),
            job_id: None,
        })
    }

    /// Start a viewer job, or reattach to `options.uuid`. While the job waits in
    /// the queue, `progress` hears its id, state and reason every few seconds;
    /// once it runs the tunnel opens as usual. A job that is still queued after
    /// `limit` is cancelled so it never runs unseen.
    pub fn connect_with_progress(
        alias: &str,
        options: &RemoteStartOptions,
        ssh_bin: Option<&str>,
        progress: &dyn Fn(&ViewerJobProgress),
        poll: Duration,
        limit: Duration,
    ) -> Result<Self, SshErrorKind> {
        if options.uuid.is_some() {
            return Self::connect_with_options(alias, options, ssh_bin);
        }
        let mut client = match ssh_bin {
            Some(bin) => RemoteClient::with_ssh_bin(alias, bin),
            None => RemoteClient::new(alias),
        };
        if !options.coperceive_command.is_empty() {
            client.coperceive_command = options.coperceive_command.clone();
        }
        let started = client.start_with_options(options)?;
        let job_id = started.job_id;
        let uuid = match (started.status.as_str(), started.uuid.clone()) {
            ("running" | "pending", Some(uuid)) => uuid,
            _ => {
                return Err(SshErrorKind::Other(format!(
                    "Failed to start remote session: {:?} (error: {:?})",
                    started.status, started.error
                )))
            }
        };
        if started.status == "running" {
            if let (Some(ip), Some(port)) = (started.ip.clone(), started.port) {
                let local_port = find_free_loopback_port().map_err(|e| {
                    SshErrorKind::Other(format!("Failed to find free local port: {}", e))
                })?;
                let token = started.token.clone().unwrap_or_default();
                let mut session = Self::attach(
                    client,
                    alias,
                    ssh_bin,
                    local_port,
                    uuid,
                    ip,
                    port,
                    token,
                    &options.data_root,
                )?;
                session.job_id = job_id;
                return Ok(session);
            }
        } else {
            progress(&ViewerJobProgress {
                job_id,
                state: "PENDING".to_string(),
                reason: started.reason.clone(),
            });
        }
        Self::wait_and_attach(client, alias, ssh_bin, &uuid, job_id, &options.data_root, progress, poll, limit)
    }

    /// Reattach to a viewer job the app started before (its id kept in
    /// state.json), waiting while it still starts.
    pub fn reattach_with_progress(
        alias: &str,
        options: &RemoteStartOptions,
        ssh_bin: Option<&str>,
        uuid: &str,
        progress: &dyn Fn(&ViewerJobProgress),
        poll: Duration,
        limit: Duration,
    ) -> Result<Self, SshErrorKind> {
        let mut client = match ssh_bin {
            Some(bin) => RemoteClient::with_ssh_bin(alias, bin),
            None => RemoteClient::new(alias),
        };
        if !options.coperceive_command.is_empty() {
            client.coperceive_command = options.coperceive_command.clone();
        }
        Self::wait_and_attach(client, alias, ssh_bin, uuid, None, &options.data_root, progress, poll, limit)
    }

    /// Poll a started job until its session is reachable, then open the tunnel
    /// from that reply. Polls by session id and job id: a job Slurm already
    /// runs may not have written its session file yet (it is starting, which an
    /// older CLI reported as `stopped` with state RUNNING).
    #[allow(clippy::too_many_arguments)]
    fn wait_and_attach(
        client: RemoteClient,
        alias: &str,
        ssh_bin: Option<&str>,
        uuid: &str,
        job_id: Option<u64>,
        data_root: &str,
        progress: &dyn Fn(&ViewerJobProgress),
        poll: Duration,
        limit: Duration,
    ) -> Result<Self, SshErrorKind> {
        let deadline = std::time::Instant::now() + limit;
        let mut job_id = job_id;
        loop {
            let status = client.status(Some(uuid), job_id)?;
            job_id = job_id.or(status.job_id); // the job we started wins
            if reachable(&status) {
                let (Some(ip), Some(port)) = (status.ip.clone(), status.port) else {
                    unreachable!("reachable() checked the address")
                };
                let token = client.fetch_token(uuid)?;
                let local_port = find_free_loopback_port().map_err(|e| {
                    SshErrorKind::Other(format!("Failed to find free local port: {}", e))
                })?;
                let mut session = Self::attach(
                    client,
                    alias,
                    ssh_bin,
                    local_port,
                    uuid.to_string(),
                    ip,
                    port,
                    token,
                    data_root,
                )?;
                session.job_id = job_id;
                return Ok(session);
            }
            if !is_starting(&status) {
                return Err(SshErrorKind::Other(format!(
                    "The viewer job stopped before it started ({}{}).",
                    status.status,
                    status
                        .state
                        .as_deref()
                        .map(|state| format!(", {state}"))
                        .unwrap_or_default()
                )));
            }
            progress(&ViewerJobProgress {
                job_id,
                state: status.state.clone().unwrap_or_else(|| "PENDING".to_string()),
                reason: status.reason.clone(),
            });
            if std::time::Instant::now() >= deadline {
                let _ = client.stop(Some(uuid), job_id);
                return Err(SshErrorKind::Other(format!(
                    "The viewer job waited in the queue for {} minutes and was cancelled{}. Open the scene again to retry.",
                    limit.as_secs() / 60,
                    status
                        .reason
                        .as_deref()
                        .map(|reason| format!(" (queue reason: {reason})"))
                        .unwrap_or_default()
                )));
            }
            std::thread::sleep(poll);
        }
    }

    /// The tunnel to the viewer job is still up.
    pub fn tunnel_alive(&mut self) -> bool {
        self.tunnel.as_mut().is_some_and(|tunnel| tunnel.is_alive())
    }

    /// The viewer job's scheduler status (state, time left), for the viewer.
    pub fn job_status(&self) -> Result<RemoteStatusResponse, SshErrorKind> {
        self.client.status(Some(&self.uuid), self.job_id)
    }

    pub fn origin(&self) -> String {
        format!("http://127.0.0.1:{}", self.local_port)
    }

    pub fn disconnect(&mut self) -> Result<(), SshErrorKind> {
        if let Some(mut t) = self.tunnel.take() {
            let _ = t.close();
        }
        self.client.stop(Some(&self.uuid), None)?;
        Ok(())
    }
}

#[cfg(all(test, unix))]
mod tests {
    use super::*;
    use std::fs;
    use std::path::PathBuf;

    struct FakeSshScript {
        path: PathBuf,
        _guard: std::sync::MutexGuard<'static, ()>,
    }

    impl FakeSshScript {
        fn create() -> Self {
            let guard = crate::test_seams::FAKE_SSH_LOCK
                .lock()
                .unwrap_or_else(|p| p.into_inner());
            let fixture = concat!(env!("CARGO_MANIFEST_DIR"), "/tests/fixtures/hpc/fake_ssh.sh");
            Self {
                path: PathBuf::from(fixture),
                _guard: guard,
            }
        }
    }

    impl Drop for FakeSshScript {
        fn drop(&mut self) {}
    }

    #[test]
    fn a_queued_viewer_job_is_reported_then_attached_when_it_runs() {
        let fake = FakeSshScript::create();
        let seen = std::sync::Mutex::new(Vec::new());
        let session = RemoteSessionManager::connect_with_progress(
            "hpc-alias",
            &RemoteStartOptions {
                data_root: "/queued".to_string(),
                wait_timeout: Some(5),
                ..Default::default()
            },
            fake.path.to_str(),
            &|job| seen.lock().unwrap().push(job.clone()),
            Duration::from_millis(10),
            Duration::from_secs(5),
        )
        .expect("attached once running");
        assert_eq!(
            seen.lock().unwrap().first(),
            Some(&ViewerJobProgress {
                job_id: Some(5560),
                state: "PENDING".to_string(),
                reason: Some("Priority".to_string()),
            })
        );
        assert_eq!((session.uuid.as_str(), session.job_id), ("test-queued", Some(5560)));
        assert_eq!(session.token, "tok-reattach-99");
    }

    #[test]
    fn a_running_job_whose_session_is_not_written_yet_is_attached_not_refused() {
        // Reproduces "Cannot reattach: session … is not running (state: Some(\"RUNNING\"))":
        // after the queue, a lookup by session id alone got {"status":"stopped","state":"RUNNING"}.
        let fake = FakeSshScript::create();
        let session = RemoteSessionManager::connect_with_progress(
            "hpc-alias",
            &RemoteStartOptions {
                data_root: "/slow-session".to_string(),
                wait_timeout: Some(5),
                ..Default::default()
            },
            fake.path.to_str(),
            &|_| {},
            Duration::from_millis(10),
            Duration::from_secs(5),
        )
        .expect("attached by job id");
        assert_eq!((session.uuid.as_str(), session.job_id), ("test-slow", Some(5570)));
        assert_eq!(session.remote_port, 52700);
    }

    #[test]
    fn a_stopped_reply_with_an_active_state_is_still_starting() {
        let reply: RemoteStatusResponse = serde_json::from_str(
            "{\n  \"status\": \"stopped\",\n  \"state\": \"RUNNING\",\n  \"uuid\": \"u\"\n}",
        )
        .unwrap();
        assert!(is_starting(&reply) && !reachable(&reply));
        let empty: RemoteStatusResponse = serde_json::from_str(
            r#"{"status": "running", "state": "RUNNING", "ip": "", "port": 0}"#,
        )
        .unwrap();
        assert!(!reachable(&empty), "an empty address is not a session");
        let ended: RemoteStatusResponse =
            serde_json::from_str(r#"{"status": "stopped", "state": "CANCELLED"}"#).unwrap();
        assert!(!is_starting(&ended));
    }

    #[test]
    fn a_viewer_job_that_never_leaves_the_queue_is_cancelled() {
        let fake = FakeSshScript::create();
        let log = std::env::temp_dir().join(format!("fake_ssh_stuck_{}.log", std::process::id()));
        let _ = fs::remove_file(&log);
        std::env::set_var("FAKE_SSH_LOG", &log);
        let error = RemoteSessionManager::connect_with_progress(
            "hpc-alias",
            &RemoteStartOptions {
                data_root: "/stuck".to_string(),
                wait_timeout: Some(5),
                ..Default::default()
            },
            fake.path.to_str(),
            &|_| {},
            Duration::from_millis(10),
            Duration::from_millis(0),
        )
        .err()
        .expect("cancelled");
        std::env::remove_var("FAKE_SSH_LOG");
        let message = match error {
            SshErrorKind::Other(message) => message,
            other => format!("{other:?}"),
        };
        assert!(message.contains("waited in the queue") && message.contains("ReqNodeNotAvail"), "{message}");
        let logged = fs::read_to_string(&log).unwrap_or_default();
        assert!(logged.contains("remote start") && logged.contains("--wait-timeout 5"), "{logged}");
        assert!(logged.contains("remote stop"), "the queued job must be cancelled: {logged}");
        let _ = fs::remove_file(&log);
    }

    #[test]
    fn remote_start_passes_the_uploaded_static_directory() {
        let fake = FakeSshScript::create();
        let log = std::env::temp_dir().join(format!(
            "coperceive_remote_start_static_{}_{}.log",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap_or_default()
                .as_nanos()
        ));
        std::env::set_var("FAKE_SSH_LOG", &log);
        let client = RemoteClient::with_ssh_bin("hpc-alias", fake.path.to_str().unwrap());
        let options = RemoteStartOptions {
            data_root: "/path/to/scenes".to_string(),
            static_dir: Some("~/.cache/coperceive/static/0.1.0".to_string()),
            media_roots: vec!["/datasets/opencood/opv2v".to_string()],
            ..Default::default()
        };
        client.start_with_options(&options).expect("start");
        std::env::remove_var("FAKE_SSH_LOG");
        let logged = fs::read_to_string(&log).expect("read log");
        assert!(
            logged.contains("--static") && logged.contains("~/.cache/coperceive/static/0.1.0"),
            "the remote start must carry the uploaded viewer directory: {logged}"
        );
        assert!(
            logged.contains("--media-root /datasets/opencood/opv2v"),
            "a cluster dataset's raw root is the server's media root: {logged}"
        );
        let _ = fs::remove_file(&log);
    }

    #[test]
    fn test_remote_start_and_status_parsing() {
        let fake = FakeSshScript::create();
        let client = RemoteClient::with_ssh_bin("hpc-alias", fake.path.to_str().unwrap());

        let start_res = client.start("/path/to/scenes", None, None, None).expect("start");
        assert_eq!(start_res.status, "running");
        assert_eq!(start_res.uuid.as_deref(), Some("test-uuid-99"));
        assert_eq!(start_res.job_id, Some(5555));
        assert_eq!(start_res.ip.as_deref(), Some("10.20.240.48"));
        assert_eq!(start_res.port, Some(52657));
        assert_eq!(start_res.token.as_deref(), Some("tok12345"));

        // Running status
        let status_res = client.status(Some("test-uuid-99"), None).expect("status running");
        assert_eq!(status_res.status, "running");
        assert_eq!(status_res.state.as_deref(), Some("RUNNING"));
        assert_eq!(status_res.healthy, Some(true));
        assert_eq!(status_res.last_heartbeat_age_s, Some(12.5));

        // Pending status
        let pending_res = client.status(Some("test-pending"), None).expect("status pending");
        assert_eq!(pending_res.status, "pending");
        assert_eq!(pending_res.state.as_deref(), Some("PENDING"));
        assert_eq!(pending_res.reason.as_deref(), Some("ReqNodeNotAvail"));

        // Timeout status
        let timeout_res = client.status(Some("test-timeout"), None).expect("status timeout");
        assert_eq!(timeout_res.status, "stopped");
        assert_eq!(timeout_res.state.as_deref(), Some("TIMEOUT"));
        assert_eq!(timeout_res.stopped_reason.as_deref(), Some("timeout"));
    }

    #[test]
    fn test_remote_heartbeat_and_stop() {
        let fake = FakeSshScript::create();
        let client = RemoteClient::with_ssh_bin("hpc-alias", fake.path.to_str().unwrap());

        let hb = client.heartbeat("test-uuid-99").expect("heartbeat");
        assert_eq!(hb.status, "ok");
        assert_eq!(hb.uuid.as_deref(), Some("test-uuid-99"));
        assert_eq!(hb.timestamp, Some(1792500000.0));

        let stop = client.stop(Some("test-uuid-99"), None).expect("stop");
        assert_eq!(stop.status, "stopped");
        assert_eq!(stop.cancelled, Some(true));
    }

    #[test]
    fn test_remote_ssh_errors_unreachable_and_publickey() {
        let fake = FakeSshScript::create();

        // Unreachable host error
        let client_unreachable =
            RemoteClient::with_ssh_bin("--fail-unreachable", fake.path.to_str().unwrap());
        let err = client_unreachable.start("/root", None, None, None).unwrap_err();
        assert!(matches!(err, SshErrorKind::EduVpnOrNetworkUnreachable(_)));

        // Publickey denied error
        let client_auth = RemoteClient::with_ssh_bin("--fail-auth", fake.path.to_str().unwrap());
        let err_auth = client_auth.start("/root", None, None, None).unwrap_err();
        assert!(matches!(err_auth, SshErrorKind::PublicKeyAuthRequired(_)));
    }

    #[test]
    fn test_ssh_tunnel_lifecycle() {
        let fake = FakeSshScript::create();
        let mut tunnel = SshTunnel::open(
            "hpc-alias",
            19876,
            "10.20.240.48",
            52657,
            Some(fake.path.to_str().unwrap()),
        )
        .expect("open tunnel");

        assert!(tunnel.is_alive());
        tunnel.close().expect("close tunnel");
        assert!(!tunnel.is_alive());
    }

    #[test]
    fn test_remote_session_manager_connect_and_reattach() {
        let fake = FakeSshScript::create();

        // Fresh start
        let mut mgr = RemoteSessionManager::connect(
            "hpc-alias",
            "/data/scenes",
            None,
            Some(fake.path.to_str().unwrap()),
        )
        .expect("connect fresh");

        assert_eq!(mgr.uuid, "test-uuid-99");
        assert_eq!(mgr.remote_ip, "10.20.240.48");
        assert_eq!(mgr.remote_port, 52657);
        assert!(mgr.origin().starts_with("http://127.0.0.1:"));
        mgr.disconnect().expect("disconnect");

        // Reattach to existing session
        let mgr_reattach = RemoteSessionManager::connect(
            "hpc-alias",
            "/data/scenes",
            Some("test-uuid-99"),
            Some(fake.path.to_str().unwrap()),
        )
        .expect("reattach");

        assert_eq!(mgr_reattach.uuid, "test-uuid-99");
        assert_eq!(mgr_reattach.remote_ip, "10.20.240.48");
        assert_eq!(mgr_reattach.remote_port, 52657);
    }
    #[test]
    fn test_remote_command_line_quotes_every_argument() {
        let args = vec![
            "start".to_string(),
            "--root".to_string(),
            "/data/my scenes; touch /tmp/x".to_string(),
        ];
        assert_eq!(
            remote_command_line(&["coperceive".to_string()], &args),
            "coperceive remote start --root '/data/my scenes; touch /tmp/x'"
        );
    }

    #[test]
    fn test_remote_command_line_uses_the_saved_coperceive_argv() {
        let command = vec![
            "/home/u/venv/bin/python".to_string(),
            "-m".to_string(),
            "coperceive".to_string(),
        ];
        let args = vec!["status".to_string(), "--uuid".to_string(), "abc".to_string()];
        assert_eq!(
            remote_command_line(&command, &args),
            "/home/u/venv/bin/python -m coperceive remote status --uuid abc"
        );
    }

    #[test]
    fn test_remote_command_injection_with_spaces_quotes_and_semicolons() {
        let dir = std::env::temp_dir().join(format!(
            "coperceive-injection-{}",
            std::process::id()
        ));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).expect("temp dir");
        let bin_dir = PathBuf::from(concat!(env!("CARGO_MANIFEST_DIR"), "/tests/fixtures/bin"));

        let marker = dir.join("pwned");
        let log = dir.join("argv.log");
        let malicious = format!(
            "/data/my scenes/\"quoted\"; touch /tmp/x; touch {}",
            marker.display()
        );
        let line = remote_command_line(&["coperceive".to_string()], &[
            "start".to_string(),
            "--root".to_string(),
            malicious.clone(),
        ]);
        // The value is one single-quoted argument; the injection cannot start a
        // second command.
        assert_eq!(
            line,
            format!("coperceive remote start --root '{}'", malicious)
        );
        let path_sep = if cfg!(windows) { ";" } else { ":" };
        let path = format!(
            "{}{}{}",
            bin_dir.display(),
            path_sep,
            std::env::var("PATH").unwrap_or_default()
        );
        let status = Command::new("sh")
            .arg("-c")
            .arg(&line)
            .env("PATH", path)
            .env("FAKE_COPER_LOG", &log)
            .status()
            .expect("run remote line locally");
        assert!(status.success());
        assert!(
            !marker.exists(),
            "shell injection executed: the malicious path created {}",
            marker.display()
        );
        let received = fs::read_to_string(&log).expect("argv log");
        let received: Vec<&str> = received.lines().collect();
        assert_eq!(received, vec!["remote", "start", "--root", malicious.as_str()]);
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn test_error_messages_redact_session_token() {
        let fake = FakeSshScript::create();
        let client = RemoteClient::with_ssh_bin("--fail-json", fake.path.to_str().unwrap());
        let err = client.start("/root", None, None, None).unwrap_err();
        let message = err.to_string();
        assert!(
            !message.contains("supersecret-token-value"),
            "the error message leaked the session token: {message}"
        );
        assert!(message.contains("unexpected reply"), "{message}");
        assert!(message.contains("bytes"), "{message}");
    }

    #[test]
    fn test_reattach_gets_a_real_token_over_ssh() {
        let fake = FakeSshScript::create();
        let mgr = RemoteSessionManager::connect(
            "hpc-alias",
            "/data/scenes",
            Some("test-uuid-99"),
            Some(fake.path.to_str().unwrap()),
        )
        .expect("reattach");
        assert_eq!(mgr.token, "tok-reattach-99");
        assert!(!mgr.token.is_empty());
        // The reconnect uses the token command rather than trusting an empty
        // placeholder.
        drop(mgr);
    }

}
