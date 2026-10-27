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
pub fn remote_command_line(args: &[String]) -> String {
    let mut argv = vec!["coperceive".to_string(), "remote".to_string()];
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
            "The server sent an unexpected reply to the {label} request ({} bytes, not valid              JSON). Check that coperceive is installed and up to date on the server, then try              again.",
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
}

impl RemoteClient {
    pub fn new(alias: &str) -> Self {
        Self {
            alias: alias.to_string(),
            ssh_bin: "ssh".to_string(),
        }
    }

    pub fn with_ssh_bin(alias: &str, ssh_bin: &str) -> Self {
        Self {
            alias: alias.to_string(),
            ssh_bin: ssh_bin.to_string(),
        }
    }

    fn run_remote_cmd(&self, args: &[String]) -> Result<String, SshErrorKind> {
        let remote_cmd = remote_command_line(args);
        let ssh_args = build_ssh_command(&self.alias, &remote_cmd);

        let output = Command::new(&self.ssh_bin)
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
            "Coperceive could not read the running session's key from the server. Disconnect and              start a new session."
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

        let mut child = Command::new(bin)
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
        let client = match ssh_bin {
            Some(bin) => RemoteClient::with_ssh_bin(alias, bin),
            None => RemoteClient::new(alias),
        };

        let local_port = find_free_loopback_port()
            .map_err(|e| SshErrorKind::Other(format!("Failed to find free local port: {}", e)))?;

        // Reconnect / reattach or start fresh
        let (uuid, remote_ip, remote_port, token) = if let Some(u) = existing_uuid {
            let st = client.status(Some(u), None)?;
            if st.status != "running" {
                return Err(SshErrorKind::Other(format!(
                    "Cannot reattach: session {} is not running (state: {:?})",
                    u, st.state
                )));
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
        })
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

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::os::unix::fs::PermissionsExt;
    use std::path::PathBuf;

    struct FakeSshScript {
        path: PathBuf,
    }

    static SCRIPT_COUNTER: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(1);

    impl FakeSshScript {
        fn create() -> Self {
            let id = SCRIPT_COUNTER.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
            let tmp = std::env::temp_dir().join(format!(
                "fake_ssh_{}_{}_{}.sh",
                std::process::id(),
                std::time::SystemTime::now()
                    .duration_since(std::time::UNIX_EPOCH)
                    .unwrap_or_default()
                    .as_nanos(),
                id
            ));
            let script = r#"#!/usr/bin/env bash
args="$*"

# Record the remote command line when a test asks for it.
if [ -n "${FAKE_SSH_LOG:-}" ]; then
    printf '%s\n' "${!#}" >> "$FAKE_SSH_LOG"
fi

# Invalid JSON injection (the output looks like it may hold a token).
if [[ "$args" == *"--fail-json"* ]]; then
    echo '{"status": "running", "token": "supersecret-token-value", broken'
    exit 0
fi

# Error injection tests
if [[ "$args" == *"--fail-unreachable"* ]]; then
    echo "ssh: connect to host hpc-test port 22: No route to host" >&2
    exit 255
fi

if [[ "$args" == *"--fail-auth"* ]]; then
    echo "Permission denied (publickey)." >&2
    exit 255
fi

# Tunnel invocation
if [[ "$args" == *"-N -L"* ]]; then
    # Keep running until killed
    while true; do
        sleep 1
    done
    exit 0
fi

# Remote start
if [[ "$args" == *"coperceive remote start"* ]]; then
    echo '{"status": "running", "uuid": "test-uuid-99", "job_id": 5555, "node": "hpc-node11", "ip": "10.20.240.48", "port": 52657, "token": "tok12345"}'
    exit 0
fi

# Remote status
if [[ "$args" == *"coperceive remote status"* ]]; then
    if [[ "$args" == *"test-pending"* ]]; then
        echo '{"status": "pending", "state": "PENDING", "uuid": "test-pending", "job_id": 5556, "reason": "ReqNodeNotAvail"}'
    elif [[ "$args" == *"test-timeout"* ]]; then
        echo '{"status": "stopped", "state": "TIMEOUT", "stopped_reason": "timeout", "uuid": "test-timeout", "job_id": 5557, "last_heartbeat_age_s": 650.0}'
    else
        echo '{"status": "running", "state": "RUNNING", "uuid": "test-uuid-99", "job_id": 5555, "node": "hpc-node11", "ip": "10.20.240.48", "port": 52657, "healthy": true, "last_heartbeat_age_s": 12.5}'
    fi
    exit 0
fi

# Remote stop
if [[ "$args" == *"coperceive remote stop"* ]]; then
    echo '{"status": "stopped", "uuid": "test-uuid-99", "job_id": 5555, "cancelled": true}'
    exit 0
fi

# Remote heartbeat
if [[ "$args" == *"coperceive remote heartbeat"* ]]; then
    echo '{"status": "ok", "uuid": "test-uuid-99", "timestamp": 1792500000.0}'
    exit 0
fi

# Remote token (read-only; returns the running session key over the SSH channel)
if [[ "$args" == *"coperceive remote token"* ]]; then
    echo '{"status": "running", "token": "tok-reattach-99"}'
    exit 0
fi

# Remote list
if [[ "$args" == *"coperceive remote list"* ]]; then
    echo '{"sessions": [{"uuid": "test-uuid-99", "job_id": 5555, "state": "RUNNING", "ip": "10.20.240.48", "port": 52657, "is_orphan": false}]}'
    exit 0
fi

echo "unknown command: $args" >&2
exit 1
"#;
            fs::write(&tmp, script).expect("write fake_ssh");
            let mut perms = fs::metadata(&tmp).expect("metadata").permissions();
            perms.set_mode(0o755);
            fs::set_permissions(&tmp, perms).expect("set_permissions");
            Self { path: tmp }
        }
    }

    impl Drop for FakeSshScript {
        fn drop(&mut self) {
            let _ = fs::remove_file(&self.path);
        }
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
            remote_command_line(&args),
            "coperceive remote start --root '/data/my scenes; touch /tmp/x'"
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
        let bin_dir = dir.join("bin");
        fs::create_dir_all(&bin_dir).expect("bin dir");
        let fake_coperceive = bin_dir.join("coperceive");
        fs::write(
            &fake_coperceive,
            "#!/bin/bash\nprintf '%s\\n' \"$@\" > \"$FAKE_COPER_LOG\"\n",
        )
        .expect("write fake coperceive");
        let mut perms = fs::metadata(&fake_coperceive)
            .expect("metadata")
            .permissions();
        perms.set_mode(0o755);
        fs::set_permissions(&fake_coperceive, perms).expect("chmod");

        let marker = dir.join("pwned");
        let log = dir.join("argv.log");
        let malicious = format!(
            "/data/my scenes/\"quoted\"; touch /tmp/x; touch {}",
            marker.display()
        );
        let line = remote_command_line(&[
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
        let path = format!(
            "{}:{}",
            bin_dir.display(),
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
